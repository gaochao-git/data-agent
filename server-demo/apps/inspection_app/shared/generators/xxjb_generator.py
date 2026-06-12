"""
信息简表生成器 (Info Summary Generator)
输出：Excel 文档
"""

import os
from langchain_core.messages import AIMessage
from langgraph.types import RunnableConfig

from ..models import ViolationsAnalysisResult
from ..task_manager import TaskManager
from ...inspection_utils import (
    get_violation_generated_dir,
    create_text_block,
    create_file_block,
    create_task_progress_block,
    create_file_title_block,
    create_excel_data_block,
)
from ...inspection_utils.excel_generator import generate_info_summary_excel
from utils.logger import get_logger
from utils.exceptions import BusinessException

logger = get_logger(__name__)


def extract_xxjb_data(result: ViolationsAnalysisResult, violation_type_level1: str = "") -> list[dict]:
    """
    从标准数据结构提取信息简表所需数据（纯函数）

    Args:
        result: 标准化的违规分析结果
        violation_type_level1: 违法行为类型-一级（已弃用，保留参数兼容性）

    Returns:
        List[{
            "violation_id": int,
            "original_fact": str,
            "ai_expansion": str,
            "violation_type_level1": str,
            "violation_type_level2": str,
            "legal_basis": str,
            "penalty_clause": str,
            "discretion_standards": str
        }]
    """
    violations_data = []

    for idx, violation in enumerate(result.violations, 1):
        # 合并违反条款（去重：基于法律名称+条款名称）
        legal_basis_list = []
        seen_legal_basis = set()
        for provision in violation.violated_provisions:
            law_name = provision.law_name
            if not (law_name.startswith('《') and law_name.endswith('》')):
                law_name = f"《{law_name}》"
            law_item_name = provision.law_item_name
            key = f"{law_name}|{law_item_name}"

            if key not in seen_legal_basis:
                seen_legal_basis.add(key)
                legal_basis_list.append(f"{law_name} {law_item_name}")

        # 合并处罚条款（去重：基于法律名称+条款名称）
        penalty_text_list = []
        seen_penalty = set()
        for provision in violation.penalty_provisions:
            law_name = provision.law_name
            if not (law_name.startswith('《') and law_name.endswith('》')):
                law_name = f"《{law_name}》"
            law_item_name = provision.law_item_name
            key = f"{law_name}|{law_item_name}"

            if key not in seen_penalty:
                seen_penalty.add(key)
                penalty_text_list.append(f"{law_name} {law_item_name}")

        # 提取裁量标准信息
        discretion_text = "；".join(violation.discretionary_benchmarks) if violation.discretionary_benchmarks else ""

        # 提取整改措施
        rectification_text = "；".join(violation.rectification_measures) if violation.rectification_measures else ""

        # 提取处罚建议
        penalty_suggestions = violation.penalty_recommendations if violation.penalty_recommendations else []

        # 违法行为类型：一级和二级都使用 violation.title
        # 目前数据结构中没有更高层级的分类，所以一级二级相同
        violation_type = violation.title

        violations_data.append({
            "violation_id": violation.violation_id,
            "original_fact": violation.title,
            "ai_expansion": violation.facts,
            "violation_type_level1": violation_type,
            "violation_type_level2": violation_type,
            "legal_basis": "；".join(legal_basis_list) if legal_basis_list else "（待补充）",
            "penalty_clause": "；".join(penalty_text_list) if penalty_text_list else "（待补充）",
            "discretion_standards": discretion_text,
            "rectification_measures": rectification_text,
            "penalty_suggestions": penalty_suggestions  # 原始列表，方便后续处理
        })

    return violations_data


