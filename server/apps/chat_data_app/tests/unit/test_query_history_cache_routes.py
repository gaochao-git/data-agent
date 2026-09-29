import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")

from utils.error_codes import ErrorCode
from apps.chat_data_app.routers import query_favorites as query_favorites_routes
from apps.chat_data_app.routers import query_execution as query_execution_routes
from apps.chat_data_app.routers import query_history as query_history_routes
from apps.chat_data_app.schema import ExecuteSqlRequest, QueryFavoriteCreateRequest, QueryFavoriteUpdateRequest, QueryTaskCreateRequest
from apps.chat_data_app.services.dashboard import runtime as dashboard_runtime
from apps.chat_data_app.services.sql_run import cache as query_cache, providers as query_providers, runtime as query_runtime
from apps.chat_data_app.services.sql_run.history_service import QueryHistoryService
from utils.models import Base, ChatDataQueryExecution, ChatDataQueryTask, ChatDataSqlResultCache, SqlQueryFavorite


@pytest.fixture()
def route_db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    async def create_tables():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(create_tables())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def fake_get_session():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr(query_providers, "get_session", fake_get_session)
    monkeypatch.setattr(query_runtime, "get_session", fake_get_session)
    monkeypatch.setattr(query_cache, "get_session", fake_get_session)
    monkeypatch.setattr(query_history_routes, "_get_current_user", lambda _request: SimpleNamespace(user_id="u1", username="测试用户"))
    monkeypatch.setattr(query_execution_routes, "_get_current_user", lambda _request: SimpleNamespace(user_id="u1", username="测试用户"))
    monkeypatch.setattr(query_favorites_routes, "_get_current_user", lambda _request: SimpleNamespace(user_id="u1", username="测试用户"))
    yield session_factory
    asyncio.run(engine.dispose())


def _make_request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})


def _payload(response):
    return json.loads(response.body.decode("utf-8"))


async def _seed_query_history(session_factory):
    now = datetime(2026, 6, 2, 10, 0, 0)
    async with session_factory() as session:
        session.add_all([
            ChatDataQueryTask(
                task_id="task_builder",
                task_type="sql",
                datasource_id="ds1",
                query_type="builder",
                task_status="succeeded",
                request_payload=json.dumps({"operator_name": "可视化用户"}),
                create_time=now,
                update_time=now,
                create_by_name="可视化用户",
            ),
            ChatDataQueryTask(
                task_id="task_agent",
                task_type="sql",
                datasource_id="ds1",
                query_type="agent",
                task_status="cancelled",
                request_payload=json.dumps({"operator_name": "智能用户"}),
                create_time=now - timedelta(minutes=1),
                update_time=now - timedelta(minutes=1),
            ),
            ChatDataQueryTask(
                task_id="task_dashboard",
                task_type="dashboard_batch",
                datasource_id="ds1",
                query_type="dashboard",
                task_status="succeeded",
                request_payload=json.dumps({
                    "operator_name": "看板用户",
                    "panel_execution_plan": {
                        "panel_1": {
                            "mode": "multi",
                            "member_datasource_ids": ["m1", "m2"],
                            "primary_member_datasource_id": "m1",
                        }
                    },
                }, ensure_ascii=False),
                create_time=now - timedelta(minutes=3),
                update_time=now - timedelta(minutes=3),
            ),
            ChatDataQueryTask(
                task_id="task_sql_batch_cancelled",
                task_type="sql_batch",
                datasource_id="ds1",
                query_type="sql",
                task_status="cancelled",
                request_payload=json.dumps({"operator_name": "SQL用户", "sql": ["select 1", "select 2"]}),
                create_time=now - timedelta(minutes=4),
                update_time=now - timedelta(minutes=4),
            ),
            ChatDataQueryExecution(
                execution_id="ex_sql",
                task_id="",
                user_id="u1",
                datasource_id="ds1",
                member_datasource_id="",
                member_name="默认库",
                executor_type="direct",
                sql_text="select 1",
                sql_status="succeeded",
                execution_time_ms=5,
                result_row_count=1,
                result_payload='{"columns":["one"],"data":[{"one":1}]}',
                started_time=now - timedelta(minutes=2),
                finished_time=now - timedelta(minutes=2),
                create_time=now - timedelta(minutes=2),
                update_time=now - timedelta(minutes=2),
                create_by="u1",
            ),
            ChatDataQueryExecution(
                execution_id="ex_builder",
                task_id="task_builder",
                user_id="u1",
                datasource_id="ds1",
                member_datasource_id="m1",
                member_name="成员库",
                executor_type="direct",
                sql_text="select city, sum(amount) from orders group by city",
                sql_status="succeeded",
                execution_time_ms=8,
                result_row_count=2,
                result_payload='{"columns":["city"],"data":[{"city":"杭州"}]}',
                started_time=now,
                finished_time=now,
                create_time=now,
                update_time=now,
                create_by="u1",
            ),
            ChatDataQueryExecution(
                execution_id="ex_agent_cancelled",
                task_id="task_agent",
                user_id="u1",
                datasource_id="ds1",
                member_datasource_id="m1",
                member_name="成员库",
                executor_type="direct",
                sql_text="select * from orders",
                sql_status="cancelled",
                execution_time_ms=None,
                result_row_count=None,
                result_payload=None,
                started_time=now - timedelta(minutes=1),
                finished_time=None,
                create_time=now - timedelta(minutes=1),
                update_time=now - timedelta(minutes=1),
                create_by="u1",
            ),
            ChatDataQueryExecution(
                execution_id="ex_dashboard",
                task_id="task_dashboard",
                user_id="u1",
                datasource_id="ds1",
                member_datasource_id="m1",
                member_name="成员库1",
                executor_type="direct",
                sql_text="select amount from dashboard_result",
                sql_status="succeeded",
                execution_time_ms=12,
                result_row_count=2,
                result_payload='{"columns":["amount"],"data":[{"amount":1}]}',
                started_time=now - timedelta(minutes=3),
                finished_time=now - timedelta(minutes=3),
                create_time=now - timedelta(minutes=3),
                update_time=now - timedelta(minutes=3),
                create_by="u1",
            ),
            ChatDataQueryExecution(
                execution_id="ex_sql_batch_done",
                task_id="task_sql_batch_cancelled",
                user_id="u1",
                datasource_id="ds1",
                member_datasource_id="m1",
                member_name="成员库",
                executor_type="direct",
                sql_text="select 1",
                sql_status="succeeded",
                execution_time_ms=6,
                result_row_count=1,
                result_payload='{"status":"succeeded","columns":["one"],"data":[{"one":1}]}',
                started_time=now - timedelta(minutes=4),
                finished_time=now - timedelta(minutes=4),
                create_time=now - timedelta(minutes=4),
                update_time=now - timedelta(minutes=4),
                create_by="u1",
            ),
            ChatDataQueryExecution(
                execution_id="ex_sql_batch_pending",
                task_id="task_sql_batch_cancelled",
                user_id="u1",
                datasource_id="ds1",
                member_datasource_id="m1",
                member_name="成员库",
                executor_type="direct",
                sql_text="select 2",
                sql_status="pending",
                execution_time_ms=None,
                result_row_count=None,
                result_payload=None,
                started_time=None,
                finished_time=None,
                create_time=now - timedelta(minutes=4),
                update_time=now - timedelta(minutes=4),
                create_by="u1",
            ),
            ChatDataQueryExecution(
                execution_id="ex_other_user",
                task_id="",
                user_id="u2",
                datasource_id="ds1",
                sql_text="select other",
                sql_status="succeeded",
                create_time=now,
                update_time=now,
                create_by="u2",
            ),
        ])
        await session.commit()


