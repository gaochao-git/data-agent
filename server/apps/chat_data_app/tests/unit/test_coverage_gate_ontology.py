"""覆盖率补强：本体模块缺失分支回归。

本文件只针对覆盖率基线（/tmp/cov_base.gate）中本体相关文件缺失的行/分支补用例，
断言一律落在真实行为上（返回值、异常、落库状态、错误码），不改生产代码。

复用 `test_ontology_admin` 的 sqlite 事务作用域夹具（对象类型/属性/关系类型走真实 ORM），
校验器/注入的纯函数直接断言输入输出，路由用桩 service 断言响应与错误码映射。
"""

from __future__ import annotations

import asyncio
import importlib
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.chat_data_app.daos import dao_session
from apps.chat_data_app.daos.ontology_dao import OntologyDao, _parse_json
from apps.chat_data_app.daos.semantic_config_dao import SemanticConfigDao
from apps.chat_data_app.routers import ontology_admin as ontology_admin_router
from apps.chat_data_app.schema import (
    OntologyObjectTypeOntologyRequest,
    OntologyObjectTypeStatusRequest,
    OntologyObjectTypeUpsertRequest,
    OntologyPropertyItem,
    OntologyRelationTypeStatusRequest,
    OntologyRelationTypeUpsertRequest,
    OntologyUpsertRequest,
)
from apps.chat_data_app.services.ontology import (
    OntologyAlreadyExistsError,
    OntologyInvalidError,
    OntologyNotFoundError,
    OntologyService,
    OntologyStateInvalidError,
    ontology_injection,
    ontology_service,
)
from apps.chat_data_app.services.ontology import ontology_validator as validator
from apps.chat_data_app.tests.unit.test_ontology_admin import (
    DIMENSION_INDEX,
    SEMANTIC_MAPPING,
    VALID_PROPERTY,
    _object_payload,
    _StubSemanticConfigDao,
)
from apps.chat_data_app.tests.unit.test_ontology_admin import (
    deps_ok as _imported_deps_ok,
)
from apps.chat_data_app.tests.unit.test_ontology_admin import (
    harness as _imported_harness,
)
from utils.error_codes import ErrorCode
from utils.models import (
    Base,
    ChatDataOntology,
    ChatDataOntologyObjectType,
    ChatDataOntologyProperty,
    ChatDataOntologyRelationType,
    SemanticConfig,
)

ontology_service_module = importlib.import_module("apps.chat_data_app.services.ontology.ontology_service")

# 跨模块复用 test_ontology_admin 的夹具：pytest 按模块属性名收集 fixture，
# 这里用原始名注册（避免每个用例重复声明或复制夹具实现）。
harness = _imported_harness
deps_ok = _imported_deps_ok

# ---------------------------------------------------------------- 校验器 ----------------


@pytest.mark.parametrize(
    ("invoke", "message"),
    [
        (lambda: validator.validate_object_type_payload({"display_name": ""}), "对象类型展示名必填"),
        (lambda: validator.validate_object_type_payload({"display_name": "x" * 129}), "对象类型展示名必填"),
        (
            lambda: validator.validate_object_type_payload({"display_name": "x", "description": "d" * 1025}),
            "对象类型描述不超过",
        ),
        (lambda: validator.validate_object_type_payload({"display_name": "x", "ontology_id": "1"}), "本体ID必须为整数"),
        (lambda: validator.validate_ontology_payload({"display_name": "x" * 129}), "本体展示名不超过"),
        (
            lambda: validator.validate_ontology_payload({"display_name": "x", "description": "d" * 513}),
            "本体描述不超过",
        ),
        (lambda: validator.validate_property_items([]), "对象类型至少需要一个属性"),
        (lambda: validator.validate_property_items("bad"), "对象类型至少需要一个属性"),
        (lambda: validator.validate_property_items([{"property_name": "p1"}]), "标识属性"),
        (
            lambda: validator.validate_property_items(
                [
                    {**VALID_PROPERTY, "is_title": True},
                    {**VALID_PROPERTY, "property_name": "p2", "is_identifier": False, "is_title": True},
                ]
            ),
            "至多一个标题属性",
        ),
        (lambda: validator.validate_property_items(["bad"]), "属性条目格式非法"),
        (lambda: validator.validate_property_items([dict(VALID_PROPERTY), dict(VALID_PROPERTY)]), "属性API名重复"),
        (lambda: validator.validate_property_items([{**VALID_PROPERTY, "value_type": "Float"}]), "值类型"),
        (lambda: validator.validate_property_items([{**VALID_PROPERTY, "value_type": "enum"}]), "enum_ref"),
        (
            lambda: validator.validate_property_items([{**VALID_PROPERTY, "enum_ref": "order_status"}]),
            "不能携带 enum_ref",
        ),
    ],
)
def test_validator_object_and_property_red_lines(invoke, message):
    with pytest.raises(OntologyInvalidError, match=message):
        invoke()


def test_validator_property_render_hints_rules():
    with pytest.raises(OntologyInvalidError, match="render_hints 必须是对象"):
        validator.validate_property_items([{**VALID_PROPERTY, "render_hints": "bad"}])
    with pytest.raises(OntologyInvalidError, match="不支持字段"):
        validator.validate_property_items([{**VALID_PROPERTY, "render_hints": {"foo": True}}])
    with pytest.raises(OntologyInvalidError, match="必须是布尔值"):
        validator.validate_property_items([{**VALID_PROPERTY, "render_hints": {"searchable": "yes"}}])

    # 合法提示原样保留；空提示规范化为空字典
    items = validator.validate_property_items(
        [{**VALID_PROPERTY, "render_hints": {"searchable": True, "sortable": False}}]
    )
    assert items[0]["render_hints"] == {"searchable": True, "sortable": False}
    assert validator.validate_property_items([{**VALID_PROPERTY, "render_hints": {}}])[0]["render_hints"] == {}


