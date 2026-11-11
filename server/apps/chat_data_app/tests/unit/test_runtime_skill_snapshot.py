"""runtime_skill_snapshot 快照构建行为测试。"""

import asyncio
import errno
import fcntl
import os
import subprocess
import sys
import textwrap
import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.chat_data_app.agent.skills import skill_service as skill_service_module
from apps.chat_data_app.agent.workspace import runtime as runtime_module
from apps.chat_data_app.agent.workspace import runtime_skill_snapshot as snapshot_module
from apps.chat_data_app.agent.workspace.runtime import ConversationWorkspaceRuntime
from utils import storage_paths as storage_paths_module


def _make_skill(root: Path, name: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# skill", encoding="utf-8")
    return skill_dir


def _conv_dir(tmp_path: Path, user_id: str = "u1", conversation_id: str = "c1") -> Path:
    """与 conversation_cache_dir 布局一致的本地派生目录。"""
    date_str = datetime.now().strftime("%Y-%m-%d")
    return tmp_path / "cache" / "threads" / user_id / date_str / conversation_id


def _patch_env(monkeypatch, tmp_path, sources):
    monkeypatch.setattr(storage_paths_module, "THREADS_DIR", tmp_path / "threads")
    monkeypatch.setattr(storage_paths_module, "CACHE_THREADS_DIR", tmp_path / "cache" / "threads")
    monkeypatch.setattr(storage_paths_module, "CACHE_RUNTIME_SKILLS_DIR", tmp_path / "cache" / "runtime-skills")

    class FakeSkillService:
        async def list_runtime_skill_sources_async(self, user_id):
            return sources

    # 快照模块对 skill_service 是函数内延迟 import，需 patch 真实服务实例的方法。
    monkeypatch.setattr(
        skill_service_module.skill_service,
        "list_runtime_skill_sources_async",
        FakeSkillService().list_runtime_skill_sources_async,
    )


def test_build_snapshot_reuses_when_sources_unchanged(tmp_path, monkeypatch):
    src_root = tmp_path / "mgmt"
    sources = [("skill-a", _make_skill(src_root, "skill-a"))]
    _patch_env(monkeypatch, tmp_path, sources)

    target = asyncio.run(snapshot_module.build_runtime_skill_snapshot_async("u1", "c1"))
    assert (target / "skill-a" / "SKILL.md").is_file()

    # 管理域未变化：直接复用快照，不再复制
    copy_calls: list[str] = []
    real_copy = snapshot_module._copy_skill_dir

    def counting_copy(src, dst):
        copy_calls.append(src.name)
        return real_copy(src, dst)

    monkeypatch.setattr(snapshot_module, "_copy_skill_dir", counting_copy)
    again = asyncio.run(snapshot_module.build_runtime_skill_snapshot_async("u1", "c1"))
    assert again == target
    assert copy_calls == []


def test_shared_snapshot_reused_across_conversations_and_users(tmp_path, monkeypatch):
    """同一用户同一技能集合共享目录；不同用户不共享。"""
    skill_dir = _make_skill(tmp_path / "mgmt", "skill-a")
    _patch_env(monkeypatch, tmp_path, [("skill-a", skill_dir)])

    first = asyncio.run(snapshot_module.build_runtime_skill_snapshot_async("u1", "c1"))
    second = asyncio.run(snapshot_module.build_runtime_skill_snapshot_async("u1", "c2"))
    other_user = asyncio.run(snapshot_module.build_runtime_skill_snapshot_async("u2", "c1"))

    assert first == second
    assert first != other_user
    assert first.is_relative_to(tmp_path / "cache" / "runtime-skills" / "u1")


def test_runtime_skills_dir_is_pinned_for_one_runtime(tmp_path, monkeypatch):
    """同一 Runtime 内固定一个技能目录，不因技能变化中途切换。"""
    skill_dir = _make_skill(tmp_path / "mgmt", "skill-a")
    _patch_env(monkeypatch, tmp_path, [("skill-a", skill_dir)])
    workspace_root = tmp_path / "threads" / "u1" / "2026-11-11" / "c1" / "user-data"
    workspace_root.mkdir(parents=True)
    runtime = ConversationWorkspaceRuntime(workspace_root=str(workspace_root), user_id="u1")

    first = asyncio.run(runtime.ensure_runtime_skills_async())
    (skill_dir / "SKILL.md").write_text("# changed", encoding="utf-8")
    second = asyncio.run(runtime.ensure_runtime_skills_async())
    fresh_runtime = ConversationWorkspaceRuntime(workspace_root=str(workspace_root), user_id="u1")
    latest = asyncio.run(fresh_runtime.ensure_runtime_skills_async())

    assert first == second
    assert latest == first
    assert (latest / "skill-a" / "SKILL.md").read_text(encoding="utf-8") == "# changed"


def test_build_snapshot_rebuilds_when_skill_content_changes(tmp_path, monkeypatch):
    skill_dir = _make_skill(tmp_path / "mgmt", "skill-a")
    _patch_env(monkeypatch, tmp_path, [("skill-a", skill_dir)])

    target = asyncio.run(snapshot_module.build_runtime_skill_snapshot_async("u1", "c1"))
    assert (target / "skill-a" / "SKILL.md").read_text(encoding="utf-8") == "# skill"

    # 管理域技能内容变更：同一用户目录重新构建为最新内容
    (skill_dir / "SKILL.md").write_text("# skill v2", encoding="utf-8")
    rebuilt = asyncio.run(snapshot_module.build_runtime_skill_snapshot_async("u1", "c1"))
    assert rebuilt == target
    assert (rebuilt / "skill-a" / "SKILL.md").read_text(encoding="utf-8") == "# skill v2"


def test_build_snapshot_rebuilds_when_skill_added_or_removed(tmp_path, monkeypatch):
    src_root = tmp_path / "mgmt"
    sources = [("skill-a", _make_skill(src_root, "skill-a"))]
    _patch_env(monkeypatch, tmp_path, sources)

    target = asyncio.run(snapshot_module.build_runtime_skill_snapshot_async("u1", "c1"))

    # 新增技能：重建后纳入
    sources.append(("skill-b", _make_skill(src_root, "skill-b")))
    target = asyncio.run(snapshot_module.build_runtime_skill_snapshot_async("u1", "c1"))
    assert (target / "skill-b" / "SKILL.md").is_file()

    # 移除技能：重建后剔除
    sources.pop()
    target = asyncio.run(snapshot_module.build_runtime_skill_snapshot_async("u1", "c1"))
    assert not (target / "skill-b").exists()


def test_build_snapshot_skips_symlinks(tmp_path, monkeypatch):
    skill_dir = _make_skill(tmp_path / "mgmt", "skill-a")
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    (skill_dir / "link.txt").symlink_to(outside)
    _patch_env(monkeypatch, tmp_path, [("skill-a", skill_dir)])

    target = asyncio.run(snapshot_module.build_runtime_skill_snapshot_async("u1", "c1"))
    assert not (target / "skill-a" / "link.txt").exists()


def test_build_snapshot_sweeps_stale_temp_dirs(tmp_path, monkeypatch):
    _patch_env(monkeypatch, tmp_path, [("skill-a", _make_skill(tmp_path / "mgmt", "skill-a"))])
    conv_dir = _conv_dir(tmp_path)
    conv_dir.mkdir(parents=True)
    stale_building = conv_dir / "runtime-skills.building-abc"
    stale_old = conv_dir / "runtime-skills.old-def"
    stale_building.mkdir()
    stale_old.mkdir()
    expired = time.time() - snapshot_module.SNAPSHOT_TMP_TTL_SECONDS - 60
    for stale in (stale_building, stale_old):
        os.utime(stale, (expired, expired))
    target = conv_dir / "runtime-skills"

    asyncio.run(snapshot_module.build_runtime_skill_snapshot_async("u1", "c1", target_dir=target))
    assert not stale_building.exists()
    assert not stale_old.exists()
    assert target.is_dir()


def test_build_snapshot_keeps_fresh_sibling_temp_dirs(tmp_path, monkeypatch):
    """别的会话正在构建的临时目录不能被按前缀清扫掉。"""
    _patch_env(monkeypatch, tmp_path, [("skill-a", _make_skill(tmp_path / "mgmt", "skill-a"))])
    conv_dir = _conv_dir(tmp_path)
    conv_dir.mkdir(parents=True)
    active = conv_dir / "runtime-skills.building-other-session"
    active.mkdir()
    (active / "in-progress.txt").write_text("x", encoding="utf-8")
    target = conv_dir / "runtime-skills"

    asyncio.run(snapshot_module.build_runtime_skill_snapshot_async("u1", "c1", target_dir=target))

    assert active.is_dir()
    assert (active / "in-progress.txt").read_text(encoding="utf-8") == "x"
    assert (target / "skill-a" / "SKILL.md").is_file()


def test_build_snapshot_serializes_concurrent_conversations(tmp_path, monkeypatch):
    """同一用户两个会话同时首建共享快照：都不报错，且落到同一可用目录。"""
    _patch_env(monkeypatch, tmp_path, [("skill-a", _make_skill(tmp_path / "mgmt", "skill-a"))])

    async def _build_both():
        return await asyncio.gather(
            snapshot_module.build_runtime_skill_snapshot_async("u1", "c1"),
            snapshot_module.build_runtime_skill_snapshot_async("u1", "c2"),
        )

    first, second = asyncio.run(_build_both())
    assert first == second
    assert (first / "skill-a" / "SKILL.md").is_file()
    assert snapshot_module.is_runtime_skill_snapshot_ready(first)
    assert not [item for item in first.parent.iterdir() if item.name.startswith("runtime-skills.building-")]


def test_build_snapshot_failure_cleans_tmp(tmp_path, monkeypatch):
    _patch_env(monkeypatch, tmp_path, [("missing-skill", tmp_path / "mgmt" / "missing-skill")])

    with pytest.raises(FileNotFoundError):
        asyncio.run(snapshot_module.build_runtime_skill_snapshot_async("u1", "c1"))

    conv_dir = _conv_dir(tmp_path)
    leftovers = (
        [p for p in conv_dir.iterdir() if p.name.startswith("runtime-skills.building-")] if conv_dir.is_dir() else []
    )
    assert leftovers == []


def test_build_snapshot_upgrades_legacy_snapshot_without_fingerprint(tmp_path, monkeypatch):
    """早期无指纹的存量快照：下次运行自动重建对齐（写入指纹、纳入当前可见技能）。"""
    _patch_env(monkeypatch, tmp_path, [("skill-a", _make_skill(tmp_path / "mgmt", "skill-a"))])
    conv_dir = _conv_dir(tmp_path)
    existing = conv_dir / "runtime-skills"
    existing.mkdir(parents=True)
    (existing / "skill-old").mkdir()  # 早期快照：非空但标记无指纹
    (existing / snapshot_module.SNAPSHOT_READY_MARKER).touch()

    target = asyncio.run(snapshot_module.build_runtime_skill_snapshot_async("u1", "c1", target_dir=existing))
    assert target == existing
    assert (target / "skill-a").is_dir()
    assert not (target / "skill-old").exists()
    # 升级后再运行即走指纹复用路径
    assert snapshot_module._read_snapshot_fingerprints(target) is not None


def test_build_snapshot_populates_precreated_empty_dir(tmp_path, monkeypatch):
    _patch_env(monkeypatch, tmp_path, [("skill-a", _make_skill(tmp_path / "mgmt", "skill-a"))])
    conv_dir = _conv_dir(tmp_path)
    target = conv_dir / "runtime-skills"
    target.mkdir(parents=True)  # 兼容测试：显式预建空目录

    built = asyncio.run(snapshot_module.build_runtime_skill_snapshot_async("u1", "c1", target_dir=target))
    assert (built / "skill-a" / "SKILL.md").is_file()


def test_build_snapshot_empty_sources_are_frozen(tmp_path, monkeypatch):
    _patch_env(monkeypatch, tmp_path, [])

    target = asyncio.run(snapshot_module.build_runtime_skill_snapshot_async("u1", "c1"))
    assert (target / snapshot_module.SNAPSHOT_READY_MARKER).is_file()
    assert snapshot_module.list_snapshot_skill_names(target) == []

    # 管理域发布系统技能后，下次运行重建快照纳入新技能
    _patch_env(monkeypatch, tmp_path, [("skill-a", _make_skill(tmp_path / "mgmt", "skill-a"))])
    target = asyncio.run(snapshot_module.build_runtime_skill_snapshot_async("u1", "c1"))
    assert (target / "skill-a" / "SKILL.md").is_file()


def test_snapshot_fingerprint_roundtrip_and_change_detection(tmp_path, monkeypatch):
    """指纹计算对内容/mtime 敏感，标记读写往返一致。"""
    skill_dir = _make_skill(tmp_path / "mgmt", "skill-a")
    sources = [("skill-a", skill_dir)]

    fingerprints = snapshot_module.compute_skill_fingerprints(sources)
    assert set(fingerprints) == {"skill-a"}

    # 同一内容重复计算指纹稳定
    assert snapshot_module.compute_skill_fingerprints(sources) == fingerprints

    # 内容变化 → 指纹变化
    (skill_dir / "SKILL.md").write_text("# changed", encoding="utf-8")
    assert snapshot_module.compute_skill_fingerprints(sources) != fingerprints

    # 源目录缺失 → 占位指纹，不抛异常
    missing = snapshot_module.compute_skill_fingerprints([("gone", tmp_path / "nope")])
    assert set(missing) == {"gone"}

    # 标记写入/读取往返；损坏标记降级为 None
    snapshot_module._write_ready_marker(tmp_path, fingerprints)
    assert snapshot_module._read_snapshot_fingerprints(tmp_path) == fingerprints
    (tmp_path / snapshot_module.SNAPSHOT_READY_MARKER).write_text("not-json", encoding="utf-8")
    assert snapshot_module._read_snapshot_fingerprints(tmp_path) is None


def test_list_snapshot_skill_metadata_uses_runtime_directory_name(tmp_path):
    snapshot_dir = tmp_path / "runtime-skills"
    skill_dir = snapshot_dir / "runtime-name"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: stale-management-name\ntitle: Runtime title\ndescription: Runtime description\n---\n# skill\n",
        encoding="utf-8",
    )
    invalid_dir = snapshot_dir / "missing-frontmatter"
    invalid_dir.mkdir()
    (invalid_dir / "SKILL.md").write_text("# skill\n", encoding="utf-8")

    skills = snapshot_module.list_snapshot_skill_metadata(snapshot_dir)

    assert skills == [
        {
            "name": "missing-frontmatter",
            "title": "missing-frontmatter",
            "description": "",
            "domain": "",
            "category": "",
        },
        {
            "name": "runtime-name",
            "title": "Runtime title",
            "description": "Runtime description",
            "domain": "",
            "category": "",
        },
    ]


