import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))

from apps.chat_data_app.services.sql_run import concurrency as limiter


async def _run_limited_pair(user_id: str, first_target: dict, second_target: dict) -> tuple[float, list[str]]:
    events: list[str] = []

    async def operation(name: str) -> str:
        events.append(f"start:{name}")
        await asyncio.sleep(0.15)
        events.append(f"end:{name}")
        return name

    start = time.perf_counter()
    await asyncio.gather(
        limiter.run_with_sql_user_datasource_limit(user_id, first_target, lambda: operation("first")),
        limiter.run_with_sql_user_datasource_limit(user_id, second_target, lambda: operation("second")),
    )
    return time.perf_counter() - start, events


def test_sql_user_datasource_limiter_serializes_same_user_same_datasource(monkeypatch):
    monkeypatch.setenv(limiter.SQL_USER_DATASOURCE_MAX_CONCURRENCY_ENV, "1")

    elapsed, events = asyncio.run(_run_limited_pair(
        "user_a",
        {"member_datasource_id": "member_a"},
        {"member_datasource_id": "member_a"},
    ))

    assert elapsed >= 0.28
    assert events == ["start:first", "end:first", "start:second", "end:second"]


def test_sql_user_datasource_limiter_allows_different_datasources(monkeypatch):
    monkeypatch.setenv(limiter.SQL_USER_DATASOURCE_MAX_CONCURRENCY_ENV, "1")

    elapsed, events = asyncio.run(_run_limited_pair(
        "user_a",
        {"member_datasource_id": "member_a"},
        {"member_datasource_id": "member_b"},
    ))

    assert elapsed < 0.25
    assert events[0:2] == ["start:first", "start:second"]


def test_sql_user_datasource_limiter_allows_different_users(monkeypatch):
    monkeypatch.setenv(limiter.SQL_USER_DATASOURCE_MAX_CONCURRENCY_ENV, "1")

    async def run_pair() -> float:
        async def operation() -> None:
            await asyncio.sleep(0.15)

        start = time.perf_counter()
        await asyncio.gather(
            limiter.run_with_sql_user_datasource_limit("user_a", {"member_datasource_id": "member_a"}, operation),
            limiter.run_with_sql_user_datasource_limit("user_b", {"member_datasource_id": "member_a"}, operation),
        )
        return time.perf_counter() - start

    elapsed = asyncio.run(run_pair())

    assert elapsed < 0.25


def test_sql_user_datasource_limiter_env_and_key_helpers(monkeypatch):
    async def _get_max_concurrency():
        return await limiter.get_sql_user_datasource_max_concurrency()

    monkeypatch.delenv(limiter.SQL_USER_DATASOURCE_MAX_CONCURRENCY_ENV, raising=False)
    assert asyncio.run(_get_max_concurrency()) == limiter.DEFAULT_SQL_USER_DATASOURCE_MAX_CONCURRENCY

    # 非法/越界 env 值经配置服务解析失败后回退注册表默认（2）
    monkeypatch.setenv(limiter.SQL_USER_DATASOURCE_MAX_CONCURRENCY_ENV, "bad")
    assert asyncio.run(_get_max_concurrency()) == limiter.DEFAULT_SQL_USER_DATASOURCE_MAX_CONCURRENCY

    monkeypatch.setenv(limiter.SQL_USER_DATASOURCE_MAX_CONCURRENCY_ENV, "0")
    assert asyncio.run(_get_max_concurrency()) == limiter.DEFAULT_SQL_USER_DATASOURCE_MAX_CONCURRENCY

    monkeypatch.setenv(limiter.SQL_USER_DATASOURCE_MAX_CONCURRENCY_ENV, "3")
    assert asyncio.run(_get_max_concurrency()) == 3

    assert limiter.build_sql_concurrency_key(None, None) == "anonymous:unknown"
    assert limiter.build_sql_concurrency_key("  ", {}) == "anonymous:unknown"
    assert limiter.build_sql_concurrency_key("user_a", {"datasource_id": "ds_a"}) == "user_a:ds_a"
    assert limiter.build_sql_concurrency_key("user_a", {"id": "raw_id"}) == "user_a:raw_id"
    assert limiter.build_sql_concurrency_key("user_a", {"ds_id": "legacy_id"}) == "user_a:legacy_id"


