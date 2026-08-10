from utils.error_codes import ErrorCode
from apps.chat_data_app.services.sql_run import providers as query_providers, runtime as query_runtime


def _add_semantic_join_fixture(fake_datasource_manager):
    datasource = fake_datasource_manager.datasources["ds_a"]
    datasource["ds_tables"].append(
        {
            "table_name": "city_dim",
            "description": "城市维表",
            "columns": [
                {"name": "city_id", "type": "int"},
                {"name": "city_name", "type": "varchar"},
            ],
        }
    )
    config = datasource["semantic_configs"][0]
    config["metrics"] = [
        {"name": "销售额", "expression": "SUM(orders.amount)", "table": "orders"}
    ]
    config["dimensions"] = [
        {"name": "城市", "column": "city_name", "table": "city_dim", "logical_type": "text"},
    ]
    config["relationships"] = [
        {
            "left_table": "orders",
            "right_table": "city_dim",
            "join_type": "left",
            "join_conditions": [{"left_column": "city_id", "right_column": "city_id"}],
            "relationship_type": "many_to_one",
        }
    ]


def test_compile_semantic_builder_sql_derives_join_from_backend_config(client, auth_headers, fake_datasource_manager, monkeypatch):
    _add_semantic_join_fixture(fake_datasource_manager)
    monkeypatch.setattr(query_providers, "get_datasource_manager", lambda: fake_datasource_manager)
    monkeypatch.setattr("apps.chat_data_app.services.semantic.run.semantic_query_service.get_datasource_manager", lambda: fake_datasource_manager)

    response = client.post(
        "/api/chatdata/compile-semantic-builder-sql",
        json={
            "datasource_id": "ds_a",
            "semantic_config_id": "cfg_a",
            "semantic_builder_config": {
                "dimensions": [{"table": "city_dim", "column": "city_name", "alias": "城市"}],
                "aggregations": [{"func": "SUM", "table": "orders", "column": "amount", "alias": "销售额"}],
                "orderBy": [{"table": "orders", "column": "amount", "aggFunc": "SUM", "direction": "DESC"}],
                "limit": 10,
            },
        },
        headers=auth_headers,
    )

    payload = response.json()
    assert payload["status"] == "success"
    sql = payload["data"]["sql"]
    assert "FROM city_dim" in sql
    assert "LEFT JOIN orders ON city_dim.city_id = orders.city_id" in sql
    assert "SUM(orders.amount)" in sql
    assert "LIMIT 10" in sql
    assert payload["data"]["builder_config"]["joins"][0]["leftAlias"] == "city_dim"


def test_compile_semantic_builder_sql_rejects_frontend_join_payload(client, auth_headers, fake_datasource_manager, monkeypatch):
    monkeypatch.setattr(query_providers, "get_datasource_manager", lambda: fake_datasource_manager)
    monkeypatch.setattr("apps.chat_data_app.services.semantic.run.semantic_query_service.get_datasource_manager", lambda: fake_datasource_manager)

    response = client.post(
        "/api/chatdata/compile-semantic-builder-sql",
        json={
            "datasource_id": "ds_a",
            "semantic_config_id": "cfg_a",
            "semantic_builder_config": {
                "mainTable": "orders",
                "joins": [{"rightTable": "danger"}],
                "dimensions": [{"table": "orders", "column": "city", "alias": "城市"}],
            },
        },
        headers=auth_headers,
    )

    payload = response.json()
    assert payload["status"] == "error"
    assert payload["code"] == ErrorCode.CHATDATA_BAD_REQUEST
    assert "不允许包含前端表关系字段" in payload["msg"]


