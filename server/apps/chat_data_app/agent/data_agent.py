"""ChatData Agent 的构建与工具装配。

装配以 ``AgentAssemblySpec`` 声明式规格驱动：内置工具按 ``tools/registry.py``
注册表装配后按规格做工具名级筛选，system prompt 与 LangGraph agent 名由规格
提供。``chat_data`` 是第一个声明式规格；后续装配型智能体（如智能运维）按同一
规格接入，不再复制装配函数。
"""

from dataclasses import dataclass
from typing import Any, Callable

from langchain.agents import create_agent

from utils.logger import get_logger

from ..agent.context.context_budget import ContextRequestBudget
from .workspace.runtime import ConversationWorkspaceRuntime, ensure_conversation_workspace
from ..services.llm_config import get_chat_data_llm
from .agent_runtime import (
    build_standard_agent_middleware,
    create_streaming_chat_model,
)
from .agent_state import ChatDataAgentState
from .runtime_options import AgentRuntimeOptions, get_chat_data_default_runtime_options
from .middleware.context_compression import create_context_compression_middleware
from .middleware.subagents import SubagentMiddleware, SubagentSpec
from .middleware.context_usage import (
    bind_context_usage_middleware,
    create_request_context_usage_middleware,
)
from .middleware.after_model_history import create_after_model_history_middleware
from .middleware.image_injection import create_image_injection_middleware
from .middleware.tool_result import create_tool_result_middleware
from .middleware.tool_execution_fence import create_tool_execution_fence_middleware
from .middleware.tool_call_history_repair import create_tool_call_history_repair_middleware
from .middleware.steering import create_steering_middleware
from .prompts.chat_data import (
    DATA_EXPLORE_SUBAGENT_PROMPT,
    PLAN_SUBAGENT_PROMPT,
    build_unified_system_prompt,
)
from .tools.registry import (
    DATA_EXPLORE_EXTRA_EXCLUDED_TOOL_NAMES,
    PLAN_EXTRA_EXCLUDED_TOOL_NAMES,
    SUBAGENT_BASE_EXCLUDED_TOOL_NAMES,
    TOOL_SPECS,
    ToolBuildContext,
)

logger = get_logger(__name__)


@dataclass(frozen=True)
class AgentRunControls:
    """一次 agent run 的控制面对象。

    工具租约围栏（tool_execution_fence）与 steering 注入总是成组出现：
    五个字段全部非空才启用，缺任何一个都视为本次运行不支持中断续跑，
    静默降级为无围栏、无 steering 的普通运行。
    """

    run_id: str
    owner_token: str
    inbox_service: Any
    admit_inbox_messages: Any
    steering_tracker: Any

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
    datasource_group_context: str | None = None


@dataclass(frozen=True)
class ModelInvocationConfig:
    """模型怎么调——与业务无关的 LLM 调用参数。"""

    system_prompt: str | None = None
    model_id: str | None = None
    enable_thinking: bool | None = None
    thinking_effort: str | None = None


@dataclass(frozen=True)
class AgentAssemblySpec:
    """一个可装配 Agent 的声明式规格：工具选择 + prompt + 运行选项边界。

    - ``tool_includes`` / ``tool_excludes``：对 ``tools/registry.py`` 内置注册表
      的工具名级筛选；``tool_includes=None`` 表示全部内置工具，``tool_excludes``
      优先级更高。子智能体仍只使用内置工具（MCP 等动态工具不下发给子智能体）。
    - ``enable_mcp_tools``：是否在装配时枚举 MCP/插件声明的动态工具
      （见 ``agent/mcp/manager.py``）；未配置任何 MCP server 时为空、零开销。
    - ``build_system_prompt``：``(runtime_options, workspace_root) -> prompt``，
      每次装配都会调用，prompt 必须由规格自身决定。
    """

    agent_id: str
    agent_name: str
    build_system_prompt: Callable[[AgentRuntimeOptions, str], str]
    tool_includes: frozenset[str] | None = None
    tool_excludes: frozenset[str] = frozenset()
    enable_mcp_tools: bool = True


