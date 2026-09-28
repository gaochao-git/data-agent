"""image_injection 辅助函数的分支覆盖补测。"""
import base64
import os
import sys

from langchain_core.messages import AIMessage, HumanMessage

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))

from apps.chat_data_app.agent.middleware import image_injection as ii

# 1x1 透明 PNG
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def test_collect_pending_requests_skips_invalid_attachment_paths():
    """附件标记里的非字符串/空白项被跳过（104->103）。"""
    human = HumanMessage(
        "看图",
        additional_kwargs={ii.VISION_ATTACHMENTS_KEY: ["/tmp/a.png", "", 123, "   "]},
    )
    requests = ii._collect_pending_image_requests([human])
    assert requests == [(ii.USER_UPLOAD_SOURCE, "/tmp/a.png")]


def test_tool_file_paths_skips_invalid_calls():
    """tool_call 非 dict/无 id（124->125）、args 非 dict/file_path 空（128->123）都被跳过。"""
    ai = AIMessage.model_construct(
        content="",
        id="m1",
        tool_calls=[
            "bad",
            {"args": {"file_path": "/tmp/x.png"}},
            {"id": "t1", "args": "bad-args"},
            {"id": "t2", "args": {"file_path": "   "}},
            {"id": "t3", "args": {"file_path": "/tmp/ok.png"}},
        ],
    )
    paths = ii._tool_file_paths_by_call_id([ai, HumanMessage("x")])
    assert paths == {"t3": "/tmp/ok.png"}


def test_load_injection_images_records_empty_path_and_resolve_failures(tmp_path):
    """空路径给出专属失败说明（183->184），解析失败计入 failures（187->188）。"""
    blocks, sources, failures = ii._load_injection_images(
        str(tmp_path),
        [(ii.READ_IMAGE_TOOL_NAME, ""), (ii.READ_IMAGE_TOOL_NAME, "nonexistent/a.png")],
        max_image_size=5 * 1024 * 1024,
    )
    assert blocks == []
    assert sources == {ii.USER_UPLOAD_SOURCE: [], ii.READ_IMAGE_TOOL_NAME: []}
    # 逆序处理：排在前面的路径先出结果
    assert "nonexistent/a.png" in failures[0]
    assert "缺少可追溯的 file_path" in failures[1]
    assert len(failures) == 2


def test_load_injection_images_size_suffix_and_dedup_guards(tmp_path, monkeypatch):
    """超限（218->219）、不支持格式（221->222）、同路径去重都按预期跳过。"""
    # 大小上限由调用方传参（运行期经配置服务读取）：1x1 PNG 约 70 字节，200 字节文件超限
    # 可见路径 /mnt/user-data/workspace/* 映射到 {workspace_root}/workspace/*
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "big.png").write_bytes(b"x" * 200)
    (ws / "doc.txt").write_bytes(b"hello")
    (ws / "ok.png").write_bytes(PNG_1PX)

    visible = "/mnt/user-data/workspace"
    blocks, sources, failures = ii._load_injection_images(
        str(tmp_path),
        [
            (ii.USER_UPLOAD_SOURCE, f"{visible}/big.png"),
            (ii.USER_UPLOAD_SOURCE, f"{visible}/doc.txt"),
            (ii.USER_UPLOAD_SOURCE, f"{visible}/ok.png"),
            (ii.USER_UPLOAD_SOURCE, f"{visible}/ok.png"),
        ],
        max_image_size=100,
    )
    assert len(blocks) == 1
    assert sources[ii.USER_UPLOAD_SOURCE] == [f"{visible}/ok.png"]
    assert len(failures) == 2
    # 逆序处理，doc.txt（后提交）先出现在 failures
    assert "不支持的图片格式" in failures[0]
    assert "图片超过大小上限" in failures[1]


def test_override_request_prefers_override_method():
    """带 override 方法按 LangChain ModelRequest 处理（247->248）。"""

    class Req:
        def override(self, **kwargs):
            return ("overridden", kwargs)

    assert ii._override_request(Req(), messages=[]) == ("overridden", {"messages": []})
