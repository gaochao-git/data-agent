import os
import sys
import json
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from langchain_core.messages import AIMessage, HumanMessage

from apps.chat_data_app.services.semantic_tools import (
    SemanticToolHandler,
    create_semantic_config_selection_tools,
    extract_datasource_id,
    extract_datasource_group_context,
    extract_semantic_config_id,
    extract_user_query,
    format_semantic_config_directory_for_prompt,
    list_runtime_semantic_config_directory,
    load_runtime_semantic_config,
    _is_semantic_config_directory_query,
    _selection_to_tool_payload,
)


def test_extract_datasource_id_from_state():
    assert extract_datasource_id({"datasource_id": "ds_001"}) == "ds_001"
    assert extract_datasource_id({"datasource_id": "  ds_002  "}) == "ds_002"
    assert extract_datasource_id({"datasource_id": ""}) is None
    assert extract_datasource_id({}) is None
    assert extract_datasource_id(None) is None
    assert extract_semantic_config_id(None) is None
    assert extract_semantic_config_id({"semantic_config_id": " sc_001 "}) == "sc_001"
    assert extract_semantic_config_id({"semantic_config_id": ""}) is None
    assert extract_datasource_group_context({"datasource_group_context": "  分库说明  "}) == "分库说明"
    assert extract_datasource_group_context({}) == ""
    assert extract_datasource_group_context(None) == ""


def test_extract_user_query_reads_last_human_message():
    assert extract_user_query(None) is None
    assert extract_user_query({}) is None
    assert extract_user_query({"messages": [HumanMessage(content="第一次"), AIMessage(content="回复"), HumanMessage(content="第二次")]}) == "第二次"
    assert extract_user_query({"messages": [AIMessage(content="没有用户消息")]}) is None


def test_semantic_config_directory_query_detection():
    assert _is_semantic_config_directory_query("你现在能看到哪些指标集") is True
    assert _is_semantic_config_directory_query("当前可用数据集有哪些") is True
    assert _is_semantic_config_directory_query("查询订单数") is False
    assert _is_semantic_config_directory_query("有哪些指标可以查") is False


def test_format_semantic_config_directory_for_prompt_keeps_full_description():
    long_description = "订单经营分析" * 40
    prompt = format_semantic_config_directory_for_prompt([
        {
            "id": "sc_order",
            "name": "订单分析",
            "description": long_description,
            "metrics_total": 3,
            "dimensions_total": 5,
        },
        {"name": "", "description": "无名称会跳过"},
        "invalid",
    ])

    assert "## 当前可见指标集目录" in prompt
    assert "名称：订单分析" in prompt
    assert f"描述：{long_description}" in prompt
    assert "指标数：3；维度数：5" in prompt
    assert "无名称会跳过" not in prompt
    assert format_semantic_config_directory_for_prompt([]) == ""


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
    assert "业务口径无法唯一判断" in ambiguous["message"]
    assert cross_config["cross_config_required"] is True
    assert "不支持跨指标集 JOIN" in cross_config["message"]
    assert unsupported["message"] == "当前指标体系不支持该问题，请说明缺少的指标、维度或业务口径"


def test_selection_payload_selected_without_config_summary():
    selected = _selection_to_tool_payload({
        "selected_config_id": "cfg_a",
        "coverage": "full",
    })

    assert selected["selected"] is True
    assert selected["selected_config_name"] == "cfg_a"
    assert "semantic_config" not in selected


def test_selection_to_tool_payload_keeps_multi_config_query_plan():
    payload = _selection_to_tool_payload({
        "coverage": "partial",
        "query_plan": {
            "type": "multi_config_independent",
            "tasks": [
                {"task_id": "t1", "semantic_config_id": "cfg_order", "semantic_config_name": "订单分析", "question": "查订单金额"},
                {"task_id": "t2", "semantic_config_id": "cfg_user", "semantic_config_name": "用户分析", "question": "查用户数"},
            ],
            "final_action": "summarize",
        },
        "candidates": [
            {"id": "cfg_order", "name": "订单分析"},
            {"id": "cfg_user", "name": "用户分析"},
        ],
    })

    assert payload["selected"] is False
    assert payload["query_plan"]["type"] == "multi_config_independent"
    assert [item["semantic_config_id"] for item in payload["query_plan"]["tasks"]] == ["cfg_order", "cfg_user"]
    assert "逐个调用 dsl2sql_tool" in payload["message"]


