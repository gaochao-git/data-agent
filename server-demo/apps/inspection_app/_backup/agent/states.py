"""
LangGraph 状态定义
符合 LangGraph 官方最佳实践：使用共享状态设计
"""

from typing import TypedDict, Annotated, Optional, Union
import operator
from langgraph.graph import add_messages
from langchain_core.messages import BaseMessage
from pydantic import BaseModel, Field


# ============ 标准数据模型（所有报告的数据源） ============

class LegalProvision(BaseModel):
    """法律条款模型（违反条款、处罚条款共用）"""
    law_name: str = Field(description="法律名称，如：《非金融机构支付服务管理办法》")
    law_item_name: str = Field(description="条款名称，如：第三条、第四十二条")
    law_item_content: str = Field(description="条款具体内容")
    relevance_score: float = Field(default=1.0, description="相关度分数（0-1），RAG检索时使用")


class EvidenceItem(BaseModel):
    """单条证据信息"""
    evidence_name_code: str = Field(description="证据名称及编号")
    evidence_source: str = Field(description="证据来源")
    evidence_form: str = Field(description="证据表现形式，如：电子数据、书证")
    evidence_truth: str = Field(description="证据真实性转述")
    provider_sign: Optional[str] = Field(default=None, description="证据提供人签字及日期")
    remark: Optional[str] = Field(default=None, description="备注")


class HistoricalCase(BaseModel):
    """历史案例模型（完整执法案例）"""
    # 基本信息
    case_id: str = Field(description="案例ID/案件编号")
    case_title: str = Field(description="案例标题")
    case_summary: str = Field(description="案例摘要")

    # 执法主体信息
    inspected_person: Optional[str] = Field(default=None, description="被检查人")
    inspector: Optional[str] = Field(default=None, description="检查人")
    inspection_date: Optional[str] = Field(default=None, description="检查时间")
    inspection_location: Optional[str] = Field(default=None, description="检查地点")

    # 违法事实和法律依据
    violation_facts: str = Field(description="违法事实")
    violated_provisions: Optional[list[dict]] = Field(
        default_factory=list,
        description="违反的法律条款（简化版）"
    )
    penalty_provisions: Optional[list[dict]] = Field(
        default_factory=list,
        description="处罚法律条款（简化版）"
    )

    # 裁量和处罚
    discretionary_benchmarks: Optional[list[str]] = Field(
        default_factory=list,
        description="裁量基准"
    )
    rectification_measures: Optional[list[str]] = Field(
        default_factory=list,
        description="整改措施"
    )
    penalty_result: str = Field(description="处罚结果")

    # 参考价值
    reference_value: str = Field(description="参考价值说明")
    similarity_score: float = Field(default=1.0, description="相似度分数（0-1）")


class ViolationData(BaseModel):
    """单个违法行为的完整数据（所有报告的标准数据源）"""
    violation_id: int = Field(description="违法行为ID")
    title: str = Field(description="违法行为标题/检查内容")
    facts: str = Field(description="违法事实描述")

    # 法律检索数据（5项核心检索）
    violated_provisions: list[LegalProvision] = Field(
        default_factory=list,
        description="违反的法律条款列表"
    )
    penalty_provisions: list[LegalProvision] = Field(
        default_factory=list,
        description="处罚法律条款列表"
    )
    discretionary_benchmarks: list[str] = Field(
        default_factory=list,
        description="裁量基准列表"
    )
    rectification_measures: list[str] = Field(
        default_factory=list,
        description="整改措施列表"
    )
    penalty_recommendations: list[str] = Field(
        default_factory=list,
        description="处罚建议列表"
    )

    # 历史案例参考 ⭐ 新增
    historical_cases: list[HistoricalCase] = Field(
        default_factory=list,
        description="历史案例列表（用于事实认定分析和参考）"
    )

    # 证据信息（仅报告生成时使用）
    evidence_list: list[EvidenceItem] = Field(
        default_factory=list,
        description="证据清单"
    )
    evidence_process: Optional[str] = Field(
        default=None,
        description="取证过程记录"
    )
    evidence_record_file: Optional[dict] = Field(
        default=None,
        description="取证记录表文件信息（每个违法行为独立的取证记录表）"
    )


