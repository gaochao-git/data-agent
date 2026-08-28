"""filesystem.py 模型可见工具包装层覆盖率补充测试。

既有测试多直接调用 runtime.*，绕过了 @tool 包装层，导致参数校验、
结果格式化与统一错误文案这些"模型真正看到的行为"未被验证。
本文件一律从 tool.ainvoke 入口进入，用桩 runtime 断言返回文本与错误口径。
"""

import json
import os
import sys
from typing import Any

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.agent.tools import filesystem as fs


class StubRuntime:
    """记录调用并可控抛错的会话工作区替身。"""

    def __init__(self, **overrides):
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_with: Exception | None = None
        self.results: dict[str, Any] = {}
        for key, value in overrides.items():
            setattr(self, key, value)

    def _result(self, name: str, default: Any) -> Any:
        self.calls.append((name, {}))
        if self.fail_with is not None:
            raise self.fail_with
        return self.results.get(name, default)

    async def read_text_file(self, **kwargs):
        self.calls.append(("read_text_file", kwargs))
        if self.fail_with is not None:
            raise self.fail_with
        return self.results.get("read_text_file", {"lines": ["a"], "total_lines": 1})

    async def write_text_file(self, **kwargs):
        self.calls.append(("write_text_file", kwargs))
        if self.fail_with is not None:
            raise self.fail_with
        return self.results.get("write_text_file", {"bytes": 1})

    async def replace_text_file(self, **kwargs):
        self.calls.append(("replace_text_file", kwargs))
        if self.fail_with is not None:
            raise self.fail_with
        return self.results.get("replace_text_file", {"replaced_count": 1})

    async def move_path(self, **kwargs):
        self.calls.append(("move_path", kwargs))
        if self.fail_with is not None:
            raise self.fail_with
        return self.results.get("move_path", {"type": "file", "destination_path": "/mnt/user-data/workspace/b.txt"})

    async def copy_path(self, **kwargs):
        self.calls.append(("copy_path", kwargs))
        if self.fail_with is not None:
            raise self.fail_with
        return self.results.get("copy_path", {"type": "file", "bytes": 3})

    async def delete_path(self, **kwargs):
        self.calls.append(("delete_path", kwargs))
        if self.fail_with is not None:
            raise self.fail_with
        return self.results.get("delete_path", {"type": "file", "path": "/mnt/user-data/workspace/a.txt"})

    async def search_files(self, **kwargs):
        self.calls.append(("search_files", kwargs))
        if self.fail_with is not None:
            raise self.fail_with
        return self.results.get("search_files", [{"path": "/mnt/user-data/workspace/a.py", "type": "file"}])

    async def grep_text(self, **kwargs):
        self.calls.append(("grep_text", kwargs))
        if self.fail_with is not None:
            raise self.fail_with
        return self.results.get("grep_text", {
            "matches": [{"path": "a.py", "line_number": 1, "line": "hello"}],
            "has_more": True, "skipped_binary": 2, "skipped_large": 1,
        })

    async def create_dir(self, **kwargs):
        self.calls.append(("create_dir", kwargs))
        if self.fail_with is not None:
            raise self.fail_with
        return self.results.get("create_dir", {"path": "/mnt/user-data/workspace/new", "type": "dir"})

    async def list_dir(self, **kwargs):
        self.calls.append(("list_dir", kwargs))
        if self.fail_with is not None:
            raise self.fail_with
        return self.results.get("list_dir", [
            {"type": "dir", "path": "/mnt/user-data/workspace/sub/", "size": "-", "mtime": "2026-08-28 10:00"},
            {"type": "file", "path": "/mnt/user-data/workspace/a.txt", "size": 3, "mtime": "2026-08-28 10:00"},
        ])


def _tools(runtime) -> dict:
    return {tool.name: tool for tool in fs.create_filesystem_tools(runtime)}


async def _invoke(runtime, name: str, args: dict) -> str:
    return await _tools(runtime)[name].ainvoke(args)


def _payload(text: str) -> dict:
    # 工具返回形如 "[file_move]\n{json}"
    return json.loads(text.split("\n", 1)[1])


# ---------------------------------------------------------------------------
# 参数校验辅助函数
# ---------------------------------------------------------------------------


