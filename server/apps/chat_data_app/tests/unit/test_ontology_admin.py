"""本体管理单测：对象/属性/关系 CRUD、校验器红线、治理门禁与状态机。

harness 与 test_setting_service 同模式：sqlite 内存库建全量表 + monkeypatch
session_scope，DAO 走真实 ORM；全局枚举存在性打桩在 global_enum_service，
语义模型存在性/维度索引打桩在 semantic_config_dao。
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


class _StubSemanticConfigDao:
    """语义模型 DAO 桩：存在性放行 + 维度索引（维度名 → 所属表）。"""

    def __init__(self, existing_ids: set[str] | None = None, dimension_index: dict[str, dict[str, str]] | None = None):
        self._existing_ids = existing_ids
        self._dimension_index = dimension_index or {}

    async def filter_existing_config_ids_async(self, config_ids):
        if self._existing_ids is None:
            return set(config_ids)
        return set(config_ids) & self._existing_ids

    async def get_config_dimensions_async(self, config_ids):
        return {config_id: self._dimension_index.get(config_id, {}) for config_id in config_ids}


VALID_PROPERTY = {
    "property_name": "order_id",
    "display_name": "订单ID",
    "value_type": "String",
    "is_identifier": True,
}

# 语义模型映射（层级 = 组 → 模型 → 维度映射，不触碰物理表）
SEMANTIC_MAPPING = {
    "semantic_config_id": "cfg-1",
    "column_mappings": {"order_id": "订单编号"},
}

# 维度索引桩：cfg-1/cfg-2 的维度跨 orders/users 两张表（跨表拒绝用例依赖），cfg-user 挂 users 表
DIMENSION_INDEX = {
    "cfg-1": {"订单编号": "orders", "用户编号": "users"},
    "cfg-2": {"订单编号": "orders"},
    "cfg-user": {"用户编号": "users"},
}


def _object_payload(**overrides):
    payload = {
        "display_name": "订单",
        "properties": [{**VALID_PROPERTY}],
        "mappings": [],
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
    """外部存在性校验放行（全局枚举 + 映射的语义模型与维度），专项用例单独收紧。"""

    async def fake_get_enum(enum_name, business_domain=""):
        return {"business_domain": business_domain, "enum_name": enum_name}

    monkeypatch.setattr(ontology_service_module, "global_enum_service", _stub_enum_service(fake_get_enum))
    monkeypatch.setattr(
        ontology_service_module,
        "semantic_config_dao",
        _StubSemanticConfigDao(dimension_index=DIMENSION_INDEX),
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


def test_mapping_column_mapping_unknown_property_rejected(harness, deps_ok):
    with pytest.raises(OntologyInvalidError, match="未定义属性"):
        asyncio.run(
            ontology_service.upsert_object_async(
                "Order",
                _object_payload(
                    mappings=[
                        {
                            "semantic_config_id": "cfg-1",
                            "column_mappings": {"unknown_prop": "订单编号"},
                        }
                    ]
                ),
            )
        )


def test_mapping_unknown_dimension_rejected(harness, deps_ok):
    """映射右值必须是所选模型中真实存在的维度（本体只引用模型维度，不定义物理列）。"""
    with pytest.raises(OntologyInvalidError, match="不存在的维度"):
        asyncio.run(
            ontology_service.upsert_object_async(
                "Order",
                _object_payload(
                    mappings=[
                        {
                            "semantic_config_id": "cfg-1",
                            "column_mappings": {"order_id": "幽灵维度"},
                        }
                    ]
                ),
            )
        )


def test_mapping_cross_table_dimensions_rejected(harness, deps_ok):
    """同一映射的维度必须同表（实体锚点唯一），跨表映射拒绝。"""
    with pytest.raises(OntologyInvalidError, match="跨多张表"):
        asyncio.run(
            ontology_service.upsert_object_async(
                "Order",
                _object_payload(
                    properties=[
                        dict(VALID_PROPERTY),
                        {"property_name": "user_name", "value_type": "String"},
                    ],
                    mappings=[
                        {
                            "semantic_config_id": "cfg-1",
                            "column_mappings": {"order_id": "订单编号", "user_name": "用户编号"},
                        }
                    ],
                ),
            )
        )


def test_mapping_ownership_conflict_rejected(harness, deps_ok):
    mapping = dict(SEMANTIC_MAPPING)
    asyncio.run(ontology_service.upsert_object_async("Order", _object_payload(mappings=[mapping])))
    with pytest.raises(OntologyInvalidError, match="已被对象"):
        asyncio.run(
            ontology_service.upsert_object_async(
                "Order2",
                _object_payload(properties=[dict(VALID_PROPERTY)], mappings=[dict(mapping)]),
            )
        )


def test_mapping_change_requires_destructive_confirm(harness, deps_ok):
    mapping = dict(SEMANTIC_MAPPING)
    asyncio.run(ontology_service.upsert_object_async("Order", _object_payload(mappings=[dict(mapping)])))
    edited_mapping = {**mapping, "semantic_config_id": "cfg-2"}
    with pytest.raises(OntologyStateInvalidError, match="破坏性变更"):
        asyncio.run(
            ontology_service.upsert_object_async(
                "Order", _object_payload(mappings=[edited_mapping]), allow_overwrite=True
            )
        )
    saved = asyncio.run(
        ontology_service.upsert_object_async(
            "Order",
            _object_payload(mappings=[edited_mapping], confirm_destructive=True),
            allow_overwrite=True,
        )
    )
    assert saved["mappings"][0]["semantic_config_id"] == "cfg-2"


def test_certified_requires_mapping(harness, deps_ok):
    asyncio.run(ontology_service.upsert_object_async("Order", _object_payload()))
    # 无映射：拒绝
    with pytest.raises(OntologyInvalidError, match="语义模型映射"):
        asyncio.run(ontology_service.set_object_status_async("Order", "certified", {}))
    # 补上语义模型映射后可认证
    asyncio.run(
        ontology_service.upsert_object_async(
            "Order", _object_payload(mappings=[dict(SEMANTIC_MAPPING)], confirm_destructive=True), allow_overwrite=True
        )
    )
    asyncio.run(ontology_service.set_object_status_async("Order", "certified", {}))
    # certified 编辑时移除唯一映射：冻结门禁拒绝
    with pytest.raises(OntologyStateInvalidError, match="语义模型映射"):
        asyncio.run(
            ontology_service.upsert_object_async(
                "Order", _object_payload(mappings=[], confirm_destructive=True), allow_overwrite=True
            )
        )


def test_certified_freeze_blocks_key_and_property_change(harness, deps_ok):
    mapping = dict(SEMANTIC_MAPPING)
    asyncio.run(
        ontology_service.upsert_object_async(
            "Order",
            _object_payload(
                properties=[dict(VALID_PROPERTY), {"property_name": "amount", "value_type": "Decimal"}],
                mappings=[dict(mapping)],
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
                    mappings=[dict(mapping)],
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
                "Order", _object_payload(mappings=[dict(mapping)], properties=[dict(VALID_PROPERTY)]),
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
    order_mapping = dict(SEMANTIC_MAPPING)
    user_mapping = {
        "semantic_config_id": "cfg-user",
        "column_mappings": {"order_id": "用户编号"},
    }
    asyncio.run(
        ontology_service.upsert_object_async(
            "Order", _object_payload(properties=[dict(VALID_PROPERTY)], mappings=[order_mapping])
        )
    )
    asyncio.run(
        ontology_service.upsert_object_async(
            "User", _object_payload(properties=[dict(VALID_PROPERTY)], mappings=[user_mapping])
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


def test_relation_status_manual_migration(harness, deps_ok):
    """关系状态手动迁移：上限=两侧对象较低者；级联只降不升，升级走手动入口。"""
    order_mapping = dict(SEMANTIC_MAPPING)
    user_mapping = {"semantic_config_id": "cfg-user", "column_mappings": {"order_id": "用户编号"}}
    asyncio.run(
        ontology_service.upsert_object_async(
            "Order", _object_payload(properties=[dict(VALID_PROPERTY)], mappings=[order_mapping])
        )
    )
    asyncio.run(
        ontology_service.upsert_object_async(
            "User", _object_payload(properties=[dict(VALID_PROPERTY)], mappings=[user_mapping])
        )
    )
    asyncio.run(ontology_service.set_object_status_async("Order", "certified", {}))
    # User 升 experimental → 关系创建时 derived = 两侧较低者 = experimental
    asyncio.run(ontology_service.set_object_status_async("User", "experimental", {}))
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
    # 超过上限（certified > experimental）：拒绝
    with pytest.raises(OntologyStateInvalidError, match="上限"):
        asyncio.run(ontology_service.set_relation_status_async("r1", "certified"))
    # 同状态迁移：幂等返回
    same = asyncio.run(ontology_service.set_relation_status_async("r1", "experimental"))
    assert same["status"] == "experimental"
    # User 降级 draft → 关系自动级联降级
    asyncio.run(ontology_service.set_object_status_async("User", "draft", {}))
    assert asyncio.run(ontology_service.list_relations_async())[0]["status"] == "draft"
    # 对象仍是 draft：关系升 experimental 超上限，拒绝
    with pytest.raises(OntologyStateInvalidError, match="上限"):
        asyncio.run(ontology_service.set_relation_status_async("r1", "experimental"))
    # User 升回 experimental（级联不升关系）→ 手动升级放行
    asyncio.run(ontology_service.set_object_status_async("User", "experimental", {}))
    assert asyncio.run(ontology_service.list_relations_async())[0]["status"] == "draft"
    upgraded = asyncio.run(ontology_service.set_relation_status_async("r1", "experimental"))
    assert upgraded["status"] == "experimental"


def test_relation_status_deprecated_final_and_guard(harness, deps_ok):
    """deprecated 仅随对象级联：手动迁移 deprecated 关系被拒；废弃对象的关系同样拒绝手动迁移。"""
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
    asyncio.run(
        ontology_service.set_object_status_async(
            "Order", "deprecated", {"deprecation_reason": "口径迁移", "successor_object_name": "User"}
        )
    )
    relations = asyncio.run(ontology_service.list_relations_async())
    assert relations[0]["status"] == "deprecated"
    with pytest.raises(OntologyStateInvalidError, match="终态"):
        asyncio.run(ontology_service.set_relation_status_async("r1", "draft"))
    with pytest.raises(OntologyStateInvalidError, match="deprecated"):
        asyncio.run(ontology_service.set_relation_status_async("r1", "deprecated"))


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
    mapping = dict(SEMANTIC_MAPPING)
    asyncio.run(
        ontology_service.upsert_object_async(
            "Cert", _object_payload(properties=[dict(VALID_PROPERTY)], mappings=[dict(mapping)])
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


def test_mapping_semantic_config_must_exist(harness, monkeypatch):
    """映射引用的语义模型必须真实存在（存在性校验；归属检查不覆盖悬空 ID）。"""
    monkeypatch.setattr(
        ontology_service_module,
        "semantic_config_dao",
        _StubSemanticConfigDao(existing_ids=set(), dimension_index=DIMENSION_INDEX),
    )
    with pytest.raises(OntologyInvalidError, match="语义模型不存在"):
        asyncio.run(ontology_service.upsert_object_async("Order", _object_payload(mappings=[dict(SEMANTIC_MAPPING)])))
    # 无映射的对象不受影响
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


def test_mappings_roundtrip_via_mapping_table(harness, deps_ok):
    """映射独立存映射表：详情回读、编辑全量重写无残留、删除后归属释放。"""
    mapping = dict(SEMANTIC_MAPPING)
    asyncio.run(ontology_service.upsert_object_async("Order", _object_payload(mappings=[dict(mapping)])))
    detail = asyncio.run(ontology_service.get_object_async("Order"))
    assert {k: v for k, v in detail["mappings"][0].items() if k != "object_id"} == {
        "binding_type": "semantic",
        "semantic_config_id": "cfg-1",
        "column_mappings": {"order_id": "订单编号"},
    }
    assert detail["mappings"][0]["object_id"] == detail["id"]
    # 编辑换模型：映射表全量重写，旧行不残留（同对象重绑自身放行）
    edited = {**mapping, "semantic_config_id": "cfg-2"}
    asyncio.run(
        ontology_service.upsert_object_async(
            "Order", _object_payload(mappings=[dict(edited)], confirm_destructive=True), allow_overwrite=True
        )
    )
    detail = asyncio.run(ontology_service.get_object_async("Order"))
    assert [item["semantic_config_id"] for item in detail["mappings"]] == ["cfg-2"]
    # 删除对象后归属释放：其他对象可占用同一映射
    asyncio.run(ontology_service.delete_object_async("Order"))
    asyncio.run(
        ontology_service.upsert_object_async(
            "Order2", _object_payload(properties=[dict(VALID_PROPERTY)], mappings=[dict(mapping)])
        )
    )
    detail = asyncio.run(ontology_service.get_object_async("Order2"))
    assert detail["mappings"][0]["semantic_config_id"] == "cfg-1"


def test_mapping_same_object_rebind_allowed(harness, deps_ok):
    """编辑自身携带原映射：归属检查排除自身，不误报占用。"""
    mapping = dict(SEMANTIC_MAPPING)
    asyncio.run(ontology_service.upsert_object_async("Order", _object_payload(mappings=[dict(mapping)])))
    saved = asyncio.run(
        ontology_service.upsert_object_async(
            "Order",
            _object_payload(display_name="订单A", mappings=[dict(mapping)], confirm_destructive=False),
            allow_overwrite=True,
        )
    )
    assert saved["display_name"] == "订单A"
    assert saved["mappings"][0]["semantic_config_id"] == "cfg-1"


def test_property_identifier_rejects_decimal(harness, deps_ok):
    """PK 红线（对齐 Palantir）：浮点/小数类型不可做标识属性，非标识 Decimal 正常放行。"""
    with pytest.raises(OntologyInvalidError, match="不可做主键"):
        asyncio.run(
            ontology_service.upsert_object_async(
                "Order",
                _object_payload(
                    properties=[{**VALID_PROPERTY, "property_name": "amount", "value_type": "Decimal"}]
                ),
            )
        )
    asyncio.run(
        ontology_service.upsert_object_async(
            "Order",
            _object_payload(
                properties=[
                    dict(VALID_PROPERTY),
                    {
                        "property_name": "amount",
                        "display_name": "金额",
                        "value_type": "Decimal",
                        "is_identifier": False,
                    },
                ]
            ),
        )
    )
    saved = asyncio.run(ontology_service.get_object_async("Order"))
    assert {item["property_name"] for item in saved["properties"]} == {"order_id", "amount"}


def test_injection_bundle_excludes_non_active_relations(harness, deps_ok):
    """AI 目录只注入 certified/experimental 关系：draft 手动降级后不进目录（deprecated 随对象级联同验证）。"""
    from apps.chat_data_app.daos.ontology_dao import OntologyDao

    order_mapping = dict(SEMANTIC_MAPPING)
    user_mapping = {"semantic_config_id": "cfg-user", "column_mappings": {"order_id": "用户编号"}}
    asyncio.run(
        ontology_service.upsert_object_async(
            "Order", _object_payload(properties=[dict(VALID_PROPERTY)], mappings=[order_mapping])
        )
    )
    asyncio.run(
        ontology_service.upsert_object_async(
            "User", _object_payload(properties=[dict(VALID_PROPERTY)], mappings=[user_mapping])
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
    # certified 关系注入
    bundle = asyncio.run(OntologyDao().load_injection_bundle_async(["cfg-1", "cfg-user"]))
    assert [item["relation_name"] for item in bundle["relations"]] == ["r1"]
    # 手动降级 draft → 不注入（对象仍 certified，排除只可能来自关系状态过滤）
    asyncio.run(ontology_service.set_relation_status_async("r1", "draft"))
    bundle = asyncio.run(OntologyDao().load_injection_bundle_async(["cfg-1", "cfg-user"]))
    assert bundle["relations"] == []
    # 手动升回 experimental → 恢复注入
    asyncio.run(ontology_service.set_relation_status_async("r1", "experimental"))
    bundle = asyncio.run(OntologyDao().load_injection_bundle_async(["cfg-1", "cfg-user"]))
    assert len(bundle["relations"]) == 1
    # Order 对象废弃 → 级联 deprecated，对象与关系双双退出目录
    asyncio.run(
        ontology_service.set_object_status_async(
            "Order", "deprecated", {"deprecation_reason": "口径迁移", "successor_object_name": "User"}
        )
    )
    bundle = asyncio.run(OntologyDao().load_injection_bundle_async(["cfg-1", "cfg-user"]))
    # Order 退出目录；User 仍 certified 合法保留
    assert [item["object_name"] for item in bundle["objects"]] == ["User"]
    assert bundle["relations"] == []
