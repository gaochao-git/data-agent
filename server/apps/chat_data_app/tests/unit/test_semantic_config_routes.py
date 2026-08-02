import asyncio
import json
import os
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from utils.error_codes import ErrorCode
from apps.chat_data_app.routers import semantic_config
from apps.chat_data_app.agent.prompts.dim_metric_auto_prefill import (
    build_auto_prefill_knowledge_prompt,
    build_auto_prefill_prompt,
    build_prefill_model_guidance,
)
from apps.chat_data_app.schema import (
    SemanticAutoPrefillRequest,
    SemanticConfigCreateRequest,
    SemanticConfigUpdateRequest,
    SemanticHistoryLearningRequest,
    SemanticPrefillRequest,
)
from apps.chat_data_app.services.file_text_extractor import extract_text_from_file_bytes
from apps.chat_data_app.services import semantic_config_validator
from apps.chat_data_app.services import semantic_history_learning_service
from apps.chat_data_app.services.semantic_history_learning_service import build_semantic_history_learning_service


def _patch_history_learning_service_session(monkeypatch, session_factory):
    monkeypatch.setattr(
        semantic_config,
        "_build_history_learning_service",
        lambda: build_semantic_history_learning_service(
            session_context=session_factory,
            context_loader=semantic_config._load_history_conversation_context,
            suggestion_loader=semantic_config._generate_history_learning_suggestions_by_llm,
            history_matcher=semantic_config._history_belongs_to_config,
        ),
    )


def test_semantic_permission_helpers():
    own = {"id": "cfg_a", "create_by": "user_a", "scope_type": "private"}
    public = {"id": "cfg_b", "create_by": "admin"}

    assert semantic_config._can_manage_semantic_config(own, "user_a") is True
    assert semantic_config._can_manage_semantic_config(own, "user_b") is False
    assert semantic_config._with_semantic_config_permissions(public, "user_a")["scope_type"] == "public"
    assert semantic_config._with_semantic_config_permissions(public, "user_a")["can_manage"] is False
    assert semantic_config._with_semantic_config_permissions(public, "user_a", admin_manage=True)["can_manage"] is True


def test_validate_semantic_fields_checks_dimensions_and_metrics():
    datasource = {
        "ds_tables": [
            {"table_name": "orders", "columns": [{"name": "amount"}, {"name": "city"}]},
        ]
    }

    assert semantic_config_validator.validate_semantic_fields(
        datasource,
        metrics=[{"name": "销售额", "table": "orders", "expression": "SUM(amount)"}],
        dimensions=[{"name": "城市", "table": "orders", "column": "city"}],
    ) is None
    assert "所属表不存在" in semantic_config_validator.validate_semantic_fields(datasource, [], [{"name": "城市", "table": "missing", "column": "city"}])
    assert "字段不存在" in semantic_config_validator.validate_semantic_fields(datasource, [], [{"name": "城市", "table": "orders", "column": "missing"}])
    assert "必须引用表" in semantic_config_validator.validate_semantic_fields(datasource, [{"name": "销售额", "table": "orders", "expression": "SUM(price)"}], [])
    assert "字段显示名称重复" in semantic_config_validator.validate_semantic_fields(
        datasource,
        metrics=[{"name": "数量", "table": "orders", "expression": "SUM(amount)"}],
        dimensions=[{"name": "数量", "table": "orders", "column": "city"}],
    )
    assert "字段显示名称不能为空" in semantic_config_validator.validate_semantic_fields(
        datasource,
        metrics=[{"name": " ", "table": "orders", "expression": "SUM(amount)"}],
        dimensions=[],
    )
    assert "所属表不存在" in semantic_config_validator.validate_semantic_fields(
        datasource,
        metrics=[{"name": "销售额", "table": "missing", "expression": "SUM(amount)"}],
        dimensions=[],
    )
    assert "缺少表结构" in semantic_config_validator.validate_semantic_fields({"ds_tables": []}, [], [])


def test_validate_semantic_logical_types_requires_enum_column_dict():
    class Manager:
        async def get_column_dict_async(self, datasource_id, table_name, column_name):
            if column_name == "status":
                return {"enum_values": [{"value": "1", "label": "有效"}]}
            if column_name == "empty_enum":
                return {"enum_values": []}
            return None

    manager = Manager()
    assert asyncio.run(semantic_config_validator.validate_semantic_logical_types(
        manager,
        "ds_a",
        [{"name": "状态", "table": "orders", "column": "status", "logical_type": "enum"}],
    )) is None
    assert "请先维护字段字典枚举值" in asyncio.run(semantic_config_validator.validate_semantic_logical_types(
        manager,
        "ds_a",
        [{"name": "空枚举", "table": "orders", "column": "empty_enum", "logical_type": "enum"}],
    ))
    assert asyncio.run(semantic_config_validator.validate_semantic_logical_types(
        manager,
        "ds_a",
        [{"name": "城市", "table": "orders", "column": "city", "logical_type": "text"}],
    )) is None


def test_history_message_and_feedback_helpers():
    assert semantic_history_learning_service._history_message_text("  hello  ") == "hello"
    assert semantic_history_learning_service._history_message_text([{"text": "问题"}, {"content": "回答"}, "补充"]) == "问题\n回答\n补充"
    assert semantic_history_learning_service._history_message_text([{"text": ""}, {"content": None}]) == ""
    assert semantic_history_learning_service._history_message_text(None) == ""
    assert semantic_history_learning_service._is_negative_feedback_text("这个结果不对") is True
    assert semantic_history_learning_service._is_negative_feedback_text("") is False
    assert semantic_history_learning_service._is_negative_feedback_text("好的") is False
    assert semantic_history_learning_service._is_history_tool_error_message('{"success": false, "error": "表不存在"}') is True
    assert semantic_history_learning_service._is_history_tool_error_message('{"success": true, "error": "仍然有错误"}') is True
    assert semantic_history_learning_service._is_history_tool_error_message("") is False
    assert semantic_history_learning_service._is_history_tool_error_message("[execute_sql]\n{\"success\": true}") is False
    assert semantic_history_learning_service._is_history_tool_error_message("Traceback exception") is True


def test_compact_history_conversation_messages_keeps_useful_context():
    compact = semantic_history_learning_service._compact_history_conversation_messages([
        {"role": "user", "content": "查询销售额", "timestamp": "t1"},
        {"role": "assistant", "content": "已查询", "timestamp": "t2"},
        {"role": "tool", "tool_name": "execute_sql", "content": '{"success": false, "error": "字段不存在"}', "timestamp": "t3"},
        {"role": "user", "content": "不对，少了退款订单", "timestamp": "t4"},
    ])

    assert compact["user_turns"][0]["content"] == "查询销售额"
    assert compact["assistant_turns"][0]["content"] == "已查询"
    assert compact["tool_errors"][0]["tool_name"] == "execute_sql"
    assert compact["negative_feedback"] == ["不对，少了退款订单"]


def test_history_belongs_to_config_for_agent_builder_and_sql(monkeypatch):
    config = {
        "metrics": [{"table": "orders"}],
        "dimensions": [{"table": "products"}],
        "relationships": [{"left_table": "orders", "right_table": "products"}],
    }

    assert semantic_config._history_belongs_to_config("agent", {"semantic_config_id": "cfg_a"}, "", "cfg_a", config, "mysql")[:2] == (True, "matched_config_id")
    assert semantic_config._history_belongs_to_config("builder", {"semantic_config_id": "other"}, "", "cfg_a", config, "mysql")[:2] == (False, "config_id_mismatch")

    monkeypatch.setattr(semantic_config, "extract_physical_tables", lambda sql, source_type="mysql": SimpleNamespace(success=True, tables={"orders"}, error=""))
    assert semantic_config._history_belongs_to_config("sql", {}, "select * from orders", "cfg_a", config, "mysql")[:2] == (True, "matched_table_scope")
    monkeypatch.setattr(semantic_config, "extract_physical_tables", lambda sql, source_type="mysql": SimpleNamespace(success=True, tables={"users"}, error=""))
    assert semantic_config._history_belongs_to_config("sql", {}, "select * from users", "cfg_a", config, "mysql")[:2] == (False, "table_scope_mismatch")
    monkeypatch.setattr(semantic_config, "extract_physical_tables", lambda sql, source_type="mysql": SimpleNamespace(success=False, tables=set(), error="syntax"))
    assert semantic_config._history_belongs_to_config("sql", {}, "bad sql", "cfg_a", config, "mysql") == (False, "sql_parse_failed", [], "syntax")
    assert semantic_config._history_belongs_to_config("agent", {}, "", "cfg_a", config, "mysql")[:2] == (False, "config_id_mismatch")
    assert semantic_config._history_belongs_to_config("sql", {}, "select 1", "cfg_a", {"metrics": [], "dimensions": [], "relationships": []}, "mysql")[:2] == (False, "empty_scope")
    monkeypatch.setattr(semantic_config, "extract_physical_tables", lambda sql, source_type="mysql": SimpleNamespace(success=True, tables=set(), error=""))
    assert semantic_config._history_belongs_to_config("sql", {}, "select 1", "cfg_a", config, "mysql")[:2] == (False, "empty_history_tables")


def test_history_learning_compact_sample_and_normalize_item_branches():
    long_text = "x" * ErrorCode.SUCCESS
    compact_agent = semantic_history_learning_service._compact_history_sample_for_llm({
        "execution_id": "exec_1",
        "source": "agent",
        "status": "failed",
        "query_text": long_text,
        "sql": long_text,
        "error_message": long_text,
        "executed_at": "2026-06-02",
        "conversation_context": {"user_turns": [{"content": "查销售额"}]},
    })
    compact_builder = semantic_history_learning_service._compact_history_sample_for_llm({
        "execution_id": "exec_2",
        "source": "builder",
        "semantic_builder_config": {
            "mainTable": "orders",
            "joins": [{"id": i} for i in range(12)],
            "dimensions": [{"id": i} for i in range(25)],
            "metrics": [{"id": i} for i in range(25)],
            "filters": [{"id": i} for i in range(25)],
            "order_by": [{"id": i} for i in range(12)],
        },
    })

    samples = [{"execution_id": "exec_1", "source": "sql"}]
    sample_map = semantic_history_learning_service._sample_map_by_execution_id(samples)
    datasource = {"ds_tables": [{"table_name": "orders", "columns": [{"name": "amount"}]}]}

    assert len(compact_agent["query_text"]) == 300
    assert len(compact_agent["sql"]) == 1200
    assert len(compact_agent["error_message"]) == 500
    assert compact_agent["conversation_context"]["user_turns"][0]["content"] == "查销售额"
    assert len(compact_builder["builder_config"]["joins"]) == 10
    assert len(compact_builder["builder_config"]["dimensions"]) == 20
    assert len(compact_builder["builder_config"]["aggregations"]) == 20
    assert len(compact_builder["builder_config"]["orderBy"]) == 10
    assert semantic_history_learning_service._normalize_history_suggestion_item("bad", "metrics", sample_map, datasource) is None
    assert semantic_history_learning_service._normalize_history_suggestion_item({"source": "bad"}, "metrics", sample_map, datasource) is None
    assert semantic_history_learning_service._normalize_history_suggestion_item({"source": "sql", "confidence": "bad"}, "metrics", sample_map, datasource) is None
    assert semantic_history_learning_service._normalize_history_suggestion_item({"source": "sql", "confidence": "high", "sample_execution_ids": "bad"}, "metrics", sample_map, datasource) is None
    assert semantic_history_learning_service._normalize_history_suggestion_item({"source": "sql", "confidence": "high", "sample_execution_ids": ["missing"], "support_count": 1}, "metrics", sample_map, datasource) is None
    assert semantic_history_learning_service._normalize_history_suggestion_item({"source": "sql", "confidence": "high", "sample_execution_ids": ["exec_1"], "support_count": "bad"}, "metrics", sample_map, datasource) is None
    assert semantic_history_learning_service._normalize_history_suggestion_item({
        "source": "sql",
        "confidence": "high",
        "sample_execution_ids": ["exec_1"],
        "support_count": 1,
        "name": "",
        "reason": "缺名称",
    }, "metrics", sample_map, datasource) is None
    assert semantic_history_learning_service._normalize_history_suggestion_item({
        "source": "sql",
        "confidence": "high",
        "sample_execution_ids": ["exec_1"],
        "support_count": 1,
        "title": "",
        "content": "内容",
        "reason": "缺标题",
    }, "issues", sample_map, datasource) is None
    default_text_dimension = semantic_history_learning_service._normalize_history_suggestion_item({
        "source": "sql",
        "confidence": "high",
        "sample_execution_ids": ["exec_1"],
        "support_count": 1,
        "name": "渠道",
        "table": "orders",
        "field_name": "amount",
        "reason": "历史过滤",
    }, "dimensions", sample_map, datasource)
    assert default_text_dimension["logical_type"] == "text"


