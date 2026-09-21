import json
import os
import sys

import pytest
from starlette.responses import StreamingResponse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.evaluate.accuracy import runner, scorers
from apps.chat_data_app.evaluate.accuracy.runner import AccuracyRunConfig, LiveCaseResult, ToolTrace


def _case(case_id: str = "case_1") -> dict:
    return {
        "id": case_id,
        "nl": "查询已支付订单销售额",
        "gold_dsl": {
            "metrics": ["销售额"],
            "dimensions": ["订单状态"],
            "filters": [{"dimension": "订单状态", "operator": "=", "value": "paid"}],
            "having": [],
            "order_by": [],
            "limit": 10,
        },
        "gold_sql": "select status, sum(amount) from orders where status = 'paid' group by status limit 10",
        "query_type": "aggregation",
        "difficulty": "easy",
        "source": "human",
        "business_domain": "订单分析",
    }


def test_scorer_normalizes_dimensions_temporal_filters_and_offset():
    assert scorers.normalize_dimension({"name": "下单时间", "time_granularity": "month"}) == {"name": "下单时间", "time_grain": "month"}
    assert scorers.normalize_dimension({"name": "订单状态"}) == "订单状态"
    assert scorers.normalize_list(["B", "A"]) == ["A", "B"]
    assert scorers.normalize_temporal_lower("2026-06") == "2026-06-01 00:00:00"
    assert scorers.normalize_temporal_exclusive_upper("2026-06", inclusive=True) == "2026-07-01 00:00:00"
    assert scorers.normalize_temporal_exclusive_upper("2026-06-02", inclusive=True) == "2026-06-03 00:00:00"
    assert scorers.normalize_temporal_exclusive_upper("2026-06-02 10:00:00", inclusive=True) == "2026-06-02 10:00:01"
    assert scorers.normalize_temporal_lower("bad") is None

    result = scorers.score_dsl(
        {
            "metrics": ["销售额"],
            "dimensions": [{"name": "下单时间", "time_grain": "month"}],
            "filters": [{"field": "下单时间", "operator": "<=", "value": "2026-06-02"}],
            "having": [],
            "order_by": [{"field": "下单时间", "direction": "ASC"}],
            "limit": 10,
            "offset": 5,
        },
        {
            "metrics": ["销售额"],
            "dimensions": [{"name": "下单时间", "time_grain": "month"}],
            "filters": [{"dimension": "下单时间", "operator": "<", "value": "2026-06-03 00:00:00"}],
            "having": [],
            "order_by": [{"field": "下单时间", "direction": "ASC"}],
            "limit": 10,
            "offset": 5,
        },
    )

    assert result["dsl_exact_match"] is True
    assert result["slot_scores"]["filters"] == 1.0
    assert result["slot_scores"]["offset"] == 1.0


