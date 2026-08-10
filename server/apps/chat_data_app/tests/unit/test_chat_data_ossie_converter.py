import json
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parents[4]))

from apps.chat_data_app.services.semantic.run.chat_data_ossie_converter import ChatDataToOssieConverter


def _config(relationship_type="many_to_one"):
    return {
        "id": "sc_order",
        "name": "订单分析",
        "description": "订单经营分析",
        "ontology_rules": "用户下单后生成订单，支付成功后计入有效成交。",
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


def _config_with_indexes():
    config = _config()
    config["tables"] = [
        {
            "table_name": "orders",
            "primary_keys": [["order_id"]],
            "unique_indexes": [
                {"name": "uniq_order_no", "columns": ["order_no"]},
                {"name": "uniq_tenant_order", "columns": ["tenant_id", "order_no"]},
            ],
            "manual_indexes": [
                {"index_type": "primary", "index_name": "PRIMARY", "columns": ["manual_id"]},
                {"index_type": "unique", "index_name": "uniq_order_no_manual", "columns": ["order_no"]},
                {"index_type": "unique", "index_name": "uniq_external_id", "columns": ["external_id"]},
                {"index_type": "secondary", "index_name": "idx_city", "columns": ["city"]},
            ],
        },
        {
            "table_name": "users",
            "primary_keys": [],
            "unique_indexes": [],
            "manual_indexes": [
                {"index_type": "primary", "index_name": "PRIMARY", "columns": ["id"]},
                {"index_type": "unique", "index_name": "uniq_mobile", "columns": ["mobile"]},
            ],
        },
    ]
    return config


def test_convert_is_valid_ossie_pydantic_document():
    from ossie import OSIDocument

    document = yaml.safe_load(ChatDataToOssieConverter().convert(_config()))
    parsed = OSIDocument.model_validate(document)

    assert parsed.semantic_model[0].name == "订单分析"
    assert parsed.semantic_model[0].ai_context == "用户下单后生成订单，支付成功后计入有效成交。"
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
    assert document["semantic_model"][0]["ai_context"] == "用户下单后生成订单，支付成功后计入有效成交。"
    assert relationship["from"] == "users"
    assert relationship["to"] == "orders"
    assert all(dataset["primary_key"] == [] for dataset in document["semantic_model"][0]["datasets"])
    assert all(dataset["unique_keys"] == [[]] for dataset in document["semantic_model"][0]["datasets"])
    assert "  - []" in ChatDataToOssieConverter().convert(_config("one_to_many"))


def test_convert_maps_physical_and_manual_table_keys():
    content = ChatDataToOssieConverter().convert(_config_with_indexes())
    document = yaml.safe_load(content)
    datasets = {
        dataset["name"]: dataset
        for dataset in document["semantic_model"][0]["datasets"]
    }

    assert datasets["orders"]["primary_key"] == ["order_id"]
    assert datasets["orders"]["unique_keys"] == [
        ["order_no"],
        ["tenant_id", "order_no"],
        ["external_id"],
    ]
    assert datasets["users"]["primary_key"] == ["id"]
    assert datasets["users"]["unique_keys"] == [["mobile"]]
    assert "primary_key: [order_id]" in content
    assert "  - [order_no]" in content
    assert "  - [tenant_id, order_no]" in content


def test_convert_uses_physical_column_type_and_minimal_field_extension():
    config = {
        "id": "sc_category",
        "name": "产品分类",
        "metrics": [],
        "dimensions": [{
            "name": "类别ID",
            "table": "categories",
            "column": "category_id",
            "field_name": "category_id",
            "logical_type": "number",
            "mapping": {"一级": "1"},
            "allowed_values": ["一级"],
            "allowed_operators": ["=", "IN"],
        }],
        "relationships": [],
        "tables": [{
            "table_name": "categories",
            "columns": [{
                "name": "category_id",
                "type": "int(11)",
                "mysql_type": "int(11)",
                "description": "id",
                "primary_key": True,
                "unique_key": False,
                "index_key": False,
                "nullable": False,
            }],
        }],
    }

    document = yaml.safe_load(ChatDataToOssieConverter().convert(config))
    field = document["semantic_model"][0]["datasets"][0]["fields"][0]
    extension_data = json.loads(field["custom_extensions"][0]["data"])

    assert field["name"] == "类别ID"
    assert field["expression"]["dialects"][0]["expression"] == "category_id"
    assert field["datatype"] == "Integer"
    assert extension_data == {"table": "categories", "column": "category_id"}


def test_convert_falls_back_to_logical_type_without_physical_column_type():
    document = yaml.safe_load(ChatDataToOssieConverter().convert(_config()))
    orders = next(
        dataset
        for dataset in document["semantic_model"][0]["datasets"]
        if dataset["name"] == "orders"
    )

    assert orders["fields"][0]["datatype"] == "String"


def test_invalid_relationship_type_is_rejected():
    with pytest.raises(ValueError, match="unsupported_cardinality"):
        ChatDataToOssieConverter().convert(_config("unsupported_cardinality"))


def test_missing_config_id_fails():
    with pytest.raises(ValueError, match="semantic_config_id"):
        ChatDataToOssieConverter().convert({"name": "missing"})
