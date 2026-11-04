"""workspace_local_backend.py 覆盖率补充测试。

聚焦本地后端尚未被触达的分支：文本行片段切分的 CRLF 跨块边界、awk 行数
统计失败、workspace/outputs 目录增删移拷、技能路径解析、ripgrep 缺失与
执行失败、以及后端异步入口的委派与超时收口。
全部落在 tmp_path 与 monkeypatch 上，不依赖真实 officecli / 数据库。
"""

import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")

from apps.chat_data_app.agent.workspace import runtime as cw_runtime_module
from apps.chat_data_app.agent.workspace import workspace_local_backend as lb
from apps.chat_data_app.services.agent_run import (
    conversation_workspace as cw,  # noqa: F401  # 测试按模块属性引用 conversation_workspace，属有意重导出
)
from apps.chat_data_app.services.agent_run.conversation_workspace import (
    ConversationWorkspaceRuntime,
    ensure_conversation_workspace,
)


@pytest.fixture(autouse=True)
def _chat_data_home(tmp_path, monkeypatch):
    monkeypatch.setattr(cw_runtime_module, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setattr("utils.storage_paths.THREADS_DIR", tmp_path / "threads")


def _runtime(tmp_path: Path) -> tuple[ConversationWorkspaceRuntime, Path]:
    root = ensure_conversation_workspace("conv_local", "user_a")
    workspace_root = Path(root["root"])
    runtime = ConversationWorkspaceRuntime(
        workspace_root=str(workspace_root),
        user_id="user_a",
        backend="local",
    )
    return runtime, workspace_root


class _ChunkedReader:
    """模拟按固定字符数分块 read() 的文本文件句柄。"""

    def __init__(self, chunks: list[str]):
        self._chunks = list(chunks)

    def read(self, _size: int) -> str:
        return self._chunks.pop(0) if self._chunks else ""


# ---------------------------------------------------------------------------
# 文本行片段与行数统计
# ---------------------------------------------------------------------------


def test_iter_text_line_fragments_handles_cr_at_chunk_boundary():
    # 第一块以 \r 结尾、第二块以 \n 结尾：CR 必须被正确回填而不是丢失
    fragments = list(lb._iter_text_line_fragments(_ChunkedReader(["ab\r", "cd\nef"])))
    assert fragments == [("ab", False), ("\rcd", True), ("ef", False)]


def test_iter_text_line_fragments_flushes_trailing_cr():
    assert list(lb._iter_text_line_fragments(_ChunkedReader(["xy\r"]))) == [
        ("xy", False),
        ("\r", False),
    ]
    assert list(lb._iter_text_line_fragments(_ChunkedReader(["a\r\nb"]))) == [
        ("a", True),
        ("b", False),
    ]


def test_count_local_text_lines_cached_error_branches(tmp_path, monkeypatch):
    target = tmp_path / "counted.txt"
    target.write_text("one\ntwo\n", encoding="utf-8")

    def failing_run(*_args, **_kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="awk: boom")

    monkeypatch.setattr(lb.subprocess, "run", failing_run)
    with pytest.raises(OSError, match="awk: boom"):
        lb._count_local_text_lines_cached(str(target), 1, 11, 111)

    def empty_stderr_run(*_args, **_kwargs):
        return SimpleNamespace(returncode=2, stdout="", stderr="")

    monkeypatch.setattr(lb.subprocess, "run", empty_stderr_run)
    with pytest.raises(OSError, match="awk 行数统计失败"):
        lb._count_local_text_lines_cached(str(target), 2, 22, 222)

    def invalid_run(*_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout="not-a-number", stderr="")

    monkeypatch.setattr(lb.subprocess, "run", invalid_run)
    with pytest.raises(OSError, match="结果无效"):
        lb._count_local_text_lines_cached(str(target), 3, 33, 333)

    def ok_run(*_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout="  7  \n", stderr="")

    monkeypatch.setattr(lb.subprocess, "run", ok_run)
    assert lb._count_local_text_lines_cached(str(target), 4, 44, 444) == 7


def test_count_local_text_lines_uses_real_awk(tmp_path):
    if shutil.which("awk") is None:
        pytest.skip("宿主机未安装 awk")
    target = tmp_path / "lines.txt"
    target.write_text("a\nb\nc", encoding="utf-8")
    assert lb._count_local_text_lines(target) == 3


# ---------------------------------------------------------------------------
# workspace / outputs 目录与文件操作
# ---------------------------------------------------------------------------


def test_resolve_local_mutable_path_rejects_roots(tmp_path):
    _runtime_obj, root = _runtime(tmp_path)
    with pytest.raises(ValueError, match="不允许操作"):
        lb._resolve_local_mutable_path(str(root), "/mnt/user-data/workspace")
    with pytest.raises(ValueError, match="不允许操作"):
        lb._resolve_local_mutable_path(str(root), "/mnt/user-data/outputs")


def test_create_local_dir_branches(tmp_path):
    _runtime_obj, root = _runtime(tmp_path)
    created = lb._create_local_dir(str(root), "/mnt/user-data/workspace/newdir")
    assert created == {"path": "/mnt/user-data/workspace/newdir", "type": "dir"}
    assert (root / "workspace" / "newdir").is_dir()
    with pytest.raises(ValueError, match="目录已存在"):
        lb._create_local_dir(str(root), "/mnt/user-data/workspace/newdir")


def test_delete_local_path_branches(tmp_path):
    _runtime_obj, root = _runtime(tmp_path)
    workspace = root / "workspace"
    (workspace / "a.txt").write_text("a", encoding="utf-8")
    (workspace / "sub" / "inner").mkdir(parents=True)
    (workspace / "sub" / "inner" / "deep.txt").write_text("d", encoding="utf-8")
    (workspace / "empty").mkdir()
    (workspace / "link.txt").symlink_to(workspace / "a.txt")

    # 本地与沙箱同口径：符号链接一律拒绝，且不得波及链接指向的目标文件
    with pytest.raises(ValueError, match="不允许操作符号链接"):
        lb._delete_local_path(str(root), "/mnt/user-data/workspace/link.txt", False, "file")
    assert (workspace / "a.txt").exists()
    with pytest.raises(FileNotFoundError, match="路径不存在"):
        lb._delete_local_path(str(root), "/mnt/user-data/workspace/missing.txt", False, "file")
    with pytest.raises(ValueError, match="不能作为dir删除"):
        lb._delete_local_path(str(root), "/mnt/user-data/workspace/a.txt", False, "dir")
    with pytest.raises(ValueError, match="不能作为file删除"):
        lb._delete_local_path(str(root), "/mnt/user-data/workspace/sub", False, "file")

    assert lb._delete_local_path(str(root), "/mnt/user-data/workspace/a.txt", False, "file") == {
        "path": "/mnt/user-data/workspace/a.txt",
        "type": "file",
    }
    assert not (workspace / "a.txt").exists()
    assert lb._delete_local_path(str(root), "/mnt/user-data/workspace/empty", False, "dir")["type"] == "dir"
    assert lb._delete_local_path(str(root), "/mnt/user-data/workspace/sub", True, "dir")["type"] == "dir"
    assert not (workspace / "sub").exists()


def test_move_local_path_branches(tmp_path):
    _runtime_obj, root = _runtime(tmp_path)
    workspace = root / "workspace"
    (workspace / "a.txt").write_text("a", encoding="utf-8")
    (workspace / "b.txt").write_text("b", encoding="utf-8")
    (workspace / "link.txt").symlink_to(workspace / "a.txt")

    # 移动链接同样被拒绝，且不能把目标文件搬走
    with pytest.raises(ValueError, match="不允许操作符号链接"):
        lb._move_local_path(str(root), "/mnt/user-data/workspace/link.txt", "/mnt/user-data/workspace/c.txt")
    assert (workspace / "a.txt").exists()
    assert not (workspace / "c.txt").exists()
    with pytest.raises(FileNotFoundError, match="源路径不存在"):
        lb._move_local_path(str(root), "/mnt/user-data/workspace/missing.txt", "/mnt/user-data/workspace/c.txt")
    with pytest.raises(ValueError, match="目标路径已存在"):
        lb._move_local_path(str(root), "/mnt/user-data/workspace/a.txt", "/mnt/user-data/workspace/b.txt")

    moved = lb._move_local_path(str(root), "/mnt/user-data/workspace/a.txt", "/mnt/user-data/workspace/moved.txt")
    assert moved["source_path"] == "/mnt/user-data/workspace/a.txt"
    assert moved["destination_path"] == "/mnt/user-data/workspace/moved.txt"
    assert (workspace / "moved.txt").read_text(encoding="utf-8") == "a"
    assert not (workspace / "a.txt").exists()


def test_copy_local_path_branches(tmp_path):
    _runtime_obj, root = _runtime(tmp_path)
    workspace = root / "workspace"
    uploads = root / "uploads"
    outputs = root / "outputs"
    (workspace / "note.txt").write_text("hello", encoding="utf-8")
    (workspace / "dir").mkdir()
    link = workspace / "link.txt"
    link.symlink_to(workspace / "note.txt")

    with pytest.raises(ValueError, match="系统结果目录"):
        lb._copy_local_path(
            str(root),
            f"/mnt/user-data/outputs/{cw_runtime_module.TOOL_OFFLOAD_DIR_NAME}/r.json",
            "/mnt/user-data/workspace/r.json",
        )
    with pytest.raises(FileNotFoundError, match="源路径不存在"):
        lb._copy_local_path(str(root), "/mnt/user-data/workspace/missing.txt", "/mnt/user-data/workspace/x.txt")
    with pytest.raises(ValueError, match="只支持复制单个文件"):
        lb._copy_local_path(str(root), "/mnt/user-data/workspace/dir", "/mnt/user-data/workspace/x.txt")
    with pytest.raises(ValueError, match="不允许操作符号链接"):
        lb._copy_local_resolved_path(
            str(root), link, "/mnt/user-data/workspace/link.txt", "/mnt/user-data/workspace/x.txt"
        )
    with pytest.raises(ValueError, match="目标路径已存在"):
        lb._copy_local_path(str(root), "/mnt/user-data/workspace/note.txt", "/mnt/user-data/workspace/note.txt")

    copied = lb._copy_local_path(str(root), "/mnt/user-data/workspace/note.txt", "/mnt/user-data/workspace/copy.txt")
    assert copied["bytes"] == 5
    assert copied["destination_path"] == "/mnt/user-data/workspace/copy.txt"
    assert (workspace / "copy.txt").read_text(encoding="utf-8") == "hello"

    # 目标父目录不存在
    with pytest.raises(ValueError, match="目标父目录不存在"):
        lb._copy_local_path(str(root), "/mnt/user-data/workspace/note.txt", "/mnt/user-data/workspace/nope/x.txt")
    assert uploads.is_dir() and outputs.is_dir()


# ---------------------------------------------------------------------------
# 技能路径解析
# ---------------------------------------------------------------------------


def _runtime_with_skill(tmp_path: Path) -> tuple[ConversationWorkspaceRuntime, Path]:
    runtime, root = _runtime(tmp_path)
    skill_dir = runtime.runtime_skills_dir / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: demo-skill\n---\n# Demo\n", encoding="utf-8")
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "guide.md").write_text("# guide\n", encoding="utf-8")
    outside = tmp_path / "outside-skill"
    outside.mkdir()
    (runtime.runtime_skills_dir / "evil-skill").symlink_to(outside, target_is_directory=True)
    return runtime, root


