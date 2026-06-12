"""Shared filesystem paths derived from DATA_ROOT settings."""
from __future__ import annotations

import os
import re
from pathlib import Path

_SERVER_DIR = Path(__file__).resolve().parents[1]


def _resolve_data_root() -> Path:
    value = os.getenv("DATA_ROOT")
    if not value:
        raise RuntimeError("DATA_ROOT is required")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = _SERVER_DIR / path
    return path


_BASE_PATH = _resolve_data_root()

DATA_ROOT = str(_BASE_PATH)
COMPLAINTS_BASE_DIR = str(_BASE_PATH / "complaints")
VIOLATION_BASE_DIR = str(_BASE_PATH / "violation")
CASE_LIBRARY_BASE_DIR = str(_BASE_PATH / "case_library")
NL2SQL_BASE_DIR = str(_BASE_PATH / "nl2sql")

_SEGMENT_SAFE_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


def _normalize_segment(
    value: str | None,
    *,
    replace_invalid: bool,
) -> str:
    if not isinstance(value, str):
        raise ValueError("Invalid path segment type")

    trimmed = value.strip()
    if not trimmed:
        raise ValueError("Path segment must not be empty")

    # Guard against directory traversal
    if "/" in trimmed or "\\" in trimmed:
        trimmed = trimmed.replace("/", "_").replace("\\", "_")

    normalized = trimmed

    if replace_invalid:
        normalized = re.sub(r"[^A-Za-z0-9_.-]", "_", normalized)
    elif not _SEGMENT_SAFE_PATTERN.match(normalized):
        raise ValueError("Path segment contains unsupported characters")

    if not normalized or normalized in {".", ".."} or normalized.startswith("."):
        raise ValueError("Path segment must contain valid characters")

    return normalized


def normalize_user_id(user_id: str) -> str:
    """Return a filesystem-safe representation of the authenticated user id."""
    return _normalize_segment(user_id, replace_invalid=True)


def validate_base_id(base_id: str) -> str:
    """Ensure the complaint base_id can safely be used as a filesystem segment."""
    return _normalize_segment(base_id, replace_invalid=False)


def get_user_dir(user_id: str) -> str:
    """Return the per-user complaints directory."""
    normalized_user = normalize_user_id(user_id)
    return os.path.join(COMPLAINTS_BASE_DIR, normalized_user)


def get_user_complaint_root(user_id: str, base_id: str) -> str:
    """Return the filesystem root for a specific complaint instance."""
    complaint_root = os.path.join(get_user_dir(user_id), validate_base_id(base_id))
    return complaint_root


def get_complaints_real_path(user_id: str, base_id: str, relative_path: str) -> str:
    """Return an absolute path under DATA_ROOT/complaints/<user>/<base_id>/..."""
    return os.path.join(get_user_complaint_root(user_id, base_id), relative_path)


__all__ = [
    "DATA_ROOT",
    "COMPLAINTS_BASE_DIR",
    "VIOLATION_BASE_DIR",
    "CASE_LIBRARY_BASE_DIR",
    "NL2SQL_BASE_DIR",
    "get_complaints_real_path",
    "get_user_complaint_root",
    "get_user_dir",
    "normalize_user_id",
    "validate_base_id",
]
