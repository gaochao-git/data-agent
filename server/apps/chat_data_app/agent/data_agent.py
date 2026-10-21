"""ChatData Agent 的构建与工具装配。

装配以 ``AgentAssemblySpec`` 声明式规格驱动：内置工具按 ``tools/registry.py``
注册表装配后按规格做工具名级筛选，system prompt 与 LangGraph agent 名由规格
提供。``chat_data`` 是第一个声明式规格；后续装配型智能体（如智能运维）按同一
规格接入，不再复制装配函数。
"""

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from utils.logger import get_logger

from ..agent.context.context_budget import (
    ContextRequestBudget,
    count_tools_tokens,
    get_model_context_budget,
)
from ..services.llm_config import get_chat_data_llm, get_chat_data_model_config, get_default_chat_data_model
from ..services.memory.conversation_memory_service import schedule_memory_extraction_now
from .agent_runtime import (
    build_standard_agent_middleware,
    create_streaming_chat_model,
)
from .agent_state import ChatDataAgentState
from .middleware.after_model_history import create_after_model_history_middleware
from .middleware.context_compression import create_context_compression_middleware
from .middleware.context_usage import create_request_context_usage_middleware
from .middleware.image_injection import create_image_injection_middleware
from .middleware.mcp_tool_discovery import MCPToolDiscoveryMiddleware
from .middleware.steering import create_steering_middleware
from .middleware.subagents import SubagentMiddleware, SubagentSpec
from .middleware.tool_approval import create_tool_approval_middleware
from .middleware.tool_call_history_repair import create_tool_call_history_repair_middleware
from .middleware.tool_execution_fence import create_tool_execution_fence_middleware
from .middleware.tool_result import create_tool_result_middleware
from .prompts.chat_data import (
    DATA_EXPLORE_SUBAGENT_PROMPT,
    GENERAL_PURPOSE_NO_DELETE_HINT,
    GENERAL_PURPOSE_SUBAGENT_PROMPT,
    PLAN_SUBAGENT_PROMPT,
    build_unified_system_prompt,
)
from .runtime_options import AgentRuntimeOptions, get_chat_data_default_runtime_options
from .tools.registry import (
    APPROVAL_REQUIRED_TOOL_NAMES,
    DATA_EXPLORE_EXTRA_EXCLUDED_TOOL_NAMES,
    GENERAL_PURPOSE_EXTRA_EXCLUDED_TOOL_NAMES,
    PLAN_EXTRA_EXCLUDED_TOOL_NAMES,
    SUBAGENT_BASE_EXCLUDED_TOOL_NAMES,
    TOOL_SPECS,
    ToolBuildContext,
)
from .workspace.runtime import ConversationWorkspaceRuntime, ensure_conversation_workspace

if TYPE_CHECKING:
    from .mcp.catalog import MCPToolSearchService

logger = get_logger(__name__)


@dataclass(frozen=True)
class AgentRunControls:
    """一次 agent run 的控制面对象。

    工具租约围栏（tool_execution_fence）与 steering 注入总是成组出现：
    五个字段全部非空才启用，缺任何一个都视为本次运行不支持中断续跑，
    静默降级为无围栏、无 steering 的普通运行。

    ``disable_user_interaction`` 独立于上述围栏组：无人值守运行（定时轮）
    置 True，工具装配时跳过 ``requires_user_interaction`` 的注册条目
    （ask_user_question），防止提问后无人应答导致轮次悬挂。

    ``approval_mode``：``ask`` 时装配 ToolApprovalMiddleware，注册表标记
    （``ToolSpec.approval_required``）的工具执行前中断询问用户；``auto`` 直接
    执行，但 ``tool_approvals=force_ask`` 的 MCP 工具例外——auto 下仍装配
    中间件强制审批（无人值守则在装配层剔除该工具，fail-closed）。
    无人值守运行由调用方强制 ``auto``。
    """

    run_id: str | None = None
    owner_token: str | None = None
    inbox_service: Any = None
    admit_inbox_messages: Any = None
    steering_tracker: Any = None
    disable_user_interaction: bool = False
    approval_mode: str = "auto"

    def is_complete(self) -> bool:
        return bool(
            self.run_id
            and self.owner_token
            and self.inbox_service is not None
            and self.admit_inbox_messages is not None
            and self.steering_tracker is not None
        )


@dataclass(frozen=True)
class IdentityContext:
    """谁在问——来自认证层，一次登录内不变。"""

    user_id: str | None = None
    operator_name: str | None = None


