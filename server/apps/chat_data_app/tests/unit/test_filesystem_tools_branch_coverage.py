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
        return self.results.get(
            "grep_text",
            {
                "matches": [{"path": "a.py", "line_number": 1, "line": "hello"}],
                "has_more": True,
                "skipped_binary": 2,
                "skipped_large": 1,
            },
        )

    async def create_dir(self, **kwargs):
        self.calls.append(("create_dir", kwargs))
        if self.fail_with is not None:
            raise self.fail_with
        return self.results.get("create_dir", {"path": "/mnt/user-data/workspace/new", "type": "dir"})

    async def list_dir(self, **kwargs):
        self.calls.append(("list_dir", kwargs))
        if self.fail_with is not None:
            raise self.fail_with
        return self.results.get(
            "list_dir",
            [
                {"type": "dir", "path": "/mnt/user-data/workspace/sub/", "size": "-", "mtime": "2026-08-28 10:00"},
                {"type": "file", "path": "/mnt/user-data/workspace/a.txt", "size": 3, "mtime": "2026-08-28 10:00"},
            ],
        )


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
    text = await _invoke(
        runtime,
        "file_move",
        {
            "source_path": "/mnt/user-data/workspace/a.txt",
            "destination_path": "/mnt/user-data/workspace/b.txt",
        },
    )
    payload = _payload(text)
    assert payload["destination_path"] == "/mnt/user-data/workspace/b.txt"
    assert runtime.calls == [
        (
            "move_path",
            {
                "source_path": "/mnt/user-data/workspace/a.txt",
                "destination_path": "/mnt/user-data/workspace/b.txt",
            },
        )
    ]

    failing = StubRuntime()
    failing.fail_with = ValueError("目标路径已存在")
    with pytest.raises(ValueError, match="目标路径已存在"):
        await _invoke(
            failing,
            "file_move",
            {
                "source_path": "/mnt/user-data/workspace/a.txt",
                "destination_path": "/mnt/user-data/workspace/b.txt",
            },
        )


@pytest.mark.asyncio
async def test_file_copy_tool_success_and_unexpected_error():
    runtime = StubRuntime()
    payload = _payload(
        await _invoke(
            runtime,
            "file_copy",
            {
                "source_path": "/mnt/user-data/uploads/a.csv",
                "destination_path": "/mnt/user-data/workspace/a.csv",
            },
        )
    )
    assert payload == {"type": "file", "bytes": 3}

    # 失败改为 raise 原异常，由 ToolErrorMiddleware 统一转成 status="error" 的 ToolMessage
    failing = StubRuntime()
    failing.fail_with = RuntimeError("connection pool exhausted at 10.0.0.5:3306")
    with pytest.raises(RuntimeError, match="connection pool exhausted"):
        await _invoke(
            failing,
            "file_copy",
            {
                "source_path": "/mnt/user-data/uploads/a.csv",
                "destination_path": "/mnt/user-data/workspace/a.csv",
            },
        )


@pytest.mark.asyncio
async def test_file_delete_tool_pins_file_semantics():
    runtime = StubRuntime()
    await _invoke(runtime, "file_delete", {"file_path": "/mnt/user-data/workspace/a.txt"})
    assert runtime.calls == [
        (
            "delete_path",
            {
                "path": "/mnt/user-data/workspace/a.txt",
                "recursive": False,
                "expected_type": "file",
            },
        )
    ]


@pytest.mark.asyncio
async def test_dir_delete_tool_passes_recursive_flag():
    runtime = StubRuntime()
    text = await _invoke(runtime, "dir_delete", {"path": "/mnt/user-data/workspace/sub", "recursive": True})
    assert _payload(text)["type"] == "file"  # 桩返回复用，重点在参数透传
    assert runtime.calls == [
        (
            "delete_path",
            {
                "path": "/mnt/user-data/workspace/sub",
                "recursive": True,
                "expected_type": "dir",
            },
        )
    ]

    shallow = StubRuntime()
    await _invoke(shallow, "dir_delete", {"path": "/mnt/user-data/workspace/sub"})
    assert shallow.calls[0][1]["recursive"] is False


