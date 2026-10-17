"""本体消费注入单测：开关行为、目录渲染、物化写盘与尽力而为降级。"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
import yaml
from apps.chat_data_app.services.ontology import ontology_injection
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


def _bundle() -> dict[str, Any]:
    return {
        "objects": [
            {
                "id": 1,
                "object_name": "order",
                "display_name": "订单",
                "status": "certified",
                "description": "销售订单",
            },
            {
                "id": 2,
                "object_name": "user",
                "display_name": "用户",
                "status": "experimental",
                "description": "下单用户",
            },
        ],
        "properties": [
            {
                "object_id": 1,
                "property_name": "order_id",
                "display_name": "订单ID",
                "value_type": "String",
                "is_identifier": True,
                "is_title": False,
            },
            {
                "object_id": 1,
                "property_name": "amount",
                "display_name": "金额",
                "value_type": "Decimal",
                "is_identifier": False,
                "is_title": False,
            },
            {
                "object_id": 2,
                "property_name": "user_id",
                "display_name": "用户ID",
                "value_type": "String",
                "is_identifier": True,
                "is_title": True,
            },
        ],
        "relations": [
            {
                "relation_name": "order_belongs_to_user",
                "from_object_id": 1,
                "to_object_id": 2,
                "from_columns": ["user_id"],
                "to_columns": ["user_id"],
                "multiplicity": "many_to_one",
                "from_display_name": "下单用户",
                "to_display_name": "名下订单",
                "from_verbalize": "{order} 归属于 {user}",
                "to_verbalize": "",
            }
        ],
        "mappings": [
            {
                "object_id": 1,
                "semantic_config_id": "sc_order",
                "column_mappings": {"order_id": "订单编号", "amount": "订单金额", "user_id": "下单用户"},
            },
            {
                "object_id": 2,
                "semantic_config_id": "sc_user",
                "column_mappings": {"user_id": "用户编号"},
            },
        ],
    }


@pytest.fixture()
def enabled(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_INJECTION_ENABLED", "1")


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CHAT_DATA_ONTOLOGY_INJECTION_ENABLED", raising=False)
    assert ontology_injection.ontology_injection_enabled() is False


# 维度索引：属性 → 维度名 → 物理列 的第二级翻译来源
_DIMENSION_COLUMNS = {
    "sc_order": {"订单编号": "ORDER_ID", "订单金额": "AMOUNT", "下单用户": "USER_ID"},
    "sc_user": {"用户编号": "UID"},
}


def test_render_catalog_shapes_objects_and_relations():
    content = ontology_injection._render_catalog(
        _bundle(),
        {"sc_order": "订单模型", "sc_user": "用户模型"},
        _DIMENSION_COLUMNS,
        model_files={"sc_order": "订单模型__1234.yaml", "sc_user": "用户模型__5678.yaml"},
    )
    doc = yaml.safe_load(content)
    assert [obj["object"] for obj in doc["objects"]] == ["order", "user"]
    order = doc["objects"][0]
    assert order["display_name"] == "订单"
    assert {prop["name"] for prop in order["properties"]} == {"order_id", "amount"}
    assert order["mappings"][0]["semantic_config_name"] == "订单模型"
    # mappings.file = 模型快照文件名：本体目录接管语义目录索引职责后的取数入口
    assert order["mappings"][0]["file"] == "订单模型__1234.yaml"
    # 映射右值是模型维度名（概念层不重复定义物理列）
    assert order["mappings"][0]["column_mappings"]["amount"] == "订单金额"
    assert "table_alias" not in order["mappings"][0]
    rel = doc["relations"][0]
    assert rel["from"] == "order" and rel["to"] == "user"
    # 关系列两级翻译：属性 user_id → 维度（下单用户/用户编号）→ 物理列
    assert rel["from_columns"] == ["USER_ID"]
    assert rel["to_columns"] == ["UID"]


def test_translate_keeps_unknown_dimension_name():
    """维度/映射任一级未知时保留原文（等价映射天然兼容）。"""
    columns = ontology_injection._translate_to_physical_columns(
        ["user_id"], [{"semantic_config_id": "sc_user", "column_mappings": {"user_id": "用户编号"}}], _DIMENSION_COLUMNS
    )
    assert columns == ["UID"]
    fallback = ontology_injection._translate_to_physical_columns(
        ["raw_col"], [{"semantic_config_id": "sc_user", "column_mappings": {}}], _DIMENSION_COLUMNS
    )
    assert fallback == ["raw_col"]


def test_materialize_writes_catalog_and_counts_objects(enabled, monkeypatch):
    runtime = _FakeRuntime()

    async def fake_bundle(config_ids):
        assert set(config_ids) == {"sc_order", "sc_user"}
        return _bundle()

    monkeypatch.setattr(ontology_injection.OntologyDao, "load_injection_bundle_async", staticmethod(fake_bundle))
    count = asyncio.run(
        ontology_injection.materialize_ontology_catalog_async(
            runtime,
            ["sc_order", "sc_user"],
            configs=[],
            model_files={"sc_order": "订单模型__1234.yaml", "sc_user": "用户模型__5678.yaml"},
        )
    )
    assert count == 2
    path = f"{ontology_injection.ONTOLOGY_SNAPSHOT_DIR}/{ontology_injection.ONTOLOGY_CATALOG_FILE_NAME}"
    assert path in runtime.written
    # 本体目录与语义资产同放 workspace/semantic/（不再单开 ontology/ 目录）
    assert "/workspace/semantic/_ontology_catalog.yaml" in path
    doc = yaml.safe_load(runtime.written[path])
    assert len(doc["objects"]) == 2
    assert doc["objects"][0]["mappings"][0]["file"] == "订单模型__1234.yaml"
    # 旧版独立 ontology/ 目录残留被顺手清理
    assert runtime.deleted == [ontology_injection.ONTOLOGY_LEGACY_CATALOG_PATH]


def test_materialize_skips_when_disabled(monkeypatch):
    monkeypatch.delenv("CHAT_DATA_ONTOLOGY_INJECTION_ENABLED", raising=False)
    runtime = _FakeRuntime()
    count = asyncio.run(ontology_injection.materialize_ontology_catalog_async(runtime, ["sc_x"]))
    assert count == 0
    assert runtime.written == {}


def test_materialize_swallows_dao_errors(enabled, monkeypatch):
    runtime = _FakeRuntime()

    async def broken_bundle(config_ids):
        raise RuntimeError("db down")

    monkeypatch.setattr(ontology_injection.OntologyDao, "load_injection_bundle_async", staticmethod(broken_bundle))
    count = asyncio.run(ontology_injection.materialize_ontology_catalog_async(runtime, ["sc_x"], configs=[]))
    assert count == 0


def test_prematerialize_invokes_ontology_injection(monkeypatch, enabled):
    """预物化主链路把启用模型 ID、configs 与模型文件名映射透传给本体目录物化，
    并在本体目录接管时跳过语义资产目录（二选一）。"""
    runtime = _FakeRuntime()
    catalog_calls: list[dict[str, Any]] = []
    materialize_kwargs: list[dict[str, Any]] = []

    async def fake_catalog(workspace_runtime, config_ids, configs=None, model_files=None):
        catalog_calls.append(
            {
                "config_ids": list(config_ids),
                "configs": list(configs or []),
                "model_files": dict(model_files or {}),
            }
        )
        return 1

    async def fake_bundle(datasource_id, user_id):
        return [{"id": "sc_order", "enabled": True, "name": "订单模型"}], {}

    async def fake_materialize(**kwargs):
        materialize_kwargs.append(kwargs)
        return {"directory": [{"id": "sc_order"}], "failed_ids": []}

    # semantic_materializer 在 import 时绑定了函数引用，patch 必须落在它的命名空间
    monkeypatch.setattr(semantic_materializer, "materialize_ontology_catalog_async", fake_catalog)

    class _StubService:
        get_runtime_bundle_async = staticmethod(fake_bundle)

    monkeypatch.setattr(semantic_materializer, "get_semantic_config_service", lambda: _StubService())
    monkeypatch.setattr(semantic_materializer, "materialize_runtime_semantic_models", fake_materialize)
    result = asyncio.run(
        semantic_materializer.prematerialize_enabled_semantic_models(
            workspace_runtime=runtime, datasource_id="ds_1", user_id="u1"
        )
    )
    assert result["total"] == 1
    assert catalog_calls and catalog_calls[0]["config_ids"] == ["sc_order"]
    assert catalog_calls[0]["configs"] == [{"id": "sc_order", "enabled": True, "name": "订单模型"}]
    assert catalog_calls[0]["model_files"] == {"sc_order": "订单模型__order.yaml"}
    # 本体目录接管 → 语义资产目录跳过；本体目录路径进 prune 保留集
    assert materialize_kwargs[0]["include_semantic_catalog"] is False
    assert materialize_kwargs[0]["keep_extra_paths"] == {"/mnt/user-data/workspace/semantic/_ontology_catalog.yaml"}


def test_prematerialize_semantic_catalog_when_no_objects(monkeypatch, enabled):
    """无本体对象时语义资产目录照常物化（二选一回落）。"""
    runtime = _FakeRuntime()
    materialize_kwargs: list[dict[str, Any]] = []

    async def fake_bundle(datasource_id, user_id):
        return [{"id": "sc_order", "enabled": True, "name": "订单模型"}], {}

    async def fake_materialize(**kwargs):
        materialize_kwargs.append(kwargs)
        return {"directory": [{"id": "sc_order"}], "failed_ids": []}

    class _StubService:
        get_runtime_bundle_async = staticmethod(fake_bundle)

    monkeypatch.setattr(semantic_materializer, "get_semantic_config_service", lambda: _StubService())
    monkeypatch.setattr(semantic_materializer, "materialize_runtime_semantic_models", fake_materialize)
    asyncio.run(
        semantic_materializer.prematerialize_enabled_semantic_models(
            workspace_runtime=runtime, datasource_id="ds_1", user_id="u1"
        )
    )
    assert materialize_kwargs[0]["include_semantic_catalog"] is True
    assert materialize_kwargs[0]["keep_extra_paths"] is None
