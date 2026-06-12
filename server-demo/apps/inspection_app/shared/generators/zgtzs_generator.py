"""
整改通知书生成器 (Rectification Notice Generator)
输出：Word 文档

格式要求：
（一）[违法行为1标题]
[事实内容]
[违法行为描述]

（二）[违法行为2标题]
[事实内容]
[违法行为描述]

对此行为，{被检查人}应当采取以下整改措施：
1. xxx
2. yyy
"""

import os
from datetime import datetime
from docx import Document
from docx.enum.text import WD_COLOR_INDEX
from langchain_core.messages import AIMessage
from langgraph.types import RunnableConfig

from ..models import ViolationsAnalysisResult
from ..task_manager import TaskManager
from ...inspection_utils import (
    set_chinese_font,
    get_violation_generated_path,
    create_text_block,
    create_file_block,
    create_task_progress_block,
    create_file_title_block,
)
from utils.logger import get_logger
from utils.exceptions import BusinessException

logger = get_logger(__name__)

# 待补充标记
PLACEHOLDER_TEXT = "待补充"


def add_text_with_highlight(paragraph, text: str, font_name: str = '仿宋_GB2312', font_size: int = 16):
    """
    添加文字，如果包含"待补充"则只对"待补充"添加黄色高亮

    Args:
        paragraph: Word段落对象
        text: 要添加的文字
        font_name: 字体名称
        font_size: 字体大小

    Returns:
        run: 最后添加的run对象
    """
    if PLACEHOLDER_TEXT not in text:
        # 不包含待补充，直接添加
        run = paragraph.add_run(text)
        set_chinese_font(run, font_name, font_size)
        return run

    # 包含待补充，需要拆分
    parts = text.split(PLACEHOLDER_TEXT)
    last_run = None

    for i, part in enumerate(parts):
        if part:
            run = paragraph.add_run(part)
            set_chinese_font(run, font_name, font_size)
            last_run = run

        # 在每个部分之后添加"待补充"（除了最后一个）
        if i < len(parts) - 1:
            run = paragraph.add_run(PLACEHOLDER_TEXT)
            set_chinese_font(run, font_name, font_size)
            run.font.highlight_color = WD_COLOR_INDEX.YELLOW
            last_run = run

    return last_run


def highlight_placeholder_in_paragraph(paragraph):
    """
    遍历段落中的所有run，将包含"待补充"的run拆分，只高亮"待补充"部分

    Args:
        paragraph: Word段落对象
    """
    from copy import copy
    from docx.oxml.ns import qn

    runs = list(paragraph.runs)
    for run in runs:
        text = run.text
        if PLACEHOLDER_TEXT not in text:
            continue

        # 拆分文本
        parts = text.split(PLACEHOLDER_TEXT)
        if len(parts) <= 1:
            # 整个run就是"待补充"
            run.font.highlight_color = WD_COLOR_INDEX.YELLOW
            continue

        # 获取run的父元素和位置
        run_element = run._element
        parent = run_element.getparent()

        # 保存原始run的格式属性
        rPr = run_element.find(qn('w:rPr'))
        original_rPr = copy(rPr) if rPr is not None else None

        # 清空原run，设为第一部分
        run.text = parts[0]

        # 在原run后面依次插入新run
        insert_after = run_element

        for i, part in enumerate(parts[1:], 1):
            # 添加"待补充"（带高亮）
            from docx.oxml import OxmlElement
            new_r = OxmlElement('w:r')
            if original_rPr is not None:
                new_rPr = copy(original_rPr)
                new_r.append(new_rPr)

            new_t = OxmlElement('w:t')
            new_t.text = PLACEHOLDER_TEXT
            new_r.append(new_t)

            # 添加高亮
            highlight = OxmlElement('w:highlight')
            highlight.set(qn('w:val'), 'yellow')
            rPr_elem = new_r.find(qn('w:rPr'))
            if rPr_elem is None:
                rPr_elem = OxmlElement('w:rPr')
                new_r.insert(0, rPr_elem)
            rPr_elem.append(highlight)

            insert_after.addnext(new_r)
            insert_after = new_r

            # 添加后续部分（如果有）
            if part:
                rest_r = OxmlElement('w:r')
                if original_rPr is not None:
                    rest_rPr = copy(original_rPr)
                    rest_r.append(rest_rPr)

                rest_t = OxmlElement('w:t')
                rest_t.text = part
                rest_r.append(rest_t)

                insert_after.addnext(rest_r)
                insert_after = rest_r


