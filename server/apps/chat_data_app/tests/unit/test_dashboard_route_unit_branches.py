import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from utils.error_codes import ErrorCode
from apps.chat_data_app.routers import dashboard as dashboard_routes
from apps.chat_data_app.schema import (
    DashboardCreateRequest,
    DashboardFromSpecRequest,
    DashboardUpdateRequest,
    LayoutUpdateRequest,
    PanelBatchExecuteRequest,
    PanelCreateRequest,
    PanelNameGenerateRequest,
    PanelUpdateRequest,
)
from apps.chat_data_app.services.dashboard import runtime as dashboard_runtime


@pytest.fixture(autouse=True)
def mirror_dashboard_service():
    previous = dashboard_runtime.get_dashboard_service
    dashboard_runtime.get_dashboard_service = lambda: dashboard_routes.get_dashboard_service()
    yield
    dashboard_runtime.get_dashboard_service = previous


@pytest.fixture(autouse=True)
def mirror_dashboard_service():
    previous = dashboard_runtime.get_dashboard_service
    dashboard_runtime.get_dashboard_service = lambda: dashboard_routes.get_dashboard_service()
    yield
    dashboard_runtime.get_dashboard_service = previous


def response_payload(response):
    if isinstance(response, dict):
        return response
    return json.loads(response.body.decode("utf-8"))


async def collect_streaming_response(response):
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk))
    return "".join(chunks)


class FakeDashboardService:
    def __init__(self):
        self.dashboard = {
            "id": "dash_a",
            "name": "看板A",
            "user_id": "u1",
            "datasource_id": "ds_a",
            "panels": [
                {
                    "id": "panel_a",
                    "name": "面板A",
                    "chart_type": "table",
                    "chart_config": {},
                    "sql": "select 1",
                    "datasource_id": "ds_a",
                    "member_datasource_id": "",
                },
                {
                    "id": "panel_b",
                    "name": "面板B",
                    "chart_type": "bar",
                    "chart_config": {},
                    "sql": "select 2",
                    "datasource_id": "",
                    "member_datasource_id": "",
                },
            ],
        }
        self.deleted = []
        self.public_state = None
        self.layout_calls = []

    async def list_dashboards(self, user_id="", scope="mine"):
        return [dict(self.dashboard)] if scope in {"mine", "public"} else []

    async def create_dashboard(self, **kwargs):
        return {"id": "dash_new", "user_id": kwargs["user_id"], "name": kwargs["name"]}

    async def get_dashboard(self, dashboard_id, user_id=None, include_public=True):
        if dashboard_id == "missing":
            return None
        if dashboard_id == "forbidden":
            return {"id": dashboard_id, "user_id": "u2", "panels": []} if user_id is None else None
        if dashboard_id == "empty":
            return {"id": dashboard_id, "name": "空看板", "user_id": user_id or "u1", "datasource_id": "", "panels": []}
        return dict(self.dashboard)

    async def update_dashboard(self, **kwargs):
        if kwargs["dashboard_id"] == "missing":
            return None
        return {"id": kwargs["dashboard_id"], "user_id": kwargs["user_id"], "name": kwargs.get("name") or "updated"}

    async def delete_dashboard(self, dashboard_id, **_kwargs):
        if dashboard_id in {"missing", "forbidden"}:
            return False
        self.deleted.append(dashboard_id)
        return True

    async def set_dashboard_public(self, dashboard_id, user_id="", is_public=False, **_kwargs):
        if dashboard_id in {"missing", "forbidden"}:
            return None
        self.public_state = is_public
        return {"id": dashboard_id, "user_id": user_id, "is_public": is_public}

    async def add_panel(self, **kwargs):
        if kwargs["dashboard_id"] == "missing":
            return None
        return {"id": "panel_new", "name": kwargs["name"], "sql": kwargs["sql"]}

    async def get_panel(self, panel_id, dashboard_id=None, user_id=None, include_public=True):
        for panel in self.dashboard["panels"]:
            if panel["id"] == panel_id:
                return dict(panel)
        return None

    async def update_panel(self, **kwargs):
        if kwargs["panel_id"] == "missing":
            return None
        return {"id": kwargs["panel_id"], "name": kwargs.get("name")}

    async def delete_panel(self, dashboard_id, panel_id, **_kwargs):
        return panel_id != "missing"

    async def update_panels_layout(self, dashboard_id, layouts, **_kwargs):
        if dashboard_id == "missing":
            return None
        self.layout_calls.append(layouts)
        return len(layouts)


class FakeDatasourceManager:
    async def get_execution_datasources_async(self, datasource_id, selected_member_ids=None):
        member_id = (selected_member_ids or ["member_a"])[0]
        if member_id == "missing_member":
            raise ValueError("成员数据源不存在或不属于当前组: missing_member")
        return [{
            "member_datasource_id": member_id,
            "member_name": "成员A",
            "source_type": "mysql",
            "query_executor_type": "direct",
        }]

    async def get_authorized_execution_datasources_async(
        self,
        datasource_id,
        selected_member_ids=None,
        user_id=None,
        username=None,
    ):
        return await self.get_execution_datasources_async(datasource_id, selected_member_ids)


class FakeRuntime:
    def __init__(self):
        self.ds_manager = FakeDatasourceManager()
        self.updates = []
        self.created_tasks = []
        self.cancelled = False
        self.executions = []
        self.query_task_poll_interval_ms = 1500

    def get_datasource_manager(self):
        return self.ds_manager

    async def execute_sql_for_member(self, **kwargs):
        self.executions.append(kwargs)
        return {
            "member_datasource_id": kwargs["execution_target"].get("member_datasource_id"),
            "member_name": kwargs["execution_target"].get("member_name"),
            "success": "fail" not in kwargs["sql"],
            "columns": ["c"],
            "data": [{"c": 1}],
            "total_count": 1,
            "execution_time_ms": 3,
            "error": "boom" if "fail" in kwargs["sql"] else None,
            "sql": kwargs["sql"],
        }

    async def create_task_record(self, **kwargs):
        self.created_tasks.append(kwargs)
        return {"task_id": f"task_{len(self.created_tasks)}", "task_type": kwargs["task_type"], "status": "pending"}

    async def update_query_task(self, task_id, **kwargs):
        self.updates.append({"task_id": task_id, **kwargs})
        return {"task_id": task_id, **kwargs}

    async def is_query_task_cancelled(self, _task_id):
        return self.cancelled

    def schedule_query_task(self, task_id):
        self.scheduled = task_id


