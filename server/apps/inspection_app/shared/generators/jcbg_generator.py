"""
检查报告生成器 (Inspection Report Generator)
输出：Word 文档
"""

import os
from datetime import datetime
from docx import Document
from docx.shared import Pt, Cm
from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate
from langgraph.types import RunnableConfig

from ..models import ViolationsAnalysisResult
from ..task_manager import TaskManager
from ..prompts import (
    JCBG_BT_GENERATION_PROMPT,
    JCBG_SS_EXTRACTION_PROMPT,
    JCBG_WFXW_POLISH_PROMPT,
    JCBG_ZGCS_POLISH_PROMPT,
    JCBG_CFJY_POLISH_PROMPT,
)
from ...inspection_utils import (
    set_chinese_font,
    get_violation_generated_path,
    create_text_block,
    create_file_block,
    create_task_progress_block,
    create_file_title_block,
    create_report_source_data_block,
    get_chat_model,
)
from utils.logger import get_logger
from utils.exceptions import BusinessException

logger = get_logger(__name__)


def extract_jcbg_data(result: ViolationsAnalysisResult) -> dict:
    """
    从标准数据结构提取检查报告所需数据（纯函数）

    Args:
        result: 标准化的违规分析结果

    Returns:
        {
            "inspected_person": str,
            "violations_content": [{
                "inspection_content": str,
                "violation_facts": str,
                "legal_basis_list": List[str],
                "penalty_clauses": List[dict],
                "discretion_standards": List[str],
                "rectification_measures_list": List[str],
                "penalty_suggestions_list": List[str]
            }, ...]
        }
    """
    violations_content = []

    for violation in result.violations:
        # 违反条款格式化（用于大模型润色）
        legal_basis_list = []
        for provision in violation.violated_provisions:
            law_name = provision.law_name
            if not (law_name.startswith('《') and law_name.endswith('》')):
                law_name = f"《{law_name}》"

            # 格式化为《法律名》第x条关于"具体内容"
            formatted = f"{law_name}{provision.law_item_name}"
            if provision.law_item_content:
                # 提取关键内容（前50字）
                content_summary = provision.law_item_content[:50]
                if len(provision.law_item_content) > 50:
                    content_summary += "..."
                formatted += f'关于"{content_summary}"'
            legal_basis_list.append(formatted)

        # 处罚条款格式化
        penalty_clauses = []
        for provision in violation.penalty_provisions:
            penalty_clauses.append({
                "law_name": provision.law_name,
                "law_item_name": provision.law_item_name,
                "law_item_content": provision.law_item_content
            })

        violations_content.append({
            "inspection_content": violation.title,
            "violation_facts": violation.facts,
            "legal_basis_list": legal_basis_list,
            "penalty_clauses": penalty_clauses,
            "discretion_standards": violation.discretionary_benchmarks,
            "rectification_measures_list": violation.rectification_measures,
            "penalty_suggestions_list": violation.penalty_recommendations
        })

    return {
        "inspected_person": result.inspected_person,
        "violations_content": violations_content
    }