def test_history_learning_context_and_indexes():
    datasource = {
        "ds_tables": [
            {"table_name": "orders", "description": "订单", "columns": [{"name": "amount", "mysql_type": "decimal", "description": "金额"}]},
            {"table_name": "users", "columns": [{"name": "id"}]},
        ]
    }
    config = {
        "metrics": [{"name": "销售额", "table": "orders", "expression": "SUM(amount)"}],
        "dimensions": [{"name": "城市", "table": "orders", "column": "city", "field_name": "city"}],
        "relationships": [],
    }

    table_context = semantic_history_learning_service._build_history_learning_table_context(datasource, config)
    indexes = semantic_history_learning_service._build_existing_semantic_indexes(config)

    assert table_context == [{"table_name": "orders", "description": "订单", "columns": [{"name": "amount", "type": "decimal", "description": "金额"}]}]
    assert "销售额" in indexes["metric_names"]
    assert "orders.sum(amount)" in indexes["metric_expressions"]
    assert "orders.city" in indexes["dimension_columns"]
    assert semantic_history_learning_service._normalize_semantic_text(" `City` ") == "city"
    assert semantic_history_learning_service._normalize_semantic_expression(" SUM( `Amount` ) ") == "sum(`amount`)"
    assert semantic_history_learning_service._extract_expression_columns("SUM(orders.amount) + COUNT(DISTINCT `order_id`) + CASE WHEN status = 'x' THEN 1 END") == {"amount", "order_id", "status"}
    assert semantic_history_learning_service._is_existing_history_suggestion({"name": "销售额"}, "metrics", indexes) is True
    assert semantic_history_learning_service._is_existing_history_suggestion({"table": "orders", "expression": "SUM(amount)"}, "metrics", indexes) is True
    assert semantic_history_learning_service._is_existing_history_suggestion({"name": "城市"}, "dimensions", indexes) is True
    assert semantic_history_learning_service._is_existing_history_suggestion({"table": "orders", "column": "city"}, "dimensions", indexes) is True
    assert semantic_history_learning_service._is_existing_history_suggestion({"name": "新字段"}, "unknown", indexes) is False


def test_normalize_history_learning_result_filters_invalid_existing_and_flags_review():
    datasource = {
        "ds_tables": [
            {"table_name": "orders", "columns": [{"name": "amount"}, {"name": "city"}]},
        ]
    }
    config = {
        "metrics": [{"name": "销售额", "table": "orders", "expression": "SUM(amount)"}],
        "dimensions": [{"name": "城市", "table": "orders", "column": "city"}],
    }
    samples = [{"execution_id": "exec_1", "source": "sql"}, {"execution_id": "exec_2", "source": "builder"}]
    raw = {
        "suggestions": {
            "metrics": [
                {"name": "销售额", "table": "orders", "expression": "SUM(amount)", "reason": "已存在", "source": "sql", "confidence": "high", "sample_execution_ids": ["exec_1"], "support_count": 1},
                {"name": "客单价", "table": "orders", "expression": "AVG(price)", "reason": "历史多次查询", "source": "sql", "confidence": "medium", "sample_execution_ids": ["exec_1"], "support_count": 2},
            ],
            "dimensions": [
                {"name": "新城市", "table": "orders", "field_name": "city", "column": "city", "type": "bad", "reason": "类型无效", "source": "builder", "confidence": "high", "sample_execution_ids": ["exec_2"], "support_count": 1},
                {"name": "渠道", "table": "orders", "field_name": "channel", "column": "channel", "logical_type": "text", "reason": "历史过滤", "source": "builder", "confidence": "high", "sample_execution_ids": ["exec_2"], "support_count": 1},
            ],
            "issues": [
                {"title": "缺少枚举", "content": "订单状态需要枚举", "reason": "用户反馈", "source": "sql", "confidence": "high", "sample_execution_ids": ["exec_1"], "support_count": 1},
            ],
        }
    }

    result = semantic_history_learning_service._normalize_history_learning_result(raw, samples, datasource, config)

    assert [item["name"] for item in result["metrics"]] == ["客单价"]
    assert result["metrics"][0]["needs_review"] is True
    assert "price" in result["metrics"][0]["review_reason"]
    assert [item["name"] for item in result["dimensions"]] == ["渠道"]
    assert result["dimensions"][0]["needs_review"] is True
    assert result["issues"][0]["title"] == "缺少枚举"


def test_prefill_parse_and_alias_helpers_cover_edge_cases():
    tables = [
        {"table_name": "orders", "table_alias": "buyer_orders", "columns": [{"name": "amount", "mysql_type": "decimal"}, {"name": ""}]},
        {"table_name": "orders", "table_alias": "seller_orders", "columns": [{"name": "amount"}]},
        {"table_name": "", "columns": [{"name": "skip"}]},
    ]
    table_payload = semantic_config._build_prefill_table_payload(tables)
    alias_map = semantic_config._build_prefill_alias_map(tables)
    parsed = {
        "fields": [
            {"table": "orders", "column": "amount", "name": "金额，后续说明"},
            {"table": "missing", "column": "amount", "name": "跳过"},
            "skip",
        ],
        "metrics": [
            {"table": "orders", "column": "amount", "agg": "SUM", "name": "金额"},
            {"table": "orders", "source_column": "amount", "agg": "SUM", "name": "重复"},
            {"table": "orders", "column": "missing", "agg": "SUM", "name": "跳过"},
            {"table": "orders", "column": "amount", "agg": "UNSAFE", "name": "跳过"},
            "skip",
        ],
        "knowledge": {"business_flow": "下单 -> 支付", "other": "drop"},
    }

    assert semantic_config._normalize_prefill_display_name("订单金额，越长越好", "amount") == "订单金额"
    assert semantic_config._normalize_prefill_display_name("关联 orders.amount 的金额", "amount") == "关联ordersamount的"
    assert semantic_config._normalize_prefill_display_name("", "amount") == "amount"
    deduped_long_name = semantic_config._dedupe_prefill_display_names([
        {"table_alias": "long_alias", "name": "字段名很长很长很长很长很长很长很长很长很长"},
        {"table_alias": "other_alias", "name": "字段名很长很长很长很长很长很长很长很长很长"},
    ])
    assert len(deduped_long_name[0]["name"]) == semantic_config.PREFILL_DISPLAY_NAME_MAX_LENGTH
    assert deduped_long_name[0]["name"] != deduped_long_name[1]["name"]
    assert semantic_config._build_prefill_alias_map(tables) == {"orders": ["buyer_orders", "seller_orders"]}
    assert table_payload == [{"table": "orders", "description": "", "columns": [{"name": "amount", "type": "decimal", "description": ""}]}]
    assert semantic_config._parse_prefill_dimension_name_map({"fields": "bad"}, table_payload) == {}
    assert semantic_config._parse_prefill_dimension_name_map(parsed, table_payload) == {"orders.amount": "金额"}
    dimension_fields = semantic_config._build_prefill_dimension_fields(table_payload, alias_map, {"orders.amount": "金额"})
    metrics = semantic_config._parse_metric_items(parsed, table_payload, max_metrics=5, table_alias_map=alias_map)
    assert [item["table_alias"] for item in dimension_fields] == ["buyer_orders", "seller_orders"]
    assert [item["table_alias"] for item in metrics] == ["buyer_orders", "seller_orders"]
    assert len(metrics) == 2
    assert semantic_config._parse_metric_items({"metrics": "bad"}, table_payload) == []
    assert semantic_config._filter_prefill_knowledge(parsed["knowledge"], generate_business_flow=False) == {}
    assert semantic_config._filter_prefill_knowledge(parsed["knowledge"], generate_business_flow=True) == {"business_flow": "下单 -> 支付"}
    assert semantic_config._filter_prefill_knowledge("bad", True) == {}
    assert semantic_config._normalize_prefill_knowledge(parsed, True) == {"business_flow": "下单 -> 支付"}
    assert semantic_config._prefill_knowledge_missing({}, True) is True
    assert semantic_config._prefill_knowledge_missing({"business_flow": "已生成"}, True) is False
    assert semantic_config._normalize_prefill_item({"table": "", "column": "id", "role": "dimension"}) is None
    assert semantic_config._normalize_prefill_item({"table": "orders", "column": "id", "role": "bad"}) is None
    assert semantic_config._normalize_prefill_item({"table": "orders", "column": "id", "is_metric": True})["role"] == "metric"
    assert semantic_config._normalize_prefill_item({"table": "orders", "column": "id", "is_dimension": True})["role"] == "dimension"
    assert semantic_history_learning_service._extract_expression_columns("SUM(.amount) + ` ` + t.price + orders.") == {"amount", "price"}


def test_prefill_helpers_for_detail_and_aggregated(monkeypatch):
    tables = [{"table_name": "orders", "columns": [{"name": "amount"}, {"name": "city"}]}]
    calls = []

    async def fake_metric_recommendation(table_payload, business_context="", max_metrics=10, **_kwargs):
        calls.append({"table_payload": table_payload, "business_context": business_context, "max_metrics": max_metrics})
        return {
            "fields": [
                {"table": "orders", "table_alias": "orders", "column": "amount", "role": "dimension", "expression": "`amount`", "name": "订单金额"},
                {"table": "orders", "table_alias": "orders", "column": "city", "role": "dimension", "expression": "`city`", "name": "城市"},
            ],
            "metrics": [{"table": "orders", "table_alias": "orders", "column": "amount", "source_column": "amount", "role": "metric", "field_name": "amount_sum", "name": "金额合计", "agg": "SUM", "expression": "SUM(`amount`)"}],
        }

    monkeypatch.setattr(semantic_config, "_call_metric_recommendation_llm", fake_metric_recommendation)
    detail = asyncio.run(semantic_config._prefill_by_llm(tables, "detail"))
    aggregated = asyncio.run(semantic_config._prefill_by_llm(tables, "aggregated", business_context="订单金额常做汇总"))

    assert len(calls) == 2
    assert calls[1]["business_context"] == "订单金额常做汇总"
    assert detail["fields"] == [
        {"table": "orders", "table_alias": "orders", "column": "amount", "role": "dimension", "expression": "`amount`", "name": "订单金额"},
        {"table": "orders", "table_alias": "orders", "column": "city", "role": "dimension", "expression": "`city`", "name": "城市"},
        {"table": "orders", "table_alias": "orders", "column": "amount", "source_column": "amount", "role": "metric", "field_name": "amount_sum", "name": "金额合计", "agg": "SUM", "expression": "SUM(`amount`)"},
    ]
    assert aggregated["fields"][0]["role"] == "dimension"
    assert aggregated["fields"][-1]["role"] == "metric"


def test_auto_prefill_prompt_generation_request_branches():
    prompt = build_auto_prefill_prompt(
        model_guidance=build_prefill_model_guidance("detail"),
        table_payload=[{"table": "orders", "columns": [{"name": "amount"}]}],
        generate_business_flow=True,
    )
    knowledge_prompt = build_auto_prefill_knowledge_prompt(
        table_payload=[{"table": "orders", "columns": [{"name": "amount"}]}],
        generate_business_flow=True,
    )

    assert '"generation_request": {"business_flow": true}' in prompt
    assert '"business_flow":"业务运转说明"' in prompt
    assert "business_flow 面向业务整体" in prompt
    assert '"generation_request": {"business_flow": true}' in knowledge_prompt
    assert '"business_flow":"业务运转说明"' in knowledge_prompt


def test_auto_prefill_endpoint_uses_detail_default(monkeypatch):
    class FakeManager:
        async def get_datasource_async(self, datasource_id):
            return {"id": datasource_id, "ds_tables": [{"table_name": "orders", "columns": [{"name": "id"}]}]}

        async def get_semantic_config_async(self, datasource_id, config_id):
            return {"id": config_id, "scope_type": "private", "create_by": "user_a"}

    monkeypatch.setattr(semantic_config, "get_datasource_manager", lambda: FakeManager())
    monkeypatch.setattr(semantic_config, "_get_current_user", lambda _request: SimpleNamespace(user_id="user_a"))
    monkeypatch.setattr(semantic_config, "_prefill_by_llm", lambda *_args, **_kwargs: asyncio.sleep(0, result={"fields": [{"table": "orders", "column": "id", "role": "dimension", "expression": "`id`"}], "knowledge": {}}))

    response = asyncio.run(semantic_config.auto_prefill_semantic_fields(
        "ds_a",
        "cfg_a",
        SemanticAutoPrefillRequest(selected_tables=["orders"], table_model_type="detail"),
        object(),
    ))
    payload = json.loads(response.body.decode("utf-8"))

    assert payload["status"] == "success"
    assert payload["data"]["fields"][0]["role"] == "dimension"


def test_semantic_prefill_endpoint_returns_error_for_incomplete_llm(monkeypatch):
    class FakeManager:
        async def get_datasource_async(self, datasource_id):
            return {"id": datasource_id}

        async def get_semantic_config_async(self, datasource_id, config_id):
            return {"id": config_id, "scope_type": "private", "create_by": "user_a"}

    monkeypatch.setattr(semantic_config, "get_datasource_manager", lambda: FakeManager())
    monkeypatch.setattr(semantic_config, "_get_current_user", lambda _request: SimpleNamespace(user_id="user_a"))
    monkeypatch.setattr(semantic_config, "_prefill_by_llm", lambda *_args, **_kwargs: asyncio.sleep(0, result=None))

    response = asyncio.run(semantic_config.semantic_prefill(SemanticPrefillRequest(
        datasource_id="ds_a",
        config_id="cfg_a",
        tables=[{"table_name": "orders", "columns": [{"name": "id"}]}],
        table_model_type="aggregated",
    ), object()))
    payload = json.loads(response.body.decode("utf-8"))

    assert payload["status"] == "error"
    assert payload["code"] == ErrorCode.CHATDATA_SERVICE_ERROR


