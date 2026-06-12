"""
事实认定分析智能体状态定义
"""

from typing import TypedDict, Annotated, Optional
from langgraph.graph import add_messages
from langchain_core.messages import BaseMessage

from ..shared.models import ViolationsAnalysisResult


class FactAnalysisState(TypedDict):
    """事实认定分析专用状态"""
    # 消息历史
    messages: Annotated[list[BaseMessage], add_messages]

    # 用户输入的违法事实描述
    user_query: Optional[str]

    # 任务类型: "violation_analysis" | "qa"
    task_type: Optional[str]

    # 临时构造的文件分组（用于检索）
    violation_file_groups: Optional[list[dict]]

    # 分析结果（包含法律检索数据）
    violations_analysis_result: Optional[ViolationsAnalysisResult]

    # 任务进度
    task_progress: Optional[list[dict]]
