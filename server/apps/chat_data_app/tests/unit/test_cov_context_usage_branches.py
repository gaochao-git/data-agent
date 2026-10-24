"""context_usage 残余分支补测：压缩快照、摘要注入、缓存口径与角色映射。"""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from apps.chat_data_app.agent.middleware import context_usage as cu


def test_compact_token_usage_for_state_rejects_non_mapping():
    assert cu.compact_token_usage_for_state(None) is None
    assert cu.compact_token_usage_for_state("usage") is None


def test_merge_summary_returns_cleaned_messages_when_builder_returns_none(monkeypatch):
    monkeypatch.setattr(cu, "build_runtime_context_summary_message", lambda _summary: None)
    messages = [HumanMessage(content="问题"), AIMessage(content="回答")]

    system_prompt, merged = cu._merge_compression_summary_into_system_prompt(
        "系统提示词",
        messages,
        context_summary="上一轮摘要",
    )

    assert system_prompt == "系统提示词"
    assert merged == messages


def test_add_cache_hit_rate_skips_when_rate_not_reportable():
    payload = {}

    cu._add_cache_hit_rate(payload, "rate", 5, None)
    cu._add_cache_hit_rate(payload, "rate", 5, 10, has_reported_input=False)
    cu._add_cache_hit_rate(payload, "rate", 5, 0)

    assert payload == {}


def test_cached_tokens_from_openai_usage_prefers_input_token_details():
    assert cu._cached_tokens_from_openai_usage({"input_token_details": {"cache_read": 7}}) == 7


def test_optional_non_negative_int_rejects_bool():
    assert cu._optional_non_negative_int(True) is None
    assert cu._optional_non_negative_int(False) is None
    assert cu._optional_non_negative_int(3) == 3


def test_message_role_maps_tool_message_and_falls_back_to_message():
    assert cu._message_role(ToolMessage(content="结果", tool_call_id="c1")) == "tool"
    assert cu._message_role(HumanMessage(content="问题")) == "human"
    assert cu._message_role(object()) == "message"
