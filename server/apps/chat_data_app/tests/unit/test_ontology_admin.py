"""本体管理单测：对象/属性/关系 CRUD、校验器红线、治理门禁与状态机。

harness 与 test_setting_service 同模式：sqlite 内存库建全量表 + monkeypatch
session_scope，DAO 走真实 ORM；全局枚举存在性打桩在 global_enum_service。
"""

import asyncio
import importlib
import json
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.chat_data_app.services.ontology import (
    OntologyInvalidError,
    OntologyNameInvalidError,
    OntologyNotFoundError,
    OntologyStateInvalidError,
    ontology_service,
)
from utils.models import Base

# 包 __init__ 导出的单例与模块同名，必须用 importlib 拿 service 模块本体（打桩用）
ontology_service_module = importlib.import_module("apps.chat_data_app.services.ontology.ontology_service")


def _stub_enum_service(handler):
    return type("StubEnumService", (), {"get_enum_async": staticmethod(handler)})()


def _stub_semantic_config_dao(handler):
    return type("StubSemanticConfigDao", (), {"filter_existing_config_ids_async": staticmethod(handler)})()


VALID_PROPERTY = {
    "property_name": "order_id",
    "display_name": "订单ID",
    "value_type": "String",
    "is_identifier": True,
}

# 语义模型绑定（统一单轨：不触碰物理表）
SEMANTIC_BINDING = {
    "semantic_config_id": "cfg-1",
    "table_alias": "dwd_order",
    "column_mappings": {"order_id": "order_no"},
}


def _object_payload(**overrides):
    payload = {
        "display_name": "订单",
        "properties": [{**VALID_PROPERTY}],
        "bindings": [],
        "confirm_destructive": False,
    }
    payload.update(overrides)
    return payload


