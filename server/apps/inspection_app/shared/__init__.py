"""
共享组件模块

包含所有智能体共用的：
- 数据模型（Pydantic 模型）
- 任务管理器
- 检索服务
- 报告工具
- 报告生成器
"""

from .models import (
    LegalProvision,
    EvidenceItem,
    HistoricalCase,
    ViolationData,
    ViolationsAnalysisResult,
    ViolationFileGroup,
)
from .task_manager import TaskManager
from .retrieval_service import retrieval_service
from .report_utils import build_violations_analysis_result

__all__ = [
    # 数据模型
    "LegalProvision",
    "EvidenceItem",
    "HistoricalCase",
    "ViolationData",
    "ViolationsAnalysisResult",
    "ViolationFileGroup",
    # 工具
    "TaskManager",
    "retrieval_service",
    "build_violations_analysis_result",
]
