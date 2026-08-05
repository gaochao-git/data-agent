import os
import sys
import json
import asyncio

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from langchain_core.messages import AIMessage, HumanMessage

from apps.chat_data_app.agent.tools.search_dataset import create_search_dataset_tool
from apps.chat_data_app.services.semantic_tools import (
    extract_datasource_id,
    extract_datasource_group_context,
    extract_user_query,
    format_semantic_config_directory_for_prompt,
    list_runtime_semantic_config_directory,
    load_runtime_semantic_config,
    _is_semantic_config_directory_query,
    _selection_to_tool_payload,
)


def _semantic_snapshot(configs):
    directory = []
    paths = {}
    indexed = {}
    for config in configs:
        config_id = config["id"]
        path = f"/mnt/user-data/workspace/semantic/{config_id}.yaml"
        directory.append({
            "id": config_id,
            "name": config.get("name", config_id),
            "description": config.get("description", ""),
            "metrics_total": len(config.get("metrics") or []),
            "dimensions_total": len(config.get("dimensions") or []),
            "path": path,
        })
        paths[config_id] = path
        indexed[config_id] = config
    return {"snapshot_id": "semantic", "directory": directory, "paths": paths, "configs": indexed}


def test_extract_datasource_id_from_state():
    assert extract_datasource_id({"datasource_id": "ds_001"}) == "ds_001"
    assert extract_datasource_id({"datasource_id": "  ds_002  "}) == "ds_002"
    assert extract_datasource_id({"datasource_id": ""}) is None
    assert extract_datasource_id({}) is None
    assert extract_datasource_id(None) is None
    assert extract_datasource_group_context({"datasource_group_context": "  分库说明  "}) == "分库说明"
    assert extract_datasource_group_context({}) == ""
    assert extract_datasource_group_context(None) == ""


def test_search_dataset_uses_bound_datasource_group_context(monkeypatch):
    captured = {}

    async def fake_select_semantic_config_for_query(**kwargs):
        captured.update(kwargs)
        return {"semantic_config_ids": []}

    monkeypatch.setattr(
        "apps.chat_data_app.agent.tools.search_dataset.select_semantic_config_for_query",
        fake_select_semantic_config_for_query,
    )
    tool = create_search_dataset_tool(
        datasource_id="ds_bound",
        datasource_group_context="仅查询已授权机构",
    )

    asyncio.run(tool.ainvoke({"query": "查询订单金额"}))

    assert captured["datasource_id"] == "ds_bound"
    assert captured["datasource_group_context"] == "仅查询已授权机构"


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
    assert "ID：sc_order" in prompt
    assert "名称：订单分析" in prompt
    assert "YAML路径：/mnt/user-data/workspace/semantic/sc_order.yaml" in prompt
    assert f"描述：{long_description}" in prompt
    assert "指标数：3；维度数：5" in prompt
    assert "无名称会跳过" not in prompt
    assert format_semantic_config_directory_for_prompt([]) == ""


def test_selection_to_tool_payload_branches():
    empty = _selection_to_tool_payload(None)
    payload = _selection_to_tool_payload(
        {"semantic_config_ids": ["cfg_order", "cfg_user", "missing"]},
        {
            "cfg_order": {"id": "cfg_order", "name": "订单分析", "description": "", "metrics_total": 1, "dimensions_total": 0, "path": "/mnt/user-data/workspace/semantic/order.yaml"},
            "cfg_user": {"id": "cfg_user", "name": "用户分析", "description": "", "metrics_total": 0, "dimensions_total": 1, "path": "/mnt/user-data/workspace/semantic/user.yaml"},
        },
    )

    assert empty["success"] is False
    assert empty["message"].startswith("当前数据源组没有")
    assert payload["success"] is True
    assert payload["semantic_config_ids"] == ["cfg_order", "cfg_user"]
    assert payload["semantic_configs"]["cfg_order"] == {
        "id": "cfg_order",
        "name": "订单分析",
        "description": "",
        "metrics_total": 1,
        "dimensions_total": 0,
        "path": "/mnt/user-data/workspace/semantic/order.yaml",
    }
    assert "query_plan" not in payload
    assert "cross_config_required" not in payload


