"""本体管理单测：对象类型/属性/关系类型 CRUD、校验器红线、治理门禁与状态机。

harness 与 test_setting_service 同模式：sqlite 内存库建全量表 + monkeypatch
session_scope/transaction_scope，DAO 走真实 ORM（service 事务作用域复用同一
sqlite 会话，多步写失败整体回滚）；全局枚举存在性打桩在 global_enum_service，
语义模型存在性/维度索引/所属组打桩在 semantic_config_dao。
"""

import asyncio
import importlib
import json
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.chat_data_app.daos import dao_session
from apps.chat_data_app.services.ontology import (
    OntologyAlreadyExistsError,
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
    """语义模型 DAO 桩：存在性 + 维度索引（维度名→所属表）+ 所属数据源组。"""

    def __init__(
        self,
        existing_ids: set[str] | None = None,
        dimension_index: dict[str, dict[str, str]] | None = None,
        group_ids: dict[str, str] | None = None,
    ):
        self._existing_ids = existing_ids
        self._dimension_index = dimension_index or {}
        self._group_ids = group_ids or {}

    async def filter_existing_config_ids_async(self, config_ids):
        if self._existing_ids is None:
            return set(config_ids)
        return set(config_ids) & self._existing_ids

    async def get_config_dimensions_async(self, config_ids):
        return {config_id: self._dimension_index.get(config_id, {}) for config_id in config_ids}

    async def get_config_group_ids_async(self, config_ids):
        return {config_id: self._group_ids[config_id] for config_id in config_ids if config_id in self._group_ids}


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
        active_session = dao_session._ORM_SESSION.get()
        if active_session is not None:
            yield active_session
            return
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    @asynccontextmanager
    async def test_transaction_scope():
        """service 事务作用域：把环境会话注入 ContextVar，DAO 复用同一 sqlite 会话。"""
        active_session = dao_session._ORM_SESSION.get()
        if active_session is not None:
            yield active_session
            return
        async with session_factory() as session:
            token = dao_session._ORM_SESSION.set(session)
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                dao_session._ORM_SESSION.reset(token)

    monkeypatch.setattr("apps.chat_data_app.daos.ontology_dao.session_scope", test_session_scope)
    monkeypatch.setattr(ontology_service_module, "transaction_scope", test_transaction_scope)
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
            ontology_service.upsert_object_type_async(
                "Order",
                _object_payload(properties=[{**VALID_PROPERTY, "is_identifier": False}]),
            )
        )


def test_object_create_rejects_bad_api_name(harness, deps_ok):
    with pytest.raises(OntologyNameInvalidError):
        asyncio.run(
            ontology_service.upsert_object_type_async(
                "bad name!",
                _object_payload(properties=[{**VALID_PROPERTY, "property_name": "ok_id", "is_identifier": True}]),
            )
        )


def test_object_create_and_get(harness, deps_ok):
    saved = asyncio.run(ontology_service.upsert_object_type_async("Order", _object_payload()))
    assert saved["display_name"] == "订单"
    assert saved["status"] == "draft"
    detail = asyncio.run(ontology_service.get_object_type_async("Order"))
    assert detail["object_type_name"] == "Order"
    assert detail["properties"][0]["property_name"] == "order_id"
    assert detail["properties"][0]["is_identifier"] is True


def test_object_create_rejects_duplicate_without_overwrite(harness, deps_ok):
    asyncio.run(ontology_service.upsert_object_type_async("Order", _object_payload()))
    with pytest.raises(OntologyAlreadyExistsError, match="已存在"):
        asyncio.run(ontology_service.upsert_object_type_async("Order", _object_payload()))


