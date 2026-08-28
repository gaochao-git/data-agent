"""semantic_model_search.py 分支补测。

该文件此前分支覆盖仅 66.7%：所有缺口都落在纯函数上（表/关系摘要裁剪、
分片预算、结果归并、异常收敛），无需任何外部依赖即可走通。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from apps.chat_data_app.services.semantic.run import semantic_model_search as search_module
from apps.chat_data_app.services.semantic.run.semantic_model_search import (
    _chunk_semantic_config_summaries,
    _compact_relationship_summary,
    _compact_table_summary,
    _normalize_semantic_config_ids,
    _parse_selection_response,
    _select_semantic_config_chunks,
    _semantic_table_names,
    _truncate,
    search_semantic_models,
)


# ---------------------------------------------------------------------------
# 文本裁剪
# ---------------------------------------------------------------------------


def test_truncate_appends_ellipsis_only_when_overflowing():
    assert _truncate("abcdef", 10) == "abcdef"
    assert _truncate("abcdef", 3) == "abc..."
    assert _truncate(None, 3) == ""


# ---------------------------------------------------------------------------
# 表 / 关系摘要
# ---------------------------------------------------------------------------


def test_semantic_table_names_collects_relationships_partially():
    """关系左右表都可能缺失，缺失一侧不产生表名，循环仍要跑完。"""
    config = {
        "metrics": [{"table": "orders"}],
        "dimensions": [{"table": "dim_user"}],
        "relationships": [
            {"left_table": "orders", "right_table": "dim_user"},   # 两侧都有
            {"left_table": "", "right_table": "dim_item"},         # 左表缺失
            {"left_table": "orders", "right_table": ""},           # 右表缺失
            {"left_table": "", "right_table": ""},                 # 两侧都缺失
        ],
    }
    assert _semantic_table_names(config) == {"orders", "dim_user", "dim_item"}


def test_compact_table_summary_skips_unreferenced_and_nameless_entries():
    config = {
        "metrics": [{"table": "orders"}],
        "tables": [
            {"table_name": "", "columns": [{"name": "id"}]},              # 无表名
            {"table_name": "unused_table", "columns": [{"name": "x"}]},   # 未被引用
            {
                "table_name": "orders",
                "columns": [
                    {"column_name": ""},                                   # 无字段名
                    {"name": "amount", "type": "bigint", "comment": "金额"},
                ],
            },
        ],
    }
    result = _compact_table_summary(config)
    assert [table["table_name"] for table in result] == ["orders"]
    assert [column["name"] for column in result[0]["columns"]] == ["amount"]
    assert result[0]["columns"][0]["type"] == "bigint"
    assert result[0]["columns"][0]["description"] == "金额"


def test_compact_table_summary_returns_empty_without_referenced_tables():
    assert _compact_table_summary({"metrics": [], "tables": [{"table_name": "orders"}]}) == []


def test_compact_relationship_summary_filters_incomplete_sides_and_conditions():
    config = {
        "relationships": [
            {
                "left_table": "a",
                "right_table": "b",
                "join_type": "LEFT JOIN",
                "join_conditions": [
                    {"left_column": "a.id", "right_column": "b.id"},   # 完整
                    {"left_column": "", "right_column": "b.x"},        # 左列缺失
                    {"left_column": "a.y"},                            # 右列缺失
                ],
            },
            {"left_table": "a", "right_table": ""},                    # 整条跳过
            {"left_table": "a", "right_table": "c"},                   # 无 join_conditions
        ]
    }
    result = _compact_relationship_summary(config)
    assert [item["right_table"] for item in result] == ["b", "c"]
    assert result[0]["conditions"] == ["a.id=b.id"]
    assert result[0]["join_type"] == "LEFT JOIN"
    assert result[1]["conditions"] == []


# ---------------------------------------------------------------------------
# 分片预算
# ---------------------------------------------------------------------------


def test_chunk_summaries_returns_empty_for_empty_input():
    assert _chunk_semantic_config_summaries([], "查询", 10_000) == []


def test_chunk_summaries_warns_but_keeps_oversized_single_config():
    """单个指标集就超预算时不拆分，只告警并单独成片。"""
    summaries = [{"id": "cfg-1", "name": "大宽表"}, {"id": "cfg-2", "name": "订单"}]
    chunks = _chunk_semantic_config_summaries(summaries, "查询", token_budget=1)
    assert len(chunks) == 2
    assert [chunk[0]["id"] for chunk in chunks] == ["cfg-1", "cfg-2"]


def test_chunk_summaries_packs_multiple_configs_within_budget():
    summaries = [{"id": f"cfg-{index}", "name": "小主题"} for index in range(4)]
    chunks = _chunk_semantic_config_summaries(summaries, "查询", token_budget=10_000)
    assert len(chunks) == 1 and len(chunks[0]) == 4


# ---------------------------------------------------------------------------
# 结果标准化
# ---------------------------------------------------------------------------


def test_normalize_ids_caps_at_selector_limit():
    names = {f"cfg-{index}": f"主题{index}" for index in range(8)}
    value = [f"cfg-{index}" for index in range(8)] + ["cfg-0", "unknown", "", None]
    result = _normalize_semantic_config_ids(value, names)
    assert len(result) == 5
    assert result == [f"cfg-{index}" for index in range(5)]


def test_parse_selection_response_falls_back_for_non_dict_payload():
    configs = [{"id": "cfg-1", "name": "订单"}]
    assert _parse_selection_response("[1, 2]", configs) == {"semantic_config_ids": []}
    assert _parse_selection_response("完全不是 JSON", configs) == {"semantic_config_ids": []}
    assert _parse_selection_response(
        json.dumps({"semantic_config_ids": ["cfg-1", "cfg-1", "cfg-x"]}), configs
    ) == {"semantic_config_ids": ["cfg-1"]}


# ---------------------------------------------------------------------------
# 并发分片选择的异常收敛
# ---------------------------------------------------------------------------


class _FlakyLLM:
    """按顺序返回预设响应，其中 `None` 表示本次调用抛错。"""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = 0

    async def ainvoke(self, _messages):
        index = self.calls
        self.calls += 1
        payload = self._responses[min(index, len(self._responses) - 1)]
        if payload is None:
            raise ConnectionResetError("selector backend down")
        return payload


def _chunk(config_id: str) -> list[dict]:
    return [{"id": config_id, "name": f"主题-{config_id}", "metrics": [], "dimensions": []}]


@pytest.mark.asyncio
async def test_select_chunks_keeps_successful_results_and_swallows_failures(monkeypatch):
    """分片选择并发执行：单个分片失败只告警，不影响其余分片。"""
    alarms = []
    monkeypatch.setattr(
        search_module,
        "emit_llm_connection_alarm_if_needed",
        lambda exc, ext1=None, ext2=None: alarms.append(ext1),
    )
    llm = _FlakyLLM(
        json.dumps({"semantic_config_ids": ["cfg-1"]}),
        None,                                               # 第二个分片整体失败
        json.dumps({"semantic_config_ids": ["cfg-3"]}),
    )

    selections = await _select_semantic_config_chunks(
        llm, "查询", [_chunk("cfg-1"), _chunk("cfg-2"), _chunk("cfg-3")]
    )

    assert selections == [
        {"semantic_config_ids": ["cfg-1"]},
        {"semantic_config_ids": ["cfg-3"]},
    ]
    assert alarms == [{"purpose": "semantic_model_search"}]
    assert llm.calls == 3


# ---------------------------------------------------------------------------
# 入口：注入指标集
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_semantic_models_uses_injected_configs_without_service(monkeypatch):
    """显式传入 semantic_configs 时不再回源查询配置服务。"""
    monkeypatch.setattr(
        search_module,
        "get_chat_data_llm",
        lambda **_kwargs: _FlakyLLM(json.dumps({"semantic_config_ids": ["cfg-1"]})),
    )

    async def _boom(*_args, **_kwargs):
        raise AssertionError("不应回源查询配置服务")

    monkeypatch.setattr(
        search_module, "get_semantic_config_service",
        lambda: SimpleNamespace(get_runtime_configs_async=_boom),
    )

    result = await search_semantic_models(
        "ds-1",
        "本月销售额是多少",
        user_id="u-1",
        datasource_group_context="财付通",
        semantic_configs=[
            {"id": "cfg-1", "name": "订单主题", "enabled": True, "metrics": [], "dimensions": []},
            {"id": "cfg-2", "name": "已下线主题", "enabled": False, "metrics": [], "dimensions": []},
        ],
    )
    assert result == {"semantic_config_ids": ["cfg-1"]}
