from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from utils.logger import get_logger
from utils.storage_paths import DATA_ROOT

logger = get_logger(__name__)


def normalize_trace_file_ids(file_ids: list[str] | tuple[str, ...] | None) -> list[str]:
    """规范化 file_ids，保证同一组文件得到同一 base_id。"""
    return sorted({str(value or "").strip() for value in file_ids or [] if str(value or "").strip()})


def build_trace_base_id(file_ids: list[str] | tuple[str, ...]) -> str:
    """按 complaint 现有规则生成 base_id。"""
    normalized = normalize_trace_file_ids(file_ids)
    if not normalized:
        raise ValueError("file_ids 不能为空")
    if len(normalized) == 1:
        return hashlib.sha1(normalized[0].encode("utf-8")).hexdigest()[:16]
    raw = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def get_wordfit_root_dir() -> str:
    """读取 WordFit 根目录。"""
    configured = str(os.getenv("WORDFIT_ROOT_DIR") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        return str(path if path.is_absolute() else Path(DATA_ROOT) / path)
    return os.path.join(DATA_ROOT, "wordfit")


def utc_now_iso() -> str:
    """返回 UTC ISO 时间。"""
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def build_wordfit_trace_context(file_ids: list[str], conversation_id: str, analysis_user_id: str | None = None) -> dict[str, str]:
    """构造字段提取 trace 关联信息。"""
    safe_conversation_id = "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in str(conversation_id or "").strip())
    return {
        "base_id": build_trace_base_id(file_ids),
        "conversation_id": safe_conversation_id or utc_now_iso().replace(":", "").replace("-", ""),
        "analysis_user_id": str(analysis_user_id or "").strip(),
    }


def save_wordfit_extract_trace(payload: dict[str, Any]) -> None:
    """按 base_id/conversation_id 保存字段提取 trace。"""
    if not isinstance(payload, dict):
        return
    base_id = str(payload.get("base_id") or "").strip()
    conversation_id = str(payload.get("conversation_id") or "").strip()
    if not base_id or not conversation_id:
        return
    try:
        trace_dir = Path(get_wordfit_root_dir()) / "traces" / "by_base" / base_id
        trace_dir.mkdir(parents=True, exist_ok=True)
        trace_path = trace_dir / f"{conversation_id}.json"
        with open(trace_path, "w", encoding="utf-8") as file_obj:
            json.dump(payload, file_obj, ensure_ascii=False, indent=2, default=str)
    except Exception as exc:
        logger.error("[WordFitTrace] save_failed base_id=%s conversation_id=%s err=%s", base_id, conversation_id, exc)


def list_wordfit_extract_traces(base_id: str) -> list[dict[str, Any]]:
    """读取同一 base_id 下全部字段提取 trace。"""
    trace_dir = Path(get_wordfit_root_dir()) / "traces" / "by_base" / str(base_id or "").strip()
    if not trace_dir.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(trace_dir.glob("*.json")):
        try:
            with open(path, "r", encoding="utf-8") as file_obj:
                payload = json.load(file_obj)
            if isinstance(payload, dict):
                payload.setdefault("trace_path", str(path))
                records.append(payload)
        except Exception as exc:
            logger.error("[WordFitTrace] read_failed path=%s err=%s", path, exc)
    return records
