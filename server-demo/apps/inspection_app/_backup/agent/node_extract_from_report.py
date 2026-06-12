"""
从检查报告Word文档提取数据节点
用于整改通知书、信息简表、证据目录的生成流程
"""

import os
from docx import Document
from langchain_core.messages import AIMessage
from langgraph.config import get_stream_writer

from .states import GraphState, ViolationsAnalysisResult, ViolationData, LegalProvision
from .task_manager import TaskManager
from ..inspection_utils import get_violation_uploads_dir, create_progress_message
from utils.logger import get_logger
from utils.exceptions import BusinessException

logger = get_logger(__name__)


def create_extract_from_report_node(config: dict):
    """
    创建从检查报告提取数据的节点（工厂函数）

    Args:
        config: 节点配置 (包含 tasks)
    """
    def extract_from_report_node(state: GraphState) -> dict:
        """
        从检查报告Word文档提取violations_analysis_result

        要求：
        - state中必须有uploaded_report_file（用户上传的检查报告）
        - 使用LLM从Word中提取结构化数据
        """
        logger.info("开始从检查报告提取数据...")

        task_mgr = TaskManager(config['tasks'])
        extract_task_id = 'extract_from_report'
        task_mgr.set_in_progress(extract_task_id)

        # 从 violations_analysis_result.source_files 中获取检查报告
        analysis_result = state.get("violations_analysis_result")
        if not analysis_result:
            raise BusinessException(msg="请上传检查报告", code=40001)

        # 提取 source_files
        if isinstance(analysis_result, dict):
            source_files = analysis_result.get("source_files", [])
        else:
            source_files = analysis_result.source_files if hasattr(analysis_result, "source_files") else []

        # 查找检查报告
        uploaded_report = None
        for file_info in source_files:
            if file_info.get("file_type") == "检查报告":
                uploaded_report = file_info
                break

        if not uploaded_report:
            raise BusinessException(msg="请上传检查报告", code=40001)

        file_path = uploaded_report.get("file_path")
        if not file_path or not os.path.exists(file_path):
            raise BusinessException(msg="检查报告文件不存在", code=40002)

        logger.info(f"读取检查报告: {file_path}")

        # 读取Word文档内容
        try:
            doc = Document(file_path)
            full_text = "\n".join([para.text for para in doc.paragraphs if para.text.strip()])
            logger.info(f"检查报告文本长度: {len(full_text)} 字符")
        except Exception as e:
            raise BusinessException(msg=f"读取检查报告失败: {str(e)}", code=50001)

        # 使用LLM提取结构化数据
        try:
            violations_data = _extract_violations_with_llm(full_text, state)
            logger.info(f"从检查报告提取到 {len(violations_data['violations'])} 个违法行为")
        except Exception as e:
            logger.error(f"LLM提取数据失败: {str(e)}", exc_info=True)
            raise BusinessException(msg=f"数据提取失败: {str(e)}", code=50002)

        # 构建 ViolationsAnalysisResult
        violations_analysis_result = ViolationsAnalysisResult(
            case_number=violations_data.get("case_number", ""),
            inspected_person=violations_data.get("inspected_person", ""),
            inspector=violations_data.get("inspector"),
            inspection_date=violations_data.get("inspection_date"),
            inspection_location=violations_data.get("inspection_location"),
            violations=violations_data["violations"],
            source_files=[]  # 从检查报告提取的流程不需要source_files
        )

        task_mgr.set_completed(extract_task_id)
        progress_msg = create_progress_message(task_mgr._build_task_list(extract_task_id, 'completed'))

        return {
            "violations_analysis_result": violations_analysis_result,
            "messages": [progress_msg]
        }

    return extract_from_report_node


def _extract_violations_with_llm(report_text: str, state: GraphState) -> dict:
    """
    使用LLM从检查报告文本中提取结构化数据

    Returns:
        {
            "case_number": str,
            "inspected_person": str,
            "inspector": str,
            "inspection_date": str,
            "inspection_location": str,
            "violations": List[ViolationData]
        }
    """
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import JsonOutputParser
    from pydantic import BaseModel, Field
    from ..inspection_utils import get_chat_model

    # 定义提取的数据结构
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
        inspector: str = Field(default="", description="检查人")
        inspection_date: str = Field(default="", description="检查时间")
        inspection_location: str = Field(default="", description="检查地点")
        violations: list[ViolationExtract] = Field(description="违法行为列表")

    # 构建提示词
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

可选信息（如报告中有则提取）：
- 案件编号
- 检查人
- 检查时间
- 检查地点
- 裁量基准

重要格式要求：
1. 所有法律、法规、规章名称必须使用书名号《》，例如：《中华人民共和国行政处罚法》、《非金融机构支付服务管理办法》
2. 如果原文中法律名称没有书名号，请自动添加

请严格按照以下JSON格式返回（IMPORTANT: 必须包含violations数组）：
{{
  "case_number": "",
  "inspected_person": "被检查单位名称",
  "inspector": "",
  "inspection_date": "",
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
4. 其他字段（案件编号、检查人、检查时间、检查地点）可以为空""
"""),
        ("human", "检查报告内容：\n\n{report_text}\n\n请提取上述信息，返回JSON格式。")
    ])

    # 初始化LLM - 使用项目统一配置
    llm = get_chat_model(temperature=0)

    # 创建解析器
    parser = JsonOutputParser(pydantic_object=ReportExtract)

    # 构建链
    chain = prompt | llm | parser

    # 执行提取
    logger.info("调用LLM提取数据...")
    result = chain.invoke({"report_text": report_text})

    # 打印LLM返回结果用于调试
    logger.info(f"LLM 返回结果: {result}")
    logger.info(f"LLM 返回结果的 keys: {result.keys() if isinstance(result, dict) else 'Not a dict'}")

    # 检查是否包含 violations 字段
    if not isinstance(result, dict) or "violations" not in result:
        logger.error(f"LLM 返回的数据格式不正确，缺少 violations 字段。返回内容: {result}")
        raise BusinessException(msg="LLM 返回的数据格式不正确，请检查检查报告格式", code=50003)

    # 转换为 ViolationData 对象
    violations = []
    for v in result["violations"]:
        # 转换 legal_basis
        violated_provisions = [
            LegalProvision(
                law_name=item["law_name"],
                law_item_name=item["law_item_name"],
                law_item_content=item["law_item_content"],
                relevance_score=1.0
            )
            for item in v["legal_basis"]
        ]

        # 转换 penalty_clause
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
            evidence_list=[],  # 从检查报告提取不包含evidence_list
        ))

    return {
        "case_number": result.get("case_number", ""),
        "inspected_person": result["inspected_person"],
        "inspector": result.get("inspector", ""),
        "inspection_date": result.get("inspection_date", ""),
        "inspection_location": result.get("inspection_location", ""),
        "violations": violations
    }
