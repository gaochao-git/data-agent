"""ChatData router dependencies."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import HTTPException, Request

from auth import AuthenticationError, authenticate_request


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
