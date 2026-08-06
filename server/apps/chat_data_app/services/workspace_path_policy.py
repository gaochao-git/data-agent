"""Shared logical path and permission policy for conversation workspaces."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal, cast
from urllib.parse import unquote

from utils.skill_paths import SKILLS_VIRTUAL_ROOT
from utils.tool_result_store import TOOL_OFFLOAD_DIR_NAME

WorkspacePathKind = Literal["workspace", "outputs", "uploads", "skills"]

_WORKSPACE_ROOTS = frozenset({"workspace", "outputs", "uploads"})


@dataclass(frozen=True)
class LogicalWorkspacePath:
    """A validated logical path before backend-specific path translation."""

    normalized: str
    kind: WorkspacePathKind
    relative_path: str

    @property
    def is_semantic_model(self) -> bool:
        return self.kind == "workspace" and (
            self.relative_path == "semantic"
            or self.relative_path.startswith("semantic/")
        )

    @property
    def is_tool_result(self) -> bool:
        return self.kind == "outputs" and (
            self.relative_path == TOOL_OFFLOAD_DIR_NAME
            or self.relative_path.startswith(f"{TOOL_OFFLOAD_DIR_NAME}/")
        )

    @property
    def is_skill(self) -> bool:
        return self.kind == "skills"


class WorkspacePathPolicy:
    """Validate logical workspace paths independently from storage backends."""

    def parse(self, path: str, *, allow_empty: bool = False) -> LogicalWorkspacePath:
        raw = unquote(str(path or "").strip())
        while raw.startswith("./"):
            raw = raw[2:]
        if not raw:
            if allow_empty:
                return LogicalWorkspacePath("", "workspace", "")
            raise ValueError("必须指定 workspace、outputs 或 uploads 下的路径")
        if "\x00" in raw:
            raise ValueError("路径不能包含空字节")

        raw = self._remove_alias(raw)
        if raw.startswith("/"):
            if raw == SKILLS_VIRTUAL_ROOT:
                raise ValueError("技能路径必须包含技能名")
            if raw.startswith(f"{SKILLS_VIRTUAL_ROOT}/"):
                skill_path = raw[len(SKILLS_VIRTUAL_ROOT) + 1 :]
                return self._build_skill_path(skill_path, raw)
            raise ValueError(f"只允许访问当前会话目录内的相对路径: {path}")

        parts = PurePosixPath(raw).parts
        if not parts or ".." in parts:
            raise ValueError(f"路径越界，禁止访问当前会话目录之外的路径: {path}")
        kind = parts[0]
        if kind not in _WORKSPACE_ROOTS:
            raise ValueError("只允许访问 workspace/、outputs/ 或 uploads/")
        relative = "/".join(parts[1:])
        normalized = kind if not relative else f"{kind}/{relative}"
        return LogicalWorkspacePath(
            normalized,
            cast(WorkspacePathKind, kind),
            relative,
        )

    def assert_readable(self, logical_path: LogicalWorkspacePath) -> None:
        if logical_path.is_skill and not logical_path.relative_path:
            raise ValueError("技能路径必须包含技能名")

    def assert_writable(
        self,
        logical_path: LogicalWorkspacePath,
        *,
        allow_managed_semantic_model_write: bool = False,
    ) -> None:
        if logical_path.kind not in {"workspace", "outputs"}:
            raise ValueError("只允许写入 workspace/ 或 outputs/")
        if logical_path.is_tool_result:
            raise ValueError("不允许通过文件编辑工具修改 outputs/.tool-offload/")
        if logical_path.is_semantic_model and not allow_managed_semantic_model_write:
            raise ValueError("workspace/semantic/ 是本轮语义模型只读快照，不能修改")
        if allow_managed_semantic_model_write and (
            not logical_path.is_semantic_model
            or not logical_path.relative_path.lower().endswith(".yaml")
        ):
            raise ValueError("内部语义模型写入只能覆盖 workspace/semantic/ 下的 YAML 文件")

    def to_sandbox_path(self, logical_path: LogicalWorkspacePath) -> str:
        if logical_path.is_skill:
            return f"{SKILLS_VIRTUAL_ROOT}/{logical_path.relative_path}"
        return f"/mnt/user-data/{logical_path.normalized}"

    @staticmethod
    def _remove_alias(raw: str) -> str:
        aliases = (
            ("/mnt/user-data/workspace", "workspace"),
            ("/mnt/user-data/outputs", "outputs"),
            ("/mnt/user-data/uploads", "uploads"),
            ("/workspace", "workspace"),
            ("/outputs", "outputs"),
            ("/uploads", "uploads"),
        )
        for prefix, alias in aliases:
            if raw == prefix:
                return alias
            if raw.startswith(f"{prefix}/"):
                return f"{alias}/{raw[len(prefix) + 1 :]}"
        return raw

    @staticmethod
    def _build_skill_path(relative: str, original: str) -> LogicalWorkspacePath:
        parts = PurePosixPath(relative).parts
        if not parts or parts[0] in {"", ".", ".."} or ".." in parts:
            raise ValueError(f"技能路径无效: {original}")
        skill_relative = "/".join(parts)
        return LogicalWorkspacePath(
            f"{SKILLS_VIRTUAL_ROOT}/{skill_relative}",
            "skills",
            skill_relative,
        )


WORKSPACE_PATH_POLICY = WorkspacePathPolicy()

__all__ = [
    "LogicalWorkspacePath",
    "WORKSPACE_PATH_POLICY",
    "WorkspacePathKind",
    "WorkspacePathPolicy",
]
