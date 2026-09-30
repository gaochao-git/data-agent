"""ChatData router dependencies."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request, status

from auth import AuthenticatedUser, AuthenticationError, get_forwarded_user


def _get_current_user(request: Request) -> AuthenticatedUser:
    """解析可信代理转发的当前用户，不负责 service token 校验。"""
    try:
        user = get_forwarded_user(request)
    except AuthenticationError as err:
        raise HTTPException(status_code=err.status_code, detail=err.message) from err
    user_id = str(user.user_id or "").strip()
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing user_id in token")
    return user


def _current_user_names(user: Any) -> dict[str, str]:
    """构造当前用户 ID 到用户名的映射。"""
    user_id = str(getattr(user, "user_id", "") or "").strip()
    username = str(getattr(user, "username", "") or "").strip()
    return {user_id: username} if user_id and username else {}
