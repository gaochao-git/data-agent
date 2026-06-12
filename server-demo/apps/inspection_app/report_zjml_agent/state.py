"""
证据目录智能体状态定义
"""

from typing import TypedDict, Annotated, Optional
from langgraph.graph import add_messages
from langchain_core.messages import BaseMessage

from ..shared.models import ViolationsAnalysisResult


class ZjMlState(TypedDict):
    """证据目录专用状态"""
    messages: Annotated[list[BaseMessage], add_messages]
    user_query: Optional[str]
    task_type: Optional[str]
    violations_analysis_result: Optional[ViolationsAnalysisResult]
    task_progress: Optional[list[dict]]
    human_approved_retrieve: Optional[bool]  # 人工审核检索结果是否通过
