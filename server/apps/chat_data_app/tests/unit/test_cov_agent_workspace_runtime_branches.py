"""agent/workspace/runtime.py 与 artifact_registry.py 剩余分支。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from apps.chat_data_app.agent.workspace import artifact_registry
from apps.chat_data_app.agent.workspace import runtime as workspace_runtime
from apps.chat_data_app.agent.workspace.workspace_path_policy import WORKSPACE_PATH_POLICY


def _make_workspace_root(root: Path) -> Path:
    for name in workspace_runtime.WORKSPACE_DIR_NAMES:
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


class _AllowAllPolicy:
    """放开逻辑路径校验，专测真实路径边界（正常链路由策略先拦截）。"""

    def parse(self, path: str, **_kwargs: Any) -> Any:
        return WORKSPACE_PATH_POLICY.parse(path)

    def assert_writable(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def assert_readable(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _FakeLogicalPath:
    normalized = "other/file.txt"
    kind = "workspace"
    is_skill = False


class _OutsideVisibleRootsPolicy(_AllowAllPolicy):
    def parse(self, path: str, **_kwargs: Any) -> Any:
        return _FakeLogicalPath()


def test_resolve_workspace_path_rejects_symlink_escape(tmp_path, monkeypatch):
    """符号链接目录把真实路径带出会话根目录时拒绝访问。"""
    root = _make_workspace_root(tmp_path / "root")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("private", encoding="utf-8")
    (root / "workspace" / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="路径越界"):
        workspace_runtime.resolve_workspace_path(
            str(root), "/mnt/user-data/workspace/link/secret.txt", materialize_uploads=False
        )


def test_resolve_workspace_path_rejects_path_outside_visible_roots(tmp_path, monkeypatch):
    """真实路径在会话根内但不在任一可见根下时拒绝。"""
    root = _make_workspace_root(tmp_path / "root")
    (root / "other").mkdir()
    (root / "other" / "file.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(workspace_runtime, "WORKSPACE_PATH_POLICY", _OutsideVisibleRootsPolicy())

    with pytest.raises(ValueError, match="只允许访问"):
        workspace_runtime.resolve_workspace_path(str(root), "other/file.txt", materialize_uploads=False)


def test_resolve_workspace_path_rejects_system_result_write_after_policy(tmp_path, monkeypatch):
    """策略放行后仍拒绝写系统结果目录（真实路径二次校验）。"""
    root = _make_workspace_root(tmp_path / "root")
    monkeypatch.setattr(workspace_runtime, "WORKSPACE_PATH_POLICY", _AllowAllPolicy())

    with pytest.raises(ValueError, match="系统结果目录"):
        workspace_runtime.resolve_workspace_path(
            str(root), "/mnt/user-data/outputs/.tool-offload/private.json", writable=True
        )


def test_resolve_workspace_path_rejects_semantic_model_write_after_policy(tmp_path, monkeypatch):
    """策略放行后仍拒绝写语义模型只读快照（真实路径二次校验）。"""
    root = _make_workspace_root(tmp_path / "root")
    monkeypatch.setattr(workspace_runtime, "WORKSPACE_PATH_POLICY", _AllowAllPolicy())

    with pytest.raises(ValueError, match="语义模型只读快照"):
        workspace_runtime.resolve_workspace_path(
            str(root), "/mnt/user-data/workspace/semantic/model.yaml", writable=True
        )


def test_selector_indices_after_unknown_verb_returns_empty_set():
    """未知动词不产生任何 selector 下标。"""
    assert workspace_runtime._selector_indices_after_verb(["officecli", "unknown"], 1) == set()
    assert workspace_runtime._selector_indices_after_verb(["officecli", "swap", "a", "b"], 1) == {3}


def test_move_selector_indices_without_body_token_returns_empty_set():
    """move 后没有足够 token 时不越界取下标。"""
    assert workspace_runtime._move_selector_indices(["officecli", "move"], 1) == set()
    assert workspace_runtime._move_selector_indices(["officecli", "move", "a", "--to", "b"], 1) == {3}


def test_chart_spec_encoding_valid_rejects_non_dict_encoding():
    """encoding 不是字典时视为非法。"""
    assert artifact_registry._chart_spec_encoding_valid("bar", None) is False
    assert artifact_registry._chart_spec_encoding_valid("bar", []) is False


def test_output_relative_path_returns_none_for_non_outputs_path():
    """非 outputs/ 前缀与缺少前缀的路径都返回 None。"""
    assert artifact_registry._output_relative_path("readme.txt") is None
    assert artifact_registry._output_relative_path("/mnt/user-data/workspace/a.png") is None


def test_output_relative_path_accepts_outputs_prefixed_paths():
    """outputs/ 前缀与 /outputs/ 形式都能解析出相对路径。"""
    assert str(artifact_registry._output_relative_path("outputs/a.png")) == "outputs/a.png"
    assert str(artifact_registry._output_relative_path("/outputs/a.png")) == "outputs/a.png"


def test_parse_output_artifact_returns_empty_for_non_outputs_path():
    """非 outputs 产物直接返回空 payload。"""
    assert artifact_registry.parse_output_artifact(visible_path="workspace/notes.md", data=b"hello") == {}