def test_list_snapshot_skill_names_missing_dir(tmp_path):
    assert snapshot_module.list_snapshot_skill_names(tmp_path / "nope") == []


def test_rmtree_uses_onerror_on_python_older_than_312(tmp_path):
    """3.12 以下走 onerror 回调：同一份兼容代码两条分支都要兜住。"""
    target = tmp_path / "legacy"
    target.mkdir()
    (target / "f.txt").write_text("x", encoding="utf-8")
    real = sys.version_info
    try:
        sys.version_info = (3, 11, 0)
        snapshot_module._rmtree(target)
    finally:
        sys.version_info = real
    assert not target.exists()


def test_copy_skill_dir_replaces_existing_destination(tmp_path):
    src = _make_skill(tmp_path / "src", "skill-a")
    dst = tmp_path / "dst" / "skill-a"
    dst.mkdir(parents=True)
    (dst / "stale.txt").write_text("old", encoding="utf-8")

    snapshot_module._copy_skill_dir(src, dst)

    assert (dst / "SKILL.md").is_file()
    assert not (dst / "stale.txt").exists()


def test_on_rm_error_recovers_locked_file(tmp_path):
    locked_file = tmp_path / "f.txt"
    locked_file.write_text("x", encoding="utf-8")
    locked_file.chmod(0o400)

    snapshot_module._on_rm_error(os.chmod, str(locked_file), None)
    assert not locked_file.exists()