async def _seed_http_query_history(session_factory):
    now = datetime(2026, 6, 2, 10, 0, 0)
    async with session_factory() as session:
        session.add_all([
            ChatDataQueryTask(
                task_id="task_http",
                task_type="sql",
                datasource_id="ds1",
                query_type="sql",
                task_status="succeeded",
                request_payload=json.dumps({"operator_name": "HTTP用户"}),
                remote_task_payload=json.dumps([{
                    "member_datasource_id": "m_http",
                    "member_name": "HTTP成员",
                    "remote_task_id": "remote_http_1",
                    "source_type": "mysql",
                }]),
                create_time=now,
                update_time=now,
            ),
            ChatDataQueryExecution(
                execution_id="ex_http",
                task_id="task_http",
                user_id="u1",
                datasource_id="ds1",
                member_datasource_id="m_http",
                member_name="HTTP成员",
                executor_type="http",
                sql_text="select * from remote_orders",
                sql_status="succeeded",
                remote_task_id="remote_http_1",
                execution_time_ms=10,
                result_row_count=1,
                result_payload=None,
                started_time=now,
                finished_time=now,
                create_time=now,
                update_time=now,
                create_by="u1",
                create_by_name="HTTP用户",
            ),
        ])
        await session.commit()


def test_get_query_history_filters_builder_and_normalizes_page(route_db):
    asyncio.run(_seed_query_history(route_db))

    response = asyncio.run(query_history_routes.get_query_history(
        _make_request(),
        datasource_id="ds1",
        page=-2,
        page_size=200,
        source="builder",
        status="succeeded",
    ))

    body = _payload(response)
    assert body["status"] == "success"
    assert body["data"]["total"] == 1
    assert body["data"]["page"] == 1


def test_get_query_history_exposes_dashboard_panel_execution_plan(route_db):
    asyncio.run(_seed_query_history(route_db))

    response = asyncio.run(query_history_routes.get_query_history(
        _make_request(),
        datasource_id="ds1",
        source="dashboard",
    ))

    body = _payload(response)
    assert body["status"] == "success"
    assert body["data"]["total"] == 1
    item = body["data"]["queries"][0]
    assert item["source"] == "看板"
    assert item["panel_execution_plan"] == {
        "panel_1": {
            "mode": "multi",
            "member_datasource_ids": ["m1", "m2"],
            "primary_member_datasource_id": "m1",
        }
    }
    assert item["execution_id"] == "ex_dashboard"
    assert item["success_flag"] == 1
    assert item["operator_name"] == "看板用户"
    assert item["has_result"] is True
    assert "result_payload" not in item


def test_get_query_history_supports_sql_null_task_and_cancelled_task(route_db):
    asyncio.run(_seed_query_history(route_db))

    sql_response = asyncio.run(query_history_routes.get_query_history(
        _make_request(),
        datasource_id="ds1",
        source="sql",
        status="succeeded",
    ))
    sql_body = _payload(sql_response)
    assert sql_body["data"]["total"] == 2
    sql_items = {item["execution_id"]: item for item in sql_body["data"]["queries"]}
    assert sql_items["ex_sql"]["source"] == "SQL编辑器"
    assert sql_items["ex_sql_batch_done"]["sql_status"] == "succeeded"
    assert sql_items["ex_sql_batch_done"]["success_flag"] == 1

    sql_cancelled_response = asyncio.run(query_history_routes.get_query_history(
        _make_request(),
        datasource_id="ds1",
        source="sql",
        status="cancelled",
    ))
    sql_cancelled_body = _payload(sql_cancelled_response)
    assert sql_cancelled_body["data"]["total"] == 0

    sql_pending_response = asyncio.run(query_history_routes.get_query_history(
        _make_request(),
        datasource_id="ds1",
        source="sql",
        status="pending",
    ))
    sql_pending_body = _payload(sql_pending_response)
    assert sql_pending_body["data"]["total"] == 1
    assert sql_pending_body["data"]["queries"][0]["execution_id"] == "ex_sql_batch_pending"
    assert sql_pending_body["data"]["queries"][0]["sql_status"] == "pending"

    cancelled_response = asyncio.run(query_history_routes.get_query_history(
        _make_request(),
        datasource_id="ds1",
        source="agent",
        status="cancelled",
    ))
    cancelled_body = _payload(cancelled_response)
    assert cancelled_body["data"]["total"] == 1
    assert cancelled_body["data"]["queries"][0]["execution_id"] == "ex_agent_cancelled"
    assert cancelled_body["data"]["queries"][0]["sql_status"] == "cancelled"
    assert cancelled_body["data"]["queries"][0]["source"] == "智能问数"
    assert cancelled_body["data"]["queries"][0]["success_flag"] == 0

    all_response = asyncio.run(query_history_routes.get_query_history(
        _make_request(),
        datasource_id="ds1",
        source=None,
        status=None,
    ))
    all_body = _payload(all_response)
    assert all_body["data"]["total"] == 6
    all_items = {item["execution_id"]: item for item in all_body["data"]["queries"]}
    assert all_items["ex_sql_batch_pending"]["task_status"] == "cancelled"
    assert all_items["ex_sql_batch_pending"]["sql_status"] == "pending"


def test_get_query_history_filters_by_executed_time_range(route_db):
    asyncio.run(_seed_query_history(route_db))

    response = asyncio.run(query_history_routes.get_query_history(
        _make_request(),
        datasource_id="ds1",
        start_time="2026-06-02 09:59:30",
        end_time="2026-06-02 10:00:30",
    ))

    body = _payload(response)
    assert body["status"] == "success"
    assert body["data"]["total"] == 1
    assert body["data"]["queries"][0]["execution_id"] == "ex_builder"

    invalid_response = asyncio.run(query_history_routes.get_query_history(
        _make_request(),
        datasource_id="ds1",
        start_time="bad-time",
    ))
    invalid_body = _payload(invalid_response)
    assert invalid_body["status"] == "error"
    assert invalid_body["code"] == ErrorCode.CHATDATA_BAD_REQUEST


def test_query_history_accepts_url_encoded_datetime():
    from apps.chat_data_app.services.sql_run.history_service import QueryHistoryService

    assert QueryHistoryService.parse_history_time("2026-07-06%2010%3A37%3A58") == datetime(2026, 7, 6, 10, 37, 58)


def test_get_admin_query_history_filters_globally(route_db):
    asyncio.run(_seed_query_history(route_db))

    response = asyncio.run(query_history_routes.get_admin_query_history(
        _make_request(),
        datasource_id="ds1",
        user_id="u2",
        keyword="other",
        source="sql",
        status="succeeded",
        page=1,
        page_size=20,
    ))

    body = _payload(response)
    assert body["status"] == "success"
    assert body["data"]["total"] == 1
    item = body["data"]["queries"][0]
    assert item["execution_id"] == "ex_other_user"
    assert item["user_id"] == "u2"
    assert item["query"] == "select other"
    assert item["source"] == "SQL编辑器"
    assert "result_payload" not in item

    username_response = asyncio.run(query_history_routes.get_admin_query_history(
        _make_request(),
        datasource_id="ds1",
        username="可视化",
        source="builder",
        status="succeeded",
    ))
    username_body = _payload(username_response)
    assert username_body["data"]["total"] == 1
    assert username_body["data"]["queries"][0]["execution_id"] == "ex_builder"

    time_response = asyncio.run(query_history_routes.get_admin_query_history(
        _make_request(),
        datasource_id="ds1",
        start_time="2026-06-02 09:59:30",
        end_time="2026-06-02 10:00:30",
    ))
    time_body = _payload(time_response)
    assert time_body["data"]["total"] == 2
    assert {item["execution_id"] for item in time_body["data"]["queries"]} == {"ex_builder", "ex_other_user"}


