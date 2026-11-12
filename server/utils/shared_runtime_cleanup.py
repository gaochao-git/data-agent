"""共享运行目录清理：只清过期且没有 ref 指向的 semantic；skills 仅清临时残留。"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from utils.storage_paths import THREADS_DIR, semantic_cache_dir


def _is_old(path: Path, cutoff: float) -> bool:
    try:
        return path.stat().st_mtime < cutoff
    except OSError:
        return False


def _protected_semantic_paths() -> set[Path]:
    protected: set[Path] = set()
    for ref_file in THREADS_DIR.glob("*/runtime/semantic/.refs/**/*.json"):
        try:
            data = json.loads(ref_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        datasource_id = str(data.get("datasource_id") or "").strip()
        user_id = str(data.get("user_id") or "").strip()
        if datasource_id and user_id:
            protected.add(semantic_cache_dir(user_id, datasource_id).resolve())
    return protected


def cleanup_shared_runtime(
    *, max_age_seconds: float, now: float | None = None, dry_run: bool = False
) -> dict[str, list[str]]:
    """清理共享 runtime 的过期派生数据。

    semantic 只删没有会话 ref 指向的目录；skills 只删构建临时目录，不删用户快照。
    """
    cutoff = (now if now is not None else time.time()) - max_age_seconds
    removed: dict[str, list[str]] = {"semantic": [], "skill_tmp": []}
    protected = _protected_semantic_paths()
    for user_dir in THREADS_DIR.glob("*/runtime"):
        semantic_root = user_dir / "semantic"
        if semantic_root.is_dir():
            for path in semantic_root.iterdir():
                if path.name == ".refs" or not path.is_dir():
                    continue
                if path.resolve() in protected or not _is_old(path, cutoff):
                    continue
                removed["semantic"].append(str(path))
                if not dry_run:
                    shutil.rmtree(path, ignore_errors=True)
        # 构建临时目录是 runtime/skills.building-* / skills.old-*，位于 skills 的兄弟层。
        for path in user_dir.iterdir():
            if path.name == "skills" or not path.is_dir():
                continue
            if ".building-" not in path.name and ".old-" not in path.name:
                continue
            if not _is_old(path, cutoff):
                continue
            removed["skill_tmp"].append(str(path))
            if not dry_run:
                shutil.rmtree(path, ignore_errors=True)
    return removed