@pytest.mark.asyncio
async def test_dir_create_tool_success_and_error():
    runtime = StubRuntime()
    assert _payload(await _invoke(runtime, "dir_create", {"path": "/mnt/user-data/workspace/new"}))["type"] == "dir"
    failing = StubRuntime()
    failing.fail_with = ValueError("目录已存在")
    with pytest.raises(ValueError, match="目录已存在"):
        await _invoke(failing, "dir_create", {"path": "/mnt/user-data/workspace/new"})


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
        "path": "/mnt/user-data/workspace",
        "pattern": "*.py",
        "max_results": fs._DEFAULT_GLOB_RESULTS,
    }

    # 路径型 pattern 在沙箱 Files search 语义下不允许（校验失败现在以异常抛出）
    with pytest.raises(ValueError, match="只能匹配文件名"):
        await _invoke(StubRuntime(), "file_glob", {"pattern": "sub/*.py"})
    with pytest.raises(ValueError, match="pattern 不能为空"):
        await _invoke(StubRuntime(), "file_glob", {"pattern": "  "})
    # max_results 越界同样以异常抛出，由 ToolErrorMiddleware 统一转成 error ToolMessage
    with pytest.raises(ValueError, match="max_results 必须在 1-500 之间"):
        await _invoke(StubRuntime(), "file_glob", {"pattern": "*.py", "max_results": 0})
    with pytest.raises(ValueError, match="max_results 必须在 1-500 之间"):
        await _invoke(StubRuntime(), "file_glob", {"pattern": "*.py", "max_results": 9999})


@pytest.mark.asyncio
async def test_file_grep_tool_splits_or_patterns_and_reports_skips():
    runtime = StubRuntime()
    payload = _payload(
        await _invoke(
            runtime,
            "file_grep",
            {
                "pattern": "error|warn",
                "path": "outputs",
                "glob": "**/*.log",
                "max_results": 10,
            },
        )
    )
    assert payload["pattern"] == "error|warn"
    assert payload["glob"] == "**/*.log"
    assert payload["count"] == 1
    assert payload["has_more"] is True
    assert payload["skipped_binary"] == 2
    assert payload["skipped_large"] == 1
    assert runtime.calls[0][1]["patterns"] == ["error", "warn"]

    with pytest.raises(ValueError, match="pattern 不能为空"):
        await _invoke(StubRuntime(), "file_grep", {"pattern": "||"})
    with pytest.raises(ValueError, match="glob 只能在指定目录内匹配"):
        await _invoke(StubRuntime(), "file_grep", {"pattern": "x", "glob": "/etc/*"})
    with pytest.raises(ValueError, match="max_results 必须在 1-200 之间"):
        await _invoke(StubRuntime(), "file_grep", {"pattern": "x", "max_results": 500})


@pytest.mark.asyncio
async def test_file_grep_tool_regex_case_context_options():
    """file_grep 新旋钮：use_regex/ignore_case/context_lines 透传，content 模式带 before/after。"""
    runtime = StubRuntime()
    runtime.results["grep_text"] = {
        "matches": [
            {
                "path": "/mnt/user-data/workspace/a.py",
                "line_number": 2,
                "line": "def two_impl():",
                "before": ["    return 1"],
                "after": ["    return 2"],
            }
        ],
        "has_more": False,
        "skipped_binary": 0,
        "skipped_large": 0,
    }
    payload = _payload(
        await _invoke(
            runtime,
            "file_grep",
            {
                "pattern": r"def \w+_impl",
                "use_regex": True,
                "ignore_case": True,
                "context_lines": 2,
                "output_mode": "content",
            },
        )
    )
    assert payload["output_mode"] == "content"
    assert payload["matches"][0]["before"] == ["    return 1"]
    assert payload["matches"][0]["after"] == ["    return 2"]
    call = runtime.calls[0][1]
    assert call["use_regex"] is True
    assert call["ignore_case"] is True
    assert call["context_lines"] == 2
    assert call["output_mode"] == "content"

    with pytest.raises(ValueError, match="output_mode"):
        await _invoke(StubRuntime(), "file_grep", {"pattern": "x", "output_mode": "bogus"})
    with pytest.raises(ValueError, match="context_lines 必须在 0-10 之间"):
        await _invoke(StubRuntime(), "file_grep", {"pattern": "x", "context_lines": 11})
    with pytest.raises(ValueError, match="context_lines 必须在 0-10 之间"):
        await _invoke(StubRuntime(), "file_grep", {"pattern": "x", "context_lines": -1})