@dataclass(frozen=True)
class DatasourceContext:
    """问哪个数据域——ChatData 业务概念，每轮请求可变。"""

    datasource_id: str | None = None
    source_type: str | None = None


@dataclass(frozen=True)
class ModelInvocationConfig:
    """模型怎么调——与业务无关的 LLM 调用参数。"""

    system_prompt: str | None = None
    model_id: str | None = None
    enable_thinking: bool | None = None
    thinking_effort: str | None = None
    # 会话技能清单（<available_skills> XML，agent_stream 每轮构建）。
    # 归「模型怎么调」组：目录本是系统提示的一部分，子智能体与主智能体一致继承。
    available_skills_xml: str | None = None
    # 会话记忆内容段（conversation_memory_service.render_session_memory_block 产物）。
    # 主图 system prompt 由 agent_stream 预先拼好（本字段已并入其中），此处仅供
    # 子图 spec 注入只读记忆段；空串时子图不注入。
    memory_block: str = ""


@dataclass(frozen=True)
class AgentAssemblySpec:
    """一个可装配 Agent 的声明式规格：工具选择 + prompt + 运行选项边界。

    - ``tool_includes`` / ``tool_excludes``：对 ``tools/registry.py`` 内置注册表
      的工具名级筛选；``tool_includes=None`` 表示全部内置工具，``tool_excludes``
      优先级更高。子智能体仍只使用内置工具（MCP 等动态工具不下发给子智能体）。
    - ``enable_mcp_tools``：是否在装配时枚举 MCP/插件声明的动态工具
      （见 ``agent/mcp/manager.py``）；未配置任何 MCP server 时为空、零开销。
    - ``mcp_server_ids``：MCP server 白名单（``None`` 表示全部允许）；在
      MCPToolInfo 层按 server_id 精确过滤，白名单外的 server 不枚举不挂载。
    - ``mcp_tool_search``：MCP 工具两级加载策略——``off`` 全量挂载不检索；
      ``auto`` 工具 schema token 超过模型窗口 ``mcp_tool_search_threshold``
      比例才启用检索模式；``forced`` 无条件启用。检索模式下 MCP 目标工具
      全部隐藏，统一先 ``mcp_tool_search``、再通过 ``mcp_tool_call`` 代理执行。
    - ``build_system_prompt``：``(runtime_options, workspace_root, memory_block="") -> prompt``，
      每次装配都会调用，prompt 必须由规格自身决定。
    """

    agent_id: str
    agent_name: str
    build_system_prompt: Callable[..., str]
    tool_includes: frozenset[str] | None = None
    tool_excludes: frozenset[str] = frozenset()
    enable_mcp_tools: bool = True
    mcp_server_ids: frozenset[str] | None = None
    mcp_tool_search: str = "auto"
    mcp_tool_search_threshold: float = 0.10


VALID_MCP_TOOL_SEARCH_MODES = frozenset({"off", "auto", "forced"})


def validate_agent_assembly_spec(spec: AgentAssemblySpec) -> None:
    """规格声明的工具名必须都已登记注册表，include/exclude 不得冲突，MCP 选项合法。"""
    from .tools.registry import provided_tool_names

    registered = provided_tool_names()
    unknown_includes = set(spec.tool_includes or ()) - registered
    if unknown_includes:
        raise ValueError(
            f"Agent 装配规格 {spec.agent_id} 的 tool_includes 引用了未注册工具: {sorted(unknown_includes)}"
        )
    unknown_excludes = set(spec.tool_excludes) - registered
    if unknown_excludes:
        raise ValueError(
            f"Agent 装配规格 {spec.agent_id} 的 tool_excludes 引用了未注册工具: {sorted(unknown_excludes)}"
        )
    if spec.tool_includes is not None:
        conflict = spec.tool_includes & spec.tool_excludes
        if conflict:
            raise ValueError(f"Agent 装配规格 {spec.agent_id} 的工具同时出现在 includes/excludes: {sorted(conflict)}")
    if spec.mcp_tool_search not in VALID_MCP_TOOL_SEARCH_MODES:
        raise ValueError(
            f"Agent 装配规格 {spec.agent_id} 的 mcp_tool_search 必须是 "
            f"{sorted(VALID_MCP_TOOL_SEARCH_MODES)} 之一: {spec.mcp_tool_search!r}"
        )
    if not 0.0 < spec.mcp_tool_search_threshold <= 1.0:
        raise ValueError(
            f"Agent 装配规格 {spec.agent_id} 的 mcp_tool_search_threshold 必须在 (0, 1] 内: "
            f"{spec.mcp_tool_search_threshold!r}"
        )
    invalid_server_ids = {server_id for server_id in (spec.mcp_server_ids or ()) if not str(server_id or "").strip()}
    if invalid_server_ids:
        raise ValueError(f"Agent 装配规格 {spec.agent_id} 的 mcp_server_ids 含空项: {sorted(invalid_server_ids)}")


