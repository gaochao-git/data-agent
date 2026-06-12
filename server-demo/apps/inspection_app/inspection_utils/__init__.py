"""
执法检查工具模块

包含：
- common: 通用工具函数（聊天模型、违规拆分、消息块工厂函数等）
- document_parser: 文档解析工具（解析 docx 文件）
- excel_generator: Excel 报告生成工具
"""

from .common import (
    # 聊天模型
    get_chat_model,
    # 文件路径管理
    get_violation_upload_path,
    get_violation_generated_path,
    get_violation_uploads_dir,
    get_violation_generated_dir,
    # 字体设置
    set_chinese_font,
    # 任务
    Task,
    # 消息内容块工厂函数
    create_text_block,
    create_file_block,
    create_non_standard_block,
    create_task_progress_block,
    create_analysis_data_block,
    create_excel_data_block,
    create_file_title_block,
    create_report_source_data_block,
    create_progress_message,
)
from .document_parser import (
    get_file_type,
    parse_fact_determination_doc,
    parse_evidence_records,
)

__all__ = [
    # common - 聊天模型
    "get_chat_model",
    # common - 文件路径管理
    "get_violation_upload_path",
    "get_violation_generated_path",
    "get_violation_uploads_dir",
    "get_violation_generated_dir",
    # common - 字体设置
    "set_chinese_font",
    # common - 任务
    "Task",
    # common - 消息内容块工厂函数
    "create_text_block",
    "create_file_block",
    "create_non_standard_block",
    "create_task_progress_block",
    "create_analysis_data_block",
    "create_excel_data_block",
    "create_file_title_block",
    "create_report_source_data_block",
    "create_progress_message",
    # document_parser
    "get_file_type",
    "parse_fact_determination_doc",
    "parse_evidence_records",
]