def test_resolve_local_skill_path_branches(tmp_path):
    runtime, _root = _runtime_with_skill(tmp_path)

    target, visible = lb._resolve_local_skill_path(runtime, "/mnt/skills/demo-skill/references/guide.md")
    assert target.name == "guide.md"
    assert visible == "/mnt/skills/demo-skill/references/guide.md"

    with pytest.raises(ValueError, match="只允许访问技能路径"):
        lb._resolve_local_skill_path(runtime, "/mnt/user-data/workspace/a.txt")
    with pytest.raises(ValueError, match="技能路径无效"):
        lb._resolve_local_skill_path(runtime, "/mnt/skills/")
    # 共享实现把 ".." 归类为越界(而非无效),语义更精确
    with pytest.raises(ValueError, match="技能路径越界"):
        lb._resolve_local_skill_path(runtime, "/mnt/skills/demo-skill/../escape")
    with pytest.raises(ValueError, match="技能不存在或无权限访问"):
        lb._resolve_local_skill_path(runtime, "/mnt/skills/evil-skill/SKILL.md")
    with pytest.raises(ValueError, match="技能不存在或无权限访问"):
        lb._resolve_local_skill_path(runtime, "/mnt/skills/unknown/SKILL.md")


def test_search_local_workspace_files_branches(tmp_path):
    runtime, root = _runtime_with_skill(tmp_path)
    workspace = root / "workspace"
    (workspace / "a.py").write_text("a", encoding="utf-8")
    (workspace / "sub").mkdir()
    (workspace / "sub" / "b.py").write_text("b", encoding="utf-8")

    found = lb._search_local_workspace_files(runtime, "/mnt/user-data/workspace", "*.py", 10)
    assert [item["path"] for item in found] == ["/mnt/user-data/workspace/a.py", "/mnt/user-data/workspace/sub/b.py"]
    assert lb._search_local_workspace_files(runtime, "/mnt/user-data/workspace", "*.py", 1) == [
        {"path": "/mnt/user-data/workspace/a.py", "type": "file"}
    ]
    assert lb._search_local_workspace_files(runtime, "", "*.nope", 10) == []

    skill_found = lb._search_local_workspace_files(
        runtime,
        "/mnt/skills/demo-skill",
        "*.md",
        10,
    )
    assert {item["path"] for item in skill_found} == {
        "/mnt/skills/demo-skill/SKILL.md",
        "/mnt/skills/demo-skill/references/guide.md",
    }

    with pytest.raises(ValueError, match="搜索路径必须是目录"):
        lb._search_local_workspace_files(runtime, "/mnt/user-data/workspace/a.py", "*", 10)