async def _seed_running_query_history(session_factory):
    now = datetime(2026, 6, 2, 11, 0, 0)
    async with session_factory() as session:
        session.add_all([
            ChatDataQueryTask(
                task_id="task_u1_ds1",
                task_type="sql",
                datasource_id="ds1",
                query_type="sql",
                task_status="running",
                request_payload=json.dumps({"operator_name": "用户一", "user_id": "u1"}),
                create_time=now,
                update_time=now,
                create_by="u1",
                create_by_name="用户一",
            ),
            ChatDataQueryTask(
                task_id="task_u2_ds1",
                task_type="sql",
                datasource_id="ds1",
                query_type="sql",
                task_status="running",
                request_payload=json.dumps({"operator_name": "用户二", "user_id": "u2"}),
                create_time=now,
                update_time=now,
                create_by="u2",
                create_by_name="用户二",
            ),
            ChatDataQueryTask(
                task_id="task_u1_ds2",
                task_type="sql",
                datasource_id="ds2",
                query_type="sql",
                task_status="running",
                request_payload=json.dumps({"operator_name": "用户一", "user_id": "u1"}),
                create_time=now,
                update_time=now,
                create_by="u1",
                create_by_name="用户一",
            ),
            ChatDataQueryExecution(
                execution_id="ex_u1_ds1_pending",
                task_id="task_u1_ds1",
                user_id="u1",
                datasource_id="ds1",
                member_datasource_id="m1",
                member_name="成员1",
                executor_type="direct",
                sql_text="select sleep(10)",
                sql_status="pending",
                create_time=now,
                update_time=now,
                create_by="u1",
                create_by_name="用户一",
            ),
            ChatDataQueryExecution(
                execution_id="ex_u1_ds2_pending",
                task_id="task_u1_ds2",
                user_id="u1",
                datasource_id="ds2",
                member_datasource_id="m2",
                member_name="成员2",
                executor_type="direct",
                sql_text="select sleep(20)",
                sql_status="pending",
                create_time=now,
                update_time=now,
                create_by="u1",
                create_by_name="用户一",
            ),
            ChatDataQueryExecution(
                execution_id="ex_u2_ds1_pending",
                task_id="task_u2_ds1",
                user_id="u2",
                datasource_id="ds1",
                member_datasource_id="m1",
                member_name="成员1",
                executor_type="direct",
                sql_text="select sleep(30)",
                sql_status="pending",
                create_time=now,
                update_time=now,
                create_by="u2",
                create_by_name="用户二",
            ),
            ChatDataQueryExecution(
                execution_id="ex_u1_ds1_done",
                task_id="",
                user_id="u1",
                datasource_id="ds1",
                member_datasource_id="m1",
                member_name="成员1",
                executor_type="direct",
                sql_text="select 1",
                sql_status="succeeded",
                create_time=now,
                update_time=now,
                create_by="u1",
                create_by_name="用户一",
            ),
        ])
        await session.commit()


def test_cancel_my_running_query_history_only_current_user(route_db):
    asyncio.run(_seed_running_query_history(route_db))

    response = asyncio.run(query_history_routes.cancel_my_running_query_history(_make_request()))
    body = _payload(response)

    assert body["status"] == "success"
    assert body["data"]["total"] == 2
    assert body["data"]["cancelled"] == 2
    assert body["data"]["failed"] == 0

    async def read_statuses():
        async with route_db() as session:
            execution_result = await session.execute(select(ChatDataQueryExecution.execution_id, ChatDataQueryExecution.sql_status))
            task_result = await session.execute(select(ChatDataQueryTask.task_id, ChatDataQueryTask.task_status))
            return dict(execution_result.all()), dict(task_result.all())

    execution_statuses, task_statuses = asyncio.run(read_statuses())
    assert execution_statuses["ex_u1_ds1_pending"] == "cancelled"
    assert execution_statuses["ex_u1_ds2_pending"] == "cancelled"
    assert execution_statuses["ex_u2_ds1_pending"] == "pending"
    assert execution_statuses["ex_u1_ds1_done"] == "succeeded"
    assert task_statuses["task_u1_ds1"] == "cancelled"
    assert task_statuses["task_u1_ds2"] == "cancelled"
    assert task_statuses["task_u2_ds1"] == "running"


def test_cancel_admin_running_query_history_can_filter_datasource(route_db):
    asyncio.run(_seed_running_query_history(route_db))

    response = asyncio.run(query_history_routes.cancel_admin_running_query_history(_make_request(), datasource_id="ds1"))
    body = _payload(response)

    assert body["status"] == "success"
    assert body["data"]["total"] == 2
    assert {item["execution_id"] for item in body["data"]["executions"]} == {"ex_u1_ds1_pending", "ex_u2_ds1_pending"}

    async def read_statuses():
        async with route_db() as session:
            execution_result = await session.execute(select(ChatDataQueryExecution.execution_id, ChatDataQueryExecution.sql_status))
            task_result = await session.execute(select(ChatDataQueryTask.task_id, ChatDataQueryTask.task_status))
            return dict(execution_result.all()), dict(task_result.all())

    execution_statuses, task_statuses = asyncio.run(read_statuses())
    assert execution_statuses["ex_u1_ds1_pending"] == "cancelled"
    assert execution_statuses["ex_u2_ds1_pending"] == "cancelled"
    assert execution_statuses["ex_u1_ds2_pending"] == "pending"
    assert task_statuses["task_u1_ds1"] == "cancelled"
    assert task_statuses["task_u2_ds1"] == "cancelled"
    assert task_statuses["task_u1_ds2"] == "running"


def test_cancel_query_history_execution_only_cancels_current_execution(route_db):
    asyncio.run(_seed_running_query_history(route_db))

    response = asyncio.run(query_history_routes.cancel_query_history_execution(
        "ex_u1_ds1_pending",
        _make_request(),
    ))
    body = _payload(response)

    assert body["status"] == "success"
    assert body["data"]["execution_id"] == "ex_u1_ds1_pending"
    assert body["data"]["status"] == "cancelled"

    async def read_statuses():
        async with route_db() as session:
            execution_result = await session.execute(select(ChatDataQueryExecution.execution_id, ChatDataQueryExecution.sql_status))
            task_result = await session.execute(select(ChatDataQueryTask.task_id, ChatDataQueryTask.task_status))
            return dict(execution_result.all()), dict(task_result.all())

    execution_statuses, task_statuses = asyncio.run(read_statuses())
    assert execution_statuses["ex_u1_ds1_pending"] == "cancelled"
    assert execution_statuses["ex_u1_ds2_pending"] == "pending"
    assert execution_statuses["ex_u2_ds1_pending"] == "pending"
    assert execution_statuses["ex_u1_ds1_done"] == "succeeded"
    assert task_statuses["task_u1_ds1"] == "running"
    assert task_statuses["task_u1_ds2"] == "running"
    assert task_statuses["task_u2_ds1"] == "running"


def test_cancel_query_history_execution_hides_other_user_execution(route_db):
    asyncio.run(_seed_running_query_history(route_db))

    response = asyncio.run(query_history_routes.cancel_query_history_execution(
        "ex_u2_ds1_pending",
        _make_request(),
    ))
    body = _payload(response)

    assert body["status"] == "error"
    assert body["code"] == ErrorCode.CHATDATA_NOT_FOUND

    async def read_status():
        async with route_db() as session:
            result = await session.execute(
                select(ChatDataQueryExecution.sql_status).filter_by(execution_id="ex_u2_ds1_pending")
            )
            return result.scalar_one()

    assert asyncio.run(read_status()) == "pending"


def test_cancel_running_query_history_keeps_batch_when_one_execution_fails(route_db, monkeypatch):
    asyncio.run(_seed_running_query_history(route_db))
    original_cancel = query_providers.query_task_service.cancel_query_execution

    async def fake_cancel(execution, **kwargs):
        if execution.execution_id == "ex_u1_ds1_pending":
            raise RuntimeError("remote stop failed")
        return await original_cancel(execution, **kwargs)

    monkeypatch.setattr(query_providers.query_task_service, "cancel_query_execution", fake_cancel)

    response = asyncio.run(query_history_routes.cancel_my_running_query_history(_make_request()))
    body = _payload(response)

    assert body["status"] == "success"
    assert body["data"]["total"] == 2
    assert body["data"]["cancelled"] == 1
    assert body["data"]["failed"] == 1
    results = {item["execution_id"]: item for item in body["data"]["executions"]}
    assert results["ex_u1_ds1_pending"]["status"] == "kill_failed"
    assert results["ex_u1_ds2_pending"]["status"] == "cancelled"

    async def read_task_status():
        async with route_db() as session:
            result = await session.execute(select(ChatDataQueryTask.task_status).filter_by(task_id="task_u1_ds1"))
            return result.scalar_one()

    assert asyncio.run(read_task_status()) == "failed"


