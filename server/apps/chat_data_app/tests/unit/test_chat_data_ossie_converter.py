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
        "business_notes": "用户下单后生成订单，支付成功后计入有效成交。",
        "update_time": "2026-08-05 10:00:00",
        "metrics": [{"name": "销售额", "expression": "SUM(orders.amount)", "table": "orders"}],
        "dimensions": [{"name": "城市", "table": "orders", "column": "city", "logical_type": "string"}],
        "relationships": [
            {
                "left_table": "orders",
                "right_table": "users",
                "relationship_type": relationship_type,
                "join_type": "left",
                "join_conditions": [{"left_column": "user_id", "right_column": "id"}],
            }
        ],
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
    from ossie import OssieDocument

    document = yaml.safe_load(ChatDataToOssieConverter().convert(_config()))
    parsed = OssieDocument.model_validate(document)

    assert parsed.name == "订单分析"
    assert parsed.ai_context == "用户下单后生成订单，支付成功后计入有效成交。"
    assert parsed.metrics[0].expression.dialects[0].expression == "SUM(orders.amount)"
    assert parsed.relationships[0].from_dataset == "orders"
    assert parsed.relationships[0].to == "users"
    dataset = document["datasets"][0]
    assert dataset["primary_key"] == []
    assert dataset["unique_keys"] == [[]]
    assert dataset["fields"]


def test_convert_passes_official_json_schema():
    from jsonschema import validate

    schema_path = Path(__file__).parents[1] / "fixtures" / "ossie" / "osi-schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    document = yaml.safe_load(ChatDataToOssieConverter().convert(_config("one_to_many")))

    validate(document, schema)
    relationship = document["relationships"][0]
    assert document["ai_context"] == "用户下单后生成订单，支付成功后计入有效成交。"
    assert relationship["from"] == "users"
    assert relationship["to"] == "orders"
    assert all(dataset["primary_key"] == [] for dataset in document["datasets"])
    assert all(dataset["unique_keys"] == [[]] for dataset in document["datasets"])
    assert "  - []" in ChatDataToOssieConverter().convert(_config("one_to_many"))


def test_convert_maps_physical_and_manual_table_keys():
    content = ChatDataToOssieConverter().convert(_config_with_indexes())
    document = yaml.safe_load(content)
    datasets = {dataset["name"]: dataset for dataset in document["datasets"]}

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
        "dimensions": [
            {
                "name": "类别ID",
                "table": "categories",
                "column": "category_id",
                "field_name": "category_id",
                "logical_type": "number",
                "mapping": {"一级": "1"},
                "allowed_values": ["一级"],
                "allowed_operators": ["=", "IN"],
            }
        ],
        "relationships": [],
        "tables": [
            {
                "table_name": "categories",
                "columns": [
                    {
                        "name": "category_id",
                        "type": "int(11)",
                        "mysql_type": "int(11)",
                        "description": "id",
                        "primary_key": True,
                        "unique_key": False,
                        "index_key": False,
                        "nullable": False,
                    }
                ],
            }
        ],
    }

    document = yaml.safe_load(ChatDataToOssieConverter().convert(config))
    field = document["datasets"][0]["fields"][0]
    extension_data = json.loads(field["custom_extensions"][0]["data"])

    assert field["name"] == "类别ID"
    assert field["expression"]["dialects"][0]["expression"] == "category_id"
    assert field["datatype"] == "Integer"
    # field_name 等于物理列时是冗余信息，不写入扩展
    assert extension_data == {"table": "categories", "column": "category_id"}


def test_convert_falls_back_to_logical_type_without_physical_column_type():
    document = yaml.safe_load(ChatDataToOssieConverter().convert(_config()))
    orders = next(dataset for dataset in document["datasets"] if dataset["name"] == "orders")

    assert orders["fields"][0]["datatype"] == "String"


def test_invalid_relationship_type_is_rejected():
    with pytest.raises(ValueError, match="unsupported_cardinality"):
        ChatDataToOssieConverter().convert(_config("unsupported_cardinality"))


def test_missing_config_id_fails():
    with pytest.raises(ValueError, match="semantic_config_id"):
        ChatDataToOssieConverter().convert({"name": "missing"})


def test_convert_writes_field_name_and_table_alias_into_extension():
    """field_name / table_alias 必须写入扩展，否则导出→导入往返后静默丢失。"""
    config = _config()
    config["metrics"][0].update({"field_name": "sales_amount", "table_alias": "o"})
    config["dimensions"][0].update({"field_name": "city_name", "table_alias": "o"})

    document = yaml.safe_load(ChatDataToOssieConverter().convert(config))

    metric_ext = json.loads(document["metrics"][0]["custom_extensions"][0]["data"])
    dimension_ext = json.loads(
        document["datasets"][0]["fields"][0]["custom_extensions"][0]["data"]
    )
    assert metric_ext["field_name"] == "sales_amount"
    assert metric_ext["table_alias"] == "o"
    assert dimension_ext["field_name"] == "city_name"
    assert dimension_ext["table_alias"] == "o"


