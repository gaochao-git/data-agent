"""runtime_skill_snapshot 快照构建行为测试。"""

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.chat_data_app.agent.workspace import runtime_skill_snapshot as snapshot_module
from apps.chat_data_app.agent.skills import skill_service as skill_service_module
from utils import storage_paths as storage_paths_module


def _make_skill(root: Path, name: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# skill", encoding="utf-8")
    return skill_dir


def _conv_dir(tmp_path: Path, user_id: str = "u1", conversation_id: str = "c1") -> Path:
    """与 conversation_thread_dir 布局一致的会话目录（threads/<uid>/<yyyy-MM-DD>/<cid>）。"""
    date_str = datetime.now().strftime("%Y-%m-%d")
    return tmp_path / "threads" / user_id / date_str / conversation_id


def _patch_env(monkeypatch, tmp_path, sources):
    monkeypatch.setattr(storage_paths_module, "THREADS_DIR", tmp_path / "threads")

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


def test_build_snapshot_rebuilds_when_skill_content_changes(tmp_path, monkeypatch):
    skill_dir = _make_skill(tmp_path / "mgmt", "skill-a")
    _patch_env(monkeypatch, tmp_path, [("skill-a", skill_dir)])

    target = asyncio.run(snapshot_module.build_runtime_skill_snapshot_async("u1", "c1"))
    assert (target / "skill-a" / "SKILL.md").read_text(encoding="utf-8") == "# skill"

    # 管理域技能内容变更：下次运行原子重建，快照跟上最新内容
    (skill_dir / "SKILL.md").write_text("# skill v2", encoding="utf-8")
    rebuilt = asyncio.run(snapshot_module.build_runtime_skill_snapshot_async("u1", "c1"))
    assert rebuilt == target
    assert (target / "skill-a" / "SKILL.md").read_text(encoding="utf-8") == "# skill v2"


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

    asyncio.run(snapshot_module.build_runtime_skill_snapshot_async("u1", "c1"))
    assert not stale_building.exists()
    assert not stale_old.exists()
    assert (conv_dir / "runtime-skills").is_dir()


def test_build_snapshot_failure_cleans_tmp(tmp_path, monkeypatch):
    _patch_env(monkeypatch, tmp_path, [("missing-skill", tmp_path / "mgmt" / "missing-skill")])

    with pytest.raises(FileNotFoundError):
        asyncio.run(snapshot_module.build_runtime_skill_snapshot_async("u1", "c1"))

    conv_dir = _conv_dir(tmp_path)
    leftovers = [p for p in conv_dir.iterdir() if p.name.startswith("runtime-skills.building-")] if conv_dir.is_dir() else []
    assert leftovers == []


def test_build_snapshot_upgrades_legacy_snapshot_without_fingerprint(tmp_path, monkeypatch):
    """早期无指纹的存量快照：下次运行自动重建对齐（写入指纹、纳入当前可见技能）。"""
    _patch_env(monkeypatch, tmp_path, [("skill-a", _make_skill(tmp_path / "mgmt", "skill-a"))])
    conv_dir = _conv_dir(tmp_path)
    existing = conv_dir / "runtime-skills"
    existing.mkdir(parents=True)
    (existing / "skill-old").mkdir()  # 早期快照：非空但标记无指纹
    (existing / snapshot_module.SNAPSHOT_READY_MARKER).touch()

    target = asyncio.run(snapshot_module.build_runtime_skill_snapshot_async("u1", "c1"))
    assert target == existing
    assert (target / "skill-a").is_dir()
    assert not (target / "skill-old").exists()
    # 升级后再运行即走指纹复用路径
    assert snapshot_module._read_snapshot_fingerprints(target) is not None


def test_build_snapshot_populates_precreated_empty_dir(tmp_path, monkeypatch):
    _patch_env(monkeypatch, tmp_path, [("skill-a", _make_skill(tmp_path / "mgmt", "skill-a"))])
    conv_dir = _conv_dir(tmp_path)
    (conv_dir / "runtime-skills").mkdir(parents=True)  # ensure_conversation_workspace 预建空目录

    target = asyncio.run(snapshot_module.build_runtime_skill_snapshot_async("u1", "c1"))
    assert (target / "skill-a" / "SKILL.md").is_file()


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

    target = asyncio.run(snapshot_module.build_runtime_skill_snapshot_async("u1", "c1"))
    assert target.is_dir()
    assert not stale_tmp.exists()


def test_build_snapshot_reuses_snapshot_completed_during_populate(tmp_path, monkeypatch):
    """填充期间并发请求先完成构建时：丢弃本次临时目录，复用已有快照。"""
    monkeypatch.setattr(storage_paths_module, "THREADS_DIR", tmp_path / "threads")
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

    result = asyncio.run(snapshot_module.build_runtime_skill_snapshot_async("u1", "c1"))
    assert result == target
    assert (target / "skill-other").is_dir()
    assert not (target / "skill-a").exists()
    leftovers = [p.name for p in conv_dir.iterdir() if p.name.startswith("runtime-skills.building-")]
    assert leftovers == []