# ---------------------------------------------------------------------------
# 动态上限：收紧/放开对新准入立即生效（旧实现按 (loop, key, limit) 缓存信号量，
# 收紧期间在途查询走旧信号量、新查询走新信号量，总并发短暂超额）
# ---------------------------------------------------------------------------


def test_limiter_tightening_blocks_new_admission_until_slot_frees(monkeypatch):
    limiter._limiters.clear()

    async def scenario() -> tuple[int, list[str]]:
        monkeypatch.setenv(limiter.SQL_USER_DATASOURCE_MAX_CONCURRENCY_ENV, "2")
        active = 0
        peak = 0
        events: list[str] = []

        async def operation(name: str) -> None:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            events.append(f"start:{name}")
            await asyncio.sleep(0.1)
            events.append(f"end:{name}")
            active -= 1

        first = asyncio.create_task(
            limiter.run_with_sql_user_datasource_limit("u", {"ds_id": "d"}, lambda: operation("a"))
        )
        await asyncio.sleep(0.02)  # a 已在 limit=2 下准入
        second = asyncio.create_task(
            limiter.run_with_sql_user_datasource_limit("u", {"ds_id": "d"}, lambda: operation("b"))
        )
        await asyncio.sleep(0.02)  # b 已准入（active=2）
        monkeypatch.setenv(limiter.SQL_USER_DATASOURCE_MAX_CONCURRENCY_ENV, "1")  # 收紧
        third = asyncio.create_task(
            limiter.run_with_sql_user_datasource_limit("u", {"ds_id": "d"}, lambda: operation("c"))
        )
        await asyncio.gather(first, second, third)
        return peak, events

    peak, events = asyncio.run(scenario())

    # c 在 a、b 都结束（active 归零）后才准入：新上限 1 对新准入立即生效，
    # 不存在 c 与在途查询叠加导致超额
    assert events.index("start:c") > events.index("end:a")
    assert events.index("start:c") > events.index("end:b")
    # 峰值 2 = 收紧前合法准入的两条；旧实现下 c 会立即并发，峰值为 3
    assert peak == 2


def test_limiter_loosening_wakes_queued_waiter(monkeypatch):
    limiter._limiters.clear()

    async def scenario() -> tuple[int, list[str]]:
        monkeypatch.setenv(limiter.SQL_USER_DATASOURCE_MAX_CONCURRENCY_ENV, "1")
        active = 0
        peak = 0
        events: list[str] = []

        async def operation(name: str) -> None:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            events.append(f"start:{name}")
            await asyncio.sleep(0.1)
            events.append(f"end:{name}")
            active -= 1

        first = asyncio.create_task(
            limiter.run_with_sql_user_datasource_limit("u", {"ds_id": "d"}, lambda: operation("a"))
        )
        await asyncio.sleep(0.02)
        second = asyncio.create_task(
            limiter.run_with_sql_user_datasource_limit("u", {"ds_id": "d"}, lambda: operation("b"))
        )
        await asyncio.sleep(0.02)  # b 在 limit=1 下排队等待
        monkeypatch.setenv(limiter.SQL_USER_DATASOURCE_MAX_CONCURRENCY_ENV, "2")  # 放开
        # 放开的唤醒随下一笔查询到达传播（run() 刷新上限时 notify_all），c 即触发者
        third = asyncio.create_task(
            limiter.run_with_sql_user_datasource_limit("u", {"ds_id": "d"}, lambda: operation("c"))
        )
        await asyncio.gather(first, second, third)
        return peak, events

    peak, events = asyncio.run(scenario())

    # 放开立即对新准入生效：c 与 a 并发（旧 limit=1 下不可能），峰值恰为新上限 2
    assert events.index("start:c") < events.index("end:a")
    assert peak == 2
    # 队列不楔死：排队中的 b 最终也得到执行
    assert "start:b" in events and "end:b" in events


def test_limiter_cache_stays_single_entry_across_limit_changes(monkeypatch):
    limiter._limiters.clear()

    async def scenario() -> None:
        async def operation() -> None:
            return None

        for value in ("2", "1", "3", "2"):
            monkeypatch.setenv(limiter.SQL_USER_DATASOURCE_MAX_CONCURRENCY_ENV, value)
            await limiter.run_with_sql_user_datasource_limit("u", {"ds_id": "d"}, operation)

    asyncio.run(scenario())

    # 同一 (用户, 数据源) 只有一个闸条目：改 limit 不再新增永久驻留对象
    assert list(limiter._limiters.keys()) == ["u:d"]
