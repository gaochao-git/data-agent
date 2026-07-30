"""OpenSandbox workspace execution for ChatData conversations."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import time
from typing import Any
from urllib.parse import unquote
from uuid import uuid4

from utils.crypto import decrypt_env_value
from utils.logger import get_logger
from utils.skill_paths import SKILLS_VIRTUAL_ROOT
from utils.storage_paths import CHAT_DATA_HOME
from .artifact_registry import parse_output_artifact
from .workspace_patch import FileState, PatchOp, plan_patch_ops

logger = get_logger(__name__)

OPENSANDBOX_BACKEND = "opensandbox"
MAX_OUTPUT_CHARS = 12000
_SANDBOX_RUNTIME_UID = 65534


def _sandbox_root() -> str:
    # 沙箱 VM 内挂载根固定为 /mnt（sandbox_visible_path / sandbox_workspace_dir 等映射
    # 硬编码 /mnt/user-data），不对外暴露为可配置项，避免与映射前缀不一致。
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
    # 沙箱内技能挂载根与 Agent 视角的虚拟根一致（容器 /mnt 即虚拟根）。
    return SKILLS_VIRTUAL_ROOT


@dataclass
class SandboxExecutionSession:
    sandbox: Any
    sandbox_id: str
    workspace_root: str
    mode: str = "sandbox"
    runtime_skills_dir: Path | None = None
    cache_key: str = ""
    application_manifest: dict[str, tuple[int, int, str]] | None = None
    pending_persistence: bool = False
    operation_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


@dataclass
class CachedSandboxExecutionSession:
    session: SandboxExecutionSession
    expires_at: float


class SandboxExecutionSessionRef:
    def __init__(self) -> None:
        self.current: SandboxExecutionSession | None = None


_WORKSPACE_EXECUTION_SESSION_CACHE: dict[str, CachedSandboxExecutionSession] = {}
_WORKSPACE_EXECUTION_SESSION_LOCKS: dict[str, asyncio.Lock] = {}


def require_sandbox_session(session_ref: SandboxExecutionSessionRef | None) -> SandboxExecutionSession:
    session = session_ref.current if session_ref is not None else None
    if session is None or session.sandbox is None:
        raise ValueError("OpenSandbox 执行环境未准备")
    return session


def _import_opensandbox_sdk() -> tuple[Any, Any, Any, Any]:
    try:
        from opensandbox import Sandbox
        from opensandbox.config import ConnectionConfig
        from opensandbox.models.execd import RunCommandOpts
        from opensandbox.models.sandboxes import NetworkPolicy, NetworkRule
    except ImportError as exc:
        raise ValueError("OpenSandbox Python SDK 未安装或不支持 Files API / NetworkPolicy，请升级 `opensandbox`") from exc
    return Sandbox, ConnectionConfig, RunCommandOpts, (NetworkPolicy, NetworkRule)


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        logger.warning("OpenSandbox 配置不是合法整数: %s=%r，使用默认值 %s", name, raw, default)
        return default


def _str_to_bool(value: str | None, default: bool = False) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "on"}


def _workspace_session_cache_key(
    workspace_root: str,
    mode: str = "sandbox",
    runtime_skills_dir: Path | None = None,
) -> str:
    skills = str(runtime_skills_dir or "")
    return f"{_application_workspace_root(workspace_root)}#{mode}#{skills}"


def _workspace_session_ttl_seconds() -> int:
    timeout_seconds = _env_int("OPENSANDBOX_TIMEOUT_SECONDS", 600)
    return max(30, min(timeout_seconds - 30, 300))


def _workspace_session_lock(cache_key: str) -> asyncio.Lock:
    lock = _WORKSPACE_EXECUTION_SESSION_LOCKS.get(cache_key)
    if lock is None:
        lock = asyncio.Lock()
        _WORKSPACE_EXECUTION_SESSION_LOCKS[cache_key] = lock
    return lock


def _network_default_action() -> str | None:
    raw = str(os.getenv("OPENSANDBOX_NETWORK_DEFAULT_ACTION") or "deny").strip().lower()
    if raw in {"allow", "deny"}:
        return raw
    logger.warning("OpenSandbox 网络默认策略配置非法: OPENSANDBOX_NETWORK_DEFAULT_ACTION=%r，使用 deny", raw)
    return "deny"


def _network_allow_targets() -> list[str]:
    raw = str(os.getenv("OPENSANDBOX_NETWORK_ALLOW_TARGETS") or "")
    targets: list[str] = []
    seen: set[str] = set()
    for part in raw.replace("\n", ",").split(","):
        target = part.strip()
        if not target or target in seen:
            continue
        seen.add(target)
        targets.append(target)
    return targets


def _validate_workspace_backend_config() -> None:
    # 应用侧 CHAT_DATA_HOME 是唯一持久化根。它不需要暴露给 OpenSandbox 宿主机，
    # 但必须是绝对路径，以免运行期上传/下载落到随进程目录变化的位置。
    raw_home = str(os.getenv("CHAT_DATA_HOME") or "").strip()
    if not Path(raw_home).expanduser().is_absolute():
        raise ValueError("sandbox 模式下 CHAT_DATA_HOME 必须为应用节点上的绝对路径")


def _application_workspace_root(workspace_root: str) -> Path:
    """Validate the application-owned persistent conversation directory."""
    resolved = Path(workspace_root).expanduser().resolve()
    chat_data_root = Path(CHAT_DATA_HOME).expanduser().resolve()
    if chat_data_root != resolved and chat_data_root not in resolved.parents:
        raise ValueError(f"会话工作区不在 CHAT_DATA_HOME 内: {workspace_root}")
    if resolved.name != "user-data":
        raise ValueError(f"会话工作区必须是 user-data 目录: {workspace_root}")
    return resolved



def _sandbox_python_bin() -> str:
    python_bin = str(os.getenv("OPENSANDBOX_PYTHON_BIN") or "python3").strip()
    if not python_bin:
        return "python3"
    if any(char.isspace() for char in python_bin) or any(char in python_bin for char in "'\";&|`$<>"):
        raise ValueError("OPENSANDBOX_PYTHON_BIN 只能配置 Python 命令名或绝对路径")
    return python_bin


def _connection_config(timeout_seconds: int) -> Any:
    _, ConnectionConfig, _, _ = _import_opensandbox_sdk()
    sandbox_url = str(os.getenv("OPENSANDBOX_URL") or "").strip()
    if not sandbox_url:
        raise ValueError("未配置 OPENSANDBOX_URL，无法创建 OpenSandbox")
    return ConnectionConfig(
        domain=sandbox_url,
        api_key=decrypt_env_value("OPENSANDBOX_API_KEY") or None,
        request_timeout=timedelta(seconds=max(30, timeout_seconds)),
        use_server_proxy=_str_to_bool(os.getenv("OPENSANDBOX_USE_SERVER_PROXY"), False),
    )


def _build_network_policy() -> Any | None:
    action = _network_default_action()
    if not action or action == "allow":
        return None
    _, _, _, models = _import_opensandbox_sdk()
    NetworkPolicy, NetworkRule = models
    egress_rules = None
    allow_targets = _network_allow_targets()
    if allow_targets:
        egress_rules = [NetworkRule(action="allow", target=target) for target in allow_targets]
    return NetworkPolicy(defaultAction=action, egress=egress_rules)


async def _write_sandbox_file(*, sandbox: Any, path: str, data: bytes) -> None:
    files = getattr(sandbox, "files", None)
    writer = getattr(files, "write_file", None)
    if not callable(writer):
        raise ValueError("当前 OpenSandbox SDK 不支持 files.write_file")
    await writer(path, data, mode=0o644)
    logger.info(
        "OpenSandbox Files API 上传完成: sandbox_id=%s path=%s bytes=%s",
        str(getattr(sandbox, "id", "") or ""),
        path,
        len(data),
    )


async def _grant_sandbox_runtime_write_access(
    *,
    session: SandboxExecutionSession,
    paths: list[str],
) -> None:
    """Make Files API writes editable by the unprivileged sandbox process.

    Files API writes may be owned by the service user. Bash and filesystem RPCs
    run as the isolated runtime uid, so normalize ownership after every remote
    write instead of relying on the OpenSandbox implementation detail.
    """
    unique_paths = sorted({str(path) for path in paths if str(path).strip()})
    if not unique_paths:
        return
    command = " && ".join([
        "chown {uid}:{uid} {paths}".format(
            uid=_SANDBOX_RUNTIME_UID,
            paths=" ".join(shlex.quote(path) for path in unique_paths),
        ),
        "chmod u+rw {paths}".format(
            paths=" ".join(shlex.quote(path) for path in unique_paths),
        ),
    ])
    exit_code, _, stderr = await run_command_in_sandbox(
        session=session,
        command=f"bash -o pipefail -lc {shlex.quote(command)}",
        privileged=True,
    )
    if exit_code != 0:
        raise ValueError(f"设置沙箱文件写权限失败: {stderr or '未知错误'}")


async def _read_sandbox_file_bytes(*, sandbox: Any, path: str) -> bytes:
    files = getattr(sandbox, "files", None)
    reader = getattr(files, "read_bytes", None)
    if not callable(reader):
        raise ValueError("当前 OpenSandbox SDK 不支持 files.read_bytes")
    data = bytes(await reader(path))
    logger.info(
        "OpenSandbox Files API 下载完成: sandbox_id=%s path=%s bytes=%s",
        str(getattr(sandbox, "id", "") or ""),
        path,
        len(data),
    )
    return data


async def _read_sandbox_file_bytes_with_fallback(*, session: SandboxExecutionSession, path: str) -> bytes:
    try:
        return await _read_sandbox_file_bytes(sandbox=session.sandbox, path=path)
    except (FileNotFoundError, IsADirectoryError):
        raise
    except Exception as exc:
        logger.warning("OpenSandbox files.read_bytes 失败，尝试沙箱内读取: path=%s error=%s", path, exc)
        result = await run_sandbox_python_json(
            session=session,
            payload={"path": path},
            script_body=r'''
from pathlib import Path
import base64
path = Path(payload["path"])
data = path.read_bytes()
print(json.dumps({"content_b64": base64.b64encode(data).decode("ascii")}, ensure_ascii=False))
''',
        )
        return base64.b64decode(str(result.get("content_b64") or ""))


async def _delete_sandbox_files(*, sandbox: Any, paths: list[str]) -> None:
    if not paths:
        return
    files = getattr(sandbox, "files", None)
    deleter = getattr(files, "delete_files", None)
    if not callable(deleter):
        raise ValueError("当前 OpenSandbox SDK 不支持 files.delete_files")
    await deleter(paths)


def _iter_application_files(root: Path) -> list[tuple[str, Path]]:
    """Return regular files only; uploads must never follow application symlinks."""
    if not root.is_dir() or root.is_symlink():
        return []
    files: list[tuple[str, Path]] = []
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            files.append((path.relative_to(root).as_posix(), path))
    return files


def _file_fingerprint(path: Path) -> tuple[int, int, str]:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, digest.hexdigest()


def _application_manifest(workspace_root: Path) -> dict[str, tuple[int, int, str]]:
    manifest: dict[str, tuple[int, int, str]] = {}
    for directory_name in ("workspace", "outputs", "uploads"):
        directory = workspace_root / directory_name
        for relative, path in _iter_application_files(directory):
            manifest[f"{directory_name}/{relative}"] = _file_fingerprint(path)
    return manifest


def _mutable_application_manifest(workspace_root: Path) -> dict[str, tuple[int, int, str]]:
    """Fingerprint only trees that a sandbox operation is allowed to persist."""
    manifest: dict[str, tuple[int, int, str]] = {}
    for directory_name in ("workspace", "outputs"):
        directory = workspace_root / directory_name
        for relative, path in _iter_application_files(directory):
            manifest[f"{directory_name}/{relative}"] = _file_fingerprint(path)
    return manifest


async def _upload_application_tree(
    *,
    sandbox: Any,
    application_root: Path,
    sandbox_root: str,
) -> tuple[int, int]:
    files = _iter_application_files(application_root)
    total_bytes = 0
    for relative, path in files:
        data = await asyncio.to_thread(path.read_bytes)
        await _write_sandbox_file(
            sandbox=sandbox,
            path=f"{sandbox_root}/{relative}",
            data=data,
        )
        total_bytes += len(data)
    return len(files), total_bytes


async def _initialize_sandbox_filesystem(
    *,
    session: SandboxExecutionSession,
) -> None:
    """Seed the isolated sandbox from application-owned persistent files."""
    application_root = _application_workspace_root(session.workspace_root)
    directory_command = "mkdir -p " + " ".join(
        shlex.quote(path)
        for path in (
            sandbox_workspace_dir(),
            sandbox_outputs_dir(),
            f"{sandbox_outputs_dir()}/.tool-results",
            sandbox_uploads_dir(),
            sandbox_skills_root(),
        )
    )
    exit_code, _, stderr = await run_command_in_sandbox(
        session=session,
        command=f"bash -o pipefail -lc {shlex.quote(directory_command)}",
        privileged=True,
    )
    if exit_code != 0:
        raise ValueError(f"初始化沙箱工作目录失败: {stderr or '未知错误'}")
    user_data_count, user_data_bytes = await _upload_application_tree(
        sandbox=session.sandbox,
        application_root=application_root,
        sandbox_root=sandbox_thread_root(),
    )
    skill_count = skill_bytes = 0
    if session.runtime_skills_dir is not None:
        skill_count, skill_bytes = await _upload_application_tree(
            sandbox=session.sandbox,
            application_root=session.runtime_skills_dir,
            sandbox_root=sandbox_skills_root(),
        )
    logger.info(
        "OpenSandbox 会话快照上传完成: sandbox_id=%s user_data_files=%s user_data_bytes=%s skill_files=%s skill_bytes=%s",
        session.sandbox_id,
        user_data_count,
        user_data_bytes,
        skill_count,
        skill_bytes,
    )
    # The sandbox is a disposable copy. Bash is explicitly allowed to mutate
    # every uploaded file; only workspace/outputs are downloaded back to A.
    permission_command = " && ".join([
        f"chmod -R u+rwX,go-rwx {shlex.quote(sandbox_thread_root())} {shlex.quote(sandbox_skills_root())}",
        f"chown -R {_SANDBOX_RUNTIME_UID}:{_SANDBOX_RUNTIME_UID} {shlex.quote(sandbox_thread_root())} {shlex.quote(sandbox_skills_root())}",
    ])
    exit_code, _, stderr = await run_command_in_sandbox(
        session=session,
        command=f"bash -o pipefail -lc {shlex.quote(permission_command)}",
        privileged=True,
    )
    if exit_code != 0:
        raise ValueError(f"设置沙箱工作区权限失败: {stderr or '未知错误'}")
    session.application_manifest = await asyncio.to_thread(_mutable_application_manifest, application_root)


def _application_path_for_sandbox_path(session: SandboxExecutionSession, sandbox_path: str) -> Path:
    root = _application_workspace_root(session.workspace_root)
    sandbox_root = PurePosixPath(sandbox_thread_root())
    target = PurePosixPath(sandbox_path)
    try:
        relative = target.relative_to(sandbox_root)
    except ValueError as exc:
        raise ValueError(f"沙箱路径不属于当前会话: {sandbox_path}") from exc
    if not relative.parts or relative.parts[0] not in {"workspace", "outputs", "uploads"}:
        raise ValueError(f"沙箱路径不属于允许同步目录: {sandbox_path}")
    local_path = (root / relative.as_posix()).resolve()
    if root not in local_path.parents:
        raise ValueError(f"沙箱同步路径越界: {sandbox_path}")
    return local_path


def _write_application_bytes_atomically(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.parent / f".{path.name}.{uuid4().hex}.sync"
    try:
        with temporary_path.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _delete_application_file(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"拒绝删除应用侧符号链接: {path}")
    if path.exists():
        if not path.is_file():
            raise ValueError(f"拒绝用文件同步覆盖目录: {path}")
        path.unlink()


async def _persist_sandbox_paths(
    *,
    session: SandboxExecutionSession,
    changed_paths: list[str],
    deleted_paths: list[str] | None = None,
) -> None:
    """Download completed sandbox writes before committing them to CHAT_DATA_HOME."""
    staged: list[tuple[Path, bytes]] = []
    try:
        for sandbox_path in changed_paths:
            local_path = _application_path_for_sandbox_path(session, sandbox_path)
            data = await _read_sandbox_file_bytes_with_fallback(
                session=session,
                path=sandbox_path,
            )
            staged.append((local_path, data))
        _ensure_application_paths_unchanged(
            session=session,
            paths=[path for path, _ in staged]
            + [
                _application_path_for_sandbox_path(session, sandbox_path)
                for sandbox_path in deleted_paths or []
            ],
        )
        for local_path, data in staged:
            await asyncio.to_thread(_write_application_bytes_atomically, local_path, data)
        for sandbox_path in deleted_paths or []:
            local_path = _application_path_for_sandbox_path(session, sandbox_path)
            await asyncio.to_thread(_delete_application_file, local_path)
    except Exception:
        session.pending_persistence = True
        raise
    session.application_manifest = await asyncio.to_thread(
        _mutable_application_manifest,
        _application_workspace_root(session.workspace_root),
    )
    session.pending_persistence = False
    logger.info(
        "OpenSandbox 工作区同步回写完成: sandbox_id=%s changed_files=%s changed_bytes=%s deleted_files=%s",
        session.sandbox_id,
        len(staged),
        sum(len(data) for _, data in staged),
        len(deleted_paths or []),
    )


def _ensure_application_paths_unchanged(
    *,
    session: SandboxExecutionSession,
    paths: list[Path],
) -> None:
    expected = session.application_manifest
    if expected is None:
        return
    root = _application_workspace_root(session.workspace_root)
    current = _mutable_application_manifest(root)
    conflicts = []
    for path in paths:
        relative = path.relative_to(root).as_posix()
        if current.get(relative) != expected.get(relative):
            conflicts.append(relative)
    if conflicts:
        raise ValueError(
            "应用侧文件在沙箱运行期间已变更，拒绝覆盖: " + ", ".join(conflicts[:3])
        )


async def _list_sandbox_files_under(*, session: SandboxExecutionSession, root: str) -> list[str]:
    result = await run_sandbox_python_json(
        session=session,
        payload={"root": root},
        script_body=r'''
from pathlib import Path

root = Path(payload["root"])
if not root.exists():
    print(json.dumps({"files": []}, ensure_ascii=False))
else:
    files = [str(path) for path in root.rglob("*") if path.is_file() and not path.is_symlink()]
    print(json.dumps({"files": sorted(files)}, ensure_ascii=False))
''',
    )
    return [str(path) for path in result.get("files") or []]


async def _sync_sandbox_working_trees(session: SandboxExecutionSession) -> None:
    """Persist the sandbox-owned mutable trees after commands with unknown file effects."""
    remote_files: list[str] = []
    for root in (sandbox_workspace_dir(), sandbox_outputs_dir()):
        remote_files.extend(await _list_sandbox_files_under(session=session, root=root))
    remote_set = set(remote_files)
    application_root = _application_workspace_root(session.workspace_root)
    local_files: list[Path] = []
    for directory_name in ("workspace", "outputs"):
        local_files.extend(path for _, path in _iter_application_files(application_root / directory_name))
    deleted_paths = [
        f"{sandbox_thread_root()}/{path.relative_to(application_root).as_posix()}"
        for path in local_files
        if f"{sandbox_thread_root()}/{path.relative_to(application_root).as_posix()}" not in remote_set
    ]
    await _persist_sandbox_paths(
        session=session,
        changed_paths=sorted(remote_set),
        deleted_paths=deleted_paths,
    )


async def sync_sandbox_working_trees(session: SandboxExecutionSession) -> None:
    """Persist workspace and outputs after one sandbox command has completed."""
    await _sync_sandbox_working_trees(session)


async def _recover_pending_persistence(session: SandboxExecutionSession) -> None:
    if session.pending_persistence:
        await _sync_sandbox_working_trees(session)


def _is_sandbox_file_not_found(error: Exception) -> bool:
    """Recognize an absent remote file without hiding transport or permission errors."""
    if isinstance(error, FileNotFoundError):
        return True
    if getattr(error, "status_code", None) == 404:
        return True
    message = str(error).lower()
    return "filenotfounderror" in message or "no such file or directory" in message


def _extract_command_output(execution: Any, command_status: Any | None, status_lookup_error: str = "") -> tuple[int, str, str]:
    stdout = "\n".join(str(getattr(item, "text", "")) for item in getattr(getattr(execution, "logs", None), "stdout", []) or [] if str(getattr(item, "text", "")))
    stderr_parts = [str(getattr(item, "text", "")) for item in getattr(getattr(execution, "logs", None), "stderr", []) or [] if str(getattr(item, "text", ""))]
    execution_error = getattr(execution, "error", None)
    if execution_error is not None:
        error_text = ": ".join(part for part in (str(getattr(execution_error, "name", "")).strip(), str(getattr(execution_error, "value", "")).strip()) if part)
        if error_text:
            stderr_parts.append(error_text)
    elif status_lookup_error:
        stderr_parts.append(f"OpenSandbox 未能确认命令最终退出状态，按失败处理: {status_lookup_error}")
    exit_code = getattr(command_status, "exit_code", None)
    if exit_code is None:
        exit_code = 1 if (execution_error is not None or status_lookup_error) else 0
    return int(exit_code), stdout, "\n".join(stderr_parts)


async def run_command_in_sandbox(
    *,
    session: SandboxExecutionSession,
    command: str,
    timeout_seconds: int = 30,
    privileged: bool = False,
) -> tuple[int, str, str]:
    """Run a sandbox command as the unprivileged workspace user by default."""
    _, _, RunCommandOpts, _ = _import_opensandbox_sdk()
    options: dict[str, Any] = {"timeout": timedelta(seconds=max(1, timeout_seconds))}
    if not privileged:
        options.update({"uid": _SANDBOX_RUNTIME_UID, "gid": _SANDBOX_RUNTIME_UID})
    execution = await session.sandbox.commands.run(command, opts=RunCommandOpts(**options))
    command_status = None
    status_lookup_error = ""
    execution_id = getattr(execution, "id", None)
    if execution_id:
        try:
            command_status = await session.sandbox.commands.get_command_status(execution_id)
        except Exception as exc:
            status_lookup_error = str(exc)
            logger.warning("OpenSandbox 获取 command status 失败: %s", exc, exc_info=True)
    return _extract_command_output(execution, command_status, status_lookup_error)


async def run_sandbox_python_json(*, session: SandboxExecutionSession, script_body: str, payload: dict[str, Any], timeout_seconds: int = 30) -> dict[str, Any]:
    payload_b64 = base64.b64encode(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")).decode("ascii")
    command = (
        f"PAYLOAD_B64={shlex.quote(payload_b64)} {shlex.quote(_sandbox_python_bin())} - <<'PY'\n"
        "import base64\nimport json\nimport os\n"
        "payload = json.loads(base64.b64decode(os.environ['PAYLOAD_B64']).decode('utf-8'))\n"
        f"{script_body}\n"
        "PY"
    )
    exit_code, stdout, stderr = await run_command_in_sandbox(session=session, command=f"bash -o pipefail -lc {shlex.quote(command)}", timeout_seconds=timeout_seconds)
    if exit_code != 0:
        raise ValueError((stderr or stdout or "sandbox filesystem execution failed").strip())
    text = str(stdout or "").strip()
    if not text:
        raise ValueError("sandbox filesystem execution returned empty stdout")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"sandbox filesystem execution returned invalid JSON: {text[:200]}") from exc


async def prepare_workspace_execution_session(
    workspace_root: str,
    user_id: str = "system",
    mode: str = "sandbox",
    runtime_skills_dir: Path | None = None,
) -> SandboxExecutionSession:
    _validate_workspace_backend_config()
    Sandbox, _, _, _ = _import_opensandbox_sdk()
    timeout_seconds = _env_int("OPENSANDBOX_TIMEOUT_SECONDS", 600)
    ready_timeout = _env_int("OPENSANDBOX_READY_TIMEOUT_SECONDS", 60, minimum=10)
    image = str(os.getenv("OPENSANDBOX_IMAGE") or "ubuntu").strip() or "ubuntu"
    create_kwargs = {
        "image": image,
        "timeout": timedelta(seconds=timeout_seconds),
        "ready_timeout": timedelta(seconds=ready_timeout),
        "entrypoint": ["sleep", "infinity"],
        "connection_config": _connection_config(timeout_seconds),
        "metadata": {"managed_by": "fxcjg", "module": "chat_data_app", "workspace_backend": OPENSANDBOX_BACKEND, "sandbox_mode": mode},
    }
    network_policy = _build_network_policy()
    if network_policy is not None:
        create_kwargs["network_policy"] = network_policy
    sandbox = await Sandbox.create(**create_kwargs)
    session = SandboxExecutionSession(
        sandbox=sandbox,
        sandbox_id=str(getattr(sandbox, "id", "") or ""),
        workspace_root=str(_application_workspace_root(workspace_root)),
        mode=mode,
        runtime_skills_dir=runtime_skills_dir,
        cache_key=_workspace_session_cache_key(workspace_root, mode, runtime_skills_dir),
    )
    try:
        await _initialize_sandbox_filesystem(session=session)
    except Exception:
        await cleanup_workspace_execution_session(session)
        raise
    return session


async def get_workspace_execution_session(
    workspace_root: str,
    user_id: str = "system",
    mode: str = "sandbox",
    runtime_skills_dir: Path | None = None,
) -> SandboxExecutionSession:
    cache_key = _workspace_session_cache_key(workspace_root, mode, runtime_skills_dir)
    now = time.monotonic()
    cached = _WORKSPACE_EXECUTION_SESSION_CACHE.get(cache_key)
    if cached is not None and cached.expires_at > now:
        return cached.session

    async with _workspace_session_lock(cache_key):
        now = time.monotonic()
        cached = _WORKSPACE_EXECUTION_SESSION_CACHE.get(cache_key)
        if cached is not None and cached.expires_at > now:
            return cached.session
        if cached is not None:
            _WORKSPACE_EXECUTION_SESSION_CACHE.pop(cache_key, None)
            await release_workspace_execution_session(cached.session)
        session = await prepare_workspace_execution_session(
            workspace_root,
            user_id,
            mode,
            runtime_skills_dir,
        )
        _WORKSPACE_EXECUTION_SESSION_CACHE[cache_key] = CachedSandboxExecutionSession(
            session=session,
            expires_at=time.monotonic() + _workspace_session_ttl_seconds(),
        )
        return session


async def attach_workspace_execution_session(session: SandboxExecutionSession | None, user_id: str = "system") -> None:
    if session is None:
        return
    cache_key = session.cache_key or _workspace_session_cache_key(
        session.workspace_root,
        session.mode,
        session.runtime_skills_dir,
    )
    cached = _WORKSPACE_EXECUTION_SESSION_CACHE.get(cache_key)
    if cached is not None and cached.session is not session:
        await release_workspace_execution_session(cached.session)
    _WORKSPACE_EXECUTION_SESSION_CACHE[cache_key] = CachedSandboxExecutionSession(
        session=session,
        expires_at=time.monotonic() + _workspace_session_ttl_seconds(),
    )


async def invalidate_workspace_execution_session(session: SandboxExecutionSession | None, user_id: str = "system") -> None:
    if session is None:
        return
    cache_key = session.cache_key or _workspace_session_cache_key(
        session.workspace_root,
        session.mode,
        session.runtime_skills_dir,
    )
    cached = _WORKSPACE_EXECUTION_SESSION_CACHE.get(cache_key)
    if cached is not None and cached.session is session:
        _WORKSPACE_EXECUTION_SESSION_CACHE.pop(cache_key, None)
    await cleanup_workspace_execution_session(session)


async def cleanup_workspace_execution_session(session: SandboxExecutionSession | None) -> None:
    if session is None or session.sandbox is None:
        return
    for cache_key, cached in list(_WORKSPACE_EXECUTION_SESSION_CACHE.items()):
        if cached.session is session:
            _WORKSPACE_EXECUTION_SESSION_CACHE.pop(cache_key, None)
    try:
        await session.sandbox.kill()
    except Exception as exc:
        logger.warning("OpenSandbox kill sandbox 失败: %s", exc, exc_info=True)
    try:
        await session.sandbox.close()
    except Exception as exc:
        logger.warning("OpenSandbox close sandbox 失败: %s", exc, exc_info=True)


async def release_workspace_execution_session(session: SandboxExecutionSession | None) -> None:
    if session is None or session.sandbox is None:
        return
    try:
        await session.sandbox.close()
    except Exception as exc:
        logger.warning("OpenSandbox close sandbox 失败: %s", exc, exc_info=True)


def sandbox_visible_path(
    path: str,
    *,
    writable: bool = False,
) -> str:
    raw = unquote(str(path or "").strip())
    while raw.startswith("./"):
        raw = raw[2:]
    if not raw:
        return sandbox_thread_root()
    if "\x00" in raw:
        raise ValueError("路径不能包含空字节")
    mappings = (
        ("/mnt/user-data/workspace", sandbox_workspace_dir()),
        ("/mnt/user-data/outputs", sandbox_outputs_dir()),
        ("/mnt/user-data/uploads", sandbox_uploads_dir()),
        ("/workspace", sandbox_workspace_dir()),
        ("/outputs", sandbox_outputs_dir()),
        ("/uploads", sandbox_uploads_dir()),
        (sandbox_skills_root(), sandbox_skills_root()),
    )
    sandbox_path = ""
    for prefix, target_prefix in mappings:
        if raw == prefix:
            sandbox_path = target_prefix
            break
        if raw.startswith(f"{prefix}/"):
            sandbox_path = f"{target_prefix}{raw[len(prefix):]}"
            break
    if not sandbox_path:
        if raw.startswith("/"):
            raise ValueError(f"只允许访问当前会话目录内的路径: {path}")
        sandbox_path = f"{sandbox_thread_root()}/{raw.lstrip('/')}"
    if ".." in PurePosixPath(sandbox_path).parts:
        raise ValueError(f"路径越界，禁止访问当前会话目录之外的路径: {path}")
    # 运行域固定两卷：/mnt/user-data 整卷（可写，含 workspace/outputs/uploads）+ /mnt/skills（只读快照）。
    workspace_roots = (sandbox_workspace_dir(), sandbox_outputs_dir(), sandbox_uploads_dir())
    readable_roots = workspace_roots + (sandbox_skills_root(),)
    if not any(sandbox_path == root or sandbox_path.startswith(f"{root}/") for root in readable_roots):
        raise ValueError(f"只允许访问 workspace/、outputs/、uploads/ 或技能路径: {path}")
    if (
        sandbox_path == sandbox_skills_root()
        or (
            sandbox_path.startswith(f"{sandbox_skills_root()}/")
            and len(PurePosixPath(sandbox_path).parts) < 4
        )
    ):
        raise ValueError(f"技能路径必须包含技能名: {path}")
    if writable:
        writable_roots = (sandbox_workspace_dir(), sandbox_outputs_dir())
        if not any(sandbox_path == root or sandbox_path.startswith(f"{root}/") for root in writable_roots):
            raise ValueError("只允许写入 workspace/ 或 outputs/")
        if _is_tool_results_sandbox_path(sandbox_path):
            raise ValueError("不允许通过文件编辑工具修改 outputs/.tool-results/")
    return sandbox_path


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
    root = f"{sandbox_outputs_dir()}/.tool-results"
    return path == root or path.startswith(f"{root}/")


def _artifact_payload_from_bytes(*, visible_path: str, data: bytes) -> dict[str, Any]:
    return parse_output_artifact(visible_path=visible_path, data=data)


async def _resolve_sandbox_real_path(
    *,
    session: SandboxExecutionSession,
    file_path: str,
    writable: bool = False,
    outputs_only: bool = False,
    allow_tool_result_write: bool = False,
) -> str:
    """Resolve a sandbox path and reject symlinks that leave the mounted roots."""
    target = sandbox_visible_path(
        file_path,
        writable=writable and not allow_tool_result_write,
    )
    result = await run_sandbox_python_json(
        session=session,
        payload={
            "path": target,
            "readable_roots": [
                sandbox_workspace_dir(),
                sandbox_outputs_dir(),
                sandbox_uploads_dir(),
                sandbox_skills_root(),
            ],
            "writable_roots": [sandbox_workspace_dir(), sandbox_outputs_dir()],
            "tool_results_root": f"{sandbox_outputs_dir()}/.tool-results",
            "writable": writable,
            "outputs_only": outputs_only,
            "allow_tool_result_write": allow_tool_result_write,
        },
        script_body=r'''
from pathlib import Path

target = Path(payload["path"])
resolved = target.resolve(strict=False)

def within(path, root):
    return path == root or root in path.parents

readable_roots = [Path(value).resolve() for value in payload["readable_roots"]]
if not any(within(resolved, root) for root in readable_roots):
    raise ValueError("路径越界，禁止通过软链接访问当前会话目录之外的路径")
if payload["writable"]:
    writable_roots = [Path(value).resolve() for value in payload["writable_roots"]]
    if not any(within(resolved, root) for root in writable_roots):
        raise ValueError("只允许写入 workspace/ 或 outputs/")
    tool_results_root = Path(payload["tool_results_root"]).resolve()
    if within(resolved, tool_results_root) and not payload["allow_tool_result_write"]:
        raise ValueError("不允许通过文件编辑工具修改 outputs/.tool-results/")
if payload["outputs_only"]:
    outputs_root = Path(payload["writable_roots"][1]).resolve()
    if not within(resolved, outputs_root):
        raise ValueError("只允许呈现 outputs/ 下的产物")
print(json.dumps({"path": str(resolved)}, ensure_ascii=False))
''',
    )
    return str(result["path"])


async def sandbox_artifact_payload(*, session_ref: SandboxExecutionSessionRef, file_path: str) -> dict[str, Any]:
    session = require_sandbox_session(session_ref)
    async with session.operation_lock:
        await _recover_pending_persistence(session)
        lexical_target = sandbox_visible_path(file_path)
        if _is_tool_results_sandbox_path(lexical_target):
            return {}
        try:
            target = await _resolve_sandbox_real_path(
                session=session, file_path=file_path, outputs_only=True
            )
        except ValueError:
            return {}
        try:
            data = await _read_sandbox_file_bytes_with_fallback(session=session, path=target)
        except Exception:
            return {}
        visible_path = sandbox_path_to_visible(target)
        return _artifact_payload_from_bytes(visible_path=visible_path, data=data)


async def sandbox_read_text_file(
    *,
    session_ref: SandboxExecutionSessionRef,
    file_path: str,
    offset: int,
    limit: int,
    max_chars: int,
) -> dict[str, Any]:
    """Read a text page inside the sandbox after applying the virtual path policy."""
    session = require_sandbox_session(session_ref)
    async with session.operation_lock:
        await _recover_pending_persistence(session)
        target = await _resolve_sandbox_real_path(session=session, file_path=file_path)
        result = await run_sandbox_python_json(
            session=session,
            payload={
                "path": target,
                "offset": max(0, int(offset)),
                "limit": max(1, int(limit)),
                "max_chars": max(1, int(max_chars)),
            },
            script_body=r'''
from pathlib import Path

path = Path(payload["path"])
visible_path = payload["path"]
if not path.exists():
    print(json.dumps({"exists": False}, ensure_ascii=False))
elif path.is_dir():
    print(json.dumps({"exists": True, "is_dir": True}, ensure_ascii=False))
else:
    with path.open("rb") as file_obj:
        sample = file_obj.read(4096)
    if b"\x00" in sample:
        print(json.dumps({"exists": True, "is_binary": True}, ensure_ascii=False))
    else:
        offset = payload["offset"]
        limit = payload["limit"]
        max_chars = payload["max_chars"]
        lines = []
        truncated_lines = []
        chars = 0
        has_more = False
        with path.open("r", encoding="utf-8", errors="replace", newline=None) as file_obj:
            for line_index, line in enumerate(file_obj):
                if line_index < offset:
                    continue
                if len(lines) >= limit:
                    has_more = True
                    break
                body = line.rstrip("\n\r")
                available = max_chars - chars - (1 if lines else 0)
                if available <= 0:
                    has_more = True
                    break
                returned = body[:min(500, available)]
                if len(returned) < len(body):
                    truncated_lines.append({
                        "line_number": line_index + 1,
                        "original_chars": len(body),
                        "returned_chars": len(returned),
                    })
                lines.append(returned)
                chars += len(returned) + (1 if len(lines) > 1 else 0)
        if not lines:
            with path.open("r", encoding="utf-8", errors="replace", newline=None) as file_obj:
                total_lines = sum(1 for _ in file_obj)
            result = {
                "exists": True,
                "is_dir": False,
                "is_binary": False,
                "total_lines": total_lines,
                "empty": total_lines == 0,
                "offset_out_of_range": total_lines > 0,
            }
        else:
            result = {
                "exists": True,
                "is_dir": False,
                "is_binary": False,
                "lines": lines,
                "total_lines": None,
                "start_line": offset + 1,
                "end_line": offset + len(lines),
                "next_offset": offset + len(lines) if has_more else None,
                "has_more_lines": has_more,
                "has_more": has_more,
                "truncated_lines": truncated_lines,
            }
        print(json.dumps(result, ensure_ascii=False))
''',
        )
        result["path"] = sandbox_path_to_visible(target)
        logger.info(
            "OpenSandbox file_read 完成: sandbox_id=%s path=%s offset=%s limit=%s returned_lines=%s returned_chars=%s has_more=%s",
            session.sandbox_id,
            result["path"],
            offset,
            limit,
            len(result.get("lines") or []),
            sum(len(str(line)) for line in result.get("lines") or []),
            bool(result.get("has_more")),
        )
        return result


async def sandbox_load_skill_payload(
    *,
    session_ref: SandboxExecutionSessionRef,
    skill_name: str,
    max_chars: int,
) -> dict[str, Any]:
    """Load one mounted runtime skill without consulting the management directory."""
    session = require_sandbox_session(session_ref)
    async with session.operation_lock:
        await _recover_pending_persistence(session)
        normalized_name = str(skill_name or "").strip()
        if not re.fullmatch(r"[a-z]+(?:-[a-z]+)+", normalized_name):
            raise ValueError(f"技能名无效: {skill_name}")
        skill_root = await _resolve_sandbox_real_path(
            session=session,
            file_path=f"{sandbox_skills_root()}/{normalized_name}",
        )
        result = await run_sandbox_python_json(
            session=session,
            payload={"root": skill_root, "name": normalized_name, "max_chars": max(1, int(max_chars)) + 1},
            script_body=r'''
from pathlib import Path

root = Path(payload["root"])
if not root.is_dir() or root.is_symlink():
    raise ValueError(f"技能不存在或无权限访问: {payload['name']}")
skill_md_path = root / "SKILL.md"
if not skill_md_path.is_file() or skill_md_path.is_symlink():
    raise ValueError(f"技能文件不存在: {payload['name']}")
skill_md = skill_md_path.read_text(encoding="utf-8", errors="replace")[:payload["max_chars"]]
title = description = ""
if skill_md.startswith("---\n"):
    closing = skill_md.find("\n---\n", len("---\n"))
    if closing >= 0:
        for line in skill_md[len("---\n"):closing].splitlines():
            key, separator, value = line.partition(":")
            if not separator:
                continue
            value = value.strip().strip('"').strip("'")
            if key.strip() == "title":
                title = value
            elif key.strip() == "description":
                description = value

def files_in(name, suffix=None):
    directory = root / name
    if not directory.is_dir() or directory.is_symlink():
        return []
    return sorted(
        str(item.relative_to(directory).with_suffix("")) if name == "references" else str(item.relative_to(directory))
        for item in directory.rglob("*" if suffix is None else suffix)
        if item.is_file() and not item.is_symlink()
    )

print(json.dumps({
    "name": payload["name"],
    "title": title or payload["name"],
    "description": description,
    "skill_md": skill_md,
    "references": files_in("references", "*.md"),
    "scripts": files_in("scripts"),
    "assets": files_in("assets"),
}, ensure_ascii=False))
''',
        )
        result["visible_path"] = f"{sandbox_skills_root()}/{normalized_name}"
        logger.info(
            "OpenSandbox skill_load 完成: sandbox_id=%s skill_name=%s skill_chars=%s references=%s scripts=%s assets=%s",
            session.sandbox_id,
            normalized_name,
            len(str(result.get("skill_md") or "")),
            len(result.get("references") or []),
            len(result.get("scripts") or []),
            len(result.get("assets") or []),
        )
        return result


async def sandbox_list_dir(
    *,
    session_ref: SandboxExecutionSessionRef,
    path: str,
    max_depth: int,
) -> list[dict[str, Any]]:
    """List one sandbox-visible directory with the same hiding rules as local mode."""
    session = require_sandbox_session(session_ref)
    async with session.operation_lock:
        await _recover_pending_persistence(session)
        lexical_target = sandbox_visible_path(path)
        if _is_tool_results_sandbox_path(lexical_target):
            raise ValueError("outputs/.tool-results/ 是隐藏的工具结果缓存，不能列出")
        target = await _resolve_sandbox_real_path(session=session, file_path=path)
        if _is_tool_results_sandbox_path(target):
            raise ValueError("outputs/.tool-results/ 是隐藏的工具结果缓存，不能列出")
        visible_root = sandbox_path_to_visible(target)
        result = await run_sandbox_python_json(
            session=session,
            payload={"path": target, "visible_root": visible_root, "max_depth": max(1, int(max_depth))},
            script_body=r'''
from datetime import datetime
from pathlib import Path

root = Path(payload["path"])
if not root.exists():
    raise FileNotFoundError(f"目录不存在: {root}")
if not root.is_dir():
    raise ValueError(f"目标不是目录: {root}")
entries = []
hidden_root = Path("/mnt/user-data/outputs/.tool-results")

def visit(directory, visible_dir, depth):
    if len(entries) >= 500:
        return
    try:
        children = sorted(directory.iterdir(), key=lambda item: (item.is_file(), item.name))
    except OSError:
        return
    for child in children:
        if len(entries) >= 500:
            return
        if child.is_symlink() or child == hidden_root or hidden_root in child.parents:
            continue
        try:
            stat = child.stat()
        except OSError:
            continue
        is_dir = child.is_dir()
        visible = f"{visible_dir}/{child.name}"
        entries.append({
            "type": "dir" if is_dir else "file",
            "path": visible + ("/" if is_dir else ""),
            "size": "-" if is_dir else stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        })
        if is_dir and depth + 1 < payload["max_depth"]:
            visit(child, visible, depth + 1)

visit(root, payload["visible_root"], 0)
print(json.dumps({"entries": entries}, ensure_ascii=False))
''',
        )
        entries = result.get("entries")
        return entries if isinstance(entries, list) else []


async def sandbox_write_text_file(*, session_ref: SandboxExecutionSessionRef, file_path: str, content: str, mode: str) -> dict[str, Any]:
    session = require_sandbox_session(session_ref)
    async with session.operation_lock:
        await _recover_pending_persistence(session)
        target = await _resolve_sandbox_real_path(session=session, file_path=file_path, writable=True)
        if mode == "overwrite":
            data = content.encode("utf-8")
            await _write_sandbox_file(sandbox=session.sandbox, path=target, data=data)
        else:
            result = await run_sandbox_python_json(
                session=session,
                payload={"path": target, "content": content},
                script_body=r'''
from pathlib import Path
path = Path(payload["path"])
path.parent.mkdir(parents=True, exist_ok=True)
with path.open("a", encoding="utf-8") as file_obj:
    file_obj.write(str(payload.get("content") or ""))
print(json.dumps({"bytes": len(str(payload.get("content") or "").encode("utf-8"))}, ensure_ascii=False))
''',
            )
            data = await _read_sandbox_file_bytes_with_fallback(session=session, path=target)
        await _grant_sandbox_runtime_write_access(session=session, paths=[target])
        await _persist_sandbox_paths(session=session, changed_paths=[target])
        return {
            "operation": "write",
            "file_path": sandbox_path_to_visible(target),
            "mode": mode,
            "bytes": len(data),
        }


async def sandbox_apply_patch_ops(*, session_ref: SandboxExecutionSessionRef, ops: list[PatchOp], dry_run: bool) -> list[str]:
    session = require_sandbox_session(session_ref)
    async with session.operation_lock:
        await _recover_pending_persistence(session)
        resolved_paths: dict[str, str] = {}
        source: dict[str, FileState] = {}
        for op in ops:
            if op.path in resolved_paths:
                continue
            target = await _resolve_sandbox_real_path(
                session=session,
                file_path=op.path,
                writable=True,
            )
            resolved_paths[op.path] = target
            try:
                source[op.path] = FileState(
                    exists=True,
                    text=(await _read_sandbox_file_bytes_with_fallback(session=session, path=target)).decode(
                        "utf-8"
                    ),
                )
            except Exception as exc:
                if not _is_sandbox_file_not_found(exc):
                    raise
                source[op.path] = FileState(exists=False)
        actions, changes = plan_patch_ops(ops, source)
        if dry_run:
            return actions
        writes = [change for change in changes if change.exists]
        deletes = [resolved_paths[change.path] for change in changes if not change.exists]
        for change in writes:
            await _write_sandbox_file(
                sandbox=session.sandbox,
                path=resolved_paths[change.path],
                data=change.text.encode("utf-8"),
            )
        await _grant_sandbox_runtime_write_access(
            session=session,
            paths=[resolved_paths[change.path] for change in writes],
        )
        await _delete_sandbox_files(sandbox=session.sandbox, paths=deletes)
        await _persist_sandbox_paths(
            session=session,
            changed_paths=[resolved_paths[change.path] for change in writes],
            deleted_paths=deletes,
        )
        return actions


async def sandbox_write_tool_result(
    *,
    session_ref: SandboxExecutionSessionRef,
    file_path: str,
    content: str,
) -> dict[str, Any]:
    """Write a middleware-owned hidden result to the active sandbox view once."""
    session = require_sandbox_session(session_ref)
    async with session.operation_lock:
        await _recover_pending_persistence(session)
        target = await _resolve_sandbox_real_path(
            session=session,
            file_path=file_path,
            writable=True,
            allow_tool_result_write=True,
        )
        if not _is_tool_results_sandbox_path(target):
            raise ValueError("内部工具结果只能写入 outputs/.tool-results/")
        try:
            await _read_sandbox_file_bytes_with_fallback(session=session, path=target)
        except Exception as exc:
            if not _is_sandbox_file_not_found(exc):
                raise
        else:
            raise ValueError("工具结果文件已存在")
        data = content.encode("utf-8")
        await _write_sandbox_file(sandbox=session.sandbox, path=target, data=data)
        await _grant_sandbox_runtime_write_access(session=session, paths=[target])
        await _persist_sandbox_paths(session=session, changed_paths=[target])
        return {"file_path": sandbox_path_to_visible(target), "bytes": len(data)}


def truncate_output(text: str, limit: int = MAX_OUTPUT_CHARS) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n...[truncated]", True