def _filter_tools_by_spec(tools: list, spec: AgentAssemblySpec) -> list:
    """按规格对内置工具做工具名级筛选；excludes 优先于 includes。"""
    return [
        tool
        for tool in tools
        if str(getattr(tool, "name", "") or "") not in spec.tool_excludes
        and (spec.tool_includes is None or str(getattr(tool, "name", "") or "") in spec.tool_includes)
    ]


def _is_forced_approval_tool(tool: Any) -> bool:
    """工具是否为 ``tool_approvals=force_ask`` 的 MCP 工具（metadata 由 mcp/manager 透出）。"""
    metadata = getattr(tool, "metadata", None)
    return isinstance(metadata, dict) and str(metadata.get("approval") or "") == "force_ask"


def _build_tool_approval_middleware(run_controls: "AgentRunControls | None", tools: list | None = None) -> Any:
    """装配审批中间件；无人值守恒不装配（force_ask 工具由装配层过滤，不悬挂）。

    ask 档：注册表标记 + MCP 全员审批（``tool_approvals=allow`` 豁免）；
    auto 档：仅当装配工具存在 ``tool_approvals=force_ask`` 时按 auto 模式装配
    （只审 forced 集），否则返回 None 保持零开销。``tools`` 同时用于提取 MCP
    display_name（审批卡展示友好名）与审批档位。
    """
    if run_controls is None or run_controls.disable_user_interaction:
        return None
    if run_controls.approval_mode == "ask":
        return create_tool_approval_middleware(tools, run_id=run_controls.run_id)
    if any(_is_forced_approval_tool(tool) for tool in tools or []):
        return create_tool_approval_middleware(tools, run_id=run_controls.run_id, mode="auto")
    return None


def assemble_tools(
    runtime_options: AgentRuntimeOptions,
    workspace_runtime: ConversationWorkspaceRuntime,
    *,
    identity_context: IdentityContext | None = None,
    datasource_context: DatasourceContext | None = None,
    model_invocation: ModelInvocationConfig | None = None,
    spec: AgentAssemblySpec | None = None,
    run_controls: "AgentRunControls | None" = None,
) -> list:
    """按 ``tools/registry.py`` 注册表装配工具集，只有 Bash 受运行选项控制。

    传入 ``spec`` 时在注册表装配结果上再按规格做工具名级筛选（内置工具）。
    ``run_controls.disable_user_interaction``（无人值守，定时轮派发）时跳过
    ``requires_user_interaction`` 的注册条目（ask_user_question）：模型看不到
    也执行不了，提问入口从装配层关闭。
    """
    context = ToolBuildContext(
        runtime_options=runtime_options,
        workspace_runtime=workspace_runtime,
        identity=identity_context or IdentityContext(),
        datasource=datasource_context or DatasourceContext(),
        model_invocation=model_invocation or ModelInvocationConfig(),
    )
    skip_user_interaction = bool(run_controls and run_controls.disable_user_interaction)
    tools = [
        tool
        for tool_spec in TOOL_SPECS
        if not (skip_user_interaction and tool_spec.requires_user_interaction)
        for tool in tool_spec.build(context)
    ]
    if spec is not None:
        validate_agent_assembly_spec(spec)
        tools = _filter_tools_by_spec(tools, spec)
    return tools


# 子智能体工具可见性策略（SUBAGENT_BASE_EXCLUDED_TOOL_NAMES /
# DATA_EXPLORE_EXTRA_EXCLUDED_TOOL_NAMES / PLAN_EXTRA_EXCLUDED_TOOL_NAMES /
# GENERAL_PURPOSE_EXTRA_EXCLUDED_TOOL_NAMES）
# 单一事实源在 tools/registry.py，本模块按名字导入以保持既有引用路径不变。

DATA_EXPLORE_SUBAGENT_NAME = "data-explore"
DATA_EXPLORE_SUBAGENT_DESCRIPTION = (
    "只读事实收集：遍历语义模型/多表/多指标做批量盘点、口径确认与数据核对，"
    "只回传结构化事实清单，不写文件、不下最终结论。"
    "适合需要跨多模型/多表摸底数据事实的场景。"
)