# ---------------------------------------------------------------------------
# ripgrep 检索
# ---------------------------------------------------------------------------


def test_check_rg_available_failure(monkeypatch):
    monkeypatch.setattr(lb, "_rg_available", None)

    def boom(*_args, **_kwargs):
        raise OSError("rg missing")

    monkeypatch.setattr(lb.subprocess, "run", boom)
    assert lb._check_rg_available() is False
    # 结果被缓存，第二次调用不再探测
    monkeypatch.setattr(lb.subprocess, "run", lambda *_a, **_k: None)
    assert lb._check_rg_available() is False
    monkeypatch.setattr(lb, "_rg_available", None)


def test_grep_local_workspace_text_branches(tmp_path, monkeypatch):
    runtime, root = _runtime_with_skill(tmp_path)
    (root / "workspace" / "a.py").write_text("hello world\n", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="路径不存在"):
        lb._grep_local_workspace_text(
            runtime,
            "/mnt/user-data/workspace/missing-dir",
            ["x"],
            "**/*",
            10,
            100,
        )

    monkeypatch.setattr(lb, "_rg_available", False)
    with pytest.raises(RuntimeError, match="ripgrep"):
        lb._grep_local_workspace_text(runtime, "/mnt/user-data/workspace", ["x"], "**/*", 10, 100)

    monkeypatch.setattr(lb, "_rg_available", True)

    def failing(*_args, **_kwargs):
        return SimpleNamespace(returncode=2, stdout=b"", stderr=b"bad flag")

    monkeypatch.setattr(lb.subprocess, "run", failing)
    with pytest.raises(RuntimeError, match="rg 执行失败"):
        lb._grep_local_workspace_text(runtime, "/mnt/user-data/workspace", ["x"], "*.py", 10, 100)

    match_line = (
        '{"type": "match", "data": {"path": {"text": "%s"}, "line_number": 1, '
        '"lines": {"text": "hello world\\n"}}}' % (root / "workspace" / "a.py")
    )

    def ok(*args, **_kwargs):
        assert "--glob" in args[0]  # 非 **/* 的 glob 会透传给 rg
        return SimpleNamespace(returncode=0, stdout=match_line.encode(), stderr=b"")

    monkeypatch.setattr(lb.subprocess, "run", ok)
    payload = lb._grep_local_workspace_text(runtime, "/mnt/user-data/workspace", ["hello"], "*.py", 10, 100)
    assert payload["matches"] == [
        {
            "path": "/mnt/user-data/workspace/a.py",
            "line_number": 1,
            "line": "hello world",
        }
    ]
    assert payload["has_more"] is False


