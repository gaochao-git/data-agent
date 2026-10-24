"""覆盖率补测：``workspace/runtime_skill_snapshot.py`` 与 ``workspace/workspace_ops.py``。

快照侧覆盖：快照中缺少 SKILL.md 的技能被跳过、指纹标记结构非法降级为 None、
``_on_rm_error`` 处理目录、固定 uuid 时清理同名 building 临时目录。
沙箱侧覆盖：探测目标抛出非「不存在」异常时向上传播、内部语义模型写入/删除的
二次路径校验（仅能通过 monkeypatch 边界层触发）。
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.chat_data_app.agent.skills import skill_service as skill_service_module
from apps.chat_data_app.agent.workspace import runtime_skill_snapshot as snapshot_module
from apps.chat_data_app.agent.workspace import workspace_ops as we_ops
from apps.chat_data_app.agent.workspace.workspace_sandbox_api import (
    SandboxExecutionSession,
    SandboxExecutionSessionRef,
)

# --------------------------------------------------------------------------- #
# runtime_skill_snapshot
# --------------------------------------------------------------------------- #


def _make_skill(root: Path, name: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# skill", encoding="utf-8")
    return skill_dir


def _patch_skill_sources(monkeypatch, sources):
    class FakeSkillService:
        async def list_runtime_skill_sources_async(self, user_id):
            return sources

    monkeypatch.setattr(
        skill_service_module.skill_service,
        "list_runtime_skill_sources_async",
        FakeSkillService().list_runtime_skill_sources_async,
    )


def test_list_snapshot_skill_metadata_skips_dirs_without_skill_md(tmp_path):
    snapshot_dir = tmp_path / "runtime-skills"
    valid = snapshot_dir / "valid-skill"
    valid.mkdir(parents=True)
    (valid / "SKILL.md").write_text(
        "---\nname: valid-skill\ntitle: 有效技能\n---\n# valid",
        encoding="utf-8",
    )
    (snapshot_dir / "no-skill-md").mkdir()

    entries = snapshot_module.list_snapshot_skill_metadata(snapshot_dir)

    assert [entry["name"] for entry in entries] == ["valid-skill"]
    assert entries[0]["title"] == "有效技能"


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"skills": ["not-a-dict"]}, id="skills-not-dict"),
        pytest.param({"skills": {"skill-a": 1}}, id="fingerprint-not-str"),
        pytest.param([], id="marker-not-dict"),
    ],
)
def test_read_snapshot_fingerprints_degrades_for_invalid_structure(tmp_path, payload):
    (tmp_path / snapshot_module.SNAPSHOT_READY_MARKER).write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )

    assert snapshot_module._read_snapshot_fingerprints(tmp_path) is None


def test_on_rm_error_recovers_directory(tmp_path):
    """rmtree 回调收到目录路径时走目录分支（syspath 已是可写目录，重建删除成功）。"""
    target = tmp_path / "locked-dir"
    target.mkdir()

    snapshot_module._on_rm_error(os.chmod, str(target), None)

    assert not target.exists()


def test_build_snapshot_removes_preexisting_tmp_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot_module, "uuid4", lambda: SimpleNamespace(hex="fixed"))
    # 预清扫失效（如父目录不可写）时，构建阶段仍须清掉同名 building 目录
    monkeypatch.setattr(snapshot_module, "_sweep_stale_siblings", lambda target: None)
    _patch_skill_sources(monkeypatch, [("skill-a", _make_skill(tmp_path / "mgmt", "skill-a"))])

    target = tmp_path / "conv" / "runtime-skills"
    stale_tmp = target.with_name("runtime-skills.building-fixed")
    stale_tmp.mkdir(parents=True)
    (stale_tmp / "leftover.txt").write_text("x", encoding="utf-8")

    built = asyncio.run(
        snapshot_module.build_runtime_skill_snapshot_async("u1", "c1", target_dir=target),
    )

    assert built == target
    assert not stale_tmp.exists()
    assert (target / "skill-a" / "SKILL.md").is_file()


# --------------------------------------------------------------------------- #
# workspace_ops（沙箱 Files API 边界）
# --------------------------------------------------------------------------- #


class _NoopSandbox:
    """仅满足 require_sandbox_session 的最小 sandbox 占位。"""


def _session_ref(tmp_path: Path) -> SandboxExecutionSessionRef:
    session = SandboxExecutionSession(
        sandbox=_NoopSandbox(),
        sandbox_id="sbx_cov",
        workspace_root=str(tmp_path),
    )
    ref = SandboxExecutionSessionRef()
    ref.current = session
    return ref


@pytest.fixture()
def noop_persistence(monkeypatch):
    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(we_ops, "_recover_pending_persistence", _noop)
    monkeypatch.setattr(we_ops, "sync_sandbox_working_trees", _noop)


def test_sandbox_create_dir_propagates_unexpected_probe_error(tmp_path, monkeypatch, noop_persistence):
    async def _target(**_kwargs):
        return "/mnt/user-data/workspace/newdir"

    async def _probe(**_kwargs):
        raise PermissionError("remote permission denied")

    monkeypatch.setattr(we_ops, "_sandbox_mutable_path", _target)
    monkeypatch.setattr(we_ops, "_sandbox_path_kind", _probe)

    with pytest.raises(PermissionError, match="permission denied"):
        asyncio.run(we_ops.sandbox_create_dir(session_ref=_session_ref(tmp_path), path="/workspace/newdir"))


def test_sandbox_move_path_propagates_unexpected_probe_error(tmp_path, monkeypatch, noop_persistence):
    async def _target(*, session, file_path):
        return file_path

    async def _probe(*, session, path, entry=None):
        if path.endswith("/dest.txt"):
            raise TimeoutError("remote timeout")
        return "file"

    monkeypatch.setattr(we_ops, "_sandbox_mutable_path", _target)
    monkeypatch.setattr(we_ops, "_sandbox_path_kind", _probe)

    with pytest.raises(TimeoutError, match="remote timeout"):
        asyncio.run(
            we_ops.sandbox_move_path(
                session_ref=_session_ref(tmp_path),
                source_path="/mnt/user-data/workspace/src.txt",
                destination_path="/mnt/user-data/workspace/dest.txt",
            )
        )


def test_resolve_sandbox_target_for_batch_rejects_non_semantic_path(monkeypatch):
    """二次校验：边界层放行后仍拒绝非语义模型路径（防御分支）。"""
    monkeypatch.setattr(we_ops, "sandbox_visible_path", lambda path, **_kwargs: "/mnt/user-data/workspace/notes.txt")

    with pytest.raises(ValueError, match="系统批量写入仅允许"):
        we_ops._resolve_sandbox_target_for_batch(None, "/mnt/user-data/workspace/notes.txt")


def test_delete_internal_text_file_rejects_non_semantic_path(tmp_path, monkeypatch, noop_persistence):
    """二次校验：边界层放行后仍拒绝非语义模型路径（防御分支）。"""
    monkeypatch.setattr(we_ops, "sandbox_visible_path", lambda path, **_kwargs: "/mnt/user-data/workspace/notes.txt")

    with pytest.raises(ValueError, match="系统删除仅允许"):
        asyncio.run(
            we_ops.sandbox_delete_internal_text_file(
                session_ref=_session_ref(tmp_path),
                path="/mnt/user-data/workspace/notes.txt",
            )
        )
