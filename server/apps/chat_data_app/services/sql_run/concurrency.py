"""SQL 查询并发限制。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from utils.logger import get_logger

from ..setting_service import get_runtime_setting

logger = get_logger(__name__)

DEFAULT_SQL_USER_DATASOURCE_MAX_CONCURRENCY = 2
SQL_USER_DATASOURCE_MAX_CONCURRENCY_ENV = "CHAT_DATA_SQL_USER_DATASOURCE_MAX_CONCURRENCY"

T = TypeVar("T")

_semaphores: dict[tuple[int, str, int], asyncio.Semaphore] = {}
_semaphores_lock = asyncio.Lock()


async def get_sql_user_datasource_max_concurrency() -> int:
    """读取同一用户同一数据源 SQL 最大并发数（运行期经后台配置变量表）。

    信号量按 (loop, key, limit) 维度缓存：改值后新 limit 生成新信号量，
    旧信号量随旧查询释放自然淘汰。
    """
    return await get_runtime_setting(SQL_USER_DATASOURCE_MAX_CONCURRENCY_ENV)


def build_sql_concurrency_key(user_id: str | None, execution_target: dict[str, Any] | None) -> str:
    """生成用户和执行数据源维度的并发限制键。"""
    target = execution_target or {}
    normalized_user_id = str(user_id or "anonymous").strip() or "anonymous"
    datasource_key = (
        str(target.get("member_datasource_id") or "").strip()
        or str(target.get("datasource_id") or "").strip()
        or str(target.get("id") or "").strip()
        or str(target.get("ds_id") or "").strip()
        or "unknown"
    )
    return f"{normalized_user_id}:{datasource_key}"


async def run_with_sql_user_datasource_limit(
    user_id: str | None,
    execution_target: dict[str, Any] | None,
    operation: Callable[[], Awaitable[T]],
) -> T:
    """按用户和数据源维度限制 SQL 查询并发。"""
    max_concurrency = await get_sql_user_datasource_max_concurrency()
    limiter_key = build_sql_concurrency_key(user_id, execution_target)
    semaphore_key = (id(asyncio.get_running_loop()), limiter_key, max_concurrency)
    async with _semaphores_lock:
        semaphore = _semaphores.get(semaphore_key)
        if semaphore is None:
            semaphore = asyncio.Semaphore(max_concurrency)
            _semaphores[semaphore_key] = semaphore
    async with semaphore:
        return await operation()
