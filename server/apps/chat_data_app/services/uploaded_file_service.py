"""上传文件业务服务。"""

from __future__ import annotations

import asyncio
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import UploadFile

from utils.logger import get_logger

from ..daos.uploaded_file_dao import UploadedFileDao
from .uploaded_file_parser import parse_file

logger = get_logger(__name__)

UPLOADS_ROOT = Path(__file__).resolve().parents[3] / "uploads"


def _get_storage_dir(thread_id: str, file_id: str) -> str:
    """生成相对 uploads 根目录的存储目录。"""
    return f"{time.strftime('%Y-%m-%d')}/{thread_id}/{file_id}"


def _get_file_extension(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    return ext[1:] if ext else ""


def _file_info(row: Any) -> Dict[str, Any]:
    return {
        "file_id": row.file_id,
        "thread_id": row.thread_id,
        "original_filename": row.original_filename,
        "file_type": row.file_type,
        "file_size": row.file_size,
        "storage_dir": row.storage_dir,
        "status": row.parse_status,
        "parse_filename": row.parse_filename,
        "create_time": row.create_time.strftime("%Y-%m-%d %H:%M:%S") if row.create_time else "",
    }


def _file_summary(row: Any) -> Dict[str, Any]:
    return {
        "file_id": row.file_id,
        "original_filename": row.original_filename,
        "file_type": row.file_type,
        "file_size": row.file_size,
        "status": row.parse_status,
        "create_time": row.create_time.strftime("%Y-%m-%d %H:%M:%S") if row.create_time else "",
    }


class UploadedFileService:
    """上传文件保存、解析和查询编排。"""

    def __init__(self, dao: Optional[UploadedFileDao] = None):
        self.dao = dao or UploadedFileDao()

    async def upload_file(self, file: UploadFile, thread_id: str) -> Dict[str, Any]:
        file_id = str(uuid.uuid4())
        original_filename = file.filename or "unknown"
        file_type = _get_file_extension(original_filename)
        storage_dir = _get_storage_dir(thread_id, file_id)
        full_dir = UPLOADS_ROOT / storage_dir
        full_dir.mkdir(parents=True, exist_ok=True)

        content = await file.read()
        file_size = len(content)
        (full_dir / f"original.{file_type}").write_bytes(content)

        await self.dao.create_file(
            file_id=file_id,
            thread_id=thread_id,
            original_filename=original_filename,
            file_type=file_type,
            file_size=file_size,
            storage_dir=storage_dir,
            now=datetime.now(),
        )
        logger.info("文件上传成功: %s, %s", file_id, original_filename)
        asyncio.create_task(self.parse_file_async(file_id))
        return {
            "file_id": file_id,
            "status": "parsing",
            "original_filename": original_filename,
            "file_type": file_type,
            "file_size": file_size,
        }

    async def parse_file_async(self, file_id: str) -> None:
        try:
            await parse_file(file_id, self.dao)
        except Exception as exc:
            logger.error("文件解析失败: %s, %s", file_id, exc, exc_info=True)
            await self.dao.update_parse_status(file_id=file_id, status="failed")

    async def get_file_info(self, file_id: str) -> Optional[Dict[str, Any]]:
        row = await self.dao.get_file(file_id)
        return _file_info(row) if row else None

    async def delete_file(self, file_id: str) -> bool:
        info = await self.get_file_info(file_id)
        if not info:
            return False
        storage_dir = UPLOADS_ROOT / info["storage_dir"]
        if storage_dir.exists():
            shutil.rmtree(storage_dir)
        await self.dao.delete_file(file_id)
        logger.info("文件删除成功: %s", file_id)
        return True

    async def get_file_content(self, file_id: str) -> Optional[bytes]:
        info = await self.get_file_info(file_id)
        if not info:
            return None
        file_path = UPLOADS_ROOT / info["storage_dir"] / f"original.{info['file_type']}"
        return file_path.read_bytes() if file_path.exists() else None

    async def get_parsed_content(self, file_id: str) -> Optional[str]:
        info = await self.get_file_info(file_id)
        if not info or info["status"] != "completed" or not info["parse_filename"]:
            return None
        parsed_path = UPLOADS_ROOT / info["storage_dir"] / info["parse_filename"]
        return parsed_path.read_text(encoding="utf-8") if parsed_path.exists() else None

    async def list_files_by_thread(self, thread_id: str) -> list:
        rows = await self.dao.list_files_by_thread(thread_id)
        return [_file_summary(row) for row in rows]

    async def get_files_by_thread(self, thread_id: str) -> list:
        rows = await self.dao.list_files_by_thread(thread_id, ascending=True)
        return [
            {
                "id": row.file_id,
                "thread_id": row.thread_id,
                "original_filename": row.original_filename,
                "file_type": row.file_type,
                "file_size": row.file_size,
                "storage_dir": row.storage_dir,
                "status": row.parse_status,
                "parse_filename": row.parse_filename,
                "create_time": row.create_time.strftime("%Y-%m-%d %H:%M:%S") if row.create_time else "",
            }
            for row in rows
        ]

    async def get_file_path_by_id(self, file_id: str) -> Optional[str]:
        info = await self.get_file_info(file_id)
        if not info:
            return None
        file_path = UPLOADS_ROOT / info["storage_dir"] / f"original.{info['file_type']}"
        return str(file_path) if file_path.exists() else None


_uploaded_file_service: Optional[UploadedFileService] = None


def get_uploaded_file_service() -> UploadedFileService:
    """获取上传文件服务实例。"""
    global _uploaded_file_service
    if _uploaded_file_service is None:
        _uploaded_file_service = UploadedFileService()
    return _uploaded_file_service


async def upload_file(file: UploadFile, thread_id: str) -> Dict[str, Any]:
    return await get_uploaded_file_service().upload_file(file, thread_id)


async def get_file_info(file_id: str) -> Optional[Dict[str, Any]]:
    return await get_uploaded_file_service().get_file_info(file_id)


async def delete_file(file_id: str) -> bool:
    return await get_uploaded_file_service().delete_file(file_id)


async def get_file_content(file_id: str) -> Optional[bytes]:
    return await get_uploaded_file_service().get_file_content(file_id)


async def get_parsed_content(file_id: str) -> Optional[str]:
    return await get_uploaded_file_service().get_parsed_content(file_id)


async def list_files_by_thread(thread_id: str) -> list:
    return await get_uploaded_file_service().list_files_by_thread(thread_id)


async def get_files_by_thread(thread_id: str) -> list:
    return await get_uploaded_file_service().get_files_by_thread(thread_id)


async def get_file_path_by_id(file_id: str) -> Optional[str]:
    return await get_uploaded_file_service().get_file_path_by_id(file_id)