@pytest.fixture()
def dashboard_context(monkeypatch):
    service = FakeDashboardService()
    runtime = FakeRuntime()
    user = SimpleNamespace(user_id="u1", username="用户一")
    monkeypatch.setattr(dashboard_routes, "_current_user", lambda _request: user)
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: service)
    monkeypatch.setattr(dashboard_runtime, "_dashboard_runtime_value", runtime)
    return service, runtime, user


def test_dashboard_permission_and_small_helpers(dashboard_context):
    service, _runtime, _user = dashboard_context

    assert dashboard_runtime.dashboard_runtime() is _runtime
    dashboard_runtime.configure_dashboard_runtime(_runtime)
    assert dashboard_runtime.dashboard_runtime() is _runtime
    dashboard_runtime._dashboard_runtime_value = None
    with pytest.raises(RuntimeError, match="not configured"):
        dashboard_runtime.dashboard_runtime()
    dashboard_runtime.configure_dashboard_runtime(_runtime)
    assert dashboard_routes._with_dashboard_permission(None, "u1") is None
    assert dashboard_routes._with_dashboard_permission({"user_id": "u1"}, "u1")["can_manage"] is True
    assert dashboard_routes._with_dashboards_permission([{"user_id": "u2"}], "u1")[0]["can_manage"] is False
    assert dashboard_runtime.build_dashboard_execution_target_cache_key(" ds ", None) == ("ds", "")
    assert dashboard_runtime.build_effective_panel_member_datasource_ids(
        ["p1", "p2", "p3"],
        {
            "p1": {"member_datasource_id": "saved_1"},
            "p2": {"member_datasource_id": "saved_2"},
            "p3": {},
        },
        {"p1": "override_1"},
    ) == {"p1": "override_1", "p2": "saved_2"}
    assert dashboard_runtime.build_panel_execution_plan(
        ["p1", "p2"],
        {
            "p1": {
                "member_datasource_id": "saved_1",
                "chart_config": {"multiSource": {"enabled": True, "memberDatasourceIds": ["saved_1", "saved_2"]}},
            },
            "p2": {"member_datasource_id": "saved_3"},
        },
        {"p2": "override_3"},
    ) == {
        "p1": {
            "mode": "multi",
            "member_datasource_ids": ["saved_1", "saved_2"],
            "primary_member_datasource_id": "saved_1",
        },
        "p2": {
            "mode": "single",
            "member_datasource_ids": ["override_3"],
            "primary_member_datasource_id": "override_3",
        },
    }
    assert dashboard_runtime.build_dashboard_panel_sql_preview(" select   *  from t ", 100) == "select * from t"
    assert dashboard_runtime.build_dashboard_panel_sql_preview("x" * 200, 10) == "x" * 10 + "..."
    assert dashboard_routes._truncate_text("  abcdef  ", 3) == "abc"
    assert dashboard_routes._truncate_text("   ", 3) == ""
    assert "_" in dashboard_routes._build_panel_name_fallback("table")
    assert dashboard_routes._chart_type_label("table")
    dashboard_runtime.log_dashboard_panel_timing({"id": "p", "name": "面板", "sql": "select 1"}, 1, "succeeded")
    dashboard_runtime.log_dashboard_panel_timing(None, 1, "failed", error="boom")


@pytest.mark.asyncio
async def test_dashboard_panel_execution_helpers(dashboard_context, monkeypatch):
    service, runtime, _user = dashboard_context
    local_panel = {"id": "panel_local", "name": "本地", "chart_type": "table", "chart_config": {}, "sql": "select 1", "datasource_id": ""}
    remote_panel = dict(service.dashboard["panels"][0])

    local_result = await dashboard_runtime.execute_dashboard_panel_sql(local_panel)
    assert local_result["status"] == "failed"
    assert local_result["error"] == "面板缺少数据源配置"
    assert local_result["results"][0]["member_datasource_id"] is None

    remote_result = await dashboard_runtime.execute_dashboard_panel_sql(remote_panel, member_datasource_id="member_b", user_id="u1", operator_name="用户一")
    assert remote_result["results"][0]["member_datasource_id"] == "member_b"
    assert runtime.executions[-1]["username"] == "用户一"

    explicit_target_result = await dashboard_runtime.execute_dashboard_panel_sql_with_target(remote_panel, execution_target={"member_datasource_id": "m2", "member_name": "M2", "source_type": "hive"})
    assert explicit_target_result["results"][0]["member_name"] == "M2"

    payload = await dashboard_runtime.build_dashboard_panel_execution_result(remote_panel, execution_target={"member_datasource_id": "m3", "source_type": "mysql"})
    assert payload["panel_id"] == "panel_a"
    assert payload["result"]["status"] == "succeeded"
    dashboard_runtime.raise_if_dashboard_panel_execution_failed(payload)
    with pytest.raises(RuntimeError, match="boom"):
        dashboard_runtime.raise_if_dashboard_panel_execution_failed({"panel_id": "p", "result": {"status": "failed", "error": "boom"}})
    with pytest.raises(RuntimeError, match="面板执行失败"):
        dashboard_runtime.raise_if_dashboard_panel_execution_failed({"panel_id": "p", "result": {"status": "failed"}})


@pytest.mark.asyncio
async def test_create_dashboard_panel_execution_persists_expected_record(dashboard_context, monkeypatch):
    _service, _runtime, _user = dashboard_context
    added = []
    commits = []

    class FakeSession:
        def add(self, item):
            added.append(item)

        async def commit(self):
            commits.append(True)

    @asynccontextmanager
    async def fake_get_session():
        yield FakeSession()

    monkeypatch.setattr(dashboard_runtime, "get_session", fake_get_session)

    assert await dashboard_runtime.create_dashboard_panel_execution(
        task_id="",
        panel={"datasource_id": "ds_a", "sql": "select 1"},
        execution_target={},
        user_id="u1",
        operator_name="用户一",
    ) is None
    assert await dashboard_runtime.create_dashboard_panel_execution(
        task_id="task_a",
        panel={"datasource_id": "", "sql": "select 1"},
        execution_target={},
        user_id="u1",
        operator_name="用户一",
    ) is None

    execution_id = await dashboard_runtime.create_dashboard_panel_execution(
        task_id="task_a",
        panel={"datasource_id": " ds_a ", "sql": " select 1 "},
        execution_target={"member_datasource_id": "m1", "member_name": "成员1", "query_executor_type": "HTTP"},
        user_id="u1",
        operator_name="用户一",
    )

    assert execution_id
    assert commits == [True]
    assert added[0].task_id == "task_a"
    assert added[0].datasource_id == "ds_a"
    assert added[0].sql_text == "select 1"
    assert added[0].member_datasource_id == "m1"
    assert added[0].executor_type == "http"


