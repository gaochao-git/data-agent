"""
证据目录智能体节点定义
"""

from langgraph.types import interrupt, RunnableConfig
from langgraph.config import get_stream_writer
from langchain_core.messages import HumanMessage, AIMessage

from .state import ZjMlState
from ..shared.task_manager import TaskManager
from ..shared.models import ViolationData, LegalProvision, EvidenceItem, ViolationsAnalysisResult
from ..shared.generators.zjml_generator import generate_zjml_document
from ..shared.extract_from_report import extract_violations_from_report
from ..inspection_utils import create_progress_message, get_chat_model, get_violation_upload_path
from utils.logger import get_logger
from utils.exceptions import BusinessException

logger = get_logger(__name__)

TASKS = [
    {"id": "check_deps", "title": "提取所需数据"},
    {"id": "review_retrieve", "title": "确认数据并上传取证记录表"},
    {"id": "generate", "title": "生成Excel文档"},
]


def check_deps_node(state: ZjMlState, config: RunnableConfig) -> dict:
    logger.info("检查证据目录所需数据...")
    task_mgr = TaskManager(TASKS)
    task_mgr.set_in_progress("check_deps")

    thread_id = config.get("configurable", {}).get("thread_id")
    logger.info(f"thread_id: {thread_id}")

    # 检查是否已有分析结果
    analysis_result = state.get("violations_analysis_result")
    logger.info(f"现有 analysis_result: {analysis_result}")

    # 检查 violations 是否为空
    has_violations = analysis_result and hasattr(analysis_result, 'violations') and len(analysis_result.violations) > 0

    # 如果没有分析结果或 violations 为空，尝试从上传的检查报告中提取
    if not has_violations:
        logger.info("未找到有效的分析结果（violations 为空），尝试从上传的检查报告提取...")

        # 优先从 analysis_result.source_files 中获取文件路径
        file_path = None
        if analysis_result and hasattr(analysis_result, 'source_files') and analysis_result.source_files:
            for source_file in analysis_result.source_files:
                if source_file.get("file_type") == "检查报告":
                    file_path = source_file.get("file_path")
                    logger.info(f"从 source_files 找到检查报告: {file_path}")
                    break
            # 如果没有找到标记为检查报告的文件，使用第一个文件
            if not file_path and analysis_result.source_files:
                file_path = analysis_result.source_files[0].get("file_path")
                logger.info(f"使用第一个 source_file: {file_path}")

        # 如果 source_files 中没有，尝试从消息的 uploaded_files 中获取
        if not file_path:
            logger.info("source_files 中未找到文件，尝试从消息获取...")
            messages = state.get("messages", [])
            uploaded_files = []
            for msg in reversed(messages):
                if hasattr(msg, 'additional_kwargs'):
                    task = msg.additional_kwargs.get("task", {})
                    uploaded_files = task.get("uploaded_files", [])
                    if uploaded_files:
                        break

            if uploaded_files:
                # 查找检查报告文件
                report_file = None
                for f in uploaded_files:
                    file_type = f.get("file_type", "")
                    if file_type == "检查报告" or "报告" in f.get("file_name", ""):
                        report_file = f
                        break
                if not report_file:
                    report_file = uploaded_files[0]

                file_name = report_file.get("file_name") or report_file.get("filename")
                if file_name:
                    file_path = get_violation_upload_path(thread_id, file_name)

        if not file_path:
            raise BusinessException(msg="请上传检查报告文件", code=40001)

        logger.info(f"从检查报告提取数据: {file_path}")

        # 提取数据
        analysis_result = extract_violations_from_report(file_path)
        logger.info(f"从检查报告提取到 {len(analysis_result.violations)} 个违法行为")

    violations_count = len(analysis_result.violations) if hasattr(analysis_result, 'violations') else 0
    logger.info(f"检测到分析结果，包含 {violations_count} 个违法行为")

    task_mgr.set_completed("check_deps")
    progress_msg = create_progress_message(task_mgr._build_task_list("check_deps", 'completed'))
    return {
        "violations_analysis_result": analysis_result,
        "messages": [progress_msg]
    }


