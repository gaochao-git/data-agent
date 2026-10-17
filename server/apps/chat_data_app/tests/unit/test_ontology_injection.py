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


def _render(bundle: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    content = ontology_injection._render_catalog(
        bundle,
        kwargs.pop("config_names", {}),
        kwargs.pop("dimension_columns", None),
        kwargs.pop("domain_names", None),
        kwargs.pop("model_files", None),
        kwargs.pop("configs", None),
    )
    assert not kwargs, f"未消费的渲染参数: {sorted(kwargs)}"
    return yaml.safe_load(content)


def _patch_bundle(monkeypatch, bundle: dict[str, Any] | None = None) -> dict[str, Any]:
    resolved = bundle or _bundle()

    async def fake_bundle(config_ids):
        return resolved

    monkeypatch.setattr(ontology_injection.OntologyDao, "load_injection_bundle_async", staticmethod(fake_bundle))
    return resolved


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
    doc = _render(
        _bundle(),
        config_names={"sc_order": "订单模型", "sc_user": "用户模型"},
        dimension_columns=_DIMENSION_COLUMNS,
        model_files={"sc_order": "订单模型__1234.yaml", "sc_user": "用户模型__5678.yaml"},
    )
    # 未传 configs 时不渲染 models 段（生产链路必传 runtime configs）
    assert doc["models"] == []
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
    assert rel["from_columns_by_model"] == {"sc_order": ["USER_ID"]}
    assert rel["to_columns_by_model"] == {"sc_user": ["UID"]}
    # 单映射全部解析成功时不出现 unresolved 噪声
    assert "unresolved" not in rel
    # 关系条目补齐 description/status 字段
    assert "description" in rel and "status" in rel


def test_render_catalog_models_section_lists_all_enabled_models():
    """models 段是全部启用模型索引：停用/重复剔除，未建本体对象的模型也在其中。"""
    configs = [
        {
            "id": "sc_order",
            "name": "订单模型",
            "enabled": True,
            "description": "订单分析",
            "business_notes": "口径说明",
        },
        {"id": "sc_user", "name": "用户模型", "enabled": True, "semantic_config_desc": "用户分析"},
        {"id": "sc_orphan", "name": "未建对象模型", "enabled": True, "description": "没有本体对象"},
        {"id": "sc_off", "name": "停用模型", "enabled": False},
        {"id": "sc_order", "name": "重复模型", "enabled": True},
    ]
    doc = _render(
        _bundle(),
        dimension_columns=_DIMENSION_COLUMNS,
        model_files={"sc_order": "订单模型__order.yaml", "sc_user": "用户模型__user.yaml"},
        configs=configs,
    )
    models = doc["models"]
    assert [model["semantic_config_id"] for model in models] == ["sc_order", "sc_orphan", "sc_user"]
    assert models[0] == {
        "semantic_config_id": "sc_order",
        "name": "订单模型",
        "file": "订单模型__order.yaml",
        "description": "订单分析",
        "business_notes": "口径说明",
    }
    # 未建对象的启用模型也保留 file/description 索引，二选一不丢信息
    orphan = models[1]
    assert orphan["name"] == "未建对象模型" and orphan["file"] == ""
    assert orphan["description"] == "没有本体对象"
    # 停用模型不出现
    assert "sc_off" not in {model["semantic_config_id"] for model in models}
    # comment 说明 models/objects 的关系
    assert "models 是全部启用模型索引" in doc["comment"]
    assert "模型可能不在 objects 中" in doc["comment"]


def test_translate_keeps_unknown_dimension_name():
    """维度/映射任一级未知时保留原文，并标记未解析（等价映射天然兼容但不能当已验证）。"""
    columns, unresolved = ontology_injection._translate_to_physical_columns(
        ["user_id"], [{"semantic_config_id": "sc_user", "column_mappings": {"user_id": "用户编号"}}], _DIMENSION_COLUMNS
    )
    assert columns == ["UID"]
    assert unresolved is False
    fallback, fallback_unresolved = ontology_injection._translate_to_physical_columns(
        ["raw_col"], [{"semantic_config_id": "sc_user", "column_mappings": {}}], _DIMENSION_COLUMNS
    )
    assert fallback == ["raw_col"]
    assert fallback_unresolved is True


def _multi_mapping_bundle(mappings: list[dict[str, Any]]) -> dict[str, Any]:
    bundle = _bundle()
    bundle["mappings"] = mappings
    return bundle


def test_multi_mapping_translation_is_deterministic_and_never_mixes_models():
    """多模型绑定时用「同一条映射」的 config_id 翻译，且行序不影响输出。"""
    mappings = [
        # 第一条 column_mappings 为空：旧实现会跳过它取列，却仍用 mappings[0] 的 config_id 查维度
        {"object_id": 1, "semantic_config_id": "sc_a", "column_mappings": {}},
        {"object_id": 1, "semantic_config_id": "sc_b", "column_mappings": {"user_id": "用户编号"}},
        {"object_id": 2, "semantic_config_id": "sc_user", "column_mappings": {"user_id": "用户编号"}},
    ]
    dimension_columns = {
        "sc_a": {"用户编号": "USER_FROM_A"},
        "sc_b": {"用户编号": "UID"},
        "sc_user": {"用户编号": "UID"},
    }
    for ordered in (mappings, list(reversed(mappings))):
        doc = _render(_multi_mapping_bundle(ordered), dimension_columns=dimension_columns)
        rel = doc["relations"][0]
        # 旧实现会输出 sc_a 的 USER_FROM_A（串模型）；正确结果必须来自 sc_b
        assert rel["from_columns"] == ["UID"]
        assert rel["from_columns_by_model"] == {"sc_b": ["UID"]}
        assert rel["to_columns"] == ["UID"]
        assert rel["to_columns_by_model"] == {"sc_user": ["UID"]}
        # 空 column_mappings 的映射行不渲染（对象上只剩 sc_b）
        assert [mapping["semantic_config_id"] for mapping in doc["objects"][0]["mappings"]] == ["sc_b"]


def test_relation_marks_unresolved_when_column_falls_back():
    """属性未映射 / 维度只有 expression 时保留原文，并置 unresolved 供模型回读 YAML。"""
    bundle = _bundle()
    bundle["mappings"] = [
        # from 侧 sc_order 没有 user_id 映射 → 回退原始属性名
        {"object_id": 1, "semantic_config_id": "sc_order", "column_mappings": {"amount": "订单金额"}},
        # to 侧映射到「用户编号」，但该维度是 expression 无物理列 → 回退维度名
        {"object_id": 2, "semantic_config_id": "sc_user", "column_mappings": {"user_id": "用户编号"}},
    ]
    doc = _render(
        bundle,
        dimension_columns={"sc_order": {"订单金额": "AMOUNT"}, "sc_user": {}},
    )
    rel = doc["relations"][0]
    assert rel["from_columns"] == ["user_id"]
    assert rel["to_columns"] == ["用户编号"]
    assert rel["from_columns_by_model"] == {"sc_order": ["user_id"]}
    assert rel["to_columns_by_model"] == {"sc_user": ["用户编号"]}
    assert rel["unresolved"] is True


def test_object_mappings_filter_empty_and_object_survives():
    """空 column_mappings 的映射行从对象条目剔除，对象本身保留。"""
    bundle = _bundle()
    bundle["mappings"] = [
        {"object_id": 1, "semantic_config_id": "sc_empty", "column_mappings": {}},
        {"object_id": 1, "semantic_config_id": "sc_order", "column_mappings": {"order_id": "订单编号"}},
        {"object_id": 2, "semantic_config_id": "sc_user", "column_mappings": {}},
    ]
    doc = _render(bundle)
    assert [obj["object"] for obj in doc["objects"]] == ["order", "user"]
    assert [mapping["semantic_config_id"] for mapping in doc["objects"][0]["mappings"]] == ["sc_order"]
    # user 只有空映射：对象保留，mappings 为空列表而不是 {}
    assert doc["objects"][1]["mappings"] == []


def test_relations_sorted_by_name_and_carry_description_status():
    bundle = _bundle()
    bundle["relations"] = [
        dict(
            bundle["relations"][0],
            relation_name="order_belongs_to_user",
            description="订单归属用户",
            status="certified",
        ),
        dict(
            bundle["relations"][0],
            relation_name="aaa_user_has_orders",
            description="用户拥有订单",
            status="experimental",
        ),
    ]
    doc = _render(bundle, dimension_columns=_DIMENSION_COLUMNS)
    assert [rel["name"] for rel in doc["relations"]] == ["aaa_user_has_orders", "order_belongs_to_user"]
    assert doc["relations"][0]["description"] == "用户拥有订单"
    assert doc["relations"][0]["status"] == "experimental"
    assert doc["relations"][1]["description"] == "订单归属用户"
    assert doc["relations"][1]["status"] == "certified"


def test_properties_carry_enum_ref_and_description():
    bundle = _bundle()
    bundle["properties"][0].update({"value_type": "enum", "enum_ref": "order_status", "description": "订单状态"})
    doc = _render(bundle)
    props = {prop["name"]: prop for prop in doc["objects"][0]["properties"]}
    assert props["order_id"]["enum_ref"] == "order_status"
    assert props["order_id"]["description"] == "订单状态"
    # 非 enum 属性不带 enum_ref 噪声
    assert "enum_ref" not in props["amount"]


def _large_bundle(object_count: int = 20, property_count: int = 40) -> dict[str, Any]:
    objects = []
    properties = []
    for index in range(object_count):
        object_id = index + 1
        objects.append(
            {
                "id": object_id,
                "object_name": f"obj_{index:02d}",
                "display_name": f"对象{index:02d}",
                "status": "certified",
                "description": f"第 {index} 个对象",
            }
        )
        for prop_index in range(property_count):
            properties.append(
                {
                    "object_id": object_id,
                    "property_name": f"prop_{prop_index:02d}",
                    "display_name": f"属性{prop_index:02d}",
                    "value_type": "String",
                    "is_identifier": prop_index == 0,
                    "is_title": False,
                }
            )
    relations = [
        {
            "relation_name": "rel_keep",
            "from_object_id": 1,
            "to_object_id": 2,
            "from_columns": ["prop_00"],
            "to_columns": ["prop_00"],
            "multiplicity": "many_to_one",
        },
        {
            "relation_name": "rel_dropped",
            "from_object_id": 1,
            "to_object_id": object_count,
            "from_columns": ["prop_00"],
            "to_columns": ["prop_00"],
            "multiplicity": "many_to_one",
        },
    ]
    return {"objects": objects, "properties": properties, "relations": relations, "mappings": []}


def test_catalog_truncates_objects_by_character_budget():
    """超预算时按对象名顺序截断，只保留截断后对象间的关系，models 照常输出。"""
    bundle = _large_bundle()
    doc = _render(
        bundle,
        model_files={"sc_order": "订单模型__order.yaml"},
        configs=[{"id": "sc_order", "name": "订单模型", "enabled": True, "description": "订单分析"}],
    )
    assert doc["truncated"] is True
    assert doc["omitted_objects"] > 0
    assert len(doc["objects"]) + doc["omitted_objects"] == len(bundle["objects"])
    assert len(doc["objects"]) < len(bundle["objects"])
    kept_names = [obj["object"] for obj in doc["objects"]]
    assert kept_names == sorted(kept_names)
    # 截断后只保留两端都在目录里的关系（rel_dropped 的 to 对象已被截掉）
    assert [rel["name"] for rel in doc["relations"]] == ["rel_keep"]
    # models 段很小，不受截断影响
    assert [model["semantic_config_id"] for model in doc["models"]] == ["sc_order"]


def test_catalog_has_no_truncation_fields_within_budget():
    doc = _render(_bundle(), dimension_columns=_DIMENSION_COLUMNS)
    assert "truncated" not in doc
    assert "omitted_objects" not in doc


def test_materialize_writes_catalog_and_counts_objects(enabled, monkeypatch):
    runtime = _FakeRuntime()
    _patch_bundle(monkeypatch)
    count = asyncio.run(
        ontology_injection.materialize_ontology_catalog_async(
            runtime,
            ["sc_order", "sc_user"],
            configs=[
                {"id": "sc_order", "name": "订单模型", "enabled": True, "description": "订单分析"},
                {"id": "sc_user", "name": "用户模型", "enabled": True},
            ],
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
    # 顶层 models 覆盖全部启用模型（含对象覆盖不到的模型）
    assert [model["semantic_config_id"] for model in doc["models"]] == ["sc_order", "sc_user"]
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


def test_materialize_survives_legacy_cleanup_sandbox_denial(enabled, monkeypatch):
    """旧目录清理被沙箱拒绝只记一次 info，不影响目录写入与返回值。"""
    monkeypatch.setattr(ontology_injection, "_legacy_cleanup_denied_logged", False)

    class _DeniedRuntime(_FakeRuntime):
        async def delete_internal_text_files(self, path):
            raise ValueError(f"路径不在允许范围: {path}")

    runtime = _DeniedRuntime()
    _patch_bundle(monkeypatch)
    count = asyncio.run(ontology_injection.materialize_ontology_catalog_async(runtime, ["sc_order"], configs=[]))
    assert count == 2
    assert runtime.written
    assert ontology_injection._legacy_cleanup_denied_logged is True


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
