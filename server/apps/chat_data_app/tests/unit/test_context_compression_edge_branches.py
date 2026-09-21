"""context_compression.py 分支缺口补测（第二轮）。

针对 `test_context_compression_branch_coverage.py` 未覆盖的分支，分三类处理：

1. **纯函数**：直接调用断言，零依赖。
2. **多轮压缩迭代体**：把 `count_messages_tokens` / `_summary_tokens` 换成
   确定性计数器（内容长度即 token 数），让压缩循环的每一次判定都可精确编排，
   避免依赖真实 token 估算的浮点式配比。
3. **不可达分支**：在下方的 `KNOWN_UNREACHABLE` 中写明原因，不用猜测式
   配比硬凑覆盖率。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from apps.chat_data_app.agent.middleware import context_compression
from apps.chat_data_app.agent.middleware.context_compression import (
    ContextInputLimitExceededError,
    _build_edge_excerpt,
    _build_referenced_tool_message,
    _compact_tool_result_for_summary,
    _current_turn_messages,
    _drop_current_turn_groups_to_limit,
    _drop_oldest_summary_round,
    _enforce_hard_input_limit,
    _fit_text_to_token_limit,
    _last_human_index,
    _prepare_summary_input,
    _reference_tool_results_to_target,
    _select_current_turn_tail_start,
    _truncate_current_human_to_limit,
    compress_messages_for_state,
)

# ---------------------------------------------------------------------------
# 已知不可达（防御性分支），记录原因以免后人重复尝试
# ---------------------------------------------------------------------------
# - 290->291：middleware 里 report["estimated_tokens_after"] 恒 <= hard_input_limit_tokens。
#   因为 _enforce_hard_input_limit 返回前必然把输入压到 guard_tokens(=95% 硬上限) 以下，
#   否则它自己会先抛 ContextInputLimitExceededError。
# - 898->899：while prepared_messages 保证进入循环时非空，而
#   _drop_oldest_summary_round 对非空输入恒返回 removed_count = len(messages) > 0。
# - 924->927：要走到「只剩省略标记」的收尾，必须在循环内先出现
#   count([marker] + 剩余) > limit 才把 prepared 置空退出循环；退出后重新计算的是
#   同一个 marker，故必然仍 > limit，只能走 926 的不可用出口。
KNOWN_UNREACHABLE = {
    "290->291": "硬性输入超限已在 _enforce_hard_input_limit 内部收敛，middleware 层不可达",
    "898->899": "_drop_oldest_summary_round 对非空输入恒返回 removed_count>0",
    "924->927": "省略标记单独计数不可能既超过预算又不超过预算",
}


def _tool_call(call_id: str) -> dict:
    return {"name": "semantic_query_tool", "args": {}, "id": call_id}


def _recovery_artifact(path: str = "/mnt/user-data/outputs/result.json") -> dict:
    return {
        "chatdata_tool_result_recovery": {
            "version": 1,
            "source_tool": "semantic_query_tool",
            "result_path": path,
        }
    }


def _len_tokens(messages) -> int:
    """内容长度即 token 数：让压缩循环的算术完全可控。"""
    return sum(len(str(getattr(message, "content", "") or "")) for message in messages or [])


@pytest.fixture
def deterministic_tokens(monkeypatch):
    """把 token 估算替换为确定性实现，供多轮压缩场景编排使用。"""
    monkeypatch.setattr(context_compression, "count_messages_tokens", _len_tokens)
    monkeypatch.setattr(context_compression, "_summary_tokens", lambda text: len(text or ""))


def _base_kwargs(**overrides):
    kwargs = dict(
        static_input_tokens=0,
        compression_trigger_tokens=1,
        hard_input_limit_tokens=100_000,
        compression_target_tokens=1,
        config={"enabled": True},
    )
    kwargs.update(overrides)
    return kwargs


# ---------------------------------------------------------------------------
# 纯函数分支
# ---------------------------------------------------------------------------


def test_build_edge_excerpt_full_text_when_budget_exceeds_length():
    assert _build_edge_excerpt("abcdef", 10) == "abcdef"
    assert _build_edge_excerpt("abcdef", 0) == "[正文已省略]"


def test_build_referenced_tool_message_returns_same_message_without_recovery():
    message = ToolMessage(content="原始结果", tool_call_id="c-1")
    assert _build_referenced_tool_message(message) is message


def test_compact_tool_result_skips_blank_and_already_fitting_content():
    blank = ToolMessage(content="   ", tool_call_id="c-0")
    assert _compact_tool_result_for_summary(blank) is blank

    short = ToolMessage(content="ok", tool_call_id="c-1")
    assert _compact_tool_result_for_summary(short) is short


def test_fit_text_to_token_limit_falls_back_when_marker_exceeds_budget():
    assert _fit_text_to_token_limit("x" * 10_000, prefix="", token_limit=1) == "[工具结果已压缩]"


def test_drop_oldest_summary_round_on_empty_input():
    assert _drop_oldest_summary_round([]) == ([], 0)


def test_last_human_index_without_any_human_message():
    assert _last_human_index([AIMessage(content="a")]) == 1
    assert _current_turn_messages([AIMessage(content="a")]) == []


def test_select_current_turn_tail_start_short_circuits():
    # 整段当前回合都在预算内：整体保留
    assert _select_current_turn_tail_start(
        [HumanMessage(content="a"), AIMessage(content="b")],
        static_input_tokens=0,
        compression_target_tokens=10_000,
    ) == 1

    # 当前回合没有完整分组（工具调用悬空）：只能整体保留
    dangling = [HumanMessage(content="a"), AIMessage(content="", tool_calls=[_tool_call("c-1")])]
    assert _select_current_turn_tail_start(
        dangling, static_input_tokens=0, compression_target_tokens=0
    ) == 1


def test_select_current_turn_tail_start_walks_boundaries_without_fit():
    """首个候选边界就超预算 → 回退到「保留最新完整分组」的下标。"""
    turn = [
        HumanMessage(content="H"),
        AIMessage(content="", tool_calls=[_tool_call("c-1")]),
        ToolMessage(content="T" * 10, tool_call_id="c-1"),
        AIMessage(content="", tool_calls=[_tool_call("c-2")]),
        ToolMessage(content="T" * 10, tool_call_id="c-2"),
    ]
    assert _select_current_turn_tail_start(
        turn, static_input_tokens=0, compression_target_tokens=1
    ) == 3


def test_drop_current_turn_groups_without_human_returns_empty():
    assert _drop_current_turn_groups_to_limit(
        [AIMessage(content="a")], guard_tokens=1, summary_text=None, static_input_tokens=0
    ) == []


def test_drop_current_turn_groups_keeps_only_human_when_nothing_fits():
    messages = [HumanMessage(content="x" * 100), AIMessage(content="y" * 100)]
    result = _drop_current_turn_groups_to_limit(
        messages, guard_tokens=1, summary_text=None, static_input_tokens=10_000
    )
    assert result == [messages[0]]


def test_truncate_current_human_noop_when_already_within_budget():
    human = HumanMessage(content="hi")
    truncated, changed = _truncate_current_human_to_limit(
        [human], guard_tokens=10_000, summary_text=None, static_input_tokens=0
    )
    assert truncated == [human] and changed is False


def test_reference_tool_results_stops_immediately_when_within_target():
    tool_message = ToolMessage(
        content="x" * 100, tool_call_id="c-1", artifact=_recovery_artifact()
    )
    working, referenced = _reference_tool_results_to_target(
        [HumanMessage(content="h"), tool_message],
        static_input_tokens=0,
        summary_text=None,
        target_tokens=100_000,
    )
    assert referenced == 0
    assert working[1] is tool_message


def test_build_context_compression_event_without_any_human_message():
    event = context_compression._build_context_compression_event(
        [AIMessage(content="a"), AIMessage(content="b")], {"triggered": True}
    )
    assert event["turn_id"] == ""
    assert event["estimated_tokens_before"] == 0


def test_build_context_compression_event_skips_messages_without_id():
    """从后往前找首个「带 id 的用户消息」：跳过 AI 消息与无 id 的用户消息。"""
    event = context_compression._build_context_compression_event(
        [
            HumanMessage(content="a", id="h-1"),
            HumanMessage(content="b"),          # 无 id：必须继续往前找
            AIMessage(content="c"),             # 非用户消息：跳过
        ],
        {"triggered": True},
    )
    assert event["turn_id"] == "h-1"


def test_prepare_summary_input_skips_uncompactable_tool_results():
    """空内容的工具结果压缩后仍是同一对象 → continue；单条压缩后仍超预算 → 继续循环。"""
    messages = [
        ToolMessage(content="", tool_call_id="c-0"),
        ToolMessage(content="y" * 5_000, tool_call_id="c-1"),
    ]
    prepared, report = _prepare_summary_input(
        messages, previous_summary=None, summary_input_limit_tokens=10
    )
    assert report["summary_tool_results_compacted"] == 1
    assert report["summary_input_unusable"] is True
    assert prepared == []


@pytest.mark.asyncio
async def test_summarize_for_compression_short_circuits_on_empty_input():
    summary, report = await context_compression._summarize_for_compression(
        [],
        previous_summary=None,
        summarizer=None,
        summary_input_limit_tokens=None,
        summary_model_id=None,
    )
    assert summary == ""
    assert report["summary_input_unusable"] is False


@pytest.mark.asyncio
async def test_enforce_hard_input_limit_drops_summary_before_touching_messages():
    report = context_compression._build_report(
        context_compression._config_from_mapping(None),
        before_tokens=4_500,
        after_tokens=4_500,
        threshold_tokens=1,
        hard_input_limit_tokens=1_000,
        compression_target_tokens=1,
        static_input_tokens=0,
    )
    messages = [HumanMessage(content="x" * 2_000), AIMessage(content="y" * 2_000)]
    working, summary, report = _enforce_hard_input_limit(
        messages,
        "S" * 500,
        report,
        before_tokens=4_500,
        static_input_tokens=0,
        hard_input_limit_tokens=1_000,
    )
    assert summary is None
    assert report["hard_limit_summary_dropped"] is True
    assert working


@pytest.mark.asyncio
async def test_compress_messages_raises_when_static_input_exceeds_guard():
    with pytest.raises(ContextInputLimitExceededError, match="固定输入超过上下文安全水位"):
        await compress_messages_for_state(
            [HumanMessage(content="hi")],
            **_base_kwargs(
                static_input_tokens=50_000,
                hard_input_limit_tokens=1_000,
                config={"enabled": False},
            ),
        )


@pytest.mark.asyncio
async def test_compress_messages_reports_no_completed_groups():
    """没有任何完整消息分组时跳过压缩，但硬上限校验仍然执行。"""
    dangling = [AIMessage(content="x" * 10, tool_calls=[_tool_call("c-1")])]
    messages, summary, report = await compress_messages_for_state(dangling, **_base_kwargs())
    assert report["compression_skipped_reason"] == "no_completed_message_groups"
    assert report["estimated_tokens_after"] == report["estimated_tokens_before"]
    assert summary is None
    assert messages


# ---------------------------------------------------------------------------
# 多轮压缩迭代体（确定性 token 计数）
# ---------------------------------------------------------------------------


def _two_turn_messages() -> list:
    """当前回合内含两个完整工具分组的历史。内容长度 = token 数。"""
    return [
        HumanMessage(content="O" * 5, id="u-old"),
        AIMessage(content="P" * 5, id="a-old"),
        HumanMessage(content="H", id="u-cur"),
        AIMessage(content="", id="a-1", tool_calls=[_tool_call("c-1")]),
        ToolMessage(content="T" * 10, tool_call_id="c-1", id="t-1"),
        AIMessage(content="", id="a-2", tool_calls=[_tool_call("c-2")]),
        ToolMessage(content="T" * 10, tool_call_id="c-2", id="t-2"),
    ]


def _recording_summarizer(*summaries: str):
    calls: list[list] = []

    async def _summarize(messages):
        calls.append(messages)
        index = min(len(calls) - 1, len(summaries) - 1)
        return summaries[index]

    return _summarize, calls


@pytest.mark.asyncio
async def test_compression_stops_when_second_pass_has_nothing_left(deterministic_tokens):
    """第二轮分区为空 → break（当前回合只剩一条用户消息，不可再压缩）。"""
    messages = [
        HumanMessage(content="aaaa", id="u-old"),
        AIMessage(content="bb", id="a-old"),
        HumanMessage(content="cc", id="u-cur"),
        AIMessage(content="", id="a-dangling", tool_calls=[_tool_call("c-1")]),
    ]
    summarizer, calls = _recording_summarizer("S")

    compressed, summary, report = await compress_messages_for_state(
        messages, summarizer=summarizer, **_base_kwargs(compression_target_tokens=1)
    )

    assert len(calls) == 1                       # 第二轮直接 break，不再调用摘要
    assert report["compression_passes"] == 1
    assert summary == "S"
    assert [message.id for message in compressed] == ["u-cur", "a-dangling"]


@pytest.mark.asyncio
async def test_compression_breaks_when_second_summary_is_empty(deterministic_tokens):
    summarizer, calls = _recording_summarizer("S" * 20, "")
    compressed, summary, report = await compress_messages_for_state(
        _two_turn_messages(),
        summarizer=summarizer,
        **_base_kwargs(compression_target_tokens=21),
    )
    assert len(calls) == 2
    assert report["compression_passes"] == 1
    assert summary == "S" * 20
    assert [message.id for message in compressed] == ["u-cur", "a-1", "t-1", "a-2", "t-2"]


@pytest.mark.asyncio
async def test_compression_breaks_when_second_pass_does_not_shrink(deterministic_tokens):
    """第二轮摘要比省下的内容还长 → next_tokens >= after_tokens，停止迭代。"""
    summarizer, calls = _recording_summarizer("S" * 20, "S" * 40)
    compressed, summary, report = await compress_messages_for_state(
        _two_turn_messages(),
        summarizer=summarizer,
        **_base_kwargs(compression_target_tokens=21),
    )
    assert len(calls) == 2
    assert report["compression_passes"] == 1
    assert summary == "S" * 20                   # 未采纳更长的第二轮摘要
    assert [message.id for message in compressed] == ["u-cur", "a-1", "t-1", "a-2", "t-2"]


@pytest.mark.asyncio
async def test_compression_accepts_second_pass_when_it_shrinks(deterministic_tokens):
    """第二轮确实更小 → 采纳并累加 passes。"""
    summarizer, calls = _recording_summarizer("S" * 20, "SS")
    compressed, summary, report = await compress_messages_for_state(
        _two_turn_messages(),
        summarizer=summarizer,
        **_base_kwargs(compression_target_tokens=21),
    )
    assert len(calls) == 2
    assert report["compression_passes"] == 2
    assert summary == "SS"
    assert [message.id for message in compressed] == ["u-cur", "a-2", "t-2"]


# ---------------------------------------------------------------------------
# 中间件出口
# ---------------------------------------------------------------------------


def _middleware(monkeypatch, *, trigger, target, hard_limit, compressed_summary="摘要结果"):
    monkeypatch.setattr(
        context_compression.ContextRequestBudget,
        "configure",
        lambda self, system_prompt, tools, model_id=None: SimpleNamespace(
            hard_input_limit_tokens=hard_limit,
            compression_trigger_tokens=trigger,
            compression_target_tokens=target,
        ),
    )
    monkeypatch.setattr(
        context_compression, "get_context_compression_config",
        lambda: {"enabled": True, "trigger_ratio": 0.10},
    )
    monkeypatch.setattr(context_compression, "_get_summary_input_limit_tokens", lambda _m: 100_000)

    async def _summarize(*_args, **_kwargs):
        return compressed_summary

    monkeypatch.setattr(context_compression, "_summarize_messages_with_llm", _summarize)
    monkeypatch.setattr(context_compression, "get_stream_writer", lambda: (lambda *_a: None))
    return context_compression.create_context_compression_middleware(
        system_prompt="智能问数系统提示词", state_schema=dict
    )


@pytest.mark.asyncio
async def test_middleware_migrates_legacy_summary_without_compression_event(monkeypatch):
    """旧 checkpoint 的内嵌摘要需要迁移到 state，但本轮并未压缩 → 不发分界事件。"""
    middleware = _middleware(monkeypatch, trigger=100_000, target=100_000, hard_limit=100_000)
    messages = [
        SystemMessage(
            content="[历史对话摘要]\n\n旧摘要正文",
            additional_kwargs={"context_compression": True},
        ),
        HumanMessage(content="当前问题", id="u-current"),
    ]
    update = await middleware.abefore_model({"messages": messages}, None)

    assert update["context_compression"]["legacy_summary_migrated"] is True
    assert update["context_compression"]["triggered"] is False
    assert "context_compression_events" not in update
    # 旧的 SystemMessage 摘要已从消息流剥离，改为独立 state 字段
    assert update["context_summary"] == "旧摘要正文"
    assert not any(isinstance(message, SystemMessage) for message in update["messages"][1:])


@pytest.mark.asyncio
async def test_middleware_emits_compression_event_with_message_updates(monkeypatch):
    """压缩真正发生时：state 更新里必须带上 context_compression_events。"""
    middleware = _middleware(monkeypatch, trigger=1, target=1, hard_limit=100_000)
    messages = []
    for index in range(3):
        messages.extend([
            HumanMessage(content=f"历史问题{index}" + " 查询订单销售额 " * 40, id=f"u-{index}"),
            AIMessage(content=f"历史回答{index}" + " 销售额同比环比口径 " * 40, id=f"a-{index}"),
        ])
    messages.append(HumanMessage(content="当前问题", id="u-current"))

    update = await middleware.abefore_model(
        {"messages": messages, "context_summary": None}, None
    )

    assert update["context_compression"]["triggered"] is True
    assert len(update["context_compression_events"]) == 1
    event = update["context_compression_events"][0]
    assert event["turn_id"] == "u-current"
    assert event["id"].startswith("context-compression-")
    # 事件只带指标，不带摘要正文
    assert "摘要结果" not in str(event)


@pytest.mark.asyncio
async def test_middleware_emits_compression_started_event_before_completed(monkeypatch):
    """压缩真正触发时，摘要开始前先推送 phase=started 占位事件，完成后推送 completed。"""
    captured: list[dict] = []
    middleware = _middleware(monkeypatch, trigger=1, target=1, hard_limit=100_000)
    monkeypatch.setattr(context_compression, "get_stream_writer", lambda: (lambda payload: captured.append(payload)))
    messages = []
    for index in range(3):
        messages.extend([
            HumanMessage(content=f"历史问题{index}" + " 查询订单销售额 " * 40, id=f"u-{index}"),
            AIMessage(content=f"历史回答{index}" + " 销售额同比环比口径 " * 40, id=f"a-{index}"),
        ])
    messages.append(HumanMessage(content="当前问题", id="u-current"))

    update = await middleware.abefore_model(
        {"messages": messages, "context_summary": None}, None
    )

    phases = [payload["event"]["phase"] for payload in captured if payload.get("type") == "context_compression"]
    assert phases == ["started", "completed"]
    started_payload = captured[0]["event"]
    assert started_payload["id"].startswith("context-compression-started-")
    assert started_payload["turn_id"] == "u-current"
    # started 是实时占位事件，不写入检查点
    assert all(event["phase"] == "completed" for event in update["context_compression_events"])


@pytest.mark.asyncio
async def test_middleware_skips_started_event_when_compression_not_triggered(monkeypatch):
    """未触发摘要压缩时不推送 started 占位，避免每次模型调用都出现占位分界。"""
    captured: list[dict] = []
    middleware = _middleware(monkeypatch, trigger=100_000, target=100_000, hard_limit=100_000)
    monkeypatch.setattr(context_compression, "get_stream_writer", lambda: (lambda payload: captured.append(payload)))
    messages = [HumanMessage(content="当前问题", id="u-current")]

    await middleware.abefore_model({"messages": messages, "context_summary": None}, None)

    assert not [payload for payload in captured if payload.get("type") == "context_compression"]
