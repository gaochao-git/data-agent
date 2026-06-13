import os
import sys
import json
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1")
os.environ.setdefault("FXCJG_DB_PORT", "3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from langchain_core.messages import AIMessage, HumanMessage

from apps.chat_data_app.services.semantic_tools import (
    SemanticToolHandler,
    create_semantic_config_selection_tools,
    extract_datasource_id,
    extract_semantic_config_id,
    extract_user_query,
)


def test_extract_datasource_id_from_state():
    assert extract_datasource_id({"datasource_id": "ds_001"}) == "ds_001"
    assert extract_datasource_id({"datasource_id": "  ds_002  "}) == "ds_002"
    assert extract_datasource_id({"datasource_id": ""}) is None
    assert extract_datasource_id({}) is None
    assert extract_datasource_id(None) is None
    assert extract_semantic_config_id({"semantic_config_id": " sc_001 "}) == "sc_001"
    assert extract_semantic_config_id({"semantic_config_id": ""}) is None


def test_extract_user_query_reads_last_human_message():
    assert extract_user_query(None) is None
    assert extract_user_query({}) is None
    assert extract_user_query({"messages": [HumanMessage(content="第一次"), AIMessage(content="回复"), HumanMessage(content="第二次")]}) == "第二次"


def test_select_semantic_config_tool_uses_bound_datasource_id(monkeypatch):
    captured = {}

    async def fake_select_semantic_config_for_query(datasource_id, user_query, model_id=None):
        captured.update({"datasource_id": datasource_id, "user_query": user_query, "model_id": model_id})
        return {
            "selected_config_id": "sc_a",
            "selected_config_name": "订单分析",
            "coverage": "full",
            "confidence": "high",
            "reason": "命中订单金额",
        }

    async def fake_load_runtime_semantic_config(datasource_id, semantic_config_id):
        captured["load_args"] = (datasource_id, semantic_config_id)
        return {"id": semantic_config_id, "name": "订单分析", "metrics": [], "dimensions": []}

    monkeypatch.setattr("apps.chat_data_app.services.semantic_tools.select_semantic_config_for_query", fake_select_semantic_config_for_query)
    monkeypatch.setattr("apps.chat_data_app.services.semantic_tools.load_runtime_semantic_config", fake_load_runtime_semantic_config)

    tool = create_semantic_config_selection_tools(
        model_id="model_a",
        include_query_tools=False,
        datasource_id="ds_bound",
    )[0]
    result = asyncio.run(tool.ainvoke({"query": "查询订单金额"}))
    payload = json.loads(result.replace("[select_semantic_config_tool]", "", 1).strip())

    assert captured["datasource_id"] == "ds_bound"
    assert captured["user_query"] == "查询订单金额"
    assert captured["model_id"] == "model_a"
    assert captured["load_args"] == ("ds_bound", "sc_a")
    assert payload["selected"] is True


def test_generate_sql_uses_semantic_params_only(monkeypatch):
    captured = {}
    handler = SemanticToolHandler({"metrics": [], "dimensions": [], "relationships": []})

    def fake_generate_sql(**kwargs):
        captured.update(kwargs)
        return {"success": True, "sql": "SELECT 1"}

    monkeypatch.setattr(handler.generator, "generate_sql", fake_generate_sql)

    result = handler.generate_sql(
        metrics=["总销量"],
        dimensions=[],
        filters=[],
        having=[],
        order_by=[],
        limit=100,
        user_query="查询总销量",
    )

    assert "SELECT 1" in result
    assert captured["metrics"] == ["总销量"]
    assert "visualization" not in captured


def test_generate_sql_returns_error_text_when_generator_fails(monkeypatch):
    handler = SemanticToolHandler({"metrics": [], "dimensions": [], "relationships": []})

    monkeypatch.setattr(handler.generator, "generate_sql", lambda **_kwargs: {"success": False, "error": "错误：指标不存在"})

    result = handler.generate_sql([], [], [], [], [], None, user_query="查不存在指标")

    assert result == "错误：指标不存在"


def test_generate_sql_returns_user_message_for_unsupported_query(monkeypatch):
    handler = SemanticToolHandler({"metrics": [], "dimensions": [], "relationships": []})

    monkeypatch.setattr(handler.generator, "generate_sql", lambda **_kwargs: {
        "success": True,
        "sql": "",
        "user_message": "当前指标集不支持该问题",
    })

    result = handler.generate_sql([], [], [], [], [], None, user_query="查退款原因")

    assert result == "当前指标集不支持该问题"


def test_unified_semantic_tools_use_bound_config_without_selector():
    tools = create_semantic_config_selection_tools(semantic_config={
        "id": "sc_a",
        "source_type": "mysql",
        "metrics": [{"name": "订单数", "expression": "COUNT(orders.order_id)", "table": "orders"}],
        "dimensions": [{"name": "订单状态", "column": "status", "table": "orders", "type": "categorical"}],
        "relationships": [],
    })
    assert [item.name for item in tools] == ["select_semantic_config_tool", "dsl2sql_tool"]
    _, dsl_tool = tools

    sql_text = asyncio.run(dsl_tool.ainvoke({
        "metrics": ["订单数"],
        "dimensions": ["订单状态"],
        "filters": [],
        "having": [],
        "order_by": [{"field": "订单数", "direction": "DESC"}],
        "limit": 10,
        "state": {"messages": [HumanMessage(content="按状态统计订单数")], "datasource_id": "ds_a", "semantic_config_id": "sc_a"},
    }))

    assert "COUNT(" in sql_text
    assert "ORDER BY COUNT(" in sql_text