def generate_jcbg_document(state: dict, config: RunnableConfig, tasks: list) -> dict:
    """
    生成检查报告 Word 文档

    Args:
        state: 图状态
        config: 运行时配置
        tasks: 任务列表（用于进度显示）

    Returns:
        包含 AIMessage 的字典
    """
    logger.info("生成检查报告...")

    thread_id = config.get("configurable", {}).get("thread_id")

    task_mgr = TaskManager(tasks)
    task_mgr.set_in_progress('generate')

    # 从标准数据结构获取数据 ⭐
    violations_analysis_result = state.get("violations_analysis_result")
    if not violations_analysis_result:
        raise BusinessException(msg="未找到违规分析结果，请先生成检查报告或进行事实认定分析", code=40060)

    # 提取检查报告所需数据
    report_data = extract_jcbg_data(violations_analysis_result)

    try:
        inspected_person = report_data.get("inspected_person", "")
        violations_content = report_data.get("violations_content", [])

        logger.info(f"提取的数据 - 检查内容数量: {len(violations_content)}")

        # 加载模板文件
        template_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "report_template",
            "检查报告.docx"
        )

        if not os.path.exists(template_path):
            raise BusinessException(msg=f"模板文件不存在: {template_path}", code=50041)

        logger.info(f"加载模板: {template_path}")
        doc = Document(template_path)

        # 查找"二、支付结算方面的主要问题"段落的索引
        target_index = -1
        for i, para in enumerate(doc.paragraphs):
            if "二、支付结算方面的主要问题" in para.text:
                target_index = i
                logger.info(f"找到目标段落索引: {i}, 内容: {para.text}")
                break

        if target_index == -1:
            raise BusinessException(msg="未找到'二、支付结算方面的主要问题'段落", code=50042)

        # 找到占位符段落的位置，删除它，然后在此位置插入新段落
        if target_index + 1 < len(doc.paragraphs):
            placeholder_para = doc.paragraphs[target_index + 1]
            # 获取占位符段落所在的父元素
            p_element = placeholder_para._element
            parent = p_element.getparent()
            insert_index = parent.index(p_element)
            # 删除占位符
            parent.remove(p_element)

            # 构建主要问题内容（支持多个检查内容）
            problems_paragraphs = []

            for idx, vio_data in enumerate(violations_content, start=1):
                # （一）违规行为描述（30字以内，大模型生成）
                title = _call_llm_with_prompt(
                    prompt_template=JCBG_BT_GENERATION_PROMPT,
                    params={
                        'inspection_content': vio_data['inspection_content'],
                        'violation_summary': vio_data['violation_facts'][:500]
                    },
                    task_name="标题生成",
                    fallback_fn=lambda: vio_data['inspection_content'][:30]
                )
                if len(title) > 30:
                    title = title[:30]
                problems_paragraphs.append({
                    'text': f"（{_num_to_chinese(idx)}）{title}",
                    'first_line_indent': False
                })

                # 事实内容（大模型提炼关键事实）
                if vio_data['violation_facts']:
                    key_facts = _call_llm_with_prompt(
                        prompt_template=JCBG_SS_EXTRACTION_PROMPT,
                        params={
                            'inspected_person': inspected_person or "未提供",
                            'violation_facts': vio_data['violation_facts']
                        },
                        task_name="事实提炼",
                        fallback_fn=lambda: vio_data['violation_facts']
                    )
                    problems_paragraphs.append({
                        'text': key_facts,
                        'first_line_indent': True
                    })

                # 违法行为（大模型润色）
                if vio_data['legal_basis_list']:
                    legal_paragraph = _call_llm_with_prompt(
                        prompt_template=JCBG_WFXW_POLISH_PROMPT,
                        params={'legal_basis_list': "\n".join([f"- {item}" for item in vio_data['legal_basis_list']])},
                        task_name="违法行为润色",
                        fallback_fn=lambda: f"以上行为违反了{'、'.join(vio_data['legal_basis_list'])}"
                    )
                    problems_paragraphs.append({
                        'text': legal_paragraph,
                        'first_line_indent': True
                    })

                # 整改措施（大模型润色）
                if vio_data['rectification_measures_list']:
                    rectification_paragraph = _call_llm_with_prompt(
                        prompt_template=JCBG_ZGCS_POLISH_PROMPT,
                        params={
                            'inspected_person': inspected_person,
                            'rectification_measures_list': "\n".join([f"- {item}" for item in vio_data['rectification_measures_list']])
                        },
                        task_name="整改措施润色",
                        fallback_fn=lambda: f"对此行为，{inspected_person}应当采取以下整改措施：{'，'.join(vio_data['rectification_measures_list'])}"
                    )
                    problems_paragraphs.append({
                        'text': rectification_paragraph,
                        'first_line_indent': True
                    })

                # 处罚建议（大模型润色，包含处罚条款和裁量基准）
                if vio_data['penalty_suggestions_list']:
                    # 格式化处罚条款
                    penalty_clauses_text = ""
                    if vio_data['penalty_clauses']:
                        for clause in vio_data['penalty_clauses']:
                            law_name = clause.get("law_name", "")
                            law_item_name = clause.get("law_item_name", "")
                            law_item_content = clause.get("law_item_content", "")
                            penalty_clauses_text += f"- {law_name}{law_item_name}：{law_item_content}\n"

                    penalty_paragraph = _call_llm_with_prompt(
                        prompt_template=JCBG_CFJY_POLISH_PROMPT,
                        params={
                            'violation_title': title,  # 传递违规行为标题
                            'penalty_suggestions_list': "\n".join([f"- {item}" for item in vio_data['penalty_suggestions_list']]),
                            'penalty_clauses': penalty_clauses_text if penalty_clauses_text else "无",
                            'discretion_standards': "\n".join([f"- {item}" for item in vio_data['discretion_standards']]) if vio_data['discretion_standards'] else "无"
                        },
                        task_name="处罚建议润色",
                        fallback_fn=lambda: '；'.join(vio_data['penalty_suggestions_list']),
                        timeout=60
                    )
                    problems_paragraphs.append({
                        'text': penalty_paragraph,
                        'first_line_indent': True
                    })

                # 检查内容之间空一行
                if idx < len(violations_content):
                    problems_paragraphs.append({
                        'text': '',
                        'first_line_indent': False
                    })

            # 在指定位置插入段落
            logger.info(f"填充内容 - 共 {len(violations_content)} 个检查内容，{len(problems_paragraphs)} 个段落")
            for para_data in problems_paragraphs:
                # 在指定位置插入新段落
                new_para = doc.add_paragraph()
                # 将新段落移动到正确位置
                new_p_element = new_para._element
                parent.insert(insert_index, new_p_element)
                insert_index += 1

                # 设置段落内容和格式
                if para_data['text']:
                    run = new_para.add_run(para_data['text'])
                    set_chinese_font(run, '仿宋_GB2312', 16)

                # 设置首行缩进（2字符 = 约0.74cm）
                if para_data['first_line_indent']:
                    new_para.paragraph_format.first_line_indent = Cm(0.74)

        # 保存文档
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"检查报告_{timestamp}.docx"
        file_path = get_violation_generated_path(thread_id, filename)
        doc.save(file_path)

        logger.info(f"检查报告生成成功: {file_path}")
        task_mgr.set_completed('generate')

        # 构建返回消息（保留报告源数据用于前端编辑）
        report_source_data = {
            "report_type": "jcbg",
            "violations_count": len(violations_content),
            "inspected_person": inspected_person
        }

        content_blocks = [
            create_text_block(f"✅ 检查报告生成完成\n\n文档名称：{filename}"),
            create_file_block(
                file_id=filename,
                mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                url=f"/api/download/{thread_id}/{filename}",
            ),
            create_task_progress_block([{**task, "status": "completed"} for task in tasks]),
            create_file_title_block("检查报告"),
            create_report_source_data_block(report_source_data),
        ]

        return {
            "messages": [AIMessage(content=content_blocks)]
        }

    except Exception as e:
        error_msg = f"检查报告生成失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return {
            "messages": [
                AIMessage(
                    content=[create_text_block(f"❌ {error_msg}")],
                    additional_kwargs={"error": True}
                )
            ]
        }