def test_scorer_filter_and_slot_mismatch_branches():
    assert scorers.normalize_temporal_exclusive_upper("bad", inclusive=True) is None
    assert scorers.normalize_filter("plain") == ["plain"]
    assert scorers.normalize_filter({"dimension": "下单时间", "operator": "between", "value": ["bad", "2026-06-02"]}) == [
        {"dimension": "下单时间", "operator": "between", "value": ["bad", "2026-06-02"]}
    ]
    assert scorers.normalize_filter({"dimension": "下单时间", "operator": "between", "value": "2026-06-02"}) == [
        {"dimension": "下单时间", "operator": "between", "value": "2026-06-02"}
    ]
    assert scorers.normalize_filter({"dimension": "下单时间", "operator": "between", "value": ["2026-06-01", "2026-06-02"]}) == [
        {"dimension": "下单时间", "operator": ">=", "value": "2026-06-01 00:00:00"},
        {"dimension": "下单时间", "operator": "<", "value": "2026-06-03 00:00:00"},
    ]
    assert scorers.normalize_filter({"dimension": "下单时间", "operator": ">=", "value": "2026-06-01"}) == [
        {"dimension": "下单时间", "operator": ">=", "value": "2026-06-01 00:00:00"}
    ]
    assert scorers.normalize_filter({"dimension": "下单时间", "operator": ">", "value": "2026-06-01"}) == [
        {"dimension": "下单时间", "operator": ">", "value": "2026-06-01 00:00:00"}
    ]
    assert scorers.normalize_filter({"dimension": "下单时间", "operator": "<", "value": "2026-06-01"}) == [
        {"dimension": "下单时间", "operator": "<", "value": "2026-06-01 00:00:00"}
    ]
    assert scorers.normalize_filter({"dimension": "下单时间", "operator": "<", "value": "bad"}) == [
        {"dimension": "下单时间", "operator": "<", "value": "bad"}
    ]
    assert scorers.normalize_filter({"dimension": "下单时间", "operator": "<=", "value": "bad"}) == [
        {"dimension": "下单时间", "operator": "<=", "value": "bad"}
    ]

    score = scorers.score_dsl(
        {"metrics": ["销售额"], "dimensions": ["城市"], "order_by": [{"field": "城市"}], "limit": "10"},
        {"metrics": ["订单数"], "dimensions": [], "order_by": [], "limit": 10},
    )

    assert score["dsl_exact_match"] is False
    assert score["slot_scores"]["metrics"] == 0.0
    assert score["slot_scores"]["dimensions"] == 0.0
    assert score["slot_scores"]["order_by"] == 1.0
    assert score["slot_scores"]["limit"] == 1.0


def test_runner_case_validation_and_sql_helpers(tmp_path):
    cases = [_case()]
    runner.validate_cases(cases, tmp_path / "cases.json")
    assert cases[0]["source"] == "human"
    legacy = [_case()]
    legacy[0]["source"] = "model_generated"
    runner.validate_cases(legacy, tmp_path / "cases.json")
    assert legacy[0]["source"] == "llm"
    legacy_case = [{"question": "旧格式问题", "expected_dsl": {"metrics": ["订单数"]}}]
    runner.validate_cases(legacy_case, tmp_path / "legacy_cases.json")

    assert runner.case_id(cases[0]) == "case_1"
    assert runner.case_question({"question": "旧问题"}) == "旧问题"
    assert runner.case_expected_dsl({"expected_dsl": {"metrics": ["订单数"]}}) == {"metrics": ["订单数"]}
    assert runner.case_gold_sql(cases[0]).startswith("select status")
    assert runner.case_metadata(cases[0])["business_domain"] == "订单分析"
    assert runner.is_seven_tuple_case(cases[0]) is True
    assert runner.normalize_sql(" select  *\n from orders ") == "select * from orders"
    assert runner.score_sql("select 1", "")["sql_exact_match"] is None
    assert runner.score_sql("select  1", "select 1")["sql_exact_match"] is True

    invalid_cases = [
        ([1], "case 必须是对象"),
        ([{"nl": "", "gold_dsl": {"metrics": ["订单数"]}}], "缺少 nl/question"),
        ([{"nl": "查", "gold_dsl": {}}], "缺少 gold_dsl/expected_dsl"),
        ([{"nl": "查", "gold_dsl": {"metrics": ["订单数"]}, "gold_sql": "select 1"}], "7 元组 case 字段不完整"),
        ([{**_case(), "query_type": "bad"}], "query_type 非法"),
        ([{**_case(), "difficulty": "bad"}], "difficulty 非法"),
        ([{**_case(), "source": "bad"}], "source 非法"),
        ([{**_case(), "business_domain": ""}], "business_domain 不能为空"),
    ]
    for payload, expected in invalid_cases:
        with pytest.raises(ValueError, match=expected):
            runner.validate_cases(payload, tmp_path / "cases.json")


