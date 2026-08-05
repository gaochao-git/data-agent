import json
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parents[4]))

from apps.chat_data_app.services.chat_data_ossie_converter import ChatDataToOssieConverter


def _config(relationship_type="many_to_one"):
    return {
        "id": "sc_order",
        "name": "订单分析",
        "description": "订单经营分析",
        "update_time": "2026-08-05 10:00:00",
        "metrics": [{"name": "销售额", "expression": "SUM(orders.amount)", "table": "orders"}],
        "dimensions": [{"name": "城市", "table": "orders", "column": "city", "logical_type": "string"}],
        "relationships": [{
            "left_table": "orders",
            "right_table": "users",
            "relationship_type": relationship_type,
            "join_type": "left",
            "join_conditions": [{"left_column": "user_id", "right_column": "id"}],
        }],
    }


def test_convert_is_valid_ossie_pydantic_document():
    from ossie import OSIDocument

    document = yaml.safe_load(ChatDataToOssieConverter().convert(_config()))
    parsed = OSIDocument.model_validate(document)

    assert parsed.semantic_model[0].name == "订单分析"
    assert parsed.semantic_model[0].metrics[0].expression.dialects[0].expression == "SUM(orders.amount)"
    assert parsed.semantic_model[0].relationships[0].from_dataset == "orders"
    assert parsed.semantic_model[0].relationships[0].to == "users"
    dataset = document["semantic_model"][0]["datasets"][0]
    assert dataset["primary_key"] == []
    assert dataset["unique_keys"] == [[]]
    assert dataset["fields"]


def test_convert_passes_official_json_schema():
    from jsonschema import validate

    schema_path = Path(__file__).parents[1] / "fixtures" / "ossie" / "osi-schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    document = yaml.safe_load(ChatDataToOssieConverter().convert(_config("one_to_many")))

    validate(document, schema)
    relationship = document["semantic_model"][0]["relationships"][0]
    assert relationship["from"] == "users"
    assert relationship["to"] == "orders"
    assert all(dataset["primary_key"] == [] for dataset in document["semantic_model"][0]["datasets"])
    assert all(dataset["unique_keys"] == [[]] for dataset in document["semantic_model"][0]["datasets"])


def test_invalid_relationship_type_is_rejected():
    with pytest.raises(ValueError, match="unsupported_cardinality"):
        ChatDataToOssieConverter().convert(_config("unsupported_cardinality"))


def test_missing_config_id_fails():
    with pytest.raises(ValueError, match="semantic_config_id"):
        ChatDataToOssieConverter().convert({"name": "missing"})
