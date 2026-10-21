"""上传文件业务服务。"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from utils.logger import get_logger
from utils.upload_storage_crypto import MAGIC, encryption_enabled, new_stream_encryptor, read_plaintext_bytes

from ...daos.uploaded_file_dao import UploadedFileDao
from ..setting_service import get_upload_size_limit
from .conversation_workspace import get_conversation_created_date, resolve_uploads_root

logger = get_logger(__name__)

# 单文件上传上限：配置用 MB（CHAT_DATA_MAX_UPLOAD_SIZE_MB，正整数），运行期经
# 后台配置变量表读取（表覆盖 > env > 默认 50），改后最迟 30s 生效，无需重启。
# 前端 AnalysisConversation.vue 另有一份 50MB 的**客户端预检**（MAX_FILE_SIZE），
# 只用于提前给出提示、不代替本处校验；把上限调大时须同步前端，否则大文件会在前端被拦。
UPLOAD_CHUNK_SIZE = 1024 * 1024

# 多模态图片附件限制：作为 content blocks 直接进入模型消息的准入条件
from apps.chat_data_app.agent.workspace.uploaded_file_parser import IMAGE_EXTENSIONS  # noqa: F401

MAX_IMAGES_PER_MESSAGE = 4

# 允许上传的文件类型白名单（唯一来源；与前端 AnalysisConversation.vue 的 ACCEPTED_FILE_TYPES
# 保持一致）。图片类型（png/jpg/jpeg/webp/gif）不进入文档解析，作为 vision 输入直接使用。
# 主对话上传（routers/uploaded_files.py）与技能文件上传（agent/skills/skill_service.py）共用本口径。
ALLOWED_FILE_EXTENSIONS = {"txt", "md", "sql", "docx", "xlsx", "pptx", "pdf", "png", "jpg", "jpeg", "webp", "gif"}


def _unique_storage_name(uploads_root: Path, original_filename: str) -> str:
    """生成 uploads 根目录下不重名的存储文件名（保留原始文件名，重名加 -N 后缀）。"""
    base = Path(original_filename).name or "unknown"
    candidate = base
    counter = 1
    while (uploads_root / candidate).exists():
        stem = Path(base).stem
        suffix = Path(base).suffix
        candidate = f"{stem}-{counter}{suffix}"
        counter += 1
    return candidate


def _get_file_extension(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    return ext[1:] if ext else ""


def _file_info(row: Any) -> dict[str, Any]:
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


def _file_summary(row: Any) -> dict[str, Any]:
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

    def __init__(self, dao: UploadedFileDao | None = None) -> None:
        self.dao = dao or UploadedFileDao()

    async def upload_file(self, file: UploadFile, thread_id: str, user_id: str = "system") -> dict[str, Any]:
        if not await self.dao.thread_belongs_to_user(thread_id, user_id=user_id):
            raise ValueError("会话不存在或无权访问")
        file_id = str(uuid.uuid4())
        original_filename = file.filename or "unknown"
        file_type = _get_file_extension(original_filename)
        created_date = await get_conversation_created_date(thread_id, user_id=user_id)
        uploads_root = resolve_uploads_root(user_id, thread_id, created_date=created_date)
        storage_name = _unique_storage_name(uploads_root, original_filename)
        full_path = uploads_root / storage_name
        temporary_path = uploads_root / f".{storage_name}.{uuid.uuid4().hex}.upload"
        file_size = 0
        max_upload_size_mb, max_upload_size = await get_upload_size_limit()
        try:
            encryptor = None
            with temporary_path.open("xb") as output:
                if encryption_enabled():
                    nonce, encryptor = new_stream_encryptor()
                    output.write(MAGIC)
                    output.write(nonce)
                while chunk := await file.read(UPLOAD_CHUNK_SIZE):
                    file_size += len(chunk)
                    if file_size > max_upload_size:
                        raise ValueError(f"文件大小不能超过{max_upload_size_mb}MB")
                    output.write(encryptor.update(chunk) if encryptor is not None else chunk)
                if encryptor is not None:
                    output.write(encryptor.finalize())
                    output.write(encryptor.tag)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_path, full_path)
            try:
                await self.dao.create_file(
                    file_id=file_id,
                    thread_id=thread_id,
                    original_filename=original_filename,
                    file_type=file_type,
                    file_size=file_size,
                    storage_dir=storage_name,
                    now=datetime.now(),
                )
            except Exception:
                full_path.unlink(missing_ok=True)
                raise
        finally:
            temporary_path.unlink(missing_ok=True)
        logger.info("文件上传成功: %s, %s", file_id, original_filename)
        return {
            "file_id": file_id,
            "original_filename": original_filename,
            "file_type": file_type,
            "file_size": file_size,
            "storage_dir": storage_name,
        }

    async def get_file_info(self, file_id: str, user_id: str = "system", thread_id: str = "") -> dict[str, Any] | None:
        row = await self.dao.get_file(file_id, user_id=user_id)
        if row and thread_id and row.thread_id != thread_id:
            return None
        return _file_info(row) if row else None

    async def delete_file(self, file_id: str, user_id: str = "system", thread_id: str = "") -> bool:
        info = await self.get_file_info(file_id, user_id=user_id, thread_id=thread_id)
        if not info:
            return False
        target_thread_id = thread_id or info["thread_id"]
        created_date = await get_conversation_created_date(target_thread_id, user_id=user_id)
        uploads_root = resolve_uploads_root(user_id, target_thread_id, created_date=created_date)
        original_path = uploads_root / info["storage_dir"]
        parsed_path = uploads_root / info["parse_filename"] if info.get("parse_filename") else None
        for target in (original_path, parsed_path):
            if target and target.exists():
                try:
                    target.unlink()
                except OSError as exc:
                    # 物理删除失败时保留记账记录并返回失败：账删了文件还在
                    # 会产生孤儿文件，且接口返回"已删除"是误导；记录保留后
                    # 用户重试即可（已删掉的文件 exists 跳过，重试安全）
                    logger.warning("删除上传文件失败，保留上传记录待重试: %s error=%s", target, exc)
                    return False
        await self.dao.delete_file(file_id, user_id=user_id)
        logger.info("文件删除成功: %s", file_id)
        return True

    async def get_file_content(self, file_id: str, user_id: str = "system", thread_id: str = "") -> bytes | None:
        info = await self.get_file_info(file_id, user_id=user_id, thread_id=thread_id)
        if not info:
            return None
        target_thread_id = thread_id or info["thread_id"]
        created_date = await get_conversation_created_date(target_thread_id, user_id=user_id)
        uploads_root = resolve_uploads_root(user_id, target_thread_id, created_date=created_date)
        file_path = uploads_root / info["storage_dir"]
        return await asyncio.to_thread(read_plaintext_bytes, file_path) if file_path.exists() else None

    async def list_files_by_thread(self, thread_id: str, user_id: str = "system") -> list:
        rows = await self.dao.list_files_by_thread(thread_id, user_id=user_id)
        return [_file_summary(row) for row in rows]

    async def get_files_by_thread(self, thread_id: str, user_id: str = "system") -> list:
        rows = await self.dao.list_files_by_thread(thread_id, user_id=user_id, ascending=True)
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

    async def get_file_path_by_id(self, file_id: str, user_id: str = "system", thread_id: str = "") -> str | None:
        info = await self.get_file_info(file_id, user_id=user_id, thread_id=thread_id)
        if not info:
            return None
        target_thread_id = thread_id or info["thread_id"]
        created_date = await get_conversation_created_date(target_thread_id, user_id=user_id)
        uploads_root = resolve_uploads_root(user_id, target_thread_id, created_date=created_date)
        file_path = uploads_root / info["storage_dir"]
        return str(file_path) if file_path.exists() else None


_uploaded_file_service: UploadedFileService | None = None


def get_uploaded_file_service() -> UploadedFileService:
    """获取上传文件服务实例。"""
    global _uploaded_file_service
    if _uploaded_file_service is None:
        _uploaded_file_service = UploadedFileService()
    return _uploaded_file_service


async def upload_file(file: UploadFile, thread_id: str, user_id: str = "system") -> dict[str, Any]:
    return await get_uploaded_file_service().upload_file(file, thread_id, user_id=user_id)


async def get_file_info(file_id: str, user_id: str = "system", thread_id: str = "") -> dict[str, Any] | None:
    return await get_uploaded_file_service().get_file_info(file_id, user_id=user_id, thread_id=thread_id)


async def delete_file(file_id: str, user_id: str = "system", thread_id: str = "") -> bool:
    return await get_uploaded_file_service().delete_file(file_id, user_id=user_id, thread_id=thread_id)


async def get_file_content(file_id: str, user_id: str = "system", thread_id: str = "") -> bytes | None:
    return await get_uploaded_file_service().get_file_content(file_id, user_id=user_id, thread_id=thread_id)


async def list_files_by_thread(thread_id: str, user_id: str = "system") -> list:
    return await get_uploaded_file_service().list_files_by_thread(thread_id, user_id=user_id)


async def get_files_by_thread(thread_id: str, user_id: str = "system") -> list:
    return await get_uploaded_file_service().get_files_by_thread(thread_id, user_id=user_id)


async def get_file_path_by_id(file_id: str, user_id: str = "system", thread_id: str = "") -> str | None:
    return await get_uploaded_file_service().get_file_path_by_id(file_id, user_id=user_id, thread_id=thread_id)