def test_on_rm_error_swallows_unrecoverable_path(tmp_path):
    """路径已不存在时 chmod 直接失败：只记日志，不向外抛。"""
    snapshot_module._on_rm_error(os.chmod, str(tmp_path / "gone"), None)


def test_remove_tree_swallows_os_error(tmp_path, monkeypatch):
    def _boom(path, **_kwargs):
        raise OSError("read-only fs")

    monkeypatch.setattr(snapshot_module, "_rmtree", _boom)
    snapshot_module._remove_tree(tmp_path)


def test_build_snapshot_removes_preexisting_tmp_dir(tmp_path, monkeypatch):
    """固定 uuid，验证构建前会清掉同名的残留 building 临时目录。"""
    monkeypatch.setattr(storage_paths_module, "THREADS_DIR", tmp_path / "threads")
    monkeypatch.setattr(storage_paths_module, "CACHE_THREADS_DIR", tmp_path / "cache" / "threads")
    conv_dir = _conv_dir(tmp_path)
    conv_dir.mkdir(parents=True)

    class FakeSkillService:
        async def list_runtime_skill_sources_async(self, user_id):
            return []

    monkeypatch.setattr(
        skill_service_module.skill_service,
        "list_runtime_skill_sources_async",
        FakeSkillService().list_runtime_skill_sources_async,
    )
    monkeypatch.setattr(snapshot_module, "uuid4", lambda: SimpleNamespace(hex="fixed"))

    stale_tmp = conv_dir / "runtime-skills.building-fixed"
    stale_tmp.mkdir(parents=True)
    (stale_tmp / "leftover.txt").write_text("x", encoding="utf-8")

    target = asyncio.run(
        snapshot_module.build_runtime_skill_snapshot_async("u1", "c1", target_dir=conv_dir / "runtime-skills")
    )
    assert target.is_dir()
    assert not stale_tmp.exists()