def test_selection_to_tool_payload_skips_invalid_query_plan_tasks_and_uses_candidate_name():
    payload = _selection_to_tool_payload({
        "coverage": "partial",
        "query_plan": {
            "type": "multi_config_independent",
            "tasks": [
                "skip",
                {"task_id": "t1", "semantic_config_id": "cfg_order", "question": "查订单金额", "intent": "aggregate", "reason": "覆盖订单"},
                {"task_id": "t2", "semantic_config_id": "cfg_missing", "question": "查未知指标"},
            ],
        },
        "candidates": [
            {"id": "cfg_order", "name": "订单分析"},
        ],
    })

    tasks = payload["query_plan"]["tasks"]
    assert [item["semantic_config_name"] for item in tasks] == ["订单分析", "cfg_missing"]
    assert tasks[0]["reason"] == "覆盖订单"


def test_selection_to_tool_payload_blocks_cross_join_query_plan():
    payload = _selection_to_tool_payload({
        "coverage": "partial",
        "query_plan": {
            "type": "cross_join_required",
            "tasks": [
                {"task_id": "t1", "semantic_config_id": "cfg_order", "semantic_config_name": "订单分析", "question": "按商品维度关联订单金额"},
            ],
            "final_action": "unsupported",
        },
        "candidates": [
            {"id": "cfg_order", "name": "订单分析"},
            {"id": "cfg_product", "name": "商品分析"},
        ],
    })

    assert payload["selected"] is False
    assert payload["cross_config_required"] is True
    assert payload["query_plan"]["type"] == "cross_join_required"
    assert "不支持跨指标集 JOIN" in payload["message"]


def test_selection_to_tool_payload_keeps_unsupported_query_plan_message_fallback():
    payload = _selection_to_tool_payload({
        "coverage": "none",
        "reason": "缺少明确业务口径",
        "query_plan": {
            "type": "unsupported",
            "tasks": [
                {"task_id": "skip", "semantic_config_id": "", "question": "无效任务"},
            ],
            "final_action": "ask_user",
        },
    })

    assert payload["selected"] is False
    assert payload["query_plan"]["type"] == "unsupported"
    assert payload["query_plan"]["tasks"] == [{"task_id": "skip", "semantic_config_id": "", "semantic_config_name": "", "question": "无效任务", "intent": "", "reason": ""}]
    assert payload["message"] == "当前指标体系不支持该问题，请说明缺少的指标、维度或业务口径"


def test_select_semantic_config_tool_returns_full_directory_for_directory_query(monkeypatch):
    captured = {}

    class FakeDatasourceManager:
        async def get_runtime_semantic_configs_async(self, datasource_id, user_id=""):
            assert datasource_id == "ds_bound"
            captured["user_id"] = user_id
            return [
                {"id": "sc_order", "name": "订单分析", "description": "订单经营分析", "enabled": True, "metrics": [{"name": "订单数"}], "dimensions": [{"name": "城市"}]},
                {"id": "sc_disabled", "name": "停用指标集", "enabled": False, "metrics": [{"name": "停用指标"}], "dimensions": []},
                {"id": "sc_student", "name": "学生课程选课分析", "description": "选课与成绩分析", "enabled": True, "metrics": [{"name": "平均成绩"}], "dimensions": [{"name": "课程"}, {"name": "学生"}]},
            ]

    async def fail_select_semantic_config_for_query(*_args, **_kwargs):
        raise AssertionError("directory query should not call selector llm")

    monkeypatch.setattr("apps.chat_data_app.services.semantic_tools.get_datasource_manager", lambda: FakeDatasourceManager())
    monkeypatch.setattr("apps.chat_data_app.services.semantic_tools.select_semantic_config_for_query", fail_select_semantic_config_for_query)

    tool = create_semantic_config_selection_tools(include_query_tools=False, datasource_id="ds_bound", user_id="u1")[0]
    result = asyncio.run(tool.ainvoke({"query": "你现在能看到哪些指标集"}))
    payload = json.loads(result.replace("[select_semantic_config_tool]", "", 1).strip())

    assert captured["user_id"] == "u1"
    assert payload["directory"] is True
    assert payload["selected"] is False
    assert [item["name"] for item in payload["semantic_configs"]] == ["订单分析", "学生课程选课分析"]
    assert payload["semantic_configs"][0]["metrics_total"] == 1
    assert payload["semantic_configs"][1]["dimensions_total"] == 2