class ViolationsAnalysisResult(BaseModel):
    """
    违规分析完整结果 - 所有报告的标准数据源

    这是系统的核心领域模型，集成了：
    - 案件基本信息
    - 事实认定数据
    - 法律检索结果
    - 证据信息
    - 原始文件元数据

    所有报告生成器（检查报告、整改通知书、证据目录等）都从此结构提取数据
    """
    # ============ 案件基本信息 ============
    case_number: Optional[str] = Field(default=None, description="案件编号（可选）")
    inspected_person: str = Field(description="被检查人名称")
    inspector: Optional[str] = Field(default=None, description="检查人（可选）")
    inspection_date: Optional[str] = Field(default=None, description="检查时间（可选）")
    inspection_location: Optional[str] = Field(default=None, description="检查地点（可选）")

    # ============ 违法行为数据 ============
    violations: list[ViolationData] = Field(description="违法行为列表")

    # ============ 元数据 ============
    source_files: Optional[list[dict]] = Field(
        default_factory=list,
        description="原始文件分组信息（来自 violation_file_groups），包含文件名、上传时间等元数据"
    )


class ViolationFileGroup(BaseModel):
    """
    违法行为文件分组

    业务规则：
    - 1个事实认定书 = 1个违法行为
    - 取证记录表为可选补充材料
    """
    group_id: str = Field(description="分组唯一ID（UUID）")

    fact_determination: dict = Field(
        description="事实认定书信息（必须）",
        examples=[{
            "file_id": "file_abc123",
            "filename": "事实认定书_A公司.docx",
            "upload_time": "2025-12-03T10:30:00",
            "parsed_data": {
                "inspection_content": "检查内容...",
                "violation_facts": "事实认定..."
            }
        }]
    )

    evidence_records: Optional[dict] = Field(
        default=None,
        description="取证记录表信息（可选）",
        examples=[{
            "file_id": "file_def456",
            "filename": "取证记录表_A公司.docx",
            "upload_time": "2025-12-03T10:35:00",
            "parsed_data": {
                "basic_info": {},
                "evidence_process": "...",
                "evidence_list": []
            }
        }]
    )

    created_at: str = Field(description="分组创建时间（ISO格式）")
    order: int = Field(description="排序序号（按创建时间）")


# ============ LangGraph 统一状态（主图和子图共享）============

class GraphState(TypedDict):
    """
    统一图状态 - 主图和子图共享
    """

    # ========================================
    # 核心字段（跨子图共享）
    # ========================================
    messages: Annotated[list[BaseMessage], add_messages]  # 消息历史，使用 add_messages reducer
    user_query: Optional[str]  # 当前用户输入
    answer: Optional[Union[str, dict]]  # 当前AI回复

    # ========================================
    # 违法行为文件分组（用户上传的原始文件）
    # ========================================
    violation_file_groups: Annotated[Optional[list[dict]], operator.add]  # 文件分组列表（累加器），每个分组对应1个违法行为

    # ========================================
    # 标准化数据结构（唯一数据源）⭐
    # ========================================
    violations_analysis_result: Optional[ViolationsAnalysisResult]  # 违规分析完整结果 - 包含所有数据（检索结果 + 文件元数据）

    # ========================================
    # 任务进度（持久化到 checkpoint）
    # ========================================
    task_progress: Optional[list[dict]]  # 当前任务进度: [{"id": str, "title": str, "status": str}, ...]

    # ========================================
    # 会话元数据
    # ========================================
    tool_type: Optional[str]  # 工具类型，如 "violation-analysis", "report-jcbg" 等（用于前端恢复引导语）
