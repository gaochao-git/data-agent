"""ontology_consistency 纯函数单测：ONT_* 规则族的触发/豁免/形状。"""

from __future__ import annotations

import pytest

from apps.chat_data_app.services.ontology.ontology_consistency import (
    RULES,
    check_ontology_model_consistency,
)


def _bundle() -> dict:
    """最小双对象类型 bundle：customer(id=1) / order(id=2)，一条关系类型 + 一条映射/对象类型。"""
    return {
        "object_types": [
            {"id": 1, "object_type_name": "customer", "display_name": "客户", "status": "certified"},
            {"id": 2, "object_type_name": "sales_order", "display_name": "订单", "status": "certified"},
        ],
        "properties": [
            {
                "id": 11,
                "object_type_id": 1,
                "property_name": "customer_id",
                "value_type": "String",
                "is_identifier": True,
            },
            {
                "id": 12,
                "object_type_id": 1,
                "property_name": "customer_name",
                "value_type": "String",
                "is_identifier": False,
            },
            {"id": 21, "object_type_id": 2, "property_name": "order_no", "value_type": "String", "is_identifier": True},
            {"id": 22, "object_type_id": 2, "property_name": "amount", "value_type": "Decimal", "is_identifier": False},
        ],
        "relation_types": [
            {
                "id": 1,
                "relation_type_name": "customer_has_orders",
                "from_object_type_id": 1,
                "to_object_type_id": 2,
                "from_columns": ["customer_id"],
                "to_columns": ["order_no"],
                "multiplicity": "OneToMany",
                "status": "certified",
            }
        ],
        "mappings": [
            {
                "object_type_id": 1,
                "semantic_config_id": "cfg-cust",
                "column_mappings": {"customer_id": "客户ID", "customer_name": "客户名称"},
            },
            {
                "object_type_id": 2,
                "semantic_config_id": "cfg-order",
                "column_mappings": {"order_no": "订单编号", "amount": "订单金额"},
            },
        ],
    }


def _configs() -> list[dict]:
    return [
        {
            "id": "cfg-cust",
            "name": "客户模型",
            "dimensions": [
                {"name": "客户ID", "column": "customer_id", "table": "customers", "logical_type": "text"},
                {"name": "客户名称", "column": "customer_name", "table": "customers", "logical_type": "text"},
            ],
        },
        {
            "id": "cfg-order",
            "name": "订单模型",
            "dimensions": [
                {"name": "订单编号", "column": "order_id", "table": "orders", "logical_type": "text"},
                {"name": "订单金额", "column": "amount", "table": "orders", "logical_type": "number"},
            ],
        },
    ]


def _codes(findings: list[dict]) -> list[str]:
    return [item["code"] for item in findings]


# ---------- 基线 ----------


def test_healthy_bundle_returns_empty():
    """全部健康（维度存在、属性全映射、关系类型列有效且 to 侧标识、类型匹配）→ 无 findings。"""
    assert check_ontology_model_consistency(_bundle(), _configs()) == []


def test_empty_inputs_return_empty():
    assert check_ontology_model_consistency({}, _configs()) == []
    assert check_ontology_model_consistency(_bundle(), []) == []
    assert check_ontology_model_consistency(None, _configs()) == []


def test_findings_shape_matches_checker_contract():
    """findings 形状与 checker 契约同构：code/severity/category/message/targets。"""
    bundle = _bundle()
    bundle["mappings"][1]["column_mappings"]["order_no"] = "已删除维度"
    findings = check_ontology_model_consistency(bundle, _configs())
    target = next(item for item in findings if item["code"] == "ONT_MAPPING_DIM_MISSING")
    assert target["severity"] == "error"
    assert target["category"] == "ontology"
    assert isinstance(target["message"], str) and target["message"]
    assert target["targets"] == ["sales_order", "cfg-order"]


# ---------- ONT_MAPPING_DIM_MISSING ----------