@pytest.fixture()
def harness(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async def create_tables():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    asyncio.run(create_tables())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def test_session_scope():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr("apps.chat_data_app.daos.ontology_dao.session_scope", test_session_scope)
    yield {"session_factory": session_factory}


@pytest.fixture()
def deps_ok(monkeypatch):
    """外部存在性校验放行（全局枚举 + 绑定的语义模型存在性），专项用例单独收紧。"""

    async def fake_get_enum(enum_name, business_domain=""):
        return {"business_domain": business_domain, "enum_name": enum_name}

    monkeypatch.setattr(ontology_service_module, "global_enum_service", _stub_enum_service(fake_get_enum))

    async def fake_existing_ids(config_ids):
        return set(config_ids)

    monkeypatch.setattr(
        ontology_service_module, "semantic_config_dao", _stub_semantic_config_dao(fake_existing_ids)
    )


def test_object_create_requires_identifier(harness, deps_ok):
    with pytest.raises(OntologyInvalidError, match="标识属性"):
        asyncio.run(
            ontology_service.upsert_object_async(
                "Order",
                _object_payload(properties=[{**VALID_PROPERTY, "is_identifier": False}]),
            )
        )


def test_object_create_rejects_bad_api_name(harness, deps_ok):
    with pytest.raises(OntologyNameInvalidError):
        asyncio.run(
            ontology_service.upsert_object_async(
                "bad name!",
                _object_payload(properties=[{**VALID_PROPERTY, "property_name": "ok_id", "is_identifier": True}]),
            )
        )


def test_object_create_and_get(harness, deps_ok):
    saved = asyncio.run(
        ontology_service.upsert_object_async("Order", _object_payload(domain="交易"))
    )
    assert saved["display_name"] == "订单"
    assert saved["status"] == "draft"
    detail = asyncio.run(ontology_service.get_object_async("Order"))
    assert detail["object_name"] == "Order"
    assert detail["properties"][0]["property_name"] == "order_id"
    assert detail["properties"][0]["is_identifier"] is True


def test_object_create_rejects_duplicate_without_overwrite(harness, deps_ok):
    asyncio.run(ontology_service.upsert_object_async("Order", _object_payload()))
    with pytest.raises(OntologyStateInvalidError, match="已存在"):
        asyncio.run(ontology_service.upsert_object_async("Order", _object_payload()))


def test_binding_column_mapping_unknown_property_rejected(harness, deps_ok):
    with pytest.raises(OntologyInvalidError, match="未定义属性"):
        asyncio.run(
            ontology_service.upsert_object_async(
                "Order",
                _object_payload(
                    bindings=[
                        {
                            "semantic_config_id": "cfg-1",
                            "table_alias": "dwd_order",
                            "column_mappings": {"unknown_prop": "col_a"},
                        }
                    ]
                ),
            )
        )


def test_binding_ownership_conflict_rejected(harness, deps_ok):
    binding = dict(SEMANTIC_BINDING)
    asyncio.run(ontology_service.upsert_object_async("Order", _object_payload(bindings=[binding])))
    with pytest.raises(OntologyInvalidError, match="已被对象"):
        asyncio.run(
            ontology_service.upsert_object_async(
                "Order2",
                _object_payload(properties=[dict(VALID_PROPERTY)], bindings=[dict(binding)]),
            )
        )


def test_binding_change_requires_destructive_confirm(harness, deps_ok):
    binding = dict(SEMANTIC_BINDING)
    asyncio.run(ontology_service.upsert_object_async("Order", _object_payload(bindings=[dict(binding)])))
    edited_binding = {**binding, "table_alias": "dwd_order_2"}
    with pytest.raises(OntologyStateInvalidError, match="破坏性变更"):
        asyncio.run(
            ontology_service.upsert_object_async(
                "Order", _object_payload(bindings=[edited_binding]), allow_overwrite=True
            )
        )
    saved = asyncio.run(
        ontology_service.upsert_object_async(
            "Order",
            _object_payload(bindings=[edited_binding], confirm_destructive=True),
            allow_overwrite=True,
        )
    )
    assert saved["bindings"][0]["table_alias"] == "dwd_order_2"


def test_certified_requires_binding(harness, deps_ok):
    asyncio.run(ontology_service.upsert_object_async("Order", _object_payload()))
    # 无绑定：拒绝
    with pytest.raises(OntologyInvalidError, match="语义模型绑定"):
        asyncio.run(ontology_service.set_object_status_async("Order", "certified", {}))
    # 补上语义模型绑定后可认证
    asyncio.run(
        ontology_service.upsert_object_async(
            "Order", _object_payload(bindings=[dict(SEMANTIC_BINDING)], confirm_destructive=True), allow_overwrite=True
        )
    )
    asyncio.run(ontology_service.set_object_status_async("Order", "certified", {}))
    # certified 编辑时移除唯一绑定：冻结门禁拒绝
    with pytest.raises(OntologyStateInvalidError, match="语义模型绑定"):
        asyncio.run(
            ontology_service.upsert_object_async(
                "Order", _object_payload(bindings=[], confirm_destructive=True), allow_overwrite=True
            )
        )


def test_certified_freeze_blocks_key_and_property_change(harness, deps_ok):
    binding = dict(SEMANTIC_BINDING)
    asyncio.run(
        ontology_service.upsert_object_async(
            "Order",
            _object_payload(
                properties=[dict(VALID_PROPERTY), {"property_name": "amount", "value_type": "Decimal"}],
                bindings=[dict(binding)],
            ),
        )
    )
    asyncio.run(ontology_service.set_object_status_async("Order", "certified", {}))
    # 主键组合变更：certified 直接拒绝
    with pytest.raises(OntologyStateInvalidError, match="冻结"):
        asyncio.run(
            ontology_service.upsert_object_async(
                "Order",
                _object_payload(
                    bindings=[dict(binding)],
                    properties=[
                        {**VALID_PROPERTY, "is_identifier": False},
                        {"property_name": "user_id", "value_type": "String", "is_identifier": True},
                    ],
                ),
                allow_overwrite=True,
            )
        )
    # 删除既有属性：certified 直接拒绝（删除非标识属性 amount）
    with pytest.raises(OntologyStateInvalidError, match="禁止删除属性"):
        asyncio.run(
            ontology_service.upsert_object_async(
                "Order",
                _object_payload(bindings=[dict(binding)], properties=[dict(VALID_PROPERTY)]),
                allow_overwrite=True,
            )
        )


def test_status_deprecation_rules_and_finality(harness, deps_ok):
    asyncio.run(ontology_service.upsert_object_async("Order", _object_payload()))
    asyncio.run(ontology_service.upsert_object_async("Payment", _object_payload()))
    with pytest.raises(OntologyStateInvalidError, match="废弃原因"):
        asyncio.run(ontology_service.set_object_status_async("Order", "deprecated", {}))
    with pytest.raises(OntologyNotFoundError, match="替代对象不存在"):
        asyncio.run(
            ontology_service.set_object_status_async(
                "Order", "deprecated", {"deprecation_reason": "口径迁移", "successor_object_name": "Ghost"}
            )
        )
    asyncio.run(
        ontology_service.set_object_status_async(
            "Order", "deprecated", {"deprecation_reason": "口径迁移", "successor_object_name": "Payment"}
        )
    )
    with pytest.raises(OntologyStateInvalidError, match="不可逆"):
        asyncio.run(ontology_service.set_object_status_async("Order", "draft", {}))
    with pytest.raises(OntologyStateInvalidError, match="禁止编辑"):
        asyncio.run(ontology_service.upsert_object_async("Order", _object_payload(), allow_overwrite=True))


def test_relation_column_must_reference_target_identifier(harness, deps_ok):
    asyncio.run(
        ontology_service.upsert_object_async(
            "Order",
            _object_payload(properties=[dict(VALID_PROPERTY), {"property_name": "user_name", "value_type": "String"}]),
        )
    )
    asyncio.run(ontology_service.upsert_object_async("User", _object_payload(properties=[dict(VALID_PROPERTY)])))
    with pytest.raises(OntologyInvalidError, match="标识属性"):
        asyncio.run(
            ontology_service.upsert_relation_async(
                "order_belongs_user",
                {
                    "from_object_name": "Order",
                    "to_object_name": "User",
                    "from_columns": ["user_name"],
                    "to_columns": ["user_name"],
                },
            )
        )
    saved = asyncio.run(
        ontology_service.upsert_relation_async(
            "order_belongs_user",
            {
                "from_object_name": "Order",
                "to_object_name": "User",
                "from_columns": ["user_name"],
                "to_columns": ["order_id"],
                "from_verbalize": "{from} 归属于 {to}",
            },
        )
    )
    assert saved["status"] == "draft"
    relations = asyncio.run(ontology_service.list_relations_async(from_object_name="Order"))
    assert relations[0]["from_object_name"] == "Order"
    assert relations[0]["to_object_name"] == "User"


def test_relation_multiplicity_closed_vocabulary(harness, deps_ok):
    asyncio.run(ontology_service.upsert_object_async("Order", _object_payload()))
    with pytest.raises(OntologyInvalidError, match="基数"):
        asyncio.run(
            ontology_service.upsert_relation_async(
                "r1",
                {
                    "from_object_name": "Order",
                    "to_object_name": "Order",
                    "multiplicity": "many_to_many",
                    "from_columns": ["order_id"],
                    "to_columns": ["order_id"],
                },
            )
        )


def test_relation_structural_change_requires_confirm(harness, deps_ok):
    for name, prop in (("Order", dict(VALID_PROPERTY)), ("User", dict(VALID_PROPERTY))):
        asyncio.run(ontology_service.upsert_object_async(name, _object_payload(properties=[prop])))
    asyncio.run(
        ontology_service.upsert_relation_async(
            "r1",
            {
                "from_object_name": "Order",
                "to_object_name": "User",
                "from_columns": ["order_id"],
                "to_columns": ["order_id"],
            },
        )
    )
    with pytest.raises(OntologyStateInvalidError, match="破坏性变更"):
        asyncio.run(
            ontology_service.upsert_relation_async(
                "r1",
                {
                    "from_object_name": "User",
                    "to_object_name": "Order",
                    "from_columns": ["order_id"],
                    "to_columns": ["order_id"],
                },
                allow_overwrite=True,
            )
        )
    asyncio.run(
        ontology_service.upsert_relation_async(
            "r1",
            {
                "from_object_name": "User",
                "to_object_name": "Order",
                "from_columns": ["order_id"],
                "to_columns": ["order_id"],
                "confirm_destructive": True,
            },
            allow_overwrite=True,
        )
    )


def test_object_downgrade_cascades_relations(harness, deps_ok):
    order_binding = dict(SEMANTIC_BINDING)
    user_binding = {
        "semantic_config_id": "cfg-user",
        "table_alias": "dwd_user",
        "column_mappings": {"order_id": "user_no"},
    }
    asyncio.run(
        ontology_service.upsert_object_async(
            "Order", _object_payload(properties=[dict(VALID_PROPERTY)], bindings=[order_binding])
        )
    )
    asyncio.run(
        ontology_service.upsert_object_async(
            "User", _object_payload(properties=[dict(VALID_PROPERTY)], bindings=[user_binding])
        )
    )
    asyncio.run(ontology_service.set_object_status_async("Order", "certified", {}))
    asyncio.run(ontology_service.set_object_status_async("User", "certified", {}))
    asyncio.run(
        ontology_service.upsert_relation_async(
            "r1",
            {
                "from_object_name": "Order",
                "to_object_name": "User",
                "from_columns": ["order_id"],
                "to_columns": ["order_id"],
            },
        )
    )
    relations = asyncio.run(ontology_service.list_relations_async())
    assert relations[0]["status"] == "certified"
    # Order 降级 → 关系自动降级（升级手动）
    asyncio.run(ontology_service.set_object_status_async("Order", "experimental", {}))
    relations = asyncio.run(ontology_service.list_relations_async())
    assert relations[0]["status"] == "experimental"


def test_enum_ref_must_exist(harness, monkeypatch):
    async def missing_enum(enum_name, business_domain=""):
        return None

    monkeypatch.setattr(ontology_service_module, "global_enum_service", _stub_enum_service(missing_enum))
    with pytest.raises(OntologyInvalidError, match="全局枚举不存在"):
        asyncio.run(
            ontology_service.upsert_object_async(
                "Order",
                _object_payload(
                    properties=[
                        {**VALID_PROPERTY, "value_type": "enum", "enum_ref": "order_status"},
                    ]
                ),
            )
        )


def test_delete_object_cascades_and_certified_blocked(harness, deps_ok):
    asyncio.run(ontology_service.upsert_object_async("Order", _object_payload()))
    asyncio.run(ontology_service.upsert_object_async("User", _object_payload(properties=[dict(VALID_PROPERTY)])))
    asyncio.run(
        ontology_service.upsert_relation_async(
            "r1",
            {
                "from_object_name": "Order",
                "to_object_name": "User",
                "from_columns": ["order_id"],
                "to_columns": ["order_id"],
            },
        )
    )
    binding = dict(SEMANTIC_BINDING)
    asyncio.run(
        ontology_service.upsert_object_async(
            "Cert", _object_payload(properties=[dict(VALID_PROPERTY)], bindings=[dict(binding)])
        )
    )
    asyncio.run(ontology_service.set_object_status_async("Cert", "certified", {}))
    with pytest.raises(OntologyStateInvalidError, match="certified 对象禁止删除"):
        asyncio.run(ontology_service.delete_object_async("Cert"))
    asyncio.run(ontology_service.delete_object_async("Order"))
    assert asyncio.run(ontology_service.get_object_async("Order")) is None
    assert asyncio.run(ontology_service.list_relations_async(from_object_name="Order")) == []
    # 复活模式：同名重建复用原行
    asyncio.run(ontology_service.upsert_object_async("Order", _object_payload()))
    detail = asyncio.run(ontology_service.get_object_async("Order"))
    assert detail["status"] == "draft"
    assert json.dumps(detail["properties"], ensure_ascii=False)


def test_binding_semantic_config_must_exist(harness, monkeypatch):
    """绑定引用的语义模型必须真实存在（存在性校验；归属检查不覆盖悬空 ID）。"""

    async def missing_ids(config_ids):
        return set()

    monkeypatch.setattr(ontology_service_module, "semantic_config_dao", _stub_semantic_config_dao(missing_ids))
    with pytest.raises(OntologyInvalidError, match="语义模型不存在"):
        asyncio.run(
            ontology_service.upsert_object_async("Order", _object_payload(bindings=[dict(SEMANTIC_BINDING)]))
        )
    # 无绑定的对象不受影响
    asyncio.run(ontology_service.upsert_object_async("Order", _object_payload()))


def test_relation_from_columns_must_reference_defined_property(harness, deps_ok):
    """起始侧关联列必须引用 from 对象已定义的属性（外键侧不要求是标识属性）。"""
    asyncio.run(
        ontology_service.upsert_object_async(
            "Order",
            _object_payload(properties=[dict(VALID_PROPERTY), {"property_name": "user_name", "value_type": "String"}]),
        )
    )
    asyncio.run(ontology_service.upsert_object_async("User", _object_payload(properties=[dict(VALID_PROPERTY)])))
    with pytest.raises(OntologyInvalidError, match="已定义的属性"):
        asyncio.run(
            ontology_service.upsert_relation_async(
                "r1",
                {
                    "from_object_name": "Order",
                    "to_object_name": "User",
                    "from_columns": ["ghost_col"],
                    "to_columns": ["order_id"],
                },
            )
        )
    # 引用已定义的非标识属性：放行
    saved = asyncio.run(
        ontology_service.upsert_relation_async(
            "r1",
            {
                "from_object_name": "Order",
                "to_object_name": "User",
                "from_columns": ["user_name"],
                "to_columns": ["order_id"],
            },
        )
    )
    assert saved["relation_name"] == "r1"


def test_bindings_roundtrip_via_mapping_table(harness, deps_ok):
    """绑定独立存映射表：详情回读、编辑全量重写无残留、删除后归属释放。"""
    binding = dict(SEMANTIC_BINDING)
    asyncio.run(ontology_service.upsert_object_async("Order", _object_payload(bindings=[dict(binding)])))
    detail = asyncio.run(ontology_service.get_object_async("Order"))
    assert {k: v for k, v in detail["bindings"][0].items() if k != "object_id"} == {
        "binding_type": "semantic",
        "semantic_config_id": "cfg-1",
        "table_alias": "dwd_order",
        "column_mappings": {"order_id": "order_no"},
    }
    assert detail["bindings"][0]["object_id"] == detail["id"]
    # 编辑换表别名：映射表全量重写，旧行不残留（同对象重绑自身放行）
    edited = {**binding, "table_alias": "dwd_order_2"}
    asyncio.run(
        ontology_service.upsert_object_async(
            "Order", _object_payload(bindings=[dict(edited)], confirm_destructive=True), allow_overwrite=True
        )
    )
    detail = asyncio.run(ontology_service.get_object_async("Order"))
    assert [item["table_alias"] for item in detail["bindings"]] == ["dwd_order_2"]
    # 删除对象后归属释放：其他对象可占用同一绑定
    asyncio.run(ontology_service.delete_object_async("Order"))
    asyncio.run(
        ontology_service.upsert_object_async(
            "Order2", _object_payload(properties=[dict(VALID_PROPERTY)], bindings=[dict(binding)])
        )
    )
    detail = asyncio.run(ontology_service.get_object_async("Order2"))
    assert detail["bindings"][0]["semantic_config_id"] == "cfg-1"


def test_binding_same_object_rebind_allowed(harness, deps_ok):
    """编辑自身携带原绑定：归属检查排除自身，不误报占用。"""
    binding = dict(SEMANTIC_BINDING)
    asyncio.run(ontology_service.upsert_object_async("Order", _object_payload(bindings=[dict(binding)])))
    saved = asyncio.run(
        ontology_service.upsert_object_async(
            "Order",
            _object_payload(display_name="订单A", bindings=[dict(binding)], confirm_destructive=False),
            allow_overwrite=True,
        )
    )
    assert saved["display_name"] == "订单A"
    assert saved["bindings"][0]["table_alias"] == "dwd_order"
