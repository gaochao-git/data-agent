"""
通用文件上传服务模块

提供文件上传、解析、查询、删除等功能
"""

from .service import (
    upload_file,
    get_file_info,
    delete_file,
    get_file_content,
    get_parsed_content,
)
from .routes import router as file_router

__all__ = [
    'upload_file',
    'get_file_info',
    'delete_file',
    'get_file_content',
    'get_parsed_content',
    'file_router',
]