def test_tool_content_and_sse_parsing_cover_json_markdown_and_errors():
    assert runner.parse_sse_data('{"ok": true}') == {"ok": True}
    assert runner.parse_sse_data("plain text") == "plain text"
    assert runner.parse_tool_content("") == {}
    assert runner.parse_tool_content("[data_analyzing_tool]") == {}
    assert runner.parse_tool_content("[data_analyzing_tool]\n" + json.dumps({"sql": "select 1"})) == {"sql": "select 1"}
    assert runner.parse_tool_content("[data_analyzing_tool]\n[]") == {}
    assert runner.parse_tool_content("[]") == {}
    assert runner.extract_sql_from_tool_content("") == ""
    assert runner.extract_sql_from_tool_content("[data_analyzing_tool]\n" + json.dumps({"sql": "select json"})) == "select json"
    assert runner.extract_sql_from_tool_content("[data_analyzing_tool]\n```sql\nselect * from orders\n```") == "select * from orders"
    assert runner.extract_sql_from_tool_content("with t as (select 1) select * from t") == "with t as (select 1) select * from t"
    assert runner.extract_sql_from_tool_content("执行失败") == ""
    assert runner.extract_error_from_event("message", {"error": "x"}) == ""
    assert runner.extract_error_from_event("error", {"message": "模型错误"}) == "模型错误"
    assert runner.extract_error_from_event("error", "plain") == "plain"

    state = {"current_event": "message", "data_lines": [], "error": ""}
    traces: list[ToolTrace] = []
    runner.consume_sse_line(line="event: tool", state=state, tool_traces=traces)
    runner.consume_sse_line(line='data: {"tool_name":"data_analyzing_tool","status":"done","args":{"action":"validate_dsl","action_payload":{"metrics":["订单数"]}},"content":"select 1"}', state=state, tool_traces=traces)
    runner.consume_sse_line(line="", state=state, tool_traces=traces)
    runner.consume_sse_line(line="event: error", state=state, tool_traces=traces)
    runner.consume_sse_line(line='data: {"error":"模型失败"}', state=state, tool_traces=traces)
    runner.consume_sse_line(line="ignored: value", state=state, tool_traces=traces)
    runner.flush_sse_event(state=state, tool_traces=traces)
    runner.flush_sse_event(state={"current_event": "", "data_lines": []}, tool_traces=traces)

    assert traces[0].name == "data_analyzing_tool"
    assert traces[0].args == {"action": "validate_dsl", "action_payload": {"metrics": ["订单数"]}}
    assert state["error"] == "模型失败"

    dsl = runner.normalize_pagination_values({"limit": " 10 ", "offset": "05", "keep": " 7a "})
    assert dsl == {"limit": 10, "offset": 5, "keep": " 7a "}
    runner.flush_sse_event(state={"current_event": None, "data_lines": []}, tool_traces=traces)
    assert len(traces) == 1


def test_dsl2sql_payload_selection_and_execution_summary():
    trace_from_json = ToolTrace(
        name="data_analyzing_tool",
        status="done",
        args={"metrics": ["订单数"], "limit": 5},
        content="[data_analyzing_tool]\n" + json.dumps({"success": True, "sql": "select count(*) from orders"}),
    )
    trace_from_text = ToolTrace(
        name="data_analyzing_tool",
        status="done",
        args={"dsl": {"metrics": ["销售额"]}},
        content="[data_analyzing_tool]\n```sql\nselect sum(amount) from orders\n```",
    )
    other_trace = ToolTrace(
        name="data_analyzing_tool",
        status="done",
        content=json.dumps({"status": "failed"}),
    )

    payload = runner.build_dsl2sql_payload(trace_from_json)
    assert payload["dsl"] == {"metrics": ["订单数"], "limit": 5}
    assert payload["sql"] == "select count(*) from orders"
    assert runner.build_dsl_from_tool_args("bad") == {}

    empty_payload = runner.build_dsl2sql_payload(ToolTrace(name="data_analyzing_tool", status="done", args={}, content="not sql"))
    assert empty_payload == {}

    json_without_dsl = ToolTrace(name="data_analyzing_tool", status="done", args={}, content=json.dumps({"success": True}))
    assert runner.build_dsl2sql_payload(json_without_dsl) == {"success": True}

    text_payload = runner.build_dsl2sql_payload(trace_from_text)
    assert text_payload["success"] is True
    assert text_payload["dsl"] == {"metrics": ["销售额"]}
    assert text_payload["sql"] == "select sum(amount) from orders"

    selected_trace, selected_payload = runner.choose_final_dsl2sql_trace([trace_from_json, trace_from_text])
    assert selected_trace is trace_from_text
    assert selected_payload["dsl"] == {"metrics": ["销售额"]}
    no_trace, no_payload = runner.choose_final_dsl2sql_trace([
        ToolTrace(name="calculator_tool", status="done", content=json.dumps({"success": True})),
        ToolTrace(name="data_analyzing_tool", status="done", content="bad"),
    ])
    assert no_trace is None
    assert no_payload == {}
    assert runner.summarize_execution_success([trace_from_json]) is None
    assert runner.summarize_execution_success([other_trace]) is False


