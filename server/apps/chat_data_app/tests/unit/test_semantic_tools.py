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

from apps.chat_data_app.agent.tools.semantic_search import (
    build_semantic_field_index,
    extract_datasource_id,
    extract_datasource_group_context,
    extract_user_query,
    _selection_to_tool_payload,
    create_semantic_search,
)
from apps.chat_data_app.services.semantic.run.semantic_model_repository import list_runtime_semantic_config_directory, load_runtime_semantic_config
from utils.builtin_skills import DATA_ANALYZING_SKILL


def _patch_runtime_configs(monkeypatch, configs=None):
    """打桩 _search_models 的全量候选拉取，避免触库。"""

    class FakeConfigService:
        async def get_runtime_configs_async(self, datasource_id, user_id=""):
            return list(configs or [])

    monkeypatch.setattr(
        "apps.chat_data_app.agent.tools.semantic_search.get_semantic_config_service",
        lambda: FakeConfigService(),
    )


def test_semantic_search_description_defers_to_data_analyzing_skill():
    """工具契约不再自行教检索流程，统一指向该技能。

    工具描述是常量 docstring（f-string 不能当 docstring，``__doc__`` 会变 ``None``），插不进常量，
    所以反过来断言「描述里写死的技能名 == 常量」。**这里用技能名而不是显示名**：显示名是
    frontmatter ``title``，属于展示层，2026-09-18 当天就被改过两次，绑显示名的字面量会跟着红。
    """
    description = create_semantic_search(datasource_id="ds_a").description

    assert "当前用户可见的全部启用语义模型" in description
    assert f"`{DATA_ANALYZING_SKILL}`" in description
    assert "本工具不重复定义" in description
    assert "file_grep" not in description


def test_semantic_search_list_fields_requires_semantic_config_id():
    """list_fields 必须显式传 semantic_config_id（原 SemanticToolInput 校验分支）。"""
    tool = create_semantic_search(datasource_id="ds_a")

    with pytest.raises(ValueError, match="必须传 semantic_config_id"):
        asyncio.run(tool.ainvoke({"operation": "list_fields"}))


def test_extract_datasource_id_from_state():
    assert extract_datasource_id({"datasource_id": "ds_001"}) == "ds_001"
    assert extract_datasource_id({"datasource_id": "  ds_002  "}) == "ds_002"
    assert extract_datasource_id({"datasource_id": ""}) is None
    assert extract_datasource_id({}) is None
    assert extract_datasource_id(None) is None
    assert extract_datasource_group_context({"datasource_group_context": "  分库说明  "}) == "分库说明"
    assert extract_datasource_group_context({}) == ""
    assert extract_datasource_group_context(None) == ""


def test_semantic_search_uses_bound_datasource_group_context(monkeypatch):
    captured = {}
    _patch_runtime_configs(monkeypatch, [])

    async def fake_search_semantic_models(**kwargs):
        captured.update(kwargs)
        return {"semantic_config_ids": []}

    monkeypatch.setattr(
        "apps.chat_data_app.agent.tools.semantic_search.search_semantic_models",
        fake_search_semantic_models,
    )
    tool = create_semantic_search(
        datasource_id="ds_bound",
        datasource_group_context="仅查询已授权机构",
    )

    asyncio.run(tool.ainvoke({"operation": "search_models", "query": "查询订单金额"}))

    assert captured["datasource_id"] == "ds_bound"
    assert captured["datasource_group_context"] == "仅查询已授权机构"


def test_extract_user_query_reads_last_human_message():
    assert extract_user_query(None) is None
    assert extract_user_query({}) is None
    assert extract_user_query({"messages": [HumanMessage(content="第一次"), AIMessage(content="回复"), HumanMessage(content="第二次")]}) == "第二次"
    assert extract_user_query({"messages": [AIMessage(content="没有用户消息")]}) is None


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


