"""ChatData 子智能体：单一 ``task`` 工具 + 上下文隔离。

参考 deepagents ``SubAgentMiddleware`` 的三个核心机制（单 task 工具、状态裁剪
透传、Command 回传结论），并按 ChatData 的事件流与状态设计做了裁剪：

- 子智能体是无状态的一次性调用：只看到任务书（HumanMessage），不继承主对话历史。
- 仅白名单状态键进入子智能体（身份/数据域/工作区），主图的压缩账本与 token
  用量不透传，子图消息永远不回流——主对话上下文只增加一条 ToolMessage 结论。
  子图内部挂载与主图同源的压缩中间件（见 ``_compile_subagent``）：上下文逼近
  触发线时先压缩续命，预算告警只在压缩后仍超线时才注入，两层口径同源。
- 子图调用显式切断回调继承（``callbacks=[]``），子图内的模型 token 不进入
  主 SSE 流；内层事件通过 ``_run_subagent_with_events`` 挑选后经父图 custom
  流转发（``parent_tool_call_id`` 挂到所属 task 调用下），前端在 task 卡片内
  展示调用过程。文本与思考以 ``messages`` 模式做 token 级增量转发
  （``subagent_message_delta`` / ``subagent_thinking_delta``），工具生命周期
  仍以 ``updates`` 模式为准。
- 子智能体不注册 ``ask_user_question``，第一版不支持嵌套人工中断。
"""

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from itertools import count
from typing import Annotated, Any

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, AgentState, ModelCallResult, ModelRequest, ToolErrorMiddleware
from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, RemoveMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.config import get_stream_writer
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.types import Command
from pydantic import BaseModel, Field

from apps.chat_data_app.services.runtime_config import (
    SUBAGENT_BUDGET_ALERT_TRIGGER_RATIO,
    SUBAGENT_MESSAGE_PREVIEW_MAX_CHARS,
    SUBAGENT_RESULT_PREVIEW_MAX_CHARS,
    SUBAGENT_THINKING_PREVIEW_MAX_CHARS,
)
from apps.chat_data_app.services.setting_service import get_runtime_setting
from apps.chat_data_app.sse_protocol import (
    SSE_EVENT_SUBAGENT_CONTEXT_USAGE,
    SSE_EVENT_SUBAGENT_MESSAGE,
    SSE_EVENT_SUBAGENT_MESSAGE_DELTA,
    SSE_EVENT_SUBAGENT_THINKING_DELTA,
    SSE_EVENT_TOOL_COMPLETED,
    SSE_EVENT_TOOL_FAILED,
    SSE_EVENT_TOOL_STARTED,
)
from utils.logger import get_logger
from utils.tool_call_args import coerce_tool_call_args

from ..agent_runtime import on_tool_error
from ..agent_state import merge_context_compression_events
from ..context.context_budget import (
    build_runtime_context_summary_message,
    count_messages_tokens,
    count_tools_tokens,
    estimate_text_tokens,
    get_model_context_budget,
)
from ..tools.registry import TOOL_REQUIRED_SKILLS
from .context_compression import create_context_compression_middleware
from .skill_prerequisite import SkillPrerequisiteMiddleware
from .tool_call_normalizer import create_tool_call_name_normalizer_middleware
from .tool_execution_fence import create_tool_execution_fence_middleware
from .tool_result import create_tool_result_middleware

logger = get_logger(__name__)

# 过程外发上限的口径说明见 services/runtime_config.py：工具结果上限与
# TOOL_RESULT_OFFLOAD_SIZE 对齐（子图同样挂载 ToolResultMiddleware，超限内容
# 已被替换成信封），正文/思考单独配置。三者都保留截断逻辑作为安全兜底，
# 正常路径下不会触发——过程区的意图就是让用户看到子智能体实际做了什么。

SUBAGENT_STATE_PASSTHROUGH_KEYS = frozenset(
    {
        "user_id",
        "operator_name",
        "conversation_id",
        "execution_context",
        "workspace",
    }
)
"""允许从主对话复制进子智能体的状态键白名单（身份/数据域/工作区上下文）。

主图的压缩账本（context_compression_events / context_summary）、token_usage
等一律不透传：前者带自定义 reducer，回流会污染主对话的流重试去重；后者是
主对话的记账口径。子图挂压缩中间件后在 ``ChatDataSubAgentState`` 里维护
自己的压缩账本，与主图互不影响。"""

TASK_TOOL_NAME = "task"

_TASK_TOOL_DESCRIPTION_TEMPLATE = """派发一个隔离上下文的子智能体处理复杂多步任务，只把最终结论带回主对话：
派发的价值是保护主对话上下文——大量文件/数据读取与中间转储留在子图内消化，
主对话只保留结论，不要为省事把子智能体能完成的工作搬到主对话自己重做。

可用子智能体类型：
{available_agents}

使用要求：
- 子智能体没有记忆，每次派发都是全新上下文，只能看到 description，看不到主对话
  历史和用户消息：任务书必须自带全部上下文，
  包含 ① 目标 ② 已知事实与相关文件路径 ③ 期望的产出格式 ④ 约束与禁止事项，
  并写明任务意图（研究盘点 / 分析 / 产出交付物）——子智能体看不到用户的原始意图。
- 适合派发：需要读取多个文件或大体量数据的盘点/调研、可并行且互不依赖的子任务；
  不适合派发：已知位置的单点查询、需要向用户提问或展示中间过程的步骤——直接自己做。
- 已派发的任务不要自己重复执行，等回传结果；回传不理想时先分析原因再决定补派，
  不要原样重发同一个任务书。
- 子智能体同样看得到会话技能清单（<available_skills>），但看不到主对话历史：
  任务与本轮技能（尤其用户自定义技能）直接相关时，任务书仍应写明技能名称与
  skill_file 路径，帮助子智能体选对技能、不走偏。
- 子智能体同样看得到数据上下文（数据源组说明与已选执行对象清单），查询类任务
  的 member_datasource_ids 优先按清单选择；任务只涉及其中部分成员时，任务书仍应
  写明本次限定范围，避免子智能体扩大查询口径。
- 大块背景事实（盘点结果、此前查询结论、长段分析等）必须先落盘：用 file_write 写入
  workspace，任务书里给文件路径让子智能体自己读取；只存在于对话中、没写进文件的信息
  子智能体拿不到，禁止在任务书里写"已完成 XX"却不给落盘路径。
- 派发前确认任务所需动作与子智能体能力匹配：data-explore 是只读角色，没有任何
  写盘工具，任务书不得要求它写文件——需要落盘的交付物由主智能体在收到回传后
  自己写，或改派具备写盘能力的 general-purpose（通用执行）。
- 只有子智能体的最终回复会回到主对话，中间过程用户不可见；回传内容要完整自含。
- 回传是给你看的结论，不是给用户的：向用户汇报时用自己的话转述关键结论与产物路径，
  不要把子智能体的回传原文原样粘贴给用户。
- 子智能体没有向用户展示文件的工具，只负责把产物写入 workspace；主智能体收到回传后，
  用 present_artifacts 等方式向用户展示交付物。
- 相互独立的子任务可以在一条消息里并行发起多个 task 调用。
- 需要向用户提问或展示中间过程的工作不要派发，直接自己做。"""


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


