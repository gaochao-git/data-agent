"""message_compose 纯函数测试——附件占位文本与图片附件准入规则。"""

from __future__ import annotations

from apps.chat_data_app.services.agent_run import message_compose as mc


def _image(path: str | None = "/mnt/user-data/uploads/a.png", **overrides) -> dict:
    item = {"file_type": "png", "file_id": "f1", "file_size": 1024}
    if path is not None:
        item["virtual_path"] = path
    item.update(overrides)
    return item


def test_build_file_attachment_text_empty():
    assert mc.build_file_attachment_text([]) == ""
    assert mc.build_file_attachment_text(None) == ""


def test_build_file_attachment_text_prefers_virtual_path():
    items = [
        {"virtual_path": "/mnt/user-data/uploads/a.xlsx"},
        {"parsed_path": "/mnt/user-data/outputs/a.md"},
        {"file_name": "no-path.txt"},
    ]
    out = mc.build_file_attachment_text(items)
    assert out == (
        "\n[attachment] /mnt/user-data/uploads/a.xlsx"
        "\n[attachment] /mnt/user-data/outputs/a.md"
    )


def test_is_image_attachment_type_and_size_rules():
    assert mc.is_image_attachment(_image()) is True
    # 类型大小写归一
    assert mc.is_image_attachment(_image(file_type="PNG")) is True
    # 非图片类型
    assert mc.is_image_attachment(_image(file_type="pdf")) is False
    # 缺 file_id
    assert mc.is_image_attachment(_image(file_id=None)) is False
    # 超大小上限
    assert mc.is_image_attachment(_image(file_size=mc.MAX_IMAGE_SIZE + 1)) is False
    # 缺大小按 0 处理
    assert mc.is_image_attachment(_image(file_size=None)) is True


def test_is_image_attachment_size_not_a_number():
    """file_size 无法转 int 时按非图片处理（覆盖异常兜底分支）。"""
    assert mc.is_image_attachment(_image(file_size="not-a-number")) is False
    assert mc.is_image_attachment(_image(file_size=object())) is False


def test_select_vision_attachments_respects_cap():
    items = [
        _image(path=f"/mnt/user-data/uploads/{i}.png") for i in range(mc.MAX_IMAGES_PER_MESSAGE + 2)
    ]
    out = mc.select_vision_attachments(items)
    assert len(out) == mc.MAX_IMAGES_PER_MESSAGE


def test_select_vision_attachments_skips_non_image_and_pathless():
    assert mc.select_vision_attachments([_image(file_type="pdf")]) == []
    assert mc.select_vision_attachments([_image(path=None)]) == []
    assert mc.select_vision_attachments([]) == []


def test_select_vision_attachments_mixed_items():
    items = [
        {"file_type": "pdf", "file_id": "x", "file_size": 10, "virtual_path": "/mnt/user-data/uploads/a.pdf"},
        _image(path="/mnt/user-data/uploads/b.png"),
        _image(path=None),
        _image(path="/mnt/user-data/outputs/c.jpg", file_type="jpg"),
    ]
    assert mc.select_vision_attachments(items) == [
        "/mnt/user-data/uploads/b.png",
        "/mnt/user-data/outputs/c.jpg",
    ]