PLAN_SUBAGENT_NAME = "plan"
PLAN_SUBAGENT_DESCRIPTION = (
    "统一规划：为复杂任务产出可执行计划文件（步骤分解、输入路径、期望产出、验收标准），"
    "报告类任务按办公技能 report-workflow 产出需求说明、样式清单与大纲。"
    "只规划不执行，计划写入会话工作区，只回传计划路径与步骤清单。"
    "适合多步骤报告、跨数据源综合分析等先规划后执行的任务。"
    "子智能体与主智能体共享技能清单，但看不到主对话历史：任务相关的技能（尤其"
    "用户自定义技能）建议在任务书中写明技能名称与 skill_file 路径。"
)

GENERAL_PURPOSE_SUBAGENT_NAME = "general-purpose"
GENERAL_PURPOSE_SUBAGENT_DESCRIPTION = (
    "通用执行：受委派在隔离上下文完成多步执行任务——按计划/要求生成报告、Excel、"
    "Word、HTML 等交付物，批量文件加工，执行语义查询并汇总落盘。"
    "可写 workspace/outputs，回传结论摘要与产物路径清单，产物展示由主智能体完成。"
    "适合中间过程大体量、只需要结论与交付物的执行环节。"
    "子智能体与主智能体共享技能清单，但看不到主对话历史：任务相关的技能（尤其"
    "用户自定义技能）建议在任务书中写明技能名称与 skill_file 路径。"
)


def _filter_subagent_tools(tools: list, excluded: frozenset) -> list:
    """按排除集过滤子智能体工具。"""
    return [tool for tool in tools if str(getattr(tool, "name", "") or "") not in excluded]


def _subagent_approval_exclusions(run_controls: "AgentRunControls | None") -> frozenset[str]:
    """按需审批模式下子智能体不挂审批工具：子图不支持人工中断，委派删除/移动会绕过审批。

    与 ``ask_user_question`` 同款处理——子智能体哲学是「不注册人工交互」，审批类
    工具从能力面剔除；auto 模式行为不变（子智能体照常可用）。
    """
    if run_controls is None or run_controls.approval_mode != "ask" or run_controls.disable_user_interaction:
        return frozenset()
    return APPROVAL_REQUIRED_TOOL_NAMES


def _general_purpose_system_prompt(approval_tools_excluded: bool) -> str:
    """general-purpose 的系统提示：审批工具被剔除时补一段能力边界说明。

    剔除只解决了「能不能执行」，没有解决「模型知不知道自己不能执行」：不声明时
    模型会先派一次工具拿到「工具不存在」，白跑一轮 token 才退回主智能体。这段
    与 ``_subagent_approval_exclusions`` 共用同一判据，auto/无人值守不受影响。
    """
    if not approval_tools_excluded:
        return GENERAL_PURPOSE_SUBAGENT_PROMPT
    return f"{GENERAL_PURPOSE_SUBAGENT_PROMPT}{GENERAL_PURPOSE_NO_DELETE_HINT}"