def test_dsl2sql_payload_can_fill_sql_when_extractor_returns_value(monkeypatch):
    monkeypatch.setattr(runner, "extract_sql_from_tool_content", lambda _content: "select patched")
    payload = runner.build_dsl2sql_payload(ToolTrace(
        name="data_analyzing_tool",
        status="done",
        args={},
        content=json.dumps({"success": True}),
    ))

    assert payload["sql"] == "select patched"


@pytest.mark.asyncio
async def test_consume_internal_completion_reads_handler_stream(monkeypatch):
    async def fake_sql_handler(conversation_id, query, options, user_id):
        assert conversation_id == "conv_1"
        assert query == "查订单"
        assert options["datasource_id"] == "ds_a"
        assert user_id == "tester"

        async def body():
            payload = {"tool_name": "data_analyzing_tool", "status": "done", "content": "select 1"}
            yield f"event: tool\ndata: {json.dumps(payload)}\n\n".encode("utf-8")

        return StreamingResponse(body(), media_type="text/event-stream")

    import apps.chat_data_app.agent_stream as agent_stream

    monkeypatch.setattr(agent_stream, "sql_completion_handler", fake_sql_handler)

    traces, error = await runner.consume_internal_completion(
        conversation_id="conv_1",
        query="查订单",
        options={"datasource_id": "ds_a"},
        user_id="tester",
    )

    assert traces[0].name == "data_analyzing_tool"
    assert error == ""


@pytest.mark.asyncio
async def test_compile_and_execute_local_branches(monkeypatch):
    class FakeGenerator:
        def __init__(self, config):
            self.config = config

        def generate_sql(self, **kwargs):
            return {"success": True, "sql": "select sum(amount) from orders", "error": ""}

    async def fake_get_config(datasource_id, semantic_config_id):
        assert datasource_id == "ds_a"
        assert semantic_config_id == "sc_a"
        return {"id": "sc_a"}

    def fake_clean_dsl(dsl, semantic_config):
        return dsl, ["warning"]

    import apps.chat_data_app.services.dashboard.spec_service as dashboard_spec_service
    import apps.chat_data_app.services.semantic.run.semantic_query_service as semantic_query_service

    monkeypatch.setattr(dashboard_spec_service, "get_dashboard_spec_semantic_config", fake_get_config)
    monkeypatch.setattr(dashboard_spec_service, "remove_mixed_raw_metrics_for_aggregate_dsl", fake_clean_dsl)
    monkeypatch.setattr(semantic_query_service, "SemanticAIQueryAdapter", FakeGenerator)

    compiled = await runner.compile_dsl_locally(
        datasource_id="ds_a",
        semantic_config_id="sc_a",
        dsl={"metrics": ["销售额"]},
    )
    assert compiled["success"] is True
    assert compiled["warnings"] == ["warning"]

    compare_result = await runner.compile_dsl_locally(
        datasource_id="ds_a",
        semantic_config_id="sc_a",
        dsl={"compare": {"type": "yoy"}},
    )
    assert compare_result["success"] is False
    assert "复杂同比/环比请使用智能分析" in compare_result["error"]

    invalid_result = await runner.compile_dsl_locally(
        datasource_id="ds_a",
        semantic_config_id="sc_a",
        dsl=None,
    )
    assert invalid_result["success"] is False
    assert "未捕获到可编译 DSL" in invalid_result["error"]

    async def fake_execute_semantic_query_sql(sql, datasource_id, member_datasource_ids=None, user_id=None):
        return {"success": True, "data": [{"ok": 1}]}

    monkeypatch.setattr(semantic_query_service, "execute_semantic_query_sql", fake_execute_semantic_query_sql)
    assert (await runner.execute_sql_locally(sql="", datasource_id="ds_a", user_id="u"))["success"] is False
    assert (await runner.execute_sql_locally(sql="select 1", datasource_id="ds_a", user_id="u"))["success"] is True


