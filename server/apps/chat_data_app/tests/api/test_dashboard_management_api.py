import asyncio
import json
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
    PanelExecuteRequest,
    PanelNameGenerateRequest,
    PanelUpdateRequest,
)
from apps.chat_data_app.services import dashboard_runtime


@pytest.fixture(autouse=True)
def restore_dashboard_runtime():
    previous = dashboard_runtime._dashboard_runtime_value
    yield
    dashboard_runtime._dashboard_runtime_value = previous


@pytest.fixture(autouse=True)
def mirror_dashboard_service():
    previous = dashboard_runtime.get_dashboard_service
    dashboard_runtime.get_dashboard_service = lambda: dashboard_routes.get_dashboard_service()
    yield
    dashboard_runtime.get_dashboard_service = previous


def test_get_dashboard_detail_api_contract(client, auth_headers, fake_dashboard_service, monkeypatch):
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: fake_dashboard_service)

    response = client.get("/api/chatdata/dashboards/dash_a", headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["dashboard_id"] == "dash_a"
    assert payload["data"]["panels"][0]["panel_id"] == "panel_a"


def test_update_dashboard_api_contract(client, auth_headers, fake_dashboard_service, monkeypatch):
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: fake_dashboard_service)

    response = client.put(
        "/api/chatdata/dashboards/dash_a",
        json={"name": "经营看板V2", "description": "更新后", "theme": "dark", "background_id": "bg_a"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["name"] == "经营看板V2"
    assert fake_dashboard_service.updated_dashboard["user_id"] == "user_a"
    assert fake_dashboard_service.updated_dashboard["background_id"] == "bg_a"


def test_publish_and_unpublish_dashboard_api_contract(client, auth_headers, fake_dashboard_service, monkeypatch):
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: fake_dashboard_service)

    publish_response = client.post("/api/chatdata/dashboards/dash_a/publish", headers=auth_headers)
    unpublish_response = client.post("/api/chatdata/dashboards/dash_a/unpublish", headers=auth_headers)

    assert publish_response.status_code == 200
    assert publish_response.json()["data"]["is_public"] is True
    assert unpublish_response.status_code == 200
    assert unpublish_response.json()["data"]["is_public"] is False
    assert fake_dashboard_service.public_state is False


def test_update_panel_and_layout_api_contract(client, auth_headers, fake_dashboard_service, monkeypatch):
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: fake_dashboard_service)

    panel_response = client.put(
        "/api/chatdata/dashboards/dash_a/panels/panel_a",
        json={
            "name": "城市销售额V2",
            "sql": "select city, count(*) cnt from orders group by city",
            "chart_type": "line",
            "layout_w": 8,
            "layout_h": 5,
        },
        headers=auth_headers,
    )
    layout_response = client.put(
        "/api/chatdata/dashboards/dash_a/layout",
        json={"layouts": [{"panel_id": "panel_a", "x": 0, "y": 0, "w": 8, "h": 5}]},
        headers=auth_headers,
    )

    assert panel_response.status_code == 200
    assert panel_response.json()["data"]["name"] == "城市销售额V2"
    assert fake_dashboard_service.updated_panel["user_id"] == "user_a"
    assert layout_response.status_code == 200
    assert layout_response.json()["data"]["updated"] == 1
    assert fake_dashboard_service.updated_layouts == [{"panel_id": "panel_a", "x": 0, "y": 0, "w": 8, "h": 5}]


def test_delete_panel_and_dashboard_api_contract(client, auth_headers, fake_dashboard_service, monkeypatch):
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: fake_dashboard_service)

    panel_response = client.delete("/api/chatdata/dashboards/dash_a/panels/panel_a", headers=auth_headers)
    dashboard_response = client.delete("/api/chatdata/dashboards/dash_a", headers=auth_headers)

    assert panel_response.status_code == 200
    assert panel_response.json()["data"]["panel_id"] == "panel_a"
    assert fake_dashboard_service.deleted_panel_id == "panel_a"
    assert dashboard_response.status_code == 200
    assert dashboard_response.json()["data"]["dashboard_id"] == "dash_a"
    assert fake_dashboard_service.deleted_dashboard_id == "dash_a"


def test_dashboard_not_found_error_paths(client, auth_headers, fake_dashboard_service, monkeypatch):
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: fake_dashboard_service)

    async def fake_add_panel(**kwargs):
        return None if kwargs.get("dashboard_id") == "missing" else {"panel_id": "panel_new", **kwargs}

    async def fake_update_panels_layout(dashboard_id, layouts, user_id, operator_name=""):
        return None if dashboard_id == "missing" else len(layouts)

    fake_dashboard_service.add_panel = fake_add_panel
    fake_dashboard_service.update_panels_layout = fake_update_panels_layout

    responses = [
        client.get("/api/chatdata/dashboards/missing", headers=auth_headers),
        client.put("/api/chatdata/dashboards/missing", json={"name": "x"}, headers=auth_headers),
        client.delete("/api/chatdata/dashboards/missing", headers=auth_headers),
        client.post("/api/chatdata/dashboards/missing/publish", headers=auth_headers),
        client.post("/api/chatdata/dashboards/missing/unpublish", headers=auth_headers),
        client.post("/api/chatdata/dashboards/missing/panels", json={"name": "p", "sql": "select 1"}, headers=auth_headers),
        client.put("/api/chatdata/dashboards/dash_a/panels/missing", json={"name": "p"}, headers=auth_headers),
        client.delete("/api/chatdata/dashboards/dash_a/panels/missing", headers=auth_headers),
        client.put("/api/chatdata/dashboards/missing/layout", json={"layouts": []}, headers=auth_headers),
    ]

    for response in responses:
        payload = response.json()
        assert payload["status"] == "error"
        assert payload["code"] == ErrorCode.CHATDATA_NOT_FOUND


def test_generate_panel_name_prefers_provided_name_and_fallbacks(client, monkeypatch):
    monkeypatch.setattr(dashboard_routes, "generate_query_metadata", lambda sql: (_ for _ in ()).throw(RuntimeError("llm failed")))
    monkeypatch.setattr(dashboard_routes, "_get_timestamp", lambda: "unused", raising=False)

    explicit = client.post("/api/chatdata/panel-name-generate", json={"name": "  明确标题  ", "sql": "select 1", "chart_type": "bar"})
    fallback = client.post("/api/chatdata/panel-name-generate", json={"name": "", "sql": "select 1", "chart_type": "bar"})

    assert explicit.json()["data"]["name"] == "明确标题"
    assert "柱状图_" in fallback.json()["data"]["name"]


