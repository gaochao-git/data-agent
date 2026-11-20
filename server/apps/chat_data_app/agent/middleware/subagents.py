"""ChatData 子智能体：单个 subagent 工具（dispatch/send/manage/decide 分层）+ 上下文隔离。

参考 deepagents ``SubAgentMiddleware`` 的核心机制（工具入口、状态裁剪
透传、Command 回传结论），并按 ChatData 的事件流与状态设计做了裁剪：

- 首轮是隔离上下文调用：子智能体只看到任务书（HumanMessage），不继承主对话历史；
  ``follow_up`` 会在同一子会话内恢复历史消息后继续执行，但仍不读取主对话正文。
- 仅白名单状态键进入子智能体（身份/数据域/工作区），主图的压缩账本与 token
  用量不透传，子图消息永远不回流——主对话上下文只增加一条 ToolMessage 结论。
  子图内部挂载与主图同源的压缩中间件（见 ``_compile_subagent``）：上下文逼近
  触发线时压缩续命并持续执行，不再依赖「提前收尾」软提示。
- 子图调用显式切断回调继承（``callbacks=[]``），子图内的模型 token 不进入
  主 SSE 流；内层事件通过 ``_run_subagent_with_events`` 挑选后经父图 custom
  流转发（``parent_tool_call_id`` 挂到所属 subagent 调用下），前端在 subagent 卡片内
  展示调用过程。文本与思考以 ``messages`` 模式做 token 级增量转发
  （``subagent_message_delta`` / ``subagent_thinking_delta``），工具生命周期
  仍以 ``updates`` 模式为准。
- 子智能体不注册 ``ask_user_question``；但后台任务可把审批中断冒泡到
  主会话，并通过 ``steer/approve/reject`` 在安全点继续。
"""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass, field
from typing import Annotated, Any, NoReturn

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, AgentState, ModelCallResult, ModelRequest
from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages import RemoveMessage as RemoveMessage  # noqa: F401 - 兼容 re-export
from langchain_core.tools import StructuredTool
from langgraph.config import get_stream_writer
from langgraph.types import Command

from apps.chat_data_app.services.runtime_config import (
    SUBAGENT_MESSAGE_PREVIEW_MAX_CHARS as SUBAGENT_MESSAGE_PREVIEW_MAX_CHARS,  # noqa: F401 - 兼容 re-export
)
from apps.chat_data_app.services.runtime_config import (
    SUBAGENT_RESULT_PREVIEW_MAX_CHARS,
    TOOL_RESULT_OFFLOAD_SIZE,
)
from apps.chat_data_app.services.runtime_config import (
    SUBAGENT_THINKING_PREVIEW_MAX_CHARS as SUBAGENT_THINKING_PREVIEW_MAX_CHARS,  # noqa: F401 - 兼容 re-export
)
from apps.chat_data_app.services.setting_service import get_runtime_setting
from utils.checkpoint import get_checkpointer
from utils.logger import get_logger

from ..agent_state import merge_context_compression_events, merge_loaded_skill_names, replace_approval_decisions
from ..approval_policy import ask_tool_names, resolve_approval_policies
from ..context.context_budget import (
    ContextBudget,
    ContextRequestBudget,
    count_tools_tokens,
    estimate_text_tokens,
    get_model_context_budget,
)
from ..context.runtime import ContextRuntime
from ..contracts.subagent_capabilities import resolve_task_capabilities
from ..contracts.subagent_task import (
    ACTIVE_TASK_STATUSES,
    TASK_STATUS_CANCELLED,
    TASK_STATUS_COMPLETED,
    TASK_STATUS_FAILED,
    TASK_STATUS_INTERRUPTED,
    TASK_STATUS_LABELS,
    TASK_STATUS_LOST,
    TASK_STATUS_TIMEOUT,
    TASK_STATUS_WAITING_APPROVAL,
    TERMINAL_TASK_STATUSES,
    WAITING_APPROVAL_TASK_STATUSES,
    SubagentTaskCapacityError,
    SubagentTaskPort,
    SubagentTaskView,
    get_subagent_task_service,
    is_write_capable_subagent,
    normalize_write_scope,
    normalize_write_scopes,
)
from .context_compression import create_context_compression_middleware
from .context_usage import create_context_usage_middleware
from .sandbox_renewal import create_sandbox_renewal_middleware
from .subagent_assembly import assemble_subagent_middleware
from .subagent_async import (
    _append_partial_report,
    _apply_task_write_scopes,
    _close_aborted_sync_task,
    _get_subagent_stream_writer,
    _launch_task_runner,
    _read_conversation_context,
    _run_sync_follow_up,
    _spawn_background_task,
    _spawn_follow_up_task,
    _truncate_text,
    _watch_background_task,
    manage_async_task,
)
from .subagent_config import build_subagent_invocation_config
from .subagent_control import create_subagent_control_middleware
from .subagent_history import (
    deserialize_subagent_messages,
    serialize_subagent_messages,
)
from .subagent_history import (
    legacy_session_history as _legacy_session_history,
)
from .subagent_stream import (
    SUBAGENT_STATE_PASSTHROUGH_KEYS as SUBAGENT_STATE_PASSTHROUGH_KEYS,
)
from .subagent_stream import (
    SubagentSuspended,
    _build_subagent_state,
    _resolve_subagent_usage_detail,
    _run_subagent_with_events,
    _subagent_process_snapshot,
)
from .subagent_stream import (
    _aggregate_subagent_token_usage as _aggregate_subagent_token_usage,
)
from .subagent_stream import (
    _emit_subagent_tool_event as _emit_subagent_tool_event,
)
from .subagent_stream import (
    _extract_interrupt_payload as _extract_interrupt_payload,
)
from .subagent_stream import (
    _iter_chunk_stream_events as _iter_chunk_stream_events,
)
from .subagent_stream import (
    _iter_subagent_stream_events as _iter_subagent_stream_events,
)
from .subagent_stream import (
    _process_subagent_chunk as _process_subagent_chunk,
)
from .subagent_stream import (
    _StreamCapState as _StreamCapState,
)
from .subagent_stream import (
    _subagent_message_event as _subagent_message_event,
)
from .subagent_stream import (
    _subagent_progress_snapshot as _subagent_progress_snapshot,
)
from .subagent_stream import (
    _subagent_tool_result_event as _subagent_tool_result_event,
)
from .subagent_stream import (
    _SubagentStreamState as _SubagentStreamState,
)
from .subagent_text import (
    StreamingToolCallMarkupFilter as _StreamingToolCallMarkupFilter,  # noqa: F401 - 兼容 re-export
)
from .subagent_text import (
    chunk_reasoning_text as _chunk_reasoning_text,  # noqa: F401 - 兼容 re-export
)
from .subagent_text import (
    chunk_text_delta as _chunk_text_delta,  # noqa: F401 - 兼容 re-export
)
from .subagent_text import (
    plain_text as _plain_text,
)
from .subagent_text import (
    sanitize_report_text as _sanitize_report_text,
)
from .subagent_tool_schema import (
    DecideArgs,
    DispatchArgs,
    ManageArgs,
    SendArgs,
    SubagentToolArgs,
)
from .subagent_trail import (
    _build_trail_snapshot,
    _collect_subagent_tool_results,  # noqa: F401 - 兼容 re-export
    _trail_orphan_tool_entry,  # noqa: F401 - 兼容 re-export
    _trail_tool_entry,  # noqa: F401 - 兼容 re-export
    anonymous_message_id,  # noqa: F401 - 兼容 re-export
)
from .tool_approval import create_tool_approval_middleware
from .tool_execution_fence import create_subagent_execution_fence_middleware, create_tool_execution_fence_middleware
from .tool_result import externalize_tool_text

logger = get_logger(__name__)


# 过程外发上限的口径说明见 services/runtime_config.py：工具结果上限与
# TOOL_RESULT_OFFLOAD_SIZE 对齐（子图同样挂载 ToolResultMiddleware，超限内容
# 已被替换成信封），正文/思考单独配置。三者都保留截断逻辑作为安全兜底，
# 正常路径下不会触发——过程区的意图就是让用户看到子智能体实际做了什么。

SUBAGENT_TOOL_NAME = "subagent"
# 工具结果外置时记录的逻辑来源，用于标识“来自子智能体报告”，不是模型工具名。
SUBAGENT_RESULT_SOURCE = "subagent"

# 子智能体报告进入父上下文前的封顶：报告是结论而非过程，单次派发不应把父
# 上下文推过压缩线。阈值按父 state 的硬上限占比算（32k 与 128k 窗口的合理
# 字节数不同，不写死字符数），超过后复用工具结果同一条落盘/信封通路。
SUBAGENT_REPORT_MAX_TOKEN_RATIO = 0.08
SUBAGENT_REPORT_MIN_TOKEN_LIMIT = 1000
SUBAGENT_REPORT_PREVIEW_CHARS = 2000

_SUBAGENT_TOOL_DESCRIPTION_TEMPLATE = """派发并管理隔离子智能体：只把最终结论带回主对话，过程留在子图以保护上下文。

可用子智能体类型：
{available_agents}

每次调用必须且只能选择 dispatch、send、manage、decide 之一：
- dispatch：派发新任务；任务书必须包含全部背景、要求和交付格式。
- send：向已有子会话发消息；运行中自动插话，已结束自动开启下一轮追问。
- manage：list/check/wait/cancel 子智能体任务。
- decide：对 waiting_approval 任务执行 allow/deny 审批。

参数必须嵌套在所选操作键下，不能把 action、task_id、description 等字段放到顶层：
- {"dispatch":{"subagent_type":"explore","description":"完整任务书","background":true}}
- {"send":{"task_id":"任务 ID","message":"追问内容"}}
- {"manage":{"action":"list"}}
- {"manage":{"action":"check","task_id":"任务 ID"}}
- {"decide":{"task_id":"任务 ID","decision":"allow"}}
错误示例（会因顶层未知字段被拒绝）：{"action":"list"}、{"task_id":"...","message":"..."}

background 默认 true（异步返回 task_id，终态自动唤醒会话）；false 同步等待。
会写 workspace 的 general 任务必须在 dispatch/send 中声明 write_scopes；并发规则见系统提示。
子智能体由用户在管理面板关闭；关闭后不能再 send，如需同类任务请重新 dispatch。"""


