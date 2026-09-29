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
from apps.chat_data_app.agent.workspace import runtime as workspace_runtime_module
from apps.chat_data_app.services.agent_run import conversation_workspace


def _request(
    *,
    conversation_id: str = "conv_a",
    user_id: str = "user_a",
    workspace_root: str | None = None,
):
    return SimpleNamespace(state={
        "conversation_id": conversation_id,
        "user_id": user_id,
        "messages": [HumanMessage(content="查询结果")],
        "workspace": {
            "workspace_root": workspace_root or "",
            "workspace_backend": "local",
        },
    })


def _workspace_request(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace_runtime_module, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setattr("utils.storage_paths.THREADS_DIR", tmp_path / "threads")
    paths = conversation_workspace.ensure_conversation_workspace("conv_a", "user_a")
    return _request(workspace_root=paths["root"]), paths


def _query_tool_message(query_result: dict) -> ToolMessage:
    return ToolMessage(
        content=tool_results.build_internal_query_result(
            query_result,
            query_mode="dsl2sql",
        ),
        name="semantic_query_tool",
        tool_call_id="call_query",
    )


@pytest.mark.asyncio
async def test_query_result_is_saved_as_reference_only(monkeypatch, tmp_path):
    request, paths = _workspace_request(monkeypatch, tmp_path)
    message = _query_tool_message({
        "status": "succeeded",
        "sql": "select 1",
        "results": [{"status": "succeeded", "data": [{"value": 1}]}],
        "total_count": 1,
    })

    result = await tool_result._finalize_tool_message(message, request)
    payload = json.loads(result.content)

    assert payload["type"] == "query_result"
    assert payload["status"] == "succeeded"
    assert payload["data"] is None
    result_id = payload["meta"]["result_id"]
    assert payload["meta"]["result_path"] == f"/mnt/user-data/outputs/query_result/{result_id}.json"
    assert json.loads(
        (Path(paths["outputs"]) / "query_result" / f"{result_id}.json").read_text(encoding="utf-8")
    )["results"][0]["data"] == [{"value": 1}]


@pytest.mark.asyncio
async def test_query_result_is_offloaded_to_hidden_outputs_store(monkeypatch, tmp_path):
    request, paths = _workspace_request(monkeypatch, tmp_path)
    monkeypatch.setattr(tool_result, "TOOL_RESULT_OFFLOAD_SIZE", 100)
    message = _query_tool_message({
        "status": "succeeded",
        "sql": "select content from report",
        "results": [{"status": "succeeded", "data": [{"content": "x" * 200}]}],
        "total_count": 1,
        "execution_time_ms": 12,
    })

    result = await tool_result._finalize_tool_message(message, request)
    payload = json.loads(result.content)
    result_id = payload["meta"]["result_id"]

    assert payload["data"] is None
    assert payload["meta"]["result_path"] == f"/mnt/user-data/outputs/query_result/{result_id}.json"
    assert payload["meta"]["content_in_context"] is False
    assert "完整结果已保存" in payload["meta"]["message"]
    saved_path = Path(paths["outputs"]) / "query_result" / f"{result_id}.json"
    assert json.loads(saved_path.read_text(encoding="utf-8"))["results"][0]["data"][0]["content"] == "x" * 200


@pytest.mark.asyncio
async def test_generic_tool_result_is_offloaded_to_hidden_outputs_text_file(monkeypatch, tmp_path):
    request, paths = _workspace_request(monkeypatch, tmp_path)
    monkeypatch.setattr(tool_result, "TOOL_RESULT_OFFLOAD_SIZE", 100)
    message = ToolMessage(content="x" * 200, name="calculator_tool", tool_call_id="call_calculator")

    result = await tool_result._finalize_tool_message(message, request)

    assert result.content.startswith("[tool_result_offloaded]\n")
    assert "source_tool=calculator_tool" in result.content
    assert "result_path=/mnt/user-data/outputs/.tool-offload/" in result.content
    assert "content_in_context=false" in result.content
    assert "file_read" not in result.content
    files = list((Path(paths["outputs"]) / ".tool-offload").glob("*.txt"))
    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8") == "x" * 200


@pytest.mark.asyncio
async def test_structured_generic_tool_result_is_serialized_before_offload(monkeypatch, tmp_path):
    request, paths = _workspace_request(monkeypatch, tmp_path)
    monkeypatch.setattr(tool_result, "TOOL_RESULT_OFFLOAD_SIZE", 100)
    content = [{"value": "x" * 200}]
    message = ToolMessage(content=content, name="calculator_tool", tool_call_id="call_structured")

    result = await tool_result._finalize_tool_message(message, request)

    assert "/mnt/user-data/outputs/.tool-offload/" in result.content
    files = list((Path(paths["outputs"]) / ".tool-offload").glob("*.txt"))
    assert len(files) == 1
    assert json.loads(files[0].read_text(encoding="utf-8")) == content


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["file_read"])
async def test_paginated_read_tools_are_not_offloaded(monkeypatch, tmp_path, tool_name):
    monkeypatch.setattr(tool_result, "TOOL_RESULT_OFFLOAD_SIZE", 100)
    message = ToolMessage(content="x" * 200, name=tool_name, tool_call_id="call_read")

    result = await tool_result._finalize_tool_message(message, _request())

    assert result is message
    assert not list(tmp_path.rglob("*.txt"))