def test_search_dataset_returns_full_directory_for_directory_query(monkeypatch):
    captured = {}

    class FakeDatasourceManager:
        async def get_semantic_config_directory_async(self, datasource_id, user_id=""):
            assert datasource_id == "ds_bound"
            captured["user_id"] = user_id
            return [
                {"id": "sc_order", "name": "订单分析", "description": "订单经营分析", "enabled": True, "metrics_total": 1, "dimensions_total": 1},
                {"id": "sc_disabled", "name": "停用指标集", "enabled": False, "metrics_total": 1, "dimensions_total": 0},
                {"id": "sc_student", "name": "学生课程选课分析", "description": "选课与成绩分析", "enabled": True, "metrics_total": 1, "dimensions_total": 2},
            ]

        async def get_runtime_semantic_configs_async(self, *_args, **_kwargs):
            raise AssertionError("directory query must not load runtime semantic configs")

    async def fail_select_semantic_config_for_query(*_args, **_kwargs):
        raise AssertionError("directory query should not call selector llm")

    monkeypatch.setattr("apps.chat_data_app.services.semantic_tools.get_datasource_manager", lambda: FakeDatasourceManager())
    monkeypatch.setattr("apps.chat_data_app.agent.tools.search_dataset.select_semantic_config_for_query", fail_select_semantic_config_for_query)

    tool = create_search_dataset_tool(datasource_id="ds_bound", user_id="u1")
    result = asyncio.run(tool.ainvoke({"query": "你现在能看到哪些指标集"}))
    payload = json.loads(result.replace("[search_dataset]", "", 1).strip())

    assert captured["user_id"] == "u1"
    assert payload["directory"] is True
    assert payload["semantic_config_ids"] == []
    assert [item["name"] for item in payload["semantic_config_directory"]] == ["订单分析", "学生课程选课分析"]
    assert payload["semantic_config_directory"][0]["metrics_total"] == 1
    assert payload["semantic_config_directory"][1]["dimensions_total"] == 2


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
        "path": "/mnt/user-data/workspace/semantic/sc_legacy.yaml",
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


def test_search_dataset_uses_bound_datasource_id(monkeypatch):
    captured = {}

    async def fake_select_semantic_config_for_query(
        datasource_id,
        user_query,
        model_id=None,
        user_id=None,
        datasource_group_context=None,
        semantic_configs=None,
    ):
        captured.update({
            "datasource_id": datasource_id,
            "user_query": user_query,
            "model_id": model_id,
            "user_id": user_id,
            "datasource_group_context": datasource_group_context,
            "semantic_configs": semantic_configs,
        })
        return {"semantic_config_ids": ["sc_a"]}

    monkeypatch.setattr("apps.chat_data_app.agent.tools.search_dataset.select_semantic_config_for_query", fake_select_semantic_config_for_query)
    config = {"id": "sc_a", "name": "订单分析", "metrics": [], "dimensions": []}

    tool = create_search_dataset_tool(
        model_id="model_a",
        datasource_id="ds_bound",
        user_id="u1",
        datasource_group_context="当前数据源组按执行对象数据源分库",
        semantic_snapshot=_semantic_snapshot([config]),
    )
    result = asyncio.run(tool.ainvoke({
        "query": "查询订单金额",
    }))
    payload = json.loads(result.replace("[search_dataset]", "", 1).strip())

    assert captured["datasource_id"] == "ds_bound"
    assert captured["user_query"] == "查询订单金额"
    assert captured["model_id"] == "model_a"
    assert captured["user_id"] == "u1"
    assert captured["datasource_group_context"] == "当前数据源组按执行对象数据源分库"
    assert captured["semantic_configs"] == [config]
    assert payload["semantic_config_ids"] == ["sc_a"]
    assert payload["semantic_configs"]["sc_a"]["name"] == "订单分析"
    assert payload["semantic_configs"]["sc_a"]["path"].endswith("/sc_a.yaml")


def test_search_dataset_returns_lightweight_references_for_all_selected_configs(monkeypatch):
    async def fake_select_semantic_config_for_query(datasource_id, user_query, model_id=None, user_id=None, datasource_group_context=None, semantic_configs=None):
        assert datasource_id == "ds_bound"
        return {"semantic_config_ids": ["sc_order", "sc_user"]}

    configs = [
        {"id": "sc_order", "name": "订单分析", "metrics": [{"name": "订单金额"}], "dimensions": []},
        {"id": "sc_user", "name": "用户分析", "metrics": [{"name": "用户数"}], "dimensions": []},
    ]

    monkeypatch.setattr("apps.chat_data_app.agent.tools.search_dataset.select_semantic_config_for_query", fake_select_semantic_config_for_query)

    tool = create_search_dataset_tool(datasource_id="ds_bound", semantic_snapshot=_semantic_snapshot(configs))
    result = asyncio.run(tool.ainvoke({"query": "查询订单金额和用户数"}))
    payload = json.loads(result.replace("[search_dataset]", "", 1).strip())

    assert payload["semantic_config_ids"] == ["sc_order", "sc_user"]
    assert payload["semantic_configs"]["sc_order"]["metrics_total"] == 1
    assert payload["semantic_configs"]["sc_user"]["name"] == "用户分析"
    assert all(
        set(item) == {"id", "name", "description", "metrics_total", "dimensions_total", "path"}
        for item in payload["semantic_configs"].values()
    )


