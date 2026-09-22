"""ChatData 单一 Agent 的构建与工具装配。"""

from dataclasses import dataclass
from typing import Any

from langchain.agents import create_agent

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
    # 会话技能清单（<available_skills> XML，agent_stream 每轮构建）。
    # 归「模型怎么调」组：目录本是系统提示的一部分，子智能体与主智能体一致继承。
    available_skills_xml: str | None = None
    # 会话数据源组上下文（含「当前用户已选择的执行对象数据源」成员清单）。
    # 归「模型怎么调」组：查询工具要求模型显式传 member_datasource_ids，
    # 子智能体与主智能体一致继承该清单。
    datasource_group_context: str | None = None


def assemble_tools(
    runtime_options: AgentRuntimeOptions,
    workspace_runtime: ConversationWorkspaceRuntime,
    *,
    identity_context: IdentityContext | None = None,
    datasource_context: DatasourceContext | None = None,
    model_invocation: ModelInvocationConfig | None = None,
) -> list:
    """按 ``tools/registry.py`` 注册表装配固定工具集，只有 Bash 受运行选项控制。"""
    context = ToolBuildContext(
        runtime_options=runtime_options,
        workspace_runtime=workspace_runtime,
        identity=identity_context or IdentityContext(),
        datasource=datasource_context or DatasourceContext(),
        model_invocation=model_invocation or ModelInvocationConfig(),
    )
    return [tool for spec in TOOL_SPECS for tool in spec.build(context)]


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
    "子智能体与主智能体共享技能清单，但看不到主对话历史：任务相关的技能（尤其"
    "用户自定义技能）建议在任务书中写明技能名称与 skill_file 路径。"
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
    model_id: str | None = None,
    run_controls: "AgentRunControls | None" = None,
    available_skills_xml: str | None = None,
    datasource_group_context: str | None = None,
) -> SubagentMiddleware | None:
    """按运行选项构建子智能体中间件；没有任何子智能体启用时返回 None。

    子智能体继承主智能体工具（各自扣除排除集），使用与主智能体相同的模型，
    运行在隔离上下文中；data-explore 额外扣除全部写操作工具保持只读。
    ``model_id`` 与主图同源传入：子图预算告警据此取同一 ContextBudget 的
    压缩触发阈值，保证「上下文将尽」的口径与主图一致。
    ``available_skills_xml`` 与主智能体同一会话技能清单（一致继承）：子图
    system prompt 由此注入目录段，正文仍按需 file_read；未传或为空则不注入。
    ``datasource_group_context`` 与主智能体同一会话数据源组上下文（含已选
    执行对象成员清单）：查询工具要求模型显式传 member_datasource_ids，
    清单不进子图提示词则查询派发无从得知合法 ID；未传或为空则不注入。
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
            model_id=model_id,
            available_skills_xml=available_skills_xml or "",
            datasource_group_context=datasource_group_context or "",
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
            model_id=model_id,
            available_skills_xml=available_skills_xml or "",
            datasource_group_context=datasource_group_context or "",
        ))
    if not specs:
        return None
    return SubagentMiddleware(specs, run_controls=run_controls)


def create_data_agent(
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
    """创建唯一的 ``chat_data`` Agent。

    参数按关注点分组：``identity_context``（谁在问）、``datasource_context``
    （问哪个数据域）、``model_invocation``（模型怎么调）、``run_controls``
    （run 怎么管）；新增参数必须先归入其中一组或说明为何单列。
    """
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
    tools = assemble_tools(
        options,
        runtime,
        identity_context=identity,
        datasource_context=datasource,
        model_invocation=invocation,
    )
    effective_system_prompt = invocation.system_prompt or build_unified_system_prompt(
        options,
        workspace_root=runtime.workspace_root,
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
        options, tools, model=model, model_id=model_id, run_controls=run_controls,
        available_skills_xml=invocation.available_skills_xml,
        datasource_group_context=invocation.datasource_group_context,
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
        name="chat_data_agent",
    )
    bind_context_usage_middleware(agent, context_usage_middleware)
    return agent


def get_data_agent_graph(
    runtime_options: AgentRuntimeOptions | None = None,
    *,
    checkpointer=None,
    **kwargs,
):
    """供会话读取和历史回放使用的单一 graph 入口。"""
    return create_data_agent(runtime_options, checkpointer=checkpointer, **kwargs)