class TaskToolArgs(BaseModel):
    """``task`` 工具入参。"""

    description: str = Field(
        min_length=1,
        max_length=8000,
        description=(
            "给子智能体的完整任务书：背景、要做什么、期望的交付格式。"
            "子智能体看不到主对话，所有必要上下文都必须写在里面。"
        ),
    )
    subagent_type: str = Field(
        min_length=1,
        max_length=64,
        description="子智能体类型，必须是工具描述中列出的类型之一。",
    )


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
    # 预算告警的口径来源：默认与主图同一 model_id，取同一 ContextBudget 的压缩触发
    # 阈值作为「上下文将尽」的告警线（与主图同源）；压测/调试可用
    # CHAT_DATA_SUBAGENT_BUDGET_ALERT_TRIGGER_RATIO 单独覆盖子图告警线
    # （窗口比例口径，只影响子图，主图压缩不受影响，见 runtime_config.py 常量注释）。
    model_id: str | None = None
    # None 表示跟随调用方传入的运行期配置（CHAT_DATA_AGENT_RECURSION_LIMIT，
    # 主图同源）；在 dataclass 默认值里直接写死常量会在 import 时固化取值，
    # 运行时改配置表 / env / monkeypatch 都不生效。
    recursion_limit: int | None = None


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


# 模型在无工具轮次把「本来要发的工具调用」当正文输出的残留标记（Qwen 系
# 在 tools=[] 请求下的已知退化形态，实测见 2026-09-20 强制收尾首验）：
# <tool_call>...</tool_call> 整块，或只有开头没有结尾的悬空块。
_TOOL_CALL_MARKUP_RE = re.compile(r"<tool_call\b[^>]*>.*?(?:</tool_call>|$)", re.DOTALL)


def _sanitize_report_text(text: str) -> str:
    """剥掉正文里的裸工具调用标记，避免垃圾 XML 混进回传主智能体的报告。"""
    return _TOOL_CALL_MARKUP_RE.sub("", str(text or "")).strip()


_TOOL_CALL_OPENER = "<tool_call"
_TOOL_CALL_CLOSER = "</tool_call>"


class _StreamingToolCallMarkupFilter:
    """流式剥除正文增量里的裸 ``<tool_call>`` 标记（整段版 ``_sanitize_report_text``
    的流式对应物）。

    硬线摘工具轮次模型会把待发工具调用退化成正文标记（Qwen 系已知形态），
    该文本会经 ``subagent_message_delta`` 逐 token 流给前端——标记可能被切成
    任意小的碎片（``<`` / ``<tool`` / ``_call>``…），必须跨增量缓冲匹配。

    状态机三态：
    - normal：正常透传，遇到 ``<`` 进入 partial 判定；
    - partial：缓冲尾巴可能是 opener 前缀，暂扣不发；确认非 opener 后原样补发；
    - inside：确认进入标记块，静默吞掉直到 closer 完整出现（closer 自身可能
      被切碎，同样用后缀缓冲容忍）。

    消息结束 ``flush``：partial 未决说明只是普通 ``<`` 文本，原样补发；inside
    未闭合按悬空块处理吞到末尾（与整段版口径一致）。已知取舍：flush 出的
    残片不再作为增量补发（最多丢 opener 前缀长度的普通文本，且仅发生在
    标记被切碎的退化轮次），换取直播内容零垃圾 XML。
    """

    def __init__(self) -> None:
        self._inside = False
        self._pending = ""

    @staticmethod
    def _partial_suffix(text: str, token: str) -> str:
        """返回 text 末尾与 token 前缀重合的最长后缀（可能被切碎的 token 暂扣）。"""
        for length in range(min(len(text), len(token) - 1), 0, -1):
            if text.endswith(token[:length]):
                return text[-length:]
        return ""

    def feed(self, delta: str) -> str:
        out: list[str] = []
        text = self._pending + delta if self._pending else delta
        self._pending = ""
        while text:
            if self._inside:
                idx = text.find(_TOOL_CALL_CLOSER)
                if idx == -1:
                    self._pending = self._partial_suffix(text, _TOOL_CALL_CLOSER)
                    text = ""
                else:
                    text = text[idx + len(_TOOL_CALL_CLOSER) :]
                    self._inside = False
                continue
            idx = text.find("<")
            if idx == -1:
                out.append(text)
                break
            out.append(text[:idx])
            text = text[idx:]
            if _TOOL_CALL_OPENER.startswith(text):
                # 可能是 opener 的前缀（如 "<"、"<tool"），暂扣等下一个增量裁决
                self._pending = text
                text = ""
            elif text.startswith(_TOOL_CALL_OPENER):
                self._inside = True
                text = text[len(_TOOL_CALL_OPENER) :]
            else:
                # 与 opener 无关的普通 "<" 文本，透传后继续扫描
                out.append("<")
                text = text[1:]
        return "".join(out)

    def flush(self) -> str:
        pending, self._pending = self._pending, ""
        if self._inside:
            return ""  # 悬空标记块：吞到末尾
        if len(pending) >= len(_TOOL_CALL_OPENER):
            return ""  # 正好停在 opener 全串上，按标记块开头处理
        return pending


def _extract_final_report(messages: list) -> str:
    """取子图最后一个非空 AIMessage 文本作为回传结论。"""
    for message in reversed(messages or []):
        if not isinstance(message, AIMessage):
            continue
        text = _sanitize_report_text(getattr(message, "text", "") or "")
        if text:
            return text
    return ""


_SUBAGENT_BUDGET_ALERT_PROMPT = """## 上下文预算提示

当前上下文估算已使用约 {used_tokens} tokens，达到本任务上限 {threshold_tokens} tokens（约 {percent}%）。请就地停止收尾：从本轮起不要再调用任何工具（包括 file_read、dir_list 与一切查询），也不要"先补读几条再收尾"——未完成的部分直接写进下方待完成清单，不缺证据。直接输出如下格式的收尾报告：

子智能体由于本轮上下文限制未能完成全部任务。
当前已完成任务为：
（逐项列出：做了什么、结果或数据、来源文件/模型/指标）
待完成任务为：
（逐项列出：还差什么、停在哪一步、已尝试的口径）

只汇报事实，不要替主智能体决定后续安排；收尾本身比多收集一条事实更重要。"""


