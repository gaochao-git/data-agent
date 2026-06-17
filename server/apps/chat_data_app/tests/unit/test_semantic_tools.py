import os
import sys
import json
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
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
    load_runtime_semantic_config,
    _selection_to_tool_payload,
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
    assert extract_user_query({"messages": [AIMessage(content="没有用户消息")]}) is None


def test_selection_to_tool_payload_branches():
    empty = _selection_to_tool_payload(None)
    selected = _selection_to_tool_payload(
        {
            "selected_config_id": "cfg_a",
            "selected_config_name": "订单分析",
            "coverage": "full",
            "confidence": "high",
            "reason": "命中订单金额",
            "matched_metrics": ["订单金额"],
            "matched_dimensions": ["城市"],
            "candidates": "bad",
        },
        {"id": "cfg_a", "name": "订单分析", "metrics": [{"name": "订单金额"}], "dimensions": [{"name": "城市"}]},
    )
    ambiguous = _selection_to_tool_payload({
        "selected_config_id": "cfg_a",
        "coverage": "full",
        "ambiguous": True,
        "ambiguity_reason": "多个指标集都命中",
        "candidates": [{"id": "cfg_a", "name": "订单分析"}],
    })
    cross_config = _selection_to_tool_payload({
        "selected_config_id": "cfg_a",
        "coverage": "partial",
        "cross_config_required": True,
        "cross_config_reason": "需要跨指标集",
    })
    unsupported = _selection_to_tool_payload({"selected_config_id": "", "coverage": "none", "reason": "缺少退款指标"})

    assert empty["selected"] is False
    assert empty["message"].startswith("当前数据源组没有")
    assert selected["selected"] is True
    assert selected["semantic_config"]["id"] == "cfg_a"
    assert selected["candidates"] == []
    assert ambiguous["selected"] is False
    assert ambiguous["ambiguous"] is True
    assert "多个可用指标集" in ambiguous["message"]
    assert cross_config["cross_config_required"] is True
    assert "跨指标集" in cross_config["message"]
    assert unsupported["message"] == "当前指标体系不支持该问题，请说明缺少的指标、维度或业务口径"


def test_load_runtime_semantic_config_branches(monkeypatch):
    class RuntimeManager:
        async def get_datasource_async(self, datasource_id):
            if datasource_id == "missing":
                return None
            return {"id": datasource_id, "source_type": "hive"}

        async def get_runtime_semantic_config_async(self, datasource_id, semantic_config_id):
            if semantic_config_id == "enabled":
                return {"id": semantic_config_id, "enabled": True, "relationships": [{"left_table": "orders"}]}
            if semantic_config_id == "disabled":
                return {"id": semantic_config_id, "enabled": False}
            if semantic_config_id == "no_id":
                return {"enabled": True}
            return None

    class FallbackManager:
        async def get_datasource_async(self, datasource_id):
            return {"id": datasource_id, "source_type": "mysql"}

        async def get_semantic_config_async(self, datasource_id, semantic_config_id):
            return {"id": semantic_config_id, "enabled": True, "joins": [{"left_table": "kept"}]}

    monkeypatch.setattr("apps.chat_data_app.services.semantic_tools.get_datasource_manager", lambda: RuntimeManager())

    assert asyncio.run(load_runtime_semantic_config(None, "cfg")) is None
    assert asyncio.run(load_runtime_semantic_config("ds", None)) is None
    assert asyncio.run(load_runtime_semantic_config("missing", "enabled")) is None
    assert asyncio.run(load_runtime_semantic_config("ds", "missing")) is None
    assert asyncio.run(load_runtime_semantic_config("ds", "disabled")) is None

    enabled = asyncio.run(load_runtime_semantic_config("ds", "enabled"))
    no_id = asyncio.run(load_runtime_semantic_config("ds", "no_id"))
    assert enabled["joins"] == [{"left_table": "orders"}]
    assert enabled["source_type"] == "hive"
    assert no_id["id"] == "no_id"

    monkeypatch.setattr("apps.chat_data_app.services.semantic_tools.get_datasource_manager", lambda: FallbackManager())
    fallback = asyncio.run(load_runtime_semantic_config("ds", "cfg_b"))
    assert fallback["source_type"] == "mysql"
    assert fallback["joins"] == [{"left_table": "kept"}]


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


