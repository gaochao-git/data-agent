"""runtime_skill_snapshot 快照构建行为测试。"""

import asyncio
from pathlib import Path

import pytest

from apps.chat_data_app.services import runtime_skill_snapshot as snapshot_module
from apps.chat_data_app.services import skill_service as skill_service_module


def _make_skill(root: Path, name: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# skill", encoding="utf-8")
    return skill_dir


def _patch_env(monkeypatch, tmp_path, sources):
    monkeypatch.setattr(snapshot_module, "THREADS_DIR", tmp_path / "threads")

    class FakeSkillService:
        async def list_runtime_skill_sources_async(self, user_id):
            return sources

    # 快照模块对 skill_service 是函数内延迟 import，需 patch 真实服务实例的方法。
    monkeypatch.setattr(
        skill_service_module.skill_service,
        "list_runtime_skill_sources_async",
        FakeSkillService().list_runtime_skill_sources_async,
    )


def test_build_snapshot_freezes_within_conversation(tmp_path, monkeypatch):
    src_root = tmp_path / "mgmt"
    sources = [("skill-a", _make_skill(src_root, "skill-a"))]
    _patch_env(monkeypatch, tmp_path, sources)

    target = asyncio.run(snapshot_module.build_runtime_skill_snapshot_async("u1", "c1"))
    assert (target / "skill-a" / "SKILL.md").is_file()

    # 管理域新增技能后再次调用：会话内冻结，不重建
    sources.append(("skill-b", _make_skill(src_root, "skill-b")))
    again = asyncio.run(snapshot_module.build_runtime_skill_snapshot_async("u1", "c1"))
    assert again == target
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
    conv_dir = tmp_path / "threads" / "u1" / "c1"
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

    with pytest.raises(Exception):
        asyncio.run(snapshot_module.build_runtime_skill_snapshot_async("u1", "c1"))

    conv_dir = tmp_path / "threads" / "u1" / "c1"
    leftovers = [p for p in conv_dir.iterdir() if p.name.startswith("runtime-skills.building-")] if conv_dir.is_dir() else []
    assert leftovers == []


def test_build_snapshot_concurrent_winner_reused(tmp_path, monkeypatch):
    _patch_env(monkeypatch, tmp_path, [("skill-a", _make_skill(tmp_path / "mgmt", "skill-a"))])
    conv_dir = tmp_path / "threads" / "u1" / "c1"
    existing = conv_dir / "runtime-skills"
    existing.mkdir(parents=True)
    (existing / "skill-old").mkdir()  # 已有内容视为并发请求先完成的快照

    # 已存在非空快照：直接复用，不重建
    target = asyncio.run(snapshot_module.build_runtime_skill_snapshot_async("u1", "c1"))
    assert target == existing
    assert not (existing / "skill-a").exists()


def test_build_snapshot_populates_precreated_empty_dir(tmp_path, monkeypatch):
    _patch_env(monkeypatch, tmp_path, [("skill-a", _make_skill(tmp_path / "mgmt", "skill-a"))])
    conv_dir = tmp_path / "threads" / "u1" / "c1"
    (conv_dir / "runtime-skills").mkdir(parents=True)  # ensure_conversation_workspace 预建空目录

    target = asyncio.run(snapshot_module.build_runtime_skill_snapshot_async("u1", "c1"))
    assert (target / "skill-a" / "SKILL.md").is_file()


def test_build_snapshot_empty_sources_not_persisted(tmp_path, monkeypatch):
    _patch_env(monkeypatch, tmp_path, [])

    target = asyncio.run(snapshot_module.build_runtime_skill_snapshot_async("u1", "c1"))
    # 空快照不落盘，下次构建可重试
    assert not target.exists()

    # 系统技能部署后重试可成功
    _patch_env(monkeypatch, tmp_path, [("skill-a", _make_skill(tmp_path / "mgmt", "skill-a"))])
    target = asyncio.run(snapshot_module.build_runtime_skill_snapshot_async("u1", "c1"))
    assert (target / "skill-a" / "SKILL.md").is_file()
