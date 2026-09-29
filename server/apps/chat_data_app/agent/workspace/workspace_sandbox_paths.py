"""Sandbox path roots, visibility mapping and managed/unmanaged predicates."""


from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from utils.logger import get_logger
from utils.skill_paths import SKILLS_VIRTUAL_ROOT
from utils.storage_paths import CHAT_DATA_HOME
from utils.tool_result_store import QUERY_RESULT_DIR_NAME, TOOL_OFFLOAD_DIR_NAME

from .workspace_path_policy import WORKSPACE_PATH_POLICY

logger = get_logger(__name__)


# OpenSandbox execd parses permission values as base-8 *strings*.  These must
# therefore be decimal integers whose digits are the intended Unix mode.
_SANDBOX_WRITABLE_FILE_MODE = 666
_SANDBOX_WRITABLE_DIRECTORY_MODE = 777
_SANDBOX_READONLY_FILE_MODE = 444
_SANDBOX_READONLY_DIRECTORY_MODE = 555


def _sandbox_root() -> str:
    # 沙箱 Files API 快照根固定为 /mnt（sandbox_visible_path / sandbox_workspace_dir
    # 等映射硬编码 /mnt/user-data），不对外暴露为可配置项，避免前缀不一致。
    return "/mnt"


def sandbox_thread_root() -> str:
    return f"{_sandbox_root()}/user-data"


def sandbox_workspace_dir() -> str:
    return f"{sandbox_thread_root()}/workspace"


def sandbox_outputs_dir() -> str:
    return f"{sandbox_thread_root()}/outputs"


def sandbox_uploads_dir() -> str:
    return f"{sandbox_thread_root()}/uploads"


def sandbox_skills_root() -> str:
    # 沙箱内技能快照根与 Agent 视角的虚拟根一致。
    return SKILLS_VIRTUAL_ROOT


def _application_workspace_root(workspace_root: str) -> Path:
    """Validate the application-owned persistent conversation directory."""
    resolved = Path(workspace_root).expanduser().resolve()
    chat_data_root = Path(CHAT_DATA_HOME).expanduser().resolve()
    if chat_data_root != resolved and chat_data_root not in resolved.parents:
        raise ValueError(f"会话工作区不在 CHAT_DATA_HOME 内: {workspace_root}")
    if resolved.name != "user-data":
        raise ValueError(f"会话工作区必须是 user-data 目录: {workspace_root}")
    return resolved


def _is_sandbox_descendant(path: str, root: str) -> bool:
    try:
        relative = PurePosixPath(path).relative_to(PurePosixPath(root))
    except ValueError:
        return False
    return bool(relative.parts)


def _is_hidden_tool_results_local_path(application_root: Path, path: Path) -> bool:
    output_root = application_root / "outputs"
    roots = (
        output_root / TOOL_OFFLOAD_DIR_NAME,
        output_root / QUERY_RESULT_DIR_NAME,
    )
    return any(path == root or root in path.parents for root in roots)


def _is_managed_semantic_model_local_path(application_root: Path, path: Path) -> bool:
    semantic_root = application_root / "workspace" / "semantic"
    return path == semantic_root or semantic_root in path.parents


def _is_unmanaged_sandbox_path(path: str) -> bool:
    return not _is_managed_semantic_model_sandbox_path(path) and not _is_tool_results_sandbox_path(path)


def _is_unmanaged_local_path(application_root: Path, path: Path) -> bool:
    return not _is_hidden_tool_results_local_path(application_root, path) and not _is_managed_semantic_model_local_path(application_root, path)


def _is_sandbox_file_not_found(error: Exception) -> bool:
    """Recognize an absent remote file without hiding transport or permission errors."""
    if isinstance(error, FileNotFoundError):
        return True
    if getattr(error, "status_code", None) == 404:
        return True
    message = str(error).lower()
    return "filenotfounderror" in message or "no such file or directory" in message


def sandbox_visible_path(
    path: str,
    *,
    writable: bool = False,
    allow_managed_semantic_model_write: bool = False,
) -> str:
    if not str(path or "").strip():
        return sandbox_thread_root()
    logical_path = WORKSPACE_PATH_POLICY.parse(path)
    WORKSPACE_PATH_POLICY.assert_readable(logical_path)
    if writable:
        WORKSPACE_PATH_POLICY.assert_writable(
            logical_path,
            allow_managed_semantic_model_write=allow_managed_semantic_model_write,
        )
    return WORKSPACE_PATH_POLICY.to_sandbox_path(logical_path)


def sandbox_path_to_visible(path: str) -> str:
    mappings = (
        (sandbox_workspace_dir(), f"{sandbox_thread_root()}/workspace"),
        (sandbox_outputs_dir(), f"{sandbox_thread_root()}/outputs"),
        (sandbox_uploads_dir(), f"{sandbox_thread_root()}/uploads"),
        (sandbox_skills_root(), sandbox_skills_root()),
    )
    value = str(path or "").strip()
    for prefix, visible in mappings:
        if value == prefix:
            return visible
        if value.startswith(f"{prefix}/"):
            return f"{visible}{value[len(prefix):]}"
    return value


def _is_outputs_sandbox_path(path: str) -> bool:
    return path == sandbox_outputs_dir() or path.startswith(f"{sandbox_outputs_dir()}/")


def _is_tool_results_sandbox_path(path: str) -> bool:
    roots = (
        f"{sandbox_outputs_dir()}/{TOOL_OFFLOAD_DIR_NAME}",
        f"{sandbox_outputs_dir()}/{QUERY_RESULT_DIR_NAME}",
    )
    return any(path == root or path.startswith(f"{root}/") for root in roots)


def _is_uploads_sandbox_path(path: str) -> bool:
    root = sandbox_uploads_dir()
    return path == root or path.startswith(f"{root}/")


def _is_managed_semantic_model_sandbox_path(path: str) -> bool:
    root = f"{sandbox_workspace_dir()}/semantic"
    return path == root or path.startswith(f"{root}/")


def _sandbox_entry_type(entry: Any) -> str:
    """Normalize the SDK's optional entry type without trusting a path suffix."""
    value = getattr(entry, "entry_type", getattr(entry, "type", ""))
    return str(value or "").strip().lower()


def _is_sandbox_not_directory(error: Exception) -> bool:
    message = str(error).lower()
    return "not a directory" in message or "enotdir" in message