def validate_agent_assembly_spec(spec: AgentAssemblySpec) -> None:
    """规格声明的工具名必须都已登记注册表，且 include/exclude 不得冲突。"""
    from .tools.registry import provided_tool_names

    registered = provided_tool_names()
    unknown_includes = set(spec.tool_includes or ()) - registered
    if unknown_includes:
        raise ValueError(
            f"Agent 装配规格 {spec.agent_id} 的 tool_includes 引用了未注册工具: "
            f"{sorted(unknown_includes)}"
        )
    unknown_excludes = set(spec.tool_excludes) - registered
    if unknown_excludes:
        raise ValueError(
            f"Agent 装配规格 {spec.agent_id} 的 tool_excludes 引用了未注册工具: "
            f"{sorted(unknown_excludes)}"
        )
    if spec.tool_includes is not None:
        conflict = spec.tool_includes & spec.tool_excludes
        if conflict:
            raise ValueError(
                f"Agent 装配规格 {spec.agent_id} 的工具同时出现在 includes/excludes: "
                f"{sorted(conflict)}"
            )


def _filter_tools_by_spec(tools: list, spec: AgentAssemblySpec) -> list:
    """按规格对内置工具做工具名级筛选；excludes 优先于 includes。"""
    return [
        tool for tool in tools
        if str(getattr(tool, "name", "") or "") not in spec.tool_excludes
        and (
            spec.tool_includes is None
            or str(getattr(tool, "name", "") or "") in spec.tool_includes
        )
    ]


def assemble_tools(
    runtime_options: AgentRuntimeOptions,
    workspace_runtime: ConversationWorkspaceRuntime,
    *,
    identity_context: IdentityContext | None = None,
    datasource_context: DatasourceContext | None = None,
    model_invocation: ModelInvocationConfig | None = None,
    spec: AgentAssemblySpec | None = None,
) -> list:
    """按 ``tools/registry.py`` 注册表装配工具集，只有 Bash 受运行选项控制。

    传入 ``spec`` 时在注册表装配结果上再按规格做工具名级筛选（内置工具）。
    """
    context = ToolBuildContext(
        runtime_options=runtime_options,
        workspace_runtime=workspace_runtime,
        identity=identity_context or IdentityContext(),
        datasource=datasource_context or DatasourceContext(),
        model_invocation=model_invocation or ModelInvocationConfig(),
    )
    tools = [tool for tool_spec in TOOL_SPECS for tool in tool_spec.build(context)]
    if spec is not None:
        validate_agent_assembly_spec(spec)
        tools = _filter_tools_by_spec(tools, spec)
    return tools


# 子智能体工具可见性策略（SUBAGENT_BASE_EXCLUDED_TOOL_NAMES /
# DATA_EXPLORE_EXTRA_EXCLUDED_TOOL_NAMES / PLAN_EXTRA_EXCLUDED_TOOL_NAMES）
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
    "子智能体看不到 available_skills 技能清单：任务与本轮可用技能（尤其用户自定义技能）"
    "相关时，必须在任务书中写明技能名称与 skill_file 路径，否则无从读取。"
)


def _filter_subagent_tools(tools: list, excluded: frozenset) -> list:
    """按排除集过滤子智能体工具。"""
    return [
        tool for tool in tools
        if str(getattr(tool, "name", "") or "") not in excluded
    ]


def build_subagent_middleware(
    runtime_options: AgentRuntimeOptions,
    tools: list,
    *,
    model,
    run_controls: "AgentRunControls | None" = None,
) -> SubagentMiddleware | None:
    """按运行选项构建子智能体中间件；没有任何子智能体启用时返回 None。

    子智能体继承主智能体工具（各自扣除排除集），使用与主智能体相同的模型，
    运行在隔离上下文中；data-explore 额外扣除全部写操作工具保持只读。
    """
    specs: list[SubagentSpec] = []
    if runtime_options.explore_subagent_enabled:
        specs.append(SubagentSpec(
            name=DATA_EXPLORE_SUBAGENT_NAME,
            description=DATA_EXPLORE_SUBAGENT_DESCRIPTION,
            system_prompt=DATA_EXPLORE_SUBAGENT_PROMPT,
            tools=_filter_subagent_tools(
                tools,
                SUBAGENT_BASE_EXCLUDED_TOOL_NAMES | DATA_EXPLORE_EXTRA_EXCLUDED_TOOL_NAMES,
            ),
            model=model,
        ))
    if runtime_options.plan_subagent_enabled:
        specs.append(SubagentSpec(
            name=PLAN_SUBAGENT_NAME,
            description=PLAN_SUBAGENT_DESCRIPTION,
            system_prompt=PLAN_SUBAGENT_PROMPT,
            tools=_filter_subagent_tools(
                tools,
                SUBAGENT_BASE_EXCLUDED_TOOL_NAMES | PLAN_EXTRA_EXCLUDED_TOOL_NAMES,
            ),
            model=model,
        ))
    if not specs:
        return None
    return SubagentMiddleware(specs, run_controls=run_controls)


