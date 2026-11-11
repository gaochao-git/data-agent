"""本地派生缓存清理：只删除过期且未被会话 ref 引用的缓存目录。"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from utils.storage_paths import (
    CACHE_RUNTIME_SKILLS_DIR,
    CACHE_SEMANTIC_DIR,
    CACHE_SEMANTIC_REF_DIR,
    semantic_cache_dir,
)


def _is_old(path: Path, cutoff: float) -> bool:
    try:
        return path.stat().st_mtime < cutoff
    except OSError:
        return False


def _leaf_dirs(root: Path, depth: int) -> list[Path]:
    if not root.is_dir():
        return []
    candidates = root.glob("/".join(["*"] * depth))
    return [path for path in candidates if path.is_dir()]


# 与运行域快照完成标记一致；utils 层不反向 import apps 层，故此处保留字面量。
_SNAPSHOT_READY_MARKER = ".snapshot-ready"


def _is_snapshot_temp_dir(path: Path) -> bool:
    return ".building-" in path.name or ".old-" in path.name


def _is_protected(path: Path, protected: set[Path]) -> bool:
    """目录本身被 ref 指向，或它是被保护目录的祖先（删父会连带删子）。"""
    resolved = path.resolve()
    return resolved in protected or any(resolved in item.parents for item in protected)


def _protected_semantic_paths() -> set[Path]:
    """当前会话 ref 指向的语义缓存目录；ref 只认 semantic/.refs 一个真值源。"""
    protected: set[Path] = set()
    if not CACHE_SEMANTIC_REF_DIR.is_dir():
        return protected
    for ref_file in CACHE_SEMANTIC_REF_DIR.rglob("*.json"):
        try:
            data = json.loads(ref_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        datasource_id = str(data.get("datasource_id") or "").strip()
        if not datasource_id:
            continue
        relative_parts = ref_file.relative_to(CACHE_SEMANTIC_REF_DIR).parts
        user_id = str(data.get("user_id") or "").strip() or (relative_parts[0] if relative_parts else "system")
        protected.add(semantic_cache_dir(user_id, datasource_id).resolve())
    return protected


def cleanup_derived_caches(
    *, max_age_seconds: float, now: float | None = None, dry_run: bool = False
) -> dict[str, list[str]]:
    """清理过期的本地派生缓存；只认新布局，历史目录由运维按扫描结果单独处理。

    返回字典固定两类：``runtime_skills``（快照临时/异常目录）与 ``semantic``
    （未被会话 ref 引用的用户/数据源组目录）。运行期 GC 不扫描旧布局目录，
    避免把「兼容读取」以清理逻辑的形式留在代码里。
    """
    cutoff = (now if now is not None else time.time()) - max_age_seconds
    removed: dict[str, list[str]] = {"runtime_skills": [], "semantic": []}
    protected = _protected_semantic_paths()

    # 共享快照按用户一份（runtime-skills/<uid>），删掉会让该用户所有活跃会话
    # 同时重建；数量随用户数收敛、有上界，因此 depth=1 不作为 GC 目标。
    for path in _leaf_dirs(CACHE_RUNTIME_SKILLS_DIR, 2):
        # depth=2 可能是共享快照里的技能子目录（父目录带完成标记），
        # 它们必须整目录保留；只清临时残留与无完成标记的过期目录。
        if (path.parent / _SNAPSHOT_READY_MARKER).is_file() and not _is_snapshot_temp_dir(path):
            continue
        if _is_old(path, cutoff):
            removed["runtime_skills"].append(str(path))
            if not dry_run:
                shutil.rmtree(path, ignore_errors=True)

    for path in _leaf_dirs(CACHE_SEMANTIC_DIR, 1):
        if path.name == ".refs" or _is_protected(path, protected) or not _is_old(path, cutoff):
            continue
        removed["semantic"].append(str(path))
        if not dry_run:
            shutil.rmtree(path, ignore_errors=True)

    for path in _leaf_dirs(CACHE_SEMANTIC_DIR, 2):
        if path.parts[-2] == ".refs" or _is_protected(path, protected) or not _is_old(path, cutoff):
            continue
        removed["semantic"].append(str(path))
        if not dry_run:
            shutil.rmtree(path, ignore_errors=True)

    for path in _leaf_dirs(CACHE_SEMANTIC_DIR, 3):
        if ".refs" in path.parts or _is_protected(path, protected) or not _is_old(path, cutoff):
            continue
        removed["semantic"].append(str(path))
        if not dry_run:
            shutil.rmtree(path, ignore_errors=True)

    return removed
