import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.agent.tools import calculator_tool
from apps.chat_data_app.agent.tools import semantic_query as agent_tools
from apps.chat_data_app.agent.prompts.semantic_layer import build_semantic_prompt
from apps.chat_data_app.services import query_runtime
from apps.chat_data_app.services import sql_assist_context


def _query_result_payload(result: str) -> dict:
    payload = json.loads(result)
    assert payload["type"] == "query_result"
    assert payload["version"] == 1
    return payload


def test_calculator_tool_calculates_decimal_expression_and_rounds():
    result = asyncio.run(calculator_tool.ainvoke({
        "calculations": [
            {"name": "分转元", "expression": "123456789 / 100", "decimal_places": 2},
            {"name": "占比", "expression": "25 / 200 * 100", "decimal_places": 1},
        ],
    }))

    payload = json.loads(result)
    assert payload == {
        "success": True,
        "results": [
            {"expression": "123456789 / 100", "name": "分转元", "success": True, "result": "1234567.89"},
            {"expression": "25 / 200 * 100", "name": "占比", "success": True, "result": "12.5"},
        ],
    }


def test_calculator_tool_rejects_unsafe_expression():
    result = asyncio.run(calculator_tool.ainvoke({
        "calculations": [
            {"name": "危险表达式", "expression": "__import__('os').system('echo bad')"},
            {"name": "正常表达式", "expression": "10 + 1"},
        ],
    }))

    payload = json.loads(result)
    assert payload == {
        "success": False,
        "results": [
            {
                "expression": "__import__('os').system('echo bad')",
                "name": "危险表达式",
                "success": False,
                "error": "expression 只能包含数字、括号和 + - * /",
            },
            {"expression": "10 + 1", "name": "正常表达式", "success": True, "result": "11"},
        ],
    }


def test_calculator_tool_covers_empty_division_and_unary_branches():
    result = asyncio.run(calculator_tool.ainvoke({
        "calculations": [
            {"expression": "   "},
            {"name": "除零", "expression": "1 / 0"},
            {"name": "一元运算", "expression": "-1 + +2", "decimal_places": 0},
        ],
    }))

    payload = json.loads(result)

    assert payload["success"] is False
    assert payload["results"][0] == {"expression": "", "success": False, "error": "expression 不能为空"}
    assert payload["results"][1]["error"] == "除数不能为 0"
    assert payload["results"][2]["result"] == "1"


def test_build_semantic_prompt_includes_ontology_rules():
    prompt = build_semantic_prompt({
        "description": "订单指标集",
        "ontology_rules": "有效订单仅统计已支付且未退款订单",
        "metrics": [{"name": "订单金额", "expression": "SUM(pay_amount)", "description": "实付金额"}],
        "dimensions": [{"name": "订单状态", "expression": "orders.status", "description": "状态枚举"}],
        "relationships": [],
    })

    assert "**配置说明**: 订单指标集" in prompt
    assert "**业务描述**: 有效订单仅统计已支付且未退款订单" in prompt
    assert "订单金额" in prompt
    assert "订单状态" in prompt


def test_table_metadata_formatters_cover_empty_and_selected_tables():
    tables = [
        {
            "table_name": "orders",
            "description": "订单表",
            "columns": [
                {"name": "order_id", "type": "bigint", "primary_key": True, "description": "订单ID"},
                {"name": "status", "description": "订单状态"},
            ],
        },
        {"table_name": "users", "description": "用户表", "columns": []},
    ]

    assert sql_assist_context.get_tables_create_format([], ["orders"]) == ""
    assert sql_assist_context.get_tables_create_format(tables, []) == ""
    assert sql_assist_context.get_tables_create_format(tables, ["missing"]) == ""

    ddl = sql_assist_context.get_tables_create_format(tables, ["orders"])

    assert "CREATE TABLE orders" in ddl
    assert "order_id bigint PRIMARY KEY  -- 订单ID" in ddl
    assert "status TEXT  -- 订单状态" in ddl

    no_comment = sql_assist_context.get_tables_create_format(
        [{"table_name": "logs", "description": "", "columns": [{"name": "id"}]}],
        ["logs"],
    )
    assert "    id TEXT" in no_comment
    assert "--" not in no_comment


def test_build_semantic_prompt_includes_dialect_types_allowed_values_and_relationships():
    prompt = build_semantic_prompt({
        "source_type": "hive",
        "metrics": [
            {"name": "订单数", "label": "订单数量", "aggregation": "COUNT", "field": "order_id", "mysql_type": "bigint", "metadata_supplement": "同义词：订单量"},
            {"name": "销售额", "expression": "SUM(amount)", "data_type": "decimal"},
        ],
        "dimensions": [
            {"name": "订单状态", "table": "orders", "column": "status", "hive_type": "string", "allowed_values": ["paid", "cancelled"], "metadata_supplement": "枚举值：paid=已支付，cancelled=已取消"},
            {"name": "用户ID", "field": "orders.user_id", "columnType": "bigint"},
        ],
        "relationships": [
            {
                "left_table": "orders",
                "right_table": "users",
                "join_type": "inner",
                "relationship_type": "many_to_one",
                "join_conditions": [
                    {"left_column": "user_id", "right_column": "user_id"},
                    {"left_column": "bad"},
                ],
            },
            {"left_table": "orders", "right_table": "items", "join_conditions": []},
        ],
        "tables": [
            {
                "table_name": "orders",
                "description": "订单主表",
                "columns": [
                    {"name": "order_id", "type": "bigint", "description": "订单ID"},
                    {"name": "status", "type": "string", "description": "订单状态"},
                ],
            },
            {
                "table_name": "users",
                "description": "用户表",
                "columns": [{"name": "user_id", "type": "bigint", "description": "用户ID"}],
            },
            {
                "table_name": "unrelated",
                "description": "无关表",
                "columns": [{"name": "secret_col", "type": "string"}],
            },
        ],
    })

    assert "**SQL方言**: HIVE" in prompt
    assert prompt.index("### 可用维度/字段（可用于返回、筛选、分组）") < prompt.index("### 可用指标（仅用于明确聚合口径）")
    assert "COUNT(order_id)" in prompt
    assert "类型: bigint" in prompt
    assert "元数据补充: 同义词：订单量" in prompt
    assert "元数据补充: 枚举值：paid=已支付，cancelled=已取消" in prompt
    assert "可选值: [paid、cancelled]" in prompt
    assert "### 相关表和原始字段（用于补充字段理解和 SQL 回退）" in prompt
    assert "**orders**：订单主表" in prompt
    assert "order_id，类型: bigint，备注: 订单ID" in prompt
    assert "**users**：用户表" in prompt
    assert "secret_col" not in prompt
    assert "**orders** → **users**: orders.user_id = users.user_id (INNER JOIN, many_to_one)" in prompt
    assert "orders** → **items" not in prompt


