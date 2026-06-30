import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.routers import evaluation
from apps.chat_data_app.schema import AccuracyEvaluationCasesUpsertRequest, AccuracyEvaluationDraftCaseRequest, AccuracyEvaluationRunRequest
from utils.lang_util.models import Base, ChatDataEvalCase, ChatDataEvalCaseSet, ChatDataEvalRun, ChatDataEvalRunCase


@pytest.fixture()
def eval_db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    async def create_tables():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(create_tables())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def fake_get_session():
        async with session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr(evaluation, "get_session", fake_get_session)
    yield session_factory
    asyncio.run(engine.dispose())


def test_evaluation_scene_helpers_and_json_parsing():
    assert evaluation._sanitize_scene_segment(" ds/a 中文 ", "fallback") == "ds_a"
    assert evaluation._sanitize_scene_segment("中文", "fallback") == "fallback"
    assert evaluation._scene_case_paths("ds/a", "cfg b")["scene_id"] == "ds_a__cfg_b"
    assert len(evaluation._scene_case_set_id("ds_a", "cfg_a")) == 32
    assert evaluation._safe_json_loads('{"a":1}', {}) == {"a": 1}
    assert evaluation._safe_json_loads("{bad", {"fallback": True}) == {"fallback": True}


def test_case_row_to_payload_normalizes_model_source():
    row = ChatDataEvalCase(
        case_id="case_1",
        nl_text="查询销售额",
        gold_dsl_text='{"metrics":["销售额"]}',
        gold_sql_text="select sum(amount) from orders",
        query_type="aggregation",
        case_difficulty="easy",
        case_source="model_generated",
        business_domain="订单分析",
    )

    payload = evaluation._case_row_to_payload(row)

    assert payload["id"] == "case_1"
    assert payload["gold_dsl"] == {"metrics": ["销售额"]}
    assert payload["source"] == "llm"


def test_validate_eval_case_ids_rejects_missing_and_duplicate_ids():
    evaluation._validate_eval_case_ids([{"id": "case_1"}, {"id": "case_2"}])

    for cases, expected in [
        ([{"id": ""}], "缺少用例ID"),
        ([{"id": "case_1"}, {"id": "case_1"}], "用例ID重复"),
    ]:
        try:
            evaluation._validate_eval_case_ids(cases)
        except ValueError as exc:
            assert expected in str(exc)
        else:
            raise AssertionError("invalid case ids should be rejected")


def test_build_seed_case_candidates_covers_detail_aggregation_time_filter_and_join():
    config = {
        "metrics": [
            {"name": "销售额", "table": "orders", "expression": "SUM(amount)"},
            {"name": "订单数", "table": "orders", "expression": "COUNT(order_id)"},
        ],
        "dimensions": [
            {"name": "订单状态", "table": "orders", "column": "status", "type": "categorical", "allowed_values": ["paid"]},
            {"name": "下单时间", "table": "orders", "column": "created_at", "type": "time"},
            {"name": "商品分类", "table": "products", "column": "category", "type": "categorical", "allowed_values": ["数码"]},
            {"name": "用户ID", "table": "users", "column": "user_id", "type": "categorical"},
        ],
        "relationships": [{"left_table": "orders", "right_table": "products"}],
    }

    cases = evaluation._build_seed_case_candidates("cfg_a", config, "订单分析")
    query_types = {item["query_type"] for item in cases}
    questions = [item["nl"] for item in cases]

    assert "aggregation" in query_types
    assert "filter" in query_types
    assert any("每月" in question for question in questions)
    assert any("商品分类" in question for question in questions)
    assert any(case["gold_dsl"]["filters"] for case in cases)
    assert len(questions) == len(set(questions))


def test_build_seed_case_candidates_detail_table_uses_dimensions_only():
    config = {
        "metrics": [],
        "dimensions": [
            {"name": "订单ID", "table": "orders", "column": "order_id", "type": "categorical"},
            {"name": "订单状态", "table": "orders", "column": "status", "type": "categorical", "allowed_values": ["paid"]},
        ],
        "relationships": [],
    }

    cases = evaluation._build_seed_case_candidates("cfg_detail", config, "订单明细")

    assert cases[0]["nl"] == "查看明细数据"
    assert cases[0]["gold_dsl"]["metrics"] == []
    assert cases[0]["gold_dsl"]["dimensions"] == ["订单ID", "订单状态"]
    assert any(case["gold_dsl"]["filters"] for case in cases)