def test_grep_local_workspace_text_skill_paths(tmp_path, monkeypatch):
    runtime, _root = _runtime_with_skill(tmp_path)
    skill_guide = runtime.runtime_skills_dir / "demo-skill" / "references" / "guide.md"
    monkeypatch.setattr(lb, "_rg_available", True)

    match_line = (
        f'{{"type": "match", "data": {{"path": {{"text": "{skill_guide}"}}, "line_number": 1, '
        '"lines": {"text": "# guide"}}}'
    )

    def ok(*_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout=match_line.encode(), stderr=b"")

    monkeypatch.setattr(lb.subprocess, "run", ok)
    payload = lb._grep_local_workspace_text(
        runtime,
        "/mnt/skills/demo-skill",
        ["guide"],
        "**/*",
        10,
        100,
    )
    assert payload["matches"][0]["path"].startswith("/mnt/skills/demo-skill")


def test_grep_local_workspace_text_regex_case_context_and_modes(tmp_path, monkeypatch):
    """file_grep 增强：正则/忽略大小写/上下文归属与 files/count 聚合（真实 rg 验证）。"""
    runtime, root = _runtime_with_skill(tmp_path)
    ws = root / "workspace"
    (ws / "a.py").write_text(
        "def one():\n    return 1\n\ndef two_impl():\n    return 2\n\ndef three_impl():\n    return 3\n",
        encoding="utf-8",
    )
    (ws / "b.md").write_text("some Error here\nand error there\n", encoding="utf-8")
    monkeypatch.setattr(lb, "_rg_available", True)

    # 正则：-F 字面量模式下无法命中的 \w+ 模式
    payload = lb._grep_local_workspace_text(
        runtime,
        "/mnt/user-data/workspace",
        [r"def \w+_impl"],
        "**/*",
        10,
        20000,
        use_regex=True,
    )
    assert [m["line_number"] for m in payload["matches"]] == [4, 7]

    # 忽略大小写：字面 error 命中 Error/error 两行
    payload = lb._grep_local_workspace_text(
        runtime,
        "/mnt/user-data/workspace",
        ["error"],
        "*.md",
        10,
        20000,
        ignore_case=True,
    )
    assert [m["line_number"] for m in payload["matches"]] == [1, 2]

    # 上下文归属：前 1 行是空行（第 3 行），后 1 行是 return 2
    payload = lb._grep_local_workspace_text(
        runtime,
        "/mnt/user-data/workspace",
        ["two_impl"],
        "*.py",
        10,
        20000,
        context_lines=1,
    )
    match = payload["matches"][0]
    assert match["line_number"] == 4
    assert match["before"] == [""]
    assert match["after"] == ["    return 2"]

    # count 模式：可见路径 + 每文件命中计数
    payload = lb._grep_local_workspace_text(
        runtime,
        "/mnt/user-data/workspace",
        ["impl"],
        "**/*",
        10,
        20000,
        output_mode="count",
    )
    assert payload["entries"] == [{"path": "/mnt/user-data/workspace/a.py", "match_count": 2}]

    # files_with_matches 模式：仅命中文件清单
    payload = lb._grep_local_workspace_text(
        runtime,
        "/mnt/user-data/workspace",
        ["impl"],
        "**/*",
        10,
        20000,
        output_mode="files_with_matches",
    )
    assert payload["entries"] == [{"path": "/mnt/user-data/workspace/a.py"}]


