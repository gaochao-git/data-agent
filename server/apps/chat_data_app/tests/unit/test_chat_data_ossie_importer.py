import asyncio
import json
import os
import sys
from types import SimpleNamespace

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from utils.error_codes import ErrorCode

from ossie import OSIDataType

from apps.chat_data_app.services.semantic.run.chat_data_ossie_converter import ChatDataToOssieConverter
from apps.chat_data_app.services.semantic.run.chat_data_ossie_importer import (
    OssieSemanticModelImport,
    OssieToChatDataImporter,
    build_model_checklist,
    parse_ossie_document,
)


def _full_config() -> dict:
    """含原子/派生/复合指标、多表关系与键信息的完整语义配置（round-trip 基准）。"""
    return {
        "id": "sc_order_full",
        "name": "订单分析",
        "description": "订单经营分析",
        "business_notes": "用户下单后生成订单，支付成功后计入有效成交。",
        "update_time": "2026-09-17 10:00:00",
        "source_type": "mysql",
        "metrics": [
            {"name": "销售额", "expression": "SUM(orders.amount)", "table": "orders"},
            {"name": "订单数", "expression": "COUNT(DISTINCT orders.order_id)", "table": "orders"},
            {
                "name": "本月成交额",
                "metric_kind": "derived",
                "base_metric": "销售额",
                "period": {"kind": "this_month", "time_dimension": "下单时间"},
                "filters": [{"dimension": "订单状态", "operator": "=", "value": "已支付"}],
            },
            {
                "name": "客单价",
                "metric_kind": "composite",
                "formula": "{销售额} / {订单数}",
            },
        ],
        "dimensions": [
            {"name": "下单时间", "column": "order_time", "table": "orders", "logical_type": "datetime",
             "expression": "`order_time`", "description": "订单下单时间"},
            {"name": "订单状态", "column": "status", "table": "orders", "logical_type": "enum",
             "expression": "`status`"},
            {"name": "用户昵称", "column": "nickname", "table": "users", "logical_type": "text",
             "expression": "`nickname`"},
        ],
        "relationships": [
            {
                "left_table": "orders",
                "right_table": "users",
                "relationship_type": "many_to_one",
                "join_type": "left",
                "join_conditions": [{"left_column": "user_id", "right_column": "id"}],
            },
            {
                "left_table": "users",
                "right_table": "orders",
                "relationship_type": "one_to_many",
                "join_type": "inner",
                "join_conditions": [{"left_column": "id", "right_column": "user_id"},
                                    {"left_column": "tenant_id", "right_column": "tenant_id"}],
            },
        ],
        "tables": [
            {
                "table_name": "orders",
                "columns": [
                    {"name": "amount", "mysql_type": "decimal(10,2)"},
                    {"name": "order_id", "mysql_type": "bigint"},
                    {"name": "order_time", "mysql_type": "datetime"},
                    {"name": "status", "mysql_type": "varchar(32)"},
                    {"name": "user_id", "mysql_type": "bigint"},
                    {"name": "tenant_id", "mysql_type": "bigint"},
                ],
                "primary_keys": [["order_id"]],
                "unique_indexes": [],
                "manual_indexes": [],
            },
            {
                "table_name": "users",
                "columns": [
                    {"name": "id", "mysql_type": "bigint"},
                    {"name": "nickname", "mysql_type": "varchar(64)"},
                    {"name": "tenant_id", "mysql_type": "bigint"},
                ],
                "primary_keys": [["id"]],
                "unique_indexes": [],
                "manual_indexes": [],
            },
        ],
    }


def test_round_trip_full_config_preserves_semantics():
    """导出→导入 round-trip：正向保留的信息必须无损还原。"""
    original = _full_config()
    exported = ChatDataToOssieConverter().convert(original, source_type="mysql")

    imports = OssieToChatDataImporter().import_yaml(exported)
    assert len(imports) == 1
    result = imports[0]
    assert result.importable, result.errors
    assert result.name == "订单分析"
    assert result.description == "订单经营分析"
    assert result.business_notes == "用户下单后生成订单，支付成功后计入有效成交。"
    assert result.source_config_id == "sc_order_full"

    metrics = {metric["name"]: metric for metric in result.metrics}
    assert metrics["销售额"]["expression"] == "SUM(orders.amount)"
    assert metrics["销售额"]["table"] == "orders"
    assert "metric_kind" not in metrics["销售额"]

    derived = metrics["本月成交额"]
    assert derived["metric_kind"] == "derived"
    assert derived["base_metric"] == "销售额"
    assert derived["expression"] == ""
    assert derived["period"] == {"kind": "this_month", "time_dimension": "下单时间"}
    assert derived["filters"] == [{"dimension": "订单状态", "operator": "=", "value": "已支付"}]

    composite = metrics["客单价"]
    assert composite["metric_kind"] == "composite"
    assert composite["formula"] == "{销售额} / {订单数}"
    assert composite["expression"] == ""

    dimensions = {dimension["name"]: dimension for dimension in result.dimensions}
    assert dimensions["下单时间"]["table"] == "orders"
    assert dimensions["下单时间"]["column"] == "order_time"
    assert dimensions["下单时间"]["expression"] == "`order_time`"
    # 导出只携带 is_time 与物理类型派生的 datatype，时间维度可无损还原
    assert dimensions["下单时间"]["logical_type"] == "datetime"
    assert dimensions["下单时间"]["description"] == "订单下单时间"
    # 枚举逻辑类型无法由 datatype 还原，导出写入 CHAT_DATA 扩展，导入原样还原
    assert dimensions["订单状态"]["logical_type"] == "enum"

    relationships = result.relationships
    assert relationships[0] == {
        "left_table": "orders",
        "right_table": "users",
        "join_conditions": [{"left_column": "user_id", "right_column": "id"}],
        "relationship_type": "many_to_one",
        "join_type": "left",
    }
    # one_to_many 导出时反转过方向，导入必须还原原始左右表与字段
    assert relationships[1] == {
        "left_table": "users",
        "right_table": "orders",
        "join_conditions": [{"left_column": "id", "right_column": "user_id"},
                            {"left_column": "tenant_id", "right_column": "tenant_id"}],
        "relationship_type": "one_to_many",
        "join_type": "inner",
    }


