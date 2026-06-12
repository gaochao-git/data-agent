from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, Tuple

from utils.logger import get_logger
from utils.storage_paths import ComplaintScopeContext, get_complaints_real_path, normalize_complaint_scope_context

logger = get_logger(__name__)

_DEFAULT_MANIFEST = {
    "text": {"current": 0},
    "summary": {"current": 0},
    "analysis": {"current": 0},
    "classification": {"current": 0},
}


def _manifest_path(user_id: str, base_id: str, scope_context: ComplaintScopeContext | dict[str, Any] | None = None) -> str:
    """Return absolute manifest.json path for a complaint."""
    return get_complaints_real_path(user_id, base_id, "manifest.json", scope_context=scope_context)


def load_manifest(user_id: str, base_id: str, scope_context: ComplaintScopeContext | dict[str, Any] | None = None) -> Dict[str, Any]:
    """Load manifest.json; fallback to defaults when missing/invalid."""
    path = _manifest_path(user_id, base_id, scope_context)
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                merged = {**_DEFAULT_MANIFEST, **loaded}
                for key in _DEFAULT_MANIFEST:
                    if key not in merged or not isinstance(merged[key], dict):
                        merged[key] = dict(_DEFAULT_MANIFEST[key])
                    merged[key].setdefault("current", 0)
                return merged
    except Exception as exc:
        logger.error("[complaint_versioning] load_manifest_failed user_id=%s base_id=%s path=%s err=%s", user_id, base_id, path, exc)
    return json.loads(json.dumps(_DEFAULT_MANIFEST))


def save_manifest(user_id: str, base_id: str, manifest: Dict[str, Any], scope_context: ComplaintScopeContext | dict[str, Any] | None = None) -> None:
    """Persist manifest.json for a complaint (best-effort)."""
    path = _manifest_path(user_id, base_id, scope_context)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2, default=str)
    except Exception as exc:
        logger.error("[complaint_versioning] save_manifest_failed user_id=%s base_id=%s path=%s err=%s", user_id, base_id, path, exc)


def next_version(manifest: Dict[str, Any], stage: str) -> int:
    """Return the next version number for a stage based on manifest.current."""
    stage_meta = manifest.get(stage) or {}
    try:
        current = int(stage_meta.get("current") or 0)
    except Exception:
        current = 0
    return current + 1


def update_manifest(manifest: Dict[str, Any], stage: str, version: int) -> Dict[str, Any]:
    """Update manifest.current for a stage and return the mutated manifest."""
    manifest = manifest or {}
    stage_meta = manifest.get(stage) if isinstance(manifest.get(stage), dict) else {}
    stage_meta["current"] = int(version)
    manifest[stage] = stage_meta
    return manifest


def stage_dir(user_id: str, base_id: str, stage: str, version: int | None = None, scope_context: ComplaintScopeContext | dict[str, Any] | None = None) -> str:
    """Return the directory path for a given stage/version (or stage root if version is None)."""
    folder = "texts" if stage == "text" else stage
    base_dir = get_complaints_real_path(user_id, base_id, folder, scope_context=scope_context)
    if version is None:
        return base_dir
    return os.path.join(base_dir, f"v{int(version)}")


def write_meta(
    user_id: str,
    base_id: str,
    stage: str,
    version: int,
    source: str,
    base_versions: Dict[str, Any] | None = None,
    extra: Dict[str, Any] | None = None,
    scope_context: ComplaintScopeContext | dict[str, Any] | None = None,
    updated_by: str | None = None,
) -> str:
    """Write meta.json for a version directory; returns meta path."""
    meta = {
        "stage": stage,
        "version": int(version),
        "source": source,
        "ts": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "created_by": str(updated_by or user_id or "").strip(),
        "updated_by": str(updated_by or user_id or "").strip(),
    }
    resolved_scope = normalize_complaint_scope_context(scope_context)
    if resolved_scope is not None:
        meta.update(resolved_scope.to_meta())
    if base_versions:
        meta.update({k: v for k, v in base_versions.items() if v is not None})
    if extra:
        meta.update({k: v for k, v in extra.items() if v is not None})

    version_dir = stage_dir(user_id, base_id, stage, version, scope_context=scope_context)
    os.makedirs(version_dir, exist_ok=True)
    meta_path = os.path.join(version_dir, "meta.json")
    try:
        with open(meta_path, "w", encoding="utf-8") as handle:
            json.dump(meta, handle, ensure_ascii=False, indent=2, default=str)
    except Exception as exc:
        logger.error("[complaint_versioning] write_meta_failed user_id=%s base_id=%s stage=%s version=%s path=%s err=%s", user_id, base_id, stage, version, meta_path, exc)
    return meta_path


def load_latest_analysis(user_id: str, base_id: str, manifest: Dict[str, Any], scope_context: ComplaintScopeContext | dict[str, Any] | None = None) -> Tuple[list, list]:
    """Load items/appeals from the current analysis version recorded in manifest."""
    analysis_meta = manifest.get("analysis") or {}
    try:
        current = int(analysis_meta.get("current") or 0)
    except Exception:
        current = 0
    if current <= 0:
        return [], []
    return load_analysis_version(user_id, base_id, current, scope_context=scope_context)


def load_analysis_version(user_id: str, base_id: str, version: int | None, scope_context: ComplaintScopeContext | dict[str, Any] | None = None) -> Tuple[list, list]:
    """Load items/appeals from a specific analysis version."""
    if not version or version <= 0:
        return [], []
    analysis_path = os.path.join(stage_dir(user_id, base_id, "analysis", version, scope_context=scope_context), "final.json")
    try:
        with open(analysis_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        items = data.get("items") if isinstance(data, dict) else None
        if items is None:
            items = data.get("complaints") if isinstance(data, dict) else []
        appeals = data.get("appeals") if isinstance(data, dict) else []
        if not isinstance(items, list):
            items = []
        if not isinstance(appeals, list):
            appeals = []
        return items, appeals
    except Exception:
        return [], []
