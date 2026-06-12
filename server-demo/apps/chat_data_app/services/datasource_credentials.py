"""ChatData 数据源统一连接凭证。"""

from __future__ import annotations

import os
from typing import Any, Dict

from utils.crypto import decrypt_password


def _get_env_value(name: str) -> str | None:
    value = str(os.getenv(name) or "").strip()
    return value or None


def _get_env_password(name: str) -> str | None:
    value = _get_env_value(name)
    if not value:
        return None
    return decrypt_password(value)


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
    """获取 Hive 统一连接凭证。"""
    username = _get_env_value("CHAT_DATA_HIVE_USER")
    password = _get_env_password("CHAT_DATA_HIVE_PASSWORD")
    if password is None:
        password = ""
    if not username:
        raise ValueError("缺少 Hive 统一连接账号配置: CHAT_DATA_HIVE_USER")
    return {"username": username, "password": password}