def test_build_seed_case_candidates_skip_and_limit_edges():
    detail_no_named_dimensions = evaluation._build_seed_case_candidates(
        "cfg_detail",
        {
            "metrics": [],
            "dimensions": [
                {"name": "", "table": "orders", "column": "status", "type": "categorical", "allowed_values": ["paid"]},
                {"name": "空值维度", "table": "orders", "column": "city", "type": "categorical", "allowed_values": []},
            ],
            "relationships": [],
        },
        "订单明细",
    )
    assert detail_no_named_dimensions[0]["gold_dsl"]["dimensions"] == ["空值维度"]
    assert all(case["gold_dsl"]["filters"] == [] for case in detail_no_named_dimensions)

    config = {
        "metrics": [
            {"name": f"指标{i}", "table": "orders", "expression": f"SUM(amount{i})"}
            for i in range(1, 8)
        ],
        "dimensions": [
            {"name": "", "table": "orders", "column": "ignored", "type": "categorical", "allowed_values": ["x"]},
            {"name": "跨表维度", "table": "dim_table", "column": "name", "type": "categorical", "allowed_values": ["A"]},
            {"name": "无枚举维度", "table": "orders", "column": "city", "type": "categorical", "allowed_values": []},
            {"name": "空枚举值维度", "table": "orders", "column": "status", "type": "categorical", "allowed_values": [""]},
        ],
        "relationships": [{"left_table": "orders", "right_table": "dim_table"}],
    }

    cases = evaluation._build_seed_case_candidates("cfg_many", config, "订单分析")

    assert len(cases) <= 30
    assert all(
        case["gold_dsl"]["filters"][0]["dimension"] != "空枚举值维度"
        for case in cases
        if case["gold_dsl"]["filters"]
    )
    assert any("跨表维度" in case["nl"] for case in cases)

    no_detail_case = evaluation._build_seed_case_candidates(
        "cfg_empty_detail",
        {
            "metrics": [],
            "dimensions": [{"name": "", "table": "orders", "column": "id", "type": "categorical"}],
            "relationships": [],
        },
        "订单明细",
    )
    assert no_detail_case == []

    skip_blank_dimension_config = {
        "metrics": [{"name": "销售额", "table": "orders", "expression": "SUM(amount)"}],
        "dimensions": [
            {"name": "", "table": "orders", "column": "city", "type": "categorical", "allowed_values": ["杭州"]},
            {"name": "", "table": "orders", "column": "dt", "type": "time"},
        ],
        "relationships": [],
    }
    skip_blank_cases = evaluation._build_seed_case_candidates("cfg_skip", skip_blank_dimension_config, "订单分析")
    assert [case["nl"] for case in skip_blank_cases] == ["查询销售额"]

    many_join_config = {
        "metrics": [
            {"name": f"指标{i}", "table": "fact", "expression": f"SUM(amount{i})"}
            for i in range(1, 12)
        ],
        "dimensions": [
            {"name": f"维度{i}", "table": f"dim{i}", "column": "name", "type": "categorical", "allowed_values": ["A"]}
            for i in range(1, 12)
        ],
        "relationships": [{"left_table": "fact", "right_table": "dim1"}],
    }
    many_join_cases = evaluation._build_seed_case_candidates("cfg_join_many", many_join_config, "订单分析")
    assert len(many_join_cases) <= 30


def test_build_seed_case_candidates_internal_empty_helper_edges(monkeypatch):
    monkeypatch.setattr(evaluation, "_metric_names", lambda _config: [])
    monkeypatch.setattr(evaluation, "_dimension_items", lambda _config: [{"name": ""}])
    monkeypatch.setattr(evaluation, "_categorical_dimensions", lambda _config: [])
    monkeypatch.setattr(evaluation, "_time_dimensions", lambda _config: [])
    assert evaluation._build_seed_case_candidates("cfg_blank_detail", {}, "订单分析") == []

    monkeypatch.setattr(evaluation, "_metric_names", lambda _config: ["销售额"])
    monkeypatch.setattr(evaluation, "_dimension_items", lambda _config: [])
    monkeypatch.setattr(evaluation, "_categorical_dimensions", lambda _config: [{"name": ""}])
    monkeypatch.setattr(evaluation, "_time_dimensions", lambda _config: [{"name": ""}])
    metric_only_cases = evaluation._build_seed_case_candidates("cfg_blank_dims", {"relationships": []}, "订单分析")
    assert [case["nl"] for case in metric_only_cases] == ["查询销售额"]

    monkeypatch.setattr(evaluation, "_categorical_dimensions", lambda _config: [])
    no_join_cases = evaluation._build_seed_case_candidates(
        "cfg_no_join_candidates",
        {"metrics": [{"name": "销售额", "table": "orders"}], "relationships": [{"left_table": "orders", "right_table": "users"}]},
        "订单分析",
    )
    assert [case["query_type"] for case in no_join_cases] == ["aggregation"]


def test_looks_like_sql_detects_select_and_with():
    assert evaluation._looks_like_sql("select * from orders") is True
    assert evaluation._looks_like_sql(" WITH t AS (select 1) select * from t") is True
    assert evaluation._looks_like_sql("查询订单金额") is False


def test_accuracy_bp_and_case_pass_logic():
    assert evaluation._accuracy_bp(0.87654) == 8765
    assert evaluation._accuracy_bp(2) == 10000
    assert evaluation._accuracy_bp(-1) == 0
    assert evaluation._accuracy_bp("bad") == 0
    assert evaluation._is_eval_case_passed({"status": "succeeded", "semantic_accuracy": 1, "dsl_exact_match": True, "compile_success": True}) is True
    assert evaluation._is_eval_case_passed({"status": "failed", "semantic_accuracy": 1}) is False
    assert evaluation._is_eval_case_passed({"status": "succeeded", "semantic_accuracy": 0.9}) is False
    assert evaluation._is_eval_case_passed({"status": "succeeded", "semantic_accuracy": 1, "dsl_exact_match": False}) is False