def extract_zgtzs_data(result: ViolationsAnalysisResult) -> dict:
    """
    从标准数据结构提取整改通知书所需数据（纯函数）

    Args:
        result: 标准化的违规分析结果

    Returns:
        包含基本信息和违法行为内容的字典
    """
    violations_content = []

    # 提取每个违法行为的数据
    for idx, violation in enumerate(result.violations, start=1):
        # 违反条款格式化
        legal_basis_parts = []
        for provision in violation.violated_provisions:
            law_name = provision.law_name
            if not (law_name.startswith('《') and law_name.endswith('》')):
                law_name = f"《{law_name}》"
            legal_basis_parts.append(f"{law_name}{provision.law_item_name}")

        legal_basis_text = "、".join(legal_basis_parts) if legal_basis_parts else ""

        # 构造违法行为描述
        if legal_basis_text:
            legal_basis_desc = f"上述行为违反了{legal_basis_text}的规定。"
        else:
            legal_basis_desc = ""

        violations_content.append({
            "title": violation.title,
            "facts": violation.facts,
            "legal_basis": legal_basis_desc
        })

    # 收集所有整改措施（去重）
    all_measures_set = set()
    all_measures = []
    for violation in result.violations:
        for measure in violation.rectification_measures:
            if measure not in all_measures_set:
                all_measures_set.add(measure)
                all_measures.append(measure)

    return {
        "inspected_person": result.inspected_person or "待补充",
        "inspected_address": result.inspected_address or "待补充",
        "legal_representative": result.legal_representative or "待补充",
        "inspection_start_date": result.inspection_start_date or "待补充",
        "inspection_end_date": result.inspection_end_date or "待补充",
        "business_start_date": result.business_start_date or "待补充",
        "business_end_date": result.business_end_date or "待补充",
        "violations_content": violations_content,
        "rectification_measures": all_measures
    }