def test_search_dataset_returns_selected_reference_without_full_config(monkeypatch):
    async def fake_select_semantic_config_for_query(**_kwargs):
        return {"semantic_config_ids": ["sc_order"]}

    config = {
        "id": "sc_order",
        "name": "订单分析",
        "metrics": [{"name": "订单金额", "table": "orders"}],
        "dimensions": [{"name": "用户名称", "table": "users"}],
        "relationships": [{"left_table": "orders", "right_table": "users"}],
        "joins": [{"left_table": "orders", "right_table": "users"}],
        "tables": [{"table_name": "orders", "columns": [{"name": "amount"}]}],
    }

    monkeypatch.setattr("apps.chat_data_app.agent.tools.search_dataset.select_semantic_config_for_query", fake_select_semantic_config_for_query)

    tool = create_search_dataset_tool(datasource_id="ds_bound", semantic_snapshot=_semantic_snapshot([config]))
    result = asyncio.run(tool.ainvoke({"query": "查询订单金额"}))
    payload = json.loads(result.replace("[search_dataset]", "", 1).strip())

    selected_config = payload["semantic_configs"]["sc_order"]
    assert set(selected_config) == {"id", "name", "description", "metrics_total", "dimensions_total", "path"}
    assert selected_config["metrics_total"] == 1
    assert selected_config["dimensions_total"] == 1
    assert selected_config["path"].endswith("/sc_order.yaml")


def test_search_dataset_ignores_selected_config_absent_from_snapshot(monkeypatch):
    async def fake_select_semantic_config_for_query(datasource_id, user_query, model_id=None, user_id=None, datasource_group_context=None, semantic_configs=None):
        return {"semantic_config_ids": ["sc_order", "sc_missing"]}

    monkeypatch.setattr("apps.chat_data_app.agent.tools.search_dataset.select_semantic_config_for_query", fake_select_semantic_config_for_query)

    snapshot = _semantic_snapshot([
        {"id": "sc_order", "name": "订单分析", "metrics": [{"name": "订单金额"}], "dimensions": []},
    ])
    tool = create_search_dataset_tool(datasource_id="ds_bound", semantic_snapshot=snapshot)
    result = asyncio.run(tool.ainvoke({"query": "查询订单金额"}))
    payload = json.loads(result.replace("[search_dataset]", "", 1).strip())

    assert payload["semantic_config_ids"] == ["sc_order"]
    assert list(payload["semantic_configs"]) == ["sc_order"]


def test_search_dataset_returns_all_selected_configs_without_own_context_budget(monkeypatch):
    async def fake_select_semantic_config_for_query(**_kwargs):
        return {"semantic_config_ids": ["sc_order", "sc_user"]}

    async def fake_load_runtime_semantic_config(_datasource_id, semantic_config_id):
        return {
            "id": semantic_config_id,
            "name": semantic_config_id,
            "metrics": [{"name": "指标", "description": "指标说明" * 8}],
            "dimensions": [],
        }

    monkeypatch.setattr("apps.chat_data_app.agent.tools.search_dataset.select_semantic_config_for_query", fake_select_semantic_config_for_query)
    monkeypatch.setattr("apps.chat_data_app.services.semantic_tools.load_runtime_semantic_config", fake_load_runtime_semantic_config)

    tool = create_search_dataset_tool(datasource_id="ds_bound")
    result = asyncio.run(tool.ainvoke({"query": "查询订单和用户", "state": {"messages": []}}))
    payload = json.loads(result.replace("[search_dataset]", "", 1).strip())

    assert payload["semantic_config_ids"] == ["sc_order", "sc_user"]
    assert list(payload["semantic_configs"]) == ["sc_order", "sc_user"]


def test_search_dataset_error_branches(monkeypatch):
    select_tool = create_search_dataset_tool(datasource_id="ds_a")

    missing_datasource = asyncio.run(create_search_dataset_tool().ainvoke({"query": "查询订单"}))
    missing_query = asyncio.run(select_tool.ainvoke({"query": ""}))

    async def boom_select_semantic_config_for_query(**_kwargs):
        raise RuntimeError("selector failed")

    monkeypatch.setattr("apps.chat_data_app.agent.tools.search_dataset.select_semantic_config_for_query", boom_select_semantic_config_for_query)
    failed = asyncio.run(select_tool.ainvoke({"query": "查询订单"}))

    assert "未绑定数据源" in missing_datasource
    assert "缺少用户问题" in missing_query
    assert "selector failed" in failed