@pytest.mark.asyncio
async def test_remaining_context_budget_does_not_change_per_result_threshold(monkeypatch, tmp_path):
    request, _paths = _workspace_request(monkeypatch, tmp_path)
    monkeypatch.setattr(tool_result, "TOOL_RESULT_OFFLOAD_SIZE", 10000)
    message = ToolMessage(content="结果内容 " * 20, name="calculator_tool", tool_call_id="call_budget")

    result = await tool_result._finalize_tool_message(message, request)

    assert result.content == message.content
    assert not list((Path(_paths["outputs"]) / ".tool-offload").glob("*"))


@pytest.mark.asyncio
async def test_query_result_storage_failure_returns_structured_delivery_error(monkeypatch, tmp_path):
    request, _paths = _workspace_request(monkeypatch, tmp_path)

    async def fail_write_tool_result(self, **_kwargs):
        raise OSError("storage unavailable")

    monkeypatch.setattr(conversation_workspace.ConversationWorkspaceRuntime, "write_tool_result", fail_write_tool_result)
    message = _query_tool_message({
        "status": "succeeded",
        "sql": "select content",
        "results": [{"status": "succeeded", "data": [{"content": "x" * 200}]}],
    })

    result = await tool_result._finalize_tool_message(message, request)
    payload = json.loads(result.content)

    assert payload == {
        "type": "query_result",
        "version": 1,
        "status": "failed",
        "data": None,
        "error": "查询已执行，但结果保存失败，当前无法展示或继续分析",
        "meta": {
            "query_mode": "dsl2sql",
            "result_persisted": False,
        },
    }
    assert result.artifact is None


@pytest.mark.asyncio
async def test_generic_storage_failure_returns_incomplete_result_marker(monkeypatch, tmp_path):
    request, _paths = _workspace_request(monkeypatch, tmp_path)

    async def fail_write_tool_result(self, **_kwargs):
        raise OSError("storage unavailable")

    monkeypatch.setattr(conversation_workspace.ConversationWorkspaceRuntime, "write_tool_result", fail_write_tool_result)
    monkeypatch.setattr(tool_result, "TOOL_RESULT_OFFLOAD_SIZE", 100)
    message = ToolMessage(content="x" * 200, name="calculator_tool", tool_call_id="call_generic")

    result = await tool_result._finalize_tool_message(message, request)

    assert result.content.startswith("[tool_result_truncated]\n")
    assert "source_tool=calculator_tool" in result.content
    assert "content_complete=false" in result.content
    assert "x" * 20 not in result.content
    assert result.artifact is None


def test_incomplete_result_marker_survives_tiny_configured_threshold(monkeypatch):
    monkeypatch.setattr(tool_result, "TOOL_RESULT_OFFLOAD_SIZE", 1)

    content = tool_result._build_truncated_tool_result_message("semantic_query_tool", 1000)

    assert content == "[tool_result_truncated]\ncontent_complete=false"