def test_convert_omits_empty_field_name_and_table_alias():
    """空值不写扩展键：保持既有扩展内容最小化，旧断言行为不变。"""
    document = yaml.safe_load(ChatDataToOssieConverter().convert(_config()))
    dimension_ext = json.loads(
        document["datasets"][0]["fields"][0]["custom_extensions"][0]["data"]
    )
    assert "field_name" not in dimension_ext
    assert "table_alias" not in dimension_ext


def test_convert_omits_field_name_equal_to_column_and_alias_equal_to_table():
    """field_name==column、table_alias==table 时是默认值冗余，不写扩展，避免 diff 噪音。"""
    config = _config()
    # fixture 的 metric 无 column 键，补上才能构造「field_name==column」的默认值冗余场景
    config["metrics"][0].update({"column": "amount", "field_name": "amount", "table_alias": "orders"})
    config["dimensions"][0].update({"field_name": "city", "table_alias": "orders"})

    document = yaml.safe_load(ChatDataToOssieConverter().convert(config))

    metric_ext = json.loads(document["metrics"][0]["custom_extensions"][0]["data"])
    dimension_ext = json.loads(
        document["datasets"][0]["fields"][0]["custom_extensions"][0]["data"]
    )
    assert metric_ext == {"table": "orders", "column": "amount"}
    assert dimension_ext == {"table": "orders", "column": "city"}


def _config_with_physical_types():
    config = _config()
    config["tables"] = [
        {
            "table_name": "orders",
            "columns": [
                {"name": "city", "mysql_type": "varchar(64)"},
                {"name": "amount", "mysql_type": "longtext"},
                {"name": "status", "mysql_type": "varchar(16)"},
            ],
        }
    ]
    return config


def test_convert_maps_longtext_physical_type_to_string():
    """物理列 longtext/mediumtext/tinytext/json 必须映射 String，而不是 Opaque。"""
    config = _config_with_physical_types()
    config["dimensions"][0]["column"] = "amount"

    document = yaml.safe_load(ChatDataToOssieConverter().convert(config))
    field = document["datasets"][0]["fields"][0]
    assert field["datatype"] == "String"


def test_convert_writes_logical_type_into_extension_only_when_lossy():
    """logical_type 能由 datatype 还原（text→String）时不写扩展；enum 必须写扩展。"""
    config = _config_with_physical_types()
    config["dimensions"][0] = {
        "name": "状态",
        "table": "orders",
        "column": "status",
        "logical_type": "enum",
    }

    document = yaml.safe_load(ChatDataToOssieConverter().convert(config))
    field = document["datasets"][0]["fields"][0]
    extension = json.loads(field["custom_extensions"][0]["data"])
    assert extension["logical_type"] == "enum"

    # text + varchar → String 可无损还原，不写扩展（避免噪音）
    config["dimensions"][0] = {
        "name": "城市",
        "table": "orders",
        "column": "city",
        "logical_type": "text",
    }
    document = yaml.safe_load(ChatDataToOssieConverter().convert(config))
    field = document["datasets"][0]["fields"][0]
    extension = json.loads(field["custom_extensions"][0]["data"])
    assert "logical_type" not in extension


def test_convert_writes_data_type_into_extension_only_when_lossy():
    """metric data_type 是自由文本：decimal(10,2) 写扩展，int 可还原不写。"""
    config = _config()
    config["metrics"] = [
        {"name": "金额", "expression": "SUM(amount)", "table": "orders", "data_type": "decimal(10,2)"},
        {"name": "笔数", "expression": "COUNT(1)", "table": "orders", "data_type": "int"},
    ]

    document = yaml.safe_load(ChatDataToOssieConverter().convert(config))
    metrics = document["metrics"]
    lossy_ext = json.loads(metrics[0]["custom_extensions"][0]["data"])
    assert lossy_ext["data_type"] == "decimal(10,2)"
    lossless = metrics[1]
    assert "data_type" not in json.loads(lossless["custom_extensions"][0]["data"])


def test_convert_records_dimension_order_for_round_trip():
    """维度平铺顺序在 dataset→field 结构里没有位置，写入模型扩展供导入侧还原保序。"""
    config = _config()
    config["dimensions"] = [
        {"name": "城市", "table": "orders", "column": "city", "logical_type": "string"},
        {"name": "昵称", "table": "users", "column": "nickname", "logical_type": "string"},
        {"name": "状态", "table": "orders", "column": "status", "logical_type": "string"},
    ]
    document = yaml.safe_load(ChatDataToOssieConverter().convert(config))
    ext = json.loads(document["custom_extensions"][0]["data"])
    assert ext["dimension_order"] == ["orders.city", "users.nickname", "orders.status"]