def test_list_runtime_semantic_config_directory_falls_back_to_legacy_manager(monkeypatch):
    captured = {}

    class LegacyDatasourceManager:
        async def get_semantic_configs_async(self, datasource_id, user_id=""):
            assert datasource_id == "ds_legacy"
            captured["user_id"] = user_id
            return [
                {"semantic_config_id": "sc_legacy", "semantic_config_name": "历史指标集", "semantic_config_desc": "历史描述", "enabled": True},
            ]

    monkeypatch.setattr("apps.chat_data_app.services.semantic_tools.get_datasource_manager", lambda: LegacyDatasourceManager())

    directory = asyncio.run(list_runtime_semantic_config_directory("ds_legacy", "u2"))

    assert captured["user_id"] == "u2"
    assert directory == [{
        "id": "sc_legacy",
        "name": "历史指标集",
        "description": "历史描述",
        "metrics_total": 0,
        "dimensions_total": 0,
    }]


def test_load_runtime_semantic_config_branches(monkeypatch):
    class RuntimeManager:
        async def get_datasource_async(self, datasource_id):
            if datasource_id == "missing":
                return None
            if datasource_id == "no_type":
                return {"id": datasource_id}
            return {"id": datasource_id, "source_type": "hive"}

        async def get_runtime_semantic_config_async(self, datasource_id, semantic_config_id):
            if semantic_config_id == "enabled":
                return {"id": semantic_config_id, "enabled": True, "relationships": [{"left_table": "orders"}]}
            if semantic_config_id == "disabled":
                return {"id": semantic_config_id, "enabled": False}
            if semantic_config_id == "plain":
                return {"id": semantic_config_id, "enabled": True}
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
    plain = asyncio.run(load_runtime_semantic_config("ds", "plain"))
    no_source_type = asyncio.run(load_runtime_semantic_config("no_type", "plain"))
    no_id = asyncio.run(load_runtime_semantic_config("ds", "no_id"))
    assert enabled["joins"] == [{"left_table": "orders"}]
    assert enabled["source_type"] == "hive"
    assert "joins" not in plain
    assert "source_type" not in no_source_type
    assert no_id["id"] == "no_id"

    monkeypatch.setattr("apps.chat_data_app.services.semantic_tools.get_datasource_manager", lambda: FallbackManager())
    fallback = asyncio.run(load_runtime_semantic_config("ds", "cfg_b"))
    assert fallback["source_type"] == "mysql"
    assert fallback["joins"] == [{"left_table": "kept"}]


def test_select_semantic_config_tool_uses_bound_datasource_id(monkeypatch):
    captured = {}

    async def fake_select_semantic_config_for_query(
        datasource_id,
        user_query,
        model_id=None,
        user_id=None,
        datasource_group_context=None,
    ):
        captured.update({
            "datasource_id": datasource_id,
            "user_query": user_query,
            "model_id": model_id,
            "user_id": user_id,
            "datasource_group_context": datasource_group_context,
        })
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
        user_id="u1",
    )[0]
    result = asyncio.run(tool.ainvoke({
        "query": "查询订单金额",
        "state": {"datasource_group_context": "当前数据源组按执行对象数据源分库"},
    }))
    payload = json.loads(result.replace("[select_semantic_config_tool]", "", 1).strip())

    assert captured["datasource_id"] == "ds_bound"
    assert captured["user_query"] == "查询订单金额"
    assert captured["model_id"] == "model_a"
    assert captured["user_id"] == "u1"
    assert captured["datasource_group_context"] == "当前数据源组按执行对象数据源分库"
    assert captured["load_args"] == ("ds_bound", "sc_a")
    assert payload["selected"] is True