class ChatDataSubAgentState(AgentState, total=False):
    """子智能体状态：messages + 白名单透传的业务上下文 + 子图自有压缩账本。

    压缩三键与主图 ``ChatDataAgentState`` 同名同型：子图挂载
    ``ContextCompressionMiddleware`` 后由它写入（摘要 + 压缩报告 + 事件账本），
    生命周期完全属于本次派发，与主图账本互不影响——主图侧仍不透传这些键
    （见 ``SUBAGENT_STATE_PASSTHROUGH_KEYS``），回传也只带最终结论。
    """

    user_id: str | None
    operator_name: str | None
    conversation_id: str | None
    execution_context: dict[str, Any]
    workspace: dict[str, Any]
    context_summary: str | None
    context_compression: dict[str, Any] | None
    context_compression_events: Annotated[list[dict[str, Any]], merge_context_compression_events]
    loaded_skills: Annotated[list[str], merge_loaded_skill_names]
    # 子图自有记账：SubagentContextUsageMiddleware 每次模型调用后写回全量明细
    # （含 breakdown），调用方从 updates 捕获。必须在此声明 channel，否则
    # Command(update=...) 会被 LangGraph 丢弃，前端只能看到汇总、无明细。
    token_usage: dict[str, Any] | None
    # 审批决策：与主图同构，供子图内 ToolApprovalMiddleware 消费。
    approval_decisions: Annotated[dict[str, str], replace_approval_decisions]
    # 后台任务控制身份：仅用于中间件读取账本，不进入模型上下文。
    subagent_task_id: str | None
    subagent_owner_token: str | None


@dataclass(frozen=True)
class SubagentSpec:
    """子智能体定义。"""

    name: str
    description: str
    system_prompt: str
    tools: list = field(default_factory=list)
    model: Any = None
    # 主智能体同一会话的技能清单（<available_skills> XML，format_skills_prompt 产物）。
    # 与主智能体保持一致继承：子图 system prompt 由此注入目录段，正文仍按需
    # file_read /mnt/skills/<name>/SKILL.md；空串或空目录时不注入。
    available_skills_xml: str = ""
    # 主智能体同一会话的记忆条目文本（conversation_memory_service.render_session_memory_block
    # 产物，已逐条转义）。子智能体看不到主对话历史，注入记忆帮助它沿用已确认的
    # 口径与偏好；子任务只引用不写入，注入为只读段，空串时不注入。
    memory_block: str = ""
    # 上下文预算口径来源：与主图同一 model_id，取同一 ContextBudget 的压缩触发
    # 阈值与上下文窗口，用于子图压缩和前端用量展示。
    model_id: str | None = None
    # None 表示跟随调用方传入的运行期配置（CHAT_DATA_AGENT_RECURSION_LIMIT，
    # 主图同源）；在 dataclass 默认值里直接写死常量会在 import 时固化取值，
    # 运行时改配置表 / env / monkeypatch 都不生效。
    recursion_limit: int | None = None
    # 仅 sandbox 模式：模型调用前后续期同一个沙箱（子智能体与主智能体共用工作区）。
    sandbox_renewal: Any = None
    # 编译期创建、压缩/用量展示共享的同一份请求预算；运行期由
    # ``_compile_subagent`` 写入（frozen dataclass 内部构建缓存，不参与身份），
    # 避免三处重复解析模型预算与静态 token。
    _context_request_budget: Any = field(default=None, init=False, compare=False, hash=False, repr=False)


def validate_subagent_specs(specs: list[SubagentSpec]) -> None:
    """校验子智能体定义：至少一个、名称非空且唯一。"""
    if not specs:
        raise ValueError("至少需要定义一个子智能体")
    names = [str(spec.name or "").strip() for spec in specs]
    if any(not name for name in names):
        raise ValueError("子智能体名称不能为空")
    duplicated = {name for name in names if names.count(name) > 1}
    if duplicated:
        raise ValueError(f"子智能体名称重复: {', '.join(sorted(duplicated))}")


def _extract_final_report(messages: list) -> str:
    """取子图最后一个非空 AIMessage 文本作为回传结论。"""
    for message in reversed(messages or []):
        if not isinstance(message, AIMessage):
            continue
        text = _sanitize_report_text(getattr(message, "text", "") or "")
        if text:
            return text
    return ""


# 子图技能目录段的引导语与 XML 拼装：目录与主智能体同一份 <available_skills>，
# 正文消费方式一致（load_skill 加载 SKILL.md）。仅当目录非空时注入。
_SUBAGENT_SKILLS_SECTION_TEMPLATE = """## 可用技能
以下技能清单与主智能体共享同一会话快照；任务与技能相关时，先 `load_skill(name=技能名)`
加载正文并严格按其执行：
{skills_xml}"""

# 子图会话记忆只读段：条目与主图同源（同一渲染入口，已转义）。子智能体没有
# 维护记忆的职责，声明只读，避免子任务写主对话的记忆文件。
_SUBAGENT_MEMORY_SECTION_TEMPLATE = """## 会话记忆（只读）
以下是主对话中已沉淀的本会话记忆条目，供你沿用已确认的口径、纠错结论与偏好：
{memory_block}
子任务只引用以上记忆，不写入、不修改记忆文件（`/mnt/user-data/memory/memory.md`）。"""


def _subagent_system_prompt(spec: SubagentSpec) -> str:
    """返回子图实际生效的 system prompt：spec.system_prompt + 会话级共享段。

    共享段为技能目录（``<available_skills>``，与主图同源）与会话记忆只读段
    （``spec.memory_block``，与主图同源）；为空（未传或本轮无内容）时不追加空块。
    """
    prompt = spec.system_prompt
    xml = str(spec.available_skills_xml or "").strip()
    if "<skill>" in xml:
        prompt += "\n\n" + _SUBAGENT_SKILLS_SECTION_TEMPLATE.format(skills_xml=xml)
    memory = str(spec.memory_block or "").strip()
    if memory:
        prompt += "\n\n" + _SUBAGENT_MEMORY_SECTION_TEMPLATE.format(memory_block=memory)
    return prompt


def _subagent_budget_meta(
    spec: SubagentSpec,
    tools: list,
    *,
    system_prompt: str | None = None,
    context_request_budget: ContextRequestBudget | None = None,
) -> dict[str, int]:
    """子图用量展示的公共口径：静态 token（system+tools）与压缩触发阈值。

    ``subagent_context_usage`` 事件与子图压缩共用同一份 ``ContextBudget``，
    保证前端展示的阈值与实际压缩线一致（同一
    ``ContextBudget.compression_trigger_tokens``），换模型后不会漂移。
    ``system_prompt`` 传入增强后的实际 system prompt（含技能目录段），缺省用
    spec.system_prompt。
    传入已配置的 ``context_request_budget`` 时直接复用它，避免与压缩中间件
    各自重复解析模型预算、重复估算 system+tools token。
    """
    if context_request_budget is not None and context_request_budget.is_configured:
        budget: ContextBudget = context_request_budget.budget
        static_tokens = context_request_budget.static_input_tokens
    else:
        budget = get_model_context_budget(spec.model_id)
        static_tokens = estimate_text_tokens(system_prompt or spec.system_prompt) + count_tools_tokens(
            list(tools or [])
        )
    threshold_tokens = max(int(budget.compression_trigger_tokens), 1)
    return {
        "static_tokens": static_tokens,
        "threshold_tokens": threshold_tokens,
        # 上下文窗口：前端展示与主对话同口径的「占窗口百分比」需要它
        # （测试桩/降级预算可能没有该字段，回退到阈值，保证百分比口径不炸）
        "context_window": max(int(getattr(budget, "context_window", 0) or threshold_tokens), 1),
    }


def _safe_subagent_budget_meta(
    spec: SubagentSpec,
    tools: list,
    *,
    system_prompt: str | None = None,
) -> dict[str, int] | None:
    """用量推送口径的降级封装：预算查询失败只放弃展示，不影响子智能体执行。"""
    try:
        return _subagent_budget_meta(
            spec,
            tools,
            system_prompt=system_prompt,
            context_request_budget=getattr(spec, "_context_request_budget", None),
        )
    except Exception as exc:
        logger.warning("[tools] 子图上下文用量口径获取失败，本次派发不做用量推送: %s", exc)
        return None


