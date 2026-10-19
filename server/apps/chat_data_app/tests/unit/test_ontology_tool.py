"""ontology_tool 单测：本体对象类型导航工具（list_object_types / locate_object_type）。

2026-10-19 自 test_data_analyzing_tool.py 的本体段落平移：两 action 随工具拆分
独立成测，与 data_analyzing_tool 单测解耦。
"""

import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")

from apps.chat_data_app.agent.tools import ontology_tool
from apps.chat_data_app.services.semantic.run.semantic_materializer import model_snapshot_file_name

RESULT_PREFIX = "[ontology_tool]\n"

_ONTOLOGY_BUNDLE = {
    "object_types": [
        {"id": 1, "object_type_name": "order", "display_name": "订单", "status": "certified"},
        {"id": 2, "object_type_name": "user", "display_name": "用户", "status": "experimental"},
    ],
    "properties": [
        {
            "object_type_id": 1,
            "property_name": "order_id",
            "display_name": "订单编号",
            "value_type": "String",
            "is_identifier": True,
        },
        {
            "object_type_id": 1,
            "property_name": "city",
            "display_name": "城市",
            "value_type": "String",
            "is_identifier": False,
        },
        {
            "object_type_id": 2,
            "property_name": "user_id",
            "display_name": "用户编号",
            "value_type": "String",
            "is_identifier": True,
        },
    ],
    "relation_types": [
        {
            "relation_type_name": "order_belongs_to_user",
            "from_object_type_id": 1,
            "to_object_type_id": 2,
            "multiplicity": "many_to_one",
            "from_verbalize": "订单归属用户",
            "to_verbalize": "用户拥有订单",
        }
    ],
    "mappings": [
        {
            "object_type_id": 1,
            "semantic_config_id": "sc_order",
            "column_mappings": {"order_id": "订单编号", "city": "城市"},
        },
        {
            "object_type_id": 2,
            "semantic_config_id": "sc_user",
            "column_mappings": {"user_id": "用户编号"},
        },
    ],
}


class _FakeOntologyConfigService:
    async def get_configs_async(self, datasource_id, user_id=""):
        return [
            {
                "id": "sc_order",
                "name": "订单模型",
                "enabled": True,
                # 失修比对基准：与 _FakeOntologyDao 的 column_mappings 右值一致 → healthy
                "dimensions": [{"name": "订单编号"}, {"name": "城市"}],
            },
            {"id": "sc_disabled", "name": "停用模型", "enabled": False},
        ]


class _FakeOntologyDao:
    def __init__(self):
        self.captured_ids = None

    async def load_injection_bundle_async(self, config_ids):
        self.captured_ids = list(config_ids)
        return _ONTOLOGY_BUNDLE


def _onto_state(*, datasource_id: str = "ds_a", **state: object) -> dict:
    return {
        **state,
        "execution_context": {"datasource_id": datasource_id},
    }


def _invoke_locate_object_type(monkeypatch, *, action_payload=None, state=None):
    fake_dao = _FakeOntologyDao()
    monkeypatch.setattr(ontology_tool, "get_semantic_config_service", lambda: _FakeOntologyConfigService())
    monkeypatch.setattr(ontology_tool, "OntologyDao", lambda: fake_dao)
    raw = asyncio.run(
        ontology_tool.create_ontology_tool().ainvoke(
            {
                "action": "locate_object_type",
                "action_payload": action_payload or {"object_types": ["order"]},
                "state": state if state is not None else _onto_state(user_id="u1", conversation_id="c1"),
            }
        )
    )
    return json.loads(raw.replace(RESULT_PREFIX, "", 1)), fake_dao


def _invoke_list_object_types(monkeypatch, *, state=None):
    fake_dao = _FakeOntologyDao()
    monkeypatch.setattr(ontology_tool, "get_semantic_config_service", lambda: _FakeOntologyConfigService())
    monkeypatch.setattr(ontology_tool, "OntologyDao", lambda: fake_dao)
    raw = asyncio.run(
        ontology_tool.create_ontology_tool().ainvoke(
            {
                "action": "list_object_types",
                "action_payload": {},
                "state": state if state is not None else _onto_state(user_id="u1", conversation_id="c1"),
            }
        )
    )
    return json.loads(raw.replace(RESULT_PREFIX, "", 1)), fake_dao


def test_payload_models_registered():
    assert ontology_tool.ONTOLOGY_ACTION_PAYLOAD_MODELS["locate_object_type"].model_validate({"object_types": ["order"]})
    assert ontology_tool.ONTOLOGY_ACTION_PAYLOAD_MODELS["locate_object_type"].model_validate(
        {"object_types": ["order", "user"], "semantic_config_id": "sc_order"}
    )
    with pytest.raises(ValueError, match="locate_object_type"):
        ontology_tool.validate_ontology_payload("locate_object_type", {})
    # 无业务参数：范围由服务端结构性收敛，与 list_execution_targets 同口径
    assert ontology_tool.validate_ontology_payload("list_object_types", {}) is not None
    with pytest.raises(ValueError, match="不支持的 action"):
        ontology_tool.validate_ontology_payload("execute_query", {})


def test_locate_object_type_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CHAT_DATA_ONTOLOGY_LOCATE_ENABLED", raising=False)
    with pytest.raises(ValueError, match="CHAT_DATA_ONTOLOGY_LOCATE_ENABLED"):
        asyncio.run(
            ontology_tool.create_ontology_tool().ainvoke(
                {
                    "action": "locate_object_type",
                    "action_payload": {"object_types": ["order"]},
                    "state": _onto_state(),
                }
            )
        )


