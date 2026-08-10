import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.agent.middleware.context_usage import (
    SYSTEM_PROMPT_PLACEHOLDER,
    bind_context_usage_middleware,
    build_request_breakdown,
    build_usage_from_llm_calls,
    build_message_breakdown,
    build_prompt_breakdown,
    compact_token_usage_for_state,
    count_messages_tokens,
    count_tools_tokens,
    create_request_context_usage_middleware,
    estimate_prompt_usage,
    estimate_text_tokens,
    estimate_tokens,
    invoke_with_context_usage,
    _build_request_messages,
    _extract_result_messages,
    _get_agent_context_usage,
    _merge_response_usage,
    _request_model_name,
)
from apps.chat_data_app.services.agent_run.context_budget import (
    ContextInputLimitExceededError,
    ContextBudget,
    ContextRequestBudget,
    build_context_usage_payload,
    get_model_context_budget,
    hard_input_guard_tokens,
    sanitize_context_usage_for_response,
    validate_context_budget_startup_config,
)
from apps.chat_data_app.services.runtime_config import get_context_window
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage


def test_estimate_tokens_uses_project_formula():
    assert estimate_tokens("你好 abc!") == int(2 * 0.7 + 1 * 1.3 + 1 * 0.5)


def test_estimate_tokens_applies_byte_floor_to_unbroken_ascii():
    assert estimate_tokens("x" * 5000) >= 1250


def test_context_and_payload_helpers_handle_empty_and_unknown_values():
    assert estimate_text_tokens("") == 0
    assert get_context_window("") == 128000
    assert get_context_window("unknown-model") == 128000
    assert get_context_window("QWEN-Plus") == 128000

    payload = build_context_usage_payload(
        ContextBudget(
            context_window=1,
            max_output_tokens=0,
            compression_trigger_ratio=1.0,
            hard_input_limit_tokens=1,
            compression_trigger_tokens=1,
            compression_target_tokens=1,
        ),
        used=-10,
    )
    assert payload["used"] == 0
    assert payload["total"] == 1
    assert payload["percentage"] == 0.0
    assert payload["remaining"] == 1
    assert payload["hard_input_guard_ratio"] == 0.95
    assert payload["hard_input_guard_tokens"] == 1


def test_sanitize_context_usage_for_response_redacts_legacy_breakdown_without_mutation():
    usage = {
        "breakdown": [{
            "key": "system_prompt",
            "content": "SECRET_SYSTEM_PROMPT",
            "children": [{"role": "system", "content": "SECRET_SYSTEM_PROMPT"}],
        }],
        "llm_calls": [{
            "breakdown": [{"role": "system", "content": "SECRET_SYSTEM_PROMPT"}],
        }],
    }

    sanitized = sanitize_context_usage_for_response(usage)

    assert sanitized["breakdown"][0]["content"] == SYSTEM_PROMPT_PLACEHOLDER
    assert sanitized["breakdown"][0]["children"][0]["content"] == SYSTEM_PROMPT_PLACEHOLDER
    assert sanitized["llm_calls"][0]["breakdown"][0]["content"] == SYSTEM_PROMPT_PLACEHOLDER
    assert "SECRET_SYSTEM_PROMPT" not in json.dumps(sanitized, ensure_ascii=False)
    assert usage["breakdown"][0]["content"] == "SECRET_SYSTEM_PROMPT"


def test_sanitize_context_usage_keeps_system_prompt_when_debug_enabled(monkeypatch):
    usage = {
        "breakdown": [{
            "key": "system_prompt",
            "content": "SECRET_SYSTEM_PROMPT",
            "children": [{"role": "system", "content": "SECRET_SYSTEM_PROMPT"}],
        }],
    }

    monkeypatch.setattr(
        "apps.chat_data_app.services.agent_run.context_budget.is_context_usage_system_prompt_visible",
        lambda: True,
    )

    sanitized = sanitize_context_usage_for_response(usage)

    assert sanitized is usage
    assert sanitized["breakdown"][0]["content"] == "SECRET_SYSTEM_PROMPT"


