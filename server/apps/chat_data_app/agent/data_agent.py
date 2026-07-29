"""ChatData 单一 Agent 的构建与工具装配。"""

from langchain.agents import create_agent

from ..services.context_budget import ToolResultBudget
from ..services.conversation_workspace import ConversationWorkspaceRuntime, ensure_conversation_workspace
from ..services.llm_config import get_chat_data_llm
from .agent_runtime import (
    build_standard_agent_middleware,
    create_semantic_selection_tools_bundle,
    create_streaming_chat_model,
)
from .agent_state import ChatDataAgentState
from .runtime_options import AgentRuntimeOptions, get_chat_data_default_runtime_options
from .middleware.context_compression import create_context_compression_middleware
from .middleware.context_usage import (
    bind_context_usage_middleware,
    create_request_context_usage_middleware,
)
from .middleware.tool_result import create_tool_result_middleware
from .prompts.unified import build_unified_system_prompt
from .tools.calculator import calculator_tool
from .tools.current_time import get_current_time_tool
from .tools.dsl2sql import create_dsl2sql_tool
from .tools.file_edit import create_file_edit_tool
from .tools.file_read import create_file_read_tool
from .tools.list_dir import create_list_dir_tool
from .tools.present_artifacts import create_present_artifacts_tool
from .tools.bash_run import create_bash_run_tool
from .tools.semantic_query import semantic_query_tool
from .tools.skill_load import create_skill_load_tool


def assemble_tools(
    runtime_options: AgentRuntimeOptions,
    workspace_runtime: ConversationWorkspaceRuntime,
    *,
    datasource_id: str | None = None,
    semantic_config: dict | None = None,
    semantic_config_id: str | None = None,
    user_id: str = "system",
    operator_name: str | None = None,
    model_id: str | None = None,
    tool_result_budget: ToolResultBudget | None = None,
    datasource_group_context: str | None = None,
) -> list:
    """注册单一 Agent 的固定工具集，只有 Bash 受运行选项控制。"""
    selection_bundle = create_semantic_selection_tools_bundle(
        model_id=model_id,
        datasource_id=datasource_id,
        semantic_config=semantic_config,
        include_query_tools=False,
        user_id=user_id,
        tool_result_budget=tool_result_budget,
        datasource_group_context=datasource_group_context,
    )
    tools = [
        create_skill_load_tool(workspace_runtime, user_id=user_id),
        create_file_read_tool(workspace_runtime),
        create_file_edit_tool(workspace_runtime),
        create_list_dir_tool(workspace_runtime),
        create_present_artifacts_tool(workspace_runtime),
        get_current_time_tool,
        *selection_bundle,
        create_dsl2sql_tool(
            datasource_id,
            semantic_config_id,
            user_id=user_id,
            operator_name=operator_name,
        ),
        semantic_query_tool,
        calculator_tool,
    ]
    if runtime_options.bash_enabled:
        tools.append(create_bash_run_tool(workspace_runtime))
    return tools


def create_data_agent(
    runtime_options: AgentRuntimeOptions | None = None,
    *,
    checkpointer=None,
    semantic_config: dict | None = None,
    datasource_id: str | None = None,
    semantic_config_id: str | None = None,
    system_prompt: str | None = None,
    model_id: str | None = None,
    enable_thinking: bool | None = None,
    user_id: str | None = None,
    operator_name: str | None = None,
    datasource_group_context: str | None = None,
    workspace_runtime: ConversationWorkspaceRuntime | None = None,
):
    """创建唯一的 ``chat_data`` Agent。"""
    options = runtime_options or get_chat_data_default_runtime_options()
    runtime = workspace_runtime
    if runtime is None:
        workspace_paths = ensure_conversation_workspace("_history_reader", user_id or "system")
        runtime = ConversationWorkspaceRuntime(
            workspace_root=workspace_paths["root"],
            user_id=user_id or "system",
            backend=options.workspace_backend,
        )
    tool_result_budget = ToolResultBudget(model_id)
    tools = assemble_tools(
        options,
        runtime,
        datasource_id=datasource_id,
        semantic_config=semantic_config,
        semantic_config_id=semantic_config_id,
        user_id=user_id or "",
        operator_name=operator_name,
        model_id=model_id,
        tool_result_budget=tool_result_budget,
        datasource_group_context=datasource_group_context,
    )
    effective_system_prompt = system_prompt or build_unified_system_prompt(
        options,
        semantic_config=semantic_config,
        workspace_root=runtime.workspace_root,
    )
    context_usage_middleware = create_request_context_usage_middleware(
        model_id,
        tools=tools,
        tool_result_budget=tool_result_budget,
        state_schema=ChatDataAgentState,
    )
    context_compression_middleware = create_context_compression_middleware(
        system_prompt=effective_system_prompt,
        tools=tools,
        model_id=model_id,
        tool_result_budget=tool_result_budget,
        state_schema=ChatDataAgentState,
    )
    tool_result_middleware = create_tool_result_middleware(tool_result_budget)
    middleware = build_standard_agent_middleware(
        tools,
        context_compression_middleware=context_compression_middleware,
        context_usage_middleware=context_usage_middleware,
        tool_result_middleware=tool_result_middleware,
        include_response_timestamp=True,
        include_tool_call_normalizer=True,
    )
    agent = create_agent(
        model=create_streaming_chat_model(
            model_id=model_id,
            enable_thinking=enable_thinking,
            stream_usage=True,
            llm_factory=get_chat_data_llm,
        ),
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
