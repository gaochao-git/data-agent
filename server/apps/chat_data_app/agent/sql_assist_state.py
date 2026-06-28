"""SQL 助手状态定义。"""

from typing import Annotated
from typing_extensions import TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class SqlAssistState(TypedDict, total=False):
    """SQL 助手只保存生成 SQL 必需的上下文。"""

    messages: Annotated[list[BaseMessage], add_messages]
    tables_ddl_info: str
