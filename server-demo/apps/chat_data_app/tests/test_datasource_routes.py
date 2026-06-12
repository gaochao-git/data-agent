import asyncio
import copy
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1")
os.environ.setdefault("FXCJG_DB_PORT", "3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app import routes
from apps.chat_data_app.routes import (
    clear_default_datasource,
    create_dashboard_query_task,
    create_panel_query_task,
    create_query_task,
    create_datasource_group,
    delete_datasource,
    execute_panel,
    execute_panels_batch,
    execute_sql_direct,
    get_query_task,
    get_datasources,
    list_semantic_configs,
    refresh_single_datasource,
    set_default_datasource,
    update_datasource_group_config,
    update_datasource_meta,
    update_mapped_modules,
)
from apps.chat_data_app.handlers import _build_execution_target_info
from apps.chat_data_app.schema import (
    DatasourceGroupCreateRequest,
    DatasourceGroupUpdateRequest,
    DatasourceRefreshRequest,
    DatasourceUpdateRequest,
    PanelBatchExecuteRequest,
    PanelExecuteRequest,
    QuickSqlRequest,
    QueryTaskCreateRequest,
)


def _payload(resp):
    return json.loads(resp.body.decode("utf-8"))


class _FakeDatasourceManager:
    def __init__(self):
        self.datasources = {
            "ds_a": {
                "id": "ds_a",
                "group_id": "group_ds_a",
                "template_datasource_id": "phy_a",
                "ds_name": "数据源A",
                "ds_description": "描述A",
                "source_type": "mysql",
                "query_executor_type": "direct",
                "source_config": {"host": "127.0.0.1", "port": 3306, "database": "db_a"},
                "ds_tables": [{"table_name": "t_a", "description": "t_a", "columns": []}],
            "mapped_modules": ["投诉举报"],
            "is_default": True,
            "member_sources": [
                {"member_datasource_id": "phy_a", "source_type": "mysql", "host": "127.0.0.1", "port": 3306, "database": "db_a"},
            ],
            "semantic_configs": [],
        },
            "ds_b": {
                "id": "ds_b",
                "group_id": "group_ds_b",
                "template_datasource_id": "phy_b",
                "ds_name": "数据源B",
                "ds_description": "描述B",
                "source_type": "mysql",
                "query_executor_type": "direct",
                "source_config": {"host": "127.0.0.2", "port": 3306, "database": "db_b"},
                "ds_tables": [{"table_name": "t_b", "description": "t_b", "columns": []}],
            "mapped_modules": [],
            "is_default": False,
            "member_sources": [
                {"member_datasource_id": "phy_b", "source_type": "mysql", "host": "127.0.0.2", "port": 3306, "database": "db_b"},
            ],
            "semantic_configs": [],
        },
        }
        self.last_update_call = None
        self.deleted_ids = []
    def get_all_datasources(self, include_tables: bool = True):
        items = []
        for item in self.datasources.values():
            next_item = copy.deepcopy(item)
            next_item["ds_tables"] = list(item.get("ds_tables") or []) if include_tables else []
            items.append(next_item)
        return items

    def get_datasource(self, datasource_id: str):
        item = self.datasources.get(datasource_id)
        return copy.deepcopy(item) if item else None

    def has_datasource(self, datasource_id: str):
        return datasource_id in self.datasources

    def get_datasource_by_name(self, name: str):
        for item in self.datasources.values():
            if item["ds_name"] == name:
                return copy.deepcopy(item)
        return None

    def update_datasource(self, datasource_id: str, **kwargs):
        item = self.datasources.get(datasource_id)
        if not item:
            return None
        self.last_update_call = {"datasource_id": datasource_id, **kwargs}
        if "name" in kwargs and kwargs["name"] is not None:
            item["ds_name"] = kwargs["name"]
        if "description" in kwargs and kwargs["description"] is not None:
            item["ds_description"] = kwargs["description"]
        if "mapped_modules" in kwargs and kwargs["mapped_modules"] is not None:
            item["mapped_modules"] = list(kwargs["mapped_modules"])
        if "tables" in kwargs and kwargs["tables"] is not None:
            item["ds_tables"] = list(kwargs["tables"])
        if "source_config" in kwargs and kwargs["source_config"] is not None:
            item["source_config"] = dict(kwargs["source_config"])
        return copy.deepcopy(item)

    def update_datasource_group_from_sources(
        self,
        datasource_id: str,
        group_name: str,
        group_desc: str,
        source_type: str,
        template_source_config: dict,
        member_source_configs: list[dict] | None = None,
        tables: list[dict] | None = None,
        update_by: str = "",
    ):
        item = self.datasources.get(datasource_id)
        if not item:
            return None
        item["ds_name"] = group_name
        item["ds_description"] = group_desc
        item["source_type"] = source_type
        item["query_executor_type"] = template_source_config.get("query_executor_type") or "direct"
        item["source_config"] = dict(template_source_config)
        item["ds_tables"] = list(tables or [])
        item["member_sources"] = [
            {"member_datasource_id": "phy_template", "source_type": source_type, **template_source_config},
            *[
                {"member_datasource_id": f"phy_{index}", "source_type": source_type, **cfg}
                for index, cfg in enumerate(member_source_configs or [], start=1)
            ],
        ]
        return copy.deepcopy(item)

    def set_default_datasource(self, datasource_id: str):
        if datasource_id not in self.datasources:
            return False
        for item in self.datasources.values():
            item["is_default"] = False
        self.datasources[datasource_id]["is_default"] = True
        return True

    def clear_default_datasource(self, datasource_id: str):
        if datasource_id not in self.datasources or not self.datasources[datasource_id].get("is_default"):
            return False
        self.datasources[datasource_id]["is_default"] = False
        return True

    def delete_datasource(self, datasource_id: str):
        if datasource_id not in self.datasources:
            return False
        self.deleted_ids.append(datasource_id)
        del self.datasources[datasource_id]
        return True

    def get_execution_datasources(self, datasource_id: str, member_datasource_ids: list[str] | None = None):
        item = self.datasources.get(datasource_id)
        if not item:
            raise ValueError(f"数据源不存在: {datasource_id}")
        members = list(item.get("member_sources") or [])
        if not members:
            raise ValueError("当前数据源组下没有可执行的数据源")
        member_map = {member["member_datasource_id"]: member for member in members if member.get("member_datasource_id")}
        selected_ids = [member_id for member_id in (member_datasource_ids or []) if member_id]
        if not selected_ids:
            selected_ids = [item.get("template_datasource_id") or members[0]["member_datasource_id"]]
        result = []
        for member_id in selected_ids:
            member = member_map.get(member_id)
            if not member:
                raise ValueError(f"成员数据源不存在或不属于当前组: {member_id}")
            result.append({
                "id": member_id,
                "member_datasource_id": member_id,
                "member_name": member.get("datasource_name") or member_id,
                "source_type": member.get("source_type") or item.get("source_type"),
                "query_executor_type": member.get("query_executor_type") or item.get("query_executor_type") or "direct",
                "source_config": {
                    "host": member.get("host"),
                    "port": member.get("port"),
                    "database": member.get("database"),
                },
            })
        return result

    def create_datasource_group_from_sources(
        self,
        group_name: str,
        group_desc: str,
        source_type: str,
        template_source_config: dict,
        member_source_configs: list[dict] | None = None,
        tables: list[dict] | None = None,
        create_by: str = "",
    ):
        datasource_id = "ds_group_from_template"
        datasource = {
            "id": datasource_id,
            "group_id": f"group_{datasource_id}",
            "template_datasource_id": "phy_template",
            "member_datasource_ids": ["phy_template", "phy_member_a"],
            "ds_name": group_name,
            "ds_description": group_desc,
            "source_type": source_type,
            "query_executor_type": template_source_config.get("query_executor_type") or "direct",
            "source_config": dict(template_source_config),
            "ds_tables": list(tables or []),
            "mapped_modules": [],
            "is_default": False,
            "semantic_configs": [],
        }
        self.datasources[datasource_id] = datasource
        return copy.deepcopy(datasource)


class _FakeMySQLSyncService:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def get_full_schema_async(self, table_names=None):
        names = table_names or ["orders", "users"]
        return [{"table_name": name, "description": name, "columns": [{"name": "id", "type": "bigint", "description": ""}]} for name in names]


class _FakeSessionContext:
    def __init__(self):
        self.items = []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def add(self, item):
        self.items.append(item)

    async def commit(self):
        self.committed = True


class _FakeExecutor:
    def __init__(self, datasource: dict):
        self.datasource = datasource

    async def execute(self, sql: str, limit: int = 100, timeout: int = 30, user_id: str | None = None):
        member_name = self.datasource.get("member_name") or self.datasource.get("member_datasource_id")
        if self.datasource.get("member_datasource_id") == "phy_error":
            return {
                "success": False,
                "columns": [],
                "data": [],
                "total_count": 0,
                "error": f"{member_name} 执行失败",
            }
        return {
            "success": True,
            "columns": ["member_name", "sql_text"],
            "data": [{"member_name": member_name, "sql_text": sql}],
            "total_count": 1,
            "execution_time_ms": 12,
        }

    async def close(self):
        return None


class _FakeDashboardService:
    def __init__(self):
        self.panels = {
            "panel_a": {
                "id": "panel_a",
                "dashboard_id": "dash_a",
                "name": "面板A",
                "sql": "select 1",
                "chart_type": "table",
                "chart_config": {},
                "datasource_id": "ds_a",
            },
            "panel_b": {
                "id": "panel_b",
                "dashboard_id": "dash_a",
                "name": "面板B",
                "sql": "select 2",
                "chart_type": "table",
                "chart_config": {},
                "datasource_id": "ds_a",
            },
        }

    async def get_panel(self, panel_id: str):
        panel = self.panels.get(panel_id)
        return copy.deepcopy(panel) if panel else None

    async def get_dashboard(self, dashboard_id: str):
        if dashboard_id != "dash_a":
            return None
        return {
            "id": dashboard_id,
            "panels": [copy.deepcopy(panel) for panel in self.panels.values()],
        }


async def _collect_stream_chunks(response):
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)
    return chunks


