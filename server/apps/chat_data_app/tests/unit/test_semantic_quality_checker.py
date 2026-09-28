import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.services.semantic.run.semantic_quality_checker import (
    check_cross_model_metrics,
    first_structure_error,
    get_semantic_quality_checker,
    grade_for_score,
)


def _good_config() -> dict:
    """描述齐全、口径可执行、关系无环的基准配置（接近满分）。"""
    return {
        "id": "sc_order_full",
        "name": "订单分析",
        "description": "订单经营分析",
        "business_notes": "用户下单后生成订单，支付成功后计入有效成交。",
        "metrics": [
            {"name": "销售额", "expression": "SUM(orders.amount)", "table": "orders",
             "description": "已支付订单金额合计"},
            {"name": "订单数", "expression": "COUNT(DISTINCT orders.order_id)", "table": "orders",
             "description": "去重订单数"},
            {
                "name": "本月成交额",
                "metric_kind": "derived",
                "base_metric": "销售额",
                "period": {"kind": "this_month", "time_dimension": "下单时间"},
                "filters": [{"dimension": "订单状态", "operator": "=", "value": "已支付"}],
                "description": "本月已支付销售额",
            },
            {
                "name": "客单价",
                "metric_kind": "composite",
                "formula": "{销售额} / {订单数}",
                "description": "平均单笔成交金额",
            },
        ],
        "dimensions": [
            {"name": "下单时间", "column": "order_time", "table": "orders", "logical_type": "datetime",
             "expression": "`order_time`", "description": "订单下单时间"},
            {"name": "订单状态", "column": "status", "table": "orders", "logical_type": "enum",
             "expression": "`status`", "description": "订单当前状态"},
            {"name": "用户昵称", "column": "nickname", "table": "users", "logical_type": "text",
             "expression": "`nickname`", "description": "用户昵称"},
        ],
        "relationships": [
            {
                "left_table": "orders",
                "right_table": "users",
                "relationship_type": "many_to_one",
                "join_type": "left",
                "join_conditions": [{"left_column": "user_id", "right_column": "id"}],
            },
        ],
    }


PHYSICAL_COLUMNS = {
    ("orders", "amount"),
    ("orders", "order_id"),
    ("orders", "order_time"),
    ("orders", "status"),
    ("orders", "user_id"),
    ("users", "nickname"),
    ("users", "id"),
    ("users", "tenant_id"),
}


def _codes(report) -> list:
    return [finding.code for finding in report.findings]


def _find(report, code: str):
    for finding in report.findings:
        if finding.code == code:
            return finding
    return None


def test_good_config_scores_high_without_errors_or_warnings():
    report = get_semantic_quality_checker().check_config(_good_config(), physical_columns=PHYSICAL_COLUMNS)
    assert report.score >= 95
    assert all(finding.severity == "info" for finding in report.findings)
    assert report.stats["atomic_metric_count"] == 2
    assert report.stats["derived_metric_count"] == 1
    assert report.stats["composite_metric_count"] == 1
    assert report.stats["dimension_count"] == 3


def test_unmodeled_columns_aggregated_with_count():
    report = get_semantic_quality_checker().check_config(_good_config(), physical_columns=PHYSICAL_COLUMNS)
    finding = _find(report, "COV_UNMODELED_COLUMNS")
    assert finding is not None
    # orders.user_id（关系 join 列，未被维度/指标建模）+ users.id + users.tenant_id 未被引用
    assert "orders.user_id(3列)" in finding.message


def test_atomic_metric_missing_table_is_error():
    config = _good_config()
    del config["metrics"][0]["table"]
    report = get_semantic_quality_checker().check_config(config)
    finding = _find(report, "PROV_METRIC_MISSING_TABLE")
    assert finding is not None and finding.severity == "error"
    assert "销售额" in finding.targets


def test_dimension_missing_table_and_column():
    config = _good_config()
    del config["dimensions"][0]["table"]
    del config["dimensions"][1]["column"]
    report = get_semantic_quality_checker().check_config(config)
    assert _find(report, "PROV_DIMENSION_MISSING_TABLE") is not None
    finding = _find(report, "PROV_DIMENSION_MISSING_COLUMN")
    assert finding is not None and "订单状态" in finding.targets