@pytest.mark.asyncio
async def test_run_live_case_report_and_event_iteration(monkeypatch):
    async def fake_consume(**_kwargs):
        payload = {
            "success": True,
            "dsl": _case()["gold_dsl"],
            "sql": _case()["gold_sql"],
        }
        return [ToolTrace(name="data_analyzing_tool", status="done", content="[data_analyzing_tool]\n" + json.dumps(payload, ensure_ascii=False))], ""

    async def fake_compile(**_kwargs):
        return {"success": True, "sql": _case()["gold_sql"], "error": ""}

    async def fake_execute(**_kwargs):
        return {"success": True}

    monkeypatch.setattr(runner, "consume_internal_completion", fake_consume)
    monkeypatch.setattr(runner, "compile_dsl_locally", fake_compile)
    monkeypatch.setattr(runner, "execute_sql_locally", fake_execute)

    args = AccuracyRunConfig(
        datasource_id="ds_a",
        semantic_config_id="sc_a",
        user_id="tester",
        include_traces=True,
        local_compile=True,
        skip_execution=False,
    )
    result = await runner.run_live_case(args=args, case=_case(), datasource_id="ds_a", semantic_config_id="sc_a")
    assert result.status == "scored"
    assert result.compile_success is True
    assert result.execution_success is True
    assert result.dsl_score["dsl_exact_match"] is True
    assert result.sql_score["sql_exact_match"] is True

    payload = runner.live_result_to_dict(result, include_traces=True)
    assert payload["tool_traces"][0]["name"] == "data_analyzing_tool"

    report = runner.build_live_report([result], include_traces=False)
    assert report["summary"]["semantic_accuracy"] == 1.0
    assert report["summary"]["sql_execution_success_rate"] == 1.0

    live_report = await runner.run_live_score(args, [_case()])
    assert live_report["summary"]["total"] == 1

    events = []
    async for event in runner.iter_accuracy_evaluation_events(args, [_case()]):
        events.append(event["event"])
    assert events == ["start", "case_start", "case_result", "progress", "done"]


@pytest.mark.asyncio
async def test_run_live_case_compile_and_execute_failure_branches(monkeypatch):
    async def fake_consume(**kwargs):
        assert kwargs["options"]["model_id"] == "model_a"
        payload = {"success": True, "dsl": _case()["gold_dsl"], "sql": "select from tool"}
        return [ToolTrace(name="data_analyzing_tool", status="done", content=json.dumps(payload, ensure_ascii=False))], ""

    async def compile_failed(**_kwargs):
        return {"success": False, "sql": "", "error": "compile failed"}

    async def compile_success(**_kwargs):
        return {"success": True, "sql": _case()["gold_sql"], "error": ""}

    async def execute_failed(**_kwargs):
        return {"success": False, "error": "execute failed"}

    monkeypatch.setattr(runner, "consume_internal_completion", fake_consume)
    monkeypatch.setattr(runner, "compile_dsl_locally", compile_failed)

    failed_compile = await runner.run_live_case(
        args=AccuracyRunConfig(datasource_id="ds_a", semantic_config_id="sc_a", model_id="model_a", local_compile=True),
        case=_case("compile_failed"),
        datasource_id="ds_a",
        semantic_config_id="sc_a",
    )
    assert failed_compile.status == "scored"
    assert failed_compile.compile_success is False
    assert failed_compile.error == "compile failed"

    monkeypatch.setattr(runner, "compile_dsl_locally", compile_success)
    skip_execution = await runner.run_live_case(
        args=AccuracyRunConfig(datasource_id="ds_a", semantic_config_id="sc_a", model_id="model_a", local_compile=True, skip_execution=True),
        case=_case("skip_exec"),
        datasource_id="ds_a",
        semantic_config_id="sc_a",
    )
    assert skip_execution.compile_success is True
    assert skip_execution.execution_success is None

    monkeypatch.setattr(runner, "execute_sql_locally", execute_failed)
    failed_execution = await runner.run_live_case(
        args=AccuracyRunConfig(datasource_id="ds_a", semantic_config_id="sc_a", model_id="model_a", local_compile=True, skip_execution=False),
        case=_case("exec_failed"),
        datasource_id="ds_a",
        semantic_config_id="sc_a",
    )
    assert failed_execution.execution_success is False
    assert failed_execution.error == "execute failed"


