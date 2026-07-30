import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage, ToolMessage

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "dummy")

from apps.chat_data_app.agent import tool_results
from apps.chat_data_app.agent.middleware import tool_result
from apps.chat_data_app.services.context_budget import estimate_text_tokens
from utils import tool_result_store


class _Budget:
    def __init__(self, remaining: int = 100000):
        self.remaining = remaining

    def remaining_for_tool_result(self, _messages):
        return self.remaining


def _request(*, conversation_id: str = "conv_a", user_id: str = "user_a"):
    return SimpleNamespace(state={
        "conversation_id": conversation_id,
        "user_id": user_id,
        "messages": [HumanMessage(content="查询结果")],
    })


def _query_tool_message(query_result: dict) -> ToolMessage:
    return ToolMessage(
        content=tool_results.build_internal_query_result(
            query_result,
            query_mode="dsl2sql",
            confidence=0.9,
        ),
        name="semantic_query_tool",
        tool_call_id="call_query",
    )


@pytest.mark.asyncio
async def test_query_result_is_inlined_as_public_envelope(monkeypatch, tmp_path):
    monkeypatch.setattr(tool_result_store, "CHAT_DATA_HOME", str(tmp_path))
    message = _query_tool_message({
        "status": "succeeded",
        "sql": "select 1",
        "results": [{"status": "succeeded", "data": [{"value": 1}]}],
        "total_count": 1,
    })

    result = await tool_result._finalize_tool_message(message, _request(), _Budget())
    payload = json.loads(result.content)

    assert payload["type"] == "query_result"
    assert payload["status"] == "succeeded"
    assert payload["data"]["results"][0]["data"] == [{"value": 1}]
    assert payload["meta"] == {"query_mode": "dsl2sql", "confidence": 0.9}
    assert not list(tmp_path.rglob("*.json"))


@pytest.mark.asyncio
async def test_query_result_is_offloaded_to_hidden_outputs_store(monkeypatch, tmp_path):
    monkeypatch.setattr(tool_result_store, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(tool_result, "TOOL_RESULT_OFFLOAD_SIZE", 100)
    message = _query_tool_message({
        "status": "succeeded",
        "sql": "select content from report",
        "results": [{"status": "succeeded", "data": [{"content": "x" * 200}]}],
        "total_count": 1,
        "execution_time_ms": 12,
    })

    result = await tool_result._finalize_tool_message(message, _request(), _Budget())
    payload = json.loads(result.content)
    result_id = payload["meta"]["result_id"]

    assert payload["data"] is None
    assert payload["meta"]["result_path"] == f"/mnt/user-data/outputs/.tool-results/{result_id}.json"
    assert payload["meta"]["llm_analysis_skipped"] is True
    saved_path = tmp_path / "threads" / "user_a" / "conv_a" / "user-data" / "outputs" / ".tool-results" / f"{result_id}.json"
    assert json.loads(saved_path.read_text(encoding="utf-8"))["results"][0]["data"][0]["content"] == "x" * 200


@pytest.mark.asyncio
async def test_generic_tool_result_is_offloaded_to_hidden_outputs_text_file(monkeypatch, tmp_path):
    monkeypatch.setattr(tool_result_store, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(tool_result, "TOOL_RESULT_OFFLOAD_SIZE", 100)
    message = ToolMessage(content="x" * 200, name="calculator_tool", tool_call_id="call_calculator")

    result = await tool_result._finalize_tool_message(message, _request(), _Budget())

    assert "/mnt/user-data/outputs/.tool-results/" in result.content
    assert "file_read 按行分段读取" in result.content
    files = list((tmp_path / "threads" / "user_a" / "conv_a" / "user-data" / "outputs" / ".tool-results").glob("*.txt"))
    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8") == "x" * 200


@pytest.mark.asyncio
async def test_structured_generic_tool_result_is_serialized_before_offload(monkeypatch, tmp_path):
    monkeypatch.setattr(tool_result_store, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(tool_result, "TOOL_RESULT_OFFLOAD_SIZE", 100)
    content = [{"value": "x" * 200}]
    message = ToolMessage(content=content, name="calculator_tool", tool_call_id="call_structured")

    result = await tool_result._finalize_tool_message(message, _request(), _Budget())

    assert "/mnt/user-data/outputs/.tool-results/" in result.content
    files = list((tmp_path / "threads" / "user_a" / "conv_a" / "user-data" / "outputs" / ".tool-results").glob("*.txt"))
    assert len(files) == 1
    assert json.loads(files[0].read_text(encoding="utf-8")) == content


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["file_read", "skill_load"])
async def test_paginated_read_tools_are_not_offloaded(monkeypatch, tmp_path, tool_name):
    monkeypatch.setattr(tool_result_store, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(tool_result, "TOOL_RESULT_OFFLOAD_SIZE", 100)
    message = ToolMessage(content="x" * 200, name=tool_name, tool_call_id="call_read")

    result = await tool_result._finalize_tool_message(message, _request(), _Budget())

    assert result is message
    assert not list(tmp_path.rglob("*.txt"))


@pytest.mark.asyncio
async def test_remaining_context_budget_triggers_offload_below_static_threshold(monkeypatch, tmp_path):
    monkeypatch.setattr(tool_result_store, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(tool_result, "TOOL_RESULT_OFFLOAD_SIZE", 10000)
    message = ToolMessage(content="结果内容 " * 20, name="calculator_tool", tool_call_id="call_budget")

    result = await tool_result._finalize_tool_message(message, _request(), _Budget(remaining=520))

    assert "/mnt/user-data/outputs/.tool-results/" in result.content


@pytest.mark.asyncio
async def test_storage_failure_returns_bounded_query_preview(monkeypatch):
    class _FailingStore:
        def save_query_result(self, *_args, **_kwargs):
            raise OSError("storage unavailable")

    monkeypatch.setattr(tool_result, "ToolResultStore", _FailingStore)
    monkeypatch.setattr(tool_result, "TOOL_RESULT_OFFLOAD_SIZE", 100)
    message = _query_tool_message({
        "status": "succeeded",
        "sql": "select content",
        "results": [{"status": "succeeded", "data": [{"content": "x" * 200}]}],
    })

    result = await tool_result._finalize_tool_message(message, _request(), _Budget(remaining=550))
    payload = json.loads(result.content)

    assert payload["data"] is None
    assert payload["meta"]["result_externalization_failed"] is True
    assert estimate_text_tokens(payload["meta"]["preview"]) <= 38
