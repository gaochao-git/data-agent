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


class _UserDatasourceLimiter:
    """同一 (用户, 数据源) 维度的动态并发闸。

    与固定信号量的差别：上限在每次准入时刷新为当前配置值，收紧/放开
    立即对新准入生效（active >= 新上限即排队等待），不存在新旧两个
    信号量并存导致的「收紧后总并发短暂超额」。已在途的查询不受影响
    （准入早已发生），这是不取消在途任务前提下的最强约束。
    放开上限的唤醒随下一笔查询到达（run 刷新时 notify_all）或任一
    在途结束传播，无后台监听。唤醒的等待者与同刻新到达者按锁竞争
    准入（非严格 FIFO），极端下后来者可插队；同维度查询均为短任务，
    不做公平性保证。
    """

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._active = 0
        self._cond = asyncio.Condition()

    async def run(self, operation: Callable[[], Awaitable[T]], limit: int) -> T:
        """刷新上限并准入执行 operation；返回其结果。

        刷新与准入在同一临界区内完成：放开上限时唤醒既有等待者按新
        上限重新竞争，收紧上限时后续准入直接按新值排队。
        """
        async with self._cond:
            if limit != self._limit:
                self._limit = limit
                self._cond.notify_all()
            while self._active >= self._limit:
                await self._cond.wait()
            self._active += 1
        try:
            return await operation()
        finally:
            async with self._cond:
                self._active -= 1
                self._cond.notify()


_limiters: dict[str, _UserDatasourceLimiter] = {}
# 条目按 (用户, 数据源) 维度常驻是刻意取舍：条目数上界 = 用户数 × 数据源数，
# 单条仅持一个计数器与条件变量，改 limit 不产生新条目。旧实现按
# (loop, key, limit) 缓存信号量且无淘汰——改一次 limit 多一批永久驻留对象，
# 且收紧期间同一维度的在途/新查询被摊到两个信号量上，总并发短暂超额。
_limiters_lock = asyncio.Lock()


async def get_sql_user_datasource_max_concurrency() -> int:
    """读取同一用户同一数据源 SQL 最大并发数（运行期经后台配置变量表）。

    上限变化即时生效：并发闸按 (用户, 数据源) 维度常驻并在每次准入时
    刷新上限，不再按 limit 缓存多个信号量。
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
    async with _limiters_lock:
        limiter = _limiters.get(limiter_key)
        if limiter is None:
            limiter = _UserDatasourceLimiter(max_concurrency)
            _limiters[limiter_key] = limiter
    return await limiter.run(operation, max_concurrency)
