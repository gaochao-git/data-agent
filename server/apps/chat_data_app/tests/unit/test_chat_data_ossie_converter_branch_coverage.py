"""chat_data_ossie_converter 纯函数/类方法的分支覆盖补测。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))

from apps.chat_data_app.services.semantic.run.chat_data_ossie_converter import (
    ChatDataToOssieConverter,
    _datatype,
    _extension,
    _key_columns,
    _wrap_ossie_key_lists,
)


def test_datatype_map_prefix_and_opaque_fallback():
    assert _datatype("varchar(255)") == "String"
    assert _datatype("BIGINT") == "Integer"
    # 遍历完整张映射表仍无匹配 → Opaque（65->68）
    assert _datatype("json") == "Opaque"
    assert _datatype("") is None


def test_extension_empty_data_returns_none():
    assert _extension({}) is None
    assert _extension({"semantic_config_id": "c1"}) is not None


def test_key_columns_non_list_and_dedup():
    assert _key_columns("id") == []
    assert _key_columns(None) == []
    # 空名与重复名都被跳过（102->100）
    assert _key_columns(["id", "", "id", "name"]) == ["id", "name"]


def test_table_names_skips_invalid_items():
    converter = ChatDataToOssieConverter()
    config = {
        "semantic_config_id": "cfg-1",
        "metrics": ["not-dict", {"table": "orders"}],
        "dimensions": [{"table": "orders"}, {"name": "no-table"}],
        "relationships": ["bad", {"left_table": "", "right_table": "customers"}, {"left_table": "customers"}],
    }
    # 非 dict 指标（246->245）、非 dict 关系（251->252）、空表名/重复表名都被跳过
    assert converter._table_names(config) == ["orders", "customers"]


def test_table_names_empty_falls_back_to_config_id():
    converter = ChatDataToOssieConverter()
    assert converter._table_names({"semantic_config_id": "cfg-9"}) == ["cfg-9"]


def test_wrap_ossie_key_lists_without_keys_keeps_dataset_untouched():
    data = {"semantic_model": [{"datasets": [{"name": "t1"}]}, {}]}
    _wrap_ossie_key_lists(data)
    assert data["semantic_model"][0]["datasets"][0] == {"name": "t1"}


def test_collect_unique_keys_dedups_and_skips_invalid():
    table_config = {
        "unique_indexes": [{"columns": ["a"]}, {"columns": ["a"]}, "bad", {"columns": []}],
        "manual_indexes": [
            {"index_type": "unique", "columns": ["a"]},
            {"index_type": "unique", "columns": ["b"]},
            {"index_type": "primary", "columns": ["c"]},
        ],
    }
    manual = ChatDataToOssieConverter._manual_indexes(table_config)
    # 物理唯一索引去重（339->337）、手工唯一索引追加、重复/primary/坏项跳过
    assert ChatDataToOssieConverter._collect_unique_keys(table_config, manual) == [["a"], ["b"]]


def test_dimension_datatype_fallback_without_column():
    # 缺物理列名 → 直接按 logical_type 兜底（361->381）
    assert ChatDataToOssieConverter._dimension_datatype(
        {"tables": []}, {"table": "orders", "logical_type": "date"}
    ) == "Date"


def test_dimension_datatype_scans_tables_and_columns():
    config = {
        "tables": [
            "bad-table",  # tables 里非 dict 项（363->364）
            {"table": "orders", "columns": ["bad-col", {"name": "city"}, {"name": "other"}]},
            {"table": "orders", "table_alias": "o", "columns": [{"name": "city", "mysql_type": "varchar(64)"}]},
        ]
    }
    dimension = {"table": "orders", "column": "city", "logical_type": "date"}
    # 非 dict 列（372->373）、无物理类型的列（379->371）、名字不匹配的列，最终命中第二个表的 varchar
    assert ChatDataToOssieConverter._dimension_datatype(config, dimension) == "String"


def test_dataset_fields_skip_empty_name_or_expression():
    converter = ChatDataToOssieConverter()
    # name/column/expression 全空 → name 与 expression 都为空 → continue（291->292）
    config = {"dimensions": [{"table": "orders"}]}
    assert converter._dataset_fields(config, "orders", None, None, None, None, None, None) == []


def test_metrics_skips_invalid_items():
    converter = ChatDataToOssieConverter()
    config = {
        "metrics": [
            "bad",            # 非 dict（386->387）
            {"name": ""},     # name/expression 全空（390->391）
        ]
    }
    # 只走 continue 分支，不会触达 metric_type 构造，传 None 安全
    assert converter._metrics(config, None, None, None, None, None) == []


def test_relationships_skips_invalid_items():
    converter = ChatDataToOssieConverter()
    config = {
        "semantic_config_id": "cfg-1",
        "relationships": [
            "bad",                      # 非 dict（405->406）
            {"left_table": "a"},        # 表/条件不全（410->411）
            {"left_table": "a", "right_table": "b", "join_conditions": [{}]},  # 条件全被过滤（410->411）
            {"left_table": "a", "right_table": "b", "join_conditions": [{"left_column": "id"}]},  # 方向列不全（413->414）
        ],
    }
    # 只走 continue 分支，不会触达 relationship_type 构造，传 None 安全
    assert converter._relationships(config, None, None) == []