@pytest.mark.asyncio
async def test_file_grep_regex_mode_keeps_pipe_as_alternation():
    """正则模式不按 | 拆分：(a|b) 分组、[a|b] 字符类整体交给 rg，| 就是正则交替。"""
    runtime = StubRuntime()
    runtime.results["grep_text"] = {"matches": [], "has_more": False, "skipped_binary": 0, "skipped_large": 0}
    await _invoke(runtime, "file_grep", {"pattern": r"(def|class) \w+", "use_regex": True})
    call = runtime.calls[0][1]
    assert call["patterns"] == [r"(def|class) \w+"]

    # 字面模式维持既有拆分语义
    runtime2 = StubRuntime()
    runtime2.results["grep_text"] = {"matches": [], "has_more": False, "skipped_binary": 0, "skipped_large": 0}
    await _invoke(runtime2, "file_grep", {"pattern": "error|warn"})
    assert runtime2.calls[0][1]["patterns"] == ["error", "warn"]

    # 正则模式下 pattern 不能整体为空
    with pytest.raises(ValueError, match="pattern 不能为空"):
        await _invoke(StubRuntime(), "file_grep", {"pattern": "  ", "use_regex": True})


@pytest.mark.asyncio
async def test_file_grep_tool_files_and_count_modes():
    """files_with_matches / count 模式返回 entries 而非 matches，count 带每文件计数。"""
    runtime = StubRuntime()
    runtime.results["grep_text"] = {
        "entries": [
            {"path": "/mnt/user-data/workspace/a.py", "match_count": 3},
            {"path": "/mnt/user-data/workspace/b.md"},
        ],
        "has_more": False,
        "skipped_binary": 0,
        "skipped_large": 0,
    }
    payload = _payload(
        await _invoke(
            runtime,
            "file_grep",
            {
                "pattern": "error",
                "ignore_case": True,
                "output_mode": "count",
                "max_results": 50,
            },
        )
    )
    assert payload["output_mode"] == "count"
    assert "matches" not in payload
    assert payload["entries"][0]["match_count"] == 3
    assert payload["count"] == 2

    files_runtime = StubRuntime()
    files_runtime.results["grep_text"] = {
        "entries": [{"path": "/mnt/user-data/workspace/a.py"}],
        "has_more": True,
        "skipped_binary": 0,
        "skipped_large": 0,
    }
    files_payload = _payload(
        await _invoke(
            files_runtime,
            "file_grep",
            {
                "pattern": "error",
                "output_mode": "files_with_matches",
            },
        )
    )
    assert files_payload["entries"] == [{"path": "/mnt/user-data/workspace/a.py"}]
    assert files_payload["has_more"] is True


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
        with pytest.raises(ValueError, match="max_depth 必须在 1-5 之间"):
            await _invoke(StubRuntime(), "dir_list", {"max_depth": bad_depth})

    empty = StubRuntime()
    empty.results["list_dir"] = []
    out = await _invoke(empty, "dir_list", {"path": "workspace"})
    assert "count=0" in out and "(空目录)" in out

    # 反斜杠路径统一成正斜杠后原样交给 runtime（规范化由策略层负责）
    slashed = StubRuntime()
    await _invoke(slashed, "dir_list", {"path": "/mnt/user-data/workspace\\sub"})
    assert slashed.calls[0][1]["path"] == "/mnt/user-data/workspace/sub"


@pytest.mark.asyncio
async def test_dir_list_tool_error_raises_unexpected_exception():
    runtime = StubRuntime()
    runtime.fail_with = RuntimeError("sandbox unreachable")
    with pytest.raises(RuntimeError, match="sandbox unreachable"):
        await _invoke(runtime, "dir_list", {"path": "workspace"})


@pytest.mark.asyncio
async def test_file_read_tool_rejects_bad_pagination_before_runtime():
    runtime = StubRuntime()
    with pytest.raises(ValueError, match="offset 必须大于等于 0"):
        await _invoke(
            runtime,
            "file_read",
            {
                "file_path": "/mnt/user-data/workspace/a.txt",
                "offset": -1,
            },
        )
    with pytest.raises(ValueError, match="limit 必须在 1-500 之间"):
        await _invoke(
            runtime,
            "file_read",
            {
                "file_path": "/mnt/user-data/workspace/a.txt",
                "limit": 0,
            },
        )
    assert runtime.calls == []


