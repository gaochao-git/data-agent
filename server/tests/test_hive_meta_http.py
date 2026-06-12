import os
import sys

import pytest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1")
os.environ.setdefault("FXCJG_DB_PORT", "3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.services import hive_meta_http


def test_normalize_table_names_accepts_object_items():
    tables = [
        {"table_name": "ods_pay_org_general_ledger_transfer_detail_incr_d", "table_description": "非现场监管-支付机构分类账明细表"},
        {"table_name": "ods_special_merchant_info_incr_m", "table_description": ""},
    ]

    assert hive_meta_http._normalize_table_items(tables) == [
        {"table_name": "ods_pay_org_general_ledger_transfer_detail_incr_d", "description": "非现场监管-支付机构分类账明细表"},
        {"table_name": "ods_special_merchant_info_incr_m", "description": ""},
    ]


def test_normalize_schema_tables_uses_type_as_default_hive_type():
    tables = [{
        "table_name": "ods_pay_org_general_ledger_transfer_detail_incr_d",
        "external_table_extra": "ignored",
        "columns": [{
            "is_partition": False,
            "name": "trans_date",
            "type": "string",
            "external_column_extra": "ignored",
        }],
    }]

    normalized = hive_meta_http._normalize_schema_tables(
        tables,
        {"ods_pay_org_general_ledger_transfer_detail_incr_d": "非现场监管-支付机构分类账明细表"},
    )

    assert normalized[0]["description"] == "非现场监管-支付机构分类账明细表"
    assert normalized[0]["columns"][0]["type"] == "string"
    assert normalized[0]["columns"][0]["hive_type"] == "string"
    assert normalized[0]["columns"][0]["description"] == "trans_date"
    assert normalized[0]["columns"][0]["primary_key"] is False
    assert normalized[0]["columns"][0]["nullable"] is True
    assert normalized[0]["columns"][0]["is_partition"] is False
    assert "external_table_extra" not in normalized[0]
    assert "external_column_extra" not in normalized[0]["columns"][0]


def test_normalize_schema_tables_rejects_string_partition_flag():
    tables = [{
        "table_name": "orders",
        "columns": [{
            "is_partition": "false",
            "name": "order_id",
            "type": "string",
        }],
    }]

    with pytest.raises(RuntimeError, match="is_partition 必须是布尔值"):
        hive_meta_http._normalize_schema_tables(tables)


def test_normalize_table_items_rejects_non_object_items():
    with pytest.raises(RuntimeError, match="第 1 项不是对象"):
        hive_meta_http._normalize_table_items(["orders"])
