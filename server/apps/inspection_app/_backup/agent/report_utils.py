"""
报告生成公共工具函数
提供可复用的数据提取和处理方法
"""

from typing import Dict, List, Optional
from .states import (
    ViolationsAnalysisResult,
    ViolationData,
    LegalProvision,
    EvidenceItem,
)
from utils.logger import get_logger

logger = get_logger(__name__)


def build_violations_analysis_result(
    violation_file_groups: List[Dict],
    retrieval_results: List[Dict],
    inspected_person: Optional[str] = None,
    inspector: Optional[str] = None,
    inspection_date: Optional[str] = None,
    inspection_location: Optional[str] = None,
    case_number: Optional[str] = None
) -> ViolationsAnalysisResult:
    """
    构建标准化的违规分析结果（所有报告的数据源）

    Args:
        violation_file_groups: 文件分组列表（每个分组 = 1个违法行为）
        retrieval_results: 检索结果列表
        inspected_person: 被检查人（如果为None，从file_groups提取）
        inspector: 检查人（可选）
        inspection_date: 检查时间（可选）
        inspection_location: 检查地点（可选）
        case_number: 案件编号（可选）

    Returns:
        ViolationsAnalysisResult: 标准化数据结构（包含所有数据 + 文件元数据）
    """
    logger.info("开始构建标准化违规分析结果...")

    if not violation_file_groups:
        raise ValueError("violation_file_groups 不能为空")

    # 提取基本信息（优先使用传入参数，否则从file_groups提取）
    if not inspected_person or not inspector or not inspection_date:
        for group in violation_file_groups:
            fact_det = group.get("fact_determination", {})
            parsed_data = fact_det.get("parsed_data", {})

            if not inspected_person:
                inspected_person = parsed_data.get("inspected_person", "")
            if not inspector:
                inspector = parsed_data.get("inspector")
            if not inspection_date:
                inspection_date = parsed_data.get("inspection_time") or parsed_data.get("inspection_date")
            if not inspection_location:
                inspection_location = parsed_data.get("inspection_location")

            if inspected_person and inspector and inspection_date:
                break

    # 设置默认值
    if not inspected_person:
        inspected_person = "被检查单位"
        logger.warning("未找到被检查人，使用默认值")

    # 构建每个违法行为的数据
    violations = []

    for idx, file_group in enumerate(violation_file_groups, start=1):
        violation_id = idx  # 业务规则：文件分组顺序 = violation_id

        # 提取事实认定数据
        fact_det = file_group.get("fact_determination", {})
        parsed_data = fact_det.get("parsed_data", {})

        title = parsed_data.get("inspection_content", "")  # 检查内容（标题）
        facts = parsed_data.get("violation_facts", "")    # 违法事实（内容）

        # 从检索结果中提取该violation_id对应的数据
        violated_provisions_list = []
        penalty_provisions_list = []
        discretionary_benchmarks_list = []
        rectification_measures_list = []
        penalty_recommendations_list = []
        historical_cases_list = []  # ⭐ 新增

        for result in retrieval_results:
            if result.get("violation_id") != violation_id:
                continue

            result_type = result.get("type")
            data = result.get("data", [])

            if result_type == "legal_basis":
                # 转换为 LegalProvision 对象
                for item in data:
                    violated_provisions_list.append(LegalProvision(
                        law_name=item.get("law_name", ""),
                        law_item_name=item.get("law_item_name", ""),
                        law_item_content=item.get("law_item_content", ""),
                        relevance_score=item.get("relevance_score", 1.0)
                    ))

            elif result_type == "penalty_clause":
                # 转换为 LegalProvision 对象
                for item in data:
                    penalty_provisions_list.append(LegalProvision(
                        law_name=item.get("law_name", ""),
                        law_item_name=item.get("law_item_name", ""),
                        law_item_content=item.get("law_item_content", ""),
                        relevance_score=item.get("relevance_score", 1.0)
                    ))

            elif result_type == "discretion_standards":
                discretionary_benchmarks_list.extend(data)

            elif result_type == "rectification_measures":
                rectification_measures_list.extend(data)

            elif result_type == "penalty_suggestions":
                penalty_recommendations_list.extend(data)

            elif result_type == "historical_cases":  # ⭐ 新增
                # 转换为 HistoricalCase 对象
                from .states import HistoricalCase
                for item in data:
                    historical_cases_list.append(HistoricalCase(
                        case_id=item.get("case_id", ""),
                        case_title=item.get("case_title", ""),
                        case_summary=item.get("case_summary", ""),
                        violation_facts=item.get("violation_facts", ""),
                        penalty_result=item.get("penalty_result", ""),
                        reference_value=item.get("reference_value", ""),
                        similarity_score=item.get("similarity_score", 1.0)
                    ))

        # 提取证据（从文件分组中的取证记录表）
        evidence_list_data = []
        evidence_process = None

        evidence_records = file_group.get("evidence_records")
        if evidence_records:
            evidence_parsed = evidence_records.get("parsed_data", {})
            evidence_process = evidence_parsed.get("evidence_process")
            evidence_list_raw = evidence_parsed.get("evidence_list", [])

            # 转换为 EvidenceItem 对象
            for item in evidence_list_raw:
                evidence_list_data.append(EvidenceItem(
                    evidence_name_code=item.get("evidence_name_code", ""),
                    evidence_source=item.get("evidence_source", ""),
                    evidence_form=item.get("evidence_form", ""),
                    evidence_truth=item.get("evidence_truth", ""),
                    provider_sign=item.get("provider_sign"),
                    remark=item.get("remark")
                ))

        # 构建 ViolationData
        violation_data = ViolationData(
            violation_id=violation_id,
            title=title,
            facts=facts,
            violated_provisions=violated_provisions_list,
            penalty_provisions=penalty_provisions_list,
            discretionary_benchmarks=discretionary_benchmarks_list,
            rectification_measures=rectification_measures_list,
            penalty_recommendations=penalty_recommendations_list,
            historical_cases=historical_cases_list,  # ⭐ 新增
            evidence_list=evidence_list_data,
            evidence_process=evidence_process
        )

        violations.append(violation_data)

    # 保存原始文件元数据（包含 parsed_data，用于审核时显示取证记录表）
    source_files = []
    for group in violation_file_groups:
        source_files.append({
            "group_id": group.get("group_id"),
            "fact_determination": {
                "file_id": group.get("fact_determination", {}).get("file_id"),
                "filename": group.get("fact_determination", {}).get("filename"),
                "upload_time": group.get("fact_determination", {}).get("upload_time"),
            },
            "evidence_records": {
                "file_id": group.get("evidence_records", {}).get("file_id") if group.get("evidence_records") else None,
                "filename": group.get("evidence_records", {}).get("filename") if group.get("evidence_records") else None,
                "upload_time": group.get("evidence_records", {}).get("upload_time") if group.get("evidence_records") else None,
                "parsed_data": group.get("evidence_records", {}).get("parsed_data") if group.get("evidence_records") else None,
            } if group.get("evidence_records") else None,
            "created_at": group.get("created_at"),
            "order": group.get("order"),
        })

    result = ViolationsAnalysisResult(
        case_number=case_number,
        inspected_person=inspected_person,
        inspector=inspector,
        inspection_date=inspection_date,
        inspection_location=inspection_location,
        violations=violations,
        source_files=source_files
    )

    logger.info(f"标准化数据构建完成 - 案件: {case_number or '未命名'}, "
                f"被检查人: {inspected_person}, 检查人: {inspector or '未填写'}, "
                f"违法行为数: {len(violations)}, 源文件数: {len(source_files)}")

    return result
