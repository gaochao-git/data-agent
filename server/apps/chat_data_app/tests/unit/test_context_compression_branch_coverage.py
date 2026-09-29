"""context_compression.py 覆盖率补充测试。

聚焦中间件里尚未触达的分支：摘要 LLM 调用异常告警与 list 型返回、
无 Human 消息时的轮次裁剪短路、摘要输入预算耗尽后的省略标记与不可用出口、
当前回合无用户消息时的分区路径、压缩跳过原因，以及多轮压缩迭代收口。
全部用桩 summarizer，不发起真实模型调用。
"""

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from apps.chat_data_app.agent.middleware import context_compression

# ---------------------------------------------------------------------------
# 摘要 LLM 调用
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summarize_messages_alarms_and_reraises_on_llm_failure(monkeypatch):
    class FailingLLM:
        async def ainvoke(self, _messages):
            raise ConnectionResetError("connection reset by peer")

    monkeypatch.setattr(context_compression, "get_chat_data_llm", lambda **_kwargs: FailingLLM())
    alarms = []
    monkeypatch.setattr(
        context_compression,
        "emit_llm_connection_alarm_if_needed",
        lambda exc, ext1=None, ext2=None: alarms.append((type(exc).__name__, ext1)),
    )

    with pytest.raises(ConnectionResetError):
        await context_compression._summarize_messages_with_llm(
            [HumanMessage(content="历史")],
            model_id="summary-model",
        )
    assert len(alarms) == 1
    assert alarms[0][0] == "ConnectionResetError"
    assert alarms[0][1] == {"model_id": "summary-model", "purpose": "context_compression"}


@pytest.mark.asyncio
async def test_summarize_messages_joins_list_content_blocks(monkeypatch):
    class BlockLLM:
        async def ainvoke(self, _messages):
            return AIMessage(content=[{"type": "text", "text": "第一段"}, {"type": "text", "text": "第二段"}])

    monkeypatch.setattr(context_compression, "get_chat_data_llm", lambda **_kwargs: BlockLLM())
    monkeypatch.setattr(context_compression, "get_context_window", lambda _model_id: 256000)
    summary = await context_compression._summarize_messages_with_llm(
        [HumanMessage(content="历史")],
        model_id="m",
    )
    assert summary == "第一段\n第二段"


@pytest.mark.asyncio
async def test_summarize_messages_handles_plain_string_response(monkeypatch):
    class StringLLM:
        async def ainvoke(self, _messages):
            return "直接返回字符串"

    monkeypatch.setattr(context_compression, "get_chat_data_llm", lambda **_kwargs: StringLLM())
    assert (
        await context_compression._summarize_messages_with_llm(
            [HumanMessage(content="历史")],
        )
        == "直接返回字符串"
    )


# ---------------------------------------------------------------------------
# 摘要输入预算
# ---------------------------------------------------------------------------


def test_drop_oldest_summary_round_without_human_message_drops_everything():
    messages = [SystemMessage(content="系统"), AIMessage(content="回答"), ToolMessage(content="r", tool_call_id="c")]
    remaining, removed = context_compression._drop_oldest_summary_round(messages)
    assert remaining == [] and removed == len(messages)


def test_drop_oldest_summary_round_removes_only_oldest_round():
    messages = [
        HumanMessage(content="第一轮问题"),
        AIMessage(content="第一轮回答"),
        HumanMessage(content="第二轮问题"),
        AIMessage(content="第二轮回答"),
    ]
    remaining, removed = context_compression._drop_oldest_summary_round(messages)
    assert removed == 2
    assert [message.content for message in remaining] == ["第二轮问题", "第二轮回答"]


def test_build_summary_omission_marker_records_drop_counts():
    marker = context_compression._build_summary_omission_marker(5, 2)
    assert isinstance(marker, SystemMessage)
    assert "已省略轮次=2" in marker.content
    assert "已省略消息=5" in marker.content
    assert marker.additional_kwargs["context_compression_summary_omission"] is True


def test_prepare_summary_input_prunes_rounds_and_marks_unusable_when_exhausted():
    messages = []
    for index in range(6):
        messages.append(HumanMessage(content=f"第{index}轮问题" + " 销售额维度口径说明 " * 120))
        messages.append(AIMessage(content=f"第{index}轮回答" + " 同比环比指标统计结果 " * 120))

    # 预算给到刚好必须一路裁到只剩省略标记的程度
    only_marker_tokens = context_compression._count_summary_input_tokens(
        [context_compression._build_summary_omission_marker(1, 1)],
        previous_summary=None,
    )
    prepared, report = context_compression._prepare_summary_input(
        messages,
        previous_summary=None,
        summary_input_limit_tokens=only_marker_tokens,
    )
    assert report["summary_input_pruned_rounds"] >= 1
    assert report["summary_input_unusable"] is False
    assert prepared and prepared[0].additional_kwargs.get("context_compression_summary_omission") is True

    # 预算比省略标记本身还小：判定不可用并返回空输入
    prepared, report = context_compression._prepare_summary_input(
        messages,
        previous_summary=None,
        summary_input_limit_tokens=1,
    )
    assert prepared == []
    assert report["summary_input_unusable"] is True


