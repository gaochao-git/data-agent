"""会话记忆注入链路测试：system prompt 注入、子图只读段、压缩丢弃回调与 hook 构造。

覆盖三段接线：
- prompts/chat_data.py 的 ``build_unified_system_prompt(memory_block=...)``；
- middleware/subagents.py 的 ``SubagentSpec.memory_block`` 只读注入段；
- middleware/context_compression.py 的 ``on_dropped_messages`` 与
  data_agent.py 的 ``_build_memory_compression_hook``。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from apps.chat_data_app.agent import data_agent
from apps.chat_data_app.agent.middleware import context_compression, subagents as subagents_module
from apps.chat_data_app.agent.middleware.subagents import SubagentSpec
from apps.chat_data_app.agent.prompts import chat_data
from apps.chat_data_app.agent.runtime_options import AgentRuntimeOptions


# ---------------------------------------------------------------------------
# 主图 system prompt 注入
# ---------------------------------------------------------------------------


def test_system_prompt_appends_memory_section_when_provided():
    prompt = chat_data.build_unified_system_prompt(
        AgentRuntimeOptions(), memory_block="- [口径] 销售额含税"
    )
    assert chat_data.MEMORY_BLOCK in prompt
    assert "## 会话记忆\n- [口径] 销售额含税" in prompt


def test_system_prompt_omits_memory_section_when_empty():
    prompt = chat_data.build_unified_system_prompt(AgentRuntimeOptions(), memory_block="   ")
    assert chat_data.MEMORY_BLOCK in prompt
    assert "## 会话记忆\n" not in prompt


def test_memory_section_sits_between_convention_block_and_data_context():
    prompt = chat_data.build_unified_system_prompt(
        AgentRuntimeOptions(), memory_block="- [口径] A"
    )
    assert prompt.index("## 会话记忆约定") < prompt.index("## 会话记忆\n") < prompt.index("# 数据上下文")


# ---------------------------------------------------------------------------
# 子图只读记忆段
# ---------------------------------------------------------------------------


def _spec(**overrides):
    kwargs = {
        "name": "data-explore",
        "description": "只读事实收集",
        "system_prompt": "你是子智能体",
        "tools": [],
        "model": object(),
    }
    kwargs.update(overrides)
    return SubagentSpec(**kwargs)


def test_subagent_prompt_injects_readonly_memory_section():
    spec = _spec(memory_block="- [口径] A")
    prompt = subagents_module._subagent_system_prompt(spec)
    assert "## 会话记忆（只读）" in prompt
    assert "- [口径] A" in prompt
    assert "只引用以上记忆" in prompt
    assert "/mnt/user-data/memory/memory.md" in prompt


def test_subagent_prompt_keeps_original_when_memory_empty():
    spec = _spec()
    assert subagents_module._subagent_system_prompt(spec) == spec.system_prompt


def test_subagent_spec_memory_block_defaults_empty():
    assert _spec().memory_block == ""


def test_build_subagent_middleware_passes_memory_block_to_specs():
    options = AgentRuntimeOptions(explore_subagent_enabled=True, plan_subagent_enabled=True)
    middleware = data_agent.build_subagent_middleware(
        options, [], model=object(), memory_block="- [口径] A"
    )
    assert middleware is not None
    assert {spec.memory_block for spec in middleware.specs} == {"- [口径] A"}

    empty = data_agent.build_subagent_middleware(options, [], model=object())
    assert all(spec.memory_block == "" for spec in empty.specs)


# ---------------------------------------------------------------------------
# 压缩丢弃回调链
# ---------------------------------------------------------------------------


def test_memory_compression_hook_none_without_conversation():
    common = dict(workspace_root="/w", user_id=None, username=None, model_id=None)
    assert data_agent._build_memory_compression_hook(None, **common) is None
    assert data_agent._build_memory_compression_hook("  ", **common) is None


def test_memory_compression_hook_delegates_to_schedule_now(monkeypatch, tmp_path):
    received: list[dict] = []
    monkeypatch.setattr(data_agent, "schedule_memory_extraction_now", lambda **kw: received.append(kw))

    hook = data_agent._build_memory_compression_hook(
        "c1", workspace_root=str(tmp_path), user_id="u", username="n", model_id="m"
    )
    dropped = [HumanMessage("旧消息")]
    hook(dropped)
    assert received[0]["conversation_id"] == "c1"
    assert received[0]["workspace_root"] == str(tmp_path)
    assert received[0]["messages"] == dropped


def test_notify_dropped_messages_hook_passes_copy_and_swallow_errors():
    received: list[list] = []
    dropped = [HumanMessage("m")]
    context_compression._notify_dropped_messages_hook(received.append, dropped)
    assert received[0] == dropped
    assert received[0] is not dropped  # 传入拷贝隔离

    def boom(messages):
        raise RuntimeError("listener broken")

    # 监听者异常不影响压缩主流程
    context_compression._notify_dropped_messages_hook(boom, dropped)


def test_notify_dropped_messages_hook_skips_empty_and_none():
    called: list[list] = []
    context_compression._notify_dropped_messages_hook(called.append, [])
    context_compression._notify_dropped_messages_hook(None, [HumanMessage("m")])
    assert called == []


def _len_tokens(messages) -> int:
    """内容长度即 token 数：让压缩触发与目标算术完全可控。"""
    return sum(len(str(getattr(message, "content", "") or "")) for message in messages or [])


@pytest.fixture
def deterministic_tokens(monkeypatch):
    monkeypatch.setattr(context_compression, "count_messages_tokens", _len_tokens)
    monkeypatch.setattr(context_compression, "_summary_tokens", lambda text: len(text or ""))


async def _summarizer(messages) -> str:
    return "摘要"


@pytest.mark.asyncio
async def test_compress_state_notifies_dropped_messages(deterministic_tokens):
    dropped_batches: list[list] = []
    old_question = "q1" + "x" * 60
    old_answer = "a1" + "y" * 60
    messages = [
        HumanMessage(content=old_question),
        AIMessage(content=old_answer),
        HumanMessage(content="最新问题"),
    ]

    compressed, summary, report = await context_compression.compress_messages_for_state(
        messages,
        static_input_tokens=0,
        compression_trigger_tokens=1,
        hard_input_limit_tokens=100_000,
        # 目标必须小于现有 tokens（128），否则走「工具结果外置已达标，跳过摘要」分支，
        # 不产生摘要也不交付丢弃消息
        compression_target_tokens=10,
        config={"enabled": True},
        summarizer=_summarizer,
        on_dropped_messages=dropped_batches.append,
    )
    assert report.get("triggered")
    assert compressed and summary == "摘要"
    assert dropped_batches, "压缩触发时必须交付丢弃消息"
    flattened = [message for batch in dropped_batches for message in batch]
    assert any(message.content == old_question for message in flattened)
    assert any(message.content == old_answer for message in flattened)
    # 当前回合的最新 Human 消息不会被丢弃
    assert all(message.content != "最新问题" for message in flattened)


@pytest.mark.asyncio
async def test_build_update_passes_through_dropped_callback(deterministic_tokens):
    dropped_batches: list[list] = []
    messages = [
        HumanMessage(content="q1" + "x" * 60),
        AIMessage(content="a1" + "y" * 60),
        HumanMessage(content="最新问题"),
    ]

    update, summary, report = await context_compression.build_context_compression_update(
        messages,
        static_input_tokens=0,
        compression_trigger_tokens=1,
        hard_input_limit_tokens=100_000,
        # 目标必须小于现有 tokens（128），否则走「跳过摘要」分支，不产生摘要也不回调
        compression_target_tokens=10,
        config={"enabled": True},
        summarizer=_summarizer,
        on_dropped_messages=dropped_batches.append,
    )
    assert update is not None and summary == "摘要"
    assert dropped_batches


@pytest.mark.asyncio
async def test_compress_state_skips_callback_when_not_triggered(deterministic_tokens):
    """未触发压缩（输入低于阈值）时不交付任何丢弃消息。"""
    called: list[list] = []
    messages = [HumanMessage(content="短问题"), AIMessage(content="短回答"), HumanMessage(content="最新问题")]

    compressed, summary, report = await context_compression.compress_messages_for_state(
        messages,
        static_input_tokens=0,
        compression_trigger_tokens=10_000,
        hard_input_limit_tokens=100_000,
        compression_target_tokens=1000,
        config={"enabled": True},
        summarizer=_summarizer,
        on_dropped_messages=called.append,
    )
    assert not report.get("triggered")
    assert called == []


def test_agent_stream_imports_memory_turn_hook():
    """agent_stream 的 after_turn 收尾必须是记忆钩子本体（防止接线被改丢）。"""
    import apps.chat_data_app.agent_stream as agent_stream
    from apps.chat_data_app.services.memory import conversation_memory_service

    assert agent_stream.after_agent_turn_with_memory is (
        conversation_memory_service.after_agent_turn_with_memory
    )