def _mock_authenticated_user(monkeypatch, user_id: str = "chatdata_user", username: str = "chatdata_user"):
    monkeypatch.setattr(
        routes,
        "authenticate_request",
        lambda _request: SimpleNamespace(user_id=user_id, username=username),
    )


def test_get_datasources_returns_group_datasource_list(monkeypatch):
    from apps.chat_data_app import routes

    manager = _FakeDatasourceManager()
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)

    resp = asyncio.run(get_datasources())
    data = _payload(resp)
    assert data["status"] == "success"
    assert len(data["data"]["datasources"]) == 2
    assert data["data"]["datasources"][0]["group_id"].startswith("group_")


def test_get_datasources_supports_lightweight_mode(monkeypatch):
    from apps.chat_data_app import routes

    manager = _FakeDatasourceManager()
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)

    resp = asyncio.run(get_datasources(include_tables=False))
    data = _payload(resp)

    assert data["status"] == "success"
    assert all(item["ds_tables"] == [] for item in data["data"]["datasources"])


def test_list_semantic_configs_uses_lightweight_existence_check(monkeypatch):
    manager = _FakeDatasourceManager()
    calls = []

    def _get_datasource(_datasource_id: str):
        raise AssertionError("should not load datasource detail")

    def _has_datasource(datasource_id: str):
        calls.append(("has_datasource", datasource_id))
        return datasource_id == "ds_a"

    def _get_semantic_configs(datasource_id: str):
        calls.append(("get_semantic_configs", datasource_id))
        return [{"semantic_config_id": "cfg_1"}]

    manager.get_datasource = _get_datasource
    manager.has_datasource = _has_datasource
    manager.get_semantic_configs = _get_semantic_configs
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)

    resp = asyncio.run(list_semantic_configs("ds_a"))
    data = _payload(resp)

    assert data["status"] == "success"
    assert data["data"]["configs"] == [{"semantic_config_id": "cfg_1"}]
    assert calls == [("has_datasource", "ds_a"), ("get_semantic_configs", "ds_a")]