def test_create_dashboard_from_spec_validates_required_payload(client, auth_headers):
    missing_ds = client.post("/api/chatdata/dashboards/from-spec", json={"dashboard_spec": {"cards": [{"title": "a"}]}}, headers=auth_headers)
    clarify = client.post("/api/chatdata/dashboards/from-spec", json={"datasource_id": "ds_a", "dashboard_spec": {"need_clarify": True}}, headers=auth_headers)
    no_cards = client.post("/api/chatdata/dashboards/from-spec", json={"datasource_id": "ds_a", "dashboard_spec": {"cards": []}}, headers=auth_headers)
    missing_semantic = client.post("/api/chatdata/dashboards/from-spec", json={"datasource_id": "ds_a", "dashboard_spec": {"cards": [{"title": "a"}]}}, headers=auth_headers)

    assert missing_ds.json()["code"] == ErrorCode.CHATDATA_VALIDATION_FAILED
    assert clarify.json()["code"] == ErrorCode.CHATDATA_OPERATION_NOT_ALLOWED
    assert no_cards.json()["code"] == ErrorCode.CHATDATA_DASHBOARD_CARD_REQUIRED
    assert missing_semantic.json()["code"] == ErrorCode.CHATDATA_SEMANTIC_CONFIG_REQUIRED


def _payload(response):
    return json.loads(response.body.decode("utf-8"))


async def _collect_stream(response):
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)
    return chunks


def _patch_dashboard_user(monkeypatch):
    monkeypatch.setattr(dashboard_routes, "_current_user", lambda _request: SimpleNamespace(user_id="user_a", username="测试用户"))


def _patch_dashboard_execution_service_shape(fake_dashboard_service):
    def normalize_panel(panel):
        result = dict(panel)
        result.setdefault("id", result.get("panel_id"))
        result.setdefault("panel_id", result.get("id"))
        result.setdefault("chart_config", {})
        result.setdefault("datasource_id", "ds_a")
        return result

    async def get_panel(panel_id, dashboard_id=None, user_id=None, include_public=True):
        for panel in fake_dashboard_service.dashboard.get("panels") or []:
            if panel.get("panel_id") == panel_id or panel.get("id") == panel_id:
                if dashboard_id and panel.get("dashboard_id") != dashboard_id:
                    return None
                return normalize_panel(panel)
        return None

    async def get_dashboard(dashboard_id, user_id=None, include_public=True):
        if dashboard_id != fake_dashboard_service.dashboard.get("dashboard_id"):
            return None
        dashboard = dict(fake_dashboard_service.dashboard)
        dashboard.setdefault("id", dashboard.get("dashboard_id"))
        dashboard.setdefault("datasource_id", "ds_a")
        dashboard["panels"] = [normalize_panel(panel) for panel in dashboard.get("panels") or []]
        return dashboard

    fake_dashboard_service.get_panel = get_panel
    fake_dashboard_service.get_dashboard = get_dashboard


class FakeDashboardDatasourceManager:
    async def get_execution_datasources_async(self, datasource_id, member_datasource_ids=None):
        selected = member_datasource_ids or ["member_a"]
        return [
            {
                "member_datasource_id": selected[0],
                "member_name": "成员A",
                "source_type": "mysql",
                "query_executor_type": "direct",
            }
        ]

    async def get_authorized_execution_datasources_async(
        self,
        datasource_id,
        member_datasource_ids=None,
        user_id=None,
        username=None,
    ):
        return await self.get_execution_datasources_async(datasource_id, member_datasource_ids)


class FakeDashboardRuntime:
    def __init__(self, fail_members=None, cancelled_after=999):
        self.manager = FakeDashboardDatasourceManager()
        self.fail_members = set(fail_members or [])
        self.cancelled_after = cancelled_after
        self.cancel_checks = 0
        self.created_tasks = []
        self.updated_tasks = []
        self.scheduled = []

    def get_datasource_manager(self):
        return self.manager

    async def execute_sql_for_member(self, execution_target, sql, **kwargs):
        member_id = execution_target.get("member_datasource_id")
        if member_id in self.fail_members:
            return {
                "member_datasource_id": member_id,
                "member_name": execution_target.get("member_name"),
                "sql": sql,
                "success": False,
                "columns": [],
                "data": [],
                "total_count": 0,
                "error": "执行失败",
            }
        return {
            "member_datasource_id": member_id,
            "member_name": execution_target.get("member_name"),
            "sql": sql,
            "success": True,
            "columns": ["id"],
            "data": [{"id": 1}],
            "total_count": 1,
            "execution_time_ms": 3,
        }

    async def create_task_record(self, **kwargs):
        task_id = f"task_{len(self.created_tasks) + 1}"
        task = {"task_id": task_id, "task_type": kwargs.get("task_type"), "status": "pending", **kwargs}
        self.created_tasks.append(task)
        return task

    async def update_query_task(self, task_id, **kwargs):
        self.updated_tasks.append((task_id, kwargs))
        return {"task_id": task_id, **kwargs}

    async def is_query_task_cancelled(self, task_id):
        self.cancel_checks += 1
        return self.cancel_checks >= self.cancelled_after

    def schedule_query_task(self, task_id):
        self.scheduled.append(task_id)

    @property
    def query_task_poll_interval_ms(self):
        return 1500


def test_list_and_create_dashboard_direct_routes(fake_dashboard_service, monkeypatch):
    _patch_dashboard_user(monkeypatch)
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: fake_dashboard_service)

    mine = _payload(asyncio.run(dashboard_routes.list_dashboards(SimpleNamespace(), scope="bad")))
    public = _payload(asyncio.run(dashboard_routes.list_dashboards(SimpleNamespace(), scope="public")))
    created = _payload(asyncio.run(dashboard_routes.create_dashboard(
        DashboardCreateRequest(name="新看板", description="描述", datasource_id="ds_a", theme="dark"),
        SimpleNamespace(),
    )))

    assert mine["data"]["dashboards"][0]["scope"] == "mine"
    assert public["data"]["dashboards"][0]["scope"] == "public"
    assert created["data"]["dashboard_id"] == "dash_new"
    assert fake_dashboard_service.created_dashboard["user_id"] == "user_a"


