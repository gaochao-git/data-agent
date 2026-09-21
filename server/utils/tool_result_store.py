"""Private per-conversation storage for agent tool results."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from utils.logger import get_logger
from utils.storage_paths import (
    CHAT_DATA_HOME,
    normalize_conversation_id,
    normalize_created_date,
    normalize_user_id,
)

logger = get_logger(__name__)

TOOL_OFFLOAD_DIR_NAME = ".tool-offload"
TOOL_OFFLOAD_VIRTUAL_ROOT = "/mnt/user-data/outputs/.tool-offload"
QUERY_RESULT_DIR_NAME = "query_result"
QUERY_RESULT_VIRTUAL_ROOT = "/mnt/user-data/outputs/query_result"
_RESULT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_ALLOWED_SUFFIXES = frozenset({".json", ".txt"})


@dataclass(frozen=True)
class ToolResultRecord:
    """A private result file belonging to one user and one conversation."""

    result_id: str
    suffix: str
    user_id: str
    conversation_id: str
    directory_name: str = TOOL_OFFLOAD_DIR_NAME

    @property
    def virtual_path(self) -> str:
        virtual_root = (
            QUERY_RESULT_VIRTUAL_ROOT
            if self.directory_name == QUERY_RESULT_DIR_NAME
            else TOOL_OFFLOAD_VIRTUAL_ROOT
        )
        return f"{virtual_root}/{self.result_id}{self.suffix}"


def validate_tool_result_id(value: str) -> str:
    result_id = str(value or "").strip()
    if not _RESULT_ID_PATTERN.fullmatch(result_id):
        raise ValueError("result_id 非法")
    return result_id


def _safe_conversation_id(value: str) -> str:
    try:
        return normalize_conversation_id(value)
    except ValueError as exc:
        raise ValueError("非法会话 ID") from exc


class ToolResultStore:
    """Read and write hidden tool result files beneath each conversation's outputs."""

    def __init__(self, base_dir: str | Path | None = None):
        self.base_dir = Path(base_dir or CHAT_DATA_HOME).expanduser().resolve()

    def save_query_result(
        self,
        data: dict[str, Any],
        *,
        conversation_id: str,
        user_id: str,
        result_id: str | None = None,
    ) -> ToolResultRecord:
        if not isinstance(data, dict):
            raise ValueError("查询结果必须是对象")
        record = self._record(
            result_id=result_id or str(uuid.uuid4()),
            suffix=".json",
            conversation_id=conversation_id,
            user_id=user_id,
            directory_name=QUERY_RESULT_DIR_NAME,
        )
        path = self._path_for_record(record)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
        except FileExistsError as exc:
            raise ValueError(f"result_id 已存在: {record.result_id}") from exc
        return record

    def save_text_result(
        self,
        content: str,
        *,
        conversation_id: str,
        user_id: str,
        result_id: str | None = None,
    ) -> ToolResultRecord:
        record = self._record(
            result_id=result_id or str(uuid.uuid4()),
            suffix=".txt",
            conversation_id=conversation_id,
            user_id=user_id,
        )
        path = self._path_for_record(record)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(str(content or ""))
        except FileExistsError as exc:
            raise ValueError(f"result_id 已存在: {record.result_id}") from exc
        return record

    def load_query_result_for_conversation(
        self,
        *,
        result_id: str,
        conversation_id: str,
        user_id: str,
        created_date: str | None = None,
    ) -> dict[str, Any] | None:
        """读取一个查询结果；``created_date`` 为会话创建日期（YYYY-MM-DD），须与写入侧一致。"""
        record = self._record(
            result_id=result_id,
            suffix=".json",
            conversation_id=conversation_id,
            user_id=user_id,
            directory_name=QUERY_RESULT_DIR_NAME,
        )
        try:
            with self._path_for_record(record, created_date=created_date).open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("加载查询结果文件失败: result_id=%s error=%s", result_id, exc)
            return None
        return data if isinstance(data, dict) else None

    def _record(
        self,
        *,
        result_id: str,
        suffix: str,
        conversation_id: str,
        user_id: str,
        directory_name: str = TOOL_OFFLOAD_DIR_NAME,
    ) -> ToolResultRecord:
        if suffix not in _ALLOWED_SUFFIXES:
            raise ValueError("私有工具结果文件类型无效")
        if directory_name not in {TOOL_OFFLOAD_DIR_NAME, QUERY_RESULT_DIR_NAME}:
            raise ValueError("私有工具结果目录无效")
        return ToolResultRecord(
            result_id=validate_tool_result_id(result_id),
            suffix=suffix,
            user_id=str(user_id).strip(),
            conversation_id=str(conversation_id).strip(),
            directory_name=directory_name,
        )

    def _path_for_record(self, record: ToolResultRecord, *, created_date: str | None = None) -> Path:
        if not record.user_id or not record.conversation_id:
            raise ValueError("保存工具结果必须提供 conversation_id 与 user_id")
        user_id = normalize_user_id(record.user_id)
        conversation_id = _safe_conversation_id(record.conversation_id)
        path = (
            self.base_dir
            / "threads"
            / user_id
            / normalize_created_date(created_date)
            / conversation_id
            / "user-data"
            / "outputs"
            / record.directory_name
            / f"{record.result_id}{record.suffix}"
        ).resolve()
        if self.base_dir != path and self.base_dir not in path.parents:
            raise ValueError("工具结果存储路径越界")
        return path