@pytest.mark.asyncio
async def test_dashboard_batch_context_and_task_items(dashboard_context, monkeypatch):
    service, runtime, _user = dashboard_context

    panel_map, target_map = await dashboard_runtime.prepare_dashboard_batch_context(
        "dash_a",
        ["panel_a", "panel_b"],
        {"panel_a": "member_a"},
        preloaded_dashboard=service.dashboard,
        user_id="u1",
    )
    assert set(panel_map) == {"panel_a", "panel_b"}
    assert ("ds_a", "member_a") in target_map

    with pytest.raises(ValueError, match="面板不存在"):
        await dashboard_runtime.prepare_dashboard_batch_context("dash_a", ["missing"], {}, preloaded_dashboard=service.dashboard)
    with pytest.raises(ValueError, match="大屏不存在"):
        await dashboard_runtime.prepare_dashboard_batch_context("missing", ["panel_a"], {})

    monkeypatch.setattr(dashboard_runtime, "create_dashboard_panel_execution", lambda **_kwargs: asyncio.sleep(0, result="exec_a"))
    panel_id, entry = await dashboard_runtime.execute_dashboard_panel_task_item(
        "panel_a",
        semaphore=asyncio.Semaphore(1),
        panel=panel_map["panel_a"],
        execution_target=target_map[("ds_a", "member_a")],
        query_task_id="task_a",
        user_id="u1",
        operator_name="用户一",
    )
    assert panel_id == "panel_a"
    assert entry["status"] == "succeeded"

    failed_id, failed_entry = await dashboard_runtime.execute_dashboard_panel_task_item(
        "panel_fail",
        panel={"id": "panel_fail", "name": "失败", "chart_type": "table", "chart_config": {}, "sql": "fail sql", "datasource_id": "ds_a"},
        execution_target={"member_datasource_id": "m", "source_type": "mysql"},
    )
    assert failed_id == "panel_fail"
    assert failed_entry["status"] == "failed"


@pytest.mark.asyncio
async def test_dashboard_batch_shared_sql_semaphore_caps_panel_and_member_queries(dashboard_context, monkeypatch):
    _service, runtime, _user = dashboard_context
    active_count = 0
    max_active_count = 0

    monkeypatch.setattr(dashboard_runtime, "DASHBOARD_PANEL_MAX_CONCURRENCY", 2)
    monkeypatch.setattr(dashboard_runtime, "create_dashboard_panel_execution", lambda **_kwargs: asyncio.sleep(0, result="exec_a"))

    async def execute_sql_for_member(**kwargs):
        nonlocal active_count, max_active_count
        active_count += 1
        max_active_count = max(max_active_count, active_count)
        await asyncio.sleep(0.01)
        active_count -= 1
        return {
            "member_datasource_id": kwargs["execution_target"].get("member_datasource_id"),
            "member_name": kwargs["execution_target"].get("member_name"),
            "success": True,
            "columns": ["c"],
            "data": [{"c": 1}],
            "total_count": 1,
            "execution_time_ms": 1,
            "sql": kwargs["sql"],
        }

    runtime.execute_sql_for_member = execute_sql_for_member
    panel_map = {
        "panel_a": {
            "id": "panel_a",
            "name": "面板A",
            "chart_type": "table",
            "chart_config": {"multiSource": {"enabled": True, "memberDatasourceIds": ["m1", "m2", "m3"]}},
            "sql": "select 1",
            "datasource_id": "ds_a",
        },
        "panel_b": {
            "id": "panel_b",
            "name": "面板B",
            "chart_type": "table",
            "chart_config": {"multiSource": {"enabled": True, "memberDatasourceIds": ["m1", "m2", "m3"]}},
            "sql": "select 2",
            "datasource_id": "ds_a",
        },
    }

    class MultiMemberDatasourceManager:
        async def get_execution_datasources_async(self, _datasource_id, selected_member_ids=None):
            return [
                {"member_datasource_id": member_id, "member_name": member_id, "source_type": "mysql"}
                for member_id in selected_member_ids or []
            ]

        async def get_authorized_execution_datasources_async(self, datasource_id, selected_member_ids=None, user_id=None, username=None):
            return await self.get_execution_datasources_async(datasource_id, selected_member_ids)

    runtime.ds_manager = MultiMemberDatasourceManager()
    task = {
        "request_payload": json.dumps({
            "dashboard_id": "dash_a",
            "panel_ids": ["panel_a", "panel_b"],
            "user_id": "u1",
            "operator_name": "用户一",
        }),
    }
    monkeypatch.setattr(dashboard_runtime, "prepare_dashboard_batch_context", lambda *_args, **_kwargs: asyncio.sleep(0, result=(panel_map, {})))

    await dashboard_runtime.run_dashboard_batch_task("task_shared_limit", task)

    assert max_active_count <= 2
    assert len(runtime.executions) == 0


@pytest.mark.asyncio
async def test_dashboard_batch_context_reuses_execution_target_cache(dashboard_context, monkeypatch):
    _service, runtime, _user = dashboard_context
    calls = []

    class CountingDatasourceManager:
        async def get_execution_datasources_async(self, datasource_id, selected_member_ids=None):
            calls.append((datasource_id, selected_member_ids))
            return [{
                "member_datasource_id": (selected_member_ids or ["member_a"])[0],
                "member_name": "成员A",
                "source_type": "mysql",
            }]

        async def get_authorized_execution_datasources_async(self, datasource_id, selected_member_ids=None, user_id=None, username=None):
            return await self.get_execution_datasources_async(datasource_id, selected_member_ids)

    runtime.ds_manager = CountingDatasourceManager()
    dashboard = {
        "id": "dash_cached",
        "panels": [
            {"id": "p1", "datasource_id": "ds_a", "sql": "select 1"},
            {"id": "p2", "datasource_id": "ds_a", "sql": "select 2"},
        ],
    }

    panel_map, target_map = await dashboard_runtime.prepare_dashboard_batch_context(
        "dash_cached",
        ["p1", "p2"],
        {"p1": "member_a", "p2": "member_a"},
        preloaded_dashboard=dashboard,
        user_id="u1",
    )

    assert set(panel_map) == {"p1", "p2"}
    assert target_map[("ds_a", "member_a")]["member_datasource_id"] == "member_a"
    assert calls == [("ds_a", ["member_a"])]


