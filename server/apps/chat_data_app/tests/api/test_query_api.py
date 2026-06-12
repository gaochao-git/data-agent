from apps.chat_data_app import routes


def test_compile_builder_sql_api_contract(client, fake_datasource_manager, monkeypatch):
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: fake_datasource_manager)
    monkeypatch.setattr(routes, "compile_builder_sql", lambda builder_config, dialect="mysql": "select city, sum(amount) from orders group by city")

    response = client.post(
        "/api/chatdata/compile-builder-sql",
        json={
            "datasource_id": "ds_a",
            "builder_config": {
                "metrics": [{"name": "销售额", "expression": "SUM(amount)", "table": "orders"}],
                "dimensions": [{"name": "城市", "column": "city", "table": "orders"}],
                "limit": 10,
                "offset": 0,
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["sql"].startswith("select city")


def test_compile_builder_sql_uses_datasource_dialect(client, fake_datasource_manager, monkeypatch):
    captured = {}

    def fake_compile_builder_sql(builder_config, dialect="mysql"):
        captured["builder_config"] = builder_config
        captured["dialect"] = dialect
        return "select * from orders"

    fake_datasource_manager.datasources["ds_a"]["source_type"] = "hive"
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: fake_datasource_manager)
    monkeypatch.setattr(routes, "compile_builder_sql", fake_compile_builder_sql)

    response = client.post(
        "/api/chatdata/compile-builder-sql",
        json={
            "datasource_id": "ds_a",
            "builder_config": {
                "mainTable": "orders",
                "dimensions": [{"table": "orders", "column": "id"}],
            },
        },
    )

    payload = response.json()
    assert payload["status"] == "success"
    assert captured["dialect"] == "hive"
    assert captured["builder_config"]["mainTable"] == "orders"


def test_compile_builder_sql_returns_datasource_not_found(client, fake_datasource_manager, monkeypatch):
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: fake_datasource_manager)

    response = client.post(
        "/api/chatdata/compile-builder-sql",
        json={"datasource_id": "missing_ds", "builder_config": {"mainTable": "orders"}},
    )

    payload = response.json()
    assert payload["status"] == "error"
    assert payload["code"] == 40404
    assert "数据源不存在" in payload["msg"]


def test_compile_builder_sql_returns_value_error(client, monkeypatch):
    monkeypatch.setattr(routes, "compile_builder_sql", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("缺少主表")))

    response = client.post(
        "/api/chatdata/compile-builder-sql",
        json={"builder_config": {"dimensions": []}},
    )

    payload = response.json()
    assert payload["status"] == "error"
    assert payload["code"] == 40001
    assert payload["msg"] == "缺少主表"