def test_cancel_running_query_history_continues_when_failure_status_update_fails(route_db, monkeypatch):
    asyncio.run(_seed_running_query_history(route_db))
    cancelled_ids = []

    async def fake_cancel(execution, **_kwargs):
        if execution.execution_id == "ex_u1_ds1_pending":
            raise RuntimeError("stop failed")
        cancelled_ids.append(execution.execution_id)
        return {"execution_id": execution.execution_id, "status": "cancelled"}

    async def failing_update(execution_id, **_kwargs):
        if execution_id == "ex_u1_ds1_pending":
            raise RuntimeError("status update failed")

    monkeypatch.setattr(query_providers.query_task_service, "cancel_query_execution", fake_cancel)
    monkeypatch.setattr(query_providers.query_task_service, "update_query_execution", failing_update)

    response = asyncio.run(query_history_routes.cancel_my_running_query_history(_make_request()))
    body = _payload(response)

    assert body["status"] == "success"
    assert body["data"]["cancelled"] == 1
    assert body["data"]["failed"] == 1
    assert cancelled_ids == ["ex_u1_ds2_pending"]


def test_get_query_history_result_success_and_not_found(route_db):
    asyncio.run(_seed_query_history(route_db))

    response = asyncio.run(query_history_routes.get_query_history_result("ex_builder", _make_request()))
    body = _payload(response)
    assert body["status"] == "success"
    assert body["data"]["execution_id"] == "ex_builder"
    assert body["data"]["query"].startswith("select city")
    assert body["data"]["result_payload"]["status"] == "succeeded"
    assert body["data"]["result_payload"]["columns"] == ["city"]
    assert "success" not in body["data"]["result_payload"]

    missing_response = asyncio.run(query_history_routes.get_query_history_result("missing", _make_request()))
    missing_body = _payload(missing_response)
    assert missing_body["status"] == "error"
    assert missing_body["code"] == ErrorCode.CHATDATA_NOT_FOUND


def test_get_query_history_result_fetches_remote_http_result(route_db, monkeypatch):
    asyncio.run(_seed_http_query_history(route_db))
    captured = {}

    async def fake_fetch(remote_task_id, source_type="hive", user_id=None, username=None):
        captured.update({
            "remote_task_id": remote_task_id,
            "source_type": source_type,
            "user_id": user_id,
            "username": username,
        })
        return {
            "status": "succeeded",
            "success": True,
            "columns": ["id"],
            "data": [{"id": 1}],
            "total_count": 1,
            "execution_time_ms": 15,
        }

    response = asyncio.run(query_history_routes.get_query_history_result(
        "ex_http",
        _make_request(),
        remote_result_fetcher=fake_fetch,
    ))
    body = _payload(response)
    assert body["status"] == "success"
    assert body["data"]["result_payload"]["columns"] == ["id"]
    assert body["data"]["result_payload"]["data"] == [{"id": 1}]
    assert captured == {
        "remote_task_id": "remote_http_1",
        "source_type": "mysql",
        "user_id": "u1",
        "username": "HTTP用户",
    }


def test_get_query_history_result_remote_http_unavailable(route_db, monkeypatch):
    asyncio.run(_seed_http_query_history(route_db))

    async def fake_fetch(*_args, **_kwargs):
        return {"status": "failed", "error": "结果文件已清理"}

    response = asyncio.run(query_history_routes.get_query_history_result(
        "ex_http",
        _make_request(),
        remote_result_fetcher=fake_fetch,
    ))
    body = _payload(response)
    assert body["status"] == "success"
    assert body["data"]["result_payload"]["status"] == "failed"
    assert body["data"]["result_payload"]["error"] == "结果文件已清理"


def test_query_history_routes_return_error_when_session_fails(monkeypatch):
    @asynccontextmanager
    async def broken_session():
        raise RuntimeError("db down")
        yield

    monkeypatch.setattr(query_providers, "get_session", broken_session)
    monkeypatch.setattr(query_history_routes, "_get_current_user", lambda _request: SimpleNamespace(user_id="u1", username="测试用户"))

    history = asyncio.run(query_history_routes.get_query_history(_make_request(), datasource_id="ds1"))
    detail = asyncio.run(query_history_routes.get_query_history_result("ex1", _make_request()))

    assert _payload(history)["code"] == ErrorCode.CHATDATA_QUERY_HISTORY_FAILED
    assert _payload(detail)["code"] == ErrorCode.CHATDATA_QUERY_HISTORY_FAILED


def test_query_favorite_builder_validation_and_delete(route_db):
    missing_builder = QueryFavoriteCreateRequest(
        datasource_id="ds1",
        query_type="builder",
        favorite_title="可视化收藏",
        query_text="select 1",
    )
    missing_response = asyncio.run(query_favorites_routes.create_query_favorite(missing_builder, _make_request()))
    assert _payload(missing_response)["code"] == ErrorCode.CHATDATA_INVALID_FAVORITE_CONFIG

    create_payload = QueryFavoriteCreateRequest(
        datasource_id="ds1",
        query_type="builder",
        favorite_title="可视化收藏",
        query_text="select city from orders",
        builder_config={"dimensions": [{"name": "城市"}]},
    )
    create_response = asyncio.run(query_favorites_routes.create_query_favorite(create_payload, _make_request()))
    created = _payload(create_response)["data"]
    assert created["user_id"] == "u1"
    assert created["builder_config"]["dimensions"][0]["name"] == "城市"

    edit_raw_payload = QueryFavoriteUpdateRequest(query_text="select amount from orders")
    edit_raw_response = asyncio.run(query_favorites_routes.update_query_favorite(created["id"], edit_raw_payload, _make_request()))
    assert _payload(edit_raw_response)["code"] == ErrorCode.CHATDATA_OPERATION_NOT_ALLOWED

    rename_payload = QueryFavoriteUpdateRequest(favorite_title="可视化收藏2", semantic_config_id="sc_builder")
    rename_response = asyncio.run(query_favorites_routes.update_query_favorite(created["id"], rename_payload, _make_request()))
    renamed = _payload(rename_response)["data"]
    assert renamed["favorite_title"] == "可视化收藏2"
    assert renamed["semantic_config_id"] == "sc_builder"

    delete_response = asyncio.run(query_favorites_routes.delete_query_favorite(created["id"], _make_request()))
    assert _payload(delete_response)["status"] == "success"

    update_deleted_response = asyncio.run(query_favorites_routes.update_query_favorite(created["id"], QueryFavoriteUpdateRequest(favorite_title="新标题"), _make_request()))
    assert _payload(update_deleted_response)["code"] == ErrorCode.CHATDATA_FAVORITE_NOT_FOUND

    delete_deleted_response = asyncio.run(query_favorites_routes.delete_query_favorite(created["id"], _make_request()))
    assert _payload(delete_deleted_response)["code"] == ErrorCode.CHATDATA_FAVORITE_NOT_FOUND


