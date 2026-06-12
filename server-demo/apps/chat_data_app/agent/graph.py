"""
SQL Generator 图定义

标准 Agent ↔ Tools 循环
Human-in-the-Loop 通过工具内部的 interrupt() 函数实现

支持语义模式：
- 当传入 semantic_config 时，添加 dsl2sql_tool 和 validate_sql_tool 工具
- 否则使用默认的 NL2SQL 工具集
"""

from langgraph.graph import StateGraph, START
from langgraph.prebuilt import ToolNode, tools_condition

from utils.logger import get_logger
from .state import SqlGeneratorState
from .nodes import create_agent_node
from .tools import TOOLS, create_execute_semantic_tool

logger = get_logger(__name__)


def _format_tool_error(exc: Exception) -> str:
    """格式化工具异常，避免前端出现空白错误信息。"""
    error_text = str(exc).strip() or repr(exc).strip()
    if not error_text:
        error_text = "工具执行失败，请检查参数后重试"
    logger.error(f"[ToolNode] 工具执行异常: {error_text}", exc_info=True)
    return error_text


def get_graph(checkpointer=None, semantic_config: dict | None = None, model_id: str | None = None):
    """
    编译图（带持久化）

    Args:
        checkpointer: LangGraph checkpointer
        semantic_config: 语义配置（可选）
            传入时会添加 dsl2sql_tool 和 validate_sql_tool 工具，并在 system prompt 中注入语义信息

    Returns:
        编译后的 LangGraph 图
    """
    # 根据是否有语义配置，构建工具列表
    if semantic_config:
        # 语义模式：添加语义层专用工具（生成SQL + 验证SQL）
        semantic_tools = create_execute_semantic_tool(semantic_config)  # 返回 [dsl2sql_tool, validate_sql_tool]
        # 保留 TOOLS 中的所有工具（包括 execute_sql_tool），因为 execute_sql_tool 是通用的
        tools = semantic_tools + TOOLS
    else:
        # 普通模式：使用默认工具集
        tools = TOOLS
        semantic_config = None

    # 创建 agent 节点，传入 semantic_config（而不是 semantic_prompt）
    agent_node = create_agent_node(tools, semantic_config, model_id=model_id)

    # 构建图
    builder = StateGraph(SqlGeneratorState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(tools, handle_tool_errors=_format_tool_error))

    # 边
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", tools_condition)  # agent → tools 或 END
    builder.add_edge("tools", "agent")  # tools → agent

    return builder.compile(checkpointer=checkpointer)


# 编译默认图（无持久化，无语义模式）- 用于测试
graph = get_graph()
