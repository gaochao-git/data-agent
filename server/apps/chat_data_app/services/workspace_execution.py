"""OpenSandbox workspace execution for DataAnalyze."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from datetime import timedelta
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import tarfile
import time
from typing import Any
from urllib.parse import unquote

from utils.logger import get_logger
from ..chart_types import chart_type_requires_data, chart_type_requires_x, chart_type_requires_y, is_supported_chart_type_input, normalize_chart_type

logger = get_logger(__name__)

OPENSANDBOX_BACKEND = "opensandbox"
OPENSANDBOX_REMOTE_HOST_VOLUME_MODE = "remote-host-volume"
MAX_OUTPUT_CHARS = 12000
EXCLUDED_NAMES = {".cache", ".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".venv", "__pycache__", "node_modules", "venv"}
SEED_DIRS = ("uploads", "workspace", "outputs")
def _sandbox_root() -> str:
    root = str(os.getenv("OPENSANDBOX_WORKSPACE_ROOT") or "/mnt").strip().rstrip("/")
    return root or "/mnt"


def sandbox_thread_root() -> str:
    return f"{_sandbox_root()}/user-data"


def sandbox_workspace_dir() -> str:
    return f"{sandbox_thread_root()}/workspace"


def sandbox_outputs_dir() -> str:
    return f"{sandbox_thread_root()}/outputs"


def sandbox_uploads_dir() -> str:
    return f"{sandbox_thread_root()}/uploads"


def sandbox_runtime_skills_dir() -> str:
    return f"{sandbox_thread_root()}/runtime-skills"


@dataclass
class SandboxExecutionSession:
    sandbox: Any
    sandbox_id: str
    workspace_root: str
    host_workspace_path: str


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


def _sanitize_path_part(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9-]+", "-", str(value or "").strip().lower())
    normalized = re.sub(r"-+", "-", normalized).strip("-")
    return normalized or "thread"


def get_host_workspace_path(workspace_root: str, user_id: str = "system") -> str:
    """返回 OpenSandbox 宿主机上的当前会话工作区叶子目录。"""
    remote_root = str(os.getenv("OPENSANDBOX_HOST_WORKSPACE_ROOT") or "/data/opensandbox-workspaces").strip().rstrip("/")
    if not remote_root or remote_root in {"/", "."} or "\x00" in remote_root:
        raise ValueError("OPENSANDBOX_HOST_WORKSPACE_ROOT 配置非法")
    project = _sanitize_path_part(os.getenv("OPENSANDBOX_HOST_PROJECT_NAME") or "fxcjg")
    user = _sanitize_path_part(user_id or "system")
    conversation_id = _sanitize_path_part(Path(workspace_root).resolve().name)
    date_part = _workspace_date_part(workspace_root)
    return f"{remote_root}/{project}/{user}/{date_part}/{conversation_id}"


def _workspace_date_part(workspace_root: str) -> str:
    marker = Path(workspace_root).resolve() / ".sandbox_workspace_meta.json"
    if marker.exists():
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
            value = str(payload.get("date") or "").strip()
            if re.fullmatch(r"\d{8}", value):
                return value
        except Exception:
            logger.warning("读取沙箱工作区元数据失败: %s", marker, exc_info=True)
    from datetime import datetime
    date_part = datetime.now().strftime("%Y%m%d")
    try:
        marker.write_text(json.dumps({"date": date_part}, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        logger.warning("写入沙箱工作区元数据失败: %s", marker, exc_info=True)
    return date_part


def _import_opensandbox_sdk() -> tuple[Any, Any, Any, Any]:
    try:
        from opensandbox import Sandbox
        from opensandbox.config import ConnectionConfig
        from opensandbox.models.execd import RunCommandOpts
        from opensandbox.models.sandboxes import Host, NetworkPolicy, NetworkRule, Volume
    except ImportError as exc:
        raise ValueError("OpenSandbox Python SDK 未安装或不支持 Host volume / NetworkPolicy，请升级 `opensandbox`") from exc
    return Sandbox, ConnectionConfig, RunCommandOpts, (Host, NetworkPolicy, NetworkRule, Volume)


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


def _workspace_session_cache_key(workspace_root: str, user_id: str = "system") -> str:
    return get_host_workspace_path(workspace_root, user_id)


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
    backend = str(os.getenv("WORKSPACE_EXECUTION_BACKEND") or OPENSANDBOX_BACKEND).strip().lower()
    if backend != OPENSANDBOX_BACKEND:
        raise ValueError("DataAnalyze 当前仅支持 OpenSandbox 执行后端，不支持 WORKSPACE_EXECUTION_BACKEND=" + backend)
    sync_mode = str(os.getenv("OPENSANDBOX_WORKSPACE_SYNC_MODE") or OPENSANDBOX_REMOTE_HOST_VOLUME_MODE).strip().lower()
    if sync_mode != OPENSANDBOX_REMOTE_HOST_VOLUME_MODE:
        raise ValueError("DataAnalyze 当前仅支持 OpenSandbox remote-host-volume 工作区，不支持 OPENSANDBOX_WORKSPACE_SYNC_MODE=" + sync_mode)


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
        api_key=str(os.getenv("OPENSANDBOX_API_KEY") or "").strip() or None,
        request_timeout=timedelta(seconds=max(30, timeout_seconds)),
        use_server_proxy=_str_to_bool(os.getenv("OPENSANDBOX_USE_SERVER_PROXY"), False),
    )


def _build_host_volume(*, Host: Any, Volume: Any, name: str, host_path: str, mount_path: str, read_only: bool) -> Any:
    return Volume(name=name, host=Host(path=host_path), mountPath=mount_path, readOnly=read_only)


def _build_network_policy() -> Any | None:
    action = _network_default_action()
    if not action or action == "allow":
        return None
    _, _, _, models = _import_opensandbox_sdk()
    _, NetworkPolicy, NetworkRule, _ = models
    egress_rules = None
    allow_targets = _network_allow_targets()
    if allow_targets:
        egress_rules = [NetworkRule(action="allow", target=target) for target in allow_targets]
    return NetworkPolicy(defaultAction=action, egress=egress_rules)


def _build_volumes(workspace_root: str, user_id: str = "system") -> list[Any]:
    _, _, _, models = _import_opensandbox_sdk()
    Host, _, _, Volume = models
    host_workspace_path = get_host_workspace_path(workspace_root, user_id)
    # DataAnalyze 在 skill_load 时将技能上传到 /mnt/user-data/runtime-skills，因此运行沙箱只挂载当前会话叶子目录。
    # 写入权限由工具层限制在 workspace/outputs，uploads/runtime-skills 不作为写入目标暴露给模型。
    return [
        _build_host_volume(Host=Host, Volume=Volume, name="thread-user-data", host_path=host_workspace_path, mount_path=sandbox_thread_root(), read_only=False)
    ]


def _should_exclude(path: Path) -> bool:
    return any(part in EXCLUDED_NAMES for part in path.parts)


def _build_seed_tar_bytes(thread_dir: str | Path) -> bytes:
    root = Path(thread_dir).resolve()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for dirname in SEED_DIRS:
            source = root / dirname
            if not source.exists():
                continue
            for current_root, dirnames, filenames in os.walk(source):
                current = Path(current_root)
                dirnames[:] = [item for item in dirnames if item not in EXCLUDED_NAMES]
                relative_current = current.relative_to(root)
                if _should_exclude(relative_current):
                    continue
                tar.add(current, arcname=relative_current.as_posix(), recursive=False)
                for filename in filenames:
                    file_path = current / filename
                    relative = file_path.relative_to(root)
                    if _should_exclude(relative):
                        continue
                    tar.add(file_path, arcname=relative.as_posix(), recursive=False)
    return buffer.getvalue()


def _build_directory_tar_bytes(source_dir: str | Path) -> bytes:
    root = Path(source_dir).resolve()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for current_root, dirnames, filenames in os.walk(root):
            current = Path(current_root)
            dirnames[:] = [item for item in dirnames if item not in EXCLUDED_NAMES]
            relative_current = current.relative_to(root)
            if relative_current.parts and _should_exclude(relative_current):
                continue
            arcname = "." if relative_current == Path(".") else relative_current.as_posix()
            tar.add(current, arcname=arcname, recursive=False)
            for filename in filenames:
                file_path = current / filename
                relative = file_path.relative_to(root)
                if _should_exclude(relative):
                    continue
                tar.add(file_path, arcname=relative.as_posix(), recursive=False)
    return buffer.getvalue()


async def _write_sandbox_file(*, sandbox: Any, path: str, data: bytes) -> None:
    files = getattr(sandbox, "files", None)
    writer = getattr(files, "write_file", None)
    if not callable(writer):
        raise ValueError("当前 OpenSandbox SDK 不支持 files.write_file")
    await writer(path, data, mode=0o644)


async def _read_sandbox_file_bytes(*, sandbox: Any, path: str) -> bytes:
    files = getattr(sandbox, "files", None)
    reader = getattr(files, "read_bytes", None)
    if not callable(reader):
        raise ValueError("当前 OpenSandbox SDK 不支持 files.read_bytes")
    return bytes(await reader(path))


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


async def run_command_in_sandbox(*, session: SandboxExecutionSession, command: str, timeout_seconds: int = 30) -> tuple[int, str, str]:
    _, _, RunCommandOpts, _ = _import_opensandbox_sdk()
    execution = await session.sandbox.commands.run(command, opts=RunCommandOpts(timeout=timedelta(seconds=max(1, timeout_seconds))))
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


async def prepare_workspace_execution_session(workspace_root: str, user_id: str = "system") -> SandboxExecutionSession:
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
        "volumes": _build_volumes(workspace_root, user_id),
        "connection_config": _connection_config(timeout_seconds),
        "metadata": {"managed_by": "fxcjg", "module": "chat_data_app", "workspace_backend": OPENSANDBOX_BACKEND},
    }
    network_policy = _build_network_policy()
    if network_policy is not None:
        create_kwargs["network_policy"] = network_policy
    sandbox = await Sandbox.create(**create_kwargs)
    session = SandboxExecutionSession(
        sandbox=sandbox,
        sandbox_id=str(getattr(sandbox, "id", "") or ""),
        workspace_root=workspace_root,
        host_workspace_path=get_host_workspace_path(workspace_root, user_id),
    )
    await _ensure_workspace_dirs(session=session)
    await _seed_workspace_if_needed(session=session)
    return session


async def get_workspace_execution_session(workspace_root: str, user_id: str = "system") -> SandboxExecutionSession:
    cache_key = _workspace_session_cache_key(workspace_root, user_id)
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
        session = await prepare_workspace_execution_session(workspace_root, user_id)
        _WORKSPACE_EXECUTION_SESSION_CACHE[cache_key] = CachedSandboxExecutionSession(
            session=session,
            expires_at=time.monotonic() + _workspace_session_ttl_seconds(),
        )
        return session


async def attach_workspace_execution_session(session: SandboxExecutionSession | None, user_id: str = "system") -> None:
    if session is None:
        return
    cache_key = _workspace_session_cache_key(session.workspace_root, user_id)
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
    cache_key = _workspace_session_cache_key(session.workspace_root, user_id)
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


async def _seed_workspace_if_needed(*, session: SandboxExecutionSession) -> None:
    await _ensure_workspace_dirs(session=session)
    marker_path = f"{sandbox_thread_root()}/.fxcjg-host-workspace-initialized"
    exit_code, _, _ = await run_command_in_sandbox(session=session, command="bash -lc " + shlex.quote(f"test -f {shlex.quote(marker_path)}"), timeout_seconds=30)
    if exit_code == 0:
        return
    archive_path = f"/tmp/fxcjg-host-seed-{_sanitize_path_part(Path(session.workspace_root).name)}.tar.gz"
    archive_data = await asyncio.to_thread(_build_seed_tar_bytes, session.workspace_root)
    await _write_sandbox_file(sandbox=session.sandbox, path=archive_path, data=archive_data)
    command = (
        "bash -lc "
        + shlex.quote(
            "set -e; "
            f"mkdir -p {shlex.quote(sandbox_thread_root())}; "
            f"tar --no-same-owner --no-same-permissions -xzf {shlex.quote(archive_path)} -C {shlex.quote(sandbox_thread_root())}; "
            f"mkdir -p {shlex.quote(sandbox_uploads_dir())} {shlex.quote(sandbox_workspace_dir())} {shlex.quote(sandbox_outputs_dir())} {shlex.quote(sandbox_runtime_skills_dir())}; "
            f"touch {shlex.quote(marker_path)}; "
            f"rm -f {shlex.quote(archive_path)}"
        )
    )
    exit_code, stdout, stderr = await run_command_in_sandbox(session=session, command=command, timeout_seconds=60)
    if exit_code != 0:
        raise ValueError("初始化 OpenSandbox host 工作区失败: " + (stderr or stdout).strip())


async def _ensure_workspace_dirs(*, session: SandboxExecutionSession) -> None:
    command = (
        "bash -lc "
        + shlex.quote(
            "set -e; "
            f"mkdir -p {shlex.quote(sandbox_uploads_dir())} {shlex.quote(sandbox_workspace_dir())} "
            f"{shlex.quote(sandbox_outputs_dir())} {shlex.quote(sandbox_runtime_skills_dir())}"
        )
    )
    exit_code, stdout, stderr = await run_command_in_sandbox(session=session, command=command, timeout_seconds=30)
    if exit_code != 0:
        raise ValueError("初始化 OpenSandbox 工作目录失败: " + (stderr or stdout).strip())


def sandbox_visible_path(path: str, *, writable: bool = False) -> str:
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
        ("/mnt/user-data/runtime-skills", sandbox_runtime_skills_dir()),
        ("/workspace", sandbox_workspace_dir()),
        ("/outputs", sandbox_outputs_dir()),
        ("/uploads", sandbox_uploads_dir()),
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
    allowed_roots = (sandbox_workspace_dir(), sandbox_outputs_dir(), sandbox_uploads_dir(), sandbox_runtime_skills_dir())
    if not any(sandbox_path == root or sandbox_path.startswith(f"{root}/") for root in allowed_roots):
        raise ValueError(f"只允许访问 workspace/outputs/uploads/runtime-skills: {path}")
    if writable:
        writable_roots = (sandbox_workspace_dir(), sandbox_outputs_dir())
        if not any(sandbox_path == root or sandbox_path.startswith(f"{root}/") for root in writable_roots):
            raise ValueError("只允许写入 workspace/ 或 outputs/")
    return sandbox_path


def sandbox_path_to_visible(path: str) -> str:
    mappings = (
        (sandbox_workspace_dir(), f"{sandbox_thread_root()}/workspace"),
        (sandbox_outputs_dir(), f"{sandbox_thread_root()}/outputs"),
        (sandbox_uploads_dir(), f"{sandbox_thread_root()}/uploads"),
        (sandbox_runtime_skills_dir(), sandbox_runtime_skills_dir()),
    )
    value = str(path or "").strip()
    for prefix, visible in mappings:
        if value == prefix:
            return visible
        if value.startswith(f"{prefix}/"):
            return f"{visible}{value[len(prefix):]}"
    return value


async def sandbox_read_text_file(*, session_ref: SandboxExecutionSessionRef, file_path: str, offset: int, limit: int) -> dict[str, Any]:
    session = require_sandbox_session(session_ref)
    target = sandbox_visible_path(file_path)
    try:
        data = await _read_sandbox_file_bytes(sandbox=session.sandbox, path=target)
    except FileNotFoundError:
        return {"exists": False, "path": sandbox_path_to_visible(target)}
    except IsADirectoryError:
        return {"exists": True, "is_dir": True, "path": sandbox_path_to_visible(target)}
    if b"\x00" in data[:4096]:
        return {"exists": True, "is_binary": True, "path": sandbox_path_to_visible(target)}
    lines = data.decode("utf-8", errors="replace").splitlines()
    start = max(0, int(offset or 0))
    count = max(1, int(limit or 1))
    selected = lines[start:start + count]
    return {
        "exists": True,
        "is_dir": False,
        "is_binary": False,
        "path": sandbox_path_to_visible(target),
        "lines": selected,
        "total_lines": len(lines),
        "start_line": start + 1,
        "end_line": start + len(selected),
        "has_more": start + len(selected) < len(lines),
    }


def _is_outputs_sandbox_path(path: str) -> bool:
    return path == sandbox_outputs_dir() or path.startswith(f"{sandbox_outputs_dir()}/")


def _artifact_kind_from_path(path: str) -> str:
    name = PurePosixPath(str(path or "")).name.lower()
    if name.endswith(".chart.json"):
        return "chart_spec"
    if name.endswith(".dashboard.json"):
        return "dashboard_spec"
    if name.endswith(".table.json") or name.endswith(".table.csv"):
        return "table"
    if name.endswith(".docx"):
        return "document"
    if name.endswith(".xlsx"):
        return "spreadsheet"
    if name.endswith(".pdf"):
        return "pdf"
    if name.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
        return "image"
    return ""


def _parse_chart_spec_payload(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    if not is_supported_chart_type_input(payload.get("chart_type")):
        return None
    chart_type = normalize_chart_type(payload.get("chart_type"))
    payload["chart_type"] = chart_type
    if str(payload.get("version") or "").strip() != "1.0":
        return None
    if chart_type == "graph":
        graph_payload = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        links = graph_payload.get("links") if isinstance(graph_payload.get("links"), list) else graph_payload.get("edges")
        if isinstance(graph_payload.get("nodes"), list) and isinstance(links, list) and graph_payload.get("nodes") and links:
            return payload
    if not isinstance(payload.get("data"), list):
        return None
    data = payload.get("data")
    if chart_type_requires_data(chart_type) and not data:
        return None
    encoding = payload.get("encoding")
    if not isinstance(encoding, dict):
        return None
    y_fields = encoding.get("y")
    if chart_type_requires_y(chart_type) and (not isinstance(y_fields, list) or not any(str(item).strip() for item in y_fields)):
        return None
    if chart_type_requires_x(chart_type) and not str(encoding.get("x") or "").strip():
        return None
    if data:
        row_fields = set()
        for row in data:
            if isinstance(row, dict):
                row_fields.update(str(key) for key in row.keys())
        x_field = str(encoding.get("x") or "").strip()
        if chart_type_requires_x(chart_type) and x_field and x_field not in row_fields:
            return None
        if isinstance(y_fields, list):
            for item in y_fields:
                y_field = str(item or "").strip()
                if y_field and y_field not in row_fields:
                    return None
    return payload


def _artifact_payload_from_bytes(*, visible_path: str, data: bytes) -> dict[str, Any]:
    artifact_kind = _artifact_kind_from_path(visible_path)
    payload: Any = None
    if visible_path.lower().endswith(".json"):
        try:
            payload = json.loads(data.decode("utf-8", errors="replace"))
        except Exception:
            payload = None
    if artifact_kind == "dashboard_spec" and isinstance(payload, dict):
        return {
            "file_path": visible_path,
            "artifact_type": "dashboard_spec",
            "renderer": "dashboard",
            "artifact_version": "v1",
            "artifact_payload": payload,
            "dashboard_spec": payload,
        }
    if artifact_kind == "chart_spec" and isinstance(payload, dict):
        chart_payload = _parse_chart_spec_payload(payload)
        if chart_payload is not None:
            return {
                "file_path": visible_path,
                "artifact_type": "chart_spec",
                "renderer": "chart",
                "artifact_version": "v1",
                "artifact_payload": chart_payload,
                "chart_spec": chart_payload,
            }
    if isinstance(payload, dict) and isinstance(payload.get("cards"), list):
        return {
            "file_path": visible_path,
            "artifact_type": "dashboard_spec",
            "renderer": "dashboard",
            "artifact_version": "v1",
            "artifact_payload": payload,
            "dashboard_spec": payload,
        }
    if isinstance(payload, dict):
        chart_payload = _parse_chart_spec_payload(payload)
        if chart_payload is not None:
            return {
                "file_path": visible_path,
                "artifact_type": "chart_spec",
                "renderer": "chart",
                "artifact_version": "v1",
                "artifact_payload": chart_payload,
                "chart_spec": chart_payload,
            }
    renderer_map = {
        "table": "table",
        "document": "document",
        "spreadsheet": "spreadsheet",
        "pdf": "pdf",
        "image": "image",
    }
    if artifact_kind in renderer_map:
        return {
            "file_path": visible_path,
            "artifact_type": artifact_kind,
            "renderer": renderer_map[artifact_kind],
            "artifact_version": "v1",
        }
    return {}


async def sandbox_artifact_payload(*, session_ref: SandboxExecutionSessionRef, file_path: str) -> dict[str, Any]:
    session = require_sandbox_session(session_ref)
    target = sandbox_visible_path(file_path)
    if not _is_outputs_sandbox_path(target):
        return {}
    try:
        data = await _read_sandbox_file_bytes(sandbox=session.sandbox, path=target)
    except Exception:
        return {}
    visible_path = sandbox_path_to_visible(target)
    return _artifact_payload_from_bytes(visible_path=visible_path, data=data)


async def sandbox_write_text_file(*, session_ref: SandboxExecutionSessionRef, file_path: str, content: str, mode: str) -> dict[str, Any]:
    session = require_sandbox_session(session_ref)
    target = sandbox_visible_path(file_path, writable=True)
    if mode == "overwrite":
        await run_sandbox_python_json(
            session=session,
            payload={"parent": str(PurePosixPath(target).parent)},
            script_body="from pathlib import Path\nPath(payload['parent']).mkdir(parents=True, exist_ok=True)\nprint(json.dumps({'ok': True}, ensure_ascii=False))",
        )
        data = content.encode("utf-8")
        await _write_sandbox_file(sandbox=session.sandbox, path=target, data=data)
        return {"operation": "write", "file_path": sandbox_path_to_visible(target), "mode": mode, "bytes": len(data)}
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
    return {"operation": "write", "file_path": sandbox_path_to_visible(target), "mode": mode, "bytes": int(result.get("bytes") or 0)}


async def sandbox_apply_patch_ops(*, session_ref: SandboxExecutionSessionRef, ops: list[dict[str, Any]], dry_run: bool) -> list[str]:
    session = require_sandbox_session(session_ref)
    normalized_ops = []
    for op in ops:
        item = dict(op)
        item["path"] = sandbox_visible_path(str(item.get("path") or ""), writable=True)
        normalized_ops.append(item)
    result = await run_sandbox_python_json(
        session=session,
        payload={"ops": normalized_ops, "dry_run": bool(dry_run)},
        script_body=r'''
from pathlib import Path

class PatchError(Exception):
    pass

def find_hunk_start(content, pattern, start_index):
    if not pattern:
        return start_index
    max_start = len(content) - len(pattern)
    for index in range(max(start_index, 0), max_start + 1):
        if content[index:index + len(pattern)] == pattern:
            return index
    raise PatchError(f"failed to locate hunk context: {pattern[:3]}")

actions = []
state = {}
for op in payload.get("ops") or []:
    path = Path(op["path"])
    key = str(path)
    if key not in state:
        state[key] = {"exists": path.exists(), "text": path.read_text(encoding="utf-8") if path.exists() else ""}
    current = state[key]
    kind = op.get("kind")
    if kind == "add":
        if current["exists"]:
            raise PatchError(f"cannot add existing file: {op.get('visible_path') or path}")
        current["exists"] = True
        current["text"] = "\n".join(op.get("lines") or []) + ("\n" if op.get("lines") else "")
        actions.append(f"add {op.get('visible_path') or path}")
    elif kind == "delete":
        if not current["exists"]:
            raise PatchError(f"cannot delete missing file: {op.get('visible_path') or path}")
        current["exists"] = False
        current["text"] = ""
        actions.append(f"delete {op.get('visible_path') or path}")
    elif kind == "update":
        if not current["exists"]:
            raise PatchError(f"cannot update missing file: {op.get('visible_path') or path}")
        updated = current["text"].splitlines()
        search_start = 0
        for hunk in op.get("hunks") or []:
            lines = hunk.get("lines") or []
            old_lines = [line[1:] for line in lines if line and line[0] in (" ", "-")]
            new_lines = [line[1:] for line in lines if line and line[0] in (" ", "+")]
            start = find_hunk_start(updated, old_lines, search_start)
            updated[start:start + len(old_lines)] = new_lines
            search_start = start + len(new_lines)
        current["text"] = "\n".join(updated) + ("\n" if current["text"].endswith("\n") or updated else "")
        actions.append(f"update {op.get('visible_path') or path}")
    else:
        raise PatchError(f"unsupported op: {kind}")

if not payload.get("dry_run"):
    for raw_path, item in state.items():
        path = Path(raw_path)
        if item["exists"]:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(item["text"], encoding="utf-8")
        elif path.exists():
            path.unlink()
print(json.dumps({"actions": actions}, ensure_ascii=False))
''',
    )
    return [str(item) for item in result.get("actions") or []]


async def upload_skill_directory_to_sandbox(*, session_ref: SandboxExecutionSessionRef, skill_name: str, source_dir: Path) -> str:
    session = require_sandbox_session(session_ref)
    safe_name = _sanitize_path_part(skill_name)
    if safe_name != skill_name:
        raise ValueError(f"非法技能名称: {skill_name}")
    target_dir = f"{sandbox_runtime_skills_dir()}/{skill_name}"
    archive_path = f"/tmp/fxcjg-skill-{skill_name}.tar.gz"
    archive_data = await asyncio.to_thread(_build_directory_tar_bytes, source_dir)
    await _write_sandbox_file(sandbox=session.sandbox, path=archive_path, data=archive_data)
    command = (
        "bash -lc "
        + shlex.quote(
            "set -e; "
            f"rm -rf {shlex.quote(target_dir)}; "
            f"mkdir -p {shlex.quote(target_dir)}; "
            f"tar --no-same-owner --no-same-permissions -xzf {shlex.quote(archive_path)} -C {shlex.quote(target_dir)}; "
            f"rm -f {shlex.quote(archive_path)}"
        )
    )
    exit_code, stdout, stderr = await run_command_in_sandbox(session=session, command=command, timeout_seconds=60)
    if exit_code != 0:
        raise ValueError("上传技能到 OpenSandbox 失败: " + (stderr or stdout).strip())
    return sandbox_path_to_visible(target_dir)


async def read_workspace_file_from_sandbox(*, workspace_root: str, file_path: str, user_id: str = "system", max_bytes: int = 1024 * 1024) -> dict[str, Any]:
    session = await get_workspace_execution_session(workspace_root, user_id)
    try:
        target = sandbox_visible_path(file_path)
        data = await _read_sandbox_file_bytes(sandbox=session.sandbox, path=target)
        if len(data) > max_bytes:
            raise ValueError(f"文件过大，暂不支持预览超过 {max_bytes} 字节的文件")
        result = {
            "path": sandbox_path_to_visible(target),
            "relative_path": sandbox_path_to_visible(target).lstrip("/"),
            "content": data.decode("utf-8", errors="replace"),
            "size": len(data),
        }
    except Exception:
        await invalidate_workspace_execution_session(session, user_id)
        raise
    return result


async def list_workspace_files_from_sandbox(*, workspace_root: str, user_id: str = "system") -> list[dict[str, Any]]:
    session = await get_workspace_execution_session(workspace_root, user_id)
    try:
        result = await run_sandbox_python_json(
            session=session,
            payload={
                "roots": [
                    {"path": sandbox_uploads_dir(), "source": "uploads"},
                    {"path": sandbox_runtime_skills_dir(), "source": "runtime-skills"},
                    {"path": sandbox_workspace_dir(), "source": "workspace"},
                    {"path": sandbox_outputs_dir(), "source": "outputs"},
                ],
            },
            script_body=r'''
from pathlib import Path
import json
roots = payload.get("roots") or []
items = []
for root_item in roots:
    root = Path(root_item.get("path") or "")
    source = str(root_item.get("source") or root.name)
    if not root.exists():
        continue
    for item in sorted(root.rglob("*")):
        if not item.is_file():
            continue
        stat = item.stat()
        relative = item.relative_to(root).as_posix()
        visible_path = "/mnt/user-data/" + source + (("/" + relative) if relative else "")
        row = {
            "path": visible_path,
            "relative_path": visible_path,
            "directory": source,
            "name": item.name,
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "source": source,
        }
        if source == "outputs" and item.name.lower().endswith(".json") and stat.st_size <= 1048576:
            try:
                row["content_hex"] = item.read_bytes().hex()
            except Exception:
                pass
        items.append(row)
print(json.dumps({"files": items}, ensure_ascii=False))
''',
            timeout_seconds=30,
        )
        files: list[dict[str, Any]] = []
        for item in result.get("files") or []:
            if not isinstance(item, dict):
                continue
            content_hex = item.pop("content_hex", "")
            if content_hex:
                try:
                    artifact = _artifact_payload_from_bytes(visible_path=str(item.get("path") or ""), data=bytes.fromhex(str(content_hex)))
                    item.update(artifact)
                except Exception:
                    logger.warning("识别 outputs 产物失败: %s", item.get("path"), exc_info=True)
            elif str(item.get("source") or "") == "outputs":
                item.update(_artifact_payload_from_bytes(visible_path=str(item.get("path") or ""), data=b""))
            files.append(item)
        result_files = files
    except Exception:
        await invalidate_workspace_execution_session(session, user_id)
        raise
    return result_files


def truncate_output(text: str, limit: int = MAX_OUTPUT_CHARS) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n...[truncated]", True
