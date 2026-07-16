from utils.error_codes import ErrorCode
from apps.chat_data_app.services import query_cache, query_providers, query_runtime
from apps.chat_data_app.services.sql_executor import build_effective_sql, validate_sql


def _patch_execute_sql_runtime(monkeypatch):
    captured = {
        "created_tasks": [],
        "execution_records": [],
        "task_updates": [],
        "executor_calls": [],
        "closed_targets": [],
    }

    class FakeRuntimeDatasourceManager:
        async def get_datasource_async(self, datasource_id: str):
            if datasource_id != "ds_a":
                return None
            return {"id": datasource_id, "source_type": "mysql"}

        async def get_execution_datasources_async(self, datasource_id: str, member_datasource_ids=None):
            member_ids = member_datasource_ids or ["member_a"]
            return [
                {
                    "id": datasource_id,
                    "datasource_id": datasource_id,
                    "member_datasource_id": member_id,
                    "member_name": f"{member_id}库",
                    "source_type": "mysql",
                    "query_executor_type": "direct",
                }
                for member_id in member_ids
            ]

    class FakeExecutor:
        def __init__(self, execution_target):
            self.execution_target = execution_target

        async def execute(
            self,
            sql: str,
            limit: int = 100,
            timeout=None,
            user_id=None,
            username=None,
            remote_task_callback=None,
            db_connection_callback=None,
        ):
            captured["executor_calls"].append(
                {
                    "sql": sql,
                    "limit": limit,
                    "timeout": timeout,
                    "user_id": user_id,
                    "username": username,
                    "member_datasource_id": self.execution_target.get("member_datasource_id"),
                }
            )
            is_valid, error_msg = validate_sql(sql, source_type=self.execution_target.get("source_type"))
            if not is_valid:
                return {"success": False, "error": error_msg, "columns": [], "data": [], "total_count": 0}
            effective_sql = build_effective_sql(sql, limit, source_type=self.execution_target.get("source_type"))
            return {
                "success": True,
                "columns": ["member_datasource_id", "sql_text", "operator"],
                "data": [
                    {
                        "member_datasource_id": self.execution_target.get("member_datasource_id"),
                        "sql_text": effective_sql,
                        "operator": username,
                    }
                ],
                "total_count": 1,
                "execution_time_ms": 3,
            }

        async def close(self):
            captured["closed_targets"].append(self.execution_target.get("member_datasource_id"))

    async def fake_create_task_for_execute_request(body, query_type=None, operator_name=None):
        task_id = f"task_{len(captured['created_tasks']) + 1}"
        captured["created_tasks"].append(
            {
                "task_id": task_id,
                "body": body,
                "query_type": query_type,
                "operator_name": operator_name,
            }
        )
        return {"task_id": task_id}

    async def fake_create_query_executions_for_task(task_id, body, operator_name=None):
        captured["execution_records"].append(
            {
                "task_id": task_id,
                "body": body,
                "operator_name": operator_name,
            }
        )
        return {}

    async def fake_update_query_task(task_id, **kwargs):
        captured["task_updates"].append({"task_id": task_id, **kwargs})
        return {"task_id": task_id}

    monkeypatch.setattr(query_cache, "QUERY_CACHE_ENABLED", False)
    monkeypatch.setattr(query_runtime, "get_datasource_manager", lambda: FakeRuntimeDatasourceManager())
    monkeypatch.setattr(query_runtime, "get_executor", lambda execution_target: FakeExecutor(execution_target))
    monkeypatch.setattr(query_runtime, "create_task_for_execute_request", fake_create_task_for_execute_request)
    monkeypatch.setattr(query_runtime, "create_query_executions_for_task", fake_create_query_executions_for_task)
    monkeypatch.setattr(query_runtime, "update_query_task", fake_update_query_task)
    return captured


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