def test_list_runtime_semantic_config_directory_uses_semantic_service(monkeypatch):
    captured = {}

    class FakeSemanticService:
        async def get_directory_async(self, datasource_id, user_id=""):
            assert datasource_id == "ds_legacy"
            captured["user_id"] = user_id
            return [
                {
                    "id": "sc_legacy",
                    "name": "历史指标集",
                    "description": "历史描述",
                    "enabled": True,
                    "metrics_total": 0,
                    "dimensions_total": 0,
                },
            ]

    monkeypatch.setattr("apps.chat_data_app.services.semantic.run.semantic_model_repository.get_semantic_config_service", lambda: FakeSemanticService())

    directory = asyncio.run(list_runtime_semantic_config_directory("ds_legacy", "u2"))

    assert captured["user_id"] == "u2"
    assert directory == [{
        "id": "sc_legacy",
        "name": "历史指标集",
        "description": "历史描述",
        "enabled": True,
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

    runtime_manager = RuntimeManager()

    class RuntimeSemanticService:
        async def get_runtime_config_async(self, datasource_id, config_id, user_id=""):
            if datasource_id == "missing":
                return None
            return await runtime_manager.get_runtime_semantic_config_async(datasource_id, config_id)

    monkeypatch.setattr("apps.chat_data_app.services.semantic.run.semantic_model_repository.get_datasource_manager", lambda: runtime_manager)
    monkeypatch.setattr("apps.chat_data_app.services.semantic.run.semantic_model_repository.get_semantic_config_service", lambda: RuntimeSemanticService())

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

    fallback_manager = FallbackManager()

    class FallbackSemanticService:
        async def get_runtime_config_async(self, datasource_id, config_id, user_id=""):
            return await fallback_manager.get_semantic_config_async(datasource_id, config_id)

    monkeypatch.setattr("apps.chat_data_app.services.semantic.run.semantic_model_repository.get_datasource_manager", lambda: fallback_manager)
    monkeypatch.setattr("apps.chat_data_app.services.semantic.run.semantic_model_repository.get_semantic_config_service", lambda: FallbackSemanticService())
    fallback = asyncio.run(load_runtime_semantic_config("ds", "cfg_b"))
    assert fallback["source_type"] == "mysql"
    assert fallback["joins"] == [{"left_table": "kept"}]


def test_semantic_search_uses_bound_datasource_id(monkeypatch):
    captured = {}
    _patch_runtime_configs(monkeypatch, [
        {"id": "sc_a", "enabled": True},
        {"id": "sc_b", "enabled": False},
    ])

    async def fake_search_semantic_models(
        datasource_id,
        user_query,
        model_id=None,
        user_id=None,
        datasource_group_context=None,
        **_kwargs,
    ):
        assert "semantic_configs" in _kwargs, "打分器必须复用调用方拉取的全量候选"
        captured.update({
            "datasource_id": datasource_id,
            "user_query": user_query,
            "model_id": model_id,
            "user_id": user_id,
            "datasource_group_context": datasource_group_context,
        })
        return {"semantic_config_ids": ["sc_a"]}

    monkeypatch.setattr("apps.chat_data_app.agent.tools.semantic_search.search_semantic_models", fake_search_semantic_models)
    class FakeRuntime:
        pass

    async def fake_materialize(**kwargs):
        captured["materialize"] = kwargs
        return {
            "directory": [{
                "id": "sc_a",
                "name": "订单分析",
                "description": "",
                "metrics_total": 0,
                "dimensions_total": 0,
                "path": "/mnt/user-data/workspace/semantic/sc_a.yaml",
            }],
        }

    monkeypatch.setattr("apps.chat_data_app.agent.tools.semantic_search.materialize_runtime_semantic_models", fake_materialize)

    tool = create_semantic_search(
        model_id="model_a",
        datasource_id="ds_bound",
        user_id="u1",
        datasource_group_context="当前数据源组按执行对象数据源分库",
        workspace_runtime=FakeRuntime(),
    )
    result = asyncio.run(tool.ainvoke({
        "operation": "search_models",
        "query": "查询订单金额",
    }))
    payload = json.loads(result.replace("[semantic_search]", "", 1).strip())

    assert captured["datasource_id"] == "ds_bound"
    assert captured["user_query"] == "查询订单金额"
    assert captured["model_id"] == "model_a"
    assert captured["user_id"] == "u1"
    assert captured["datasource_group_context"] == "当前数据源组按执行对象数据源分库"
    # 全量物化：物化范围是全部启用候选（sc_b 被禁用被排除），不是 LLM 召回的 top5
    assert captured["materialize"]["semantic_config_ids"] == ["sc_a"]
    assert payload["semantic_config_ids"] == ["sc_a"]
    assert payload["semantic_configs"]["sc_a"]["name"] == "订单分析"
    assert payload["semantic_configs"]["sc_a"]["path"].endswith("/sc_a.yaml")
    assert payload["semantic_dir"] == "/mnt/user-data/workspace/semantic"
    assert payload["materialized_total"] == 1


def test_semantic_search_returns_lightweight_references_for_all_selected_configs(monkeypatch):
    _patch_runtime_configs(monkeypatch, [{"id": "sc_order", "enabled": True}, {"id": "sc_user", "enabled": True}])

    async def fake_search_semantic_models(datasource_id, user_query, **_kwargs):
        assert datasource_id == "ds_bound"
        return {"semantic_config_ids": ["sc_order", "sc_user"]}

    monkeypatch.setattr("apps.chat_data_app.agent.tools.semantic_search.search_semantic_models", fake_search_semantic_models)
    async def fake_materialize(**_kwargs):
        return {"directory": [
            {"id": "sc_order", "name": "订单分析", "metrics_total": 1, "dimensions_total": 0, "path": "/mnt/user-data/workspace/semantic/sc_order.yaml"},
            {"id": "sc_user", "name": "用户分析", "metrics_total": 1, "dimensions_total": 0, "path": "/mnt/user-data/workspace/semantic/sc_user.yaml"},
        ]}

    monkeypatch.setattr("apps.chat_data_app.agent.tools.semantic_search.materialize_runtime_semantic_models", fake_materialize)

    tool = create_semantic_search(datasource_id="ds_bound", workspace_runtime=object())
    result = asyncio.run(tool.ainvoke({"operation": "search_models", "query": "查询订单金额和用户数"}))
    payload = json.loads(result.replace("[semantic_search]", "", 1).strip())

    assert payload["semantic_config_ids"] == ["sc_order", "sc_user"]
    assert payload["semantic_configs"]["sc_order"]["metrics_total"] == 1
    assert payload["semantic_configs"]["sc_user"]["name"] == "用户分析"
    assert all("path" in item for item in payload["semantic_configs"].values())
    assert all("metrics_total" in item and "dimensions_total" in item for item in payload["semantic_configs"].values())


def test_semantic_search_returns_selected_reference_without_full_config(monkeypatch):
    _patch_runtime_configs(monkeypatch, [{"id": "sc_order", "enabled": True}])

    async def fake_search_semantic_models(**_kwargs):
        return {"semantic_config_ids": ["sc_order"]}

    monkeypatch.setattr("apps.chat_data_app.agent.tools.semantic_search.search_semantic_models", fake_search_semantic_models)
    async def fake_materialize(**_kwargs):
        return {"directory": [{
            "id": "sc_order",
            "name": "订单分析",
            "metrics_total": 1,
            "dimensions_total": 1,
            "path": "/mnt/user-data/workspace/semantic/sc_order.yaml",
        }]}

    monkeypatch.setattr("apps.chat_data_app.agent.tools.semantic_search.materialize_runtime_semantic_models", fake_materialize)

    tool = create_semantic_search(datasource_id="ds_bound", workspace_runtime=object())
    result = asyncio.run(tool.ainvoke({"operation": "search_models", "query": "查询订单金额"}))
    payload = json.loads(result.replace("[semantic_search]", "", 1).strip())

    selected_config = payload["semantic_configs"]["sc_order"]
    assert set(selected_config) == {"id", "name", "metrics_total", "dimensions_total", "path"}
    assert selected_config["metrics_total"] == 1
    assert selected_config["dimensions_total"] == 1
    assert selected_config["path"].endswith("/sc_order.yaml")


def test_semantic_search_returns_all_selected_configs_without_own_context_budget(monkeypatch):
    _patch_runtime_configs(monkeypatch, [{"id": "sc_order", "enabled": True}, {"id": "sc_user", "enabled": True}])

    async def fake_search_semantic_models(**_kwargs):
        return {"semantic_config_ids": ["sc_order", "sc_user"]}

    async def fake_load_runtime_semantic_config(_datasource_id, semantic_config_id):
        return {
            "id": semantic_config_id,
            "name": semantic_config_id,
            "metrics": [{"name": "指标", "description": "指标说明" * 8}],
            "dimensions": [],
        }

    monkeypatch.setattr("apps.chat_data_app.agent.tools.semantic_search.search_semantic_models", fake_search_semantic_models)
    async def fake_materialize(**_kwargs):
        return {"directory": [
            {"id": "sc_order", "name": "sc_order", "metrics_total": 1, "dimensions_total": 0, "path": "/mnt/user-data/workspace/semantic/sc_order.yaml"},
            {"id": "sc_user", "name": "sc_user", "metrics_total": 1, "dimensions_total": 0, "path": "/mnt/user-data/workspace/semantic/sc_user.yaml"},
        ]}

    monkeypatch.setattr("apps.chat_data_app.agent.tools.semantic_search.materialize_runtime_semantic_models", fake_materialize)

    tool = create_semantic_search(datasource_id="ds_bound", workspace_runtime=object())
    result = asyncio.run(tool.ainvoke({"operation": "search_models", "query": "查询订单和用户", "state": {"messages": []}}))
    payload = json.loads(result.replace("[semantic_search]", "", 1).strip())

    assert payload["semantic_config_ids"] == ["sc_order", "sc_user"]
    assert list(payload["semantic_configs"]) == ["sc_order", "sc_user"]


def test_build_semantic_field_index_is_lightweight_and_stable():
    long_description = "指标口径" * 100
    config = {
        "metrics": [
            {
                "name": "订单总额",
                "description": long_description,
                "expression": "SUM(orders.amount)",
                "table": "orders",
                "column": "amount",
            },
            {"name": "订单总数", "metadata_supplement": "去重订单数"},
        ],
        "dimensions": [
            {
                "name": "下单时间",
                "logical_type": "datetime",
                "expression": "orders.created_at",
                "table": "orders",
                "column": "created_at",
            },
        ],
        "relationships": [{"left_table": "orders", "right_table": "users"}],
    }

    fields = build_semantic_field_index(config)

    assert [item["name"] for item in fields] == ["订单总额", "订单总数", "下单时间"]
    assert [item["field_type"] for item in fields] == ["metric", "metric", "dimension"]
    assert fields[0]["description"].endswith("...")
    assert len(fields[0]["description"]) == 243
    assert fields[1]["description"] == "去重订单数"
    assert fields[2]["logical_type"] == "datetime"
    assert all(
        forbidden not in item
        for item in fields
        for forbidden in ("expression", "table", "column", "relationships", "joins")
    )
    assert [item["name"] for item in build_semantic_field_index(config, field_type="metric")] == ["订单总额", "订单总数"]
    assert [item["name"] for item in build_semantic_field_index(config, field_type="dimension")] == ["下单时间"]


def test_semantic_search_list_fields_reauthorizes_materializes_and_paginates(monkeypatch):
    captured = {}
    config = {
        "id": "sc_order",
        "name": "订单模型",
        "metrics": [
            {"name": "订单总额", "description": "订单金额之和", "expression": "SUM(amount)"},
            {"name": "订单总数", "description": "订单数量", "expression": "COUNT(*)"},
        ],
        "dimensions": [
            {"name": "下单时间", "logical_type": "datetime", "table": "orders", "column": "created_at"},
            {"name": "订单状态", "table": "orders", "column": "status"},
        ],
    }
    reference = {
        "id": "sc_order",
        "name": "订单模型",
        "metrics_total": 2,
        "dimensions_total": 2,
        "path": "/mnt/user-data/workspace/semantic/sc_order.yaml",
    }

    async def fake_materialize(**kwargs):
        captured.update(kwargs)
        return {"configs": {"sc_order": config}, "directory": [reference]}

    monkeypatch.setattr(
        "apps.chat_data_app.agent.tools.semantic_search.materialize_runtime_semantic_models",
        fake_materialize,
    )
    runtime = object()
    tool = create_semantic_search(
        datasource_id="ds_bound",
        user_id="u1",
        source_type="hive",
        workspace_runtime=runtime,
    )

    result = asyncio.run(tool.ainvoke({
        "operation": "list_fields",
        "semantic_config_id": "sc_order",
        "offset": 1,
        "limit": 2,
    }))
    payload = json.loads(result.replace("[semantic_search]", "", 1).strip())

    assert captured == {
        "workspace_runtime": runtime,
        "datasource_id": "ds_bound",
        "user_id": "u1",
        "source_type": "hive",
        "semantic_config_ids": ["sc_order"],
    }
    assert payload["operation"] == "list_fields"
    assert payload["semantic_config"]["path"] == reference["path"]
    assert payload["metrics_total"] == 2
    assert payload["dimensions_total"] == 2
    assert payload["total"] == 4
    assert payload["offset"] == 1
    assert payload["has_more"] is True
    assert payload["next_offset"] == 3
    assert [item["name"] for item in payload["fields"]] == ["订单总数", "下单时间"]
    assert f"{DATA_ANALYZING_SKILL} 技能" in payload["message"]


def test_semantic_search_list_fields_filters_and_rejects_inaccessible_model(monkeypatch):
    async def fake_materialize(**kwargs):
        if kwargs["semantic_config_ids"] == ["missing"]:
            return {"configs": {}, "directory": []}
        return {
            "configs": {
                "sc_order": {
                    "id": "sc_order",
                    "metrics": [{"name": "订单总数"}],
                    "dimensions": [{"name": "下单时间"}, {"name": "订单状态"}],
                },
            },
            "directory": [{
                "id": "sc_order",
                "name": "订单模型",
                "metrics_total": 1,
                "dimensions_total": 2,
                "path": "/mnt/user-data/workspace/semantic/sc_order.yaml",
            }],
        }

    monkeypatch.setattr(
        "apps.chat_data_app.agent.tools.semantic_search.materialize_runtime_semantic_models",
        fake_materialize,
    )
    tool = create_semantic_search(datasource_id="ds_bound", user_id="u1", workspace_runtime=object())

    result = asyncio.run(tool.ainvoke({
        "operation": "list_fields",
        "semantic_config_id": "sc_order",
        "field_type": "dimension",
        "limit": 1,
    }))
    payload = json.loads(result.replace("[semantic_search]", "", 1).strip())
    with pytest.raises(ValueError, match="未找到当前用户可用的语义模型 missing"):
        asyncio.run(tool.ainvoke({
            "operation": "list_fields",
            "semantic_config_id": "missing",
        }))

    assert payload["field_type"] == "dimension"
    assert payload["total"] == 2
    assert payload["next_offset"] == 1
    assert payload["fields"] == [{"field_type": "dimension", "name": "下单时间"}]


def test_semantic_search_error_branches(monkeypatch):
    _patch_runtime_configs(monkeypatch, [])
    select_tool = create_semantic_search(datasource_id="ds_a")

    with pytest.raises(ValueError, match="当前会话未绑定数据源"):
        asyncio.run(create_semantic_search().ainvoke({"operation": "search_models", "query": "查询订单"}))
    with pytest.raises(ValueError, match="缺少用户问题"):
        asyncio.run(select_tool.ainvoke({"operation": "search_models", "query": ""}))

    async def boom_search_semantic_models(**_kwargs):
        raise RuntimeError("selector failed")

    monkeypatch.setattr("apps.chat_data_app.agent.tools.semantic_search.search_semantic_models", boom_search_semantic_models)
    with pytest.raises(RuntimeError, match="selector failed"):
        asyncio.run(select_tool.ainvoke({"operation": "search_models", "query": "查询订单"}))
