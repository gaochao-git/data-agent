"""Shared helpers for ChatData routers."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import HTTPException, Request

from auth import AuthenticationError, authenticate_request
from ..services.semantic_common import (
    AGENT_ID,
    DATA_ANALYZE_AGENT_ID,
    SQL_ASSIST_AGENT_ID,
    _build_table_columns_map,
    _semantic_config_prompt_payload,
)

def _get_current_user(request: Request):
    """解析当前登录用户。"""
    try:
        user = authenticate_request(request)
    except AuthenticationError as err:
        raise HTTPException(status_code=err.status_code, detail=err.message) from err
    user_id = str(user.user_id or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Missing user_id in token")
    return user


def _optional_current_user(request: Request):
    """尽量解析当前用户，兼容未登录测试场景。"""
    try:
        return _get_current_user(request)
    except Exception:
        return None


def _current_user_names(user: Any) -> Dict[str, str]:
    """构造当前用户 ID 到用户名的映射。"""
    user_id = str(getattr(user, "user_id", "") or "").strip()
    username = str(getattr(user, "username", "") or "").strip()
    return {user_id: username} if user_id and username else {}


def _semantic_config_scope_type(config: Dict[str, Any]) -> str:
    """读取指标集范围，缺省按公共指标集处理。"""
    return str((config or {}).get("scope_type") or "public").strip().lower()


def _is_public_semantic_config(config: Dict[str, Any]) -> bool:
    """判断是否为系统公共指标集。"""
    return bool(config and _semantic_config_scope_type(config) == "public")


def _is_private_semantic_config_owner(config: Dict[str, Any], user_id: str) -> bool:
    """判断当前用户是否拥有该个人指标集。"""
    return bool(
        config
        and _semantic_config_scope_type(config) == "private"
        and str((config or {}).get("create_by") or "").strip() == str(user_id or "").strip()
    )


def _can_mutate_semantic_config(config: Dict[str, Any], user_id: str, admin_manage: bool = False) -> bool:
    """判断当前入口是否允许修改指标集。"""
    return _is_public_semantic_config(config) if admin_manage else _is_private_semantic_config_owner(config, user_id)