def test_execute_sql_direct_returns_selected_member_results(monkeypatch):
    from apps.chat_data_app import routes
    from apps.chat_data_app.schema import ExecuteSqlRequest

    manager = _FakeDatasourceManager()
    manager.datasources["ds_a"]["member_sources"] = [
        {"member_datasource_id": "phy_a", "datasource_name": "成员A", "source_type": "mysql", "host": "127.0.0.1", "port": 3306, "database": "db_a"},
        {"member_datasource_id": "phy_b", "datasource_name": "成员B", "source_type": "mysql", "host": "127.0.0.2", "port": 3306, "database": "db_b"},
    ]
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(routes, "get_executor", lambda datasource: _FakeExecutor(datasource))
    _mock_authenticated_user(monkeypatch)

    resp = asyncio.run(execute_sql_direct(SimpleNamespace(), ExecuteSqlRequest(
        sql="select 1",
        datasource_id="ds_a",
        member_datasource_ids=["phy_b"],
    )))
    data = _payload(resp)
    assert data["status"] == "success"
    assert data["data"]["member_datasource_id"] == "phy_b"
    assert len(data["data"]["results"]) == 1
    assert data["data"]["results"][0]["member_name"] == "成员B"


def test_execute_sql_direct_returns_multi_member_results(monkeypatch):
    from apps.chat_data_app import routes
    from apps.chat_data_app.schema import ExecuteSqlRequest

    manager = _FakeDatasourceManager()
    manager.datasources["ds_a"]["member_sources"] = [
        {"member_datasource_id": "phy_a", "datasource_name": "成员A", "source_type": "mysql", "host": "127.0.0.1", "port": 3306, "database": "db_a"},
        {"member_datasource_id": "phy_b", "datasource_name": "成员B", "source_type": "mysql", "host": "127.0.0.2", "port": 3306, "database": "db_b"},
    ]
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(routes, "get_executor", lambda datasource: _FakeExecutor(datasource))
    _mock_authenticated_user(monkeypatch)

    resp = asyncio.run(execute_sql_direct(SimpleNamespace(), ExecuteSqlRequest(
        sql="select 1",
        datasource_id="ds_a",
        member_datasource_ids=["phy_a", "phy_b"],
    )))
    data = _payload(resp)
    assert data["status"] == "success"
    assert data["data"]["member_datasource_id"] == "phy_a"
    assert len(data["data"]["results"]) == 2
    assert data["data"]["results"][0]["member_name"] == "成员A"
    assert data["data"]["results"][1]["member_name"] == "成员B"


def test_create_query_task_schedules_background_run(monkeypatch):
    manager = _FakeDatasourceManager()
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    _mock_authenticated_user(monkeypatch)

    captured = {}

    async def fake_create_task(payload):
        captured["payload"] = payload
        return {
            "task_id": "task_001",
            "task_type": "sql",
            "status": "pending",
        }

    def fake_schedule(task_id: str):
        captured["scheduled_task_id"] = task_id

    monkeypatch.setattr(routes, "_create_query_task", fake_create_task)
    monkeypatch.setattr(routes, "_schedule_query_task", fake_schedule)

    resp = asyncio.run(create_query_task(SimpleNamespace(), QueryTaskCreateRequest(
        task_type="sql",
        datasource_id="ds_a",
        member_datasource_ids=["phy_a"],
        sql="select 1",
        limit=100,
        timeout=30,
        query_type="sql",
    )))

    data = _payload(resp)
    assert data["status"] == "success"
    assert data["data"]["task_id"] == "task_001"
    assert data["data"]["status"] == "pending"
    assert captured["payload"].datasource_id == "ds_a"
    assert captured["scheduled_task_id"] == "task_001"


