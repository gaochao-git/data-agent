import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.services import semantic_model_search as selector
from apps.chat_data_app.services.semantic_model_search import (
    _build_selector_prompt,
    _chunk_semantic_config_summaries,
    _merge_semantic_config_selections,
    _parse_selection_response,
    _retrieve_semantic_config_candidates,
    _summarize_semantic_config,
    search_semantic_models,
)


def test_parse_selection_response_keeps_only_known_retrieved_ids():
    configs = [{"id": "sc_order"}, {"id": "sc_user"}]

    parsed = _parse_selection_response(
        json.dumps({"semantic_config_ids": ["sc_user", "missing", "sc_user", "sc_order"]}),
        configs,
    )

    assert parsed == {"semantic_config_ids": ["sc_user", "sc_order"]}


def test_parse_selection_response_does_not_accept_legacy_selection_protocol():
    parsed = _parse_selection_response(
        json.dumps({"selected_config_id": "sc_order", "coverage": "full"}),
        [{"id": "sc_order"}],
    )

    assert parsed == {"semantic_config_ids": []}


def test_selector_prompt_is_retrieval_only_and_keeps_datasource_context():
    prompt = _build_selector_prompt(
        "统计订单金额",
        [{"id": "sc_order", "name": "订单分析"}],
        datasource_group_context="机构按数据源拆分",
    )
    payload = json.loads(prompt.split("输入：\n", 1)[1])

    assert payload["datasource_group_context"] == "机构按数据源拆分"
    assert "不负责回答、澄清、查询规划或计算" in prompt
    assert "这是检索，不是覆盖校验" in prompt
    assert "query_plan" not in prompt
    assert "cross_config_required" not in prompt
    assert '{"semantic_config_ids":["..."]}' in prompt


def test_retrieve_semantic_config_candidates_uses_directory_and_fields():
    configs = [
        {"id": "sc_order", "name": "订单分析", "description": "订单经营分析", "metrics": [{"name": "订单金额"}]},
        {"id": "sc_course", "name": "课程报名", "description": "课程和学生分析"},
    ]

    candidates, retrieval = _retrieve_semantic_config_candidates(configs, "查询订单金额", top_k=1)

    assert [item["id"] for item in candidates] == ["sc_order"]
    assert retrieval[0]["id"] == "sc_order"
    assert "search_terms" not in retrieval[0]


def test_summarize_semantic_config_keeps_query_facts_without_physical_expression():
    summary = _summarize_semantic_config({
        "id": "sc_order",
        "name": "订单分析",
        "description": "订单经营口径",
        "metrics": [{"name": "订单金额", "description": "实收金额", "expression": "SUM(amount)", "table": "orders"}],
        "dimensions": [{"name": "下单时间", "description": "订单创建时间", "table": "orders", "column": "created_at", "logical_type": "datetime"}],
        "tables": [{"table_name": "orders", "columns": [{"name": "amount", "type": "decimal", "description": "金额"}]}],
    })

    assert summary["id"] == "sc_order"
    assert summary["metrics"][0]["name"] == "订单金额"
    assert summary["dimensions"][0]["logical_type"] == "datetime"
    assert summary["tables"][0]["columns"][0]["name"] == "amount"
    assert "expression" not in summary["metrics"][0]
    assert "column" not in summary["dimensions"][0]


def test_chunk_semantic_config_summaries_never_splits_a_config(monkeypatch):
    summaries = [{"id": "sc_a"}, {"id": "sc_b"}, {"id": "sc_c"}]
    estimates = iter([4, 4, 8, 4, 8])
    monkeypatch.setattr(selector, "_estimate_selector_prompt_tokens", lambda *_args, **_kwargs: next(estimates))

    chunks = _chunk_semantic_config_summaries(summaries, "查询", token_budget=6)

    assert chunks == [[summaries[0]], [summaries[1]], [summaries[2]]]


def test_merge_semantic_config_selections_deduplicates_in_chunk_order(monkeypatch):
    monkeypatch.setattr(selector, "SELECTOR_MAX_SELECTED_CONFIGS", 3)

    merged = _merge_semantic_config_selections([
        {"semantic_config_ids": ["sc_order", "sc_user"]},
        {"semantic_config_ids": ["sc_user", "sc_product", "sc_other"]},
    ])

    assert merged == {"semantic_config_ids": ["sc_order", "sc_user", "sc_product"]}


class _FakeDatasourceManager:
    def __init__(self, configs):
        self.configs = configs

    async def get_runtime_semantic_configs_async(self, datasource_id, user_id=""):
        assert datasource_id == "ds_a"
        assert user_id == "u1"
        return self.configs


def test_search_semantic_models_returns_retrieved_ids(monkeypatch):
    configs = [
        {"id": "sc_order", "name": "订单分析", "enabled": True, "metrics": [{"name": "订单金额"}], "dimensions": []},
        {"id": "sc_user", "name": "用户分析", "enabled": True, "metrics": [{"name": "用户数"}], "dimensions": []},
    ]

    class FakeLLM:
        async def ainvoke(self, messages):
            assert "这是检索，不是覆盖校验" in messages[0].content
            return type("Response", (), {"content": '{"semantic_config_ids":["sc_order"]}'})()

    monkeypatch.setattr(selector, "get_datasource_manager", lambda: _FakeDatasourceManager(configs))
    monkeypatch.setattr(selector, "get_chat_data_llm", lambda **_kwargs: FakeLLM())

    selection = asyncio.run(search_semantic_models("ds_a", "查询订单金额", user_id="u1"))

    assert selection == {"semantic_config_ids": ["sc_order"]}


def test_search_semantic_models_merges_parallel_chunk_results(monkeypatch):
    configs = [
        {"id": "sc_order", "name": "订单分析", "enabled": True},
        {"id": "sc_user", "name": "用户分析", "enabled": True},
    ]

    async def fake_select_chunks(_llm, _query, _chunks, _context=""):
        return [{"semantic_config_ids": ["sc_order"]}, {"semantic_config_ids": ["sc_user", "sc_order"]}]

    monkeypatch.setattr(selector, "get_datasource_manager", lambda: _FakeDatasourceManager(configs))
    monkeypatch.setattr(selector, "get_chat_data_llm", lambda **_kwargs: object())
    monkeypatch.setattr(selector, "_chunk_semantic_config_summaries", lambda summaries, *_args, **_kwargs: [[summaries[0]], [summaries[1]]])
    monkeypatch.setattr(selector, "_select_semantic_config_chunks", fake_select_chunks)

    selection = asyncio.run(search_semantic_models("ds_a", "查询订单和用户", user_id="u1"))

    assert selection == {"semantic_config_ids": ["sc_order", "sc_user"]}


def test_search_semantic_models_returns_none_for_invalid_or_disabled_configs(monkeypatch):
    assert asyncio.run(search_semantic_models("", "查询")) is None
    assert asyncio.run(search_semantic_models("ds_a", "")) is None
    monkeypatch.setattr(selector, "get_datasource_manager", lambda: _FakeDatasourceManager([{"id": "sc_disabled", "enabled": False}]))

    assert asyncio.run(search_semantic_models("ds_a", "查询", user_id="u1")) is None
