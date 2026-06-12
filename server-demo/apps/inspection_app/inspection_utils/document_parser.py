"""
文档解析工具
提供统一的文档解析功能，用于解析事实认定书和取证记录表
同时提供文件类型识别和映射功能
"""

import os
import tempfile
from docx import Document
from typing import Dict, List, Optional
from utils.logger import get_logger
from utils.exceptions import BusinessException

logger = get_logger(__name__)


# MIME type 到简化类型的映射
MIME_TYPE_MAP = {
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'DOCX',
    'application/msword': 'DOC',
    'application/pdf': 'PDF',
    'text/plain': 'TXT'
}

# 扩展名到简化类型的映射
EXT_TYPE_MAP = {
    '.docx': 'DOCX',
    '.doc': 'DOC',
    '.pdf': 'PDF',
    '.txt': 'TXT'
}


def get_file_type(mime_type: Optional[str] = None, extension: Optional[str] = None) -> str:
    """
    根据 MIME type 或文件扩展名获取简化的文件类型

    Args:
        mime_type: MIME 类型，如 'application/pdf'
        extension: 文件扩展名，如 '.pdf' 或 'pdf'

    Returns:
        简化的文件类型，如 'PDF', 'DOCX' 等
    """
    # 优先使用 MIME type 映射
    if mime_type and mime_type in MIME_TYPE_MAP:
        return MIME_TYPE_MAP[mime_type]

    # 使用扩展名映射
    if extension:
        # 确保扩展名以点开头
        if not extension.startswith('.'):
            extension = f'.{extension}'
        extension = extension.lower()

        if extension in EXT_TYPE_MAP:
            return EXT_TYPE_MAP[extension]

        # 如果映射表中没有，返回扩展名的大写形式（去掉点号）
        return extension.upper().replace('.', '')

    # 都没有则返回默认值
    return 'FILE'


def parse_fact_determination_doc(file_content: bytes) -> Dict[str, str]:
    """
    解析事实认定书，提取检查内容、事实认定、被检查人和检查时间

    Args:
        file_content: 文件二进制内容

    Returns:
        {
            "inspection_content": str,  # 检查内容
            "violation_facts": str,      # 事实认定
            "inspected_person": str,     # 被检查人
            "inspection_time": str       # 检查时间
        }
    """
    try:
        # 保存到临时文件
        with tempfile.NamedTemporaryFile(delete=False, suffix='.docx') as temp_file:
            temp_file.write(file_content)
            temp_path = temp_file.name

        # 读取Word文档
        doc = Document(temp_path)

        inspection_content = ""
        violation_facts = ""
        inspected_person = ""
        inspection_time = ""

        # 方法1: 从表格中提取
        if doc.tables:
            for table_idx, table in enumerate(doc.tables):
                logger.info(f"解析表格 {table_idx + 1}，共 {len(table.rows)} 行")
                for row_idx, row in enumerate(table.rows):
                    cells = row.cells
                    if len(cells) >= 2:
                        # 打印当前行所有单元格内容用于调试
                        all_cells_text = [f"[{i}]:{cell.text.strip()}" for i, cell in enumerate(cells)]
                        logger.debug(f"  行 {row_idx}: {' | '.join(all_cells_text)}")

                        # 遍历每个单元格，寻找标签
                        for cell_idx in range(len(cells) - 1):
                            label = cells[cell_idx].text.strip()
                            label_normalized = label.replace('\n', '').replace(' ', '').replace('\t', '')

                            # 查找下一个单元格作为值
                            value = ""
                            for next_idx in range(cell_idx + 1, len(cells)):
                                cell_text = cells[next_idx].text.strip()
                                cell_normalized = cell_text.replace('\n', '').replace(' ', '').replace('\t', '')
                                # 找到第一个不等于标签的非空值
                                if cell_text and cell_normalized != label_normalized:
                                    value = cell_text
                                    break

                            # 匹配"被检查人"
                            if '被检查人' in label_normalized and value and not inspected_person:
                                inspected_person = value
                                logger.info(f"  ✓ 提取被检查人: {value}")

                            # 匹配"检查时间"
                            elif ('检查时间' in label_normalized or '检查期间' in label_normalized) and value and not inspection_time:
                                inspection_time = value
                                logger.info(f"  ✓ 提取检查时间: {value}")

                            # 匹配"检查内容"
                            elif '检查内容' in label_normalized and value and not inspection_content:
                                # 排除"检查时间"等其他标签被误取为值
                                if '检查时间' not in value and '检查期间' not in value:
                                    inspection_content = value
                                    logger.info(f"  ✓ 提取检查内容: {value[:30]}...")

                            # 匹配"事实认定"
                            elif '事实认定' in label_normalized and value and not violation_facts:
                                violation_facts = value
                                logger.info(f"  ✓ 提取事实认定: {len(value)} 字符")

        # 方法2: 如果表格提取失败，从段落提取
        if not inspection_content and not violation_facts:
            all_text = '\n'.join([p.text for p in doc.paragraphs if p.text.strip()])
            lines = all_text.split('\n')
            current_field = None

            for line in lines:
                line = line.strip()
                if not line:
                    continue

                has_colon = ':' in line or '：' in line

                if '检查内容' in line and has_colon:
                    current_field = 'inspection'
                    parts = line.split('：', 1) if '：' in line else line.split(':', 1)
                    if len(parts) > 1 and parts[1].strip():
                        inspection_content += parts[1].strip() + '\n'
                elif '事实认定' in line and has_colon:
                    current_field = 'facts'
                    parts = line.split('：', 1) if '：' in line else line.split(':', 1)
                    if len(parts) > 1 and parts[1].strip():
                        violation_facts += parts[1].strip() + '\n'
                elif current_field == 'inspection':
                    inspection_content += line + '\n'
                elif current_field == 'facts':
                    violation_facts += line + '\n'

        # 清理临时文件
        os.unlink(temp_path)

        logger.info(f"事实认定书解析完成 - 被检查人: {inspected_person}, 检查时间: {inspection_time}, "
                   f"检查内容: {len(inspection_content)} 字符, 事实认定: {len(violation_facts)} 字符")

        return {
            "inspection_content": inspection_content.strip(),
            "violation_facts": violation_facts.strip(),
            "inspected_person": inspected_person.strip(),
            "inspection_time": inspection_time.strip()
        }

    except Exception as e:
        error_msg = f"事实认定书解析失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise BusinessException(msg=error_msg, code=50001)


