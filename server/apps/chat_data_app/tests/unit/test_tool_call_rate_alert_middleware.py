import os
import sys
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "dummy")

from apps.chat_data_app.agent.middleware import tool_call_rate_alert
from apps.chat_data_app.agent.middleware.tool_call_rate_alert import (
    ConversationToolCallWindow,
    create_tool_call_rate_alert_middleware,
    reset_tool_call_rate_window,
)
from utils import alarm as alarm_module


def _request(conversation_id="conv-1", user_id="u-1", tool_name="run_sql"):
    return SimpleNamespace(
        state={"conversation_id": conversation_id, "user_id": user_id},
        tool_call={"id": "call-1", "name": tool_name, "args": {}},
        runtime=None,
    )


async def _ok_handler(_request):
    return ToolMessage(content="结果", name="run_sql", tool_call_id="call-1")


@pytest.fixture(autouse=True)
def _isolate_window():
    reset_tool_call_rate_window()
    yield
    reset_tool_call_rate_window()


@pytest.fixture
def alarms(monkeypatch):
    """捕获告警调用（patch 打在消费方模块的顶层绑定上）。"""
    emitted = []
    monkeypatch.setattr(tool_call_rate_alert, "emit_tool_call_rate_alarm", lambda **kwargs: emitted.append(kwargs))
    return emitted


# --------------------------- middleware 行为 ---------------------------


@pytest.mark.asyncio
async def test_below_threshold_does_not_alert(alarms):
    middleware = create_tool_call_rate_alert_middleware(threshold=3, window_seconds=60)

    result = None
    for _ in range(3):
        result = await middleware.awrap_tool_call(_request(), _ok_handler)

    assert alarms == []
    assert result.content == "结果"


@pytest.mark.asyncio
async def test_exceeding_threshold_alerts_with_conversation_context(alarms):
    middleware = create_tool_call_rate_alert_middleware(threshold=2, window_seconds=60)

    for _ in range(3):
        await middleware.awrap_tool_call(_request(), _ok_handler)

    assert len(alarms) == 1
    assert alarms[0]["conversation_id"] == "conv-1"
    assert alarms[0]["count"] == 3
    assert alarms[0]["threshold"] == 2
    assert alarms[0]["window_seconds"] == 60
    assert alarms[0]["tool_name"] == "run_sql"
    assert alarms[0]["user_id"] == "u-1"


@pytest.mark.asyncio
async def test_same_conversation_alerts_only_once_per_window(alarms):
    """持续超限时同一窗口内只告警一次，避免刷屏。"""
    middleware = create_tool_call_rate_alert_middleware(threshold=1, window_seconds=60)

    for _ in range(5):
        await middleware.awrap_tool_call(_request(), _ok_handler)

    assert len(alarms) == 1


@pytest.mark.asyncio
async def test_conversations_are_counted_separately(alarms):
    middleware = create_tool_call_rate_alert_middleware(threshold=2, window_seconds=60)

    for _ in range(2):
        await middleware.awrap_tool_call(_request(conversation_id="conv-a"), _ok_handler)
        await middleware.awrap_tool_call(_request(conversation_id="conv-b"), _ok_handler)

    assert alarms == []


@pytest.mark.asyncio
async def test_missing_conversation_id_skips_counting(alarms):
    request = SimpleNamespace(state={}, tool_call={"id": "c", "name": "run_sql", "args": {}}, runtime=None)
    middleware = create_tool_call_rate_alert_middleware(threshold=1, window_seconds=60)

    result = None
    for _ in range(3):
        result = await middleware.awrap_tool_call(request, _ok_handler)

    assert alarms == []
    assert result.content == "结果"


@pytest.mark.asyncio
async def test_falls_back_to_thread_id_from_runnable_config(alarms):
    """state 无 conversation_id 时回退 config.configurable.thread_id。"""
    request = SimpleNamespace(
        state={},
        tool_call={"id": "c", "name": "run_sql", "args": {}},
        runtime=SimpleNamespace(config={"configurable": {"thread_id": "conv-from-config"}}),
    )
    middleware = create_tool_call_rate_alert_middleware(threshold=1, window_seconds=60)

    for _ in range(2):
        await middleware.awrap_tool_call(request, _ok_handler)

    assert len(alarms) == 1
    assert alarms[0]["conversation_id"] == "conv-from-config"


