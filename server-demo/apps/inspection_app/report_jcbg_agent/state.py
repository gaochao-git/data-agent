"""
检查报告智能体状态定义
"""

from typing import TypedDict, Annotated, Optional
import operator
from langgraph.graph import add_messages
from langchain_core.messages import BaseMessage

from ..shared.models import ViolationsAnalysisResult


class JcbgState(TypedDict):
    """检查报告专用状态"""
    # 消息列表
    messages: Annotated[list[BaseMessage], add_messages]

    # 用户输入
    user_query: Optional[str]

    # 任务类型: "report" | "qa"
    task_type: Optional[str]

    # 事实认定书文件组（输入数据）
    violation_file_groups: Annotated[Optional[list[dict]], operator.add]

    # 检索分析结果（标准化数据）
    violations_analysis_result: Optional[ViolationsAnalysisResult]

    # 人工审核标记
    human_approved_retrieve: Optional[bool]

    # 任务进度
    task_progress: Optional[list[dict]]