def test_select_semantic_config_tool_returns_query_plan_semantic_summaries(monkeypatch):
    async def fake_select_semantic_config_for_query(datasource_id, user_query, model_id=None, user_id=None, datasource_group_context=None):
        assert datasource_id == "ds_bound"
        return {
            "coverage": "partial",
            "query_plan": {
                "type": "multi_config_independent",
                "tasks": [
                    {"task_id": "order", "semantic_config_id": "sc_order", "question": "查询订单金额"},
                    {"task_id": "user", "semantic_config_id": "sc_user", "question": "查询用户数"},
                ],
                "final_action": "summarize",
            },
            "candidates": [
                {"id": "sc_order", "name": "订单分析"},
                {"id": "sc_user", "name": "用户分析"},
            ],
        }

    async def fake_load_runtime_semantic_config(datasource_id, semantic_config_id):
        return {
            "id": semantic_config_id,
            "name": "订单分析" if semantic_config_id == "sc_order" else "用户分析",
            "metrics": [{"name": "订单金额" if semantic_config_id == "sc_order" else "用户数"}],
            "dimensions": [],
        }

    monkeypatch.setattr("apps.chat_data_app.services.semantic_tools.select_semantic_config_for_query", fake_select_semantic_config_for_query)
    monkeypatch.setattr("apps.chat_data_app.services.semantic_tools.load_runtime_semantic_config", fake_load_runtime_semantic_config)

    tool = create_semantic_config_selection_tools(include_query_tools=False, datasource_id="ds_bound")[0]
    result = asyncio.run(tool.ainvoke({"query": "查询订单金额和用户数"}))
    payload = json.loads(result.replace("[select_semantic_config_tool]", "", 1).strip())

    assert payload["selected"] is False
    assert payload["query_plan"]["semantic_configs"]["sc_order"]["metrics"][0]["name"] == "订单金额"
    assert payload["query_plan"]["semantic_configs"]["sc_user"]["metrics"][0]["name"] == "用户数"


def test_select_semantic_config_tool_skips_duplicate_and_missing_task_summaries(monkeypatch):
    loaded = []

    async def fake_select_semantic_config_for_query(datasource_id, user_query, model_id=None, user_id=None, datasource_group_context=None):
        return {
            "coverage": "partial",
            "query_plan": {
                "type": "multi_config_independent",
                "tasks": [
                    {"task_id": "order_1", "semantic_config_id": "sc_order", "question": "查询订单金额"},
                    {"task_id": "order_2", "semantic_config_id": "sc_order", "question": "重复订单金额"},
                    {"task_id": "blank", "semantic_config_id": "", "question": "无效"},
                    {"task_id": "missing", "semantic_config_id": "sc_missing", "question": "缺失"},
                ],
            },
            "candidates": [{"id": "sc_order", "name": "订单分析"}],
        }

    async def fake_load_runtime_semantic_config(datasource_id, semantic_config_id):
        loaded.append(semantic_config_id)
        if semantic_config_id == "sc_missing":
            return None
        return {"id": semantic_config_id, "name": "订单分析", "metrics": [{"name": "订单金额"}], "dimensions": []}

    monkeypatch.setattr("apps.chat_data_app.services.semantic_tools.select_semantic_config_for_query", fake_select_semantic_config_for_query)
    monkeypatch.setattr("apps.chat_data_app.services.semantic_tools.load_runtime_semantic_config", fake_load_runtime_semantic_config)

    tool = create_semantic_config_selection_tools(include_query_tools=False, datasource_id="ds_bound")[0]
    result = asyncio.run(tool.ainvoke({"query": "查询订单金额"}))
    payload = json.loads(result.replace("[select_semantic_config_tool]", "", 1).strip())

    assert loaded == ["sc_order", "sc_missing"]
    assert list(payload["query_plan"]["semantic_configs"]) == ["sc_order"]


def test_select_semantic_config_tool_omits_empty_query_plan_semantic_summaries(monkeypatch):
    async def fake_select_semantic_config_for_query(datasource_id, user_query, model_id=None, user_id=None, datasource_group_context=None):
        return {
            "coverage": "partial",
            "query_plan": {
                "type": "multi_config_independent",
                "tasks": [{"task_id": "missing", "semantic_config_id": "sc_missing", "question": "缺失"}],
            },
            "candidates": [{"id": "sc_missing", "name": "缺失指标集"}],
        }

    async def fake_load_runtime_semantic_config(datasource_id, semantic_config_id):
        return None

    monkeypatch.setattr("apps.chat_data_app.services.semantic_tools.select_semantic_config_for_query", fake_select_semantic_config_for_query)
    monkeypatch.setattr("apps.chat_data_app.services.semantic_tools.load_runtime_semantic_config", fake_load_runtime_semantic_config)

    tool = create_semantic_config_selection_tools(include_query_tools=False, datasource_id="ds_bound")[0]
    result = asyncio.run(tool.ainvoke({"query": "查询缺失指标"}))
    payload = json.loads(result.replace("[select_semantic_config_tool]", "", 1).strip())

    assert "semantic_configs" not in payload["query_plan"]


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


