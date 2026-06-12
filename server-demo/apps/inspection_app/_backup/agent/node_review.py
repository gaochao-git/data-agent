"""
报告生成人工审核节点
使用 LangGraph interrupt 机制实现暂停和恢复
包含2个审核节点：
1. review_retrieve - 审核检索结果（法律依据检索）
2. review_evidence - 审核取证记录表（证据目录报告）

注：extract 和 analyze 审核节点已废弃，文件在上传时已解析，不再需要在报告生成时审核
"""

from langgraph.types import interrupt, Command
from langgraph.config import get_stream_writer

from .states import GraphState
from .task_manager import TaskManager
from ..inspection_utils import create_progress_message
from utils.logger import get_logger

logger = get_logger(__name__)


def create_human_review_retrieve_node(config: dict):
    """
    创建检索结果人工审核节点（工厂函数）

    Args:
        config: 节点配置 (包含 tasks)
    """
    def human_review_retrieve_node(state: GraphState) -> GraphState:
        """
        检索结果人工审核节点

        审核法律依据检索结果：
        - violations: 违法行为列表，每项包含完整的5项法律数据

        支持的操作：
        - approve: 确认当前结果，继续执行
        - edit: 修改后继续执行
        - reject: 拒绝，终止执行
        """
        violations_analysis_result = state.get("violations_analysis_result")

        if not violations_analysis_result:
            logger.error("检索结果为空，无法进行审核")
            raise ValueError("检索结果为空，请先完成法律依据检索")

        task_mgr = TaskManager(config['tasks'])
        review_task_id = 'review_retrieve'
        task_mgr.set_in_progress(review_task_id)

        # 从标准化结构提取审核数据
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

            # 获取该违法行为对应的附件（source_files 索引与 violation 索引对应）
            source_files_for_violation = []
            evidence_rec = None  # 初始化变量，避免作用域问题
            group_id = None  # 保存 group_id，用于前端上传取证记录表

            if violations_analysis_result.source_files and idx < len(violations_analysis_result.source_files):
                group = violations_analysis_result.source_files[idx]
                group_id = group.get("group_id")  # 获取分组ID
                logger.info(f"违法行为 {idx} 的附件组: {group}, group_id: {group_id}")

                # 构建附件列表：事实认定书 + 取证记录表（如果有）
                if group.get("fact_determination"):
                    fact_det = group["fact_determination"]
                    if fact_det.get("file_id"):
                        source_files_for_violation.append({
                            "file_id": fact_det["file_id"],
                            "filename": fact_det.get("filename", "事实认定书"),
                            "file_name": fact_det.get("filename", "事实认定书"),
                            "file_type": "事实认定书",
                            "upload_time": fact_det.get("upload_time"),
                            "group_id": group_id  # 包含 group_id
                        })
                        logger.info(f"添加事实认定书: {fact_det.get('file_id')}")

                evidence_rec = group.get("evidence_records")
                logger.info(f"取证记录表数据: {evidence_rec}")
                if evidence_rec and evidence_rec.get("file_id"):
                    source_files_for_violation.append({
                        "file_id": evidence_rec["file_id"],
                        "filename": evidence_rec.get("filename", "取证记录表"),
                        "file_name": evidence_rec.get("filename", "取证记录表"),
                        "file_type": "取证记录表",
                        "upload_time": evidence_rec.get("upload_time"),
                        "group_id": group_id  # 包含 group_id
                    })
                    logger.info(f"添加取证记录表: {evidence_rec.get('file_id')}")

            # 获取该违法行为对应的取证记录表证据列表
            evidence_list = []
            if evidence_rec and evidence_rec.get("file_id"):
                # 从 parsed_data 中提取证据列表
                parsed_data = evidence_rec.get("parsed_data", {})
                evidence_list = parsed_data.get("evidence_list", [])
                logger.info(f"提取证据列表: {len(evidence_list)} 条证据")

            review_data.append({
                "violation_id": violation.violation_id,
                "title": violation.title,
                "facts": violation.facts,
                # 基本信息（从案件级别传递给每个违法行为，方便前端编辑）
                "inspected_person": violations_analysis_result.inspected_person,
                "inspector": violations_analysis_result.inspector,
                "inspection_date": violations_analysis_result.inspection_date,
                "inspection_location": violations_analysis_result.inspection_location,
                # 法律检索结果
                "legal_basis": legal_basis_list,
                "penalty_clause": penalty_clause_list,
                "discretion_standard": violation.discretionary_benchmarks,
                "rectification_measures": violation.rectification_measures,
                "penalty_suggestion": violation.penalty_recommendations,
                # 附件信息（该违法行为对应的源文件）
                "source_files": source_files_for_violation,
                # 取证记录表证据列表
                "evidence_list": evidence_list
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
                        # 附加汇总数据供参考
                        "summary": {
                            "violation_count": len(review_data),
                            "inspected_person": violations_analysis_result.inspected_person
                        }
                    }
                }
            }
        })

        logger.info(f"等待用户审核检索结果，共 {len(review_data)} 个违法行为...")

        # 判断是否需要取证记录表（证据目录报告需要）
        report_type = config.get("report_type", "")
        requires_evidence = report_type == "zjml"  # 证据目录需要取证记录表

        # 检查每个违法行为是否已上传取证记录表
        violation_upload_status = {}
        all_uploaded = True

        if requires_evidence:
            for violation in violations_analysis_result.violations:
                v_id = violation.violation_id
                has_file = violation.evidence_record_file is not None
                violation_upload_status[v_id] = has_file
                if not has_file:
                    all_uploaded = False

        logger.info(f"报告类型: {report_type}, 需要取证记录表: {requires_evidence}, 上传状态: {violation_upload_status}, 全部上传: {all_uploaded}")

        # 中断执行，等待用户输入
        human_input = interrupt({
            "review_type": "retrieve",
            "message": "请确认法律依据检索结果，确认后将生成报告",
            "current_data": {
                "violations": review_data
            },
            "requires_evidence_upload": requires_evidence,  # 是否需要上传取证记录表
            "violation_upload_status": violation_upload_status,  # 每个违法行为的上传状态
            "all_evidence_uploaded": all_uploaded  # 是否所有违法行为都已上传
        })

        logger.info(f"收到用户审核结果: {human_input}")

        # 处理用户输入
        # 注意：前端会在 human_input.data.violations 中包含更新后的所有数据（包括新上传的文件）
        action = human_input.get("action", "approve")
        task_mgr.set_completed(review_task_id)

        # 保存进度到 messages
        progress_msg = create_progress_message(task_mgr._build_task_list(review_task_id, 'completed'))

        if action == "reject":
            logger.info("用户拒绝检索结果，终止执行")
            raise ValueError("用户取消操作")

        elif action == "edit":
            # 用户编辑了数据，需要重建 violations_analysis_result
            modified_violations = human_input.get("data", {}).get("violations", [])
            if modified_violations:
                from .states import ViolationData, LegalProvision
                from .report_utils import build_violations_analysis_result

                # 提取基本信息（从第一个违法行为中获取，因为前端在第一个违法行为中编辑）
                first_violation = modified_violations[0] if modified_violations else {}
                inspected_person = first_violation.get("inspected_person") or violations_analysis_result.inspected_person
                inspector = first_violation.get("inspector") or violations_analysis_result.inspector
                inspection_date = first_violation.get("inspection_date") or violations_analysis_result.inspection_date
                inspection_location = first_violation.get("inspection_location") or violations_analysis_result.inspection_location

                # 重建 ViolationData 列表
                new_violations = []
                for idx, v in enumerate(modified_violations):
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

                    # 重建 evidence_list（从前端传来的 evidence_list 或保留原有证据）
                    evidence_list_data = []
                    if v.get("evidence_list"):
                        # 前端传来了新的 evidence_list（用户上传了取证记录表）
                        from .states import EvidenceItem
                        for item in v.get("evidence_list", []):
                            evidence_list_data.append(EvidenceItem(
                                evidence_name_code=item.get("evidence_name_code", ""),
                                evidence_source=item.get("evidence_source", ""),
                                evidence_form=item.get("evidence_form", ""),
                                evidence_truth=item.get("evidence_truth", ""),
                                provider_sign=item.get("provider_sign"),
                                remark=item.get("remark")
                            ))
                    elif idx < len(violations_analysis_result.violations):
                        # 保留原有的 evidence_list
                        evidence_list_data = violations_analysis_result.violations[idx].evidence_list

                    # ⭐ 提取取证记录表文件信息（从 source_files 中查找）
                    evidence_record_file_data = None
                    violation_source_files = v.get("source_files", [])
                    for file in violation_source_files:
                        if file.get("file_type") == "取证记录表":
                            evidence_record_file_data = {
                                "file_id": file.get("file_id"),
                                "filename": file.get("filename") or file.get("file_name"),
                                "upload_time": file.get("upload_time"),
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
                        evidence_list=evidence_list_data,  # 保留或更新证据列表
                        evidence_record_file=evidence_record_file_data  # ⭐ 设置取证记录表文件信息
                    ))

                # 重建 ViolationsAnalysisResult（包含更新后的基本信息）
                # 需要从 human_input 中重新构建 source_files（因为用户可能上传了取证记录表）
                from .states import ViolationsAnalysisResult

                # 从 human_input.data.violations 中提取更新后的 source_files
                # 前端会将新上传的文件信息包含在这里
                source_files = []
                for idx, v in enumerate(modified_violations):
                    # 从前端发送的数据中提取 source_files
                    violation_source_files = v.get("source_files", [])

                    # 按前端格式构建 source_files 结构
                    group_id = violation_source_files[0].get("group_id") if violation_source_files else None

                    # 分离事实认定书和取证记录表
                    fact_det_file = None
                    evidence_rec_file = None

                    for file in violation_source_files:
                        if file.get("file_type") == "事实认定书":
                            fact_det_file = file
                        elif file.get("file_type") == "取证记录表":
                            evidence_rec_file = file

                    source_files.append({
                        "group_id": group_id,
                        "fact_determination": {
                            "file_id": fact_det_file.get("file_id") if fact_det_file else None,
                            "filename": fact_det_file.get("filename") or fact_det_file.get("file_name") if fact_det_file else None,
                            "upload_time": fact_det_file.get("upload_time") if fact_det_file else None,
                        } if fact_det_file else None,
                        "evidence_records": {
                            "file_id": evidence_rec_file.get("file_id") if evidence_rec_file else None,
                            "filename": evidence_rec_file.get("filename") or evidence_rec_file.get("file_name") if evidence_rec_file else None,
                            "upload_time": evidence_rec_file.get("upload_time") if evidence_rec_file else None,
                            # parsed_data 需要从后端重新读取（前端没有这个数据）
                            "parsed_data": None,  # 稍后从文件系统读取
                        } if evidence_rec_file else None,
                    })

                new_result = ViolationsAnalysisResult(
                    case_number=violations_analysis_result.case_number,
                    inspected_person=inspected_person,
                    inspector=inspector,
                    inspection_date=inspection_date,
                    inspection_location=inspection_location,
                    violations=new_violations,
                    source_files=source_files  # 使用重新构建的 source_files
                )

                logger.info(f"用户修改了检索结果，新结果包含 {len(new_violations)} 个违法行为，"
                           f"被检查人: {inspected_person}, 检查人: {inspector}, 检查时间: {inspection_date}")

                # 更新状态并继续执行（不使用 Command，直接返回状态更新）
                return {
                    "violations_analysis_result": new_result,
                    "human_approved_retrieve": True,
                    "messages": state.get("messages", []) + [progress_msg]
                }

        # 默认：用户确认
        logger.info("用户确认检索结果")
        return {
            "human_approved_retrieve": True,
            "messages": state.get("messages", []) + [progress_msg]
        }

    return human_review_retrieve_node