def test_add_panel_truncates_or_fallbacks_name(fake_dashboard_service, monkeypatch):
    _patch_dashboard_user(monkeypatch)
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: fake_dashboard_service)
    monkeypatch.setattr(dashboard_routes, "_build_panel_name_fallback", lambda chart_type, max_length=24: "兜底标题")

    long_name = "很长的标题" * 20
    named = _payload(asyncio.run(dashboard_routes.add_panel(
        "dash_a",
        PanelCreateRequest(name=long_name, sql="select 1", chart_type="table"),
        SimpleNamespace(),
    )))
    fallback = _payload(asyncio.run(dashboard_routes.add_panel(
        "dash_a",
        PanelCreateRequest(name="", sql="select 1", chart_type="bar"),
        SimpleNamespace(),
    )))

    assert named["data"]["name"] == long_name[:24]
    assert fallback["data"]["name"] == "兜底标题"


def test_execute_panel_success_and_failure(fake_dashboard_service, monkeypatch):
    _patch_dashboard_user(monkeypatch)
    _patch_dashboard_execution_service_shape(fake_dashboard_service)
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: fake_dashboard_service)
    monkeypatch.setattr(dashboard_runtime, "create_dashboard_panel_execution", lambda **_kwargs: asyncio.sleep(0, result="exec_1"))
    runtime = FakeDashboardRuntime()
    dashboard_runtime.configure_dashboard_runtime(dashboard_runtime.DashboardRuntime(
        get_datasource_manager=runtime.get_datasource_manager,
        execute_sql_for_member=runtime.execute_sql_for_member,
        create_task_record=runtime.create_task_record,
        update_query_task=runtime.update_query_task,
        is_query_task_cancelled=runtime.is_query_task_cancelled,
        schedule_query_task=runtime.schedule_query_task,
        query_task_poll_interval_ms=runtime.query_task_poll_interval_ms,
    ))

    success = _payload(asyncio.run(dashboard_routes.execute_panel(
        "dash_a",
        "panel_a",
        SimpleNamespace(),
        PanelExecuteRequest(member_datasource_id="member_a"),
    )))
    runtime.fail_members.add("member_fail")
    failure = _payload(asyncio.run(dashboard_routes.execute_panel(
        "dash_a",
        "panel_a",
        SimpleNamespace(),
        PanelExecuteRequest(member_datasource_id="member_fail"),
    )))

    assert success["status"] == "success"
    assert success["data"]["result"]["status"] == "succeeded"
    assert success["data"]["result"]["results"][0]["status"] == "succeeded"
    assert failure["code"] == ErrorCode.CHATDATA_DASHBOARD_EXECUTE_FAILED
    assert any(update[1].get("status") == "failed" for update in runtime.updated_tasks)


def test_execute_panel_without_datasource_uses_tool_direct(monkeypatch):
    _patch_dashboard_user(monkeypatch)

    class NoDatasourceDashboardService:
        async def get_panel(self, panel_id, dashboard_id=None, user_id=None, include_public=True):
            return {"id": panel_id, "name": "SQL面板", "chart_type": "table", "chart_config": {}, "sql": "select 1", "datasource_id": ""}

    async def fake_execute_sql_direct_runner(sql, _datasource_id):
        return {"success": True, "columns": ["x"], "data": [{"x": 1}], "total_count": 1}

    runtime = FakeDashboardRuntime()
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: NoDatasourceDashboardService())
    monkeypatch.setattr(dashboard_runtime, "execute_sql_direct_runner", fake_execute_sql_direct_runner)
    monkeypatch.setattr(dashboard_runtime, "create_dashboard_panel_execution", lambda **_kwargs: asyncio.sleep(0, result=None))
    dashboard_runtime.configure_dashboard_runtime(dashboard_runtime.DashboardRuntime(
        get_datasource_manager=runtime.get_datasource_manager,
        execute_sql_for_member=runtime.execute_sql_for_member,
        create_task_record=runtime.create_task_record,
        update_query_task=runtime.update_query_task,
        is_query_task_cancelled=runtime.is_query_task_cancelled,
        schedule_query_task=runtime.schedule_query_task,
        query_task_poll_interval_ms=runtime.query_task_poll_interval_ms,
    ))

    payload = _payload(asyncio.run(dashboard_routes.execute_panel("dash_a", "panel_a", SimpleNamespace(), None)))

    assert payload["status"] == "success"
    assert payload["data"]["result"]["results"][0]["columns"] == ["x"]


def test_create_dashboard_query_task_and_panel_alias(fake_dashboard_service, monkeypatch):
    _patch_dashboard_user(monkeypatch)
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: fake_dashboard_service)
    runtime = FakeDashboardRuntime()
    dashboard_runtime.configure_dashboard_runtime(dashboard_runtime.DashboardRuntime(
        get_datasource_manager=runtime.get_datasource_manager,
        execute_sql_for_member=runtime.execute_sql_for_member,
        create_task_record=runtime.create_task_record,
        update_query_task=runtime.update_query_task,
        is_query_task_cancelled=runtime.is_query_task_cancelled,
        schedule_query_task=runtime.schedule_query_task,
        query_task_poll_interval_ms=runtime.query_task_poll_interval_ms,
    ))

    task_payload = _payload(asyncio.run(dashboard_routes.create_dashboard_query_task(
        "dash_a",
        SimpleNamespace(),
        PanelBatchExecuteRequest(panel_ids=["panel_a"], panel_member_datasource_ids={"panel_a": "member_a"}),
    )))
    alias_payload = _payload(asyncio.run(dashboard_routes.create_panel_query_task(
        "dash_a",
        "panel_a",
        SimpleNamespace(),
        PanelExecuteRequest(member_datasource_id="member_a"),
    )))

    assert task_payload["data"]["task_id"] == "task_1"
    assert alias_payload["data"]["task_id"] == "task_2"
    assert runtime.scheduled == ["task_1", "task_2"]


