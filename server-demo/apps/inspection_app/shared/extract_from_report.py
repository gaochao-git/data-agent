"""
从检查报告Word文档提取数据
用于整改通知书、信息简表、证据目录等报告的生成
"""

import os
from docx import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from pydantic import BaseModel, Field

from .models import ViolationsAnalysisResult, ViolationData, LegalProvision
from ..inspection_utils import get_chat_model
from utils.logger import get_logger
from utils.exceptions import BusinessException

logger = get_logger(__name__)


class LegalProvisionExtract(BaseModel):
    law_name: str = Field(description="法律名称")
    law_item_name: str = Field(description="条款名称")
    law_item_content: str = Field(description="条款内容")


class ViolationExtract(BaseModel):
    violation_id: int = Field(description="违法行为序号")
    title: str = Field(description="违法行为标题")
    facts: str = Field(description="违法事实")
    legal_basis: list[LegalProvisionExtract] = Field(description="违反条款列表")
    penalty_clause: list[LegalProvisionExtract] = Field(description="处罚条款列表")
    discretion_standard: list[str] = Field(default=[], description="裁量基准")
    rectification_measures: list[str] = Field(default=[], description="整改措施")
    penalty_suggestion: list[str] = Field(default=[], description="处罚建议")


class ReportExtract(BaseModel):
    case_number: str = Field(default="", description="案件编号")
    inspected_person: str = Field(description="被检查人/单位")
    inspected_address: str = Field(default="", description="被检查人地址")
    legal_representative: str = Field(default="", description="法定代表人")
    inspector: str = Field(default="", description="检查人")
    inspection_date: str = Field(default="", description="检查时间")
    inspection_start_date: str = Field(default="", description="检查开始时间")
    inspection_end_date: str = Field(default="", description="检查结束时间")
    business_start_date: str = Field(default="", description="业务开始时间")
    business_end_date: str = Field(default="", description="业务结束时间")
    inspection_location: str = Field(default="", description="检查地点")
    violations: list[ViolationExtract] = Field(description="违法行为列表")


def extract_violations_from_report(file_path: str) -> ViolationsAnalysisResult:
    """
    从检查报告Word文档提取violations_analysis_result

    Args:
        file_path: 检查报告文件路径

    Returns:
        ViolationsAnalysisResult
    """
    logger.info(f"开始从检查报告提取数据: {file_path}")

    if not os.path.exists(file_path):
        raise BusinessException(msg="检查报告文件不存在", code=40002)

    # 读取Word文档内容
    try:
        doc = Document(file_path)
        full_text = "\n".join([para.text for para in doc.paragraphs if para.text.strip()])
        logger.info(f"检查报告文本长度: {len(full_text)} 字符")
    except Exception as e:
        raise BusinessException(msg=f"读取检查报告失败: {str(e)}", code=50001)

    # 使用LLM提取结构化数据
    try:
        result = _extract_with_llm(full_text)
        logger.info(f"从检查报告提取到 {len(result['violations'])} 个违法行为")
    except Exception as e:
        logger.error(f"LLM提取数据失败: {str(e)}", exc_info=True)
        raise BusinessException(msg=f"数据提取失败: {str(e)}", code=50002)

    # 转换为 ViolationData 对象
    violations = []
    for v in result["violations"]:
        violated_provisions = [
            LegalProvision(
                law_name=item["law_name"],
                law_item_name=item["law_item_name"],
                law_item_content=item["law_item_content"],
                relevance_score=1.0
            )
            for item in v["legal_basis"]
        ]

        penalty_provisions = [
            LegalProvision(
                law_name=item["law_name"],
                law_item_name=item["law_item_name"],
                law_item_content=item["law_item_content"],
                relevance_score=1.0
            )
            for item in v["penalty_clause"]
        ]

        violations.append(ViolationData(
            violation_id=v["violation_id"],
            title=v["title"],
            facts=v["facts"],
            violated_provisions=violated_provisions,
            penalty_provisions=penalty_provisions,
            discretionary_benchmarks=v.get("discretion_standard", []),
            rectification_measures=v.get("rectification_measures", []),
            penalty_recommendations=v.get("penalty_suggestion", []),
            evidence_list=[],
            historical_cases=[]
        ))

    return ViolationsAnalysisResult(
        case_number=result.get("case_number", ""),
        inspected_person=result["inspected_person"],
        inspected_address=result.get("inspected_address"),
        legal_representative=result.get("legal_representative"),
        inspector=result.get("inspector"),
        inspection_date=result.get("inspection_date"),
        inspection_start_date=result.get("inspection_start_date"),
        inspection_end_date=result.get("inspection_end_date"),
        business_start_date=result.get("business_start_date"),
        business_end_date=result.get("business_end_date"),
        inspection_location=result.get("inspection_location"),
        violations=violations,
        source_files=[]
    )


