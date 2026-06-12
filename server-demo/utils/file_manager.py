"""
公共文件管理模块
提供文件上传、下载等通用功能
"""

import os
import uuid
import aiofiles
from typing import Optional, Dict, Any
from pathlib import Path
from urllib.parse import quote
from fastapi import UploadFile
from fastapi.responses import Response
from utils.logger import get_logger

logger = get_logger(__name__)

# MIME 类型映射
MIME_TYPES = {
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.xls': 'application/vnd.ms-excel',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.doc': 'application/msword',
    '.wps': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.pdf': 'application/pdf',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.txt': 'text/plain',
    '.csv': 'text/csv',
    '.json': 'application/json',
}


def get_upload_dir(base_dir: str, conversation_id: str) -> str:
    """
    获取上传目录路径，如果不存在则创建

    Args:
        base_dir: 基础目录
        conversation_id: 会话ID

    Returns:
        上传目录路径
    """
    upload_dir = os.path.join(base_dir, conversation_id, "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    return upload_dir


def get_file_path(base_dir: str, conversation_id: str, file_id: str) -> str:
    """
    获取文件完整路径

    Args:
        base_dir: 基础目录
        conversation_id: 会话ID
        file_id: 文件ID（包含扩展名）

    Returns:
        文件完整路径
    """
    upload_dir = get_upload_dir(base_dir, conversation_id)
    return os.path.join(upload_dir, file_id)


def validate_path(value: str) -> bool:
    """
    验证路径是否安全（防止路径遍历攻击）

    Args:
        value: 要验证的值

    Returns:
        是否安全
    """
    return '..' not in value and '/' not in value and '\\' not in value


def get_mime_type(filename: str) -> str:
    """
    根据文件名获取 MIME 类型

    Args:
        filename: 文件名

    Returns:
        MIME 类型
    """
    ext = os.path.splitext(filename)[1].lower()
    return MIME_TYPES.get(ext, 'application/octet-stream')


async def save_file(
    base_dir: str,
    conversation_id: str,
    file: UploadFile,
    allowed_extensions: list = None
) -> Dict[str, Any]:
    """
    保存上传的文件

    Args:
        base_dir: 基础目录
        conversation_id: 会话ID
        file: 上传的文件
        allowed_extensions: 允许的扩展名列表（如 ['.docx', '.pdf']）

    Returns:
        {
            "file_id": "xxx.docx",
            "file_name": "原始文件名.docx",
            "file_path": "/path/to/file",
            "file_size": 1234,
            "mime_type": "application/..."
        }

    Raises:
        ValueError: 文件名为空、扩展名不允许等
    """
    if not file.filename:
        raise ValueError("文件名为空")

    if not validate_path(conversation_id):
        raise ValueError("无效的会话ID")

    # 获取文件扩展名
    original_filename = file.filename
    ext = os.path.splitext(original_filename)[1].lower()

    # 检查扩展名
    if allowed_extensions and ext not in allowed_extensions:
        raise ValueError(f"不支持的文件格式，只允许: {', '.join(allowed_extensions)}")

    # 生成文件ID
    file_id = f"upload_{uuid.uuid4()}{ext}"

    # 获取保存路径
    file_path = get_file_path(base_dir, conversation_id, file_id)

    # 读取并保存文件
    content = await file.read()
    async with aiofiles.open(file_path, 'wb') as f:
        await f.write(content)

    logger.info(f"文件保存成功: {file_id}, 原始名: {original_filename}, 大小: {len(content)}")

    return {
        "file_id": file_id,
        "file_name": original_filename,
        "file_path": file_path,
        "file_size": len(content),
        "mime_type": get_mime_type(original_filename)
    }


async def read_file(
    base_dir: str,
    conversation_id: str,
    file_id: str,
    preview: bool = False
) -> Response:
    """
    读取文件并返回响应

    Args:
        base_dir: 基础目录
        conversation_id: 会话ID
        file_id: 文件ID
        preview: 是否预览模式

    Returns:
        FastAPI Response

    Raises:
        ValueError: 路径不安全
        FileNotFoundError: 文件不存在
    """
    if not validate_path(conversation_id):
        raise ValueError("无效的会话ID")

    if not validate_path(file_id):
        raise ValueError("无效的文件ID")

    file_path = get_file_path(base_dir, conversation_id, file_id)

    if not os.path.exists(file_path):
        raise FileNotFoundError(f"文件不存在: {file_id}")

    # 读取文件
    async with aiofiles.open(file_path, 'rb') as f:
        content = await f.read()

    # 获取 MIME 类型
    mime_type = get_mime_type(file_id)

    # 设置响应头
    encoded_filename = quote(file_id)
    if preview:
        disposition = f"inline; filename*=UTF-8''{encoded_filename}"
    else:
        disposition = f"attachment; filename*=UTF-8''{encoded_filename}"

    logger.info(f"读取文件: {file_id}, 大小: {len(content)}, 预览: {preview}")

    return Response(
        content=content,
        media_type=mime_type,
        headers={'Content-Disposition': disposition}
    )


async def delete_file(base_dir: str, conversation_id: str, file_id: str) -> bool:
    """
    删除文件

    Args:
        base_dir: 基础目录
        conversation_id: 会话ID
        file_id: 文件ID

    Returns:
        是否删除成功
    """
    if not validate_path(conversation_id) or not validate_path(file_id):
        return False

    file_path = get_file_path(base_dir, conversation_id, file_id)

    if os.path.exists(file_path):
        os.remove(file_path)
        logger.info(f"删除文件: {file_id}")
        return True

    return False


def list_files(base_dir: str, conversation_id: str) -> list:
    """
    列出会话的所有文件

    Args:
        base_dir: 基础目录
        conversation_id: 会话ID

    Returns:
        文件列表 [{"file_id": "...", "file_size": 123}, ...]
    """
    if not validate_path(conversation_id):
        return []

    upload_dir = os.path.join(base_dir, conversation_id, "uploads")
    if not os.path.exists(upload_dir):
        return []

    files = []
    for filename in os.listdir(upload_dir):
        file_path = os.path.join(upload_dir, filename)
        if os.path.isfile(file_path):
            files.append({
                "file_id": filename,
                "file_size": os.path.getsize(file_path),
                "mime_type": get_mime_type(filename)
            })

    return files