def test_missing_dimension_fires_error():
    bundle = _bundle()
    bundle["mappings"][1]["column_mappings"]["order_no"] = "已删除维度"
    findings = check_ontology_model_consistency(bundle, _configs())
    assert "ONT_MAPPING_DIM_MISSING" in _codes(findings)
    finding = next(item for item in findings if item["code"] == "ONT_MAPPING_DIM_MISSING")
    assert "已删除维度" in finding["message"]
    assert "sales_order" in finding["message"]


def test_mapping_to_model_outside_compare_scope_is_skipped():
    """映射指向比较域外的模型（未传入 configs）不判——无法证明失修。"""
    bundle = _bundle()
    bundle["mappings"].append(
        {"object_type_id": 2, "semantic_config_id": "cfg-other", "column_mappings": {"order_no": "不存在的维度"}}
    )
    assert "ONT_MAPPING_DIM_MISSING" not in _codes(check_ontology_model_consistency(bundle, _configs()))


def test_draft_object_type_mapping_not_reported():
    """映射行的对象类型不在 AI 可见对象类型集（bundle.object_types）时不判。"""
    bundle = _bundle()
    bundle["mappings"].append(
        {"object_type_id": 99, "semantic_config_id": "cfg-order", "column_mappings": {"x": "不存在"}}
    )
    assert "ONT_MAPPING_DIM_MISSING" not in _codes(check_ontology_model_consistency(bundle, _configs()))


# ---------- ONT_PROP_UNMAPPED ----------


def test_unmapped_property_fires_info():
    bundle = _bundle()
    del bundle["mappings"][1]["column_mappings"]["amount"]
    findings = check_ontology_model_consistency(bundle, _configs())
    finding = next(item for item in findings if item["code"] == "ONT_PROP_UNMAPPED")
    assert finding["severity"] == "info"
    assert "amount" in finding["message"]


def test_object_without_mapping_rows_skips_unmapped_check():
    """对象类型无任何映射行时跳过（另一条链路负责映射完整性，此处只查「有映射但漏属性」）。"""
    bundle = _bundle()
    bundle["mappings"] = [bundle["mappings"][0]]
    assert "ONT_PROP_UNMAPPED" not in _codes(check_ontology_model_consistency(bundle, _configs()))


# ---------- ONT_REL_COLUMN_INVALID / ONT_REL_TO_NOT_IDENTIFIER ----------


def test_relation_column_not_a_property_fires_error():
    bundle = _bundle()
    bundle["relation_types"][0]["from_columns"] = ["not_a_prop"]
    findings = check_ontology_model_consistency(bundle, _configs())
    finding = next(item for item in findings if item["code"] == "ONT_REL_COLUMN_INVALID")
    assert "not_a_prop" in finding["message"]


def test_relation_column_unmapped_fires_error():
    bundle = _bundle()
    del bundle["mappings"][1]["column_mappings"]["order_no"]
    findings = check_ontology_model_consistency(bundle, _configs())
    assert "ONT_REL_COLUMN_INVALID" in _codes(findings)


def test_relation_to_side_non_identifier_fires_warning():
    bundle = _bundle()
    bundle["properties"][2]["is_identifier"] = False  # order_no 非标识
    findings = check_ontology_model_consistency(bundle, _configs())
    finding = next(item for item in findings if item["code"] == "ONT_REL_TO_NOT_IDENTIFIER")
    assert finding["severity"] == "warning"


def test_relation_endpoint_outside_bundle_skipped():
    """端点对象类型不在 bundle（对象类型名不可知）时不判该侧。"""
    bundle = _bundle()
    bundle["relation_types"][0]["to_object_type_id"] = 42
    assert "ONT_REL_COLUMN_INVALID" not in _codes(check_ontology_model_consistency(bundle, _configs()))


# ---------- ONT_REL_ENDPOINT_TYPE ----------


