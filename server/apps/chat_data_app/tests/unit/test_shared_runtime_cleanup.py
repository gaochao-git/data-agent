"""共享 runtime 清理：semantic 按 ref 保护，skills 只清构建临时目录。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from utils import shared_runtime_cleanup as cleanup
from utils import storage_paths


def _set_mtime(path: Path, value: float) -> None:
    os.utime(path, (value, value))


def test_cleanup_protects_referenced_semantic_and_clears_stale(tmp_path, monkeypatch):
    threads = tmp_path / "threads"
    monkeypatch.setattr(storage_paths, "THREADS_DIR", threads)
    monkeypatch.setattr(cleanup, "THREADS_DIR", threads)
    old = time.time() - 10 * 86400
    now = time.time()

    protected = threads / "u1" / "runtime" / "semantic" / "ds1"
    stale = threads / "u1" / "runtime" / "semantic" / "ds2"
    protected.mkdir(parents=True)
    stale.mkdir(parents=True)
    _set_mtime(protected, old)
    _set_mtime(stale, old)
    ref = threads / "u1" / "runtime" / "semantic" / ".refs" / "2026-11-12" / "c1.json"
    ref.parent.mkdir(parents=True)
    ref.write_text(json.dumps({"user_id": "u1", "datasource_id": "ds1"}), encoding="utf-8")

    tmp_skill = threads / "u1" / "runtime" / "skills.building-abc"
    tmp_skill.mkdir(parents=True)
    _set_mtime(tmp_skill, old)
    ready_skill = threads / "u1" / "runtime" / "skills"
    ready_skill.mkdir(parents=True)
    (ready_skill / ".snapshot-ready").touch()

    removed = cleanup.cleanup_shared_runtime(max_age_seconds=7 * 86400, now=now)

    assert removed["semantic"] == [str(stale)]
    assert protected.is_dir()
    assert not stale.exists()
    assert removed["skill_tmp"] == [str(tmp_skill)]
    assert ready_skill.is_dir()


def test_cleanup_dry_run_keeps_stale(tmp_path, monkeypatch):
    threads = tmp_path / "threads"
    monkeypatch.setattr(cleanup, "THREADS_DIR", threads)
    stale = threads / "u1" / "runtime" / "semantic" / "ds2"
    stale.mkdir(parents=True)
    _set_mtime(stale, time.time() - 30 * 86400)

    removed = cleanup.cleanup_shared_runtime(max_age_seconds=7 * 86400, dry_run=True)

    assert removed["semantic"] == [str(stale)]
    assert stale.is_dir()
