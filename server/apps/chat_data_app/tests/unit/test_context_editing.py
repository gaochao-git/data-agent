import os
import sys

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.agent.middleware.context_editing import edit_messages_for_llm


def _tool(content: str, call_id: str) -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=call_id, name="semantic_query_tool")


def _config(**overrides):
    config = {
        "enabled": True,
        "trigger_ratio": 0.01,
    }
    config.update(overrides)
    return config


def test_context_editing_disabled_or_not_triggered_keeps_original_messages():
    messages = [HumanMessage(content="问题"), AIMessage(content="回答")]

    disabled, disabled_report = edit_messages_for_llm(messages, context_window=100, config={**_config(), "enabled": False})
    not_triggered, not_triggered_report = edit_messages_for_llm(messages, context_window=100000, config={**_config(), "trigger_ratio": 1.0})

    assert disabled is not messages
    assert disabled == messages
    assert disabled_report["triggered"] is False
    assert not_triggered == messages
    assert not_triggered_report["triggered"] is False


def test_context_editing_summarizes_old_tool_results_until_budget_and_keeps_tool_args():
    big_result = "<query_result>" + (
        '{"success":true,"total_count":10000,"columns":["订单ID","金额"],"sql":"select * from orders","data":['
        + '{"订单ID":1,"金额":2},' * 500
        + "{}]}"
    ) + "</query_result>"
    messages = [
        HumanMessage(content="查订单1"),
        AIMessage(content="", tool_calls=[{"id": "call_1", "name": "semantic_query_tool", "args": {"sql": "select * from orders"}}]),
        _tool(big_result, "call_1"),
        AIMessage(content="结果1"),
        HumanMessage(content="查订单2"),
        AIMessage(content="", tool_calls=[{"id": "call_2", "name": "semantic_query_tool", "args": {}}]),
        _tool('{"success":true,"total_count":1,"columns":["A"],"data":[{"A":1}]}', "call_2"),
        AIMessage(content="结果2"),
        HumanMessage(content="继续分析"),
    ]

    edited, report = edit_messages_for_llm(messages, context_window=6000, config=_config(trigger_ratio=0.70))

    assert report["triggered"] is True
    assert report["cleared_tool_results"] == 1
    assert edited[1].tool_calls[0]["args"]["sql"] == "select * from orders"
    assert edited[2].content.startswith("[工具结果已移出模型上下文]")
    assert "total_count=10000" in edited[2].content
    assert "columns=订单ID,金额" in edited[2].content
    assert edited[6].content.startswith('{"success":true')
    assert messages[2].content == big_result


def test_context_editing_does_not_clear_current_turn_tool_result():
    messages = [
        HumanMessage(content="历史"),
        _tool("x" * 5000, "old"),
        HumanMessage(content="当前"),
        _tool("y" * 5000, "current"),
    ]

    edited, report = edit_messages_for_llm(messages, context_window=100, config=_config())

    assert report["triggered"] is True
    assert edited[1].content.startswith("[工具结果已移出模型上下文]")
    assert edited[-1].content == "y" * 5000


def test_context_editing_trims_old_rounds_when_tool_summaries_are_not_enough():
    messages = []
    for index in range(12):
        messages.extend([
            HumanMessage(content=f"历史问题{index}" + "x" * 1000),
            AIMessage(content=f"历史回答{index}" + "y" * 1000),
        ])
    messages.append(HumanMessage(content="当前问题"))

    edited, report = edit_messages_for_llm(
        messages,
        context_window=100,
        config=_config(),
    )

    assert report["triggered"] is True
    assert report["history_trimmed"] is True
    assert report["kept_recent_rounds"] == 1
    assert edited[0].content.startswith("[历史消息已移出模型上下文]")
    assert edited[-1].content == "当前问题"
    assert any(getattr(message, "content", "").startswith("历史问题11") for message in edited)