@pytest.mark.asyncio
async def test_dashboard_permission_name_and_panel_loader_branches(dashboard_context, monkeypatch):
    service, _runtime, _user = dashboard_context

    denied = response_payload(await dashboard_routes._dashboard_permission_error(service, "forbidden"))
    assert denied["code"] == ErrorCode.CHATDATA_DASHBOARD_PERMISSION_DENIED
    not_found = response_payload(await dashboard_routes._dashboard_permission_error(service, "missing"))
    assert not_found["code"] == ErrorCode.CHATDATA_NOT_FOUND

    assert (await dashboard_runtime.load_dashboard_panel_or_raise("panel_a"))["id"] == "panel_a"
    with pytest.raises(ValueError, match="面板不存在"):
        await dashboard_runtime.load_dashboard_panel_or_raise("missing")

    monkeypatch.setattr(dashboard_routes, "generate_query_metadata", lambda _sql: asyncio.sleep(0, result={"title": "模型标题"}))
    assert await dashboard_routes._resolve_panel_name("select 1", "table", None) == "模型标题"
    monkeypatch.setattr(dashboard_routes, "generate_query_metadata", lambda _sql: asyncio.sleep(0, result={"title": "SQL查询"}))
    assert await dashboard_routes._resolve_panel_name("select 1", "table", None)

    async def metadata_raises(_sql):
        raise RuntimeError("llm down")

    monkeypatch.setattr(dashboard_routes, "generate_query_metadata", metadata_raises)
    assert await dashboard_routes._resolve_panel_name("select 1", "table", "") != ""


@pytest.mark.asyncio
async def test_dashboard_run_panel_and_batch_failure_statuses(dashboard_context, monkeypatch):
    service, runtime, _user = dashboard_context
    monkeypatch.setattr(dashboard_runtime, "create_dashboard_panel_execution", lambda **_kwargs: asyncio.sleep(0, result="exec_a"))
    result_calls = []

    async def success_result(**kwargs):
        panel = kwargs["panel"]
        result_calls.append(kwargs)
        return {
            "panel_id": panel["id"],
            "name": panel["name"],
            "chart_type": panel["chart_type"],
            "chart_config": {},
            "result": {
                "status": "succeeded",
                "results": [{
                    "member_datasource_id": kwargs.get("member_datasource_id") or "",
                    "member_name": "",
                    "status": "succeeded",
                    "columns": [],
                    "data": [],
                    "total_count": 0,
                }],
            },
        }

    monkeypatch.setattr(dashboard_runtime, "build_dashboard_panel_execution_result", success_result)
    await dashboard_runtime.run_dashboard_panel_task(
        "task_panel_ok",
        {"request_payload": json.dumps({"dashboard_id": "dash_a", "panel_id": "panel_a", "user_id": "u1", "operator_name": "用户一"})},
    )
    assert runtime.updates[-1]["status"] == "succeeded"
    assert runtime.updates[-1]["result_payload"]["result"]["results"][0]["member_datasource_id"] == ""
    assert result_calls[-1]["user_id"] == "u1"
    assert result_calls[-1]["operator_name"] == "用户一"
    assert result_calls[-1]["query_task_id"] == "task_panel_ok"

    service.dashboard["panels"][0]["member_datasource_id"] = "member_b"
    await dashboard_runtime.run_dashboard_panel_task(
        "task_panel_saved_member",
        {"request_payload": json.dumps({"dashboard_id": "dash_a", "panel_id": "panel_a", "user_id": "u1"})},
    )
    assert runtime.updates[-1]["status"] == "succeeded"
    assert runtime.updates[-1]["result_payload"]["result"]["results"][0]["member_datasource_id"] == "member_b"
    service.dashboard["panels"][0]["member_datasource_id"] = ""

    async def failed_result(**kwargs):
        panel = kwargs["panel"]
        return {"panel_id": panel["id"], "name": panel["name"], "chart_type": panel["chart_type"], "chart_config": {}, "result": {"status": "failed", "error": "boom", "results": []}}

    monkeypatch.setattr(dashboard_runtime, "build_dashboard_panel_execution_result", failed_result)
    await dashboard_runtime.run_dashboard_panel_task(
        "task_panel_failed",
        {"request_payload": json.dumps({"dashboard_id": "dash_a", "panel_id": "panel_a", "user_id": "u1"})},
    )
    assert runtime.updates[-1]["status"] == "failed"

    async def fake_item(panel_id, *_args, **_kwargs):
        if panel_id == "panel_a":
            return panel_id, {"status": "succeeded", "payload": {"panel_id": panel_id, "result": {"success": True}}}
        return panel_id, {"status": "failed", "error": "boom"}

    monkeypatch.setattr(dashboard_runtime, "execute_dashboard_panel_task_item", fake_item)
    await dashboard_runtime.run_dashboard_batch_task(
        "task_batch_partial",
        {"request_payload": json.dumps({"dashboard_id": "dash_a", "panel_ids": ["panel_a", "panel_b"], "user_id": "u1"})},
    )
    assert runtime.updates[-1]["status"] == "partial_success"

    async def fail_item(panel_id, *_args, **_kwargs):
        return panel_id, {"status": "failed", "error": "boom"}

    monkeypatch.setattr(dashboard_runtime, "execute_dashboard_panel_task_item", fail_item)
    await dashboard_runtime.run_dashboard_batch_task(
        "task_batch_failed",
        {"request_payload": json.dumps({"dashboard_id": "dash_a", "panel_ids": ["panel_a"], "user_id": "u1"})},
    )
    assert runtime.updates[-1]["status"] == "failed"


@pytest.mark.asyncio
async def test_dashboard_run_batch_task_statuses(dashboard_context, monkeypatch):
    service, runtime, _user = dashboard_context
    monkeypatch.setattr(dashboard_runtime, "create_dashboard_panel_execution", lambda **_kwargs: asyncio.sleep(0, result="exec_a"))

    await dashboard_runtime.run_dashboard_batch_task(
        "task_a",
        {"request_payload": json.dumps({"dashboard_id": "dash_a", "panel_ids": ["panel_a"], "user_id": "u1", "operator_name": "用户一"})},
    )
    assert runtime.updates[-1]["status"] == "succeeded"
    assert runtime.updates[-1]["result_payload"]["succeeded"] == 1

    service.dashboard["panels"] = []
    runtime.updates.clear()
    await dashboard_runtime.run_dashboard_batch_task("task_empty", {"request_payload": json.dumps({"dashboard_id": "dash_a", "panel_ids": [], "user_id": "u1"})})
    assert runtime.updates[-1]["progress_text"] == "无面板需要执行"

    runtime.cancelled = True
    runtime.updates.clear()
    await dashboard_runtime.run_dashboard_batch_task("task_cancelled", {"request_payload": "{}"})
    assert runtime.updates == []