def test_get_all_tables_async_reads_single_or_all_datasources(monkeypatch):
    class FakeManager:
        async def get_datasource_async(self, datasource_id):
            return {"ds_tables": [{"table_name": datasource_id}]} if datasource_id == "ds_a" else None

        async def get_cached_schema_async(self, datasource_id):
            datasource = await self.get_datasource_async(datasource_id)
            if not datasource:
                raise ValueError("数据源不存在")
            return datasource.get("ds_tables", [])

        async def get_all_datasources_async(self):
            return [
                {"ds_tables": [{"table_name": "orders"}]},
                {"ds_tables": [{"table_name": "users"}]},
            ]

    monkeypatch.setattr(sql_assist_context, "get_datasource_manager", lambda: FakeManager())

    assert asyncio.run(sql_assist_context.get_all_tables_async("ds_a")) == [{"table_name": "ds_a"}]
    assert asyncio.run(sql_assist_context.get_all_tables_async("missing")) == []
    assert asyncio.run(sql_assist_context.get_all_tables_async(None)) == [{"table_name": "orders"}, {"table_name": "users"}]


class _FakeDatasourceManager:
    async def get_datasource_async(self, datasource_id: str):
        if datasource_id != "ds_group":
            return None
        return {
            "id": datasource_id,
            "source_type": "mysql",
        }

    async def get_execution_datasources_async(self, datasource_id: str, member_datasource_ids: list[str] | None = None):
        member_ids = member_datasource_ids or ["member_a"]
        all_targets = {
            "member_a": {
                "member_datasource_id": "member_a",
                "member_name": "杭州库",
                "source_type": "mysql",
                "source_config": {"host": "127.0.0.1", "port": 3306, "database": "db_a", "user": "u", "password": "p"},
            },
            "member_b": {
                "member_datasource_id": "member_b",
                "member_name": "宁波库",
                "source_type": "mysql",
                "source_config": {"host": "127.0.0.2", "port": 3306, "database": "db_b", "user": "u", "password": "p"},
            },
        }
        return [all_targets[item] for item in member_ids]

    async def get_authorized_execution_datasources_async(
        self,
        datasource_id: str,
        member_datasource_ids: list[str] | None = None,
        user_id: str | None = None,
        username: str | None = None,
    ):
        return await self.get_execution_datasources_async(datasource_id, member_datasource_ids)


async def _fake_execute_sql_on_target(sql: str, target_datasource: dict, user_id: str | None = None, operator_name: str | None = None) -> dict:
    member_id = target_datasource.get("member_datasource_id")
    return {
        "success": True,
        "columns": ["member_id", "sql_text"],
        "data": [{"member_id": member_id, "sql_text": sql}],
        "total_count": 1,
        "execution_time_ms": 15,
    }


def _fake_runtime_execute_sql_for_member(handler):
    async def fake_execute_sql_for_member(
        *,
        execution_target: dict,
        sql: str,
        limit: int = 100,
        timeout: int | None = None,
        user_id: str | None = None,
        username: str | None = None,
        query_task_id: str | None = None,
        execution_id: str | None = None,
        skip_query_cache: bool = False,
        datasource_id: str | None = None,
    ) -> dict:
        member_id = execution_target.get("member_datasource_id")
        member_name = execution_target.get("member_name")
        try:
            try:
                base_result = await handler(sql, execution_target, user_id=user_id, operator_name=username, execution_id=execution_id)
            except TypeError:
                base_result = await handler(sql, execution_target, user_id=user_id, operator_name=username)
        except Exception as exc:
            base_result = {
                "success": False,
                "columns": [],
                "data": [],
                "total_count": 0,
                "error": f"执行失败: {str(exc) or type(exc).__name__}",
            }
        return {
            "success": bool(base_result.get("success")),
            "sql": base_result.get("sql") or sql,
            "columns": base_result.get("columns", []),
            "data": base_result.get("data", []),
            "total_count": base_result.get("total_count", 0),
            "execution_time_ms": base_result.get("execution_time_ms"),
            "error": base_result.get("error"),
            "member_datasource_id": base_result.get("member_datasource_id") or member_id,
            "member_name": base_result.get("member_name") or member_name,
        }

    return fake_execute_sql_for_member