def test_create_query_task_api_contract(client, auth_headers, fake_datasource_manager, sample_query_task, monkeypatch):
    captured = {}

    async def fake_create_query_task(body):
        captured["body"] = body
        return sample_query_task

    async def fake_prepare_sql_request_for_execution(body, datasource=None):
        return body

    monkeypatch.setattr(routes, "get_datasource_manager", lambda: fake_datasource_manager)
    monkeypatch.setattr(routes, "_prepare_sql_request_for_execution", fake_prepare_sql_request_for_execution)
    monkeypatch.setattr(routes, "_create_query_task", fake_create_query_task)
    monkeypatch.setattr(routes, "_schedule_query_task", lambda task_id: captured.setdefault("scheduled", task_id))

    response = client.post(
        "/api/chatdata/query-tasks",
        json={
            "task_type": "sql",
            "datasource_id": "ds_a",
            "sql": "select * from orders",
            "query_type": "sql",
            "limit": 10,
            "skip_query_cache": True,
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["task_id"] == "task_a"
    assert payload["data"]["status"] == "pending"
    assert payload["data"]["poll_interval_ms"] > 0
    assert captured["body"].user_id == "user_a"
    assert captured["body"].operator_name == "测试用户"
    assert captured["body"].skip_query_cache is True
    assert captured["scheduled"] == "task_a"


def test_create_query_task_returns_datasource_not_found(client, auth_headers, fake_datasource_manager, monkeypatch):
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: fake_datasource_manager)

    response = client.post(
        "/api/chatdata/query-tasks",
        json={"task_type": "sql", "datasource_id": "missing_ds", "sql": "select 1"},
        headers=auth_headers,
    )

    payload = response.json()
    assert payload["status"] == "error"
    assert payload["code"] == 40404
    assert "数据源不存在" in payload["msg"]


def test_create_query_task_returns_sql_prepare_error(client, auth_headers, fake_datasource_manager, monkeypatch):
    async def fake_prepare_sql_request_for_execution(body, datasource=None):
        raise ValueError("一次只能执行一条 SQL，请拆分后分别执行")

    monkeypatch.setattr(routes, "get_datasource_manager", lambda: fake_datasource_manager)
    monkeypatch.setattr(routes, "_prepare_sql_request_for_execution", fake_prepare_sql_request_for_execution)

    response = client.post(
        "/api/chatdata/query-tasks",
        json={"task_type": "sql", "datasource_id": "ds_a", "sql": "select 1; select 2"},
        headers=auth_headers,
    )

    payload = response.json()
    assert payload["status"] == "error"
    assert payload["code"] == 40001
    assert "一次只能执行一条 SQL" in payload["msg"]


def test_get_query_task_api_contract(client, sample_query_task, monkeypatch):
    async def fake_get_query_task(task_id):
        return sample_query_task if task_id == "task_a" else None

    async def fake_get_query_executions(task_id):
        return []

    monkeypatch.setattr(routes, "_get_query_task", fake_get_query_task)
    monkeypatch.setattr(routes, "_get_query_executions", fake_get_query_executions)

    response = client.get("/api/chatdata/query-tasks/task_a")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["task_id"] == "task_a"
    assert payload["data"]["task_type"] == "sql"
    assert payload["data"]["datasource_id"] == "ds_a"
    assert payload["data"]["results"] is None
    assert payload["data"]["executions"] == []


def test_get_query_task_returns_not_found(client, monkeypatch):
    async def fake_get_query_task(task_id):
        return None

    monkeypatch.setattr(routes, "_get_query_task", fake_get_query_task)

    response = client.get("/api/chatdata/query-tasks/missing_task")

    payload = response.json()
    assert payload["status"] == "error"
    assert payload["code"] == 40404
    assert "任务不存在" in payload["msg"]


def test_get_query_task_merges_live_remote_sql_result(client, monkeypatch):
    async def fake_get_query_task(task_id):
        return {
            "task_id": task_id,
            "task_type": "sql",
            "datasource_id": "ds_a",
            "query_type": "sql",
            "status": "running",
            "progress_text": "SQL 执行中",
            "request_payload": '{"sql":"select * from orders"}',
            "result_payload": None,
            "remote_task_payload": '[{"member_datasource_id":"m1","member_name":"杭州库","remote_task_id":"remote_1","source_type":"hive"}]',
            "error_message": None,
            "started_time": None,
            "finished_time": None,
            "create_time": None,
            "update_time": None,
        }

    async def fake_get_query_executions(task_id):
        return []

    async def fake_build_live_remote_sql_result(response_data, executions=None):
        return {
            "status": "succeeded",
            "progress_text": "执行完成",
            "error_message": None,
            "result_payload": {
                "success": True,
                "sql": "select * from orders",
                "columns": ["id"],
                "data": [{"id": 1}],
                "total_count": 1,
                "results": [{"member_datasource_id": "m1", "success": True}],
            },
        }

    monkeypatch.setattr(routes, "_get_query_task", fake_get_query_task)
    monkeypatch.setattr(routes, "_get_query_executions", fake_get_query_executions)
    monkeypatch.setattr(routes, "_build_live_remote_sql_result", fake_build_live_remote_sql_result)

    response = client.get("/api/chatdata/query-tasks/task_remote")

    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["status"] == "succeeded"
    assert payload["data"]["results"] == [{"member_datasource_id": "m1", "success": True}]
    assert payload["data"]["remote_tasks"][0]["remote_task_id"] == "remote_1"


def test_execute_sql_api_contract(client, auth_headers, sample_query_task, monkeypatch):
    captured = {}
    result_data = {
        "success": True,
        "columns": ["id"],
        "data": [{"id": 1}],
        "total_count": 1,
        "results": [],
    }

    async def fake_create_task_for_execute_request(body, query_type=None, operator_name=None):
        captured["body"] = body
        captured["query_type"] = query_type
        captured["operator_name"] = operator_name
        return sample_query_task

    async def fake_update_query_task(task_id, **kwargs):
        captured.setdefault("updates", []).append({"task_id": task_id, **kwargs})
        return sample_query_task

    async def fake_prepare_sql_request_for_execution(body):
        return body

    monkeypatch.setattr(routes, "_prepare_sql_request_for_execution", fake_prepare_sql_request_for_execution)
    monkeypatch.setattr(routes, "_create_task_for_execute_request", fake_create_task_for_execute_request)
    async def fake_create_query_executions_for_task(task_id, body, operator_name=None):
        return {}

    monkeypatch.setattr(routes, "_create_query_executions_for_task", fake_create_query_executions_for_task)
    monkeypatch.setattr(routes, "_update_query_task", fake_update_query_task)
    async def fake_build_execute_sql_result_data(body, query_task_id=None, execution_map=None, username=None):
        return result_data

    monkeypatch.setattr(routes, "_build_execute_sql_result_data", fake_build_execute_sql_result_data)

    response = client.post(
        "/api/chatdata/execute-sql",
        json={
            "datasource_id": "ds_a",
            "sql": "select * from orders",
            "limit": 5,
            "skip_query_cache": True,
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["success"] is True
    assert payload["data"]["columns"] == ["id"]
    assert captured["body"].user_id == "user_a"
    assert captured["body"].skip_query_cache is True
    assert captured["operator_name"] == "测试用户"
    assert captured["updates"][-1]["status"] == "succeeded"


def test_execute_sql_returns_result_error_and_preserves_raw_message(client, auth_headers, sample_query_task, monkeypatch):
    captured = {}
    result_data = {
        "success": False,
        "sql": "SELECT * FROM missing_table LIMIT 5",
        "columns": [],
        "data": [],
        "total_count": 0,
        "results": [],
        "error": "Table 'demo.missing_table' doesn't exist",
    }

    async def fake_create_task_for_execute_request(body, query_type=None, operator_name=None):
        return sample_query_task

    async def fake_create_query_executions_for_task(task_id, body, operator_name=None):
        return {}

    async def fake_update_query_task(task_id, **kwargs):
        captured.setdefault("updates", []).append({"task_id": task_id, **kwargs})
        return sample_query_task

    async def fake_prepare_sql_request_for_execution(body):
        return body

    async def fake_build_execute_sql_result_data(body, query_task_id=None, execution_map=None, username=None):
        return result_data

    monkeypatch.setattr(routes, "_prepare_sql_request_for_execution", fake_prepare_sql_request_for_execution)
    monkeypatch.setattr(routes, "_create_task_for_execute_request", fake_create_task_for_execute_request)
    monkeypatch.setattr(routes, "_create_query_executions_for_task", fake_create_query_executions_for_task)
    monkeypatch.setattr(routes, "_update_query_task", fake_update_query_task)
    monkeypatch.setattr(routes, "_build_execute_sql_result_data", fake_build_execute_sql_result_data)

    response = client.post(
        "/api/chatdata/execute-sql",
        json={"datasource_id": "ds_a", "sql": "select * from missing_table", "limit": 5},
        headers=auth_headers,
    )

    payload = response.json()
    assert payload["status"] == "error"
    assert payload["code"] == 50010
    assert payload["msg"] == "Table 'demo.missing_table' doesn't exist"
    assert payload["data"]["error"] == "Table 'demo.missing_table' doesn't exist"
    assert captured["updates"][-1]["status"] == "failed"


def test_execute_sql_returns_prepare_error_without_creating_task(client, auth_headers, monkeypatch):
    captured = {"created": False}

    async def fake_prepare_sql_request_for_execution(body):
        raise ValueError("SQL 注释移除后为空，请输入有效 SQL")

    async def fake_create_task_for_execute_request(*_args, **_kwargs):
        captured["created"] = True
        return {}

    monkeypatch.setattr(routes, "_prepare_sql_request_for_execution", fake_prepare_sql_request_for_execution)
    monkeypatch.setattr(routes, "_create_task_for_execute_request", fake_create_task_for_execute_request)

    response = client.post(
        "/api/chatdata/execute-sql",
        json={"datasource_id": "ds_a", "sql": "-- only comment"},
        headers=auth_headers,
    )

    payload = response.json()
    assert payload["status"] == "error"
    assert payload["code"] == 40001
    assert "SQL 注释移除后为空" in payload["msg"]
    assert captured["created"] is False
