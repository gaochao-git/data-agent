import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB_PORT", "3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.agent.middleware.token_usage_middleware import (
    bind_token_usage_middleware,
    build_token_usage_payload,
    build_request_breakdown,
    build_usage_from_llm_calls,
    build_message_breakdown,
    build_prompt_breakdown,
    count_messages_tokens,
    count_tools_tokens,
    create_request_token_usage_middleware,
    estimate_prompt_usage,
    estimate_text_tokens,
    estimate_tokens,
    get_context_window,
    invoke_with_token_usage,
    _build_request_messages,
    _extract_result_messages,
    _get_agent_token_usage,
    _message_tokens,
    _merge_response_usage,
    _request_model_name,
)
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage


def test_estimate_tokens_uses_project_formula():
    assert estimate_tokens("你好 abc!") == int(2 * 0.7 + 1 * 1.3 + 1 * 0.5)


def test_context_and_payload_helpers_handle_empty_and_unknown_values():
    assert estimate_text_tokens("") == 0
    assert get_context_window("") == 128000
    assert get_context_window("unknown-model") == 128000
    assert get_context_window("QWEN-Plus") == 128000
    assert get_context_window("kimi-k2.5") == 256000
    assert get_context_window("qwen3.6-35b-a3b") == 256000

    payload = build_token_usage_payload(used=-10, total=0)
    assert payload == {
        "used": 0,
        "total": 1,
        "percentage": 0.0,
        "remaining": 1,
    }


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
        "apps.chat_data_app.agent.middleware.token_usage_middleware.convert_to_openai_tool",
        lambda _tool: (_ for _ in ()).throw(RuntimeError("bad tool")),
    )

    assert count_tools_tokens([BadTool()]) == estimate_tokens('"bad tool schema"')


def test_estimate_prompt_usage_marks_estimated():
    usage = estimate_prompt_usage(system_prompt="system", messages=[], model_id="qwen3.5-35b-a3b")

    assert usage["used"] > 0
    assert usage["input_tokens"] == usage["used"]
    assert usage["total"] == 256000
    assert usage["estimated"] is True


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

    assert _message_tokens(message) > 4
    assert count_messages_tokens([message]) == _message_tokens(message)
    rows = build_message_breakdown([message])
    assert "raw_tool_calls" in rows[0]["content"]


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


def test_build_prompt_breakdown_does_not_add_provider_overhead():
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
    assert not any(item["key"] == "framework_overhead" for item in breakdown)


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


def test_build_request_breakdown_includes_system_message_group():
    breakdown = build_request_breakdown(
        messages=[
            SystemMessage(content="系统提示"),
            HumanMessage(content="用户问题"),
        ],
        message_tokens=20,
        tools=[],
        tools_tokens=0,
        overhead_tokens=3,
    )

    assert [item["key"] for item in breakdown] == ["system_prompt", "messages"]
    assert breakdown[0]["children"][0]["role"] == "system"
    assert breakdown[1]["children"][0]["message_index"] == 1


def test_build_tools_breakdown_fallback_payload(monkeypatch):
    from apps.chat_data_app.agent.middleware import token_usage_middleware as module

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


def test_bind_and_get_agent_token_usage_handles_weakref_and_attribute_fallback():
    class Agent:
        pass

    class NonWeakrefAgent:
        __slots__ = ("_chat_data_token_usage_middleware",)

    middleware = SimpleNamespace(get_token_usage=lambda: {"used": 10})
    agent = Agent()
    bind_token_usage_middleware(agent, middleware)
    assert _get_agent_token_usage(agent) == {"used": 10}

    fallback_agent = NonWeakrefAgent()
    bind_token_usage_middleware(fallback_agent, middleware)
    assert _get_agent_token_usage(fallback_agent) == {"used": 10}
    assert _get_agent_token_usage(object()) is None


def test_extract_result_messages_filters_non_messages():
    messages = _extract_result_messages({"messages": [HumanMessage(content="ok"), "bad"]})
    assert len(messages) == 1
    assert messages[0].content == "ok"
    assert _extract_result_messages({"messages": "bad"}) == []


def test_create_request_token_usage_middleware_records_and_merges_usage():
    async def run():
        middleware = create_request_token_usage_middleware()
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
    assert response.content == "ok"
    assert len(middleware.token_usage_records) == 1
    record = middleware.token_usage_records[0]
    assert record["model"] == "qwen3.5-35b-a3b"
    assert record["actual_input_tokens"] == 100
    assert record["output_tokens"] == 20
    assert middleware.get_token_usage()["llm_calls"][0]["actual_total_tokens"] == 120


def test_invoke_with_token_usage_prefers_runtime_usage_and_falls_back_to_estimate():
    import asyncio

    class Agent:
        def __init__(self, result):
            self.result = result

        async def ainvoke(self, _state):
            return self.result

    middleware = SimpleNamespace(get_token_usage=lambda: {"used": 99, "total": 100})
    agent = Agent({"messages": [AIMessage(content="完成")]})
    bind_token_usage_middleware(agent, middleware)
    result, usage = asyncio.run(invoke_with_token_usage(
        agent=agent,
        state={"messages": [HumanMessage(content="问题")]},
        system_prompt="系统",
        model_id="qwen",
    ))
    assert result["messages"][0].content == "完成"
    assert usage == {"used": 99, "total": 100}

    fallback_agent = Agent({"messages": [AIMessage(content="没有 runtime usage")]})
    _result, fallback_usage = asyncio.run(invoke_with_token_usage(
        agent=fallback_agent,
        state={"messages": [HumanMessage(content="问题")]},
        system_prompt="系统",
        model_id="qwen",
    ))
    assert fallback_usage["estimated"] is True
    assert fallback_usage["input_tokens"] > 0
