"""Shared logical path and permission policy for conversation workspaces."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal, cast
from urllib.parse import unquote

from utils.skill_paths import SKILLS_VIRTUAL_ROOT
from utils.tool_result_store import QUERY_RESULT_DIR_NAME, TOOL_OFFLOAD_DIR_NAME

WorkspacePathKind = Literal["workspace", "outputs", "uploads", "memory", "skills"]

# 工作区规范虚拟前缀（模型/前端唯一合法形式），与沙箱挂载路径字面一致。
USER_DATA_VIRTUAL_ROOT = "/mnt/user-data"

_WORKSPACE_ROOTS = frozenset({"workspace", "outputs", "uploads", "memory"})


@dataclass(frozen=True)
class LogicalWorkspacePath:
    """A validated logical path before backend-specific path translation."""

    normalized: str
    kind: WorkspacePathKind
    relative_path: str

    @property
    def is_semantic_model(self) -> bool:
        return self.kind == "workspace" and (
            self.relative_path == "semantic" or self.relative_path.startswith("semantic/")
        )

    @property
    def is_ontology_catalog(self) -> bool:
        return self.kind == "workspace" and (
            self.relative_path == "ontology" or self.relative_path.startswith("ontology/")
        )

    @property
    def is_tool_offload(self) -> bool:
        return self.kind == "outputs" and (
            self.relative_path == TOOL_OFFLOAD_DIR_NAME or self.relative_path.startswith(f"{TOOL_OFFLOAD_DIR_NAME}/")
        )

    @property
    def is_query_result(self) -> bool:
        return self.kind == "outputs" and (
            self.relative_path == QUERY_RESULT_DIR_NAME or self.relative_path.startswith(f"{QUERY_RESULT_DIR_NAME}/")
        )

    @property
    def is_system_result(self) -> bool:
        """系统管理的结果目录（outputs/query_result 与 outputs/.tool-offload）。"""
        return self.is_tool_offload or self.is_query_result

    @property
    def is_skill(self) -> bool:
        return self.kind == "skills"


class WorkspacePathPolicy:
    """Validate logical workspace paths independently from storage backends."""

    def parse(self, path: str, *, allow_empty: bool = False) -> LogicalWorkspacePath:
        """严进：只接受规范形式 /mnt/user-data/<root>/... 与只读技能 /mnt/skills/<name>/...。

        裸别名（/workspace/...）、相对形式（workspace/...）、./ 前缀一律拒绝，
        报错信息携带规范形式要求，保证模型可自我纠正。
        """
        raw = unquote(str(path or "").strip())
        if not raw:
            if allow_empty:
                return LogicalWorkspacePath("", "workspace", "")
            raise ValueError("路径必须以 /mnt/user-data/ 或 /mnt/skills/ 开头")
        if "\x00" in raw:
            raise ValueError("路径不能包含空字节")

        if raw == SKILLS_VIRTUAL_ROOT:
            raise ValueError("技能路径必须包含技能名")
        if raw.startswith(f"{SKILLS_VIRTUAL_ROOT}/"):
            return self._build_skill_path(raw[len(SKILLS_VIRTUAL_ROOT) + 1 :], raw)

        prefix = f"{USER_DATA_VIRTUAL_ROOT}/"
        if not raw.startswith(prefix):
            raise ValueError(f"路径必须以 /mnt/user-data/ 或 /mnt/skills/ 开头: {path}")
        parts = PurePosixPath(raw[len(prefix) :]).parts
        if not parts or ".." in parts:
            raise ValueError(f"路径越界，禁止访问当前会话目录之外的路径: {path}")
        kind = parts[0]
        if kind not in _WORKSPACE_ROOTS:
            raise ValueError(
                "只允许访问 /mnt/user-data/workspace、/mnt/user-data/outputs、"
                "/mnt/user-data/uploads 或 /mnt/user-data/memory"
            )
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
        allow_uploads_write: bool = False,
    ) -> None:
        """校验逻辑路径可写。

        ``allow_uploads_write`` 只服务于**人侧**的 uploads 删除接口（用户清理自己上传的文件），
        默认为 False：uploads 在沙箱内是只读挂载，智能体的 file_write/file_edit 不应拿到写权限。
        绝不要用「把 uploads 加进白名单」的方式实现，否则等于顺带把写权限也给了模型工具。
        """
        if logical_path.kind not in {"workspace", "outputs", "memory"} and not (
            allow_uploads_write and logical_path.kind == "uploads"
        ):
            raise ValueError("只允许写入 workspace/、outputs/ 或 memory/")
        if logical_path.is_system_result:
            raise ValueError("不允许通过文件编辑工具修改系统结果目录")
        if logical_path.is_semantic_model and not allow_managed_semantic_model_write:
            raise ValueError("/mnt/user-data/workspace/semantic/ 是本轮语义模型只读快照，不能修改")
        if allow_managed_semantic_model_write and (
            not logical_path.is_semantic_model and not logical_path.is_ontology_catalog
            or not logical_path.relative_path.lower().endswith(".yaml")
        ):
            raise ValueError(
                "内部系统写入只允许 /mnt/user-data/workspace/semantic/ 或 ontology/ 下的 YAML 文件"
            )

    def to_sandbox_path(self, logical_path: LogicalWorkspacePath) -> str:
        if logical_path.is_skill:
            return f"{SKILLS_VIRTUAL_ROOT}/{logical_path.relative_path}"
        return f"{USER_DATA_VIRTUAL_ROOT}/{logical_path.normalized}"

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
    "USER_DATA_VIRTUAL_ROOT",
    "WORKSPACE_PATH_POLICY",
    "WorkspacePathKind",
    "WorkspacePathPolicy",
]