def test_import_ai_generated_document_without_extensions_single_dataset():
    """AI 从零生成的文档（无 CHAT_DATA 扩展）：单 dataset 时推断表归属与物理字段。"""
    document = """
version: "1.0"
semantic_model:
  - name: 商品模型
    description: 商品
    ai_context: 商品基础信息
    datasets:
      - name: products
        source: products
        primary_key: []
        unique_keys: [[]]
        fields:
          - name: 商品名称
            expression:
              dialects:
                - dialect: ANSI_SQL
                  expression: '`product_name`'
            datatype: String
    metrics:
      - name: 商品数
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: COUNT(product_id)
        datatype: Integer
"""
    result = OssieToChatDataImporter().import_yaml(document)[0]
    assert result.importable, result.errors
    assert result.dimensions[0]["table"] == "products"
    assert result.dimensions[0]["column"] == "product_name"
    assert result.dimensions[0]["logical_type"] == "text"
    assert result.metrics[0]["table"] == "products"
    assert result.metrics[0]["expression"] == "COUNT(product_id)"
    assert result.metrics[0]["data_type"] == "int"
    assert any("唯一 dataset" in warning for warning in result.warnings)
    assert any("物理字段" in warning for warning in result.warnings)


def test_import_multi_dataset_without_extension_reports_error():
    """多 dataset 且无扩展：不允许猜测归属表，必须记入 errors 阻断。"""
    document = """
version: "1.0"
semantic_model:
  - name: 双表模型
    datasets:
      - name: orders
        source: orders
        fields: []
      - name: users
        source: users
        fields: []
    metrics:
      - name: 总额
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: SUM(amount)
"""
    result = OssieToChatDataImporter().import_yaml(document)[0]
    assert not result.importable
    assert any("无法确定归属表" in error for error in result.errors)


def test_import_version_float_and_non_ansi_dialect():
    """version 数字容错；仅非 ANSI_SQL 方言时阻断。"""
    document = """
version: 1.0
semantic_model:
  - name: 方言模型
    datasets:
      - name: t
        source: t
        fields:
          - name: 字段
            expression:
              dialects:
                - dialect: SNOWFLAKE
                  expression: col
    metrics: []
"""
    result = OssieToChatDataImporter().import_yaml(document)[0]
    assert not result.importable
    assert any("ANSI_SQL" in error for error in result.errors)


def test_atomic_metric_with_complex_expression_imports_cleanly():
    """复杂表达式原子指标可正常导入；指标管理区展示全部指标，
    不再有“不会显示在字段配置表格”的警告（EXE_NON_SIMPLE_AGG 已删除）。"""
    document = """
version: "1.0"
semantic_model:
  - name: 表达式模型
    datasets:
      - name: t
        source: t
        fields: []
    metrics:
      - name: 复杂指标
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: COUNT(CASE WHEN status = 'ok' THEN 1 END)
      - name: 简单指标
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: COUNT(DISTINCT `order_id`)
"""
    result = OssieToChatDataImporter().import_yaml(document)[0]
    assert result.importable
    assert not any("不会显示在字段配置表格" in warning for warning in result.warnings)


def test_import_derived_missing_base_metric_is_blocked():
    document = """
version: "1.0"
semantic_model:
  - name: 残缺模型
    datasets:
      - name: t
        source: t
        fields: []
    metrics:
      - name: 派生指标
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: SUM(a)
        custom_extensions:
          - vendor_name: CHAT_DATA
            data: '{"metric_kind": "derived"}'
"""
    result = OssieToChatDataImporter().import_yaml(document)[0]
    assert not result.importable
    assert any("base_metric" in error for error in result.errors)


def test_import_composite_missing_formula_is_blocked():
    document = """
version: "1.0"
semantic_model:
  - name: 残缺模型
    datasets:
      - name: t
        source: t
        fields: []
    metrics:
      - name: 复合指标
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: SUM(a)
        custom_extensions:
          - vendor_name: CHAT_DATA
            data: '{"metric_kind": "composite"}'
"""
    result = OssieToChatDataImporter().import_yaml(document)[0]
    assert not result.importable
    assert any("formula" in error for error in result.errors)