# ========== 辅助函数 ==========

def _call_llm_with_prompt(prompt_template, params, task_name, fallback_fn=None, temperature=0.3, timeout=30):
    """
    通用LLM调用函数

    Args:
        prompt_template: 提示词模板（来自 prompts.py）
        params: 格式化参数字典
        task_name: 任务名称（用于日志）
        fallback_fn: 失败时的fallback函数
        temperature: LLM温度
        timeout: 超时时间

    Returns:
        LLM生成的结果
    """
    try:
        llm = get_chat_model(temperature=temperature, timeout=timeout)
        formatted_prompt = prompt_template.format(**params)
        response = llm.invoke(formatted_prompt)
        result = response.content.strip()
        logger.info(f"{task_name}完成")
        return result
    except Exception as e:
        logger.error(f"{task_name}失败: {str(e)}, 使用fallback方案")
        if fallback_fn:
            return fallback_fn()
        return ""


def _num_to_chinese(num):
    """
    将数字转换为中文数字

    Args:
        num: 数字（1-10）

    Returns:
        中文数字字符串
    """
    chinese_nums = {
        1: "一",
        2: "二",
        3: "三",
        4: "四",
        5: "五",
        6: "六",
        7: "七",
        8: "八",
        9: "九",
        10: "十"
    }

    return chinese_nums.get(num, str(num))