def test_compile_semantic_builder_sql_uses_semantic_metric_name_for_ambiguous_columns(client, auth_headers, fake_datasource_manager, monkeypatch):
    config = fake_datasource_manager.datasources["ds_a"]["semantic_configs"][0]
    config["metrics"] = [
        {"name": "销售额", "expression": "SUM(orders.amount)", "table": "orders"},
        {"name": "退款额", "expression": "SUM(orders.refund_amount)", "table": "orders", "field_name": "amount"},
    ]
    config["dimensions"] = [
        {"name": "城市", "column": "city", "table": "orders", "logical_type": "text"},
    ]
    monkeypatch.setattr(query_providers, "get_datasource_manager", lambda: fake_datasource_manager)
    monkeypatch.setattr("apps.chat_data_app.services.semantic.run.semantic_query_service.get_datasource_manager", lambda: fake_datasource_manager)

    response = client.post(
        "/api/chatdata/compile-semantic-builder-sql",
        json={
            "datasource_id": "ds_a",
            "semantic_config_id": "cfg_a",
            "semantic_builder_config": {
                "dimensions": [{"semanticName": "城市", "table": "orders", "column": "city"}],
                "aggregations": [{"semanticName": "退款额", "func": "SUM", "table": "orders", "column": "amount"}],
                "filters": [{"semanticName": "退款额", "aggFunc": "SUM", "table": "orders", "column": "amount", "operator": ">", "value": 0}],
                "orderBy": [{"semanticName": "退款额", "aggFunc": "SUM", "table": "orders", "column": "amount", "direction": "DESC"}],
            },
        },
        headers=auth_headers,
    )

    payload = response.json()
    assert payload["status"] == "success"
    sql = payload["data"]["sql"]
    assert "SUM(refund_amount) AS `退款额`" in sql
    assert "HAVING SUM(refund_amount) > 0" in sql
    assert "ORDER BY SUM(refund_amount) DESC" in sql
    assert "SUM(amount) AS `退款额`" not in sql


def test_compile_semantic_builder_sql_does_not_reuse_selected_metric_for_named_having_order(client, auth_headers, fake_datasource_manager, monkeypatch):
    config = fake_datasource_manager.datasources["ds_a"]["semantic_configs"][0]
    config["metrics"] = [
        {"name": "销售额", "expression": "SUM(orders.amount)", "table": "orders"},
        {"name": "销售额含税", "expression": "SUM(orders.amount * 1.06)", "table": "orders", "field_name": "amount"},
    ]
    config["dimensions"] = [
        {"name": "城市", "column": "city", "table": "orders", "logical_type": "text"},
    ]
    monkeypatch.setattr(query_providers, "get_datasource_manager", lambda: fake_datasource_manager)
    monkeypatch.setattr("apps.chat_data_app.services.semantic.run.semantic_query_service.get_datasource_manager", lambda: fake_datasource_manager)

    response = client.post(
        "/api/chatdata/compile-semantic-builder-sql",
        json={
            "datasource_id": "ds_a",
            "semantic_config_id": "cfg_a",
            "semantic_builder_config": {
                "dimensions": [{"semanticName": "城市", "table": "orders", "column": "city"}],
                "aggregations": [{"semanticName": "销售额", "func": "SUM", "table": "orders", "column": "amount"}],
                "filters": [{"semanticName": "销售额含税", "aggFunc": "SUM", "table": "orders", "column": "amount", "operator": ">", "value": 100}],
                "orderBy": [{"semanticName": "销售额含税", "aggFunc": "SUM", "table": "orders", "column": "amount", "direction": "DESC"}],
            },
        },
        headers=auth_headers,
    )

    payload = response.json()
    assert payload["status"] == "success"
    sql = payload["data"]["sql"]
    assert "SUM(amount) AS `销售额`" in sql
    assert "HAVING SUM(orders.amount * 1.06) > 100" in sql
    assert "ORDER BY SUM(orders.amount * 1.06) DESC" in sql
    assert "HAVING SUM(amount) > 100" not in sql


