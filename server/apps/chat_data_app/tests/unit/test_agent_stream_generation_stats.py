"""agent_stream 生成统计（generation_stats / token_usage 事件 / 实时速率采样）单测。

自 test_agent_stream.py 拆出（行数红线），覆盖口径见各用例 docstring。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")

from types import SimpleNamespace

from langchain_core.messages import ToolMessageChunk

from apps.chat_data_app import agent_stream as agent_stream_module


class _FakeStampedMessage:
    """模拟带模型调用起止盖章的 AIMessage（context_usage 中间件盖章产物）。"""

    def __init__(self, started_at_ms: int | None, ended_at_ms: int | None, *, context_summary: bool = False):
        kwargs: dict = {}
        if started_at_ms is not None:
            kwargs["started_at_ms"] = started_at_ms
        if ended_at_ms is not None:
            kwargs["ended_at_ms"] = ended_at_ms
        if context_summary:
            kwargs["context_summary"] = True
        self.additional_kwargs = kwargs


def test_generation_stats_requires_positive_duration_and_tokens(monkeypatch):
    """收口速率（generation_stats）口径：解码窗口 = 首 token→流结束 − 工具时间。"""
    import time

    now_ms = int(time.time() * 1000)
    # 首 token 锚点缺失 → 不产 stats
    assert (
        agent_stream_module._generation_stats_from_usage(
            first_token_ms=None,
            first_token_wall_ms=now_ms,
            stream_ended_wall_ms=now_ms,
            usage={},
        )
        is None
    )
    # 无输出 token → 不产 stats
    assert (
        agent_stream_module._generation_stats_from_usage(
            first_token_ms=10,
            first_token_wall_ms=now_ms,
            stream_ended_wall_ms=now_ms,
            usage={"output_tokens": 0},
        )
        is None
    )
    # 解码窗口为 0 → 无法得到正时长
    assert (
        agent_stream_module._generation_stats_from_usage(
            first_token_ms=10,
            first_token_wall_ms=now_ms,
            stream_ended_wall_ms=now_ms,
            usage={"output_tokens": 10},
        )
        is None
    )
    # 正常路径：解码窗口 1000ms（首 token → 流结束），其中 250ms 为工具耗时 → 生成时长 750ms
    # 工具行挂在 breakdown「会话消息」节点的 children 下（嵌套结构），需递归找到
    # 模型侧首 token = 首 token 墙钟 − 覆盖首 token 的那次调用起点；输入总 token 取 usage 累计
    stats = agent_stream_module._generation_stats_from_usage(
        first_token_ms=10,
        first_token_wall_ms=now_ms - 1000,
        stream_ended_wall_ms=now_ms,
        usage={
            "output_tokens": 30,
            "cumulative_input_tokens": 12205,
            "cumulative_cached_input_tokens": 9650,
            "cumulative_cache_hit_rate": 79.1,
            "llm_calls": [{"model": "qwen3"}, {"model": "deepseek-v4-flash", "output_tokens": 30}],
            "breakdown": [
                {"key": "system_prompt", "tokens": 100},
                {
                    "key": "messages",
                    "children": [
                        {"role": "ai", "started_at_ms": now_ms - 900, "ended_at_ms": now_ms - 650},
                        {"role": "tool", "started_at_ms": now_ms - 900, "ended_at_ms": now_ms - 650},
                    ],
                },
                {"role": "context_summary", "started_at_ms": now_ms - 2000, "ended_at_ms": now_ms - 1500},
            ],
        },
        messages=[
            _FakeStampedMessage(now_ms - 1250, now_ms - 350),
            _FakeStampedMessage(now_ms - 300, now_ms - 100),
            _FakeStampedMessage(now_ms - 2000, now_ms - 1500, context_summary=True),
        ],
    )
    assert stats is not None
    assert stats["duration_ms"] == 750
    assert stats["output_tokens"] == 30
    assert stats["tokens_per_second"] == 40
    assert stats["input_tokens"] == 12205
    assert stats["cached_input_tokens"] == 9650
    assert stats["cache_hit_rate"] == 79.1
    assert stats["model"] == "deepseek-v4-flash"  # 最后一次调用的模型名
    # 模型侧首 token：首 token 墙钟落在第一次调用窗口 [now-1250, now-350] 内 → 250ms；
    # 不再取「最早调用起点」做全局减法（那会把前置装配和调用间隙混进 TTFT）
    assert stats["model_first_token_ms"] == 250
    assert "total_duration_ms" not in stats  # 任务总耗时由 finish 层接力累计，不在此函数职责内
    # usage 缺输入/模型记录 → 对应字段省略，不影响主 stats
    stats_no_model = agent_stream_module._generation_stats_from_usage(
        first_token_ms=10,
        first_token_wall_ms=now_ms - 1000,
        stream_ended_wall_ms=now_ms,
        usage={"output_tokens": 30},
        messages=[_FakeStampedMessage(None, None)],
    )
    assert stats_no_model is not None
    assert "input_tokens" not in stats_no_model
    assert "cached_input_tokens" not in stats_no_model  # 模型未上报缓存信息 → 省略不硬凑
    assert "cache_hit_rate" not in stats_no_model
    assert "model" not in stats_no_model
    assert "model_first_token_ms" not in stats_no_model
    # 流结束锚点缺失（异常中断路径）→ 回退当前时刻，仍可产 stats
    monkeypatch.setattr(agent_stream_module.time, "time", lambda: now_ms / 1000)
    stats_fallback = agent_stream_module._generation_stats_from_usage(
        first_token_ms=10,
        first_token_wall_ms=now_ms - 1000,
        stream_ended_wall_ms=None,
        usage={"output_tokens": 30},
    )
    assert stats_fallback is not None
    assert stats_fallback["duration_ms"] == 1000
    assert stats_fallback["tokens_per_second"] == 30
    # 显式 model_name 优先（sanitize 前提取路径）：usage 已被剥离 llm_calls 仍可携带模型名
    stats_explicit = agent_stream_module._generation_stats_from_usage(
        first_token_ms=10,
        first_token_wall_ms=now_ms - 1000,
        stream_ended_wall_ms=now_ms,
        usage={"output_tokens": 30, "cumulative_input_tokens": 500},
        model_name="deepseek-v4-flash",
    )
    assert stats_explicit is not None
    assert stats_explicit["model"] == "deepseek-v4-flash"
    assert stats_explicit["input_tokens"] == 500


def test_handle_token_usage_payload_extracts_model_before_sanitize():
    """模型名契约：发射端在 sanitize（剥 llm_calls）前提取，放 payload["model"]。

    adapter 读 payload["model"]（非空才覆盖——subagent 等无 model 事件不清空主记录）；
    下发的 token_usage 仍被 sanitize 剥离 llm_calls 明细。
    """
    adapter = agent_stream_module._AgentSseStreamAdapter(
        agent=None,
        initial_state=None,
        config={},
        request=None,
    )
    # 主 agent 事件：payload["model"] 已由发射端提取 → 记录模型名
    sanitized_usage = {"output_tokens": 30}
    events = adapter._handle_token_usage_payload(
        {"type": "token_usage", "usage": sanitized_usage, "model": "deepseek-v4-flash"}
    )
    assert adapter.latest_model_name == "deepseek-v4-flash"
    assert events and "llm_calls" not in events[0]
    # subagent 等无 model 的事件：不清空已记录的名字
    adapter._handle_token_usage_payload({"type": "token_usage", "usage": {"output_tokens": 5}})
    assert adapter.latest_model_name == "deepseek-v4-flash"
    # 换模型（多轮调用最后一次为准）→ 覆盖
    adapter._handle_token_usage_payload({"type": "token_usage", "usage": {"output_tokens": 8}, "model": "qwen3"})
    assert adapter.latest_model_name == "qwen3"


def test_sample_chunk_generation_speed_excludes_tool_args_and_results():
    """实时速率只统计用户可见文本（thinking/正文）：工具参数 JSON 与工具结果不进滑窗。

    工具参数虽是模型产出，但界面只渲染工具动画不展示文字，且 JSON 片段批量到达
    会显著抬高滑窗速率（与 TTFT 的「用户可见」口径对齐）；工具结果不是模型输出。
    """
    adapter = agent_stream_module._AgentSseStreamAdapter(
        agent=None,
        initial_state=None,
        config={},
        request=None,
    )
    flags_type = agent_stream_module._MessageChunkFlags
    # 工具调用参数 chunk：不采样
    tool_args_chunk = SimpleNamespace(
        content="",
        tool_call_chunks=[
            {"id": "c1", "name": "sql_tool", "args": '{"sql":"SELECT * FROM orders WHERE amount > 100"}'}
        ],
    )
    adapter._sample_chunk_generation_speed(tool_args_chunk, flags_type.of(tool_args_chunk))
    assert not adapter._speed_samples
    # 工具结果消息：不采样
    tool_result_chunk = ToolMessageChunk(content="查询结果 647 行", tool_call_id="c1")
    adapter._sample_chunk_generation_speed(tool_result_chunk, flags_type.of(tool_result_chunk))
    assert not adapter._speed_samples
    # 正文：照常采样（CJK 1 字/token）
    text_chunk = SimpleNamespace(content="已为你生成下单用户数查询")
    adapter._sample_chunk_generation_speed(text_chunk, flags_type.of(text_chunk))
    assert adapter._speed_samples and adapter._speed_samples[0][1] > 0


def test_emit_context_usage_payload_carries_model_name():
    """发射端契约：sanitize 前提取模型名塞进 payload["model"]，usage 本体仍被剥离 llm_calls。"""
    emitted: list[dict] = []

    class _Writer:
        def __call__(self, payload):
            emitted.append(payload)

    from apps.chat_data_app.agent.middleware import context_usage as context_usage_module

    usage = {
        "output_tokens": 30,
        "llm_calls": [{"model": "qwen3"}, {"model": "deepseek-v4-flash"}],
    }
    # middleware 是 from-import 绑定，须打桩其模块命名空间
    original = context_usage_module.get_stream_writer
    context_usage_module.get_stream_writer = lambda: _Writer()
    try:
        context_usage_module._emit_context_usage(usage)
    finally:
        context_usage_module.get_stream_writer = original
    assert emitted and emitted[0]["type"] == "token_usage"
    assert emitted[0]["model"] == "deepseek-v4-flash"  # sanitize 前提取
    assert "llm_calls" not in emitted[0]["usage"]  # usage 本体仍被剥离明细