@pytest.mark.asyncio
async def test_file_edit_tool_rejects_blank_old_content_before_runtime():
    runtime = StubRuntime()
    # 非字符串类型在 ainvoke 入口先被 langchain 参数 pydantic 校验拦截；
    # 空字符串才落到工具体校验，统一以异常抛出
    with pytest.raises(ValueError, match="old_content 必须是非空字符串"):
        await _invoke(
            runtime,
            "file_edit",
            {
                "file_path": "/mnt/user-data/workspace/a.txt",
                "old_content": "",
                "new_content": "new",
            },
        )
    assert runtime.calls == []


@pytest.mark.asyncio
async def test_file_edit_tool_rejects_identical_content_before_runtime():
    runtime = StubRuntime()
    with pytest.raises(ValueError, match="old_content 与 new_content 相同"):
        await _invoke(
            runtime,
            "file_edit",
            {
                "file_path": "/mnt/user-data/workspace/a.txt",
                "old_content": "same",
                "new_content": "same",
            },
        )
    # new_content 为非字符串时按序列化结果比较，同样拦截
    with pytest.raises(ValueError, match="old_content 与 new_content 相同"):
        await _invoke(
            runtime,
            "file_edit",
            {
                "file_path": "/mnt/user-data/workspace/a.txt",
                "old_content": "123",
                "new_content": 123,
            },
        )
    assert runtime.calls == []


# ---------------------------------------------------------------------------
# file_diff / read_text_file_full（分页契约替身）
# ---------------------------------------------------------------------------


class PagedFileRuntime:
    """基于临时目录的 read_text_file 分页契约替身：offset 0 起始、lines/has_more/next_offset 翻页。"""

    def __init__(
        self, root, page_size: int | None = None, binary_paths: set[str] | None = None, infinite: bool = False
    ):
        self.root = root
        self.page_size = page_size
        self.binary_paths = binary_paths or set()
        self.infinite = infinite

    async def read_text_file(self, *, file_path, offset, limit, max_chars):
        relative = str(file_path).replace("/mnt/user-data/", "").lstrip("/")
        target = self.root / relative
        if not target.exists():
            return {"exists": False, "path": file_path}
        if target.is_dir():
            return {"exists": True, "is_dir": True, "path": file_path}
        if str(file_path) in self.binary_paths:
            return {"exists": True, "is_dir": False, "is_binary": True, "path": file_path}
        content = target.read_text(encoding="utf-8")
        lines = content.splitlines()
        effective_limit = self.page_size or limit
        if self.infinite and offset >= len(lines):
            # 模拟超长文件：内容读完后仍不断声称有下一页，用于验证翻页上限的 fail-fast
            return {
                "exists": True,
                "is_dir": False,
                "is_binary": False,
                "path": file_path,
                "total_lines": len(lines),
                "empty": False,
                "offset_out_of_range": False,
                "lines": [f"filler-{offset}"],
                "has_more": True,
                "next_offset": offset + effective_limit,
                "truncated_lines": [],
            }
        page_lines = lines[offset : offset + effective_limit]
        has_more = offset + effective_limit < len(lines)
        next_offset = offset + effective_limit if has_more else None
        if self.infinite and not has_more and page_lines:
            has_more = True
            next_offset = offset + effective_limit
        return {
            "exists": True,
            "is_dir": False,
            "is_binary": False,
            "path": file_path,
            "total_lines": len(lines),
            "empty": content == "",
            "offset_out_of_range": content != "" and offset >= len(lines),
            "lines": page_lines,
            "has_more": has_more,
            "next_offset": next_offset,
            "truncated_lines": [],
        }


def _write(root, name: str, text: str) -> str:
    (root / "workspace").mkdir(exist_ok=True)
    (root / "workspace" / name).write_text(text, encoding="utf-8")
    return f"/mnt/user-data/workspace/{name}"


@pytest.mark.asyncio
async def test_file_diff_tool_identical_files(tmp_path):
    runtime = PagedFileRuntime(tmp_path)
    path_a = _write(tmp_path, "a.yaml", "version: '1'\nname: m\n")
    path_b = _write(tmp_path, "b.yaml", "version: '1'\nname: m\n")
    payload = _payload(await _invoke(runtime, "file_diff", {"file_a": path_a, "file_b": path_b}))
    assert payload["status"] == "identical"
    assert payload["added_lines"] == 0
    assert payload["removed_lines"] == 0
    assert "diff" not in payload


