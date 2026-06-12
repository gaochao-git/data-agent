"""
通用助手状态定义
"""

from typing import Annotated
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages


class GeneralAssistantState(TypedDict):
    """对话状态

    注意：只有 messages 会被 checkpointer 持久化
    文件上下文不在 state 中，而是在节点中按需从数据库加载
    """
    messages: Annotated[list, add_messages]
