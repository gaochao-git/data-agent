import base64
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "dummy")

from apps.chat_data_app.agent.middleware import image_injection
from apps.chat_data_app.agent.tools import read_image as read_image_module
from apps.chat_data_app.services.agent_run import conversation_workspace


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def _init_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(conversation_workspace, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(conversation_workspace, "THREADS_DIR", tmp_path / "threads")
    return conversation_workspace.ensure_conversation_workspace("conv_a", "user_a")


def _payload(tool_result: str) -> dict:
    """去掉工具回执的 ``[read_image]`` 前缀行后解析 JSON。"""
    _, _, body = tool_result.partition("\n")
    return json.loads(body)


def _runtime(paths):
    return conversation_workspace.ConversationWorkspaceRuntime(
        workspace_root=paths["root"],
        user_id="user_a",
        backend="local",
    )


def _request(paths):
    return SimpleNamespace(
        state={
            "conversation_id": "conv_a",
            "user_id": "user_a",
            "workspace": {"workspace_root": paths["root"], "workspace_backend": "local"},
        },
    )


def _read_image_exchange(file_path: str, *, call_id: str = "call_img"):
    """构造一轮 AI tool_calls + read_image ToolMessage。"""
    ai_message = AIMessage(
        content="",
        tool_calls=[{"id": call_id, "name": "read_image", "args": {"file_path": file_path}}],
    )
    tool_message = ToolMessage(
        content=f'[read_image]\n{{"success": true, "file_path": "{file_path}"}}',
        name="read_image",
        tool_call_id=call_id,
    )
    return [ai_message, tool_message]


# ---------------------------------------------------------------------------
# 工具本身
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_read_image_tool_success(monkeypatch, tmp_path):
    paths = _init_workspace(monkeypatch, tmp_path)
    (Path(paths["uploads"]) / "chart.png").write_bytes(PNG_BYTES)

    tool = read_image_module.create_read_image_tool(_runtime(paths))[0]
    result = _payload(await tool.ainvoke({"file_path": "/mnt/user-data/uploads/chart.png"}))

    assert result["success"] is True
    assert result["file_path"].endswith("/chart.png")
    assert result["size_bytes"] == len(PNG_BYTES)


@pytest.mark.asyncio
async def test_read_image_tool_rejects_non_image(monkeypatch, tmp_path):
    paths = _init_workspace(monkeypatch, tmp_path)
    (Path(paths["uploads"]) / "data.txt").write_text("hello")

    tool = read_image_module.create_read_image_tool(_runtime(paths))[0]
    result = _payload(await tool.ainvoke({"file_path": "/mnt/user-data/uploads/data.txt"}))

    assert result["success"] is False
    assert "不支持的图片格式" in result["error"]


@pytest.mark.asyncio
async def test_read_image_tool_rejects_missing_file_and_escape(monkeypatch, tmp_path):
    paths = _init_workspace(monkeypatch, tmp_path)
    runtime = _runtime(paths)
    tool = read_image_module.create_read_image_tool(runtime)[0]

    missing = _payload(await tool.ainvoke({"file_path": "/mnt/user-data/uploads/nope.png"}))
    assert missing["success"] is False
    assert "不存在" in missing["error"]

    outside = _payload(await tool.ainvoke({"file_path": "/etc/passwd"}))
    assert outside["success"] is False


@pytest.mark.asyncio
async def test_read_image_tool_rejects_oversize(monkeypatch, tmp_path):
    paths = _init_workspace(monkeypatch, tmp_path)
    big_path = Path(paths["workspace"]) / "big.png"
    big_path.write_bytes(b"0" * (5 * 1024 * 1024 + 1))

    tool = read_image_module.create_read_image_tool(_runtime(paths))[0]
    result = _payload(await tool.ainvoke({"file_path": "/mnt/user-data/workspace/big.png"}))

    assert result["success"] is False
    assert "大小上限" in result["error"]


# ---------------------------------------------------------------------------
# 注入中间件
# ---------------------------------------------------------------------------

def _middleware(model_id="qwen-test", monkeypatch=None, *, vlm=True):
    if monkeypatch is not None:
        monkeypatch.setattr(
            image_injection,
            "get_chat_data_model_config",
            lambda _model_id: {"vlm": vlm},
        )
    return image_injection.create_image_injection_middleware(model_id)


def _handler_capturing(captured):
    async def handler(request):
        captured["request"] = request
        return "ok"
    return handler


@pytest.mark.asyncio
async def test_injection_appends_hidden_human_message(monkeypatch, tmp_path):
    paths = _init_workspace(monkeypatch, tmp_path)
    (Path(paths["outputs"]) / "report.png").write_bytes(PNG_BYTES)
    request = _request(paths)
    request.messages = [
        HumanMessage(content="帮我看看这张图"),
        *_read_image_exchange("/mnt/user-data/outputs/report.png"),
    ]

    captured = {}
    response = await _middleware(monkeypatch=monkeypatch).awrap_model_call(request, _handler_capturing(captured))

    assert response == "ok"
    sent = captured["request"].messages
    injected = sent[-1]
    assert isinstance(injected, HumanMessage)
    assert injected.additional_kwargs.get("image_injection") is True
    assert injected.additional_kwargs.get("hide_from_ui") is True
    blocks = injected.content
    assert isinstance(blocks, list) and blocks[0]["type"] == "text"
    expected_url = "data:image/png;base64," + base64.b64encode(PNG_BYTES).decode("ascii")
    assert blocks[1]["image_url"]["url"] == expected_url


@pytest.mark.asyncio
async def test_injection_skipped_without_read_image_messages(monkeypatch, tmp_path):
    paths = _init_workspace(monkeypatch, tmp_path)
    request = _request(paths)
    request.messages = [HumanMessage(content="普通问题")]

    captured = {}
    await _middleware(monkeypatch=monkeypatch).awrap_model_call(request, _handler_capturing(captured))

    # 无注入消息时请求原样透传（不重建 messages）
    assert captured["request"].messages == request.messages


@pytest.mark.asyncio
async def test_injection_vlm_off_rewrites_tool_result_only(monkeypatch, tmp_path):
    paths = _init_workspace(monkeypatch, tmp_path)
    (Path(paths["uploads"]) / "a.png").write_bytes(PNG_BYTES)
    request = _request(paths)
    exchange = _read_image_exchange("/mnt/user-data/uploads/a.png")
    original_content = exchange[1].content
    request.messages = [HumanMessage(content="看图"), *exchange]

    captured = {}
    await _middleware(monkeypatch=monkeypatch, vlm=False).awrap_model_call(request, _handler_capturing(captured))

    sent = captured["request"].messages
    assert len(sent) == 3
    assert "vlm-off" in str(sent[-1].content)
    for message in sent:
        content = message.content
        assert not (isinstance(content, list) and any(
            isinstance(block, dict) and block.get("type") == "image_url" for block in content
        ))
    assert str(sent[-1].content).startswith(original_content)


@pytest.mark.asyncio
async def test_injection_notes_missing_file_instead_of_crashing(monkeypatch, tmp_path):
    paths = _init_workspace(monkeypatch, tmp_path)
    request = _request(paths)
    request.messages = [
        HumanMessage(content="看图"),
        *_read_image_exchange("/mnt/user-data/uploads/gone.png"),
    ]

    captured = {}
    await _middleware(monkeypatch=monkeypatch).awrap_model_call(request, _handler_capturing(captured))

    injected = captured["request"].messages[-1]
    assert isinstance(injected, HumanMessage)
    text_blocks = [block for block in injected.content if block.get("type") == "text"]
    image_blocks = [block for block in injected.content if block.get("type") == "image_url"]
    assert not image_blocks
    assert "读取失败" in text_blocks[0]["text"]


@pytest.mark.asyncio
async def test_injection_dedupes_same_file_and_caps_count(monkeypatch, tmp_path):
    paths = _init_workspace(monkeypatch, tmp_path)
    uploads_dir = Path(paths["uploads"])
    for index in range(6):
        (uploads_dir / f"img-{index}.png").write_bytes(PNG_BYTES + bytes([index]))

    request = _request(paths)
    messages = [HumanMessage(content="多图")]
    for index in range(6):
        messages.extend(_read_image_exchange(
            f"/mnt/user-data/uploads/img-{index}.png", call_id=f"call_{index}"
        ))
    messages.extend(_read_image_exchange(
        "/mnt/user-data/uploads/img-0.png", call_id="call_dup"
    ))
    request.messages = messages

    captured = {}
    await _middleware(monkeypatch=monkeypatch).awrap_model_call(request, _handler_capturing(captured))

    injected = captured["request"].messages[-1]
    image_blocks = [block for block in injected.content if block.get("type") == "image_url"]
    urls = [block["image_url"]["url"] for block in image_blocks]
    assert len(image_blocks) == image_injection.MAX_INJECTED_IMAGES
    # 去重：img-0 只出现一次；保留的是最近的几张
    assert len(urls) == len(set(urls))


# ---------------------------------------------------------------------------
# 用户上传图片附件（vision_attachments 标记来源）
# ---------------------------------------------------------------------------

def _marked_user_message(paths_list, content="看图"):
    """构造带 vision_attachments 路径标记的用户消息。"""
    return HumanMessage(
        content=content,
        additional_kwargs={"vision_attachments": list(paths_list)},
    )


@pytest.mark.asyncio
async def test_injection_loads_user_vision_attachments(monkeypatch, tmp_path):
    paths = _init_workspace(monkeypatch, tmp_path)
    (Path(paths["uploads"]) / "photo.png").write_bytes(PNG_BYTES)
    request = _request(paths)
    request.messages = [_marked_user_message(["/mnt/user-data/uploads/photo.png"])]

    captured = {}
    await _middleware(monkeypatch=monkeypatch).awrap_model_call(request, _handler_capturing(captured))

    injected = captured["request"].messages[-1]
    assert isinstance(injected, HumanMessage)
    assert injected.additional_kwargs.get("image_injection") is True
    text_blocks = [block for block in injected.content if block.get("type") == "text"]
    image_blocks = [block for block in injected.content if block.get("type") == "image_url"]
    assert "用户上传附件" in text_blocks[0]["text"]
    expected_url = "data:image/png;base64," + base64.b64encode(PNG_BYTES).decode("ascii")
    assert [block["image_url"]["url"] for block in image_blocks] == [expected_url]


@pytest.mark.asyncio
async def test_injection_user_attachments_pass_through_when_vlm_off(monkeypatch, tmp_path):
    paths = _init_workspace(monkeypatch, tmp_path)
    (Path(paths["uploads"]) / "a.png").write_bytes(PNG_BYTES)
    request = _request(paths)
    request.messages = [_marked_user_message(["/mnt/user-data/uploads/a.png"])]

    captured = {}
    await _middleware(monkeypatch=monkeypatch, vlm=False).awrap_model_call(request, _handler_capturing(captured))

    # 用户附件在 vlm-off 下静默跳过，请求原样透传
    assert captured["request"].messages == request.messages


@pytest.mark.asyncio
async def test_injection_merges_sources_dedupes_by_physical_path(monkeypatch, tmp_path):
    paths = _init_workspace(monkeypatch, tmp_path)
    (Path(paths["uploads"]) / "same.png").write_bytes(PNG_BYTES)
    request = _request(paths)
    request.messages = [
        *_read_image_exchange("/mnt/user-data/uploads/same.png"),
        _marked_user_message(["/mnt/user-data/uploads/same.png"]),
    ]

    captured = {}
    await _middleware(monkeypatch=monkeypatch).awrap_model_call(request, _handler_capturing(captured))

    injected = captured["request"].messages[-1]
    image_blocks = [block for block in injected.content if block.get("type") == "image_url"]
    # 双来源同一物理文件只注入一次，归属最新来源（用户上传）
    assert len(image_blocks) == 1
    text_blocks = [block for block in injected.content if block.get("type") == "text"]
    assert "用户上传附件" in text_blocks[0]["text"]
    assert "read_image 加载" not in text_blocks[0]["text"]


@pytest.mark.asyncio
async def test_injection_rolling_window_across_turns(monkeypatch, tmp_path):
    paths = _init_workspace(monkeypatch, tmp_path)
    uploads_dir = Path(paths["uploads"])
    for index in range(6):
        (uploads_dir / f"u-{index}.png").write_bytes(PNG_BYTES + bytes([index]))

    request = _request(paths)
    # 两轮历史消息共带 6 个标记路径，只应注入最近 4 张
    request.messages = [
        _marked_user_message([
            "/mnt/user-data/uploads/u-0.png",
            "/mnt/user-data/uploads/u-1.png",
            "/mnt/user-data/uploads/u-2.png",
        ], content="第一批"),
        AIMessage(content="收到"),
        _marked_user_message([
            "/mnt/user-data/uploads/u-3.png",
            "/mnt/user-data/uploads/u-4.png",
            "/mnt/user-data/uploads/u-5.png",
        ], content="第二批"),
    ]

    captured = {}
    await _middleware(monkeypatch=monkeypatch).awrap_model_call(request, _handler_capturing(captured))

    injected = captured["request"].messages[-1]
    image_blocks = [block for block in injected.content if block.get("type") == "image_url"]
    assert len(image_blocks) == image_injection.MAX_INJECTED_IMAGES
    text_blocks = [block for block in injected.content if block.get("type") == "text"]
    # 最新优先：第二批全部保留，最早的 u-0 被挤出窗口
    assert "u-0.png" not in text_blocks[0]["text"]
    assert "u-5.png" in text_blocks[0]["text"]