def test_compile_semantic_builder_sql_keeps_metric_expression_for_having_and_order_only(client, auth_headers, fake_datasource_manager, monkeypatch):
    datasource = fake_datasource_manager.datasources["ds_a"]
    datasource["ds_tables"].extend([
        {
            "table_name": "order_items",
            "description": "订单明细",
            "columns": [
                {"name": "product_id", "type": "int"},
                {"name": "quantity", "type": "int"},
                {"name": "unit_price", "type": "decimal"},
            ],
        },
        {
            "table_name": "products",
            "description": "商品",
            "columns": [
                {"name": "product_id", "type": "int"},
                {"name": "product_name", "type": "varchar"},
            ],
        },
    ])
    config = datasource["semantic_configs"][0]
    config["metrics"] = [
        {"name": "明细销售额", "expression": "SUM(order_items.quantity * order_items.unit_price)", "table": "order_items"}
    ]
    config["dimensions"] = [
        {"name": "商品名称", "column": "product_name", "table": "products", "logical_type": "text"},
    ]
    config["relationships"] = [
        {
            "left_table": "order_items",
            "right_table": "products",
            "join_type": "left",
            "join_conditions": [{"left_column": "product_id", "right_column": "product_id"}],
            "relationship_type": "many_to_one",
        }
    ]
    monkeypatch.setattr(query_providers, "get_datasource_manager", lambda: fake_datasource_manager)
    monkeypatch.setattr("apps.chat_data_app.services.semantic.run.semantic_query_service.get_datasource_manager", lambda: fake_datasource_manager)

    response = client.post(
        "/api/chatdata/compile-semantic-builder-sql",
        json={
            "datasource_id": "ds_a",
            "semantic_config_id": "cfg_a",
            "semantic_builder_config": {
                "dimensions": [{"semanticName": "商品名称", "table": "products", "column": "product_name"}],
                "filters": [{"semanticName": "明细销售额", "aggFunc": "SUM", "table": "order_items", "column": "unit_price", "operator": ">", "value": 100}],
                "orderBy": [{"semanticName": "明细销售额", "aggFunc": "SUM", "table": "order_items", "column": "unit_price", "direction": "DESC"}],
            },
        },
        headers=auth_headers,
    )

    payload = response.json()
    assert payload["status"] == "success"
    sql = payload["data"]["sql"]
    assert "HAVING SUM(order_items.quantity * order_items.unit_price) > 100" in sql
    assert "GROUP BY products.`product_name`" in sql
    assert "ORDER BY SUM(order_items.quantity * order_items.unit_price) DESC" in sql
    assert "SUM(order_items.`unit_price`)" not in sql


def test_compile_semantic_builder_sql_accepts_structured_metric_having(client, auth_headers, fake_datasource_manager, monkeypatch):
    datasource = fake_datasource_manager.datasources["ds_a"]
    datasource["ds_tables"].append(
        {
            "table_name": "order_items",
            "description": "订单明细",
            "columns": [
                {"name": "order_id", "type": "int"},
                {"name": "amount", "type": "decimal"},
            ],
        }
    )
    config = datasource["semantic_configs"][0]
    config["metrics"] = [
        {"name": "出金总额", "expression": "SUM(orders.out_amount)", "table": "orders"},
        {"name": "销售额", "expression": "SUM(order_items.amount)", "table": "order_items"},
    ]
    config["dimensions"] = [
        {"name": "订单状态", "column": "status", "table": "orders", "logical_type": "text"},
    ]
    config["relationships"] = [
        {
            "left_table": "orders",
            "right_table": "order_items",
            "join_type": "left",
            "join_conditions": [{"left_column": "order_id", "right_column": "order_id"}],
            "relationship_type": "one_to_many",
        }
    ]
    monkeypatch.setattr(query_providers, "get_datasource_manager", lambda: fake_datasource_manager)
    monkeypatch.setattr("apps.chat_data_app.services.semantic.run.semantic_query_service.get_datasource_manager", lambda: fake_datasource_manager)

    response = client.post(
        "/api/chatdata/compile-semantic-builder-sql",
        json={
            "datasource_id": "ds_a",
            "semantic_config_id": "cfg_a",
            "semantic_builder_config": {
                "dimensions": [{"semanticName": "订单状态"}],
                "aggregations": [{"semanticName": "出金总额"}, {"semanticName": "销售额"}],
                "filters": [
                    {
                        "aggFunc": "SUM",
                        "left": {"type": "metric", "name": "出金总额"},
                        "operator": ">",
                        "right": {
                            "type": "binary",
                            "operator": "*",
                            "left": {"type": "metric", "name": "销售额"},
                            "right": {"type": "literal", "value": 10},
                        },
                    }
                ],
            },
        },
        headers=auth_headers,
    )

    payload = response.json()
    assert payload["status"] == "success"
    sql = payload["data"]["sql"]
    assert "LEFT JOIN order_items" in sql
    assert "HAVING SUM(orders.out_amount) > (SUM(order_items.amount) * 10)" in sql