def test_execute_panels_batch_streams_success_and_failure(fake_dashboard_service, monkeypatch):
    _patch_dashboard_user(monkeypatch)
    _patch_dashboard_execution_service_shape(fake_dashboard_service)
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: fake_dashboard_service)
    monkeypatch.setattr(dashboard_runtime, "create_dashboard_panel_execution", lambda **_kwargs: asyncio.sleep(0, result="exec_1"))
    runtime = FakeDashboardRuntime(fail_members={"member_fail"})
    dashboard_runtime.configure_dashboard_runtime(dashboard_runtime.DashboardRuntime(
        get_datasource_manager=runtime.get_datasource_manager,
        execute_sql_for_member=runtime.execute_sql_for_member,
        create_task_record=runtime.create_task_record,
        update_query_task=runtime.update_query_task,
        is_query_task_cancelled=runtime.is_query_task_cancelled,
        schedule_query_task=runtime.schedule_query_task,
        query_task_poll_interval_ms=runtime.query_task_poll_interval_ms,
    ))

    response = asyncio.run(dashboard_routes.execute_panels_batch(
        "dash_a",
        SimpleNamespace(),
        PanelBatchExecuteRequest(panel_ids=["panel_a"], panel_member_datasource_ids={"panel_a": "member_fail"}),
    ))
    chunks = asyncio.run(_collect_stream(response))
    content = "".join(chunks)

    assert "执行失败" in content
    assert "data: [DONE]" in content
    assert any(update[1].get("status") == "failed" for update in runtime.updated_tasks)


def test_execute_panels_batch_returns_empty_for_dashboard_without_panels(monkeypatch):
    _patch_dashboard_user(monkeypatch)

    class EmptyDashboardService:
        async def get_dashboard(self, dashboard_id, user_id=None, include_public=True):
            return {"id": dashboard_id, "datasource_id": "ds_a", "panels": []}

    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: EmptyDashboardService())

    payload = _payload(asyncio.run(dashboard_routes.execute_panels_batch("dash_empty", SimpleNamespace(), None)))

    assert payload["status"] == "success"
    assert payload["msg"] == "无面板需要执行"


def test_dashboard_background_tasks_update_status(fake_dashboard_service, monkeypatch):
    _patch_dashboard_user(monkeypatch)
    _patch_dashboard_execution_service_shape(fake_dashboard_service)
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: fake_dashboard_service)
    monkeypatch.setattr(dashboard_runtime, "create_dashboard_panel_execution", lambda **_kwargs: asyncio.sleep(0, result="exec_1"))
    runtime = FakeDashboardRuntime()
    dashboard_runtime.configure_dashboard_runtime(dashboard_runtime.DashboardRuntime(
        get_datasource_manager=runtime.get_datasource_manager,
        execute_sql_for_member=runtime.execute_sql_for_member,
        create_task_record=runtime.create_task_record,
        update_query_task=runtime.update_query_task,
        is_query_task_cancelled=runtime.is_query_task_cancelled,
        schedule_query_task=runtime.schedule_query_task,
        query_task_poll_interval_ms=runtime.query_task_poll_interval_ms,
    ))

    asyncio.run(dashboard_runtime.run_dashboard_panel_task("task_panel", {
        "request_payload": json.dumps({"dashboard_id": "dash_a", "panel_id": "panel_a", "member_datasource_id": "member_a", "user_id": "user_a"}),
    }))
    asyncio.run(dashboard_runtime.run_dashboard_batch_task("task_batch", {
        "request_payload": json.dumps({"dashboard_id": "dash_a", "panel_ids": ["panel_a"], "panel_member_datasource_ids": {"panel_a": "member_a"}, "user_id": "user_a", "operator_name": "测试用户"}),
    }))

    assert any(task_id == "task_panel" and kwargs.get("status") == "succeeded" for task_id, kwargs in runtime.updated_tasks)
    assert any(task_id == "task_batch" and kwargs.get("status") == "succeeded" for task_id, kwargs in runtime.updated_tasks)


def test_dashboard_runtime_and_helper_error_branches(monkeypatch):
    previous = dashboard_runtime._dashboard_runtime_value
    dashboard_runtime._dashboard_runtime_value = None
    try:
        try:
            dashboard_runtime.dashboard_runtime()
        except RuntimeError as exc:
            assert "runtime" in str(exc)
        else:
            raise AssertionError("runtime must be configured")
    finally:
        dashboard_runtime._dashboard_runtime_value = previous

    assert dashboard_runtime.build_dashboard_panel_sql_preview("select " + "x" * 200, 20).endswith("...")
    assert dashboard_runtime.build_dashboard_execution_target_cache_key(" ds ", " member ") == ("ds", "member")

    try:
        dashboard_runtime.raise_if_dashboard_panel_execution_failed({"panel_id": "p1", "result": {"success": False, "error": "失败"}})
    except RuntimeError as exc:
        assert str(exc) == "失败"
    else:
        raise AssertionError("failed panel result should raise")


def test_create_dashboard_panel_execution_persists_record(monkeypatch):
    added = []
    committed = {"value": False}

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def add(self, item):
            added.append(item)

        async def commit(self):
            committed["value"] = True

    monkeypatch.setattr(dashboard_runtime, "get_session", lambda: FakeSession())

    assert asyncio.run(dashboard_runtime.create_dashboard_panel_execution(
        task_id="",
        panel={"datasource_id": "ds_a", "sql": "select 1"},
        execution_target={},
        user_id="user_a",
        operator_name="测试用户",
    )) is None

    execution_id = asyncio.run(dashboard_runtime.create_dashboard_panel_execution(
        task_id="task_a",
        panel={"datasource_id": "ds_a", "sql": "select 1"},
        execution_target={"member_datasource_id": "member_a", "member_name": "成员A", "query_executor_type": "http"},
        user_id="user_a",
        operator_name="测试用户",
    ))

    assert len(execution_id) == 32
    assert committed["value"] is True
    assert added[0].task_id == "task_a"
    assert added[0].member_datasource_id == "member_a"
    assert added[0].executor_type == "http"


def test_prepare_dashboard_batch_context_errors_and_cache(fake_dashboard_service, monkeypatch):
    _patch_dashboard_execution_service_shape(fake_dashboard_service)
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: fake_dashboard_service)
    runtime = FakeDashboardRuntime()
    dashboard_runtime.configure_dashboard_runtime(dashboard_runtime.DashboardRuntime(
        get_datasource_manager=runtime.get_datasource_manager,
        execute_sql_for_member=runtime.execute_sql_for_member,
        create_task_record=runtime.create_task_record,
        update_query_task=runtime.update_query_task,
        is_query_task_cancelled=runtime.is_query_task_cancelled,
        schedule_query_task=runtime.schedule_query_task,
        query_task_poll_interval_ms=runtime.query_task_poll_interval_ms,
    ))

    panel_map, target_map = asyncio.run(dashboard_runtime.prepare_dashboard_batch_context(
        "dash_a",
        ["panel_a"],
        {"panel_a": "member_a"},
        user_id="user_a",
    ))

    assert panel_map["panel_a"]["datasource_id"] == "ds_a"
    assert target_map[("ds_a", "member_a")]["member_datasource_id"] == "member_a"
    with pytest.raises(ValueError, match="大屏不存在"):
        asyncio.run(dashboard_runtime.prepare_dashboard_batch_context("missing", ["panel_a"], {}, user_id="user_a"))
    with pytest.raises(ValueError, match="面板不存在"):
        asyncio.run(dashboard_runtime.prepare_dashboard_batch_context("dash_a", ["missing"], {}, user_id="user_a"))