def test_eval_run_payload_formatters_include_summary_and_case_defaults():
    started = datetime(2026, 6, 2, 10, 0, 0)
    run = ChatDataEvalRun(
        run_id="run_1",
        case_set_id="set_1",
        datasource_id="ds_a",
        semantic_config_id="cfg_a",
        user_id="u1",
        run_mode="live",
        run_status="succeeded",
        request_payload='{"limit":10,"operator_name":"测试员"}',
        summary_payload='{"total":1}',
        total_count=1,
        scored_count=1,
        passed_count=1,
        failed_count=0,
        semantic_accuracy_bp=10000,
        started_time=started,
        finished_time=started,
        create_time=started,
        update_time=started,
        create_by="u1",
        update_by="u1",
    )
    case = ChatDataEvalRunCase(
        run_case_id="rc_1",
        run_id="run_1",
        case_id="case_1",
        case_status="passed",
        result_payload='{"semantic_accuracy":1}',
        semantic_accuracy_bp=10000,
        elapsed_ms=12,
        error_message=None,
        create_time=started,
        update_time=started,
    )

    run_payload = evaluation._eval_run_row_to_payload(run)
    case_payload = evaluation._eval_run_case_row_to_payload(case)

    assert run_payload["tester_name"] == "测试员"
    assert run_payload["semantic_accuracy"] == 1
    assert run_payload["started_time"] == "2026-06-02 10:00:00"
    assert case_payload["result"]["id"] == "case_1"
    assert case_payload["result"]["status"] == "passed"
    assert case_payload["semantic_accuracy"] == 1


def test_build_accuracy_runner_args_validates_scene():
    payload = AccuracyEvaluationRunRequest(
        datasource_id="ds_a",
        semantic_config_id="cfg_a",
        model_id="m1",
        limit=5,
        case_ids=["case_1"],
        include_traces=True,
        local_compile=False,
        skip_execution=True,
        request_timeout=120,
    )

    args = asyncio.run(evaluation._build_accuracy_runner_args(payload, SimpleNamespace(user_id="u1")))

    assert args.agent_id == "chat_data"
    assert args.datasource_id == "ds_a"
    assert args.semantic_config_id == "cfg_a"
    assert args.case_id == ["case_1"]
    assert args.limit == 5


def test_generate_draft_case_by_llm_handles_nl_and_sql(monkeypatch):
    captured = {}

    class FakeLLM:
        async def ainvoke(self, messages):
            captured["messages"] = messages
            FakeLLM.prompt = messages[0].content
            return SimpleNamespace(content=json.dumps({
                "nl": "查询销售额",
                "gold_dsl": {"metrics": ["销售额"], "dimensions": []},
                "query_type": "aggregation",
                "difficulty": "easy",
            }, ensure_ascii=False))

    monkeypatch.setattr(evaluation, "get_chat_data_llm", lambda **_kwargs: FakeLLM())
    monkeypatch.setattr(evaluation.time, "time", lambda: 123.456)
    config = {"id": "cfg_a", "name": "订单分析", "metrics": [{"name": "销售额"}], "dimensions": []}

    nl_case = asyncio.run(evaluation._generate_draft_case_by_llm(
        "cfg_a",
        config,
        AccuracyEvaluationDraftCaseRequest(hint="查销售额", existing_questions=[]),
    ))
    sql_case = asyncio.run(evaluation._generate_draft_case_by_llm(
        "cfg_a",
        config,
        AccuracyEvaluationDraftCaseRequest(hint="select sum(amount) from orders", existing_questions=[]),
    ))

    assert nl_case["nl"] == "查销售额"
    assert getattr(captured["messages"][0], "type", "") == "human"
    assert nl_case["id"] == "cfg_a_123456"
    assert sql_case["nl"] == "查询销售额"
    assert sql_case["gold_sql"] == "select sum(amount) from orders"


def test_generate_draft_case_by_llm_rejects_empty_or_invalid_response(monkeypatch):
    class BadLLM:
        async def ainvoke(self, _messages):
            return SimpleNamespace(content='{"nl": "", "gold_dsl": []}')

    monkeypatch.setattr(evaluation, "get_chat_data_llm", lambda **_kwargs: BadLLM())

    for request in [
        AccuracyEvaluationDraftCaseRequest(hint="", existing_questions=[]),
        AccuracyEvaluationDraftCaseRequest(hint="查销售额", existing_questions=[]),
    ]:
        try:
            asyncio.run(evaluation._generate_draft_case_by_llm("cfg_a", {"name": "订单"}, request))
        except ValueError as exc:
            assert "生成 gold_dsl" in str(exc) or "合法评测用例 JSON" in str(exc)
        else:
            raise AssertionError("invalid draft input should be rejected")


def test_iter_accuracy_evaluation_sse_updates_records(monkeypatch):
    calls = []

    async def fake_create(run_id, payload, current_user):
        calls.append(("create", run_id, payload.datasource_id, current_user.user_id))

    async def fake_args(payload, current_user):
        return SimpleNamespace(limit=payload.limit)

    async def fake_cases(payload, args):
        return [{"id": "case_1"}]

    async def fake_upsert(run_id, case_id, result, operator_id=None, operator_name=None):
        calls.append(("upsert", run_id, case_id, result["semantic_accuracy"]))

    async def fake_update(run_id, **kwargs):
        calls.append(("update", run_id, kwargs))

    async def fake_iter(_args, _cases):
        yield {"event": "case_result", "id": "case_1", "result": {"semantic_accuracy": 1, "status": "succeeded"}}
        yield {"event": "progress", "summary": {"scored": 1}}
        yield {"event": "heartbeat", "ts": 1}
        yield {"event": "done", "report": {"summary": {"total": 1, "scored": 1, "failed": 0, "semantic_accuracy": 1}}}

    monkeypatch.setattr(evaluation.uuid, "uuid4", lambda: SimpleNamespace(hex="run_1"))
    monkeypatch.setattr(evaluation, "_create_eval_run_record", fake_create)
    monkeypatch.setattr(evaluation, "_build_accuracy_runner_args", fake_args)
    monkeypatch.setattr(evaluation, "_load_accuracy_runner_cases", fake_cases)
    monkeypatch.setattr(evaluation, "_upsert_eval_run_case_record", fake_upsert)
    monkeypatch.setattr(evaluation, "_update_eval_run_record", fake_update)
    monkeypatch.setitem(sys.modules, "apps.chat_data_app.evaluate.accuracy.runner", SimpleNamespace(
        iter_accuracy_evaluation_events=fake_iter,
    ))

    events = asyncio.run(_collect(evaluation._iter_accuracy_evaluation_sse(
        AccuracyEvaluationRunRequest(datasource_id="ds_a", semantic_config_id="cfg_a"),
        request=None,
        current_user=SimpleNamespace(user_id="u1"),
    )))

    assert len(events) == 4
    assert '"event": "case_result"' in events[0]
    assert '"event": "heartbeat"' in events[2]
    assert ("upsert", "run_1", "case_1", 1) in calls
    assert any(call[0] == "update" and call[2].get("status") == "succeeded" for call in calls)


