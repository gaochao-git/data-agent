"""chat_data_ossie_importer 分支覆盖补测。

针对 2026-09-18 全量覆盖率审计出的 23 条未覆盖分支弧：
归一化容错（非 list/非 dict）、扩展解析容错（vendor 不符/坏 JSON/非对象）、
模型/字段/指标/关系的各校验出口，与派生/复合指标的 period/filters/description 可选分支。
零 DB、零 mock，全部通过 import_document / 模块级纯函数驱动。
"""

import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")

from apps.chat_data_app.services.semantic.run.chat_data_ossie_importer import (
    OssieSemanticModelImport,
    OssieToChatDataImporter,
    _ansi_sql_expression,
    _extension_data,
    _normalize_ossie_datatypes,
)


def _ext(payload) -> SimpleNamespace:
    """构造 CHAT_DATA 扩展对象（vendor_name + JSON 字符串 data）。"""
    return SimpleNamespace(vendor_name="CHAT_DATA", data=json.dumps(payload))


def _ansi(expression: str) -> SimpleNamespace:
    """构造仅含 ANSI_SQL 方言的表达式对象。"""
    return SimpleNamespace(dialects=[SimpleNamespace(dialect=SimpleNamespace(value="ANSI_SQL"), expression=expression)])


def _field(name="f1", expression="`col`", extensions=None, datatype=None, description=None, dimension=None) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        custom_extensions=extensions if extensions is not None else [_ext({"table": "orders", "column": "col"})],
        expression=_ansi(expression),
        datatype=datatype,
        description=description,
        dimension=dimension,
    )


def _dataset(name="orders", fields=None) -> SimpleNamespace:
    return SimpleNamespace(name=name, fields=fields or [])


def _model(name="模型", datasets=None, metrics=None, relationships=None, extensions=None) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        description=None,
        ai_context=None,
        custom_extensions=extensions if extensions is not None else [],
        datasets=datasets or [],
        metrics=metrics or [],
        relationships=relationships or [],
    )


def _import(model) -> OssieSemanticModelImport:
    results = OssieToChatDataImporter().import_document(SimpleNamespace(semantic_model=[model]))
    assert len(results) == 1
    return results[0]


# ---------------------------------------------------------------- _normalize_ossie_datatypes

def test_normalize_datatypes_returns_early_when_models_not_list():
    data = {"semantic_model": "not-a-list"}
    _normalize_ossie_datatypes(data)  # 不抛错即走 156->157 早返回
    assert data["semantic_model"] == "not-a-list"


def test_normalize_datatypes_skips_non_dict_model():
    data = {"semantic_model": ["junk", {"metrics": [{"datatype": "varchar"}]}]}
    _normalize_ossie_datatypes(data)
    assert data["semantic_model"][1]["metrics"][0]["datatype"] == "String"


def test_normalize_datatypes_skips_non_list_datasets():
    item = {"datatype": "bigint"}
    model = {"metrics": [item]}  # 无 datasets 键
    _normalize_ossie_datatypes({"semantic_model": [model]})
    assert item["datatype"] == "Integer"


def test_normalize_datatypes_skips_non_dict_item():
    item = "junk"
    model = {"metrics": [item], "datasets": [{"fields": [123]}]}
    _normalize_ossie_datatypes({"semantic_model": [model]})
    assert item == "junk"  # 非 dict 项原样保留


# ---------------------------------------------------------------- _extension_data

def test_extension_data_skips_other_vendor():
    other = SimpleNamespace(vendor_name="OTHER", data="{}")
    assert _extension_data([other], [], "label") == {}


def test_extension_data_warns_on_invalid_json():
    bad = SimpleNamespace(vendor_name="CHAT_DATA", data="{not-json")
    warnings: list = []
    assert _extension_data([bad], warnings, "指标 x") == {}
    assert warnings == ["指标 x 的 CHAT_DATA 扩展不是合法 JSON，已忽略"]


def test_extension_data_warns_on_non_object_payload():
    bad = SimpleNamespace(vendor_name="CHAT_DATA", data='[1,2]')
    warnings: list = []
    assert _extension_data([bad], warnings, "指标 x") == {}
    assert warnings == ["指标 x 的 CHAT_DATA 扩展不是对象，已忽略"]


def test_ansi_sql_expression_returns_none_without_ansi_dialect():
    expr = SimpleNamespace(dialects=[SimpleNamespace(dialect=SimpleNamespace(value="MYSQL"), expression="1")])
    assert _ansi_sql_expression(expr) is None
    assert _ansi_sql_expression(None) is None


# ---------------------------------------------------------------- import_document / _convert_model

def test_import_document_raises_on_empty_models():
    with pytest.raises(ValueError, match="不包含 semantic_model"):
        OssieToChatDataImporter().import_document(SimpleNamespace(semantic_model=[]))


def test_convert_model_error_when_name_missing():
    result = _import(_model(name="  "))
    assert "semantic_model 缺少 name，无法导入" in result.errors


def test_convert_model_error_on_duplicate_dataset_names():
    result = _import(_model(datasets=[_dataset("orders"), _dataset("orders")]))
    assert any("重名 dataset" in e for e in result.errors)


# ---------------------------------------------------------------- _convert_field

def test_convert_field_error_when_name_missing():
    result = _import(_model(datasets=[_dataset(fields=[_field(name="")])]))
    assert "dataset 存在缺少名称的字段，已跳过" in result.errors


