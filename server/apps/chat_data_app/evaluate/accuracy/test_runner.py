"""Tests for accuracy runner case formats."""

import json

import pytest

from runner import (
    ToolTrace,
    build_dsl2sql_payload,
    choose_final_dsl2sql_trace,
    consume_sse_line,
    case_gold_sql,
    case_metadata,
    case_question,
    extract_sql_from_tool_content,
    flush_sse_event,
    score_sql,
    validate_cases,
)
from scorers import score_dsl


def test_case_helpers_support_seven_tuple_case():
    case = {
        "id": "case_001",
        "nl": "查询订单总销量",
        "gold_dsl": {
            "metrics": ["销量"],
            "dimensions": [],
            "filters": [],
            "having": [],
            "order_by": [],
            "limit": 100,
        },
        "gold_sql": "select sum(quantity) from order_items",
        "predicted_dsl": {
            "metrics": ["销量"],
            "dimensions": [],
            "filters": [],
            "having": [],
            "order_by": [],
            "limit": 100,
        },
        "predicted_sql": "select  sum(quantity)  from order_items",
        "query_type": "aggregation",
        "difficulty": "easy",
        "source": "human",
        "business_domain": "order_operating",
    }

    dsl_result = score_dsl(case["predicted_dsl"], case["gold_dsl"])
    sql_result = score_sql(case["predicted_sql"], case_gold_sql(case))

    assert case_question(case) == "查询订单总销量"
    assert dsl_result["dsl_exact_match"] is True
    assert sql_result["sql_exact_match"] is True
    assert case_metadata(case)["business_domain"] == "order_operating"


def test_validate_cases_rejects_incomplete_seven_tuple_case(tmp_path):
    cases = [{
        "nl": "查询订单总销量",
        "gold_dsl": {"metrics": ["销量"]},
        "query_type": "aggregation",
    }]

    with pytest.raises(ValueError, match="7 元组 case 字段不完整"):
        validate_cases(cases, tmp_path / "cases.json")


def test_score_dsl_ignores_predicted_order_by_when_expected_is_empty():
    expected = {
        "metrics": ["销量"],
        "dimensions": ["用户名称"],
        "filters": [],
        "having": [],
        "order_by": [],
        "limit": 100,
    }
    predicted = {
        "metrics": ["销量"],
        "dimensions": ["用户名称"],
        "filters": [],
        "having": [],
        "order_by": [{"field": "销量", "direction": "DESC"}],
        "limit": 100,
    }

    result = score_dsl(predicted, expected)

    assert result["slot_scores"]["order_by"] == 1.0
    assert result["dsl_exact_match"] is True


def test_score_dsl_requires_order_by_when_expected_has_value():
    expected = {
        "metrics": ["销量"],
        "dimensions": ["用户名称"],
        "filters": [],
        "having": [],
        "order_by": [{"field": "销量", "direction": "DESC"}],
        "limit": 100,
    }
    predicted = {
        "metrics": ["销量"],
        "dimensions": ["用户名称"],
        "filters": [],
        "having": [],
        "order_by": [],
        "limit": 100,
    }

    result = score_dsl(predicted, expected)

    assert result["slot_scores"]["order_by"] == 0.0
    assert result["dsl_exact_match"] is False


def test_score_dsl_compares_order_by_when_both_sides_have_value():
    expected = {
        "metrics": ["销量"],
        "dimensions": ["用户名称"],
        "filters": [],
        "having": [],
        "order_by": [{"field": "销量", "direction": "DESC"}],
        "limit": 100,
    }
    predicted = {
        "metrics": ["销量"],
        "dimensions": ["用户名称"],
        "filters": [],
        "having": [],
        "order_by": [{"field": "用户名称", "direction": "ASC"}],
        "limit": 100,
    }

    result = score_dsl(predicted, expected)

    assert result["slot_scores"]["order_by"] == 0.0
    assert result["dsl_exact_match"] is False


def test_score_dsl_ignores_visualization_difference():
    expected = {
        "metrics": ["订单数量"],
        "dimensions": [],
        "filters": [],
        "having": [],
        "order_by": [],
        "limit": 100,
        "visualization": {},
    }
    predicted = {
        "metrics": ["订单数量"],
        "dimensions": [],
        "filters": [],
        "having": [],
        "order_by": [],
        "limit": 100,
        "visualization": {"type": "kpi", "x_field": None, "y_field": "订单数量"},
    }

    result = score_dsl(predicted, expected)

    assert "visualization" not in result["slot_scores"]
    assert result["dsl_exact_match"] is True
    assert result["dsl_slot_accuracy"] == 1.0


