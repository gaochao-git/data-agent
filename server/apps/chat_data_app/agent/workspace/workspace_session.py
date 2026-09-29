"""Cached workspace execution session lifecycle."""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx

from utils.crypto import decrypt_env_value
from utils.logger import get_logger

from .workspace_sandbox_api import (
    SandboxExecutionSession,
    _import_opensandbox_sdk,
)
from .workspace_sandbox_paths import _application_workspace_root
from .workspace_sync import (
    _initialize_sandbox_filesystem,
    _mutable_application_metadata_manifest,
    _sync_sandbox_uploads,
)

logger = get_logger(__name__)

OPENSANDBOX_BACKEND = "opensandbox"


@dataclass
class CachedSandboxExecutionSession:
    session: SandboxExecutionSession
    expires_at: float


_WORKSPACE_EXECUTION_SESSION_CACHE: dict[str, CachedSandboxExecutionSession] = {}
_WORKSPACE_EXECUTION_SESSION_LOCKS: dict[str, asyncio.Lock] = {}


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


async def _cached_session_matches_application(session: SandboxExecutionSession) -> bool:
    """Compare cached mutable trees without rereading every file body."""
    current_manifest = await asyncio.to_thread(
        _mutable_application_metadata_manifest,
        _application_workspace_root(session.workspace_root),
    )
    expected_manifest = {
        path: (fingerprint[0], fingerprint[1]) for path, fingerprint in (session.application_manifest or {}).items()
    }
    return current_manifest == expected_manifest


def _workspace_session_lock(cache_key: str) -> asyncio.Lock:
    lock = _WORKSPACE_EXECUTION_SESSION_LOCKS.get(cache_key)
    if lock is None:
        lock = asyncio.Lock()
        _WORKSPACE_EXECUTION_SESSION_LOCKS[cache_key] = lock
    return lock


def _connection_config(request_timeout_seconds: int) -> Any:
    _, ConnectionConfig, _, _ = _import_opensandbox_sdk()
    sandbox_url = str(os.getenv("OPENSANDBOX_URL") or "").strip()
    if not sandbox_url:
        raise ValueError("未配置 OPENSANDBOX_URL，无法创建 OpenSandbox")
    return ConnectionConfig(
        domain=sandbox_url,
        api_key=decrypt_env_value("OPENSANDBOX_API_KEY") or None,
        request_timeout=timedelta(seconds=max(1, request_timeout_seconds)),
        use_server_proxy=_str_to_bool(os.getenv("OPENSANDBOX_USE_SERVER_PROXY"), False),
    )


def _has_exception_type(exc: BaseException, expected_types: tuple[type[BaseException], ...]) -> bool:
    current: BaseException | None = exc
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        if isinstance(current, expected_types):
            return True
        visited.add(id(current))
        current = current.__cause__ or current.__context__
    return False


def _raise_sandbox_create_error(exc: Exception) -> None:
    if _has_exception_type(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        raise ValueError("OpenSandbox 服务不可达，请检查服务状态、OPENSANDBOX_URL 和网络连通性") from exc
    if _has_exception_type(exc, (httpx.TimeoutException,)):
        raise ValueError("OpenSandbox 控制面请求超时，请检查服务状态和网络质量") from exc
    raise exc


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


async def prepare_workspace_execution_session(
    workspace_root: str,
    user_id: str = "system",
    mode: str = "sandbox",
    runtime_skills_dir: Path | None = None,
) -> SandboxExecutionSession:
    _validate_workspace_backend_config()
    Sandbox, _, _, _ = _import_opensandbox_sdk()
    lifetime_seconds = _env_int("OPENSANDBOX_TIMEOUT_SECONDS", 600)
    request_timeout = _env_int("OPENSANDBOX_REQUEST_TIMEOUT_SECONDS", 10)
    ready_timeout = _env_int("OPENSANDBOX_READY_TIMEOUT_SECONDS", 60, minimum=10)
    create_timeout = _env_int(
        "OPENSANDBOX_CREATE_TIMEOUT_SECONDS",
        ready_timeout + request_timeout * 3 + 10,
        minimum=ready_timeout,
    )
    image = str(os.getenv("OPENSANDBOX_IMAGE") or "ubuntu").strip() or "ubuntu"
    create_kwargs = {
        "image": image,
        "timeout": timedelta(seconds=lifetime_seconds),
        "ready_timeout": timedelta(seconds=ready_timeout),
        "entrypoint": ["sleep", "infinity"],
        "connection_config": _connection_config(request_timeout),
        "metadata": {
            "managed_by": "fxcjg",
            "module": "chat_data_app",
            "workspace_backend": OPENSANDBOX_BACKEND,
            "sandbox_mode": mode,
        },
    }
    network_policy = _build_network_policy()
    if network_policy is not None:
        create_kwargs["network_policy"] = network_policy
    try:
        sandbox = await asyncio.wait_for(
            Sandbox.create(**create_kwargs),
            timeout=create_timeout,
        )
    except TimeoutError as exc:
        raise ValueError(f"OpenSandbox 创建超时（{create_timeout} 秒），请检查服务状态、镜像和网络连通性") from exc
    except Exception as exc:
        _raise_sandbox_create_error(exc)
    session = SandboxExecutionSession(
        sandbox=sandbox,
        sandbox_id=str(getattr(sandbox, "id", "") or ""),
        workspace_root=str(_application_workspace_root(workspace_root)),
        mode=mode,
        runtime_skills_dir=runtime_skills_dir,
        cache_key=_workspace_session_cache_key(
            workspace_root,
            mode,
            runtime_skills_dir,
        ),
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
    cache_key = _workspace_session_cache_key(
        workspace_root,
        mode,
        runtime_skills_dir,
    )
    now = time.monotonic()
    cached = _WORKSPACE_EXECUTION_SESSION_CACHE.get(cache_key)
    if cached is not None and cached.expires_at > now:
        async with cached.session.operation_lock:
            if await _cached_session_matches_application(cached.session):
                await _sync_sandbox_uploads(cached.session)
                return cached.session

    async with _workspace_session_lock(cache_key):
        now = time.monotonic()
        cached = _WORKSPACE_EXECUTION_SESSION_CACHE.get(cache_key)
        if cached is not None and cached.expires_at > now:
            async with cached.session.operation_lock:
                if await _cached_session_matches_application(cached.session):
                    await _sync_sandbox_uploads(cached.session)
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


async def invalidate_workspace_execution_session(
    session: SandboxExecutionSession | None, user_id: str = "system"
) -> None:
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