def test_unknown_column_against_physical_inventory():
    """结构校验接管列存在性：维度单列与指标表达式双路径都报 STR。"""
    config = _good_config()
    config["dimensions"][0]["column"] = "not_exist_col"
    config["metrics"][0]["table"] = "fact_missing"
    report = get_semantic_quality_checker().check_config(config, physical_columns=PHYSICAL_COLUMNS)
    finding = _find(report, "STR_DIMENSION_INVALID")
    assert finding is not None and "not_exist_col" in finding.message
    finding = _find(report, "STR_METRIC_INVALID")
    assert finding is not None and "fact_missing" in finding.message


def test_unknown_column_skipped_without_inventory():
    config = _good_config()
    config["dimensions"][0]["column"] = "not_exist_col"
    report = get_semantic_quality_checker().check_config(config, physical_columns=None)
    assert _find(report, "STR_DIMENSION_INVALID") is None


def test_derived_metric_base_missing_and_no_constraint():
    config = _good_config()
    config["metrics"][2]["base_metric"] = "不存在的指标"
    report = get_semantic_quality_checker().check_config(config)
    finding = _find(report, "STR_DERIVED_INVALID")
    assert finding is not None and "本月成交额" in finding.message

    config2 = _good_config()
    config2["metrics"][2].pop("period")
    config2["metrics"][2].pop("filters")
    report2 = get_semantic_quality_checker().check_config(config2)
    finding2 = _find(report2, "EXE_DERIVED_NO_CONSTRAINT")
    assert finding2 is not None and finding2.severity == "info"


def test_composite_formula_missing_and_unresolved_tokens():
    config = _good_config()
    config["metrics"][3].pop("formula")
    report = get_semantic_quality_checker().check_config(config)
    finding = _find(report, "STR_COMPOSITE_INVALID")
    assert finding is not None and "formula" in finding.message

    config2 = _good_config()
    config2["metrics"][3]["formula"] = "{销售额} / {毛利率}"
    report2 = get_semantic_quality_checker().check_config(config2)
    finding2 = _find(report2, "EXE_COMPOSITE_UNRESOLVED")
    assert finding2 is not None and "毛利率" in finding2.message


def test_composite_formula_with_braced_members_resolves():
    config = _good_config()
    report = get_semantic_quality_checker().check_config(config)
    assert _find(report, "EXE_COMPOSITE_UNRESOLVED") is None


def test_coverage_missing_metrics_or_dimensions():
    config = _good_config()
    config["metrics"] = []
    report = get_semantic_quality_checker().check_config(config)
    assert _find(report, "COV_NO_METRICS") is not None

    config2 = _good_config()
    config2["dimensions"] = []
    report2 = get_semantic_quality_checker().check_config(config2)
    assert _find(report2, "COV_NO_DIMENSIONS") is not None


def test_duplicate_dimension_column():
    config = _good_config()
    config["dimensions"].append(dict(config["dimensions"][0], name="下单时间副本"))
    report = get_semantic_quality_checker().check_config(config)
    finding = _find(report, "COV_DUPLICATE_DIMENSION_COLUMN")
    assert finding is not None and "orders.order_time" in finding.message


def test_duplicate_metric_expression():
    config = _good_config()
    config["metrics"].append({"name": "销售额副本", "expression": "SUM(orders.amount)", "table": "orders",
                              "description": "重复口径"})
    report = get_semantic_quality_checker().check_config(config)
    finding = _find(report, "CON_DUPLICATE_METRIC_EXPRESSION")
    assert finding is not None and "销售额副本" in finding.message


def test_duplicate_names_within_namespace():
    config = _good_config()
    config["metrics"].append(dict(config["metrics"][0], expression="SUM(orders.other)"))
    config["dimensions"].append(dict(config["dimensions"][0], column="other_time"))
    report = get_semantic_quality_checker().check_config(config)
    finding = _find(report, "CON_NAME_DUPLICATE")
    assert finding is not None and finding.severity == "warning"
    assert "指标:销售额" in finding.targets
    assert "维度:下单时间" in finding.targets


