"""OpenSandbox workspace execution for ChatData conversations."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from datetime import timedelta
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import time
from typing import Any
from urllib.parse import unquote

from utils.crypto import decrypt_env_value
from utils.logger import get_logger
from utils.storage_paths import CHAT_DATA_HOME
from .artifact_registry import parse_output_artifact

logger = get_logger(__name__)

OPENSANDBOX_BACKEND = "opensandbox"
MAX_OUTPUT_CHARS = 12000


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
    return f"{_sandbox_root()}/skills"


@dataclass
class SandboxExecutionSession:
    sandbox: Any
    sandbox_id: str
    workspace_root: str
    host_workspace_path: str
    mode: str = "sandbox"
    runtime_skill_mounts: tuple[object, ...] = ()
    cache_key: str = ""


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


def _resolve_host_root(name: str, *, application_root: Path | None = None) -> Path:
    configured = str(os.getenv(name) or "").strip()
    if not configured:
        if application_root is None:
            raise ValueError(
                f"{name} 未设置：使用 OpenSandbox 后端时必须显式配置宿主工作区根目录"
            )
        path = application_root.expanduser()
    else:
        path = Path(configured).expanduser()
    if (
        not path.is_absolute()
        or path == Path("/")
        or ".." in path.parts
        or "\x00" in str(path)
    ):
        raise ValueError(f"{name} 配置非法：必须为绝对路径且不能是根目录")
    # 该路径由 OpenSandbox 宿主机解释，不能在应用节点解析符号链接。
    return path


def _translate_host_path(
    path: Path,
    *,
    application_root: Path,
    host_root_name: str,
    allow_application_root: bool = False,
) -> str:
    resolved = path.expanduser().resolve()
    root = application_root.expanduser().resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"路径不在允许的根目录内: {path}") from exc
    host_root = _resolve_host_root(
        host_root_name,
        application_root=root if allow_application_root else None,
    )
    return str(host_root / relative)


def get_host_workspace_path(workspace_root: str) -> str:
    """将应用侧会话目录翻译为 OpenSandbox 宿主机共享卷路径。

    ``OPENSANDBOX_HOST_CHAT_DATA_HOME`` 为空时表示两端使用同一绝对路径；
    非空时必须是 Sandbox 宿主机看到同一共享卷的根目录，路径按 ``CHAT_DATA_HOME``
    的相对位置转换。容器内路径始终固定为 ``/mnt/user-data``。
    """
    resolved = Path(workspace_root).expanduser().resolve()
    root = Path(CHAT_DATA_HOME).expanduser().resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"会话工作区不在 CHAT_DATA_HOME 内: {workspace_root}")
    return _translate_host_path(
        resolved,
        application_root=root,
        host_root_name="OPENSANDBOX_HOST_CHAT_DATA_HOME",
        allow_application_root=True,
    )


def _runtime_skill_mount_values(mount: object) -> tuple[str, str, Path]:
    name = str(getattr(mount, "name", "")).strip()
    category = str(getattr(mount, "category", "")).strip()
    source_dir = Path(getattr(mount, "source_dir", "")).expanduser().resolve()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,63}", name) or category not in {"system", "user"}:
        raise ValueError("运行时技能挂载配置非法")
    if not source_dir.is_dir():
        raise ValueError(f"技能目录不存在: {source_dir}")
    return name, category, source_dir


def _get_host_skill_path(source_dir: Path, category: str) -> str:
    resolved = source_dir.expanduser().resolve()
    skill_root_name = category
    root = (Path(CHAT_DATA_HOME) / "skills" / skill_root_name).expanduser().resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"技能不在 CHAT_DATA_HOME/skills/{skill_root_name} 内: {source_dir}")
    return _translate_host_path(
        resolved,
        application_root=Path(CHAT_DATA_HOME),
        host_root_name="OPENSANDBOX_HOST_CHAT_DATA_HOME",
        allow_application_root=True,
    )


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


def _workspace_session_cache_key(
    workspace_root: str,
    mode: str = "sandbox",
    runtime_skill_mounts: tuple[object, ...] = (),
) -> str:
    mounted_skills = sorted(
        f"{category}/{name}:{source_dir}"
        for name, category, source_dir in (_runtime_skill_mount_values(item) for item in runtime_skill_mounts)
    )
    return f"{get_host_workspace_path(workspace_root)}#{mode}#{'|'.join(mounted_skills)}"


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
    # OpenSandbox 后端固定使用宿主机 volume。CHAT_DATA_HOME 必须显式指定为绝对路径，
    # 且物理上位于沙箱服务端 allowed_host_paths 之内（见 sandbox.toml）。相对路径
    # 会按进程 cwd 解析，几乎必然不在服务端白名单内，创建沙箱时才以 SandboxApiException
    # 暴露，排查成本极高，这里 fail-fast 强制绝对路径。
    raw_home = str(os.getenv("CHAT_DATA_HOME") or "").strip()
    if not Path(raw_home).expanduser().is_absolute():
        raise ValueError(
            "sandbox 模式下 CHAT_DATA_HOME 必须为绝对路径，"
            "且须位于 OpenSandbox 服务端 allowed_host_paths 之下"
        )
    _resolve_host_root(
        "OPENSANDBOX_HOST_CHAT_DATA_HOME",
        application_root=Path(CHAT_DATA_HOME),
    )



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


def _build_volumes(workspace_root: str, runtime_skill_mounts: tuple[object, ...] = ()) -> list[Any]:
    _, _, _, models = _import_opensandbox_sdk()
    Host, _, _, Volume = models
    volumes = [
        _build_host_volume(
            Host=Host,
            Volume=Volume,
            name="thread-user-data",
            host_path=get_host_workspace_path(workspace_root),
            mount_path=sandbox_thread_root(),
            read_only=False,
        )
    ]
    for name, category, source_dir in (_runtime_skill_mount_values(item) for item in runtime_skill_mounts):
        volumes.append(
            _build_host_volume(
                Host=Host,
                Volume=Volume,
                name=f"skill-{category}-{name}",
                host_path=_get_host_skill_path(source_dir, category),
                mount_path=f"{sandbox_skills_root()}/{category}/{name}",
                read_only=True,
            )
        )
    return volumes


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


async def prepare_workspace_execution_session(
    workspace_root: str,
    user_id: str = "system",
    mode: str = "sandbox",
    runtime_skill_mounts: tuple[object, ...] = (),
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
        "volumes": _build_volumes(workspace_root, runtime_skill_mounts),
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
        workspace_root=workspace_root,
        host_workspace_path=get_host_workspace_path(workspace_root),
        mode=mode,
        runtime_skill_mounts=runtime_skill_mounts,
        cache_key=_workspace_session_cache_key(workspace_root, mode, runtime_skill_mounts),
    )
    return session


async def get_workspace_execution_session(
    workspace_root: str,
    user_id: str = "system",
    mode: str = "sandbox",
    runtime_skill_mounts: tuple[object, ...] = (),
) -> SandboxExecutionSession:
    cache_key = _workspace_session_cache_key(workspace_root, mode, runtime_skill_mounts)
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
            runtime_skill_mounts,
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
        session.runtime_skill_mounts,
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
        session.runtime_skill_mounts,
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


def _sandbox_runtime_skill_roots(runtime_skill_mounts: tuple[object, ...]) -> tuple[str, ...]:
    """返回当前会话已授权的沙箱技能只读挂载根目录。"""
    return tuple(
        f"{sandbox_skills_root()}/{category}/{name}"
        for name, category, _source_dir in (_runtime_skill_mount_values(item) for item in runtime_skill_mounts)
    )


def sandbox_visible_path(
    path: str,
    *,
    writable: bool = False,
    runtime_skill_mounts: tuple[object, ...] = (),
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
    workspace_roots = (sandbox_workspace_dir(), sandbox_outputs_dir(), sandbox_uploads_dir())
    skill_roots = _sandbox_runtime_skill_roots(runtime_skill_mounts)
    readable_roots = workspace_roots + skill_roots
    if not any(sandbox_path == root or sandbox_path.startswith(f"{root}/") for root in readable_roots):
        raise ValueError(f"只允许访问 workspace/、outputs/、uploads/ 或当前已加载的技能路径: {path}")
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
        (sandbox_skills_root(), sandbox_skills_root()),
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
    target = sandbox_visible_path(file_path, runtime_skill_mounts=session.runtime_skill_mounts)
    try:
        data = await _read_sandbox_file_bytes_with_fallback(session=session, path=target)
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


def _artifact_payload_from_bytes(*, visible_path: str, data: bytes) -> dict[str, Any]:
    return parse_output_artifact(visible_path=visible_path, data=data)


async def sandbox_artifact_payload(*, session_ref: SandboxExecutionSessionRef, file_path: str) -> dict[str, Any]:
    session = require_sandbox_session(session_ref)
    target = sandbox_visible_path(file_path)
    if not _is_outputs_sandbox_path(target):
        return {}
    try:
        data = await _read_sandbox_file_bytes_with_fallback(session=session, path=target)
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


def truncate_output(text: str, limit: int = MAX_OUTPUT_CHARS) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n...[truncated]", True