def build_subagent_middleware(
    runtime_options: AgentRuntimeOptions,
    tools: list,
    *,
    model: BaseChatModel,
    model_id: str | None = None,
    run_controls: "AgentRunControls | None" = None,
    available_skills_xml: str | None = None,
    memory_block: str | None = None,
) -> SubagentMiddleware | None:
    """按运行选项构建子智能体中间件；没有任何子智能体启用时返回 None。

    子智能体继承主智能体工具（各自扣除排除集），使用与主智能体相同的模型，
    运行在隔离上下文中；data-explore 额外扣除全部写操作工具保持只读。
    ``model_id`` 与主图同源传入：子图预算告警据此取同一 ContextBudget 的
    压缩触发阈值，保证「上下文将尽」的口径与主图一致。
    ``available_skills_xml`` 与主智能体同一会话技能清单（一致继承）：子图
    system prompt 由此注入目录段，正文仍按需 file_read；未传或为空则不注入。
    ``memory_block`` 与主智能体同一份会话记忆条目（一致继承）：子图 system
    prompt 由此注入只读记忆段；未传或为空则不注入。
    """
    approval_excluded = _subagent_approval_exclusions(run_controls)
    base_excluded = SUBAGENT_BASE_EXCLUDED_TOOL_NAMES | approval_excluded
    specs: list[SubagentSpec] = []
    if runtime_options.explore_subagent_enabled:
        specs.append(
            SubagentSpec(
                name=DATA_EXPLORE_SUBAGENT_NAME,
                description=DATA_EXPLORE_SUBAGENT_DESCRIPTION,
                system_prompt=DATA_EXPLORE_SUBAGENT_PROMPT,
                tools=_filter_subagent_tools(
                    tools,
                    base_excluded | DATA_EXPLORE_EXTRA_EXCLUDED_TOOL_NAMES,
                ),
                model=model,
                model_id=model_id,
                available_skills_xml=available_skills_xml or "",
                memory_block=memory_block or "",
            )
        )
    if runtime_options.plan_subagent_enabled:
        specs.append(
            SubagentSpec(
                name=PLAN_SUBAGENT_NAME,
                description=PLAN_SUBAGENT_DESCRIPTION,
                system_prompt=PLAN_SUBAGENT_PROMPT,
                tools=_filter_subagent_tools(
                    tools,
                    base_excluded | PLAN_EXTRA_EXCLUDED_TOOL_NAMES,
                ),
                model=model,
                model_id=model_id,
                available_skills_xml=available_skills_xml or "",
                memory_block=memory_block or "",
            )
        )
    if runtime_options.general_purpose_subagent_enabled:
        specs.append(
            SubagentSpec(
                name=GENERAL_PURPOSE_SUBAGENT_NAME,
                description=GENERAL_PURPOSE_SUBAGENT_DESCRIPTION,
                system_prompt=_general_purpose_system_prompt(bool(approval_excluded)),
                tools=_filter_subagent_tools(
                    tools,
                    base_excluded | GENERAL_PURPOSE_EXTRA_EXCLUDED_TOOL_NAMES,
                ),
                model=model,
                model_id=model_id,
                available_skills_xml=available_skills_xml or "",
                memory_block=memory_block or "",
            )
        )
    if not specs:
        return None
    return SubagentMiddleware(specs, run_controls=run_controls)


def _chat_data_system_prompt(
    options: AgentRuntimeOptions,
    workspace_root: str,
    memory_block: str = "",
) -> str:
    return build_unified_system_prompt(options, workspace_root=workspace_root, memory_block=memory_block)


MCP_TOOL_SEARCH_ENV = "CHAT_DATA_AGENT_MCP_TOOL_SEARCH"


def _env_mcp_tool_search_mode() -> str:
    """从环境变量读取检索模式（部署级覆盖），未配置或非法值返回默认 ``auto``。"""
    raw = str(os.getenv(MCP_TOOL_SEARCH_ENV) or "").strip()
    if not raw:
        return "auto"
    if raw not in VALID_MCP_TOOL_SEARCH_MODES:
        logger.warning(
            "环境变量 %s 非法（需为 %s 之一），忽略并使用 auto: %r",
            MCP_TOOL_SEARCH_ENV,
            sorted(VALID_MCP_TOOL_SEARCH_MODES),
            raw,
        )
        return "auto"
    return raw


CHAT_DATA_AGENT_SPEC = AgentAssemblySpec(
    agent_id="chat_data",
    agent_name="chat_data_agent",
    build_system_prompt=_chat_data_system_prompt,
    mcp_tool_search=_env_mcp_tool_search_mode(),
)


def _system_prompt_with_mcp_tools(
    system_prompt: str,
    *,
    search_active: bool,
    mcp_tools: list,
) -> str:
    """检索模式下把 MCP 能力摘要追加到系统提示；独立函数控制装配函数复杂度。"""
    if not search_active:
        return system_prompt
    reminder = _mcp_tool_reminder(mcp_tools)
    return f"{system_prompt.rstrip()}\n\n{reminder}" if reminder else system_prompt


def _mcp_tool_reminder(mcp_tools: list) -> str:
    """检索模式下按 server 汇总 MCP 能力；不列目标工具名，强制先搜索 schema。"""
    groups: dict[str, list[str]] = {}
    for tool in mcp_tools:
        name = str(getattr(tool, "name", "") or "")
        metadata = getattr(tool, "metadata", None)
        metadata = metadata if isinstance(metadata, dict) else {}
        server_id = str(metadata.get("mcp_server_id") or "MCP").strip() or "MCP"
        label = str(metadata.get("display_name") or metadata.get("mcp_tool_name") or name).strip()
        groups.setdefault(server_id, []).append(label)
    if not groups:
        return ""
    lines: list[str] = []
    for server_id, labels in sorted(groups.items()):
        examples = "、".join(sorted(set(labels))[:3])
        lines.append(f"- {server_id}：{len(labels)} 个工具，如 {examples}")
    listed = "\n".join(lines[:10])
    if len(groups) > 10:
        listed += f"\n- …其余 {len(groups) - 10} 个 MCP server"
    return (
        "<system-reminder>\n"
        "当前启用 MCP 插件能力（schema 未加载）：\n"
        f"{listed}\n"
        "需要具体工具时，必须先用 mcp_tool_search 搜索（select:工具名 可精确加载），"
        "拿到 input_schema 后通过 mcp_tool_call({name,input}) 调用；不要直接调用目标工具名。\n"
        "</system-reminder>"
    )