def _compile_subagent(spec: SubagentSpec, run_controls: Any = None, *, control_enabled: bool = False) -> Any:
    """把 spec 编译成独立 langgraph runnable。

    ``run_controls`` 完整时为子图挂上与主图相同的租约围栏：租约丢失/被接管后
    内层工具（general 的 file_write、explore 的查询等）同样拒绝产生新副作用，
    而不是只靠心跳的协作式取消。
    """
    tools = list(spec.tools or [])
    # 技能目录与主智能体一致继承：增强后的 system prompt（含 <available_skills> 段）
    # 是压缩中间件、用量展示与 create_agent 三处的同一份口径。
    effective_system_prompt = _subagent_system_prompt(spec)
    # 同一份请求预算供压缩与用量展示共用；静态 token 只按子图
    # 增强后的 system prompt + 实际工具集算一次。
    subagent_static_tokens = estimate_text_tokens(effective_system_prompt) + count_tools_tokens(tools)
    subagent_runtime = ContextRuntime.create(
        model_id=spec.model_id,
        system_prompt=effective_system_prompt,
        tools=tools,
        static_input_tokens=subagent_static_tokens,
    )
    object.__setattr__(spec, "_context_request_budget", subagent_runtime.request_budget)
    # 主图同款上下文计量实例：子图把全量 usage（含 breakdown）写进 state，
    # 由调用方从 updates 里按 run 捕获——不依赖跨 run 共享的 middleware 实例状态。
    context_usage_middleware = create_context_usage_middleware(
        context_runtime=subagent_runtime,
        tools=tools,
        state_schema=ChatDataSubAgentState,
        name="SubagentContextUsageMiddleware",
        store_full_usage=True,
    )
    # 中间件顺序由 SUBAGENT_MIDDLEWARE_CANONICAL_ORDER 唯一决定：出口处理链
    # （归一化→结果→错误）的次序与主图保持一致；压缩中间件是 before_model
    # 节点（独立图节点，先于模型调用改写 state），上下文计量负责把压缩摘要
    # 注入当轮请求。
    # 注意：子图不挂 TodoListMiddleware——子智能体不给规划/进度类工具，
    # 进度与完成情况由收尾协议直接写进最终报告。
    control_middleware = None
    approval_middleware = None
    if control_enabled:
        control_middleware = create_subagent_control_middleware()
        approval_policies = resolve_approval_policies(tools)
        if ask_tool_names(approval_policies):
            # 审批只在后台任务启用（有独立 checkpointer 才能中断/恢复）；
            # 前台同步派发保持原有行为，不引入无法恢复的悬挂中断。
            approval_middleware = create_tool_approval_middleware(tools, approval_policies=approval_policies)
    sandbox_renewal_middleware = (
        create_sandbox_renewal_middleware(spec.sandbox_renewal) if spec.sandbox_renewal is not None else None
    )
    execution_fence_middleware = (
        create_tool_execution_fence_middleware(
            run_id=run_controls.run_id,
            owner_token=run_controls.owner_token,
            inbox_service=run_controls.inbox_service,
        )
        if run_controls is not None and run_controls.is_complete()
        else (create_subagent_execution_fence_middleware() if control_enabled else None)
    )
    middleware = assemble_subagent_middleware(
        tools,
        # 与主图同源的上下文压缩：trigger=窗口 70% 时摘要/引用外置/确定性收缩
        # 压到 30%，模型该调工具照常调（不改工具面），压缩后可继续执行。
        context_compression_middleware=create_context_compression_middleware(
            system_prompt=effective_system_prompt,
            tools=tools,
            model_id=spec.model_id,
            context_runtime=subagent_runtime,
            state_schema=ChatDataSubAgentState,
        ),
        # 与主图同源的上下文计量：产出 cache/breakdown 明细（前端「上下文占用明细」用）；
        # 子图虽订阅 custom 流，但只挑选压缩 started 占位转发，token_usage 依旧
        # 不会透传到主对话。
        context_usage_middleware=context_usage_middleware,
        control_middleware=control_middleware,
        approval_middleware=approval_middleware,
        sandbox_renewal_middleware=sandbox_renewal_middleware,
        execution_fence_middleware=execution_fence_middleware,
    )
    return create_agent(
        spec.model,
        tools=tools,
        system_prompt=effective_system_prompt,
        state_schema=ChatDataSubAgentState,
        name=f"subagent_{spec.name}",
        # 内层模型与主智能体同一 vLLM，同样可能返回重复工具名/字符串 args，
        # 必须挂同一归一化 middleware，否则内层工具会以原始形态执行。
        # ToolResultMiddleware 必须同样挂载：查询工具返回的是内部协议 JSON，
        # 靠它在出口转成模型可读的小信封并外置全量数据；不挂则子图内
        # 全量查询数据内联进上下文（顺序与主图 MIDDLEWARE_CANONICAL_ORDER
        # 一致：先归一化入参，再处理出口）。白名单已透传
        # user_id/conversation_id/workspace，卸载副本写入所需的字段齐备。
        # ToolErrorMiddleware 同为必备：工具失败统一 raise 后，子图 ToolNode
        # 的默认 handler 只认 ToolInvocationError、其余异常原样上抛会炸掉
        # 整个子图；挂上后与主图口径一致，异常统一转 status="error" 的
        # ToolMessage，子智能体可读取错误详情自行纠正。
        middleware=middleware,
    )


def _truncate_subagent_result(content: Any) -> str:
    """把内层工具结果压成 SSE 可安全外发的文本预览。"""
    return _truncate_text(_plain_text(content), SUBAGENT_RESULT_PREVIEW_MAX_CHARS)


def _subagent_report_token_limit(runtime: ToolRuntime) -> int | None:
    """报告封顶阈值 = 父上下文硬上限的固定占比，避免父上下文被单次报告推过压缩线。"""
    state = getattr(runtime, "state", None)
    usage = state.get("token_usage") if isinstance(state, dict) else None
    if not isinstance(usage, dict):
        return None
    hard_limit = int(usage.get("hard_input_limit_tokens") or 0)
    if hard_limit <= 0:
        return None
    return max(int(hard_limit * SUBAGENT_REPORT_MAX_TOKEN_RATIO), SUBAGENT_REPORT_MIN_TOKEN_LIMIT)


async def _offload_subagent_report(report: str, runtime: ToolRuntime) -> tuple[str, dict[str, Any] | None]:
    """超长报告落盘：前台用信封保护上下文，后台只取 recovery 元数据。"""
    limit = _subagent_report_token_limit(runtime)
    report_tokens = estimate_text_tokens(report)
    over_token_budget = limit is not None and report_tokens > limit
    if not over_token_budget and len(report) <= TOOL_RESULT_OFFLOAD_SIZE:
        return report, None
    state = getattr(runtime, "state", None)
    envelope, recovery = await externalize_tool_text(
        report,
        source_tool=SUBAGENT_RESULT_SOURCE,
        state=dict(state) if isinstance(state, dict) else {},
        suffix=".md",
        preview_chars=SUBAGENT_REPORT_PREVIEW_CHARS,
    )
    if recovery is None:
        # 报告是昂贵且不可复现的结论：落盘失败时保留原文，不能静默换成无内容的截断信封。
        logger.warning(
            "[tools] 子智能体报告落盘失败，保留原文: limit_tokens=%s report_tokens=%s",
            limit,
            report_tokens,
        )
        return report, None
    logger.info(
        "[tools] 子智能体报告超过父上下文预算，已落盘外置: limit_tokens=%s report_tokens=%s",
        limit,
        report_tokens,
    )
    return envelope, recovery


def _build_task_result_artifacts(
    messages: list,
    context_usage: Any,
    captured: dict[str, Any],
) -> dict[str, Any]:
    """subagent 结果的持久化产物：过程快照 + 上下文用量 + token 用量 + 压缩/计量明细。"""
    artifacts: dict[str, Any] = {}
    trail = _build_trail_snapshot(messages, captured.get("compactions"))
    if trail:
        artifacts["subagent_trail"] = trail
    artifacts.update(
        _subagent_process_snapshot(
            messages,
            context_usage,
            captured.get("compactions"),
            _resolve_subagent_usage_detail(captured),
            captured.get("model"),
            captured.get("generation_timing"),
        )
    )
    return artifacts


@dataclass(frozen=True)
class _TaskExecution:
    """子图执行的可选控制参数：后台任务、追问、恢复共用一组上下文。"""

    background_task_id: str | None = None
    background_agent_session_id: str | None = None
    background_owner_token: str | None = None
    background_subagent_type: str | None = None
    history: list[Any] | None = None
    checkpointer: Any = None
    resume: bool = False
    resume_value: Any = None
    recover: bool = False


async def _build_task_tool_impl(
    specs: list[SubagentSpec],
    compiled: dict[str, Any],
    subagent_type: str,
    description: str,
    runtime: ToolRuntime,
    *,
    stream_events: bool = True,
    stream_writer: Callable[[Any], None] | None = None,
    offload_report: bool = True,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    state_sink: dict[str, Any] | None = None,
    execution: _TaskExecution | None = None,
) -> Any:
    """执行一次子智能体派发：运行子图、收集过程、产出 Command 更新。

    ``stream_events=False`` 用于后台子智能体：父级 subagent 调用已先行返回
    （卡片已关闭），子图过程不再往父流转发，静默执行只留最终报告。
    """
    execution = execution or _TaskExecution()
    runnable = compiled.get(subagent_type)
    if runnable is None:
        allowed = ", ".join(f"`{name}`" for name in compiled)
        _raise_tool_error(f"无法调用子智能体 {subagent_type}：类型不存在，可用类型为 {allowed}")
    spec = next(item for item in specs if item.name == subagent_type)
    writer = stream_writer or _get_subagent_stream_writer(stream_events, get_stream_writer)
    collected: list = []
    captured: dict[str, Any] = {}
    if state_sink is not None:
        state_sink["messages"] = collected
        state_sink["captured"] = captured
    messages, report, tool_status = await _invoke_task_subagent(
        runnable,
        spec,
        description,
        runtime,
        writer=writer,
        collected=collected,
        captured=captured,
        progress_callback=progress_callback,
        execution=execution,
    )
    context_usage = captured.get("context_usage")
    if state_sink is not None:
        # 账本需要保留完整原文；报告外置只会改写返回给父上下文的 content。
        state_sink["report"] = report
        state_sink["tool_status"] = tool_status
    if tool_status == "success" and offload_report:
        report, recovery = await _offload_subagent_report(report, runtime)
        if state_sink is not None and recovery is not None:
            state_sink["report_path"] = recovery["result_path"]
            state_sink["report_size_chars"] = recovery["result_size_chars"]
    # 过程快照盖在 ToolMessage 上随消息持久化：直播过程走 SSE，历史回放
    # 走 conversation_event 的 tool_result payload（见
    # conversation_event_service._serialize_tool_message）。
    # 上下文用量 + 子图 token 用量：随 ToolMessage 持久化，历史回放还原
    # （直播路径上下文用量走 subagent_context_usage SSE 事件，token 用量随完成事件下发）。
    additional_kwargs = _build_task_result_artifacts(messages, context_usage, captured)
    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=report,
                    tool_call_id=runtime.tool_call_id or "",
                    status=tool_status,
                    additional_kwargs=additional_kwargs,
                )
            ],
        }
    )