def test_convert_field_error_when_no_ansi_expression():
    field = _field()
    field.expression = SimpleNamespace(dialects=[SimpleNamespace(dialect=SimpleNamespace(value="MYSQL"), expression="1")])
    result = _import(_model(datasets=[_dataset(fields=[field])]))
    assert any("缺少 ANSI_SQL 方言表达式" in e for e in result.errors)


def test_convert_field_fails_when_table_unresolvable_with_multi_dataset():
    """无扩展 table + 多 dataset → 归属失败出口（294->295）。"""
    field = _field(extensions=[])  # 无任何扩展
    result = _import(_model(datasets=[_dataset("orders", [field]), _dataset("users")]))
    assert any("无法确定归属表" in e for e in result.errors)
    assert result.dimensions == []


def test_convert_field_error_when_column_missing_and_expression_not_identifier():
    """单 dataset 推断表成功但表达式非单一物理字段（300->304）。"""
    field = _field(expression="SUM(amount)", extensions=[])  # 无扩展：表推断成功、无 column
    result = _import(_model(datasets=[_dataset("orders", [field])]))
    assert any("表达式不是单一物理字段" in e for e in result.errors)


def test_convert_field_omits_logical_type_when_unresolvable():
    """扩展无 logical_type、datatype 无法映射、非时间维度 → entry 不带 logical_type（323->325）。"""
    field = _field(datatype=None, description=None)
    result = _import(_model(datasets=[_dataset(fields=[field])]))
    assert len(result.dimensions) == 1
    assert "logical_type" not in result.dimensions[0]


# ---------------------------------------------------------------- _convert_metric

def test_convert_metric_error_when_name_missing():
    result = _import(_model(metrics=[SimpleNamespace(
        name="", custom_extensions=[], expression=_ansi("SUM(1)"), datatype=None, description=None)]))
    assert "semantic_model 存在缺少名称的指标，已跳过" in result.errors


def test_convert_metric_error_when_expression_missing():
    result = _import(_model(metrics=[SimpleNamespace(
        name="m1", custom_extensions=[], expression=None, datatype=None, description=None)]))
    assert any("缺少 ANSI_SQL 方言表达式" in e for e in result.errors)


def test_convert_derived_metric_without_period_filters_with_description():
    """派生指标：无 period（394->396）、无 filters（397->399）、带 description（400->401）。"""
    metric = SimpleNamespace(
        name="本月销售额",
        custom_extensions=[_ext({"metric_kind": "derived", "base_metric": "销售额"})],
        expression=None, datatype=None, description="按月聚合",
    )
    result = _import(_model(metrics=[metric]))
    assert len(result.metrics) == 1
    entry = result.metrics[0]
    assert entry["metric_kind"] == "derived"
    assert "period" not in entry
    assert "filters" not in entry
    assert entry["description"] == "按月聚合"
    assert result.stats["kind_restored"] == 1


def test_convert_composite_metric_with_description():
    """复合指标：带 description（426->427）。"""
    metric = SimpleNamespace(
        name="客单价",
        custom_extensions=[_ext({"metric_kind": "composite", "formula": "{a}/{b}"})],
        expression=None, datatype=None, description="客单价口径",
    )
    result = _import(_model(metrics=[metric]))
    entry = result.metrics[0]
    assert entry["metric_kind"] == "composite"
    assert entry["description"] == "客单价口径"


# ---------------------------------------------------------------- _convert_relationship

def _relationship(from_dataset="orders", to="users", from_columns=None, to_columns=None, extensions=None) -> SimpleNamespace:
    return SimpleNamespace(
        from_dataset=from_dataset,
        to=to,
        from_columns=from_columns if from_columns is not None else ["user_id"],
        to_columns=to_columns if to_columns is not None else ["id"],
        custom_extensions=extensions if extensions is not None else [_ext({"relationship_type": "many_to_one", "join_type": "left"})],
    )


def test_convert_relationship_error_when_dataset_missing():
    result = _import(_model(relationships=[_relationship(from_dataset="")]))
    assert any("缺少 from/to 数据集" in e for e in result.errors)


def test_convert_relationship_error_when_columns_mismatch():
    result = _import(_model(relationships=[_relationship(from_columns=["a"], to_columns=["x", "y"])]))
    assert any("关联字段为空或左右数量不一致" in e for e in result.errors)


def test_convert_relationship_warns_on_unsupported_join_type():
    """join_type 无法识别且非空 → 按 left 兜底并告警（453->454）。"""
    result = _import(_model(relationships=[_relationship(extensions=[_ext({"relationship_type": "many_to_one", "join_type": "full"})])]))
    assert any("join_type 不支持" in w for w in result.warnings)
    assert result.relationships[0]["join_type"] == "left"


def test_convert_relationship_warns_when_ext_tables_mismatch():
    """扩展左右表与 from/to 不一致 → 以扩展为准并告警（460->461）。"""
    ext = _ext({"relationship_type": "many_to_one", "join_type": "left", "left_table": "customers", "right_table": "orders"})
    result = _import(_model(relationships=[_relationship(extensions=[ext])]))
    assert any("以扩展为准" in w for w in result.warnings)
    assert result.relationships[0]["left_table"] == "customers"
    assert result.relationships[0]["right_table"] == "orders"