def test_execute_dashboard_panel_task_item_success_and_failure(monkeypatch):
    panel = {"id": "panel_a", "name": "面板A", "chart_type": "bar", "chart_config": {}, "datasource_id": "ds_a", "sql": "select 1"}
    runtime = FakeDashboardRuntime(fail_members={"member_fail"})
    dashboard_runtime.configure_dashboard_runtime(dashboard_runtime.DashboardRuntime(
        get_datasource_manager=runtime.get_datasource_manager,
        execute_sql_for_member=runtime.execute_sql_for_member,
        create_task_record=runtime.create_task_record,
        update_query_task=runtime.update_query_task,
        is_query_task_cancelled=runtime.is_query_task_cancelled,
        schedule_query_task=runtime.schedule_query_task,
        query_task_poll_interval_ms=runtime.query_task_poll_interval_ms,
    ))
    monkeypatch.setattr(dashboard_runtime, "create_dashboard_panel_execution", lambda **_kwargs: asyncio.sleep(0, result="exec_a"))

    success_panel_id, success_entry = asyncio.run(dashboard_runtime.execute_dashboard_panel_task_item(
        "panel_a",
        "member_a",
        asyncio.Semaphore(1),
        panel=panel,
        execution_target={"member_datasource_id": "member_a", "member_name": "成员A", "source_type": "mysql"},
        query_task_id="task_a",
        user_id="user_a",
        operator_name="测试用户",
    ))
    fail_panel_id, fail_entry = asyncio.run(dashboard_runtime.execute_dashboard_panel_task_item(
        "panel_a",
        "member_fail",
        panel=panel,
        execution_target={"member_datasource_id": "member_fail", "member_name": "失败成员", "source_type": "mysql"},
        query_task_id="task_a",
        user_id="user_a",
        operator_name="测试用户",
    ))

    assert success_panel_id == "panel_a"
    assert success_entry["status"] == "succeeded"
    assert fail_panel_id == "panel_a"
    assert fail_entry["status"] == "failed"


def test_run_dashboard_batch_task_cancel_empty_and_partial(monkeypatch):
    _patch_dashboard_user(monkeypatch)

    class BatchDashboardService:
        def __init__(self, panels):
            self.dashboard = {"id": "dash_a", "dashboard_id": "dash_a", "datasource_id": "ds_a", "panels": panels}

        async def get_dashboard(self, dashboard_id, user_id=None, include_public=True):
            return self.dashboard if dashboard_id == "dash_a" else None

    def configure(runtime):
        dashboard_runtime.configure_dashboard_runtime(dashboard_runtime.DashboardRuntime(
            get_datasource_manager=runtime.get_datasource_manager,
            execute_sql_for_member=runtime.execute_sql_for_member,
            create_task_record=runtime.create_task_record,
            update_query_task=runtime.update_query_task,
            is_query_task_cancelled=runtime.is_query_task_cancelled,
            schedule_query_task=runtime.schedule_query_task,
            query_task_poll_interval_ms=runtime.query_task_poll_interval_ms,
        ))

    cancelled_runtime = FakeDashboardRuntime(cancelled_after=1)
    configure(cancelled_runtime)
    asyncio.run(dashboard_runtime.run_dashboard_batch_task("task_cancel", {"request_payload": "{}"}))
    assert cancelled_runtime.updated_tasks == []

    early_cancel_runtime = FakeDashboardRuntime(cancelled_after=2)
    configure(early_cancel_runtime)
    asyncio.run(dashboard_runtime.run_dashboard_batch_task("task_cancel_after_start", {"request_payload": "{}"}))
    assert any(update[1].get("status") == "running" for update in early_cancel_runtime.updated_tasks)

    empty_runtime = FakeDashboardRuntime()
    configure(empty_runtime)
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: BatchDashboardService([]))
    asyncio.run(dashboard_runtime.run_dashboard_batch_task("task_empty", {
        "request_payload": json.dumps({"dashboard_id": "dash_a", "user_id": "user_a"}),
    }))
    assert any(update[1].get("progress_text") == "无面板需要执行" for update in empty_runtime.updated_tasks)

    panels = [
        {"id": "panel_a", "name": "面板A", "chart_type": "bar", "chart_config": {}, "datasource_id": "ds_a", "sql": "select 1"},
        {"id": "panel_b", "name": "面板B", "chart_type": "bar", "chart_config": {}, "datasource_id": "ds_a", "sql": "select 2"},
    ]
    partial_runtime = FakeDashboardRuntime(fail_members={"member_fail"})
    configure(partial_runtime)
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: BatchDashboardService(panels))
    monkeypatch.setattr(dashboard_runtime, "create_dashboard_panel_execution", lambda **_kwargs: asyncio.sleep(0, result="exec_a"))
    asyncio.run(dashboard_runtime.run_dashboard_batch_task("task_partial", {
        "request_payload": json.dumps({
            "dashboard_id": "dash_a",
            "panel_ids": ["panel_a", "panel_b"],
            "panel_member_datasource_ids": {"panel_a": "member_a", "panel_b": "member_fail"},
            "user_id": "user_a",
            "operator_name": "测试用户",
        }),
    }))

    assert any(update[1].get("status") == "partial_success" for update in partial_runtime.updated_tasks)


