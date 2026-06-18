import os
import sys
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB_PORT", "3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from langchain_core.tools import tool

from apps.chat_data_app.agent.tools.retry_guard import _tool_call_key, create_tool_retry_guard, is_failed_tool_result


@tool("unstable_tool")
async def unstable_tool(value: str) -> str:
    """Test unstable tool."""
    if value == "ok":
        return "ok"
    return "unstable_tool 执行失败: bad value"


def test_retry_guard_blocks_same_failed_call_after_two_failures():
    guarded = create_tool_retry_guard([unstable_tool])[0]

    first = asyncio.run(guarded.ainvoke({"value": "bad"}))
    second = asyncio.run(guarded.ainvoke({"value": "bad"}))
    third = asyncio.run(guarded.ainvoke({"value": "bad"}))

    assert "执行失败" in first
    assert "执行失败" in second
    assert "调用被阻断" in third
    assert "相同参数已连续失败 2 次" in third


def test_retry_guard_tracks_arguments_independently():
    guarded = create_tool_retry_guard([unstable_tool])[0]

    asyncio.run(guarded.ainvoke({"value": "bad_a"}))
    asyncio.run(guarded.ainvoke({"value": "bad_a"}))
    different_args_result = asyncio.run(guarded.ainvoke({"value": "bad_b"}))

    assert "执行失败" in different_args_result
    assert "调用被阻断" not in different_args_result


def test_retry_guard_clears_failure_count_after_success():
    guarded = create_tool_retry_guard([unstable_tool])[0]

    asyncio.run(guarded.ainvoke({"value": "bad"}))
    asyncio.run(guarded.ainvoke({"value": "ok"}))
    second_failure_after_success = asyncio.run(guarded.ainvoke({"value": "bad"}))

    assert "执行失败" in second_failure_after_success
    assert "调用被阻断" not in second_failure_after_success


def test_retry_guard_does_not_treat_success_payload_error_null_as_failure():
    result = '[execute_sql_tool]\n{"success": true, "error": null, "rows": []}'

    assert is_failed_tool_result(result) is False


def test_retry_guard_helper_edges():
    assert is_failed_tool_result({"success": False}) is False
    assert is_failed_tool_result("   ") is False
    assert is_failed_tool_result("[tool]\nnot json but failed") is True
    assert _tool_call_key("demo", {"items": {3, 1}, "tuple": ("b", "a")}) == _tool_call_key(
        "demo",
        {"tuple": ["b", "a"], "items": [1, 3]},
    )

    passthrough = object()
    assert create_tool_retry_guard([passthrough]) == [passthrough]
