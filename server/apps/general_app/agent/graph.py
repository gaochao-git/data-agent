"""
通用助手图定义
"""

from langgraph.graph import StateGraph, START, END

from .state import GeneralAssistantState
from .nodes import chat_node
from utils.lang_util.checkpoint import DB_PATH, get_checkpointer


def get_graph(checkpointer=None):
    """构建并返回对话图"""
    builder = StateGraph(GeneralAssistantState)

    # 添加节点
    builder.add_node("chat", chat_node)

    # 添加边
    builder.add_edge(START, "chat")
    builder.add_edge("chat", END)

    # 编译图
    return builder.compile(checkpointer=checkpointer)


# get_checkpointer 和 DB_PATH 从 utils.lang_util.conversation 导入并重新导出
__all__ = ['get_graph', 'get_checkpointer', 'DB_PATH']