# ---------------------------------------------------------------------------
# 消息分区
# ---------------------------------------------------------------------------


def test_partition_messages_without_current_human_uses_completed_round_start():
    messages = []
    for index in range(4):
        messages.append(HumanMessage(content=f"问题{index}" + " 销售额维度口径说明 " * 60, id=f"u-{index}"))
        messages.append(AIMessage(content=f"回答{index}" + " 同比环比统计结果明细 " * 60, id=f"a-{index}"))

    summarized, retained, count = context_compression._partition_messages_for_compression(
        messages,
        len(messages),
        current_human_index=len(messages) + 5,  # 越界：走"无当前用户消息"分支
        static_input_tokens=0,
        compression_target_tokens=1500,  # 实测：4 轮 3708 tokens 时保留最近 2 条
    )
    assert count == len(summarized)
    assert len(summarized) == 6 and len(retained) == 2
    # 分区必须无损且保序
    assert [*summarized, *retained] == messages

    # 目标极小时全部进摘要、保留集为空，仍满足无损不变式
    summarized_all, retained_all, count_all = context_compression._partition_messages_for_compression(
        messages,
        len(messages),
        current_human_index=len(messages) + 5,
        static_input_tokens=0,
        compression_target_tokens=1,
    )
    assert count_all == len(summarized_all) == len(messages)
    assert retained_all == []


def test_last_complete_boundary_counts_completed_groups():
    # 单独一条 Human 已构成完整分组；未完成的 tool call 不额外推进边界
    dangling = [
        HumanMessage(content="问题", id="u-1"),
        AIMessage(content="", id="a-call", tool_calls=[{"name": "run_sql", "args": {}, "id": "c-1"}]),
    ]
    assert context_compression._last_complete_boundary(dangling) == 1

    completed = [
        *dangling,
        ToolMessage(content="结果", tool_call_id="c-1", id="t-1"),
        AIMessage(content="结论", id="a-1"),
    ]
    assert context_compression._last_complete_boundary(completed) == 4
    assert list(context_compression.complete_message_boundaries(completed)) == [1, 3, 4]


def test_partition_messages_keeps_current_human_and_trailing_tool_call():
    messages = [
        HumanMessage(content="旧问题", id="u-old"),
        AIMessage(content="旧回答", id="a-old"),
        HumanMessage(content="当前问题", id="turn-current"),
        AIMessage(content="", id="a-call", tool_calls=[{"name": "run_sql", "args": {}, "id": "c-1"}]),
        ToolMessage(content="结果", tool_call_id="c-1", id="t-1"),
    ]
    summarized, retained, count = context_compression._partition_messages_for_compression(
        messages,
        completed_end=2,
        current_human_index=2,
        static_input_tokens=0,
        compression_target_tokens=1,
    )
    # 当前用户问题必须保留，不能被摘要掉
    assert any(message.id == "turn-current" for message in retained)
    assert count == len(summarized)


# ---------------------------------------------------------------------------
# 中间件入口：跳过原因与多轮压缩
# ---------------------------------------------------------------------------


class _Budget:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _middleware(monkeypatch, *, trigger_tokens, target_tokens, hard_limit, summarizer_result="摘要结果"):
    monkeypatch.setattr(
        context_compression.ContextRequestBudget,
        "configure",
        lambda self, system_prompt, tools, model_id=None: _Budget(
            context_window=hard_limit + 500,
            max_output_tokens=200,
            compression_trigger_ratio=0.70,
            hard_input_limit_tokens=hard_limit,
            compression_trigger_tokens=trigger_tokens,
            compression_target_tokens=target_tokens,
        ),
    )
    monkeypatch.setattr(
        context_compression,
        "get_context_compression_config",
        lambda: {"enabled": True, "trigger_ratio": 0.10},
    )
    monkeypatch.setattr(context_compression, "_get_summary_input_limit_tokens", lambda _m: 100_000)

    calls = {"count": 0}

    async def summarize(*_args, **_kwargs):
        calls["count"] += 1
        if isinstance(summarizer_result, list):
            return summarizer_result[min(calls["count"] - 1, len(summarizer_result) - 1)]
        return summarizer_result

    monkeypatch.setattr(context_compression, "_summarize_messages_with_llm", summarize)
    monkeypatch.setattr(context_compression, "get_stream_writer", lambda: (lambda *_a: None))
    middleware = context_compression.create_context_compression_middleware(
        system_prompt="智能问数系统提示词",
        state_schema=dict,
    )
    return middleware, calls