def test_compile_semantic_builder_sql_normalizes_time_filter_to_range(client, auth_headers, fake_datasource_manager, monkeypatch):
    config = fake_datasource_manager.datasources["ds_a"]["semantic_configs"][0]
    config["metrics"] = [
        {"name": "订单数", "expression": "COUNT(DISTINCT orders.order_id)", "table": "orders"},
    ]
    config["dimensions"] = [
        {"name": "订单状态", "column": "status", "table": "orders", "logical_type": "text"},
        {"name": "下单时间", "column": "order_time", "table": "orders", "logical_type": "datetime"},
    ]
    monkeypatch.setattr(query_providers, "get_datasource_manager", lambda: fake_datasource_manager)
    monkeypatch.setattr("apps.chat_data_app.services.semantic.run.semantic_query_service.get_datasource_manager", lambda: fake_datasource_manager)

    response = client.post(
        "/api/chatdata/compile-semantic-builder-sql",
        json={
            "datasource_id": "ds_a",
            "semantic_config_id": "cfg_a",
            "semantic_builder_config": {
                "dimensions": [{"semanticName": "订单状态"}],
                "aggregations": [{"semanticName": "订单数"}],
                "filters": [{"semanticName": "下单时间", "operator": "=", "value": "2024"}],
            },
        },
        headers=auth_headers,
    )

    payload = response.json()
    assert payload["status"] == "success"
    sql = payload["data"]["sql"]
    assert "`order_time` >= '2024-01-01 00:00:00'" in sql
    assert "`order_time` < '2025-01-01 00:00:00'" in sql
    assert "`order_time` = 2024" not in sql


def test_compile_semantic_builder_sql_rejects_ambiguous_metric_without_semantic_name(client, auth_headers, fake_datasource_manager, monkeypatch):
    config = fake_datasource_manager.datasources["ds_a"]["semantic_configs"][0]
    config["metrics"] = [
        {"name": "销售额", "expression": "SUM(orders.amount)", "table": "orders"},
        {"name": "支付额", "expression": "SUM(orders.amount)", "table": "orders"},
    ]
    monkeypatch.setattr(query_providers, "get_datasource_manager", lambda: fake_datasource_manager)
    monkeypatch.setattr("apps.chat_data_app.services.semantic.run.semantic_query_service.get_datasource_manager", lambda: fake_datasource_manager)

    response = client.post(
        "/api/chatdata/compile-semantic-builder-sql",
        json={
            "datasource_id": "ds_a",
            "semantic_config_id": "cfg_a",
            "semantic_builder_config": {
                "aggregations": [{"func": "SUM", "table": "orders", "column": "amount"}],
            },
        },
        headers=auth_headers,
    )

    payload = response.json()
    assert payload["status"] == "error"
    assert payload["code"] == ErrorCode.CHATDATA_BAD_REQUEST
    assert "匹配到多个语义指标" in payload["msg"]


def test_compile_semantic_builder_sql_requires_auth(client, fake_datasource_manager, monkeypatch):
    monkeypatch.setattr(query_providers, "get_datasource_manager", lambda: fake_datasource_manager)
    monkeypatch.setattr("apps.chat_data_app.services.semantic.run.semantic_query_service.get_datasource_manager", lambda: fake_datasource_manager)

    response = client.post(
        "/api/chatdata/compile-semantic-builder-sql",
        json={
            "datasource_id": "ds_a",
            "semantic_config_id": "cfg_a",
            "semantic_builder_config": {"dimensions": [{"semanticName": "城市"}]},
        },
    )

    payload = response.json()
    assert payload["status"] == "error"
    assert payload["code"] == ErrorCode.CHATDATA_AUTHENTICATION_FAILED


