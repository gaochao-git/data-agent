"""ontology_tool 单测：本体对象类型导航工具（list_object_types / locate_object_type / search_around）。

2026-10-19 自 test_data_analyzing_tool.py 的本体段落平移：action 随工具拆分
独立成测，与 data_analyzing_tool 单测解耦；同日追加 search_around（一跳展开）用例。
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


def _internal_query_payload(raw: str) -> dict:
    return json.loads(raw)["payload"]


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
            "from_columns": ["order_id"],
            "to_columns": ["user_id"],
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
                # 失修比对基准：与 _FakeOntologyDao 的 column_mappings 右值一致 → healthy；
                # column 供 search_around 的「维度名 → 物理列」翻译索引使用
                "dimensions": [
                    {"name": "订单编号", "column": "ORDER_ID"},
                    {"name": "城市", "column": "CITY"},
                ],
            },
            {"id": "sc_disabled", "name": "停用模型", "enabled": False},
        ]


class _FakeOntologyDao:
    def __init__(self, bundle=None):
        self._bundle = bundle if bundle is not None else _ONTOLOGY_BUNDLE
        self.captured_ids = None

    async def load_injection_bundle_async(self, config_ids):
        self.captured_ids = list(config_ids)
        return self._bundle


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
    assert ontology_tool.ONTOLOGY_ACTION_PAYLOAD_MODELS["locate_object_type"].model_validate(
        {"object_types": ["order"]}
    )
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


class _SearchAroundConfigService:
    """search_around 专用：order/user 两模型均启用且带物理列，供 join 翻译。"""

    async def get_configs_async(self, datasource_id, user_id=""):
        return [
            {
                "id": "sc_order",
                "name": "订单模型",
                "enabled": True,
                "dimensions": [
                    {"name": "订单编号", "column": "ORDER_ID"},
                    {"name": "城市", "column": "CITY"},
                ],
            },
            {
                "id": "sc_user",
                "name": "用户模型",
                "enabled": True,
                "dimensions": [{"name": "用户编号", "column": "USER_ID"}],
            },
        ]


def _invoke_search_around(monkeypatch, *, action_payload, bundle=None, service=None):
    fake_dao = _FakeOntologyDao(bundle)
    monkeypatch.setattr(ontology_tool, "get_semantic_config_service", lambda: service or _SearchAroundConfigService())
    monkeypatch.setattr(ontology_tool, "OntologyDao", lambda: fake_dao)
    raw = asyncio.run(
        ontology_tool.create_ontology_tool().ainvoke(
            {
                "action": "search_around",
                "action_payload": action_payload,
                "state": _onto_state(user_id="u1", conversation_id="c1"),
            }
        )
    )
    return json.loads(raw.replace(RESULT_PREFIX, "", 1)), fake_dao


def test_search_around_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CHAT_DATA_ONTOLOGY_LOCATE_ENABLED", raising=False)
    with pytest.raises(ValueError, match="CHAT_DATA_ONTOLOGY_LOCATE_ENABLED"):
        asyncio.run(
            ontology_tool.create_ontology_tool().ainvoke(
                {
                    "action": "search_around",
                    "action_payload": {"object_type": "order"},
                    "state": _onto_state(),
                }
            )
        )


def test_search_around_payload_models_registered():
    assert ontology_tool.validate_ontology_payload("search_around", {"object_type": "order"}) is not None
    assert (
        ontology_tool.validate_ontology_payload(
            "search_around",
            {"object_type": "order", "relation": "order_belongs_to_user", "semantic_config_id": "sc_order"},
        )
        is not None
    )
    with pytest.raises(ValueError, match="object_type"):
        ontology_tool.validate_ontology_payload("search_around", {})


def test_search_around_expands_neighbor_with_join_columns(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_LOCATE_ENABLED", "1")
    payload, fake_dao = _invoke_search_around(monkeypatch, action_payload={"object_type": "order"})
    # 范围收敛：只按启用模型取 bundle
    assert fake_dao.captured_ids == ["sc_order", "sc_user"]
    assert payload["success"] is True
    assert payload["center"] == {"object_type": "order", "display_name": "订单"}
    assert payload["not_found"] == []
    (hop,) = payload["hops"]
    assert hop["relation"] == "order_belongs_to_user"
    assert hop["direction"] == "from"
    assert hop["multiplicity"] == "many_to_one"
    assert hop["verbalize"] == "订单归属用户"
    # join：属性 → 维度 → 物理列（目录同源翻译），单模型绑定回退视图与 by_model 一致
    assert hop["join"]["from_columns"] == ["ORDER_ID"]
    assert hop["join"]["to_columns"] == ["USER_ID"]
    assert hop["join"]["from_columns_by_model"] == {"sc_order": ["ORDER_ID"]}
    assert hop["join"]["to_columns_by_model"] == {"sc_user": ["USER_ID"]}
    assert "unresolved" not in hop["join"]
    # neighbor 是对端完整定位条目：属性/绑定齐全，关系收敛为名字列表（下一跳提示）
    neighbor = hop["neighbor"]
    assert neighbor["object_type"] == "user"
    assert neighbor["display_name"] == "用户"
    assert {prop["name"] for prop in neighbor["properties"]} == {"user_id"}
    assert neighbor["bindings"][0]["semantic_config_id"] == "sc_user"
    assert neighbor["relations"] == ["order_belongs_to_user"]
    assert "relation_types" not in neighbor
    # 使用说明引导 join 用法与继续展开方式
    assert "from_columns_by_model" in payload["usage"]
    assert "execute_query" in payload["usage"]


def test_search_around_relation_filter(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_LOCATE_ENABLED", "1")
    payload, _dao = _invoke_search_around(
        monkeypatch,
        action_payload={"object_type": "order", "relation": "order_belongs_to_user"},
    )
    assert len(payload["hops"]) == 1
    # 不存在的关系名 → 空展开（不报错，模型可换名重试）
    payload, _dao = _invoke_search_around(
        monkeypatch,
        action_payload={"object_type": "order", "relation": "nope"},
    )
    assert payload["hops"] == []
    assert payload["center"] == {"object_type": "order", "display_name": "订单"}


def test_search_around_center_not_found(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_LOCATE_ENABLED", "1")
    payload, _dao = _invoke_search_around(monkeypatch, action_payload={"object_type": "ghost"})
    assert payload["center"] is None
    assert payload["hops"] == []
    assert payload["not_found"] == [
        {"object_type": "ghost", "reason": "对象类型不存在或未绑定本轮启用语义模型", "available": "order、user"}
    ]


def test_search_around_untranslated_column_marked(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_LOCATE_ENABLED", "1")
    bundle = {
        **_ONTOLOGY_BUNDLE,
        "relation_types": [
            {
                **_ONTOLOGY_BUNDLE["relation_types"][0],
                "from_columns": ["order_id", "ghost_attr"],
            }
        ],
    }
    payload, _dao = _invoke_search_around(monkeypatch, action_payload={"object_type": "order"}, bundle=bundle)
    (hop,) = payload["hops"]
    # 未映射属性保留原文并标 unresolved：join 前必须回读模型 YAML
    assert hop["join"]["from_columns_by_model"] == {"sc_order": ["ORDER_ID", "ghost_attr"]}
    assert hop["join"]["unresolved"] is True


# ---------- list_execution_targets（机构范围发现） ----------


def test_list_execution_targets_payload_registered():
    assert ontology_tool.validate_ontology_payload("list_execution_targets", {}) is not None


def test_list_execution_targets_requires_datasource(monkeypatch):
    with pytest.raises(ValueError, match="未绑定数据源"):
        asyncio.run(
            ontology_tool.create_ontology_tool("", user_id="u1").ainvoke(
                {
                    "action": "list_execution_targets",
                    "action_payload": {},
                    "state": {"user_id": "u1", "execution_context": {}},
                }
            )
        )


def test_list_execution_targets_returns_authorized_members(monkeypatch):
    class _FakeManager:
        async def list_authorized_execution_targets_async(self, datasource_id, **kwargs):
            assert datasource_id == "ds_a"
            return [
                {"member_datasource_id": "ORG_CODE1", "member_name": "财付通", "org_code": "ORG_CODE1"},
                {"member_datasource_id": "ORG_CODE2", "member_name": "支付宝", "org_code": "ORG_CODE2"},
            ]

    monkeypatch.setattr(ontology_tool, "get_datasource_manager", lambda: _FakeManager())
    raw = asyncio.run(
        ontology_tool.create_ontology_tool("ds_a", user_id="u1").ainvoke(
            {"action": "list_execution_targets", "action_payload": {}, "state": _onto_state(user_id="u1")}
        )
    )
    payload = json.loads(raw.replace(RESULT_PREFIX, "", 1))
    assert payload["count"] == 2
    assert payload["execution_targets"][1]["member_datasource_id"] == "ORG_CODE2"


# ---------- find_objects（对象实例查询） ----------


def test_find_objects_payload_registered():
    payload = ontology_tool.validate_ontology_payload(
        "find_objects",
        {
            "object_type": "shipment",
            "filters": [{"property": "status", "operator": "eq", "value": "delivered"}],
            "sort": [{"property": "shipped_at", "direction": "desc"}],
        },
    )
    assert payload.object_type == "shipment"
    assert payload.filters[0].operator == "eq"


def test_find_objects_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CHAT_DATA_ONTOLOGY_OBJECT_READ_ENABLED", raising=False)
    with pytest.raises(ValueError, match="CHAT_DATA_ONTOLOGY_OBJECT_READ_ENABLED"):
        asyncio.run(
            ontology_tool.create_ontology_tool().ainvoke(
                {
                    "action": "find_objects",
                    "action_payload": {"object_type": "shipment"},
                    "state": _onto_state(),
                }
            )
        )


def test_find_objects_happy_path(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_OBJECT_READ_ENABLED", "1")

    async def fake_get_object_type(_name):
        return {"id": 7, "object_type_name": "shipment", "status": "certified"}

    async def fake_search(object_type, **kwargs):
        assert kwargs["filters"][0]["property"] == "status"
        assert kwargs["member_datasource_ids"] == ["ORG_CODE1"]
        return {
            "objects": [
                {
                    "object_type": object_type,
                    "object_key": "shipment:SHIP000001",
                    "properties": {"tracking_no": "TRK0000000001", "status": "delivered"},
                }
            ],
            "has_more": False,
            "limit": 10,
            "offset": 0,
            "total": 1,
            "title_property": "tracking_no",
            "properties": ["tracking_no", "status"],
            "sql": "SELECT tracking_no, status FROM shipments LIMIT 11",
            "member_datasource_id": "ORG_CODE1",
            "member_name": "财付通",
        }

    monkeypatch.setattr(ontology_tool.ontology_service, "get_object_type_async", fake_get_object_type)
    monkeypatch.setattr(ontology_tool, "search_object_projection", fake_search)
    raw = asyncio.run(
        ontology_tool.create_ontology_tool().ainvoke(
            {
                "action": "find_objects",
                "action_payload": {
                    "object_type": "shipment",
                    "member_datasource_ids": ["ORG_CODE1"],
                    "filters": [{"property": "status", "operator": "eq", "value": "delivered"}],
                },
                "state": _onto_state(user_id="u1"),
            }
        )
    )
    payload = _internal_query_payload(raw)
    assert payload["status"] == "succeeded"
    member = payload["results"][0]
    assert member["data"][0]["object_key"] == "shipment:SHIP000001"
    assert member["columns"] == ["object_key", "tracking_no", "status"]
    assert member["member_datasource_id"] == "ORG_CODE1"
    assert payload["preview"]["kind"] == "objects"


def test_find_objects_projection_error_is_reported(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_OBJECT_READ_ENABLED", "1")

    async def fake_get_object_type(_name):
        return {"id": 7, "object_type_name": "shipment", "status": "certified"}

    async def fake_search(_object_type, **_kwargs):
        raise ontology_tool.ObjectProjectionError("对象查询失败")

    monkeypatch.setattr(ontology_tool.ontology_service, "get_object_type_async", fake_get_object_type)
    monkeypatch.setattr(ontology_tool, "search_object_projection", fake_search)
    with pytest.raises(ValueError, match="对象查询失败"):
        asyncio.run(
            ontology_tool.create_ontology_tool().ainvoke(
                {
                    "action": "find_objects",
                    "action_payload": {"object_type": "shipment"},
                    "state": _onto_state(user_id="u1"),
                }
            )
        )


def test_find_objects_runs_per_member_scope(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_OBJECT_READ_ENABLED", "1")

    async def fake_get_object_type(_name):
        return {"id": 7, "object_type_name": "shipment", "status": "certified"}

    seen: list[str] = []

    async def fake_search(object_type, **kwargs):
        member_id = (kwargs.get("member_datasource_ids") or [""])[0]
        seen.append(member_id)
        return {
            "objects": [{"object_type": object_type, "object_key": f"shipment:{member_id}", "properties": {}}],
            "total": 1,
            "properties": [],
            "title_property": "shipment_id",
            "sql": "SELECT 1",
            "member_datasource_id": member_id,
            "member_name": member_id,
        }

    monkeypatch.setattr(ontology_tool.ontology_service, "get_object_type_async", fake_get_object_type)
    monkeypatch.setattr(ontology_tool, "search_object_projection", fake_search)
    state = _onto_state(user_id="u1")
    state["execution_context"]["matched_member_datasource_ids"] = []
    state["execution_context"]["available_member_datasource_ids"] = ["ORG_CODE1", "ORG_CODE2"]
    raw = asyncio.run(
        ontology_tool.create_ontology_tool().ainvoke(
            {
                "action": "find_objects",
                "action_payload": {"object_type": "shipment", "member_datasource_ids": ["ORG_CODE1", "ORG_CODE2"]},
                "state": state,
            }
        )
    )
    payload = _internal_query_payload(raw)
    assert seen == ["ORG_CODE1", "ORG_CODE2"]
    assert [item["member_datasource_id"] for item in payload["results"]] == ["ORG_CODE1", "ORG_CODE2"]


def test_find_objects_uses_single_context_member_when_scope_omitted(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_OBJECT_READ_ENABLED", "1")

    async def fake_get_object_type(_name):
        return {"id": 7, "object_type_name": "shipment", "status": "certified"}

    seen: list[str] = []

    async def fake_search(object_type, **kwargs):
        seen.extend(kwargs.get("member_datasource_ids") or [])
        return {
            "objects": [{"object_type": object_type, "object_key": "shipment:1", "properties": {}}],
            "total": 1,
            "properties": [],
            "sql": "SELECT 1",
            "member_datasource_id": "ORG_CODE1",
            "member_name": "财付通",
        }

    monkeypatch.setattr(ontology_tool.ontology_service, "get_object_type_async", fake_get_object_type)
    monkeypatch.setattr(ontology_tool, "search_object_projection", fake_search)
    state = _onto_state(user_id="u1")
    state["execution_context"]["available_member_datasource_ids"] = ["ORG_CODE1"]
    raw = asyncio.run(
        ontology_tool.create_ontology_tool().ainvoke(
            {
                "action": "find_objects",
                "action_payload": {"object_type": "shipment"},
                "state": state,
            }
        )
    )
    assert _internal_query_payload(raw)["results"][0]["member_datasource_id"] == "ORG_CODE1"
    assert seen == ["ORG_CODE1"]


def test_find_objects_requires_scope_when_context_has_multiple_members(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_OBJECT_READ_ENABLED", "1")

    async def fake_get_object_type(_name):
        return {"id": 7, "object_type_name": "shipment", "status": "certified"}

    monkeypatch.setattr(ontology_tool.ontology_service, "get_object_type_async", fake_get_object_type)
    state = _onto_state(user_id="u1")
    state["execution_context"]["available_member_datasource_ids"] = ["ORG_CODE1", "ORG_CODE2"]
    with pytest.raises(ValueError, match="多个可执行数据源"):
        asyncio.run(
            ontology_tool.create_ontology_tool().ainvoke(
                {
                    "action": "find_objects",
                    "action_payload": {"object_type": "shipment"},
                    "state": state,
                }
            )
        )


# ---------- aggregate_objects（对象聚合） ----------


def test_aggregate_objects_payload_registered():
    payload = ontology_tool.validate_ontology_payload(
        "aggregate_objects",
        {
            "object_type": "shipment",
            "group_by": ["carrier"],
            "metrics": [{"operation": "count"}, {"operation": "sum", "property": "amount"}],
        },
    )
    assert payload.group_by == ["carrier"]
    assert payload.metrics[0].operation == "count"


def test_aggregate_objects_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CHAT_DATA_ONTOLOGY_OBJECT_READ_ENABLED", raising=False)
    with pytest.raises(ValueError, match="CHAT_DATA_ONTOLOGY_OBJECT_READ_ENABLED"):
        asyncio.run(
            ontology_tool.create_ontology_tool().ainvoke(
                {
                    "action": "aggregate_objects",
                    "action_payload": {"object_type": "shipment", "metrics": [{"operation": "count"}]},
                    "state": _onto_state(),
                }
            )
        )


def test_aggregate_objects_projection_error_is_reported(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_OBJECT_READ_ENABLED", "1")

    async def fake_get_object_type(_name):
        return {"id": 7, "object_type_name": "shipment", "status": "certified"}

    async def fake_aggregate(_object_type, **_kwargs):
        raise ontology_tool.ObjectProjectionError("指标属性未映射")

    monkeypatch.setattr(ontology_tool.ontology_service, "get_object_type_async", fake_get_object_type)
    monkeypatch.setattr(ontology_tool, "aggregate_object_projection", fake_aggregate)
    with pytest.raises(ValueError, match="指标属性未映射"):
        asyncio.run(
            ontology_tool.create_ontology_tool().ainvoke(
                {
                    "action": "aggregate_objects",
                    "action_payload": {"object_type": "shipment", "metrics": [{"operation": "count"}]},
                    "state": _onto_state(),
                }
            )
        )


def test_aggregate_objects_happy_path(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_OBJECT_READ_ENABLED", "1")

    async def fake_get_object_type(_name):
        return {"id": 7, "object_type_name": "shipment", "status": "certified"}

    async def fake_aggregate(object_type, **kwargs):
        assert kwargs["group_by"] == ["carrier"]
        assert kwargs["member_datasource_ids"] == ["ORG_CODE1"]
        assert kwargs["metrics"] == [{"operation": "count", "property": None}]
        return {
            "object_type": object_type,
            "group_by": ["carrier"],
            "metric_keys": ["count"],
            "groups": [
                {"keys": {"carrier": "顺丰"}, "metrics": {"count": 75}},
                {"keys": {"carrier": "京东"}, "metrics": {"count": 80}},
            ],
            "limit": 100,
            "sql": "SELECT carrier, COUNT(*) AS count FROM shipments GROUP BY carrier",
            "member_datasource_id": "ORG_CODE1",
            "member_name": "财付通",
        }

    monkeypatch.setattr(ontology_tool.ontology_service, "get_object_type_async", fake_get_object_type)
    monkeypatch.setattr(ontology_tool, "aggregate_object_projection", fake_aggregate)
    raw = asyncio.run(
        ontology_tool.create_ontology_tool().ainvoke(
            {
                "action": "aggregate_objects",
                "action_payload": {
                    "object_type": "shipment",
                    "member_datasource_ids": ["ORG_CODE1"],
                    "group_by": ["carrier"],
                    "metrics": [{"operation": "count"}],
                },
                "state": _onto_state(user_id="u1"),
            }
        )
    )
    payload = _internal_query_payload(raw)
    assert payload["status"] == "succeeded"
    member = payload["results"][0]
    assert member["data"][0] == {"carrier": "顺丰", "count": 75}
    assert member["columns"] == ["carrier", "count"]
    assert payload["preview"]["kind"] == "aggregate"


# ---------- get_object（对象实例读取） ----------


def test_get_object_payload_registered():
    assert ontology_tool.ONTOLOGY_ACTION_PAYLOAD_MODELS["get_object"].model_validate(
        {"object_type": "sales_order", "object_key": "ORD2024000001"}
    )
    with pytest.raises(ValueError, match="get_object"):
        ontology_tool.validate_ontology_payload("get_object", {"object_type": "sales_order"})


def test_get_object_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CHAT_DATA_ONTOLOGY_OBJECT_READ_ENABLED", raising=False)
    with pytest.raises(ValueError, match="CHAT_DATA_ONTOLOGY_OBJECT_READ_ENABLED"):
        asyncio.run(
            ontology_tool.create_ontology_tool().ainvoke(
                {
                    "action": "get_object",
                    "action_payload": {"object_type": "sales_order", "object_key": "ORD2024000001"},
                    "state": _onto_state(),
                }
            )
        )


def test_get_object_rejects_multiple_members(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_OBJECT_READ_ENABLED", "1")
    with pytest.raises(ValueError, match="只能指定一个"):
        asyncio.run(
            ontology_tool.create_ontology_tool().ainvoke(
                {
                    "action": "get_object",
                    "action_payload": {
                        "object_type": "sales_order",
                        "object_key": "ORD1",
                        "member_datasource_ids": ["ORG_CODE1", "ORG_CODE2"],
                    },
                    "state": _onto_state(user_id="u1"),
                }
            )
        )


def test_get_object_projection_error_is_reported(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_OBJECT_READ_ENABLED", "1")

    async def fake_get_object_type(_name):
        return {"id": 1, "object_type_name": "sales_order", "status": "certified"}

    async def fake_read_object(*_args, **_kwargs):
        raise ontology_tool.ObjectProjectionError("对象不存在")

    monkeypatch.setattr(ontology_tool.ontology_service, "get_object_type_async", fake_get_object_type)
    monkeypatch.setattr(ontology_tool, "read_object_projection", fake_read_object)
    with pytest.raises(ValueError, match="对象不存在"):
        asyncio.run(
            ontology_tool.create_ontology_tool().ainvoke(
                {
                    "action": "get_object",
                    "action_payload": {"object_type": "sales_order", "object_key": "missing"},
                    "state": _onto_state(user_id="u1"),
                }
            )
        )


def test_get_object_happy_path(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_OBJECT_READ_ENABLED", "1")

    async def fake_get_object_type(_name):
        return {"id": 1, "object_type_name": "sales_order", "status": "certified"}

    async def fake_read_object(object_type, object_key, **_kwargs):
        return {
            "object_type": object_type,
            "object_key": f"{object_type}:{object_key}",
            "properties": {"order_no": object_key, "pay_status": "paid"},
            "links": {"order_belongs_to_customer": [{"related_object_type": "customer"}]},
        }

    monkeypatch.setattr(ontology_tool.ontology_service, "get_object_type_async", fake_get_object_type)
    monkeypatch.setattr(ontology_tool, "read_object_projection", fake_read_object)
    raw = asyncio.run(
        ontology_tool.create_ontology_tool().ainvoke(
            {
                "action": "get_object",
                "action_payload": {"object_type": "sales_order", "object_key": "ORD2024000001"},
                "state": _onto_state(user_id="u1"),
            }
        )
    )
    payload = json.loads(raw.replace(RESULT_PREFIX, "", 1))
    assert payload["success"] is True
    assert payload["object_key"] == "sales_order:ORD2024000001"
    assert payload["links"]["order_belongs_to_customer"][0]["related_object_type"] == "customer"
    assert "execute_query" in payload["usage"]