def test_dashboard_route_exception_branches(monkeypatch):
    _patch_dashboard_user(monkeypatch)

    class RaisingDashboardService:
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

    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: RaisingDashboardService())

    responses = [
        _payload(asyncio.run(dashboard_routes.list_dashboards(SimpleNamespace()))),
        _payload(asyncio.run(dashboard_routes.create_dashboard(DashboardCreateRequest(name="x"), SimpleNamespace()))),
        _payload(asyncio.run(dashboard_routes.get_dashboard("dash_a", SimpleNamespace()))),
        _payload(asyncio.run(dashboard_routes.update_dashboard("dash_a", DashboardUpdateRequest(name="x"), SimpleNamespace()))),
        _payload(asyncio.run(dashboard_routes.delete_dashboard("dash_a", SimpleNamespace()))),
        _payload(asyncio.run(dashboard_routes.publish_dashboard("dash_a", SimpleNamespace()))),
        _payload(asyncio.run(dashboard_routes.unpublish_dashboard("dash_a", SimpleNamespace()))),
        _payload(asyncio.run(dashboard_routes.add_panel("dash_a", PanelCreateRequest(sql="select 1"), SimpleNamespace()))),
        _payload(asyncio.run(dashboard_routes.update_panel("dash_a", "panel_a", PanelUpdateRequest(name="x"), SimpleNamespace()))),
        _payload(asyncio.run(dashboard_routes.delete_panel("dash_a", "panel_a", SimpleNamespace()))),
        _payload(asyncio.run(dashboard_routes.update_layout("dash_a", LayoutUpdateRequest(layouts=[]), SimpleNamespace()))),
    ]

    assert [item["code"] for item in responses] == [ErrorCode.CHATDATA_DASHBOARD_LIST_FAILED, ErrorCode.CHATDATA_DASHBOARD_CREATE_FAILED, ErrorCode.CHATDATA_DASHBOARD_GET_FAILED, ErrorCode.CHATDATA_DASHBOARD_UPDATE_FAILED, ErrorCode.CHATDATA_DASHBOARD_DELETE_FAILED, ErrorCode.CHATDATA_DASHBOARD_PUBLISH_FAILED, ErrorCode.CHATDATA_DASHBOARD_UNPUBLISH_FAILED, ErrorCode.CHATDATA_DASHBOARD_PANEL_LIST_FAILED, ErrorCode.CHATDATA_DASHBOARD_PANEL_CREATE_FAILED, ErrorCode.CHATDATA_DASHBOARD_PANEL_UPDATE_FAILED, ErrorCode.CHATDATA_DASHBOARD_PANEL_DELETE_FAILED]


def test_panel_name_and_create_from_spec_error_branches(monkeypatch):
    _patch_dashboard_user(monkeypatch)

    async def fake_generate_query_metadata(_sql):
        return {"title": "历史销售趋势分析标题超长超长超长超长"}

    monkeypatch.setattr(dashboard_routes, "generate_query_metadata", fake_generate_query_metadata)
    name_payload = _payload(asyncio.run(dashboard_routes.generate_panel_name(PanelNameGenerateRequest(sql="select 1", chart_type="line", name=""))))
    assert name_payload["data"]["name"] == "历史销售趋势分析标题超长超长超长超长"[:24]

    async def fake_resolve_panel_name(**_kwargs):
        raise RuntimeError("name failed")

    monkeypatch.setattr(dashboard_routes, "_resolve_panel_name", fake_resolve_panel_name)
    name_error = _payload(asyncio.run(dashboard_routes.generate_panel_name(PanelNameGenerateRequest(sql="select 1", chart_type="line", name=""))))
    assert name_error["code"] == ErrorCode.CHATDATA_DASHBOARD_LAYOUT_FAILED

    async def fake_create_no_panels(**_kwargs):
        return {"panels": [], "warnings": ["bad card"]}

    monkeypatch.setattr(dashboard_routes, "create_dashboard_from_spec_payload", fake_create_no_panels)
    no_panels = _payload(asyncio.run(dashboard_routes.create_dashboard_from_spec(
        DashboardFromSpecRequest(datasource_id="ds_a", semantic_config_id="sem_1", dashboard_spec={"cards": [{"title": "卡片"}]}),
        SimpleNamespace(),
    )))
    assert no_panels["code"] == ErrorCode.CHATDATA_DASHBOARD_NO_VALID_CARD

    async def fake_create_raises(**_kwargs):
        raise RuntimeError("spec failed")

    monkeypatch.setattr(dashboard_routes, "create_dashboard_from_spec_payload", fake_create_raises)
    create_error = _payload(asyncio.run(dashboard_routes.create_dashboard_from_spec(
        DashboardFromSpecRequest(datasource_id="ds_a", dashboard_spec={"semantic_config_id": "sem_1", "cards": [{"title": "卡片"}]}),
        SimpleNamespace(),
    )))
    assert create_error["code"] == ErrorCode.CHATDATA_DASHBOARD_AI_CREATE_FAILED


def test_execute_panel_value_and_generic_error_branches(fake_dashboard_service, monkeypatch):
    _patch_dashboard_user(monkeypatch)
    _patch_dashboard_execution_service_shape(fake_dashboard_service)
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: fake_dashboard_service)
    runtime = FakeDashboardRuntime()
    dashboard_runtime.configure_dashboard_runtime(dashboard_runtime.DashboardRuntime(
        get_datasource_manager=runtime.get_datasource_manager,
        execute_sql_for_member=runtime.execute_sql_for_member,
        create_task_record=runtime.create_task_record,
        update_query_task=runtime.update_query_task,
        is_query_task_cancelled=runtime.is_query_task_cancelled,
        schedule_query_task=runtime.schedule_query_task,
        query_task_poll_interval_ms=runtime.query_task_poll_interval_ms,
    ))
    monkeypatch.setattr(dashboard_runtime, "create_dashboard_panel_execution", lambda **_kwargs: asyncio.sleep(0, result="exec_a"))

    async def raise_value_error(**_kwargs):
        raise ValueError("参数错误")

    monkeypatch.setattr(dashboard_runtime, "build_dashboard_panel_execution_result", raise_value_error)
    value_error = _payload(asyncio.run(dashboard_routes.execute_panel("dash_a", "panel_a", SimpleNamespace(), None)))
    assert value_error["code"] == ErrorCode.CHATDATA_BAD_REQUEST

    async def raise_generic_error(**_kwargs):
        raise Exception("未知错误")

    monkeypatch.setattr(dashboard_runtime, "build_dashboard_panel_execution_result", raise_generic_error)
    generic_error = _payload(asyncio.run(dashboard_routes.execute_panel("dash_a", "panel_a", SimpleNamespace(), None)))
    assert generic_error["code"] == ErrorCode.CHATDATA_DASHBOARD_EXECUTE_FAILED
    assert any(update[1].get("status") == "failed" for update in runtime.updated_tasks)