def test_build_snapshot_reuses_snapshot_completed_during_populate(tmp_path, monkeypatch):
    """填充期间并发请求先完成构建时：丢弃本次临时目录，复用已有快照。"""
    monkeypatch.setattr(storage_paths_module, "THREADS_DIR", tmp_path / "threads")
    monkeypatch.setattr(storage_paths_module, "CACHE_THREADS_DIR", tmp_path / "cache" / "threads")
    conv_dir = _conv_dir(tmp_path)
    conv_dir.mkdir(parents=True)
    target = conv_dir / "runtime-skills"

    class FakeSkillService:
        async def list_runtime_skill_sources_async(self, user_id):
            target.mkdir(parents=True, exist_ok=True)
            (target / "skill-other").mkdir()
            return [("skill-a", _make_skill(tmp_path / "mgmt", "skill-a"))]

    monkeypatch.setattr(
        skill_service_module.skill_service,
        "list_runtime_skill_sources_async",
        FakeSkillService().list_runtime_skill_sources_async,
    )

    result = asyncio.run(snapshot_module.build_runtime_skill_snapshot_async("u1", "c1", target_dir=target))
    assert result == target
    assert (target / "skill-other").is_dir()
    assert not (target / "skill-a").exists()
    leftovers = [p.name for p in conv_dir.iterdir() if p.name.startswith("runtime-skills.building-")]
    assert leftovers == []