@pytest.mark.asyncio
async def test_file_diff_tool_changed_files_counts_and_diff(tmp_path):
    runtime = PagedFileRuntime(tmp_path)
    path_a = _write(tmp_path, "a.yaml", "version: '1'\nname: orders\nmetrics: []\n")
    path_b = _write(tmp_path, "b.yaml", "version: '1'\nname: orders_v2\nmetrics: []\n")
    payload = _payload(await _invoke(runtime, "file_diff", {"file_a": path_a, "file_b": path_b}))
    assert payload["status"] == "changed"
    assert payload["added_lines"] == 1
    assert payload["removed_lines"] == 1
    assert "-name: orders" in payload["diff"]
    assert "+name: orders_v2" in payload["diff"]
    assert payload["diff"].startswith("---")


@pytest.mark.asyncio
async def test_file_diff_tool_reads_across_pages(tmp_path, monkeypatch):
    """页大小小于文件行数时必须翻页拉全，统计不能因丢行而错。"""
    monkeypatch.setattr(fs, "_FULL_READ_PAGE_LINES", 2)
    runtime = PagedFileRuntime(tmp_path, page_size=2)
    lines_a = "\n".join(f"line-{i}" for i in range(7)) + "\n"
    lines_b = "\n".join(f"line-{i}" for i in range(7)) + "\nline-7\nline-8\n"
    path_a = _write(tmp_path, "a.txt", lines_a)
    path_b = _write(tmp_path, "b.txt", lines_b)
    payload = _payload(await _invoke(runtime, "file_diff", {"file_a": path_a, "file_b": path_b}))
    assert payload["status"] == "changed"
    assert payload["added_lines"] == 2
    assert payload["removed_lines"] == 0


@pytest.mark.asyncio
async def test_file_diff_tool_missing_file_raises(tmp_path):
    runtime = PagedFileRuntime(tmp_path)
    path_a = _write(tmp_path, "a.yaml", "version: '1'\n")
    with pytest.raises(ValueError, match="文件不存在"):
        await _invoke(runtime, "file_diff", {"file_a": path_a, "file_b": "/mnt/user-data/workspace/missing.yaml"})


@pytest.mark.asyncio
async def test_file_diff_tool_rejects_binary(tmp_path):
    runtime = PagedFileRuntime(tmp_path, binary_paths={"/mnt/user-data/uploads/blob.bin"})
    path_a = _write(tmp_path, "a.yaml", "version: '1'\n")
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "uploads" / "blob.bin").write_bytes(b"\x00\x01")
    with pytest.raises(ValueError, match="二进制"):
        await _invoke(
            runtime,
            "file_diff",
            {
                "file_a": path_a,
                "file_b": "/mnt/user-data/uploads/blob.bin",
            },
        )


@pytest.mark.asyncio
async def test_read_text_file_full_page_limit_raises(tmp_path, monkeypatch):
    """翻页次数耗尽仍未读完必须报错，不允许静默截断成假全文。"""
    monkeypatch.setattr(fs, "_FULL_READ_MAX_PAGES", 3)
    monkeypatch.setattr(fs, "_FULL_READ_PAGE_LINES", 2)
    runtime = PagedFileRuntime(tmp_path, page_size=2, infinite=True)
    path = _write(tmp_path, "long.txt", "l0\nl1\nl2\n")
    with pytest.raises(ValueError, match="行上限，无法整体读取"):
        await fs.read_text_file_full(runtime, path)


@pytest.mark.asyncio
async def test_file_diff_counts_special_prefix_content_lines(tmp_path):
    """正文行本身以 +++/--- 开头时不能被误排除：计数与前端染色口径一致。"""
    runtime = PagedFileRuntime(tmp_path)
    path_a = _write(tmp_path, "a.txt", "x\n")
    path_b = _write(tmp_path, "b.txt", "x\n++foo\n--bar\n")
    payload = _payload(await _invoke(runtime, "file_diff", {"file_a": path_a, "file_b": path_b}))
    assert payload["status"] == "changed"
    assert payload["added_lines"] == 2
    assert payload["removed_lines"] == 0


@pytest.mark.asyncio
async def test_read_text_file_full_empty_file_raises(tmp_path):
    runtime = PagedFileRuntime(tmp_path)
    path = _write(tmp_path, "empty.yaml", "")
    with pytest.raises(ValueError, match="文件为空"):
        await fs.read_text_file_full(runtime, path)