def test_get_query_task_returns_task_status(monkeypatch):
    async def fake_get_query_task(task_id: str):
        assert task_id == "task_001"
        return {
            "task_id": "task_001",
            "task_type": "sql",
            "datasource_id": "ds_a",
            "query_type": "builder",
            "status": "succeeded",
            "progress_text": "执行完成",
            "request_payload": "{\"sql\": \"select 1\"}",
            "result_payload": "{\"sql\": \"select 1\", \"columns\": [\"col_a\"], \"data\": [{\"col_a\": 1}], \"total_count\": 1, \"results\": []}",
            "remote_task_payload": "[{\"member_datasource_id\": \"phy_a\", \"member_name\": \"成员A\", \"remote_task_id\": \"remote_001\"}]",
            "error_message": None,
            "started_time": "2026-03-20T10:00:00",
            "finished_time": "2026-03-20T10:00:05",
            "create_time": "2026-03-20T10:00:00",
            "update_time": "2026-03-20T10:00:05",
        }

    monkeypatch.setattr(routes, "_get_query_task", fake_get_query_task)
    monkeypatch.setattr(routes, "_build_live_remote_sql_result", lambda _response_data: asyncio.sleep(0, result={
        "status": "succeeded",
        "progress_text": "执行完成",
        "error_message": None,
        "result_payload": {
            "success": True,
            "sql": "select 1",
            "columns": ["col_a"],
            "data": [{"col_a": 1}],
            "total_count": 1,
            "results": [{
                "member_datasource_id": "phy_a",
                "member_name": "成员A",
                "success": True,
                "columns": ["col_a"],
                "data": [{"col_a": 1}],
                "total_count": 1,
                "execution_time_ms": 12,
                "error": None,
            }],
        },
    }))

    resp = asyncio.run(get_query_task("task_001"))
    data = _payload(resp)
    assert data["status"] == "success"
    assert data["data"]["task_id"] == "task_001"
    assert data["data"]["status"] == "succeeded"
    assert data["data"]["remote_tasks"] == [{"member_datasource_id": "phy_a", "member_name": "成员A", "remote_task_id": "remote_001", "source_type": "hive"}]
    assert data["data"]["result"] is None
    assert data["data"]["results"] == [{
        "member_datasource_id": "phy_a",
        "member_name": "成员A",
        "success": True,
        "columns": ["col_a"],
        "data": [{"col_a": 1}],
        "total_count": 1,
        "execution_time_ms": 12,
        "error": None,
    }]


def test_run_sql_query_task_skips_result_payload_for_all_http_targets(monkeypatch):
    updates = []

    manager = _FakeDatasourceManager()
    manager.datasources["ds_a"]["query_executor_type"] = "http"
    manager.datasources["ds_a"]["member_sources"] = [
        {"member_datasource_id": "phy_http", "datasource_name": "HTTP成员", "source_type": "mysql", "query_executor_type": "http", "host": "127.0.0.1", "port": 3306, "database": "db_a"},
    ]
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)

    async def fake_build_result(_request, query_task_id=None):
        assert query_task_id == "task_001"
        return {
            "success": True,
            "sql": "select 1",
            "columns": ["col_a"],
            "data": [{"col_a": 1}],
            "total_count": 1,
            "execution_time_ms": 12,
            "results": [],
        }

    async def fake_update_query_task(task_id, **kwargs):
        updates.append((task_id, kwargs))
        return None

    async def fake_record_history(_request, _result_data):
        return None

    monkeypatch.setattr(routes, "_build_execute_sql_result_data", fake_build_result)
    monkeypatch.setattr(routes, "_update_query_task", fake_update_query_task)
    monkeypatch.setattr(routes, "_record_sql_query_history", fake_record_history)

    asyncio.run(routes._run_sql_query_task("task_001", {
        "request_payload": json.dumps({
            "sql": "select 1",
            "datasource_id": "ds_a",
            "member_datasource_ids": ["phy_http"],
        })
    }))

    assert updates[1][1]["status"] == "succeeded"
    assert updates[1][1]["result_payload"] is None