def test_validator_mappings_red_lines():
    # 未配置映射合法（返回空列表）
    assert validator.validate_mappings(None, {"order_id"}, "Order") == []
    # 结构红线
    with pytest.raises(OntologyInvalidError, match="映射必须是数组"):
        validator.validate_mappings("bad", {"order_id"}, "Order")
    with pytest.raises(OntologyInvalidError, match="映射条目格式非法"):
        validator.validate_mappings(["bad"], {"order_id"}, "Order")
    with pytest.raises(OntologyInvalidError, match="必须提供 semantic_config_id"):
        validator.validate_mappings([{}], {"order_id"}, "Order")
    with pytest.raises(OntologyInvalidError, match="重复映射"):
        validator.validate_mappings(
            [
                {"semantic_config_id": "cfg-1", "column_mappings": {}},
                {"semantic_config_id": "cfg-1", "column_mappings": {}},
            ],
            {"order_id"},
            "Order",
        )
    with pytest.raises(OntologyInvalidError, match="column_mappings 必须是"):
        validator.validate_mappings([{"semantic_config_id": "cfg-1", "column_mappings": "bad"}], {"order_id"}, "Order")
    with pytest.raises(OntologyInvalidError, match="映射维度名不能为空"):
        validator.validate_mappings(
            [{"semantic_config_id": "cfg-1", "column_mappings": {"order_id": ""}}], {"order_id"}, "Order"
        )

    normalized = validator.validate_mappings(
        [{"semantic_config_id": "cfg-1", "column_mappings": {"order_id": "订单编号"}}], {"order_id"}, "Order"
    )
    assert normalized == [
        {"binding_type": "semantic", "semantic_config_id": "cfg-1", "column_mappings": {"order_id": "订单编号"}}
    ]


def test_validator_relation_red_lines_and_normalization():
    with pytest.raises(OntologyInvalidError, match="关系类型基数"):
        validator.validate_relation_payload(
            {"multiplicity": "many_to_many", "from_columns": ["a"], "to_columns": ["b"]}
        )
    with pytest.raises(OntologyInvalidError, match="起始侧关联列不能为空"):
        validator.validate_relation_payload({"from_columns": [], "to_columns": ["b"]})
    with pytest.raises(OntologyInvalidError, match="等长且顺序一一对应"):
        validator.validate_relation_payload({"from_columns": ["a", "b"], "to_columns": ["c"]})
    with pytest.raises(OntologyInvalidError, match="正向关系类型模式串不超过 512 字符"):
        validator.validate_relation_payload({"from_columns": ["a"], "to_columns": ["b"], "from_verbalize": "v" * 513})

    normalized = validator.validate_relation_payload(
        {"multiplicity": "one_to_one", "from_columns": ["a_id"], "to_columns": ["b_id"], "description": "关联"}
    )
    assert normalized["multiplicity"] == "one_to_one"
    assert normalized["from_columns"] == ["a_id"]
    assert normalized["to_columns"] == ["b_id"]
    assert normalized["description"] == "关联"


# ---------------------------------------------------------------- 路由 ----------------


class _RouterServiceStub:
    """路由层桩 service：记录调用并可按需注入异常。"""

    def __init__(self) -> None:
        self.error: Exception | None = None
        self.calls: list[tuple[str, tuple, dict]] = []

    def _record(self, name: str, args: tuple, kwargs: dict) -> None:
        self.calls.append((name, args, kwargs))
        if self.error is not None:
            raise self.error

    async def list_ontologies_async(self):
        self._record("list_ontologies_async", (), {})
        return [{"ontology_name": "ontology_a"}]

    async def upsert_ontology_async(self, ontology_name, payload, **kwargs):
        self._record("upsert_ontology_async", (ontology_name, payload), kwargs)
        return {"ontology_name": ontology_name, **payload}

    async def delete_ontology_async(self, ontology_name, **kwargs):
        self._record("delete_ontology_async", (ontology_name,), kwargs)

    async def list_object_types_async(self, keyword, status, ontology_id):
        self._record("list_object_types_async", (keyword, status, ontology_id), {})
        return [{"object_type_name": "Order"}]

    async def get_object_type_async(self, object_type_name):
        self._record("get_object_type_async", (object_type_name,), {})
        return None if object_type_name == "Ghost" else {"object_type_name": object_type_name}

    async def upsert_object_type_async(self, object_type_name, payload, **kwargs):
        self._record("upsert_object_type_async", (object_type_name, payload), kwargs)
        return {"object_type_name": object_type_name}

    async def delete_object_type_async(self, object_type_name, **kwargs):
        self._record("delete_object_type_async", (object_type_name,), kwargs)

    async def set_object_type_status_async(self, object_type_name, status, payload, **kwargs):
        self._record("set_object_type_status_async", (object_type_name, status, payload), kwargs)
        return {"object_type_name": object_type_name, "status": status}

    async def set_object_type_ontology_async(self, object_type_name, ontology_id, **kwargs):
        self._record("set_object_type_ontology_async", (object_type_name, ontology_id), kwargs)
        return {"object_type_name": object_type_name, "ontology_id": ontology_id}

    async def list_relations_async(self, keyword, from_object_type, to_object_type):
        self._record("list_relations_async", (keyword, from_object_type, to_object_type), {})
        return [{"relation_type_name": "r1"}]

    async def upsert_relation_async(self, relation_type_name, payload, **kwargs):
        self._record("upsert_relation_async", (relation_type_name, payload), kwargs)
        return {"relation_type_name": relation_type_name}

    async def delete_relation_async(self, relation_type_name, **kwargs):
        self._record("delete_relation_async", (relation_type_name,), kwargs)

    async def set_relation_status_async(self, relation_type_name, status, **kwargs):
        self._record("set_relation_status_async", (relation_type_name, status), kwargs)
        return {"relation_type_name": relation_type_name, "status": status}