def review_retrieve_node(state: ZjMlState, config: RunnableConfig) -> dict:
    """
    检索结果人工审核节点 - 证据目录专用

    功能：
    1. 展示从检查报告提取的数据供用户审核
    2. 允许用户上传取证记录表（证据目录必需）
    3. 用户确认后继续生成
    """
    violations_analysis_result = state.get("violations_analysis_result")

    if not violations_analysis_result:
        raise BusinessException(msg="未找到分析结果，请先上传检查报告", code=40001)

    task_mgr = TaskManager(TASKS)
    task_mgr.set_in_progress("review_retrieve")

    thread_id = config.get("configurable", {}).get("thread_id")

    # 构建审核数据
    review_data = []
    for idx, violation in enumerate(violations_analysis_result.violations):
        # 将 LegalProvision 对象转为字典
        legal_basis_list = [
            {
                "law_name": provision.law_name,
                "law_item_name": provision.law_item_name,
                "law_item_content": provision.law_item_content,
            }
            for provision in violation.violated_provisions
        ]

        penalty_clause_list = [
            {
                "law_name": provision.law_name,
                "law_item_name": provision.law_item_name,
                "law_item_content": provision.law_item_content,
            }
            for provision in violation.penalty_provisions
        ]

        # 获取已有的证据列表
        evidence_list = [
            {
                "evidence_name_code": item.evidence_name_code,
                "evidence_source": item.evidence_source,
                "evidence_form": item.evidence_form,
                "evidence_truth": item.evidence_truth,
                "provider_sign": item.provider_sign,
                "remark": item.remark,
            }
            for item in violation.evidence_list
        ]

        # 获取附件信息
        source_files_for_violation = []
        group_id = None

        if violations_analysis_result.source_files and idx < len(violations_analysis_result.source_files):
            group = violations_analysis_result.source_files[idx]
            if isinstance(group, dict):
                group_id = group.get("group_id")

                # 事实认定书/检查报告
                if group.get("fact_determination"):
                    fact_det = group["fact_determination"]
                    if fact_det.get("file_id"):
                        source_files_for_violation.append({
                            "file_id": fact_det["file_id"],
                            "filename": fact_det.get("filename", "检查报告"),
                            "file_type": fact_det.get("file_type", "检查报告"),
                            "group_id": group_id
                        })

                # 取证记录表（如果已有）
                evidence_rec = group.get("evidence_records")
                if evidence_rec and evidence_rec.get("file_id"):
                    source_files_for_violation.append({
                        "file_id": evidence_rec["file_id"],
                        "filename": evidence_rec.get("filename", "取证记录表"),
                        "file_type": "取证记录表",
                        "group_id": group_id
                    })

        review_data.append({
            "violation_id": violation.violation_id,
            "title": violation.title,
            "facts": violation.facts,
            "inspected_person": violations_analysis_result.inspected_person,
            "inspector": violations_analysis_result.inspector,
            "inspection_date": violations_analysis_result.inspection_date,
            "inspection_location": violations_analysis_result.inspection_location,
            "legal_basis": legal_basis_list,
            "penalty_clause": penalty_clause_list,
            "discretion_standard": violation.discretionary_benchmarks,
            "rectification_measures": violation.rectification_measures,
            "penalty_suggestion": violation.penalty_recommendations,
            "source_files": source_files_for_violation,
            "evidence_list": evidence_list,
        })

    # 检查每个违法行为是否已上传取证记录表
    violation_upload_status = {}
    all_uploaded = True

    for violation in violations_analysis_result.violations:
        v_id = violation.violation_id
        has_evidence = len(violation.evidence_list) > 0
        violation_upload_status[v_id] = has_evidence
        if not has_evidence:
            all_uploaded = False

    logger.info(f"证据目录审核 - 需要取证记录表, 上传状态: {violation_upload_status}, 全部上传: {all_uploaded}")

    # 发送审核数据给前端
    writer = get_stream_writer()
    writer({
        "type": "non_standard",
        "value": {
            "type": "human_review",
            "data": {
                "review_type": "retrieve",
                "message": "请确认数据并上传取证记录表，确认后将生成证据目录",
                "data": {
                    "violations": review_data,
                    "summary": {
                        "violation_count": len(review_data),
                        "inspected_person": violations_analysis_result.inspected_person
                    }
                }
            }
        }
    })

    logger.info(f"等待用户审核，共 {len(review_data)} 个违法行为...")

    # 中断执行，等待用户输入
    human_input = interrupt({
        "review_type": "retrieve",
        "message": "请确认数据并上传取证记录表，确认后将生成证据目录",
        "current_data": {"violations": review_data},
        "requires_evidence_upload": True,  # 证据目录需要上传取证记录表
        "violation_upload_status": violation_upload_status,
        "all_evidence_uploaded": all_uploaded
    })

    logger.info(f"收到用户审核结果: {human_input}")

    action = human_input.get("action", "approve")
    task_mgr.set_completed("review_retrieve")
    progress_msg = create_progress_message(task_mgr._build_task_list("review_retrieve", 'completed'))

    if action == "reject":
        logger.info("用户拒绝，终止执行")
        raise BusinessException(msg="用户取消操作", code=40002)

    elif action == "edit":
        # 用户编辑了数据，重建 violations_analysis_result
        modified_violations = human_input.get("data", {}).get("violations", [])
        if modified_violations:
            first_violation = modified_violations[0] if modified_violations else {}
            inspected_person = first_violation.get("inspected_person") or violations_analysis_result.inspected_person
            inspector = first_violation.get("inspector") or violations_analysis_result.inspector
            inspection_date = first_violation.get("inspection_date") or violations_analysis_result.inspection_date
            inspection_location = first_violation.get("inspection_location") or violations_analysis_result.inspection_location

            new_violations = []
            for v in modified_violations:
                # 重建 violated_provisions
                violated_provisions = [
                    LegalProvision(
                        law_name=item.get("law_name", ""),
                        law_item_name=item.get("law_item_name", ""),
                        law_item_content=item.get("law_item_content", ""),
                        relevance_score=1.0
                    )
                    for item in v.get("legal_basis", [])
                ]

                # 重建 penalty_provisions
                penalty_provisions = [
                    LegalProvision(
                        law_name=item.get("law_name", ""),
                        law_item_name=item.get("law_item_name", ""),
                        law_item_content=item.get("law_item_content", ""),
                        relevance_score=1.0
                    )
                    for item in v.get("penalty_clause", [])
                ]

                # 重建 evidence_list（从前端传来的数据）
                evidence_list_data = []
                for item in v.get("evidence_list", []):
                    evidence_list_data.append(EvidenceItem(
                        evidence_name_code=item.get("evidence_name_code", ""),
                        evidence_source=item.get("evidence_source", ""),
                        evidence_form=item.get("evidence_form", ""),
                        evidence_truth=item.get("evidence_truth", ""),
                        provider_sign=item.get("provider_sign"),
                        remark=item.get("remark")
                    ))

                # 提取取证记录表文件信息
                evidence_record_file_data = None
                for file in v.get("source_files", []):
                    if file.get("file_type") == "取证记录表":
                        evidence_record_file_data = {
                            "file_id": file.get("file_id"),
                            "filename": file.get("filename") or file.get("file_name"),
                            "file_type": "取证记录表"
                        }
                        break

                new_violations.append(ViolationData(
                    violation_id=v.get("violation_id"),
                    title=v.get("title", ""),
                    facts=v.get("facts", ""),
                    violated_provisions=violated_provisions,
                    penalty_provisions=penalty_provisions,
                    discretionary_benchmarks=v.get("discretion_standard", []),
                    rectification_measures=v.get("rectification_measures", []),
                    penalty_recommendations=v.get("penalty_suggestion", []),
                    evidence_list=evidence_list_data,
                    evidence_record_file=evidence_record_file_data
                ))

            new_result = ViolationsAnalysisResult(
                case_number=violations_analysis_result.case_number,
                inspected_person=inspected_person,
                inspector=inspector,
                inspection_date=inspection_date,
                inspection_location=inspection_location,
                violations=new_violations,
                source_files=violations_analysis_result.source_files
            )

            logger.info(f"用户修改了数据，新结果包含 {len(new_violations)} 个违法行为")

            return {
                "violations_analysis_result": new_result,
                "human_approved_retrieve": True,
                "messages": [progress_msg]
            }

    # 默认：用户确认
    logger.info("用户确认数据")
    return {
        "human_approved_retrieve": True,
        "messages": [progress_msg]
    }