def test_cross_namespace_same_name_not_flagged():
    """指标与维度是两个命名空间（引用目标不同），跨空间同名不判。"""
    config = _good_config()
    config["metrics"][0]["name"] = "同名对象"
    config["dimensions"][0]["name"] = "同名对象"
    # 同步派生/复合指标的引用，避免引入无关 finding
    config["metrics"][2]["base_metric"] = "同名对象"
    config["metrics"][3]["formula"] = "{同名对象} / {订单数}"
    config["metrics"][2]["filters"] = [{"dimension": "同名对象", "operator": "=", "value": "已支付"}]
    report = get_semantic_quality_checker().check_config(config)
    assert _find(report, "CON_NAME_DUPLICATE") is None


def test_name_chars_valid_names_not_flagged():
    """中/英/数字/下划线组合的名称不触发 CON_NAME_CHARS。"""
    config = _good_config()
    config["metrics"][0]["name"] = "销售总额_2026_amount"
    config["dimensions"][0]["name"] = "下单时间2"
    report = get_semantic_quality_checker().check_config(config)
    assert _find(report, "CON_NAME_CHARS") is None


def test_name_chars_flags_special_characters():
    """空格/括号/花括号等白名单外字符触发 warning，指标维度分别报。"""
    config = _good_config()
    config["metrics"][0]["name"] = "订单 数量"
    config["dimensions"][0]["name"] = "下单时间(日期)"
    report = get_semantic_quality_checker().check_config(config)
    finding = _find(report, "CON_NAME_CHARS")
    assert finding is not None and finding.severity == "warning"
    assert "指标:订单 数量" in finding.targets
    assert "维度:下单时间(日期)" in finding.targets


def test_relationship_key_uncovered():
    config = _good_config()
    config["tables"] = [
        {"table": "users", "primary_keys": [["tenant_id"]]},
        {"table": "orders", "primary_keys": [["order_id"]]},
    ]
    # users 声明主键 tenant_id，join 只带 id → 未覆盖
    report = get_semantic_quality_checker().check_config(config)
    finding = _find(report, "CON_RELATIONSHIP_KEY_UNCOVERED")
    assert finding is not None and finding.severity == "warning"
    assert finding.targets == ["orders->users"]

    # 覆盖任一声明键（含超集）即通过：改为唯一索引声明 id + join 带上 id
    config2 = _good_config()
    config2["tables"] = [
        {"table": "users", "unique_indexes": [{"columns": ["id"]}], "manual_indexes": [
            {"index_type": "unique", "columns": ["id", "tenant_id"]},
        ]},
    ]
    report2 = get_semantic_quality_checker().check_config(config2)
    assert _find(report2, "CON_RELATIONSHIP_KEY_UNCOVERED") is None

    # 复合键超集：join 列包含键的全部列即可
    config3 = _good_config()
    config3["tables"] = [{"table": "users", "primary_keys": [["id", "tenant_id"]]}]
    config3["relationships"][0]["join_conditions"] = [
        {"left_column": "user_id", "right_column": "id"},
        {"left_column": "tenant_id", "right_column": "tenant_id"},
    ]
    report3 = get_semantic_quality_checker().check_config(config3)
    assert _find(report3, "CON_RELATIONSHIP_KEY_UNCOVERED") is None


def test_relationship_key_coverage_skipped_cases():
    """many_to_many 两端都不是键、表无声明键、无 tables 元数据时均跳过。"""
    config = _good_config()
    config["tables"] = [{"table": "users", "primary_keys": [["tenant_id"]]}]
    config["relationships"][0]["relationship_type"] = "many_to_many"
    report = get_semantic_quality_checker().check_config(config)
    assert _find(report, "CON_RELATIONSHIP_KEY_UNCOVERED") is None

    config2 = _good_config()
    config2["tables"] = [{"table": "orders", "primary_keys": [["order_id"]]}]  # users 无声明
    report2 = get_semantic_quality_checker().check_config(config2)
    assert _find(report2, "CON_RELATIONSHIP_KEY_UNCOVERED") is None

    report3 = get_semantic_quality_checker().check_config(_good_config())
    assert _find(report3, "CON_RELATIONSHIP_KEY_UNCOVERED") is None


