"""Shared async ORM session scope for ChatData DAOs."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from utils.database import (  # noqa: F401  _ACTIVE_SESSION 为历史打桩入口重导出
    _ACTIVE_SESSION,
    get_active_session,
    get_session,
)

# 兼容旧打桩入口：历史测试与 datasource_dao 重导出按 _ORM_SESSION 引用
_ORM_SESSION = _ACTIVE_SESSION


@asynccontextmanager
async def session_scope() -> AsyncIterator[Any]:
    """DAOS 会话入口：事务内复用当前事务会话，事务外新开并按请求提交。"""
    async with get_session() as session:
        yield session


@asynccontextmanager
async def transaction_scope() -> AsyncIterator[Any]:
    """显式事务边界：get_session 已是工作单元，嵌套时自然复用同一事务。"""
    async with get_session() as session:
        yield session