@pytest.mark.asyncio
async def test_dashboard_run_batch_cancellation_after_running_and_during_loop(dashboard_context, monkeypatch):
    _service, runtime, _user = dashboard_context
    monkeypatch.setattr(dashboard_runtime, "create_dashboard_panel_execution", lambda **_kwargs: asyncio.sleep(0, result="exec_a"))

    statuses = iter([False, True])

    async def cancelled_after_running(_task_id):
        return next(statuses)

    runtime.is_query_task_cancelled = cancelled_after_running
    await dashboard_runtime.run_dashboard_batch_task(
        "task_cancel_after_running",
        {"request_payload": json.dumps({"dashboard_id": "dash_a", "panel_ids": ["panel_a"], "user_id": "u1"})},
    )
    assert runtime.updates[-1]["status"] == "running"

    runtime.updates.clear()
    release = asyncio.Event()

    async def slow_item(panel_id, *_args, **_kwargs):
        await release.wait()
        return panel_id, {"status": "succeeded", "payload": {"panel_id": panel_id, "result": {"success": True}}}

    cancel_checks = iter([False, False, False, True])

    async def cancelled_inside_loop(_task_id):
        return next(cancel_checks)

    runtime.is_query_task_cancelled = cancelled_inside_loop
    monkeypatch.setattr(dashboard_runtime, "execute_dashboard_panel_task_item", slow_item)
    task = asyncio.create_task(dashboard_runtime.run_dashboard_batch_task(
        "task_cancel_inside_loop",
        {"request_payload": json.dumps({"dashboard_id": "dash_a", "panel_ids": ["panel_a", "panel_b"], "user_id": "u1"})},
    ))
    await asyncio.sleep(0)
    release.set()
    await task
    assert runtime.updates[-1]["result_payload"]["completed"] == 1


@pytest.mark.asyncio
async def test_dashboard_route_crud_handlers(dashboard_context, monkeypatch):
    service, runtime, _user = dashboard_context
    request = object()

    listed = response_payload(await dashboard_routes.list_dashboards(request, scope="bad"))
    assert listed["data"]["total"] == 1
    created = response_payload(await dashboard_routes.create_dashboard(DashboardCreateRequest(name="新看板", datasource_id="ds_a"), request))
    assert created["data"]["can_manage"] is True
    detail = response_payload(await dashboard_routes.get_dashboard("dash_a", request))
    assert detail["data"]["can_manage"] is True
    missing_detail = response_payload(await dashboard_routes.get_dashboard("missing", request))
    assert missing_detail["code"] == ErrorCode.CHATDATA_NOT_FOUND
    updated = response_payload(await dashboard_routes.update_dashboard("dash_a", DashboardUpdateRequest(name="更新"), request))
    assert updated["data"]["name"] == "更新"
    missing_update = response_payload(await dashboard_routes.update_dashboard("missing", DashboardUpdateRequest(name="更新"), request))
    assert missing_update["code"] in {ErrorCode.CHATDATA_DASHBOARD_PERMISSION_DENIED, ErrorCode.CHATDATA_NOT_FOUND}
    published = response_payload(await dashboard_routes.publish_dashboard("dash_a", request))
    unpublished = response_payload(await dashboard_routes.unpublish_dashboard("dash_a", request))
    assert published["data"]["is_public"] is True
    assert unpublished["data"]["is_public"] is False
    deleted = response_payload(await dashboard_routes.delete_dashboard("dash_a", request))
    assert deleted["data"]["dashboard_id"] == "dash_a"

    added = response_payload(await dashboard_routes.add_panel("dash_a", PanelCreateRequest(sql="select 1", chart_type="table"), request))
    assert added["data"]["id"] == "panel_new"
    missing_added = response_payload(await dashboard_routes.add_panel("missing", PanelCreateRequest(sql="select 1"), request))
    assert missing_added["code"] == ErrorCode.CHATDATA_NOT_FOUND
    panel_updated = response_payload(await dashboard_routes.update_panel("dash_a", "panel_a", PanelUpdateRequest(name="p2"), request))
    assert panel_updated["data"]["id"] == "panel_a"
    missing_panel_update = response_payload(await dashboard_routes.update_panel("dash_a", "missing", PanelUpdateRequest(name="p2"), request))
    assert missing_panel_update["code"] == ErrorCode.CHATDATA_NOT_FOUND
    panel_deleted = response_payload(await dashboard_routes.delete_panel("dash_a", "panel_a", request))
    assert panel_deleted["data"]["panel_id"] == "panel_a"
    layout = response_payload(await dashboard_routes.update_layout("dash_a", LayoutUpdateRequest(layouts=[{"panel_id": "panel_a"}]), request))
    assert layout["data"]["updated"] == 1
    assert response_payload(await dashboard_routes.publish_dashboard("missing", request))["code"] == ErrorCode.CHATDATA_NOT_FOUND
    assert response_payload(await dashboard_routes.unpublish_dashboard("forbidden", request))["code"] == ErrorCode.CHATDATA_DASHBOARD_PERMISSION_DENIED
    assert response_payload(await dashboard_routes.delete_dashboard("missing", request))["code"] == ErrorCode.CHATDATA_NOT_FOUND
    assert response_payload(await dashboard_routes.delete_panel("dash_a", "missing", request))["code"] == ErrorCode.CHATDATA_NOT_FOUND
    assert response_payload(await dashboard_routes.update_layout("missing", LayoutUpdateRequest(layouts=[]), request))["code"] == ErrorCode.CHATDATA_NOT_FOUND

    monkeypatch.setattr(dashboard_routes, "generate_query_metadata", lambda _sql: asyncio.sleep(0, result={"title": "SQL查询"}))
    generated = response_payload(await dashboard_routes.generate_panel_name(PanelNameGenerateRequest(sql="select 1", chart_type="table", name="  显式标题  ")))
    assert generated["data"]["name"] == "显式标题"


