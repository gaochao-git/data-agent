"""
检查报告智能体节点定义
"""

from langgraph.types import interrupt, RunnableConfig
from langgraph.config import get_stream_writer
from langchain_core.messages import HumanMessage, AIMessage

from .state import JcbgState
from ..shared.models import ViolationData, LegalProvision, ViolationsAnalysisResult
from ..shared.task_manager import TaskManager
from ..shared.retrieval_service import retrieval_service
from ..shared.report_utils import build_violations_analysis_result
from ..shared.generators.jcbg_generator import generate_jcbg_document
from ..inspection_utils import create_progress_message, get_chat_model
from utils.logger import get_logger
from utils.exceptions import BusinessException

logger = get_logger(__name__)


# 任务列表定义
TASKS = [
    {"id": "check_deps", "title": "检查所需文件"},
    {"id": "retrieve", "title": "检索法律依据"},
    {"id": "review_retrieve", "title": "确认检索结果"},
    {"id": "generate", "title": "生成Word文档"},
]


def check_deps_node(state: JcbgState) -> dict:
    """
    检查依赖节点 - 检查事实认定书文件是否已上传
    """
    logger.info("检查检查报告所需数据...")

    task_mgr = TaskManager(TASKS)
    task_mgr.set_in_progress("check_deps")

    # 检查违法行为文件分组
    file_groups = state.get("violation_file_groups") or []

    logger.info(f"[依赖检查] violation_file_groups: {len(file_groups)} 个分组")

    if not file_groups:
        logger.error("[依赖检查] violation_file_groups 为空")
        raise BusinessException(msg="请上传事实认定书", code=40001)

    logger.info(f"检测到 {len(file_groups)} 个违法行为文件分组")

    task_mgr.set_completed("check_deps")
    logger.info("依赖数据检查通过")

    progress_msg = create_progress_message(task_mgr._build_task_list("check_deps", 'completed'))
    return {"messages": [progress_msg]}


def retrieve_node(state: JcbgState) -> dict:
    """
    检索节点 - 检索法律依据并构建标准化数据结构
    """
    task_mgr = TaskManager(TASKS)

    # 幂等性检查
    if state.get("violations_analysis_result"):
        logger.info("检测到已有标准化分析结果，跳过检索步骤")
        task_mgr.set_completed("retrieve")
        progress_msg = create_progress_message(task_mgr._build_task_list("retrieve", 'completed'))
        return {"messages": [progress_msg]}

    # 获取文件分组
    violation_file_groups = state.get("violation_file_groups")
    if not violation_file_groups:
        raise BusinessException(msg="请上传事实认定书", code=40001)

    logger.info(f"开始检索法律依据 - 共 {len(violation_file_groups)} 个违法行为")
    task_mgr.set_in_progress("retrieve")

    # 遍历检索
    retrieval_results = []
    for idx, file_group in enumerate(violation_file_groups, start=1):
        violation_id = idx

        fact_det = file_group.get("fact_determination", {})
        parsed_data = fact_det.get("parsed_data", {})
        violation_facts = parsed_data.get("violation_facts", "")

        if not violation_facts:
            logger.warning(f"违法行为 {violation_id} 的事实认定为空，跳过检索")
            continue

        # 检索6项内容
        retrieval_results.append({
            "violation_id": violation_id,
            "type": "legal_basis",
            "data": retrieval_service.retrieve_legal_basis(violation_facts)
        })
        retrieval_results.append({
            "violation_id": violation_id,
            "type": "penalty_clause",
            "data": retrieval_service.retrieve_penalty_clause(violation_facts)
        })
        retrieval_results.append({
            "violation_id": violation_id,
            "type": "discretion_standards",
            "data": retrieval_service.retrieve_discretion_standard(violation_facts)
        })
        retrieval_results.append({
            "violation_id": violation_id,
            "type": "rectification_measures",
            "data": retrieval_service.retrieve_rectification_measures(violation_facts)
        })
        retrieval_results.append({
            "violation_id": violation_id,
            "type": "penalty_suggestions",
            "data": retrieval_service.retrieve_penalty_suggestion(violation_facts)
        })
        retrieval_results.append({
            "violation_id": violation_id,
            "type": "historical_cases",
            "data": retrieval_service.retrieve_historical_cases(violation_facts)
        })

    logger.info(f"检索完成 - 共 {len(retrieval_results)} 项检索结果")
    task_mgr.set_completed("retrieve")

    # 构建标准化数据结构
    violations_analysis_result = build_violations_analysis_result(
        violation_file_groups=violation_file_groups,
        retrieval_results=retrieval_results
    )
    logger.info("标准化数据结构构建完成")

    progress_msg = create_progress_message(task_mgr._build_task_list("retrieve", 'completed'))

    return {
        "violations_analysis_result": violations_analysis_result,
        "messages": [progress_msg],
    }


