"""
证据目录生成器 (Evidence Catalog Generator)
输出：Excel 文档（双 sheet）
"""

import os
from langchain_core.messages import AIMessage
from langgraph.types import RunnableConfig

from ..states import GraphState, ViolationsAnalysisResult
from ..task_manager import TaskManager
from ...inspection_utils import (
    get_violation_generated_dir,
    create_text_block,
    create_file_block,
    create_task_progress_block,
    create_file_title_block,
    create_excel_data_block,
)
from ...inspection_utils.excel_generator import generate_evidence_catalog_excel
from utils.logger import get_logger
from utils.exceptions import BusinessException

logger = get_logger(__name__)


def extract_zjml_data(result: ViolationsAnalysisResult) -> dict:
    """
    从标准数据结构提取证据目录所需数据（纯函数）

    Args:
        result: 标准化的违规分析结果

    Returns:
        {
            "violations": List[dict],  # 违法行为数据
            "evidence_list": List[dict],  # 证据列表
            "basic_info": dict  # 基本信息
        }
    """
    violations = []
    all_evidence_list = []

    for idx, violation in enumerate(result.violations, 1):
        # 合并违反条款（去重：基于法律名称+条款名称）
        legal_basis_items = []
        seen_legal_basis = set()
        for provision in violation.violated_provisions:
            law_name = provision.law_name
            if not (law_name.startswith('《') and law_name.endswith('》')):
                law_name = f"《{law_name}》"
            law_item_name = provision.law_item_name
            key = f"{law_name}|{law_item_name}"

            if key not in seen_legal_basis:
                seen_legal_basis.add(key)
                legal_basis_items.append({
                    "law_name": law_name,
                    "law_item_name": law_item_name,
                    "law_content": provision.law_item_content
                })

        # 合并处罚条款（去重：基于法律名称+条款名称）
        penalty_items = []
        seen_penalty = set()
        for provision in violation.penalty_provisions:
            law_name = provision.law_name
            if not (law_name.startswith('《') and law_name.endswith('》')):
                law_name = f"《{law_name}》"
            law_item_name = provision.law_item_name
            key = f"{law_name}|{law_item_name}"

            if key not in seen_penalty:
                seen_penalty.add(key)
                penalty_items.append({
                    "law_name": law_name,
                    "law_item_name": law_item_name
                })

        violations.append({
            "violation_id": violation.violation_id,
            "sequence_number": idx,
            "original_fact": violation.title,
            "ai_expansion": violation.facts,
            "legal_basis": legal_basis_items,
            "penalty_clause": penalty_items
        })

        # 收集证据列表（从每个 violation 提取）
        for evidence_item in violation.evidence_list:
            all_evidence_list.append({
                "evidence_name_code": evidence_item.evidence_name_code,
                "evidence_source": evidence_item.evidence_source,
                "evidence_form": evidence_item.evidence_form,
                "evidence_truth": evidence_item.evidence_truth,
                "provider_sign": evidence_item.provider_sign,
                "remark": evidence_item.remark
            })

    # 基本信息（可从 violation_file_groups 或其他途径获取，这里使用默认值）
    basic_info = {
        "inspected_person": result.inspected_person,
    }

    return {
        "violations": violations,
        "evidence_list": all_evidence_list,
        "basic_info": basic_info
    }


def _generate_catalog_data(violations: list, evidence_list: list) -> list[dict]:
    """生成证据目录表数据（Sheet 1）- 纯函数"""
    catalog_data = []

    for v_idx, v in enumerate(violations):
        sequence_number = v.get("sequence_number", "")
        original_fact = v.get("original_fact", "")
        ai_expansion = v.get("ai_expansion", "")

        violation_description = f"{original_fact}"
        if ai_expansion:
            violation_description += f" {ai_expansion}"

        evidence_code = ""
        evidence_name = ""
        main_content = ""
        summary = ""

        if evidence_list and v_idx < len(evidence_list):
            evidence = evidence_list[v_idx]
            full_name = evidence.get("evidence_name_code", "")

            if "：" in full_name or ":" in full_name:
                separator = "：" if "：" in full_name else ":"
                parts = full_name.split(separator, 1)
                evidence_code = parts[0].strip()
                evidence_name = parts[1].strip() if len(parts) > 1 else ""
            else:
                evidence_name = full_name

            main_content = evidence.get("evidence_source", "")

            evidence_form = evidence.get("evidence_form", "")
            evidence_truth = evidence.get("evidence_truth", "")
            if evidence_form or evidence_truth:
                summary = f"{evidence_form} {evidence_truth}".strip()

        legal_basis_items = v.get("legal_basis", [])
        legal_text = ""
        if legal_basis_items:
            legal_text = "；".join([f"{item['law_name']} {item['law_item_name']}" for item in legal_basis_items])

        penalty_items = v.get("penalty_clause", [])
        suggestion = ""
        if penalty_items:
            suggestion = "责令改正；" + "；".join([f"依据{item['law_name']}{item['law_item_name']}进行处罚" for item in penalty_items])
        else:
            suggestion = "责令改正"

        catalog_data.append({
            "sequence_number": sequence_number,
            "violation_behavior": violation_description,
            "evidence_code": evidence_code,
            "evidence_name": evidence_name,
            "main_content": main_content,
            "summary": summary,
            "legal_regulation": legal_text,
            "suggestion": suggestion,
            "remark": ""
        })

    return catalog_data