def test_relationship_self_duplicate_and_unreferenced_table():
    config = _good_config()
    config["relationships"].append({
        "left_table": "orders", "right_table": "orders",
        "relationship_type": "one_to_one", "join_type": "left",
        "join_conditions": [{"left_column": "order_id", "right_column": "order_id"}],
    })
    config["relationships"].append({
        "left_table": "ref_dict", "right_table": "orders",
        "relationship_type": "one_to_many", "join_type": "left",
        "join_conditions": [{"left_column": "id", "right_column": "order_id"}],
    })
    report = get_semantic_quality_checker().check_config(config)
    assert _find(report, "CON_RELATIONSHIP_SELF") is not None
    finding = _find(report, "CON_RELATIONSHIP_TABLE_UNREFERENCED")
    assert finding is not None and "ref_dict" in finding.targets


def test_relationship_cycle_detected():
    config = _good_config()
    config["relationships"].extend([
        {
            "left_table": "users", "right_table": "tags",
            "relationship_type": "many_to_many", "join_type": "left",
            "join_conditions": [{"left_column": "id", "right_column": "user_id"}],
        },
        {
            "left_table": "tags", "right_table": "orders",
            "relationship_type": "many_to_many", "join_type": "left",
            "join_conditions": [{"left_column": "order_id", "right_column": "order_id"}],
        },
    ])
    report = get_semantic_quality_checker().check_config(config)
    finding = _find(report, "CON_RELATIONSHIP_CYCLE")
    assert finding is not None


def test_documentation_findings_aggregated_and_scored_once():
    config = _good_config()
    for metric in config["metrics"]:
        metric.pop("description", None)
    for dimension in config["dimensions"]:
        dimension.pop("description", None)
    report = get_semantic_quality_checker().check_config(config)
    metric_doc = _find(report, "DOC_METRIC_NO_DESCRIPTION")
    dim_doc = _find(report, "DOC_DIMENSION_NO_DESCRIPTION")
    assert metric_doc is not None and len(metric_doc.targets) == 4
    assert dim_doc is not None and len(dim_doc.targets) == 3
    # 原子指标 + 派生/复合共 4 条聚合成 1 条 info，只扣 1 分
    assert report.score >= 95