def test_select_semantic_config_tool_error_branches(monkeypatch):
    tools = create_semantic_config_selection_tools(include_query_tools=False)
    select_tool = tools[0]

    missing_datasource = asyncio.run(select_tool.ainvoke({"query": "查询订单", "state": {}}))
    missing_query = asyncio.run(select_tool.ainvoke({"query": "", "state": {"datasource_id": "ds_a", "messages": []}}))

    async def boom_select_semantic_config_for_query(**_kwargs):
        raise RuntimeError("selector failed")

    monkeypatch.setattr("apps.chat_data_app.services.semantic_tools.select_semantic_config_for_query", boom_select_semantic_config_for_query)
    failed = asyncio.run(select_tool.ainvoke({"query": "查询订单", "state": {"datasource_id": "ds_a"}}))

    assert "未绑定数据源" in missing_datasource
    assert "缺少用户问题" in missing_query
    assert "selector failed" in failed


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


def test_dsl2sql_tool_loads_runtime_config_and_handles_missing(monkeypatch):
    loaded = []

    async def fake_load_runtime_semantic_config(datasource_id, semantic_config_id):
        loaded.append((datasource_id, semantic_config_id))
        if semantic_config_id == "cfg_missing":
            return None
        return {
            "id": semantic_config_id,
            "source_type": "mysql",
            "metrics": [{"name": "订单数", "expression": "COUNT(orders.order_id)", "table": "orders"}],
            "dimensions": [{"name": "城市", "column": "city", "table": "orders", "type": "categorical"}],
            "relationships": [],
        }

    monkeypatch.setattr("apps.chat_data_app.services.semantic_tools.load_runtime_semantic_config", fake_load_runtime_semantic_config)
    _, dsl_tool = create_semantic_config_selection_tools(datasource_id="ds_bound")

    missing = asyncio.run(dsl_tool.ainvoke({
        "semantic_config_id": "cfg_missing",
        "metrics": ["订单数"],
        "dimensions": [],
        "state": {"messages": [HumanMessage(content="查询订单数")]},
    }))
    sql_text = asyncio.run(dsl_tool.ainvoke({
        "semantic_config_id": "cfg_ok",
        "metrics": ["订单数"],
        "dimensions": ["城市"],
        "state": {"messages": [HumanMessage(content="按城市查询订单数")]},
    }))

    assert "未找到可用指标集 cfg_missing" in missing
    assert "COUNT(" in sql_text
    assert loaded == [("ds_bound", "cfg_missing"), ("ds_bound", "cfg_ok")]


def test_dsl2sql_tool_uses_state_semantic_config_id_when_argument_missing(monkeypatch):
    loaded = []

    async def fake_load_runtime_semantic_config(datasource_id, semantic_config_id):
        loaded.append((datasource_id, semantic_config_id))
        return {
            "id": semantic_config_id,
            "source_type": "mysql",
            "metrics": [{"name": "订单数", "expression": "COUNT(orders.order_id)", "table": "orders"}],
            "dimensions": [],
            "relationships": [],
        }

    monkeypatch.setattr("apps.chat_data_app.services.semantic_tools.load_runtime_semantic_config", fake_load_runtime_semantic_config)
    _, dsl_tool = create_semantic_config_selection_tools()

    result = asyncio.run(dsl_tool.ainvoke({
        "metrics": ["订单数"],
        "dimensions": [],
        "state": {
            "datasource_id": "ds_state",
            "semantic_config_id": "cfg_state",
            "messages": [HumanMessage(content="查询订单数")],
        },
    }))

    assert "COUNT(" in result
    assert loaded == [("ds_state", "cfg_state")]