def test_semantic_prefill_additional_edge_branches(monkeypatch):
    table_payload = [
        {"table": "", "columns": [{"name": "skip"}]},
        {"table": "orders", "columns": [{"name": ""}, {"name": "amount"}]},
    ]
    assert semantic_config._build_prefill_dimension_fields(table_payload) == [
        {
            "table": "orders",
            "table_alias": "orders",
            "column": "amount",
            "role": "dimension",
            "expression": "`amount`",
            "name": "amount",
        }
    ]
    assert semantic_config._normalize_prefill_display_name("", "") == ""
    assert semantic_config._normalize_prefill_display_name("超长别名前缀_字段名", "字段名")[:2]

    duplicate_items = semantic_config._dedupe_prefill_display_names([
        {"name": "金额", "table_alias": "买方订单"},
        {"name": "金额", "table_alias": "卖方订单"},
        {"name": "金额", "table_alias": ""},
    ])
    assert len({item["name"] for item in duplicate_items}) == 3

    parsed = {"metrics": [{"table": "orders", "column": "amount", "agg": "SUM", "name": "金额"}]}
    metrics = semantic_config._parse_metric_items(
        parsed,
        [{"table": "orders", "columns": [{"name": "amount"}]}],
        max_metrics=1,
        table_alias_map={"orders": ["买方订单", "卖方订单"]},
    )
    assert len(metrics) == 1

    class FakeFile:
        filename = "big.txt"

        async def read(self):
            return b"x" * (semantic_config.SEMANTIC_PREFILL_CONTEXT_FILE_MAX_SIZE + 1)

    too_big = _response_payload(asyncio.run(semantic_config.semantic_prefill_context_file(FakeFile())))
    assert too_big["code"] == ErrorCode.CHATDATA_VALIDATION_FAILED

    class BadFile:
        filename = "bad.bin"

        async def read(self):
            return b"abc"

    monkeypatch.setattr(semantic_config, "extract_text_from_file_bytes", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad file")))
    bad = _response_payload(asyncio.run(semantic_config.semantic_prefill_context_file(BadFile())))
    assert bad["code"] == ErrorCode.CHATDATA_VALIDATION_FAILED


def test_semantic_config_update_delete_toggle_error_edges(monkeypatch):
    manager = _patch_semantic_route_user_and_manager(monkeypatch)

    missing_ds = _response_payload(asyncio.run(semantic_config.update_semantic_config(
        "missing",
        "cfg_a",
        SemanticConfigUpdateRequest(name="x"),
        object(),
    )))
    invalid_update = _response_payload(asyncio.run(semantic_config.update_semantic_config(
        "ds_a",
        "cfg_a",
        SemanticConfigUpdateRequest(metrics=[{"name": "坏指标", "table": "orders", "expression": "SUM(missing_col)"}]),
        object(),
    )))

    original_update = manager.update_semantic_config_async

    async def update_missing_after_validate(**kwargs):
        return None

    manager.update_semantic_config_async = update_missing_after_validate
    vanished_update = _response_payload(asyncio.run(semantic_config.update_semantic_config(
        "ds_a",
        "cfg_a",
        SemanticConfigUpdateRequest(name="x"),
        object(),
    )))
    vanished_toggle = _response_payload(asyncio.run(semantic_config.toggle_semantic_config("ds_a", "cfg_a", True, object())))

    manager.update_semantic_config_async = original_update

    async def delete_returns_false(datasource_id, config_id, update_by="", update_by_name=""):
        return False

    manager.delete_semantic_config_async = delete_returns_false
    vanished_delete = _response_payload(asyncio.run(semantic_config.delete_semantic_config("ds_a", "cfg_a", object())))

    assert missing_ds["code"] == ErrorCode.CHATDATA_NOT_FOUND
    assert invalid_update["code"] == ErrorCode.CHATDATA_VALIDATION_FAILED
    assert vanished_update["code"] == ErrorCode.CHATDATA_NOT_FOUND
    assert vanished_toggle["code"] == ErrorCode.CHATDATA_NOT_FOUND
    assert vanished_delete["code"] == ErrorCode.CHATDATA_NOT_FOUND


def _response_payload(response):
    return json.loads(response.body.decode("utf-8"))


class _FakeSemanticManager:
    def __init__(self):
        self.datasource = {
            "id": "ds_a",
            "source_type": "mysql",
            "ds_tables": [
                {
                    "table_name": "orders",
                    "columns": [
                        {"name": "amount", "type": "decimal"},
                        {"name": "city", "type": "varchar"},
                        {"name": "order_id", "type": "varchar"},
                    ],
                }
            ],
        }
        self.config = {
            "id": "cfg_a",
            "name": "订单分析",
            "create_by": "user_a",
            "scope_type": "private",
            "metrics": [{"name": "销售额", "table": "orders", "expression": "SUM(amount)"}],
            "dimensions": [{"name": "城市", "table": "orders", "column": "city", "logical_type": "text"}],
            "relationships": [],
        }
        self.public_config = {
            "id": "cfg_public",
            "name": "公共指标",
            "create_by": "admin",
            "scope_type": "public",
            "metrics": [{"name": "销售额", "table": "orders", "expression": "SUM(amount)"}],
            "dimensions": [{"name": "城市", "table": "orders", "column": "city", "logical_type": "text"}],
            "relationships": [],
        }
        self.created_kwargs = None
        self.updated_kwargs = None
        self.deleted = None

    async def has_datasource_async(self, datasource_id):
        return datasource_id == "ds_a"

    async def get_datasource_async(self, datasource_id):
        return self.datasource if datasource_id == "ds_a" else None

    async def get_semantic_configs_async(self, datasource_id, user_id=""):
        if datasource_id != "ds_a":
            return []
        configs = [dict(self.public_config), dict(self.config)]
        if not user_id:
            return configs
        return [
            config for config in configs
            if config.get("scope_type") == "public"
            or (config.get("scope_type") == "private" and config.get("create_by") == user_id)
        ]

    async def get_semantic_config_async(self, datasource_id, config_id):
        if datasource_id != "ds_a":
            return None
        if config_id == "cfg_a":
            return dict(self.config)
        if config_id == "cfg_public":
            return dict(self.public_config)
        return None

    async def create_semantic_config_async(self, **kwargs):
        self.created_kwargs = dict(kwargs)
        return {"id": "cfg_new", "create_by": kwargs.get("create_by"), "scope_type": kwargs.get("scope_type"), **kwargs}

    async def update_semantic_config_async(self, **kwargs):
        self.updated_kwargs = dict(kwargs)
        if kwargs.get("config_id") == "cfg_a":
            result = dict(self.config)
        elif kwargs.get("config_id") == "cfg_public":
            result = dict(self.public_config)
        else:
            return None
        result.update({key: value for key, value in kwargs.items() if key not in {"datasource_id", "config_id"}})
        return result

    async def delete_semantic_config_async(self, datasource_id, config_id, update_by="", update_by_name=""):
        self.deleted = (datasource_id, config_id)
        return datasource_id == "ds_a" and config_id in {"cfg_a", "cfg_public"}


def _patch_semantic_route_user_and_manager(monkeypatch, manager=None, user_id="user_a"):
    fake_manager = manager or _FakeSemanticManager()
    monkeypatch.setattr(semantic_config, "_get_current_user", lambda _request: SimpleNamespace(user_id=user_id))
    monkeypatch.setattr(semantic_config, "get_datasource_manager", lambda: fake_manager)
    return fake_manager


def test_semantic_config_list_create_update_delete_toggle_routes(monkeypatch):
    manager = _patch_semantic_route_user_and_manager(monkeypatch)

    list_payload = _response_payload(asyncio.run(semantic_config.list_semantic_configs("ds_a", object())))
    my_list_payload = _response_payload(asyncio.run(semantic_config.list_my_semantic_configs("ds_a", object())))
    admin_list_payload = _response_payload(asyncio.run(semantic_config.list_admin_semantic_configs("ds_a", object())))
    missing_list = _response_payload(asyncio.run(semantic_config.list_semantic_configs("missing", object())))
    create_payload = SemanticConfigCreateRequest(
        name="新指标集",
        description="描述",
        enabled=True,
        metrics=[{"name": "销售额", "table": "orders", "expression": "SUM(amount)"}],
        dimensions=[{"name": "城市", "table": "orders", "column": "city", "logical_type": "text"}],
        relationships=[],
    )
    create_response = _response_payload(asyncio.run(semantic_config.create_semantic_config("ds_a", create_payload, object())))
    admin_create_response = _response_payload(asyncio.run(semantic_config.create_admin_semantic_config("ds_a", create_payload, object())))
    update_payload = SemanticConfigUpdateRequest(
        name="订单分析V2",
        metrics=[{"name": "销售额", "table": "orders", "expression": "SUM(amount)"}],
        dimensions=[{"name": "城市", "table": "orders", "column": "city", "logical_type": "text"}],
        relationships=[],
    )
    update_response = _response_payload(asyncio.run(semantic_config.update_semantic_config("ds_a", "cfg_a", update_payload, object())))
    admin_update_response = _response_payload(asyncio.run(semantic_config.update_admin_semantic_config("ds_a", "cfg_public", update_payload, object())))
    toggle_response = _response_payload(asyncio.run(semantic_config.toggle_semantic_config("ds_a", "cfg_a", False, object())))
    admin_toggle_response = _response_payload(asyncio.run(semantic_config.toggle_admin_semantic_config("ds_a", "cfg_public", False, object())))
    delete_response = _response_payload(asyncio.run(semantic_config.delete_semantic_config("ds_a", "cfg_a", object())))
    admin_delete_response = _response_payload(asyncio.run(semantic_config.delete_admin_semantic_config("ds_a", "cfg_public", object())))

    assert [item["id"] for item in list_payload["data"]["configs"]] == ["cfg_public", "cfg_a"]
    assert [item["can_manage"] for item in list_payload["data"]["configs"]] == [False, True]
    assert [item["id"] for item in my_list_payload["data"]["configs"]] == ["cfg_a"]
    assert [item["id"] for item in admin_list_payload["data"]["configs"]] == ["cfg_public"]
    assert admin_list_payload["data"]["configs"][0]["can_manage"] is True
    assert missing_list["code"] == ErrorCode.CHATDATA_NOT_FOUND
    assert create_response["data"]["name"] == "新指标集"
    assert create_response["data"]["scope_type"] == "private"
    assert admin_create_response["data"]["scope_type"] == "public"
    assert manager.created_kwargs["scope_type"] == "public"
    assert update_response["data"]["name"] == "订单分析V2"
    assert admin_update_response["data"]["can_manage"] is True
    assert manager.updated_kwargs["update_by"] == "user_a"
    assert toggle_response["data"]["id"] == "cfg_a"
    assert toggle_response["data"]["enabled"] is False
    assert admin_toggle_response["data"]["id"] == "cfg_public"
    assert admin_toggle_response["data"]["enabled"] is False
    assert delete_response["data"]["config_id"] == "cfg_a"
    assert admin_delete_response["data"]["config_id"] == "cfg_public"
    assert manager.deleted == ("ds_a", "cfg_public")


def test_semantic_config_route_error_mappings(monkeypatch):
    manager = _patch_semantic_route_user_and_manager(monkeypatch, user_id="other_user")
    other_user_update = _response_payload(asyncio.run(semantic_config.update_semantic_config(
        "ds_a",
        "cfg_a",
        SemanticConfigUpdateRequest(name="其他管理员可维护"),
        object(),
    )))
    other_user_delete = _response_payload(asyncio.run(semantic_config.delete_semantic_config("ds_a", "cfg_a", object())))
    other_user_toggle = _response_payload(asyncio.run(semantic_config.toggle_semantic_config("ds_a", "cfg_a", True, object())))

    _patch_semantic_route_user_and_manager(monkeypatch, manager=manager, user_id="user_a")
    invalid_create = _response_payload(asyncio.run(semantic_config.create_semantic_config(
        "ds_a",
        SemanticConfigCreateRequest(
            name="坏指标集",
            metrics=[{"name": "坏指标", "table": "orders", "expression": "SUM(missing_col)"}],
            dimensions=[],
            relationships=[],
        ),
        object(),
    )))
    missing_update = _response_payload(asyncio.run(semantic_config.update_semantic_config("ds_a", "missing", SemanticConfigUpdateRequest(name="x"), object())))

    assert other_user_update["code"] == ErrorCode.CHATDATA_SEMANTIC_CONFIG_PERMISSION_DENIED
    assert other_user_delete["code"] == ErrorCode.CHATDATA_SEMANTIC_CONFIG_PERMISSION_DENIED
    assert other_user_toggle["code"] == ErrorCode.CHATDATA_SEMANTIC_CONFIG_PERMISSION_DENIED
    assert invalid_create["code"] == ErrorCode.CHATDATA_VALIDATION_FAILED
    assert missing_update["code"] == ErrorCode.CHATDATA_NOT_FOUND


def test_history_learning_route_collects_agent_builder_and_sql_samples(monkeypatch):
    _patch_semantic_route_user_and_manager(monkeypatch)

    execution_time = datetime(2026, 6, 2, 10, 0, 0)
    rows = [
        (
            SimpleNamespace(
                execution_id="exec_agent",
                task_id="task_agent",
                sql_status="failed",
                sql_text="select sum(amount) from orders",
                error_message="字段不存在",
                finished_time=execution_time,
                started_time=None,
                create_time=execution_time,
            ),
            SimpleNamespace(
                query_type="agent",
                request_payload=json.dumps({"semantic_config_id": "cfg_a", "query_text": "销售额不对", "conversation_id": "conv_a"}, ensure_ascii=False),
                error_message="",
            ),
        ),
        (
            SimpleNamespace(
                execution_id="exec_builder",
                task_id="task_builder",
                sql_status="succeeded",
                sql_text="select city, sum(amount) from orders group by city",
                error_message="",
                finished_time=execution_time,
                started_time=None,
                create_time=execution_time,
            ),
            SimpleNamespace(
                query_type="builder",
                request_payload=json.dumps({"semantic_config_id": "cfg_a", "query_text": "按城市看销售额", "semantic_builder_config": {"dimensions": ["城市"]}}, ensure_ascii=False),
                error_message="",
            ),
        ),
        (
            SimpleNamespace(
                execution_id="exec_sql",
                task_id="task_sql",
                sql_status="succeeded",
                sql_text="select sum(amount) from orders",
                error_message="",
                finished_time=execution_time,
                started_time=None,
                create_time=execution_time,
            ),
            SimpleNamespace(
                query_type="sql",
                request_payload=json.dumps({"query_text": "SQL查销售额"}, ensure_ascii=False),
                error_message="",
            ),
        ),
    ]

    class FakeResult:
        def all(self):
            return rows

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def execute(self, _stmt):
            return FakeResult()

    async def fake_load_history_context(conversation_id, user_id):
        assert user_id == "user_a"
        return {"user_turns": [{"content": f"{conversation_id}: 销售额不对", "timestamp": "t1"}], "tool_errors": []}

    async def fake_generate_suggestions(datasource, config, samples):
        return {
            "metrics": [{"name": "客单价", "source": "sql", "support_count": 1, "samples": samples[:1]}],
            "dimensions": [],
            "issues": [],
        }

    _patch_history_learning_service_session(monkeypatch, lambda: FakeSession())
    monkeypatch.setattr(semantic_config, "_load_history_conversation_context", fake_load_history_context)
    monkeypatch.setattr(semantic_config, "_generate_history_learning_suggestions_by_llm", fake_generate_suggestions)
    monkeypatch.setattr(semantic_config, "extract_physical_tables", lambda sql, source_type="mysql": SimpleNamespace(success=True, tables={"orders"}, error=""))

    response = _response_payload(asyncio.run(semantic_config.learn_semantic_config_from_history(
        "ds_a",
        "cfg_a",
        SemanticHistoryLearningRequest(days=7, limit=3, sources=["agent", "builder", "sql"], include_failed=True),
        object(),
    )))

    assert response["status"] == "success"
    assert response["data"]["matched_count"] == 3
    assert response["data"]["source_stats"]["agent"]["matched"] == 1
    assert response["data"]["source_stats"]["builder"]["matched"] == 1
    assert response["data"]["source_stats"]["sql"]["matched"] == 1
    assert response["data"]["samples"][0]["conversation_context"]["user_turns"][0]["content"].startswith("conv_a")
    assert response["data"]["samples"][1]["semantic_builder_config"]["dimensions"] == ["城市"]
    assert response["data"]["suggestions"]["metrics"][0]["name"] == "客单价"


def test_history_learning_route_validates_sources_and_missing_objects(monkeypatch):
    _patch_semantic_route_user_and_manager(monkeypatch)

    empty_sources = _response_payload(asyncio.run(semantic_config.learn_semantic_config_from_history(
        "ds_a",
        "cfg_a",
        SemanticHistoryLearningRequest(days=7, limit=3, sources=[], include_failed=True),
        object(),
    )))
    unsupported_sources = _response_payload(asyncio.run(semantic_config.learn_semantic_config_from_history(
        "ds_a",
        "cfg_a",
        SimpleNamespace(days=7, limit=3, sources=["agent", "bad"], include_failed=True),
        object(),
    )))
    missing_datasource = _response_payload(asyncio.run(semantic_config.learn_semantic_config_from_history(
        "missing",
        "cfg_a",
        SemanticHistoryLearningRequest(days=7, limit=3, sources=["agent"], include_failed=True),
        object(),
    )))
    missing_config = _response_payload(asyncio.run(semantic_config.learn_semantic_config_from_history(
        "ds_a",
        "missing",
        SemanticHistoryLearningRequest(days=7, limit=3, sources=["agent"], include_failed=True),
        object(),
    )))

    assert empty_sources["code"] == ErrorCode.CHATDATA_VALIDATION_FAILED
    assert unsupported_sources["code"] == ErrorCode.CHATDATA_VALIDATION_FAILED
    assert missing_datasource["code"] == ErrorCode.CHATDATA_NOT_FOUND
    assert missing_config["code"] == ErrorCode.CHATDATA_NOT_FOUND


def test_semantic_helper_edge_cases():
    class FakeManager:
        async def get_semantic_configs_async(self, datasource_id, user_id=""):
            assert datasource_id == "ds_a"
            assert user_id == "user_a"
            return [
                {"id": "cfg_a", "scope_type": "public", "create_by": "admin"},
                {"id": "cfg_b", "scope_type": "private", "create_by": "user_a"},
            ]

    configs = asyncio.run(semantic_config._get_semantic_configs_for_user(FakeManager(), "ds_a", "user_a"))

    assert configs[0]["can_manage"] is False
    assert configs[1]["can_manage"] is True
    assert semantic_config_validator.expression_uses_physical_column("", {"amount"}) is False
    assert semantic_config_validator.expression_uses_physical_column("SUM(o.AMOUNT)", {"amount"}) is True
    assert semantic_config_validator.expression_uses_physical_column("SUM(total_amount)", {"amount"}) is False
    assert semantic_config._quote_identifier("a`b") == "`a``b`"
    assert semantic_history_learning_service._parse_json_object({"a": 1}) == {"a": 1}
    assert semantic_history_learning_service._parse_json_object("{bad") == {}
    assert semantic_history_learning_service._parse_json_object("[1]") == {}
    assert semantic_history_learning_service._extract_history_task_semantic_id({"semantic_config_id": " cfg "}) == "cfg"
    assert semantic_history_learning_service._extract_history_task_query_text({"query_text": " 问题 "}) == "问题"
    assert semantic_history_learning_service._extract_history_task_conversation_id({"conversation_id": " conv "}) == "conv"
    assert semantic_history_learning_service._semantic_scope_tables({"metrics": [{}], "dimensions": [{"table": ""}], "relationships": [{"left_table": "a", "right_table": "b"}]}) == {"a", "b"}
    assert semantic_config._history_belongs_to_config("sql", {}, "select 1", "cfg", {"metrics": [], "dimensions": []}, "mysql")[:2] == (False, "empty_scope")


def test_list_semantic_configs_passes_current_user_to_manager(monkeypatch):
    captured = {}

    class FakeManager:
        async def has_datasource_async(self, datasource_id):
            assert datasource_id == "ds_a"
            return True

        async def get_semantic_configs_async(self, datasource_id, user_id=""):
            captured["datasource_id"] = datasource_id
            captured["user_id"] = user_id
            return [{"id": "public", "scope_type": "public"}, {"id": "mine", "scope_type": "private", "create_by": "user_a"}]

    monkeypatch.setattr(semantic_config, "_get_current_user", lambda _request: SimpleNamespace(user_id="user_a", username="张三"))
    monkeypatch.setattr(semantic_config, "get_datasource_manager", lambda: FakeManager())

    payload = _response_payload(asyncio.run(semantic_config.list_semantic_configs("ds_a", object())))

    assert captured == {"datasource_id": "ds_a", "user_id": "user_a"}
    assert [item["id"] for item in payload["data"]["configs"]] == ["public", "mine"]
    assert [item["can_manage"] for item in payload["data"]["configs"]] == [False, True]


def test_history_context_loader_branches(monkeypatch):
    calls = []

    async def fake_get_agent_id(_db_path, conversation_id, user_id):
        calls.append(("agent", conversation_id, user_id))
        if conversation_id == "boom":
            raise RuntimeError("conversation error")
        return {
            "chat": semantic_history_learning_service.AGENT_ID,
            "nonlist": semantic_history_learning_service.AGENT_ID,
            "empty": semantic_history_learning_service.AGENT_ID,
            "unknown": "other_agent",
        }.get(conversation_id, semantic_history_learning_service.AGENT_ID)

    async def fake_get_detail(conversation_id, user_id):
        calls.append(("detail", conversation_id, user_id))
        if conversation_id == "chat":
            return {"messages": [{"role": "user", "content": "查询销售额", "timestamp": "t1"}]}
        if conversation_id == "nonlist":
            return {"messages": "bad"}
        if conversation_id == "empty":
            return {"messages": [{"role": "assistant", "content": "只有回复"}]}
        return {"messages": []}

    monkeypatch.setattr(semantic_history_learning_service.conversation, "get_agent_id", fake_get_agent_id)
    monkeypatch.setattr(semantic_history_learning_service.conversation, "get_detail", fake_get_detail)

    assert asyncio.run(semantic_config._load_history_conversation_context("", "user_a")) is None
    assert asyncio.run(semantic_config._load_history_conversation_context("unknown", "user_a")) is None
    assert asyncio.run(semantic_config._load_history_conversation_context("nonlist", "user_a")) is None
    assert asyncio.run(semantic_config._load_history_conversation_context("empty", "user_a")) is None
    assert asyncio.run(semantic_config._load_history_conversation_context("boom", "user_a")) is None
    assert asyncio.run(semantic_config._load_history_conversation_context("chat", "user_a"))["user_turns"][0]["content"] == "查询销售额"
    assert all(call[-1] == "user_a" for call in calls)


def test_history_suggestion_normalization_invalid_and_duplicate_cases():
    datasource = {"ds_tables": [{"table_name": "orders", "columns": [{"name": "amount"}, {"name": "city"}]}]}
    sample_map = {"exec_1": {"execution_id": "exec_1", "source": "sql"}}
    indexes = {
        "metric_names": {"销售额"},
        "metric_expressions": {"orders.sum(amount)"},
        "dimension_names": {"城市"},
        "dimension_columns": {"orders.city"},
        "dimension_fields": {"orders.city_alias"},
    }

    assert semantic_history_learning_service._extract_expression_columns("") == set()
    assert semantic_history_learning_service._extract_expression_columns("SUM(`amount`) + order_time") == {"amount", "order_time"}
    assert semantic_history_learning_service._is_existing_history_suggestion({"name": "销售额"}, "metrics", indexes) is True
    assert semantic_history_learning_service._is_existing_history_suggestion({"table": "orders", "expression": "SUM(amount)"}, "metrics", indexes) is True
    assert semantic_history_learning_service._is_existing_history_suggestion({"name": "城市"}, "dimensions", indexes) is True
    assert semantic_history_learning_service._is_existing_history_suggestion({"table": "orders", "column": "city"}, "dimensions", indexes) is True
    assert semantic_history_learning_service._is_existing_history_suggestion({"table": "orders", "field_name": "city_alias"}, "dimensions", indexes) is True
    assert semantic_history_learning_service._is_existing_history_suggestion({}, "issues", indexes) is False
    assert semantic_history_learning_service._normalize_history_suggestion_item("bad", "metrics", sample_map, datasource) is None
    assert semantic_history_learning_service._normalize_history_suggestion_item({"source": "api"}, "metrics", sample_map, datasource) is None
    assert semantic_history_learning_service._normalize_history_suggestion_item({"source": "sql", "confidence": "bad"}, "metrics", sample_map, datasource) is None
    assert semantic_history_learning_service._normalize_history_suggestion_item({"source": "sql", "confidence": "high", "sample_execution_ids": "exec_1"}, "metrics", sample_map, datasource) is None
    assert semantic_history_learning_service._normalize_history_suggestion_item({"source": "sql", "confidence": "high", "sample_execution_ids": ["missing"], "support_count": 1}, "metrics", sample_map, datasource) is None
    assert semantic_history_learning_service._normalize_history_suggestion_item({"source": "sql", "confidence": "high", "sample_execution_ids": ["exec_1"], "support_count": "bad"}, "metrics", sample_map, datasource) is None
    assert semantic_history_learning_service._normalize_history_suggestion_item({"source": "sql", "confidence": "high", "sample_execution_ids": ["exec_1"], "support_count": 1, "name": "", "reason": ""}, "metrics", sample_map, datasource) is None
    assert semantic_history_learning_service._normalize_history_suggestion_item({"source": "sql", "confidence": "high", "sample_execution_ids": ["exec_1"], "support_count": 1, "name": "新维度", "reason": "历史过滤", "type": "bad"}, "dimensions", sample_map, datasource) is None
    assert semantic_history_learning_service._normalize_history_suggestion_item({"source": "sql", "confidence": "high", "sample_execution_ids": ["exec_1"], "support_count": 1, "title": "", "content": "", "reason": ""}, "issues", sample_map, datasource) is None

    result = semantic_history_learning_service._normalize_history_learning_result(
        {
            "metrics": [
                {"source": "sql", "confidence": "high", "sample_execution_ids": ["exec_1"], "support_count": 1, "name": "客单价", "table": "orders", "expression": "AVG(amount)", "reason": "历史查询"},
                {"source": "sql", "confidence": "medium", "sample_execution_ids": ["exec_1"], "support_count": 2, "name": "客单价", "table": "orders", "expression": "AVG(amount)", "reason": "重复"},
            ],
            "dimensions": "bad",
            "issues": [],
        },
        [{"execution_id": "exec_1", "source": "sql"}],
        datasource,
        {},
    )

    assert len(result["metrics"]) == 1
    assert result["dimensions"] == []


def test_generate_history_learning_suggestions_by_llm(monkeypatch):
    datasource = {"ds_tables": [{"table_name": "orders", "columns": [{"name": "amount"}]}]}
    config = {"metrics": [{"name": "销售额", "table": "orders", "expression": "SUM(amount)"}], "dimensions": []}
    samples = [{"execution_id": "exec_1", "source": "sql", "sql": "select avg(amount) from orders"}]

    assert asyncio.run(semantic_config._generate_history_learning_suggestions_by_llm(datasource, config, [])) == {"metrics": [], "dimensions": [], "issues": []}

    captured = {}

    class FakeLlm:
        def __init__(self, content):
            self.content = content

        async def ainvoke(self, messages):
            assert messages
            captured["messages"] = messages
            return SimpleNamespace(content=self.content)

    monkeypatch.setattr(semantic_config, "get_chat_data_llm", lambda **_kwargs: FakeLlm("[]"))
    with pytest.raises(ValueError):
        asyncio.run(semantic_config._generate_history_learning_suggestions_by_llm(datasource, config, samples))

    monkeypatch.setattr(semantic_config, "get_chat_data_llm", lambda **_kwargs: FakeLlm(json.dumps({
        "suggestions": {
            "metrics": [
                {
                    "source": "sql",
                    "confidence": "high",
                    "sample_execution_ids": ["exec_1"],
                    "support_count": 1,
                    "name": "客单价",
                    "table": "orders",
                    "expression": "AVG(amount)",
                    "reason": "历史查询",
                }
            ],
            "dimensions": [],
            "issues": [],
        }
    }, ensure_ascii=False)))

    suggestions = asyncio.run(semantic_config._generate_history_learning_suggestions_by_llm(datasource, config, samples))
    assert getattr(captured["messages"][0], "type", "") == "human"
    assert suggestions["metrics"][0]["name"] == "客单价"


def test_prefill_parse_and_error_branches(monkeypatch):
    assert semantic_config._normalize_prefill_item({"table": "", "column": "id", "role": "dimension"}) is None
    assert semantic_config._normalize_prefill_item({"table": "orders", "column": "id", "role": "bad"}) is None
    assert semantic_config._normalize_prefill_item({"table": "orders", "column": "amount", "is_metric": True})["role"] == "metric"
    assert semantic_config._normalize_prefill_item({"table": "orders", "column": "city", "is_dimension": True})["role"] == "dimension"
    assert semantic_config._normalize_prefill_table_model_type("bad") == "detail"
    assert semantic_config._parse_prefill_fields('{"fields": "bad"}') == {}
    assert semantic_config._parse_prefill_fields(json.dumps({"fields": [{"table": "orders", "column": "amount", "role": "metric"}, "bad"]}))["orders.amount"]["role"] == "metric"
    parsed_with_metadata = semantic_config._parse_prefill_response(
        json.dumps({
            "fields": [{"table": "orders", "column": "amount", "role": "metric", "metadata_supplement": "1）金额单位为元"}],
            "knowledge": {"business_flow": "订单支付后形成成交", "metadata_supplement": "旧全局元数据"},
        }, ensure_ascii=False),
        generate_business_flow=True,
    )
    assert "metadata_supplement" not in parsed_with_metadata["fields"]["orders.amount"]
    assert parsed_with_metadata["knowledge"] == {"business_flow": "订单支付后形成成交"}
    assert asyncio.run(semantic_config._prefill_by_llm([], "detail")) == {"fields": [], "knowledge": {}}
    assert asyncio.run(semantic_config._prefill_by_llm([{"columns": [{"name": "id"}]}], "detail")) == {"fields": [], "knowledge": {}}

    async def fake_raise(*_args, **_kwargs):
        raise RuntimeError("llm failed")

    monkeypatch.setattr(semantic_config, "_call_metric_recommendation_llm", fake_raise)
    fallback_result = asyncio.run(semantic_config._prefill_by_llm([{"table_name": "orders", "columns": [{"name": "id"}]}], "aggregated"))
    assert fallback_result == {"fields": [{"table": "orders", "table_alias": "orders", "column": "id", "role": "dimension", "expression": "`id`", "name": "id"}], "knowledge": {}}

    async def fake_incomplete(*_args, **_kwargs):
        return []

    monkeypatch.setattr(semantic_config, "_call_metric_recommendation_llm", fake_incomplete)
    assert asyncio.run(semantic_config._prefill_by_llm([{"table_name": "orders", "columns": [{"name": "id"}]}], "aggregated")) == fallback_result

    datasource = {"ds_tables": [{"table_name": ""}, {"table_name": "orders"}, {"table_name": "users"}]}
    assert [item["table_name"] for item in semantic_config._resolve_prefill_tables(datasource, ["users"])] == ["users"]
    assert [item["table_name"] for item in semantic_config._resolve_prefill_tables(datasource, [])] == ["orders", "users"]


def test_prefill_prompt_includes_business_context(monkeypatch):
    captured = {}

    async def fake_recommend(table_payload, business_context="", max_metrics=10, **_kwargs):
        captured["table_payload"] = table_payload
        captured["business_context"] = business_context
        captured["max_metrics"] = max_metrics
        return [{"table": "orders", "column": "amount_sum", "source_column": "amount_sum", "role": "metric", "field_name": "amount_sum_sum", "name": "销售额合计", "agg": "SUM", "expression": "SUM(`amount_sum`)"}]

    monkeypatch.setattr(semantic_config, "_call_metric_recommendation_llm", fake_recommend)
    result = asyncio.run(semantic_config._prefill_by_llm(
        [{"table_name": "orders", "columns": [{"name": "amount_sum", "type": "decimal"}]}],
        "aggregated",
        business_context="销售额用于经营分析",
    ))

    assert result["fields"][0]["role"] == "dimension"
    assert result["fields"][1]["role"] == "metric"
    assert captured["business_context"] == "销售额用于经营分析"
    assert captured["max_metrics"] == semantic_config.PREFILL_MAX_RECOMMENDED_METRICS


def test_prefill_prompt_builders_cover_guidance_branches():
    table_payload = [{"table_name": "orders", "columns": [{"name": "amount", "type": "decimal"}]}]
    aggregated_guidance = build_prefill_model_guidance("aggregated")
    detail_guidance = build_prefill_model_guidance("detail")

    assert "预聚合表" in aggregated_guidance
    assert "明细表" in detail_guidance
    assert "generation_request" not in build_auto_prefill_prompt(
        model_guidance=detail_guidance,
        table_payload=table_payload,
        generate_business_flow=False,
    )
    assert '"knowledge":' not in build_auto_prefill_prompt(
        model_guidance=detail_guidance,
        table_payload=table_payload,
        generate_business_flow=False,
    )
    knowledge_without_business = build_auto_prefill_knowledge_prompt(
        table_payload=table_payload,
        generate_business_flow=False,
    )
    assert '"knowledge":{}' in knowledge_without_business
    assert '"business_flow"' not in knowledge_without_business
    assert '"generation_request":' not in knowledge_without_business
    with_business = build_auto_prefill_prompt(
        model_guidance=aggregated_guidance,
        table_payload=table_payload,
        generate_business_flow=True,
    )
    assert '"generation_request": {"business_flow": true}' in with_business
    assert "business_flow 面向业务整体" in with_business
    assert '"knowledge":{"business_flow":"业务运转说明"}' in build_auto_prefill_knowledge_prompt(
        table_payload=table_payload,
        generate_business_flow=True,
    )


def test_metric_recommendation_parser_filters_invalid_items():
    table_payload = [{"table": "orders", "columns": [
        {"name": "amount"},
        {"name": "price"},
        {"name": "order_id"},
    ]}]
    raw_metrics = [
        {"table": "orders", "column": "amount", "name": "金额合计", "agg": "SUM", "expression": "DROP TABLE x"},
        {"table": "orders", "column": "missing", "name": "缺失字段", "agg": "SUM"},
        {"table": "orders", "column": "price", "name": "价格中位数", "agg": "MEDIAN"},
        {"table": "orders", "column": "amount", "name": "重复金额", "agg": "SUM"},
        {"table": "orders", "column": "order_id", "name": "订单数", "agg": "COUNT"},
    ]
    parsed = semantic_config._parse_metric_recommendation_response(json.dumps({"metrics": raw_metrics}, ensure_ascii=False), table_payload, max_metrics=10)

    assert parsed == [{
        "table": "orders",
        "table_alias": "orders",
        "column": "amount",
        "source_column": "amount",
        "role": "metric",
        "field_name": "amount_sum",
        "name": "金额合计",
        "agg": "SUM",
        "expression": "SUM(`amount`)",
    }, {
        "table": "orders",
        "table_alias": "orders",
        "column": "order_id",
        "source_column": "order_id",
        "role": "metric",
        "field_name": "order_id_count",
        "name": "订单数",
        "agg": "COUNT",
        "expression": "COUNT(`order_id`)",
    }]


def test_metric_recommendation_parser_normalizes_display_name():
    table_payload = [{"table": "enrollments", "columns": [{"name": "student_id"}]}]
    parsed = semantic_config._parse_metric_recommendation_response(
        json.dumps({
            "metrics": [
                {
                    "table": "enrollments",
                    "column": "student_id",
                    "name": "选课学生ID，关联 students.student_id",
                    "agg": "COUNT",
                    "expression": "COUNT(`student_id`)",
                }
            ]
        }, ensure_ascii=False),
        table_payload,
        max_metrics=10,
    )

    assert parsed[0]["name"] == "选课学生ID"
    assert len(parsed[0]["name"]) <= semantic_config.PREFILL_DISPLAY_NAME_MAX_LENGTH


def test_metric_recommendation_parser_dedupes_names_by_table_alias():
    table_payload = [{"table": "users", "columns": [{"name": "score"}]}]
    parsed = semantic_config._parse_metric_recommendation_response(
        json.dumps({
            "metrics": [
                {
                    "table": "users",
                    "column": "score",
                    "name": "分数",
                    "agg": "SUM",
                    "expression": "SUM(`score`)",
                }
            ]
        }, ensure_ascii=False),
        table_payload,
        max_metrics=10,
        table_alias_map={"users": ["users", "users_2"]},
    )

    assert [item["table_alias"] for item in parsed] == ["users", "users_2"]
    assert parsed[0]["name"] != parsed[1]["name"]
    assert parsed[0]["name"].endswith("分数")
    assert parsed[1]["name"].endswith("分数")


def test_prefill_dimension_names_are_deduped_by_table_alias(monkeypatch):
    async def fake_recommend(*_args, **_kwargs):
        return {
            "fields": [
                {"table": "orders", "table_alias": "orders", "column": "order_id", "role": "dimension", "expression": "`order_id`", "name": "订单编号"},
                {"table": "order_items", "table_alias": "order_items", "column": "order_id", "role": "dimension", "expression": "`order_id`", "name": "订单编号"},
            ],
            "metrics": [],
        }

    monkeypatch.setattr(semantic_config, "_call_metric_recommendation_llm", fake_recommend)
    result = asyncio.run(semantic_config._prefill_by_llm([
        {"table_name": "orders", "table_alias": "orders", "columns": [{"name": "order_id", "description": "订单编号"}]},
        {"table_name": "order_items", "table_alias": "order_items", "columns": [{"name": "order_id", "description": "订单编号"}]},
    ]))

    names = [item["name"] for item in result["fields"]]
    assert len(names) == len(set(names))
    assert all(name.endswith("订单编号") or name.endswith("订单编号2") for name in names)


def test_prefill_knowledge_only_returns_requested_sections(monkeypatch):
    captured = {}

    async def fake_recommend(table_payload, business_context="", max_metrics=10, **_kwargs):
        captured["table_payload"] = table_payload
        captured["business_context"] = business_context
        captured["max_metrics"] = max_metrics
        return [{"table": "orders", "column": "amount", "source_column": "amount", "role": "metric", "field_name": "amount_sum", "name": "金额合计", "agg": "SUM", "expression": "SUM(`amount`)"}]

    monkeypatch.setattr(semantic_config, "_call_metric_recommendation_llm", fake_recommend)
    result = asyncio.run(semantic_config._prefill_by_llm(
        [{"table_name": "orders", "columns": [{"name": "amount", "type": "decimal"}]}],
        "aggregated",
        True,
    ))

    assert captured["table_payload"][0]["table"] == "orders"
    assert result["knowledge"] == {}


def test_prefill_knowledge_is_not_generated_when_missing(monkeypatch):
    calls = []

    async def fake_recommend(*_args, **_kwargs):
        calls.append((_args, _kwargs))
        return []

    monkeypatch.setattr(semantic_config, "_call_metric_recommendation_llm", fake_recommend)
    result = asyncio.run(semantic_config._prefill_by_llm(
        [{"table_name": "orders", "columns": [{"name": "amount", "type": "decimal", "description": "订单金额"}]}],
        "aggregated",
        True,
    ))

    assert len(calls) == 1
    assert result["knowledge"] == {}
    assert "metadata_supplement" not in result["fields"][0]


def test_prefill_context_file_extracts_text_and_rejects_suffix():
    result = extract_text_from_file_bytes("rules.txt", "状态 1 表示有效".encode("utf-8"))

    assert result == "状态 1 表示有效"

    with pytest.raises(ValueError):
        extract_text_from_file_bytes("rules.xlsx", b"bad")


def test_call_prefill_llm_uses_human_message(monkeypatch):
    captured = {}

    class FakeLlm:
        async def ainvoke(self, messages):
            captured["messages"] = messages
            return SimpleNamespace(content=json.dumps({
                "fields": [{
                    "table": "orders",
                    "column": "amount",
                    "role": "metric",
                }],
            }, ensure_ascii=False))

    monkeypatch.setattr(semantic_config, "get_chat_data_llm", lambda **_kwargs: FakeLlm())
    result = asyncio.run(semantic_config._call_prefill_llm("生成字段配置"))

    assert getattr(captured["messages"][0], "type", "") == "human"
    assert result["fields"]["orders.amount"]["role"] == "metric"


def test_semantic_prefill_routes_missing_and_success(monkeypatch):
    manager = _patch_semantic_route_user_and_manager(monkeypatch)
    monkeypatch.setattr(semantic_config, "_prefill_by_llm", lambda *_args, **_kwargs: asyncio.sleep(0, result={"fields": [{"table": "orders", "column": "id", "role": "dimension", "expression": "`id`"}], "knowledge": {}}))

    auto_missing_ds = _response_payload(asyncio.run(semantic_config.auto_prefill_semantic_fields(
        "missing",
        "cfg_a",
        SemanticAutoPrefillRequest(selected_tables=[], table_model_type="detail"),
        object(),
    )))
    auto_missing_config = _response_payload(asyncio.run(semantic_config.auto_prefill_semantic_fields(
        "ds_a",
        "missing",
        SemanticAutoPrefillRequest(selected_tables=[], table_model_type="detail"),
        object(),
    )))
    short_missing_ds = _response_payload(asyncio.run(semantic_config.semantic_prefill(SemanticPrefillRequest(
        datasource_id="missing",
        config_id="cfg_a",
        tables=[],
        table_model_type="detail",
    ), object())))
    short_missing_config = _response_payload(asyncio.run(semantic_config.semantic_prefill(SemanticPrefillRequest(
        datasource_id="ds_a",
        config_id="missing",
        tables=[],
        table_model_type="detail",
    ), object())))
    short_success = _response_payload(asyncio.run(semantic_config.semantic_prefill(SemanticPrefillRequest(
        datasource_id="ds_a",
        config_id="cfg_a",
        tables=[{"table_name": "orders", "columns": [{"name": "id"}]}],
        table_model_type="detail",
    ), object())))
    public_denied = _response_payload(asyncio.run(semantic_config.auto_prefill_semantic_fields(
        "ds_a",
        "cfg_public",
        SemanticAutoPrefillRequest(selected_tables=[], table_model_type="detail"),
        object(),
    )))
    admin_public_success = _response_payload(asyncio.run(semantic_config.auto_prefill_admin_semantic_fields(
        "ds_a",
        "cfg_public",
        SemanticAutoPrefillRequest(selected_tables=[], table_model_type="detail"),
        object(),
    )))

    assert manager.config["id"] == "cfg_a"
    assert auto_missing_ds["code"] == ErrorCode.CHATDATA_NOT_FOUND
    assert auto_missing_config["code"] == ErrorCode.CHATDATA_NOT_FOUND
    assert short_missing_ds["code"] == ErrorCode.CHATDATA_NOT_FOUND
    assert short_missing_config["code"] == ErrorCode.CHATDATA_NOT_FOUND
    assert short_success["data"]["total"] == 1
    assert public_denied["code"] == ErrorCode.CHATDATA_SEMANTIC_CONFIG_PERMISSION_DENIED
    assert admin_public_success["data"]["total"] == 1


def test_semantic_config_route_failure_branches(monkeypatch):
    class FailingCreateManager(_FakeSemanticManager):
        async def create_semantic_config_async(self, **kwargs):
            self.created_kwargs = kwargs
            return None

        async def update_semantic_config_async(self, **kwargs):
            self.updated_kwargs = kwargs
            return None

        async def delete_semantic_config_async(self, datasource_id, config_id, update_by="", update_by_name=""):
            return False

    manager = _patch_semantic_route_user_and_manager(monkeypatch, FailingCreateManager())

    missing_create_ds = _response_payload(asyncio.run(semantic_config.create_semantic_config(
        "missing",
        SemanticConfigCreateRequest(name="x", metrics=[], dimensions=[], relationships=[]),
        object(),
    )))
    create_failed = _response_payload(asyncio.run(semantic_config.create_semantic_config(
        "ds_a",
        SemanticConfigCreateRequest(name="x", metrics=[], dimensions=[], relationships=[]),
        object(),
    )))
    update_failed = _response_payload(asyncio.run(semantic_config.update_semantic_config(
        "ds_a",
        "cfg_a",
        SemanticConfigUpdateRequest(name="x"),
        object(),
    )))
    delete_failed = _response_payload(asyncio.run(semantic_config.delete_semantic_config("ds_a", "cfg_a", object())))
    toggle_failed = _response_payload(asyncio.run(semantic_config.toggle_semantic_config("ds_a", "cfg_a", True, object())))

    assert missing_create_ds["code"] == ErrorCode.CHATDATA_NOT_FOUND
    assert create_failed["code"] == ErrorCode.CHATDATA_SERVICE_ERROR
    assert update_failed["code"] == ErrorCode.CHATDATA_NOT_FOUND
    assert delete_failed["code"] == ErrorCode.CHATDATA_NOT_FOUND
    assert toggle_failed["code"] == ErrorCode.CHATDATA_NOT_FOUND
    assert manager.updated_kwargs["enabled"] is True


def test_semantic_route_exception_branches(monkeypatch):
    class RaisingManager(_FakeSemanticManager):
        async def has_datasource_async(self, datasource_id):
            raise RuntimeError("list failed")

        async def get_datasource_async(self, datasource_id):
            raise RuntimeError("get failed")

    _patch_semantic_route_user_and_manager(monkeypatch, RaisingManager())

    list_error = _response_payload(asyncio.run(semantic_config.list_semantic_configs("ds_a", object())))
    create_error = _response_payload(asyncio.run(semantic_config.create_semantic_config(
        "ds_a",
        SemanticConfigCreateRequest(name="x", metrics=[], dimensions=[], relationships=[]),
        object(),
    )))
    prefill_error = _response_payload(asyncio.run(semantic_config.auto_prefill_semantic_fields(
        "ds_a",
        "cfg_a",
        SemanticAutoPrefillRequest(selected_tables=[], table_model_type="detail"),
        object(),
    )))
    short_prefill_error = _response_payload(asyncio.run(semantic_config.semantic_prefill(SemanticPrefillRequest(
        datasource_id="ds_a",
        config_id="cfg_a",
        tables=[],
        table_model_type="detail",
    ), object())))
    update_error = _response_payload(asyncio.run(semantic_config.update_semantic_config("ds_a", "cfg_a", SemanticConfigUpdateRequest(name="x"), object())))
    assert list_error["code"] == ErrorCode.CHATDATA_SERVICE_ERROR
    assert create_error["code"] == ErrorCode.CHATDATA_SERVICE_ERROR
    assert prefill_error["code"] == ErrorCode.CHATDATA_SERVICE_ERROR
    assert short_prefill_error["code"] == ErrorCode.CHATDATA_SERVICE_ERROR
    assert update_error["code"] == ErrorCode.CHATDATA_SERVICE_ERROR

    class RaisingMutationManager(_FakeSemanticManager):
        async def delete_semantic_config_async(self, datasource_id, config_id, update_by="", update_by_name=""):
            raise RuntimeError("delete failed")

        async def update_semantic_config_async(self, **kwargs):
            raise RuntimeError("toggle failed")

    _patch_semantic_route_user_and_manager(monkeypatch, RaisingMutationManager())
    delete_error = _response_payload(asyncio.run(semantic_config.delete_semantic_config("ds_a", "cfg_a", object())))
    toggle_error = _response_payload(asyncio.run(semantic_config.toggle_semantic_config("ds_a", "cfg_a", True, object())))
    assert delete_error["code"] == ErrorCode.CHATDATA_SERVICE_ERROR
    assert toggle_error["code"] == ErrorCode.CHATDATA_SERVICE_ERROR


def test_history_learning_route_skip_and_auth_branches(monkeypatch):
    manager = _patch_semantic_route_user_and_manager(monkeypatch)
    execution_time = datetime(2026, 6, 2, 10, 0, 0)
    rows = [
        (
            SimpleNamespace(execution_id="exec_unknown", task_id="task_unknown", sql_status="succeeded", sql_text="select 1", error_message="", finished_time=execution_time, started_time=None, create_time=execution_time),
            SimpleNamespace(query_type="unknown", request_payload="{}", error_message=""),
        ),
        (
            SimpleNamespace(execution_id="exec_builder_skip", task_id="task_builder_skip", sql_status="succeeded", sql_text="select sum(amount) from orders", error_message="", finished_time=execution_time, started_time=None, create_time=execution_time),
            SimpleNamespace(query_type="builder", request_payload=json.dumps({"semantic_config_id": "cfg_a"}), error_message=""),
        ),
        (
            SimpleNamespace(execution_id="exec_parse", task_id="task_parse", sql_status="succeeded", sql_text="bad sql", error_message="", finished_time=execution_time, started_time=None, create_time=execution_time),
            SimpleNamespace(query_type="sql", request_payload="{}", error_message=""),
        ),
        (
            SimpleNamespace(execution_id="exec_agent_no_conv", task_id="task_agent_no_conv", sql_status="succeeded", sql_text="select sum(amount) from orders", error_message="", finished_time=None, started_time=execution_time, create_time=execution_time),
            SimpleNamespace(query_type="agent", request_payload=json.dumps({"semantic_config_id": "cfg_a"}), error_message="task error"),
        ),
        (
            SimpleNamespace(execution_id="exec_agent_empty_context", task_id="task_agent_empty_context", sql_status="succeeded", sql_text="select sum(amount) from orders", error_message="", finished_time=None, started_time=None, create_time=None),
            SimpleNamespace(query_type="agent", request_payload=json.dumps({"semantic_config_id": "cfg_a", "conversation_id": "conv_missing"}), error_message=""),
        ),
    ]

    class FakeResult:
        def all(self):
            return rows

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def execute(self, _stmt):
            return FakeResult()

    async def fake_generate_suggestions(_datasource, _config, samples):
        assert samples == []
        return {"metrics": [], "dimensions": [], "issues": []}

    def fake_extract_tables(sql, source_type="mysql"):
        if sql == "bad sql":
            return SimpleNamespace(success=False, tables=set(), error="syntax")
        return SimpleNamespace(success=True, tables={"orders"}, error="")

    _patch_history_learning_service_session(monkeypatch, lambda: FakeSession())
    monkeypatch.setattr(semantic_config, "_load_history_conversation_context", lambda _conversation_id, _user_id: asyncio.sleep(0, result=None))
    monkeypatch.setattr(semantic_config, "_generate_history_learning_suggestions_by_llm", fake_generate_suggestions)
    monkeypatch.setattr(semantic_config, "extract_physical_tables", fake_extract_tables)

    response = _response_payload(asyncio.run(semantic_config.learn_semantic_config_from_history(
        "ds_a",
        "cfg_a",
        SemanticHistoryLearningRequest(days=7, limit=10, sources=["agent", "sql"], include_failed=False),
        object(),
    )))

    assert response["status"] == "success"
    assert response["data"]["matched_count"] == 0
    assert response["data"]["skipped"]["source"] == 2
    assert response["data"]["skipped"]["sql_parse_failed"] == 1
    assert response["data"]["skipped"]["missing_conversation_context"] == 2
    assert response["data"]["source_stats"]["builder"]["skipped"] == 1

    monkeypatch.setattr(semantic_config, "_get_current_user", lambda _request: (_ for _ in ()).throw(semantic_config.AuthenticationError("未登录")))
    auth_response = _response_payload(asyncio.run(semantic_config.learn_semantic_config_from_history(
        "ds_a",
        "cfg_a",
        SemanticHistoryLearningRequest(days=7, limit=1, sources=["agent"], include_failed=True),
        object(),
    )))

    assert manager.datasource["id"] == "ds_a"
    assert auth_response["code"] == ErrorCode.CHATDATA_AUTHENTICATION_FAILED


def test_semantic_config_low_level_helper_branches(monkeypatch):
    assert semantic_history_learning_service._parse_json_object("") == {}
    assert semantic_history_learning_service._parse_json_object(None) == {}
    assert semantic_history_learning_service._parse_json_object("[1, 2]") == {}
    assert semantic_history_learning_service._parse_json_object('{"a": 1}') == {"a": 1}
    assert semantic_history_learning_service._history_message_text({"unexpected": "value"}) == "{'unexpected': 'value'}"
    assert semantic_history_learning_service._is_history_tool_error_message('["not dict"]') is False
    assert semantic_history_learning_service._is_history_tool_error_message("[tool]\nnot json but 错误") is True

    compact = semantic_history_learning_service._compact_history_conversation_messages([
        {"role": "user", "content": [{"text": ""}, {"content": "  "}], "timestamp": "skip"},
        {"role": "assistant", "content": "带工具调用的回复", "tool_calls": [{"id": "tc"}], "timestamp": "skip"},
        {"role": "assistant", "content": "正常回复", "timestamp": "t1"},
        {"role": "tool", "tool_name": "execute_sql", "content": '{"success": true}', "timestamp": "t2"},
        {"role": "tool", "tool_name": "execute_sql", "content": '{"success": false, "error": "失败"}', "timestamp": "t3"},
    ])
    assert compact["assistant_turns"] == [{"content": "正常回复", "timestamp": "t1"}]
    assert compact["tool_errors"][0]["content"] == '{"success": false, "error": "失败"}'

    assert semantic_history_learning_service._semantic_scope_tables({
        "metrics": [{"table": ""}],
        "dimensions": [{"table": "orders"}],
        "relationships": [{"left_table": "", "right_table": "users"}],
    }) == {"orders", "users"}

    async def fake_get_agent_id(_db_path, conversation_id, user_id):
        assert user_id == "user_a"
        return {
            "conv_agent": semantic_history_learning_service.AGENT_ID,
            "conv_unknown": "unknown_agent",
        }.get(conversation_id)

    async def fake_get_detail(conversation_id, user_id):
        assert user_id == "user_a"
        return {"messages": [{"role": "user", "content": f"{conversation_id} 用户问题"}]}

    monkeypatch.setattr(semantic_history_learning_service.conversation, "get_agent_id", fake_get_agent_id)
    monkeypatch.setattr(semantic_history_learning_service.conversation, "get_detail", fake_get_detail)

    agent_context = asyncio.run(semantic_config._load_history_conversation_context("conv_agent", "user_a"))
    unknown_context = asyncio.run(semantic_config._load_history_conversation_context("conv_unknown", "user_a"))
    empty_context = asyncio.run(semantic_config._load_history_conversation_context("", "user_a"))

    assert agent_context["user_turns"][0]["content"] == "conv_agent 用户问题"
    assert unknown_context is None
    assert empty_context is None

    async def raise_get_detail(*_args, **_kwargs):
        raise RuntimeError("history down")

    monkeypatch.setattr(semantic_history_learning_service.conversation, "get_detail", raise_get_detail)
    assert asyncio.run(semantic_config._load_history_conversation_context("conv_agent", "user_a")) is None


def test_prefill_dedupe_and_parse_response_extra_branches():
    long_alias = "ods_marketing_channel_role_name"
    items = [
        {"name": "金额", "table_alias": long_alias, "table": "orders"},
        {"name": "金额", "table_alias": "seller_orders", "table": "orders"},
        {"name": "金额", "table_alias": "", "table": "orders"},
        {"name": "", "table_alias": "orders", "table": "orders"},
        {"name": "", "table_alias": "orders", "table": "orders"},
    ]
    deduped = semantic_config._dedupe_prefill_display_names(items)
    names = [item["name"] for item in deduped]

    assert names[0] == "name_金额"
    assert names[1] == "orders_金额"
    assert names[2] == "orders_金额2"
    assert names[3] == ""
    assert names[4] == "2"
    assert semantic_config._normalize_prefill_table_model_type("aggregated") == "aggregated"
    assert semantic_config._normalize_prefill_table_model_type("unknown") == "detail"

    parsed = semantic_config._parse_prefill_response(json.dumps({
        "fields": [
            {"table": "orders", "column": "amount", "role": "metric"},
            "bad",
            {"table": "orders", "column": "", "role": "dimension"},
        ],
        "knowledge": {"business_flow": "下单-支付"},
    }, ensure_ascii=False), generate_business_flow=True)
    assert parsed["fields"]["orders.amount"]["expression"] == "`amount`"
    assert parsed["knowledge"] == {"business_flow": "下单-支付"}

    table_payload = [{"table": "orders", "columns": [{"name": "amount"}]}]
    recommendation = semantic_config._parse_prefill_recommendation_response(json.dumps({
        "fields": [{"table": "orders", "column": "amount", "name": "订单金额"}],
        "metrics": [{"table": "orders", "column": "amount", "agg": "SUM", "name": "销售额"}],
    }, ensure_ascii=False), table_payload, max_metrics=1)
    metric_only = semantic_config._parse_metric_recommendation_response(json.dumps({
        "metrics": [
            {"table": "orders", "column": "amount", "agg": "SUM", "name": "销售额"},
            {"table": "orders", "column": "amount", "agg": "AVG", "name": "均额"},
        ],
    }, ensure_ascii=False), table_payload, max_metrics=1)

    assert recommendation["fields"][0]["name"] == "订单金额"
    assert recommendation["metrics"][0]["name"] == "销售额"
    assert len(metric_only) == 1


def test_prefill_by_llm_handles_empty_recommendation_list_and_failure(monkeypatch):
    tables = [{"table_name": "orders", "columns": [{"name": "amount"}, {"name": "city"}]}]

    async def fake_list_recommendation(*_args, **_kwargs):
        return [{"table": "orders", "table_alias": "orders", "column": "amount", "role": "metric", "name": "销售额", "expression": "SUM(`amount`)"}]

    monkeypatch.setattr(semantic_config, "_call_metric_recommendation_llm", fake_list_recommendation)
    list_result = asyncio.run(semantic_config._prefill_by_llm(tables, "detail"))
    assert [item["role"] for item in list_result["fields"]].count("metric") == 1

    async def raise_recommendation(*_args, **_kwargs):
        raise RuntimeError("llm failed")

    monkeypatch.setattr(semantic_config, "_call_metric_recommendation_llm", raise_recommendation)
    fallback = asyncio.run(semantic_config._prefill_by_llm(tables, "detail"))
    assert fallback["fields"]
    assert all(item["role"] == "dimension" for item in fallback["fields"])
    assert asyncio.run(semantic_config._prefill_by_llm([], "detail")) == {"fields": [], "knowledge": {}}
    assert asyncio.run(semantic_config._prefill_by_llm([{"table_name": "", "columns": []}], "detail")) == {"fields": [], "knowledge": {}}


def test_fill_missing_prefill_knowledge_branches(monkeypatch):
    existing = asyncio.run(semantic_config._fill_missing_prefill_knowledge(
        table_payload=[],
        knowledge={"business_flow": "已有"},
        generate_business_flow=True,
    ))
    disabled = asyncio.run(semantic_config._fill_missing_prefill_knowledge(
        table_payload=[],
        knowledge={"business_flow": "忽略"},
        generate_business_flow=False,
    ))

    async def fake_call_prefill_llm(prompt, generate_business_flow=False):
        assert generate_business_flow is True
        assert "business_flow" in prompt
        return {"knowledge": {"business_flow": "补充业务流"}}

    monkeypatch.setattr(semantic_config, "_call_prefill_llm", fake_call_prefill_llm)
    filled = asyncio.run(semantic_config._fill_missing_prefill_knowledge(
        table_payload=[{"table": "orders", "columns": []}],
        knowledge={},
        generate_business_flow=True,
    ))

    assert existing == {"business_flow": "已有"}
    assert disabled == {}
    assert filled == {"business_flow": "补充业务流"}


def test_history_learning_normalization_duplicate_and_review_branches():
    datasource = {
        "ds_tables": [
            {"table_name": "orders", "columns": [{"name": "amount"}, {"name": "city"}]},
        ]
    }
    config = {"metrics": [], "dimensions": []}
    samples = [{"execution_id": "exec_1", "source": "sql"}, {"execution_id": "exec_2", "source": "agent"}]
    raw = {
        "metrics": [
            {"name": "新指标", "table": "missing_table", "expression": "SUM(amount)", "reason": "缺表", "source": "sql", "confidence": "high", "sample_execution_ids": ["exec_1"], "support_count": 1},
            {"name": "新指标", "table": "missing_table", "expression": "SUM(amount)", "reason": "重复", "source": "sql", "confidence": "high", "sample_execution_ids": ["exec_1"], "support_count": 1},
            {"name": "缺字段指标", "table": "orders", "expression": "SUM(pay_amount)", "reason": "缺字段", "source": "agent", "confidence": "medium", "sample_execution_ids": ["exec_2"], "support_count": 2},
        ],
        "dimensions": [
            {"name": "渠道", "table": "orders", "field_name": "channel", "column": "channel", "logical_type": "text", "reason": "缺字段", "source": "sql", "confidence": "low", "sample_execution_ids": ["exec_1"], "support_count": 1},
        ],
        "issues": "bad",
    }

    result = semantic_history_learning_service._normalize_history_learning_result(raw, samples, datasource, config)

    assert [item["name"] for item in result["metrics"]] == ["新指标", "缺字段指标"]
    assert result["metrics"][0]["needs_review"] is True
    assert "表不在当前数据源缓存中" in result["metrics"][0]["review_reason"]
    assert "pay_amount" in result["metrics"][1]["review_reason"]
    assert result["dimensions"][0]["needs_review"] is True
    assert "orders.channel" in result["dimensions"][0]["review_reason"]
    assert result["issues"] == []


def test_generate_history_learning_suggestions_llm_branches(monkeypatch):
    empty = asyncio.run(semantic_config._generate_history_learning_suggestions_by_llm({}, {}, []))
    assert empty == {"metrics": [], "dimensions": [], "issues": []}

    class FakeLlm:
        def __init__(self, content):
            self.content = content

        async def ainvoke(self, messages):
            return SimpleNamespace(content=self.content)

    samples = [{"execution_id": "exec_1", "source": "sql"}]
    datasource = {"ds_tables": [{"table_name": "orders", "columns": [{"name": "amount"}]}]}
    config = {"metrics": [], "dimensions": []}

    monkeypatch.setattr(
        semantic_config,
        "get_chat_data_llm",
        lambda **_kwargs: FakeLlm(json.dumps({
            "suggestions": {
                "metrics": [{"name": "销售额", "table": "orders", "expression": "SUM(amount)", "reason": "常查", "source": "sql", "confidence": "high", "sample_execution_ids": ["exec_1"], "support_count": 1}],
                "dimensions": [],
                "issues": [],
            }
        }, ensure_ascii=False)),
    )
    success = asyncio.run(semantic_config._generate_history_learning_suggestions_by_llm(datasource, config, samples))
    assert success["metrics"][0]["name"] == "销售额"

    monkeypatch.setattr(semantic_config, "get_chat_data_llm", lambda **_kwargs: FakeLlm("[1, 2]"))
    with pytest.raises(ValueError, match="合法 JSON"):
        asyncio.run(semantic_config._generate_history_learning_suggestions_by_llm(datasource, config, samples))


def test_semantic_config_remaining_helper_branch_edges():
    assert semantic_history_learning_service._history_message_text(["a", 123, {"text": "b"}, {"content": "c"}, {"text": ""}]) == "a\nb\nc"

    many_messages = []
    for i in range(8):
        many_messages.append({"role": "user", "content": f"用户{i}", "timestamp": str(i)})
    for i in range(5):
        many_messages.append({"role": "assistant", "content": f"助手{i}", "timestamp": str(i)})
        many_messages.append({"role": "tool", "tool_name": "execute_sql", "content": '{"success": false, "error": "失败"}', "timestamp": str(i)})
    compact = semantic_history_learning_service._compact_history_conversation_messages(many_messages)
    assert [item["content"] for item in compact["user_turns"]] == [f"用户{i}" for i in range(2, 8)]
    assert [item["content"] for item in compact["assistant_turns"]] == ["助手2", "助手3", "助手4"]
    assert len(compact["tool_errors"]) == 5

    table_context = semantic_history_learning_service._build_history_learning_table_context(
        {
            "ds_tables": [
                {"table_name": "orders", "columns": [{"name": ""}, {"name": "id", "hive_type": "bigint"}]},
                {"table_name": "users", "columns": [{"name": "id"}]},
            ]
        },
        {"metrics": [{"table": "orders"}], "dimensions": [], "relationships": []},
    )
    assert table_context[0]["columns"] == [{"name": "id", "type": "bigint", "description": ""}]

    assert semantic_config._build_prefill_alias_map([
        {"table_name": "", "table_alias": "skip"},
        {"table_name": "orders", "table_alias": ""},
        {"table_name": "orders", "table_alias": "orders"},
        {"table_name": "orders", "table_alias": "orders"},
    ]) == {"orders": ["orders"]}

    long_name = "字段名" * 12
    deduped = semantic_config._dedupe_prefill_display_names([
        {"name": long_name, "table_alias": "别名", "table": "t"},
        {"name": long_name, "table_alias": "", "table": "t"},
        {"name": "指标", "table_alias": "!!!", "table": "t"},
        {"name": "指标", "table_alias": "!!!", "table": "t"},
    ])
    assert deduped[0]["name"] == long_name[:semantic_config.PREFILL_DISPLAY_NAME_MAX_LENGTH]
    assert deduped[1]["name"].endswith("2")
    assert len(deduped[1]["name"]) == semantic_config.PREFILL_DISPLAY_NAME_MAX_LENGTH
    assert deduped[2]["name"] == "指标"
    assert deduped[3]["name"] == "指标2"

    assert semantic_history_learning_service._extract_expression_columns("SUM() + orders. + `amount`") == {"amount"}
    indexes = semantic_history_learning_service._build_existing_semantic_indexes({
        "metrics": ["bad", {"name": "", "table": "orders", "expression": ""}],
        "dimensions": ["bad", {"name": "", "table": "orders", "field_name": "city", "column": ""}],
    })
    assert indexes["dimension_fields"] == {"orders.city"}

    datasource = {"ds_tables": [{"table_name": "orders", "columns": [{"name": "amount"}]}]}
    sample_map = {"exec_1": {"execution_id": "exec_1", "source": "sql"}}
    assert semantic_history_learning_service._normalize_history_suggestion_item({
        "source": "sql",
        "confidence": "high",
        "sample_execution_ids": ["exec_1"],
        "support_count": 1,
        "name": "指标",
        "table": "missing",
        "expression": "SUM(amount)",
        "reason": "缺表",
    }, "metrics", sample_map, datasource)["needs_review"] is True
    assert semantic_history_learning_service._normalize_history_suggestion_item({
        "source": "sql",
        "confidence": "high",
        "sample_execution_ids": ["exec_1"],
        "support_count": 1,
        "name": "维度",
        "table": "orders",
        "field_name": "missing",
        "column": "missing",
        "logical_type": "text",
        "reason": "缺字段",
    }, "dimensions", sample_map, datasource)["needs_review"] is True
    assert semantic_history_learning_service._normalize_history_suggestion_item({
        "source": "sql",
        "confidence": "high",
        "sample_execution_ids": ["exec_1"],
        "support_count": 1,
        "title": "问题",
        "content": "说明",
        "reason": "历史反馈",
    }, "issues", sample_map, datasource)["title"] == "问题"


def test_semantic_config_prefill_call_llm_and_route_error_branches(monkeypatch):
    table_payload = [{"table": "orders", "columns": [{"name": "amount"}]}]
    captured = {}

    class FakeLlm:
        async def ainvoke(self, messages):
            captured["message_type"] = getattr(messages[0], "type", "")
            return SimpleNamespace(content=json.dumps({
                "fields": [{"table": "orders", "column": "amount", "name": "金额"}],
                "metrics": [{"table": "orders", "column": "amount", "agg": "SUM", "name": "销售额"}],
            }, ensure_ascii=False))

    monkeypatch.setattr(semantic_config, "get_chat_data_llm", lambda **_kwargs: FakeLlm())
    recommendation = asyncio.run(semantic_config._call_metric_recommendation_llm(
        table_payload,
        business_context="经营分析",
        max_metrics=1,
        table_alias_map={"orders": ["orders"]},
    ))
    assert captured["message_type"] == "human"
    assert recommendation["metrics"][0]["name"] == "销售额"

    manager = _patch_semantic_route_user_and_manager(monkeypatch)
    monkeypatch.setattr(semantic_config, "_prefill_by_llm", lambda *_args, **_kwargs: asyncio.sleep(0, result=None))
    auto_incomplete = _response_payload(asyncio.run(semantic_config.auto_prefill_semantic_fields(
        "ds_a",
        "cfg_a",
        SemanticAutoPrefillRequest(selected_tables=["orders"], table_model_type="detail"),
        object(),
    )))
    assert auto_incomplete["code"] == ErrorCode.CHATDATA_SERVICE_ERROR

    class FileOk:
        filename = "rules.txt"

        async def read(self):
            return b"abcdef"

    monkeypatch.setattr(semantic_config, "extract_text_from_file_bytes", lambda *_args, **_kwargs: "x" * (semantic_config.SEMANTIC_PREFILL_CONTEXT_TEXT_MAX_LENGTH + 10))
    ok_file = _response_payload(asyncio.run(semantic_config.semantic_prefill_context_file(FileOk())))
    assert ok_file["data"]["size"] == 6
    assert len(ok_file["data"]["text"]) == semantic_config.SEMANTIC_PREFILL_CONTEXT_TEXT_MAX_LENGTH

    class RaisingFile:
        filename = "rules.txt"

        async def read(self):
            raise RuntimeError("read failed")

    failed_file = _response_payload(asyncio.run(semantic_config.semantic_prefill_context_file(RaisingFile())))
    assert failed_file["code"] == ErrorCode.CHATDATA_SERVICE_ERROR
    assert manager.config["id"] == "cfg_a"


def test_semantic_config_route_optional_update_and_missing_current_branches(monkeypatch):
    manager = _patch_semantic_route_user_and_manager(monkeypatch)
    payload = SemanticConfigUpdateRequest(
        description="新描述",
        ontology_rules="规则",
        enabled=False,
        metrics=[{"name": "销售额", "table": "orders", "expression": "SUM(amount)"}],
        dimensions=[{"name": "城市", "table": "orders", "column": "city", "logical_type": "text"}],
        relationships=[{
            "left_table": "orders",
            "right_table": "orders",
            "left_column": "order_id",
            "right_column": "order_id",
            "relationship_type": "one_to_one",
            "join_type": "inner",
        }],
    )
    updated = _response_payload(asyncio.run(semantic_config.update_semantic_config("ds_a", "cfg_a", payload, object())))

    assert updated["status"] == "success"
    assert manager.updated_kwargs["description"] == "新描述"
    assert manager.updated_kwargs["ontology_rules"] == "规则"
    assert manager.updated_kwargs["enabled"] is False
    assert manager.updated_kwargs["relationships"][0]["join_type"] == "inner"

    class MissingConfigManager(_FakeSemanticManager):
        async def get_semantic_config_async(self, datasource_id, config_id):
            return None

    _patch_semantic_route_user_and_manager(monkeypatch, MissingConfigManager())
    delete_missing = _response_payload(asyncio.run(semantic_config.delete_semantic_config("ds_a", "missing", object())))
    toggle_missing = _response_payload(asyncio.run(semantic_config.toggle_semantic_config("ds_a", "missing", True, object())))
    assert delete_missing["code"] == ErrorCode.CHATDATA_NOT_FOUND
    assert toggle_missing["code"] == ErrorCode.CHATDATA_NOT_FOUND


def test_history_learning_route_exception_and_cache_branches(monkeypatch):
    _patch_semantic_route_user_and_manager(monkeypatch)
    execution_time = datetime(2026, 6, 2, 10, 0, 0)
    rows = [
        (
            SimpleNamespace(
                execution_id="exec_agent_1",
                task_id="task_agent_1",
                sql_status="succeeded",
                sql_text="select sum(amount) from orders",
                error_message="",
                finished_time=execution_time,
                started_time=None,
                create_time=execution_time,
            ),
            SimpleNamespace(
                query_type="agent",
                request_payload=json.dumps({"semantic_config_id": "cfg_a", "query_text": "销售额", "conversation_id": "conv_a"}, ensure_ascii=False),
                error_message="",
            ),
        ),
        (
            SimpleNamespace(
                execution_id="exec_agent_2",
                task_id="task_agent_2",
                sql_status="succeeded",
                sql_text="select sum(amount) from orders",
                error_message="",
                finished_time=execution_time,
                started_time=None,
                create_time=execution_time,
            ),
            SimpleNamespace(
                query_type="agent",
                request_payload=json.dumps({"semantic_config_id": "cfg_a", "query_text": "销售额2", "conversation_id": "conv_a"}, ensure_ascii=False),
                error_message="",
            ),
        ),
    ]

    class FakeResult:
        def all(self):
            return rows

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def execute(self, _stmt):
            return FakeResult()

    calls = []

    async def fake_load_context(conversation_id, user_id):
        calls.append((conversation_id, user_id))
        return {"user_turns": [{"content": "上下文"}]}

    async def fake_suggestions(_datasource, _config, samples):
        assert len(samples) == 2
        return {"metrics": [], "dimensions": [], "issues": []}

    _patch_history_learning_service_session(monkeypatch, lambda: FakeSession())
    monkeypatch.setattr(semantic_config, "_load_history_conversation_context", fake_load_context)
    monkeypatch.setattr(semantic_config, "_generate_history_learning_suggestions_by_llm", fake_suggestions)

    success = _response_payload(asyncio.run(semantic_config.learn_semantic_config_from_history(
        "ds_a",
        "cfg_a",
        SemanticHistoryLearningRequest(days=7, limit=5, sources=["agent"], include_failed=True),
        object(),
    )))
    assert success["data"]["matched_count"] == 2
    assert calls == [("conv_a", "user_a")]

    class RaisingSession(FakeSession):
        async def execute(self, _stmt):
            raise RuntimeError("db failed")

    _patch_history_learning_service_session(monkeypatch, lambda: RaisingSession())
    failed = _response_payload(asyncio.run(semantic_config.learn_semantic_config_from_history(
        "ds_a",
        "cfg_a",
        SemanticHistoryLearningRequest(days=7, limit=1, sources=["sql"], include_failed=True),
        object(),
    )))
    assert failed["code"] == ErrorCode.CHATDATA_SERVICE_ERROR


def test_semantic_config_remaining_history_helper_edges():
    compact = semantic_history_learning_service._compact_history_conversation_messages([
        {"role": "tool", "content": '{"success": true}', "timestamp": "t1"},
    ])
    assert compact == {"user_turns": [], "assistant_turns": [], "negative_feedback": [], "tool_errors": []}

    assert semantic_history_learning_service._semantic_scope_tables({
        "metrics": [{"table": "orders"}],
        "dimensions": [{"table": ""}],
        "relationships": [{"left_table": "orders", "right_table": ""}],
    }) == {"orders"}

    duplicate_items = [
        {"name": "金额", "table_alias": "a", "table": "orders"},
        {"name": "金额", "table_alias": "long_alias", "table": "orders"},
    ]
    deduped = semantic_config._dedupe_prefill_display_names(duplicate_items)
    assert deduped[0]["name"].endswith("金额")
    assert deduped[1]["name"].endswith("金额")
    assert deduped[0]["name"] != deduped[1]["name"]

    assert "amount" in semantic_history_learning_service._extract_expression_columns("SUM(`amount`) + t.price + 'literal_text'")

    indexes = semantic_history_learning_service._build_existing_semantic_indexes({
        "metrics": [{"name": "销售额", "table": "orders", "expression": "SUM(amount)"}],
        "dimensions": [
            {"name": "城市", "table": "orders", "column": ""},
            {"name": "状态", "table": "orders", "field_name": "status"},
        ],
    })
    assert "orders.status" in indexes["dimension_columns"]
    assert "orders.status" in indexes["dimension_fields"]
    assert "城市" in indexes["dimension_names"]

    sample_map = {"exec_1": {"execution_id": "exec_1", "source": "sql"}}
    datasource = {"ds_tables": [{"table_name": "orders", "columns": [{"name": "status"}, {"name": "amount"}]}]}
    missing_column = semantic_history_learning_service._normalize_history_suggestion_item(
        {
            "source": "sql",
            "confidence": "high",
            "sample_execution_ids": ["exec_1"],
            "support_count": 1,
            "name": "城市",
            "table": "orders",
            "field_name": "city",
            "column": "city",
            "logical_type": "text",
            "reason": "常用",
        },
        "dimensions",
        sample_map,
        datasource,
    )
    assert missing_column["needs_review"] is True
    assert "字段不在当前表结构中" in missing_column["review_reason"]
