"""Shared filesystem paths derived from DATA_ROOT settings."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


@dataclass(frozen=True)
class ComplaintScopeContext:
    """举报件存储 scope 上下文。"""

    scope: str
    org_id: str
    entity_id: str
    path_parts: tuple[str, ...]
    reporter_name: str = ""

    @property
    def scope_path(self) -> str:
        return "/".join(self.path_parts)

    def to_meta(self) -> dict[str, str]:
        return {"scope": self.scope, "org_id": self.org_id, "entity_id": self.entity_id, "reporter_name": self.reporter_name, "scope_path": self.scope_path}


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


def validate_scope_segment(value: str, field_name: str) -> str:
    """校验 scope 路径段。"""
    try:
        return _normalize_segment(value, replace_invalid=False)
    except ValueError as exc:
        raise ValueError(f"{field_name} 非法: {exc}")


def normalize_reporter_name(value: str | None) -> str:
    """规范化举报人姓名路径段。"""
    raw = str(value or "").strip()
    if not raw:
        return ""
    raw = re.sub(r"-fys-.+$", "", raw, flags=re.IGNORECASE).strip()
    if not raw:
        return ""
    normalized = raw.replace("/", "_").replace("\\", "_")
    normalized = re.sub(r"\s+", "_", normalized)
    normalized = normalized.strip("._-")
    if not normalized or normalized in {".", ".."}:
        raise ValueError("reporter_name 非法: Path segment must contain valid characters")
    return normalized


def _append_reporter_path(path_parts: tuple[str, ...], reporter_name: str) -> tuple[str, ...]:
    if not reporter_name:
        return path_parts
    return (*path_parts, reporter_name)


def resolve_complaint_scope(scope: str | None = None, org_id: str | None = None, entity_id: str | None = None, reporter_name: str | None = None) -> ComplaintScopeContext:
    """按入参解析举报件存储 scope。"""
    raw_scope = str(scope or "").strip().lower()
    normalized_org = validate_scope_segment(org_id, "org_id") if str(org_id or "").strip() else ""
    normalized_entity = validate_scope_segment(entity_id, "entity_id") if str(entity_id or "").strip() else ""
    normalized_reporter = normalize_reporter_name(reporter_name)

    if raw_scope in {"", "auto"}:
        if normalized_org and normalized_entity:
            raw_scope = "entity"
        elif normalized_org:
            raw_scope = "org"
        else:
            raw_scope = "default"
    if raw_scope == "default":
        return ComplaintScopeContext(scope="default", org_id="", entity_id="", reporter_name=normalized_reporter, path_parts=_append_reporter_path(("default",), normalized_reporter))
    if raw_scope in {"org", "organization"}:
        if not normalized_org:
            raise ValueError("org scope 必须提供 org_id")
        return ComplaintScopeContext(scope="org", org_id=normalized_org, entity_id="", reporter_name=normalized_reporter, path_parts=_append_reporter_path((normalized_org,), normalized_reporter))
    if raw_scope in {"entity", "org_entity", "subject"}:
        if not normalized_org:
            raise ValueError("entity scope 必须提供 org_id")
        if not normalized_entity:
            raise ValueError("entity scope 必须提供 entity_id")
        return ComplaintScopeContext(scope="entity", org_id=normalized_org, entity_id=normalized_entity, reporter_name=normalized_reporter, path_parts=_append_reporter_path((normalized_org, normalized_entity), normalized_reporter))
    raise ValueError("scope 仅支持 default/org/entity")


def normalize_complaint_scope_context(scope_context: ComplaintScopeContext | dict[str, Any] | None = None) -> ComplaintScopeContext | None:
    """规范化可选 scope 上下文。"""
    if scope_context is None:
        return None
    if isinstance(scope_context, ComplaintScopeContext):
        return scope_context
    if isinstance(scope_context, dict):
        return resolve_complaint_scope(scope_context.get("scope"), scope_context.get("org_id"), scope_context.get("entity_id"), scope_context.get("reporter_name"))
    raise ValueError("scope_context 非法")


def complaint_scope_read_chain(scope_context: ComplaintScopeContext | dict[str, Any] | None = None) -> list[ComplaintScopeContext]:
    """返回举报件读取回退链：entity -> org -> default。"""
    resolved_scope = normalize_complaint_scope_context(scope_context) or resolve_complaint_scope()
    chain = [resolved_scope]
    if resolved_scope.scope == "entity":
        chain.append(resolve_complaint_scope("org", resolved_scope.org_id, None, resolved_scope.reporter_name))
    if resolved_scope.scope in {"entity", "org"}:
        chain.append(resolve_complaint_scope("default", reporter_name=resolved_scope.reporter_name))
    result: list[ComplaintScopeContext] = []
    seen: set[str] = set()
    for item in chain:
        if item.scope_path in seen:
            continue
        seen.add(item.scope_path)
        result.append(item)
    return result


def get_user_dir(user_id: str) -> str:
    """Return the per-user complaints directory."""
    normalized_user = normalize_user_id(user_id)
    return os.path.join(COMPLAINTS_BASE_DIR, normalized_user)


def get_user_complaint_root(user_id: str, base_id: str) -> str:
    """Return the filesystem root for a specific complaint instance."""
    complaint_root = os.path.join(get_user_dir(user_id), validate_base_id(base_id))
    return complaint_root


def get_scoped_complaint_root(base_id: str, scope_context: ComplaintScopeContext | dict[str, Any] | None = None) -> str:
    """Return DATA_ROOT/complaints/<base_id>/<scope...> root."""
    resolved_scope = normalize_complaint_scope_context(scope_context) or resolve_complaint_scope()
    return os.path.join(COMPLAINTS_BASE_DIR, validate_base_id(base_id), *resolved_scope.path_parts)


def get_complaints_real_path(user_id: str, base_id: str, relative_path: str, scope_context: ComplaintScopeContext | dict[str, Any] | None = None) -> str:
    """Return an absolute complaint path; scoped paths ignore user_id."""
    root = get_scoped_complaint_root(base_id, scope_context) if scope_context is not None else get_user_complaint_root(user_id, base_id)
    return os.path.join(root, relative_path)


__all__ = [
    "DATA_ROOT",
    "COMPLAINTS_BASE_DIR",
    "ComplaintScopeContext",
    "VIOLATION_BASE_DIR",
    "CASE_LIBRARY_BASE_DIR",
    "NL2SQL_BASE_DIR",
    "get_complaints_real_path",
    "get_scoped_complaint_root",
    "get_user_complaint_root",
    "get_user_dir",
    "normalize_user_id",
    "normalize_reporter_name",
    "normalize_complaint_scope_context",
    "complaint_scope_read_chain",
    "resolve_complaint_scope",
    "validate_base_id",
    "validate_scope_segment",
]