def test_execute_sql_direct_keeps_failed_member_result(monkeypatch):
    from apps.chat_data_app import routes
    from apps.chat_data_app.schema import ExecuteSqlRequest

    manager = _FakeDatasourceManager()
    manager.datasources["ds_a"]["member_sources"] = [
        {"member_datasource_id": "phy_a", "datasource_name": "成员A", "source_type": "mysql", "host": "127.0.0.1", "port": 3306, "database": "db_a"},
        {"member_datasource_id": "phy_error", "datasource_name": "成员失败", "source_type": "mysql", "host": "127.0.0.3", "port": 3306, "database": "db_c"},
    ]
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(routes, "get_executor", lambda datasource: _FakeExecutor(datasource))
    _mock_authenticated_user(monkeypatch)

    resp = asyncio.run(execute_sql_direct(SimpleNamespace(), ExecuteSqlRequest(
        sql="select 1",
        datasource_id="ds_a",
        member_datasource_ids=["phy_a", "phy_error"],
    )))
    data = _payload(resp)
    assert data["status"] == "success"
    assert len(data["data"]["results"]) == 2
    assert data["data"]["results"][1]["success"] is False
    assert "执行失败" in data["data"]["results"][1]["error"]


def test_execute_sql_direct_returns_error_when_all_members_fail(monkeypatch):
    from apps.chat_data_app import routes
    from apps.chat_data_app.schema import ExecuteSqlRequest

    manager = _FakeDatasourceManager()
    manager.datasources["ds_a"]["member_sources"] = [
        {"member_datasource_id": "phy_error", "datasource_name": "成员失败", "source_type": "mysql", "host": "127.0.0.3", "port": 3306, "database": "db_c"},
    ]
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(routes, "get_executor", lambda datasource: _FakeExecutor(datasource))
    _mock_authenticated_user(monkeypatch)

    resp = asyncio.run(execute_sql_direct(SimpleNamespace(), ExecuteSqlRequest(
        sql="select 1",
        datasource_id="ds_a",
        member_datasource_ids=["phy_error"],
    )))
    data = _payload(resp)
    assert data["status"] == "error"
    assert data["data"]["success"] is False
    assert data["data"]["error"] == "成员失败 执行失败"


def test_record_sql_query_history_uses_execution_audit_fields(monkeypatch):
    from apps.chat_data_app import routes
    from apps.chat_data_app.schema import ExecuteSqlRequest

    session = _FakeSessionContext()
    monkeypatch.setattr(routes, "get_session", lambda: session)

    asyncio.run(routes._record_sql_query_history(
        ExecuteSqlRequest(sql="select 1", datasource_id="ds_a", user_id="user_001", query_type="sql"),
        {
            "success": False,
            "execution_time_ms": 17,
            "data": [{"a": 1}, {"a": 2}],
            "error": "SQL syntax error",
        },
    ))

    assert session.committed is True
    history = session.items[0]
    assert history.execution_time_ms == 17
    assert history.result_row_count == 2
    assert history.success_flag == 0
    assert history.error_message == "SQL syntax error"
    assert not hasattr(history, "frequency")


def test_run_sql_query_task_marks_failed_when_all_members_fail(monkeypatch):
    from apps.chat_data_app import routes

    updates = []
    history_calls = []

    async def fake_build_result(_request, query_task_id=None):
        return {
            "success": False,
            "sql": "select 1",
            "columns": [],
            "data": [],
            "total_count": 0,
            "execution_time_ms": 9,
            "error": "成员失败 执行失败",
            "results": [
                {
                    "success": False,
                    "member_datasource_id": "phy_error",
                    "member_name": "成员失败",
                    "error": "成员失败 执行失败",
                }
            ],
        }

    async def fake_update_query_task(task_id, **kwargs):
        updates.append((task_id, kwargs))
        return None

    async def fake_record_history(_request, result_data):
        history_calls.append(result_data)

    monkeypatch.setattr(routes, "_build_execute_sql_result_data", fake_build_result)
    monkeypatch.setattr(routes, "_update_query_task", fake_update_query_task)
    monkeypatch.setattr(routes, "_record_sql_query_history", fake_record_history)

    asyncio.run(routes._run_sql_query_task("task_001", {
        "request_payload": json.dumps({
            "sql": "select 1",
            "datasource_id": "ds_a",
            "member_datasource_ids": ["phy_error"],
        })
    }))

    assert history_calls and history_calls[0]["success"] is False
    assert updates[-1][0] == "task_001"
    assert updates[-1][1]["status"] == "failed"
    assert updates[-1][1]["error_message"] == "成员失败 执行失败"


def test_execute_panel_uses_selected_member_datasource(monkeypatch):
    from apps.chat_data_app import routes

    manager = _FakeDatasourceManager()
    manager.datasources["ds_a"]["member_sources"] = [
        {"member_datasource_id": "phy_a", "datasource_name": "成员A", "source_type": "mysql", "host": "127.0.0.1", "port": 3306, "database": "db_a"},
        {"member_datasource_id": "phy_b", "datasource_name": "成员B", "source_type": "mysql", "host": "127.0.0.2", "port": 3306, "database": "db_b"},
    ]
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(routes, "get_dashboard_service", lambda: _FakeDashboardService())
    monkeypatch.setattr(routes, "get_executor", lambda datasource: _FakeExecutor(datasource))

    resp = asyncio.run(execute_panel(
        "dash_a",
        "panel_a",
        PanelExecuteRequest(member_datasource_id="phy_b"),
    ))
    data = _payload(resp)
    assert data["status"] == "success"
    assert data["data"]["member_datasource_id"] == "phy_b"
    assert data["data"]["result"]["member_name"] == "成员B"