def _load_spec_mcp_tools(spec: AgentAssemblySpec) -> list:
    """装配规格允许时枚举 MCP/插件动态工具；任何失败都降级为空集，不阻断装配。"""
    if not spec.enable_mcp_tools:
        return []
    try:
        from .mcp.catalog import warn_long_mcp_descriptions
        from .mcp.manager import load_mcp_tools

        tools = list(load_mcp_tools(server_ids=spec.mcp_server_ids))
        warn_long_mcp_descriptions(tools)
        return tools
    except Exception as err:
        logger.warning("[data_agent] MCP 工具加载失败，跳过: %s", err)
        return []


def _resolve_mcp_tool_search_service(
    spec: AgentAssemblySpec,
    tool_tokens: int,
    model_id: str | None,
    *,
    excluded_names: frozenset[str] = frozenset(),
) -> "MCPToolSearchService | None":
    """两级加载判定：off 关闭；forced 启用；auto 按工具 token 占模型窗口比例判断。

    返回 ``MCPToolSearchService``（检索模式）或 ``None``（全量挂载模式，
    行为与两级加载引入前一致）。
    """
    if spec.mcp_tool_search == "off" or not tool_tokens:
        return None
    threshold_tokens = int(get_model_context_budget(model_id).context_window * spec.mcp_tool_search_threshold)
    if spec.mcp_tool_search == "auto" and tool_tokens <= threshold_tokens:
        return None
    from .mcp.catalog import MCPToolSearchService
    from .mcp.manager import get_mcp_tool_manager

    logger.info(
        "[data_agent] 启用 MCP 工具检索模式: mode=%s tool_tokens=%d threshold_tokens=%d",
        spec.mcp_tool_search,
        tool_tokens,
        threshold_tokens,
    )
    return MCPToolSearchService(get_mcp_tool_manager(), excluded_names=excluded_names)


def _build_memory_compression_hook(
    conversation_id: str | None,
    *,
    workspace_root: str,
    user_id: str | None,
    username: str | None,
    model_id: str | None,
) -> Callable[[list[Any]], None] | None:
    """构造压缩丢弃消息时的会话记忆立即抽取回调；无会话标识时返回 None。

    仅闭包绑定定位记忆文件所需的最小上下文，调度侧的开关与空快照守卫在
    ``schedule_memory_extraction_now`` 内统一执行。
    """
    normalized_conversation_id = str(conversation_id or "").strip()
    if not normalized_conversation_id:
        return None

    def _on_dropped_messages(dropped: list[Any]) -> None:
        schedule_memory_extraction_now(
            conversation_id=normalized_conversation_id,
            workspace_root=workspace_root,
            model_id=model_id,
            messages=dropped,
        )

    return _on_dropped_messages


def _normalize_model_id(model_id: str | None) -> str | None:
    """历史会话可能携带已删除/停用的 model_id：降级到当前默认模型，避免整轮对话报错。

    注册表是动态数据（管理页随时增删改），请求里的 model_id 只是当时的快照；
    未命中时 fail-fast 会把整轮对话卡死在「模型不存在」，降级为默认模型并告警
    是唯一可用的兜底。显式传 None（未选模型）不在此处理，由下游取默认。
    """
    if model_id is None:
        return None
    try:
        get_chat_data_model_config(model_id)
        return model_id
    except ValueError:
        fallback = get_default_chat_data_model()
        logger.warning("请求模型 %r 不在模型注册表中（已删除或停用），降级为默认模型 %r", model_id, fallback)
        return fallback


def _resolve_workspace_runtime(
    runtime: ConversationWorkspaceRuntime | None,
    options: AgentRuntimeOptions,
    user_id: str | None,
) -> ConversationWorkspaceRuntime:
    """未显式传入时构造历史回放工作区；独立函数控制装配函数复杂度。"""
    if runtime is not None:
        return runtime
    workspace_paths = ensure_conversation_workspace("_history_reader", user_id or "system")
    return ConversationWorkspaceRuntime(
        workspace_root=workspace_paths["root"],
        user_id=user_id or "system",
        backend=options.workspace_backend,
    )