def test_dashboard_helpers_cover_missing_panel_and_no_datasource_batch(fake_dashboard_service, monkeypatch):
    _patch_dashboard_execution_service_shape(fake_dashboard_service)
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: fake_dashboard_service)
    runtime = FakeDashboardRuntime()
    dashboard_runtime.configure_dashboard_runtime(dashboard_runtime.DashboardRuntime(
        get_datasource_manager=runtime.get_datasource_manager,
        execute_sql_for_member=runtime.execute_sql_for_member,
        create_task_record=runtime.create_task_record,
        update_query_task=runtime.update_query_task,
        is_query_task_cancelled=runtime.is_query_task_cancelled,
        schedule_query_task=runtime.schedule_query_task,
        query_task_poll_interval_ms=runtime.query_task_poll_interval_ms,
    ))

    with pytest.raises(ValueError, match="面板不存在"):
        asyncio.run(dashboard_runtime.load_dashboard_panel_or_raise("missing", dashboard_id="dash_a", user_id="user_a"))

    no_ds_dashboard = {
        "id": "dash_no_ds",
        "dashboard_id": "dash_no_ds",
        "datasource_id": "",
        "panels": [{"id": "panel_no_ds", "name": "无数据源", "chart_type": "table", "chart_config": {}, "datasource_id": "", "sql": "select 1"}],
    }
    panel_map, target_map = asyncio.run(dashboard_runtime.prepare_dashboard_batch_context(
        "dash_no_ds",
        ["panel_no_ds"],
        {},
        preloaded_dashboard=no_ds_dashboard,
        user_id="user_a",
    ))

    assert panel_map["panel_no_ds"]["sql"] == "select 1"
    assert target_map == {}


def test_run_dashboard_panel_task_failed_result(fake_dashboard_service, monkeypatch):
    _patch_dashboard_execution_service_shape(fake_dashboard_service)
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: fake_dashboard_service)
    runtime = FakeDashboardRuntime(fail_members={"member_fail"})
    dashboard_runtime.configure_dashboard_runtime(dashboard_runtime.DashboardRuntime(
        get_datasource_manager=runtime.get_datasource_manager,
        execute_sql_for_member=runtime.execute_sql_for_member,
        create_task_record=runtime.create_task_record,
        update_query_task=runtime.update_query_task,
        is_query_task_cancelled=runtime.is_query_task_cancelled,
        schedule_query_task=runtime.schedule_query_task,
        query_task_poll_interval_ms=runtime.query_task_poll_interval_ms,
    ))

    asyncio.run(dashboard_runtime.run_dashboard_panel_task("task_panel_fail", {
        "request_payload": json.dumps({
            "dashboard_id": "dash_a",
            "panel_id": "panel_a",
            "member_datasource_id": "member_fail",
            "user_id": "user_a",
        }),
    }))

    assert any(task_id == "task_panel_fail" and kwargs.get("status") == "failed" for task_id, kwargs in runtime.updated_tasks)


def test_run_dashboard_batch_task_not_found_and_all_failed(monkeypatch):
    class BatchDashboardService:
        def __init__(self, panels=None):
            self.dashboard = {"id": "dash_a", "dashboard_id": "dash_a", "datasource_id": "ds_a", "panels": panels or []}

        async def get_dashboard(self, dashboard_id, user_id=None, include_public=True):
            return self.dashboard if dashboard_id == "dash_a" else None

    panels = [
        {"id": "panel_a", "name": "面板A", "chart_type": "bar", "chart_config": {}, "datasource_id": "ds_a", "sql": "select 1"},
        {"id": "panel_b", "name": "面板B", "chart_type": "bar", "chart_config": {}, "datasource_id": "ds_a", "sql": "select 2"},
    ]

    def configure(runtime):
        dashboard_runtime.configure_dashboard_runtime(dashboard_runtime.DashboardRuntime(
            get_datasource_manager=runtime.get_datasource_manager,
            execute_sql_for_member=runtime.execute_sql_for_member,
            create_task_record=runtime.create_task_record,
            update_query_task=runtime.update_query_task,
            is_query_task_cancelled=runtime.is_query_task_cancelled,
            schedule_query_task=runtime.schedule_query_task,
            query_task_poll_interval_ms=runtime.query_task_poll_interval_ms,
        ))

    missing_runtime = FakeDashboardRuntime()
    configure(missing_runtime)
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: BatchDashboardService([]))
    with pytest.raises(ValueError, match="大屏不存在"):
        asyncio.run(dashboard_runtime.run_dashboard_batch_task("task_missing", {
            "request_payload": json.dumps({"dashboard_id": "missing", "user_id": "user_a"}),
        }))

    fail_runtime = FakeDashboardRuntime(fail_members={"member_fail"})
    configure(fail_runtime)
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: BatchDashboardService(panels))
    monkeypatch.setattr(dashboard_runtime, "create_dashboard_panel_execution", lambda **_kwargs: asyncio.sleep(0, result="exec_a"))
    asyncio.run(dashboard_runtime.run_dashboard_batch_task("task_all_failed", {
        "request_payload": json.dumps({
            "dashboard_id": "dash_a",
            "panel_ids": ["panel_a", "panel_b"],
            "panel_member_datasource_ids": {"panel_a": "member_fail", "panel_b": "member_fail"},
            "user_id": "user_a",
            "operator_name": "测试用户",
        }),
    }))

    assert any(update[1].get("status") == "failed" and update[1].get("error_message") == "所有面板执行失败" for update in fail_runtime.updated_tasks)