def _state(messages):
    return {"messages": messages, "workspace": {"workspace_root": "/tmp/x/user-data", "workspace_backend": "local"}}


@pytest.mark.asyncio
async def test_middleware_skips_when_there_is_nothing_to_summarize(monkeypatch):
    # 只有一问、没有已完成轮次 → 无可压缩消息
    middleware, calls = _middleware(monkeypatch, trigger_tokens=1, target_tokens=1, hard_limit=100_000)
    update = await middleware.abefore_model(
        _state([HumanMessage(content="只有一句话", id="turn-1")]),
        SimpleNamespace(),
    )
    assert calls["count"] == 0
    report = update["context_compression"]
    assert report["compression_skipped_reason"] == "no_compressible_messages"
    assert report["compressed_messages"] == 0


@pytest.mark.asyncio
async def test_middleware_reports_empty_summary_without_breaking(monkeypatch):
    messages = []
    for index in range(4):
        messages.extend(
            [
                HumanMessage(content=f"历史问题{index}" + " 查询订单销售额 " * 300, id=f"u-{index}"),
                AIMessage(content=f"历史回答{index}" + " 销售额同比环比口径 " * 300, id=f"a-{index}"),
            ]
        )
    messages.append(HumanMessage(content="当前问题", id="turn-current"))

    middleware, calls = _middleware(
        monkeypatch,
        trigger_tokens=1,
        target_tokens=1,
        hard_limit=100_000,
        summarizer_result="",
    )
    update = await middleware.abefore_model(_state(messages), SimpleNamespace())
    assert calls["count"] >= 1
    report = update["context_compression"]
    assert report["compression_skipped_reason"] == "empty_summary"
    # fail-closed：未超硬限安全水位时保留原样，不产生 state 写更新，等下轮重试
    assert report["summary_retry_next_turn"] is True
    assert "messages" not in update or update.get("messages") is None


@pytest.mark.asyncio
async def test_middleware_empty_summary_near_hard_limit_still_enforces_hard_limit(monkeypatch):
    # 摘要失败且逼近硬限：fail-closed 让位给可用性，仍走确定性收缩兜底
    messages = []
    for index in range(4):
        messages.extend(
            [
                HumanMessage(content=f"历史问题{index}" + " 查询订单销售额 " * 300, id=f"u-{index}"),
                AIMessage(content=f"历史回答{index}" + " 销售额同比环比口径 " * 300, id=f"a-{index}"),
            ]
        )
    messages.append(HumanMessage(content="当前问题", id="turn-current"))

    middleware, calls = _middleware(
        monkeypatch,
        trigger_tokens=1,
        target_tokens=1,
        hard_limit=1_000,
        summarizer_result="",
    )
    update = await middleware.abefore_model(_state(messages), SimpleNamespace())
    assert calls["count"] >= 1
    report = update["context_compression"]
    assert report["compression_skipped_reason"] == "empty_summary"
    assert "summary_retry_next_turn" not in report or report["summary_retry_next_turn"] is False
    assert report.get("estimated_tokens_after") is not None


def test_resolve_summary_output_max_tokens_prefers_explicit_config(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_CONTEXT_COMPRESSION_SUMMARY_OUTPUT_MAX_TOKENS", "1536")
    monkeypatch.setattr(context_compression, "get_context_window", lambda _m: 256000)
    assert context_compression._resolve_summary_output_max_tokens("any") == 1536


def test_resolve_summary_output_max_tokens_dynamic_by_window(monkeypatch):
    monkeypatch.delenv("CHAT_DATA_CONTEXT_COMPRESSION_SUMMARY_OUTPUT_MAX_TOKENS", raising=False)
    # 大窗口：5% = 12800 → 截到上限 12000
    monkeypatch.setattr(context_compression, "get_context_window", lambda _m: 256000)
    assert context_compression._resolve_summary_output_max_tokens("any") == 12000
    # 小窗口：5% 不足下限 → 抬到 2000
    monkeypatch.setattr(context_compression, "get_context_window", lambda _m: 8000)
    assert context_compression._resolve_summary_output_max_tokens("any") == 2000


# 说明：多轮压缩迭代体（compression_passes>1）与 "no_completed_message_groups"
# 分支在当前分区策略下不可达——首轮压缩后保留下来的恰是当前回合，按设计不能再被
# 摘要，因此第二轮 _partition 必然返回空并 break。这两块留作已知未覆盖，不用
# 猜测式 token 配比凑覆盖率。
