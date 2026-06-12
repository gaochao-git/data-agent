import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1")
os.environ.setdefault("FXCJG_DB_PORT", "3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

import json
import asyncio

from apps.chat_data_app.services.semantic_config_selector import (
    _build_selector_prompt,
    _chunk_semantic_config_summaries,
    _collect_selection_candidates,
    _parse_selection_response,
    _summarize_semantic_config,
    select_semantic_config_for_query,
)


def test_parse_selection_response_keeps_only_valid_config_id():
    configs = [
        {"id": "sc_order", "name": "订单分析"},
        {"id": "sc_pay", "name": "支付汇总"},
    ]

    parsed = _parse_selection_response(json.dumps({
        "selected_config_id": "sc_pay",
        "coverage": "full",
        "confidence": "high",
        "reason": "命中交易金额",
        "matched_metrics": ["交易金额", ""],
        "matched_dimensions": ["机构"],
        "candidates": [
            {"id": "sc_pay", "coverage": "full", "confidence": "high", "reason": "包含交易金额"},
            {"id": "missing", "confidence": "high", "reason": "非法候选"},
        ],
    }, ensure_ascii=False), configs)

    assert parsed["selected_config_id"] == "sc_pay"
    assert parsed["selected_config_name"] == "支付汇总"
    assert parsed["coverage"] == "full"
    assert parsed["confidence"] == "high"
    assert parsed["matched_metrics"] == ["交易金额"]
    assert parsed["matched_dimensions"] == ["机构"]
    assert parsed["candidates"] == [{"id": "sc_pay", "name": "支付汇总", "coverage": "full", "confidence": "high", "reason": "包含交易金额"}]


def test_parse_selection_response_rejects_invalid_selected_id():
    parsed = _parse_selection_response('{"selected_config_id":"bad","confidence":"high"}', [{"id": "sc_a"}])

    assert parsed["selected_config_id"] == ""
    assert parsed["confidence"] == "none"
    assert parsed["coverage"] == "none"


def test_parse_selection_response_requires_explicit_coverage():
    parsed = _parse_selection_response('{"selected_config_id":"sc_a","confidence":"high"}', [{"id": "sc_a", "name": "指标集A"}])

    assert parsed["selected_config_id"] == "sc_a"
    assert parsed["confidence"] == "high"
    assert parsed["coverage"] == "none"


def test_parse_selection_response_blocks_ambiguous_full_matches():
    configs = [
        {"id": "sc_order", "name": "订单分析"},
        {"id": "sc_pay", "name": "支付汇总"},
    ]

    parsed = _parse_selection_response(json.dumps({
        "selected_config_id": "",
        "coverage": "full",
        "confidence": "none",
        "ambiguous": True,
        "ambiguity_reason": "两个指标集都有订单金额",
        "candidates": [
            {"id": "sc_order", "coverage": "full", "confidence": "high", "reason": "覆盖订单金额"},
            {"id": "sc_pay", "coverage": "full", "confidence": "high", "reason": "覆盖支付金额"},
        ],
    }, ensure_ascii=False), configs)

    assert parsed["ambiguous"] is True
    assert parsed["coverage"] == "full"
    assert parsed["selected_config_id"] == ""
    assert [item["name"] for item in parsed["candidates"]] == ["订单分析", "支付汇总"]


def test_parse_selection_response_forces_ambiguous_when_multiple_full_candidates_even_if_selected():
    configs = [
        {"id": "sc_order", "name": "订单分析基线"},
        {"id": "sc_test", "name": "测试"},
    ]

    parsed = _parse_selection_response(json.dumps({
        "selected_config_id": "sc_order",
        "coverage": "full",
        "confidence": "high",
        "reason": "两个指标集都包含商品数量指标",
        "candidates": [
            {"id": "sc_order", "coverage": "full", "confidence": "high", "reason": "包含商品数量"},
            {"id": "sc_test", "coverage": "full", "confidence": "high", "reason": "包含商品数量"},
        ],
    }, ensure_ascii=False), configs)

    assert parsed["selected_config_id"] == ""
    assert parsed["selected_config_name"] == ""
    assert parsed["confidence"] == "none"
    assert parsed["ambiguous"] is True
    assert [item["name"] for item in parsed["candidates"]] == ["订单分析基线", "测试"]


def test_parse_selection_response_uses_coverage_not_confidence_for_ambiguity():
    configs = [
        {"id": "sc_order", "name": "订单分析基线"},
        {"id": "sc_test", "name": "测试"},
    ]

    parsed = _parse_selection_response(json.dumps({
        "selected_config_id": "sc_order",
        "coverage": "full",
        "confidence": "low",
        "reason": "模型选择订单分析基线",
        "candidates": [
            {"id": "sc_order", "coverage": "full", "confidence": "low", "reason": "可回答"},
            {"id": "sc_test", "coverage": "full", "confidence": "none", "reason": "也可回答"},
        ],
    }, ensure_ascii=False), configs)

    assert parsed["selected_config_id"] == ""
    assert parsed["confidence"] == "none"
    assert parsed["ambiguous"] is True
    assert [item["id"] for item in parsed["candidates"]] == ["sc_order", "sc_test"]


def test_parse_selection_response_does_not_infer_full_coverage_from_confidence():
    configs = [
        {"id": "sc_order", "name": "订单分析基线"},
        {"id": "sc_test", "name": "测试"},
    ]

    parsed = _parse_selection_response(json.dumps({
        "selected_config_id": "sc_order",
        "coverage": "full",
        "confidence": "high",
        "candidates": [
            {"id": "sc_order", "confidence": "high", "reason": "候选未返回覆盖度"},
            {"id": "sc_test", "confidence": "high", "reason": "候选未返回覆盖度"},
        ],
    }, ensure_ascii=False), configs)

    assert parsed["selected_config_id"] == "sc_order"
    assert parsed["ambiguous"] is False
    assert [item["coverage"] for item in parsed["candidates"]] == ["partial", "partial"]


def test_parse_selection_response_detects_cross_config_required():
    configs = [
        {"id": "sc_order", "name": "订单分析"},
        {"id": "sc_product", "name": "商品分析"},
    ]

    parsed = _parse_selection_response(json.dumps({
        "selected_config_id": "",
        "coverage": "partial",
        "confidence": "none",
        "cross_config_required": True,
        "cross_config_reason": "订单金额和商品库存分属不同指标集",
        "candidates": [
            {"id": "sc_order", "coverage": "partial", "confidence": "medium", "reason": "覆盖订单金额"},
            {"id": "sc_product", "coverage": "partial", "confidence": "medium", "reason": "覆盖商品库存"},
        ],
    }, ensure_ascii=False), configs)

    assert parsed["cross_config_required"] is True
    assert parsed["cross_config_reason"] == "订单金额和商品库存分属不同指标集"
    assert parsed["coverage"] == "partial"
    assert parsed["candidates"][0]["coverage"] == "partial"


def test_summarize_semantic_config_truncates_business_fields():
    config = {
        "id": "sc_a",
        "name": "订单分析",
        "description": "x" * 300,
        "ontology_rules": "业务规则" * 200,
        "metrics": [{"name": "销售额", "table": "orders", "expression": "sum(amount)", "metadata_supplement": "金额单位元"}],
        "dimensions": [{"name": "机构", "table": "orders", "column": "org_name", "type": "categorical"}],
    }

    summary = _summarize_semantic_config(config)

    assert summary["id"] == "sc_a"
    assert summary["metrics"][0]["name"] == "销售额"
    assert summary["metrics"][0]["supplement"] == "金额单位元"
    assert summary["dimensions"][0]["name"] == "机构"
    assert "table" not in summary
    assert "table" not in summary["metrics"][0]
    assert "expression" not in summary["metrics"][0]
    assert "column" not in summary["dimensions"][0]
    assert len(summary["description"]) <= 183


def test_summarize_semantic_config_keeps_all_metrics_dimensions_and_business_values():
    config = {
        "id": "sc_all",
        "name": "全量指标集",
        "metrics": [
            {"name": f"指标{i}", "description": f"指标描述{i}", "expression": f"SUM(col_{i})", "table": "orders"}
            for i in range(130)
        ],
        "dimensions": [
            {
                "name": f"维度{i}",
                "description": f"维度描述{i}",
                "column": f"col_{i}",
                "table": "orders",
                "type": "categorical",
                "allowed_values": [f"枚举{i}_{j}" for j in range(3)],
                "mapping": {f"别名{i}_{j}": f"标准{i}_{j}" for j in range(2)},
            }
            for i in range(150)
        ],
    }

    summary = _summarize_semantic_config(config)

    assert summary["metrics_total"] == 130
    assert summary["dimensions_total"] == 150
    assert len(summary["metrics"]) == 130
    assert len(summary["dimensions"]) == 150
    assert summary["dimensions"][149]["allowed_values"] == ["枚举149_0", "枚举149_1", "枚举149_2"]
    assert summary["dimensions"][149]["mapping"] == {"别名149_0": "标准149_0", "别名149_1": "标准149_1"}


def test_selector_prompt_allows_detail_query_with_dimensions_without_metrics():
    prompt = _build_selector_prompt("查询明细", [{
        "id": "sc_category",
        "name": "分类明细",
        "metrics_total": 0,
        "dimensions_total": 6,
        "metrics": [],
        "dimensions": [{"name": "类别名称"}, {"name": "类别ID"}],
    }])

    assert "明细查询不要求 metrics" in prompt
    assert "按 dimensions 判断展示字段和过滤字段是否可覆盖" in prompt
    assert "不要因为 metrics_total=0 就判定不支持明细查询" in prompt
    assert "聚合统计必须有可表达用户统计口径的 metrics" in prompt


def test_chunk_semantic_config_summaries_never_splits_single_config(monkeypatch):
    summaries = [
        {"id": "sc_big", "name": "大指标集", "metrics": [{"name": f"指标{i}"} for i in range(100)]},
        {"id": "sc_small", "name": "小指标集", "metrics": [{"name": "指标"}]},
    ]
    calls = []

    def fake_estimate(_query, configs, _current=""):
        calls.append([item["id"] for item in configs])
        return 200 if configs[0]["id"] == "sc_big" and len(configs) == 1 else 150

    monkeypatch.setattr("apps.chat_data_app.services.semantic_config_selector._estimate_selector_prompt_tokens", fake_estimate)

    chunks = _chunk_semantic_config_summaries(summaries, "查指标", token_budget=100)

    assert chunks == [[summaries[0]], [summaries[1]]]
    assert ["sc_big"] in calls
    assert ["sc_big", "sc_small"] in calls


def test_collect_selection_candidates_keeps_more_than_legacy_five():
    configs = [{"id": f"sc_{i}", "name": f"指标集{i}"} for i in range(8)]
    selection = {
        "candidates": [
            {"id": f"sc_{i}", "coverage": "full", "confidence": "high", "reason": f"候选{i}"}
            for i in range(8)
        ]
    }

    candidates = _collect_selection_candidates([selection], configs)

    assert [item["id"] for item in candidates] == [f"sc_{i}" for i in range(8)]


class _FakeDatasourceManager:
    def __init__(self, configs):
        self.configs = configs

    async def get_semantic_configs_async(self, _datasource_id):
        return self.configs


class _FakeLLM:
    def __init__(self):
        self.prompts = []

    async def ainvoke(self, messages):
        prompt = messages[0].content
        self.prompts.append(prompt)
        data = json.loads(prompt.split("输入：\n", 1)[1])
        config_ids = [item["id"] for item in data.get("semantic_configs", [])]
        selected_id = "sc_36" if "sc_36" in config_ids else ""
        return type("FakeResponse", (), {"content": json.dumps({
            "selected_config_id": selected_id,
            "coverage": "full" if selected_id else "none",
            "confidence": "high" if selected_id else "none",
            "reason": "命中目标指标集" if selected_id else "未命中",
        }, ensure_ascii=False)})()


def test_select_semantic_config_for_query_uses_all_enabled_configs(monkeypatch):
    configs = [
        {"id": f"sc_{i}", "name": f"指标集{i}", "enabled": True, "metrics": [{"name": f"指标{i}"}], "dimensions": []}
        for i in range(40)
    ]
    fake_llm = _FakeLLM()

    monkeypatch.setattr("apps.chat_data_app.services.semantic_config_selector.get_datasource_manager", lambda: _FakeDatasourceManager(configs))
    monkeypatch.setattr("apps.chat_data_app.services.semantic_config_selector.get_chat_data_llm", lambda **_kwargs: fake_llm)
    monkeypatch.setattr("apps.chat_data_app.services.semantic_config_selector._chunk_semantic_config_summaries", lambda summaries, *_args, **_kwargs: [summaries])

    selection = asyncio.run(select_semantic_config_for_query("ds_a", "查询指标36"))

    assert selection["selected_config_id"] == "sc_36"
    assert len(fake_llm.prompts) == 1
    prompt_payload = json.loads(fake_llm.prompts[0].split("输入：\n", 1)[1])
    assert len(prompt_payload["semantic_configs"]) == 40


def test_select_semantic_config_for_query_blocks_multiple_full_matches_across_chunks(monkeypatch):
    class FakeChunkLLM:
        def __init__(self):
            self.prompts = []

        async def ainvoke(self, messages):
            prompt = messages[0].content
            self.prompts.append(prompt)
            data = json.loads(prompt.split("输入：\n", 1)[1])
            config_ids = [item["id"] for item in data.get("semantic_configs", [])]
            selected_id = config_ids[0] if config_ids else ""
            return type("FakeResponse", (), {"content": json.dumps({
                "selected_config_id": selected_id,
                "coverage": "full",
                "confidence": "low" if selected_id == "sc_order" else "high",
                "reason": "分片内模型判断可完整覆盖",
            }, ensure_ascii=False)})()

    configs = [
        {"id": "sc_order", "name": "订单分析基线", "enabled": True, "metrics": [{"name": "商品数量"}], "dimensions": []},
        {"id": "sc_test", "name": "测试", "enabled": True, "metrics": [{"name": "商品数量"}], "dimensions": []},
    ]
    fake_llm = FakeChunkLLM()

    monkeypatch.setattr("apps.chat_data_app.services.semantic_config_selector.get_datasource_manager", lambda: _FakeDatasourceManager(configs))
    monkeypatch.setattr("apps.chat_data_app.services.semantic_config_selector.get_chat_data_llm", lambda **_kwargs: fake_llm)
    monkeypatch.setattr("apps.chat_data_app.services.semantic_config_selector._chunk_semantic_config_summaries", lambda summaries, *_args, **_kwargs: [[summaries[0]], [summaries[1]]])

    selection = asyncio.run(select_semantic_config_for_query("ds_a", "商品数量有多少"))

    assert selection["selected_config_id"] == ""
    assert selection["coverage"] == "full"
    assert selection["confidence"] == "none"
    assert selection["ambiguous"] is True
    assert [item["id"] for item in selection["candidates"]] == ["sc_test", "sc_order"]
    assert len(fake_llm.prompts) == 2
