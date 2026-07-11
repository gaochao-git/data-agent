"""
智能问数主图定义

标准 Agent ↔ Tools 循环

支持语义查询模式：
- query 模式统一添加指标集选择、DSL2SQL 和执行工具
- 是否调用指标集选择工具由同一套提示词根据当前上下文决定
"""

from langgraph.graph import StateGraph, START
from langgraph.prebuilt import ToolNode, tools_condition

from utils.logger import get_logger
from .state import ChatDataAgentState
from .nodes import create_agent_node
from .tools import TOOLS, create_semantic_config_selection_tools
from .tools.readonly_skill_load import create_readonly_skill_load_tool

logger = get_logger(__name__)


def _format_tool_error(exc: Exception) -> str:
    """格式化工具异常，避免前端出现空白错误信息。"""
    error_text = str(exc).strip() or repr(exc).strip()
    if not error_text:
        error_text = "工具执行失败，请检查参数后重试"
    logger.error(f"[ToolNode] 工具执行异常: {error_text}", exc_info=True)
    return error_text


def get_chat_data_graph(
    checkpointer=None,
    semantic_config: dict | None = None,
    datasource_id: str | None = None,
    model_id: str | None = None,
    enable_thinking: bool | None = None,
    user_id: str | None = None,
    available_skills_context: str | None = None,
):
    """
    编译图（带持久化）

    Args:
        checkpointer: LangGraph checkpointer
        semantic_config: 语义配置（可选）
            传入时在 system prompt 中注入当前指标集信息；工具列表仍使用统一语义工具链

    Returns:
        编译后的 LangGraph 图
    """
    semantic_tool_kwargs = {
        "model_id": model_id,
        "semantic_config": semantic_config,
        "user_id": user_id,
    }
    if datasource_id:
        semantic_tool_kwargs["datasource_id"] = datasource_id
    tools = [
        create_readonly_skill_load_tool(user_id=user_id or ""),
        *create_semantic_config_selection_tools(**semantic_tool_kwargs),
        *TOOLS,
    ]

    # 创建 agent 节点，传入 semantic_config（而不是 semantic_prompt）
    agent_node = create_agent_node(
        tools,
        semantic_config,
        model_id=model_id,
        enable_thinking=enable_thinking,
        available_skills_context=available_skills_context,
    )

    # 构建图
    builder = StateGraph(ChatDataAgentState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(tools, handle_tool_errors=_format_tool_error))

    # 边
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", tools_condition)  # agent → tools 或 END
    builder.add_edge("tools", "agent")  # tools → agent

    return builder.compile(checkpointer=checkpointer)


# 编译默认主图（无持久化，无语义模式）- 用于测试
graph = get_chat_data_graph()