def review_retrieve_node(state: JcbgState) -> dict:
    """
    检索结果人工审核节点 - 使用 interrupt 等待用户确认
    """
    violations_analysis_result = state.get("violations_analysis_result")

    if not violations_analysis_result:
        raise ValueError("检索结果为空，请先完成法律依据检索")

    task_mgr = TaskManager(TASKS)
    task_mgr.set_in_progress("review_retrieve")

    # 构建审核数据
    review_data = []
    for idx, violation in enumerate(violations_analysis_result.violations):
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

        # 获取附件信息
        source_files_for_violation = []
        group_id = None

        if violations_analysis_result.source_files and idx < len(violations_analysis_result.source_files):
            group = violations_analysis_result.source_files[idx]
            group_id = group.get("group_id")

            if group.get("fact_determination"):
                fact_det = group["fact_determination"]
                if fact_det.get("file_id"):
                    source_files_for_violation.append({
                        "file_id": fact_det["file_id"],
                        "filename": fact_det.get("filename", "事实认定书"),
                        "file_type": "事实认定书",
                        "group_id": group_id
                    })

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
            "evidence_list": []
        })

    # 发送审核数据给前端
    writer = get_stream_writer()
    writer({
        "type": "non_standard",
        "value": {
            "type": "human_review",
            "data": {
                "review_type": "retrieve",
                "message": "请确认法律依据检索结果，确认后将生成报告",
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

    logger.info(f"等待用户审核检索结果，共 {len(review_data)} 个违法行为...")

    # 中断执行，等待用户输入
    human_input = interrupt({
        "review_type": "retrieve",
        "message": "请确认法律依据检索结果，确认后将生成报告",
        "current_data": {"violations": review_data},
        "requires_evidence_upload": False,
        "violation_upload_status": {},
        "all_evidence_uploaded": True
    })

    logger.info(f"收到用户审核结果: {human_input}")

    action = human_input.get("action", "approve")
    task_mgr.set_completed("review_retrieve")

    progress_msg = create_progress_message(task_mgr._build_task_list("review_retrieve", 'completed'))

    if action == "reject":
        logger.info("用户拒绝检索结果，终止执行")
        raise ValueError("用户取消操作")

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
                violated_provisions = [
                    LegalProvision(
                        law_name=item.get("law_name", ""),
                        law_item_name=item.get("law_item_name", ""),
                        law_item_content=item.get("law_item_content", ""),
                        relevance_score=1.0
                    )
                    for item in v.get("legal_basis", [])
                ]

                penalty_provisions = [
                    LegalProvision(
                        law_name=item.get("law_name", ""),
                        law_item_name=item.get("law_item_name", ""),
                        law_item_content=item.get("law_item_content", ""),
                        relevance_score=1.0
                    )
                    for item in v.get("penalty_clause", [])
                ]

                new_violations.append(ViolationData(
                    violation_id=v.get("violation_id"),
                    title=v.get("title", ""),
                    facts=v.get("facts", ""),
                    violated_provisions=violated_provisions,
                    penalty_provisions=penalty_provisions,
                    discretionary_benchmarks=v.get("discretion_standard", []),
                    rectification_measures=v.get("rectification_measures", []),
                    penalty_recommendations=v.get("penalty_suggestion", []),
                    evidence_list=[],
                    evidence_process=None
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

            logger.info(f"用户修改了检索结果，新结果包含 {len(new_violations)} 个违法行为")

            return {
                "violations_analysis_result": new_result,
                "human_approved_retrieve": True,
                "messages": state.get("messages", []) + [progress_msg]
            }

    # 用户确认
    logger.info("用户确认检索结果")
    return {
        "human_approved_retrieve": True,
        "messages": state.get("messages", []) + [progress_msg]
    }


def generate_node(state: JcbgState, config: RunnableConfig) -> dict:
    """
    生成节点 - 调用 jcbg_generator 生成 Word 文档
    """
    logger.info("开始生成检查报告 Word 文档...")

    return generate_jcbg_document(state, config, TASKS)


def router_node(state: JcbgState) -> dict:
    """
    路由节点 - 根据消息中的 task 信息决定任务类型
    """
    messages = state.get("messages", [])
    if not messages:
        return {"task_type": "qa"}

    last_message = messages[-1]
    task_dict = last_message.additional_kwargs.get("task", {}) if hasattr(last_message, 'additional_kwargs') else {}
    task_type = task_dict.get("task_type", "qa")

    logger.info(f"路由决策: task_type={task_type}")
    return {"task_type": task_type}


def router_conditional(state: JcbgState) -> str:
    """
    路由条件函数 - 返回下一个节点名称
    """
    task_type = state.get("task_type", "qa")
    if task_type == "report":
        return "check_deps"
    return "qa"


def _filter_messages_for_llm(messages: list) -> list:
    """
    过滤消息，只保留 LLM 可处理的内容（text/image_url）
    对于非文本内容，生成描述性文本保留上下文
    """
    from langchain_core.messages import SystemMessage
    filtered = []
    for msg in messages:
        content = msg.content
        # 如果 content 是字符串，直接使用
        if isinstance(content, str):
            if isinstance(msg, HumanMessage):
                filtered.append(HumanMessage(content=content))
            elif isinstance(msg, AIMessage):
                filtered.append(AIMessage(content=content))
            elif isinstance(msg, SystemMessage):
                filtered.append(SystemMessage(content=content))
        # 如果 content 是列表，过滤出 text 类型，其他类型生成描述
        elif isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    block_type = block.get("type")
                    if block_type == "text":
                        text_parts.append(block.get("text", ""))
                    elif block_type == "file":
                        # 保留文件信息作为上下文
                        filename = block.get("filename", "文件")
                        text_parts.append(f"[已生成文件: {filename}]")
                    elif block_type == "task_progress":
                        # 跳过任务进度
                        pass
                    elif block_type == "non_standard":
                        value = block.get("value", {})
                        inner_type = value.get("type")
                        if inner_type == "file":
                            filename = value.get("data", {}).get("filename", "文件")
                            text_parts.append(f"[已生成文件: {filename}]")
                elif isinstance(block, str):
                    text_parts.append(block)
            if text_parts:
                combined = "\n".join(text_parts)
                if isinstance(msg, HumanMessage):
                    filtered.append(HumanMessage(content=combined))
                elif isinstance(msg, AIMessage):
                    filtered.append(AIMessage(content=combined))
    return filtered


def qa_node(state: JcbgState) -> dict:
    """
    问答节点 - 回答用户关于检查报告的问题
    LangGraph 自动维护 messages 历史，直接使用即可
    """
    logger.info("处理问答...")

    messages = state.get("messages", [])

    try:
        # 过滤消息，只保留 LLM 可处理的内容
        filtered_messages = _filter_messages_for_llm(messages[-10:])

        # 构建 system prompt + 历史 messages
        system_msg = HumanMessage(content="""你是执法检查专家助手，专门负责检查报告相关的问答。
基于对话历史回答用户问题，保持专业、客观。""")

        llm = get_chat_model(
            temperature=0,
            streaming=True,
            timeout=120
        )

        result = llm.invoke([system_msg] + filtered_messages)
        answer = result.content

        logger.info(f"问答完成，答案长度: {len(answer)} 字符")

        return {"messages": [AIMessage(content=answer)]}

    except Exception as e:
        error_msg = f"问答处理失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise BusinessException(msg=error_msg, code=50021)