def test_compile_semantic_builder_sql_derives_join_from_backend_config(client, fake_datasource_manager, monkeypatch):
    _add_semantic_join_fixture(fake_datasource_manager)
    monkeypatch.setattr(query_providers, "get_datasource_manager", lambda: fake_datasource_manager)
    monkeypatch.setattr("apps.chat_data_app.services.semantic_builder_compiler.get_datasource_manager", lambda: fake_datasource_manager)

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
    )

    payload = response.json()
    assert payload["status"] == "success"
    sql = payload["data"]["sql"]
    assert "FROM city_dim" in sql
    assert "LEFT JOIN orders ON city_dim.city_id = orders.city_id" in sql
    assert "SUM(orders.amount)" in sql
    assert "LIMIT 10" in sql
    assert payload["data"]["builder_config"]["joins"][0]["leftAlias"] == "city_dim"


def test_compile_semantic_builder_sql_rejects_frontend_join_payload(client, fake_datasource_manager, monkeypatch):
    monkeypatch.setattr(query_providers, "get_datasource_manager", lambda: fake_datasource_manager)
    monkeypatch.setattr("apps.chat_data_app.services.semantic_builder_compiler.get_datasource_manager", lambda: fake_datasource_manager)

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
    )

    payload = response.json()
    assert payload["status"] == "error"
    assert payload["code"] == ErrorCode.CHATDATA_BAD_REQUEST
    assert "不允许包含前端表关系字段" in payload["msg"]


def test_compile_semantic_builder_sql_uses_semantic_metric_name_for_ambiguous_columns(client, fake_datasource_manager, monkeypatch):
    config = fake_datasource_manager.datasources["ds_a"]["semantic_configs"][0]
    config["metrics"] = [
        {"name": "销售额", "expression": "SUM(orders.amount)", "table": "orders"},
        {"name": "退款额", "expression": "SUM(orders.refund_amount)", "table": "orders", "field_name": "amount"},
    ]
    config["dimensions"] = [
        {"name": "城市", "column": "city", "table": "orders", "logical_type": "text"},
    ]
    monkeypatch.setattr(query_providers, "get_datasource_manager", lambda: fake_datasource_manager)
    monkeypatch.setattr("apps.chat_data_app.services.semantic_builder_compiler.get_datasource_manager", lambda: fake_datasource_manager)

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
    )

    payload = response.json()
    assert payload["status"] == "success"
    sql = payload["data"]["sql"]
    assert "SUM(refund_amount) AS `退款额`" in sql
    assert "HAVING SUM(refund_amount) > 0" in sql
    assert "ORDER BY SUM(refund_amount) DESC" in sql
    assert "SUM(amount) AS `退款额`" not in sql


def test_compile_semantic_builder_sql_does_not_reuse_selected_metric_for_named_having_order(client, fake_datasource_manager, monkeypatch):
    config = fake_datasource_manager.datasources["ds_a"]["semantic_configs"][0]
    config["metrics"] = [
        {"name": "销售额", "expression": "SUM(orders.amount)", "table": "orders"},
        {"name": "销售额含税", "expression": "SUM(orders.amount * 1.06)", "table": "orders", "field_name": "amount"},
    ]
    config["dimensions"] = [
        {"name": "城市", "column": "city", "table": "orders", "logical_type": "text"},
    ]
    monkeypatch.setattr(query_providers, "get_datasource_manager", lambda: fake_datasource_manager)
    monkeypatch.setattr("apps.chat_data_app.services.semantic_builder_compiler.get_datasource_manager", lambda: fake_datasource_manager)

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
    )

    payload = response.json()
    assert payload["status"] == "success"
    sql = payload["data"]["sql"]
    assert "SUM(amount) AS `销售额`" in sql
    assert "HAVING SUM(orders.amount * 1.06) > 100" in sql
    assert "ORDER BY SUM(orders.amount * 1.06) DESC" in sql
    assert "HAVING SUM(amount) > 100" not in sql