def test_score_dsl_rejects_predicted_limit_when_expected_is_empty():
    expected = {
        "metrics": ["订单数量"],
        "dimensions": [],
        "filters": [],
        "having": [],
        "order_by": [],
    }
    predicted = {
        "metrics": ["订单数量"],
        "dimensions": [],
        "filters": [],
        "having": [],
        "order_by": [],
        "limit": 100,
    }

    result = score_dsl(predicted, expected)

    assert result["slot_scores"]["limit"] == 0.0
    assert result["dsl_exact_match"] is False
    assert result["expected_dsl"]["limit"] is None
    assert result["predicted_dsl"]["limit"] == 100


def test_score_dsl_display_keeps_missing_limit_and_offset_as_null():
    dsl = {
        "metrics": ["订单数量"],
        "dimensions": [],
        "filters": [],
        "having": [],
        "order_by": [],
    }

    result = score_dsl(dsl, dsl)

    assert result["expected_dsl"]["limit"] is None
    assert result["expected_dsl"]["offset"] is None
    assert result["predicted_dsl"]["limit"] is None
    assert result["predicted_dsl"]["offset"] is None


def test_consume_sse_line_extracts_tool_trace_and_error():
    state = {"current_event": "message", "data_lines": [], "error": ""}
    traces: list[ToolTrace] = []
    tool_payload = {
        "tool_name": "dsl2sql_tool",
        "status": "completed",
        "accumulated_args": {"metrics": ["销量"]},
        "content": json.dumps({"success": True, "dsl": {"metrics": ["销量"]}}, ensure_ascii=False),
    }

    for line in [
        "event: tool",
        f"data: {json.dumps(tool_payload, ensure_ascii=False)}",
        "",
        "event: error",
        "data: {\"error\":\"模型调用失败\"}",
        "",
    ]:
        consume_sse_line(line=line, state=state, tool_traces=traces)
    flush_sse_event(state=state, tool_traces=traces)

    assert len(traces) == 1
    assert traces[0].name == "dsl2sql_tool"
    assert traces[0].status == "completed"
    assert traces[0].args == {"metrics": ["销量"]}
    assert state["error"] == "模型调用失败"


def test_extract_sql_from_semantic_tool_markdown_content():
    content = """```sql
SELECT
  SUM(`quantity`) AS `销量`
FROM
  order_items
LIMIT
  100
```"""

    assert extract_sql_from_tool_content(content) == "SELECT\n  SUM(`quantity`) AS `销量`\nFROM\n  order_items\nLIMIT\n  100"


def test_choose_final_dsl2sql_trace_uses_semantic_tool_args_as_dsl():
    trace = ToolTrace(
        name="dsl2sql_tool",
        status="completed",
        args={
            "metrics": ["销量"],
            "dimensions": [],
            "filters": [],
            "having": [],
            "order_by": [],
            "limit": 100,
            "visualization": {"type": "kpi", "x_field": None, "y_field": "销量"},
        },
        content="""```sql
SELECT SUM(`quantity`) AS `销量` FROM order_items LIMIT 100
```""",
    )

    selected, payload = choose_final_dsl2sql_trace([trace])

    assert selected is trace
    assert payload["success"] is True
    assert payload["dsl"]["metrics"] == ["销量"]
    assert payload["dsl"]["limit"] == 100
    assert payload["sql"] == "SELECT SUM(`quantity`) AS `销量` FROM order_items LIMIT 100"


def test_build_dsl2sql_payload_keeps_tool_error_when_no_sql():
    trace = ToolTrace(
        name="dsl2sql_tool",
        status="completed",
        args={"metrics": ["不存在指标"]},
        content="错误：指标不存在",
    )

    payload = build_dsl2sql_payload(trace)

    assert payload["success"] is False
    assert payload["dsl"] == {"metrics": ["不存在指标"]}
    assert payload["sql"] == ""
    assert payload["error"] == "错误：指标不存在"
