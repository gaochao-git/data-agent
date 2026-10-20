"""划选引用（quoted_snippets）流式链路穿透测试。

与单元件测试（test_quoted_snippets.py）分层：本文件从公开入口驱动——
_generate_chat_data_stream 的拆包/re-wrap 透传与排队消息采纳点的
引用重解析，专防「单元件都对、组装处断线」的 wiring 断点回归
（2026-10-20 P0：re-wrap 漏带 quoted_snippets 导致普通发送引用失效）。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")

from types import SimpleNamespace

import pytest

from apps.chat_data_app import agent_stream
from apps.chat_data_app.agent.run.chat_data_stream_request import ChatDataStreamRequest
from apps.chat_data_app.agent_stream import _generate_chat_data_stream
from apps.chat_data_app.tests.unit.agent_stream_stubs import (
    _collect_async_iter,
    _fake_checkpointer,
    _FakeGraph,
    _FakeMessageChunk,
    _stub_semantic_conversation_context,  # noqa: F401  autouse fixture，导入即生效
)


@pytest.mark.asyncio
async def test_resolve_inbox_message_references_resolves_refs(tmp_path, monkeypatch):
    """排队消息采纳点：引用校验后追加引用短句、附件解析为占位行与 vision 标记。"""

    from langchain_core.messages import HumanMessage

    root = tmp_path / "user-data"
    (root / "uploads").mkdir(parents=True)
    (root / "uploads" / "a.pdf").write_text("data", encoding="utf-8")
    root_str = str(root)

    # 无随行元数据的消息原样返回（快速路径，不触任何 IO）
    plain = HumanMessage(id="m0", content="纯文本", additional_kwargs={"timestamp": "t"})
    resolved = await agent_stream._resolve_inbox_message_references(
        plain,
        workspace_root=root_str,
        user_id="u1",
        thread_id="conv-1",
    )
    assert resolved is plain

    # 引用 + 附件：短句/占位行进正文，kwargs 留痕与普通发送同构
    async def fake_resolve_file_attachments(file_ids, *, user_id, thread_id):
        assert file_ids == ["file-1"]
        assert user_id == "u1" and thread_id == "conv-1"
        return [
            {
                "file_id": "file-1",
                "original_filename": "b.md",
                "file_type": "md",
                "file_size": 3,
                "virtual_path": "/mnt/user-data/uploads/b.md",
            }
        ]

    monkeypatch.setattr(agent_stream, "_resolve_file_attachments", fake_resolve_file_attachments)
    message = HumanMessage(
        id="m1",
        content="排队消息",
        additional_kwargs={
            "timestamp": "t",
            "file_selections": ["uploads/a.pdf", "uploads/missing.pdf", "../../etc/passwd"],
            "file_ids": ["file-1"],
        },
    )
    resolved = await agent_stream._resolve_inbox_message_references(
        message,
        workspace_root=root_str,
        user_id="u1",
        thread_id="conv-1",
    )
    # 正文 = 原文 + 引用短句（只含校验通过的）+ 附件占位行，与普通发送同构
    assert resolved.content == (
        "排队消息\n\n[引用文件：/mnt/user-data/uploads/a.pdf]\n[attachment] /mnt/user-data/uploads/b.md"
    )
    assert resolved.additional_kwargs["file_selections"] == ["uploads/a.pdf"]
    # md 非图片：不打 vision 标记
    assert "vision_attachments" not in resolved.additional_kwargs
    assert resolved.id == "m1"

    # 图片附件：vision 标记进 kwargs，供 ImageInjectionMiddleware 装载
    async def fake_image_attachments(file_ids, *, user_id, thread_id):
        return [
            {
                "file_id": "file-img",
                "original_filename": "shot.png",
                "file_type": "png",
                "file_size": 10,
                "virtual_path": "/mnt/user-data/uploads/shot.png",
            }
        ]

    monkeypatch.setattr(agent_stream, "_resolve_file_attachments", fake_image_attachments)
    image_message = HumanMessage(
        id="m2",
        content="看图",
        additional_kwargs={"file_ids": ["file-img"]},
    )
    resolved = await agent_stream._resolve_inbox_message_references(
        image_message,
        workspace_root=root_str,
        user_id="u1",
        thread_id="conv-1",
    )
    assert resolved.additional_kwargs["vision_attachments"] == ["/mnt/user-data/uploads/shot.png"]
    assert "[attachment] /mnt/user-data/uploads/shot.png" in resolved.content

    # 划选引用：纯文本片段无外部资源可校验，重解析归一后留痕 + 标记块进正文
    quote_only = HumanMessage(
        id="m3",
        content="这个结论怎么得出的",
        additional_kwargs={
            "timestamp": "t",
            "quoted_snippets": [{"text": " 户表与订单表通过用户ID关联（一对多） "}, {"other": 1}],
        },
    )
    resolved = await agent_stream._resolve_inbox_message_references(
        quote_only,
        workspace_root=root_str,
        user_id="u1",
        thread_id="conv-1",
    )
    assert "[引用助手回复片段开始]\n户表与订单表通过用户ID关联（一对多）\n[引用助手回复片段结束]" in resolved.content
    assert resolved.additional_kwargs["quoted_snippets"] == [{"text": "户表与订单表通过用户ID关联（一对多）"}]


def test_generate_chat_data_stream_passes_quoted_snippets_to_inner(monkeypatch, tmp_path):
    """划选引用：quoted_snippets 必须经生成器 re-wrap 透传到内层。

    回归防线：_generate_chat_data_stream 拆包重建 ChatDataStreamRequest 时若漏带
    quoted_snippets，内层 parse 永远拿到 None——模型看不到标记块、
    additional_kwargs 不留痕（历史回放 chip 消失）。本用例从公开生成器入口
    驱动到消息断言，专防这类 wiring 断点。
    """
    import asyncio

    workspace_root = tmp_path / "user-data"
    (workspace_root / "uploads").mkdir(parents=True)
    (workspace_root / "outputs" / "query_result").mkdir(parents=True)
    (workspace_root / "outputs" / "query_result" / "r.json").write_text("{}", encoding="utf-8")

    async def fake_prepare_workspace(*_args, **_kwargs):
        return {"root": str(workspace_root)}

    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="已结合引用片段回答"), {"langgraph_node": "model"})],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.agent_stream.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.agent_run_support.prepare_conversation_workspace", fake_prepare_workspace)
    monkeypatch.setattr("apps.chat_data_app.agent_stream.create_data_agent", lambda *_args, **_kwargs: fake_graph)

    events = asyncio.run(
        _collect_async_iter(
            _generate_chat_data_stream(
                ChatDataStreamRequest(
                    user_id="u_test",
                    thread_id="conv_quoted_snippets",
                    user_query="这个结论怎么得出的",
                    datasource_id="ds_a",
                    quoted_snippets=[{"text": "户表与订单表通过用户ID关联（一对多）"}],
                )
            )
        )
    )

    joined = "".join(events)
    assert "已结合引用片段回答" in joined
    human_message = fake_graph.initial_state["messages"][0]
    # 模型可见标记块进入正文；additional_kwargs 留痕供事件表持久化（历史回放渲染 chip）
    assert (
        "[引用助手回复片段开始]\n户表与订单表通过用户ID关联（一对多）\n[引用助手回复片段结束]" in human_message.content
    )
    assert human_message.additional_kwargs["quoted_snippets"] == [{"text": "户表与订单表通过用户ID关联（一对多）"}]
