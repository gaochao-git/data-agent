"""Shared async ORM session scope for ChatData DAOs."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any

from utils.database import get_session

_ORM_SESSION: ContextVar[Any] = ContextVar("chat_data_orm_session", default=None)


@asynccontextmanager
async def session_scope() -> AsyncIterator[Any]:
    active_session = _ORM_SESSION.get()
    if active_session is not None:
        yield active_session
        return
    async with get_session() as session:
        yield session


@asynccontextmanager
async def transaction_scope() -> AsyncIterator[Any]:
    active_session = _ORM_SESSION.get()
    if active_session is not None:
        yield active_session
        return

    async with get_session() as session:
        token = _ORM_SESSION.set(session)
        try:
            yield session
        finally:
            _ORM_SESSION.reset(token)