def test_iter_accuracy_evaluation_sse_done_without_report_skips_update(monkeypatch):
    calls = []

    async def fake_create(_run_id, _payload, _current_user):
        calls.append(("create",))

    async def fake_args(_payload, _current_user):
        return SimpleNamespace()

    async def fake_cases(_payload, _args):
        return []

    async def fake_update(run_id, **kwargs):
        calls.append(("update", run_id, kwargs))

    async def fake_iter(_args, _cases):
        yield {"event": "done"}

    monkeypatch.setattr(evaluation.uuid, "uuid4", lambda: SimpleNamespace(hex="run_done"))
    monkeypatch.setattr(evaluation, "_create_eval_run_record", fake_create)
    monkeypatch.setattr(evaluation, "_build_accuracy_runner_args", fake_args)
    monkeypatch.setattr(evaluation, "_load_accuracy_runner_cases", fake_cases)
    monkeypatch.setattr(evaluation, "_update_eval_run_record", fake_update)
    monkeypatch.setitem(sys.modules, "apps.chat_data_app.evaluate.accuracy.runner", SimpleNamespace(
        iter_accuracy_evaluation_events=fake_iter,
    ))

    events = asyncio.run(_collect(evaluation._iter_accuracy_evaluation_sse(
        AccuracyEvaluationRunRequest(datasource_id="ds_a", semantic_config_id="cfg_a"),
        request=None,
        current_user=SimpleNamespace(user_id="u1"),
    )))

    assert events == ['data: {"event": "done"}\n\n']
    assert calls == [("create",), ("update", "run_done", {"status": "succeeded", "summary": {}})]


async def _collect(async_iterable):
    return [item async for item in async_iterable]


@pytest.mark.asyncio
async def test_scene_case_set_db_lifecycle(eval_db):
    cases_v1 = [
        {
            "id": "case_1",
            "nl": "查询销售额",
            "gold_dsl": {"metrics": ["销售额"]},
            "gold_sql": "select sum(amount) from orders",
            "query_type": "aggregation",
            "difficulty": "easy",
            "source": "human",
            "business_domain": "订单分析",
        },
        {
            "id": "case_2",
            "nl": "查询订单数",
            "gold_dsl": {"metrics": ["订单数"]},
            "gold_sql": "select count(*) from orders",
            "query_type": "aggregation",
            "difficulty": "easy",
            "source": "llm",
            "business_domain": "订单分析",
        },
    ]

    case_set_id = await evaluation._save_scene_case_set("ds_a", "cfg_a", "订单集", "订单分析", cases_v1, operator_id="tester")
    loaded_v1 = await evaluation._load_scene_case_set("ds_a", "cfg_a")
    runner_cases = await evaluation._load_scene_runner_cases("ds_a", "cfg_a")

    cases_v2 = [
        {
            "id": "case_1",
            "nl": "查询已支付销售额",
            "gold_dsl": {"metrics": ["销售额"], "filters": [{"field": "状态", "operator": "=", "value": "paid"}]},
            "gold_sql": "select sum(amount) from orders where status = 'paid'",
            "query_type": "filter",
            "difficulty": "medium",
            "source": "human",
            "business_domain": "订单分析V2",
        }
    ]
    await evaluation._save_scene_case_set("ds_a", "cfg_a", "订单集V2", "订单分析V2", cases_v2, operator_id="tester2")
    loaded_v2 = await evaluation._load_scene_case_set("ds_a", "cfg_a")

    async with eval_db() as session:
        case_set = (await session.execute(select(ChatDataEvalCaseSet).filter_by(case_set_id=case_set_id))).scalar_one()
        all_cases = (await session.execute(select(ChatDataEvalCase).filter_by(case_set_id=case_set_id))).scalars().all()

    assert loaded_v1["business_domain"] == "订单分析"
    assert [item["id"] for item in loaded_v1["cases"]] == ["case_1", "case_2"]
    assert runner_cases[0]["gold_dsl"]["metrics"] == ["销售额"]
    assert loaded_v2["business_domain"] == "订单分析V2"
    assert loaded_v2["cases"][0]["nl"] == "查询已支付销售额"
    assert case_set.version_no == 2
    assert case_set.case_count == 1
    assert {row.case_id: row.delete_flag for row in all_cases} == {"case_1": 0, "case_2": 1}


@pytest.mark.asyncio
async def test_load_scene_runner_cases_requires_saved_cases(eval_db):
    with pytest.raises(ValueError, match="尚未配置测试集"):
        await evaluation._load_scene_runner_cases("ds_missing", "cfg_missing")


