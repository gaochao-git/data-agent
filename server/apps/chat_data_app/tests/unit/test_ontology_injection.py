"""本体消费注入单测：开关行为、目录渲染、物化写盘与尽力而为降级。

2026-10-23 对象类型取数迁移 Logical Dataset source binding：目录只渲染
对象类型/属性/关系类型/Action 定义 + 数据源绑定视图，不再渲染语义模型映射。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
import yaml

from apps.chat_data_app.services.ontology import ontology_injection, query_mode
from apps.chat_data_app.services.semantic.run import semantic_materializer


class _FakeRuntime:
    backend_name = "local"

    def __init__(self):
        self.written: dict[str, str] = {}
        self.deleted: list[str] = []

    async def write_internal_text_files(self, entries):
        for path, content in entries:
            self.written[path] = content

    async def delete_internal_text_files(self, path):
        self.deleted.append(str(path))


def _binding() -> dict[str, Any]:
    return {
        "dataset_id": 11,
        "dataset_name": "order_dataset",
        "dataset_display_name": "订单数据集",
        "physical_table": "DWD_ORDER",
        "members": [{"member_datasource_id": "m1", "physical_table": "DWD_ORDER"}],
        "column_mappings": {"order_id": "order_key", "amount": "amount_value", "user_id": "buyer_id"},
        "columns": {"order_key": "ORDER_ID", "amount_value": "AMOUNT", "buyer_id": "USER_ID"},
    }


def _bundle() -> dict[str, Any]:
    return {
        "datasource_group_id": "ds_a",
        "object_types": [
            {
                "id": 2,
                "object_type_name": "user",
                "display_name": "用户",
                "status": "experimental",
                "description": "下单用户",
                "source_binding": {
                    "dataset_id": 12,
                    "dataset_name": "user_dataset",
                    "physical_table": "DWD_USER",
                    "members": [{"member_datasource_id": "m1", "physical_table": "DWD_USER"}],
                    "column_mappings": {"user_id": "uid"},
                    "columns": {"uid": "UID"},
                },
            },
            {
                "id": 1,
                "object_type_name": "order",
                "display_name": "订单",
                "status": "certified",
                "description": "销售订单",
                "source_binding": _binding(),
            },
        ],
        "properties": [
            {
                "object_type_id": 1,
                "property_name": "order_id",
                "display_name": "订单ID",
                "value_type": "String",
                "is_identifier": True,
                "is_title": False,
            },
            {
                "object_type_id": 1,
                "property_name": "amount",
                "display_name": "金额",
                "value_type": "Decimal",
                "is_identifier": False,
                "is_title": False,
            },
            {
                "object_type_id": 2,
                "property_name": "user_id",
                "display_name": "用户ID",
                "value_type": "String",
                "is_identifier": True,
                "is_title": True,
            },
        ],
        "relation_types": [
            {
                "relation_type_name": "order_belongs_to_user",
                "from_object_type_id": 1,
                "to_object_type_id": 2,
                "from_columns": ["user_id"],
                "to_columns": ["user_id"],
                "multiplicity": "many_to_one",
                "from_display_name": "下单用户",
                "to_display_name": "名下订单",
                "from_verbalize": "{order} 归属于 {user}",
                "to_verbalize": "",
                "description": "订单归属用户",
                "status": "certified",
            }
        ],
    }


def _actions() -> list[dict[str, Any]]:
    return [
        {
            "action_type_name": "ship_order",
            "display_name": "发货",
            "object_type_id": 1,
            "object_type_name": "order",
            "status": "experimental",
            "description": "标记发货",
            "parameters": [{"name": "carrier", "type": "String", "required": True, "description": "承运商"}],
        },
        {
            "action_type_name": "other_domain_action",
            "display_name": "域外动作",
            "object_type_id": 99,
            "object_type_name": "other",
            "status": "certified",
            "parameters": [],
        },
    ]


def _render(bundle: dict[str, Any], action_types: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return yaml.safe_load(ontology_injection._render_catalog(bundle, action_types))


@pytest.fixture()
def enabled(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_INJECTION_ENABLED", "1")
    query_mode.clear_query_mode_cache()


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CHAT_DATA_ONTOLOGY_INJECTION_ENABLED", raising=False)
    assert ontology_injection.ontology_injection_enabled() is False


def test_render_catalog_shapes_object_types_with_source_binding():
    doc = _render(_bundle(), _actions())
    assert doc["datasource_group_id"] == "ds_a"
    # 对象类型按名字升序
    assert [obj["object_type"] for obj in doc["object_types"]] == ["order", "user"]
    order = doc["object_types"][0]
    assert order["display_name"] == "订单"
    assert {prop["name"] for prop in order["properties"]} == {"order_id", "amount"}
    binding = order["source_binding"]
    assert binding["dataset"] == "order_dataset"
    assert binding["physical_table"] == "DWD_ORDER"
    assert binding["members"] == [{"member_datasource_id": "m1", "physical_table": "DWD_ORDER"}]
    # 属性 → 逻辑列 → 物理列双层映射：模型据此写对象查询，不依赖语义模型
    assert binding["column_mappings"]["amount"] == "amount_value"
    assert binding["columns"]["amount_value"] == "AMOUNT"
    # 语义模型索引段不存在（二选一下，本体目录不携带语义模型内容）
    assert "models" not in doc


def test_render_relation_join_translates_physical_columns():
    doc = _render(_bundle())
    rel = doc["relation_types"][0]
    assert rel["from"] == "order" and rel["to"] == "user"
    # 属性 user_id → 逻辑列 buyer_id/uid → 物理列 USER_ID/UID
    assert rel["from_columns"] == ["USER_ID"]
    assert rel["to_columns"] == ["UID"]
    assert rel["description"] == "订单归属用户"
    assert "unresolved" not in rel


def test_render_relation_marks_unresolved_when_column_falls_back():
    bundle = _bundle()
    bundle["relation_types"][0]["from_columns"] = ["order_id", "raw_col"]
    doc = _render(bundle)
    rel = doc["relation_types"][0]
    # order_id 可翻译；raw_col 未映射 → 保留原名并标 unresolved
    assert rel["from_columns"] == ["ORDER_ID", "raw_col"]
    assert rel["unresolved"] is True


def test_render_action_types_filtered_to_domain_object_types():
    doc = _render(_bundle(), _actions())
    assert [action["action"] for action in doc["action_types"]] == ["ship_order"]
    action = doc["action_types"][0]
    assert action["object_type"] == "order"
    assert action["parameters"] == [{"name": "carrier", "type": "String", "required": True, "description": "承运商"}]


def test_properties_carry_enum_ref_and_description():
    bundle = _bundle()
    bundle["properties"][0].update({"value_type": "enum", "enum_ref": "order_status", "description": "订单状态"})
    doc = _render(bundle)
    props = {prop["name"]: prop for prop in doc["object_types"][0]["properties"]}
    assert props["order_id"]["enum_ref"] == "order_status"
    assert props["order_id"]["description"] == "订单状态"
    assert "enum_ref" not in props["amount"]


def _large_bundle(object_type_count: int = 20, property_count: int = 40) -> dict[str, Any]:
    object_types = []
    properties = []
    for index in range(object_type_count):
        object_type_id = index + 1
        object_types.append(
            {
                "id": object_type_id,
                "object_type_name": f"obj_{index:02d}",
                "display_name": f"对象类型{index:02d}",
                "status": "certified",
                "description": f"第 {index} 个对象类型",
                "source_binding": _binding(),
            }
        )
        for prop_index in range(property_count):
            properties.append(
                {
                    "object_type_id": object_type_id,
                    "property_name": f"prop_{prop_index:02d}",
                    "display_name": f"属性{prop_index:02d}",
                    "value_type": "String",
                    "is_identifier": prop_index == 0,
                    "is_title": False,
                }
            )
    relation_types = [
        {
            "relation_type_name": "rel_keep",
            "from_object_type_id": 1,
            "to_object_type_id": 2,
            "from_columns": ["prop_00"],
            "to_columns": ["prop_00"],
            "multiplicity": "many_to_one",
        },
        {
            "relation_type_name": "rel_dropped",
            "from_object_type_id": 1,
            "to_object_type_id": object_type_count,
            "from_columns": ["prop_00"],
            "to_columns": ["prop_00"],
            "multiplicity": "many_to_one",
        },
    ]
    return {
        "datasource_group_id": "ds_a",
        "object_types": object_types,
        "properties": properties,
        "relation_types": relation_types,
    }


def test_catalog_truncates_objects_by_character_budget():
    """超预算时按对象类型名顺序截断，只保留截断后对象类型间的关系类型。"""
    bundle = _large_bundle()
    doc = _render(bundle)
    assert doc["truncated"] is True
    assert doc["omitted_objects"] > 0
    assert len(doc["object_types"]) + doc["omitted_objects"] == len(bundle["object_types"])
    assert len(doc["object_types"]) < len(bundle["object_types"])
    kept_names = [obj["object_type"] for obj in doc["object_types"]]
    assert kept_names == sorted(kept_names)
    assert [rel["name"] for rel in doc["relation_types"]] == ["rel_keep"]


def test_catalog_has_no_truncation_fields_within_budget():
    doc = _render(_bundle())
    assert "truncated" not in doc
    assert "omitted_objects" not in doc


def test_materialize_writes_catalog_and_counts_objects(enabled):
    runtime = _FakeRuntime()
    count = asyncio.run(
        ontology_injection.materialize_ontology_catalog_async(
            runtime,
            datasource_group_id="ds_a",
            bundle=_bundle(),
            action_types=_actions(),
        )
    )
    assert count == 2
    path = f"{ontology_injection.ONTOLOGY_SNAPSHOT_DIR}/{ontology_injection.ONTOLOGY_CATALOG_FILE_NAME}"
    assert path in runtime.written
    assert "/workspace/semantic/_ontology_catalog.yaml" in path
    doc = yaml.safe_load(runtime.written[path])
    assert [obj["object_type"] for obj in doc["object_types"]] == ["order", "user"]
    # 旧版独立 ontology/ 目录残留被顺手清理
    assert runtime.deleted[0] == ontology_injection.ONTOLOGY_LEGACY_CATALOG_PATH


def test_materialize_skips_when_disabled(monkeypatch):
    monkeypatch.delenv("CHAT_DATA_ONTOLOGY_INJECTION_ENABLED", raising=False)
    runtime = _FakeRuntime()
    count = asyncio.run(
        ontology_injection.materialize_ontology_catalog_async(runtime, datasource_group_id="ds_a", bundle=_bundle())
    )
    assert count == 0
    assert runtime.written == {}


def test_materialize_skips_without_datasource_group(enabled):
    runtime = _FakeRuntime()
    assert (
        asyncio.run(
            ontology_injection.materialize_ontology_catalog_async(runtime, datasource_group_id="", bundle=_bundle())
        )
        == 0
    )
    assert runtime.written == {}


def test_materialize_skips_empty_bundle(enabled):
    runtime = _FakeRuntime()
    count = asyncio.run(
        ontology_injection.materialize_ontology_catalog_async(
            runtime,
            datasource_group_id="ds_a",
            bundle={"datasource_group_id": "ds_a", "object_types": [], "properties": [], "relation_types": []},
        )
    )
    assert count == 0
    assert runtime.written == {}


def test_materialize_swallows_loader_errors(enabled, monkeypatch):
    runtime = _FakeRuntime()

    async def broken_loader(group_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(ontology_injection, "load_domain_ontology_bundle_async", broken_loader)
    count = asyncio.run(ontology_injection.materialize_ontology_catalog_async(runtime, datasource_group_id="ds_a"))
    assert count == 0


def test_materialize_survives_legacy_cleanup_sandbox_denial(enabled, monkeypatch):
    """旧目录清理被沙箱拒绝只记一次 info，不影响目录写入与返回值。"""
    monkeypatch.setattr(ontology_injection, "_legacy_cleanup_denied_logged", False)

    class _DeniedRuntime(_FakeRuntime):
        async def delete_internal_text_files(self, path):
            raise ValueError(f"路径不在允许范围: {path}")

    runtime = _DeniedRuntime()
    count = asyncio.run(
        ontology_injection.materialize_ontology_catalog_async(runtime, datasource_group_id="ds_a", bundle=_bundle())
    )
    assert count == 2
    assert runtime.written
    assert ontology_injection._legacy_cleanup_denied_logged is True


# ---------------------------------------------------------------------------
# prematerialize：模式判定 → 目录二选一
# ---------------------------------------------------------------------------


def _patch_mode(monkeypatch, *, ontology: bool):
    async def fake_resolve(group_id):
        if ontology:
            return query_mode.QueryModeDecision(query_mode.QueryMode.ONTOLOGY, group_id, 1, "domain_object_type_active")
        return query_mode.QueryModeDecision(query_mode.QueryMode.SEMANTIC, group_id, 0, "no_domain_object_type")

    monkeypatch.setattr(semantic_materializer, "resolve_query_mode_async", fake_resolve)


def test_prematerialize_ontology_mode_skips_semantic_catalog(monkeypatch, enabled):
    runtime = _FakeRuntime()
    semantic_calls: list[dict[str, Any]] = []
    ontology_calls: list[dict[str, Any]] = []

    async def fake_semantic(**kwargs):
        semantic_calls.append(kwargs)
        return {"directory": [], "failed_ids": []}

    async def fake_ontology(workspace_runtime, *, datasource_group_id, bundle=None, action_types=None):
        ontology_calls.append({"datasource_group_id": datasource_group_id, "bundle": bundle})
        return 2

    _patch_mode(monkeypatch, ontology=True)
    monkeypatch.setattr(semantic_materializer, "materialize_runtime_semantic_models", fake_semantic)
    monkeypatch.setattr(semantic_materializer, "materialize_ontology_catalog_async", fake_ontology)

    async def scenario() -> dict[str, Any]:
        resolved = await semantic_materializer.prematerialize_enabled_semantic_models(
            workspace_runtime=runtime, datasource_id="ds_a", user_id="u1"
        )
        assert query_mode.current_query_mode().is_ontology is True
        return resolved

    result = asyncio.run(scenario())
    assert result["query_mode"] == "ontology"
    assert result["total"] == 2
    # 语义模型目录被清空（空范围 + prune），本体目录按当前数据源组物化
    assert semantic_calls[0]["semantic_config_ids"] == []
    assert semantic_calls[0]["prune_stale"] is True
    assert ontology_calls == [{"datasource_group_id": "ds_a", "bundle": None}]


def test_prematerialize_semantic_mode_keeps_semantic_catalog(monkeypatch, enabled):
    runtime = _FakeRuntime()
    semantic_calls: list[dict[str, Any]] = []
    ontology_calls: list[dict[str, Any]] = []

    async def fake_bundle(datasource_id, user_id):
        return [{"id": "sc_order", "enabled": True, "name": "订单模型"}], {}

    async def fake_materialize(**kwargs):
        semantic_calls.append(kwargs)
        return {"directory": [{"id": "sc_order"}], "failed_ids": []}

    async def fake_ontology(*args, **kwargs):  # pragma: no cover - 语义模式不得调用
        ontology_calls.append((args, kwargs))
        return 0

    class _StubService:
        get_runtime_bundle_async = staticmethod(fake_bundle)

    _patch_mode(monkeypatch, ontology=False)
    monkeypatch.setattr(semantic_materializer, "get_semantic_config_service", lambda: _StubService())
    monkeypatch.setattr(semantic_materializer, "materialize_runtime_semantic_models", fake_materialize)
    monkeypatch.setattr(semantic_materializer, "materialize_ontology_catalog_async", fake_ontology)

    async def scenario() -> dict[str, Any]:
        resolved = await semantic_materializer.prematerialize_enabled_semantic_models(
            workspace_runtime=runtime, datasource_id="ds_a", user_id="u1"
        )
        assert query_mode.current_query_mode().is_semantic is True
        return resolved

    result = asyncio.run(scenario())
    assert result["query_mode"] == "semantic"
    assert result["total"] == 1
    assert semantic_calls[0]["include_semantic_catalog"] is True
    assert semantic_calls[0]["keep_extra_paths"] is None
    assert ontology_calls == []


def test_prematerialize_without_datasource_publishes_semantic(monkeypatch):
    runtime = _FakeRuntime()
    query_mode.clear_query_mode_cache()

    async def scenario() -> dict[str, Any]:
        resolved = await semantic_materializer.prematerialize_enabled_semantic_models(
            workspace_runtime=runtime, datasource_id=None, user_id="u1"
        )
        assert query_mode.current_query_mode().is_semantic is True
        assert query_mode.current_query_mode().reason == "no_datasource_group"
        return resolved

    result = asyncio.run(scenario())
    assert result["total"] == 0