def test_execute_panel_returns_error_when_member_execution_fails(monkeypatch):
    from apps.chat_data_app import routes

    manager = _FakeDatasourceManager()
    manager.datasources["ds_a"]["member_sources"] = [
        {"member_datasource_id": "phy_error", "datasource_name": "成员失败", "source_type": "mysql", "host": "127.0.0.3", "port": 3306, "database": "db_c"},
    ]
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(routes, "get_dashboard_service", lambda: _FakeDashboardService())
    monkeypatch.setattr(routes, "get_executor", lambda datasource: _FakeExecutor(datasource))

    resp = asyncio.run(execute_panel(
        "dash_a",
        "panel_a",
        PanelExecuteRequest(member_datasource_id="phy_error"),
    ))
    data = _payload(resp)
    assert data["status"] == "error"
    assert data["msg"] == "成员失败 执行失败"


def test_create_panel_query_task_schedules_dashboard_panel_task(monkeypatch):
    manager = _FakeDatasourceManager()
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(routes, "get_dashboard_service", lambda: _FakeDashboardService())

    captured = {}

    async def fake_create_task_record(**kwargs):
        captured["kwargs"] = kwargs
        return {
            "task_id": "panel_task_001",
            "task_type": "dashboard_batch",
            "status": "pending",
        }

    def fake_schedule(task_id: str):
        captured["task_id"] = task_id

    monkeypatch.setattr(routes, "_create_task_record", fake_create_task_record)
    monkeypatch.setattr(routes, "_schedule_query_task", fake_schedule)

    resp = asyncio.run(create_panel_query_task(
        "dash_a",
        "panel_a",
        PanelExecuteRequest(member_datasource_id="phy_b"),
    ))
    data = _payload(resp)
    assert data["status"] == "success"
    assert data["data"]["task_id"] == "panel_task_001"
    assert captured["kwargs"]["task_type"] == "dashboard_batch"
    assert captured["kwargs"]["request_payload"]["panel_ids"] == ["panel_a"]
    assert captured["kwargs"]["request_payload"]["panel_member_datasource_ids"]["panel_a"] == "phy_b"
    assert captured["task_id"] == "panel_task_001"


def test_run_dashboard_panel_task_marks_failed_when_member_execution_fails(monkeypatch):
    from apps.chat_data_app import routes

    updates = []

    async def fake_load_panel(_panel_id: str):
        return {
            "id": "panel_a",
            "name": "面板A",
            "chart_type": "table",
            "chart_config": {},
            "sql": "select 1",
            "datasource_id": "ds_a",
        }

    async def fake_build_panel_result(panel, member_datasource_id=None):
        assert panel["id"] == "panel_a"
        assert member_datasource_id == "phy_error"
        return {
            "panel_id": "panel_a",
            "name": "面板A",
            "chart_type": "table",
            "chart_config": {},
            "member_datasource_id": "phy_error",
            "member_name": "成员失败",
            "result": {
                "success": False,
                "error": "成员失败 执行失败",
                "member_datasource_id": "phy_error",
                "member_name": "成员失败",
            },
        }

    async def fake_update_query_task(task_id, **kwargs):
        updates.append((task_id, kwargs))
        return None

    monkeypatch.setattr(routes, "_load_dashboard_panel_or_raise", fake_load_panel)
    monkeypatch.setattr(routes, "_build_dashboard_panel_execution_result", fake_build_panel_result)
    monkeypatch.setattr(routes, "_update_query_task", fake_update_query_task)

    asyncio.run(routes._run_dashboard_panel_task("panel_task_001", {
        "request_payload": json.dumps({
            "panel_id": "panel_a",
            "member_datasource_id": "phy_error",
        })
    }))

    assert updates[-1][0] == "panel_task_001"
    assert updates[-1][1]["status"] == "failed"
    assert updates[-1][1]["error_message"] == "成员失败 执行失败"


def test_execute_panels_batch_uses_panel_member_mapping(monkeypatch):
    from apps.chat_data_app import routes

    manager = _FakeDatasourceManager()
    manager.datasources["ds_a"]["member_sources"] = [
        {"member_datasource_id": "phy_a", "datasource_name": "成员A", "source_type": "mysql", "host": "127.0.0.1", "port": 3306, "database": "db_a"},
        {"member_datasource_id": "phy_b", "datasource_name": "成员B", "source_type": "mysql", "host": "127.0.0.2", "port": 3306, "database": "db_b"},
    ]
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(routes, "get_dashboard_service", lambda: _FakeDashboardService())
    monkeypatch.setattr(routes, "get_executor", lambda datasource: _FakeExecutor(datasource))

    response = asyncio.run(execute_panels_batch(
        "dash_a",
        PanelBatchExecuteRequest(
            panel_ids=["panel_a"],
            panel_member_datasource_ids={"panel_a": "phy_b"},
        ),
    ))
    chunks = asyncio.run(_collect_stream_chunks(response))
    payload = next(chunk for chunk in chunks if chunk.startswith("data: {"))
    result = json.loads(payload[len("data: "):].strip())
    assert result["panel_id"] == "panel_a"
    assert result["member_datasource_id"] == "phy_b"
    assert result["result"]["member_name"] == "成员B"