@pytest.mark.asyncio
async def test_counting_failure_does_not_break_tool_execution(monkeypatch):
    """统计异常必须被吞掉：工具结果照常返回。"""

    def _boom(*_args, **_kwargs):
        raise RuntimeError("统计炸了")

    monkeypatch.setattr(tool_call_rate_alert, "resolve_conversation_id", _boom)
    middleware = create_tool_call_rate_alert_middleware(threshold=1, window_seconds=60)

    result = await middleware.awrap_tool_call(_request(), _ok_handler)

    assert result.content == "结果"


# --------------------------- 窗口内部行为（注入 now） ---------------------------


def test_window_slides_out_expired_calls():
    window = ConversationToolCallWindow()

    assert window.record("c", 60, now=100.0) == 1
    assert window.record("c", 60, now=130.0) == 2
    # 161.0 时 100.0 已滑出窗口，只剩 130.0 与当次
    assert window.record("c", 60, now=161.0) == 2


def test_alert_dedup_expires_with_window():
    window = ConversationToolCallWindow()
    window.mark_alerted("c", now=100.0)

    assert window.alerted_recently("c", 60, now=130.0) is True
    assert window.alerted_recently("c", 60, now=161.0) is False


def test_expired_conversations_are_reclaimed_when_over_capacity():
    window = ConversationToolCallWindow(max_conversations=2)

    for index in range(5):
        window.record(f"conv-{index}", 60, now=1000.0)
    # 同一窗口内的会话即使超容量也不能回收——回收会低估计数
    assert len(window._timestamps) == 5

    window.record("conv-new", 60, now=2000.0)
    assert set(window._timestamps) == {"conv-new"}


def test_alert_dedup_records_follow_stale_conversations():
    """告警去重记录随过期会话一并回收，不单独残留。"""
    window = ConversationToolCallWindow(max_conversations=2)
    window.mark_alerted("stale", now=1000.0)

    for index in range(3):
        window.record(f"conv-{index}", 60, now=1000.0)
    # 窗口未滑过：去重记录保留（回收会让同一窗口重复告警）
    assert "stale" in window._last_alert_at

    window.record("conv-new", 60, now=2000.0)
    assert "stale" not in window._last_alert_at


def test_resolvers_degrade_on_missing_fields():
    """request 缺 state / tool_call / config 时解析器返回空串而不是抛异常。"""
    bare = SimpleNamespace()
    assert tool_call_rate_alert.resolve_conversation_id(bare) == ""
    assert tool_call_rate_alert.resolve_user_id(bare) == ""
    assert tool_call_rate_alert.resolve_tool_name(bare) == ""

    # config 存在但不是映射（如误传字符串）
    not_a_mapping = SimpleNamespace(state={}, tool_call={}, runtime=SimpleNamespace(config="oops"))
    assert tool_call_rate_alert.resolve_conversation_id(not_a_mapping) == ""

    # config 是映射但缺 configurable
    without_configurable = SimpleNamespace(state={}, tool_call={}, runtime=SimpleNamespace(config={}))
    assert tool_call_rate_alert.resolve_conversation_id(without_configurable) == ""


def test_resolvers_fall_back_when_state_values_are_blank():
    """state 里有 key 但值为空/空白时不当作有效会话标识。"""
    blank = SimpleNamespace(
        state={"conversation_id": "   ", "user_id": None},
        tool_call={"name": None},
        runtime=None,
    )
    assert tool_call_rate_alert.resolve_conversation_id(blank) == ""
    assert tool_call_rate_alert.resolve_user_id(blank) == ""
    assert tool_call_rate_alert.resolve_tool_name(blank) == ""


# --------------------------- 告警入口契约 ---------------------------


def test_alarm_entry_exposes_platform_contract_fields(monkeypatch):
    """alarm_id / error_code 属 OMS 平台契约，ext1 带会话与阈值上下文。"""
    captured = {}
    monkeypatch.setattr(alarm_module, "emit_alarm", lambda message, **kwargs: captured.update({"message": message, **kwargs}))

    alarm_module.emit_tool_call_rate_alarm(
        conversation_id="conv-x",
        count=101,
        threshold=100,
        window_seconds=60,
        tool_name="run_sql",
        user_id="u-1",
    )

    assert captured["alarm_id"] == "TOOL_CALL_RATE_EXCEEDED"
    assert captured["error_code"] == alarm_module.TOOL_RATE_CONVERSATION_EXCEEDED
    assert captured["request_type"] == "TOOL"
    assert captured["message"] == "单会话工具调用频率超限"
    assert captured["ext1"] == {
        "conversation_id": "conv-x",
        "count": 101,
        "threshold": 100,
        "window_seconds": 60,
    }
    assert captured["ext2"] == {"last_tool_name": "run_sql", "user_id": "u-1"}
