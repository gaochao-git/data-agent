"""安全的 OfficeCLI 参数解析与工作区路径适配。"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

OFFICECLI_BANNED_SUBCOMMANDS = frozenset({"plugins", "watch", "unwatch", "open", "close", "mcp", "install", "skills"})
OFFICECLI_READONLY_SUBCOMMANDS = frozenset({"view", "get", "query", "validate", "dump", "raw"})
_SOURCE_PROPERTIES = frozenset({"src", "path", "fallback", "image", "preview", "background"})


def _looks_like_selector(value: str) -> bool:
    """识别误放在文件参数位置的 OfficeCLI 文档选择器。"""
    normalized = str(value or "").strip()
    if normalized == "/":
        return True
    return normalized.startswith(
        ("/body", "/slide", "/sheet", "/Sheet", "/worksheet", "/footer", "/header", "/numbering", "/namedrange")
    )


@dataclass(frozen=True)
class OfficeCliCommand:
    args: tuple[str, ...]
    subcommand: str
    filesystem_indices: frozenset[int]
    write_indices: frozenset[int]
    mutates: bool

    @property
    def readonly(self) -> bool:
        return not self.mutates


def _option_value_indices(args: list[str], names: set[str]) -> set[int]:
    indices: set[int] = set()
    for index, value in enumerate(args):
        if value in names and index + 1 < len(args):
            indices.add(index + 1)
        if any(value.startswith(f"{name}=") for name in names):
            indices.add(index)
    return indices


def _is_json_value(value: str) -> bool:
    try:
        json.loads(value)
    except (TypeError, ValueError):
        return False
    return True


def _embedded_property_indices(args: list[str]) -> set[int]:
    indices: set[int] = set()
    for index, value in enumerate(args):
        if value != "--prop" or index + 1 >= len(args):
            continue
        prop = args[index + 1]
        key, separator, prop_value = prop.partition("=")
        if separator and key.split(".", 1)[0].lower() in _SOURCE_PROPERTIES:
            resource_value = prop_value.removeprefix("image:")
            if not resource_value.startswith(("http://", "https://", "data:")):
                indices.add(index + 1)
    return indices


_FILE_PATH_SUBCOMMANDS = {
    "view",
    "get",
    "query",
    "validate",
    "set",
    "add",
    "remove",
    "move",
    "swap",
    "raw",
    "raw-set",
    "add-part",
    "save",
    "refresh",
    "create",
    "batch",
}


def _file_path_command_file_indices(subcommand: str, args: list[str]) -> set[int] | None:
    """解析以「文件路径」为第二参数的子命令组；不属于该组返回 None。参数不足时报错。"""
    if subcommand not in _FILE_PATH_SUBCOMMANDS:
        return None
    if len(args) < 2:
        raise ValueError(f"officecli {subcommand} 缺少文件路径")
    if _looks_like_selector(args[1]):
        raise ValueError(
            f"officecli {subcommand} 缺少文件路径：第二个参数 {args[1]} 是文档选择器。"
            '正确格式为 [子命令, 文件路径, 选择器]，例如 ["get",'
            ' "/mnt/user-data/workspace/报告.docx", "/body/p[1]"]。'
        )
    return {1}


def _officecli_file_indices(subcommand: str, args: list[str]) -> set[int]:
    """按子命令语义解析文件路径参数下标；参数不足时报错。"""
    path_command_indices = _file_path_command_file_indices(subcommand, args)
    if path_command_indices is not None:
        return path_command_indices
    file_indices: set[int] = set()
    if subcommand == "dump":
        if len(args) < 2:
            raise ValueError("officecli dump 缺少文件路径")
        file_indices.add(1)
    elif subcommand == "merge":
        if len(args) < 3:
            raise ValueError("officecli merge 需要模板路径和输出路径")
        file_indices.update({1, 2})
    elif subcommand == "import":
        if len(args) < 3:
            raise ValueError("officecli import 需要目标文件和工作表路径")
        file_indices.add(1)
        if len(args) >= 4 and not args[3].startswith("-"):
            file_indices.add(3)
    return file_indices


def _officecli_option_indices(args: list[str]) -> set[int]:
    option_indices = _option_value_indices(args, {"--out", "-o", "--save", "--input", "--file"})
    # --data may be an inline JSON object or a JSON file path.
    for index in _option_value_indices(args, {"--data"}):
        if not _is_json_value(args[index]):
            option_indices.add(index)
    return option_indices


def _officecli_write_indices(subcommand: str, args: list[str]) -> set[int]:
    write_indices = set()
    if subcommand in {
        "create",
        "set",
        "add",
        "remove",
        "move",
        "swap",
        "raw-set",
        "add-part",
        "save",
        "refresh",
        "batch",
        "import",
    }:
        write_indices.add(1)
    if subcommand == "merge":
        write_indices.add(2)
    if subcommand in {"view", "dump"}:
        write_indices.update(_option_value_indices(args, {"--out", "-o"}))
    if subcommand == "get":
        write_indices.update(_option_value_indices(args, {"--save"}))
    return write_indices


def parse_officecli_args(raw_args: list[str]) -> OfficeCliCommand:
    if not isinstance(raw_args, list) or not raw_args:
        raise ValueError("args 必须是非空的 OfficeCLI 子命令与参数数组")
    args = [str(value) for value in raw_args]
    if args[0] in {"officecli", "officecli.exe"}:
        args = args[1:]
    if not args:
        raise ValueError("args 必须包含 OfficeCLI 子命令")
    subcommand = args[0].lower()
    if subcommand in {"--version", "--help", "-h", "-?"}:
        return OfficeCliCommand(tuple(args), subcommand, frozenset(), frozenset(), False)
    if subcommand in OFFICECLI_BANNED_SUBCOMMANDS:
        raise ValueError(f"officecli_run 禁止子命令: {subcommand}")
    if subcommand.startswith("-"):
        raise ValueError("args 第一个元素必须是 OfficeCLI 子命令或全局选项")

    file_indices = _officecli_file_indices(subcommand, args)
    if subcommand == "help":
        return OfficeCliCommand(tuple(args), subcommand, frozenset(), frozenset(), False)
    if not file_indices:
        raise ValueError(f"officecli_run 不支持的子命令: {subcommand}")

    option_indices = _officecli_option_indices(args)
    embedded_indices = _embedded_property_indices(args)
    all_indices = file_indices | option_indices | embedded_indices
    write_indices = _officecli_write_indices(subcommand, args)
    base_mutates = subcommand not in OFFICECLI_READONLY_SUBCOMMANDS and subcommand != "help"
    return OfficeCliCommand(
        tuple(args),
        subcommand,
        frozenset(all_indices),
        frozenset(write_indices),
        base_mutates or bool(write_indices),
    )


def _cwd_join(cwd: str, value: str) -> str:
    if value.startswith("/"):
        return value
    base = cwd.rstrip("/") or "/mnt/user-data/workspace"
    return f"{base}/{value}"


def command_path_values(command: OfficeCliCommand, cwd: str) -> list[tuple[int, str]]:
    values: list[tuple[int, str]] = []
    for index in sorted(command.filesystem_indices):
        value = command.args[index]
        if "=" in value and value.startswith(("--out=", "--save=", "--data=", "--input=", "--file=")):
            value = value.split("=", 1)[1]
        if index in _embedded_property_indices(list(command.args)):
            _, _, value = value.partition("=")
            value = value.removeprefix("image:")
        if value.startswith(("http://", "https://", "data:")) or _is_json_value(value):
            continue
        values.append((index, _cwd_join(cwd, value)))
    return values


def validate_command_paths(command: OfficeCliCommand, cwd: str, resolver: Callable[[str, bool], object]) -> None:
    for index, path in command_path_values(command, cwd):
        resolver(path, index in command.write_indices)


def translate_command_args(command: OfficeCliCommand, cwd: str, translate_path: Callable[[str], str]) -> list[str]:
    translated = list(command.args)
    embedded_indices = _embedded_property_indices(list(command.args))
    for index, path in command_path_values(command, cwd):
        embedded = index in embedded_indices
        original = command.args[index]
        original_value = original.partition("=")[2] if embedded else ""
        prefix = "image:" if original_value.startswith("image:") else ""
        value = translate_path(path)
        if prefix:
            value = prefix + value
        original = command.args[index]
        if original.startswith(("--out=", "--save=", "--data=", "--input=", "--file=")):
            translated[index] = original.split("=", 1)[0] + "=" + value
        elif index in embedded_indices:
            key, _, _ = original.partition("=")
            translated[index] = f"{key}={value}"
        else:
            translated[index] = value
    return translated


def officecli_command_is_mutating(command: OfficeCliCommand) -> bool:
    return command.mutates