def test_mapping_column_mapping_unknown_property_rejected(harness, deps_ok):
    with pytest.raises(OntologyInvalidError, match="未定义属性"):
        asyncio.run(
            ontology_service.upsert_object_type_async(
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
            ontology_service.upsert_object_type_async(
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
            ontology_service.upsert_object_type_async(
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
    asyncio.run(ontology_service.upsert_object_type_async("Order", _object_payload(mappings=[mapping])))
    with pytest.raises(OntologyInvalidError, match="已被对象类型"):
        asyncio.run(
            ontology_service.upsert_object_type_async(
                "Order2",
                _object_payload(properties=[dict(VALID_PROPERTY)], mappings=[dict(mapping)]),
            )
        )


def test_mapping_change_requires_destructive_confirm(harness, deps_ok):
    mapping = dict(SEMANTIC_MAPPING)
    asyncio.run(ontology_service.upsert_object_type_async("Order", _object_payload(mappings=[dict(mapping)])))
    edited_mapping = {**mapping, "semantic_config_id": "cfg-2"}
    with pytest.raises(OntologyStateInvalidError, match="破坏性变更"):
        asyncio.run(
            ontology_service.upsert_object_type_async(
                "Order", _object_payload(mappings=[edited_mapping]), allow_overwrite=True
            )
        )
    saved = asyncio.run(
        ontology_service.upsert_object_type_async(
            "Order",
            _object_payload(mappings=[edited_mapping], confirm_destructive=True),
            allow_overwrite=True,
        )
    )
    assert saved["mappings"][0]["semantic_config_id"] == "cfg-2"


def test_certified_requires_mapping(harness, deps_ok):
    asyncio.run(ontology_service.upsert_object_type_async("Order", _object_payload()))
    # 无映射：拒绝
    with pytest.raises(OntologyInvalidError, match="语义模型映射"):
        asyncio.run(ontology_service.set_object_type_status_async("Order", "certified", {}))
    # 补上语义模型映射后可认证
    asyncio.run(
        ontology_service.upsert_object_type_async(
            "Order", _object_payload(mappings=[dict(SEMANTIC_MAPPING)], confirm_destructive=True), allow_overwrite=True
        )
    )
    asyncio.run(ontology_service.set_object_type_status_async("Order", "certified", {}))
    # certified 编辑时移除唯一映射：冻结门禁拒绝
    with pytest.raises(OntologyStateInvalidError, match="语义模型映射"):
        asyncio.run(
            ontology_service.upsert_object_type_async(
                "Order", _object_payload(mappings=[], confirm_destructive=True), allow_overwrite=True
            )
        )


def test_certified_freeze_blocks_key_and_property_change(harness, deps_ok):
    mapping = dict(SEMANTIC_MAPPING)
    asyncio.run(
        ontology_service.upsert_object_type_async(
            "Order",
            _object_payload(
                properties=[dict(VALID_PROPERTY), {"property_name": "amount", "value_type": "Decimal"}],
                mappings=[dict(mapping)],
            ),
        )
    )
    asyncio.run(ontology_service.set_object_type_status_async("Order", "certified", {}))
    # 主键组合变更：certified 直接拒绝
    with pytest.raises(OntologyStateInvalidError, match="冻结"):
        asyncio.run(
            ontology_service.upsert_object_type_async(
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
            ontology_service.upsert_object_type_async(
                "Order",
                _object_payload(mappings=[dict(mapping)], properties=[dict(VALID_PROPERTY)]),
                allow_overwrite=True,
            )
        )


def test_status_deprecation_rules_and_finality(harness, deps_ok):
    asyncio.run(ontology_service.upsert_object_type_async("Order", _object_payload()))
    asyncio.run(ontology_service.upsert_object_type_async("Payment", _object_payload()))
    with pytest.raises(OntologyStateInvalidError, match="废弃原因"):
        asyncio.run(ontology_service.set_object_type_status_async("Order", "deprecated", {}))
    with pytest.raises(OntologyNotFoundError, match="替代对象类型不存在"):
        asyncio.run(
            ontology_service.set_object_type_status_async(
                "Order", "deprecated", {"deprecation_reason": "口径迁移", "successor_object_type_name": "Ghost"}
            )
        )
    asyncio.run(
        ontology_service.set_object_type_status_async(
            "Order", "deprecated", {"deprecation_reason": "口径迁移", "successor_object_type_name": "Payment"}
        )
    )
    with pytest.raises(OntologyStateInvalidError, match="不可逆"):
        asyncio.run(ontology_service.set_object_type_status_async("Order", "draft", {}))
    with pytest.raises(OntologyStateInvalidError, match="禁止编辑"):
        asyncio.run(ontology_service.upsert_object_type_async("Order", _object_payload(), allow_overwrite=True))


def test_relation_column_must_reference_target_identifier(harness, deps_ok):
    asyncio.run(
        ontology_service.upsert_object_type_async(
            "Order",
            _object_payload(properties=[dict(VALID_PROPERTY), {"property_name": "user_name", "value_type": "String"}]),
        )
    )
    asyncio.run(ontology_service.upsert_object_type_async("User", _object_payload(properties=[dict(VALID_PROPERTY)])))
    with pytest.raises(OntologyInvalidError, match="标识属性"):
        asyncio.run(
            ontology_service.upsert_relation_async(
                "order_belongs_user",
                {
                    "from_object_type_name": "Order",
                    "to_object_type_name": "User",
                    "from_columns": ["user_name"],
                    "to_columns": ["user_name"],
                },
            )
        )
    saved = asyncio.run(
        ontology_service.upsert_relation_async(
            "order_belongs_user",
            {
                "from_object_type_name": "Order",
                "to_object_type_name": "User",
                "from_columns": ["user_name"],
                "to_columns": ["order_id"],
                "from_verbalize": "{from} 归属于 {to}",
            },
        )
    )
    assert saved["status"] == "draft"
    relation_types = asyncio.run(ontology_service.list_relations_async(from_object_type_name="Order"))
    assert relation_types[0]["from_object_type_name"] == "Order"
    assert relation_types[0]["to_object_type_name"] == "User"


def test_relation_multiplicity_closed_vocabulary(harness, deps_ok):
    asyncio.run(ontology_service.upsert_object_type_async("Order", _object_payload()))
    with pytest.raises(OntologyInvalidError, match="基数"):
        asyncio.run(
            ontology_service.upsert_relation_async(
                "r1",
                {
                    "from_object_type_name": "Order",
                    "to_object_type_name": "Order",
                    "multiplicity": "many_to_many",
                    "from_columns": ["order_id"],
                    "to_columns": ["order_id"],
                },
            )
        )


def test_relation_structural_change_requires_confirm(harness, deps_ok):
    for name, prop in (("Order", dict(VALID_PROPERTY)), ("User", dict(VALID_PROPERTY))):
        asyncio.run(ontology_service.upsert_object_type_async(name, _object_payload(properties=[prop])))
    asyncio.run(
        ontology_service.upsert_relation_async(
            "r1",
            {
                "from_object_type_name": "Order",
                "to_object_type_name": "User",
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
                    "from_object_type_name": "User",
                    "to_object_type_name": "Order",
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
                "from_object_type_name": "User",
                "to_object_type_name": "Order",
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
        ontology_service.upsert_object_type_async(
            "Order", _object_payload(properties=[dict(VALID_PROPERTY)], mappings=[order_mapping])
        )
    )
    asyncio.run(
        ontology_service.upsert_object_type_async(
            "User", _object_payload(properties=[dict(VALID_PROPERTY)], mappings=[user_mapping])
        )
    )
    asyncio.run(ontology_service.set_object_type_status_async("Order", "certified", {}))
    asyncio.run(ontology_service.set_object_type_status_async("User", "certified", {}))
    asyncio.run(
        ontology_service.upsert_relation_async(
            "r1",
            {
                "from_object_type_name": "Order",
                "to_object_type_name": "User",
                "from_columns": ["order_id"],
                "to_columns": ["order_id"],
            },
        )
    )
    relation_types = asyncio.run(ontology_service.list_relations_async())
    assert relation_types[0]["status"] == "certified"
    # Order 降级 → 关系类型自动降级（升级手动）
    asyncio.run(ontology_service.set_object_type_status_async("Order", "experimental", {}))
    relation_types = asyncio.run(ontology_service.list_relations_async())
    assert relation_types[0]["status"] == "experimental"


def test_relation_status_manual_migration(harness, deps_ok):
    """关系类型状态手动迁移：上限=两侧对象类型较低者；级联只降不升，升级走手动入口。"""
    order_mapping = dict(SEMANTIC_MAPPING)
    user_mapping = {"semantic_config_id": "cfg-user", "column_mappings": {"order_id": "用户编号"}}
    asyncio.run(
        ontology_service.upsert_object_type_async(
            "Order", _object_payload(properties=[dict(VALID_PROPERTY)], mappings=[order_mapping])
        )
    )
    asyncio.run(
        ontology_service.upsert_object_type_async(
            "User", _object_payload(properties=[dict(VALID_PROPERTY)], mappings=[user_mapping])
        )
    )
    asyncio.run(ontology_service.set_object_type_status_async("Order", "certified", {}))
    # User 升 experimental → 关系类型创建时 derived = 两侧较低者 = experimental
    asyncio.run(ontology_service.set_object_type_status_async("User", "experimental", {}))
    asyncio.run(
        ontology_service.upsert_relation_async(
            "r1",
            {
                "from_object_type_name": "Order",
                "to_object_type_name": "User",
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
    # User 降级 draft → 关系类型自动级联降级
    asyncio.run(ontology_service.set_object_type_status_async("User", "draft", {}))
    assert asyncio.run(ontology_service.list_relations_async())[0]["status"] == "draft"
    # 对象类型仍是 draft：关系类型升 experimental 超上限，拒绝
    with pytest.raises(OntologyStateInvalidError, match="上限"):
        asyncio.run(ontology_service.set_relation_status_async("r1", "experimental"))
    # User 升回 experimental（级联不升关系类型）→ 手动升级放行
    asyncio.run(ontology_service.set_object_type_status_async("User", "experimental", {}))
    assert asyncio.run(ontology_service.list_relations_async())[0]["status"] == "draft"
    upgraded = asyncio.run(ontology_service.set_relation_status_async("r1", "experimental"))
    assert upgraded["status"] == "experimental"


def test_relation_status_deprecated_final_and_guard(harness, deps_ok):
    """deprecated 仅随对象类型级联：手动迁移 deprecated 关系类型被拒；废弃对象类型的关系类型同样拒绝手动迁移。"""
    asyncio.run(ontology_service.upsert_object_type_async("Order", _object_payload()))
    asyncio.run(ontology_service.upsert_object_type_async("User", _object_payload(properties=[dict(VALID_PROPERTY)])))
    asyncio.run(
        ontology_service.upsert_relation_async(
            "r1",
            {
                "from_object_type_name": "Order",
                "to_object_type_name": "User",
                "from_columns": ["order_id"],
                "to_columns": ["order_id"],
            },
        )
    )
    asyncio.run(
        ontology_service.set_object_type_status_async(
            "Order", "deprecated", {"deprecation_reason": "口径迁移", "successor_object_type_name": "User"}
        )
    )
    relation_types = asyncio.run(ontology_service.list_relations_async())
    assert relation_types[0]["status"] == "deprecated"
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
            ontology_service.upsert_object_type_async(
                "Order",
                _object_payload(
                    properties=[
                        {**VALID_PROPERTY, "value_type": "enum", "enum_ref": "order_status"},
                    ]
                ),
            )
        )


def test_delete_object_cascades_and_certified_blocked(harness, deps_ok):
    asyncio.run(ontology_service.upsert_object_type_async("Order", _object_payload()))
    asyncio.run(ontology_service.upsert_object_type_async("User", _object_payload(properties=[dict(VALID_PROPERTY)])))
    asyncio.run(
        ontology_service.upsert_relation_async(
            "r1",
            {
                "from_object_type_name": "Order",
                "to_object_type_name": "User",
                "from_columns": ["order_id"],
                "to_columns": ["order_id"],
            },
        )
    )
    mapping = dict(SEMANTIC_MAPPING)
    asyncio.run(
        ontology_service.upsert_object_type_async(
            "Cert", _object_payload(properties=[dict(VALID_PROPERTY)], mappings=[dict(mapping)])
        )
    )
    asyncio.run(ontology_service.set_object_type_status_async("Cert", "certified", {}))
    with pytest.raises(OntologyStateInvalidError, match="certified 对象类型禁止删除"):
        asyncio.run(ontology_service.delete_object_type_async("Cert"))
    asyncio.run(ontology_service.delete_object_type_async("Order"))
    assert asyncio.run(ontology_service.get_object_type_async("Order")) is None
    assert asyncio.run(ontology_service.list_relations_async(from_object_type_name="Order")) == []
    # 复活模式：同名重建复用原行
    asyncio.run(ontology_service.upsert_object_type_async("Order", _object_payload()))
    detail = asyncio.run(ontology_service.get_object_type_async("Order"))
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
        asyncio.run(
            ontology_service.upsert_object_type_async("Order", _object_payload(mappings=[dict(SEMANTIC_MAPPING)]))
        )
    # 无映射的对象类型不受影响
    asyncio.run(ontology_service.upsert_object_type_async("Order", _object_payload()))


def test_relation_from_columns_must_reference_defined_property(harness, deps_ok):
    """起始侧关联列必须引用 from 对象类型已定义的属性（外键侧不要求是标识属性）。"""
    asyncio.run(
        ontology_service.upsert_object_type_async(
            "Order",
            _object_payload(properties=[dict(VALID_PROPERTY), {"property_name": "user_name", "value_type": "String"}]),
        )
    )
    asyncio.run(ontology_service.upsert_object_type_async("User", _object_payload(properties=[dict(VALID_PROPERTY)])))
    with pytest.raises(OntologyInvalidError, match="已定义的属性"):
        asyncio.run(
            ontology_service.upsert_relation_async(
                "r1",
                {
                    "from_object_type_name": "Order",
                    "to_object_type_name": "User",
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
                "from_object_type_name": "Order",
                "to_object_type_name": "User",
                "from_columns": ["user_name"],
                "to_columns": ["order_id"],
            },
        )
    )
    assert saved["relation_type_name"] == "r1"


def test_mappings_roundtrip_via_mapping_table(harness, deps_ok):
    """映射独立存映射表：详情回读、编辑全量重写无残留、删除后归属释放。"""
    mapping = dict(SEMANTIC_MAPPING)
    asyncio.run(ontology_service.upsert_object_type_async("Order", _object_payload(mappings=[dict(mapping)])))
    detail = asyncio.run(ontology_service.get_object_type_async("Order"))
    assert {k: v for k, v in detail["mappings"][0].items() if k != "object_type_id"} == {
        "binding_type": "semantic",
        "semantic_config_id": "cfg-1",
        "column_mappings": {"order_id": "订单编号"},
    }
    assert detail["mappings"][0]["object_type_id"] == detail["id"]
    # 编辑换模型：映射表全量重写，旧行不残留（同对象类型重绑自身放行）
    edited = {**mapping, "semantic_config_id": "cfg-2"}
    asyncio.run(
        ontology_service.upsert_object_type_async(
            "Order", _object_payload(mappings=[dict(edited)], confirm_destructive=True), allow_overwrite=True
        )
    )
    detail = asyncio.run(ontology_service.get_object_type_async("Order"))
    assert [item["semantic_config_id"] for item in detail["mappings"]] == ["cfg-2"]
    # 删除对象类型后归属释放：其他对象类型可占用同一映射
    asyncio.run(ontology_service.delete_object_type_async("Order"))
    asyncio.run(
        ontology_service.upsert_object_type_async(
            "Order2", _object_payload(properties=[dict(VALID_PROPERTY)], mappings=[dict(mapping)])
        )
    )
    detail = asyncio.run(ontology_service.get_object_type_async("Order2"))
    assert detail["mappings"][0]["semantic_config_id"] == "cfg-1"


def test_mapping_same_object_rebind_allowed(harness, deps_ok):
    """编辑自身携带原映射：归属检查排除自身，不误报占用。"""
    mapping = dict(SEMANTIC_MAPPING)
    asyncio.run(ontology_service.upsert_object_type_async("Order", _object_payload(mappings=[dict(mapping)])))
    saved = asyncio.run(
        ontology_service.upsert_object_type_async(
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
            ontology_service.upsert_object_type_async(
                "Order",
                _object_payload(properties=[{**VALID_PROPERTY, "property_name": "amount", "value_type": "Decimal"}]),
            )
        )
    asyncio.run(
        ontology_service.upsert_object_type_async(
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
    saved = asyncio.run(ontology_service.get_object_type_async("Order"))
    assert {item["property_name"] for item in saved["properties"]} == {"order_id", "amount"}


def test_injection_bundle_excludes_non_active_relations(harness, deps_ok):
    """AI 目录只注入 certified/experimental 关系类型：draft 手动降级后不进目录（deprecated 随对象类型级联同验证）。"""
    from apps.chat_data_app.daos.ontology_dao import OntologyDao

    order_mapping = dict(SEMANTIC_MAPPING)
    user_mapping = {"semantic_config_id": "cfg-user", "column_mappings": {"order_id": "用户编号"}}
    asyncio.run(
        ontology_service.upsert_object_type_async(
            "Order", _object_payload(properties=[dict(VALID_PROPERTY)], mappings=[order_mapping])
        )
    )
    asyncio.run(
        ontology_service.upsert_object_type_async(
            "User", _object_payload(properties=[dict(VALID_PROPERTY)], mappings=[user_mapping])
        )
    )
    asyncio.run(ontology_service.set_object_type_status_async("Order", "certified", {}))
    asyncio.run(ontology_service.set_object_type_status_async("User", "certified", {}))
    asyncio.run(
        ontology_service.upsert_relation_async(
            "r1",
            {
                "from_object_type_name": "Order",
                "to_object_type_name": "User",
                "from_columns": ["order_id"],
                "to_columns": ["order_id"],
            },
        )
    )
    # certified 关系类型注入
    bundle = asyncio.run(OntologyDao().load_injection_bundle_async(["cfg-1", "cfg-user"]))
    assert [item["relation_type_name"] for item in bundle["relation_types"]] == ["r1"]
    # 手动降级 draft → 不注入（对象类型仍 certified，排除只可能来自关系类型状态过滤）
    asyncio.run(ontology_service.set_relation_status_async("r1", "draft"))
    bundle = asyncio.run(OntologyDao().load_injection_bundle_async(["cfg-1", "cfg-user"]))
    assert bundle["relation_types"] == []
    # 手动升回 experimental → 恢复注入
    asyncio.run(ontology_service.set_relation_status_async("r1", "experimental"))
    bundle = asyncio.run(OntologyDao().load_injection_bundle_async(["cfg-1", "cfg-user"]))
    assert len(bundle["relation_types"]) == 1
    # Order 对象类型废弃 → 级联 deprecated，对象类型与关系类型双双退出目录
    asyncio.run(
        ontology_service.set_object_type_status_async(
            "Order", "deprecated", {"deprecation_reason": "口径迁移", "successor_object_type_name": "User"}
        )
    )
    bundle = asyncio.run(OntologyDao().load_injection_bundle_async(["cfg-1", "cfg-user"]))
    # Order 退出目录；User 仍 certified 合法保留
    assert [item["object_type_name"] for item in bundle["object_types"]] == ["User"]
    assert bundle["relation_types"] == []


# ---------- 本体（纯语义容器，不绑数据源组） ----------


def _ontology_payload(**overrides):
    payload = {"display_name": "电商交易", "description": "交易域"}
    payload.update(overrides)
    return payload


def test_ontology_crud_lifecycle(harness, deps_ok):
    # 创建
    saved = asyncio.run(ontology_service.upsert_ontology_async("ecommerce_trade", _ontology_payload()))
    assert saved["display_name"] == "电商交易"
    # 重复创建未带 allow_overwrite 拒绝（同一 API 名已被占用，走 40068）
    with pytest.raises(OntologyAlreadyExistsError, match="本体已存在"):
        asyncio.run(ontology_service.upsert_ontology_async("ecommerce_trade", _ontology_payload()))
    # 编辑（allow_overwrite）改展示名
    saved = asyncio.run(
        ontology_service.upsert_ontology_async(
            "ecommerce_trade", _ontology_payload(display_name="跨境电商"), allow_overwrite=True
        )
    )
    assert saved["display_name"] == "跨境电商"
    # 列表
    ontologies = asyncio.run(ontology_service.list_ontologies_async())
    assert [item["ontology_name"] for item in ontologies] == ["ecommerce_trade"]
    # 删除
    asyncio.run(ontology_service.delete_ontology_async("ecommerce_trade"))
    assert asyncio.run(ontology_service.list_ontologies_async()) == []
    # 再删报不存在
    with pytest.raises(OntologyNotFoundError):
        asyncio.run(ontology_service.delete_ontology_async("ecommerce_trade"))


def test_ontology_delete_blocked_when_objects_attached(harness, deps_ok):
    ontology = asyncio.run(ontology_service.upsert_ontology_async("ecommerce_trade", _ontology_payload()))
    asyncio.run(
        ontology_service.upsert_object_type_async(
            "Order", _object_payload(ontology_id=int(ontology["id"])), allow_overwrite=False
        )
    )
    with pytest.raises(OntologyStateInvalidError, match="1 个对象类型"):
        asyncio.run(ontology_service.delete_ontology_async("ecommerce_trade"))


def test_object_ontology_id_validation(harness, deps_ok):
    # 本体不存在拒绝
    with pytest.raises(OntologyInvalidError, match="本体不存在"):
        asyncio.run(ontology_service.upsert_object_type_async("Order", _object_payload(ontology_id=999)))
    # 合法本体挂接成功，详情回读 ontology_id
    ontology = asyncio.run(ontology_service.upsert_ontology_async("ecommerce_trade", _ontology_payload()))
    asyncio.run(ontology_service.upsert_object_type_async("Order", _object_payload(ontology_id=int(ontology["id"]))))
    detail = asyncio.run(ontology_service.get_object_type_async("Order"))
    assert detail["ontology_id"] == int(ontology["id"])
    # 未分域（ontology_id=None）合法
    asyncio.run(
        ontology_service.upsert_object_type_async(
            "Misc",
            _object_payload(
                properties=[{**VALID_PROPERTY, "property_name": "misc_id"}],
            ),
        )
    )


def test_object_ontology_reassign(harness, deps_ok):
    """拖拽换本体窄写路径：改归属不动对象类型名/属性，移出本体=ontology_id 置空。"""
    ontology_a = asyncio.run(ontology_service.upsert_ontology_async("ontology_a", _ontology_payload()))
    ontology_b = asyncio.run(
        ontology_service.upsert_ontology_async("ontology_b", _ontology_payload(display_name="供应链"))
    )
    asyncio.run(ontology_service.upsert_object_type_async("Order", _object_payload(ontology_id=int(ontology_a["id"]))))

    # 换到 ontology_b
    updated = asyncio.run(ontology_service.set_object_type_ontology_async("Order", int(ontology_b["id"])))
    assert updated["ontology_id"] == int(ontology_b["id"])
    detail = asyncio.run(ontology_service.get_object_type_async("Order"))
    assert detail["ontology_id"] == int(ontology_b["id"])
    # 属性不受影响（窄写只动 ontology_id）
    assert [p["property_name"] for p in detail["properties"]] == ["order_id"]

    # 移出本体（null）
    updated = asyncio.run(ontology_service.set_object_type_ontology_async("Order", None))
    assert updated["ontology_id"] is None

    # 目标本体不存在拒绝
    with pytest.raises(OntologyNotFoundError, match="目标本体不存在"):
        asyncio.run(ontology_service.set_object_type_ontology_async("Order", 999))

    # 对象类型不存在拒绝
    with pytest.raises(OntologyNotFoundError, match="对象类型不存在"):
        asyncio.run(ontology_service.set_object_type_ontology_async("Ghost", None))


# ---------- 事务原子性 ----------


def test_delete_object_midway_failure_rolls_back(harness, deps_ok, monkeypatch):
    """对象类型删除多步写（关系类型/属性/映射/对象类型）中途失败必须整体回滚，不留半删状态。"""
    asyncio.run(ontology_service.upsert_object_type_async("Order", _object_payload(mappings=[dict(SEMANTIC_MAPPING)])))
    asyncio.run(ontology_service.upsert_object_type_async("User", _object_payload(properties=[dict(VALID_PROPERTY)])))
    asyncio.run(
        ontology_service.upsert_relation_async(
            "r1",
            {
                "from_object_type_name": "Order",
                "to_object_type_name": "User",
                "from_columns": ["order_id"],
                "to_columns": ["order_id"],
            },
        )
    )

    async def boom(object_type_id):
        raise RuntimeError("模拟映射删除失败")

    monkeypatch.setattr(ontology_service._dao, "delete_object_type_mappings_async", boom)
    with pytest.raises(RuntimeError, match="模拟映射删除失败"):
        asyncio.run(ontology_service.delete_object_type_async("Order"))

    detail = asyncio.run(ontology_service.get_object_type_async("Order"))
    assert detail is not None
    assert detail["delete_flag"] == 0
    assert [item["property_name"] for item in detail["properties"]] == ["order_id"]
    assert [item["semantic_config_id"] for item in detail["mappings"]] == ["cfg-1"]
    assert [item["relation_type_name"] for item in asyncio.run(ontology_service.list_relations_async())] == ["r1"]


# ---------- 软删复活与状态口径 ----------


def test_object_upsert_returns_full_detail(harness, deps_ok):
    """写接口返回真实详情（id/属性/映射），不再返回合成局部视图。"""
    saved = asyncio.run(
        ontology_service.upsert_object_type_async("Order", _object_payload(mappings=[dict(SEMANTIC_MAPPING)]))
    )
    assert isinstance(saved["id"], int)
    assert saved["status"] == "draft"
    assert [item["property_name"] for item in saved["properties"]] == ["order_id"]
    assert [item["semantic_config_id"] for item in saved["mappings"]] == ["cfg-1"]


def test_relation_upsert_returns_readback_without_json_keys(harness, deps_ok):
    asyncio.run(ontology_service.upsert_object_type_async("Order", _object_payload()))
    asyncio.run(ontology_service.upsert_object_type_async("User", _object_payload()))
    saved = asyncio.run(
        ontology_service.upsert_relation_async(
            "r1",
            {
                "from_object_type_name": "Order",
                "to_object_type_name": "User",
                "from_columns": ["order_id"],
                "to_columns": ["order_id"],
            },
        )
    )
    assert saved["relation_type_name"] == "r1"
    assert saved["from_columns"] == ["order_id"]
    assert saved["to_columns"] == ["order_id"]
    assert saved["from_object_type_name"] == "Order"
    assert saved["to_object_type_name"] == "User"
    assert "from_columns_json" not in saved
    assert "to_columns_json" not in saved


def test_object_revival_after_experimental_resets_to_draft(harness, deps_ok):
    asyncio.run(ontology_service.upsert_object_type_async("Order", _object_payload()))
    asyncio.run(ontology_service.set_object_type_status_async("Order", "experimental", {}))
    asyncio.run(ontology_service.delete_object_type_async("Order"))
    revived = asyncio.run(ontology_service.upsert_object_type_async("Order", _object_payload()))
    assert revived["status"] == "draft"
    assert asyncio.run(ontology_service.get_object_type_async("Order"))["status"] == "draft"


def test_object_revival_after_deprecation_resets_zombie_fields(harness, deps_ok):
    asyncio.run(ontology_service.upsert_object_type_async("User", _object_payload()))
    asyncio.run(ontology_service.upsert_object_type_async("Order", _object_payload()))
    deprecated = asyncio.run(
        ontology_service.set_object_type_status_async(
            "Order",
            "deprecated",
            {"deprecation_reason": "口径迁移", "successor_object_type_name": "User", "deprecated_until": "2027-01-01"},
        )
    )
    assert deprecated["deprecated_until"] == "2027-01-01 00:00:00"
    asyncio.run(ontology_service.delete_object_type_async("Order"))
    # 同名重建 = 新对象类型：废弃状态/原因/替代对象类型/期限全部重置，不能复辟成不可编辑僵尸
    revived = asyncio.run(ontology_service.upsert_object_type_async("Order", _object_payload()))
    assert revived["status"] == "draft"
    assert revived["deprecation_reason"] == ""
    assert revived["successor_object_type_name"] == ""
    assert revived["deprecated_until"] == ""
    # 复活后可正常编辑（不是废弃终态）
    edited = asyncio.run(
        ontology_service.upsert_object_type_async("Order", _object_payload(display_name="订单新"), allow_overwrite=True)
    )
    assert edited["display_name"] == "订单新"


def test_relation_revival_derives_status_from_endpoints(harness, deps_ok):
    """关系类型软删后同名重建：按当前两端对象类型派生状态，不沿用旧状态/不要求覆盖。"""
    order_mapping = dict(SEMANTIC_MAPPING)
    user_mapping = {"semantic_config_id": "cfg-user", "column_mappings": {"order_id": "用户编号"}}
    asyncio.run(
        ontology_service.upsert_object_type_async(
            "Order", _object_payload(properties=[dict(VALID_PROPERTY)], mappings=[order_mapping])
        )
    )
    asyncio.run(
        ontology_service.upsert_object_type_async(
            "User", _object_payload(properties=[dict(VALID_PROPERTY)], mappings=[user_mapping])
        )
    )
    asyncio.run(ontology_service.set_object_type_status_async("Order", "certified", {}))
    asyncio.run(ontology_service.set_object_type_status_async("User", "certified", {}))
    asyncio.run(
        ontology_service.upsert_relation_async(
            "r1",
            {
                "from_object_type_name": "Order",
                "to_object_type_name": "User",
                "from_columns": ["order_id"],
                "to_columns": ["order_id"],
            },
        )
    )
    asyncio.run(ontology_service.delete_relation_async("r1"))
    # 一端降级后重建关系类型：状态必须是两侧较低者（不能保留旧 certified）
    asyncio.run(ontology_service.set_object_type_status_async("Order", "experimental", {}))
    revived = asyncio.run(
        ontology_service.upsert_relation_async(
            "r1",
            {
                "from_object_type_name": "Order",
                "to_object_type_name": "User",
                "from_columns": ["order_id"],
                "to_columns": ["order_id"],
            },
        )
    )
    assert revived["status"] == "experimental"


def test_relation_structural_edit_downgrades_status(harness, deps_ok):
    """活跃关系类型改连到低状态对象类型：状态降级为 min(旧, 派生)，不能高于任一端。"""
    order_mapping = dict(SEMANTIC_MAPPING)
    user_mapping = {"semantic_config_id": "cfg-user", "column_mappings": {"order_id": "用户编号"}}
    other_mapping = {"semantic_config_id": "cfg-2", "column_mappings": {"order_id": "订单编号"}}
    asyncio.run(
        ontology_service.upsert_object_type_async(
            "Order", _object_payload(properties=[dict(VALID_PROPERTY)], mappings=[order_mapping])
        )
    )
    asyncio.run(
        ontology_service.upsert_object_type_async(
            "User", _object_payload(properties=[dict(VALID_PROPERTY)], mappings=[user_mapping])
        )
    )
    asyncio.run(
        ontology_service.upsert_object_type_async(
            "Other", _object_payload(properties=[dict(VALID_PROPERTY)], mappings=[other_mapping])
        )
    )
    for name in ("Order", "User"):
        asyncio.run(ontology_service.set_object_type_status_async(name, "certified", {}))
    asyncio.run(
        ontology_service.upsert_relation_async(
            "r1",
            {
                "from_object_type_name": "Order",
                "to_object_type_name": "User",
                "from_columns": ["order_id"],
                "to_columns": ["order_id"],
            },
        )
    )
    # Other 仍是 draft：改连到 Other（结构变更）→ 关系类型状态降级为 draft
    saved = asyncio.run(
        ontology_service.upsert_relation_async(
            "r1",
            {
                "from_object_type_name": "Order",
                "to_object_type_name": "Other",
                "from_columns": ["order_id"],
                "to_columns": ["order_id"],
                "confirm_destructive": True,
            },
            allow_overwrite=True,
        )
    )
    assert saved["status"] == "draft"


def test_ontology_recreate_after_delete_without_overwrite(harness, deps_ok):
    asyncio.run(ontology_service.upsert_ontology_async("ecommerce_trade", _ontology_payload()))
    asyncio.run(ontology_service.delete_ontology_async("ecommerce_trade"))
    revived = asyncio.run(
        ontology_service.upsert_ontology_async("ecommerce_trade", _ontology_payload(display_name="复活的交易"))
    )
    assert revived["display_name"] == "复活的交易"
    assert revived["delete_flag"] == 0


def test_relation_duplicate_without_overwrite_raises_already_exists(harness, deps_ok):
    asyncio.run(ontology_service.upsert_object_type_async("Order", _object_payload()))
    asyncio.run(ontology_service.upsert_object_type_async("User", _object_payload()))
    payload = {
        "from_object_type_name": "Order",
        "to_object_type_name": "User",
        "from_columns": ["order_id"],
        "to_columns": ["order_id"],
    }
    asyncio.run(ontology_service.upsert_relation_async("r1", payload))
    with pytest.raises(OntologyAlreadyExistsError, match="已存在"):
        asyncio.run(ontology_service.upsert_relation_async("r1", payload))


# ---------- certified 门禁 ----------


def test_certified_rejects_empty_column_mappings(harness, deps_ok):
    asyncio.run(
        ontology_service.upsert_object_type_async(
            "Order", _object_payload(mappings=[{"semantic_config_id": "cfg-1", "column_mappings": {}}])
        )
    )
    with pytest.raises(OntologyInvalidError, match="column_mappings"):
        asyncio.run(ontology_service.set_object_type_status_async("Order", "certified", {}))


def test_certified_rejects_identifier_not_mapped(harness, deps_ok):
    asyncio.run(
        ontology_service.upsert_object_type_async(
            "Order",
            _object_payload(
                properties=[dict(VALID_PROPERTY), {"property_name": "amount", "value_type": "Decimal"}],
                mappings=[{"semantic_config_id": "cfg-1", "column_mappings": {"amount": "订单编号"}}],
            ),
        )
    )
    with pytest.raises(OntologyInvalidError, match="标识属性"):
        asyncio.run(ontology_service.set_object_type_status_async("Order", "certified", {}))


def test_certified_accepts_full_identifier_mapping(harness, deps_ok):
    asyncio.run(ontology_service.upsert_object_type_async("Order", _object_payload(mappings=[dict(SEMANTIC_MAPPING)])))
    certified = asyncio.run(ontology_service.set_object_type_status_async("Order", "certified", {}))
    assert certified["status"] == "certified"


# ---------- 对象类型编辑：关系类型关联列悬空门禁 ----------


def _relation_payload(from_name="Order", to_name="User", from_columns=None, to_columns=None):
    return {
        "from_object_type_name": from_name,
        "to_object_type_name": to_name,
        "from_columns": from_columns or ["order_id"],
        "to_columns": to_columns or ["order_id"],
    }


def test_edit_blocked_when_from_columns_property_removed(harness, deps_ok):
    asyncio.run(
        ontology_service.upsert_object_type_async(
            "Order",
            _object_payload(properties=[dict(VALID_PROPERTY), {"property_name": "user_name", "value_type": "String"}]),
        )
    )
    asyncio.run(ontology_service.upsert_object_type_async("User", _object_payload()))
    asyncio.run(ontology_service.upsert_relation_async("r1", _relation_payload(from_columns=["user_name"])))
    with pytest.raises(OntologyInvalidError, match="关系类型 r1"):
        asyncio.run(
            ontology_service.upsert_object_type_async(
                "Order", _object_payload(properties=[dict(VALID_PROPERTY)]), allow_overwrite=True
            )
        )


def test_edit_blocked_when_identifier_change_breaks_to_columns(harness, deps_ok):
    asyncio.run(ontology_service.upsert_object_type_async("Order", _object_payload()))
    asyncio.run(ontology_service.upsert_object_type_async("User", _object_payload()))
    asyncio.run(ontology_service.upsert_relation_async("r1", _relation_payload()))
    with pytest.raises(OntologyInvalidError, match="不再是标识属性"):
        asyncio.run(
            ontology_service.upsert_object_type_async(
                "User",
                _object_payload(
                    properties=[
                        {"property_name": "order_id", "value_type": "String", "is_identifier": False},
                        {"property_name": "user_id", "value_type": "String", "is_identifier": True},
                    ]
                ),
                allow_overwrite=True,
            )
        )


# ---------- 其他 P2 ----------


def test_deprecated_until_parse_and_invalid(harness, deps_ok):
    asyncio.run(ontology_service.upsert_object_type_async("User", _object_payload()))
    asyncio.run(ontology_service.upsert_object_type_async("Order", _object_payload()))
    with pytest.raises(OntologyInvalidError, match="废弃期限"):
        asyncio.run(
            ontology_service.set_object_type_status_async(
                "Order",
                "deprecated",
                {
                    "deprecation_reason": "口径迁移",
                    "successor_object_type_name": "User",
                    "deprecated_until": "2027/01/01",
                },
            )
        )
    # 非法值被拒绝后对象类型保持原状态
    assert asyncio.run(ontology_service.get_object_type_async("Order"))["status"] == "draft"
    saved = asyncio.run(
        ontology_service.set_object_type_status_async(
            "Order",
            "deprecated",
            {"deprecation_reason": "口径迁移", "successor_object_type_name": "User", "deprecated_until": "2027-01-01"},
        )
    )
    assert saved["deprecated_until"] == "2027-01-01 00:00:00"


def test_ontology_name_length_limit(harness, deps_ok):
    with pytest.raises(OntologyNameInvalidError, match="长度 1-64"):
        asyncio.run(ontology_service.upsert_ontology_async("d" * 65, _ontology_payload()))


# ---------- 多源属性单源 / one_to_one from 侧唯一性（2026-10-17 补） ----------


def test_multi_source_non_identifier_property_rejected(harness, deps_ok):  # noqa: F811
    """多源对象类型：非标识属性只能出现在一个映射；标识属性可跨源。"""
    payload = {
        "display_name": "多源订单",
        "properties": [
            {"property_name": "order_id", "value_type": "String", "is_identifier": True},
            {"property_name": "amount", "value_type": "String"},
        ],
        "mappings": [
            {"semantic_config_id": "cfg-1", "column_mappings": {"order_id": "订单编号", "amount": "订单编号"}},
            {"semantic_config_id": "cfg-2", "column_mappings": {"order_id": "订单编号", "amount": "订单编号"}},
        ],
    }
    with pytest.raises(OntologyInvalidError, match="非标识属性只能单源映射"):
        asyncio.run(ontology_service.upsert_object_type_async("MultiOrder", payload))

    # 标识属性跨源重复允许；非标识属性只留一个来源
    payload["mappings"] = [
        {"semantic_config_id": "cfg-1", "column_mappings": {"order_id": "订单编号", "amount": "订单编号"}},
        {"semantic_config_id": "cfg-2", "column_mappings": {"order_id": "订单编号"}},
    ]
    detail = asyncio.run(ontology_service.upsert_object_type_async("MultiOrder", payload))
    assert len(detail["mappings"]) == 2


def test_one_to_one_from_columns_require_identifier(harness, deps_ok):  # noqa: F811
    """one_to_one 起始侧关联列必须引用 from 对象类型标识属性（保证两侧唯一）。"""
    asyncio.run(
        ontology_service.upsert_object_type_async(
            "FromObj",
            {
                "display_name": "起始对象类型",
                "properties": [
                    {"property_name": "id", "value_type": "String", "is_identifier": True},
                    {"property_name": "name", "value_type": "String"},
                ],
                "mappings": [],
            },
        )
    )
    asyncio.run(ontology_service.upsert_object_type_async("ToObj", _object_payload(display_name="目标对象类型")))
    base = {"from_object_type_name": "FromObj", "to_object_type_name": "ToObj", "multiplicity": "one_to_one"}
    with pytest.raises(OntologyInvalidError, match="标识属性"):
        asyncio.run(
            ontology_service.upsert_relation_async(
                "one_one_bad", {**base, "from_columns": ["name"], "to_columns": ["order_id"]}
            )
        )
    saved = asyncio.run(
        ontology_service.upsert_relation_async(
            "one_one_ok", {**base, "from_columns": ["id"], "to_columns": ["order_id"]}
        )
    )
    assert saved["multiplicity"] == "one_to_one"
