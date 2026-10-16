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

    async def write_internal_text_files(self, entries):
        for path, content in entries:
            self.written[path] = content


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
                "table_alias": "orders",
                "column_mappings": {"order_id": "ORDER_ID", "amount": "AMOUNT"},
            },
            {
                "object_id": 2,
                "semantic_config_id": "sc_user",
                "table_alias": "users",
                "column_mappings": {"user_id": "UID"},
            },
        ],
    }


@pytest.fixture()
def enabled(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_INJECTION_ENABLED", "1")


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CHAT_DATA_ONTOLOGY_INJECTION_ENABLED", raising=False)
    assert ontology_injection.ontology_injection_enabled() is False


def test_render_catalog_shapes_objects_and_relations():
    content = ontology_injection._render_catalog(_bundle(), {"sc_order": "订单模型", "sc_user": "用户模型"})
    doc = yaml.safe_load(content)
    assert [obj["object"] for obj in doc["objects"]] == ["order", "user"]
    order = doc["objects"][0]
    assert order["display_name"] == "订单"
    assert {prop["name"] for prop in order["properties"]} == {"order_id", "amount"}
    assert order["bindings"][0]["semantic_config_name"] == "订单模型"
    assert order["bindings"][0]["column_mappings"]["amount"] == "AMOUNT"
    rel = doc["relations"][0]
    assert rel["from"] == "order" and rel["to"] == "user"
    assert rel["from_columns"] == ["user_id"]


def test_materialize_writes_catalog_and_counts_objects(enabled, monkeypatch):
    runtime = _FakeRuntime()

    async def fake_bundle(config_ids):
        assert set(config_ids) == {"sc_order", "sc_user"}
        return _bundle()

    monkeypatch.setattr(ontology_injection.OntologyDao, "load_injection_bundle_async", staticmethod(fake_bundle))
    count = asyncio.run(
        ontology_injection.materialize_ontology_catalog_async(runtime, ["sc_order", "sc_user"], configs=[])
    )
    assert count == 2
    path = f"{ontology_injection.ONTOLOGY_SNAPSHOT_DIR}/{ontology_injection.ONTOLOGY_CATALOG_FILE_NAME}"
    assert path in runtime.written
    doc = yaml.safe_load(runtime.written[path])
    assert len(doc["objects"]) == 2


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
    """预物化主链路把启用模型 ID 与 configs 透传给本体目录物化。"""
    runtime = _FakeRuntime()
    calls: list[tuple[list[str], list[dict[str, Any]]]] = []

    async def fake_catalog(workspace_runtime, config_ids, configs=None):
        calls.append((list(config_ids), list(configs or [])))
        return 1

    async def fake_bundle(datasource_id, user_id):
        return [{"id": "sc_order", "enabled": True, "name": "订单模型"}], {}

    async def fake_materialize(**kwargs):
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
    assert calls and calls[0][0] == ["sc_order"]
    assert calls[0][1] == [{"id": "sc_order", "enabled": True, "name": "订单模型"}]