def test_query_favorite_sql_can_update_query_and_builder_config(route_db):
    create_payload = QueryFavoriteCreateRequest(
        datasource_id="ds1",
        semantic_config_id="sc1",
        query_type="sql",
        favorite_title="SQL收藏",
        query_text="select 1",
    )
    create_response = asyncio.run(query_favorites_routes.create_query_favorite(create_payload, _make_request()))
    created = _payload(create_response)["data"]

    update_payload = QueryFavoriteUpdateRequest(
        favorite_title="SQL收藏2",
        query_text="select 2",
        semantic_config_id="sc2",
        builder_config={"sql": "select 2"},
    )
    update_response = asyncio.run(query_favorites_routes.update_query_favorite(created["id"], update_payload, _make_request()))
    updated = _payload(update_response)["data"]

    assert updated["favorite_title"] == "SQL收藏2"
    assert updated["query_text"] == "select 2"
    assert updated["semantic_config_id"] == "sc2"
    assert updated["builder_config"] == {"sql": "select 2"}

    list_response = asyncio.run(query_favorites_routes.get_query_favorites(_make_request(), datasource_id="ds1", query_type=None, limit=20))
    listed = _payload(list_response)
    assert listed["status"] == "success"
    assert listed["data"]["total"] >= 1


def test_sql_result_cache_insert_update_hit_and_invalid_payload(route_db, monkeypatch):
    monkeypatch.setenv("CHAT_DATA_QUERY_CACHE_TTL_SECONDS", "60")
    identity = query_cache.build_sql_result_cache_identity(
        {"id": "m1", "member_datasource_id": "m1", "query_executor_type": "direct", "source_type": "hive"},
        "select * from orders limit 10",
        datasource_id="ds1",
    )

    asyncio.run(query_cache.save_sql_result_cache(identity, {
        "success": True,
        "columns": ["id"],
        "data": [{"id": 1}],
        "total_count": 1,
        "execution_time_ms": 20,
    }, operator_id="u1"))

    first_hit = asyncio.run(query_cache.get_sql_result_cache(identity))
    assert first_hit["cache_hit"] is True
    assert first_hit["columns"] == ["id"]
    assert first_hit["data"] == [{"id": 1}]
    assert first_hit["cache_expires_in_seconds"] <= 60

    asyncio.run(query_cache.save_sql_result_cache(identity, {
        "success": True,
        "columns": ["id"],
        "data": [{"id": 2}],
        "total_count": 1,
        "execution_time_ms": 30,
    }, operator_id="u2"))
    second_hit = asyncio.run(query_cache.get_sql_result_cache(identity))
    assert second_hit["data"] == [{"id": 2}]

    async def corrupt_payload():
        async with route_db() as session:
            result = await session.execute(select(ChatDataSqlResultCache).filter_by(cache_key=identity["cache_key"]))
            cache_item = result.scalar_one()
            cache_item.result_payload = "{bad json"
            await session.commit()

    asyncio.run(corrupt_payload())
    assert asyncio.run(query_cache.get_sql_result_cache(identity)) is None

    async def read_cache_item():
        async with route_db() as session:
            result = await session.execute(select(ChatDataSqlResultCache).filter_by(cache_key=identity["cache_key"]))
            return result.scalar_one()

    assert asyncio.run(read_cache_item()).delete_flag == 1


def test_http_sql_result_cache_stores_remote_ref_only(route_db, monkeypatch):
    monkeypatch.setenv("CHAT_DATA_QUERY_CACHE_TTL_SECONDS", "60")
    identity = query_cache.build_sql_result_cache_identity(
        {"id": "m_http", "member_datasource_id": "m_http", "query_executor_type": "http", "source_type": "hive"},
        "select * from orders limit 10",
        datasource_id="ds1",
    )

    asyncio.run(query_cache.save_sql_result_cache(identity, {
        "success": True,
        "columns": ["id"],
        "data": [{"id": 1}],
        "total_count": 1,
        "execution_time_ms": 20,
        "remote_task_id": "remote_http_1",
    }, operator_id="u1", operator_name="测试用户"))

    async def read_cache_payload():
        async with route_db() as session:
            result = await session.execute(select(ChatDataSqlResultCache).filter_by(cache_key=identity["cache_key"]))
            cache_item = result.scalar_one()
            return json.loads(cache_item.result_payload), cache_item.result_row_count

    payload, row_count = asyncio.run(read_cache_payload())

    assert payload["cache_type"] == query_cache.HTTP_REMOTE_REF_CACHE_TYPE
    assert payload["remote_task_id"] == "remote_http_1"
    assert payload["source_type"] == "hive"
    assert payload["data"] == []
    assert payload["columns"] == []
    assert payload["total_count"] == 0
    assert row_count == 0


def test_sql_result_cache_helpers_handle_disabled_and_noop(route_db, monkeypatch):
    monkeypatch.setenv("CHAT_DATA_QUERY_CACHE_ENABLED", "true")
    assert asyncio.run(query_cache.is_sql_result_cache_enabled(skip_query_cache=False)) is True
    assert asyncio.run(query_cache.is_sql_result_cache_enabled(skip_query_cache=True)) is False
    monkeypatch.setenv("CHAT_DATA_QUERY_CACHE_ENABLED", "false")
    assert asyncio.run(query_cache.is_sql_result_cache_enabled(skip_query_cache=False)) is False

    assert asyncio.run(query_cache.get_sql_result_cache({})) is None
    asyncio.run(query_cache.save_sql_result_cache({}, {"success": True, "total_count": 1}))
    asyncio.run(query_cache.save_sql_result_cache({"cache_key": "cache_x"}, {"success": False, "total_count": 1}))

    async def count_cache_rows():
        async with route_db() as session:
            result = await session.execute(select(ChatDataSqlResultCache))
            return len(result.scalars().all())

    assert asyncio.run(count_cache_rows()) == 0


def test_query_task_record_and_remote_task_helpers(route_db, monkeypatch):
    class FixedUuid:
        hex = "task_generated"

    monkeypatch.setattr(query_runtime.uuid, "uuid4", lambda: FixedUuid())

    created = asyncio.run(query_providers.query_task_service.create_task_record(
        task_type="sql",
        datasource_id="ds1",
        query_type="",
        request_payload={"sql": "select 1", "user_id": "u1"},
        progress_text="等待执行",
    ))

    assert created["task_id"] == "task_generated"
    assert created["query_type"] == "sql"
    assert created["status"] == "pending"
    assert created["request_payload"] == {"sql": "select 1", "user_id": "u1"}
    assert asyncio.run(query_runtime.get_query_task("missing")) is None
    assert asyncio.run(query_runtime.get_query_task("task_generated"))["status"] == "pending"
    assert asyncio.run(query_runtime.is_query_task_cancelled("task_generated")) is False
    assert QueryHistoryService.task_user_id({"request_payload": "{\"user_id\": \"u1\"}"}) == "u1"
    assert QueryHistoryService.task_user_id({"request_payload": "{bad json"}) == ""

    started_time = datetime(2026, 6, 2, 11, 0, 0)
    finished_time = datetime(2026, 6, 2, 11, 0, 5)
    updated = asyncio.run(query_providers.query_task_service.update_query_task(
        "task_generated",
        status="running",
        progress_text="SQL 执行中",
        result_payload={"rows": [1]},
        error_message="",
        started_time=started_time,
        finished_time=finished_time,
    ))

    assert updated["status"] == "running"
    assert updated["progress_text"] == "SQL 执行中"
    assert updated["result_payload"] == {"rows": [1]}
    assert updated["started_time"] == started_time.isoformat()
    assert asyncio.run(query_providers.query_task_service.update_query_task("missing", status="running")) is None

    remote_task = asyncio.run(query_runtime.append_query_task_remote_task(
        "task_generated",
        {
            "member_datasource_id": "m1",
            "member_name": "成员1",
            "remote_task_id": "remote_1",
            "source_type": "Hive",
        },
    ))
    duplicate_task = asyncio.run(query_runtime.append_query_task_remote_task(
        "task_generated",
        {
            "member_datasource_id": "m1",
            "member_name": "成员1",
            "remote_task_id": "remote_1",
            "source_type": "hive",
        },
    ))

    assert remote_task["remote_tasks"] == [{
        "member_datasource_id": "m1",
        "member_name": "成员1",
        "remote_task_id": "remote_1",
        "source_type": "hive",
    }]
    assert duplicate_task["remote_tasks"] == remote_task["remote_tasks"]
    first_statement_task = asyncio.run(query_runtime.append_query_task_remote_task(
        "task_generated",
        {
            "member_datasource_id": "m1",
            "member_name": "成员1",
            "remote_task_id": "remote_1",
            "source_type": "hive",
            "statement_index": 0,
        },
    ))
    second_statement_task = asyncio.run(query_runtime.append_query_task_remote_task(
        "task_generated",
        {
            "member_datasource_id": "m1",
            "member_name": "成员1",
            "remote_task_id": "remote_1",
            "source_type": "hive",
            "statement_index": 1,
        },
    ))
    assert len(first_statement_task["remote_tasks"]) == 2
    assert len(second_statement_task["remote_tasks"]) == 3
    assert second_statement_task["remote_tasks"][-1]["statement_index"] == 1
    assert asyncio.run(query_runtime.append_query_task_remote_task("task_generated", {"member_datasource_id": "m2"})) is None
    assert asyncio.run(query_runtime.append_query_task_remote_task("missing", {"member_datasource_id": "m2", "remote_task_id": "remote_2"})) is None

    bad_response = query_runtime.build_query_task_response_data({
        "task_id": "bad",
        "request_payload": "{bad json",
        "result_payload": "{bad json",
        "remote_task_payload": "{bad json",
    })
    assert bad_response["request_payload"] is None
    assert bad_response["result_payload"] is None
    assert bad_response["remote_tasks"] == []