def test_awful_config_scores_low():
    """多类问题叠加时得分落在“差”区间；聚合扣分有下限保护。"""
    config = _good_config()
    config["business_notes"] = ""
    config["dimensions"] = [
        {"name": "dimA", "expression": "`a`"},  # 缺 table（error）
        {"name": "dimB", "table": "orders", "expression": "`b`"},  # 缺 column（warning）
        {"name": "状态1", "table": "orders", "column": "status", "expression": "`status`"},  # 重复列（warning）
        {"name": "状态2", "table": "orders", "column": "status", "expression": "`status`"},
    ]
    config["metrics"] = [
        {"name": "m1", "expression": "SUM(CASE WHEN 1=1 THEN x END)", "table": ""},  # 缺 table + 非简单聚合
        {"name": "m1dup", "expression": "SUM(CASE WHEN 1=1 THEN x END)", "table": "orders"},  # 重复表达式
        {"name": "m2", "expression": "SUM(orders.nope)", "table": "orders"},  # 物理列不存在
        {"name": "d1", "metric_kind": "derived", "base_metric": "幽灵指标"},  # 基础指标不存在
        {"name": "c1", "metric_kind": "composite"},  # 缺 formula
    ]
    config["relationships"] = [
        {"left_table": "orders", "right_table": "orders", "relationship_type": "one_to_one",
         "join_type": "left", "join_conditions": [{"left_column": "id", "right_column": "id"}]},
        {"left_table": "dict1", "right_table": "orders", "relationship_type": "one_to_many",
         "join_type": "left", "join_conditions": [{"left_column": "id", "right_column": "id"}]},
        {"left_table": "dict1", "right_table": "orders", "relationship_type": "one_to_many",
         "join_type": "left", "join_conditions": [{"left_column": "id", "right_column": "id"}]},
        {"left_table": "orders", "right_table": "t1", "relationship_type": "one_to_many",
         "join_type": "left", "join_conditions": [{"left_column": "id", "right_column": "oid"}]},
        {"left_table": "t1", "right_table": "t2", "relationship_type": "one_to_many",
         "join_type": "left", "join_conditions": [{"left_column": "oid", "right_column": "tid"}]},
        {"left_table": "t2", "right_table": "orders", "relationship_type": "one_to_many",
         "join_type": "left", "join_conditions": [{"left_column": "tid", "right_column": "id"}]},
    ]
    report = get_semantic_quality_checker().check_config(config, physical_columns=PHYSICAL_COLUMNS)
    codes = set(_codes(report))
    assert {
        "STR_DIMENSION_INVALID", "STR_METRIC_INVALID", "STR_DERIVED_INVALID",
        "STR_COMPOSITE_INVALID", "STR_RELATIONSHIP_INVALID",
        "PROV_METRIC_MISSING_TABLE", "PROV_DIMENSION_MISSING_TABLE", "PROV_DIMENSION_MISSING_COLUMN",
        "COV_DUPLICATE_DIMENSION_COLUMN",
        "CON_DUPLICATE_METRIC_EXPRESSION", "CON_RELATIONSHIP_DUPLICATE", "CON_RELATIONSHIP_SELF",
        "CON_RELATIONSHIP_TABLE_UNREFERENCED", "CON_RELATIONSHIP_CYCLE",
        "DOC_METRIC_NO_DESCRIPTION", "DOC_DIMENSION_NO_DESCRIPTION", "DOC_NO_BUSINESS_NOTES",
    } <= codes
    assert report.score < 40
    assert report.grade == "差"


def test_grade_boundaries():
    assert grade_for_score(100) == "优"
    assert grade_for_score(90) == "优"
    assert grade_for_score(89) == "良"
    assert grade_for_score(75) == "良"
    assert grade_for_score(74) == "中"
    assert grade_for_score(60) == "中"
    assert grade_for_score(59) == "差"


def test_derived_and_composite_missing_table_not_flagged():
    """派生/复合指标不落物理表，table 为空不判 PROV_METRIC_MISSING_TABLE。"""
    config = _good_config()
    report = get_semantic_quality_checker().check_config(config)
    finding = _find(report, "PROV_METRIC_MISSING_TABLE")
    assert finding is None


# ---------------------------------------------------------------------------
# 跨模型指标口径检查（第二层校验：check_cross_model_metrics）
# ---------------------------------------------------------------------------


def _model(name: str, metrics: list) -> dict:
    return {"name": name, "metrics": metrics}


def test_cross_model_name_conflict_is_error_and_names_both_models():
    configs = [
        _model("订单分析", [{"name": "销售额", "expression": "SUM(orders.amount)"}]),
        _model("销售主题", [{"name": "销售额", "expression": "SUM(orders.pay_amount)"}]),
    ]
    findings = check_cross_model_metrics(configs)
    assert [item["code"] for item in findings] == ["CON_CROSS_MODEL_NAME_CONFLICT"]
    assert findings[0]["severity"] == "error"
    assert findings[0]["category"] == "consistency"
    assert "订单分析" in findings[0]["targets"][0] and "销售主题" in findings[0]["targets"][0]


def test_cross_model_same_name_same_expression_is_warning():
    configs = [
        _model("模型A", [{"name": "销售额", "expression": "sum( orders.amount )"}]),
        _model("模型B", [{"name": "销售额", "expression": "SUM(orders.amount)"}]),
    ]
    findings = check_cross_model_metrics(configs)
    assert [item["code"] for item in findings] == ["CON_CROSS_MODEL_METRIC_DUPLICATE"]
    assert findings[0]["severity"] == "warning"


