import json
import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from apps.chat_data_app.semantic import SemanticConfig, SemanticMatcher, SemanticValidator, SQLTranslator
from apps.chat_data_app.semantic.core.dsl import QueryMode, DimensionSpec, SemanticQuery


TEST_DATA_DIR = os.path.join(os.path.dirname(__file__), "dsl2sql_tests")


def load_semantic_configs():
    config_path = os.path.join(TEST_DATA_DIR, "semantic_configs.json")
    with open(config_path, "r", encoding="utf-8") as file:
        return json.load(file)


SEMANTIC_CONFIGS = load_semantic_configs()


def normalize_relationships_config(config_name: str) -> dict:
    raw = dict(SEMANTIC_CONFIGS[config_name])
    if "id" not in raw:
        raw["id"] = config_name
    if "name" not in raw:
        raw["name"] = config_name
    if "joins" in raw and "relationships" not in raw:
        raw["relationships"] = raw["joins"]
    relationships = []
    for rel in raw.get("relationships", []):
        rel_item = dict(rel)
        if not rel_item.get("join_conditions"):
            left_col = rel_item.get("left_column")
            right_col = rel_item.get("right_column")
            if left_col and right_col:
                rel_item["join_conditions"] = [{"left_column": left_col, "right_column": right_col}]
        relationships.append(rel_item)
    raw["relationships"] = relationships
    return raw


def build_config(config_name: str) -> SemanticConfig:
    return SemanticConfig.model_validate(normalize_relationships_config(config_name))


def test_semantic_matcher_accepts_raw_field_metrics_in_detail_mode():
    matcher = SemanticMatcher(build_config("detail_three_table_config"))

    dsl = matcher._build_dsl_from_response(
        {
            "metrics": ["集群名称", "数据库类型"],
            "dimensions": [],
            "filters": [],
            "having": [],
            "order_by": [],
        },
        query="查看集群名称和数据库类型列表",
    )

    assert dsl.mode == QueryMode.DETAIL
    assert dsl.metrics == ["集群名称", "数据库类型"]


def test_semantic_matcher_accepts_time_grain_key():
    matcher = SemanticMatcher(build_config("ecommerce_config"))

    dsl = matcher._build_dsl_from_response(
        {
            "metrics": ["订单总金额"],
            "dimensions": [{"name": "下单日期", "time_grain": "month"}],
            "filters": [],
            "having": [],
            "order_by": [],
        },
        query="按月统计订单总金额",
    )

    assert dsl.mode == QueryMode.AGGREGATE
    assert len(dsl.dimensions) == 1
    assert isinstance(dsl.dimensions[0], DimensionSpec)
    assert dsl.dimensions[0].grain == "month"


def test_semantic_validator_and_translator_support_detail_raw_field_metrics():
    config = build_config("detail_three_table_config")
    query = SemanticQuery(
        metrics=["集群名称", "数据库类型"],
        filters=[{"dimension": "实例状态", "operator": "=", "value": "running"}],
        mode=QueryMode.DETAIL,
    )

    validation = SemanticValidator(config).validate(query)
    assert validation.is_valid, validation.to_dict()

    result = SQLTranslator(config, dialect="mysql").translate(query)
    assert result.success, result.error
    assert "cluster_name" in result.sql
    assert "db_type" in result.sql
    assert "GROUP BY" not in result.sql
