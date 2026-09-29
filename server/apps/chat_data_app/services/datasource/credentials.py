"""ChatData 数据源连接凭证与执行配置。"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from utils.crypto import decrypt_password
from utils.logger import get_logger

from ..runtime_config import read_bool_env, read_positive_int_env

logger = get_logger(__name__)
HIVE_SOCKET_TIMEOUT_SECONDS = read_positive_int_env("CHAT_DATA_HIVE_SOCKET_TIMEOUT_SECONDS", 10)
# 外部 HTTP 接口 TLS 校验开关的唯一解析口径（查询执行与元数据链路共用）
HTTP_VERIFY_TLS = read_bool_env("CHAT_DATA_HTTP_VERIFY_TLS", False)


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


def build_http_auth_headers(auth_token: str) -> Dict[str, str]:
    """构造外部 HTTP 接口统一请求头。

    token 由调用方传入（调用方模块级解析、可测试 patch），本函数只负责组装。
    """
    headers = {"Content-Type": "application/json"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    return headers


def add_http_user_params(
    payload: Dict[str, Any],
    user_id: str | None = None,
    username: str | None = None,
) -> Dict[str, Any]:
    """补充外部 HTTP 接口统一用户参数（仅边界层使用对方要求的驼峰命名）。"""
    normalized_user_id = str(user_id or "").strip()
    normalized_username = str(username or "").strip()
    if normalized_user_id:
        payload["userId"] = normalized_user_id
    if normalized_username:
        payload["username"] = normalized_username
    return payload


@asynccontextmanager
async def open_http_client(*, timeout_seconds: float, headers: Optional[Dict[str, str]] = None):
    """统一构造外部 HTTP AsyncClient，TLS 校验口径单点。

    headers 由调用方传入（各调用方模块的 token 解析保留模块级测试 patch seam）；
    httpx 在函数内导入：executor 测试通过 sys.modules 注入伪实现，模块属性
    级导入会使该 patch 方式失效，与 executor 既有模式保持一致。
    """
    import httpx

    async with httpx.AsyncClient(
        verify=HTTP_VERIFY_TLS,
        timeout=timeout_seconds,
        headers=headers,
    ) as client:
        yield client


def _apply_timeout_to_node(current: Any, timeout_ms: int, timeout_seconds: int, visited: set[int], stack: list) -> None:
    """对单个 transport 节点设置 Thrift 超时与 socket 超时，并把子节点压入栈。"""
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
        _apply_timeout_to_node(current, timeout_ms, timeout_seconds, visited, stack)


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
        _apply_http_source_identity(resolved, org_code=org_code, org_name=org_name)
        return resolved
    _apply_direct_source_credentials(normalized_source_type, resolved)
    return resolved


def _apply_http_source_identity(resolved: Dict[str, Any], *, org_code: str, org_name: str) -> None:
    """HTTP 执行：身份参数优先于配置内取值，机构编码必填。"""
    resolved_org_code = str(org_code or resolved.get("org_code") or "").strip()
    if not resolved_org_code:
        raise ValueError("HTTP 执行对象缺少机构编码")
    resolved["org_code"] = resolved_org_code
    resolved["org_name"] = str(org_name or resolved.get("org_name") or "").strip()


def _apply_direct_source_credentials(normalized_source_type: str, resolved: Dict[str, Any]) -> None:
    """直连执行：凭据不完整时从服务端凭据通道补齐。"""
    if normalized_source_type == "mysql":
        if not resolved.get("user") or "password" not in resolved:
            resolved.update(resolve_mysql_credentials(resolved))
        return
    if not resolved.get("username") or "password" not in resolved:
        resolved.update(resolve_hive_credentials(resolved))


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