class SubagentRecoveryRejected(RuntimeError):
    """当前检查点不能证明自动重放安全，任务必须 fail-closed。"""


async def _prepare_crash_recovery_input(
    runnable: Any,
    config: dict[str, Any],
    runtime: Any,
    *,
    task_id: str,
    owner_token: str,
    subagent_type: str,
) -> tuple[Command[Any], list[Any]]:
    try:
        snapshot = await runnable.aget_state(config)
    except Exception as exc:
        raise SubagentRecoveryRejected("子图检查点读取失败，无法安全续跑") from exc
    values = getattr(snapshot, "values", None)
    if not isinstance(values, dict) or not values.get("messages"):
        raise SubagentRecoveryRejected("子图检查点缺失，无法安全续跑")
    if _checkpoint_has_uncertain_tool_side_effect(snapshot, subagent_type):
        raise SubagentRecoveryRejected("任务停在工具副作用尚未确认的位置，需要人工核查后再处理")
    runtime.state = {
        **values,
        "subagent_task_id": task_id,
        "subagent_owner_token": owner_token,
    }
    update = {
        "subagent_task_id": task_id,
        "subagent_owner_token": owner_token,
    }
    return Command(update=update), list(values.get("messages") or [])


def _checkpoint_has_uncertain_tool_side_effect(snapshot: Any, subagent_type: str) -> bool:
    if not is_write_capable_subagent(subagent_type) or "tools" not in tuple(getattr(snapshot, "next", ()) or ()):
        return False
    tasks = tuple(getattr(snapshot, "tasks", ()) or ())
    return not any(tuple(getattr(task, "interrupts", ()) or ()) for task in tasks)


async def _approval_resume_input(
    runnable: Any,
    config: dict[str, Any],
    execution: _TaskExecution,
) -> tuple[Command[Any], list[Any]]:
    graph_input: Command[Any] = Command(
        update={
            "subagent_task_id": execution.background_task_id,
            "subagent_owner_token": execution.background_owner_token,
        },
        resume=execution.resume_value,
    )
    try:
        snapshot = await runnable.aget_state(config)
        values = getattr(snapshot, "values", None)
        messages = list((values or {}).get("messages") or []) if isinstance(values, dict) else []
        return graph_input, messages
    except Exception:
        logger.warning("[tools] 读取子图检查点失败，恢复后过程快照可能不完整", exc_info=True)
        return graph_input, []


async def _resolve_task_graph_input(
    runnable: Any,
    spec: SubagentSpec,
    description: str,
    runtime: ToolRuntime,
    execution: _TaskExecution,
    config: dict[str, Any],
) -> tuple[Any, list[Any] | None]:
    if execution.recover:
        return await _prepare_crash_recovery_input(
            runnable,
            config,
            runtime,
            task_id=execution.background_task_id or "",
            owner_token=execution.background_owner_token or "",
            subagent_type=execution.background_subagent_type or spec.name,
        )
    if execution.resume:
        return await _approval_resume_input(runnable, config, execution)
    return _build_subagent_state(
        runtime,
        description,
        execution.history,
        task_id=execution.background_task_id,
        owner_token=execution.background_owner_token,
    ), None


async def _invoke_task_subagent(
    runnable: Any,
    spec: SubagentSpec,
    description: str,
    runtime: ToolRuntime,
    *,
    writer: Any,
    collected: list,
    captured: dict[str, Any],
    progress_callback: Callable[[dict[str, Any]], None] | None,
    execution: _TaskExecution | None = None,
) -> tuple[list, str, str]:
    """运行子图并归一成功/失败结果，失败也保留已收集的过程。"""
    execution = execution or _TaskExecution()
    if spec.model_id:
        captured.setdefault("model", spec.model_id)
    config = build_subagent_invocation_config(
        spec,
        default_recursion_limit=await get_runtime_setting("CHAT_DATA_AGENT_RECURSION_LIMIT"),
        background_task_id=execution.background_task_id,
        agent_session_id=execution.background_agent_session_id,
        checkpointer=execution.checkpointer,
    )
    graph_input, initial_messages = await _resolve_task_graph_input(
        runnable,
        spec,
        description,
        runtime,
        execution,
        config,
    )
    try:
        messages = await _run_subagent_with_events(
            runnable,
            graph_input,
            config,
            writer,
            runtime.tool_call_id,
            collected=collected,
            captured=captured,
            usage_meta=_safe_subagent_budget_meta(
                spec,
                list(spec.tools or []),
                system_prompt=_subagent_system_prompt(spec),
            ),
            on_progress=progress_callback,
            initial_messages=initial_messages,
        )
    except SubagentSuspended:
        raise
    except Exception as exc:
        # 失败路径同样盖 trail 快照：直播时内层过程已通过 SSE 转发给用户，
        # 若异常直接抛给 tool_error 中间件（无 additional_kwargs），历史里
        # trail 会丢失，造成「直播看得到、历史看不到」的不一致。这里就地
        # 转成 error ToolMessage，主智能体可据失败原因降级接手。
        logger.error("[tools] 子智能体执行异常: %s", exc, exc_info=True)
        report = f"子智能体执行失败：{_truncate_text(str(exc) or exc.__class__.__name__, 200)}"
        return collected, report, "error"
    report = _extract_final_report(messages)
    if not report:
        report = "子智能体未产出有效结论，请检查任务书后重试或自行完成该任务。"
    return messages, report, "success"


async def _run_background_task(
    record: SubagentTaskView,
    runtime: Any,
    specs: list[SubagentSpec],
    compiled: dict[str, Any],
    *,
    resumed: bool = False,
    resume: bool = False,
    resume_value: Any = None,
    recovered: bool = False,
) -> None:
    """后台子智能体执行体：租约 + 心跳 + 超时 + 取消收口。"""
    service = get_subagent_task_service()
    if service is None:
        logger.error("[tools] 后台任务账本服务未初始化，无法执行: task_id=%s", record.task_id)
        return
    # 恢复执行器在认领任务时已经把行推进到 running 并换发了 owner；
    # 此时不能再调用 start_task（它只接受 pending），否则恢复协程会
    # 在进入执行前静默返回，任务停在 running 直到下一次租约过期。
    # 只在本轮不是「审批后恢复」时，才把账本里的 recovery_count 解释为
    # 租约接管；审批恢复必须走 Command(resume=...)，不能误走检查点重放。
    recovered = recovered or (not resumed and int(record.meta.get("recovery_count") or 0) > 0)
    if not resumed and not recovered:
        started = await service.start_task(record.task_id, record.owner_token or "")
        if started is None:
            logger.info("[tools] 后台任务已取消或归属变化，跳过执行: task_id=%s", record.task_id)
            # 本进程不再持有该任务的执行权，释放绑定的恢复执行器，避免
            # 旧闭包在租约回收时被误用。
            service.register_recovery_runner(None, task_id=record.task_id)
            return
    crash_recovery = recovered and not resume and record.meta.get("recovery_kind") == "checkpoint"
    history = None if resume or crash_recovery else await _load_background_history(service, record)
    progress: dict[str, Any] = {"phase": "running"}
    partial_state: dict[str, Any] = {}
    event_writer = service.create_event_writer(record)
    await event_writer.start()
    outcome, suspended = await _execute_background_attempt(
        record,
        runtime,
        specs,
        compiled,
        progress,
        partial_state,
        event_writer,
        history,
        resume=resume,
        resume_value=resume_value,
        recover=crash_recovery,
    )
    if suspended is not None:
        status = TASK_STATUS_WAITING_APPROVAL
        await service.suspend_task(
            record.task_id,
            record.owner_token or "",
            status=status,
            interrupt_payload=suspended.payload,
        )
        return
    assert outcome is not None
    await _finish_background_task(service, record, history, partial_state, outcome)


async def _load_background_history(service: SubagentTaskPort, record: SubagentTaskView) -> list[Any]:
    """追问轮次的前置历史；快照缺失时退回上一轮任务书+报告。"""
    if not record.parent_task_id:
        return []
    payloads = await service.load_session_messages(
        record.agent_session_id,
        conversation_id=record.conversation_id,
        user_id=record.user_id,
        # 追问第 N 轮前，快照至少应覆盖到第 N-1 轮；落后说明上一轮写入失败。
        expected_turn_index=max(record.turn_index - 1, 0),
    )
    if not payloads:
        parent = await service.get_task(
            record.parent_task_id,
            conversation_id=record.conversation_id,
            user_id=record.user_id,
        )
        if parent is not None:
            payloads = _legacy_session_history(parent)
            logger.info(
                "[tools] 追问任务缺少会话快照，使用上一轮任务书+报告兜底: task_id=%s parent=%s",
                record.task_id,
                record.parent_task_id,
            )
    return deserialize_subagent_messages(payloads)


async def _execute_background_attempt(
    record: SubagentTaskView,
    runtime: Any,
    specs: list[SubagentSpec],
    compiled: dict[str, Any],
    progress: dict[str, Any],
    partial_state: dict[str, Any],
    event_writer: Any,
    history: list[Any] | None,
    *,
    resume: bool,
    resume_value: Any,
    recover: bool = False,
) -> tuple[tuple[str, str, str, dict[str, Any]] | None, SubagentSuspended | None]:
    """在独立检查点内跑一次执行体；返回正常结果或中断载荷。"""
    service = get_subagent_task_service()
    assert service is not None
    async with AsyncExitStack() as stack:
        checkpointer = await _open_subgraph_checkpointer(stack, record.task_id)
        watcher = asyncio.create_task(
            _watch_background_task(
                record.task_id,
                record.owner_token or "",
                progress,
                asyncio.current_task(),
                service,
            ),
            name=f"chatdata-subagent-watch-{record.task_id}",
        )
        try:
            try:
                return await _run_background_attempt(
                    record,
                    runtime,
                    specs,
                    compiled,
                    progress,
                    partial_state,
                    stream_writer=event_writer,
                    history=history,
                    checkpointer=checkpointer,
                    resume=resume,
                    resume_value=resume_value,
                    recover=recover,
                ), None
            except SubagentSuspended as exc:
                return None, exc
            except asyncio.CancelledError:
                await _finish_cancelled_background_task(service, record, partial_state, history=history)
                raise
        finally:
            await event_writer.aclose()
            watcher.cancel()
            with suppress(asyncio.CancelledError):
                await watcher


