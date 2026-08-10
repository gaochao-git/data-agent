"""ChatData 数据源连接凭证与执行配置。"""

from __future__ import annotations

import os
from typing import Any, Dict

from utils.crypto import decrypt_password
from utils.logger import get_logger
from ..runtime_config import read_positive_int_env

logger = get_logger(__name__)
HIVE_SOCKET_TIMEOUT_SECONDS = read_positive_int_env("CHAT_DATA_HIVE_SOCKET_TIMEOUT_SECONDS", 10)


def _get_env_value(name: str) -> str | None:
    value = str(os.getenv(name) or "").strip()
    return value or None


def _get_env_password(name: str) -> str | None:
    value = _get_env_value(name)
    if not value:
        return None
    return decrypt_password(value)


def get_http_auth_token() -> str:
    """读取外部 HTTP 服务认证 token，环境变量中存储 SM4 密文。"""
    return _get_env_password("CHAT_DATA_HTTP_AUTH_TOKEN") or ""


def apply_hive_socket_timeout(transport: Any, timeout_seconds: int) -> None:
    """尽量将超时设置到 Thrift 底层 socket。"""
    if timeout_seconds <= 0 or transport is None:
        return

    visited: set[int] = set()
    stack = [transport]
    timeout_ms = int(timeout_seconds * 1000)
    while stack:
        current = stack.pop()
        if current is None or id(current) in visited:
            continue
        visited.add(id(current))
        set_timeout = getattr(current, "setTimeout", None)
        if callable(set_timeout):
            try:
                set_timeout(timeout_ms)
            except Exception as exc:
                logger.warning("设置 Hive transport 超时失败: %s", exc)
        handle = getattr(current, "handle", None)
        if handle is not None and hasattr(handle, "settimeout"):
            try:
                handle.settimeout(timeout_seconds)
            except Exception as exc:
                logger.warning("设置 Hive socket 超时失败: %s", exc)
        for attr_name in ("_trans", "_transport", "_sock", "sock", "_TSaslClientTransport__trans"):
            nested = getattr(current, attr_name, None)
            if nested is not None:
                stack.append(nested)


def sanitize_source_config(source_config: Dict[str, Any] | None) -> Dict[str, Any]:
    """移除不应落盘的账号密码字段。"""
    result = dict(source_config or {})
    result.pop("user", None)
    result.pop("username", None)
    result.pop("password", None)
    return result


def resolve_mysql_credentials(source_config: Dict[str, Any] | None = None) -> Dict[str, str]:
    """获取 MySQL 统一连接凭证。"""
    user = _get_env_value("CHAT_DATA_MYSQL_USER")
    password = _get_env_password("CHAT_DATA_MYSQL_PASSWORD")
    if password is None:
        password = ""
    if not user:
        raise ValueError("缺少 MySQL 统一连接账号配置: CHAT_DATA_MYSQL_USER")
    return {"user": user, "password": password}


def resolve_hive_credentials(source_config: Dict[str, Any] | None = None) -> Dict[str, str]:
    """获取 Hive 统一连接凭证，免认证 Hive 可返回空凭证。"""
    username = _get_env_value("CHAT_DATA_HIVE_USER")
    password = _get_env_password("CHAT_DATA_HIVE_PASSWORD")
    if password and not username:
        raise ValueError("缺少 Hive 统一连接账号配置: CHAT_DATA_HIVE_USER")

    result: Dict[str, str] = {}
    if username:
        result["username"] = username
    if password:
        result["password"] = password
    return result


def resolve_execution_source_config(
    source_type: str,
    query_executor_type: str,
    source_config: Dict[str, Any] | None,
    *,
    org_code: str = "",
    org_name: str = "",
) -> Dict[str, Any]:
    """构造执行器使用的完整配置，统一处理身份与服务端凭据。"""
    normalized_source_type = str(source_type or "").strip().lower()
    normalized_executor_type = str(query_executor_type or "direct").strip().lower() or "direct"
    if normalized_source_type not in {"mysql", "hive"}:
        raise ValueError(f"不支持的数据源类型: {normalized_source_type}，只支持 mysql/hive")
    if normalized_executor_type not in {"direct", "http"}:
        raise ValueError(f"不支持的查询执行方式: {normalized_executor_type}")

    resolved = dict(source_config or {})
    if normalized_executor_type == "http":
        resolved_org_code = str(org_code or resolved.get("org_code") or "").strip()
        if not resolved_org_code:
            raise ValueError("HTTP 执行对象缺少机构编码")
        resolved["org_code"] = resolved_org_code
        resolved["org_name"] = str(org_name or resolved.get("org_name") or "").strip()
        return resolved

    if normalized_source_type == "mysql":
        if not resolved.get("user") or "password" not in resolved:
            resolved.update(resolve_mysql_credentials(resolved))
        return resolved

    if not resolved.get("username") or "password" not in resolved:
        resolved.update(resolve_hive_credentials(resolved))
    return resolved


def build_hive_connect_params(
    host: str,
    port: int = 10000,
    database: str = "default",
    username: str | None = None,
    password: str | None = None,
) -> Dict[str, Any]:
    """构造 PyHive 连接参数，免认证模式不传空账号密码。"""
    params: Dict[str, Any] = {
        "host": host,
        "port": port,
        "database": database,
    }
    if username:
        params["username"] = username
    if password:
        params["password"] = password
    return params