@pytest.mark.asyncio
async def test_dashboard_from_spec_and_execute_routes(dashboard_context, monkeypatch):
    service, runtime, _user = dashboard_context
    request = object()

    assert response_payload(await dashboard_routes.create_dashboard_from_spec(DashboardFromSpecRequest(dashboard_spec={}, datasource_id=None), request))["code"] == ErrorCode.CHATDATA_VALIDATION_FAILED
    assert response_payload(await dashboard_routes.create_dashboard_from_spec(DashboardFromSpecRequest(dashboard_spec={"need_clarify": True}, datasource_id="ds"), request))["code"] == ErrorCode.CHATDATA_OPERATION_NOT_ALLOWED
    assert response_payload(await dashboard_routes.create_dashboard_from_spec(DashboardFromSpecRequest(dashboard_spec={"cards": []}, datasource_id="ds"), request))["code"] == ErrorCode.CHATDATA_DASHBOARD_CARD_REQUIRED
    assert response_payload(await dashboard_routes.create_dashboard_from_spec(DashboardFromSpecRequest(dashboard_spec={"cards": [{}]}, datasource_id="ds"), request))["code"] == ErrorCode.CHATDATA_SEMANTIC_CONFIG_REQUIRED

    async def fake_create_dashboard_from_spec_payload(**kwargs):
        return {"dashboard": {"id": "dash"}, "panels": [{"id": "p"}], "warnings": []}

    monkeypatch.setattr(dashboard_routes, "create_dashboard_from_spec_payload", fake_create_dashboard_from_spec_payload)
    spec_result = response_payload(await dashboard_routes.create_dashboard_from_spec(
        DashboardFromSpecRequest(dashboard_spec={"cards": [{}], "semantic_config_id": "cfg"}, datasource_id="ds"),
        request,
    ))
    assert spec_result["status"] == "success"

    async def empty_create_dashboard_from_spec_payload(**_kwargs):
        return {"dashboard": {"id": "dash"}, "panels": [], "warnings": ["skip card"]}

    monkeypatch.setattr(dashboard_routes, "create_dashboard_from_spec_payload", empty_create_dashboard_from_spec_payload)
    empty_spec = response_payload(await dashboard_routes.create_dashboard_from_spec(
        DashboardFromSpecRequest(dashboard_spec={"cards": [{}], "semantic_config_id": "cfg"}, datasource_id="ds"),
        request,
    ))
    assert empty_spec["code"] == ErrorCode.CHATDATA_DASHBOARD_NO_VALID_CARD

    async def raising_create_dashboard_from_spec_payload(**_kwargs):
        raise RuntimeError("spec failed")

    monkeypatch.setattr(dashboard_routes, "create_dashboard_from_spec_payload", raising_create_dashboard_from_spec_payload)
    failed_spec = response_payload(await dashboard_routes.create_dashboard_from_spec(
        DashboardFromSpecRequest(dashboard_spec={"cards": [{}], "semantic_config_id": "cfg"}, datasource_id="ds"),
        request,
    ))
    assert failed_spec["code"] == ErrorCode.CHATDATA_DASHBOARD_AI_CREATE_FAILED

    batch_task = response_payload(await dashboard_routes.create_dashboard_query_task("dash_a", request, PanelBatchExecuteRequest(panel_ids=["panel_a"])))
    assert batch_task["data"]["poll_interval_ms"] == 1500
    assert runtime.created_tasks[-1]["request_payload"]["panel_member_datasource_ids"] == {}

    service.dashboard["panels"][0]["member_datasource_id"] = "member_b"
    saved_member_task = response_payload(await dashboard_routes.create_dashboard_query_task("dash_a", request, PanelBatchExecuteRequest(panel_ids=["panel_a"])))
    assert saved_member_task["data"]["task_id"].startswith("task_")
    assert runtime.created_tasks[-1]["request_payload"]["panel_member_datasource_ids"] == {"panel_a": "member_b"}
    service.dashboard["panels"][0]["member_datasource_id"] = ""

    list_batch_task = response_payload(await dashboard_routes.create_dashboard_query_task("dash_a", request, ["panel_a"]))
    assert list_batch_task["data"]["task_id"].startswith("task_")
    missing_batch_task = response_payload(await dashboard_routes.create_dashboard_query_task("missing", request, PanelBatchExecuteRequest(panel_ids=["panel_a"])))
    assert missing_batch_task["code"] == ErrorCode.CHATDATA_NOT_FOUND


@pytest.mark.asyncio
async def test_dashboard_route_exception_branches(dashboard_context, monkeypatch):
    _service, _runtime, _user = dashboard_context
    request = object()

    class RaisingService(FakeDashboardService):
        async def list_dashboards(self, **_kwargs):
            raise RuntimeError("list failed")

        async def create_dashboard(self, **_kwargs):
            raise RuntimeError("create failed")

        async def get_dashboard(self, *_args, **_kwargs):
            raise RuntimeError("get failed")

        async def update_dashboard(self, **_kwargs):
            raise RuntimeError("update failed")

        async def delete_dashboard(self, *_args, **_kwargs):
            raise RuntimeError("delete failed")

        async def set_dashboard_public(self, *_args, **_kwargs):
            raise RuntimeError("publish failed")

        async def add_panel(self, **_kwargs):
            raise RuntimeError("add panel failed")

        async def update_panel(self, **_kwargs):
            raise RuntimeError("update panel failed")

        async def delete_panel(self, *_args, **_kwargs):
            raise RuntimeError("delete panel failed")

        async def update_panels_layout(self, *_args, **_kwargs):
            raise RuntimeError("layout failed")

    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: RaisingService())

    assert response_payload(await dashboard_routes.list_dashboards(request))["code"] == ErrorCode.CHATDATA_DASHBOARD_LIST_FAILED
    assert response_payload(await dashboard_routes.create_dashboard(DashboardCreateRequest(name="新看板"), request))["code"] == ErrorCode.CHATDATA_DASHBOARD_CREATE_FAILED
    assert response_payload(await dashboard_routes.get_dashboard("dash_a", request))["code"] == ErrorCode.CHATDATA_DASHBOARD_GET_FAILED
    assert response_payload(await dashboard_routes.update_dashboard("dash_a", DashboardUpdateRequest(name="更新"), request))["code"] == ErrorCode.CHATDATA_DASHBOARD_UPDATE_FAILED
    assert response_payload(await dashboard_routes.delete_dashboard("dash_a", request))["code"] == ErrorCode.CHATDATA_DASHBOARD_DELETE_FAILED
    assert response_payload(await dashboard_routes.publish_dashboard("dash_a", request))["code"] == ErrorCode.CHATDATA_DASHBOARD_PUBLISH_FAILED
    assert response_payload(await dashboard_routes.unpublish_dashboard("dash_a", request))["code"] == ErrorCode.CHATDATA_DASHBOARD_UNPUBLISH_FAILED
    assert response_payload(await dashboard_routes.add_panel("dash_a", PanelCreateRequest(sql="select 1"), request))["code"] == ErrorCode.CHATDATA_DASHBOARD_PANEL_LIST_FAILED
    assert response_payload(await dashboard_routes.update_panel("dash_a", "panel_a", PanelUpdateRequest(name="更新"), request))["code"] == ErrorCode.CHATDATA_DASHBOARD_PANEL_CREATE_FAILED
    assert response_payload(await dashboard_routes.delete_panel("dash_a", "panel_a", request))["code"] == ErrorCode.CHATDATA_DASHBOARD_PANEL_UPDATE_FAILED
    assert response_payload(await dashboard_routes.update_layout("dash_a", LayoutUpdateRequest(layouts=[]), request))["code"] == ErrorCode.CHATDATA_DASHBOARD_PANEL_DELETE_FAILED

    async def name_raises(*_args, **_kwargs):
        raise RuntimeError("name failed")

    monkeypatch.setattr(dashboard_routes, "_resolve_panel_name", name_raises)
    assert response_payload(await dashboard_routes.generate_panel_name(PanelNameGenerateRequest(sql="select 1")))["code"] == ErrorCode.CHATDATA_DASHBOARD_LAYOUT_FAILED