def test_compile_semantic_builder_sql_keeps_metric_expression_for_having_and_order_only(client, fake_datasource_manager, monkeypatch):
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
    monkeypatch.setattr("apps.chat_data_app.services.semantic_builder_compiler.get_datasource_manager", lambda: fake_datasource_manager)

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
    )

    payload = response.json()
    assert payload["status"] == "success"
    sql = payload["data"]["sql"]
    assert "HAVING SUM(order_items.quantity * order_items.unit_price) > 100" in sql
    assert "GROUP BY products.`product_name`" in sql
    assert "ORDER BY SUM(order_items.quantity * order_items.unit_price) DESC" in sql
    assert "SUM(order_items.`unit_price`)" not in sql


def test_compile_semantic_builder_sql_accepts_structured_metric_having(client, fake_datasource_manager, monkeypatch):
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
    monkeypatch.setattr("apps.chat_data_app.services.semantic_builder_compiler.get_datasource_manager", lambda: fake_datasource_manager)

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
    )

    payload = response.json()
    assert payload["status"] == "success"
    sql = payload["data"]["sql"]
    assert "LEFT JOIN order_items" in sql
    assert "HAVING SUM(orders.out_amount) > (SUM(order_items.amount) * 10)" in sql


def test_compile_semantic_builder_sql_normalizes_time_filter_to_range(client, fake_datasource_manager, monkeypatch):
    config = fake_datasource_manager.datasources["ds_a"]["semantic_configs"][0]
    config["metrics"] = [
        {"name": "订单数", "expression": "COUNT(DISTINCT orders.order_id)", "table": "orders"},
    ]
    config["dimensions"] = [
        {"name": "订单状态", "column": "status", "table": "orders", "logical_type": "text"},
        {"name": "下单时间", "column": "order_time", "table": "orders", "logical_type": "datetime"},
    ]
    monkeypatch.setattr(query_providers, "get_datasource_manager", lambda: fake_datasource_manager)
    monkeypatch.setattr("apps.chat_data_app.services.semantic_builder_compiler.get_datasource_manager", lambda: fake_datasource_manager)

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
    )

    payload = response.json()
    assert payload["status"] == "success"
    sql = payload["data"]["sql"]
    assert "`order_time` >= '2024-01-01 00:00:00'" in sql
    assert "`order_time` < '2025-01-01 00:00:00'" in sql
    assert "`order_time` = 2024" not in sql


def test_compile_semantic_builder_sql_rejects_ambiguous_metric_without_semantic_name(client, fake_datasource_manager, monkeypatch):
    config = fake_datasource_manager.datasources["ds_a"]["semantic_configs"][0]
    config["metrics"] = [
        {"name": "销售额", "expression": "SUM(orders.amount)", "table": "orders"},
        {"name": "支付额", "expression": "SUM(orders.amount)", "table": "orders"},
    ]
    monkeypatch.setattr(query_providers, "get_datasource_manager", lambda: fake_datasource_manager)
    monkeypatch.setattr("apps.chat_data_app.services.semantic_builder_compiler.get_datasource_manager", lambda: fake_datasource_manager)

    response = client.post(
        "/api/chatdata/compile-semantic-builder-sql",
        json={
            "datasource_id": "ds_a",
            "semantic_config_id": "cfg_a",
            "semantic_builder_config": {
                "aggregations": [{"func": "SUM", "table": "orders", "column": "amount"}],
            },
        },
    )

    payload = response.json()
    assert payload["status"] == "error"
    assert payload["code"] == ErrorCode.CHATDATA_BAD_REQUEST
    assert "匹配到多个语义指标" in payload["msg"]