def _make_ready_snapshot(target: Path, fingerprints: dict[str, str]) -> None:
    target.mkdir(parents=True, exist_ok=True)
    (target / "skill-b").mkdir(exist_ok=True)
    snapshot_module._write_ready_marker(target, fingerprints)


def _make_building_tmp(target: Path) -> Path:
    tmp = target.with_name(f"{target.name}.building-test")
    tmp.mkdir(parents=True)
    (tmp / "skill-a").mkdir()
    return tmp


def _capture_logger(monkeypatch) -> tuple[list[str], list[str]]:
    infos: list[str] = []
    warnings: list[str] = []
    monkeypatch.setattr(
        snapshot_module,
        "logger",
        SimpleNamespace(
            info=lambda *args: infos.append(str(args[0])),
            warning=lambda *args: warnings.append(str(args[0])),
            debug=lambda *args: None,
        ),
    )
    return infos, warnings


def _hold_snapshot_lock(target: Path) -> int:
    """另一个 fd 持有同一把 flock：flock 按 open file description 互斥，同进程也生效。"""
    lock_path = snapshot_module._snapshot_lock_path(target)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def _patch_short_lock_timeout(monkeypatch) -> None:
    monkeypatch.setattr(snapshot_module, "SNAPSHOT_LOCK_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(snapshot_module, "SNAPSHOT_LOCK_POLL_SECONDS", 0.01)


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl.flock 仅 Unix 平台可用")
def test_commit_snapshot_lock_timeout_reuses_existing_snapshot(tmp_path, monkeypatch):
    """持锁者卡住：降级复用现有快照，不覆盖正在被读的目录、不阻断本轮 run。"""
    target = tmp_path / "runtime-skills" / "u1"
    stale = {"skill-b": "fp-b"}
    _make_ready_snapshot(target, stale)
    tmp = _make_building_tmp(target)
    _patch_short_lock_timeout(monkeypatch)
    _, warnings = _capture_logger(monkeypatch)

    held = _hold_snapshot_lock(target)
    try:
        reused, old_target = snapshot_module._commit_snapshot(target, tmp, {"skill-a": "fp-a"}, False)
    finally:
        fcntl.flock(held, fcntl.LOCK_UN)
        os.close(held)

    assert (reused, old_target) == (True, None)
    assert snapshot_module._read_snapshot_fingerprints(target) == stale
    assert not (target / "skill-a").exists()
    assert tmp.is_dir()  # 由调用方清理，提交阶段不能动它
    assert any("等待跨进程锁超时" in message for message in warnings)


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl.flock 仅 Unix 平台可用")
def test_commit_snapshot_lock_timeout_fails_without_snapshot(tmp_path, monkeypatch):
    """没有可复用快照时超时必须显式失败，不能挂死也不能留下半成品 target。"""
    target = tmp_path / "runtime-skills" / "u1"
    tmp = _make_building_tmp(target)
    _patch_short_lock_timeout(monkeypatch)

    held = _hold_snapshot_lock(target)
    try:
        with pytest.raises(snapshot_module.SnapshotLockTimeoutError):
            snapshot_module._commit_snapshot(target, tmp, {"skill-a": "fp-a"}, False)
    finally:
        fcntl.flock(held, fcntl.LOCK_UN)
        os.close(held)
    assert not target.exists()


def test_commit_snapshot_does_not_overwrite_different_fingerprint(tmp_path, monkeypatch):
    """并发请求先提交不同指纹：先提交者赢，后提交者复用其视图并记录告警。"""
    target = tmp_path / "runtime-skills" / "u1"
    committed = {"skill-b": "fp-b"}
    _make_ready_snapshot(target, committed)
    tmp = _make_building_tmp(target)
    _, warnings = _capture_logger(monkeypatch)

    reused, old_target = snapshot_module._commit_snapshot(target, tmp, {"skill-a": "fp-a"}, False)

    assert (reused, old_target) == (True, None)
    assert snapshot_module._read_snapshot_fingerprints(target) == committed
    assert not (target / "skill-a").exists()
    assert any("不同指纹" in message for message in warnings)


def test_commit_snapshot_logs_legacy_snapshot_reuse(tmp_path, monkeypatch):
    """填充期间出现的无指纹存量快照：兼容复用，但必须与「不同指纹」区分记录。"""
    target = tmp_path / "runtime-skills" / "u1"
    target.mkdir(parents=True)
    (target / "skill-old").mkdir()
    (target / snapshot_module.SNAPSHOT_READY_MARKER).touch()
    tmp = _make_building_tmp(target)
    infos, warnings = _capture_logger(monkeypatch)

    reused, old_target = snapshot_module._commit_snapshot(target, tmp, {"skill-a": "fp-a"}, False)

    assert (reused, old_target) == (True, None)
    assert (target / "skill-old").is_dir()
    assert any("无指纹存量" in message for message in infos)
    assert not warnings


def test_build_snapshot_rebuilds_mismatched_fingerprint_next_round(tmp_path, monkeypatch):
    """复用了不同指纹的视图后，下一轮 was_ready=True 会按当前管理域自动重建收敛。"""
    source = _make_skill(tmp_path / "mgmt", "skill-a")
    _patch_env(monkeypatch, tmp_path, [("skill-a", source)])
    target = tmp_path / "cache" / "runtime-skills" / "u1"
    _make_ready_snapshot(target, {"skill-b": "fp-b"})

    built = asyncio.run(snapshot_module.build_runtime_skill_snapshot_async("u1", "c1"))

    assert built == target
    assert (built / "skill-a" / "SKILL.md").is_file()
    assert not (built / "skill-b").exists()
    assert snapshot_module._read_snapshot_fingerprints(built) == snapshot_module.compute_skill_fingerprints(
        [("skill-a", source)]
    )


def test_acquire_snapshot_lock_reraises_unexpected_errno(monkeypatch):
    """非锁竞争类 errno 必须直接抛出，不能被轮询误当成「别人持锁」。"""

    def _boom(_fd, _operation):
        raise OSError(errno.EINTR, "interrupted")

    monkeypatch.setattr(snapshot_module.fcntl, "flock", _boom)
    with pytest.raises(OSError) as exc_info:
        snapshot_module._acquire_snapshot_lock(0, timeout=0.01)
    assert exc_info.value.errno == errno.EINTR


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl.flock 仅 Unix 平台可用")
def test_acquire_snapshot_lock_retries_until_released(tmp_path, monkeypatch):
    """锁被短暂持有时：LOCK_NB 轮询在释放后成功拿到，不会误判超时。"""
    target = tmp_path / "runtime-skills" / "u1"
    held = _hold_snapshot_lock(target)
    fd = os.open(snapshot_module._snapshot_lock_path(target), os.O_CREAT | os.O_RDWR, 0o600)
    monkeypatch.setattr(snapshot_module, "SNAPSHOT_LOCK_POLL_SECONDS", 0.01)

    def _release() -> None:
        time.sleep(0.05)
        fcntl.flock(held, fcntl.LOCK_UN)
        os.close(held)

    releaser = threading.Thread(target=_release)
    releaser.start()
    try:
        assert snapshot_module._acquire_snapshot_lock(fd, timeout=2.0) is True
    finally:
        releaser.join(timeout=2.0)
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _cross_process_writer_script() -> str:
    """子进程：同一缓存根、同一用户，两个进程强制同时冲进提交阶段。"""
    return textwrap.dedent(
        """
        import asyncio
        import os
        import sys
        import time
        from pathlib import Path

        from apps.chat_data_app.agent.contracts import skill_source
        from apps.chat_data_app.agent.workspace import runtime_skill_snapshot as snap
        from utils import storage_paths as sp

        cache_root = Path(os.environ["DA_CACHE"])
        sp.CACHE_RUNTIME_SKILLS_DIR = cache_root / "runtime-skills"
        sync_dir = Path(os.environ["DA_SYNC"])
        cid = sys.argv[1]

        class Source:
            async def list_runtime_skill_sources_async(self, user_id):
                return [("skill-a", Path(os.environ["DA_SKILL_SRC"]))]

        skill_source.register_skill_snapshot_source(Source())

        original_commit = snap._commit_snapshot

        def synced_commit(*args, **kwargs):
            (sync_dir / ("ready_" + cid)).touch()
            deadline = time.time() + 30
            while len(list(sync_dir.glob("ready_*"))) < 2 and time.time() < deadline:
                time.sleep(0.01)
            reused, old_target = original_commit(*args, **kwargs)
            print("REUSE" if reused else "COMMIT", flush=True)
            return reused, old_target

        snap._commit_snapshot = synced_commit

        target = asyncio.run(snap.build_runtime_skill_snapshot_async("u1", cid))
        assert snap.is_runtime_skill_snapshot_ready(target), str(target)
        assert (target / "skill-a" / "SKILL.md").is_file(), str(target)
        print(str(target))
        """
    ).strip()


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl.flock 仅 Unix 平台可用")
def test_build_snapshot_serializes_across_processes(tmp_path):
    """生产默认 4 个 Gunicorn worker：两进程同时重建同一用户快照都必须成功。

    用 barrier 让两个进程同时进入提交阶段，确保这条用例真的能区分「有跨进程锁」
    与「只有进程内锁」——无锁时必然出现 rename 冲突（errno 2 / 66）。
    """
    skill_dir = _make_skill(tmp_path / "mgmt", "skill-a")
    cache_root = tmp_path / "shared-cache"
    sync_dir = tmp_path / "sync"
    sync_dir.mkdir()
    script = tmp_path / "snapshot_writer.py"
    script.write_text(_cross_process_writer_script(), encoding="utf-8")
    server_root = Path(snapshot_module.__file__).resolve().parents[4]
    env = {
        **os.environ,
        "PYTHONPATH": str(server_root) + os.pathsep + os.environ.get("PYTHONPATH", ""),
        "CHAT_DATA_HOME": str(tmp_path / "chat_data"),
        "CHAT_DATA_CACHE_HOME": str(cache_root),
        "DA_CACHE": str(cache_root),
        "DA_SKILL_SRC": str(skill_dir),
        "DA_SYNC": str(sync_dir),
    }
    procs = [
        subprocess.Popen(
            [sys.executable, str(script), f"c{index}"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for index in range(2)
    ]
    outcomes: list[str] = []
    for index, proc in enumerate(procs):
        stdout, stderr = proc.communicate(timeout=90)
        assert proc.returncode == 0, f"writer {index} failed: {stderr.decode('utf-8', 'replace')}"
        outcomes.extend(line for line in stdout.decode("utf-8", "replace").splitlines() if line in ("COMMIT", "REUSE"))
    # 恰好一个进程完成提交，另一个在锁内发现新指纹后复用：这正是跨进程串行化的证据
    assert sorted(outcomes) == ["COMMIT", "REUSE"]

    target = cache_root / "runtime-skills" / "u1"
    assert snapshot_module.is_runtime_skill_snapshot_ready(target)
    assert (target / "skill-a" / "SKILL.md").is_file()
    assert not [item for item in target.parent.iterdir() if ".building-" in item.name]
    # 锁文件必须保留：删掉会换 inode，新旧进程锁在不同对象上，互斥静默失效
    assert list(target.parent.glob(".*.lock"))


def test_build_snapshot_lock_path_is_stable_and_ignores_user_id_chars(tmp_path, monkeypatch):
    """锁文件按目标路径摘要命名，同一目录恒定、不同用户不串锁。"""
    _patch_env(monkeypatch, tmp_path, [])
    root = tmp_path / "cache" / "runtime-skills"
    first = snapshot_module._snapshot_lock_path(root / "u1")
    assert first == snapshot_module._snapshot_lock_path(root / "u1")
    assert first != snapshot_module._snapshot_lock_path(root / "u2")
    assert first.parent == root
    assert first.name.startswith(".") and first.name.endswith(".lock")


def test_resolve_readonly_skill_path_retries_transient_snapshot_gap(tmp_path, monkeypatch):
    """跨进程重建的两次 rename 之间目录瞬时不可用：重建后重试一次。"""
    workspace_root = tmp_path / "threads" / "u1" / "2026-11-11" / "c1" / "user-data"
    workspace_root.mkdir(parents=True)
    runtime = ConversationWorkspaceRuntime(workspace_root=str(workspace_root), user_id="u1")
    runtime._runtime_skills_dir = tmp_path / "snapshot"
    calls = {"ensure": 0, "resolve": 0}

    async def _noop():
        calls["ensure"] += 1

    def _flaky_resolve(root, path):
        calls["resolve"] += 1
        if calls["resolve"] == 1:
            raise ValueError("技能不存在或无权限访问: skill-a")
        return root / "skill-a" / "SKILL.md", path

    monkeypatch.setattr(runtime, "_ensure_snapshot_async", _noop)
    monkeypatch.setattr(runtime_module, "is_runtime_skill_snapshot_ready", lambda _path: False)
    monkeypatch.setattr(runtime_module, "resolve_skill_snapshot_path", _flaky_resolve)

    resolved, display = asyncio.run(runtime.resolve_readonly_skill_path_async("/mnt/skills/skill-a/SKILL.md"))
    assert calls == {"ensure": 2, "resolve": 2}
    assert resolved.name == "SKILL.md"
    assert display == "/mnt/skills/skill-a/SKILL.md"


def test_resolve_readonly_skill_path_does_not_retry_real_permission_error(tmp_path, monkeypatch):
    """目录就绪时的解析失败是真越权/技能不存在，不能被当成竞态吞掉。"""
    workspace_root = tmp_path / "threads" / "u1" / "2026-11-11" / "c1" / "user-data"
    workspace_root.mkdir(parents=True)
    runtime = ConversationWorkspaceRuntime(workspace_root=str(workspace_root), user_id="u1")
    runtime._runtime_skills_dir = tmp_path / "snapshot"
    calls = {"ensure": 0, "resolve": 0}

    async def _noop():
        calls["ensure"] += 1

    def _always_fail(root, path):
        calls["resolve"] += 1
        raise ValueError("技能不存在或无权限访问: skill-a")

    monkeypatch.setattr(runtime, "_ensure_snapshot_async", _noop)
    monkeypatch.setattr(runtime_module, "is_runtime_skill_snapshot_ready", lambda _path: True)
    monkeypatch.setattr(runtime_module, "resolve_skill_snapshot_path", _always_fail)

    with pytest.raises(ValueError, match="技能不存在"):
        asyncio.run(runtime.resolve_readonly_skill_path_async("/mnt/skills/skill-a/SKILL.md"))
    assert calls == {"ensure": 1, "resolve": 1}