def create_subagent_budget_alert_middleware(
    spec: SubagentSpec,
    tools: list,
    *,
    system_prompt: str | None = None,
) -> AgentMiddleware:
    """子图预算软约束中间件：上下文逼近上限时注入「提前收尾」提示。

    演进结论（2026-09-20 实测 → 09-21 双阈值试验 → 当日回归纯软约束）：
    - ``override(tools=[])`` 强制收尾实测会诱发 Qwen 系退化：模型把待发的
      工具调用意图输出成正文里的裸 ``<tool_call>`` 标记与「我还需要读取…」
      类无效叙述，报告质量反而不如不摘工具，且展示层要为此挂一整套流式
      净化补丁；
    - 最终定稿纯软约束 + 子图压缩（同日落地）：压缩中间件先于本件消费上下文
      增量（trigger=窗口 70% 压到 30%），本件只兜「压缩后仍超线」的残量
      （如摘要生成失败）；超线只注入收尾提示，**不摘工具**，是否收尾由模型
      自行决定；模型无视提示时由窗口溢出失败路径兜底（失败 ToolMessage
      同样盖 trail 快照与用量，主智能体可据失败原因降级接手）。
    每个超限轮次都注入（水位不降提示会重复出现，属预期）；低于阈值零注入。
    注入通道是隐藏 HumanMessage 追加在消息尾部（见 ``_with_budget_alert`` 内注释），
    不走 system prompt 追加。超限轮次同时设置 ``tool_choice="none"``（API 层禁止
    工具调用，双层收尾的保险丝；提示为引导层），工具 schema 保留在请求里。
    阈值默认与主图压缩触发阈值同源（同一 ``ContextBudget.compression_trigger_tokens``，
    同一 env 口径），可用 CHAT_DATA_SUBAGENT_BUDGET_ALERT_TRIGGER_RATIO
    按窗口比例单独覆盖（仅子图，主图不受影响）。

    另外负责把子图压缩摘要（state ``context_summary``）注入请求：压缩发生在
    before_model 节点改写 state，但摘要是给模型看的——必须以隐藏 HumanMessage
    前插进每次请求（与主图 ``ContextUsageMiddleware`` 同语义），且预算计数
    必须包含摘要 tokens，否则告警线漏算。只改当轮请求，不写 state、不落 checkpoint。
    """
    meta = _subagent_budget_meta(spec, tools, system_prompt=system_prompt)
    static_tokens = meta["static_tokens"]
    threshold = meta["threshold_tokens"]

    def _with_budget_alert(request: ModelRequest) -> ModelRequest:
        messages = list(getattr(request, "messages", None) or [])
        summary = _request_subagent_context_summary(request)
        if summary:
            summary_message = build_runtime_context_summary_message(summary)
            if summary_message is not None:
                # 摘要注入是无条件的（低于阈值也要注入，模型每轮都需要它续上下文）
                messages = [summary_message, *messages]
                request = request.override(messages=messages)
        used = static_tokens + count_messages_tokens(messages)
        if used < threshold:
            return request
        percent = min(int(used * 100 / threshold), 999)
        logger.info(
            "[subagent] 预算提示注入 spec=%s used=%s threshold=%s percent=%s%%",
            spec.name,
            used,
            threshold,
            percent,
        )
        # 注入通道=隐藏 HumanMessage 追加在消息尾部（2026-09-21 从 system prompt
        # 追加改来）：system 末尾的追加提示实测服从率低（模型触发后仍快速补读），
        # 用户角色 + 离最新轮次最近的位置注意力权重最高。只改当轮请求——注入消息
        # 不写 state、不进 checkpoint、不进直播/历史，每个超限轮次重新注入。
        alert_message = HumanMessage(
            content=_SUBAGENT_BUDGET_ALERT_PROMPT.format(
                used_tokens=used,
                threshold_tokens=threshold,
                percent=percent,
            ),
            additional_kwargs={"budget_alert": True, "hide_from_ui": True},
        )
        # 双层收尾（2026-10-10 接入）：①上方隐藏 HumanMessage=引导层；②tool_choice="none"
        # =保险丝——API 参数层禁止本轮工具调用，模型只能输出文本、自然走向总结。
        # 与 09-20/09-21 失败的 override(tools=[]) 本质不同：工具 schema 完整保留在请求里
        # （Qwen 裸 <tool_call> 正文退化的诱因是 schema 消失，本参数不触碰 schema）。
        # 服从性已实测通过（oMLX Qwen3.6-35B 三场景：诱导下不调工具、无退化；脚本与
        # 判据见 2026-10-10 日志）。防御纵深照留：_StreamingToolCallMarkupFilter +
        # recursion_limit。只改当轮请求，不写 state、不落 checkpoint。
        return request.override(messages=[*messages, alert_message], tool_choice="none")

    class _SubagentBudgetAlertMiddleware(AgentMiddleware):
        def wrap_model_call(
            self, request: ModelRequest, handler: Callable[[ModelRequest], ModelCallResult]
        ) -> ModelCallResult:
            return handler(_with_budget_alert(request))

        async def awrap_model_call(
            self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelCallResult]]
        ) -> ModelCallResult:
            return await handler(_with_budget_alert(request))

    return _SubagentBudgetAlertMiddleware()


def _request_subagent_context_summary(request: Any) -> str | None:
    """从当轮请求 state 读取子图运行态压缩摘要（无则 None）。"""
    state = getattr(request, "state", None)
    if not isinstance(state, dict):
        return None
    summary = str(state.get("context_summary") or "").strip()
    return summary or None


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
) -> dict[str, int]:
    """子图预算告警的公共口径：静态 token（system+tools）与告警阈值。

    预算告警中间件（决定何时注入收尾提示）与 ``subagent_context_usage``
    事件（推送给前端展示用量）共用同一份计算，保证「注入线」与「展示线」
    永远一致；阈值覆盖 env 只在这里生效一处。默认与压缩线同源（同一
    ``ContextBudget.compression_trigger_tokens``）；覆盖 env 用窗口比例口径
    （窗口 × 比例，clamp 到硬限），换模型窗口后配置不失真。
    ``system_prompt`` 传入增强后的实际 system prompt（含技能目录段），缺省用
    spec.system_prompt。
    """
    budget = get_model_context_budget(spec.model_id)
    if SUBAGENT_BUDGET_ALERT_TRIGGER_RATIO is not None:
        threshold_tokens = min(
            max(int(budget.context_window * SUBAGENT_BUDGET_ALERT_TRIGGER_RATIO), 1),
            max(int(budget.hard_input_limit_tokens), 1),
        )
    else:
        threshold_tokens = max(int(budget.compression_trigger_tokens), 1)
    return {
        "static_tokens": estimate_text_tokens(system_prompt or spec.system_prompt)
        + count_tools_tokens(list(tools or [])),
        "threshold_tokens": threshold_tokens,
    }


def _safe_subagent_budget_meta(
    spec: SubagentSpec,
    tools: list,
    *,
    system_prompt: str | None = None,
) -> dict[str, int] | None:
    """用量推送口径的降级封装：预算查询失败只放弃展示，不影响 task 执行。"""
    try:
        return _subagent_budget_meta(spec, tools, system_prompt=system_prompt)
    except Exception as exc:
        logger.warning("[tools] 子图上下文用量口径获取失败，本次派发不做用量推送: %s", exc)
        return None