@pytest.fixture()
def router_env(monkeypatch):
    stub = _RouterServiceStub()
    monkeypatch.setattr(ontology_admin_router, "ontology_service", stub)
    monkeypatch.setattr(
        ontology_admin_router, "_get_current_user", lambda request: SimpleNamespace(user_id=7, username="张三")
    )
    return stub


def _router_payloads() -> dict[str, object]:
    return {
        "ontology": OntologyUpsertRequest(display_name="电商交易"),
        "object_type": OntologyObjectTypeUpsertRequest(
            display_name="订单",
            properties=[OntologyPropertyItem(property_name="order_id", is_identifier=True)],
        ),
        "object_status": OntologyObjectTypeStatusRequest(status="deprecated", deprecation_reason="下线"),
        "object_ontology": OntologyObjectTypeOntologyRequest(ontology_id=3),
        "relation": OntologyRelationTypeUpsertRequest(
            from_object_type_name="Order", to_object_type_name="User", from_columns=["order_id"], to_columns=["user_id"]
        ),
        "relation_status": OntologyRelationTypeStatusRequest(status="experimental"),
    }


def _router_calls(payloads: dict[str, object]) -> list[tuple[str, object, tuple]]:
    request = object()
    return [
        ("list_ontologies", ontology_admin_router.list_ontologies, (request,)),
        ("upsert_ontology", ontology_admin_router.upsert_ontology, ("ecommerce", payloads["ontology"], request)),
        ("delete_ontology", ontology_admin_router.delete_ontology, ("ecommerce", request)),
        ("list_object_types", ontology_admin_router.list_ontology_object_types, (request, "订单", "draft", 3)),
        ("get_object", ontology_admin_router.get_ontology_object_type, ("Order", request)),
        (
            "upsert_object",
            ontology_admin_router.upsert_ontology_object_type,
            ("Order", payloads["object_type"], request),
        ),
        ("delete_object", ontology_admin_router.delete_ontology_object_type, ("Order", request)),
        (
            "object_status",
            ontology_admin_router.change_ontology_object_type_status,
            ("Order", payloads["object_status"], request),
        ),
        (
            "object_ontology",
            ontology_admin_router.change_ontology_object_type_ontology,
            ("Order", payloads["object_ontology"], request),
        ),
        ("list_relations", ontology_admin_router.list_ontology_relations, (request, "r", "Order", "User")),
        ("upsert_relation", ontology_admin_router.upsert_ontology_relation, ("r1", payloads["relation"], request)),
        ("delete_relation", ontology_admin_router.delete_ontology_relation, ("r1", request)),
        (
            "relation_status",
            ontology_admin_router.change_ontology_relation_status,
            ("r1", payloads["relation_status"], request),
        ),
    ]


def test_error_response_maps_each_ontology_exception():
    cases = [
        (
            ontology_admin_router.OntologyNameInvalidError("对象类型API名非法"),
            ErrorCode.CHATDATA_ONTOLOGY_NAME_INVALID,
        ),
        (OntologyInvalidError("本体非法"), ErrorCode.CHATDATA_ONTOLOGY_INVALID),
        (
            ontology_admin_router.OntologyAlreadyExistsError("对象类型已存在"),
            ErrorCode.CHATDATA_ONTOLOGY_ALREADY_EXISTS,
        ),
        (OntologyStateInvalidError("状态非法"), ErrorCode.CHATDATA_ONTOLOGY_STATE_INVALID),
        (OntologyNotFoundError("对象类型不存在"), ErrorCode.CHATDATA_ONTOLOGY_NOT_FOUND),
    ]
    for exc, expected_code in cases:
        body = json.loads(ontology_admin_router._error_response(exc, "兜底", ErrorCode.SUCCESS).body)
        assert body == {"status": "error", "msg": str(exc), "code": int(expected_code)}

    # 未知异常走兜底文案与兜底错误码（且不向前端泄漏内部异常文本）
    body = json.loads(
        ontology_admin_router._error_response(RuntimeError("boom"), "操作失败，请重试", ErrorCode.SUCCESS).body
    )
    assert body == {"status": "error", "msg": "操作失败，请重试", "code": int(ErrorCode.SUCCESS)}


def test_all_admin_routes_return_business_response(router_env):
    stub = router_env
    for name, route, args in _router_calls(_router_payloads()):
        body = json.loads(asyncio.run(route(*args)).body)
        assert body["status"] == "success", name
        assert body["code"] == int(ErrorCode.SUCCESS), name

    # 操作人四件套由路由透传给 service（以对象类型 upsert 为样本）
    upsert_call = next(call for call in stub.calls if call[0] == "upsert_object_type_async")
    assert upsert_call[2] == {"operator": "7", "operator_name": "张三", "allow_overwrite": False}
    ontology_call = next(call for call in stub.calls if call[0] == "upsert_ontology_async")
    assert ontology_call[2]["operator"] == "7"
    assert ontology_call[2]["operator_name"] == "张三"


def test_get_object_route_reports_not_found_for_missing_object(router_env):
    response = asyncio.run(ontology_admin_router.get_ontology_object_type("Ghost", object()))
    body = json.loads(response.body)
    assert body["status"] == "error"
    assert body["code"] == int(ErrorCode.CHATDATA_ONTOLOGY_NOT_FOUND)
    assert "Ghost" in body["msg"]


def test_admin_routes_map_unexpected_exception_to_operation_failed(router_env):
    router_env.error = RuntimeError("boom")
    for name, route, args in _router_calls(_router_payloads()):
        body = json.loads(asyncio.run(route(*args)).body)
        assert body["status"] == "error", name
        assert body["code"] == int(ErrorCode.CHATDATA_ONTOLOGY_OPERATION_FAILED), name
        assert "boom" not in body["msg"], name