async def _open_subgraph_checkpointer(stack: AsyncExitStack, task_id: str) -> Any:
    """检查点生命周期跟随执行体；初始化失败降级为无检查点。"""
    try:
        # 子图检查点不继承父 Run；多 worker 也能从数据库恢复同一
        # agent_session + subagent 命名空间。
        return await stack.enter_async_context(get_checkpointer())
    except Exception:
        logger.warning(
            "[tools] 子图检查点初始化失败，审批恢复将不可用，按无检查点执行: task_id=%s",
            task_id,
            exc_info=True,
        )
        return None


async def _finish_background_task(
    service: SubagentTaskPort,
    record: SubagentTaskView,
    history: list[Any] | None,
    partial_state: dict[str, Any],
    outcome: tuple[str, str, str, dict[str, Any]],
) -> None:
    status, report, error, result_meta = outcome
    history_payload = _session_history_payload(record, history, partial_state)
    await service.finish_task(
        record.task_id,
        record.owner_token or "",
        status=status,
        report=report,
        error=error,
        result_meta=result_meta,
        history=history_payload,
    )
    try:
        await service.dispatch_pending_notifications(limit=1)
    except Exception:
        logger.warning("[tools] 后台任务完成通知即时派发失败，等待调度轮询: task_id=%s", record.task_id, exc_info=True)


async def _run_background_attempt(
    record: SubagentTaskView,
    runtime: Any,
    specs: list[SubagentSpec],
    compiled: dict[str, Any],
    progress: dict[str, Any],
    partial_state: dict[str, Any],
    *,
    stream_writer: Callable[[Any], None] | None = None,
    history: list[Any] | None = None,
    checkpointer: Any = None,
    resume: bool = False,
    resume_value: Any = None,
    recover: bool = False,
) -> tuple[str, str, str, dict[str, Any]]:
    try:
        command = await asyncio.wait_for(
            _build_task_tool_impl(
                specs,
                compiled,
                record.subagent_type,
                record.description,
                runtime,
                # 后台任务不再复写父 Run 的 custom 流（父 Run 收口后没有消费者）；
                # 过程事件改走任务自己的可重放 SSE 流，详情页可在父 Run 结束后继续订阅。
                stream_events=True,
                stream_writer=stream_writer,
                offload_report=False,
                progress_callback=lambda snapshot: progress.update(snapshot),
                state_sink=partial_state,
                execution=_TaskExecution(
                    background_task_id=record.task_id,
                    background_agent_session_id=getattr(record, "agent_session_id", None),
                    background_owner_token=getattr(record, "owner_token", None),
                    background_subagent_type=record.subagent_type,
                    history=history,
                    checkpointer=checkpointer,
                    resume=resume,
                    resume_value=resume_value,
                    recover=recover,
                ),
            ),
            timeout=record.timeout_seconds,
        )
    except TimeoutError:
        return _timeout_background_outcome(record, partial_state)
    except SubagentSuspended:
        raise
    except SubagentRecoveryRejected as exc:
        report = f"后台子智能体无法安全自动续跑：{exc}。请检查已产生的副作用后重新派发或追问。"
        logger.warning("[tools] 后台子智能体恢复被拒绝: task_id=%s reason=%s", record.task_id, exc)
        return TASK_STATUS_LOST, report, str(exc), {}
    except Exception as exc:
        return _failed_background_outcome(record, exc, partial_state)
    status, report, error, result_meta = _extract_background_result(command)
    if status == TASK_STATUS_COMPLETED:
        # 账本保留全文；超长时额外落一份隐藏文件，通知与 subagent(manage={"action": "check"})
        # 会带 result_path，主智能体可直接用 file_read 读取完整报告。
        _delivered, recovery = await _offload_subagent_report(report, runtime)
        if recovery:
            result_meta["report_path"] = recovery["result_path"]
            result_meta["report_size_chars"] = recovery["result_size_chars"]
    return status, report, error, result_meta


def _timeout_background_outcome(
    record: SubagentTaskView, partial_state: dict[str, Any]
) -> tuple[str, str, str, dict[str, Any]]:
    error = f"任务超过 {record.timeout_seconds} 秒未完成"
    partial_report, result_meta = _capture_partial_background_result(partial_state)
    report = f"后台子智能体执行超时：{error}。可重新派发更小的任务。"
    logger.warning("[tools] 后台子智能体执行超时: task_id=%s", record.task_id)
    return TASK_STATUS_TIMEOUT, _append_partial_report(report, partial_report), error, result_meta


def _failed_background_outcome(
    record: SubagentTaskView, exc: Exception, partial_state: dict[str, Any]
) -> tuple[str, str, str, dict[str, Any]]:
    error = str(exc) or exc.__class__.__name__
    partial_report, result_meta = _capture_partial_background_result(partial_state)
    report = f"后台子智能体执行异常：{_truncate_text(error, 300)}"
    logger.error("[tools] 后台子智能体执行异常: task_id=%s", record.task_id, exc_info=True)
    return TASK_STATUS_FAILED, _append_partial_report(report, partial_report), error, result_meta


async def _finish_cancelled_background_task(
    service: SubagentTaskPort,
    record: SubagentTaskView,
    partial_state: dict[str, Any],
    *,
    history: list[Any] | None = None,
) -> None:
    """取消/进程中断时先落终态再继续向上抛，保留中断前过程。"""
    current_record = await service.get_task(record.task_id)
    partial_report, result_meta = _capture_partial_background_result(partial_state)
    cancelled = current_record is not None and current_record.status in {"cancelling", TASK_STATUS_CANCELLED}
    status = TASK_STATUS_CANCELLED if cancelled else TASK_STATUS_INTERRUPTED
    report = "任务已被用户或主智能体取消。" if cancelled else "任务因执行节点中断而停止；可重新派发。"
    await service.finish_task(
        record.task_id,
        record.owner_token or "",
        status=status,
        report=_append_partial_report(report, partial_report),
        error=str(current_record.error if current_record else ""),
        result_meta=result_meta,
        history=_session_history_payload(record, history, partial_state),
    )
    try:
        # 用户取消后同样通知主智能体；空闲会话由通知 Run 自动唤醒。
        await service.dispatch_pending_notifications(limit=1)
    except Exception:
        logger.warning("[tools] 子智能体取消通知即时派发失败: task_id=%s", record.task_id, exc_info=True)


def _session_history_payload(
    record: SubagentTaskView,
    history: list[Any] | None,
    partial_state: dict[str, Any],
) -> list[dict[str, Any]] | None:
    """收口时落会话快照；恢复执行缺少完整最终消息时保留原快照。"""
    captured = partial_state.get("captured")
    final_messages = captured.get("final_messages") if isinstance(captured, dict) else None
    if not isinstance(final_messages, list) or not final_messages:
        if history is None:
            return None
        final_messages = [
            *history,
            HumanMessage(content=record.description),
            *list(partial_state.get("messages") or []),
        ]
    return serialize_subagent_messages(list(final_messages))


