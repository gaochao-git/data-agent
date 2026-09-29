"""officecli_adapter 参数解析纯函数的分支覆盖补测。"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from apps.chat_data_app.agent.workspace.officecli_adapter import (
    _embedded_property_indices,
    _officecli_file_indices,
    _officecli_option_indices,
    _option_value_indices,
    command_path_values,
    parse_officecli_args,
)


def test_option_value_indices_supports_inline_equals_form():
    """--out=/path 形式的内联选项值也要计入（21->22）。"""
    assert _option_value_indices(["--out=/tmp/x.docx"], {"--out", "-o"}) == {0}
    # 普通「名 值」形式不受影响
    assert _option_value_indices(["--out", "/tmp/x.docx"], {"--out", "-o"}) == {1}
    # 选项是最后一个参数、没有值时不计
    assert _option_value_indices(["--out"], {"--out", "-o"}) == set()


def test_embedded_property_skips_remote_image_source():
    """src/image 等 source 属性指向远程 URL 时不视为本地文件（68->61）。"""
    args = ["set", "/tmp/a.docx", "--prop", "src=http://example.com/pic.png"]
    assert _embedded_property_indices(args) == set()
    # 本地路径仍会被标记
    args_local = ["set", "/tmp/a.docx", "--prop", "src=/tmp/pic.png"]
    assert _embedded_property_indices(args_local) == {3}
    # --prop 是最后一个参数（没有值）时跳过
    assert _embedded_property_indices(["set", "--prop"]) == set()


def test_file_indices_requires_path_for_view_and_dump():
    """view/dump 子命令缺少文件路径时报错（77->78 / 87->88）。"""
    with pytest.raises(ValueError, match="officecli view 缺少文件路径"):
        _officecli_file_indices("view", ["view"])
    with pytest.raises(ValueError, match="officecli dump 缺少文件路径"):
        _officecli_file_indices("dump", ["dump"])
    assert _officecli_file_indices("dump", ["dump", "/tmp/a.docx"]) == {1}


def test_file_indices_merge_branch():
    """merge 需要模板与输出两个路径（90->94 / 91->92）。"""
    with pytest.raises(ValueError, match="merge 需要模板路径和输出路径"):
        _officecli_file_indices("merge", ["merge", "/tmp/tpl.docx"])
    assert _officecli_file_indices("merge", ["merge", "/tmp/tpl.docx", "/tmp/out.docx"]) == {1, 2}


def test_file_indices_import_branch():
    """import 的目标文件与可选工作表路径（94->100 / 95->97 / 98->99 / 98->100）。"""
    with pytest.raises(ValueError, match="import 需要目标文件和工作表路径"):
        _officecli_file_indices("import", ["import", "/tmp/a.xlsx"])
    assert _officecli_file_indices("import", ["import", "/tmp/a.xlsx", "/sheet/p[1]"]) == {1}
    # 第 4 个参数存在且不是选项 → 计为文件（98->99）
    assert _officecli_file_indices("import", ["import", "/tmp/a.xlsx", "/sheet/p[1]", "/tmp/b.xlsx"]) == {1, 3}
    # 第 4 个参数是选项 → 不计（98->100）
    assert _officecli_file_indices("import", ["import", "/tmp/a.xlsx", "/sheet/p[1]", "--out", "/tmp/o"]) == {1}
    # 普通读类子命令正常路径（90->94 / 94->100 的 False 链）
    assert _officecli_file_indices("get", ["get", "/tmp/a.xlsx", "/sheet/p[1]"]) == {1}


def test_option_indices_data_json_vs_file():
    """--data 是内联 JSON 时不计为文件，是 JSON 文件路径时计入（107->106 / 107->108）。"""
    args = ["batch", "/tmp/a.xlsx", "--data", '{"rows": []}']
    assert _officecli_option_indices(args) == set()
    args_file = ["batch", "/tmp/a.xlsx", "--data", "/tmp/payload.json"]
    assert _officecli_option_indices(args_file) == {3}


def test_parse_args_strips_binary_prefix_then_empty():
    """剥离 officecli 前缀后参数为空时报错（131->132）。"""
    with pytest.raises(ValueError, match="args 必须包含 OfficeCLI 子命令"):
        parse_officecli_args(["officecli"])


def test_parse_args_help_and_unknown_subcommand():
    """help 子命令直接返回不解析路径（142->143）；未知子命令报错（144->145）。"""
    command = parse_officecli_args(["help", "get"])
    assert command.subcommand == "help"
    assert command.filesystem_indices == frozenset()
    assert command.readonly is True

    with pytest.raises(ValueError, match="不支持的子命令"):
        parse_officecli_args(["frobnicate", "/tmp/a.docx"])


def test_command_path_values_skips_remote_and_json():
    """远程 URL 与 JSON 值不参与路径解析（174->175）。"""
    command = parse_officecli_args(["get", "https://example.com/a.docx", "/sheet/p[1]", "--save", "/tmp/out.docx"])
    values = command_path_values(command, "/mnt/user-data/workspace")
    # https 文件被跳过，只剩 --save 的输出路径（--save 在下标 3，值在下标 4）
    assert [(index, path) for index, path in values] == [(4, "/tmp/out.docx")]


def test_looks_like_selector_root_path():
    """单独的 / 是文档选择器（21->22）。"""
    from apps.chat_data_app.agent.workspace.officecli_adapter import _looks_like_selector

    assert _looks_like_selector("/") is True
    assert _looks_like_selector("/body/p[1]") is True
    assert _looks_like_selector("/tmp/a.docx") is False


def test_validate_command_paths_calls_resolver_with_write_flag():
    """validate_command_paths 逐路径回调 resolver（181->182 / 181->出口）。"""
    from apps.chat_data_app.agent.workspace.officecli_adapter import validate_command_paths

    command = parse_officecli_args(["set", "/tmp/a.docx", "--out=/tmp/out.docx"])
    calls = []
    validate_command_paths(command, "/mnt/user-data/workspace", lambda p, w: calls.append((p, w)))
    # set 子命令：文件路径是写入目标；--out 输出路径不标记为写（write_indices 仅对 view/dump/get 的 --out 计入）
    assert calls == [
        ("/tmp/a.docx", True),
        ("/tmp/out.docx", False),
    ]


def test_translate_command_args_rewrites_embedded_and_inline_options():
    """translate_command_args 覆盖 image: 前缀（194->195）与 --prop 内嵌重写（199->200）。"""
    from apps.chat_data_app.agent.workspace.officecli_adapter import translate_command_args

    command = parse_officecli_args(["set", "/tmp/a.docx", "--prop", "src=image:/tmp/pic.png", "--out=/tmp/out.docx"])
    translated = translate_command_args(command, "/mnt/user-data/workspace", lambda p: p.replace("/tmp", "/mnt/ws"))
    assert list(translated) == [
        "set",
        "/mnt/ws/a.docx",
        "--prop",
        "src=image:/mnt/ws/pic.png",
        "--out=/mnt/ws/out.docx",
    ]


def test_officecli_command_is_mutating():
    """mutating 直读 mutates 标志（208）。"""
    from apps.chat_data_app.agent.workspace.officecli_adapter import officecli_command_is_mutating

    assert officecli_command_is_mutating(parse_officecli_args(["set", "/tmp/a.docx"])) is True
    assert officecli_command_is_mutating(parse_officecli_args(["view", "/tmp/a.docx", "/body/p[1]"])) is False