def generate_node(state: ZjMlState, config: RunnableConfig) -> dict:
    logger.info("开始生成证据目录 Excel 文档...")
    return generate_zjml_document(state, config, TASKS)


def router_node(state: ZjMlState) -> dict:
    """路由节点"""
    messages = state.get("messages", [])
    if not messages:
        return {"task_type": "qa"}
    last_message = messages[-1]
    task_dict = last_message.additional_kwargs.get("task", {}) if hasattr(last_message, 'additional_kwargs') else {}
    task_type = task_dict.get("task_type", "qa")
    logger.info(f"路由决策: task_type={task_type}")
    return {"task_type": task_type}


def router_conditional(state: ZjMlState) -> str:
    """路由条件函数"""
    task_type = state.get("task_type", "qa")
    return "check_deps" if task_type == "report" else "qa"


def _filter_messages_for_llm(messages: list) -> list:
    """过滤消息，只保留 LLM 可处理的内容，非文本内容生成描述"""
    from langchain_core.messages import SystemMessage
    filtered = []
    for msg in messages:
        content = msg.content
        if isinstance(content, str):
            if isinstance(msg, HumanMessage):
                filtered.append(HumanMessage(content=content))
            elif isinstance(msg, AIMessage):
                filtered.append(AIMessage(content=content))
        elif isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    block_type = block.get("type")
                    if block_type == "text":
                        text_parts.append(block.get("text", ""))
                    elif block_type == "file":
                        text_parts.append(f"[已生成文件: {block.get('filename', '文件')}]")
                    elif block_type == "non_standard" and block.get("value", {}).get("type") == "file":
                        text_parts.append(f"[已生成文件: {block.get('value', {}).get('data', {}).get('filename', '文件')}]")
                elif isinstance(block, str):
                    text_parts.append(block)
            if text_parts:
                combined = "\n".join(text_parts)
                if isinstance(msg, HumanMessage):
                    filtered.append(HumanMessage(content=combined))
                elif isinstance(msg, AIMessage):
                    filtered.append(AIMessage(content=combined))
    return filtered


def qa_node(state: ZjMlState) -> dict:
    """问答节点"""
    logger.info("处理问答...")
    messages = state.get("messages", [])
    try:
        filtered_messages = _filter_messages_for_llm(messages[-10:])
        system_msg = HumanMessage(content="你是执法检查专家助手，专门负责证据目录相关的问答。基于对话历史回答用户问题，保持专业、客观。")
        llm = get_chat_model(temperature=0, streaming=True, timeout=120)
        result = llm.invoke([system_msg] + filtered_messages)
        logger.info(f"问答完成，答案长度: {len(result.content)} 字符")
        return {"messages": [AIMessage(content=result.content)]}
    except Exception as e:
        error_msg = f"问答处理失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise BusinessException(msg=error_msg, code=50021)