def test_grep_context_multi_file_multi_hit_no_pollution(tmp_path, monkeypatch):
    """真实 rg 多文件多命中回归：before 取紧邻行、跨文件不污染、预算按结果计费。"""
    runtime, root = _runtime(tmp_path)
    ws = root / "workspace"
    # 多命中同文件：HIT-1 与 HIT-2 相距 2×context，旧实现的「保留最早 N 行」会取错
    (ws / "code.txt").write_text("HIT\nL2\nL3\nL4\nL5\nHIT\n", encoding="utf-8")
    # 前一文件尾部 context 行（c3/c4）曾会污染下一文件的首个命中
    (ws / "zzz.txt").write_text("c1\nc2\nHIT\nc3\nc4\n", encoding="utf-8")
    (ws / "aaa.txt").write_text("HIT\n", encoding="utf-8")
    monkeypatch.setattr(lb, "_rg_available", True)

    payload = lb._grep_local_workspace_text(
        runtime,
        "/mnt/user-data/workspace",
        ["HIT"],
        "**/*",
        50,
        100000,
        context_lines=2,
    )
    by_key = {(m["path"].rsplit("/", 1)[-1], m["line_number"]): m for m in payload["matches"]}
    # 滑动窗口：第二处命中拿到的是紧邻的 L4/L5，而不是命中间最早的 L2/L3
    assert by_key[("code.txt", 6)].get("before") == ["L4", "L5"]
    assert by_key[("code.txt", 6)].get("after") is None
    # 文件边界重置：code.txt 首个命中不挂 zzz.txt 的尾部行
    assert by_key[("code.txt", 1)].get("before") is None
    assert by_key[("zzz.txt", 3)].get("before") == ["c1", "c2"]
    assert by_key[("zzz.txt", 3)].get("after") == ["c3", "c4"]
    assert by_key[("aaa.txt", 1)].get("before") is None

    # 预算按结果计费：紧预算（30 字符，足够 3-4 条命中）下截断发生在贴近预算处，
    # 而不是被上下文行重复计费提前腰斩
    payload = lb._grep_local_workspace_text(
        runtime,
        "/mnt/user-data/workspace",
        ["HIT"],
        "**/*",
        50,
        30,
        context_lines=2,
    )
    assert payload["has_more"] is True
    used = sum(
        len(m["line"]) + 1 + sum(len(x) + 1 for x in m.get("before", []) + m.get("after", []))
        for m in payload["matches"]
    )
    assert used <= 30
    assert used >= 30 * 0.6  # 旧实现同预算下有效载荷不足一半


