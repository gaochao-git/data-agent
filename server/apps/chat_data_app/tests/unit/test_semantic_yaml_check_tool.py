"""semantic_yaml_check_tool 单测：会话内 YAML 自检的四道闸与结构化返回。"""

import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.agent.tools import semantic_yaml_check
from apps.chat_data_app.agent.tools.semantic_yaml_check import create_semantic_yaml_check_tool

_DATASOURCE = {
    "ds_tables": [
        {"table_name": "orders", "columns": [{"name": "amount"}, {"name": "status"}]},
    ]
}

_SCHEMA_TABLES = [
    {"table_name": "orders", "columns": [{"name": "amount"}, {"name": "status"}]},
]


class FakeManager:
    def __init__(self, datasource=_DATASOURCE):
        self._datasource = datasource

    async def get_datasource_async(self, datasource_id):
        return self._datasource

    async def get_cached_schema_async(self, datasource_id):
        return _SCHEMA_TABLES

    async def get_column_dict_async(self, datasource_id, table_name, column_name):
        return None


def _good_yaml() -> str:
    return """
version: "1.0"
semantic_model:
  - name: 订单模型
    business_notes: 电商订单核心口径
    datasets:
      - name: orders
        source: orders
        fields:
          - name: 销售额
            expression:
              dialects:
                - dialect: ANSI_SQL
                  expression: '`amount`'
            dimension:
              is_time: false
            datatype: DECIMAL
            custom_extensions:
              - vendor_name: CHAT_DATA
                data: '{"column": "amount", "table": "orders"}'
    metrics:
      - name: 销售合计
        description: 订单金额求和
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: SUM(amount)
        datatype: DECIMAL
        custom_extensions:
          - vendor_name: CHAT_DATA
            data: '{"table": "orders"}'
"""


def _invoke(monkeypatch, yaml_text: str, datasource_id: str = "ds_a") -> dict:
    manager = FakeManager()
    monkeypatch.setattr(semantic_yaml_check, "get_datasource_manager", lambda: manager)
    raw = asyncio.run(
        create_semantic_yaml_check_tool(datasource_id).ainvoke({"yaml_content": yaml_text})
    )
    return json.loads(raw.replace("[semantic_yaml_check_tool]\n", "", 1))


def test_tool_requires_bound_datasource():
    with pytest.raises(ValueError, match="未绑定数据源"):
        asyncio.run(create_semantic_yaml_check_tool("").ainvoke({"yaml_content": "version: '1'"}))


def test_tool_missing_datasource_raises(monkeypatch):
    manager = FakeManager(datasource=None)
    monkeypatch.setattr(semantic_yaml_check, "get_datasource_manager", lambda: manager)
    with pytest.raises(ValueError, match="数据源不存在"):
        asyncio.run(create_semantic_yaml_check_tool("ds_x").ainvoke({"yaml_content": "version: '1'"}))


def test_tool_parse_failure_returns_structured_result(monkeypatch):
    payload = _invoke(monkeypatch, "not: [valid: ossie")
    assert payload["success"] is False
    assert payload["stage"] == "parse"
    assert payload["error"]
    assert payload["all_importable"] is False
    assert payload["models"] == []


def test_tool_passes_good_yaml_with_quality_report(monkeypatch):
    payload = _invoke(monkeypatch, _good_yaml())
    assert payload["success"] is True
    assert payload["stage"] == "checked"
    assert payload["all_importable"] is True
    model = payload["models"][0]
    assert model["importable"] is True
    assert model["errors"] == []
    assert model["metric_count"] == 1
    assert model["dimension_count"] == 1
    # datatype DECIMAL 经别名归一为 Decimal，schema 校验通过；只透出 findings，不输出评分
    assert "score" not in model["quality"]
    assert "grade" not in model["quality"]
    assert isinstance(model["quality"]["findings"], list)
    checklist = {row["name"]: row for row in model["checklist"]}
    assert checklist["物理字段校验"]["status"] == "pass"
    assert checklist["质量规则核对"]["status"] in ("pass", "warn")


def test_tool_blocks_unknown_physical_column(monkeypatch):
    broken = _good_yaml().replace("SUM(amount)", "SUM(amount_missing)")
    payload = _invoke(monkeypatch, broken)
    assert payload["all_importable"] is False
    model = payload["models"][0]
    assert model["importable"] is False
    assert any("必须引用表 orders 的物理字段" in error for error in model["errors"])
    assert "quality" not in model
