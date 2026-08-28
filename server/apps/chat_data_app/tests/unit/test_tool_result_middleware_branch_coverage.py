"""tool_result.py 分支补测。

缺口集中在三类：
1. 纯工具函数（`_clip_text_to_chars` / `_tool_message_text` / `_read_tool_recovery` /
   `_request_tool_args` / `_required_state_value` / int 兜底 / `_replace_tool_message`）。
2. middleware 入口的非 ToolMessage 与 error 状态短路。
3. `semantic_search` 落盘成功/失败与体积阈值交叉出的四个出口，以及查询失败态。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage, ToolMessage

from apps.chat_data_app.agent import tool_results
from apps.chat_data_app.agent.middleware import tool_result
from apps.chat_data_app.services.agent_run import conversation_workspace
from apps.chat_data_app.agent.workspace import runtime as workspace_runtime_module


def _request(
    *,
    conversation_id: str = "conv_a",
    user_id: str = "user_a",
    workspace_root: str | None = None,
    tool_call: object = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        state={
            "conversation_id": conversation_id,
            "user_id": user_id,
            "messages": [HumanMessage(content="查询结果")],
            "workspace": {
                "workspace_root": workspace_root or "",
                "workspace_backend": "local",
            },
        },
        tool_call=tool_call,
    )


def _workspace_request(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace_runtime_module, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(workspace_runtime_module, "THREADS_DIR", tmp_path / "threads")
    paths = conversation_workspace.ensure_conversation_workspace("conv_a", "user_a")
    return _request(workspace_root=paths["root"]), paths


def _query_tool_message(query_result: dict) -> ToolMessage:
    return ToolMessage(
        content=tool_results.build_internal_query_result(
            query_result, query_mode="dsl2sql", confidence=0.9
        ),
        name="semantic_query_tool",
        tool_call_id="call_query",
    )


def _fail_write(monkeypatch):
    async def fail_write_tool_result(self, **_kwargs):
        raise OSError("storage unavailable")

    monkeypatch.setattr(
        conversation_workspace.ConversationWorkspaceRuntime,
        "write_tool_result",
        fail_write_tool_result,
    )


# ---------------------------------------------------------------------------
# 纯工具函数
# ---------------------------------------------------------------------------


def test_clip_text_to_chars_edge_cases():
    clip = tool_result._clip_text_to_chars
    assert clip("", 10) == ""                       # 空内容原样返回
    assert clip("abcdef", 0) == ""                  # 预算为 0
    assert clip("abcdef", -5) == ""
    assert clip("abcdef", 100) == "abcdef"          # 未超预算
    # 预算小到放不下省略标记：直接按字符硬截
    assert clip("x" * 1000, 10) == "x" * 10
    # 预算足够：首尾各留一部分，中间插入省略标记
    clipped = clip("H" * 400 + "T" * 400, 200)
    assert clipped.startswith("H") and clipped.endswith("T")
    assert "[... 工具结果已截断 ...]" in clipped
    assert len(clipped) <= 200


def test_tool_message_text_returns_none_for_unsupported_types():
    assert tool_result._tool_message_text("文本") == "文本"
    assert tool_result._tool_message_text({"a": 1}) == '{"a": 1}'
    assert tool_result._tool_message_text([1, 2]) == "[1, 2]"
    assert tool_result._tool_message_text(object()) is None
    assert tool_result._tool_message_text(None) is None


def test_read_tool_recovery_only_supports_paginated_reads_with_path():
    # 非分页读取工具：不产出恢复信息
    assert tool_result._read_tool_recovery("calculator_tool", _request(), "结果") is None
    # file_read 但没有 file_path：无法定位结果
    assert (
        tool_result._read_tool_recovery(
            "file_read", _request(tool_call={"args": {}}), "结果"
        )
        is None
    )

    recovery = tool_result._read_tool_recovery(
        "file_read",
        _request(tool_call={"args": {"file_path": "/mnt/user-data/workspace/a.txt", "offset": -3, "limit": "bad"}}),
        "结果内容",
    )
    assert recovery["result_path"] == "/mnt/user-data/workspace/a.txt"
    assert recovery["offset"] == 0          # 负数兜底为 0
    assert recovery["limit"] == 200         # 非法值兜底为默认 200
    assert recovery["result_size_chars"] == len("结果内容")


def test_request_tool_args_ignores_unusable_tool_call():
    assert tool_result._request_tool_args(_request(tool_call=None)) == {}
    assert tool_result._request_tool_args(_request(tool_call="not-a-dict")) == {}
    assert tool_result._request_tool_args(_request(tool_call={"args": "not-a-dict"})) == {}


def test_required_state_value_rejects_blank_fields():
    assert tool_result._required_state_value(_request(), "user_id") == "user_a"
    with pytest.raises(ValueError, match="缺少工具结果归属字段: user_id"):
        tool_result._required_state_value(_request(user_id="   "), "user_id")


def test_replace_tool_message_without_updates_returns_same_instance():
    message = ToolMessage(content="原文", name="t", tool_call_id="c")
    assert tool_result._replace_tool_message(message) is message


# ---------------------------------------------------------------------------
# 落盘前置校验
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_private_tool_result_requires_workspace_root():
    with pytest.raises(ValueError, match="缺少会话工作区"):
        await tool_result._write_private_tool_result(
            content="结果", suffix=".txt", request=_request()
        )


# ---------------------------------------------------------------------------
# _finalize_tool_message 分支
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_returns_message_when_content_is_not_serializable():
    message = ToolMessage(content=42, name="calculator_tool", tool_call_id="c")
    assert await tool_result._finalize_tool_message(message, _request()) is message


@pytest.mark.asyncio
async def test_semantic_search_small_result_keeps_content_when_persist_fails(monkeypatch, tmp_path):
    """落盘失败但结果不大 → 不截断，原样放行。"""
    request, _paths = _workspace_request(monkeypatch, tmp_path)
    _fail_write(monkeypatch)
    monkeypatch.setattr(tool_result, "TOOL_RESULT_OFFLOAD_SIZE", 10_000)
    message = ToolMessage(content="很小的检索结果", name="semantic_search", tool_call_id="c")

    result = await tool_result._finalize_tool_message(message, request)

    assert result is message
    assert result.artifact is None


@pytest.mark.asyncio
async def test_semantic_search_small_result_attaches_recovery_when_persisted(monkeypatch, tmp_path):
    """落盘成功但结果不大 → 内容留在上下文，只补一个恢复引用。"""
    request, _paths = _workspace_request(monkeypatch, tmp_path)
    monkeypatch.setattr(tool_result, "TOOL_RESULT_OFFLOAD_SIZE", 10_000)
    message = ToolMessage(content="很小的检索结果", name="semantic_search", tool_call_id="c")

    result = await tool_result._finalize_tool_message(message, request)

    assert result.content == "很小的检索结果"
    recovery = result.artifact["chatdata_tool_result_recovery"]
    assert recovery["source_tool"] == "semantic_search"
    assert recovery["result_size_chars"] == len("很小的检索结果")


@pytest.mark.asyncio
async def test_query_failure_returns_structured_error_with_sql(monkeypatch, tmp_path):
    """查询未成功：带上 SQL 与错误信息，不做落盘。"""
    request, _paths = _workspace_request(monkeypatch, tmp_path)
    message = _query_tool_message({
        "status": "failed",
        "sql": "select bad from t",
        "error": "列不存在",
    })

    result = await tool_result._finalize_tool_message(message, request)
    payload = json.loads(result.content)

    assert payload["status"] == "failed"
    assert payload["error"] == "列不存在"
    assert payload["meta"]["sql"] == "select bad from t"
    assert payload["meta"]["query_mode"] == "dsl2sql"
    assert result.artifact is None


@pytest.mark.asyncio
async def test_query_success_without_sql_omits_sql_from_meta(monkeypatch, tmp_path):
    """成功但无 SQL（如缓存命中）→ meta 里不应出现 sql 字段。"""
    request, _paths = _workspace_request(monkeypatch, tmp_path)
    message = _query_tool_message({
        "status": "succeeded",
        "results": [{"status": "succeeded", "data": [{"value": 1}]}],
        "total_count": 1,
    })

    result = await tool_result._finalize_tool_message(message, request)
    payload = json.loads(result.content)

    assert payload["status"] == "succeeded"
    assert "sql" not in payload["meta"]
    assert payload["meta"]["result_id"]


# ---------------------------------------------------------------------------
# middleware 入口
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_result_middleware_short_circuits_non_finalizable_results():
    middleware = tool_result.create_tool_result_middleware()

    async def _handler(_request):
        return "纯字符串结果"

    assert await middleware.awrap_tool_call(_request(), _handler) == "纯字符串结果"


@pytest.mark.asyncio
async def test_tool_result_middleware_skips_error_tool_messages():
    middleware = tool_result.create_tool_result_middleware()
    failed = ToolMessage(content="执行失败", name="t", tool_call_id="c", status="error")

    async def _handler(_request):
        return failed

    assert await middleware.awrap_tool_call(_request(), _handler) is failed


@pytest.mark.asyncio
async def test_tool_result_middleware_finalizes_successful_tool_messages(monkeypatch, tmp_path):
    request, _paths = _workspace_request(monkeypatch, tmp_path)
    monkeypatch.setattr(tool_result, "TOOL_RESULT_OFFLOAD_SIZE", 10)
    message = ToolMessage(content="x" * 200, name="calculator_tool", tool_call_id="c")

    async def _handler(_request):
        return message

    result = await tool_result.create_tool_result_middleware().awrap_tool_call(request, _handler)

    assert result.content.startswith("[tool_result_offloaded]\n")
    assert result is not message