def _generate_appendix_data(violations: list) -> list[dict]:
    """生成证据目录附件表数据（Sheet 2）- 纯函数"""
    appendix_data = []

    for v in violations:
        sequence_number = v.get("sequence_number", "")
        original_fact = v.get("original_fact", "")
        ai_expansion = v.get("ai_expansion", "")

        violation_description = f"{original_fact}"
        if ai_expansion:
            violation_description += f" {ai_expansion}"

        legal_basis_items = v.get("legal_basis", [])

        for item in legal_basis_items:
            law_regulation = f"{item['law_name']} {item['law_item_name']}"
            law_original_text = item.get('law_content', '')

            appendix_data.append({
                "specialty": "支付业务",
                "problem_number": sequence_number,
                "violation_behavior": violation_description,
                "legal_regulation": law_regulation,
                "law_original_text": law_original_text
            })

    return appendix_data


def generate_zjml_document(state: GraphState, config: RunnableConfig, tasks: list) -> dict:
    """
    生成证据目录 Excel 文档

    Args:
        state: 图状态
        config: 运行时配置
        tasks: 任务列表（用于进度显示）

    Returns:
        包含 AIMessage 的字典
    """
    logger.info("生成证据目录Excel文件...")

    thread_id = config.get("configurable", {}).get("thread_id")

    task_mgr = TaskManager(tasks)
    task_mgr.set_in_progress('generate')

    # 从标准数据结构获取数据 ⭐
    violations_analysis_result = state.get("violations_analysis_result")
    if not violations_analysis_result:
        raise BusinessException(msg="未找到违规分析结果，请先生成检查报告或进行事实认定分析", code=40060)

    # 提取证据目录所需数据
    zjml_data = extract_zjml_data(violations_analysis_result)

    violations = zjml_data["violations"]
    evidence_list = zjml_data["evidence_list"]
    basic_info = zjml_data["basic_info"]

    logger.info(f"提取数据完成 - violations: {len(violations)}, evidence_list: {len(evidence_list)}")

    # 生成两个 sheet 的数据
    logger.info("生成证据目录表数据（Sheet 1）...")
    catalog_data = _generate_catalog_data(violations, evidence_list)
    logger.info(f"证据目录表数据生成完成 - {len(catalog_data)} 条")

    logger.info("生成证据目录附件表数据（Sheet 2）...")
    appendix_data = _generate_appendix_data(violations)
    logger.info(f"证据目录附件表数据生成完成 - {len(appendix_data)} 条")

    try:
        # 生成 Excel 文件
        output_dir = get_violation_generated_dir(thread_id) if thread_id else None
        excel_path = generate_evidence_catalog_excel(
            catalog_data=catalog_data,
            appendix_data=appendix_data,
            basic_info=basic_info,
            output_dir=output_dir
        )

        # 生成预览内容
        content_parts = []
        content_parts.append("# 证据目录\n\n")
        content_parts.append("## Sheet 1: 证据目录\n\n")
        content_parts.append("| 序号 | 违规行为 | 证据编号 | 证据名称 | 主要内容 | 汇总说明 | 法律规定 | 处理建议 | 备注 |\n")
        content_parts.append("|------|---------|---------|---------|---------|---------|---------|---------|-----|\n")

        for row in catalog_data:
            violation_behavior = row['violation_behavior'][:30] + "..." if len(row['violation_behavior']) > 30 else row['violation_behavior']
            main_content = row['main_content'][:30] + "..." if len(row['main_content']) > 30 else row['main_content']
            summary = row['summary'][:30] + "..." if len(row['summary']) > 30 else row['summary']
            legal_regulation = row['legal_regulation'][:30] + "..." if len(row['legal_regulation']) > 30 else row['legal_regulation']
            suggestion = row['suggestion'][:30] + "..." if len(row['suggestion']) > 30 else row['suggestion']

            content_parts.append(
                f"| {row['sequence_number']} | {violation_behavior} | {row['evidence_code']} | "
                f"{row['evidence_name']} | {main_content} | {summary} | {legal_regulation} | "
                f"{suggestion} | {row['remark']} |\n"
            )

        content_parts.append("\n## Sheet 2: 证据目录附件\n\n")
        content_parts.append("| 检查专业 | 问题序号 | 违规行为 | 法律规定 | 法条原文 |\n")
        content_parts.append("|---------|---------|---------|---------|----------|\n")

        for row in appendix_data:
            violation_behavior = row['violation_behavior'][:30] + "..." if len(row['violation_behavior']) > 30 else row['violation_behavior']
            law_text = row['law_original_text'][:50] + "..." if len(row['law_original_text']) > 50 else row['law_original_text']
            content_parts.append(
                f"| {row['specialty']} | {row['problem_number']} | {violation_behavior} | "
                f"{row['legal_regulation']} | {law_text} |\n"
            )

        filename = os.path.basename(excel_path)
        content_parts.append(f"\n[DOWNLOAD_BUTTON:{filename}]")
        final_content = "".join(content_parts)

        logger.info(f"证据目录生成成功，Excel文件路径: {excel_path}")
        task_mgr.set_completed('generate')

        # 构建返回消息
        content_blocks = [
            create_text_block(final_content),
            create_file_block(
                file_id=filename,
                mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                url=f"/api/download/{thread_id}/{filename}",
            ),
            create_task_progress_block([{**task, "status": "completed"} for task in tasks]),
            create_file_title_block("证据目录"),
            create_excel_data_block({
                "catalog": catalog_data,
                "appendix": appendix_data
            }),
        ]

        return {
            "messages": [AIMessage(content=content_blocks)]
        }

    except Exception as e:
        error_msg = f"证据目录生成失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise BusinessException(msg=error_msg, code=50053)
