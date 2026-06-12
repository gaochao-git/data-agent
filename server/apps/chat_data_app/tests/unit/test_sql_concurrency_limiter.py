import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))

from apps.chat_data_app.services import sql_concurrency_limiter as limiter


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
    monkeypatch.delenv(limiter.SQL_USER_DATASOURCE_MAX_CONCURRENCY_ENV, raising=False)
    assert limiter.get_sql_user_datasource_max_concurrency() == limiter.DEFAULT_SQL_USER_DATASOURCE_MAX_CONCURRENCY

    monkeypatch.setenv(limiter.SQL_USER_DATASOURCE_MAX_CONCURRENCY_ENV, "bad")
    assert limiter.get_sql_user_datasource_max_concurrency() == limiter.DEFAULT_SQL_USER_DATASOURCE_MAX_CONCURRENCY

    monkeypatch.setenv(limiter.SQL_USER_DATASOURCE_MAX_CONCURRENCY_ENV, "0")
    assert limiter.get_sql_user_datasource_max_concurrency() == limiter.DEFAULT_SQL_USER_DATASOURCE_MAX_CONCURRENCY

    monkeypatch.setenv(limiter.SQL_USER_DATASOURCE_MAX_CONCURRENCY_ENV, "3")
    assert limiter.get_sql_user_datasource_max_concurrency() == 3

    assert limiter.build_sql_concurrency_key(None, None) == "anonymous:unknown"
    assert limiter.build_sql_concurrency_key("  ", {}) == "anonymous:unknown"
    assert limiter.build_sql_concurrency_key("user_a", {"datasource_id": "ds_a"}) == "user_a:ds_a"
    assert limiter.build_sql_concurrency_key("user_a", {"id": "raw_id"}) == "user_a:raw_id"
    assert limiter.build_sql_concurrency_key("user_a", {"ds_id": "legacy_id"}) == "user_a:legacy_id"