def test_grep_context_trailing_after_respects_max_chars(tmp_path, monkeypatch):
    """真实 rg 回归：尾随 after 上下文受 max_chars 硬上限，不得顶破预算。"""
    runtime, root = _runtime(tmp_path)
    ws = root / "workspace"
    (ws / "tail.txt").write_text(
        "HIT\n" + "A" * 100 + "\n" + "B" * 100 + "\n" + "C" * 100 + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(lb, "_rg_available", True)

    payload = lb._grep_local_workspace_text(
        runtime,
        "/mnt/user-data/workspace",
        ["HIT"],
        "tail.txt",
        50,
        50,
        context_lines=3,
    )
    # 剩余预算装不下任何一条 after 行：全部不进入结果，has_more 置位
    assert len(payload["matches"]) == 1
    assert payload["matches"][0].get("after") is None
    assert payload["has_more"] is True
    used = sum(
        len(m["line"]) + 1 + sum(len(x) + 1 for x in m.get("before", []) + m.get("after", []))
        for m in payload["matches"]
    )
    assert used <= 50


# ---------------------------------------------------------------------------
# 后端异步入口
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backend_dir_operations_delegate_to_local_helpers(tmp_path):
    runtime, root = _runtime(tmp_path)
    backend = runtime.backend

    created = await backend.create_dir(runtime, path="/mnt/user-data/workspace/via-backend")
    assert created["path"] == "/mnt/user-data/workspace/via-backend"
    deleted = await backend.delete_path(
        runtime,
        path="/mnt/user-data/workspace/via-backend",
        recursive=False,
        expected_type="dir",
    )
    assert deleted["type"] == "dir"
    (root / "workspace" / "src.txt").write_text("s", encoding="utf-8")
    moved = await backend.move_path(
        runtime,
        source_path="/mnt/user-data/workspace/src.txt",
        destination_path="/mnt/user-data/workspace/dst.txt",
    )
    assert moved["destination_path"] == "/mnt/user-data/workspace/dst.txt"


@pytest.mark.asyncio
async def test_backend_list_and_search_refresh_skill_snapshot(tmp_path, monkeypatch):
    runtime, _root = _runtime_with_skill(tmp_path)
    backend = runtime.backend
    refreshed: list[bool] = []

    async def fake_ensure():
        refreshed.append(True)

    monkeypatch.setattr(runtime, "_ensure_snapshot_async", fake_ensure)

    entries = await backend.list_dir(runtime, path="/mnt/skills/demo-skill", max_depth=2)
    assert any("SKILL.md" in str(item["path"]) for item in entries)
    found = await backend.search_files(
        runtime,
        path="/mnt/skills/demo-skill",
        pattern="*.md",
        max_results=5,
    )
    assert found
    assert refreshed == [True, True]


@pytest.mark.asyncio
async def test_backend_copy_from_skill_snapshot(tmp_path):
    runtime, root = _runtime_with_skill(tmp_path)
    backend = runtime.backend
    copied = await backend.copy_path(
        runtime,
        source_path="/mnt/skills/demo-skill/SKILL.md",
        destination_path="/mnt/user-data/workspace/from-skill.md",
    )
    assert copied["type"] == "file"
    assert (root / "workspace" / "from-skill.md").exists()


@pytest.mark.asyncio
async def test_backend_render_office_html_delegates(tmp_path, monkeypatch):
    runtime, _root = _runtime(tmp_path)
    seen = {}

    def fake_render(workspace_root, file_path, max_chars):
        seen.update({"root": workspace_root, "file_path": file_path, "max_chars": max_chars})
        return {"renderer": "officecli-html", "html": "<html/>", "truncated": False}

    monkeypatch.setattr(lb, "_render_local_office_html", fake_render)
    result = await runtime.backend.render_office_html(
        runtime,
        file_path="/mnt/user-data/workspace/a.docx",
        max_chars=50,
    )
    assert result["html"] == "<html/>"
    assert seen["file_path"] == "/mnt/user-data/workspace/a.docx" and seen["max_chars"] == 50


@pytest.mark.asyncio
async def test_run_bash_timeout_and_skill_cwd(tmp_path):
    runtime, _root = _runtime_with_skill(tmp_path)
    if shutil.which("bash") is None:
        pytest.skip("宿主机未安装 bash")

    timed_out = await runtime.run_bash(command="sleep 5", cwd=None, timeout_seconds=1)
    assert timed_out["timed_out"] is True
    assert timed_out["exit_code"] == 124
    assert timed_out["stderr"] == "command timed out"

    from_skill = await runtime.run_bash(
        command="pwd",
        cwd="/mnt/skills/demo-skill",
        timeout_seconds=30,
    )
    assert from_skill["exit_code"] == 0
    # 技能 cwd 返回模型可见的虚拟路径，不泄漏 runtime-skills 物理位置
    assert from_skill["cwd"] == "/mnt/skills/demo-skill"


@pytest.mark.asyncio
async def test_run_officecli_requires_binary_and_rejects_skill_cwd(tmp_path, monkeypatch):
    runtime, _root = _runtime_with_skill(tmp_path)
    monkeypatch.setattr(lb.shutil, "which", lambda _name: None)
    with pytest.raises(RuntimeError, match="未安装 OfficeCLI"):
        await runtime.run_officecli(args=["view", "a.docx", "text"], cwd=None, timeout_seconds=10)

    monkeypatch.setattr(lb.shutil, "which", lambda _name: "/usr/bin/officecli")
    with pytest.raises(ValueError, match="不允许将 cwd 设置为技能目录"):
        await runtime.run_officecli(
            args=["view", "a.docx", "text"],
            cwd="/mnt/skills/demo-skill",
            timeout_seconds=10,
        )


# ---------------------------------------------------------------------------
# 本地 / 沙箱符号链接口径对齐
# ---------------------------------------------------------------------------


def test_local_rejects_symlink_operations_and_keeps_target_intact(tmp_path):
    """本地后端与沙箱后端一致：符号链接不可读写/拷贝，列表则跳过而不是报错。"""
    _runtime_obj, root = _runtime(tmp_path)
    workspace = root / "workspace"
    (workspace / "real.txt").write_text("payload", encoding="utf-8")
    (workspace / "link.txt").symlink_to(workspace / "real.txt")
    (workspace / "dirlink").symlink_to(workspace)

    for path in ("/mnt/user-data/workspace/link.txt", "/mnt/user-data/workspace/dirlink"):
        with pytest.raises(ValueError, match="不允许操作符号链接"):
            cw_runtime_module.resolve_workspace_path(str(root), path)
        with pytest.raises(ValueError, match="不允许操作符号链接"):
            cw_runtime_module.resolve_workspace_path(str(root), path, writable=True)

    # 目标文件必须毫发无损
    assert (workspace / "real.txt").read_text(encoding="utf-8") == "payload"

    # 列表口径同样对齐沙箱：跳过链接，不抛错、也不泄漏目标内容
    listed = lb._list_local_workspace_dir(
        SimpleNamespace(workspace_root=str(root), runtime_skills_dir=None),
        "/mnt/user-data/workspace",
        3,
    )
    paths = {item["path"] for item in listed}
    assert "/mnt/user-data/workspace/real.txt" in paths
    assert not any("link" in p for p in paths)


# ---------------------------------------------------------------------------
# 间接写入变更归因（manifest 前后对比）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_bash_reports_observed_changed_files(tmp_path):
    """命令落盘的文件按 created 归因；无写入命令返回空归因。"""
    runtime, _root = _runtime(tmp_path)
    if shutil.which("bash") is None:
        pytest.skip("宿主机未安装 bash")

    result = await runtime.run_bash(
        command="echo hi > /mnt/user-data/workspace/gen.txt",
        cwd=None,
        timeout_seconds=30,
    )
    assert result["exit_code"] == 0
    assert result["changed_files"]["created"] == ["/mnt/user-data/workspace/gen.txt"]
    assert result["changed_files"]["updated"] == []
    assert result["changed_files"]["deleted"] == []
    assert result["changed_files"]["truncated"] is False

    noop = await runtime.run_bash(command="true", cwd=None, timeout_seconds=30)
    assert noop["changed_files"] == {"created": [], "updated": [], "deleted": [], "truncated": False}


@pytest.mark.asyncio
async def test_run_bash_inherits_officecli_resident_opt_out(tmp_path):
    """agent 可能经 bash_run 调 officecli：子 shell 环境同样禁用 resident 自动拉起。"""
    runtime, _root = _runtime(tmp_path)
    if shutil.which("bash") is None:
        pytest.skip("宿主机未安装 bash")

    result = await runtime.run_bash(command="echo $OFFICECLI_NO_AUTO_RESIDENT", cwd=None, timeout_seconds=30)
    assert result["exit_code"] == 0
    assert result["stdout"].strip() == "1"


@pytest.mark.asyncio
async def test_run_bash_timeout_still_attributes_writes(tmp_path):
    """超时命令可能已写入部分产物：超时路径同样归因（宁可不错过）。"""
    runtime, _root = _runtime(tmp_path)
    if shutil.which("bash") is None:
        pytest.skip("宿主机未安装 bash")

    result = await runtime.run_bash(
        command="echo partial > /mnt/user-data/workspace/partial.txt && sleep 5",
        cwd=None,
        timeout_seconds=1,
    )
    assert result["timed_out"] is True
    assert result["changed_files"]["created"] == ["/mnt/user-data/workspace/partial.txt"]


@pytest.mark.asyncio
async def test_run_officecli_reports_observed_changed_files(tmp_path, monkeypatch):
    """officecli 写入类命令的产物按 created 归因（fake 二进制向 $4 写文件模拟 -o 输出）。"""
    runtime, _root = _runtime(tmp_path)
    (Path(runtime.workspace_root) / "workspace" / "a.docx").write_bytes(b"docx")
    fake_bin = tmp_path / "fake-officecli.sh"
    fake_bin.write_text('#!/bin/bash\necho pdf-data > "$4"\n')
    fake_bin.chmod(0o755)
    monkeypatch.setattr(lb.shutil, "which", lambda _name: str(fake_bin))

    result = await runtime.run_officecli(
        args=["view", "/mnt/user-data/workspace/a.docx", "--out", "/mnt/user-data/workspace/report.pdf"],
        cwd="/mnt/user-data/workspace",
        timeout_seconds=30,
    )
    assert result["exit_code"] == 0
    assert result["changed_files"]["created"] == ["/mnt/user-data/workspace/report.pdf"]
