"""
信息简表智能体状态定义
"""

from typing import TypedDict, Annotated, Optional
from langgraph.graph import add_messages
from langchain_core.messages import BaseMessage

from ..shared.models import ViolationsAnalysisResult


class XxJbState(TypedDict):
    """信息简表专用状态"""
    messages: Annotated[list[BaseMessage], add_messages]
    user_query: Optional[str]
    task_type: Optional[str]
    violations_analysis_result: Optional[ViolationsAnalysisResult]
    task_progress: Optional[list[dict]]