def parse_evidence_records(file_content: bytes) -> Dict[str, any]:
    """
    解析取证记录表，提取基本信息、取证过程和证据清单

    Args:
        file_content: 文件二进制内容

    Returns:
        {
            "basic_info": {
                "inspected_unit": str,          # 被检查单位
                "inspection_project": str,      # 检查项目及文书编号
                "evidence_date": str,           # 取证日期
                "inspectors": str               # 检查人员
            },
            "evidence_process": str,            # 取证过程记录
            "evidence_list": [
                {
                    "evidence_name_code": str,   # 证据名称及编号
                    "evidence_source": str,       # 证据来源
                    "evidence_form": str,         # 证据表现形式
                    "evidence_truth": str,        # 证据真实性转述
                    "provider_sign": str,         # 证据提供人签字及日期
                    "remark": str                 # 备注
                },
                ...
            ]
        }
    """
    try:
        # 保存到临时文件
        with tempfile.NamedTemporaryFile(delete=False, suffix='.docx') as temp_file:
            temp_file.write(file_content)
            temp_path = temp_file.name

        # 读取Word文档
        doc = Document(temp_path)

        basic_info = {}
        evidence_process = ""
        evidence_list = []

        # 从表格中提取信息
        if doc.tables:
            # 假设只有一个主表格
            table = doc.tables[0]

            # 标识证据清单表头的行索引
            evidence_header_row = None

            for i, row in enumerate(table.rows):
                cells = row.cells

                # 跳过空行
                if not any(cell.text.strip() for cell in cells):
                    continue

                # 提取基本信息
                if len(cells) >= 2:
                    first_cell = cells[0].text.strip()
                    second_cell = cells[1].text.strip()

                    # 被检查单位
                    if '被检查单位' in first_cell:
                        basic_info['inspected_unit'] = second_cell

                    # 检查项目及文书编号
                    elif '检查项目' in first_cell and '文书编号' in first_cell:
                        basic_info['inspection_project'] = second_cell
                        # 如果有取证日期在同一行
                        if len(cells) >= 4 and '取证日期' in cells[2].text.strip():
                            basic_info['evidence_date'] = cells[3].text.strip()

                    # 取证日期（单独一行的情况）
                    elif '取证日期' in first_cell:
                        basic_info['evidence_date'] = second_cell

                    # 检查人员
                    elif '检查人员' in first_cell:
                        basic_info['inspectors'] = second_cell

                    # 取证过程记录
                    elif '取证过程记录' in first_cell:
                        evidence_process = second_cell

                    # 识别证据清单表头
                    elif '证据名称' in first_cell and '编号' in first_cell:
                        evidence_header_row = i
                        logger.info(f"发现证据清单表头在第 {i} 行")

            # 提取证据清单数据
            if evidence_header_row is not None:
                # 从表头的下一行开始提取数据
                for i in range(evidence_header_row + 1, len(table.rows)):
                    row = table.rows[i]
                    cells = row.cells

                    # 确保有足够的列
                    if len(cells) >= 6:
                        # 提取各列数据
                        evidence_name_code = cells[0].text.strip()
                        evidence_source = cells[1].text.strip()
                        evidence_form = cells[2].text.strip()
                        evidence_truth = cells[3].text.strip()
                        provider_sign = cells[4].text.strip()
                        remark = cells[5].text.strip()

                        # 过滤空行（证据名称为空的行）
                        if evidence_name_code:
                            evidence_list.append({
                                "evidence_name_code": evidence_name_code,
                                "evidence_source": evidence_source,
                                "evidence_form": evidence_form,
                                "evidence_truth": evidence_truth,
                                "provider_sign": provider_sign,
                                "remark": remark
                            })

        # 清理临时文件
        os.unlink(temp_path)

        logger.info(f"取证记录表解析完成 - 提取基本信息: {basic_info}, 证据记录: {len(evidence_list)} 条")

        return {
            "basic_info": basic_info,
            "evidence_process": evidence_process,
            "evidence_list": evidence_list
        }

    except Exception as e:
        error_msg = f"取证记录表解析失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise BusinessException(msg=error_msg, code=50002)