def _capture_partial_background_result(state_sink: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """超时/取消时取出子图已收集的过程，避免任务详情只剩一句终止提示。"""
    messages = list(state_sink.get("messages") or [])
    captured = state_sink.get("captured")
    if not isinstance(captured, dict):
        captured = {}
    result_meta = _build_task_result_artifacts(messages, captured.get("context_usage"), captured)
    result_meta["tool_status"] = "error"
    report = _extract_final_report(messages) if messages else ""
    return report, result_meta


def _extract_background_result(command: Any) -> tuple[str, str, str, dict[str, Any]]:
    update = getattr(command, "update", None) or {}
    messages = update.get("messages") or []
    message = messages[0] if messages else None
    report = str(getattr(message, "content", "") or "")
    tool_status = str(getattr(message, "status", "") or "success")
    additional = dict(getattr(message, "additional_kwargs", None) or {})
    trail = additional.get("subagent_trail")
    result_meta = {"tool_status": tool_status, "trail_items": len(trail) if isinstance(trail, list) else 0}
    for key in (
        "subagent_trail",
        "subagent_context_usage",
        "subagent_token_usage",
        "subagent_token_usage_detail",
        "subagent_compactions",
    ):
        value = additional.get(key)
        if value:
            result_meta[key] = value
    if tool_status == "error":
        return TASK_STATUS_FAILED, report, report, result_meta
    return TASK_STATUS_COMPLETED, report, "", result_meta


class _BackgroundRunnableCache:
    """后台/追问共用一套独立子图；首次需要时编译，避免前台每轮重复占用。"""

    def __init__(self, specs: list[SubagentSpec]) -> None:
        self._specs = specs
        self._compiled: dict[str, Any] | None = None

    def get(self) -> dict[str, Any]:
        if self._compiled is None:
            # 后台/追问走独立检查点，需要控制与审批中间件；前台同步派发仍用
            # build_task_tool 里未启用控制的编译结果。
            self._compiled = {spec.name: _compile_subagent(spec, control_enabled=True) for spec in self._specs}
        return self._compiled


async def _follow_up_task_action(
    *,
    task_id: str | None,
    message: str | None,
    runtime: ToolRuntime,
    run_controls: Any,
    specs: list[SubagentSpec],
    background: bool,
    write_scopes: list[str] | None,
    compiled: dict[str, Any],
    background_runnables: dict[str, Any],
) -> Command | str:
    async def _sync_runner(service: SubagentTaskPort, record: SubagentTaskView) -> Command | str:
        async def _run(started: SubagentTaskView) -> Command | str:
            return await _run_registered_sync_task(
                service,
                started,
                specs,
                compiled,
                started.subagent_type,
                started.description,
                runtime,
                history_loader=lambda: _load_background_history(service, started),
            )

        return await _run_sync_follow_up(service, record, _run)

    return await _spawn_follow_up_task(
        task_id=task_id,
        message=message,
        available_types=set(background_runnables),
        runtime=runtime,
        run_controls=run_controls,
        background=background,
        write_scopes=_validate_tool_write_scopes(write_scopes),
        runner_factory=lambda record, snapshot: _run_background_task(record, snapshot, specs, background_runnables),
        sync_runner=_sync_runner,
    )


def _raise_tool_error(message: str) -> NoReturn:
    """把工具自身的失败（参数缺失/类型不存在）变成 error 状态的工具结果。

    直接 return 字符串时，框架会包成 ``status=success`` 的工具消息，前端卡片
    显示绿色成功、模型也难以区分"已派发"和"被拒绝"。这里沿用仓库既有约定：
    工具层只 raise ValueError，由最内层 ToolErrorMiddleware 统一转
    ``status="error"`` 并按"预期校验错误"降级日志。
    """
    raise ValueError(message)


def _validate_tool_write_scopes(write_scopes: list[str] | None) -> list[str] | None:
    """校验工具边界的写范围声明；None 表示追问继承原范围。"""
    if write_scopes is None:
        return None
    invalid = [item for item in write_scopes if not normalize_write_scope(item)]
    if invalid:
        _raise_tool_error("write_scopes 必须是工作区相对文件或目录前缀，例如 outputs/word；不能为空或包含 ..。")
    return normalize_write_scopes(write_scopes)


def _tee_stream_writers(*writers: Any) -> Callable[[Any], None] | None:
    """一路过程同时写父对话流与任务详情流；单路失败不影响另一条链路。"""
    active = tuple(writer for writer in writers if callable(writer))
    if not active:
        return None

    def _write(payload: Any) -> None:
        for writer in active:
            try:
                writer(payload)
            except Exception:
                logger.debug("[tools] 子智能体过程事件单路写入失败，继续其他链路", exc_info=True)

    return _write


def _sync_task_outcome(
    command: Any,
    partial_state: dict[str, Any],
) -> tuple[str, str, str, dict[str, Any]]:
    """从同步 Command 与执行快照生成账本收口数据。"""
    status, report, error, result_meta = _extract_background_result(command)
    if status == TASK_STATUS_COMPLETED:
        # ToolMessage 可能已被外置成预览信封；任务账本保留完整原文。
        ledger_report = str(partial_state.get("report") or "").strip()
        if ledger_report:
            report = ledger_report
    report_path = str(partial_state.get("report_path") or "").strip()
    if report_path:
        result_meta["report_path"] = report_path
        result_meta["report_size_chars"] = int(partial_state.get("report_size_chars") or len(report))
    return status, report, error, result_meta


def _sync_task_failure_outcome(
    exc: BaseException, partial_state: dict[str, Any]
) -> tuple[str, str, str, dict[str, Any]]:
    """同步执行被取消或基础设施异常时，仍按统一终态收口。"""
    partial_report, result_meta = _capture_partial_background_result(partial_state)
    if isinstance(exc, asyncio.CancelledError):
        return TASK_STATUS_CANCELLED, _append_partial_report("同步子智能体已取消。", partial_report), "", result_meta
    error = str(exc) or exc.__class__.__name__
    report = f"同步子智能体执行异常：{_truncate_text(error, 300)}"
    logger.error("[tools] 同步子智能体执行异常: %s", exc, exc_info=True)
    return TASK_STATUS_FAILED, _append_partial_report(report, partial_report), error, result_meta


_SYNC_TASK_CANCEL_REASONS = frozenset({"subagent_task_cancelled", "subagent_task_closed"})


def _is_sync_task_cancellation(exc: BaseException) -> bool:
    """区分任务账本取消与父 Run 取消；父 Run 取消必须继续向外传播。"""
    return isinstance(exc, asyncio.CancelledError) and str(exc) in _SYNC_TASK_CANCEL_REASONS


async def _register_sync_task(
    *,
    description: str,
    subagent_type: str,
    label: str | None,
    write_scopes: list[str] | None,
    runtime: ToolRuntime,
    run_controls: Any,
) -> tuple[SubagentTaskPort, SubagentTaskView] | None:
    """登记并启动同步任务；账本不可用时返回 None 由调用方降级执行。"""
    service = get_subagent_task_service()
    conversation_id, user_id, operator_name, parent_run_id = _read_conversation_context(runtime, run_controls)
    if service is None or not user_id:
        logger.debug(
            "[tools] 同步子智能体缺少任务账本或用户身份，按无账本模式执行: conversation_id=%s",
            conversation_id,
        )
        return None
    try:
        record = await service.create_task(
            conversation_id=conversation_id,
            user_id=user_id,
            parent_run_id=parent_run_id,
            subagent_type=subagent_type,
            description=description,
            label=label,
            parent_tool_call_id=str(getattr(runtime, "tool_call_id", "") or ""),
            parent_owner_token=str(getattr(run_controls, "owner_token", None) or ""),
            operator_name=operator_name,
        )
    except SubagentTaskCapacityError as exc:
        _raise_tool_error(str(exc))
    except Exception:
        # 同步链路的首要契约是把结论带回当前轮；账本故障不能阻断业务执行。
        logger.error(
            "[tools] 同步子智能体任务登记失败，降级为无账本执行: conversation_id=%s type=%s",
            conversation_id,
            subagent_type,
            exc_info=True,
        )
        return None
    record = await _apply_task_write_scopes(service, record, write_scopes, log_label="同步子智能体")
    try:
        configured = await service.configure_task_execution(
            record.task_id,
            record.owner_token or "",
            execution_mode="sync",
        )
        started = await service.start_task(record.task_id, record.owner_token or "")
    except Exception:
        logger.error("[tools] 同步子智能体任务启动失败，降级为无账本执行: task_id=%s", record.task_id, exc_info=True)
        await _close_aborted_sync_task(service, record)
        return None
    if configured is None:
        await _close_aborted_sync_task(service, record)
        _raise_tool_error("同步子智能体任务启动失败：任务已被取消或归属发生变化。")
    if started is None:
        await _close_aborted_sync_task(service, record)
        _raise_tool_error("同步子智能体任务启动失败：任务已被取消或归属发生变化。")
    return service, started


async def _run_registered_sync_task(
    service: SubagentTaskPort,
    started: SubagentTaskView,
    specs: list[SubagentSpec],
    compiled: dict[str, Any],
    subagent_type: str,
    description: str,
    runtime: ToolRuntime,
    history_loader: Callable[[], Awaitable[list[Any]]] | None = None,
) -> Any:
    """执行已登记的同步任务：过程双写、心跳续租、统一收口。"""

    event_writer = service.create_event_writer(started)
    await event_writer.start()
    parent_writer = _get_subagent_stream_writer(True, get_stream_writer)
    writer = _tee_stream_writers(event_writer, parent_writer)
    progress: dict[str, Any] = {"phase": "running"}
    partial_state: dict[str, Any] = {}
    heartbeat = asyncio.create_task(
        _watch_background_task(
            started.task_id,
            started.owner_token or "",
            progress,
            asyncio.current_task(),
            service,
        ),
        name=f"chatdata-subagent-sync-watch-{started.task_id}",
    )
    command: Any = None
    failure: BaseException | None = None
    history: list[Any] | None = None
    try:
        if history_loader is not None:
            history = await history_loader()
        command = await _build_task_tool_impl(
            specs,
            compiled,
            subagent_type,
            description,
            runtime,
            stream_events=True,
            stream_writer=writer,
            # 父上下文仍按原口径外置超长报告；完整原文由账本保存。
            offload_report=True,
            progress_callback=lambda snapshot: progress.update(snapshot),
            state_sink=partial_state,
            execution=_TaskExecution(
                background_task_id=started.task_id,
                background_agent_session_id=started.agent_session_id,
                background_owner_token=started.owner_token,
                history=history,
            ),
        )
    except BaseException as exc:
        failure = exc
    finally:
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat
        try:
            await event_writer.aclose()
        except Exception:
            logger.warning("[tools] 同步子智能体过程流关闭失败: task_id=%s", started.task_id, exc_info=True)

    if failure is None:
        status, report, error, result_meta = _sync_task_outcome(command, partial_state)
    else:
        status, report, error, result_meta = _sync_task_failure_outcome(failure, partial_state)
    try:
        await service.finish_task(
            started.task_id,
            started.owner_token or "",
            status=status,
            report=report,
            error=error,
            result_meta=result_meta,
            history=_session_history_payload(started, history or [], partial_state),
        )
    except Exception:
        # 账本收口失败会由租约回收兜底；不能把已经拿到的同步结论丢掉。
        logger.error("[tools] 同步子智能体任务收口失败: task_id=%s", started.task_id, exc_info=True)
    if failure is not None:
        if _is_sync_task_cancellation(failure):
            # 任务账本取消只影响这一个子智能体调用，父 Run 继续处理即可；
            # 父 Run 自身的 CancelledError 没有该标记，仍走下面的原样上抛。
            msg = ToolMessage(
                content=report or "同步子智能体已取消。", tool_call_id=runtime.tool_call_id or "", status="error"
            )
            return Command(update={"messages": [msg]})
        raise failure
    return command


async def _run_sync_task(
    *,
    description: str,
    subagent_type: str,
    label: str | None,
    write_scopes: list[str] | None,
    runtime: ToolRuntime,
    run_controls: Any,
    specs: list[SubagentSpec],
    compiled: dict[str, Any],
) -> Any:
    """同步执行也登记统一任务账本，但不派生完成通知、不唤醒会话。"""
    registered = await _register_sync_task(
        description=description,
        subagent_type=subagent_type,
        label=label,
        write_scopes=write_scopes,
        runtime=runtime,
        run_controls=run_controls,
    )
    if registered is None:
        return await _build_task_tool_impl(specs, compiled, subagent_type, description, runtime)
    service, started = registered
    return await _run_registered_sync_task(
        service,
        started,
        specs,
        compiled,
        subagent_type,
        description,
        runtime,
    )


async def _start_task_action(
    *,
    description: str | None,
    subagent_type: str | None,
    background: bool,
    label: str | None,
    write_scopes: list[str] | None,
    runtime: ToolRuntime,
    run_controls: Any,
    specs: list[SubagentSpec],
    compiled: dict[str, Any],
    background_cache: _BackgroundRunnableCache,
) -> Command | str:
    if not description or not description.strip():
        _raise_tool_error("dispatch 需要提供 description（任务书）。")
    if not subagent_type or not subagent_type.strip():
        _raise_tool_error("dispatch 需要提供 subagent_type。")
    if compiled.get(subagent_type) is None:
        allowed = ", ".join(f"`{name}`" for name in compiled)
        _raise_tool_error(f"无法调用子智能体 {subagent_type}：类型不存在，可用类型为 {allowed}")
    if not background:
        return await _run_sync_task(
            description=description,
            subagent_type=subagent_type,
            label=label,
            write_scopes=write_scopes,
            runtime=runtime,
            run_controls=run_controls,
            specs=specs,
            compiled=compiled,
        )
    background_runnables = background_cache.get()
    return await _spawn_background_task(
        subagent_type=subagent_type,
        description=description,
        label=label,
        write_scopes=write_scopes,
        runtime=runtime,
        run_controls=run_controls,
        runner_factory=lambda record, snapshot: _run_background_task(record, snapshot, specs, background_runnables),
    )


def _build_approval_resume_value(pending: Any, decision: str) -> dict[str, Any] | None:
    """把主智能体的 allow/deny 归一成审批中间件期望的 answers 结构。"""
    label = "拒绝" if decision == "deny" else "允许"
    items = pending.get("approval_items") if isinstance(pending, dict) else None
    if isinstance(items, list) and len(items) > 1:
        answers = [
            {"id": str(item.get("call_id") or ""), "selected": [label]}
            for item in items
            if isinstance(item, dict) and item.get("call_id")
        ]
        return {"answers": answers} if answers else None
    if isinstance(pending, dict) and pending.get("kind") == "approval":
        return {"answers": [{"id": "approval", "selected": [label]}]}
    return None


async def _control_task_action(
    *,
    action: str,
    task_id: str | None,
    message: str | None,
    decision: str,
    runtime: ToolRuntime,
    run_controls: Any,
    specs: list[SubagentSpec],
    background_runnables: dict[str, Any],
) -> str:
    service = get_subagent_task_service()
    if service is None:
        return "子智能体控制失败：任务账本服务未初始化。"
    normalized = str(task_id or "").strip()
    if not normalized:
        return f"{action} 需要提供 task_id。"
    conversation_id, user_id, operator_name, _ = _read_conversation_context(runtime, run_controls)
    if not user_id:
        return "无法操作子智能体：当前会话缺少用户身份。"
    target = await service.get_task(normalized, conversation_id=conversation_id, user_id=user_id)
    if target is None:
        return f'子智能体任务 {normalized} 不存在。可用 subagent(manage={{"action": "list"}}) 查看当前会话任务。'
    if action == "steer":
        return await _control_steer(
            service,
            target,
            normalized,
            conversation_id=conversation_id,
            user_id=user_id,
            message=message,
            operator_name=operator_name,
        )
    return await _control_resume(
        action,
        service,
        target,
        normalized,
        decision=decision,
        conversation_id=conversation_id,
        user_id=user_id,
        runtime=runtime,
        specs=specs,
        background_runnables=background_runnables,
    )


async def _control_steer(
    service: SubagentTaskPort,
    target: SubagentTaskView,
    task_id: str,
    *,
    conversation_id: str,
    user_id: str,
    message: str | None,
    operator_name: str,
) -> str:
    status_label = TASK_STATUS_LABELS.get(target.status, target.status)
    capabilities = resolve_task_capabilities(target)
    if capabilities.can_follow_up:
        return f"任务已是{status_label}，不能再插话；需要继续分析请用 send。"
    if not capabilities.can_steer and str(getattr(target, "execution_mode", "") or "") == "sync":
        # 同步任务使用前台编译版子图，没有 SubagentControlMiddleware；排队后永远
        # 不会被消费，必须直接拒绝，避免主智能体误以为插话已生效。
        return "同步任务在父 Run 内阻塞执行，不支持插话；请等待本轮结果，结束后用 send 续写。"
    if not capabilities.can_steer:
        return f"任务当前为{status_label}，不能插话；请等待状态变化后重试。"
    if not str(message or "").strip():
        return "send 需要提供 message。"
    record = await service.enqueue_steer_message(
        task_id,
        conversation_id=conversation_id,
        user_id=user_id,
        message=str(message),
        operator_name=operator_name,
    )
    if record is None:
        return "插话未送达：任务已结束或状态已变化。"
    if record.status in WAITING_APPROVAL_TASK_STATUSES:
        return "插话已排队，将在审批完成并继续执行后、下一次模型调用前注入。"
    return "插话已排队，将在子智能体下一次模型调用前注入。"


async def _control_resume(
    action: str,
    service: SubagentTaskPort,
    target: SubagentTaskView,
    task_id: str,
    *,
    decision: str,
    conversation_id: str,
    user_id: str,
    runtime: ToolRuntime,
    specs: list[SubagentSpec],
    background_runnables: dict[str, Any],
) -> str:
    status_label = TASK_STATUS_LABELS.get(target.status, target.status)
    capabilities = resolve_task_capabilities(target)
    if not (capabilities.can_approve or capabilities.can_reject):
        return f"任务当前为{status_label}，不需要 {action}。只有 waiting_approval 状态可用 decide。"
    pending = target.meta.get("pending_interrupt")
    resume_value, verdict, error = _prepare_resume(action, target.status, pending, decision)
    if error:
        return error
    resumed = await service.resume_task(
        task_id,
        conversation_id=conversation_id,
        user_id=user_id,
        resume_value=resume_value,
    )
    if resumed is None:
        return "恢复失败：任务状态已变化，请先 check 最新状态。"
    record, value = resumed
    service.register_recovery_runner(
        lambda resumed_record, snapshot: _run_background_task(
            resumed_record,
            snapshot,
            specs,
            background_runnables,
        ),
        task_id=record.task_id,
    )
    _launch_task_runner(
        service,
        record,
        runtime,
        lambda resumed_record, snapshot: _run_background_task(
            resumed_record,
            snapshot,
            specs,
            background_runnables,
            resumed=True,
            resume=bool(pending),
            resume_value=value,
        ),
    )
    return f"子智能体任务 {task_id} {verdict}，已在后台继续执行；完成/失败仍会按原方式通知当前会话。"


def _prepare_resume(
    action: str,
    status: str,
    pending: Any,
    decision: str,
) -> tuple[dict[str, Any] | None, str, str]:
    """校验待审批状态并构造恢复值；返回 (resume_value, verdict, error)。"""
    status_label = TASK_STATUS_LABELS.get(status, status)
    if action not in {"approve", "reject"}:
        return None, "", f"不支持的控制动作：{action}。"
    if status != TASK_STATUS_WAITING_APPROVAL:
        return None, "", f"任务当前为{status_label}，没有待审批操作。"
    effective_decision = "deny" if action == "reject" else ("allow" if action == "approve" else decision)
    resume_value = _build_approval_resume_value(pending, effective_decision)
    if resume_value is None:
        return None, "", "待审批载荷缺失或格式异常，无法恢复；可取消后重新派发。"
    return resume_value, "已拒绝" if effective_decision == "deny" else "已批准", ""


@dataclass(frozen=True)
class _TaskToolContext:
    """单工具运行期共享依赖；把闭包提到模块级，避免 build_task_tool 复杂度堆叠。"""

    specs: list[SubagentSpec]
    compiled: dict[str, Any]
    background_cache: _BackgroundRunnableCache
    run_controls: Any


async def _tool_dispatch(ctx: _TaskToolContext, runtime: ToolRuntime, args: DispatchArgs) -> Command | str:
    return await _start_task_action(
        description=args.description,
        subagent_type=args.subagent_type,
        background=args.background,
        label=args.label,
        write_scopes=_validate_tool_write_scopes(args.write_scopes),
        runtime=runtime,
        run_controls=ctx.run_controls,
        specs=ctx.specs,
        compiled=ctx.compiled,
        background_cache=ctx.background_cache,
    )


async def _tool_send(ctx: _TaskToolContext, runtime: ToolRuntime, args: SendArgs) -> Command | str:
    """统一 follow_up 与 steer：按任务当前状态自动分流。"""
    service = get_subagent_task_service()
    if service is None:
        return "子智能体消息发送失败：任务账本服务未初始化。"
    normalized = str(args.task_id or "").strip()
    conversation_id, user_id, _operator_name, _parent_run_id = _read_conversation_context(runtime, ctx.run_controls)
    if not user_id:
        return "无法向子智能体发送消息：当前会话缺少用户身份。"
    target = await service.get_task(normalized, conversation_id=conversation_id, user_id=user_id)
    if target is None:
        return f'子智能体任务 {normalized} 不存在。可用 subagent(manage={{"action": "list"}}) 查看当前会话任务。'
    status_label = TASK_STATUS_LABELS.get(target.status, target.status)
    if target.status in WAITING_APPROVAL_TASK_STATUSES:
        _raise_tool_error(f"任务当前为{status_label}，请先用 subagent(decide={{...}}) 批准或拒绝待执行工具。")
    if target.status in TERMINAL_TASK_STATUSES:
        return await _follow_up_task_action(
            task_id=normalized,
            message=args.message,
            runtime=runtime,
            run_controls=ctx.run_controls,
            specs=ctx.specs,
            background=True if args.background is None else args.background,
            write_scopes=args.write_scopes,
            compiled=ctx.compiled,
            background_runnables=ctx.background_cache.get(),
        )
    if target.status in ACTIVE_TASK_STATUSES:
        if args.background is not None:
            _raise_tool_error("目标子智能体仍在运行，send 不能传 background；运行中消息会直接转为插话。")
        if args.write_scopes is not None:
            _raise_tool_error("目标子智能体仍在运行，send 不能传 write_scopes；写范围只在任务结束后开启下一轮时使用。")
        return await _control_task_action(
            action="steer",
            task_id=normalized,
            message=args.message,
            decision="allow",
            runtime=runtime,
            run_controls=ctx.run_controls,
            specs=ctx.specs,
            background_runnables=ctx.background_cache.get(),
        )
    _raise_tool_error(
        f'任务当前为{status_label}，不支持发送消息；请先用 subagent(manage={{"action": "check"}}) 确认状态。'
    )


async def _tool_manage(_ctx: _TaskToolContext, runtime: ToolRuntime, args: ManageArgs) -> str:
    if args.agent_id and args.action != "list":
        return 'agent_id 仅用于 manage action="list" 查询任务明细；check/cancel 等动作请传 task_id。'
    return await manage_async_task(
        args.action,
        runtime=runtime,
        task_id=args.task_id,
        task_ids=args.task_ids,
        agent_id=args.agent_id,
        status=args.status,
        limit=200,
        wait_seconds=args.wait_seconds,
    )


async def _tool_decide(ctx: _TaskToolContext, runtime: ToolRuntime, args: DecideArgs) -> str:
    return await _control_task_action(
        action="approve" if args.decision == "allow" else "reject",
        task_id=args.task_id,
        message=None,
        decision=args.decision,
        runtime=runtime,
        run_controls=ctx.run_controls,
        specs=ctx.specs,
        background_runnables=ctx.background_cache.get(),
    )


async def _route_subagent_tool(
    ctx: _TaskToolContext,
    runtime: ToolRuntime,
    dispatch: DispatchArgs | None = None,
    send: SendArgs | None = None,
    manage: ManageArgs | None = None,
    decide: DecideArgs | None = None,
) -> Command | str:
    if dispatch is not None:
        return await _tool_dispatch(ctx, runtime, dispatch)
    if send is not None:
        return await _tool_send(ctx, runtime, send)
    if manage is not None:
        return await _tool_manage(ctx, runtime, manage)
    assert decide is not None
    return await _tool_decide(ctx, runtime, decide)


class _SubagentStructuredTool(StructuredTool):
    """允许 LangGraph 注入运行时参数，同时保持模型参数 schema 严格。"""

    def _parse_input(self, tool_input: str | dict[str, Any], tool_call_id: str | None) -> str | dict[str, Any]:
        if not isinstance(tool_input, dict):
            return super()._parse_input(tool_input, tool_call_id)
        injected_keys = self._injected_args_keys
        injected = {key: tool_input[key] for key in injected_keys if key in tool_input}
        parsed = super()._parse_input(
            {key: value for key, value in tool_input.items() if key not in injected_keys},
            tool_call_id,
        )
        if isinstance(parsed, dict):
            parsed.update(injected)
        return parsed


def build_task_tool(specs: list[SubagentSpec], run_controls: Any = None) -> StructuredTool:
    """构建单一 ``subagent`` 工具：四组主参数按生命周期分层。"""
    validate_subagent_specs(specs)
    compiled = {spec.name: _compile_subagent(spec, run_controls=run_controls) for spec in specs}
    ctx = _TaskToolContext(
        specs=specs,
        compiled=compiled,
        background_cache=_BackgroundRunnableCache(specs),
        run_controls=run_controls,
    )
    available = "\n".join(f"- {spec.name}: {spec.description}" for spec in specs)

    async def subagent(
        runtime: ToolRuntime,
        dispatch: DispatchArgs | None = None,
        send: SendArgs | None = None,
        manage: ManageArgs | None = None,
        decide: DecideArgs | None = None,
    ) -> Command | str:
        return await _route_subagent_tool(ctx, runtime, dispatch, send, manage, decide)

    return _SubagentStructuredTool.from_function(
        name=SUBAGENT_TOOL_NAME,
        coroutine=subagent,
        description=_SUBAGENT_TOOL_DESCRIPTION_TEMPLATE.replace("{available_agents}", available),
        args_schema=SubagentToolArgs,
    )


class SubagentMiddleware(AgentMiddleware):
    """向主智能体注册子智能体工具，并在系统提示追加使用说明。"""

    def __init__(
        self, specs: list[SubagentSpec], *, system_prompt: str | None = None, run_controls: Any = None
    ) -> None:
        validate_subagent_specs(specs)
        self.specs = list(specs)
        self.subagent_names = frozenset(spec.name for spec in specs)
        self.tools = [build_task_tool(specs, run_controls=run_controls)]
        agents_desc = "\n".join(f"- {spec.name}: {spec.description}" for spec in specs)
        # 用 replace 而不是 format：提示词里的调用示例含花括号，format 会误解析。
        self.system_prompt = (system_prompt or _default_system_prompt()).replace("{available_agents}", agents_desc)

    def wrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelCallResult]
    ) -> ModelCallResult:
        return handler(_append_system_prompt(request, self.system_prompt))

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelCallResult]]
    ) -> ModelCallResult:
        # 提示词保持静态：动态子智能体状态不拼进 system，避免每轮改写前缀导致
        # provider 前缀缓存整段失效；数量与任务明细由 subagent(manage=...) 现查。
        return await handler(_append_system_prompt(request, self.system_prompt))


