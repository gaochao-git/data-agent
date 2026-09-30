"""上传文件数据访问层。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy import update as sa_update

from utils.database import get_session as default_get_session
from utils.models import UploadedFile, UserThread


class UploadedFileDao:
    """封装上传文件表读写。"""

    def __init__(self, session_context: Callable | None = None) -> None:
        self._session_context = session_context or default_get_session

    async def thread_belongs_to_user(self, thread_id: str, *, user_id: str) -> bool:
        async with self._session_context() as session:
            result = await session.execute(
                select(UserThread.id).where(
                    UserThread.thread_id == thread_id,
                    UserThread.user_id == user_id,
                    UserThread.delete_flag == 0,
                )
            )
            return result.scalar_one_or_none() is not None

    async def create_file(
        self,
        *,
        file_id: str,
        thread_id: str,
        original_filename: str,
        file_type: str,
        file_size: int,
        storage_dir: str,
        now: datetime,
    ) -> UploadedFile:
        async with self._session_context() as session:
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
            await session.flush()
            return uploaded_file

    async def get_file(self, file_id: str, *, user_id: str) -> UploadedFile | None:
        async with self._session_context() as session:
            result = await session.execute(
                select(UploadedFile)
                .join(UserThread, UserThread.thread_id == UploadedFile.thread_id)
                .where(
                    UploadedFile.file_id == file_id,
                    UploadedFile.delete_flag == 0,
                    UserThread.user_id == user_id,
                    UserThread.delete_flag == 0,
                )
            )
            return result.scalar_one_or_none()

    async def delete_file(self, file_id: str, *, user_id: str) -> None:
        async with self._session_context() as session:
            await session.execute(
                sa_update(UploadedFile)
                .where(
                    UploadedFile.file_id == file_id,
                    UploadedFile.delete_flag == 0,
                    UploadedFile.thread_id.in_(
                        select(UserThread.thread_id).where(
                            UserThread.user_id == user_id,
                            UserThread.delete_flag == 0,
                        )
                    ),
                )
                .values(delete_flag=1, update_time=datetime.now())
            )

    async def update_parse_status(
        self,
        *,
        file_id: str,
        status: str,
        user_id: str,
        parse_filename: str | None = None,
    ) -> None:
        values = {"parse_status": status, "update_time": datetime.now()}
        if parse_filename is not None:
            values["parse_filename"] = parse_filename
        async with self._session_context() as session:
            await session.execute(
                sa_update(UploadedFile)
                .where(
                    UploadedFile.file_id == file_id,
                    UploadedFile.delete_flag == 0,
                    UploadedFile.thread_id.in_(
                        select(UserThread.thread_id).where(
                            UserThread.user_id == user_id,
                            UserThread.delete_flag == 0,
                        )
                    ),
                )
                .values(**values)
            )

    async def list_files_by_thread(
        self,
        thread_id: str,
        *,
        user_id: str,
        ascending: bool = False,
    ) -> list[UploadedFile]:
        order_by = UploadedFile.create_time.asc() if ascending else UploadedFile.create_time.desc()
        async with self._session_context() as session:
            result = await session.execute(
                select(UploadedFile)
                .join(UserThread, UserThread.thread_id == UploadedFile.thread_id)
                .where(
                    UploadedFile.thread_id == thread_id,
                    UploadedFile.delete_flag == 0,
                    UserThread.user_id == user_id,
                    UserThread.delete_flag == 0,
                )
                .order_by(order_by)
            )
            return list(result.scalars().all())