def generate_zgtzs_document(state: dict, config: RunnableConfig, tasks: list) -> dict:
    """
    生成整改通知书 Word 文档

    Args:
        state: 图状态
        config: 运行时配置
        tasks: 任务列表（用于进度显示）

    Returns:
        包含 AIMessage 的字典
    """
    logger.info("生成整改通知书...")

    thread_id = config.get("configurable", {}).get("thread_id")

    task_mgr = TaskManager(tasks)
    task_mgr.set_in_progress('generate')

    # 从标准数据结构获取数据 ⭐
    violations_analysis_result = state.get("violations_analysis_result")
    if not violations_analysis_result:
        raise BusinessException(msg="未找到违规分析结果，请先生成检查报告或进行事实认定分析", code=40060)

    # 提取整改通知书所需数据
    report_data = extract_zgtzs_data(violations_analysis_result)

    try:
        inspected_person = report_data.get("inspected_person", "待补充")
        inspected_address = report_data.get("inspected_address", "待补充")
        legal_representative = report_data.get("legal_representative", "待补充")
        inspection_start_date = report_data.get("inspection_start_date", "待补充")
        inspection_end_date = report_data.get("inspection_end_date", "待补充")
        business_start_date = report_data.get("business_start_date", "待补充")
        business_end_date = report_data.get("business_end_date", "待补充")
        violations_content = report_data.get("violations_content", [])
        rectification_measures = report_data.get("rectification_measures", [])

        # 加载模板文件
        template_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "report_template",
            "整改通知书.docx"
        )

        if not os.path.exists(template_path):
            raise BusinessException(msg=f"模板文件不存在: {template_path}", code=50041)

        logger.info(f"加载模板: {template_path}")
        doc = Document(template_path)

        # 获取当前年份
        current_year = datetime.now().year

        # 替换模板中的占位符
        for para in doc.paragraphs:
            # 替换文书编号中的年份（2022 -> 当前年份）
            if "〔2022〕" in para.text:
                for run in para.runs:
                    if "〔2022〕" in run.text:
                        run.text = run.text.replace("〔2022〕", f"〔{current_year}〕")
            if "待补充银行股份有限公司" in para.text:
                for run in para.runs:
                    if "待补充银行股份有限公司" in run.text:
                        run.text = run.text.replace("待补充银行股份有限公司", inspected_person)
            if "地    址：" in para.text and para.text.strip() == "地    址：":
                # 在地址后添加内容（待补充时高亮）
                add_text_with_highlight(para, inspected_address)
            if "法定代表人：" in para.text and para.text.strip() == "法定代表人：":
                # 在法定代表人后添加内容（待补充时高亮）
                add_text_with_highlight(para, legal_representative)
            # 替换检查时间和业务时间
            for run in para.runs:
                text = run.text
                # 检查时间替换（模板中是 2021年待补充月待补充日至2021年待补充月待补充日）
                if "2021年待补充月待补充日至2021年待补充月待补充日" in text:
                    new_text = f"{inspection_start_date}至{inspection_end_date}"
                    run.text = text.replace(
                        "2021年待补充月待补充日至2021年待补充月待补充日",
                        new_text
                    )
                    # 如果包含待补充，添加高亮
                    if PLACEHOLDER_TEXT in new_text:
                        run.font.highlight_color = WD_COLOR_INDEX.YELLOW
                # 业务时间替换（模板中是 2020年待补充月待补充日至2020年待补充月待补充日）
                if "2020年待补充月待补充日至2020年待补充月待补充日" in text:
                    new_text = f"{business_start_date}至{business_end_date}"
                    run.text = text.replace(
                        "2020年待补充月待补充日至2020年待补充月待补充日",
                        new_text
                    )
                    # 如果包含待补充，添加高亮
                    if PLACEHOLDER_TEXT in new_text:
                        run.font.highlight_color = WD_COLOR_INDEX.YELLOW

        # 查找"违反支付结算管理规定"段落的索引
        target_index = -1
        for i, para in enumerate(doc.paragraphs):
            if "违反支付结算管理规定" in para.text:
                target_index = i
                logger.info(f"找到目标段落索引: {i}, 内容: {para.text}")
                break

        if target_index == -1:
            raise BusinessException(msg="未找到'违反支付结算管理规定'段落", code=50042)

        # 找到占位符段落并替换内容
        if target_index + 1 < len(doc.paragraphs):
            placeholder_para = doc.paragraphs[target_index + 1]
            placeholder_para.clear()

            # 第一个段落：用于第一个违法行为
            first_violation = True
            insert_position = placeholder_para._element

            # 按照新格式添加每个违法行为
            chinese_nums = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]

            for idx, vio_content in enumerate(violations_content):
                if first_violation:
                    # 第一个违法行为：使用占位符段落
                    run = placeholder_para.add_run(f"（{chinese_nums[idx]}）{vio_content['title']}")
                    set_chinese_font(run, '仿宋_GB2312', 16)
                    first_violation = False
                else:
                    # 后续违法行为：插入新段落
                    new_para = doc.add_paragraph()
                    run = new_para.add_run(f"（{chinese_nums[idx]}）{vio_content['title']}")
                    set_chinese_font(run, '仿宋_GB2312', 16)
                    insert_position.addnext(new_para._element)
                    insert_position = new_para._element

                # 插入事实内容
                new_para = doc.add_paragraph()
                run = new_para.add_run(vio_content['facts'])
                set_chinese_font(run, '仿宋_GB2312', 16)
                insert_position.addnext(new_para._element)
                insert_position = new_para._element

                # 插入违法行为描述
                if vio_content['legal_basis']:
                    new_para = doc.add_paragraph()
                    run = new_para.add_run(vio_content['legal_basis'])
                    set_chinese_font(run, '仿宋_GB2312', 16)
                    insert_position.addnext(new_para._element)
                    insert_position = new_para._element

                # 违法行为之间空行
                if idx < len(violations_content) - 1:
                    new_para = doc.add_paragraph()
                    insert_position.addnext(new_para._element)
                    insert_position = new_para._element

            # 空行分隔
            new_para = doc.add_paragraph()
            insert_position.addnext(new_para._element)
            insert_position = new_para._element

            # 插入整改措施标题
            new_para = doc.add_paragraph()
            run = new_para.add_run(f"对此行为，{inspected_person}应当采取以下整改措施：")
            set_chinese_font(run, '仿宋_GB2312', 16)
            insert_position.addnext(new_para._element)
            insert_position = new_para._element

            # 插入整改措施（逐条）
            for idx, measure in enumerate(rectification_measures, start=1):
                new_para = doc.add_paragraph()
                run = new_para.add_run(f"{idx}. {measure}")
                set_chinese_font(run, '仿宋_GB2312', 16)
                insert_position.addnext(new_para._element)
                insert_position = new_para._element

        # 最终扫描：只给"待补充"文字添加黄色高亮（拆分run，不影响其他文字）
        for para in doc.paragraphs:
            highlight_placeholder_in_paragraph(para)

        # 保存文档
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"责令整改通知书_{timestamp}.docx"
        file_path = get_violation_generated_path(thread_id, filename)
        doc.save(file_path)

        logger.info(f"整改通知书生成成功: {file_path}")
        task_mgr.set_completed('generate')

        # 统计待补充字段
        pending_fields = []
        if inspected_address == PLACEHOLDER_TEXT:
            pending_fields.append("地址")
        if legal_representative == PLACEHOLDER_TEXT:
            pending_fields.append("法定代表人")
        if inspection_start_date == PLACEHOLDER_TEXT or inspection_end_date == PLACEHOLDER_TEXT:
            pending_fields.append("检查时间")
        if business_start_date == PLACEHOLDER_TEXT or business_end_date == PLACEHOLDER_TEXT:
            pending_fields.append("业务时间")

        # 构建返回消息
        result_text = f"✅ 整改通知书生成完成\n\n文档名称：{filename}"
        if pending_fields:
            result_text += f"\n\n⚠️ 以下字段需要手动补充（已用黄色高亮标记）：\n- " + "\n- ".join(pending_fields)

        content_blocks = [
            create_text_block(result_text),
            create_file_block(
                file_id=filename,
                mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                url=f"/api/download/{thread_id}/{filename}",
            ),
            create_task_progress_block([{**task, "status": "completed"} for task in tasks]),
            create_file_title_block("整改通知书"),
        ]

        return {
            "messages": [AIMessage(content=content_blocks)]
        }

    except Exception as e:
        error_msg = f"整改通知书生成失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise BusinessException(msg=error_msg, code=50043)