def _append_system_prompt(request: ModelRequest, extra: str) -> ModelRequest:
    """向请求的系统提示追加子智能体使用说明，保持 SystemMessage 类型。"""
    system_message = getattr(request, "system_message", None)
    if system_message is None:
        return request.override(system_message=SystemMessage(content=extra))
    merged = str(getattr(system_message, "content", "") or "").rstrip()
    return request.override(
        system_message=SystemMessage(
            content=f"{merged}\n\n{extra}",
            additional_kwargs=dict(getattr(system_message, "additional_kwargs", {}) or {}),
        )
    )


def _default_system_prompt() -> str:
    return (
        "## 子智能体委派\n\n"
        "你可以通过 subagent 工具的 dispatch 参数把复杂多步任务派发给隔离上下文的子智能体，"
        "只有子智能体的最终结论会回到对话。可用类型：\n\n{available_agents}\n\n"
        "派发判断：预计需要多轮工具调用（如批量盘点、成稿写作）且过程中无需"
        "用户参与的任务才派发；简单查询和需要向用户提问、展示中间过程的工作"
        "直接自己完成。派发前必须先用 file_write 把子智能体需要的背景事实写入 workspace，"
        "任务书里给文件路径——子智能体看不到对话历史，只存在于对话中的信息它拿不到。\n\n"
        "类型选择：依赖 MCP 数据或需要落盘产出的任务派 general，纯数据事实核对才派 "
        "explore，不要选错类型。\n\n"
        "## 子智能体复用与额度\n\n"
        "每个主对话创建的子智能体总数有运行时上限。派发新任务前先用 "
        'subagent({"manage":{"action":"list"}}) 检查已有子智能体：目标相同、数据范围相同，或交付物只是补充、'
        '修订时不要新开：同类子智能体仍在执行时，用 subagent({"send":{"task_id":"...","message":"..."}}) 补充或等待；'
        "同步任务阻塞当前轮，不能发送运行中消息，等结果返回后再 send 续写；"
        "任务已结束时 send 会对最近一个终态 task_id 开启下一轮，沿用同一子会话；"
        "当前轮必须拿到追问结论时传 background=false。"
        "只有目标、范围或交付物明确不同才用 dispatch 新建。"
        "达到上限后 dispatch 会被拒绝，此时复用已有子智能体、由主智能体自己完成，"
        "或提示用户在子智能体管理面板关闭不再使用的终态子智能体以腾出额度；"
        "send 续写沿用原会话，不新增子智能体数量。\n\n"
        "## 并发写与任务分发\n\n"
        "并发写由你负责任务分发，不依赖系统写锁。会写 workspace 的 general 任务，"
        "dispatch 或任务结束后 send 开启下一轮时必须用 write_scopes 声明工作区相对文件或目录前缀，例如 "
        "outputs/word、outputs/ppt；explore 只读，不声明写范围。并行派发前先 "
        'subagent({"manage":{"action":"list"}}) 查看活跃任务的写范围：范围不相交才可并行；重叠或任一任务'
        "未声明写范围时按可能冲突处理，改为串行。shell、git、依赖安装、构建、打包等全局"
        "写操作传 workspace:*，同一时间只跑一个。子智能体必须只改声明范围；任务完成后"
        "由你检查产出、review diff 并承担最终合并责任。\n\n"
        "## 子智能体执行方式\n\n"
        "默认异步：批量盘点/全量扫描、多份成稿/多交付物、多个可并行且彼此独立的子任务，"
        "或当轮不需要其结论时，直接用 dispatch 派发（默认 background=true）；立即返回持久 task_id，"
        "期间继续其他工作或直接结束本轮，完成/失败/超时/租约失联都会自动通知当前会话。"
        "只在当前步骤必须拿到结论才能继续、用户要求当轮给出结果，或只是小范围核对/单点查询时，"
        "显式传 background=false 同步等待；已结束子智能体的 send 同样支持该参数。"
        "任务查询、等待和取消用 manage；给已有子会话发消息用 send；审批用 decide。"
        "查询按 list → agent_id → task_id 分层。"
        "manage 的 check/list 会返回「可操作」，只执行其中列出的动作；同步任务不支持运行中插话和审批。"
        "用户问数量或进展时现查，不要凭记忆回答；问之前的任务先 list，再按需 check。"
        "给任务写清 label，便于用户在任务列表识别。"
        "子智能体产出的交付物，用 present_artifacts 展示给用户。"
    )
