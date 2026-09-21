"""parse_document 工具测试——覆盖路径校验、解析/写入异常与成功路径。"""

from __future__ import annotations

import json

import pytest

from apps.chat_data_app.agent.tools import parse_document as parse_document_module
from apps.chat_data_app.agent.tools.parse_document import create_parse_document_tool
from apps.chat_data_app.agent.workspace.runtime import ConversationWorkspaceRuntime


def _make_runtime(tmp_path) -> ConversationWorkspaceRuntime:
    root = tmp_path / "ws"
    (root / "uploads").mkdir(parents=True)
    (root / "workspace").mkdir(parents=True)
    (root / "outputs").mkdir(parents=True)
    (root / "uploads" / "a.txt").write_text("hello", encoding="utf-8")
    return ConversationWorkspaceRuntime(workspace_root=str(root), user_id="u1", backend="local")


def _payload(result: str) -> dict:
    """工具返回形如 ``[parse_document]\n{json}``，取出行 JSON 部分。"""
    return json.loads(result.split("\n", 1)[1])


def _ok_parse(_path):
    async def _inner(path):
        return "# parsed"

    return _inner(path=None)


@pytest.mark.asyncio
async def test_parse_document_success(tmp_path, monkeypatch):
    runtime = _make_runtime(tmp_path)

    async def _fake_parse(path):
        assert path.name == "a.txt"
        return "# parsed"

    monkeypatch.setattr(
        parse_document_module, "parse_uploaded_file_to_markdown", _fake_parse
    )
    tool = create_parse_document_tool(runtime)[0]
    result = await tool.ainvoke(
        {
            "source_path": "/mnt/user-data/uploads/a.txt",
            "target_path": "/mnt/user-data/workspace/out.md",
        }
    )
    payload = _payload(result)
    assert payload["success"] is True
    assert payload["char_count"] == len("# parsed")
    assert payload["target_path"].endswith("out.md")


@pytest.mark.asyncio
async def test_parse_document_success_falls_back_to_target_path(tmp_path, monkeypatch):
    """运行时未回传 file_path 时，用调用方给定的 target_path。"""
    runtime = _make_runtime(tmp_path)

    async def _fake_parse(path):
        return "content"

    async def _fake_write(*, file_path, content, mode):
        return {}

    monkeypatch.setattr(
        parse_document_module, "parse_uploaded_file_to_markdown", _fake_parse
    )
    monkeypatch.setattr(runtime, "write_text_file", _fake_write)
    tool = create_parse_document_tool(runtime)[0]
    result = await tool.ainvoke(
        {
            "source_path": "/mnt/user-data/uploads/a.txt",
            "target_path": "/mnt/user-data/workspace/out.md",
        }
    )
    payload = _payload(result)
    assert payload["success"] is True
    assert payload["target_path"] == "/mnt/user-data/workspace/out.md"


@pytest.mark.asyncio
async def test_parse_document_rejects_invalid_source_path(tmp_path):
    runtime = _make_runtime(tmp_path)
    tool = create_parse_document_tool(runtime)[0]
    result = await tool.ainvoke(
        {
            "source_path": "/etc/passwd",
            "target_path": "/mnt/user-data/workspace/out.md",
        }
    )
    payload = _payload(result)
    assert payload["success"] is False
    assert "source_path 无效" in payload["error"]


@pytest.mark.asyncio
async def test_parse_document_rejects_source_outside_uploads(tmp_path):
    runtime = _make_runtime(tmp_path)
    tool = create_parse_document_tool(runtime)[0]
    result = await tool.ainvoke(
        {
            "source_path": "/mnt/user-data/workspace/a.txt",
            "target_path": "/mnt/user-data/workspace/out.md",
        }
    )
    payload = _payload(result)
    assert payload["success"] is False
    assert "uploads" in payload["error"]


@pytest.mark.asyncio
async def test_parse_document_rejects_missing_file(tmp_path):
    runtime = _make_runtime(tmp_path)
    tool = create_parse_document_tool(runtime)[0]
    result = await tool.ainvoke(
        {
            "source_path": "/mnt/user-data/uploads/missing.txt",
            "target_path": "/mnt/user-data/workspace/out.md",
        }
    )
    payload = _payload(result)
    assert payload["success"] is False
    assert "不存在" in payload["error"]


@pytest.mark.asyncio
async def test_parse_document_parse_failure(tmp_path, monkeypatch):
    runtime = _make_runtime(tmp_path)

    async def _boom(path):
        raise RuntimeError("parse exploded")

    monkeypatch.setattr(parse_document_module, "parse_uploaded_file_to_markdown", _boom)
    tool = create_parse_document_tool(runtime)[0]
    result = await tool.ainvoke(
        {
            "source_path": "/mnt/user-data/uploads/a.txt",
            "target_path": "/mnt/user-data/workspace/out.md",
        }
    )
    payload = _payload(result)
    assert payload["success"] is False
    assert "解析失败" in payload["error"]


@pytest.mark.asyncio
async def test_parse_document_write_failure(tmp_path, monkeypatch):
    """目标落在只读的 uploads 区，写入阶段应被兜底为失败结果。"""
    runtime = _make_runtime(tmp_path)

    async def _fake_parse(path):
        return "content"

    monkeypatch.setattr(
        parse_document_module, "parse_uploaded_file_to_markdown", _fake_parse
    )
    tool = create_parse_document_tool(runtime)[0]
    result = await tool.ainvoke(
        {
            "source_path": "/mnt/user-data/uploads/a.txt",
            "target_path": "/mnt/user-data/uploads/out.md",
        }
    )
    payload = _payload(result)
    assert payload["success"] is False
    assert "写入失败" in payload["error"]