def test_compile_semantic_builder_sql_rejects_other_users_private_config(client, auth_headers, fake_datasource_manager, monkeypatch):
    monkeypatch.setattr(query_providers, "get_datasource_manager", lambda: fake_datasource_manager)
    monkeypatch.setattr("apps.chat_data_app.services.semantic.run.semantic_query_service.get_datasource_manager", lambda: fake_datasource_manager)
    other_user_headers = {**auth_headers, "X-User-Id": "other_user"}

    response = client.post(
        "/api/chatdata/compile-semantic-builder-sql",
        json={
            "datasource_id": "ds_a",
            "semantic_config_id": "cfg_a",
            "semantic_builder_config": {"dimensions": [{"semanticName": "城市"}]},
        },
        headers=other_user_headers,
    )

    payload = response.json()
    assert payload["status"] == "error"
    assert payload["code"] == ErrorCode.CHATDATA_NOT_FOUND


def test_create_query_task_recompiles_semantic_builder_sql(client, auth_headers, fake_datasource_manager, sample_query_task, monkeypatch):
    _add_semantic_join_fixture(fake_datasource_manager)
    captured = {}

    async def fake_create_query_task(body):
        captured["body"] = body
        return sample_query_task

    monkeypatch.setattr(query_providers, "get_datasource_manager", lambda: fake_datasource_manager)
    monkeypatch.setattr("apps.chat_data_app.services.semantic.run.semantic_query_service.get_datasource_manager", lambda: fake_datasource_manager)
    monkeypatch.setattr(query_runtime, "create_query_task", fake_create_query_task)
    monkeypatch.setattr(query_providers.query_task_service, "schedule_query_task", lambda task_id: captured.setdefault("scheduled", task_id))

    response = client.post(
        "/api/chatdata/query-tasks",
        json={
            "datasource_id": "ds_a",
            "query_type": "builder",
            "semantic_config_id": "cfg_a",
            "builder_config": {"dimensions": [{"table": "city_dim", "column": "city_name", "alias": "城市"}]},
            "semantic_builder_config": {
                "dimensions": [{"table": "city_dim", "column": "city_name", "alias": "城市"}],
                "aggregations": [{"func": "SUM", "table": "orders", "column": "amount", "alias": "销售额"}],
            },
        },
        headers=auth_headers,
    )

    payload = response.json()
    assert payload["status"] == "success"
    assert "LEFT JOIN orders ON city_dim.city_id = orders.city_id" in captured["body"].sql[0]
    assert captured["body"].builder_config is None
    assert captured["body"].semantic_builder_config["aggregations"][0]["alias"] == "销售额"
    assert captured["scheduled"] == "task_a"