def test_query_execution_record_lifecycle(route_db, monkeypatch):
    class FakeDatasourceManager:
        async def get_execution_datasources_async(self, datasource_id, member_datasource_ids=None):
            assert datasource_id == "ds1"
            assert member_datasource_ids == ["m1", "m2"]
            return [
                {
                    "member_datasource_id": "m1",
                    "member_name": "成员1",
                    "query_executor_type": "http",
                    "source_type": "mysql",
                },
                {
                    "member_datasource_id": "m2",
                    "member_name": "成员2",
                    "source_type": "hive",
                },
            ]

        async def get_authorized_execution_datasources_async(self, datasource_id, member_datasource_ids=None, user_id=None, username=None):
            return await self.get_execution_datasources_async(datasource_id, member_datasource_ids)

    class FixedUuid:
        def __init__(self, value):
            self.hex = value

    uuid_values = iter([FixedUuid("exec_1"), FixedUuid("exec_2")])
    monkeypatch.setattr(query_providers, "get_datasource_manager", lambda: FakeDatasourceManager())
    monkeypatch.setattr(query_runtime, "get_datasource_manager", lambda: FakeDatasourceManager())
    monkeypatch.setattr(query_runtime.uuid, "uuid4", lambda: next(uuid_values))

    request = ExecuteSqlRequest(
        sql="select * from orders",
        datasource_id="ds1",
        member_datasource_ids=["m1", "m2"],
        user_id="u1",
        limit=5,
    )
    async def create_task():
        async with route_db() as session:
            session.add(ChatDataQueryTask(
                task_id="task_1",
                task_type="sql",
                datasource_id="ds1",
                query_type="sql",
                task_status="pending",
                request_payload=json.dumps({"operator_name": "操作员"}),
                create_time=datetime.now(),
                update_time=datetime.now(),
            ))
            await session.commit()

    asyncio.run(create_task())
    execution_map = asyncio.run(query_runtime.create_query_executions_for_task("task_1", request, operator_name="操作员"))

    assert execution_map == {"m1": "exec_1", "m2": "exec_2"}
    assert query_runtime.get_execution_id_by_member(execution_map, {"member_datasource_id": "m1"}) == "exec_1"
    assert query_runtime.get_execution_id_by_member({"": "exec_default"}, {}) == "exec_default"
    assert asyncio.run(query_runtime.get_query_execution_status("")) == ""
    assert asyncio.run(query_runtime.get_query_execution_status("exec_1")) == "pending"
    assert asyncio.run(query_runtime.get_query_executions("")) == []
    assert asyncio.run(query_providers.query_history_service.get_execution_models("")) == []
    assert asyncio.run(query_providers.query_history_service.get_execution_model("")) is None

    executions = asyncio.run(query_runtime.get_query_executions("task_1"))
    assert [item["execution_id"] for item in executions] == ["exec_1", "exec_2"]
    assert executions[0]["query"] == "select * from orders"
    assert executions[0]["operator_name"] == "操作员"

    started_time = datetime(2026, 6, 2, 12, 0, 0)
    finished_time = datetime(2026, 6, 2, 12, 0, 3)
    asyncio.run(query_providers.query_task_service.update_query_execution(
        "exec_1",
        sql_status="running",
        remote_task_id="remote_1",
        db_connection_id="conn_1",
        execution_time_ms=33,
        result_row_count=2,
        result_payload={"rows": [{"id": 1}]},
        error_message="",
        started_time=started_time,
        finished_time=finished_time,
    ))
    updated_model = asyncio.run(query_providers.query_history_service.get_execution_model("exec_1"))
    assert updated_model.sql_status == "running"
    assert updated_model.remote_task_id == "remote_1"
    assert updated_model.db_connection_id == "conn_1"
    assert updated_model.result_payload

    asyncio.run(query_runtime.update_query_execution_from_result("exec_1", {
        "success": True,
        "columns": ["id"],
        "data": [{"id": 1}],
        "total_count": 1,
        "execution_time_ms": 12,
        "cache_hit": True,
        "cache_age_seconds": 3,
        "cache_expires_in_seconds": 57,
    }))
    succeeded_model = asyncio.run(query_providers.query_history_service.get_execution_model("exec_1"))
    assert succeeded_model.sql_status == "succeeded"
    assert succeeded_model.result_row_count == 1
    assert json.loads(succeeded_model.result_payload)["cache_hit"] is True

    asyncio.run(query_runtime.update_query_execution_from_result("exec_1", {
        "success": True,
        "columns": ["id"],
        "data": [{"id": 2}],
        "total_count": 1,
        "execution_time_ms": 13,
    }, store_result_payload=False))
    not_stored_model = asyncio.run(query_providers.query_history_service.get_execution_model("exec_1"))
    assert not_stored_model.sql_status == "succeeded"
    assert json.loads(not_stored_model.result_payload)["cache_hit"] is True

    asyncio.run(query_runtime.update_query_execution_from_result("exec_1", {
        "success": True,
        "columns": ["id"],
        "data": [{"id": 3}],
        "total_count": 1,
        "execution_time_ms": 1780,
        "started_time": "2026-07-07 16:58:47",
        "finished_time": "2026-07-07 16:58:48",
    }))
    remote_time_model = asyncio.run(query_providers.query_history_service.get_execution_model("exec_1"))
    assert remote_time_model.execution_time_ms == 1780
    assert remote_time_model.started_time == datetime(2026, 7, 7, 16, 58, 47)
    assert remote_time_model.finished_time == datetime(2026, 7, 7, 16, 58, 48)

    asyncio.run(query_providers.query_task_service.update_query_execution("exec_2", sql_status="cancelled", error_message="已人工取消"))
    asyncio.run(query_runtime.update_query_execution_from_result("exec_2", {"success": False, "error": "不应覆盖"}))
    cancelled_model = asyncio.run(query_providers.query_history_service.get_execution_model("exec_2"))
    assert cancelled_model.sql_status == "cancelled"
    assert cancelled_model.error_message == "已人工取消"
    asyncio.run(query_providers.query_task_service.update_query_execution("", sql_status="running"))
    asyncio.run(query_providers.query_task_service.update_query_execution("missing", sql_status="running"))