def test_locate_object_type_happy_path_multiple_objects(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_LOCATE_ENABLED", "1")
    payload, fake_dao = _invoke_locate_object_type(
        monkeypatch,
        action_payload={"object_types": ["order", "user", "ghost"]},
    )
    # 启用模型集 = 服务端从可见配置推导（disabled 被剔除）
    assert fake_dao.captured_ids == ["sc_order"]
    assert payload["success"] is True
    # 批量命中保序；未命中进 not_found 并列可定位候选
    assert [item["object_type"] for item in payload["object_types"]] == ["order", "user"]
    assert payload["not_found"] == [
        {"object_type": "ghost", "reason": "对象类型不存在或未绑定本轮启用语义模型", "available": "order、user"}
    ]
    order_entry = payload["object_types"][0]
    assert order_entry["display_name"] == "订单"
    assert {
        "name": "order_id",
        "display_name": "订单编号",
        "value_type": "String",
        "is_identifier": True,
    } in order_entry["properties"]
    # 绑定：模型名 + 快照文件名（materializer 公开别名产物）+ 属性→维度对照 + 失修标注（基准内 → healthy）
    assert order_entry["bindings"] == [
        {
            "semantic_config_id": "sc_order",
            "model_name": "订单模型",
            "model_file": model_snapshot_file_name({"id": "sc_order", "name": "订单模型"}),
            "column_mappings": {"city": "城市", "order_id": "订单编号"},
            "healthy": True,
            "stale_dimensions": [],
        }
    ]
    assert order_entry["relation_types"][0]["related_object"] == "user"
    # 使用说明引导进入 execute_query
    assert "execute_query" in payload["usage"]


def test_locate_object_type_config_filter(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_LOCATE_ENABLED", "1")
    payload, _fake_dao = _invoke_locate_object_type(
        monkeypatch,
        action_payload={"object_types": ["order"], "semantic_config_id": "sc_order"},
    )
    assert payload["object_types"][0]["bindings"][0]["semantic_config_id"] == "sc_order"


def test_locate_object_type_requires_enabled_models(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_LOCATE_ENABLED", "1")

    class _EmptyService:
        async def get_configs_async(self, datasource_id, user_id=""):
            return []

    monkeypatch.setattr(ontology_tool, "get_semantic_config_service", lambda: _EmptyService())
    with pytest.raises(ValueError, match="没有启用语义模型"):
        asyncio.run(
            ontology_tool.create_ontology_tool().ainvoke(
                {
                    "action": "locate_object_type",
                    "action_payload": {"object_types": ["order"]},
                    "state": _onto_state(),
                }
            )
        )


def test_locate_object_type_requires_datasource(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_LOCATE_ENABLED", "1")
    monkeypatch.setattr(ontology_tool, "get_semantic_config_service", lambda: _FakeOntologyConfigService())
    with pytest.raises(ValueError, match="当前会话未绑定数据源"):
        asyncio.run(
            ontology_tool.create_ontology_tool().ainvoke(
                {"action": "locate_object_type", "action_payload": {"object_types": ["order"]}, "state": {}}
            )
        )


def test_locate_object_type_over_batch_limit_rejected(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_LOCATE_ENABLED", "1")
    monkeypatch.setattr(ontology_tool, "get_semantic_config_service", lambda: _FakeOntologyConfigService())
    monkeypatch.setattr(ontology_tool, "OntologyDao", lambda: _FakeOntologyDao())
    with pytest.raises(ValueError, match="单次最多定位 10 个对象"):
        asyncio.run(
            ontology_tool.create_ontology_tool().ainvoke(
                {
                    "action": "locate_object_type",
                    "action_payload": {"object_types": [f"obj{i}" for i in range(11)]},
                    "state": _onto_state(),
                }
            )
        )


def test_list_object_types_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CHAT_DATA_ONTOLOGY_LOCATE_ENABLED", raising=False)
    with pytest.raises(ValueError, match="CHAT_DATA_ONTOLOGY_LOCATE_ENABLED"):
        asyncio.run(
            ontology_tool.create_ontology_tool().ainvoke(
                {"action": "list_object_types", "action_payload": {}, "state": _onto_state()}
            )
        )


def test_list_object_types_lightweight_listing(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_LOCATE_ENABLED", "1")
    payload, fake_dao = _invoke_list_object_types(monkeypatch)
    # 范围收敛：只按启用模型取 bundle（disabled 被剔除）
    assert fake_dao.captured_ids == ["sc_order"]
    assert payload["success"] is True
    assert payload["count"] == 2
    # 轻量清单：按对象名升序，只含 名字/显示名/描述 + 绑定模型一跳，无属性/映射/关系详情
    assert [item["object_type"] for item in payload["object_types"]] == ["order", "user"]
    order_entry = payload["object_types"][0]
    assert order_entry["display_name"] == "订单"
    assert order_entry["models"] == [
        {
            "semantic_config_id": "sc_order",
            "model_name": "订单模型",
            "model_file": model_snapshot_file_name({"id": "sc_order", "name": "订单模型"}),
        }
    ]
    assert "properties" not in order_entry
    assert "bindings" not in order_entry
    assert "relation_types" not in order_entry
    # 使用说明引导：详情升级 locate_object_type，查询走 execute_query
    assert "locate_object_type" in payload["usage"]
    assert "execute_query" in payload["usage"]


def test_list_object_types_requires_enabled_models(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_LOCATE_ENABLED", "1")

    class _EmptyService:
        async def get_configs_async(self, datasource_id, user_id=""):
            return []

    monkeypatch.setattr(ontology_tool, "get_semantic_config_service", lambda: _EmptyService())
    with pytest.raises(ValueError, match="没有启用语义模型"):
        asyncio.run(
            ontology_tool.create_ontology_tool().ainvoke(
                {"action": "list_object_types", "action_payload": {}, "state": _onto_state()}
            )
        )
