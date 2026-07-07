"""
文件上传服务核心逻辑
"""

import os
import uuid
import time
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any
from fastapi import UploadFile

from sqlalchemy import select, delete as sa_delete, update as sa_update

from utils.logger import get_logger
from utils.database import get_session
from utils.models import UploadedFile

logger = get_logger(__name__)

# 上传文件根目录
UPLOADS_ROOT = Path(__file__).resolve().parents[1] / "uploads"


def _get_storage_dir(thread_id: str, file_id: str) -> str:
    """
    生成存储目录路径（相对于 uploads 根目录）
    格式: {日期}/{thread_id}/{file_id}/
    """
    date_str = time.strftime('%Y-%m-%d')
    return f"{date_str}/{thread_id}/{file_id}"


def _get_file_extension(filename: str) -> str:
    """获取文件扩展名（小写，不含点）"""
    ext = Path(filename).suffix.lower()
    return ext[1:] if ext else ""


async def upload_file(
    file: UploadFile,
    thread_id: str
) -> Dict[str, Any]:
    """
    上传文件并触发异步解析

    Args:
        file: 上传的文件
        thread_id: 会话ID

    Returns:
        {
            "file_id": "xxx",
            "status": "parsing",
            "original_filename": "xxx.pdf",
            "file_type": "pdf"
        }
    """
    file_id = str(uuid.uuid4())
    original_filename = file.filename or "unknown"
    file_type = _get_file_extension(original_filename)

    # 生成存储路径
    storage_dir = _get_storage_dir(thread_id, file_id)
    full_dir = UPLOADS_ROOT / storage_dir
    full_dir.mkdir(parents=True, exist_ok=True)

    # 保存原始文件
    original_path = full_dir / f"original.{file_type}"
    content = await file.read()
    file_size = len(content)

    with open(original_path, "wb") as f:
        f.write(content)

    # 写入数据库
    now = datetime.now()
    async with get_session() as session:
        uploaded_file = UploadedFile(
            file_id=file_id,
            thread_id=thread_id,
            original_filename=original_filename,
            file_type=file_type,
            file_size=file_size,
            storage_dir=storage_dir,
            parse_status="pending",
            create_time=now,
        )
        session.add(uploaded_file)

    logger.info(f"文件上传成功: {file_id}, {original_filename}")

    # 触发异步解析
    asyncio.create_task(_parse_file_async(file_id))

    return {
        "file_id": file_id,
        "status": "parsing",
        "original_filename": original_filename,
        "file_type": file_type,
        "file_size": file_size
    }


async def _parse_file_async(file_id: str) -> None:
    """异步解析文件"""
    from .parser import parse_file
    try:
        await parse_file(file_id)
    except Exception as e:
        logger.error(f"文件解析失败: {file_id}, {e}", exc_info=True)
        # 更新状态为失败
        async with get_session() as session:
            await session.execute(
                sa_update(UploadedFile)
                .where(UploadedFile.file_id == file_id)
                .values(parse_status="failed", update_time=datetime.now())
            )


async def get_file_info(file_id: str) -> Optional[Dict[str, Any]]:
    """
    获取文件信息

    Args:
        file_id: 文件ID

    Returns:
        文件信息字典，不存在返回None
    """
    async with get_session() as session:
        result = await session.execute(
            select(UploadedFile).where(UploadedFile.file_id == file_id)
        )
        row = result.scalar_one_or_none()

    if not row:
        return None

    return {
        "file_id": row.file_id,
        "thread_id": row.thread_id,
        "original_filename": row.original_filename,
        "file_type": row.file_type,
        "file_size": row.file_size,
        "storage_dir": row.storage_dir,
        "status": row.parse_status,
        "parse_filename": row.parse_filename,
        "create_time": row.create_time.strftime('%Y-%m-%d %H:%M:%S') if row.create_time else ""
    }


async def delete_file(file_id: str) -> bool:
    """
    删除文件

    Args:
        file_id: 文件ID

    Returns:
        是否成功
    """
    # 获取文件信息
    info = await get_file_info(file_id)
    if not info:
        return False

    # 删除文件目录
    storage_dir = UPLOADS_ROOT / info["storage_dir"]
    if storage_dir.exists():
        import shutil
        shutil.rmtree(storage_dir)

    # 删除数据库记录
    async with get_session() as session:
        await session.execute(
            sa_delete(UploadedFile).where(UploadedFile.file_id == file_id)
        )

    logger.info(f"文件删除成功: {file_id}")
    return True


async def get_file_content(file_id: str) -> Optional[bytes]:
    """
    获取原始文件内容

    Args:
        file_id: 文件ID

    Returns:
        文件二进制内容，不存在返回None
    """
    info = await get_file_info(file_id)
    if not info:
        return None

    file_path = UPLOADS_ROOT / info["storage_dir"] / f"original.{info['file_type']}"
    if not file_path.exists():
        return None

    with open(file_path, "rb") as f:
        return f.read()


async def get_parsed_content(file_id: str) -> Optional[str]:
    """
    获取解析后的内容（Markdown）

    Args:
        file_id: 文件ID

    Returns:
        解析后的Markdown内容，未解析或不存在返回None
    """
    info = await get_file_info(file_id)
    if not info:
        return None

    if info["status"] != "completed" or not info["parse_filename"]:
        return None

    parsed_path = UPLOADS_ROOT / info["storage_dir"] / info["parse_filename"]
    if not parsed_path.exists():
        return None

    with open(parsed_path, "r", encoding="utf-8") as f:
        return f.read()


async def list_files_by_thread(thread_id: str) -> list:
    """
    获取会话下的所有文件（简要信息，用于API返回）

    Args:
        thread_id: 会话ID

    Returns:
        文件列表
    """
    async with get_session() as session:
        result = await session.execute(
            select(UploadedFile)
            .where(UploadedFile.thread_id == thread_id)
            .order_by(UploadedFile.create_time.desc())
        )
        rows = result.scalars().all()

    return [{
        "file_id": row.file_id,
        "original_filename": row.original_filename,
        "file_type": row.file_type,
        "file_size": row.file_size,
        "status": row.parse_status,
        "create_time": row.create_time.strftime('%Y-%m-%d %H:%M:%S') if row.create_time else ""
    } for row in rows]


async def get_files_by_thread(thread_id: str) -> list:
    """
    获取会话下的所有文件（完整信息，用于节点加载文件上下文）

    Args:
        thread_id: 会话ID

    Returns:
        文件完整信息列表
    """
    async with get_session() as session:
        result = await session.execute(
            select(UploadedFile)
            .where(UploadedFile.thread_id == thread_id)
            .order_by(UploadedFile.create_time.asc())
        )
        rows = result.scalars().all()

    return [{
        "id": row.file_id,
        "thread_id": row.thread_id,
        "original_filename": row.original_filename,
        "file_type": row.file_type,
        "file_size": row.file_size,
        "storage_dir": row.storage_dir,
        "status": row.parse_status,
        "parse_filename": row.parse_filename,
        "create_time": row.create_time.strftime('%Y-%m-%d %H:%M:%S') if row.create_time else ""
    } for row in rows]


async def get_file_path_by_id(file_id: str) -> Optional[str]:
    """
    根据文件ID获取文件的绝对路径
    """
    info = await get_file_info(file_id)
    if not info:
        return None
    
    file_path = UPLOADS_ROOT / info["storage_dir"] / f"original.{info['file_type']}"
    if not file_path.exists():
        return None
        
    return str(file_path)