def test_semantic_tool_resolve_uses_bound_config_for_matching_id():
    tools = create_semantic_config_selection_tools(
        include_query_tools=True,
        datasource_id="ds_bound",
        semantic_config={"id": "sc_bound", "metrics": [{"name": "订单数", "expression": "COUNT(orders.id)", "table": "orders"}], "dimensions": []},
    )

    assert [item.name for item in tools] == ["select_semantic_config_tool", "dsl2sql_tool"]
    dsl_tool = tools[1]
    sql_text = asyncio.run(dsl_tool.ainvoke({
        "semantic_config_id": "sc_bound",
        "dsl": {"metrics": ["订单数"], "dimensions": [], "filters": []},
        "state": {"datasource_id": "ignored"},
    }))

    assert "COUNT(id)" in sql_text


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
        "dimensions": [{"name": "订单状态", "column": "status", "table": "orders", "logical_type": "text"}],
        "relationships": [],
    })
    assert [item.name for item in tools] == ["select_semantic_config_tool", "dsl2sql_tool"]
    _, dsl_tool = tools

    sql_text = asyncio.run(dsl_tool.ainvoke({
        "dsl": {
            "metrics": ["订单数"],
            "dimensions": ["订单状态"],
            "filters": [],
            "having": [],
            "order_by": [{"field": "订单数", "direction": "DESC"}],
            "limit": 10,
        },
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
            "dimensions": [{"name": "城市", "column": "city", "table": "orders", "logical_type": "text"}],
            "relationships": [],
        }

    monkeypatch.setattr("apps.chat_data_app.services.semantic_tools.load_runtime_semantic_config", fake_load_runtime_semantic_config)
    _, dsl_tool = create_semantic_config_selection_tools(datasource_id="ds_bound")

    missing = asyncio.run(dsl_tool.ainvoke({
        "semantic_config_id": "cfg_missing",
        "dsl": {"metrics": ["订单数"], "dimensions": []},
        "state": {"messages": [HumanMessage(content="查询订单数")]},
    }))
    sql_text = asyncio.run(dsl_tool.ainvoke({
        "semantic_config_id": "cfg_ok",
        "dsl": {"metrics": ["订单数"], "dimensions": ["城市"]},
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
        "dsl": {"metrics": ["订单数"], "dimensions": []},
        "state": {
            "datasource_id": "ds_state",
            "semantic_config_id": "cfg_state",
            "messages": [HumanMessage(content="查询订单数")],
        },
    }))

    assert "COUNT(" in result
    assert loaded == [("ds_state", "cfg_state")]


def test_dsl2sql_tool_accepts_full_dsl_argument(monkeypatch):
    captured = {}

    class FakeHandler:
        def __init__(self, config):
            captured["config"] = config

        def generate_sql(self, metrics, dimensions, filters, having, order_by, limit, offset, user_query=None):
            captured.update({
                "metrics": metrics,
                "dimensions": dimensions,
                "filters": filters,
                "having": having,
                "order_by": order_by,
                "limit": limit,
                "offset": offset,
                "user_query": user_query,
            })
            return "```sql\nSELECT 1\n```"

    async def fake_load_runtime_semantic_config(datasource_id, semantic_config_id):
        return {"id": semantic_config_id, "source_type": "mysql", "metrics": [], "dimensions": [], "relationships": []}

    monkeypatch.setattr("apps.chat_data_app.services.semantic_tools.load_runtime_semantic_config", fake_load_runtime_semantic_config)
    monkeypatch.setattr("apps.chat_data_app.services.semantic_tools.SemanticToolHandler", FakeHandler)
    _, dsl_tool = create_semantic_config_selection_tools(datasource_id="ds_bound")

    result = asyncio.run(dsl_tool.ainvoke({
        "semantic_config_id": "sc_order_operating_baseline_v1",
        "dsl": {
            "metrics": [],
            "dimensions": [
                "明细编号",
                "items_订单编号",
                "商品ID",
                "购买数量",
                "单价",
                "折扣率",
                "小计金额",
                "商品名称",
                "商品分类",
                "用户名称",
                "下单时间",
                "订单状态",
            ],
            "filters": [
                {"dimension": "下单时间", "operator": ">=", "value": "2026-07-01 00:00:00"},
                {"dimension": "下单时间", "operator": "<", "value": "2026-07-02 00:00:00"},
            ],
        },
        "state": {"messages": [HumanMessage(content="查询昨天订单明细")]},
    }))

    assert "SELECT 1" in result
    assert captured["metrics"] == []
    assert captured["dimensions"][0] == "明细编号"
    assert captured["filters"][1]["operator"] == "<"
    assert captured["user_query"] == "查询昨天订单明细"