@pytest.mark.asyncio
async def test_run_live_case_failure_paths_and_case_selection(monkeypatch):
    async def fake_consume_without_tool(**_kwargs):
        return [], "上游错误"

    monkeypatch.setattr(runner, "consume_internal_completion", fake_consume_without_tool)

    args = AccuracyRunConfig(datasource_id="ds_a", semantic_config_id="sc_a", local_compile=False)
    result = await runner.run_live_case(args=args, case=_case(), datasource_id="ds_a", semantic_config_id="sc_a")
    assert result.status == "failed"
    assert result.compile_success is False
    assert result.error == "上游错误"

    async def fake_consume_raises(**_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(runner, "consume_internal_completion", fake_consume_raises)
    failed = await runner.run_live_case(args=args, case=_case("case_2"), datasource_id="ds_a", semantic_config_id="sc_a")
    assert failed.status == "failed"
    assert "RuntimeError" in failed.error

    selected = runner.select_cases([_case("a"), _case("b")], AccuracyRunConfig(case_id=["b"], limit=1))
    assert [item["id"] for item in selected] == ["b"]
    with pytest.raises(ValueError, match="指定 case-id 不存在"):
        runner.select_cases([_case("a")], AccuracyRunConfig(case_id=["missing"]))
    with pytest.raises(ValueError, match="没有匹配的评估用例"):
        runner.select_cases([_case("a")], AccuracyRunConfig(limit=0))
    with pytest.raises(ValueError, match="缺少 datasource_id"):
        await runner.run_live_score(AccuracyRunConfig(semantic_config_id="sc_a"), [_case()])
    with pytest.raises(ValueError, match="缺少 semantic_config_id"):
        await runner.run_live_score(AccuracyRunConfig(datasource_id="ds_a"), [_case()])
    with pytest.raises(ValueError, match="缺少 datasource_id"):
        async for _event in runner.iter_accuracy_evaluation_events(AccuracyRunConfig(semantic_config_id="sc_a"), [_case()]):
            pass
    with pytest.raises(ValueError, match="缺少 semantic_config_id"):
        async for _event in runner.iter_accuracy_evaluation_events(AccuracyRunConfig(datasource_id="ds_a"), [_case()]):
            pass
    assert runner.select_cases([_case("a")], AccuracyRunConfig()).pop()["id"] == "a"


def test_live_report_handles_failed_and_unchecked_execution():
    empty_report = runner.build_live_report([], include_traces=False)
    assert empty_report["summary"]["semantic_accuracy"] == 0.0

    failed = LiveCaseResult(
        case_id="failed",
        question="失败用例",
        status="failed",
        dsl_score=scorers.score_dsl(None, {"metrics": ["销售额"]}),
        sql_score=runner.score_sql("", "select 1"),
        compile_success=False,
        execution_success=None,
        elapsed_ms=1,
    )
    scored = LiveCaseResult(
        case_id="scored",
        question="成功用例",
        status="scored",
        dsl_score=scorers.score_dsl({"metrics": ["销售额"]}, {"metrics": ["销售额"]}),
        sql_score=runner.score_sql("select 1", "select 2"),
        compile_success=False,
        execution_success=None,
        metadata={"query_type": "aggregation"},
        elapsed_ms=2,
    )

    report = runner.build_live_report([failed, scored], include_traces=False)

    assert report["summary"]["total"] == 2
    assert report["summary"]["failed"] == 1
    assert report["summary"]["execution_checked"] == 0
    assert report["summary"]["sql_execution_success_rate"] is None
    assert report["results"][1]["query_type"] == "aggregation"