def test_execute_panels_batch_accepts_list_missing_and_cancel(monkeypatch):
    _patch_dashboard_user(monkeypatch)

    class BatchDashboardService:
        async def get_dashboard(self, dashboard_id, user_id=None, include_public=True):
            if dashboard_id == "missing":
                return None
            return {
                "id": dashboard_id,
                "dashboard_id": dashboard_id,
                "datasource_id": "",
                "panels": [{"id": "panel_a", "name": "面板A", "chart_type": "table", "chart_config": {}, "datasource_id": "", "sql": "select 1"}],
            }

    async def fake_prepare_context(dashboard_id, panel_ids, panel_member_datasource_ids, **_kwargs):
        return {
            "panel_a": {"id": "panel_a", "name": "面板A", "chart_type": "table", "chart_config": {}, "datasource_id": "", "sql": "select 1"},
        }, {}

    async def fake_build_result(**kwargs):
        return {"panel_id": kwargs["panel"]["id"], "name": "面板A", "chart_type": "table", "chart_config": {}, "result": {"success": True, "data": [{"x": 1}]}}

    runtime = FakeDashboardRuntime(cancelled_after=1)
    dashboard_runtime.configure_dashboard_runtime(dashboard_runtime.DashboardRuntime(
        get_datasource_manager=runtime.get_datasource_manager,
        execute_sql_for_member=runtime.execute_sql_for_member,
        create_task_record=runtime.create_task_record,
        update_query_task=runtime.update_query_task,
        is_query_task_cancelled=runtime.is_query_task_cancelled,
        schedule_query_task=runtime.schedule_query_task,
        query_task_poll_interval_ms=runtime.query_task_poll_interval_ms,
    ))
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: BatchDashboardService())
    monkeypatch.setattr(dashboard_runtime, "prepare_dashboard_batch_context", fake_prepare_context)
    monkeypatch.setattr(dashboard_runtime, "create_dashboard_panel_execution", lambda **_kwargs: asyncio.sleep(0, result="exec_a"))
    monkeypatch.setattr(dashboard_runtime, "build_dashboard_panel_execution_result", fake_build_result)

    missing = _payload(asyncio.run(dashboard_routes.execute_panels_batch("missing", SimpleNamespace(), None)))
    response = asyncio.run(dashboard_routes.execute_panels_batch("dash_a", SimpleNamespace(), ["panel_a"]))
    chunks = asyncio.run(_collect_stream(response))

    assert missing["code"] == ErrorCode.CHATDATA_NOT_FOUND
    assert "已人工取消" in "".join(chunks)


def test_execute_panels_batch_generator_exception_and_client_cancel(monkeypatch):
    _patch_dashboard_user(monkeypatch)

    class SinglePanelService:
        async def get_dashboard(self, dashboard_id, user_id=None, include_public=True):
            return {"id": dashboard_id, "dashboard_id": dashboard_id, "datasource_id": "ds_a", "panels": [{"id": "panel_a"}]}

    async def fake_prepare_context(*_args, **_kwargs):
        return {
            "panel_a": {"id": "panel_a", "name": "面板A", "chart_type": "table", "chart_config": {}, "datasource_id": "ds_a", "sql": "select 1"},
        }, {}

    runtime = FakeDashboardRuntime(cancelled_after=999)
    dashboard_runtime.configure_dashboard_runtime(dashboard_runtime.DashboardRuntime(
        get_datasource_manager=runtime.get_datasource_manager,
        execute_sql_for_member=runtime.execute_sql_for_member,
        create_task_record=runtime.create_task_record,
        update_query_task=runtime.update_query_task,
        is_query_task_cancelled=runtime.is_query_task_cancelled,
        schedule_query_task=runtime.schedule_query_task,
        query_task_poll_interval_ms=runtime.query_task_poll_interval_ms,
    ))
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: SinglePanelService())
    monkeypatch.setattr(dashboard_runtime, "prepare_dashboard_batch_context", fake_prepare_context)
    monkeypatch.setattr(dashboard_runtime, "create_dashboard_panel_execution", lambda **_kwargs: asyncio.sleep(0, result="exec_a"))

    async def raise_panel_error(**_kwargs):
        raise RuntimeError("panel failed")

    monkeypatch.setattr(dashboard_runtime, "build_dashboard_panel_execution_result", raise_panel_error)
    response = asyncio.run(dashboard_routes.execute_panels_batch(
        "dash_a",
        SimpleNamespace(),
        PanelBatchExecuteRequest(panel_ids=["panel_a"]),
    ))
    chunks = asyncio.run(_collect_stream(response))
    assert "panel failed" in "".join(chunks)
    assert "data: [DONE]" in "".join(chunks)

    async def start_then_cancel(response):
        iterator = response.body_iterator
        task = asyncio.create_task(iterator.__anext__())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def slow_success(**kwargs):
        await asyncio.sleep(0.1)
        return {"panel_id": kwargs["panel"]["id"], "name": "面板A", "chart_type": "table", "chart_config": {}, "result": {"success": True}}

    monkeypatch.setattr(dashboard_runtime, "build_dashboard_panel_execution_result", slow_success)
    cancel_response = asyncio.run(dashboard_routes.execute_panels_batch(
        "dash_a",
        SimpleNamespace(),
        PanelBatchExecuteRequest(panel_ids=["panel_a"]),
    ))
    asyncio.run(start_then_cancel(cancel_response))

    assert any(update[1].get("error_message") == "客户端取消看板执行" for update in runtime.updated_tasks)


def test_create_dashboard_query_task_error_and_list_request(fake_dashboard_service, monkeypatch):
    _patch_dashboard_user(monkeypatch)
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: fake_dashboard_service)
    runtime = FakeDashboardRuntime()
    dashboard_runtime.configure_dashboard_runtime(dashboard_runtime.DashboardRuntime(
        get_datasource_manager=runtime.get_datasource_manager,
        execute_sql_for_member=runtime.execute_sql_for_member,
        create_task_record=runtime.create_task_record,
        update_query_task=runtime.update_query_task,
        is_query_task_cancelled=runtime.is_query_task_cancelled,
        schedule_query_task=runtime.schedule_query_task,
        query_task_poll_interval_ms=runtime.query_task_poll_interval_ms,
    ))

    list_request = _payload(asyncio.run(dashboard_routes.create_dashboard_query_task("dash_a", SimpleNamespace(), ["panel_a"])))
    missing = _payload(asyncio.run(dashboard_routes.create_dashboard_query_task("missing", SimpleNamespace(), None)))

    async def raise_create_task(**_kwargs):
        raise RuntimeError("create task failed")

    runtime.create_task_record = raise_create_task
    dashboard_runtime.configure_dashboard_runtime(dashboard_runtime.DashboardRuntime(
        get_datasource_manager=runtime.get_datasource_manager,
        execute_sql_for_member=runtime.execute_sql_for_member,
        create_task_record=runtime.create_task_record,
        update_query_task=runtime.update_query_task,
        is_query_task_cancelled=runtime.is_query_task_cancelled,
        schedule_query_task=runtime.schedule_query_task,
        query_task_poll_interval_ms=runtime.query_task_poll_interval_ms,
    ))
    generic_error = _payload(asyncio.run(dashboard_routes.create_dashboard_query_task("dash_a", SimpleNamespace(), None)))

    assert list_request["data"]["task_id"] == "task_1"
    assert missing["code"] == ErrorCode.CHATDATA_NOT_FOUND
    assert generic_error["code"] == ErrorCode.CHATDATA_DASHBOARD_EXECUTE_FAILED