@pytest.mark.asyncio
async def test_dashboard_batch_route_immediate_error_branches(dashboard_context, monkeypatch):
    service, _runtime, _user = dashboard_context
    request = object()
    monkeypatch.setattr(dashboard_runtime, "create_dashboard_panel_execution", lambda **_kwargs: asyncio.sleep(0, result="exec_a"))

    missing = response_payload(await dashboard_routes.execute_panels_batch("missing", request))
    assert missing["code"] == ErrorCode.CHATDATA_NOT_FOUND

    empty = response_payload(await dashboard_routes.execute_panels_batch("empty", request))
    assert empty["data"]["results"] == {}

    list_request_response = await dashboard_routes.execute_panels_batch("dash_a", request, ["panel_a"])
    stream_text = await collect_streaming_response(list_request_response)
    assert "panel_a" in stream_text
    assert "[DONE]" in stream_text

    service.dashboard["panels"][0]["member_datasource_id"] = "member_b"
    saved_member_response = await dashboard_routes.execute_panels_batch("dash_a", request, PanelBatchExecuteRequest(panel_ids=["panel_a"]))
    saved_member_text = await collect_streaming_response(saved_member_response)
    assert '"member_datasource_id": "member_b"' in saved_member_text
    service.dashboard["panels"][0]["member_datasource_id"] = ""


@pytest.mark.asyncio
async def test_dashboard_batch_route_failed_and_partial_streams(dashboard_context, monkeypatch):
    service, runtime, _user = dashboard_context
    request = object()
    monkeypatch.setattr(dashboard_runtime, "create_dashboard_panel_execution", lambda **_kwargs: asyncio.sleep(0, result="exec_a"))

    service.dashboard["panels"][1]["datasource_id"] = "ds_a"
    service.dashboard["panels"][1]["sql"] = "fail sql"
    partial_response = await dashboard_routes.execute_panels_batch("dash_a", request, PanelBatchExecuteRequest(panel_ids=["panel_a", "panel_b"]))
    partial_text = await collect_streaming_response(partial_response)
    assert "panel_a" in partial_text
    assert "boom" in partial_text
    assert runtime.updates[-1]["status"] == "partial_success"

    service.dashboard["panels"][0]["sql"] = "fail sql"
    runtime.updates.clear()
    failed_response = await dashboard_routes.execute_panels_batch("dash_a", request, PanelBatchExecuteRequest(panel_ids=["panel_a", "panel_b"]))
    failed_text = await collect_streaming_response(failed_response)
    assert failed_text.count("boom") >= 2
    assert runtime.updates[-1]["status"] == "failed"


@pytest.mark.asyncio
async def test_dashboard_batch_route_missing_panel_and_cancel_streams(dashboard_context, monkeypatch):
    _service, runtime, _user = dashboard_context
    request = object()
    monkeypatch.setattr(dashboard_runtime, "create_dashboard_panel_execution", lambda **_kwargs: asyncio.sleep(0, result="exec_a"))

    async def empty_context(*_args, **_kwargs):
        return {}, {}

    monkeypatch.setattr(dashboard_runtime, "prepare_dashboard_batch_context", empty_context)
    missing_panel_response = await dashboard_routes.execute_panels_batch("dash_a", request, PanelBatchExecuteRequest(panel_ids=["missing"]))
    missing_text = await collect_streaming_response(missing_panel_response)
    assert "面板不存在" in missing_text
    assert runtime.updates[-1]["status"] == "failed"

    monkeypatch.undo()
    monkeypatch.setattr(dashboard_routes, "_current_user", lambda _request: _user)
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: _service)
    monkeypatch.setattr(dashboard_runtime, "_dashboard_runtime_value", runtime)
    monkeypatch.setattr(dashboard_runtime, "create_dashboard_panel_execution", lambda **_kwargs: asyncio.sleep(0, result="exec_a"))
    runtime.updates.clear()
    async def cancelled_after_wait(_task_id):
        return True

    runtime.is_query_task_cancelled = cancelled_after_wait
    cancelled_response = await dashboard_routes.execute_panels_batch("dash_a", request, PanelBatchExecuteRequest(panel_ids=["panel_a"]))
    cancelled_text = await collect_streaming_response(cancelled_response)
    assert "已人工取消" in cancelled_text
    assert "[DONE]" in cancelled_text


@pytest.mark.asyncio
async def test_dashboard_batch_route_generic_create_task_error(dashboard_context, monkeypatch):
    _service, runtime, _user = dashboard_context
    request = object()

    async def create_task_raises(**_kwargs):
        raise RuntimeError("task failed")

    runtime.create_task_record = create_task_raises
    created = response_payload(await dashboard_routes.create_dashboard_query_task("dash_a", request, PanelBatchExecuteRequest(panel_ids=["panel_a"])))
    assert created["code"] == ErrorCode.CHATDATA_DASHBOARD_EXECUTE_FAILED