def test_relation_endpoint_type_mismatch_fires_warning():
    """两端 join 属性语义类别冲突（String ↔ Integer）→ warning（Palantir 硬约束同款）。"""
    bundle = _bundle()
    bundle["properties"][2]["value_type"] = "Integer"  # sales_order.order_no
    findings = check_ontology_model_consistency(bundle, _configs())
    finding = next(item for item in findings if item["code"] == "ONT_REL_ENDPOINT_TYPE")
    assert finding["severity"] == "warning"
    assert "customer.customer_id" in finding["message"] and "sales_order.order_no" in finding["message"]


@pytest.mark.parametrize(
    ("to_type", "should_fire"),
    [
        ("Integer", True),  # string ↔ numeric 冲突
        ("Date", True),  # string ↔ temporal 冲突
        ("enum", False),  # 小写 enum（validator 词表）与 String 同属 string 类
        ("Boolean", False),  # 维度侧不可归类 → 跳过
    ],
)
def test_relation_endpoint_type_matrix(to_type, should_fire):
    bundle = _bundle()
    bundle["properties"][2]["value_type"] = to_type
    codes = _codes(check_ontology_model_consistency(bundle, _configs()))
    assert ("ONT_REL_ENDPOINT_TYPE" in codes) is should_fire


def test_relation_endpoint_type_composite_pairs_by_position():
    """复合 join 按位置配对：第一对同类别、第二对冲突 → 报 1 对。"""
    bundle = _bundle()
    bundle["relation_types"][0]["from_columns"] = ["customer_id", "customer_name"]
    bundle["relation_types"][0]["to_columns"] = ["order_no", "amount"]
    finding = next(
        item for item in check_ontology_model_consistency(bundle, _configs()) if item["code"] == "ONT_REL_ENDPOINT_TYPE"
    )
    assert "customer.customer_name" in finding["message"]


def test_relation_endpoint_type_missing_property_skipped():
    """任一侧属性不存在时跳过（失效列由 ONT_REL_COLUMN_INVALID 负责，不双报）。"""
    bundle = _bundle()
    bundle["relation_types"][0]["to_columns"] = ["not_a_prop"]
    bundle["properties"][2]["value_type"] = "Integer"
    codes = _codes(check_ontology_model_consistency(bundle, _configs()))
    assert "ONT_REL_ENDPOINT_TYPE" not in codes
    assert "ONT_REL_COLUMN_INVALID" in codes


# ---------- ONT_REL_CROSS_SOURCE ----------


def test_relation_cross_source_fires_warning():
    """两端对象类型映射的模型分属不同数据源组 → warning（join 可执行性红线）。"""
    bundle = _bundle()
    findings = check_ontology_model_consistency(
        bundle, _configs(), config_group_ids={"cfg-cust": "g1", "cfg-order": "g2"}
    )
    finding = next(item for item in findings if item["code"] == "ONT_REL_CROSS_SOURCE")
    assert finding["severity"] == "warning"
    assert "g1" in finding["message"] and "g2" in finding["message"]


@pytest.mark.parametrize(
    ("groups", "should_fire"),
    [
        ({"cfg-cust": "g1", "cfg-order": "g1"}, False),  # 同组可 join
        ({"cfg-cust": "g1"}, False),  # to 侧组不可知 → 不误报
        ({"cfg-order": "g2"}, False),  # from 侧组不可知 → 不误报
        ({}, False),  # 组信息整体缺省 → 规则跳过
        ({"cfg-cust": "g1", "cfg-order": "g2"}, True),
    ],
)
def test_relation_cross_source_matrix(groups, should_fire):
    codes = _codes(check_ontology_model_consistency(_bundle(), _configs(), config_group_ids=groups or None))
    assert ("ONT_REL_CROSS_SOURCE" in codes) is should_fire