def test_execute_panels_batch_marks_failed_when_member_execution_fails(monkeypatch):
    from apps.chat_data_app import routes

    manager = _FakeDatasourceManager()
    manager.datasources["ds_a"]["member_sources"] = [
        {"member_datasource_id": "phy_error", "datasource_name": "成员失败", "source_type": "mysql", "host": "127.0.0.3", "port": 3306, "database": "db_c"},
    ]
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(routes, "get_dashboard_service", lambda: _FakeDashboardService())
    monkeypatch.setattr(routes, "get_executor", lambda datasource: _FakeExecutor(datasource))

    response = asyncio.run(execute_panels_batch(
        "dash_a",
        PanelBatchExecuteRequest(
            panel_ids=["panel_a"],
            panel_member_datasource_ids={"panel_a": "phy_error"},
        ),
    ))
    chunks = asyncio.run(_collect_stream_chunks(response))
    payload = next(chunk for chunk in chunks if chunk.startswith("data: {"))
    result = json.loads(payload[len("data: "):].strip())
    assert result["panel_id"] == "panel_a"
    assert result["error"] == "成员失败 执行失败"


def test_create_dashboard_query_task_schedules_dashboard_batch_task(monkeypatch):
    monkeypatch.setattr(routes, "get_dashboard_service", lambda: _FakeDashboardService())

    captured = {}

    async def fake_create_task_record(**kwargs):
        captured["kwargs"] = kwargs
        return {
            "task_id": "dash_task_001",
            "task_type": "dashboard_batch",
            "status": "pending",
        }

    def fake_schedule(task_id: str):
        captured["task_id"] = task_id

    monkeypatch.setattr(routes, "_create_task_record", fake_create_task_record)
    monkeypatch.setattr(routes, "_schedule_query_task", fake_schedule)

    resp = asyncio.run(create_dashboard_query_task(
        "dash_a",
        PanelBatchExecuteRequest(
            panel_ids=["panel_a"],
            panel_member_datasource_ids={"panel_a": "phy_b"},
        ),
    ))
    data = _payload(resp)
    assert data["status"] == "success"
    assert data["data"]["task_id"] == "dash_task_001"
    assert captured["kwargs"]["task_type"] == "dashboard_batch"
    assert captured["kwargs"]["request_payload"]["dashboard_id"] == "dash_a"
    assert captured["kwargs"]["request_payload"]["panel_member_datasource_ids"]["panel_a"] == "phy_b"
    assert captured["task_id"] == "dash_task_001"


def test_build_execution_target_info_contains_selected_members(monkeypatch):
    from apps.chat_data_app import handlers

    manager = _FakeDatasourceManager()
    manager.datasources["ds_a"]["member_sources"] = [
        {"member_datasource_id": "phy_a", "datasource_name": "投诉举报1", "source_type": "mysql", "host": "127.0.0.1", "port": 3306, "database": "db_a"},
        {"member_datasource_id": "phy_b", "datasource_name": "投诉举报2", "source_type": "mysql", "host": "127.0.0.2", "port": 3306, "database": "db_b"},
    ]
    monkeypatch.setattr(handlers, "get_datasource_manager", lambda: manager)

    info = _build_execution_target_info("ds_a", ["phy_a", "phy_b"])

    assert "当前执行对象" in info
    assert "投诉举报1" in info
    assert "投诉举报2" in info


def test_quick_sql_endpoint_formats_prompt_with_execution_target_info(monkeypatch):
    from apps.chat_data_app import routes

    class _FakeChunk:
        def __init__(self, content: str):
            self.content = content

    class _FakeLLM:
        def __init__(self):
            self.messages = None

        async def astream(self, messages):
            self.messages = messages
            yield _FakeChunk("```sql\nSELECT 1\n```")

    fake_llm = _FakeLLM()
    manager = _FakeDatasourceManager()
    manager.datasources["ds_a"]["member_sources"] = [
        {"member_datasource_id": "phy_a", "datasource_name": "成员A", "source_type": "mysql", "host": "127.0.0.1", "port": 3306, "database": "db_a"},
        {"member_datasource_id": "phy_b", "datasource_name": "成员B", "source_type": "mysql", "host": "127.0.0.2", "port": 3306, "database": "db_b"},
    ]

    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(routes, "_build_execution_target_info", lambda _datasource_id, _member_ids: "## 当前执行对象\n1. 成员B")
    monkeypatch.setattr(routes, "get_all_tables", lambda _datasource_id: [{"table_name": "users", "description": "用户表", "columns": []}])
    monkeypatch.setattr(routes, "get_tables_create_format", lambda _tables, _table_names: "CREATE TABLE users(id bigint);")
    monkeypatch.setattr(routes, "get_chat_data_llm", lambda: fake_llm)

    response = asyncio.run(routes.quick_sql_endpoint(QuickSqlRequest(
        query="有多少个用户",
        datasource_id="ds_a",
        member_datasource_ids=["phy_b"],
    )))
    chunks = asyncio.run(_collect_stream_chunks(response))
    content = "".join(chunks)

    assert '"type": "complete"' in content
    assert "SELECT 1" in content
    assert fake_llm.messages is not None
    assert "当前执行对象" in fake_llm.messages[0].content
    assert "成员B" in fake_llm.messages[0].content


