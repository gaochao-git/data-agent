"""
Excel报告生成工具
用于生成证据目录等Excel格式的报告
"""

import os
import tempfile
from datetime import datetime
import openpyxl
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from utils.logger import get_logger

logger = get_logger(__name__)

# 证据目录模板配置
CATALOG_START_ROW = 4  # 证据目录起始行（第3行是表头）
APPENDIX_START_ROW = 5  # 证据目录附件起始行（第4行是表头）

# 信息简表模板配置
INFO_SUMMARY_START_ROW = 6  # 信息简表起始行


def generate_evidence_catalog_excel(catalog_data: list, appendix_data: list, basic_info: dict = None, output_dir: str = None) -> str:
    """
    生成证据目录Excel文件

    Args:
        catalog_data: 证据目录数据列表
        appendix_data: 证据目录附件数据列表
        basic_info: 基本信息(被检查单位、检查项目等)
        output_dir: 输出目录（可选，默认使用临时目录）

    Returns:
        生成的Excel文件路径
    """
    try:
        # 加载模板（模板在上级目录的 report_template 中）
        template_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            'report_template',
            '证据目录模板.xlsx'
        )

        wb = load_workbook(template_path)

        # 辅助函数：安全设置单元格值(处理合并单元格)
        def safe_set_cell_value(sheet, row, col, value):
            """安全设置单元格值，跳过合并单元格"""
            try:
                cell = sheet.cell(row=row, column=col)
                # 检查是否是合并单元格
                if isinstance(cell, openpyxl.cell.cell.MergedCell):
                    logger.debug(f"跳过合并单元格 ({row}, {col})")
                    return
                cell.value = value
            except AttributeError:
                logger.debug(f"跳过合并单元格 ({row}, {col})")
            except Exception as e:
                logger.warning(f"设置单元格 ({row}, {col}) 值时出错: {str(e)}")

        def safe_set_cell_alignment(sheet, row, col, alignment):
            """安全设置单元格对齐方式"""
            try:
                cell = sheet.cell(row=row, column=col)
                if not isinstance(cell, openpyxl.cell.cell.MergedCell):
                    cell.alignment = alignment
            except:
                pass

        # ========== Sheet 1: 证据目录 ==========
        sheet1 = wb['证据目录']

        # 更新标题信息
        current_year = datetime.now().year
        if basic_info:
            inspected_person = basic_info.get('inspected_person', '待补充')
            inspection_group = basic_info.get('inspection_group', '待补充')
        else:
            inspected_person = '待补充'
            inspection_group = '待补充'

        # 第1行标题：替换 ××银行 为实际被检查人
        row1_content = f"中国人民银行执法检查证据目录\n（{inspected_person}综合执法检查案）"
        safe_set_cell_value(sheet1, 1, 1, row1_content)

        # 第2行：检查组、被检查人、编号
        row2_content = f"检查组：{inspection_group}                                                  被检查人：{inspected_person}                                                                                 编号：银检通字〔{current_year}〕    号"
        safe_set_cell_value(sheet1, 2, 1, row2_content)

        # 填充证据目录数据
        for idx, row_data in enumerate(catalog_data):
            current_row = CATALOG_START_ROW + idx

            # 填充9列数据
            safe_set_cell_value(sheet1, current_row, 1, row_data.get('sequence_number', ''))
            safe_set_cell_value(sheet1, current_row, 2, row_data.get('violation_behavior', ''))
            safe_set_cell_value(sheet1, current_row, 3, row_data.get('evidence_code', ''))
            safe_set_cell_value(sheet1, current_row, 4, row_data.get('evidence_name', ''))
            safe_set_cell_value(sheet1, current_row, 5, row_data.get('main_content', ''))
            safe_set_cell_value(sheet1, current_row, 6, row_data.get('summary', ''))
            safe_set_cell_value(sheet1, current_row, 7, row_data.get('legal_regulation', ''))
            safe_set_cell_value(sheet1, current_row, 8, row_data.get('suggestion', ''))
            safe_set_cell_value(sheet1, current_row, 9, row_data.get('remark', ''))

            # 设置单元格对齐方式(左对齐，顶部对齐，自动换行)
            for col in range(1, 10):
                safe_set_cell_alignment(sheet1, current_row, col, Alignment(horizontal='left', vertical='top', wrap_text=True))

        # ========== Sheet 2: 证据目录附件 ==========
        sheet2 = wb['证据目录附件']

        # 更新标题信息（与 Sheet 1 保持一致）
        row1_content_sheet2 = f"中国人民银行执法检查证据目录附件\n（{inspected_person}综合执法检查案）"
        safe_set_cell_value(sheet2, 1, 1, row1_content_sheet2)

        # 第3行：检查组、被检查人、编号（证据目录附件的信息在第3行）
        safe_set_cell_value(sheet2, 3, 1, row2_content)
        # 设置第3行加粗
        cell = sheet2.cell(row=3, column=1)
        if not isinstance(cell, openpyxl.cell.cell.MergedCell):
            cell.font = Font(bold=True)

        # 1. 取消模板预设的合并单元格（A5:A9），后续根据实际数据重新合并
        for merged_range in list(sheet2.merged_cells.ranges):
            if merged_range.min_row >= APPENDIX_START_ROW:
                try:
                    sheet2.unmerge_cells(str(merged_range))
                except Exception:
                    pass

        # 2. 获取模板第5行的样式
        template_font = sheet2.cell(row=APPENDIX_START_ROW, column=1).font.copy()
        template_border = sheet2.cell(row=APPENDIX_START_ROW, column=2).border.copy()

        # 3. 填充证据目录附件数据
        for idx, row_data in enumerate(appendix_data):
            current_row = APPENDIX_START_ROW + idx

            # 填充5列数据
            safe_set_cell_value(sheet2, current_row, 1, row_data.get('specialty', ''))
            safe_set_cell_value(sheet2, current_row, 2, row_data.get('problem_number', ''))
            safe_set_cell_value(sheet2, current_row, 3, row_data.get('violation_behavior', ''))
            safe_set_cell_value(sheet2, current_row, 4, row_data.get('legal_regulation', ''))
            safe_set_cell_value(sheet2, current_row, 5, row_data.get('law_original_text', ''))

            # 应用统一样式
            for col in range(1, 6):
                try:
                    cell = sheet2.cell(row=current_row, column=col)
                    if not isinstance(cell, openpyxl.cell.cell.MergedCell):
                        cell.font = template_font
                        cell.border = template_border
                        cell.alignment = Alignment(horizontal='left', vertical='top', wrap_text=True)
                except Exception:
                    pass

        # 4. 合并检查专业列（所有相同值的行合并）
        if len(appendix_data) > 1:
            first_row = APPENDIX_START_ROW
            last_row = APPENDIX_START_ROW + len(appendix_data) - 1
            specialty_value = appendix_data[0].get('specialty', '')
            try:
                sheet2.merge_cells(start_row=first_row, start_column=1, end_row=last_row, end_column=1)
                merged_cell = sheet2.cell(row=first_row, column=1)
                merged_cell.value = specialty_value
                merged_cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            except Exception as e:
                logger.warning(f"合并检查专业列失败: {e}")

        # 5. 清理模板多余的空行（模板预设5行，数据可能少于5行）
        data_end_row = APPENDIX_START_ROW + len(appendix_data) - 1
        template_end_row = 9  # 模板预设的最后一行
        from openpyxl.styles import Border
        no_border = Border()
        for row in range(data_end_row + 1, template_end_row + 1):
            for col in range(1, 6):
                try:
                    cell = sheet2.cell(row=row, column=col)
                    cell.value = None
                    cell.border = no_border
                except Exception:
                    pass

        # 修复证据目录附件的视图缩放比例（模板中是55%，改为100%）
        sheet2.sheet_view.zoomScale = 100

        # 设置"法条原文"列（E列）宽度为160（足够显示法条内容）
        sheet2.column_dimensions['E'].width = 160
        logger.debug("证据目录附件 E列（法条原文）宽度设置为: 160，缩放比例设置为: 100%")

        # 保存文件
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            save_dir = output_dir
        else:
            save_dir = tempfile.gettempdir()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = f"证据目录_{timestamp}.xlsx"
        output_path = os.path.join(save_dir, output_filename)

        wb.save(output_path)
        wb.close()

        logger.info(f"证据目录Excel文件生成成功: {output_path}")
        return output_path

    except Exception as e:
        logger.error(f"证据目录Excel文件生成失败: {str(e)}", exc_info=True)
        raise