def test_validate_relative_glob_rules():
    assert fs._validate_relative_glob(" **/*.py ", field_name="glob") == "**/*.py"
    assert fs._validate_relative_glob("a\\b.py", field_name="glob") == "a/b.py"
    with pytest.raises(ValueError, match="glob 不能为空"):
        fs._validate_relative_glob("   ", field_name="glob")
    with pytest.raises(ValueError, match="绝对路径或 \\.\\."):
        fs._validate_relative_glob("/etc/passwd", field_name="glob")
    with pytest.raises(ValueError, match="绝对路径或 \\.\\."):
        fs._validate_relative_glob("../secret", field_name="glob")


def test_validate_filename_pattern_rules():
    assert fs._validate_filename_pattern("*.json", field_name="pattern") == "*.json"
    with pytest.raises(ValueError, match="pattern 不能为空"):
        fs._validate_filename_pattern("", field_name="pattern")
    with pytest.raises(ValueError, match="只能匹配文件名"):
        fs._validate_filename_pattern("sub/*.json", field_name="pattern")
    with pytest.raises(ValueError, match="只能匹配文件名"):
        fs._validate_filename_pattern("..", field_name="pattern")


# ---------------------------------------------------------------------------
# 移动 / 复制 / 删除
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_file_move_tool_success_and_error():
    runtime = StubRuntime()
    text = await _invoke(runtime, "file_move", {
        "source_path": "/mnt/user-data/workspace/a.txt", "destination_path": "/mnt/user-data/workspace/b.txt",
    })
    payload = _payload(text)
    assert payload["destination_path"] == "/mnt/user-data/workspace/b.txt"
    assert runtime.calls == [("move_path", {
        "source_path": "/mnt/user-data/workspace/a.txt", "destination_path": "/mnt/user-data/workspace/b.txt",
    })]

    failing = StubRuntime()
    failing.fail_with = ValueError("目标路径已存在")
    out = await _invoke(failing, "file_move", {
        "source_path": "/mnt/user-data/workspace/a.txt", "destination_path": "/mnt/user-data/workspace/b.txt",
    })
    assert out.startswith("file_move 工具错误:") and "目标路径已存在" in out


@pytest.mark.asyncio
async def test_file_copy_tool_success_and_unexpected_error():
    runtime = StubRuntime()
    payload = _payload(await _invoke(runtime, "file_copy", {
        "source_path": "/mnt/user-data/uploads/a.csv", "destination_path": "/mnt/user-data/workspace/a.csv",
    }))
    assert payload == {"type": "file", "bytes": 3}

    # 非预期异常必须收敛成通用文案，不能把内部堆栈信息透给模型
    failing = StubRuntime()
    failing.fail_with = RuntimeError("connection pool exhausted at 10.0.0.5:3306")
    out = await _invoke(failing, "file_copy", {
        "source_path": "/mnt/user-data/uploads/a.csv", "destination_path": "/mnt/user-data/workspace/a.csv",
    })
    assert out == "file_copy 工具错误: 工具执行失败，请稍后重试"
    assert "10.0.0.5" not in out


@pytest.mark.asyncio
async def test_file_delete_tool_pins_file_semantics():
    runtime = StubRuntime()
    await _invoke(runtime, "file_delete", {"file_path": "/mnt/user-data/workspace/a.txt"})
    assert runtime.calls == [("delete_path", {
        "path": "/mnt/user-data/workspace/a.txt", "recursive": False, "expected_type": "file",
    })]


@pytest.mark.asyncio
async def test_dir_delete_tool_passes_recursive_flag():
    runtime = StubRuntime()
    text = await _invoke(runtime, "dir_delete", {"path": "/mnt/user-data/workspace/sub", "recursive": True})
    assert _payload(text)["type"] == "file"  # 桩返回复用，重点在参数透传
    assert runtime.calls == [("delete_path", {
        "path": "/mnt/user-data/workspace/sub", "recursive": True, "expected_type": "dir",
    })]

    shallow = StubRuntime()
    await _invoke(shallow, "dir_delete", {"path": "/mnt/user-data/workspace/sub"})
    assert shallow.calls[0][1]["recursive"] is False


@pytest.mark.asyncio
async def test_dir_create_tool_success_and_error():
    runtime = StubRuntime()
    assert _payload(await _invoke(runtime, "dir_create", {"path": "/mnt/user-data/workspace/new"}))["type"] == "dir"
    failing = StubRuntime()
    failing.fail_with = ValueError("目录已存在")
    assert "目录已存在" in await _invoke(failing, "dir_create", {"path": "/mnt/user-data/workspace/new"})