def _chat_data_system_prompt(options: AgentRuntimeOptions, workspace_root: str) -> str:
    return build_unified_system_prompt(options, workspace_root=workspace_root)


CHAT_DATA_AGENT_SPEC = AgentAssemblySpec(
    agent_id="chat_data",
    agent_name="chat_data_agent",
    build_system_prompt=_chat_data_system_prompt,
)


def _load_spec_mcp_tools(spec: AgentAssemblySpec) -> list:
    """装配规格允许时枚举 MCP/插件动态工具；任何失败都降级为空集，不阻断装配。"""
    if not spec.enable_mcp_tools:
        return []
    try:
        from .mcp.manager import load_mcp_tools

        return list(load_mcp_tools())
    except Exception as err:
        logger.warning("[data_agent] MCP 工具加载失败，跳过: %s", err)
        return []


def create_agent_from_spec(
    spec: AgentAssemblySpec,
    runtime_options: AgentRuntimeOptions | None = None,
    *,
    checkpointer=None,
    identity_context: IdentityContext | None = None,
    datasource_context: DatasourceContext | None = None,
    model_invocation: ModelInvocationConfig | None = None,
    workspace_runtime: ConversationWorkspaceRuntime | None = None,
    run_controls: AgentRunControls | None = None,
    persist_model_snapshot=None,
):
    """按声明式规格装配一个 LangGraph agent。

    参数按关注点分组：``identity_context``（谁在问）、``datasource_context``
    （问哪个数据域）、``model_invocation``（模型怎么调）、``run_controls``
    （run 怎么管）；新增参数必须先归入其中一组或说明为何单列。

    子智能体只继承内置工具（不含 MCP 等动态工具），且各子智能体的排除集策略
    仍由 ``tools/registry.py`` 单一事实源约束。
    """
    validate_agent_assembly_spec(spec)
    options = runtime_options or get_chat_data_default_runtime_options()
    identity = identity_context or IdentityContext()
    datasource = datasource_context or DatasourceContext()
    invocation = model_invocation or ModelInvocationConfig()
    user_id = identity.user_id
    model_id = invocation.model_id
    enable_thinking = invocation.enable_thinking
    thinking_effort = invocation.thinking_effort
    runtime = workspace_runtime
    if runtime is None:
        workspace_paths = ensure_conversation_workspace("_history_reader", user_id or "system")
        runtime = ConversationWorkspaceRuntime(
            workspace_root=workspace_paths["root"],
            user_id=user_id or "system",
            backend=options.workspace_backend,
        )
    context_request_budget = ContextRequestBudget(model_id)
    builtin_tools = assemble_tools(
        options,
        runtime,
        identity_context=identity,
        datasource_context=datasource,
        model_invocation=invocation,
        spec=spec,
    )
    tools = builtin_tools + _load_spec_mcp_tools(spec)
    effective_system_prompt = invocation.system_prompt or spec.build_system_prompt(
        options,
        runtime.workspace_root,
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
    subagent_middleware = build_subagent_middleware(options, builtin_tools, model=model, run_controls=run_controls)
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
    if run_controls is not None and run_controls.is_complete():
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
    after_model_history_middleware = None
    if persist_model_snapshot is not None:
        after_model_history_middleware = create_after_model_history_middleware(
            persist_model_snapshot,
            state_schema=ChatDataAgentState,
        )
    middleware = build_standard_agent_middleware(
        tools,
        tool_execution_fence_middleware=tool_execution_fence_middleware,
        image_injection_middleware=image_injection_middleware,
        tool_call_history_repair_middleware=tool_call_history_repair_middleware,
        steering_middleware=steering_middleware,
        subagent_middleware=subagent_middleware,
        context_compression_middleware=context_compression_middleware,
        context_usage_middleware=context_usage_middleware,
        tool_result_middleware=tool_result_middleware,
        after_model_history_middleware=after_model_history_middleware,
        include_response_timestamp=True,
        include_tool_call_normalizer=True,
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
    bind_context_usage_middleware(agent, context_usage_middleware)
    return agent


def create_data_agent(
    runtime_options: AgentRuntimeOptions | None = None,
    *,
    checkpointer=None,
    **kwargs,
):
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
    checkpointer=None,
    **kwargs,
):
    """供会话读取和历史回放使用的单一 graph 入口。"""
    return create_data_agent(runtime_options, checkpointer=checkpointer, **kwargs)
