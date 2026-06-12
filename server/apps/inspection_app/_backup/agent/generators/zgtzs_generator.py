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
from langchain_core.messages import AIMessage
from langgraph.types import RunnableConfig

from ..states import GraphState, ViolationsAnalysisResult
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


def extract_zgtzs_data(result: ViolationsAnalysisResult) -> dict:
    """
    从标准数据结构提取整改通知书所需数据（纯函数）

    Args:
        result: 标准化的违规分析结果

    Returns:
        {
            "inspected_person": str,
            "violations_content": [{"title": str, "facts": str, "legal_basis": str}, ...],
            "rectification_measures": List[str]
        }
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
        "inspected_person": result.inspected_person,
        "violations_content": violations_content,
        "rectification_measures": all_measures
    }


def generate_zgtzs_document(state: GraphState, config: RunnableConfig, tasks: list) -> dict:
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
        inspected_person = report_data.get("inspected_person", "")
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

        # 保存文档
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"中国人民银行责令整改通知书_{timestamp}.docx"
        file_path = get_violation_generated_path(thread_id, filename)
        doc.save(file_path)

        logger.info(f"整改通知书生成成功: {file_path}")
        task_mgr.set_completed('generate')

        # 构建返回消息
        content_blocks = [
            create_text_block(f"✅ 整改通知书生成完成\n\n文档名称：{filename}"),
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