def test_create_query_task_recompiles_semantic_builder_sql(client, auth_headers, fake_datasource_manager, sample_query_task, monkeypatch):
    _add_semantic_join_fixture(fake_datasource_manager)
    captured = {}

    async def fake_create_query_task(body):
        captured["body"] = body
        return sample_query_task

    monkeypatch.setattr(query_providers, "get_datasource_manager", lambda: fake_datasource_manager)
    monkeypatch.setattr("apps.chat_data_app.services.semantic_builder_compiler.get_datasource_manager", lambda: fake_datasource_manager)
    monkeypatch.setattr(query_runtime, "create_query_task", fake_create_query_task)
    monkeypatch.setattr(query_providers.query_task_service, "schedule_query_task", lambda task_id: captured.setdefault("scheduled", task_id))

    response = client.post(
        "/api/chatdata/query-tasks",
        json={
            "task_type": "sql",
            "datasource_id": "ds_a",
            "sql": "select * from tampered_table",
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
    assert "tampered_table" not in captured["body"].sql
    assert "LEFT JOIN orders ON city_dim.city_id = orders.city_id" in captured["body"].sql
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
    monkeypatch.setattr(query_providers, "get_datasource_manager", lambda: fake_datasource_manager)

    response = client.post(
        "/api/chatdata/query-tasks",
        json={"task_type": "sql", "datasource_id": "missing_ds", "sql": "select 1"},
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
        json={"task_type": "sql", "datasource_id": "ds_a", "sql": "select 1; select 2"},
        headers=auth_headers,
    )

    payload = response.json()
    assert payload["status"] == "error"
    assert payload["code"] == ErrorCode.CHATDATA_BAD_REQUEST
    assert "一次只能执行一条 SQL" in payload["msg"]


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
    assert payload["data"]["results"] is None
    assert payload["data"]["executions"] == [{
        "execution_id": "exec_1",
        "task_id": "task_a",
        "datasource_id": "ds_a",
        "member_datasource_id": "m1",
        "member_name": "杭州库",
        "executor_type": "http",
        "sql_status": "running",
        "remote_task_id": "remote_1",
        "db_connection_id": "",
        "started_time": None,
        "finished_time": None,
        "operator_name": "测试用户",
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
    assert payload["data"]["results"] == [{"member_datasource_id": "m1", "success": True}]
    assert payload["data"]["remote_tasks"][0]["remote_task_id"] == "remote_1"


def test_execute_sql_api_contract(client, auth_headers, sample_query_task, monkeypatch):
    captured = {}
    result_data = query_runtime.build_query_result_payload(
        sql="select * from orders",
        member_results=[{
            "member_datasource_id": "",
            "member_name": "",
            "status": "succeeded",
            "columns": ["id"],
            "data": [{"id": 1}],
            "total_count": 1,
            "execution_time_ms": 1,
            "error": None,
        }],
    )

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

    monkeypatch.setattr(query_runtime, "prepare_sql_request_for_execution", fake_prepare_sql_request_for_execution)
    monkeypatch.setattr(query_runtime, "create_task_for_execute_request", fake_create_task_for_execute_request)
    async def fake_create_query_executions_for_task(task_id, body, operator_name=None):
        return {}

    monkeypatch.setattr(query_runtime, "create_query_executions_for_task", fake_create_query_executions_for_task)
    monkeypatch.setattr(query_runtime, "update_query_task", fake_update_query_task)
    async def fake_build_execute_sql_result_data(body, query_task_id=None, execution_map=None, username=None):
        return result_data

    monkeypatch.setattr(query_runtime, "build_execute_sql_result_data", fake_build_execute_sql_result_data)

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
    assert payload["data"]["status"] == "succeeded"
    assert payload["data"]["results"][0]["columns"] == ["id"]
    assert captured["body"].user_id == "user_a"
    assert captured["body"].skip_query_cache is True
    assert captured["operator_name"] == "测试用户"
    assert captured["updates"][-1]["status"] == "succeeded"


def test_execute_sql_api_runs_sql_through_query_runtime(client, auth_headers, monkeypatch):
    captured = _patch_execute_sql_runtime(monkeypatch)

    sql_response = client.post(
        "/api/chatdata/execute-sql",
        json={
            "datasource_id": "ds_a",
            "member_datasource_ids": ["member_a"],
            "sql": "select * from orders",
            "query_type": "sql",
            "limit": 5,
        },
        headers=auth_headers,
    )

    sql_payload = sql_response.json()
    assert sql_payload["status"] == "success"
    assert captured["created_tasks"][0]["query_type"] == "sql"
    assert captured["created_tasks"][0]["body"].user_id == "user_a"
    assert [item["member_datasource_id"] for item in captured["executor_calls"]] == ["member_a"]
    assert captured["executor_calls"][0]["username"] == "测试用户"
    assert captured["closed_targets"] == ["member_a"]
    assert captured["task_updates"][0]["status"] == "running"
    assert captured["task_updates"][1]["status"] == "succeeded"
    assert sql_payload["data"]["results"][0]["data"][0]["sql_text"] == "select * from orders LIMIT 5"


def test_execute_sql_api_rejects_builder_without_semantic_config(client, auth_headers, monkeypatch):
    captured = _patch_execute_sql_runtime(monkeypatch)

    response = client.post(
        "/api/chatdata/execute-sql",
        json={
            "datasource_id": "ds_a",
            "member_datasource_ids": ["member_b"],
            "sql": "select city, sum(amount) amount from orders group by city",
            "query_type": "builder",
            "builder_config": {"mainTable": "orders"},
            "limit": 7,
        },
        headers=auth_headers,
    )

    payload = response.json()
    assert payload["status"] == "error"
    assert payload["code"] == ErrorCode.CHATDATA_BAD_REQUEST
    assert "必须基于指标集" in payload["msg"]
    assert captured["created_tasks"] == []
    assert captured["executor_calls"] == []


def test_execute_sql_api_blocks_dangerous_sql_at_executor_boundary(client, auth_headers, monkeypatch):
    captured = _patch_execute_sql_runtime(monkeypatch)

    response = client.post(
        "/api/chatdata/execute-sql",
        json={
            "datasource_id": "ds_a",
            "sql": "select 1; drop table orders",
            "query_type": "sql",
            "limit": 5,
        },
        headers=auth_headers,
    )

    payload = response.json()
    assert payload["status"] == "error"
    assert payload["code"] == ErrorCode.CHATDATA_SERVICE_ERROR
    assert "一次只能执行一条 SQL" in payload["msg"]
    assert captured["executor_calls"][0]["sql"] == "select 1; drop table orders"
    assert captured["task_updates"][0]["status"] == "running"
    assert captured["task_updates"][1]["status"] == "failed"


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

    monkeypatch.setattr(query_runtime, "prepare_sql_request_for_execution", fake_prepare_sql_request_for_execution)
    monkeypatch.setattr(query_runtime, "create_task_for_execute_request", fake_create_task_for_execute_request)
    monkeypatch.setattr(query_runtime, "create_query_executions_for_task", fake_create_query_executions_for_task)
    monkeypatch.setattr(query_runtime, "update_query_task", fake_update_query_task)
    monkeypatch.setattr(query_runtime, "build_execute_sql_result_data", fake_build_execute_sql_result_data)

    response = client.post(
        "/api/chatdata/execute-sql",
        json={"datasource_id": "ds_a", "sql": "select * from missing_table", "limit": 5},
        headers=auth_headers,
    )

    payload = response.json()
    assert payload["status"] == "error"
    assert payload["code"] == ErrorCode.CHATDATA_SERVICE_ERROR
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

    monkeypatch.setattr(query_runtime, "prepare_sql_request_for_execution", fake_prepare_sql_request_for_execution)
    monkeypatch.setattr(query_runtime, "create_task_for_execute_request", fake_create_task_for_execute_request)

    response = client.post(
        "/api/chatdata/execute-sql",
        json={"datasource_id": "ds_a", "sql": "-- only comment"},
        headers=auth_headers,
    )

    payload = response.json()
    assert payload["status"] == "error"
    assert payload["code"] == ErrorCode.CHATDATA_BAD_REQUEST
    assert "SQL 注释移除后为空" in payload["msg"]
    assert captured["created"] is False
