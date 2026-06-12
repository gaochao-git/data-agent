from __future__ import annotations

import json
import os

from apps.complaint_app.core.versioning import load_manifest, stage_dir
from apps.complaint_app.services.summary.storage import load_summary_json_from_storage
from utils.logger import get_logger


logger = get_logger(__name__)

CHAT_CONTEXT_MODE = os.getenv("CHAT_CONTEXT_MODE", "3")
CHAT_STRIP_MD_TABLES = os.getenv("CHAT_STRIP_MD_TABLES", "true")


def context_mode() -> int:
    try:
        mode = int(CHAT_CONTEXT_MODE)
        if mode in {1, 2, 3}:
            return mode
    except Exception:
        pass
    return 3


def strip_tables_enabled() -> bool:
    return str(CHAT_STRIP_MD_TABLES).lower() not in {"0", "false", "no", "off"}


def load_latest_summary(user_id: str, base_id: str) -> dict | None:
    """Return latest summary section (dict) from versioned summary."""
    try:
        manifest = load_manifest(user_id, base_id)
        summary_version = int(manifest.get("summary", {}).get("current") or 0) if isinstance(manifest.get("summary"), dict) else 0
    except Exception:
        summary_version = 0
    if summary_version > 0:
        version_dir = stage_dir(user_id, base_id, "summary", summary_version)
        json_path = os.path.join(version_dir, "final.json")
        md_path = os.path.join(version_dir, "final.md")
        if os.path.isfile(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                if isinstance(data, dict):
                    return data
            except Exception:
                logger.debug("Failed to read summary json for base_id=%s version=%s", base_id, summary_version, exc_info=True)
        if os.path.isfile(md_path):
            try:
                with open(md_path, "r", encoding="utf-8") as handle:
                    md_text = handle.read()
                if md_text.strip():
                    return {"markdown": md_text}
            except Exception:
                logger.debug("Failed to read summary markdown for base_id=%s version=%s", base_id, summary_version, exc_info=True)
    return load_summary_json_from_storage(user_id, base_id)


def load_latest_text(user_id: str, base_id: str) -> str:
    """Return latest raw text content from versioned text stage."""
    try:
        manifest = load_manifest(user_id, base_id)
        text_version = int(manifest.get("text", {}).get("current") or 0) if isinstance(manifest.get("text"), dict) else 0
    except Exception:
        text_version = 0
    if text_version > 0:
        version_dir = stage_dir(user_id, base_id, "text", text_version)
        for candidate in ("final.txt", "final.md"):
            candidate_path = os.path.join(version_dir, candidate)
            if os.path.isfile(candidate_path):
                try:
                    with open(candidate_path, "r", encoding="utf-8") as handle:
                        return handle.read()
                except Exception:
                    logger.debug("Failed to read text file %s", candidate_path, exc_info=True)
    return ""