def test_cross_model_same_expression_different_names_not_flagged():
    """异名同表达式不判：COUNT(id)/SUM(xxx) 类通用表达式跨模型撞车太常见，判了全是噪音。"""
    configs = [
        _model("模型A", [{"name": "销售额", "expression": "SUM(orders.amount)"}]),
        _model("模型B", [{"name": "成交额", "expression": "SUM(ORDERS.AMOUNT)"}]),
    ]
    assert check_cross_model_metrics(configs) == []


def test_cross_model_focus_only_reports_involving_focus():
    configs = [
        _model("模型A", [{"name": "销售额", "expression": "SUM(orders.amount)"}]),
        _model("模型B", [{"name": "销售额", "expression": "SUM(orders.pay_amount)"}, {"name": "订单量", "expression": "COUNT(orders.id)"}]),
        _model("模型C", [{"name": "成交量", "expression": "count( orders.id )"}]),
    ]
    focus_a = check_cross_model_metrics(configs, focus_config_name="模型A")
    assert {item["code"] for item in focus_a} == {"CON_CROSS_MODEL_NAME_CONFLICT"}
    # 模型C 与模型B 异名同表达式：不再判（只判同名）
    assert check_cross_model_metrics(configs, focus_config_name="模型C") == []
    all_findings = check_cross_model_metrics(configs)
    assert {item["code"] for item in all_findings} == {"CON_CROSS_MODEL_NAME_CONFLICT"}


def test_cross_model_skips_derived_composite_and_blank():
    configs = [
        _model("模型A", [
            {"name": "本月销售额", "metric_kind": "derived", "base_metric": "销售额"},
            {"name": "客单价", "metric_kind": "composite", "formula": "({销售额}/{订单数})"},
            {"name": "空表达式", "expression": "  "},
            {"name": "", "expression": "SUM(x)"},
        ]),
        _model("模型B", [
            {"name": "本月销售额", "metric_kind": "derived", "base_metric": "销售额"},
            {"name": "客单价", "metric_kind": "composite", "formula": "({销售额}/{订单数})"},
        ]),
    ]
    assert check_cross_model_metrics(configs) == []


def test_cross_model_three_models_same_name_aggregates_participants():
    configs = [
        _model("模型A", [{"name": "销售额", "expression": "SUM(a.amount)"}]),
        _model("模型B", [{"name": "销售额", "expression": "SUM(b.amount)"}]),
        _model("模型C", [{"name": "销售额", "expression": "SUM(c.amount)"}]),
    ]
    findings = check_cross_model_metrics(configs)
    assert len(findings) == 1
    for participant in ("模型A", "模型B", "模型C"):
        assert participant in findings[0]["targets"][0]


def test_single_model_check_never_emits_cross_model_codes():
    config = _good_config()
    config["metrics"].append({"name": "销售额", "expression": "SUM(orders.amount)", "table": "orders",
                              "description": "与首条重复口径，触发模型内 CON_DUPLICATE_METRIC_EXPRESSION"})
    report = get_semantic_quality_checker().check_config(config)
    codes = set(_codes(report))
    assert "CON_DUPLICATE_METRIC_EXPRESSION" in codes
    assert not any(code.startswith("CON_CROSS_MODEL_") for code in codes)


def test_first_structure_error_guards_empty_table_map():
    """表结构清单为空时返回同步提示文案，不进入检查。"""
    assert first_structure_error([], [], []) == "当前数据源缺少表结构信息，请先完成数据源同步"


def test_first_structure_error_returns_message_in_phase_order():
    """多条结构错误并存时按阶段序返回第一条（显示名称先于指标物理引用）。"""
    table_columns_map = {"orders": {"amount"}}
    error = first_structure_error(
        [{"name": "销售额", "table": "orders", "expression": "SUM(amount)"}],
        [{"name": "城市", "table": "missing", "column": "city"}],
        [],
        table_columns_map=table_columns_map,
    )
    assert "所属表不存在" in error


def test_first_structure_error_none_for_clean_config():
    assert first_structure_error(
        [{"name": "销售额", "table": "orders", "expression": "SUM(amount)", "description": "口径"}],
        [{"name": "城市", "table": "orders", "column": "amount"}],
        [],
        table_columns_map={"orders": {"amount"}},
    ) is None
