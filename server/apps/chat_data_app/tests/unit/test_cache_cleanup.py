"""派生缓存清理脚本行为测试：只认新布局，不再扫描历史目录。"""

import os
import time
from pathlib import Path
from types import SimpleNamespace

from utils import cache_cleanup


def _set_mtime(path: Path, timestamp: float) -> None:
    os.utime(path, (timestamp, timestamp))


def _patch_cache_roots(monkeypatch, tmp_path):
    roots = SimpleNamespace(
        runtime=tmp_path / "runtime-skills",
        semantic=tmp_path / "semantic",
        refs=tmp_path / "semantic-refs",
        threads=tmp_path / "threads",
    )
    monkeypatch.setattr(cache_cleanup, "CACHE_RUNTIME_SKILLS_DIR", roots.runtime)
    monkeypatch.setattr(cache_cleanup, "CACHE_SEMANTIC_DIR", roots.semantic)
    monkeypatch.setattr(cache_cleanup, "CACHE_SEMANTIC_REF_DIR", roots.refs)
    monkeypatch.setattr(
        cache_cleanup,
        "semantic_cache_dir",
        lambda user, datasource: roots.semantic / user / datasource,
    )
    return roots


def test_cleanup_removes_only_expired_unprotected_caches(tmp_path, monkeypatch):
    roots = _patch_cache_roots(monkeypatch, tmp_path)
    old = time.time() - 10 * 86400
    now = time.time()

    old_runtime = roots.runtime / "u1" / "old"
    new_runtime = roots.runtime / "u1" / "new"
    old_runtime.mkdir(parents=True)
    new_runtime.mkdir(parents=True)
    _set_mtime(old_runtime, old)

    user_dir = roots.semantic / "u1"
    protected_semantic = user_dir / "ds1"
    stale_semantic = user_dir / "stale_ds"
    protected_semantic.mkdir(parents=True)
    stale_semantic.mkdir(parents=True)
    _set_mtime(protected_semantic, old)
    _set_mtime(stale_semantic, old)
    _set_mtime(user_dir, old)
    ref_file = roots.refs / "u1" / "2026-11-11" / "c1.json"
    ref_file.parent.mkdir(parents=True)
    ref_file.write_text('{"datasource_id":"ds1"}', encoding="utf-8")

    removed = cache_cleanup.cleanup_derived_caches(max_age_seconds=7 * 86400, now=now)

    assert set(removed) == {"runtime_skills", "semantic"}
    assert removed["runtime_skills"] == [str(old_runtime)]
    assert removed["semantic"] == [str(stale_semantic)]
    assert not old_runtime.exists()
    assert new_runtime.exists()
    # 被 ref 指向目录的祖先不能整体删掉，否则会连带删掉活跃缓存。
    assert user_dir.is_dir()
    assert protected_semantic.exists()
    assert not stale_semantic.exists()


def test_cleanup_keeps_ready_shared_snapshot_skill_dirs(tmp_path, monkeypatch):
    """共享快照的技能子目录不是 GC 目标：删它会打断该用户所有活跃会话。"""
    roots = _patch_cache_roots(monkeypatch, tmp_path)
    expired = time.time() - 30 * 86400
    user_dir = roots.runtime / "u1"
    skill_dir = user_dir / "skill-a"
    skill_dir.mkdir(parents=True)
    (user_dir / ".snapshot-ready").write_text("{}", encoding="utf-8")
    _set_mtime(skill_dir, expired)
    # 无完成标记的过期目录（构建残留等）仍是清理目标。
    orphan = roots.runtime / "u2" / "deadbeef"
    orphan.mkdir(parents=True)
    _set_mtime(orphan, expired)

    removed = cache_cleanup.cleanup_derived_caches(max_age_seconds=7 * 86400, now=time.time())

    assert skill_dir.is_dir()
    assert str(skill_dir) not in removed["runtime_skills"]
    assert str(orphan) in removed["runtime_skills"]
    assert not orphan.exists()


def test_hash_ref_does_not_resurrect_legacy_layout(tmp_path, monkeypatch):
    """ref 只认 user + datasource：多余的 hash 字段不再把保护指向旧共享布局。"""
    roots = _patch_cache_roots(monkeypatch, tmp_path)
    expired = time.time() - 30 * 86400
    current = roots.semantic / "u1" / "ds1"
    legacy = roots.semantic / "ds1" / "scope1" / "cfg1"
    current.mkdir(parents=True)
    legacy.mkdir(parents=True)
    for path in (current, legacy, legacy.parent, legacy.parent.parent):
        _set_mtime(path, expired)
    ref_file = roots.refs / "u1" / "2026-11-11" / "c1.json"
    ref_file.parent.mkdir(parents=True)
    ref_file.write_text(
        '{"datasource_id":"ds1","user_id":"u1","scope_hash":"scope1","config_hash":"cfg1"}',
        encoding="utf-8",
    )

    removed = cache_cleanup.cleanup_derived_caches(max_age_seconds=7 * 86400, now=time.time())

    assert current.is_dir()
    assert not legacy.exists()
    assert str(legacy.parent.parent) in removed["semantic"]


def test_cleanup_does_not_scan_legacy_threads_dir(tmp_path, monkeypatch):
    """历史会话目录由运维按扫描结果单独处理，运行期 GC 不碰它们。"""
    roots = _patch_cache_roots(monkeypatch, tmp_path)
    legacy = roots.threads / "u1" / "2026-11-11" / "c1" / "runtime-skills"
    legacy.mkdir(parents=True)
    _set_mtime(legacy, time.time() - 30 * 86400)

    removed = cache_cleanup.cleanup_derived_caches(max_age_seconds=7 * 86400, now=time.time())

    assert legacy.is_dir()
    assert set(removed) == {"runtime_skills", "semantic"}


def test_cleanup_dry_run_reports_without_deleting(tmp_path, monkeypatch):
    roots = _patch_cache_roots(monkeypatch, tmp_path)
    stale = roots.semantic / "u1" / "stale_ds"
    stale.mkdir(parents=True)
    _set_mtime(stale, time.time() - 30 * 86400)

    removed = cache_cleanup.cleanup_derived_caches(max_age_seconds=7 * 86400, now=time.time(), dry_run=True)

    assert str(stale) in removed["semantic"]
    assert stale.is_dir()


def test_cleanup_keeps_snapshot_lock_file(tmp_path, monkeypatch):
    """跨进程锁文件必须常驻：删掉会换 inode，新旧 worker 锁在不同对象上。"""
    roots = _patch_cache_roots(monkeypatch, tmp_path)
    roots.runtime.mkdir(parents=True, exist_ok=True)
    expired = time.time() - 30 * 86400
    lock_file = roots.runtime / ".abc123.lock"
    lock_file.write_text("", encoding="utf-8")
    _set_mtime(lock_file, expired)
    user_dir = roots.runtime / "u1"
    user_dir.mkdir(parents=True)
    _set_mtime(user_dir, expired)

    removed = cache_cleanup.cleanup_derived_caches(max_age_seconds=7 * 86400, now=time.time())

    assert lock_file.is_file()
    assert str(lock_file) not in removed["runtime_skills"]