@pytest.mark.asyncio
async def test_eval_run_record_db_lifecycle_and_history_routes(eval_db):
    current_user = SimpleNamespace(user_id="u1", username="测试人员")
    payload = AccuracyEvaluationRunRequest(
        datasource_id="ds_a",
        semantic_config_id="cfg_a",
        model_id="m1",
        limit=2,
        case_ids=["case_1"],
    )

    await evaluation._create_eval_run_record("run_1", payload, current_user)
    await evaluation._upsert_eval_run_case_record("run_1", "case_1", {
        "id": "case_1",
        "status": "succeeded",
        "semantic_accuracy": 1,
        "dsl_exact_match": True,
        "compile_success": True,
        "elapsed_ms": 12,
    })
    await evaluation._upsert_eval_run_case_record("run_1", "case_2", {
        "id": "case_2",
        "status": "failed",
        "semantic_accuracy": 0,
        "error": "SQL错误",
    })
    await evaluation._upsert_eval_run_case_record("run_1", "case_2", {
        "id": "case_2",
        "status": "succeeded",
        "semantic_accuracy": 1,
        "dsl_exact_match": True,
        "compile_success": True,
        "elapsed_ms": 20,
    })
    await evaluation._update_eval_run_record("run_1", status="succeeded", summary={"total": 2, "scored": 2, "failed": 0, "semantic_accuracy": 1})
    await evaluation._update_eval_run_record("run_1")
    await evaluation._update_eval_run_record("run_1", summary={"total": 3, "scored": 2, "failed": 1})
    await evaluation._update_eval_run_record("run_1", error_message="")
    await evaluation._update_eval_run_record("missing", status="failed")

    list_response = await evaluation.list_accuracy_evaluation_runs(datasource_id="ds_a", semantic_config_id="cfg_a", page=-1, page_size=500)
    list_all_response = await evaluation.list_accuracy_evaluation_runs(page=1, page_size=1)
    detail_response = await evaluation.get_accuracy_evaluation_run_detail("run_1")
    missing_response = await evaluation.get_accuracy_evaluation_run_detail("missing")

    async with eval_db() as session:
        run_row = (await session.execute(select(ChatDataEvalRun).filter_by(run_id="run_1"))).scalar_one()
        case_rows = (await session.execute(select(ChatDataEvalRunCase).filter_by(run_id="run_1").order_by(ChatDataEvalRunCase.case_id))).scalars().all()

    list_payload = json.loads(list_response.body.decode("utf-8"))
    list_all_payload = json.loads(list_all_response.body.decode("utf-8"))
    detail_payload = json.loads(detail_response.body.decode("utf-8"))
    missing_payload = json.loads(missing_response.body.decode("utf-8"))

    assert run_row.run_status == "succeeded"
    assert run_row.finished_time is not None
    assert run_row.total_count == 3
    assert run_row.passed_count == 1
    assert run_row.error_message is None
    assert [row.case_status for row in case_rows] == ["passed", "passed"]
    assert list_payload["data"]["page"] == 1
    assert list_payload["data"]["page_size"] == 100
    assert list_all_payload["data"]["total"] == 1
    assert list_payload["data"]["runs"][0]["tester_name"] == "测试人员"
    assert detail_payload["data"]["run"]["run_id"] == "run_1"
    assert len(detail_payload["data"]["cases"]) == 2
    assert missing_payload["code"] == 40404


def test_accuracy_routes_error_mapping(monkeypatch):
    monkeypatch.setattr(evaluation, "_get_current_user", lambda _request: SimpleNamespace(user_id="u1", username="测试人员"))

    async def raise_value_error(_payload, _request, _current_user):
        raise ValueError("测试集为空")

    monkeypatch.setattr(evaluation, "_run_accuracy_evaluation_payload", raise_value_error)
    value_response = asyncio.run(evaluation.run_accuracy_evaluation(AccuracyEvaluationRunRequest(datasource_id="ds_a", semantic_config_id="cfg_a"), None))

    async def raise_runtime_error(_payload, _request, _current_user):
        raise RuntimeError("llm down")

    monkeypatch.setattr(evaluation, "_run_accuracy_evaluation_payload", raise_runtime_error)
    runtime_response = asyncio.run(evaluation.run_accuracy_evaluation(AccuracyEvaluationRunRequest(datasource_id="ds_a", semantic_config_id="cfg_a"), None))

    value_payload = json.loads(value_response.body.decode("utf-8"))
    runtime_payload = json.loads(runtime_response.body.decode("utf-8"))

    assert value_payload["code"] == 40001
    assert runtime_payload["code"] == 50056


