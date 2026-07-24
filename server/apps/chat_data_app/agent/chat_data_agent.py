"""
智能问数 Agent 定义

LangChain create_agent 标准 Agent ↔ Tools 循环

支持语义查询模式：
- query 模式统一添加指标集选择、DSL2SQL 和执行工具
- 是否调用指标集选择工具由同一套提示词根据当前上下文决定
"""

from typing import Annotated, Any

from langchain.agents import AgentState, create_agent
from typing_extensions import TypedDict

from ..services.context_budget import ToolResultBudget
from .agent_runtime import build_standard_agent_middleware, create_semantic_selection_tools_bundle, create_streaming_chat_model
from .middleware.context_compression import create_context_compression_middleware
from .middleware.context_usage import create_chat_data_context_usage_middleware
from .tools.calculator import calculator_tool
from .tools.current_time import get_current_time_tool
from .tools.readonly_skill_load import create_readonly_skill_load_tool
from .tools.semantic_query import semantic_query_tool


def merge_reasoning_segments(existing: list[dict] | None, new: list[dict] | None) -> list[dict]:
    """追加模型原生思考分段，供历史会话恢复。"""
    return list(existing or []) + list(new or [])


def keep_conversation_datasource_id(existing: str | None, new: str | None) -> str | None:
    """数据源组首次绑定后不可被后续轮次覆盖。"""
    return existing or new


class ExecutionContext(TypedDict, total=False):
    """工具执行所需的数据源和执行对象范围。"""

    datasource_id: str | None
    available_member_datasource_ids: list[str]
    matched_member_datasource_ids: list[str]


class PromptContext(TypedDict, total=False):
    """注入 system prompt 的只读上下文。"""

    datasource_group_context: str
    semantic_config_directory_info: str


class ChatDataAgentState(AgentState, total=False):
    """智能问数 Agent 状态。"""

    user_id: str | None
    operator_name: str | None
    conversation_id: str | None
    conversation_datasource_id: Annotated[str | None, keep_conversation_datasource_id]
    execution_context: ExecutionContext
    prompt_context: PromptContext
    semantic_config_id: str | None
    semantic_config: dict[str, Any] | None
    token_usage: dict[str, Any] | None
    context_compression: dict[str, Any] | None
    context_summary: str | None
    reasoning_segments: Annotated[list[dict[str, Any]], merge_reasoning_segments]


def create_chat_data_agent(
    checkpointer=None,
    semantic_config: dict | None = None,
    datasource_id: str | None = None,
    system_prompt: str | None = None,
    model_id: str | None = None,
    enable_thinking: bool | None = None,
    user_id: str | None = None,
):
    """
    创建智能问数 Agent（带持久化）

    Args:
        checkpointer: LangGraph checkpointer
        semantic_config: 语义配置（可选）
            传入时在 system prompt 中注入当前指标集信息；工具列表仍使用统一语义工具链

    Returns:
        LangChain create_agent 返回的可流式执行 Agent
    """
    tool_result_budget = ToolResultBudget(model_id)
    effective_system_prompt = system_prompt or "你是智能问数助手，请基于当前会话上下文和可用工具回答用户问题。"
    tools = [
        create_readonly_skill_load_tool(user_id=user_id or ""),
        *create_semantic_selection_tools_bundle(
            model_id=model_id,
            datasource_id=datasource_id,
            semantic_config=semantic_config,
            user_id=user_id,
            tool_result_budget=tool_result_budget,
        ),
        semantic_query_tool,
        calculator_tool,
        get_current_time_tool,
    ]

    context_usage_middleware = create_chat_data_context_usage_middleware(
        tools,
        model_id=model_id,
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
    middleware = build_standard_agent_middleware(
        tools,
        context_compression_middleware=context_compression_middleware,
        context_usage_middleware=context_usage_middleware,
        include_response_timestamp=True,
        include_tool_call_normalizer=True,
    )

    return create_agent(
        model=create_streaming_chat_model(
            model_id=model_id,
            enable_thinking=enable_thinking,
            stream_usage=True,
        ),
        tools=tools,
        system_prompt=effective_system_prompt,
        middleware=middleware,
        state_schema=ChatDataAgentState,
        checkpointer=checkpointer,
        name="chat_data_agent",
    )


def get_chat_data_graph(*args, **kwargs):
    """注册中心历史接口：返回智能问数 Agent。"""
    return create_chat_data_agent(*args, **kwargs)