def generate_xxjb_document(state: dict, config: RunnableConfig, tasks: list) -> dict:
    """
    生成信息简表 Excel 文档

    Args:
        state: 图状态
        config: 运行时配置
        tasks: 任务列表（用于进度显示）

    Returns:
        包含 AIMessage 的字典
    """
    logger.info("生成信息简表...")

    thread_id = config.get("configurable", {}).get("thread_id")

    task_mgr = TaskManager(tasks)
    task_mgr.set_in_progress('generate')

    # 从标准数据结构获取数据 ⭐
    violations_analysis_result = state.get("violations_analysis_result")
    if not violations_analysis_result:
        raise BusinessException(msg="未找到违规分析结果，请先生成检查报告或进行事实认定分析", code=40060)

    try:
        # 提取数据（每个违法行为的一级二级分类都从自身的 title 获取）
        violations = extract_xxjb_data(violations_analysis_result)
        logger.info(f"提取到 {len(violations)} 个违法行为数据")

        # 准备 Excel 数据
        violations_data = []
        for idx, v in enumerate(violations):
            # 违法事实：原始事实 + AI扩展
            violation_facts = v.get("original_fact", "")
            if v.get("ai_expansion"):
                ai_expansion = v.get("ai_expansion", "").strip()
                if ai_expansion and ai_expansion != violation_facts:
                    violation_facts = f"{violation_facts}\n{ai_expansion}"

            # 生成序号
            sequence_number = f"4.1.{idx + 1}"

            # 智能分配处罚建议到机构/个人处理建议
            penalty_suggestions = v.get("penalty_suggestions", [])
            org_suggestions = []
            person_suggestions = []

            for suggestion in penalty_suggestions:
                suggestion_lower = suggestion.lower()
                # 判断是否为个人责任建议（包含"责任人"、"个人"、"直接责任"等关键词）
                if any(keyword in suggestion for keyword in ["责任人", "个人", "直接责任", "相关人员", "从业人员"]):
                    person_suggestions.append(suggestion)
                else:
                    # 默认为机构建议
                    org_suggestions.append(suggestion)

            # 合并处理建议（如果没有分类，则全部放入机构建议）
            if not org_suggestions and not person_suggestions and penalty_suggestions:
                org_suggestions = penalty_suggestions

            org_suggestion_text = "；".join(org_suggestions) if org_suggestions else ""
            person_suggestion_text = "；".join(person_suggestions) if person_suggestions else ""

            # 备注信息：包含裁量标准和整改措施
            remark_parts = []
            if v.get('discretion_standards'):
                remark_parts.append(f"裁量标准: {v.get('discretion_standards')}")
            if v.get('rectification_measures'):
                remark_parts.append(f"整改措施: {v.get('rectification_measures')}")
            remark = "；".join(remark_parts) if remark_parts else ""

            violations_data.append({
                "sequence_number": sequence_number,
                "business_area": "四、支付结算",
                "violation_type_level1": v.get("violation_type_level1", ""),
                "violation_type_level2": v.get("violation_type_level2", ""),
                "violation_facts": violation_facts,
                "legal_basis": v.get("legal_basis", ""),
                "penalty_clause": v.get("penalty_clause", ""),
                "penalty_method": "",  # 计罚方式：需要从裁量基准中提取或用户补充
                "penalty_min": "",     # 处罚下限：需要从裁量基准中提取或用户补充
                "penalty_max": "",     # 处罚上限：需要从裁量基准中提取或用户补充
                "behavior_count": "",  # 行为数量：需要用户补充
                "org_suggestion_unit": org_suggestion_text,  # 机构处理建议（检查实施单位）
                "org_suggestion_dept": "",  # 机构处理建议（相关司局）- 需要用户补充
                "org_suggestion_legal": "",  # 机构处理建议（条法司）- 需要用户补充
                "person_suggestion_unit": person_suggestion_text,  # 个人处理建议（检查实施单位）
                "person_suggestion_dept": "",  # 个人处理建议（相关司局）- 需要用户补充
                "person_suggestion_legal": "",  # 个人处理建议（条法司）- 需要用户补充
                "remark": remark
            })

        # 基本信息
        basic_info = {
            "check_case_no": "",
            "case_name": "对xx银行xx执法检查移送案件",
            "description": f"对xx违反xx金融监管规定案件，检查组对被检查单位检查，共发现{len(violations)}项违规行为。"
        }

        # 生成 Excel 文件
        output_dir = get_violation_generated_dir(thread_id) if thread_id else None
        excel_path = generate_info_summary_excel(
            violations_data=violations_data,
            basic_info=basic_info,
            output_dir=output_dir
        )

        filename = os.path.basename(excel_path)

        # 生成预览内容
        content_parts = []
        content_parts.append("✅ 信息简表生成完成\n\n")
        content_parts.append(f"文档名称: {filename}\n\n")
        content_parts.append(f"违规行为数量: {len(violations)}项\n\n")
        content_parts.append("## 违规行为概览\n\n")
        content_parts.append("| 序号 | 违法事实概要 | 处理意见 |\n")
        content_parts.append("|------|------------|----------|\n")

        for v_data in violations_data:
            seq = v_data.get('sequence_number', '')
            fact = v_data.get('violation_facts', '')[:80]
            if len(v_data.get('violation_facts', '')) > 80:
                fact += "..."
            opinion = v_data.get('handling_opinion', '')[:50]
            if len(v_data.get('handling_opinion', '')) > 50:
                opinion += "..."
            content_parts.append(f"| {seq} | {fact} | {opinion} |\n")

        content_parts.append(f"\n[DOWNLOAD_BUTTON:{filename}]")
        final_content = "".join(content_parts)

        logger.info(f"信息简表生成成功 - 违规行为数量: {len(violations)}项")
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
            create_file_title_block("信息简表"),
            create_excel_data_block(violations_data),
        ]

        return {
            "messages": [AIMessage(content=content_blocks)]
        }

    except BusinessException:
        raise
    except Exception as e:
        error_msg = f"信息简表生成失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return {
            "answer": {
                "type": "error",
                "message": error_msg
            }
        }