# ---------------------------------------------------------------------------
# file_glob / file_grep
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_file_glob_tool_validates_and_formats():
    runtime = StubRuntime()
    payload = _payload(await _invoke(runtime, "file_glob", {"pattern": "*.py"}))
    assert payload["path"] == "/mnt/user-data/workspace"
    assert payload["pattern"] == "*.py"
    assert payload["count"] == 1
    assert runtime.calls[0][1] == {
        "path": "/mnt/user-data/workspace", "pattern": "*.py", "max_results": fs._DEFAULT_GLOB_RESULTS,
    }

    # 路径型 pattern 在沙箱 Files search 语义下不允许
    assert "只能匹配文件名" in await _invoke(StubRuntime(), "file_glob", {"pattern": "sub/*.py"})
    assert "pattern 不能为空" in await _invoke(StubRuntime(), "file_glob", {"pattern": "  "})
    assert "max_results 必须在 1-500 之间" in await _invoke(
        StubRuntime(), "file_glob", {"pattern": "*.py", "max_results": 0},
    )
    assert "max_results 必须在 1-500 之间" in await _invoke(
        StubRuntime(), "file_glob", {"pattern": "*.py", "max_results": 9999},
    )


@pytest.mark.asyncio
async def test_file_grep_tool_splits_or_patterns_and_reports_skips():
    runtime = StubRuntime()
    payload = _payload(await _invoke(runtime, "file_grep", {
        "pattern": "error|warn", "path": "outputs", "glob": "**/*.log", "max_results": 10,
    }))
    assert payload["pattern"] == "error|warn"
    assert payload["glob"] == "**/*.log"
    assert payload["count"] == 1
    assert payload["has_more"] is True
    assert payload["skipped_binary"] == 2
    assert payload["skipped_large"] == 1
    assert runtime.calls[0][1]["patterns"] == ["error", "warn"]

    assert "pattern 不能为空" in await _invoke(StubRuntime(), "file_grep", {"pattern": "||"})
    assert "glob 只能在指定目录内匹配" in await _invoke(
        StubRuntime(), "file_grep", {"pattern": "x", "glob": "/etc/*"},
    )
    assert "max_results 必须在 1-200 之间" in await _invoke(
        StubRuntime(), "file_grep", {"pattern": "x", "max_results": 500},
    )


# ---------------------------------------------------------------------------
# dir_list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dir_list_tool_formats_entries_and_bounds_depth():
    runtime = StubRuntime()
    text = await _invoke(runtime, "dir_list", {"path": "workspace", "max_depth": 3})
    assert text.startswith("[dir_list]")
    assert "path=workspace" in text and "count=2" in text
    assert "type=dir  path=/mnt/user-data/workspace/sub/  size=-" in text
    assert "type=file  path=/mnt/user-data/workspace/a.txt  size=3" in text
    assert runtime.calls[0][1] == {"path": "workspace", "max_depth": 3}

    for bad_depth in (0, 6):
        assert "max_depth 必须在 1-5 之间" in await _invoke(
            StubRuntime(), "dir_list", {"max_depth": bad_depth},
        )

    empty = StubRuntime()
    empty.results["list_dir"] = []
    out = await _invoke(empty, "dir_list", {"path": "workspace"})
    assert "count=0" in out and "(空目录)" in out

    # 反斜杠路径统一成正斜杠后原样交给 runtime（规范化由策略层负责）
    slashed = StubRuntime()
    await _invoke(slashed, "dir_list", {"path": "/mnt/user-data/workspace\\sub"})
    assert slashed.calls[0][1]["path"] == "/mnt/user-data/workspace/sub"


@pytest.mark.asyncio
async def test_dir_list_tool_error_uses_generic_text_for_unexpected():
    runtime = StubRuntime()
    runtime.fail_with = RuntimeError("sandbox unreachable")
    out = await _invoke(runtime, "dir_list", {"path": "workspace"})
    assert out == "dir_list 工具错误: 工具执行失败，请稍后重试"


@pytest.mark.asyncio
async def test_file_read_tool_rejects_bad_pagination_before_runtime():
    runtime = StubRuntime()
    assert "offset 必须大于等于 0" in await _invoke(runtime, "file_read", {
        "file_path": "/mnt/user-data/workspace/a.txt", "offset": -1,
    })
    assert "limit 必须在 1-500 之间" in await _invoke(runtime, "file_read", {
        "file_path": "/mnt/user-data/workspace/a.txt", "limit": 0,
    })
    assert runtime.calls == []