def test_admin_routes_re_raise_http_exception(router_env):
    router_env.error = HTTPException(status_code=403, detail="forbidden")
    for name, route, args in _router_calls(_router_payloads()):
        with pytest.raises(HTTPException) as excinfo:
            asyncio.run(route(*args))
        assert excinfo.value.status_code == 403, name


# ------------------------------------------------------- 语义配置 DAO ----------------


@pytest.fixture()
def semantic_config_db(monkeypatch):
    """sqlite 内存库承载 semantic_config 表（复制最小夹具，不改原测试文件）。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    async def create_tables():
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: Base.metadata.create_all(sync_conn, tables=[SemanticConfig.__table__])
            )

    asyncio.run(create_tables())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def fake_get_session():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr(dao_session, "get_session", fake_get_session)
    yield session_factory
    asyncio.run(engine.dispose())


def _semantic_config_row(
    config_id: str,
    *,
    group_id: str = "grp-1",
    dimensions_json: str = "[]",
    delete_flag: int = 0,
) -> SemanticConfig:
    return SemanticConfig(
        semantic_config_id=config_id,
        group_id=group_id,
        semantic_config_name=f"模型-{config_id}",
        enabled_flag=1,
        scope_type="public",
        delete_flag=delete_flag,
        metrics_json="[]",
        dimensions_json=dimensions_json,
        relationships_json="[]",
    )


def test_semantic_config_dao_ontology_lookup_branches(semantic_config_db, monkeypatch):
    dao = SemanticConfigDao()

    async def seed():
        async with semantic_config_db() as session:
            session.add_all(
                [
                    _semantic_config_row(
                        "cfg-1",
                        dimensions_json=json.dumps(
                            [{"name": "订单编号", "table": "orders"}, {"name": "金额", "table": "orders"}, "bad"]
                        ),
                    ),
                    _semantic_config_row("cfg-2", dimensions_json=json.dumps({"not": "a-list"})),
                    _semantic_config_row("cfg-deleted", delete_flag=1),
                ]
            )
            await session.commit()

    asyncio.run(seed())

    async def run():
        # 存在性过滤：忽略空串/软删/不存在
        assert await dao.filter_existing_config_ids_async(["cfg-1", "cfg-2", "cfg-deleted", "missing", ""]) == {
            "cfg-1",
            "cfg-2",
        }
        assert await dao.filter_existing_config_ids_async(["", "  "]) == set()

        # 所属组
        assert await dao.get_config_group_ids_async(["cfg-1", "missing", ""]) == {"cfg-1": "grp-1"}
        assert await dao.get_config_group_ids_async([]) == {}

        # 维度索引：非 list 的 dimensions_json 跳过，缺陷条目过滤
        index = await dao.get_config_dimensions_async(["cfg-1", "cfg-2"])
        assert index["cfg-1"] == {"订单编号": "orders", "金额": "orders"}
        assert "cfg-2" not in index
        assert await dao.get_config_dimensions_async([""]) == {}
        assert await dao.get_config_dimensions_async(["missing"]) == {}

        # upsert 前置守卫：非法入参不落库
        assert await dao.upsert_async("", {"id": "cfg-1"}) is False
        assert await dao.upsert_async("grp-1", {}) is False
        assert await dao.upsert_many_async("", [{"id": "cfg-1"}]) is False
        assert await dao.upsert_many_async("grp-1", []) is False
        assert await dao.upsert_many_async("grp-1", [{}]) is False

        # 空分组批量软删直接返回
        await dao.soft_delete_by_group_id_async("")

    asyncio.run(run())

    # 合法 upsert 委托给批量保存（MySQL 方言语句不在 sqlite 执行，这里只验证委托契约）
    delegated: dict[str, object] = {}

    async def fake_upsert_many(group_id, configs):
        delegated.update({"group_id": group_id, "configs": configs})
        return True

    monkeypatch.setattr(dao, "upsert_many_async", fake_upsert_many)

    async def run_delegation():
        config = {"id": "cfg-1", "name": "订单模型"}
        assert await dao.upsert_async("grp-1", config) is True
        assert delegated == {"group_id": "grp-1", "configs": [config]}

    asyncio.run(run_delegation())


# ---------------------------------------------------------------- 注入 ----------------


def test_injection_dimension_column_index_skips_incomplete_entries():
    assert ontology_injection._dimension_column_map(
        [
            {"name": "订单编号", "column": "ORDER_ID"},
            None,
            {"name": "", "column": "IGNORED"},
            {"name": "金额", "column": ""},
        ]
    ) == {"订单编号": "ORDER_ID"}
    assert ontology_injection._config_dimension_columns(
        [
            {"name": "无ID模型", "dimensions": [{"name": "d", "column": "c"}]},
            {"semantic_config_id": "cfg-1", "dimensions": [{"name": "订单编号", "column": "ORDER_ID"}]},
        ]
    ) == {"cfg-1": {"订单编号": "ORDER_ID"}}


def test_injection_config_display_names_skips_entries_without_id():
    assert ontology_injection._config_display_names(
        [{"name": "无ID模型"}, {"id": "cfg-1", "name": "订单模型"}, {"semantic_config_id": "cfg-2"}]
    ) == {"cfg-1": "订单模型", "cfg-2": ""}


def test_injection_mapping_and_translation_guards():
    # column_mappings 非字典按空映射处理
    assert ontology_injection._mapping_column_map({"column_mappings": ["bad"]}) == {}
    # 空 semantic_config_id 的映射不进入按模型翻译结果
    mappings = [{"semantic_config_id": "", "column_mappings": {"order_id": "订单编号"}}]
    assert ontology_injection._translate_columns_by_model(["order_id"], mappings, None) == {}


def test_injection_materialize_returns_zero_when_bundle_has_no_objects(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_INJECTION_ENABLED", "1")

    async def empty_bundle(config_ids):
        return {"object_types": [], "properties": [], "relation_types": [], "mappings": []}

    monkeypatch.setattr(ontology_injection.OntologyDao, "load_injection_bundle_async", staticmethod(empty_bundle))
    runtime = SimpleNamespace(write_internal_text_files=_fail_writer, delete_internal_text_files=_noop_delete)
    assert asyncio.run(ontology_injection.materialize_ontology_catalog_async(runtime, ["cfg-x"])) == 0


def test_injection_legacy_cleanup_handles_missing_denied_and_unexpected(monkeypatch):
    monkeypatch.setattr(ontology_injection, "_legacy_cleanup_denied_logged", True)

    class _MissingRuntime:
        async def delete_internal_text_files(self, path):
            raise FileNotFoundError(path)

    class _DeniedRuntime:
        async def delete_internal_text_files(self, path):
            raise ValueError("路径不在允许范围")

    class _BrokenRuntime:
        async def delete_internal_text_files(self, path):
            raise RuntimeError("清理失败")

    # 旧目录本就不存在：静默返回；被沙箱拒绝且已记过日志：不再告警；未知异常：只告警
    asyncio.run(ontology_injection._cleanup_legacy_ontology_dir(_MissingRuntime()))
    asyncio.run(ontology_injection._cleanup_legacy_ontology_dir(_DeniedRuntime()))
    asyncio.run(ontology_injection._cleanup_legacy_ontology_dir(_BrokenRuntime()))


async def _fail_writer(entries):
    raise AssertionError("无对象类型时不应写盘")


async def _noop_delete(path):
    return None


# ------------------------------------------------------------ 本体 DAO ----------------


def test_ontology_dao_empty_input_guards(harness):
    """空名称/空 ID 集合的早退分支：不触库、返回稳定空值。"""
    dao = OntologyDao()
    # 已是目标类型直接返回；非法 JSON 文本按空值处理（不抛异常）
    assert _parse_json([1, 2], list) == [1, 2]
    assert _parse_json("not-json{", list) == []
    assert _parse_json("not-json{", dict) == {}

    async def run():
        assert await dao.get_ontology_row_async("  ") is None
        assert await dao.get_object_type_row_async("") is None
        assert await dao.get_relation_row_async("") is None
        assert await dao.count_objects_by_ontology_ids_async([]) == {}
        empty_bundle = {"object_types": [], "properties": [], "relation_types": [], "mappings": []}
        assert await dao.load_injection_bundle_async([]) == empty_bundle
        assert await dao.load_injection_bundle_async(["missing"]) == empty_bundle
        await dao.soft_delete_object_type_async("")
        await dao.soft_delete_relation_async("")
        assert await dao.soft_delete_ontology_async("ghost") is False

    asyncio.run(run())


def test_ontology_dao_list_object_types_keyword_status_ontology_filters(harness):
    dao = OntologyDao()
    session_factory = harness["session_factory"]

    async def seed():
        async with session_factory() as session:
            session.add_all(
                [
                    ChatDataOntologyObjectType(
                        object_type_name="Order",
                        display_name="订单",
                        description_text="销售订单",
                        lifecycle_status="certified",
                        ontology_id=3,
                    ),
                    ChatDataOntologyObjectType(
                        object_type_name="User",
                        display_name="用户",
                        description_text="下单用户",
                        lifecycle_status="draft",
                        ontology_id=4,
                    ),
                ]
            )
            await session.commit()

    asyncio.run(seed())

    async def run():
        all_objects = await dao.list_object_types_async()
        assert [item["object_type_name"] for item in all_objects] == ["Order", "User"]
        # keyword/status/ontology_id 三过滤条件同时命中
        hit = await dao.list_object_types_async(keyword="销售", status="certified", ontology_id=3)
        assert [item["object_type_name"] for item in hit] == ["Order"]
        assert await dao.list_object_types_async(keyword="不存在的对象类型") == []

    asyncio.run(run())


def test_ontology_dao_upsert_object_paths(harness):
    """对象类型窄写 upsert：插入 / 活跃未授权返回 None / 软删行复活。"""
    dao = OntologyDao()

    async def run():
        inserted = await dao.upsert_object_type_async(
            "Order", {"display_name": "订单", "status": "experimental"}, allow_overwrite=False
        )
        assert inserted == {"object_type_name": "Order", "display_name": "订单", "status": "experimental"}
        assert await dao.upsert_object_type_async("Order", {"display_name": "x"}, allow_overwrite=False) is None

        await dao.soft_delete_object_type_async("Order")
        assert await dao.get_object_type_row_async("Order") is None
        revived = await dao.upsert_object_type_async("Order", {"display_name": "复活"}, allow_overwrite=False)
        assert revived["display_name"] == "复活"
        row = await dao.get_object_type_row_async("Order")
        assert row["delete_flag"] == 0
        assert row["status"] == "experimental"

    asyncio.run(run())


def test_ontology_dao_upsert_ontology_paths(harness):
    dao = OntologyDao()

    async def run():
        created = await dao.upsert_ontology_async(
            "ontology_a", {"display_name": "交易域", "description": ""}, allow_overwrite=False
        )
        assert created == {"ontology_name": "ontology_a", "display_name": "交易域", "description": ""}
        # 活跃行未授权覆盖：返回 None 且不改库
        assert await dao.upsert_ontology_async("ontology_a", {"display_name": "改"}, allow_overwrite=False) is None
        assert (await dao.get_ontology_row_async("ontology_a"))["display_name"] == "交易域"
        # 允许覆盖后改展示名
        await dao.upsert_ontology_async("ontology_a", {"display_name": "跨境电商"}, allow_overwrite=True)
        assert (await dao.get_ontology_row_async("ontology_a"))["display_name"] == "跨境电商"
        # 软删行同名重建走复活（不要求 allow_overwrite）
        assert await dao.soft_delete_ontology_async("ontology_a") is True
        assert await dao.get_ontology_row_async("ontology_a") is None
        await dao.upsert_ontology_async("ontology_a", {"display_name": "复活的交易"}, allow_overwrite=False)
        assert (await dao.get_ontology_row_async("ontology_a"))["display_name"] == "复活的交易"

    asyncio.run(run())


def test_ontology_dao_upsert_relation_paths(harness):
    dao = OntologyDao()
    payload = {
        "from_object_type_id": 1,
        "to_object_type_id": 2,
        "from_columns_json": '["order_id"]',
        "to_columns_json": '["user_id"]',
        "multiplicity": "many_to_one",
        "status": "draft",
        "description": "关联",
    }

    async def run():
        inserted = await dao.upsert_relation_async("r1", payload, allow_overwrite=False)
        assert inserted["relation_type_name"] == "r1"
        assert inserted["from_columns"] == ["order_id"]
        assert await dao.upsert_relation_async("r1", payload, allow_overwrite=False) is None
        # 软删行复活
        await dao.soft_delete_relation_async("r1")
        assert await dao.get_relation_row_async("r1") is None
        revived = await dao.upsert_relation_async("r1", {**payload, "status": "experimental"}, allow_overwrite=False)
        assert revived["status"] == "experimental"
        assert (await dao.get_relation_row_async("r1"))["delete_flag"] == 0

    asyncio.run(run())


def test_ontology_dao_list_relations_filters(harness):
    dao = OntologyDao()
    session_factory = harness["session_factory"]

    async def seed():
        async with session_factory() as session:
            session.add_all(
                [
                    ChatDataOntologyRelationType(
                        relation_type_name="order_user",
                        from_object_type_id=1,
                        to_object_type_id=2,
                        from_columns_json='["order_id"]',
                        to_columns_json='["user_id"]',
                        description_text="订单用户",
                    ),
                    ChatDataOntologyRelationType(
                        relation_type_name="user_dept",
                        from_object_type_id=2,
                        to_object_type_id=3,
                        from_columns_json='["user_id"]',
                        to_columns_json='["dept_id"]',
                    ),
                ]
            )
            await session.commit()

    asyncio.run(seed())

    async def run():
        assert [rel["relation_type_name"] for rel in await dao.list_relations_async()] == ["order_user", "user_dept"]
        # keyword + from/to 过滤同时生效
        hit = await dao.list_relations_async(keyword="订单用户", from_object_type_id=1, to_object_type_id=2)
        assert [rel["relation_type_name"] for rel in hit] == ["order_user"]

    asyncio.run(run())


def test_service_not_found_and_object_status_guards(harness, deps_ok):
    async def run():
        with pytest.raises(OntologyNotFoundError, match="对象类型不存在"):
            await ontology_service.delete_object_type_async("Ghost")
        with pytest.raises(OntologyNotFoundError, match="对象类型不存在"):
            await ontology_service.set_object_type_status_async("Ghost", "experimental", {})
        with pytest.raises(OntologyNotFoundError, match="关系类型不存在"):
            await ontology_service.delete_relation_async("ghost_rel")
        with pytest.raises(OntologyNotFoundError, match="关系类型不存在"):
            await ontology_service.set_relation_status_async("ghost_rel", "experimental")
        with pytest.raises(OntologyNotFoundError, match="关系类型连接对象类型不存在"):
            await ontology_service.upsert_relation_async("r1", _relation_payload(from_object_type_name="Ghost"))

        await ontology_service.upsert_object_type_async("Order", _object_payload())
        with pytest.raises(OntologyStateInvalidError, match="目标状态必须是"):
            await ontology_service.set_object_type_status_async("Order", "archived", {})
        # 目标状态与当前一致：幂等返回当前行
        same = await ontology_service.set_object_type_status_async("Order", "draft", {})
        assert same["status"] == "draft"
        with pytest.raises(OntologyStateInvalidError, match="废弃对象类型必须填写废弃原因"):
            await ontology_service.set_object_type_status_async("Order", "deprecated", {})

    asyncio.run(run())


def test_service_list_relations_filters_by_endpoint_names(harness, deps_ok):
    async def run():
        await _create_order_and_user()
        await ontology_service.upsert_relation_async("r1", _relation_payload())
        assert [item["relation_type_name"] for item in await ontology_service.list_relations_async()] == ["r1"]
        assert [
            item["relation_type_name"]
            for item in await ontology_service.list_relations_async(to_object_type_name="User")
        ] == ["r1"]
        assert await ontology_service.list_relations_async(from_object_type_name="User") == []

    asyncio.run(run())


def test_service_identifier_change_requires_destructive_confirm(harness, deps_ok):
    new_properties = [{**VALID_PROPERTY, "property_name": "order_no"}]

    async def run():
        await ontology_service.upsert_object_type_async("Order", _object_payload())
        with pytest.raises(OntologyStateInvalidError, match="confirm_destructive"):
            await ontology_service.upsert_object_type_async(
                "Order", _object_payload(properties=new_properties), allow_overwrite=True
            )
        saved = await ontology_service.upsert_object_type_async(
            "Order",
            _object_payload(properties=new_properties, confirm_destructive=True),
            allow_overwrite=True,
        )
        assert [item["property_name"] for item in saved["properties"]] == ["order_no"]

    asyncio.run(run())


def test_service_object_edit_keeps_relation_columns_valid(harness, deps_ok):
    """主键组合扩展但关系类型列仍是标识属性：不触发悬空拒绝（正向放行分支）。"""

    async def run():
        await _create_order_and_user()
        await ontology_service.upsert_relation_async("r1", _relation_payload())
        updated = await ontology_service.upsert_object_type_async(
            "User",
            _object_payload(
                properties=[
                    {**VALID_PROPERTY, "property_name": "user_id"},
                    {**VALID_PROPERTY, "property_name": "user_id2"},
                ],
                confirm_destructive=True,
            ),
            allow_overwrite=True,
        )
        assert [item["property_name"] for item in updated["properties"]] == ["user_id", "user_id2"]

    asyncio.run(run())


def test_service_object_edit_validates_from_side_relation_columns(harness, deps_ok):
    """编辑「from 端」对象类型：关联列未被移除且主键未变，放行（from 侧问题为空分支）。"""

    async def run():
        await _create_order_and_user()
        await ontology_service.upsert_relation_async("r1", _relation_payload())
        updated = await ontology_service.upsert_object_type_async(
            "Order",
            _object_payload(
                properties=[
                    {**VALID_PROPERTY, "property_name": "order_id"},
                    {**VALID_PROPERTY, "property_name": "order_no", "is_identifier": False},
                ]
            ),
            allow_overwrite=True,
        )
        assert [item["property_name"] for item in updated["properties"]] == ["order_id", "order_no"]

    asyncio.run(run())


def test_service_deprecation_successor_validation(harness, deps_ok):
    async def run():
        await _create_order_and_user()
        with pytest.raises(OntologyStateInvalidError, match="替代对象类型不能是对象类型自身"):
            await ontology_service.set_object_type_status_async(
                "Order", "deprecated", {"deprecation_reason": "下线", "successor_object_type_name": "Order"}
            )
        with pytest.raises(OntologyNotFoundError, match="替代对象类型不存在"):
            await ontology_service.set_object_type_status_async(
                "Order", "deprecated", {"deprecation_reason": "下线", "successor_object_type_name": "Ghost"}
            )
        deprecated = await ontology_service.set_object_type_status_async(
            "Order",
            "deprecated",
            {
                "deprecation_reason": "下线",
                "successor_object_type_name": "User",
                "deprecated_until": "2026-12-31",
            },
        )
        assert deprecated["status"] == "deprecated"
        assert deprecated["successor_object_type_name"] == "User"
        assert deprecated["deprecated_until"] == "2026-12-31 00:00:00"

    asyncio.run(run())


def test_service_set_object_ontology_short_circuits_unchanged(harness, deps_ok):
    async def run():
        await ontology_service.upsert_object_type_async("Order", _object_payload())
        same = await ontology_service.set_object_type_ontology_async("Order", None)
        assert same["object_type_name"] == "Order"
        assert same["ontology_id"] is None

    asyncio.run(run())


def test_service_cascade_skips_when_object_type_row_missing(harness, deps_ok, monkeypatch):
    """级联时对象类型行读不到：直接跳过，状态更新本身已提交。"""
    asyncio.run(ontology_service.upsert_object_type_async("Order", _object_payload()))
    original = ontology_service._dao.get_object_type_row_async
    reads = {"count": 0}

    async def flaky(object_type_name, include_deleted=False):
        if object_type_name == "Order":
            reads["count"] += 1
            if reads["count"] >= 2:
                return None
        return await original(object_type_name, include_deleted=include_deleted)

    monkeypatch.setattr(ontology_service._dao, "get_object_type_row_async", flaky)
    updated = asyncio.run(ontology_service.set_object_type_status_async("Order", "experimental", {}))
    # 回读也拿不到时退回变更前快照（真实行为：refreshed or existing）
    assert updated["status"] == "draft"


def test_service_cascade_skips_relations_with_missing_endpoints(harness, deps_ok, monkeypatch):
    """级联时端点对象类型状态查不到：跳过该关系类型（不误降级）。"""

    async def run():
        await _create_order_and_user()
        await ontology_service.upsert_relation_async("r1", _relation_payload())

        async def empty_objects(object_type_ids):
            return {}

        monkeypatch.setattr(ontology_service._dao, "get_objects_by_ids_async", empty_objects)
        updated = await ontology_service.set_object_type_status_async("Order", "experimental", {})
        assert updated["status"] == "experimental"
        relation = await ontology_service._dao.get_relation_row_async("r1")
        assert relation["status"] == "draft"

    asyncio.run(run())


def test_service_relation_edit_without_structural_change_skips_confirm(harness, deps_ok):
    async def run():
        await _create_order_and_user()
        await ontology_service.upsert_relation_async("r1", _relation_payload())
        updated = await ontology_service.upsert_relation_async(
            "r1", _relation_payload(description="补充描述"), allow_overwrite=True
        )
        assert updated["description"] == "补充描述"

    asyncio.run(run())


def test_service_relation_rejects_deprecated_endpoint(harness, deps_ok):
    async def run():
        await _create_order_and_user()
        await ontology_service.set_object_type_status_async("Order", "deprecated", {"deprecation_reason": "下线"})
        with pytest.raises(OntologyStateInvalidError, match="废弃对象类型禁止新建或变更关系类型"):
            await ontology_service.upsert_relation_async("r1", _relation_payload())

    asyncio.run(run())


def test_service_relation_status_guards_for_inconsistent_endpoints(harness, deps_ok):
    """历史脏数据（对象类型状态/软删绕过级联）下关系类型状态手动迁移的拒绝分支。"""
    session_factory = harness["session_factory"]

    async def set_object_type_row(object_type_name: str, **values) -> None:
        # 测试入参沿用对外键名（status），DB 列按红线命名（lifecycle_status）
        row_values = {"lifecycle_status": values.pop("status")} if "status" in values else {}
        row_values.update(values)
        async with session_factory() as session:
            await session.execute(
                update(ChatDataOntologyObjectType)
                .where(ChatDataOntologyObjectType.object_type_name == object_type_name)
                .values(**row_values)
            )
            await session.commit()

    async def run():
        await _create_order_and_user()
        await ontology_service.upsert_relation_async("r1", _relation_payload())

        await set_object_type_row("Order", status="deprecated")
        with pytest.raises(OntologyStateInvalidError, match="连接对象类型已废弃"):
            await ontology_service.set_relation_status_async("r1", "experimental")

        await set_object_type_row("Order", status="draft")
        await set_object_type_row("User", delete_flag=1)
        with pytest.raises(OntologyStateInvalidError, match="关系类型连接的对象类型不存在或已被删除"):
            await ontology_service.set_relation_status_async("r1", "experimental")

    asyncio.run(run())


def test_service_ontology_accepts_cross_group_mapping(harness, deps_ok, monkeypatch):
    """本体不绑数据源组：映射模型属于任意组均可挂本体（2026-10-20 解耦组锚点）。"""
    monkeypatch.setattr(
        ontology_service_module,
        "semantic_config_dao",
        _StubSemanticConfigDao(dimension_index=DIMENSION_INDEX, group_ids={"cfg-1": "grp-whatever"}),
    )

    async def run():
        ontology = await ontology_service.upsert_ontology_async("ontology_a", {"display_name": "交易域"})
        saved = await ontology_service.upsert_object_type_async(
            "Order", _object_payload(ontology_id=int(ontology["id"]), mappings=[dict(SEMANTIC_MAPPING)])
        )
        assert saved["ontology_id"] == int(ontology["id"])
        # 拖拽换本体同样不校验映射组
        ontology_b = await ontology_service.upsert_ontology_async("ontology_b", {"display_name": "供应链"})
        moved = await ontology_service.set_object_type_ontology_async("Order", int(ontology_b["id"]))
        assert moved["ontology_id"] == int(ontology_b["id"])

    asyncio.run(run())


def test_service_enum_ref_resolves_and_is_persisted(harness, deps_ok):
    saved = asyncio.run(
        ontology_service.upsert_object_type_async(
            "Order", _object_payload(properties=[{**VALID_PROPERTY, "value_type": "enum", "enum_ref": "order_status"}])
        )
    )
    assert saved["properties"][0]["value_type"] == "enum"
    assert saved["properties"][0]["enum_ref"] == "order_status"


# ---------- 理论不可达分支（并发/唯一键兜底）用桩 DAO 固定触发 ----------


class _MissingObjectSaveDao:
    async def get_object_type_row_async(self, object_type_name, include_deleted=False):
        return None

    async def find_mapping_owners_async(self, mappings, exclude_object_type_id=None):
        return {}

    async def upsert_object_type_full_async(self, *args, **kwargs):
        return None


class _MissingDomainSaveDao:
    async def get_ontology_row_async(self, ontology_name, include_deleted=False):
        return None

    async def upsert_ontology_async(self, *args, **kwargs):
        return None


class _DomainSavedButDetailMissingDao:
    async def get_ontology_row_async(self, ontology_name, include_deleted=False):
        return None

    async def upsert_ontology_async(self, ontology_name, payload, **kwargs):
        return {"ontology_name": ontology_name, **payload}


class _RelationSaveDaoStub:
    def __init__(self, save_result):
        self._save_result = save_result

    async def get_object_type_row_async(self, object_type_name, include_deleted=False):
        if object_type_name == "Order":
            return {"id": 1, "object_type_name": "Order", "status": "draft", "delete_flag": 0}
        if object_type_name == "User":
            return {"id": 2, "object_type_name": "User", "status": "draft", "delete_flag": 0}
        return None

    async def list_properties_async(self, object_type_id):
        property_name = "order_id" if int(object_type_id) == 1 else "user_id"
        return [{"property_name": property_name, "is_identifier": True}]

    async def get_relation_row_async(self, relation_type_name, include_deleted=False):
        return None

    async def upsert_relation_async(self, *args, **kwargs):
        return self._save_result


def test_service_object_save_missing_returns_already_exists(harness, deps_ok):
    service = OntologyService(dao=_MissingObjectSaveDao())
    with pytest.raises(OntologyAlreadyExistsError, match="对象类型已存在"):
        asyncio.run(service.upsert_object_type_async("Order", _object_payload()))


def _relation_payload(**overrides):
    payload = {
        "from_object_type_name": "Order",
        "to_object_type_name": "User",
        "from_columns": ["order_id"],
        "to_columns": ["user_id"],
    }
    payload.update(overrides)
    return payload


async def _create_order_and_user() -> None:
    await ontology_service.upsert_object_type_async("Order", _object_payload())
    await ontology_service.upsert_object_type_async(
        "User", _object_payload(properties=[{**VALID_PROPERTY, "property_name": "user_id"}])
    )


def test_service_object_save_readback_missing_returns_saved_view(harness, deps_ok, monkeypatch):
    service = OntologyService()

    async def missing_detail(object_type_name):
        return None

    monkeypatch.setattr(service, "get_object_type_async", missing_detail)
    saved = asyncio.run(service.upsert_object_type_async("Order", _object_payload()))
    assert saved["object_type_name"] == "Order"
    assert "mappings" not in saved


def test_service_ontology_save_missing_returns_already_exists(harness, deps_ok):
    service = OntologyService(dao=_MissingDomainSaveDao())
    with pytest.raises(OntologyAlreadyExistsError, match="本体已存在"):
        asyncio.run(service.upsert_ontology_async("ontology_a", {"display_name": "交易域"}))


def test_service_ontology_detail_missing_returns_state_invalid(harness, deps_ok):
    service = OntologyService(dao=_DomainSavedButDetailMissingDao())
    with pytest.raises(OntologyStateInvalidError, match="本体保存后读取失败"):
        asyncio.run(service.upsert_ontology_async("ontology_a", {"display_name": "交易域"}))


def test_service_relation_save_missing_returns_already_exists(harness, deps_ok):
    service = OntologyService(dao=_RelationSaveDaoStub(None))
    with pytest.raises(OntologyAlreadyExistsError, match="关系类型已存在"):
        asyncio.run(service.upsert_relation_async("r1", _relation_payload()))


def test_service_relation_readback_missing_returns_state_invalid(harness, deps_ok):
    service = OntologyService(dao=_RelationSaveDaoStub({}))
    with pytest.raises(OntologyStateInvalidError, match="关系类型保存后读取失败"):
        asyncio.run(service.upsert_relation_async("r1", _relation_payload()))