def test_import_relationship_without_extension_defaults_direction():
    """外部文件的关系无扩展：按 from→to 导入并警告，不猜方向。"""
    document = """
version: "1.0"
semantic_model:
  - name: 外部模型
    datasets:
      - name: orders
        source: orders
        fields: []
      - name: users
        source: users
        fields: []
    relationships:
      - name: r1
        from: orders
        to: users
        from_columns: [user_id]
        to_columns: [id]
"""
    result = OssieToChatDataImporter().import_yaml(document)[0]
    assert result.relationships == [{
        "left_table": "orders",
        "right_table": "users",
        "join_conditions": [{"left_column": "user_id", "right_column": "id"}],
        "relationship_type": "one_to_one",
        "join_type": "left",
    }]
    assert any("方向" in warning for warning in result.warnings)


def test_import_relationship_with_unsupported_cardinality_is_blocked():
    document = """
version: "1.0"
semantic_model:
  - name: 基数模型
    datasets:
      - name: a
        source: a
        fields: []
      - name: b
        source: b
        fields: []
    relationships:
      - name: r1
        from: a
        to: b
        from_columns: [id]
        to_columns: [id]
        custom_extensions:
          - vendor_name: CHAT_DATA
            data: '{"relationship_type": "many_to_many", "join_type": "left", "left_table": "a", "right_table": "b"}'
"""
    result = OssieToChatDataImporter().import_yaml(document)[0]
    assert not result.importable
    assert any("不支持的语义关系基数" in error for error in result.errors)


def test_parse_ossie_document_rejects_invalid_yaml_and_empty_models():
    with pytest.raises(ValueError, match="不是合法的 YAML"):
        parse_ossie_document("version: [unclosed")
    with pytest.raises(ValueError, match="必须是 YAML 映射"):
        parse_ossie_document("- just\n- a\n- list\n")
    with pytest.raises(ValueError, match="不包含 semantic_model"):
        OssieToChatDataImporter().import_yaml('version: "1.0"\nsemantic_model: []\n')


def test_time_dimension_without_datatype_defaults_to_date_with_warning():
    document = """
version: "1.0"
semantic_model:
  - name: 时间模型
    datasets:
      - name: t
        source: t
        fields:
          - name: 下单日期
            expression:
              dialects:
                - dialect: ANSI_SQL
                  expression: '`order_date`'
            dimension:
              is_time: true
"""
    result = OssieToChatDataImporter().import_yaml(document)[0]
    assert result.importable, result.errors
    assert result.dimensions[0]["logical_type"] == "date"
    assert any("默认按 date" in warning for warning in result.warnings)


def test_parse_rejects_model_without_name():
    """OSI schema 要求 model 必须有 name，解析阶段直接拒绝。"""
    with pytest.raises(ValueError, match="结构校验失败"):
        parse_ossie_document("""
version: "1.0"
semantic_model:
  - description: 无名模型
    datasets: []
""")


def test_parse_normalizes_sql_datatype_aliases():
    """AI 生成文件常用 SQL 类型名，解析前归一为 OSI 枚举名；映射不到的仍报校验错误。"""
    document = """
version: "1.0"
semantic_model:
  - name: 类型别名模型
    datasets:
      - name: t
        source: t
        fields:
          - name: 用户名
            expression:
              dialects:
                - dialect: ANSI_SQL
                  expression: user_name
            datatype: varchar
    metrics:
      - name: 金额合计
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: SUM(amount)
        datatype: BIGINT
"""
    document_model = parse_ossie_document(document)
    metric = document_model.semantic_model[0].metrics[0]
    field = document_model.semantic_model[0].datasets[0].fields[0]
    assert metric.datatype == OSIDataType.INTEGER
    assert field.datatype == OSIDataType.STRING

    with pytest.raises(ValueError, match="结构校验失败.*datatype"):
        parse_ossie_document(document.replace("datatype: BIGINT", "datatype: SUPER_BIG_INT"))


# ============ 路由层 ============

from contextlib import asynccontextmanager

from apps.chat_data_app.routers import semantic_config
from apps.chat_data_app.schema import SemanticOssieImportRequest


def _request():
    return SimpleNamespace(headers={}, client=SimpleNamespace(host="test"))