def test_create_query_task_api_contract(client, auth_headers, fake_datasource_manager, sample_query_task, monkeypatch):
    captured = {}

    async def fake_create_query_task(body):
        captured["body"] = body
        return sample_query_task

    async def fake_prepare_sql_request_for_execution(body, datasource=None):
        return body

    monkeypatch.setattr(query_providers, "get_datasource_manager", lambda: fake_datasource_manager)
    monkeypatch.setattr(query_runtime, "prepare_sql_request_for_execution", fake_prepare_sql_request_for_execution)
    monkeypatch.setattr(query_runtime, "create_query_task", fake_create_query_task)
    monkeypatch.setattr(query_providers.query_task_service, "schedule_query_task", lambda task_id: captured.setdefault("scheduled", task_id))

    response = client.post(
        "/api/chatdata/query-tasks",
        json={
            "datasource_id": "ds_a",
            "sql": ["select * from orders"],
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


def test_create_query_task_rejects_internal_agent_type(client, auth_headers):
    response = client.post(
        "/api/chatdata/query-tasks",
        json={
            "datasource_id": "ds_a",
            "sql": ["select 1"],
            "query_type": "agent",
            "conversation_id": "foreign-conversation",
        },
        headers=auth_headers,
    )

    assert response.status_code == 422


def test_create_query_task_returns_datasource_not_found(client, auth_headers, fake_datasource_manager, monkeypatch):
    monkeypatch.setattr(query_providers, "get_datasource_manager", lambda: fake_datasource_manager)

    response = client.post(
        "/api/chatdata/query-tasks",
        json={"datasource_id": "missing_ds", "sql": ["select 1"]},
        headers=auth_headers,
    )

    payload = response.json()
    assert payload["status"] == "error"
    assert payload["code"] == ErrorCode.CHATDATA_NOT_FOUND
    assert "数据源不存在" in payload["msg"]


def test_create_query_task_returns_sql_prepare_error(client, auth_headers, fake_datasource_manager, monkeypatch):
    async def fake_prepare_sql_request_for_execution(body, datasource=None):
        raise ValueError("一次只能执行一条 SQL，请拆分后分别执行")

    monkeypatch.setattr(query_providers, "get_datasource_manager", lambda: fake_datasource_manager)
    monkeypatch.setattr(query_runtime, "prepare_sql_request_for_execution", fake_prepare_sql_request_for_execution)

    response = client.post(
        "/api/chatdata/query-tasks",
        json={"datasource_id": "ds_a", "sql": ["select 1; select 2"]},
        headers=auth_headers,
    )

    payload = response.json()
    assert payload["status"] == "error"
    assert payload["code"] == ErrorCode.CHATDATA_BAD_REQUEST
    assert "一次只能执行一条 SQL" in payload["msg"]


def test_create_query_task_rejects_dangerous_sql_before_scheduling(client, auth_headers, fake_datasource_manager, monkeypatch):
    captured = {"scheduled": False}
    monkeypatch.setattr(query_providers, "get_datasource_manager", lambda: fake_datasource_manager)
    monkeypatch.setattr(query_providers.query_task_service, "schedule_query_task", lambda _task_id: captured.update({"scheduled": True}))

    response = client.post(
        "/api/chatdata/query-tasks",
        json={"datasource_id": "ds_a", "sql": ["select 1; drop table orders"]},
        headers=auth_headers,
    )

    payload = response.json()
    assert payload["status"] == "error"
    assert payload["code"] == ErrorCode.CHATDATA_BAD_REQUEST
    assert "一次只能执行一条 SQL" in payload["msg"]
    assert captured["scheduled"] is False


def test_get_query_task_api_contract(client, auth_headers, sample_query_task, monkeypatch):
    async def fake_get_query_task(task_id):
        return sample_query_task if task_id == "task_a" else None

    async def fake_get_query_executions(task_id):
        return [{
            "execution_id": "exec_1",
            "task_id": task_id,
            "datasource_id": "ds_a",
            "member_datasource_id": "m1",
            "member_name": "杭州库",
            "executor_type": "http",
            "sql_status": "running",
            "remote_task_id": "remote_1",
            "db_connection_id": "",
            "query": "select * from orders",
            "result_payload": {"columns": ["id"], "data": [{"id": 1}]},
            "error_message": None,
            "execution_time_ms": 123,
            "result_row_count": 1,
            "started_time": None,
            "finished_time": None,
            "operator_name": "测试用户",
        }]

    monkeypatch.setattr(query_runtime, "get_query_task", fake_get_query_task)
    monkeypatch.setattr(query_runtime, "get_query_executions", fake_get_query_executions)

    response = client.get("/api/chatdata/query-tasks/task_a", headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["task_id"] == "task_a"
    assert payload["data"]["task_type"] == "sql"
    assert payload["data"]["datasource_id"] == "ds_a"
    assert "result" not in payload["data"]
    assert "executions" not in payload["data"]
    assert "success" not in payload["data"]
    assert "columns" not in payload["data"]
    assert "data" not in payload["data"]
    assert payload["data"]["results"] == [{
        "execution_id": "exec_1",
        "member_datasource_id": "m1",
        "member_name": "杭州库",
        "executor_type": "http",
        "status": "running",
        "sql": "select * from orders",
        "remote_task_id": "remote_1",
        "db_connection_id": "",
        "columns": ["id"],
        "data": [{"id": 1}],
        "total_count": 1,
        "execution_time_ms": 123,
        "error": None,
        "started_time": None,
        "finished_time": None,
    }]


def test_get_query_task_returns_not_found(client, auth_headers, monkeypatch):
    async def fake_get_query_task(task_id):
        return None

    monkeypatch.setattr(query_runtime, "get_query_task", fake_get_query_task)

    response = client.get("/api/chatdata/query-tasks/missing_task", headers=auth_headers)

    payload = response.json()
    assert payload["status"] == "error"
    assert payload["code"] == ErrorCode.CHATDATA_NOT_FOUND
    assert "任务不存在" in payload["msg"]


def test_get_query_task_requires_auth(client, monkeypatch):
    async def fake_get_query_task(_task_id):
        raise AssertionError("未认证时不应查询任务")

    monkeypatch.setattr(query_runtime, "get_query_task", fake_get_query_task)

    response = client.get("/api/chatdata/query-tasks/task_a")

    payload = response.json()
    assert payload["status"] == "error"
    assert payload["code"] == ErrorCode.CHATDATA_AUTHENTICATION_FAILED


def test_get_query_task_rejects_other_user(client, auth_headers, monkeypatch):
    async def fake_get_query_task(task_id):
        return {
            "task_id": task_id,
            "task_type": "sql",
            "datasource_id": "ds_a",
            "query_type": "sql",
            "status": "succeeded",
            "progress_text": "执行完成",
            "request_payload": '{"sql":"select * from orders","user_id":"user_b"}',
            "result_payload": '{"success":true,"results":[{"data":[{"id":1}]}]}',
            "remote_task_payload": None,
            "error_message": None,
            "started_time": None,
            "finished_time": None,
            "create_time": None,
            "update_time": None,
        }

    async def fake_get_query_executions(task_id):
        return []

    monkeypatch.setattr(query_runtime, "get_query_task", fake_get_query_task)
    monkeypatch.setattr(query_runtime, "get_query_executions", fake_get_query_executions)

    response = client.get("/api/chatdata/query-tasks/task_b", headers=auth_headers)

    payload = response.json()
    assert payload["status"] == "error"
    assert payload["code"] == ErrorCode.CHATDATA_NOT_FOUND
    assert "任务不存在" in payload["msg"]


def test_get_query_task_merges_live_remote_sql_result(client, auth_headers, monkeypatch):
    async def fake_get_query_task(task_id):
        return {
            "task_id": task_id,
            "task_type": "sql",
            "datasource_id": "ds_a",
            "query_type": "sql",
            "status": "running",
            "progress_text": "SQL 执行中",
            "request_payload": '{"sql":"select * from orders","user_id":"user_a"}',
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

    monkeypatch.setattr(query_runtime, "get_query_task", fake_get_query_task)
    monkeypatch.setattr(query_runtime, "get_query_executions", fake_get_query_executions)
    monkeypatch.setattr(query_providers.query_task_service, "build_live_remote_sql_result", fake_build_live_remote_sql_result)

    response = client.get("/api/chatdata/query-tasks/task_remote", headers=auth_headers)

    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["status"] == "succeeded"
    assert "result" not in payload["data"]
    assert "executions" not in payload["data"]
    assert "success" not in payload["data"]
    assert "columns" not in payload["data"]
    assert "data" not in payload["data"]
    assert payload["data"]["results"] == [{
        "member_datasource_id": "m1",
        "status": "succeeded",
        "sql": "select * from orders",
        "columns": [],
        "data": [],
        "total_count": 0,
        "error": None,
    }]
    assert payload["data"]["remote_tasks"][0]["remote_task_id"] == "remote_1"