def test_run_accuracy_stream_returns_auth_error(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(evaluation, "_get_current_user", lambda _request: (_ for _ in ()).throw(HTTPException(status_code=401, detail="未登录")))

    response = asyncio.run(evaluation.run_accuracy_evaluation_stream(AccuracyEvaluationRunRequest(datasource_id="ds_a", semantic_config_id="cfg_a"), None))
    chunks = asyncio.run(_collect(response.body_iterator))

    assert "未登录" in "".join(chunks)


def test_accuracy_sse_iter_handles_value_and_runtime_errors(monkeypatch):
    updates = []

    async def fake_create(_run_id, _payload, _current_user):
        return None

    async def fake_update(run_id, **kwargs):
        updates.append((run_id, kwargs))

    async def raise_value_error(_payload, _current_user):
        raise ValueError("测试集为空")

    monkeypatch.setattr(evaluation.uuid, "uuid4", lambda: SimpleNamespace(hex="run_error"))
    monkeypatch.setattr(evaluation, "_create_eval_run_record", fake_create)
    monkeypatch.setattr(evaluation, "_update_eval_run_record", fake_update)
    monkeypatch.setattr(evaluation, "_build_accuracy_runner_args", raise_value_error)

    value_events = asyncio.run(_collect(evaluation._iter_accuracy_evaluation_sse(
        AccuracyEvaluationRunRequest(datasource_id="ds_a", semantic_config_id="cfg_a"),
        request=None,
        current_user=SimpleNamespace(user_id="u1"),
    )))

    async def raise_runtime_error(_payload, _current_user):
        raise RuntimeError("runner down")

    monkeypatch.setattr(evaluation, "_build_accuracy_runner_args", raise_runtime_error)
    runtime_events = asyncio.run(_collect(evaluation._iter_accuracy_evaluation_sse(
        AccuracyEvaluationRunRequest(datasource_id="ds_a", semantic_config_id="cfg_a"),
        request=None,
        current_user=SimpleNamespace(user_id="u1"),
    )))

    assert "测试集为空" in "".join(value_events)
    assert "评测失败: runner down" in "".join(runtime_events)
    assert any(call[1].get("status") == "failed" for call in updates)


def _json_body(response):
    return json.loads(response.body.decode("utf-8"))


def test_accuracy_routes_success_auth_and_stream_response(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(evaluation, "_get_current_user", lambda _request: SimpleNamespace(user_id="u1", username="测试人员"))

    async def fake_run(payload, request, current_user):
        return {"summary": {"total": payload.limit}, "user": current_user.username}

    monkeypatch.setattr(evaluation, "_run_accuracy_evaluation_payload", fake_run)
    success_response = asyncio.run(evaluation.run_accuracy_evaluation(
        AccuracyEvaluationRunRequest(datasource_id="ds_a", semantic_config_id="cfg_a", limit=3),
        None,
    ))
    stream_response = asyncio.run(evaluation.run_accuracy_evaluation_stream(
        AccuracyEvaluationRunRequest(datasource_id="ds_a", semantic_config_id="cfg_a"),
        None,
    ))

    monkeypatch.setattr(evaluation, "_get_current_user", lambda _request: (_ for _ in ()).throw(HTTPException(status_code=403, detail="无权限")))
    auth_response = asyncio.run(evaluation.run_accuracy_evaluation(
        AccuracyEvaluationRunRequest(datasource_id="ds_a", semantic_config_id="cfg_a"),
        None,
    ))

    success_payload = _json_body(success_response)
    auth_payload = _json_body(auth_response)

    assert success_payload["msg"] == "评测完成"
    assert success_payload["data"]["summary"]["total"] == 3
    assert auth_payload["code"] == 40301
    assert stream_response.media_type == "text/event-stream"
    assert stream_response.headers["Cache-Control"] == "no-cache"


def test_build_accuracy_runner_args_and_cases_validate_required_scene(monkeypatch):
    missing_payload = AccuracyEvaluationRunRequest.model_construct(datasource_id="", semantic_config_id="")

    with pytest.raises(ValueError, match="指定数据源和指标集"):
        asyncio.run(evaluation._build_accuracy_runner_args(missing_payload, SimpleNamespace(user_id="u1")))

    with pytest.raises(ValueError, match="指定数据源和指标集"):
        asyncio.run(evaluation._load_accuracy_runner_cases(missing_payload, SimpleNamespace()))

    calls = []

    async def fake_load_cases(datasource_id, semantic_config_id):
        calls.append(("load", datasource_id, semantic_config_id))
        return [{"id": "case_1"}, {"id": "case_2"}]

    def fake_validate(cases, path):
        calls.append(("validate", len(cases), str(path)))

    def fake_select(cases, args):
        calls.append(("select", getattr(args, "limit", None)))
        return cases[:1]

    monkeypatch.setattr(evaluation, "_load_scene_runner_cases", fake_load_cases)
    monkeypatch.setitem(sys.modules, "apps.chat_data_app.evaluate.accuracy.runner", SimpleNamespace(
        select_cases=fake_select,
        validate_cases=fake_validate,
    ))

    selected = asyncio.run(evaluation._load_accuracy_runner_cases(
        AccuracyEvaluationRunRequest(datasource_id="ds_a", semantic_config_id="cfg_a"),
        SimpleNamespace(limit=1),
    ))

    assert selected == [{"id": "case_1"}]
    assert calls == [
        ("load", "ds_a", "cfg_a"),
        ("validate", 2, "chat_data_eval_case"),
        ("select", 1),
    ]


def test_run_accuracy_evaluation_payload_success_and_failure(monkeypatch):
    calls = []

    async def fake_create(run_id, payload, current_user):
        calls.append(("create", run_id, current_user.username))

    async def fake_args(payload, current_user):
        return SimpleNamespace(limit=payload.limit)

    async def fake_cases(payload, args):
        return [{"id": "case_1"}]

    async def fake_upsert(run_id, case_id, result, operator_id=None, operator_name=None):
        calls.append(("upsert", run_id, case_id, result["semantic_accuracy"]))

    async def fake_update(run_id, **kwargs):
        calls.append(("update", run_id, kwargs))

    async def fake_run_live_score(_args, _cases):
        return {
            "summary": {"total": 1, "scored": 1, "failed": 0, "semantic_accuracy": 1},
            "results": [{"id": "case_1", "semantic_accuracy": 1, "status": "succeeded"}],
        }

    monkeypatch.setattr(evaluation.uuid, "uuid4", lambda: SimpleNamespace(hex="run_payload"))
    monkeypatch.setattr(evaluation, "_create_eval_run_record", fake_create)
    monkeypatch.setattr(evaluation, "_build_accuracy_runner_args", fake_args)
    monkeypatch.setattr(evaluation, "_load_accuracy_runner_cases", fake_cases)
    monkeypatch.setattr(evaluation, "_upsert_eval_run_case_record", fake_upsert)
    monkeypatch.setattr(evaluation, "_update_eval_run_record", fake_update)
    monkeypatch.setitem(sys.modules, "apps.chat_data_app.evaluate.accuracy.runner", SimpleNamespace(
        run_live_score=fake_run_live_score,
    ))

    report = asyncio.run(evaluation._run_accuracy_evaluation_payload(
        AccuracyEvaluationRunRequest(datasource_id="ds_a", semantic_config_id="cfg_a"),
        request=None,
        current_user=SimpleNamespace(user_id="u1", username="测试人员"),
    ))

    async def raise_run_live_score(_args, _cases):
        raise RuntimeError("runner failed")

    monkeypatch.setitem(sys.modules, "apps.chat_data_app.evaluate.accuracy.runner", SimpleNamespace(
        run_live_score=raise_run_live_score,
    ))

    with pytest.raises(RuntimeError, match="runner failed"):
        asyncio.run(evaluation._run_accuracy_evaluation_payload(
            AccuracyEvaluationRunRequest(datasource_id="ds_a", semantic_config_id="cfg_a"),
            request=None,
            current_user=SimpleNamespace(user_id="u1", username="测试人员"),
        ))

    assert report["summary"]["semantic_accuracy"] == 1
    assert ("upsert", "run_payload", "case_1", 1) in calls
    assert any(call[0] == "update" and call[2].get("status") == "succeeded" for call in calls)
    assert any(call[0] == "update" and call[2].get("status") == "failed" for call in calls)


def test_accuracy_sse_iter_handles_cancelled(monkeypatch):
    updates = []

    async def fake_create(_run_id, _payload, _current_user):
        return None

    async def fake_args(_payload, _current_user):
        return SimpleNamespace()

    async def fake_cases(_payload, _args):
        return [{"id": "case_1"}]

    async def fake_update(run_id, **kwargs):
        updates.append((run_id, kwargs))

    async def fake_iter(_args, _cases):
        raise asyncio.CancelledError()
        yield {}

    monkeypatch.setattr(evaluation.uuid, "uuid4", lambda: SimpleNamespace(hex="run_cancel"))
    monkeypatch.setattr(evaluation, "_create_eval_run_record", fake_create)
    monkeypatch.setattr(evaluation, "_build_accuracy_runner_args", fake_args)
    monkeypatch.setattr(evaluation, "_load_accuracy_runner_cases", fake_cases)
    monkeypatch.setattr(evaluation, "_update_eval_run_record", fake_update)
    monkeypatch.setitem(sys.modules, "apps.chat_data_app.evaluate.accuracy.runner", SimpleNamespace(
        iter_accuracy_evaluation_events=fake_iter,
    ))

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_collect(evaluation._iter_accuracy_evaluation_sse(
            AccuracyEvaluationRunRequest(datasource_id="ds_a", semantic_config_id="cfg_a"),
            request=None,
            current_user=SimpleNamespace(user_id="u1"),
        )))

    assert updates == [("run_cancel", {"status": "cancelled", "error_message": "评测已停止"})]


def test_compile_scene_case_gold_sql_success_and_error(monkeypatch):
    async def fake_compile_success(**kwargs):
        return {"success": True, "sql": "select sum(amount) from orders", "seen": kwargs}

    monkeypatch.setitem(sys.modules, "apps.chat_data_app.evaluate.accuracy.runner", SimpleNamespace(
        compile_dsl_locally=fake_compile_success,
    ))

    compiled = asyncio.run(evaluation._compile_scene_case_gold_sql(
        "ds_a",
        "cfg_a",
        {"id": "case_1", "nl": "查询销售额", "gold_dsl": {"metrics": ["销售额"]}},
    ))

    async def fake_compile_error(**_kwargs):
        return {"success": False, "error": "字段不存在"}

    monkeypatch.setitem(sys.modules, "apps.chat_data_app.evaluate.accuracy.runner", SimpleNamespace(
        compile_dsl_locally=fake_compile_error,
    ))

    with pytest.raises(ValueError, match="字段不存在"):
        asyncio.run(evaluation._compile_scene_case_gold_sql(
            "ds_a",
            "cfg_a",
            {"id": "case_2", "nl": "查询订单数", "gold_dsl": {"metrics": ["订单数"]}},
        ))

    assert compiled["gold_sql"] == "select sum(amount) from orders"


def test_scene_evaluation_case_routes_success_and_errors(monkeypatch, eval_db):
    class FakeManager:
        async def get_semantic_config_async(self, datasource_id, config_id):
            if config_id == "missing":
                return None
            if config_id == "boom":
                raise RuntimeError("配置读取失败")
            return {"id": config_id, "name": "订单分析"}

    async def fake_compile(datasource_id, config_id, case):
        if case.get("id") == "bad_compile":
            raise ValueError("编译失败")
        return {**case, "gold_sql": f"select '{case['id']}'"}

    async def fake_generate(config_id, config, payload):
        if payload.hint == "bad":
            raise ValueError("LLM失败")
        return {
            "id": "draft_1",
            "nl": payload.hint or "查询销售额",
            "gold_dsl": {"metrics": ["销售额"]},
            "gold_sql": "",
            "query_type": "aggregation",
            "difficulty": "easy",
            "source": "llm",
            "business_domain": "订单分析",
        }

    monkeypatch.setattr(evaluation, "get_datasource_manager", lambda: FakeManager())
    monkeypatch.setattr(evaluation, "_get_current_user", lambda _request: SimpleNamespace(user_id="u1", username="测试人员"))
    monkeypatch.setattr(evaluation, "_compile_scene_case_gold_sql", fake_compile)
    monkeypatch.setattr(evaluation, "_generate_draft_case_by_llm", fake_generate)
    monkeypatch.setattr(evaluation, "_build_seed_case_candidates", lambda _config_id, _config, domain: [{
        "id": "seed_1",
        "nl": "查询销售额",
        "gold_dsl": {"metrics": ["销售额"]},
        "gold_sql": "",
        "query_type": "aggregation",
        "difficulty": "easy",
        "source": "llm",
        "business_domain": domain,
    }])

    upsert_payload = AccuracyEvaluationCasesUpsertRequest(
        business_domain="订单分析",
        cases=[{"id": "case_1", "nl": "查询销售额", "gold_dsl": {"metrics": ["销售额"]}, "business_domain": "订单分析"}],
    )
    request = SimpleNamespace()
    save_response = asyncio.run(evaluation.save_scene_evaluation_cases("ds_a", "cfg_a", upsert_payload, request))
    get_response = asyncio.run(evaluation.get_scene_evaluation_cases("ds_a", "cfg_a"))
    generate_response = asyncio.run(evaluation.generate_scene_evaluation_cases("ds_a", "cfg_a"))
    draft_response = asyncio.run(evaluation.draft_scene_evaluation_case(
        "ds_a",
        "cfg_a",
        AccuracyEvaluationDraftCaseRequest(hint="查询销售额", existing_questions=[]),
    ))
    missing_generate_response = asyncio.run(evaluation.generate_scene_evaluation_cases("ds_a", "missing"))
    missing_draft_response = asyncio.run(evaluation.draft_scene_evaluation_case(
        "ds_a",
        "missing",
        AccuracyEvaluationDraftCaseRequest(hint="查询销售额", existing_questions=[]),
    ))
    bad_save_response = asyncio.run(evaluation.save_scene_evaluation_cases(
        "ds_a",
        "cfg_a",
        AccuracyEvaluationCasesUpsertRequest(
            business_domain="订单分析",
            cases=[{"id": "bad_compile", "nl": "查询坏用例", "gold_dsl": {}, "business_domain": "订单分析"}],
        ),
        request,
    ))
    bad_draft_response = asyncio.run(evaluation.draft_scene_evaluation_case(
        "ds_a",
        "cfg_a",
        AccuracyEvaluationDraftCaseRequest(hint="bad", existing_questions=[]),
    ))
    boom_save_response = asyncio.run(evaluation.save_scene_evaluation_cases("ds_a", "boom", upsert_payload, request))

    save_payload = _json_body(save_response)
    get_payload = _json_body(get_response)
    generate_payload = _json_body(generate_response)
    draft_payload = _json_body(draft_response)

    assert save_payload["msg"] == "保存成功"
    assert save_payload["data"]["cases"][0]["gold_sql"] == "select 'case_1'"
    assert get_payload["data"]["total"] == 1
    assert generate_payload["msg"] == "生成草稿成功"
    assert draft_payload["data"]["case"]["id"] == "draft_1"
    assert _json_body(missing_generate_response)["code"] == 40404
    assert _json_body(missing_draft_response)["code"] == 40404
    assert _json_body(bad_save_response)["code"] == 40001
    assert _json_body(bad_draft_response)["code"] == 40001
    assert _json_body(boom_save_response)["code"] == 50058


@pytest.mark.asyncio
async def test_eval_run_record_update_missing_and_payload_edge_cases(eval_db):
    assert evaluation._safe_json_loads("", {"empty": True}) == {"empty": True}

    await evaluation._update_eval_run_record("missing", status="failed", summary={"total": 1}, error_message="err")
    await evaluation._create_eval_run_record(
        "run_edge",
        AccuracyEvaluationRunRequest(datasource_id="ds_a", semantic_config_id="cfg_a"),
        SimpleNamespace(user_id="", username=""),
    )
    await evaluation._update_eval_run_record("run_edge", status="running", summary={"total": 3, "scored": 1, "failed": 2, "semantic_accuracy": "bad"}, error_message="")
    await evaluation._upsert_eval_run_case_record("run_edge", "case_raw", {"semantic_accuracy": 1, "result": "ok"})

    async with eval_db() as session:
        run_row = (await session.execute(select(ChatDataEvalRun).filter_by(run_id="run_edge"))).scalar_one()

    payload = evaluation._eval_run_row_to_payload(run_row)
    case_payload = evaluation._eval_run_case_row_to_payload(SimpleNamespace(
        run_case_id="rc_raw",
        run_id="run_edge",
        case_id="case_raw",
        case_status="passed",
        result_payload='"raw-result"',
        semantic_accuracy_bp=10000,
        elapsed_ms=None,
        error_message=None,
        create_time=None,
        update_time=None,
    ))

    assert run_row.finished_time is None
    assert run_row.passed_count == 0
    assert run_row.error_message is None
    assert payload["tester_name"] == ""
    assert case_payload["result"] == "raw-result"