def _body(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


_DATASOURCE = {"id": "ds1", "ds_tables": [
    {"table_name": "orders", "columns": [
        {"name": "amount"}, {"name": "order_id"}, {"name": "user_id"},
        {"name": "tenant_id"}, {"name": "order_time"}, {"name": "status"},
    ]},
    {"table_name": "users", "columns": [{"name": "id"}, {"name": "nickname"}, {"name": "tenant_id"}]},
]}


@pytest.fixture(autouse=True)
def _passthrough_import_transaction(monkeypatch):
    """导入路由用 transaction_scope 包裹整批落库；单测不连数据库，这里退化为直接执行。

    回滚行为单独由 test_import_route_rolls_back_all_models_on_write_failure 用记录型桩验证。
    """

    @asynccontextmanager
    async def _passthrough():
        yield None

    monkeypatch.setattr(semantic_config, "transaction_scope", _passthrough)


class FakeManager:
    def __init__(self, datasource=_DATASOURCE, created=None, configs=None):
        self._datasource = datasource
        self.created_kwargs = []
        self.updated_kwargs = {}
        self._created = created
        self._configs = configs or []

    async def get_datasource_async(self, datasource_id):
        return self._datasource

    async def get_cached_schema_async(self, datasource_id):
        return self._datasource["ds_tables"]

    async def get_column_dict_async(self, datasource_id, table_name, column_name):
        return None

    async def create_async(self, **kwargs):
        self.created_kwargs.append(kwargs)
        index = len(self.created_kwargs)
        return self._created or {"id": f"sc_{index}", "name": kwargs.get("name")}

    async def get_configs_async(self, datasource_id, user_id=""):
        return list(self._configs)

    async def update_async(self, datasource_id, config_id, **kwargs):
        self.updated_kwargs[config_id] = kwargs
        return {"id": config_id, "name": kwargs.get("name", "商品模型")}


def _single_dataset_yaml() -> str:
    return """
version: "1.0"
semantic_model:
  - name: 商品模型
    datasets:
      - name: orders
        source: orders
        fields:
          - name: 销售额
            expression:
              dialects:
                - dialect: ANSI_SQL
                  expression: '`amount`'
            datatype: Decimal
    metrics:
      - name: 总额
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: SUM(amount)
"""


def test_preview_route_returns_importable_draft(monkeypatch):
    manager = FakeManager()
    monkeypatch.setattr(semantic_config, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(semantic_config, "get_semantic_config_service", lambda: manager)
    monkeypatch.setattr(semantic_config, "_get_current_user", lambda _request: SimpleNamespace(user_id="user_a", username="张三"))

    response = _body(asyncio.run(semantic_config.preview_admin_ossie_semantic_import(
        "ds1", SemanticOssieImportRequest(yaml_content=_single_dataset_yaml()), _request(),
    )))
    assert response["code"] == ErrorCode.SUCCESS
    models = response["data"]["models"]
    assert len(models) == 1
    assert models[0]["importable"] is True
    assert models[0]["dimensions"][0]["table"] == "orders"
    checklist = {row["name"]: row for row in models[0]["checklist"]}
    assert checklist["物理字段校验"]["status"] == "pass"
    assert checklist["表归属确定"]["status"] == "warn"
    assert "推断" in checklist["表归属确定"]["message"]
    assert checklist["质量规则核对"]["status"] in ("pass", "warn")


def test_preview_route_reports_validation_error(monkeypatch):
    """物理校验失败（字段不存在）必须进入 errors，导入被阻断。"""
    manager = FakeManager()
    monkeypatch.setattr(semantic_config, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(semantic_config, "get_semantic_config_service", lambda: manager)
    monkeypatch.setattr(semantic_config, "_get_current_user", lambda _request: SimpleNamespace(user_id="user_a"))

    broken_yaml = _single_dataset_yaml().replace("SUM(amount)", "SUM(amount_missing)")
    response = _body(asyncio.run(semantic_config.preview_admin_ossie_semantic_import(
        "ds1", SemanticOssieImportRequest(yaml_content=broken_yaml), _request(),
    )))
    models = response["data"]["models"]
    assert models[0]["importable"] is False
    assert any("物理字段" in error or "表达式" in error for error in models[0]["errors"])
    checklist = {row["name"]: row for row in models[0]["checklist"]}
    assert checklist["物理字段校验"]["status"] == "fail"
    assert "amount_missing" in checklist["物理字段校验"]["message"] or "物理字段" in checklist["物理字段校验"]["message"]
    assert checklist["质量规则核对"]["status"] == "skip"
    assert "score" not in models[0].get("quality", {})


def test_import_route_creates_disabled_public_configs(monkeypatch):
    manager = FakeManager()
    monkeypatch.setattr(semantic_config, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(semantic_config, "get_semantic_config_service", lambda: manager)
    monkeypatch.setattr(semantic_config, "_get_current_user", lambda _request: SimpleNamespace(user_id="user_a", username="张三"))

    response = _body(asyncio.run(semantic_config.import_admin_ossie_semantic_configs(
        "ds1", SemanticOssieImportRequest(yaml_content=_single_dataset_yaml()), _request(),
    )))
    assert response["code"] == ErrorCode.SUCCESS
    assert response["data"]["created"] == [{"config_id": "sc_1", "name": "商品模型"}]
    assert manager.created_kwargs[0]["enabled"] is False
    assert manager.created_kwargs[0]["scope_type"] == "public"
    assert manager.created_kwargs[0]["create_by"] == "user_a"


def test_import_route_blocked_by_validation_error(monkeypatch):
    manager = FakeManager()
    monkeypatch.setattr(semantic_config, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(semantic_config, "get_semantic_config_service", lambda: manager)
    monkeypatch.setattr(semantic_config, "_get_current_user", lambda _request: SimpleNamespace(user_id="user_a"))

    broken_yaml = _single_dataset_yaml().replace("SUM(amount)", "SUM(amount_missing)")
    response = _body(asyncio.run(semantic_config.import_admin_ossie_semantic_configs(
        "ds1", SemanticOssieImportRequest(yaml_content=broken_yaml), _request(),
    )))
    assert response["status"] == "error"
    assert response["data"]["models"][0]["importable"] is False


def test_import_route_rejects_invalid_document(monkeypatch):
    manager = FakeManager()
    monkeypatch.setattr(semantic_config, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(semantic_config, "get_semantic_config_service", lambda: manager)
    monkeypatch.setattr(semantic_config, "_get_current_user", lambda _request: SimpleNamespace(user_id="user_a"))

    response = _body(asyncio.run(semantic_config.import_admin_ossie_semantic_configs(
        "ds1", SemanticOssieImportRequest(yaml_content="version: [unclosed"), _request(),
    )))
    assert response["status"] == "error"
    assert "YAML" in response["msg"]


def test_import_route_missing_datasource(monkeypatch):
    manager = FakeManager(datasource=None)
    monkeypatch.setattr(semantic_config, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(semantic_config, "get_semantic_config_service", lambda: manager)
    monkeypatch.setattr(semantic_config, "_get_current_user", lambda _request: SimpleNamespace(user_id="user_a"))

    response = _body(asyncio.run(semantic_config.import_admin_ossie_semantic_configs(
        "missing", SemanticOssieImportRequest(yaml_content=_single_dataset_yaml()), _request(),
    )))
    assert response["status"] == "error"
    assert "数据源不存在" in response["msg"]


def _checklist_by_name(rows):
    return {row["name"]: row for row in rows}


def test_checklist_all_pass_and_no_score():
    item = OssieSemanticModelImport(name="模型A")
    item.bump("attribution_declared", 3)
    item.bump("physical_checked")
    rows = build_model_checklist(item, validation_error=None, quality_findings=[], inventory_available=True)
    by_name = _checklist_by_name(rows)
    assert by_name["表归属确定"]["status"] == "pass"
    assert "3 个对象" in by_name["表归属确定"]["message"]
    assert by_name["派生 / 复合指标还原"]["status"] == "pass"
    assert "无派生/复合指标" in by_name["派生 / 复合指标还原"]["message"]
    assert by_name["物理字段校验"]["status"] == "pass"
    assert by_name["质量规则核对"]["status"] == "pass"
    from apps.chat_data_app.services.semantic.run.semantic_quality_checker import RULES as quality_rules
    assert by_name["质量规则核对"]["message"] == f"{len(quality_rules)} 条质量规则全部未触发"
    assert all("score" not in row and "grade" not in row for row in rows)


def test_checklist_attribution_inferred_is_warn_and_failed_is_fail():
    item = OssieSemanticModelImport(name="模型B")
    item.bump("attribution_declared", 2)
    item.bump("attribution_inferred", 1)
    rows = build_model_checklist(item, inventory_available=False)
    by_name = _checklist_by_name(rows)
    assert by_name["表归属确定"]["status"] == "warn"

    failed = OssieSemanticModelImport(name="模型C")
    failed.bump("attribution_declared", 1)
    failed.bump("attribution_failed", 2)
    rows = build_model_checklist(failed)
    by_name = _checklist_by_name(rows)
    assert by_name["表归属确定"]["status"] == "fail"
    assert "2 个对象" in by_name["表归属确定"]["message"]


def test_checklist_kind_and_physical_states():
    item = OssieSemanticModelImport(name="模型D")
    item.metrics.append({"name": "近7日销售额", "metric_kind": "derived", "base_metric": "销售额"})
    item.bump("kind_restored")
    item.bump("kind_failed")
    rows = build_model_checklist(item)
    by_name = _checklist_by_name(rows)
    assert by_name["派生 / 复合指标还原"]["status"] == "fail"

    ok_item = OssieSemanticModelImport(name="模型E")
    ok_item.bump("physical_checked")
    rows = build_model_checklist(ok_item, validation_error="指标 X 表达式必须引用表 t 的物理字段")
    by_name = _checklist_by_name(rows)
    assert by_name["物理字段校验"]["status"] == "fail"
    assert "物理字段" in by_name["物理字段校验"]["message"]

    unchecked = OssieSemanticModelImport(name="模型F")
    rows = build_model_checklist(unchecked)
    by_name = _checklist_by_name(rows)
    assert by_name["物理字段校验"]["status"] == "skip"
    assert "物理校验未执行" in by_name["物理字段校验"]["message"]


def test_checklist_quality_findings_counts():
    item = OssieSemanticModelImport(name="模型G")
    findings = [
        {"code": "EXE_NON_SIMPLE_AGG", "severity": "warning", "message": "m"},
        {"code": "COV_UNMODELED_COLUMNS", "severity": "info", "message": "m"},
        {"code": "DOC_NO_BUSINESS_NOTES", "severity": "info", "message": "m"},
    ]
    rows = build_model_checklist(item, quality_findings=findings)
    by_name = _checklist_by_name(rows)
    assert by_name["质量规则核对"]["status"] == "warn"
    assert "1 条警告 · 2 条建议" in by_name["质量规则核对"]["message"]

    error_findings = [{"code": "EXE_UNKNOWN_COLUMN", "severity": "error", "message": "m"}]
    rows = build_model_checklist(item, quality_findings=error_findings)
    by_name = _checklist_by_name(rows)
    assert by_name["质量规则核对"]["status"] == "fail"


def test_importer_stats_bump_on_conversion():
    """导入器在真实解析路径上正确累计 attribution / kind 计数。"""
    yaml_text = _single_dataset_yaml()
    document = parse_ossie_document(yaml_text)
    item = OssieToChatDataImporter().import_document(document)[0]
    # 单 dataset 无扩展：字段+指标均按唯一 dataset 推断
    assert item.stats.get("attribution_inferred") == 2
    assert item.stats.get("attribution_declared") is None


def _conflicted_manager():
    return FakeManager(configs=[{"id": "sc_9", "name": "商品模型", "enabled": True}])


def test_preview_route_marks_existing_config_conflict(monkeypatch):
    """同组内存在同名活跃配置时，preview 标注冲突；同名不再进校验清单（覆盖导入是正常工作流）。"""
    manager = _conflicted_manager()
    monkeypatch.setattr(semantic_config, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(semantic_config, "get_semantic_config_service", lambda: manager)
    monkeypatch.setattr(semantic_config, "_get_current_user", lambda _request: SimpleNamespace(user_id="user_a"))

    response = _body(asyncio.run(semantic_config.preview_admin_ossie_semantic_import(
        "ds1", SemanticOssieImportRequest(yaml_content=_single_dataset_yaml()), _request(),
    )))
    assert response["code"] == ErrorCode.SUCCESS
    model = response["data"]["models"][0]
    # 冲突不影响可导入（由导入动作决定替换/跳过）；动作徽章数据仍随模型返回
    assert model["importable"] is True
    assert model["existing_config"] == {"config_id": "sc_9", "name": "商品模型", "enabled": True}
    # 可操作同名不再生成「模型名冲突」清单行，前端由名称行动作徽章（替换/忽略）表达
    assert all(row["name"] != "模型名冲突" for row in model["checklist"])


def test_preview_route_no_conflict_when_name_absent(monkeypatch):
    manager = FakeManager(configs=[{"id": "sc_1", "name": "其他模型", "enabled": True}])
    monkeypatch.setattr(semantic_config, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(semantic_config, "get_semantic_config_service", lambda: manager)
    monkeypatch.setattr(semantic_config, "_get_current_user", lambda _request: SimpleNamespace(user_id="user_a"))

    response = _body(asyncio.run(semantic_config.preview_admin_ossie_semantic_import(
        "ds1", SemanticOssieImportRequest(yaml_content=_single_dataset_yaml()), _request(),
    )))
    model = response["data"]["models"][0]
    assert model["existing_config"] is None
    assert all(row["name"] != "模型名冲突" for row in model["checklist"])


def test_import_route_replace_updates_existing_config(monkeypatch):
    """replace：覆盖同名配置内容，保留 config_id，强制禁用，不新建。"""
    manager = _conflicted_manager()
    monkeypatch.setattr(semantic_config, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(semantic_config, "get_semantic_config_service", lambda: manager)
    monkeypatch.setattr(semantic_config, "_get_current_user", lambda _request: SimpleNamespace(user_id="user_a", username="张三"))

    payload = SemanticOssieImportRequest(
        yaml_content=_single_dataset_yaml(),
        conflict_actions={"商品模型": "replace"},
    )
    response = _body(asyncio.run(semantic_config.import_admin_ossie_semantic_configs("ds1", payload, _request())))
    assert response["code"] == ErrorCode.SUCCESS
    assert response["data"]["created"] == []
    assert response["data"]["replaced"] == [{"config_id": "sc_9", "name": "商品模型"}]
    assert response["data"]["skipped"] == []
    assert manager.created_kwargs == []
    updated = manager.updated_kwargs["sc_9"]
    assert updated["enabled"] is False
    assert updated["update_by"] == "user_a"
    assert updated["update_by_name"] == "张三"
    assert updated["metrics"] and updated["dimensions"]


def test_import_route_skips_conflict_by_default(monkeypatch):
    """conflict_actions 缺省按 skip：不动已有配置，也不新建。"""
    manager = _conflicted_manager()
    monkeypatch.setattr(semantic_config, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(semantic_config, "get_semantic_config_service", lambda: manager)
    monkeypatch.setattr(semantic_config, "_get_current_user", lambda _request: SimpleNamespace(user_id="user_a"))

    response = _body(asyncio.run(semantic_config.import_admin_ossie_semantic_configs(
        "ds1", SemanticOssieImportRequest(yaml_content=_single_dataset_yaml()), _request(),
    )))
    assert response["code"] == ErrorCode.SUCCESS
    assert response["data"]["created"] == []
    assert response["data"]["replaced"] == []
    assert response["data"]["skipped"] == [{"name": "商品模型", "config_id": "sc_9", "reason": "同名配置已存在，未导入"}]
    assert manager.created_kwargs == []
    assert manager.updated_kwargs == {}


def test_import_route_rejects_invalid_conflict_action(monkeypatch):
    """非法动作在写库前整体拒绝（全有或全无的前置闸门）。"""
    manager = _conflicted_manager()
    monkeypatch.setattr(semantic_config, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(semantic_config, "get_semantic_config_service", lambda: manager)
    monkeypatch.setattr(semantic_config, "_get_current_user", lambda _request: SimpleNamespace(user_id="user_a"))

    payload = SemanticOssieImportRequest(
        yaml_content=_single_dataset_yaml(),
        conflict_actions={"商品模型": "delete"},
    )
    response = _body(asyncio.run(semantic_config.import_admin_ossie_semantic_configs("ds1", payload, _request())))
    assert response["status"] == "error"
    assert "replace/skip" in response["msg"]
    assert manager.created_kwargs == []
    assert manager.updated_kwargs == {}


def test_import_route_rejects_duplicate_model_names_in_request(monkeypatch):
    """请求内同名模型会让落库顺序互相踩踏，必须在写库前整体拒绝。"""
    manager = FakeManager()
    monkeypatch.setattr(semantic_config, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(semantic_config, "get_semantic_config_service", lambda: manager)
    monkeypatch.setattr(semantic_config, "_get_current_user", lambda _request: SimpleNamespace(user_id="user_a"))

    yaml_text = _single_dataset_yaml().replace(
        "semantic_model:",
        "semantic_model:\n  - name: 商品模型\n    datasets: []\n    metrics: []",
        1,
    )
    response = _body(asyncio.run(semantic_config.import_admin_ossie_semantic_configs(
        "ds1", SemanticOssieImportRequest(yaml_content=yaml_text), _request(),
    )))
    assert response["status"] == "error"
    assert "重名" in response["msg"]
    assert manager.created_kwargs == []


def _locked_conflict_manager():
    """同组内同名配置属于他人私有：admin 口径下不可操作，只能强制跳过。"""
    return FakeManager(configs=[
        {"id": "sc_p1", "name": "商品模型", "enabled": True, "scope_type": "private", "create_by": "user_b"},
    ])


def test_preview_route_locks_conflict_with_others_private_config(monkeypatch):
    """他人同名私有配置：标记 conflict_locked，且不向调用方泄露其 config_id 等元信息。"""
    manager = _locked_conflict_manager()
    monkeypatch.setattr(semantic_config, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(semantic_config, "get_semantic_config_service", lambda: manager)
    monkeypatch.setattr(semantic_config, "_get_current_user", lambda _request: SimpleNamespace(user_id="user_a"))

    response = _body(asyncio.run(semantic_config.preview_admin_ossie_semantic_import(
        "ds1", SemanticOssieImportRequest(yaml_content=_single_dataset_yaml()), _request(),
    )))
    assert response["code"] == ErrorCode.SUCCESS
    model = response["data"]["models"][0]
    # 不可操作的同名配置不进冲突表（existing_config 保持 None），改由 conflict_locked 标记
    assert model["importable"] is True
    assert model["existing_config"] is None
    assert model["conflict_locked"] is True
    assert "sc_p1" not in json.dumps(model, ensure_ascii=False)
    checklist = _checklist_by_name(model["checklist"])
    assert checklist["模型名冲突"]["status"] == "warn"
    assert "无操作权限" in checklist["模型名冲突"]["message"]
    assert "sc_p1" not in json.dumps(model["checklist"], ensure_ascii=False)


def test_import_route_skips_locked_conflict_even_with_replace_action(monkeypatch):
    """他人同名私有配置：即便显式要求 replace 也强制跳过，不新建、不覆盖他人配置。"""
    manager = _locked_conflict_manager()
    monkeypatch.setattr(semantic_config, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(semantic_config, "get_semantic_config_service", lambda: manager)
    monkeypatch.setattr(semantic_config, "_get_current_user", lambda _request: SimpleNamespace(user_id="user_a", username="张三"))

    payload = SemanticOssieImportRequest(
        yaml_content=_single_dataset_yaml(),
        conflict_actions={"商品模型": "replace"},
    )
    response = _body(asyncio.run(semantic_config.import_admin_ossie_semantic_configs("ds1", payload, _request())))
    assert response["code"] == ErrorCode.SUCCESS
    assert response["data"]["created"] == []
    assert response["data"]["replaced"] == []
    assert response["data"]["skipped"] == [{"name": "商品模型", "reason": "存在同名配置且无操作权限，已忽略"}]
    assert manager.created_kwargs == []
    assert manager.updated_kwargs == {}
    assert "sc_p1" not in json.dumps(response["data"], ensure_ascii=False)


def _two_model_yaml() -> str:
    return """
version: "1.0"
semantic_model:
  - name: 商品模型
    datasets:
      - name: orders
        source: orders
        fields:
          - name: 销售额
            expression:
              dialects:
                - dialect: ANSI_SQL
                  expression: '`amount`'
            datatype: Decimal
    metrics: []
  - name: 用户模型
    datasets:
      - name: users
        source: users
        fields:
          - name: 昵称
            expression:
              dialects:
                - dialect: ANSI_SQL
                  expression: '`nickname`'
            datatype: String
    metrics: []
"""


class _FailingCreateManager(FakeManager):
    """第 fail_at 次创建返回 None，用于验证整批写入失败时的回滚。"""

    def __init__(self, fail_at: int):
        super().__init__()
        self._fail_at = fail_at

    async def create_async(self, **kwargs):
        self.created_kwargs.append(kwargs)
        if len(self.created_kwargs) >= self._fail_at:
            return None
        return {"id": f"sc_{len(self.created_kwargs)}", "name": kwargs.get("name")}


class _RecordingTransaction:
    """记录提交/回滚的事务桩：替换路由的 transaction_scope 以观察外层行为。"""

    def __init__(self):
        self.committed = False
        self.rolled_back = False

    @asynccontextmanager
    async def __call__(self):
        try:
            yield None
        except Exception:
            self.rolled_back = True
            raise
        self.committed = True


def test_import_route_rolls_back_all_models_on_write_failure(monkeypatch):
    """任一个模型写入失败即整批回滚（异常必须穿透事务边界），不留半批草稿。"""
    manager = _FailingCreateManager(fail_at=2)
    transaction = _RecordingTransaction()
    monkeypatch.setattr(semantic_config, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(semantic_config, "get_semantic_config_service", lambda: manager)
    monkeypatch.setattr(semantic_config, "transaction_scope", transaction)
    monkeypatch.setattr(semantic_config, "_get_current_user", lambda _request: SimpleNamespace(user_id="user_a"))

    response = _body(asyncio.run(semantic_config.import_admin_ossie_semantic_configs(
        "ds1", SemanticOssieImportRequest(yaml_content=_two_model_yaml()), _request(),
    )))
    assert response["status"] == "error"
    assert "已回滚" in response["msg"]
    assert "用户模型" in response["msg"]
    assert transaction.rolled_back is True
    assert transaction.committed is False
    assert len(manager.created_kwargs) == 2


def test_checklist_conflict_row_only_for_locked_conflict():
    """可操作同名不再进清单（覆盖导入是正常工作流）；仅 locked 同名保留「模型名冲突」警告行。"""
    item = OssieSemanticModelImport(name="模型A", existing_config={"config_id": "sc_1", "name": "模型A", "enabled": False})
    rows = build_model_checklist(item)
    assert all(row["name"] != "模型名冲突" for row in rows)

    locked = OssieSemanticModelImport(name="模型C", conflict_locked=True)
    by_name = _checklist_by_name(build_model_checklist(locked))
    assert by_name["模型名冲突"]["status"] == "warn"
    assert "无操作权限" in by_name["模型名冲突"]["message"]

    no_conflict = build_model_checklist(OssieSemanticModelImport(name="模型B"))
    assert all(row["name"] != "模型名冲突" for row in no_conflict)


def test_import_restores_field_name_and_table_alias_from_extension():
    """扩展携带的 field_name / table_alias 必须还原进条目，保证往返无损。"""
    document = """
version: "1.0"
semantic_model:
  - name: 往返模型
    datasets:
      - name: orders
        source: orders
        fields:
          - name: 城市
            expression:
              dialects:
                - dialect: ANSI_SQL
                  expression: city
            datatype: String
            custom_extensions:
              - vendor_name: CHAT_DATA
                data: '{"table": "orders", "column": "city", "field_name": "city_name", "table_alias": "o"}'
    metrics:
      - name: 销售额
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: SUM(amount)
        custom_extensions:
          - vendor_name: CHAT_DATA
            data: '{"table": "orders", "column": "amount", "field_name": "sales_amount", "table_alias": "o"}'
"""
    result = OssieToChatDataImporter().import_yaml(document)[0]
    assert result.importable
    assert result.dimensions[0]["field_name"] == "city_name"
    assert result.dimensions[0]["table_alias"] == "o"
    assert result.metrics[0]["field_name"] == "sales_amount"
    assert result.metrics[0]["table_alias"] == "o"


def test_import_omits_field_name_and_table_alias_when_absent():
    """无扩展字段时不产出空键，避免与存储配置的空串产生伪差异。"""
    document = """
version: "1.0"
semantic_model:
  - name: 外部模型
    datasets:
      - name: orders
        source: orders
        fields:
          - name: 城市
            expression:
              dialects:
                - dialect: ANSI_SQL
                  expression: city
            datatype: String
            custom_extensions:
              - vendor_name: CHAT_DATA
                data: '{"table": "orders", "column": "city"}'
    metrics: []
"""
    result = OssieToChatDataImporter().import_yaml(document)[0]
    assert result.importable
    assert "field_name" not in result.dimensions[0]
    assert "table_alias" not in result.dimensions[0]


def test_import_prefers_logical_type_and_data_type_from_extension():
    """扩展携带的 logical_type / data_type 优先于 datatype 映射，enum 与精度形态不丢失。"""
    document = """
version: "1.0"
semantic_model:
  - name: 往返模型
    datasets:
      - name: orders
        source: orders
        fields:
          - name: 状态
            expression:
              dialects:
                - dialect: ANSI_SQL
                  expression: status
            datatype: String
            custom_extensions:
              - vendor_name: CHAT_DATA
                data: '{"table": "orders", "column": "status", "logical_type": "enum"}'
    metrics:
      - name: 金额
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: SUM(amount)
        datatype: Decimal
        custom_extensions:
          - vendor_name: CHAT_DATA
            data: '{"table": "orders", "column": "amount", "data_type": "decimal(10,2)"}'
"""
    result = OssieToChatDataImporter().import_yaml(document)[0]
    assert result.importable
    assert result.dimensions[0]["logical_type"] == "enum"
    assert result.metrics[0]["data_type"] == "decimal(10,2)"


def test_import_rejects_invalid_logical_type_from_extension():
    """扩展里的非法 logical_type 不入条目，回退 datatype 映射，避免脏值落库。"""
    document = """
version: "1.0"
semantic_model:
  - name: 往返模型
    datasets:
      - name: orders
        source: orders
        fields:
          - name: 状态
            expression:
              dialects:
                - dialect: ANSI_SQL
                  expression: status
            datatype: String
            custom_extensions:
              - vendor_name: CHAT_DATA
                data: '{"table": "orders", "column": "status", "logical_type": "garbage"}'
    metrics: []
"""
    result = OssieToChatDataImporter().import_yaml(document)[0]
    assert result.importable
    assert result.dimensions[0]["logical_type"] == "text"