def test_update_datasource_meta_rejects_duplicate_name(monkeypatch):
    from apps.chat_data_app import routes

    manager = _FakeDatasourceManager()
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)

    resp = asyncio.run(update_datasource_meta("ds_a", DatasourceUpdateRequest(name="数据源B")))
    data = _payload(resp)
    assert data["status"] == "error"
    assert "名称已存在" in data["msg"]


def test_update_mapped_modules_persists(monkeypatch):
    from apps.chat_data_app import routes

    manager = _FakeDatasourceManager()
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)

    resp = asyncio.run(update_mapped_modules("ds_b", {"mapped_modules": ["执法检查", "投诉举报"]}))
    data = _payload(resp)
    assert data["status"] == "success"
    assert manager.datasources["ds_b"]["mapped_modules"] == ["执法检查", "投诉举报"]


def test_create_datasource_group_from_template_source(monkeypatch):
    from apps.chat_data_app import routes

    manager = _FakeDatasourceManager()
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(routes, "MySQLSyncService", _FakeMySQLSyncService)
    monkeypatch.setattr(routes, "resolve_mysql_credentials", lambda _: {"user": "tester", "password": "secret"})

    payload = DatasourceGroupCreateRequest(
        group_name="模板组",
        group_desc="描述",
        source_type="mysql",
        template_source={
            "datasource_name": "template_a",
            "query_executor_type": "direct",
            "host": "127.0.0.1",
            "port": 3306,
            "database": "db_template",
        },
        member_sources=[
            {
                "datasource_name": "member_a",
                "query_executor_type": "direct",
                "host": "127.0.0.2",
                "port": 3306,
                "database": "db_member",
            },
        ],
    )
    resp = asyncio.run(create_datasource_group(payload))
    data = _payload(resp)
    assert data["status"] == "success"
    assert data["data"]["source_config"]["database"] == "db_template"
    assert data["data"]["member_datasource_ids"] == ["phy_template", "phy_member_a"]


def test_update_datasource_group_config_updates_group_members(monkeypatch):
    from apps.chat_data_app import routes

    manager = _FakeDatasourceManager()
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(routes, "MySQLSyncService", _FakeMySQLSyncService)
    monkeypatch.setattr(routes, "resolve_mysql_credentials", lambda _: {"user": "tester", "password": "secret"})

    payload = DatasourceGroupUpdateRequest(
        group_name="更新后的组",
        group_desc="新描述",
        source_type="mysql",
        template_source={
            "datasource_name": "template_new",
            "query_executor_type": "direct",
            "host": "127.0.0.9",
            "port": 3306,
            "database": "db_template_new",
        },
        template_table_names=["orders"],
        member_sources=[
            {
                "datasource_name": "member_new",
                "query_executor_type": "direct",
                "host": "127.0.0.8",
                "port": 3306,
                "database": "db_member_new",
            },
        ],
    )
    resp = asyncio.run(update_datasource_group_config("ds_a", payload))
    data = _payload(resp)
    assert data["status"] == "success"
    assert data["data"]["ds_name"] == "更新后的组"
    assert data["data"]["source_config"]["database"] == "db_template_new"
    assert len(data["data"]["member_sources"]) == 2


def test_set_and_clear_default_datasource(monkeypatch):
    from apps.chat_data_app import routes

    manager = _FakeDatasourceManager()
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)

    set_resp = asyncio.run(set_default_datasource("ds_b"))
    set_data = _payload(set_resp)
    assert set_data["status"] == "success"
    assert manager.datasources["ds_b"]["is_default"] is True
    assert manager.datasources["ds_a"]["is_default"] is False

    clear_resp = asyncio.run(clear_default_datasource("ds_b"))
    clear_data = _payload(clear_resp)
    assert clear_data["status"] == "success"
    assert manager.datasources["ds_b"]["is_default"] is False


def test_refresh_single_datasource_updates_table_cache(monkeypatch):
    from apps.chat_data_app import routes

    manager = _FakeDatasourceManager()
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(routes, "resolve_mysql_credentials", lambda _cfg: {"user": "u", "password": "p"})
    monkeypatch.setattr(routes, "MySQLSyncService", _FakeMySQLSyncService)

    resp = asyncio.run(refresh_single_datasource("ds_a", DatasourceRefreshRequest(table_names=["orders"])))
    data = _payload(resp)
    assert data["status"] == "success"
    assert manager.last_update_call is not None
    assert manager.last_update_call["datasource_id"] == "ds_a"
    assert manager.last_update_call["source_config"]["table_names"] == ["orders"]
    assert manager.datasources["ds_a"]["ds_tables"][0]["table_name"] == "orders"


def test_delete_datasource_removes_only_current_group(monkeypatch):
    from apps.chat_data_app import routes

    manager = _FakeDatasourceManager()
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)

    resp = asyncio.run(delete_datasource("ds_b"))
    data = _payload(resp)
    assert data["status"] == "success"
    assert manager.deleted_ids == ["ds_b"]
    assert "ds_b" not in manager.datasources
