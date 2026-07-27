"""会话内查询结果卸载存储。"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from utils.logger import get_logger
from utils.storage_paths import CHAT_DATA_HOME, normalize_conversation_id, normalize_user_id

logger = get_logger(__name__)

_TOOL_RESULTS_SUBDIR = ".tool-results"
_RESULT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


@dataclass(frozen=True)
class QueryResultRecord:
    result_id: str
    user_id: str
    conversation_id: str


def _safe_conversation_id(value: str) -> str:
    try:
        return normalize_conversation_id(value)
    except ValueError as exc:
        raise ValueError("非法会话 ID") from exc


def _validate_result_id(value: str) -> str:
    result_id = str(value or "").strip()
    if not _RESULT_ID_PATTERN.fullmatch(result_id):
        raise ValueError("result_id 非法")
    return result_id


class QueryResultStore:
    """仅负责当前会话内大查询结果的受控读写。"""

    def __init__(self, base_dir: str | Path | None = None):
        self.base_dir = Path(base_dir or CHAT_DATA_HOME).expanduser().resolve()

    def save(
        self,
        data: dict[str, Any],
        result_id: str | None = None,
        conversation_id: str | None = None,
        user_id: str | None = None,
    ) -> QueryResultRecord:
        if not conversation_id or not user_id:
            raise ValueError("保存查询结果必须提供 conversation_id 与 user_id")
        if not isinstance(data, dict):
            raise ValueError("查询结果必须是对象")
        safe_id = _validate_result_id(result_id or str(uuid.uuid4()))
        raw_user_id = str(user_id).strip()
        raw_conversation_id = str(conversation_id).strip()
        safe_user_id = normalize_user_id(raw_user_id)
        safe_conversation_id = _safe_conversation_id(raw_conversation_id)
        storage_path = self._relative_storage_path(
            user_id=safe_user_id,
            conversation_id=safe_conversation_id,
            result_id=safe_id,
        )
        file_path = self._resolve_storage_path(storage_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        wrapped = {
            "result_id": safe_id,
            "conversation_id": raw_conversation_id,
            "user_id": raw_user_id,
            "data": data,
        }
        try:
            with file_path.open("x", encoding="utf-8") as handle:
                json.dump(wrapped, handle, ensure_ascii=False, indent=2)
        except FileExistsError as exc:
            raise ValueError(f"result_id 已存在: {safe_id}") from exc
        return QueryResultRecord(
            result_id=safe_id,
            user_id=raw_user_id,
            conversation_id=raw_conversation_id,
        )

    def load_for_conversation(
        self,
        *,
        result_id: str,
        conversation_id: str,
        user_id: str,
    ) -> dict[str, Any] | None:
        storage_path = self.storage_path_for(
            result_id=result_id,
            conversation_id=conversation_id,
            user_id=user_id,
        )
        try:
            file_path = self._resolve_storage_path(storage_path)
            wrapped = json.loads(file_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("加载查询结果文件失败: path=%s error=%s", storage_path, exc)
            return None
        if not isinstance(wrapped, dict):
            return None
        if (
            str(wrapped.get("result_id") or "") != _validate_result_id(result_id)
            or str(wrapped.get("conversation_id") or "") != str(conversation_id).strip()
            or str(wrapped.get("user_id") or "") != str(user_id).strip()
            or not isinstance(wrapped.get("data"), dict)
        ):
            logger.warning("查询结果文件元信息不匹配: result_id=%s", result_id)
            return None
        return wrapped["data"]

    def storage_path_for(self, *, result_id: str, conversation_id: str, user_id: str) -> str:
        """按会话归属构造唯一允许的查询结果相对文件路径。"""
        return self._relative_storage_path(
            user_id=normalize_user_id(user_id),
            conversation_id=_safe_conversation_id(conversation_id),
            result_id=_validate_result_id(result_id),
        )

    def _relative_storage_path(
        self,
        *,
        user_id: str,
        conversation_id: str,
        result_id: str,
    ) -> str:
        return str(Path("users") / user_id / "threads" / conversation_id / "user-data" / "outputs" / _TOOL_RESULTS_SUBDIR / "query_result" / f"{result_id}.json")

    def _resolve_storage_path(self, storage_path: str) -> Path:
        raw = Path(str(storage_path or ""))
        if raw.is_absolute() or not raw.parts or ".." in raw.parts:
            raise ValueError("查询结果存储路径非法")
        path = (self.base_dir / raw).resolve()
        if self.base_dir not in path.parents:
            raise ValueError("查询结果存储路径越界")
        return path