def test_execute_sql_for_member_callbacks_cancel_and_error(monkeypatch):
    updates = []
    remote_tasks = []
    result_updates = []
    closed = []

    class CallbackExecutor:
        async def execute(self, sql, limit=100, timeout=None, user_id=None, username=None, datasource_id=None, remote_task_callback=None, db_connection_callback=None):
            assert username == "测试用户"
            assert user_id == "u1"
            await remote_task_callback({"remote_task_id": "remote_1", "source_type": "Hive"})
            await db_connection_callback({"db_connection_id": "conn_1"})
            return {
                "success": True,
                "columns": ["id"],
                "data": [{"id": 1}],
                "total_count": 1,
                "execution_time_ms": 10,
                "remote_task_id": "remote_1",
                "db_connection_id": "conn_1",
            }

        async def close(self):
            closed.append("callback")

    class FailingExecutor:
        async def execute(self, **_kwargs):
            raise RuntimeError("executor failed")

        async def close(self):
            closed.append("failing")

    async def fake_limiter(_user_id, _target, operation):
        return await operation()

    async def fake_update_execution(execution_id, **kwargs):
        updates.append((execution_id, kwargs))

    async def fake_append_remote(task_id, remote_task):
        remote_tasks.append((task_id, remote_task))
        return {}

    async def fake_update_from_result(execution_id, result, **kwargs):
        result_updates.append((execution_id, result, kwargs))
    monkeypatch.setenv("CHAT_DATA_QUERY_CACHE_ENABLED", "false")
    monkeypatch.setattr(query_runtime, "run_with_sql_user_datasource_limit", fake_limiter)
    monkeypatch.setattr(query_providers.query_task_service, "update_query_execution", fake_update_execution)
    monkeypatch.setattr(query_runtime, "update_query_execution", fake_update_execution)
    monkeypatch.setattr(query_runtime, "append_query_task_remote_task", fake_append_remote)
    monkeypatch.setattr(query_runtime, "update_query_execution_from_result", fake_update_from_result)
    monkeypatch.setattr(query_runtime, "get_query_execution_status", lambda _execution_id: asyncio.sleep(0, result="running"))
    monkeypatch.setattr(query_runtime, "get_executor", lambda _target: CallbackExecutor())

    result = asyncio.run(query_providers.query_task_service.execute_sql_for_member(
        {"member_datasource_id": "m1", "member_name": "成员1", "source_type": "mysql"},
        "select 1",
        10,
        30,
        user_id="u1",
        username="测试用户",
        query_task_id="task_1",
        execution_id="exec_1",
        datasource_id="ds1",
    ))

    assert result["status"] == "succeeded"
    assert "success" not in result
    assert result["remote_task_id"] == "remote_1"
    assert updates[0][1]["sql_status"] == "running"
    assert ("exec_1", {"remote_task_id": "remote_1"}) in updates
    assert ("exec_1", {"db_connection_id": "conn_1"}) in updates
    assert remote_tasks == [("task_1", {
        "member_datasource_id": "m1",
        "member_name": "成员1",
        "remote_task_id": "remote_1",
        "source_type": "hive",
    })]
    assert result_updates[-1][0] == "exec_1"
    assert result_updates[-1][2] == {"store_result_payload": True}
    assert closed == ["callback"]

    http_result = asyncio.run(query_providers.query_task_service.execute_sql_for_member(
        {"member_datasource_id": "m_http", "member_name": "HTTP成员", "source_type": "mysql", "query_executor_type": "http"},
        "select 1",
        10,
        30,
        user_id="u1",
        username="测试用户",
        query_task_id="task_1",
        execution_id="exec_http",
        datasource_id="ds1",
    ))
    assert http_result["status"] == "succeeded"
    assert "success" not in http_result
    assert result_updates[-1][0] == "exec_http"
    assert result_updates[-1][2] == {"store_result_payload": False}

    monkeypatch.setattr(query_runtime, "get_query_execution_status", lambda _execution_id: asyncio.sleep(0, result="cancelled"))
    cancelled = asyncio.run(query_providers.query_task_service.execute_sql_for_member(
        {"member_datasource_id": "m1", "member_name": "成员1", "source_type": "mysql"},
        "select 1",
        10,
        30,
        execution_id="exec_cancelled",
    ))
    assert cancelled["status"] == "cancelled"
    assert "success" not in cancelled
    assert cancelled["error"] == "已人工取消"

    monkeypatch.setattr(query_runtime, "get_query_execution_status", lambda _execution_id: asyncio.sleep(0, result="running"))
    monkeypatch.setattr(query_runtime, "get_executor", lambda _target: FailingExecutor())
    failed = asyncio.run(query_providers.query_task_service.execute_sql_for_member(
        {"member_datasource_id": "m2", "member_name": "成员2", "source_type": "mysql"},
        "select 1",
        10,
        30,
        execution_id="exec_failed",
    ))
    assert failed["status"] == "failed"
    assert "success" not in failed
    assert failed["error"] == "executor failed"
    assert closed[-1] == "failing"


def test_execute_sql_for_member_uses_http_remote_ref_cache(monkeypatch):
    updates = []
    remote_tasks = []
    result_updates = []

    async def fake_get_cache(cache_identity):
        assert cache_identity["executor_type"] == "http"
        return {
            "success": True,
            "cache_type": query_cache.HTTP_REMOTE_REF_CACHE_TYPE,
            "remote_task_id": "remote_cached",
            "source_type": "hive",
            "columns": [],
            "data": [],
            "total_count": 0,
            "execution_time_ms": 3,
            "cache_hit": True,
            "cache_age_seconds": 10,
            "cache_expires_in_seconds": 50,
        }

    async def fake_fetch(remote_task_id, source_type="hive", user_id=None, username=None):
        assert remote_task_id == "remote_cached"
        assert source_type == "hive"
        assert user_id == "u1"
        assert username == "测试用户"
        return {
            "status": "succeeded",
            "success": True,
            "columns": ["id"],
            "data": [{"id": 1}],
            "total_count": 1,
            "execution_time_ms": 15,
            "error": None,
        }

    async def fake_update_execution(execution_id, **kwargs):
        updates.append((execution_id, kwargs))

    async def fake_append_remote(task_id, remote_task):
        remote_tasks.append((task_id, remote_task))
        return {}

    async def fake_update_from_result(execution_id, result, **kwargs):
        result_updates.append((execution_id, result, kwargs))

    async def raise_limiter(_user_id, _target, _operation):
        raise AssertionError("HTTP remote-ref cache hit should not submit a new query")

    monkeypatch.setenv("CHAT_DATA_QUERY_CACHE_ENABLED", "true")
    monkeypatch.setattr(query_cache, "get_sql_result_cache", fake_get_cache)
    monkeypatch.setattr(query_runtime, "fetch_http_query_task_result", fake_fetch)
    monkeypatch.setattr(query_runtime, "run_with_sql_user_datasource_limit", raise_limiter)
    monkeypatch.setattr(query_runtime, "get_executor", lambda _target: (_ for _ in ()).throw(AssertionError("cache hit should not get executor")))
    monkeypatch.setattr(query_runtime, "update_query_execution", fake_update_execution)
    monkeypatch.setattr(query_runtime, "append_query_task_remote_task", fake_append_remote)
    monkeypatch.setattr(query_runtime, "update_query_execution_from_result", fake_update_from_result)
    monkeypatch.setattr(query_runtime, "get_query_execution_status", lambda _execution_id: asyncio.sleep(0, result="running"))

    result = asyncio.run(query_providers.query_task_service.execute_sql_for_member(
        {"member_datasource_id": "m_http", "member_name": "HTTP成员", "source_type": "hive", "query_executor_type": "http"},
        "select 1",
        10,
        30,
        user_id="u1",
        username="测试用户",
        query_task_id="task_1",
        execution_id="exec_http",
        datasource_id="ds1",
    ))

    assert result["status"] == "succeeded"
    assert "success" not in result
    assert result["columns"] == ["id"]
    assert result["data"] == [{"id": 1}]
    assert result["remote_task_id"] == "remote_cached"
    assert result["cache_hit"] is True
    assert result["cache_age_seconds"] == 10
    assert ("exec_http", {"remote_task_id": "remote_cached"}) in updates
    assert remote_tasks == [("task_1", {
        "member_datasource_id": "m_http",
        "member_name": "HTTP成员",
        "remote_task_id": "remote_cached",
        "source_type": "hive",
    })]
    assert result_updates[-1][0] == "exec_http"
    assert result_updates[-1][2] == {"store_result_payload": False}