def test_relation_cross_source_unmapped_endpoint_skipped():
    """任一端对象类型无映射行时跳过（无 join 基准不误报）。"""
    bundle = _bundle()
    bundle["mappings"] = [bundle["mappings"][0]]  # 只剩 customer 的映射
    codes = _codes(
        check_ontology_model_consistency(bundle, _configs(), config_group_ids={"cfg-cust": "g1", "cfg-order": "g2"})
    )
    assert "ONT_REL_CROSS_SOURCE" not in codes


def test_relation_cross_source_object_multiple_mappings_overlap():
    """对象类型可映射多个模型：任一组相交即视为可达，不报。"""
    bundle = _bundle()
    bundle["mappings"].append(
        {"object_type_id": 1, "semantic_config_id": "cfg-order", "column_mappings": {"customer_id": "订单编号"}}
    )
    codes = _codes(
        check_ontology_model_consistency(bundle, _configs(), config_group_ids={"cfg-cust": "g1", "cfg-order": "g2"})
    )
    assert "ONT_REL_CROSS_SOURCE" not in codes


# ---------- ONT_TYPE_MISMATCH ----------


@pytest.mark.parametrize(
    ("prop_type", "dim_type", "should_fire"),
    [
        ("String", "number", True),  # 实测案例：金额标 String 实为数值
        ("Decimal", "text", True),
        ("Integer", "datetime", True),
        ("Date", "text", True),
        ("String", "text", False),
        ("Decimal", "number", False),
        ("Date", "datetime", False),
        ("DateTime", "date", False),
        ("enum", "number", True),  # 回归：value_type 是小写 "enum"（validator 词表），映射到数值维度必须报
        ("enum", "enum", False),
        ("enum", "text", False),
        ("Boolean", "text", False),  # 维度侧无 boolean 类别，跳过
        ("String", "unknown_type", False),  # 未知 logical_type 跳过
    ],
)
def test_type_mismatch_matrix(prop_type, dim_type, should_fire):
    bundle = _bundle()
    configs = _configs()
    # amount 属性 (object 2) 映射到「订单金额」维度
    prop = next(item for item in bundle["properties"] if item["property_name"] == "amount")
    prop["value_type"] = prop_type
    dim = next(item for item in configs[1]["dimensions"] if item["name"] == "订单金额")
    dim["logical_type"] = dim_type
    codes = _codes(check_ontology_model_consistency(bundle, configs))
    assert ("ONT_TYPE_MISMATCH" in codes) is should_fire


def test_type_mismatch_skips_missing_dimension():
    """维度不存在时类型比较跳过（失效维度由 ONT_MAPPING_DIM_MISSING 负责，不双报）。"""
    bundle = _bundle()
    bundle["mappings"][1]["column_mappings"]["amount"] = "已删除维度"
    assert "ONT_TYPE_MISMATCH" not in _codes(check_ontology_model_consistency(bundle, _configs()))


# ---------- 排序与登记 ----------


def test_findings_sorted_deterministically():
    bundle = _bundle()
    bundle["mappings"][0]["column_mappings"]["customer_id"] = "失效A"
    bundle["mappings"][1]["column_mappings"]["order_no"] = "已删除维度"
    bundle["properties"][2]["is_identifier"] = False
    bundle["relation_types"][0]["from_columns"] = ["ghost_prop"]
    findings = check_ontology_model_consistency(bundle, _configs())
    keys = [(item["targets"][0] if item["targets"] else "", item["code"]) for item in findings]
    assert keys == sorted(keys)


def test_rules_registry_severities():
    assert RULES["ONT_MAPPING_DIM_MISSING"][0] == "error"
    assert RULES["ONT_REL_COLUMN_INVALID"][0] == "error"
    assert RULES["ONT_REL_TO_NOT_IDENTIFIER"][0] == "warning"
    assert RULES["ONT_REL_ENDPOINT_TYPE"][0] == "warning"
    assert RULES["ONT_REL_CROSS_SOURCE"][0] == "warning"
    assert RULES["ONT_TYPE_MISMATCH"][0] == "warning"
    assert RULES["ONT_PROP_UNMAPPED"][0] == "info"