def _with_mcp_control_tools(tools: list, mcp_tools: list, search_service: Any) -> list:
    """检索模式追加 search + proxy；独立函数控制装配函数复杂度。"""
    if search_service is None:
        return tools
    from .mcp.catalog import create_mcp_tool_call_tool, create_mcp_tool_search_tool

    mcp_tools_by_name = {str(tool.name): tool for tool in mcp_tools}
    return [
        *tools,
        create_mcp_tool_search_tool(search_service),
        create_mcp_tool_call_tool(mcp_tools_by_name),
    ]


def create_agent_from_spec(
    spec: AgentAssemblySpec,
    runtime_options: AgentRuntimeOptions | None = None,
    *,
    checkpointer: BaseCheckpointSaver | None = None,
    conversation_id: str | None = None,
    identity_context: IdentityContext | None = None,
    datasource_context: DatasourceContext | None = None,
    model_invocation: ModelInvocationConfig | None = None,
    workspace_runtime: ConversationWorkspaceRuntime | None = None,
    run_controls: AgentRunControls | None = None,
    persist_model_snapshot: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> CompiledStateGraph:
    """按声明式规格装配一个 LangGraph agent。

    参数按关注点分组：``identity_context``（谁在问）、``datasource_context``
    （问哪个数据域）、``model_invocation``（模型怎么调）、``run_controls``
    （run 怎么管）；新增参数必须先归入其中一组或说明为何单列。
    子智能体只继承内置工具（不含 MCP 等动态工具），且各子智能体的排除集策略
    仍由 ``tools/registry.py`` 单一事实源约束。

    ``conversation_id`` 单列：不参与 Agent 构建，仅供会话记忆压缩回调定位
    记忆文件（无会话标识的历史回放等调用方不传即关闭该回调）。
    """
    validate_agent_assembly_spec(spec)
    options = runtime_options or get_chat_data_default_runtime_options()
    identity = identity_context or IdentityContext()
    datasource = datasource_context or DatasourceContext()
    invocation = model_invocation or ModelInvocationConfig()
    user_id = identity.user_id
    model_id = _normalize_model_id(invocation.model_id)
    enable_thinking = invocation.enable_thinking
    thinking_effort = invocation.thinking_effort
    runtime = _resolve_workspace_runtime(workspace_runtime, options, user_id)
    context_request_budget = ContextRequestBudget(model_id)
    builtin_tools = assemble_tools(
        options,
        runtime,
        identity_context=identity,
        datasource_context=datasource,
        model_invocation=invocation,
        spec=spec,
        run_controls=run_controls,
    )
    mcp_tools = _load_spec_mcp_tools(spec)
    excluded_mcp_names: frozenset[str] = frozenset()
    if run_controls is not None and run_controls.disable_user_interaction:
        # 无人值守不装配审批中间件：force_ask 工具无人应答会悬挂，按 ask_user_question
        # 的先例在装配层剔除（模型不可见且不可检索）——fail-closed，而不是无人审批放行。
        excluded_mcp_names = frozenset(str(tool.name) for tool in mcp_tools if _is_forced_approval_tool(tool))
        mcp_tools = [tool for tool in mcp_tools if str(tool.name) not in excluded_mcp_names]
    tool_tokens = count_tools_tokens([*builtin_tools, *mcp_tools]) if mcp_tools else 0
    search_service = (
        _resolve_mcp_tool_search_service(spec, tool_tokens, model_id, excluded_names=excluded_mcp_names)
        if mcp_tools
        else None
    )
    tools = _with_mcp_control_tools([*builtin_tools, *mcp_tools], mcp_tools, search_service)
    logger.info(
        "[data_agent] 工具装配完成: agent=%s builtin=%d mcp=%d search_tool=%s total=%d tool_tokens=%d",
        spec.agent_id,
        len(builtin_tools),
        len(mcp_tools),
        search_service is not None,
        len(tools),
        tool_tokens,
    )
    effective_system_prompt = invocation.system_prompt or spec.build_system_prompt(
        options,
        workspace_root=runtime.workspace_root,
        memory_block=invocation.memory_block,
    )
    effective_system_prompt = _system_prompt_with_mcp_tools(
        effective_system_prompt,
        search_active=search_service is not None,
        mcp_tools=mcp_tools,
    )
    context_request_budget.configure(effective_system_prompt, tools, model_id=model_id)
    context_request_budget.validate_static_input()
    model = create_streaming_chat_model(
        model_id=model_id,
        enable_thinking=enable_thinking,
        thinking_effort=thinking_effort,
        stream_usage=True,
        llm_factory=get_chat_data_llm,
    )
    subagent_middleware = build_subagent_middleware(
        options,
        builtin_tools,
        model=model,
        model_id=model_id,
        run_controls=run_controls,
        available_skills_xml=invocation.available_skills_xml,
        memory_block=invocation.memory_block,
    )
    context_usage_middleware = create_request_context_usage_middleware(
        model_id,
        tools=tools,
        context_request_budget=context_request_budget,
        state_schema=ChatDataAgentState,
    )
    context_compression_middleware = create_context_compression_middleware(
        system_prompt=effective_system_prompt,
        tools=tools,
        model_id=model_id,
        context_request_budget=context_request_budget,
        state_schema=ChatDataAgentState,
        on_dropped_messages=_build_memory_compression_hook(
            conversation_id,
            workspace_root=runtime.workspace_root,
            user_id=identity.user_id,
            username=identity.operator_name,
            model_id=model_id,
        ),
    )
    tool_result_middleware = create_tool_result_middleware()
    image_injection_middleware = create_image_injection_middleware(
        model_id,
        state_schema=ChatDataAgentState,
    )
    tool_call_history_repair_middleware = create_tool_call_history_repair_middleware(
        state_schema=ChatDataAgentState,
    )
    steering_middleware = None
    tool_execution_fence_middleware = None
    if run_controls is not None and run_controls.run_id and run_controls.owner_token and run_controls.is_complete():
        tool_execution_fence_middleware = create_tool_execution_fence_middleware(
            run_id=run_controls.run_id,
            owner_token=run_controls.owner_token,
            inbox_service=run_controls.inbox_service,
        )
        steering_middleware = create_steering_middleware(
            run_id=run_controls.run_id,
            owner_token=run_controls.owner_token,
            inbox_service=run_controls.inbox_service,
            admit_messages=run_controls.admit_inbox_messages,
            tracker=run_controls.steering_tracker,
            state_schema=ChatDataAgentState,
        )
    # 按需审批只对交互式 chat 生效；auto 模式不装配（零开销），无人值守强制 auto
    tool_approval_middleware = _build_tool_approval_middleware(run_controls, tools)
    after_model_history_middleware = None
    if persist_model_snapshot is not None:
        after_model_history_middleware = create_after_model_history_middleware(
            persist_model_snapshot,
            state_schema=ChatDataAgentState,
        )
    middleware = build_standard_agent_middleware(
        tools,
        tool_execution_fence_middleware=tool_execution_fence_middleware,
        tool_approval_middleware=tool_approval_middleware,
        image_injection_middleware=image_injection_middleware,
        mcp_tool_discovery_middleware=(
            MCPToolDiscoveryMiddleware(
                search_active=search_service is not None,
            )
            if search_service is not None
            else None
        ),
        tool_call_history_repair_middleware=tool_call_history_repair_middleware,
        steering_middleware=steering_middleware,
        subagent_middleware=subagent_middleware,
        context_compression_middleware=context_compression_middleware,
        context_usage_middleware=context_usage_middleware,
        tool_result_middleware=tool_result_middleware,
        after_model_history_middleware=after_model_history_middleware,
        include_flags=frozenset({"response_timestamp", "tool_call_normalizer"}),
    )
    agent = create_agent(
        model=model,
        tools=tools,
        system_prompt=effective_system_prompt,
        middleware=middleware,
        state_schema=ChatDataAgentState,
        checkpointer=checkpointer,
        name=spec.agent_name,
    )
    return agent


def create_data_agent(
    runtime_options: AgentRuntimeOptions | None = None,
    *,
    checkpointer: BaseCheckpointSaver | None = None,
    **kwargs: Any,
) -> CompiledStateGraph:
    """创建 ``chat_data`` Agent（规格见 ``CHAT_DATA_AGENT_SPEC``）。"""
    return create_agent_from_spec(
        CHAT_DATA_AGENT_SPEC,
        runtime_options,
        checkpointer=checkpointer,
        **kwargs,
    )


def get_data_agent_graph(
    runtime_options: AgentRuntimeOptions | None = None,
    *,
    checkpointer: BaseCheckpointSaver | None = None,
    **kwargs: Any,
) -> CompiledStateGraph:
    """供会话读取和历史回放使用的单一 graph 入口。"""
    return create_data_agent(runtime_options, checkpointer=checkpointer, **kwargs)