def test_build_execute_sql_result_data_edge_cases(monkeypatch):
    class MissingDatasourceManager:
        async def get_datasource_async(self, _datasource_id):
            return None

    monkeypatch.setattr(query_providers, "get_datasource_manager", lambda: MissingDatasourceManager())
    monkeypatch.setattr(query_runtime, "get_datasource_manager", lambda: MissingDatasourceManager())
    with pytest.raises(ValueError, match="数据源不存在"):
        asyncio.run(query_runtime.build_execute_sql_result_data(ExecuteSqlRequest(sql="select 1", datasource_id="missing")))

    class EmptyTargetManager:
        async def get_datasource_async(self, _datasource_id):
            return {"id": "ds1"}

        async def get_execution_datasources_async(self, _datasource_id, _member_ids=None):
            return []

        async def get_authorized_execution_datasources_async(self, _datasource_id, _member_ids=None, user_id=None, username=None):
            return await self.get_execution_datasources_async(_datasource_id, _member_ids)

    monkeypatch.setattr(query_providers, "get_datasource_manager", lambda: EmptyTargetManager())
    monkeypatch.setattr(query_runtime, "get_datasource_manager", lambda: EmptyTargetManager())
    result = asyncio.run(query_runtime.build_execute_sql_result_data(ExecuteSqlRequest(sql="select 1", datasource_id="ds1")))
    assert result["status"] == "failed"
    assert result["error"] == "所有执行对象均执行失败"
    assert result["results"] == []


def test_query_task_dispatch_and_status_helpers(monkeypatch):
    calls = []
    updates = []

    assert QueryHistoryService.build_cancel_task_status([]) == "cancelled"
    assert QueryHistoryService.build_cancel_task_status([{"status": "cancelled"}]) == "cancelled"
    assert QueryHistoryService.build_cancel_task_status([{"status": "kill_failed"}]) == "failed"
    assert QueryHistoryService.build_cancel_task_status([{"status": "cancelled"}, {"status": "kill_failed"}]) == "partial_success"
    assert query_providers.query_task_service.build_cancelled_execution_map([
        {"member_datasource_id": "m1", "sql_status": "cancelled"},
        {"member_datasource_id": "m2", "sql_status": "succeeded"},
        {"member_datasource_id": "m3", "sql_status": "kill_failed"},
        {"member_datasource_id": "", "sql_status": "cancelled"},
    ]) == {
        "m1": {"member_datasource_id": "m1", "sql_status": "cancelled"},
        "m3": {"member_datasource_id": "m3", "sql_status": "kill_failed"},
    }
    assert query_runtime.load_remote_tasks([
        {"member_datasource_id": "m1", "member_name": "成员1", "remote_task_id": "r1", "source_type": "Hive"},
        {"member_datasource_id": "", "remote_task_id": "r2"},
        "bad",
    ]) == [{"member_datasource_id": "m1", "member_name": "成员1", "remote_task_id": "r1", "source_type": "hive"}]
    assert query_runtime.load_remote_tasks("{}") == []

    task_by_id = {
        "missing": None,
        "sql": {"task_id": "sql", "task_type": "sql"},
        "panel": {"task_id": "panel", "task_type": "dashboard_panel"},
        "batch": {"task_id": "batch", "task_type": "dashboard_batch"},
        "unsupported": {"task_id": "unsupported", "task_type": "unknown"},
        "crash": {"task_id": "crash", "task_type": "dashboard_panel"},
    }

    async def fake_get_task(task_id):
        return task_by_id[task_id]

    async def fake_run_sql(task_id, task):
        calls.append(("sql", task_id, task))

    async def fake_run_panel(task_id, task):
        calls.append(("panel", task_id, task))
        if task_id == "crash":
            raise RuntimeError("panel crashed")

    async def fake_run_batch(task_id, task):
        calls.append(("batch", task_id, task))

    async def fake_update_task(task_id, **kwargs):
        updates.append((task_id, kwargs))
        return None

    monkeypatch.setattr(query_runtime, "get_query_task", fake_get_task)
    monkeypatch.setattr(query_providers.query_task_service, "run_sql_query_task", fake_run_sql)
    monkeypatch.setattr(dashboard_runtime, "run_dashboard_panel_task", fake_run_panel)
    monkeypatch.setattr(dashboard_runtime, "run_dashboard_batch_task", fake_run_batch)
    monkeypatch.setattr(query_providers.query_task_service, "update_query_task", fake_update_task)

    asyncio.run(query_providers.query_task_service.run_query_task("missing"))
    asyncio.run(query_providers.query_task_service.run_query_task("sql"))
    asyncio.run(query_providers.query_task_service.run_query_task("panel"))
    asyncio.run(query_providers.query_task_service.run_query_task("batch"))
    asyncio.run(query_providers.query_task_service.run_query_task("unsupported"))
    asyncio.run(query_providers.query_task_service.run_query_task("crash"))

    assert [item[0] for item in calls] == ["sql", "panel", "batch", "panel"]
    assert updates[0][0] == "unsupported"
    assert updates[0][1]["status"] == "failed"
    assert "不支持的任务类型" in updates[0][1]["error_message"]
    assert updates[1][0] == "crash"
    assert updates[1][1]["error_message"] == "执行失败，请稍后重试或联系管理员"


def test_prepare_sql_request_keeps_sql_for_executor_validation(monkeypatch):
    class Manager:
        def __init__(self, datasource):
            self.datasource = datasource

        async def get_datasource_async(self, _datasource_id):
            return self.datasource

    builder_request = QueryTaskCreateRequest(
        datasource_id="ds1",
        sql=["select * from orders -- 注释"],
        query_type="builder",
    )
    with pytest.raises(ValueError, match="必须基于指标集"):
        asyncio.run(query_runtime.prepare_sql_batch_request_for_execution(builder_request, {"source_type": "mysql"}))

    sql_request = QueryTaskCreateRequest(
        datasource_id="ds1",
        sql=["select 1; drop table orders"],
        query_type="sql",
    )
    with pytest.raises(ValueError, match="一次只能执行一条 SQL"):
        asyncio.run(query_runtime.prepare_sql_batch_request_for_execution(sql_request, {"source_type": "mysql"}))


def test_create_query_task_endpoint_branches(monkeypatch):
    class Manager:
        def __init__(self, datasource):
            self.datasource = datasource

        async def get_datasource_async(self, _datasource_id):
            return self.datasource

    monkeypatch.setattr(query_execution_routes, "_get_current_user", lambda _request: SimpleNamespace(user_id="u1", username="测试用户"))
    monkeypatch.setattr(query_providers, "get_datasource_manager", lambda: Manager(None))
    missing_response = asyncio.run(query_execution_routes.create_query_task(_make_request(), QueryTaskCreateRequest(
        datasource_id="missing",
        sql=["select 1"],
    )))
    assert _payload(missing_response)["code"] == ErrorCode.CHATDATA_NOT_FOUND

    monkeypatch.setattr(query_providers, "get_datasource_manager", lambda: Manager({"id": "ds1", "source_type": "mysql"}))
    invalid_sql_response = asyncio.run(query_execution_routes.create_query_task(_make_request(), QueryTaskCreateRequest(
        datasource_id="ds1",
        sql=["select 1; select 2"],
    )))
    assert _payload(invalid_sql_response)["code"] == ErrorCode.CHATDATA_BAD_REQUEST