def _compile_subagent(spec: SubagentSpec, run_controls: Any = None) -> Any:
    """把 spec 编译成独立 langgraph runnable。

    ``run_controls`` 完整时为子图挂上与主图相同的租约围栏：租约丢失/被接管后
    内层工具（plan 的 file_write、explore 的查询等）同样拒绝产生新副作用，
    而不是只靠心跳的协作式取消。
    """
    tools = list(spec.tools or [])
    # 技能目录与主智能体一致继承：增强后的 system prompt（含 <available_skills> 段）
    # 是压缩中间件、预算告警静态 token 与 create_agent 三处的同一份口径。
    effective_system_prompt = _subagent_system_prompt(spec)
    # 相对顺序与主图 MIDDLEWARE_CANONICAL_ORDER 对齐：出口处理链（归一化→结果→
    # 错误）的次序保持不变。压缩中间件是 before_model 节点（独立图节点，先于
    # 模型调用改写 state），预算告警是 wrap_model_call，二者无顺序耦合；
    # 口径同源：同一 model_id 的 ContextBudget 决定压缩触发线与告警线。
    # 注意：子图不挂 TodoListMiddleware——子智能体不给规划/进度类工具，
    # 进度与完成情况由收尾协议直接写进最终报告。
    middleware = [
        # 与主图同源的上下文压缩：trigger=窗口 70% 时摘要/引用外置/确定性收缩
        # 压到 30%，模型该调工具照常调（不改工具面）。这是「提前收尾不优雅」
        # 的结构性解法——超预算先无害化（改 state），软提示只兜压缩后的残量。
        create_context_compression_middleware(
            system_prompt=effective_system_prompt,
            tools=tools,
            model_id=spec.model_id,
            state_schema=ChatDataSubAgentState,
        ),
        create_subagent_budget_alert_middleware(
            spec,
            tools,
            system_prompt=effective_system_prompt,
        ),
        SkillPrerequisiteMiddleware(TOOL_REQUIRED_SKILLS),
        create_tool_call_name_normalizer_middleware(tools),
        create_tool_result_middleware(),
        ToolErrorMiddleware(on_tool_error),
    ]
    if run_controls is not None and run_controls.is_complete():
        middleware.append(
            create_tool_execution_fence_middleware(
                run_id=run_controls.run_id,
                owner_token=run_controls.owner_token,
                inbox_service=run_controls.inbox_service,
            )
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


def build_subagent_invocation_config(
    spec: SubagentSpec,
    *,
    default_recursion_limit: int | None = None,
) -> dict[str, Any]:
    """构造子图调用配置：切断回调继承，避免子图 token 进入主 SSE 流。

    default_recursion_limit 由调用方（异步装配链路）运行期经配置服务解析后传入
    （get_runtime_setting 对注册 key 恒返回 int，不会为 None）；两者都缺省时
    langgraph 默认 25 个 super-step，不低于 1 的下限由 max 保证。
    """
    return {
        "callbacks": [],
        "recursion_limit": max(int(spec.recursion_limit or default_recursion_limit or 0), 1),
        "configurable": {"chatdata_subagent": spec.name},
    }


def _plain_text(content: Any) -> str:
    """把消息 content 归一化成纯文本（多模态 list 按行拼接）。"""
    if isinstance(content, list):
        return "\n".join(part if isinstance(part, str) else str(part) for part in content)
    return content if isinstance(content, str) else str(content or "")


def _truncate_text(text: str, max_chars: int, label: str = "结果") -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n…[{label}过长已截断，完整内容共 {len(text)} 字符]"


def _truncate_subagent_result(content: Any) -> str:
    """把内层工具结果压成 SSE 可安全外发的文本预览。"""
    return _truncate_text(_plain_text(content), SUBAGENT_RESULT_PREVIEW_MAX_CHARS)


def _chunk_text_delta(content: Any) -> str:
    """从流式 chunk 的 content 里取纯文本增量。

    ``str`` 直接返回；content blocks 形式只取 ``text`` 块，其余块（工具调用
    等结构化载荷）不参与文本增量，避免 dict 被 ``str()`` 成调试噪音。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return ""


def _chunk_reasoning_text(chunk: Any) -> str:
    """从流式 chunk 提取思考增量。

    口径与 handlers ``_extract_reasoning_content`` 一致（OpenAI compatible
    的 ``reasoning_content`` / ``reasoning`` / ``reasoning_text``），但此处
    面向 middleware 层、不依赖 handlers；展示名替换只在 handlers 出口做。
    """
    for attr in ("additional_kwargs", "response_metadata"):
        value = getattr(chunk, attr, None)
        if not isinstance(value, dict):
            continue
        for key in ("reasoning_content", "reasoning", "reasoning_text"):
            reasoning = value.get(key)
            if isinstance(reasoning, str) and reasoning:
                return reasoning
    return ""


class _StreamCapState:
    """单个流式键（正文或思考）的累计字符数与封顶标记。"""

    __slots__ = ("chars", "capped")

    def __init__(self) -> None:
        self.chars = 0
        self.capped = False

    def take(self, delta: str, max_chars: int) -> tuple[str | None, bool]:
        """返回 ``(允许外发的增量片段, 本次是否触发封顶截断)``；封顶后为 ``(None, False)``。"""
        if self.capped or not delta:
            return None, False
        remaining = max_chars - self.chars
        if remaining <= 0:
            self.capped = True
            return None, False
        if len(delta) <= remaining:
            self.chars += len(delta)
            return delta, False
        self.chars = max_chars
        self.capped = True
        return delta[:remaining] + "\n…[内容过长已截断]", True


def _iter_chunk_stream_events(
    chunk: Any,
    parent_tool_call_id: str | None,
    text_caps: dict[str, _StreamCapState],
    think_caps: dict[str, _StreamCapState],
    markup_filters: dict[str, "_StreamingToolCallMarkupFilter"] | None = None,
) -> list[dict[str, Any]]:
    """把 ``messages`` 模式的 ``AIMessageChunk`` 翻译成 token 级增量事件。

    - 正文增量 → ``subagent_message_delta``（先经 ``markup_filters`` 流式剥除
      裸 ``<tool_call>`` 标记，再计封顶——封顶口径按用户实际看到的文本算）
    - 思考增量 → ``subagent_thinking_delta``
    - 无 ``message_id`` 的 chunk 不增量转发（无法与 updates 的完整消息收敛，
      由完整消息事件兜底）

    带 ``tool_call_chunks`` 的 chunk **不能整块跳过**：OpenAI 兼容实现里正文与
    工具调用是两个独立字段，同一个 chunk 完全可以「文本 + 工具增量」同时到达。
    一旦跳过，这段文本永远补不回来——该消息已因先前的 delta 进入
    ``streamed_text_ids``，完整消息事件按收敛策略只补标记、不再携带 content。
    工具参数不会漏进正文：``_chunk_text_delta`` 只取 ``str`` 与 ``type=="text"``
    的内容块，结构化块（工具调用等）不参与文本增量。
    """
    if not isinstance(chunk, AIMessageChunk):
        return []
    message_id = getattr(chunk, "id", None)
    if not message_id:
        return []

    events: list[dict[str, Any]] = []
    text = _chunk_text_delta(getattr(chunk, "content", ""))
    if text:
        if markup_filters is not None:
            text = markup_filters.setdefault(message_id, _StreamingToolCallMarkupFilter()).feed(text)
        if text:
            cap = text_caps.setdefault(message_id, _StreamCapState())
            allowed, was_capped = cap.take(text, SUBAGENT_MESSAGE_PREVIEW_MAX_CHARS)
            if allowed:
                events.append(
                    {
                        "event_type": SSE_EVENT_SUBAGENT_MESSAGE_DELTA,
                        "message_id": message_id,
                        "delta": allowed,
                        "truncated": was_capped,
                        "parent_tool_call_id": parent_tool_call_id,
                    }
                )
    reasoning = _chunk_reasoning_text(chunk)
    if reasoning:
        cap = think_caps.setdefault(message_id, _StreamCapState())
        allowed, was_capped = cap.take(reasoning, SUBAGENT_THINKING_PREVIEW_MAX_CHARS)
        if allowed:
            events.append(
                {
                    "event_type": SSE_EVENT_SUBAGENT_THINKING_DELTA,
                    "message_id": message_id,
                    "delta": allowed,
                    "truncated": was_capped,
                    "parent_tool_call_id": parent_tool_call_id,
                }
            )
    return events


def _emit_subagent_tool_event(writer: Any, event: dict[str, Any]) -> None:
    """把子智能体内层工具事件写入父图 custom 流（writer 不可用时静默跳过）。"""
    if writer is None:
        return
    try:
        writer({"type": "subagent_tool", "event": event})
    except Exception:
        logger.warning("转发子智能体工具事件失败", exc_info=True)


def _iter_subagent_stream_events(
    messages: list,
    parent_tool_call_id: str | None = None,
    streamed_text_ids: set[str] | None = None,
    streamed_think_ids: set[str] | None = None,
    id_resolver: Callable[[Any], str] | None = None,
) -> list[dict[str, Any]]:
    """把子图节点更新里的消息翻译成子智能体内部过程事件序列。

    - AIMessage.content（非空文本）→ ``subagent_message``，即子智能体的中间
      叙述/分析结论，用 ``message_id`` 去重
    - AIMessage.tool_calls → ``tool_started``（args 尽力规整为 dict）
    - ToolMessage → ``tool_completed`` / ``tool_failed``（结果截断为预览）
    - 每个事件都挂 ``parent_tool_call_id`` 指向所属外层 task 调用，便于前端
      把内部过程挂在 task 卡片下、而不是当作顶层调用渲染。

    收敛策略（只补标记不覆盖）：``messages`` 模式已增量转发过文本/思考的
    消息（命中 ``streamed_text_ids`` / ``streamed_think_ids``），完整消息事件
    不再携带 content（前端已拼出全文），只补 ``truncated`` 标记。

    同一条 AIMessage 既有文本又有工具调用时按「先说后做」输出，保证前端拿到的
    顺序与子智能体真实执行顺序一致。

    ``id_resolver`` 为缺失 ``id`` 的消息生成稳定兜底 id：前端以 message_id 作
    为 trail 条目主键，空 id 会被整条丢弃——不能把「中间文本是否可见」押在
    provider 一定返回 chunk id 上。
    """
    streamed_text_ids = streamed_text_ids or set()
    streamed_think_ids = streamed_think_ids or set()
    events: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, AIMessage):
            message_id = getattr(message, "id", None) or (id_resolver(message) if id_resolver else None)
            text = _plain_text(getattr(message, "content", ""))
            if text.strip():
                events.append(
                    _subagent_message_event(
                        message_id,
                        text,
                        parent_tool_call_id,
                        already_streamed=bool(message_id and message_id in streamed_text_ids),
                    )
                )
            events.extend(_subagent_tool_started_events(message, parent_tool_call_id))
        elif isinstance(message, ToolMessage):
            events.append(_subagent_tool_result_event(message, parent_tool_call_id))
    return events


def _subagent_message_event(
    message_id: str | None,
    text: str,
    parent_tool_call_id: str | None,
    already_streamed: bool,
) -> dict[str, Any]:
    """生成 subagent_message 事件。

    已流式转发过的消息（``already_streamed``）不再携带 content（前端已拼出
    全文），只补 ``truncated`` 标记——只补标记不覆盖。两个分支都显式带
    ``streamed``：前端按布尔值判断收敛，不区分「字段缺失」与「未流式」。

    文本先过 ``_sanitize_report_text``：完整消息事件（尤其历史回放路径）
    不允许携带裸 ``<tool_call>`` 标记（硬线摘工具轮次的退化形态）。
    """
    text = _sanitize_report_text(text)
    truncated = len(text) > SUBAGENT_MESSAGE_PREVIEW_MAX_CHARS
    if already_streamed:
        return {
            "event_type": SSE_EVENT_SUBAGENT_MESSAGE,
            "message_id": message_id,
            "content": "",
            "streamed": True,
            "truncated": truncated,
            "parent_tool_call_id": parent_tool_call_id,
        }
    return {
        "event_type": SSE_EVENT_SUBAGENT_MESSAGE,
        "message_id": message_id,
        "content": _truncate_text(text, SUBAGENT_MESSAGE_PREVIEW_MAX_CHARS, "内容"),
        "streamed": False,
        "truncated": truncated,
        "parent_tool_call_id": parent_tool_call_id,
    }


def _subagent_tool_started_events(message: AIMessage, parent_tool_call_id: str | None) -> list[dict[str, Any]]:
    """把 AIMessage 的 tool_calls 翻译成 tool_started 事件（args 尽力规整为 dict）。"""
    events: list[dict[str, Any]] = []
    for tool_call in getattr(message, "tool_calls", None) or []:
        if not isinstance(tool_call, dict):
            continue
        events.append(
            {
                "event_type": SSE_EVENT_TOOL_STARTED,
                "tool_call_id": tool_call.get("id"),
                "tool_name": tool_call.get("name"),
                "args": coerce_tool_call_args(tool_call.get("args")) or {},
                "parent_tool_call_id": parent_tool_call_id,
            }
        )
    return events


def _subagent_tool_result_event(message: ToolMessage, parent_tool_call_id: str | None) -> dict[str, Any]:
    """把 ToolMessage 翻译成 tool_completed / tool_failed 事件（结果截断为预览）。"""
    raw = _plain_text(getattr(message, "content", ""))
    status = str(getattr(message, "status", "") or "")
    return {
        "event_type": SSE_EVENT_TOOL_FAILED if status == "error" else SSE_EVENT_TOOL_COMPLETED,
        "tool_call_id": getattr(message, "tool_call_id", None),
        "tool_name": getattr(message, "name", None),
        "content": _truncate_text(raw, SUBAGENT_RESULT_PREVIEW_MAX_CHARS),
        "truncated": len(raw) > SUBAGENT_RESULT_PREVIEW_MAX_CHARS,
        "parent_tool_call_id": parent_tool_call_id,
    }


class _SubagentStreamState:
    """子图直播的状态载体：去重、增量转发达标与匿名 id 兜底。"""

    def __init__(self, parent_tool_call_id: str | None) -> None:
        self.parent_tool_call_id = parent_tool_call_id
        self.seen_ids: set[str] = set()
        self.text_caps: dict[str, _StreamCapState] = {}
        self.think_caps: dict[str, _StreamCapState] = {}
        # 按消息隔离的流式标记剥除器：硬线摘工具轮次的退化文本不允许进直播
        self.text_markup_filters: dict[str, _StreamingToolCallMarkupFilter] = {}
        self.streamed_text_ids: set[str] = set()
        self.streamed_think_ids: set[str] = set()
        self.anonymous_seq = count(1)
        self.anonymous_ids: dict[int, str] = {}
        self.collected: list = []

    def resolve_anonymous_id(self, message: Any) -> str:
        """给缺失 id 的消息生成稳定 id。

        按对象身份缓存：同一条消息（无 id）可能出现在多个节点的 updates 里，
        必须收敛到同一个 id，否则 seen_ids 去重失效、前端会渲染出重复条目。
        消息对象在 collected 中持有强引用，``id()`` 作为 key 不会被复用。
        """
        key = id(message)
        generated = self.anonymous_ids.get(key)
        if generated is None:
            generated = f"{self.parent_tool_call_id or 'subagent'}:anon-{next(self.anonymous_seq)}"
            self.anonymous_ids[key] = generated
        return generated

    def emit_messages_chunk(self, chunk_obj: Any, writer: Any) -> None:
        """messages 模式：正文/思考增量转发并记录已达标的 message id。"""
        for event in _iter_chunk_stream_events(
            chunk_obj,
            self.parent_tool_call_id,
            self.text_caps,
            self.think_caps,
            self.text_markup_filters,
        ):
            if event["event_type"] == "subagent_message_delta":
                self.streamed_text_ids.add(event["message_id"])
            else:
                self.streamed_think_ids.add(event["message_id"])
            _emit_subagent_tool_event(writer, event)

    def collect_update_messages(self, update: dict[str, Any]) -> list:
        """updates 模式：收集本节点新增消息（去重后），返回给事件提取。

        子图压缩（``ContextCompressionMiddleware.before_model`` 是独立图节点，
        其 state 更新会作为节点更新出现在 updates 流）以
        ``[RemoveMessage(REMOVE_ALL_MESSAGES), *compressed]`` 整体替换消息列表：
        此时 ``collected``/``seen_ids`` 必须按压缩后列表重建，否则会滞留已被
        丢弃的旧消息——用量徽标虚高、trail 混入已压缩内容、最终报告取到旧文。
        压缩保留的是早先已见过的同一批消息对象，重建时不再作为「新增」外发，
        避免前端收到重复的 message / tool_started 条目。
        """
        new_messages: list = []
        for node_result in update.values():
            if not isinstance(node_result, dict):
                continue
            node_messages = node_result.get("messages") or []
            if isinstance(node_messages, dict):
                # 带 reducer 指令的消息更新（如 REMOVE_ALL_MESSAGES），跳过
                continue
            if any(isinstance(message, RemoveMessage) for message in node_messages):
                self._apply_message_removals(node_messages, new_messages)
                continue
            for message in node_messages:
                if isinstance(message, dict):
                    continue
                self._collect_new_message(message, new_messages)
        return new_messages

    def _collect_new_message(self, message: Any, new_messages: list) -> None:
        """正常节点更新的单条消息收集：按 id 去重后入 collected 并标记为新增。"""
        message_id = getattr(message, "id", None) or self.resolve_anonymous_id(message)
        if message_id in self.seen_ids:
            return
        self.seen_ids.add(message_id)
        self.collected.append(message)
        new_messages.append(message)

    @staticmethod
    def _parse_removal_update(node_messages: list) -> tuple[bool, set[str]]:
        """解析移除更新：返回（是否全量替换, 定点删除的消息 id 集合）。"""
        full_reset = any(
            isinstance(message, RemoveMessage) and getattr(message, "id", None) == REMOVE_ALL_MESSAGES
            for message in node_messages
        )
        targeted_ids = {
            str(message.id)
            for message in node_messages
            if isinstance(message, RemoveMessage) and getattr(message, "id", None) != REMOVE_ALL_MESSAGES
        }
        return full_reset, targeted_ids

    def _drop_collected_messages(self, targeted_ids: set[str]) -> None:
        """从记账与已收集列表中就地摘除定点删除的消息。

        collected 可能被调用方持有引用（失败路径 trail 快照依赖它），
        必须原地修改而非重新绑定。
        """
        for removed_id in targeted_ids:
            self.seen_ids.discard(removed_id)
            self.collected[:] = [
                message for message in self.collected if str(getattr(message, "id", "") or "") != removed_id
            ]

    def _apply_message_removals(self, node_messages: list, new_messages: list) -> None:
        """处理压缩节点的消息移除更新，重建 collected/seen_ids 记账。

        - ``RemoveMessage(REMOVE_ALL_MESSAGES)``：全量替换——collected 清空后
          按更新里携带的保留消息（compressed 列表）重建；
        - 按 id 的定点 ``RemoveMessage``：只从记账里摘除对应消息，其余消息
          沿用既有记账（同一节点更新里未删除的消息不重复入 collected）。
        """
        previous_ids = set(self.seen_ids)
        full_reset, targeted_ids = self._parse_removal_update(node_messages)
        if full_reset:
            self.collected.clear()
            self.seen_ids.clear()
        else:
            self._drop_collected_messages(targeted_ids)
        for message in node_messages:
            if isinstance(message, (RemoveMessage, dict)):
                continue
            message_id = getattr(message, "id", None) or self.resolve_anonymous_id(message)
            if str(message_id) in targeted_ids or message_id in self.seen_ids:
                continue
            self.seen_ids.add(message_id)
            self.collected.append(message)
            if message_id not in previous_ids:
                new_messages.append(message)


def _subagent_usage_snapshot(usage_meta: dict[str, int], collected: list) -> dict[str, int]:
    """按静态口径+已收集消息估算当前子图上下文用量快照。"""
    used = usage_meta["static_tokens"] + count_messages_tokens(collected)
    return {
        "used_tokens": used,
        "threshold_tokens": usage_meta["threshold_tokens"],
        "percent": min(int(used * 100 / max(usage_meta["threshold_tokens"], 1)), 999),
    }


def _process_subagent_chunk(
    chunk: Any, stream_state: "_SubagentStreamState", writer: Any, parent_tool_call_id: str | None
) -> bool:
    """消费单个子图流 chunk：messages 模式转发增量、updates 模式发工具生命周期事件。

    返回是否为 updates 模式（调用方据此推送 subagent_context_usage）。
    """
    if not (isinstance(chunk, tuple) and len(chunk) == 2):
        return False
    mode, payload = chunk
    if mode == "messages":
        if isinstance(payload, tuple) and len(payload) == 2:
            stream_state.emit_messages_chunk(payload[0], writer)
        return False
    if mode != "updates" or not isinstance(payload, dict):
        return False
    new_messages = stream_state.collect_update_messages(payload)
    for event in _iter_subagent_stream_events(
        new_messages,
        parent_tool_call_id,
        stream_state.streamed_text_ids,
        stream_state.streamed_think_ids,
        stream_state.resolve_anonymous_id,
    ):
        _emit_subagent_tool_event(writer, event)
    return True


async def _run_subagent_with_events(
    runnable: Any,
    state: dict[str, Any],
    config: dict[str, Any],
    writer: Any,
    parent_tool_call_id: str | None = None,
    collected: list | None = None,
    captured: dict[str, Any] | None = None,
    usage_meta: dict[str, int] | None = None,
) -> list:
    """流式运行子图：向父图 custom 流转发内层事件，返回完整消息列表。

    ``collected`` 可由调用方传入并持有：子图中途抛异常（如 recursion_limit、
    模型网络错误）时，调用方仍能用已收集的消息为失败路径构建 trail 快照。

    ``captured`` 可由调用方传入收集附加产物：子图结束时写入 ``context_usage``
    （最终上下文用量快照，成功与失败路径都可用），供调用方盖
    ``subagent_context_usage`` 到 ToolMessage 供历史回放还原。

    ``usage_meta`` 非 None 时，每个节点更新后估算当前子图上下文用量并向前端
    发 ``subagent_context_usage`` 事件（task 卡片标题行实时显示）；静态口径
    （system+tools）由调用方传入，与预算告警中间件共用 ``_subagent_budget_meta``。

    双模式并用：

    - ``messages``：token 级 ``AIMessageChunk``，正文/思考增量经
      ``subagent_message_delta`` / ``subagent_thinking_delta`` 转发，
      前端即时渲染；``callbacks=[]`` 不受影响（增量走 langgraph 流通道，
      不经过回调）。
    - ``updates``：节点完成后的完整消息，负责工具生命周期事件
      （tool_started / tool_completed / tool_failed）与最终报告提取；
      完整 ``subagent_message`` 事件对已增量转发的消息只补截断标记
      （收敛策略见 ``_iter_subagent_stream_events``）。
    """
    stream_state = _SubagentStreamState(parent_tool_call_id)
    if collected is not None:
        stream_state.collected = collected

    def _emit_context_usage(*, emit: bool = True) -> dict[str, int] | None:
        if usage_meta is None:
            return None
        usage = _subagent_usage_snapshot(usage_meta, stream_state.collected)
        if emit:
            _emit_subagent_tool_event(
                writer,
                {
                    "event_type": SSE_EVENT_SUBAGENT_CONTEXT_USAGE,
                    "parent_tool_call_id": parent_tool_call_id,
                    **usage,
                },
            )
        return usage

    try:
        async for chunk in runnable.astream(state, config, stream_mode=["updates", "messages"]):
            if _process_subagent_chunk(chunk, stream_state, writer, parent_tool_call_id):
                _emit_context_usage()
    finally:
        # 用量只重算不外发（直播期最后一次已随节点更新发出，避免重复事件），
        # 异常路径同样交给调用方（失败 ToolMessage 也带用量快照）。
        if captured is not None:
            captured["context_usage"] = _emit_context_usage(emit=False)
    return stream_state.collected


def _build_subagent_state(runtime: ToolRuntime, description: str) -> dict[str, Any]:
    state = {key: value for key, value in dict(runtime.state or {}).items() if key in SUBAGENT_STATE_PASSTHROUGH_KEYS}
    state["messages"] = [HumanMessage(content=description)]
    return state


def _collect_subagent_tool_results(messages: list) -> dict[str, dict[str, Any]]:
    tool_results: dict[str, dict[str, Any]] = {}
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        tool_call_id = str(getattr(message, "tool_call_id", "") or "")
        if not tool_call_id:
            continue
        raw = _plain_text(getattr(message, "content", ""))
        tool_results[tool_call_id] = {
            "status": "error" if str(getattr(message, "status", "") or "") == "error" else "completed",
            "result": _truncate_text(raw, SUBAGENT_RESULT_PREVIEW_MAX_CHARS),
        }
    return tool_results


def _trail_message_entry(message: AIMessage, message_id: str) -> dict[str, Any]:
    text = _sanitize_report_text(_plain_text(getattr(message, "content", "")))
    thinking = _chunk_reasoning_text(message)
    return {
        "kind": "message",
        "id": message_id,
        "content": _truncate_text(text, SUBAGENT_MESSAGE_PREVIEW_MAX_CHARS, "内容") if text.strip() else "",
        "truncated": len(text) > SUBAGENT_MESSAGE_PREVIEW_MAX_CHARS,
        "thinking": _truncate_text(thinking, SUBAGENT_THINKING_PREVIEW_MAX_CHARS, "思考") if thinking.strip() else "",
        "thinkingTruncated": len(thinking) > SUBAGENT_THINKING_PREVIEW_MAX_CHARS,
    }


def _trail_tool_entry(tool_call: dict[str, Any], tool_results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    tool_call_id = str(tool_call.get("id") or "")
    outcome = tool_results.get(tool_call_id) or {}
    return {
        "kind": "tool",
        "id": tool_call_id,
        "tool_name": str(tool_call.get("name") or ""),
        "args": coerce_tool_call_args(tool_call.get("args")) or {},
        "result": outcome.get("result", ""),
        "status": outcome.get("status", "completed"),
    }


def _trail_orphan_tool_entry(
    message: ToolMessage,
    tool_results: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    tool_call_id = str(getattr(message, "tool_call_id", "") or "")
    outcome = tool_results[tool_call_id]
    return {
        "kind": "tool",
        "id": tool_call_id,
        "tool_name": str(getattr(message, "name", "") or ""),
        "args": {},
        "result": outcome["result"],
        "status": outcome["status"],
    }


def _append_ai_message_trail(
    message: AIMessage,
    tool_results: dict[str, dict[str, Any]],
    trail: list[dict[str, Any]],
    seen_ids: set[str],
    anonymous_seq: count,
) -> None:
    message_id = str(getattr(message, "id", "") or "") or f"trail-anon-{next(anonymous_seq)}"
    text = _plain_text(getattr(message, "content", ""))
    thinking = _chunk_reasoning_text(message)
    if (text.strip() or thinking.strip()) and message_id not in seen_ids:
        seen_ids.add(message_id)
        trail.append(_trail_message_entry(message, message_id))
    for tool_call in getattr(message, "tool_calls", None) or []:
        if not isinstance(tool_call, dict):
            continue
        tool_call_id = str(tool_call.get("id") or "")
        if not tool_call_id or tool_call_id in seen_ids:
            continue
        seen_ids.add(tool_call_id)
        trail.append(_trail_tool_entry(tool_call, tool_results))


def _build_trail_snapshot(messages: list) -> list[dict[str, Any]]:
    """把子图消息序列化成与前端 ``SubagentTrailItem`` 同构的过程快照。

    用于历史回放：直播时过程经 SSE custom 流逐条转发，刷新/看历史后由
    task ToolMessage 的 ``subagent_trail`` 还原。截断口径与 SSE 预览共用
    同一套常量，保证用户看到的直播内容与历史内容一致。

    - AIMessage → ``message`` 条目（content + reasoning 合并为一条，text 与
      tool_calls 按「先说后做」输出，顺序与 ``_iter_subagent_stream_events``
      一致）
    - ToolMessage → 回填对应内层工具的终态 status 与结果预览
    """
    tool_results = _collect_subagent_tool_results(messages)

    trail: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    anonymous_seq = count(1)
    for message in messages:
        if isinstance(message, AIMessage):
            _append_ai_message_trail(message, tool_results, trail, seen_ids, anonymous_seq)
        elif isinstance(message, ToolMessage):
            # 内层工具先产生 ToolMessage 后才出现对应 tool_calls 的情形不
            # 应发生（OpenAI 协议要求先声明后调用）；此处只为容忍乱序输入。
            tool_call_id = str(getattr(message, "tool_call_id", "") or "")
            if tool_call_id and tool_call_id not in seen_ids:
                seen_ids.add(tool_call_id)
                trail.append(_trail_orphan_tool_entry(message, tool_results))
    return trail


async def _build_task_tool_impl(
    specs: list[SubagentSpec],
    compiled: dict[str, Any],
    subagent_type: str,
    description: str,
    runtime: ToolRuntime,
) -> Any:
    """执行单一 task 工具调用：派发子图、收集过程、产出 Command 更新。"""
    runnable = compiled.get(subagent_type)
    if runnable is None:
        allowed = ", ".join(f"`{name}`" for name in compiled)
        return f"无法调用子智能体 {subagent_type}：类型不存在，可用类型为 {allowed}"
    spec = next(item for item in specs if item.name == subagent_type)
    # writer 必须在子图调用前获取：调用后当前上下文会切进子图运行
    try:
        writer = get_stream_writer()
    except Exception as exc:
        # 拿不到 writer 只影响子图过程实时下发(SSE),子智能体仍要继续执行,故降级为 None。
        logger.warning("[tools] 获取子图流式 writer 失败,子智能体过程将不做实时下发: %s", exc)
        writer = None
    collected: list = []
    captured: dict[str, Any] = {}
    try:
        messages = await _run_subagent_with_events(
            runnable,
            _build_subagent_state(runtime, description),
            build_subagent_invocation_config(
                spec,
                default_recursion_limit=await get_runtime_setting("CHAT_DATA_AGENT_RECURSION_LIMIT"),
            ),
            writer,
            runtime.tool_call_id,
            collected=collected,
            captured=captured,
            usage_meta=_safe_subagent_budget_meta(
                spec,
                list(spec.tools or []),
                system_prompt=_subagent_system_prompt(spec),
            ),
        )
    except Exception as exc:
        # 失败路径同样盖 trail 快照：直播时内层过程已通过 SSE 转发给用户，
        # 若异常直接抛给 tool_error 中间件（无 additional_kwargs），历史里
        # trail 会丢失，造成「直播看得到、历史看不到」的不一致。这里就地
        # 转成 error ToolMessage，主智能体可据失败原因降级接手。
        logger.error("[tools] 子智能体执行异常: %s", exc, exc_info=True)
        report = f"子智能体执行失败：{_truncate_text(str(exc) or exc.__class__.__name__, 200)}"
        messages = collected
        tool_status = "error"
    else:
        report = _extract_final_report(messages)
        if not report:
            report = "子智能体未产出有效结论，请检查任务书后重试或自行完成该任务。"
        tool_status = "success"
    context_usage = captured.get("context_usage")
    if tool_status == "success" and context_usage and int(context_usage.get("percent", 0) or 0) >= 100:
        # 预算收尾路径：报告可能残缺，必须在正文里告知主智能体（模型只可靠
        # 读取 content，additional_kwargs 不进模型上下文）；只陈述事实，
        # 如何续接由主智能体自行决定。
        report = (
            "【子智能体预算提示】子智能体上下文已达预算上限"
            f"（约 {context_usage.get('used_tokens', 0)} / {context_usage.get('threshold_tokens', 0)} tokens）"
            "并已提前收尾，以下结论可能不完整。\n\n" + report
        )
    # 过程快照盖在 ToolMessage 上随消息持久化：直播过程走 SSE，历史回放
    # 走 conversation_event 的 tool_result payload（见
    # conversation_event_service._serialize_tool_message）。
    additional_kwargs: dict[str, Any] = {}
    trail = _build_trail_snapshot(messages)
    if trail:
        additional_kwargs["subagent_trail"] = trail
    # 上下文用量快照：随 ToolMessage 持久化，历史回放时前端在 task 卡片
    # 标题行还原最后一次用量（直播路径走 subagent_context_usage SSE 事件）。
    if context_usage:
        additional_kwargs["subagent_context_usage"] = context_usage
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


def build_task_tool(specs: list[SubagentSpec], run_controls: Any = None) -> StructuredTool:
    """构建单一 ``task`` 工具：按 subagent_type 派发到隔离上下文的子图。"""
    validate_subagent_specs(specs)
    compiled = {spec.name: _compile_subagent(spec, run_controls=run_controls) for spec in specs}
    available = "\n".join(f"- {spec.name}: {spec.description}" for spec in specs)

    async def atask(description: str, subagent_type: str, runtime: ToolRuntime) -> Command | str:
        return await _build_task_tool_impl(specs, compiled, subagent_type, description, runtime)

    return StructuredTool.from_function(
        name=TASK_TOOL_NAME,
        coroutine=atask,
        description=_TASK_TOOL_DESCRIPTION_TEMPLATE.format(available_agents=available),
        args_schema=TaskToolArgs,
    )


class SubagentMiddleware(AgentMiddleware):
    """向主智能体注册 ``task`` 工具，并在系统提示追加子智能体使用说明。"""

    def __init__(
        self, specs: list[SubagentSpec], *, system_prompt: str | None = None, run_controls: Any = None
    ) -> None:
        validate_subagent_specs(specs)
        self.specs = list(specs)
        self.subagent_names = frozenset(spec.name for spec in specs)
        self.tools = [build_task_tool(specs, run_controls=run_controls)]
        agents_desc = "\n".join(f"- {spec.name}: {spec.description}" for spec in specs)
        self.system_prompt = (system_prompt or _default_system_prompt()).format(
            available_agents=agents_desc,
        )

    def wrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelCallResult]
    ) -> ModelCallResult:
        return handler(_append_system_prompt(request, self.system_prompt))

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelCallResult]]
    ) -> ModelCallResult:
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
        "你可以通过 task 工具把复杂多步任务派发给隔离上下文的子智能体，"
        "只有子智能体的最终结论会回到对话。可用类型：\n\n{available_agents}\n\n"
        "派发判断：预计需要多轮工具调用（如批量盘点、成稿写作）且过程中无需"
        "用户参与的任务才派发；简单查询和需要向用户提问、展示中间过程的工作"
        "直接自己完成。派发前必须先把子智能体需要的背景事实写入 workspace 文件，"
        "任务书里给文件路径——子智能体看不到对话历史，只存在于对话中的信息它拿不到。"
    )