def create_human_review_evidence_node(config: dict):
    """
    创建取证记录表人工审核节点（工厂函数）

    Args:
        config: 节点配置 (包含 tasks)
    """
    def human_review_evidence_node(state: GraphState) -> GraphState:
        """
        取证记录表人工审核节点

        审核取证记录表的解析结果：
        - evidence_list: 证据列表，每项包含 evidence_name_code, evidence_source, evidence_form, evidence_truth
        - basic_info: 基本信息

        支持的操作：
        - approve: 确认当前结果，继续执行
        - edit: 修改后继续执行
        - reject: 拒绝，终止执行
        """
        evidence_records = state.get("evidence_records", {})

        if not evidence_records:
            logger.error("取证记录表解析结果为空，无法进行审核")
            raise ValueError("取证记录表解析结果为空")

        task_mgr = TaskManager(config['tasks'])
        review_task_id = 'review_evidence'
        task_mgr.set_in_progress(review_task_id)

        evidence_list = evidence_records.get("evidence_list", [])
        basic_info = evidence_records.get("basic_info", {})

        # 构建审核数据
        review_data = {
            "evidence_list": evidence_list,
            "basic_info": basic_info,
        }

        # 发送审核数据给前端
        writer = get_stream_writer()
        writer({
            "type": "non_standard",
            "value": {
                "type": "human_review",
                "data": {
                    "review_type": "evidence",
                    "message": "请确认取证记录表解析结果，确认后将生成证据目录",
                    "data": review_data
                }
            }
        })

        logger.info(f"等待用户审核取证记录表，共 {len(evidence_list)} 条证据...")

        # 中断执行，等待用户输入
        human_input = interrupt({
            "review_type": "evidence",
            "message": "请确认取证记录表解析结果，确认后将生成证据目录",
            "current_data": review_data
        })

        logger.info(f"收到用户审核结果: {human_input}")

        # 处理用户输入
        action = human_input.get("action", "approve")
        task_mgr.set_completed(review_task_id)

        # 保存进度到 messages
        progress_msg = create_progress_message(task_mgr._build_task_list(review_task_id, 'completed'))

        if action == "reject":
            logger.info("用户拒绝取证记录表，终止执行")
            raise ValueError("用户取消操作")

        elif action == "edit":
            # 用户编辑了数据，更新状态
            modified_data = human_input.get("data", {})
            if modified_data:
                new_evidence_records = {
                    "evidence_list": modified_data.get("evidence_list", evidence_list),
                    "basic_info": modified_data.get("basic_info", basic_info),
                }
                logger.info(f"用户修改了取证记录表，新结果包含 {len(new_evidence_records['evidence_list'])} 条证据")

                # 更新状态并继续执行（不使用 Command，直接返回状态更新）
                return {
                    "evidence_records": new_evidence_records,
                    "human_approved_evidence": True,
                    "messages": state.get("messages", []) + [progress_msg]
                }

        # 默认：用户确认
        logger.info("用户确认取证记录表")
        return {
            "human_approved_evidence": True,
            "messages": state.get("messages", []) + [progress_msg]
        }

    return human_review_evidence_node