def test_execute_sql_real_returns_multi_member_results(monkeypatch):
    fake_manager = _FakeDatasourceManager()
    monkeypatch.setattr(query_runtime, "get_datasource_manager", lambda: fake_manager)
    monkeypatch.setattr(query_runtime, "create_task_for_execute_request", lambda *_args, **_kwargs: asyncio.sleep(0, result={"task_id": "task_1"}))
    monkeypatch.setattr(query_runtime, "create_query_executions_for_task", lambda *_args, **_kwargs: asyncio.sleep(0, result={}))
    monkeypatch.setattr(query_runtime, "update_query_task", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(query_runtime, "execute_sql_for_member", _fake_runtime_execute_sql_for_member(_fake_execute_sql_on_target))

    result = asyncio.run(agent_tools._execute_sql_real(
        sql="select 1",
        datasource_id="ds_group",
        member_datasource_ids=["member_a", "member_b"],
    ))

    assert result["status"] == "succeeded"
    assert result["sql"] == "select 1"
    assert len(result["results"]) == 2
    assert result["results"][0]["member_name"] == "杭州库"
    assert result["results"][1]["member_name"] == "宁波库"
    assert result["results"][1]["data"][0]["member_id"] == "member_b"


async def _fake_execute_sql_on_target_with_failure(sql: str, target_datasource: dict, user_id: str | None = None, operator_name: str | None = None) -> dict:
    member_id = target_datasource.get("member_datasource_id")
    if member_id == "member_a":
        return {
            "success": False,
            "columns": [],
            "data": [],
            "total_count": 0,
            "error": "杭州库执行失败",
        }
    return {
        "success": True,
        "columns": ["member_id", "sql_text"],
        "data": [{"member_id": member_id, "sql_text": sql}],
        "total_count": 1,
        "execution_time_ms": 18,
    }


def test_execute_sql_real_uses_first_successful_member_as_primary(monkeypatch):
    fake_manager = _FakeDatasourceManager()
    monkeypatch.setattr(query_runtime, "get_datasource_manager", lambda: fake_manager)
    monkeypatch.setattr(query_runtime, "create_task_for_execute_request", lambda *_args, **_kwargs: asyncio.sleep(0, result={"task_id": "task_1"}))
    monkeypatch.setattr(query_runtime, "create_query_executions_for_task", lambda *_args, **_kwargs: asyncio.sleep(0, result={}))
    monkeypatch.setattr(query_runtime, "update_query_task", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(query_runtime, "execute_sql_for_member", _fake_runtime_execute_sql_for_member(_fake_execute_sql_on_target_with_failure))

    result = asyncio.run(agent_tools._execute_sql_real(
        sql="select 1",
        datasource_id="ds_group",
        member_datasource_ids=["member_a", "member_b"],
    ))

    assert result["status"] == "succeeded"
    assert result["results"][1]["member_name"] == "宁波库"
    assert result["results"][1]["columns"] == ["member_id", "sql_text"]
    assert result["results"][1]["data"][0]["member_id"] == "member_b"


async def _fake_execute_sql_on_target_with_delay(sql: str, target_datasource: dict, user_id: str | None = None, operator_name: str | None = None) -> dict:
    await asyncio.sleep(0.2)
    member_id = target_datasource.get("member_datasource_id")
    return {
        "success": True,
        "columns": ["member_id"],
        "data": [{"member_id": member_id}],
        "total_count": 1,
        "execution_time_ms": 200,
    }


def test_execute_sql_real_runs_members_concurrently(monkeypatch):
    fake_manager = _FakeDatasourceManager()
    monkeypatch.setattr(query_runtime, "get_datasource_manager", lambda: fake_manager)
    monkeypatch.setattr(query_runtime, "create_task_for_execute_request", lambda *_args, **_kwargs: asyncio.sleep(0, result={"task_id": "task_1"}))
    monkeypatch.setattr(query_runtime, "create_query_executions_for_task", lambda *_args, **_kwargs: asyncio.sleep(0, result={}))
    monkeypatch.setattr(query_runtime, "update_query_task", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(query_runtime, "execute_sql_for_member", _fake_runtime_execute_sql_for_member(_fake_execute_sql_on_target_with_delay))

    start = time.perf_counter()
    result = asyncio.run(agent_tools._execute_sql_real(
        sql="select 1",
        datasource_id="ds_group",
        member_datasource_ids=["member_a", "member_b"],
    ))
    elapsed = time.perf_counter() - start

    assert result["status"] == "succeeded"
    assert len(result["results"]) == 2
    assert result["results"][0]["member_datasource_id"] == "member_a"
    assert result["results"][1]["member_datasource_id"] == "member_b"
    assert elapsed < 0.35


async def _fake_execute_sql_on_target_with_exception(sql: str, target_datasource: dict, user_id: str | None = None, operator_name: str | None = None) -> dict:
    member_id = target_datasource.get("member_datasource_id")
    if member_id == "member_a":
        raise RuntimeError("boom")
    return {
        "success": True,
        "columns": ["member_id", "sql_text"],
        "data": [{"member_id": member_id, "sql_text": sql}],
        "total_count": 1,
        "execution_time_ms": 18,
    }


def test_execute_sql_real_keeps_other_members_when_one_member_raises(monkeypatch):
    fake_manager = _FakeDatasourceManager()
    monkeypatch.setattr(query_runtime, "get_datasource_manager", lambda: fake_manager)
    monkeypatch.setattr(query_runtime, "create_task_for_execute_request", lambda *_args, **_kwargs: asyncio.sleep(0, result={"task_id": "task_1"}))
    monkeypatch.setattr(query_runtime, "create_query_executions_for_task", lambda *_args, **_kwargs: asyncio.sleep(0, result={}))
    monkeypatch.setattr(query_runtime, "update_query_task", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(query_runtime, "execute_sql_for_member", _fake_runtime_execute_sql_for_member(_fake_execute_sql_on_target_with_exception))

    result = asyncio.run(agent_tools._execute_sql_real(
        sql="select 1",
        datasource_id="ds_group",
        member_datasource_ids=["member_a", "member_b"],
    ))

    assert result["status"] == "succeeded"
    assert result["results"][1]["member_name"] == "宁波库"
    assert result["results"][0]["status"] == "failed"
    assert result["results"][0]["error"] == "执行失败: boom"
    assert result["results"][1]["status"] == "succeeded"


def test_execute_sql_real_returns_errors_for_missing_datasource(monkeypatch):
    class FakeManager:
        async def get_datasource_async(self, datasource_id):
            return None

    monkeypatch.setattr(query_runtime, "get_datasource_manager", lambda: FakeManager())

    missing_id = asyncio.run(agent_tools._execute_sql_real("select 1", None))
    missing_datasource = asyncio.run(agent_tools._execute_sql_real("select 1", "missing"))

    assert missing_id["error"] == "未指定数据源ID，无法执行查询"
    assert missing_datasource["error"] == "数据源不存在: missing"


def test_execute_sql_real_updates_task_success_and_failure(monkeypatch):
    class FakeManager(_FakeDatasourceManager):
        pass

    updates = []

    async def fake_create_task_for_execute_request(body, query_type=None, operator_name=None):
        return {"task_id": "task_1"}

    async def fake_create_query_executions_for_task(task_id, body, operator_name=None):
        return {"member_a": "exec_a"}

    def fake_get_execution_id_by_member(execution_map, target):
        return execution_map.get(target.get("member_datasource_id"))

    async def fake_update_query_task(task_id, **kwargs):
        updates.append({"task_id": task_id, **kwargs})
        return {"task_id": task_id}

    async def fake_execute_sql_for_member_success(sql, target_datasource, user_id=None, operator_name=None, execution_id=None):
        return {
            "success": True,
            "sql": sql,
            "columns": ["id"],
            "data": [{"id": 1}],
            "total_count": 1,
            "member_datasource_id": target_datasource["member_datasource_id"],
        }

    async def fake_execute_sql_for_member_error(sql, target_datasource, user_id=None, operator_name=None, execution_id=None):
        raise ValueError("不支持的数据源类型")

    monkeypatch.setattr(query_runtime, "get_datasource_manager", lambda: FakeManager())
    monkeypatch.setattr(query_runtime, "create_task_for_execute_request", fake_create_task_for_execute_request)
    monkeypatch.setattr(query_runtime, "create_query_executions_for_task", fake_create_query_executions_for_task)
    monkeypatch.setattr(query_runtime, "get_execution_id_by_member", fake_get_execution_id_by_member)
    monkeypatch.setattr(query_runtime, "update_query_task", fake_update_query_task)
    monkeypatch.setattr(query_runtime, "execute_sql_for_member", _fake_runtime_execute_sql_for_member(fake_execute_sql_for_member_success))
    success = asyncio.run(agent_tools._execute_sql_real("select 1", "ds_group", ["member_a"], user_id="user_a", operator_name="测试用户"))

    async def fake_runtime_execute_error(**_kwargs):
        raise ValueError("不支持的数据源类型")

    monkeypatch.setattr(query_runtime, "execute_sql_for_member", fake_runtime_execute_error)
    failed = asyncio.run(agent_tools._execute_sql_real("select 1", "ds_group", ["member_a"]))

    assert success["status"] == "succeeded"
    assert updates[0]["status"] == "running"
    assert updates[1]["status"] == "succeeded"
    assert failed["status"] == "failed"
    assert failed["error"] == "不支持的数据源类型"
    assert updates[-1]["status"] == "failed"


def test_semantic_query_tool_returns_query_result_or_error(monkeypatch):
    from langchain_core.messages import HumanMessage

    class FakeGenerator:
        def __init__(self, config):
            assert config["id"] == "cfg_a"

        def generate_sql(self, **kwargs):
            assert kwargs["metrics"] == ["订单数"]
            assert kwargs["user_query"] == "查订单"
            return {"success": True, "sql": "select 1"}

    async def fake_load_config(datasource_id, semantic_config_id, user_id=None):
        assert datasource_id == "ds_a"
        assert semantic_config_id == "cfg_a"
        assert user_id == "user_a"
        return {"id": "cfg_a", "metrics": [], "dimensions": []}

    async def fake_execute_sql_real(sql, datasource_id, member_datasource_ids, user_id, semantic_config_id, query_text, conversation_id, operator_name=None):
        assert sql == "select 1"
        assert datasource_id == "ds_a"
        assert member_datasource_ids == ["m1"]
        assert user_id == "user_a"
        assert semantic_config_id == "cfg_a"
        assert query_text == "查订单"
        assert conversation_id == "conv_a"
        assert operator_name == "测试用户"
        return {"success": True, "columns": ["id"], "data": [{"id": 1}], "total_count": 1}

    async def fake_execute_sql_real_failed(*_args, **_kwargs):
        return {"success": False, "error": "表不存在"}

    state = {
        "datasource_id": "ds_a",
        "user_id": "user_a",
        "operator_name": "测试用户",
        "available_member_datasource_ids": ["m1"],
        "semantic_config_id": "cfg_a",
        "conversation_id": "conv_a",
        "messages": [HumanMessage(content="查订单")],
    }

    monkeypatch.setattr(agent_tools, "load_runtime_semantic_config", fake_load_config)
    monkeypatch.setattr(agent_tools, "SemanticSQLGenerator", FakeGenerator)
    monkeypatch.setattr(agent_tools, "_execute_sql_real", fake_execute_sql_real)
    success = asyncio.run(agent_tools.semantic_query_tool.ainvoke({
        "dsl": {"metrics": ["订单数"]},
        "semantic_config_id": "cfg_a",
        "member_datasource_ids": ["m1"],
        "state": state,
    }))

    monkeypatch.setattr(agent_tools, "_execute_sql_real", fake_execute_sql_real_failed)
    failed = asyncio.run(agent_tools.semantic_query_tool.ainvoke({
        "dsl": {"metrics": ["订单数"]},
        "semantic_config_id": "cfg_a",
        "member_datasource_ids": ["m1"],
        "state": state,
    }))

    success_payload = _query_result_payload(success)
    assert success_payload["status"] == "succeeded"
    assert success_payload["data"]["data"] == [{"id": 1}]
    assert success_payload["data"]["sql"] == "select 1"
    assert success_payload["meta"]["query_mode"] == "dsl2sql"
    assert success_payload["meta"]["confidence"] == 0.9
    failed_payload = _query_result_payload(failed)
    assert failed_payload["status"] == "failed"
    assert failed_payload["error"] == "表不存在"


def test_semantic_query_tool_allows_model_selected_member_ids(monkeypatch):
    from langchain_core.messages import HumanMessage

    class FakeGenerator:
        def __init__(self, config):
            assert config["id"] == "cfg_a"

        def generate_sql(self, **_kwargs):
            return {"success": True, "sql": "select 1"}

    async def fake_load_config(*_args, **_kwargs):
        return {"id": "cfg_a", "metrics": [], "dimensions": []}

    captured = {}

    async def fake_execute_sql_real(sql, datasource_id, member_datasource_ids, *_args, **_kwargs):
        captured["sql"] = sql
        captured["datasource_id"] = datasource_id
        captured["member_datasource_ids"] = member_datasource_ids
        return {"success": True, "columns": ["id"], "data": [{"id": 1}], "total_count": 1}

    state = {
        "datasource_id": "ds_a",
        "available_member_datasource_ids": ["org_b", "org_c"],
        "semantic_config_id": "cfg_a",
        "messages": [HumanMessage(content="查指定机构订单")],
    }

    monkeypatch.setattr(agent_tools, "load_runtime_semantic_config", fake_load_config)
    monkeypatch.setattr(agent_tools, "SemanticSQLGenerator", FakeGenerator)
    monkeypatch.setattr(agent_tools, "_execute_sql_real", fake_execute_sql_real)

    result = asyncio.run(agent_tools.semantic_query_tool.ainvoke({
        "dsl": {"metrics": ["订单数"]},
        "semantic_config_id": "cfg_a",
        "member_datasource_ids": [" org_b ", "org_b", "", "org_c"],
        "state": state,
    }))

    assert _query_result_payload(result)["status"] == "succeeded"
    assert captured["member_datasource_ids"] == ["org_b", "org_c"]


def test_semantic_query_tool_allows_member_ids_from_available_scope(monkeypatch):
    from langchain_core.messages import HumanMessage

    class FakeGenerator:
        def __init__(self, config):
            assert config["id"] == "cfg_a"

        def generate_sql(self, **_kwargs):
            return {"success": True, "sql": "select 1"}

    async def fake_load_config(*_args, **_kwargs):
        return {"id": "cfg_a", "metrics": [], "dimensions": []}

    captured = {}

    async def fake_execute_sql_real(sql, datasource_id, member_datasource_ids, *_args, **_kwargs):
        captured["member_datasource_ids"] = member_datasource_ids
        return {"success": True, "columns": ["id"], "data": [{"id": 1}], "total_count": 1}

    state = {
        "datasource_id": "ds_a",
        "member_datasource_ids": [],
        "available_member_datasource_ids": ["org_a", "org_b"],
        "semantic_config_id": "cfg_a",
        "messages": [HumanMessage(content="查 org_b 订单")],
    }

    monkeypatch.setattr(agent_tools, "load_runtime_semantic_config", fake_load_config)
    monkeypatch.setattr(agent_tools, "SemanticSQLGenerator", FakeGenerator)
    monkeypatch.setattr(agent_tools, "_execute_sql_real", fake_execute_sql_real)

    result = asyncio.run(agent_tools.semantic_query_tool.ainvoke({
        "dsl": {"metrics": ["订单数"]},
        "semantic_config_id": "cfg_a",
        "member_datasource_ids": ["org_b"],
        "state": state,
    }))

    assert _query_result_payload(result)["status"] == "succeeded"
    assert captured["member_datasource_ids"] == ["org_b"]


def test_semantic_query_tool_does_not_default_to_state_member_ids(monkeypatch):
    from langchain_core.messages import HumanMessage

    class FakeGenerator:
        def __init__(self, config):
            assert config["id"] == "cfg_a"

        def generate_sql(self, **_kwargs):
            return {"success": True, "sql": "select 1"}

    async def fake_load_config(*_args, **_kwargs):
        return {"id": "cfg_a", "metrics": [], "dimensions": []}

    async def fail_execute_sql_real(*_args, **_kwargs):
        raise AssertionError("member_datasource_ids must be a tool parameter")

    state = {
        "datasource_id": "ds_a",
        "member_datasource_ids": ["org_a"],
        "available_member_datasource_ids": ["org_a", "org_b"],
        "semantic_config_id": "cfg_a",
        "messages": [HumanMessage(content="查订单")],
    }

    monkeypatch.setattr(agent_tools, "load_runtime_semantic_config", fake_load_config)
    monkeypatch.setattr(agent_tools, "SemanticSQLGenerator", FakeGenerator)
    monkeypatch.setattr(agent_tools, "_execute_sql_real", fail_execute_sql_real)

    result = asyncio.run(agent_tools.semantic_query_tool.coroutine(
        dsl={"metrics": ["订单数"]},
        semantic_config_id="cfg_a",
        state=state,
    ))

    assert "缺少 member_datasource_ids" in result


def test_semantic_query_tool_rejects_member_ids_outside_available_scope(monkeypatch):
    from langchain_core.messages import HumanMessage

    class FakeGenerator:
        def __init__(self, config):
            assert config["id"] == "cfg_a"

        def generate_sql(self, **_kwargs):
            return {"success": True, "sql": "select 1"}

    async def fake_load_config(*_args, **_kwargs):
        return {"id": "cfg_a", "metrics": [], "dimensions": []}

    async def fail_execute_sql_real(*_args, **_kwargs):
        raise AssertionError("should not execute unavailable member id")

    state = {
        "datasource_id": "ds_a",
        "available_member_datasource_ids": ["org_a", "org_b"],
        "semantic_config_id": "cfg_a",
        "messages": [HumanMessage(content="查指定机构订单")],
    }

    monkeypatch.setattr(agent_tools, "load_runtime_semantic_config", fake_load_config)
    monkeypatch.setattr(agent_tools, "SemanticSQLGenerator", FakeGenerator)
    monkeypatch.setattr(agent_tools, "_execute_sql_real", fail_execute_sql_real)

    result = asyncio.run(agent_tools.semantic_query_tool.ainvoke({
        "dsl": {"metrics": ["订单数"]},
        "semantic_config_id": "cfg_a",
        "member_datasource_ids": ["org_b", "org_c"],
        "state": state,
    }))

    assert "本次查询包含不可用的执行对象 org_c" in result


def test_semantic_query_tool_rejects_member_ids_outside_available_member_scope(monkeypatch):
    from langchain_core.messages import HumanMessage

    class FakeGenerator:
        def __init__(self, config):
            assert config["id"] == "cfg_a"

        def generate_sql(self, **_kwargs):
            return {"success": True, "sql": "select 1"}

    async def fake_load_config(*_args, **_kwargs):
        return {"id": "cfg_a", "metrics": [], "dimensions": []}

    async def fail_execute_sql_real(*_args, **_kwargs):
        raise AssertionError("should not execute unavailable member id")

    state = {
        "datasource_id": "ds_a",
        "member_datasource_ids": [],
        "available_member_datasource_ids": ["org_a", "org_b"],
        "semantic_config_id": "cfg_a",
        "messages": [HumanMessage(content="查指定机构订单")],
    }

    monkeypatch.setattr(agent_tools, "load_runtime_semantic_config", fake_load_config)
    monkeypatch.setattr(agent_tools, "SemanticSQLGenerator", FakeGenerator)
    monkeypatch.setattr(agent_tools, "_execute_sql_real", fail_execute_sql_real)

    result = asyncio.run(agent_tools.semantic_query_tool.ainvoke({
        "dsl": {"metrics": ["订单数"]},
        "semantic_config_id": "cfg_a",
        "member_datasource_ids": ["org_c"],
        "state": state,
    }))

    assert "本次查询包含不可用的执行对象 org_c" in result


def test_semantic_query_tool_rejects_member_ids_conflicting_with_matched_scope(monkeypatch):
    from langchain_core.messages import HumanMessage

    class FakeGenerator:
        def __init__(self, config):
            assert config["id"] == "cfg_a"

        def generate_sql(self, **_kwargs):
            return {"success": True, "sql": "select 1"}

    async def fake_load_config(*_args, **_kwargs):
        return {"id": "cfg_a", "metrics": [], "dimensions": []}

    async def fail_execute_sql_real(*_args, **_kwargs):
        raise AssertionError("should not execute member id that conflicts with exact query match")

    state = {
        "datasource_id": "ds_a",
        "member_datasource_ids": [],
        "available_member_datasource_ids": ["org_a", "org_b"],
        "matched_member_datasource_ids": ["org_b"],
        "semantic_config_id": "cfg_a",
        "messages": [HumanMessage(content="查机构B订单")],
    }

    monkeypatch.setattr(agent_tools, "load_runtime_semantic_config", fake_load_config)
    monkeypatch.setattr(agent_tools, "SemanticSQLGenerator", FakeGenerator)
    monkeypatch.setattr(agent_tools, "_execute_sql_real", fail_execute_sql_real)

    result = asyncio.run(agent_tools.semantic_query_tool.ainvoke({
        "dsl": {"metrics": ["订单数"]},
        "semantic_config_id": "cfg_a",
        "member_datasource_ids": ["org_a"],
        "state": state,
    }))

    assert "与用户问题中指定的机构不一致" in result
    assert "org_b" in result


def test_semantic_query_tool_requires_member_ids(monkeypatch):
    from langchain_core.messages import HumanMessage

    class FakeGenerator:
        def __init__(self, config):
            assert config["id"] == "cfg_a"

        def generate_sql(self, **_kwargs):
            return {"success": True, "sql": "select 1"}

    async def fake_load_config(*_args, **_kwargs):
        return {"id": "cfg_a", "metrics": [], "dimensions": []}

    async def fail_execute_sql_real(*_args, **_kwargs):
        raise AssertionError("should not execute without explicit member ids")

    state = {
        "datasource_id": "ds_a",
        "available_member_datasource_ids": ["m1", "m2"],
        "semantic_config_id": "cfg_a",
        "messages": [HumanMessage(content="查订单")],
    }

    monkeypatch.setattr(agent_tools, "load_runtime_semantic_config", fake_load_config)
    monkeypatch.setattr(agent_tools, "SemanticSQLGenerator", FakeGenerator)
    monkeypatch.setattr(agent_tools, "_execute_sql_real", fail_execute_sql_real)

    result = asyncio.run(agent_tools.semantic_query_tool.coroutine(
        dsl={"metrics": ["订单数"]},
        semantic_config_id="cfg_a",
        state=state,
    ))

    assert "缺少 member_datasource_ids" in result


def test_semantic_query_tool_rejects_empty_available_member_scope(monkeypatch):
    from langchain_core.messages import HumanMessage

    class FakeGenerator:
        def __init__(self, config):
            assert config["id"] == "cfg_a"

        def generate_sql(self, **_kwargs):
            return {"success": True, "sql": "select 1"}

    async def fake_load_config(*_args, **_kwargs):
        return {"id": "cfg_a", "metrics": [], "dimensions": []}

    async def fail_execute_sql_real(*_args, **_kwargs):
        raise AssertionError("should not execute without request-visible member ids")

    state = {
        "datasource_id": "ds_a",
        "member_datasource_ids": [],
        "available_member_datasource_ids": [],
        "semantic_config_id": "cfg_a",
        "messages": [HumanMessage(content="查订单")],
    }

    monkeypatch.setattr(agent_tools, "load_runtime_semantic_config", fake_load_config)
    monkeypatch.setattr(agent_tools, "SemanticSQLGenerator", FakeGenerator)
    monkeypatch.setattr(agent_tools, "_execute_sql_real", fail_execute_sql_real)

    result = asyncio.run(agent_tools.semantic_query_tool.ainvoke({
        "dsl": {"metrics": ["订单数"]},
        "semantic_config_id": "cfg_a",
        "member_datasource_ids": ["org_a"],
        "state": state,
    }))

    assert "本次请求没有可用执行对象" in result


def test_semantic_query_tool_uses_latest_human_query_and_rejects_sql_inside_dsl(monkeypatch):
    from langchain_core.messages import AIMessage, HumanMessage

    captured = {}

    class FakeGenerator:
        def __init__(self, config):
            assert config["id"] == "cfg_a"

        def generate_sql(self, **kwargs):
            captured["user_query"] = kwargs["user_query"]
            return {"success": True, "sql": "select latest"}

    async def fake_load_config(*_args, **_kwargs):
        return {"id": "cfg_a", "metrics": [], "dimensions": []}

    async def fake_execute_sql_real(sql, *_args, **_kwargs):
        return {"success": True, "sql": sql, "columns": ["v"], "data": [{"v": 1}], "total_count": 1}

    state = {
        "datasource_id": "ds_a",
        "semantic_config_id": "cfg_a",
        "available_member_datasource_ids": ["m1"],
        "messages": [
            HumanMessage(content="上一轮问题"),
            AIMessage(content="上一轮回答"),
            HumanMessage(content="最新问题"),
        ],
    }

    monkeypatch.setattr(agent_tools, "load_runtime_semantic_config", fake_load_config)
    monkeypatch.setattr(agent_tools, "SemanticSQLGenerator", FakeGenerator)
    monkeypatch.setattr(agent_tools, "_execute_sql_real", fake_execute_sql_real)

    result = asyncio.run(agent_tools.semantic_query_tool.ainvoke({
        "dsl": {"metrics": ["订单数"]},
        "semantic_config_id": "cfg_a",
        "member_datasource_ids": ["m1"],
        "state": state,
    }))
    assert '"sql": "select latest"' in result
    assert captured["user_query"] == "最新问题"

    legacy_sql_result = asyncio.run(agent_tools.semantic_query_tool.ainvoke({
        "dsl": {"sql": "select 1"},
        "semantic_config_id": "cfg_a",
        "member_datasource_ids": ["m1"],
        "state": state,
    }))
    assert legacy_sql_result == "semantic_query_tool 执行失败: sql 必须放在顶层 sql 参数中，不要放入 dsl"


def test_semantic_query_tool_executes_top_level_sql_as_nl2sql(monkeypatch):
    from langchain_core.messages import HumanMessage

    async def fake_load_config(datasource_id, semantic_config_id, user_id=None):
        assert datasource_id == "ds_a"
        assert semantic_config_id == "cfg_a"
        assert user_id == "user_a"
        return {"id": "cfg_a", "metrics": [], "dimensions": []}

    class FailGenerator:
        def __init__(self, _config):
            raise AssertionError("top-level sql should not go through DSL2SQL")

    captured = {}

    async def fake_execute_sql_real(sql, datasource_id, member_datasource_ids, user_id, semantic_config_id, query_text, conversation_id, operator_name=None):
        captured.update({
            "sql": sql,
            "datasource_id": datasource_id,
            "member_datasource_ids": member_datasource_ids,
            "user_id": user_id,
            "semantic_config_id": semantic_config_id,
            "query_text": query_text,
            "conversation_id": conversation_id,
            "operator_name": operator_name,
        })
        return {"success": True, "columns": ["v"], "data": [{"v": 1}], "total_count": 1}

    state = {
        "datasource_id": "ds_a",
        "semantic_config_id": "cfg_a",
        "available_member_datasource_ids": ["org_a"],
        "user_id": "user_a",
        "operator_name": "测试用户",
        "conversation_id": "conv_a",
        "messages": [HumanMessage(content="用复杂 SQL 查询")],
    }

    monkeypatch.setattr(agent_tools, "load_runtime_semantic_config", fake_load_config)
    monkeypatch.setattr(agent_tools, "SemanticSQLGenerator", FailGenerator)
    monkeypatch.setattr(agent_tools, "_execute_sql_real", fake_execute_sql_real)

    result = asyncio.run(agent_tools.semantic_query_tool.ainvoke({
        "sql": "with t as (select 1 as v) select * from t",
        "semantic_config_id": "cfg_a",
        "member_datasource_ids": ["org_a"],
        "state": state,
    }))
    payload = _query_result_payload(result)

    assert captured == {
        "sql": "with t as (select 1 as v) select * from t",
        "datasource_id": "ds_a",
        "member_datasource_ids": ["org_a"],
        "user_id": "user_a",
        "semantic_config_id": "cfg_a",
        "query_text": "用复杂 SQL 查询",
        "conversation_id": "conv_a",
        "operator_name": "测试用户",
    }
    assert payload["meta"]["query_mode"] == "nl2sql"
    assert payload["meta"]["confidence"] == 0.6
    assert payload["data"]["sql"] == "with t as (select 1 as v) select * from t"


def test_semantic_query_tool_validation_and_compile_error_branches(monkeypatch):
    assert agent_tools._state_semantic_config_matches({}, "cfg_a") is False
    assert agent_tools._state_semantic_config_matches({"id": "cfg_a"}, None) is True
    assert agent_tools._state_semantic_config_matches({"id": ""}, "cfg_a") is True
    assert agent_tools._state_semantic_config_matches({"id": "other"}, "cfg_a") is False
    assert agent_tools._latest_human_query([type("Msg", (), {"type": "human", "content": "伪用户消息"})()]) == "伪用户消息"

    missing_config = asyncio.run(agent_tools.semantic_query_tool.coroutine(
        dsl={"metrics": ["订单数"]},
        state={},
        member_datasource_ids=["m1"],
    ))
    bad_dsl = asyncio.run(agent_tools.semantic_query_tool.coroutine(["bad"], {"semantic_config": {}}))
    mixed_payload = asyncio.run(agent_tools.semantic_query_tool.ainvoke({
        "dsl": {"metrics": ["订单数"]},
        "sql": "select 1",
        "semantic_config_id": "cfg_a",
        "member_datasource_ids": ["m1"],
        "state": {},
    }))

    assert missing_config == "semantic_query_tool 执行失败: 缺少指标集ID，请先调用 select_semantic_config_tool 后显式传入 semantic_config_id"
    assert bad_dsl == "semantic_query_tool 执行失败: dsl 必须是对象"
    assert mixed_payload == "semantic_query_tool 执行失败: 必须且只能传入 dsl 或 sql 其中一个"

    async def fake_load_config(*_args, **_kwargs):
        return {"id": "cfg_a", "metrics": [], "dimensions": []}

    class FakeGenerator:
        def __init__(self, _config):
            pass

        def generate_sql(self, **kwargs):
            metrics = kwargs.get("metrics") or []
            if metrics == ["bad"]:
                return {"success": False, "error": "指标不存在"}
            return {"success": True, "sql": "   "}

    monkeypatch.setattr(agent_tools, "load_runtime_semantic_config", fake_load_config)
    monkeypatch.setattr(agent_tools, "SemanticSQLGenerator", FakeGenerator)

    compile_error = asyncio.run(agent_tools.semantic_query_tool.ainvoke({
        "dsl": {"metrics": ["bad"]},
        "semantic_config_id": "cfg_a",
        "member_datasource_ids": ["m1"],
        "state": {"semantic_config_id": "cfg_a", "available_member_datasource_ids": ["m1"]},
    }))
    empty_sql = asyncio.run(agent_tools.semantic_query_tool.ainvoke({
        "dsl": {"metrics": ["empty"]},
        "semantic_config_id": "cfg_a",
        "member_datasource_ids": ["m1"],
        "state": {"semantic_config_id": "cfg_a", "available_member_datasource_ids": ["m1"]},
    }))

    assert compile_error == "semantic_query_tool 执行失败: 指标不存在"
    assert empty_sql == "semantic_query_tool 执行失败: DSL2SQL 未生成 SQL"


def test_resolve_semantic_query_config_injects_missing_state_config_id(monkeypatch):
    async def fail_load_config(*_args, **_kwargs):
        raise AssertionError("state semantic_config should be reused")

    monkeypatch.setattr(agent_tools, "load_runtime_semantic_config", fail_load_config)

    config = asyncio.run(agent_tools._resolve_semantic_query_config(
        {"semantic_config": {"metrics": [], "dimensions": []}},
        datasource_id="ds_a",
        semantic_config_id="cfg_a",
    ))

    assert config["id"] == "cfg_a"


def test_semantic_query_large_result_summary_omits_blank_sql(monkeypatch):
    class FakeGenerator:
        def __init__(self, _config):
            pass

        def generate_sql(self, **_kwargs):
            return {"success": True, "sql": "select generated"}

    async def fake_load_config(*_args, **_kwargs):
        return {"id": "cfg_a", "metrics": [], "dimensions": []}

    async def fake_execute_sql_real(*_args, **_kwargs):
        return {
            "success": True,
            "sql": "",
            "columns": ["content"],
            "data": [{"content": "x" * 200}],
            "total_count": 1,
        }

    monkeypatch.setattr(agent_tools, "load_runtime_semantic_config", fake_load_config)
    monkeypatch.setattr(agent_tools, "SemanticSQLGenerator", FakeGenerator)
    monkeypatch.setattr(agent_tools, "_execute_sql_real", fake_execute_sql_real)
    monkeypatch.setattr(agent_tools, "save_query_result", lambda *_args, **_kwargs: "artifact_blank_sql")
    monkeypatch.setattr(agent_tools, "TOOL_RESULT_OFFLOAD_SIZE", 100)

    result = asyncio.run(agent_tools.semantic_query_tool.ainvoke({
        "dsl": {"metrics": ["内容"]},
        "semantic_config_id": "cfg_a",
        "member_datasource_ids": ["m1"],
        "state": {"semantic_config_id": "cfg_a", "available_member_datasource_ids": ["m1"]},
    }))
    payload = _query_result_payload(result)

    assert payload["meta"]["artifact_id"] == "artifact_blank_sql"
    assert payload["meta"]["sql"] == "select generated"


def test_semantic_query_tool_reports_missing_runtime_config(monkeypatch):
    async def fake_load_config(*_args, **_kwargs):
        return None

    monkeypatch.setattr(agent_tools, "load_runtime_semantic_config", fake_load_config)

    result = asyncio.run(agent_tools.semantic_query_tool.ainvoke({
        "dsl": {"metrics": ["订单数"]},
        "semantic_config_id": "cfg_missing",
        "member_datasource_ids": ["m1"],
        "state": {"datasource_id": "ds_a", "semantic_config_id": "cfg_missing"},
    }))

    assert result == "semantic_query_tool 执行失败: 未找到可用指标集 cfg_missing"


def test_semantic_query_tool_stores_large_result_as_artifact(monkeypatch):
    from langchain_core.messages import HumanMessage

    class FakeGenerator:
        def __init__(self, config):
            assert config["id"] == "cfg_a"

        def generate_sql(self, **_kwargs):
            return {"success": True, "sql": "select content from big_table"}

    async def fake_load_config(*_args, **_kwargs):
        return {"id": "cfg_a", "metrics": [], "dimensions": []}

    async def fake_execute_sql_real(*_args, **_kwargs):
        return {
            "success": True,
            "columns": ["content"],
            "data": [{"content": "x" * 200}],
            "total_count": 1,
            "execution_time_ms": 12,
            "sql": "select content from big_table",
        }

    captured = {}

    def fake_save_query_result(result, conversation_id=None):
        captured["result"] = result
        captured["conversation_id"] = conversation_id
        return "artifact_large"

    state = {
        "datasource_id": "ds_a",
        "user_id": "user_a",
        "operator_name": "测试用户",
        "available_member_datasource_ids": ["m1"],
        "semantic_config_id": "cfg_a",
        "conversation_id": "conv_a",
        "messages": [HumanMessage(content="查大结果")],
    }

    monkeypatch.setattr(agent_tools, "load_runtime_semantic_config", fake_load_config)
    monkeypatch.setattr(agent_tools, "SemanticSQLGenerator", FakeGenerator)
    monkeypatch.setattr(agent_tools, "_execute_sql_real", fake_execute_sql_real)
    monkeypatch.setattr(agent_tools, "save_query_result", fake_save_query_result)
    monkeypatch.setattr(agent_tools, "TOOL_RESULT_OFFLOAD_SIZE", 100)

    result = asyncio.run(agent_tools.semantic_query_tool.ainvoke({
        "dsl": {"metrics": ["内容"]},
        "semantic_config_id": "cfg_a",
        "member_datasource_ids": ["m1"],
        "state": state,
    }))

    payload = _query_result_payload(result)
    assert payload["meta"]["artifact_id"] == "artifact_large"
    assert payload["meta"]["llm_analysis_skipped"] is True
    assert "查询结果已超过限制，完整结果已写入 artifact" in result
    assert "x" * 120 not in result
    assert captured["result"]["data"][0]["content"] == "x" * 200
    assert captured["conversation_id"] == "conv_a"


def test_semantic_query_tool_uses_state_semantic_config_and_includes_compiled_sql_in_large_summary(monkeypatch):
    class FakeGenerator:
        def __init__(self, config):
            assert config["id"] == "cfg_temp"

        def generate_sql(self, **_kwargs):
            return {"success": True, "sql": "select compiled from temp_config"}

    async def fake_execute_sql_real(*_args, **_kwargs):
        return {
            "success": True,
            "columns": ["content"],
            "data": [{"content": "x" * 200}],
            "total_count": 1,
            "execution_time_ms": 12,
        }

    async def fail_load_config(*_args, **_kwargs):
        raise AssertionError("state semantic_config should be used before runtime loading")

    monkeypatch.setattr(agent_tools, "load_runtime_semantic_config", fail_load_config)
    monkeypatch.setattr(agent_tools, "SemanticSQLGenerator", FakeGenerator)
    monkeypatch.setattr(agent_tools, "_execute_sql_real", fake_execute_sql_real)
    monkeypatch.setattr(agent_tools, "save_query_result", lambda *_args, **_kwargs: "artifact_no_sql")
    monkeypatch.setattr(agent_tools, "TOOL_RESULT_OFFLOAD_SIZE", 100)

    state = {
        "semantic_config_id": "cfg_temp",
        "semantic_config": {"id": "cfg_temp", "metrics": [], "dimensions": []},
        "available_member_datasource_ids": ["m1"],
    }
    result = asyncio.run(agent_tools.semantic_query_tool.ainvoke({
        "dsl": {"metrics": ["临时指标"]},
        "semantic_config_id": "cfg_temp",
        "member_datasource_ids": ["m1"],
        "state": state,
    }))

    payload = _query_result_payload(result)
    assert payload["meta"]["artifact_id"] == "artifact_no_sql"
    assert payload["meta"]["sql"] == "select compiled from temp_config"


def test_semantic_query_tool_injects_explicit_id_into_temp_semantic_config(monkeypatch):
    class FakeGenerator:
        def __init__(self, config):
            assert config["metrics"] == []
            assert config["id"] == "cfg_temp"

        def generate_sql(self, **_kwargs):
            return {"success": True, "sql": "select temp"}

    async def fail_load_config(*_args, **_kwargs):
        raise AssertionError("temp semantic_config should not require runtime loading")

    async def fake_execute_sql_real(sql, *_args, **_kwargs):
        return {"success": True, "sql": sql, "columns": ["v"], "data": [{"v": 1}], "total_count": 1}

    monkeypatch.setattr(agent_tools, "load_runtime_semantic_config", fail_load_config)
    monkeypatch.setattr(agent_tools, "SemanticSQLGenerator", FakeGenerator)
    monkeypatch.setattr(agent_tools, "_execute_sql_real", fake_execute_sql_real)

    result = asyncio.run(agent_tools.semantic_query_tool.ainvoke({
        "dsl": {"metrics": ["临时指标"]},
        "semantic_config_id": "cfg_temp",
        "member_datasource_ids": ["m1"],
        "state": {
            "semantic_config": {"metrics": [], "dimensions": []},
            "available_member_datasource_ids": ["m1"],
        },
    }))

    assert '"sql": "select temp"' in result


def test_semantic_query_tool_executes_compiled_sql_directly(monkeypatch):
    class FakeGenerator:
        def __init__(self, config):
            assert config["id"] == "cfg_a"

        def generate_sql(self, **_kwargs):
            return {"success": True, "sql": "select 1"}

    async def fake_load_config(*_args, **_kwargs):
        return {"id": "cfg_a", "metrics": [], "dimensions": []}

    executed_sql = []

    async def fake_execute_sql_real(sql, *_args, **_kwargs):
        executed_sql.append(sql)
        return {"success": True, "sql": sql, "columns": ["sql"], "data": [{"sql": sql}], "total_count": 1}

    state = {"semantic_config_id": "cfg_a", "available_member_datasource_ids": ["m1"]}
    monkeypatch.setattr(agent_tools, "load_runtime_semantic_config", fake_load_config)
    monkeypatch.setattr(agent_tools, "SemanticSQLGenerator", FakeGenerator)
    monkeypatch.setattr(agent_tools, "_execute_sql_real", fake_execute_sql_real)
    result = asyncio.run(agent_tools.semantic_query_tool.ainvoke({
        "dsl": {"metrics": ["指标"]},
        "semantic_config_id": "cfg_a",
        "member_datasource_ids": ["m1"],
        "state": state,
    }))

    assert executed_sql == ["select 1"]
    assert '"sql": "select 1"' in result


def test_sql_tool_remaining_helpers_cover_prompt_alias_branch():
    prompt = build_semantic_prompt({
        "metrics": [
            {"name": "无表指标", "expression": "COUNT(*)", "table": "", "description": ""},
            {"name": "同表指标", "expression": "SUM(amount)", "table": "orders", "table_alias": "orders"},
        ],
        "dimensions": [
            {"name": "别名维度", "table": "", "table_alias": "alias_only", "column": "", "description": ""},
            {"name": "空表达式", "table": "", "column": "", "description": ""},
        ],
        "relationships": [],
    })

    assert "无表指标" in prompt
    assert "无表指标，表达式: COUNT(*)，表" not in prompt
    assert "**同表指标**，表达式: SUM(amount)，表: orders" in prompt
    assert "表: alias_only" in prompt
    assert "表达式: -" in prompt


def test_execute_direct_sql_delegates_to_agent_sql(monkeypatch):
    captured = {}

    async def fake_execute_agent_sql(sql, datasource_id):
        captured["sql"] = sql
        captured["datasource_id"] = datasource_id
        return {"success": True}

    monkeypatch.setattr(query_runtime, "execute_agent_sql", fake_execute_agent_sql)

    result = asyncio.run(query_runtime.execute_direct_sql("select 1", "ds_a"))

    assert result == {"success": True}
    assert captured == {"sql": "select 1", "datasource_id": "ds_a"}