@pytest.mark.asyncio
async def test_dashboard_batch_task_missing_dashboard_and_cancellation_branches(dashboard_context, monkeypatch):
    _service, runtime, _user = dashboard_context

    with pytest.raises(ValueError, match="大屏不存在"):
        await dashboard_runtime.run_dashboard_batch_task(
            "task_missing_dashboard",
            {"request_payload": json.dumps({"dashboard_id": "missing", "panel_ids": [], "user_id": "u1"})},
        )

    cancel_checks = iter([False, False, True])

    async def cancelled_before_await(_task_id):
        return next(cancel_checks)

    async def slow_item(panel_id, *_args, **_kwargs):
        await asyncio.Event().wait()
        return panel_id, {"status": "succeeded", "payload": {"panel_id": panel_id, "result": {"success": True}}}

    runtime.is_query_task_cancelled = cancelled_before_await
    monkeypatch.setattr(dashboard_runtime, "execute_dashboard_panel_task_item", slow_item)
    real_as_completed = dashboard_runtime.asyncio.as_completed
    monkeypatch.setattr(dashboard_runtime.asyncio, "as_completed", lambda _tasks: [object()])
    await dashboard_runtime.run_dashboard_batch_task(
        "task_cancel_before_await",
        {"request_payload": json.dumps({"dashboard_id": "dash_a", "panel_ids": ["panel_a", "panel_b"], "user_id": "u1"})},
    )
    assert runtime.updates[-1]["progress_text"] == "看板执行中"

    runtime.updates.clear()
    monkeypatch.setattr(dashboard_runtime.asyncio, "as_completed", real_as_completed)
    cancel_after_update_checks = iter([False, False, False, True])

    async def cancelled_after_first_result(_task_id):
        return next(cancel_after_update_checks)

    async def one_fast_one_slow(panel_id, *_args, **_kwargs):
        if panel_id == "panel_a":
            return panel_id, {"status": "succeeded", "payload": {"panel_id": panel_id, "result": {"success": True}}}
        await asyncio.Event().wait()
        return panel_id, {"status": "succeeded", "payload": {"panel_id": panel_id, "result": {"success": True}}}

    runtime.is_query_task_cancelled = cancelled_after_first_result
    monkeypatch.setattr(dashboard_runtime, "execute_dashboard_panel_task_item", one_fast_one_slow)
    await dashboard_runtime.run_dashboard_batch_task(
        "task_cancel_after_update",
        {"request_payload": json.dumps({"dashboard_id": "dash_a", "panel_ids": ["panel_a", "panel_b"], "user_id": "u1"})},
    )
    assert runtime.updates[-1]["result_payload"]["completed"] == 1

    runtime.updates.clear()
    final_cancel_checks = iter([False, False, False, False, True])

    async def cancelled_at_final(_task_id):
        return next(final_cancel_checks)

    async def fast_item(panel_id, *_args, **_kwargs):
        return panel_id, {"status": "succeeded", "payload": {"panel_id": panel_id, "result": {"success": True}}}

    runtime.is_query_task_cancelled = cancelled_at_final
    monkeypatch.setattr(dashboard_runtime, "execute_dashboard_panel_task_item", fast_item)
    await dashboard_runtime.run_dashboard_batch_task(
        "task_cancel_at_final",
        {"request_payload": json.dumps({"dashboard_id": "dash_a", "panel_ids": ["panel_a"], "user_id": "u1"})},
    )
    assert all(update.get("status") != "succeeded" for update in runtime.updates)


@pytest.mark.asyncio
async def test_dashboard_streaming_batch_cancellation_and_exception_branches(dashboard_context, monkeypatch):
    service, runtime, user = dashboard_context
    request = object()
    monkeypatch.setattr(dashboard_runtime, "create_dashboard_panel_execution", lambda **_kwargs: asyncio.sleep(0, result="exec_a"))

    async def fast_or_slow_result(**kwargs):
        panel = kwargs["panel"]
        if panel["id"] == "panel_b":
            await asyncio.Event().wait()
        return {"panel_id": panel["id"], "name": panel["name"], "chart_type": panel["chart_type"], "chart_config": {}, "result": {"success": True}}

    monkeypatch.setattr(dashboard_runtime, "build_dashboard_panel_execution_result", fast_or_slow_result)
    runtime.is_query_task_cancelled = lambda _task_id: asyncio.sleep(0, result=True)
    cancelled_before_process = await dashboard_routes.execute_panels_batch("dash_a", request, PanelBatchExecuteRequest(panel_ids=["panel_a", "panel_b"]))
    before_text = await collect_streaming_response(cancelled_before_process)
    assert "已人工取消" in before_text

    runtime.updates.clear()
    checks = iter([False, True])

    async def cancelled_after_process(_task_id):
        return next(checks)

    runtime.is_query_task_cancelled = cancelled_after_process
    after_process = await dashboard_routes.execute_panels_batch("dash_a", request, PanelBatchExecuteRequest(panel_ids=["panel_a", "panel_b"]))
    after_text = await collect_streaming_response(after_process)
    assert "panel_a" in after_text
    assert "已人工取消" in after_text

    runtime.updates.clear()
    final_checks = iter([False, False, True])

    async def cancelled_at_stream_final(_task_id):
        return next(final_checks)

    async def fast_result(**kwargs):
        panel = kwargs["panel"]
        return {"panel_id": panel["id"], "name": panel["name"], "chart_type": panel["chart_type"], "chart_config": {}, "result": {"success": True}}

    runtime.is_query_task_cancelled = cancelled_at_stream_final
    monkeypatch.setattr(dashboard_runtime, "build_dashboard_panel_execution_result", fast_result)
    final_response = await dashboard_routes.execute_panels_batch("dash_a", request, PanelBatchExecuteRequest(panel_ids=["panel_a"]))
    final_text = await collect_streaming_response(final_response)
    assert final_text.endswith("data: [DONE]\n\n")
    assert all(update.get("status") != "succeeded" for update in runtime.updates)

    class ResultRaisesTask:
        def __init__(self, coro):
            coro.close()

        def __hash__(self):
            return id(self)

        def result(self):
            raise RuntimeError("task result failed")

    monkeypatch.setattr(dashboard_runtime.asyncio, "create_task", lambda coro: ResultRaisesTask(coro))
    runtime.is_query_task_cancelled = lambda _task_id: asyncio.sleep(0, result=False)

    async def fake_wait(pending, return_when=None):
        return set(pending), set()

    monkeypatch.setattr(dashboard_runtime.asyncio, "wait", fake_wait)
    error_response = await dashboard_routes.execute_panels_batch("dash_a", request, PanelBatchExecuteRequest(panel_ids=["panel_a"]))
    error_text = await collect_streaming_response(error_response)
    assert "task result failed" in error_text


@pytest.mark.asyncio
async def test_dashboard_query_task_remaining_branches(dashboard_context, monkeypatch):
    service, runtime, _user = dashboard_context
    request = object()
    monkeypatch.setattr(dashboard_runtime, "create_dashboard_panel_execution", lambda **_kwargs: asyncio.sleep(0, result="exec_a"))

    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: service)
    runtime.is_query_task_cancelled = lambda _task_id: asyncio.sleep(0, result=False)
    batch_response = await dashboard_routes.execute_panels_batch("dash_a", request, None)
    assert "[DONE]" in await collect_streaming_response(batch_response)

    task_response = response_payload(await dashboard_routes.create_dashboard_query_task("dash_a", request, None))
    assert task_response["data"]["task_id"].startswith("task_")