def _extract_with_llm(report_text: str) -> dict:
    """使用LLM从检查报告文本中提取结构化数据"""

    prompt = ChatPromptTemplate.from_messages([
        ("system", """你是一个专业的执法文书解析助手。请从检查报告中提取以下信息：

核心信息（必须提取）：
1. 被检查人/单位（必填）
2. 违法行为列表（必填），每个违法行为包含：
   - 检查内容/违法行为标题
   - 违法事实描述
   - 违反的法律条款（法律名称必须使用书名号《》）
   - 处罚依据条款（法律名称必须使用书名号《》）
   - 整改措施
   - 处罚建议

可选信息（如报告中有则提取，没有则留空）：
- 案件编号
- 被检查人地址
- 法定代表人
- 检查人
- 检查开始时间（格式：YYYY年MM月DD日）
- 检查结束时间（格式：YYYY年MM月DD日）
- 业务开始时间（格式：YYYY年MM月DD日）
- 业务结束时间（格式：YYYY年MM月DD日）
- 检查地点
- 裁量基准

重要格式要求：
1. 所有法律、法规、规章名称必须使用书名号《》
2. 如果原文中法律名称没有书名号，请自动添加
3. 时间格式统一为：YYYY年MM月DD日

请严格按照以下JSON格式返回：
{{
  "case_number": "",
  "inspected_person": "被检查单位名称",
  "inspected_address": "被检查人地址",
  "legal_representative": "法定代表人姓名",
  "inspector": "",
  "inspection_date": "",
  "inspection_start_date": "检查开始时间，如2021年1月1日",
  "inspection_end_date": "检查结束时间，如2021年6月30日",
  "business_start_date": "业务开始时间，如2020年1月1日",
  "business_end_date": "业务结束时间，如2020年12月31日",
  "inspection_location": "",
  "violations": [
    {{
      "violation_id": 1,
      "title": "检查内容或违法行为标题",
      "facts": "违法事实的详细描述",
      "legal_basis": [
        {{
          "law_name": "《法律名称》",
          "law_item_name": "第X条",
          "law_item_content": "条款的具体内容"
        }}
      ],
      "penalty_clause": [
        {{
          "law_name": "《法律名称》",
          "law_item_name": "第X条",
          "law_item_content": "处罚条款的具体内容"
        }}
      ],
      "discretion_standard": [],
      "rectification_measures": ["整改措施1", "整改措施2"],
      "penalty_suggestion": ["处罚建议1"]
    }}
  ]
}}

注意事项：
1. violations数组必须存在，即使为空也要返回[]
2. 如果某个字段找不到对应内容，使用空字符串""或空数组[]
3. 被检查人/单位和violations是必须字段
"""),
        ("human", "检查报告内容：\n\n{report_text}\n\n请提取上述信息，返回JSON格式。")
    ])

    llm = get_chat_model(temperature=0)
    parser = JsonOutputParser(pydantic_object=ReportExtract)
    chain = prompt | llm | parser

    logger.info("调用LLM提取数据...")
    result = chain.invoke({"report_text": report_text})

    if not isinstance(result, dict) or "violations" not in result:
        logger.error(f"LLM返回的数据格式不正确: {result}")
        raise BusinessException(msg="LLM返回的数据格式不正确", code=50003)

    return result