# ---------- ONT_REL_MODEL_PATH_MISSING / ONT_REL_CROSS_MODEL ----------


def test_relation_same_model_without_join_path_fires_error():
    bundle = _bundle()
    bundle["mappings"] = [
        {"object_type_id": 1, "semantic_config_id": "cfg-shared", "column_mappings": {"customer_id": "客户ID"}},
        {"object_type_id": 2, "semantic_config_id": "cfg-shared", "column_mappings": {"order_no": "订单编号"}},
    ]
    configs = [
        {
            "id": "cfg-shared",
            "dimensions": [
                {"name": "客户ID", "column": "customer_id", "table": "customers", "logical_type": "text"},
                {"name": "订单编号", "column": "order_id", "table": "orders", "logical_type": "text"},
            ],
            "relationships": [],
        }
    ]
    findings = check_ontology_model_consistency(bundle, configs)
    finding = next(item for item in findings if item["code"] == "ONT_REL_MODEL_PATH_MISSING")
    assert finding["severity"] == "error"
    assert "customer_has_orders" in finding["message"]


def test_relation_same_model_with_join_path_is_healthy():
    bundle = _bundle()
    bundle["mappings"] = [
        {"object_type_id": 1, "semantic_config_id": "cfg-shared", "column_mappings": {"customer_id": "客户ID"}},
        {"object_type_id": 2, "semantic_config_id": "cfg-shared", "column_mappings": {"order_no": "订单编号"}},
    ]
    configs = [
        {
            "id": "cfg-shared",
            "dimensions": [
                {"name": "客户ID", "column": "customer_id", "table": "customers", "logical_type": "text"},
                {"name": "订单编号", "column": "order_id", "table": "orders", "logical_type": "text"},
            ],
            "relationships": [{"left_table": "customers", "right_table": "orders"}],
        }
    ]
    assert "ONT_REL_MODEL_PATH_MISSING" not in _codes(check_ontology_model_consistency(bundle, configs))


def test_relation_cross_model_same_group_is_info():
    findings = check_ontology_model_consistency(
        _bundle(), _configs(), config_group_ids={"cfg-cust": "g1", "cfg-order": "g1"}
    )
    finding = next(item for item in findings if item["code"] == "ONT_REL_CROSS_MODEL")
    assert finding["severity"] == "info"
    assert "customer_has_orders" in finding["message"]

# ---------- Dataset source binding（2026-10-23 解耦后）：映射左值=本体属性、右值=Dataset 逻辑列 ----------


def _dataset_bundle() -> dict:
    bundle = _bundle()
    bundle["mappings"] = [
        {
            "object_type_id": 1,
            "column_mappings": {"customer_id": "customer_id", "customer_name": "customer_name"},
        },
        {
            "object_type_id": 2,
            "column_mappings": {"order_no": "order_id", "amount": "amount"},
        },
    ]
    return bundle


def test_dataset_binding_mappings_do_not_trigger_relation_column_invalid():
    """回归：Dataset 绑定解耦后 mappings 不再带 semantic_config_id，关系列映射检查仍须按 Dataset 口径生效。"""
    codes = _codes(check_ontology_model_consistency(_dataset_bundle(), _configs()))
    assert "ONT_REL_COLUMN_INVALID" not in codes
    assert "ONT_PROP_UNMAPPED" not in codes


def test_dataset_binding_mappings_detect_unmapped_property():
    bundle = _dataset_bundle()
    bundle["mappings"][0]["column_mappings"].pop("customer_name")
    findings = check_ontology_model_consistency(bundle, _configs())
    unmapped = [item for item in findings if item["code"] == "ONT_PROP_UNMAPPED"]
    assert len(unmapped) == 1
    assert "customer_name" in unmapped[0]["message"]