def generate_info_summary_excel(violations_data: list, basic_info: dict = None, output_dir: str = None) -> str:
    """
    生成信息简表Excel文件

    Args:
        violations_data: 违规行为数据列表，每个元素包含：
            - sequence_number: 序号
            - general: 一般(可选)
            - special: 二般(可选)
            - business_type: 业务类型(可选)
            - violation_facts: 违法事实(必填)
            - handling_opinion: 处理意见(必填)
            - fine_amount: 罚款金额(可选)
            - related_decision_no_1/2: 关联决定书编号(可选)
            - check_notice_no_1/2: 检查通知书编号(可选)
            - comprehensive_penalty: 综合类/处罚(可选)
            - comprehensive_handling: 综合类/处理(可选)
            - main_responsible_person: 主要责任人(可选)
            - comprehensive_responsibility: 综合类(可选)
            - main_responsibility: 主要责任(可选)
        basic_info: 基本信息(检查案号、检查单位等)，包含：
            - check_case_no: 检查案号(可选)
            - case_name: 案件名称(可选)
            - description: 检查说明(可选)
        output_dir: 输出目录（可选，默认使用临时目录）

    Returns:
        生成的Excel文件路径
    """
    try:
        # 验证输入
        if not violations_data:
            raise ValueError("违规数据列表不能为空")

        # 加载模板（模板在上级目录的 report_template 中）
        template_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            'report_template',
            '信息简表模板.xlsx'
        )

        if not os.path.exists(template_path):
            raise FileNotFoundError(f"模板文件不存在: {template_path}")

        wb = load_workbook(template_path)
        ws = wb['sheet1']  # 实际模板中的Sheet名称为sheet1

        # 辅助函数：安全设置单元格值(处理合并单元格) - 先定义
        def safe_set_cell_value(row, col, value):
            """安全设置单元格值，跳过合并单元格"""
            try:
                cell = ws.cell(row=row, column=col)
                is_merged = isinstance(cell, openpyxl.cell.cell.MergedCell)

                # 检查是否是合并单元格
                if is_merged:
                    logger.debug(f"跳过合并单元格 ({row}, {col})")
                    return

                cell.value = value
            except AttributeError:
                # MergedCell对象无法设置值，跳过
                logger.debug(f"跳过合并单元格 ({row}, {col})")
            except Exception as e:
                logger.warning(f"设置单元格 ({row}, {col}) 值时出错: {str(e)}")

        # 辅助函数：安全设置单元格样式
        def safe_set_cell_alignment(row, col, alignment):
            """安全设置单元格对齐方式"""
            try:
                cell = ws.cell(row=row, column=col)
                if not isinstance(cell, openpyxl.cell.cell.MergedCell):
                    cell.alignment = alignment
            except:
                pass

        # 更新基本信息 (第2行、第3行) - 使用安全函数
        if basic_info:
            # 检查案号 (A2 - 第2行第1列)
            check_case_no = basic_info.get('check_case_no', '')
            if check_case_no:
                safe_set_cell_value(2, 1, f"检查案号:{check_case_no}")

            # 案件名称 (E2 - 第2行第5列，根据模板实际位置)
            case_name = basic_info.get('case_name', '')
            if case_name:
                safe_set_cell_value(2, 5, case_name)

            # 第3行：牵头指导司局和检查实施单位
            inspection_unit = basic_info.get('inspection_unit', '')
            safe_set_cell_value(3, 1, f"牵头指导司局和检查实施单位：{inspection_unit}")
        else:
            # basic_info 为空时，仍然设置第3行
            safe_set_cell_value(3, 1, "牵头指导司局和检查实施单位：")

        # 填写违法行为个数（第2行第12列）
        violation_count = len(violations_data)
        safe_set_cell_value(2, 12, f"{violation_count}个")

        # 清除表头行（第4-5行）的灰色背景，并设置第2-5行字体加粗
        no_fill = PatternFill(fill_type=None)
        bold_font = Font(bold=True)
        for row in range(2, 6):
            for col in range(1, 20):
                cell = ws.cell(row=row, column=col)
                if not isinstance(cell, openpyxl.cell.cell.MergedCell):
                    # 第4-5行清除灰色背景
                    if row >= 4:
                        cell.fill = no_fill
                    # 第2-5行设置加粗
                    cell.font = bold_font

        # 处理模板行：删除第7行（填写说明），并根据需要插入新行
        data_count = len(violations_data)
        logger.info(f"准备填充 {data_count} 条违法行为数据")

        # 删除第7行（填写说明）
        ws.delete_rows(7, 1)

        # 清空第6行的示例数据
        for col in range(1, 19):
            cell = ws.cell(6, col)
            if not isinstance(cell, openpyxl.cell.cell.MergedCell):
                cell.value = None

        # 如果有多条数据，需要在第6行后插入新行
        if data_count > 1:
            # 插入 (data_count - 1) 行，因为第6行已经存在
            ws.insert_rows(7, data_count - 1)

            # 取消所有影响数据行（第6行及以后）的合并单元格
            ranges_to_unmerge = []
            for merged_range in list(ws.merged_cells.ranges):
                if merged_range.min_row >= 6:
                    ranges_to_unmerge.append(merged_range)

            # 取消合并（使用安全方式，避免KeyError）
            for merged_range in ranges_to_unmerge:
                try:
                    ws.unmerge_cells(range_string=str(merged_range))
                except KeyError:
                    # openpyxl的bug：删除行后合并单元格引用可能失效，手动移除
                    try:
                        ws.merged_cells.ranges.remove(merged_range)
                    except Exception:
                        pass

            logger.info(f"已插入 {data_count - 1} 行并取消 {len(ranges_to_unmerge)} 个合并单元格")

            # 复制第6行的格式到新插入的行
            for new_row in range(7, 6 + data_count):
                for col in range(1, 19):
                    source_cell = ws.cell(6, col)
                    target_cell = ws.cell(new_row, col)
                    # 复制样式
                    if source_cell.has_style:
                        target_cell.font = source_cell.font.copy()
                        target_cell.border = source_cell.border.copy()
                        target_cell.fill = source_cell.fill.copy()
                        target_cell.number_format = source_cell.number_format
                        target_cell.protection = source_cell.protection.copy()
                        target_cell.alignment = source_cell.alignment.copy()

        # 填充违规数据
        for idx, violation in enumerate(violations_data):
            current_row = INFO_SUMMARY_START_ROW + idx

            # 验证必填字段
            if not violation.get('violation_facts'):
                logger.warning(f"违规数据第{idx+1}项缺少违法事实字段")

            # A列: 编号
            seq_num = violation.get('sequence_number', idx + 1)
            safe_set_cell_value(current_row, 1, seq_num)

            # B列: 业务领域
            business_area = violation.get('business_area', '四、支付结算')
            safe_set_cell_value(current_row, 2, business_area)

            # C列: 违法行为类型-一级
            level1 = violation.get('violation_type_level1', '')
            safe_set_cell_value(current_row, 3, level1)

            # D列: 违法行为类型-二级
            level2 = violation.get('violation_type_level2', '')
            safe_set_cell_value(current_row, 4, level2)

            # E列: 具体违法行为概要
            violation_facts = violation.get('violation_facts', '')
            safe_set_cell_value(current_row, 5, violation_facts)

            # F列: 违反条款
            legal_basis = violation.get('legal_basis', '')
            safe_set_cell_value(current_row, 6, legal_basis)

            # G列: 处罚条款
            penalty_clause = violation.get('penalty_clause', '')
            safe_set_cell_value(current_row, 7, penalty_clause)

            # H-K列: 裁量区间
            # H列: 计罚方式
            safe_set_cell_value(current_row, 8, violation.get('penalty_method', ''))
            # I列: 单次处罚下限
            safe_set_cell_value(current_row, 9, violation.get('penalty_min', ''))
            # J列: 单次处罚上限
            safe_set_cell_value(current_row, 10, violation.get('penalty_max', ''))
            # K列: 行为数量
            safe_set_cell_value(current_row, 11, violation.get('behavior_count', ''))

            # L-N列: 机构处理建议（暂不填写）
            # O-Q列: 个人处理建议（暂不填写）
            # R列: 备注（暂不填写）

            # 设置单元格对齐方式
            for col in range(1, 19):
                # 序号列居中，其他列左对齐，顶部对齐，自动换行
                if col == 1:
                    safe_set_cell_alignment(current_row, col, Alignment(horizontal='center', vertical='center', wrap_text=True))
                else:
                    safe_set_cell_alignment(current_row, col, Alignment(horizontal='left', vertical='top', wrap_text=True))

        # 保存文件
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            save_dir = output_dir
        else:
            save_dir = tempfile.gettempdir()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = f"信息简表_{timestamp}.xlsx"
        output_path = os.path.join(save_dir, output_filename)

        wb.save(output_path)
        wb.close()

        logger.info(f"信息简表Excel文件生成成功: {output_path}")
        logger.info(f"  - 违规行为数量: {len(violations_data)}")
        logger.info(f"  - 输出路径: {output_path}")

        return output_path

    except Exception as e:
        logger.error(f"信息简表Excel文件生成失败: {str(e)}", exc_info=True)
        raise