def test_count_tools_tokens_uses_tool_schema_text():
    tokens = count_tools_tokens([{
        "type": "function",
        "function": {
            "name": "run_sql",
            "description": "执行 SQL",
            "parameters": {
                "type": "object",
                "properties": {"sql": {"type": "string"}},
            },
        },
    }])

    assert tokens > estimate_tokens("run_sql")


def test_count_tools_tokens_falls_back_when_tool_conversion_fails(monkeypatch):
    class BadTool:
        def __str__(self):
            return "bad tool schema"

    monkeypatch.setattr(
        "apps.chat_data_app.agent.middleware.context_usage.convert_to_openai_tool",
        lambda _tool: (_ for _ in ()).throw(RuntimeError("bad tool")),
    )

    assert count_tools_tokens([BadTool()]) == estimate_tokens('"bad tool schema"')


def test_estimate_prompt_usage_marks_estimated(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_MODEL", "qwen3.5-35b-a3b;0.2;256000")
    usage = estimate_prompt_usage(system_prompt="system", messages=[], model_id="qwen3.5-35b-a3b")

    assert usage["used"] > 0
    assert usage["input_tokens"] == usage["used"]
    budget = get_model_context_budget("qwen3.5-35b-a3b")
    assert usage["total"] == budget.context_window
    assert usage["context_window"] == budget.context_window
    assert usage["max_output_tokens"] == budget.max_output_tokens
    assert usage["hard_input_limit_tokens"] == budget.hard_input_limit_tokens
    assert usage["hard_input_guard_tokens"] == hard_input_guard_tokens(budget.hard_input_limit_tokens)
    assert usage["compression_trigger_tokens"] == budget.compression_trigger_tokens
    assert usage["estimated"] is True


def test_model_context_budget_separates_output_reserve_from_compression_trigger(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_MODEL", "budget-model;0.2;100000")
    monkeypatch.setenv("CHAT_DATA_OUTPUT_MAX_TOKENS", "20000")

    budget = get_model_context_budget("budget-model")

    assert budget.hard_input_limit_tokens == 80000
    assert hard_input_guard_tokens(budget.hard_input_limit_tokens) == 76000
    assert budget.compression_trigger_tokens == 70000
    assert budget.compression_target_tokens == 21000


def test_model_context_budget_never_delays_compression_past_output_reserve(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_MODEL", "budget-model;0.2;100000")
    monkeypatch.setenv("CHAT_DATA_OUTPUT_MAX_TOKENS", "30000")
    monkeypatch.setattr(
        "apps.chat_data_app.services.agent_run.context_budget.get_context_compression_config",
        lambda: {"enabled": True, "trigger_ratio": 0.80},
    )

    budget = get_model_context_budget("budget-model")

    assert budget.hard_input_limit_tokens == 70000
    assert budget.compression_trigger_tokens == 66500


def test_context_budget_startup_validation_rejects_output_reserve_without_input_space(monkeypatch):
    monkeypatch.setattr(
        "apps.chat_data_app.services.agent_run.context_budget.get_chat_data_model_configs",
        lambda: [{"id": "tiny", "context_window": 1000}],
    )
    monkeypatch.setattr(
        "apps.chat_data_app.services.agent_run.context_budget.get_chat_data_output_max_tokens",
        lambda: 1000,
    )
    monkeypatch.setattr(
        "apps.chat_data_app.services.agent_run.context_budget.get_context_compression_summary_output_max_tokens",
        lambda: 200,
    )

    with pytest.raises(RuntimeError, match="CHAT_DATA_OUTPUT_MAX_TOKENS=1000"):
        validate_context_budget_startup_config()


def test_context_budget_startup_validation_rejects_trigger_ratio_above_guard(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_CONTEXT_COMPRESSION_TRIGGER_RATIO", "0.951")

    with pytest.raises(RuntimeError, match="CHAT_DATA_CONTEXT_COMPRESSION_TRIGGER_RATIO"):
        validate_context_budget_startup_config()


def test_context_request_budget_rejects_dynamic_static_input_above_guard(monkeypatch):
    monkeypatch.setattr(
        "apps.chat_data_app.services.agent_run.context_budget.get_model_context_budget",
        lambda *_args, **_kwargs: ContextBudget(
            context_window=100,
            max_output_tokens=20,
            compression_trigger_ratio=0.70,
            hard_input_limit_tokens=80,
            compression_trigger_tokens=56,
            compression_target_tokens=16,
        ),
    )
    budget = ContextRequestBudget("tiny")
    budget.configure("系统提示词" * 200, [])

    with pytest.raises(ContextInputLimitExceededError, match="固定输入超过上下文安全水位"):
        budget.validate_static_input()


def test_build_message_breakdown_includes_message_metadata_and_content():
    rows = build_message_breakdown([
        HumanMessage(content="你好"),
        AIMessage(content="我来分析"),
    ])

    assert rows[0]["role"] == "human"
    assert rows[0]["round"] == 1
    assert rows[0]["message_index"] == 0
    assert rows[0]["content"] == "你好"
    assert rows[1]["role"] == "ai"
    assert rows[1]["round"] == 1
    assert rows[1]["message_index"] == 1
    assert rows[1]["content"] == "我来分析"


def test_build_message_breakdown_keeps_system_message_content_by_default():
    rows = build_message_breakdown([
        SystemMessage(content="# 角色 SECRET_SYSTEM_PROMPT"),
    ])

    assert rows[0]["role"] == "system"
    assert rows[0]["content"] == "# 角色 SECRET_SYSTEM_PROMPT"
    assert rows[0].get("redacted") is None


def test_build_message_breakdown_redacts_runtime_context_summary():
    rows = build_message_breakdown([
        HumanMessage(content="内部摘要内容", additional_kwargs={"context_summary": True}),
        HumanMessage(content="当前用户问题"),
    ])

    assert rows[0]["label"] == "历史摘要（运行态）"
    assert rows[0]["role"] == "context_summary"
    assert rows[0]["content"] == SYSTEM_PROMPT_PLACEHOLDER
    assert rows[0]["redacted"] is True
    assert rows[1]["round"] == 1


def test_build_message_breakdown_redacts_when_explicitly_requested():
    rows = build_message_breakdown([
        SystemMessage(content="# 角色 SECRET_SYSTEM_PROMPT"),
    ], hide_content=True)

    assert rows[0]["role"] == "system"
    assert rows[0]["content"] == SYSTEM_PROMPT_PLACEHOLDER
    assert rows[0]["description"] == SYSTEM_PROMPT_PLACEHOLDER
    assert rows[0]["redacted"] is True
    assert "SECRET_SYSTEM_PROMPT" not in json.dumps(rows, ensure_ascii=False)


def test_build_message_breakdown_includes_ai_tool_call_arguments():
    rows = build_message_breakdown([
        AIMessage(
            content="",
            tool_calls=[{
                "id": "call_1",
                "name": "run_sql",
                "args": {"sql": "select count(*) from orders"},
            }],
        )
    ])

    assert "tool_calls" in rows[0]["content"]
    assert "run_sql" in rows[0]["content"]
    assert "select count(*) from orders" in rows[0]["content"]


def test_message_tokens_handle_structured_content_and_raw_tool_calls():
    message = AIMessage(
        content=[{"type": "text", "text": "结构化内容"}],
        additional_kwargs={"tool_calls": [{"function": {"name": "run_sql", "arguments": "{\"sql\":\"select 1\"}"}}]},
    )

    assert count_messages_tokens([message]) > 4
    rows = build_message_breakdown([message])
    assert "raw_tool_calls" in rows[0]["content"]


def test_message_tokens_do_not_double_count_normalized_and_raw_tool_calls():
    normalized = [{"id": "call_1", "name": "run_sql", "args": {"sql": "select 1"}}]
    raw = [{
        "id": "call_1",
        "type": "function",
        "function": {"name": "run_sql", "arguments": '{"sql":"select 1"}'},
    }]
    normalized_only = AIMessage(content="", tool_calls=normalized)
    normalized_and_raw = AIMessage(
        content="",
        tool_calls=normalized,
        additional_kwargs={"tool_calls": raw},
    )

    assert count_messages_tokens([normalized_and_raw]) == count_messages_tokens([normalized_only])


def test_build_message_breakdown_includes_tool_output_content():
    rows = build_message_breakdown([
        ToolMessage(content='{"rows": [{"count": 12}]}', tool_call_id="call_1"),
    ])

    assert rows[0]["role"] == "tool"
    assert rows[0]["label"].endswith("工具结果")
    assert '{"rows": [{"count": 12}]}' in rows[0]["content"]


def test_build_prompt_breakdown_keeps_current_input_context_only():
    breakdown = build_prompt_breakdown(
        system_tokens=10,
        messages=[HumanMessage(content="统计订单数")],
        message_tokens=10,
        measured_input_tokens=20,
    )

    assert [item["key"] for item in breakdown] == ["system_prompt", "messages"]
    assert breakdown[1]["children"][0]["content"] == "统计订单数"


def test_build_prompt_breakdown_includes_provider_overhead_when_measured():
    breakdown = build_prompt_breakdown(
        system_tokens=100,
        messages=[HumanMessage(content="你好")],
        message_tokens=10,
        measured_input_tokens=1000,
    )

    system_item = next(item for item in breakdown if item["key"] == "system_prompt")
    message_item = next(item for item in breakdown if item["key"] == "messages")
    assert system_item["tokens"] == 100
    assert message_item["tokens"] == 10
    overhead_item = next(item for item in breakdown if item["key"] == "request_overhead")
    assert overhead_item["tokens"] == 890


def test_build_request_breakdown_groups_tools_and_keeps_messages():
    breakdown = build_request_breakdown(
        messages=[
            HumanMessage(content="你好"),
            AIMessage(content="", tool_calls=[{"id": "call_1", "name": "run_sql", "args": {"sql": "select 1"}}]),
            ToolMessage(content='{"rows": [{"count": 1}]}', tool_call_id="call_1"),
        ],
        message_tokens=30,
        tools=[{
            "type": "function",
            "function": {
                "name": "run_sql",
                "description": "执行 SQL",
                "parameters": {"type": "object", "properties": {"sql": {"type": "string"}}},
            },
        }],
        tools_tokens=20,
        overhead_tokens=3,
    )

    tool_item = next(item for item in breakdown if item["key"] == "tool_schema")
    message_item = next(item for item in breakdown if item["key"] == "messages")
    assert tool_item["children"][0]["label"] == "工具定义 · run_sql"
    assert "run_sql" in tool_item["children"][0]["content"]
    assert len(message_item["children"]) == 3
    assert message_item["children"][0]["role"] == "human"
    assert message_item["children"][1]["role"] == "ai"
    assert message_item["children"][2]["role"] == "tool"
    assert breakdown[-1]["key"] == "request_overhead"
    assert breakdown[-1]["tokens"] == 3
    assert sum(item["tokens"] for item in breakdown) == 53


def test_build_request_breakdown_includes_system_message_group():
    system_prompt = "系统提示 SECRET_SYSTEM_PROMPT"
    system_tokens = count_messages_tokens([SystemMessage(content=system_prompt)])
    breakdown = build_request_breakdown(
        messages=[
            SystemMessage(content=system_prompt),
            HumanMessage(content="用户问题"),
        ],
        message_tokens=20,
        tools=[],
        tools_tokens=0,
        overhead_tokens=3,
    )

    assert [item["key"] for item in breakdown] == ["system_prompt", "messages", "request_overhead"]
    assert breakdown[0]["children"][0]["role"] == "system"
    assert breakdown[0]["children"][0]["content"] == system_prompt
    assert breakdown[0]["children"][0]["tokens"] == system_tokens
    assert breakdown[1]["children"][0]["message_index"] == 1
    assert breakdown[2]["tokens"] == 3


def test_build_request_breakdown_can_return_only_tool_schema():
    breakdown = build_request_breakdown(
        messages=[],
        message_tokens=0,
        tools=[{
            "type": "function",
            "function": {"name": "only_tool", "description": "工具", "parameters": {"type": "object"}},
        }],
        tools_tokens=10,
        overhead_tokens=0,
    )

    assert [item["key"] for item in breakdown] == ["tool_schema"]
    assert breakdown[0]["children"][0]["label"] == "工具定义 · only_tool"


def test_build_tools_breakdown_fallback_payload(monkeypatch):
    from apps.chat_data_app.agent.middleware import context_usage as module

    class BadTool:
        def __str__(self):
            return "bad tool"

    monkeypatch.setattr(module, "convert_to_openai_tool", lambda _tool: (_ for _ in ()).throw(RuntimeError("bad")))
    item = module.build_tools_breakdown([BadTool()])[0]

    assert item["label"] == "工具定义 · 1"
    assert "bad tool" in item["content"]


def test_build_usage_from_llm_calls_uses_current_call_as_context():
    usage = build_usage_from_llm_calls([
        {"index": 1, "input_tokens": 100, "output_tokens": 10},
        {"index": 2, "input_tokens": 300, "output_tokens": 20},
        {"index": 3, "input_tokens": 200, "output_tokens": 30},
    ], 1000)

    assert usage["used"] == 200
    assert usage["input_tokens"] == 200
    assert usage["current_input_tokens"] == 200
    assert usage["max_input_tokens"] == 300
    assert usage["cumulative_input_tokens"] == 600
    assert usage["output_tokens"] == 60
    assert usage["total_tokens"] == 660


def test_build_usage_from_llm_calls_includes_cache_metrics_only_when_reported():
    usage = build_usage_from_llm_calls([
        {"index": 1, "input_tokens": 100, "actual_input_tokens": 120, "cached_input_tokens": 0},
        {"index": 2, "input_tokens": 300, "actual_input_tokens": 400, "cached_input_tokens": 320},
    ], 1000)

    assert usage["current_cached_input_tokens"] == 320
    assert usage["cumulative_cached_input_tokens"] == 320
    assert usage["current_cache_hit_rate"] == 80.0
    assert usage["cumulative_cache_hit_rate"] == 61.5

    partial_usage = build_usage_from_llm_calls([
        {"index": 1, "input_tokens": 100, "cached_input_tokens": 80},
        {"index": 2, "input_tokens": 300, "actual_input_tokens": 400, "cached_input_tokens": 320},
    ], 1000)
    assert partial_usage["cumulative_cached_input_tokens"] == 320
    assert partial_usage["cumulative_cache_hit_rate"] == 80.0

    unsupported_usage = build_usage_from_llm_calls([
        {"index": 1, "input_tokens": 100, "actual_input_tokens": 120},
    ], 1000)
    assert "current_cached_input_tokens" not in unsupported_usage
    assert "cumulative_cached_input_tokens" not in unsupported_usage
    assert "current_cache_hit_rate" not in unsupported_usage


def test_build_usage_from_llm_calls_returns_none_for_empty_calls():
    assert build_usage_from_llm_calls([], 1000) is None


def test_request_message_and_model_helpers():
    request = SimpleNamespace(
        system_message=SystemMessage(content="系统"),
        messages=[HumanMessage(content="问题"), "bad"],
        model=SimpleNamespace(model_name="qwen-test"),
    )

    messages = _build_request_messages(request)
    assert [message.type for message in messages] == ["system", "human"]
    assert _request_model_name(request) == "qwen-test"
    assert _request_model_name(SimpleNamespace(model=SimpleNamespace(model="model-field"))) == "model-field"
    assert _request_model_name(SimpleNamespace(model=SimpleNamespace(model_id="model-id-field"))) == "model-id-field"
    assert _request_model_name(SimpleNamespace(model=SimpleNamespace())) is None

    request_without_system = SimpleNamespace(messages=[HumanMessage(content="用户问题")])
    assert _build_request_messages(request_without_system) == request_without_system.messages


def test_merge_response_usage_supports_message_and_result_wrapper():
    record = {"input_tokens": 1}
    _merge_response_usage(record, AIMessage(content="ok", usage_metadata={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}))
    assert record["actual_input_tokens"] == 10
    assert record["output_tokens"] == 2
    assert record["actual_total_tokens"] == 12

    wrapper_record = {"input_tokens": 1}
    _merge_response_usage(wrapper_record, SimpleNamespace(result=[
        AIMessage(content="ok", usage_metadata={"input_tokens": 5, "output_tokens": 3, "total_tokens": 8})
    ]))
    assert wrapper_record["actual_total_tokens"] == 8

    ignored_record = {"input_tokens": 1}
    _merge_response_usage(ignored_record, SimpleNamespace(result=[]))
    assert ignored_record == {"input_tokens": 1}


def test_merge_response_usage_extracts_normalized_and_openai_cache_metrics():
    normalized_record = {"input_tokens": 1}
    _merge_response_usage(normalized_record, AIMessage(
        content="ok",
        usage_metadata={
            "input_tokens": 100,
            "output_tokens": 2,
            "total_tokens": 102,
            "input_token_details": {"cache_read": 80},
        },
    ))
    assert normalized_record["cached_input_tokens"] == 80

    priority_record = {"input_tokens": 1}
    _merge_response_usage(priority_record, AIMessage(
        content="ok",
        usage_metadata={
            "input_tokens": 100,
            "output_tokens": 2,
            "total_tokens": 102,
            "input_token_details": {"priority_cache_read": 80},
        },
    ))
    assert priority_record["cached_input_tokens"] == 80

    raw_record = {"input_tokens": 1}
    _merge_response_usage(raw_record, AIMessage(
        content="ok",
        usage_metadata={"input_tokens": 100, "output_tokens": 2, "total_tokens": 102},
        response_metadata={"token_usage": {"prompt_tokens_details": {"cached_tokens": 0}}},
    ))
    assert raw_record["cached_input_tokens"] == 0

    raw_usage_record = {"input_tokens": 1}
    _merge_response_usage(raw_usage_record, AIMessage(
        content="ok",
        response_metadata={"token_usage": {
            "prompt_tokens": 100,
            "completion_tokens": 2,
            "total_tokens": 102,
            "prompt_tokens_details": {"cached_tokens": 80},
        }},
    ))
    assert raw_usage_record["actual_input_tokens"] == 100
    assert raw_usage_record["cached_input_tokens"] == 80

    unsupported_record = {"input_tokens": 1}
    _merge_response_usage(unsupported_record, AIMessage(
        content="ok",
        usage_metadata={"input_tokens": 100, "output_tokens": 2, "total_tokens": 102},
    ))
    assert "cached_input_tokens" not in unsupported_record


def test_bind_and_get_agent_context_usage_handles_weakref_and_attribute_fallback():
    class Agent:
        pass

    class NonWeakrefAgent:
        __slots__ = ("_chat_data_context_usage_middleware",)

    middleware = SimpleNamespace(get_context_usage=lambda: {"used": 10})
    agent = Agent()
    bind_context_usage_middleware(agent, middleware)
    assert _get_agent_context_usage(agent) == {"used": 10}

    fallback_agent = NonWeakrefAgent()
    bind_context_usage_middleware(fallback_agent, middleware)
    assert _get_agent_context_usage(fallback_agent) == {"used": 10}
    assert _get_agent_context_usage(object()) is None


def test_extract_result_messages_filters_non_messages():
    messages = _extract_result_messages({"messages": [HumanMessage(content="ok"), "bad"]})
    assert len(messages) == 1
    assert messages[0].content == "ok"
    assert _extract_result_messages({"messages": "bad"}) == []


def test_create_request_context_usage_middleware_records_and_merges_usage():
    async def run():
        middleware = create_request_context_usage_middleware()
        request = SimpleNamespace(
            system_message=SystemMessage(content="系统提示"),
            messages=[HumanMessage(content="统计订单数")],
            tools=[{
                "type": "function",
                "function": {"name": "run_sql", "parameters": {"type": "object"}},
            }],
            model=SimpleNamespace(model_name="qwen3.5-35b-a3b"),
        )

        async def handler(_request):
            return AIMessage(content="ok", usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120})

        response = await middleware.awrap_model_call(request, handler)
        return middleware, response

    import asyncio

    middleware, response = asyncio.run(run())
    assert response.model_response.result[0].content == "ok"
    assert len(middleware.context_usage_records) == 1
    record = middleware.context_usage_records[0]
    assert record["model"] == "qwen3.5-35b-a3b"
    assert record["actual_input_tokens"] == 100
    assert record["output_tokens"] == 20
    assert middleware.get_context_usage()["llm_calls"][0]["actual_total_tokens"] == 120


def test_compact_token_usage_for_state_removes_large_details_without_mutation():
    usage = {
        "used": 10,
        "total": 100,
        "input_tokens": 10,
        "llm_calls": [{"breakdown": [{"content": "large prompt"}]}],
        "breakdown": [{"content": "large prompt"}],
        "context_compression": {
            "triggered": False,
            "summary": "large summary should not be persisted",
            "summary_chars": 12,
        },
    }

    compact = compact_token_usage_for_state(usage)

    assert compact == {
        "used": 10,
        "total": 100,
        "input_tokens": 10,
        "context_compression": {"triggered": False, "summary_chars": 12},
    }
    assert "llm_calls" in usage
    assert "breakdown" in usage


def test_create_request_context_usage_middleware_keeps_tool_results(monkeypatch):
    async def run():
        from apps.chat_data_app.agent.middleware import context_usage

        monkeypatch.setattr(
            context_usage.ContextRequestBudget,
            "configure",
            lambda self, system_prompt, tools, model_id=None: ContextBudget(
                context_window=10000,
                max_output_tokens=2000,
                compression_trigger_ratio=0.70,
                hard_input_limit_tokens=8000,
                compression_trigger_tokens=7000,
                compression_target_tokens=2100,
            ),
        )
        monkeypatch.setattr(context_usage.ContextRequestBudget, "remaining_for_messages", lambda self: 7000)
        monkeypatch.setattr(context_usage, "_emit_context_usage", lambda _usage: None)
        middleware = create_request_context_usage_middleware("qwen-test", state_schema=dict)
        captured = {}
        request = SimpleNamespace(
            system_message=SystemMessage(content="分析系统提示"),
            messages=[
                HumanMessage(content="历史问题"),
                ToolMessage(content="历史工具结果 " * 5000, tool_call_id="call_old", name="semantic_query_tool"),
                AIMessage(content="历史回答"),
                HumanMessage(content="当前问题"),
            ],
            tools=[],
            model=SimpleNamespace(model_name="qwen-test"),
        )

        async def handler(inner_request):
            captured["request"] = inner_request
            return AIMessage(content="ok", usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120})

        response = await middleware.awrap_model_call(request, handler)
        return middleware, response, captured

    import asyncio

    middleware, response, captured = asyncio.run(run())
    sent_messages = captured["request"].messages
    assert sent_messages[1].content.startswith("历史工具结果")
    assert response.model_response.result[0].content == "ok"
    usage = response.command.update["token_usage"]
    assert "context_editing" not in usage
    assert "llm_calls" not in usage
    assert "breakdown" not in usage
    assert "llm_calls" in middleware.get_context_usage()
    assert "breakdown" in middleware.get_context_usage()
    assert "context_editing" not in middleware.get_context_usage()


def test_invoke_with_context_usage_prefers_runtime_usage_and_falls_back_to_estimate():
    import asyncio

    class Agent:
        def __init__(self, result):
            self.result = result

        async def ainvoke(self, _state):
            return self.result

    middleware = SimpleNamespace(get_context_usage=lambda: {"used": 99, "total": 100})
    agent = Agent({"messages": [AIMessage(content="完成")]})
    bind_context_usage_middleware(agent, middleware)
    result, usage = asyncio.run(invoke_with_context_usage(
        agent=agent,
        state={"messages": [HumanMessage(content="问题")]},
        system_prompt="系统",
        model_id="qwen",
    ))
    assert result["messages"][0].content == "完成"
    assert usage == {"used": 99, "total": 100}

    fallback_agent = Agent({"messages": [AIMessage(content="没有 runtime usage")]})
    _result, fallback_usage = asyncio.run(invoke_with_context_usage(
        agent=fallback_agent,
        state={"messages": [HumanMessage(content="问题")]},
        system_prompt="系统",
        model_id="qwen",
    ))
    assert fallback_usage["estimated"] is True
    assert fallback_usage["input_tokens"] > 0
