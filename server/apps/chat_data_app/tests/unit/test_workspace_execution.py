import asyncio
from datetime import timedelta
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")

from apps.chat_data_app.agent.tools import bash_run
from apps.chat_data_app.services.agent_run.conversation_workspace import (
    ConversationWorkspaceRuntime,
    SandboxWorkspaceBackend,
    WorkspacePathMapping,
    _absolute_command_path_tokens,
)
from apps.chat_data_app.agent.workspace.workspace_sandbox_api import (
    SandboxExecutionSession,
    SandboxExecutionSessionRef,
)
from apps.chat_data_app.agent.workspace.workspace_sandbox_paths import (
    sandbox_visible_path,
)
from apps.chat_data_app.agent.workspace import (
    workspace_ops as we_ops,
    workspace_sandbox_api as we_api,
    workspace_sandbox_paths as we_paths,
    workspace_search as we_search,
    workspace_session as we_session,
    workspace_sync as we_sync,
)


def _workspace_root(tmp_path: Path) -> Path:
    # 新布局：threads/<uid>/<yyyy-MM-DD>/<cid>/{user-data/{workspace,outputs,uploads}, runtime-skills/}
    # 本用例直接手工搭目录传 workspace_root，不经 ensure_conversation_workspace，日期层不参与。
    root = tmp_path / "chat-data" / "threads" / "user_a" / "conv_a" / "user-data"
    for name in ("workspace", "outputs", "uploads"):
        (root / name).mkdir(parents=True, exist_ok=True)
    (root.parent / "runtime-skills").mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture(autouse=True)
def _disable_bash_kill_switch(monkeypatch):
    """bash_run 的代码级总闸（BASH_RUN_DISABLED=True）默认拒绝一切执行；
    本文件测的是执行/路径校验机制本身，统一绕过总闸（总闸行为在 test_bash_run.py 覆盖）。"""
    monkeypatch.setattr(bash_run, "BASH_RUN_DISABLED", False)


def test_validate_backend_requires_absolute_chat_data_home(monkeypatch):
    # sandbox 模式将应用侧会话目录通过 Files API 上传，仍要求持久化根为绝对路径。
    monkeypatch.setenv("CHAT_DATA_HOME", "uploads/chat_data")
    with pytest.raises(ValueError, match="绝对路径"):
        we_session._validate_workspace_backend_config()

    monkeypatch.setenv("CHAT_DATA_HOME", "/data/sandbox/workspace/chat_data")
    we_session._validate_workspace_backend_config()


def test_sandbox_visible_path_allows_workspace_and_flat_skills(tmp_path):
    # 运行域技能统一为扁平 /mnt/skills/<name>，不再按类别/挂载逐条校验；
    # sandbox_visible_path 仅做路径级放行与越界校验，文件存在性由运行时快照层保证。
    assert sandbox_visible_path(
        "/mnt/skills/data-analyzing/references/guide.md"
    ) == "/mnt/skills/data-analyzing/references/guide.md"
    # URL 编码只允许出现在规范前缀内；编码短形式按非规范输入拒绝。
    assert sandbox_visible_path("/mnt/user-data%2Foutputs%2Fdashboard.dashboard.json") == "/mnt/user-data/outputs/dashboard.dashboard.json"
    with pytest.raises(ValueError, match="/mnt/user-data/"):
        sandbox_visible_path("outputs%2Fdashboard.dashboard.json")
    assert sandbox_visible_path("/mnt/user-data/outputs/.tool-offload/result_1.json") == "/mnt/user-data/outputs/.tool-offload/result_1.json"
    with pytest.raises(ValueError, match="不允许通过文件编辑工具"):
        sandbox_visible_path("/mnt/user-data/outputs/.tool-offload/result_1.json", writable=True)
    with pytest.raises(ValueError, match="语义模型只读快照"):
        sandbox_visible_path("/mnt/user-data/workspace/semantic/model.yaml", writable=True)
    assert sandbox_visible_path(
        "/mnt/user-data/workspace/semantic/model.yaml",
        writable=True,
        allow_managed_semantic_model_write=True,
    ) == "/mnt/user-data/workspace/semantic/model.yaml"
    # 扁平模型下无逐技能门控：未知技能路径在路径层被放行（越权由运行时快照文件不存在拦截）。
    assert sandbox_visible_path("/mnt/skills/other-skill/SKILL.md") == "/mnt/skills/other-skill/SKILL.md"
    with pytest.raises(ValueError, match="/mnt/user-data/ 或 /mnt/skills/ 开头"):
        sandbox_visible_path("outputs%2F..%2Fsecret.txt")
    # 技能路径只读，写入被拒。
    with pytest.raises(ValueError, match="只允许写入"):
        sandbox_visible_path(
            "/mnt/skills/data-analyzing/SKILL.md",
            writable=True,
        )


def test_sandbox_artifact_payload_rejects_hidden_tool_result():
    session_ref = SandboxExecutionSessionRef()
    session_ref.current = SandboxExecutionSession(
        sandbox=object(),
        sandbox_id="sbx_1",
        workspace_root="/host/user-data",
    )

    payload = asyncio.run(we_ops.sandbox_artifact_payload(
        session_ref=session_ref,
        file_path="/mnt/user-data/outputs/.tool-offload/result_1.json",
    ))

    assert payload == {}


def test_sandbox_path_requires_a_skill_name():
    with pytest.raises(ValueError, match="技能路径必须包含技能名"):
        sandbox_visible_path("/mnt/skills")


def test_bash_path_validation_allows_officecli_dom_selectors():
    command = (
        "officecli get '/mnt/user-data/workspace/报告.docx' /body/p[1] "
        "--depth 2 --json"
    )
    assert _absolute_command_path_tokens(command) == ["/mnt/user-data/workspace/报告.docx"]
    assert _absolute_command_path_tokens(
        "officecli get '/mnt/user-data/workspace/销售.xlsx' '/Sheet1/A1:D20' --json"
    ) == ["/mnt/user-data/workspace/销售.xlsx"]
    assert _absolute_command_path_tokens(
        "officecli add '/mnt/user-data/workspace/报告.docx' /body --type paragraph"
    ) == ["/mnt/user-data/workspace/报告.docx"]


def test_bash_path_validation_still_rejects_real_absolute_paths():
    assert _absolute_command_path_tokens("cat /etc/passwd") == ["/etc/passwd"]


def test_sandbox_read_text_file_uses_resolved_path_and_returns_visible_path(monkeypatch):
    captured = {}

    class FakeFiles:
        async def get_file_info(self, paths):
            captured["info_paths"] = paths
            return {
                paths[0]: SimpleNamespace(
                    path=paths[0], entry_type="file", size=4, modified_at="2026-08-10T00:00:00Z",
                )
            }

        async def read_bytes(self, path, *, range_header=None, offset=None, limit=None):
            captured.setdefault("reads", []).append({
                "path": path,
                "range_header": range_header,
                "offset": offset,
                "limit": limit,
            })
            return b"row\n"

    session_ref = SandboxExecutionSessionRef()
    session_ref.current = SandboxExecutionSession(
        sandbox=SimpleNamespace(files=FakeFiles()),
        sandbox_id="sbx_1",
        workspace_root="/host/user-data",
    )

    async def fake_resolve(**kwargs):
        captured["resolve"] = kwargs
        return "/mnt/user-data/workspace/report.txt"

    monkeypatch.setattr(we_ops, "_resolve_sandbox_real_path", fake_resolve)

    async def fake_line_count(**_kwargs):
        return 1

    monkeypatch.setattr(we_ops, "_sandbox_text_line_count", fake_line_count)

    result = asyncio.run(we_ops.sandbox_read_text_file(
        session_ref=session_ref,
        file_path="/mnt/user-data/workspace/report.txt",
        offset=0,
        limit=200,
        max_chars=25000,
    ))

    assert captured["resolve"]["file_path"] == "/mnt/user-data/workspace/report.txt"
    assert captured["info_paths"] == ["/mnt/user-data/workspace/report.txt"]
    assert captured["reads"] == [
        {
            "path": "/mnt/user-data/workspace/report.txt",
            "range_header": "bytes=0-4095",
            "offset": None,
            "limit": None,
        },
        {
            "path": "/mnt/user-data/workspace/report.txt",
            "range_header": None,
            "offset": 1,
            "limit": 201,
        },
    ]
    assert result["path"] == "/mnt/user-data/workspace/report.txt"
    assert result["total_lines"] == 1
    assert result["has_more_lines"] is False


def test_sandbox_read_text_file_keeps_content_when_line_count_fails(monkeypatch):
    class FakeFiles:
        async def get_file_info(self, paths):
            return {
                paths[0]: SimpleNamespace(
                    path=paths[0], entry_type="file", size=8, modified_at="2026-08-10T00:00:00Z",
                )
            }

        async def read_bytes(self, _path, *, range_header=None, offset=None, limit=None):
            if range_header:
                return b"one\ntwo\n"
            return b"one\ntwo\n"

    session_ref = SandboxExecutionSessionRef()
    session_ref.current = SandboxExecutionSession(
        sandbox=SimpleNamespace(files=FakeFiles()),
        sandbox_id="sbx_1",
        workspace_root="/host/user-data",
    )

    async def fake_resolve(**_kwargs):
        return "/mnt/user-data/workspace/report.txt"

    async def fail_line_count(**_kwargs):
        raise RuntimeError("awk unavailable")

    monkeypatch.setattr(we_api, "_resolve_sandbox_real_path", fake_resolve)
    monkeypatch.setattr(we_ops, "_sandbox_text_line_count", fail_line_count)

    result = asyncio.run(we_ops.sandbox_read_text_file(
        session_ref=session_ref,
        file_path="/mnt/user-data/workspace/report.txt",
        offset=0,
        limit=200,
        max_chars=25000,
    ))

    assert result["lines"] == ["one", "two"]
    assert result["total_lines"] is None
    assert result["has_more"] is False


def test_sandbox_text_line_count_uses_metadata_cache(monkeypatch):
    calls = []
    session = SandboxExecutionSession(
        sandbox=object(),
        sandbox_id="sbx_1",
        workspace_root="/host/user-data",
    )

    async def fake_run_command_in_sandbox(*, session, command, timeout_seconds):
        calls.append((session, command, timeout_seconds))
        return 0, "3\n", ""

    monkeypatch.setattr(we_ops, "run_command_in_sandbox", fake_run_command_in_sandbox)
    path = "/mnt/user-data/workspace/report file.txt"
    first_entry = SimpleNamespace(size=20, modified_at="2026-08-10T00:00:00Z")

    first = asyncio.run(we_ops._sandbox_text_line_count(
        session=session, path=path, entry=first_entry,
    ))
    second = asyncio.run(we_ops._sandbox_text_line_count(
        session=session, path=path, entry=first_entry,
    ))
    changed = asyncio.run(we_ops._sandbox_text_line_count(
        session=session,
        path=path,
        entry=SimpleNamespace(size=24, modified_at="2026-08-10T00:00:01Z"),
    ))

    assert first == second == changed == 3
    assert len(calls) == 2
    assert calls[0][1] == "awk 'END { print NR }' < '/mnt/user-data/workspace/report file.txt'"
    assert calls[0][2] == 30


@pytest.mark.parametrize(
    ("command_result", "expected_error"),
    [
        ((1, "", "permission denied"), "permission denied"),
        ((0, "not-a-number", ""), "沙箱文本行数统计结果无效"),
    ],
)
def test_sandbox_text_line_count_rejects_command_failures(monkeypatch, command_result, expected_error):
    session = SandboxExecutionSession(
        sandbox=object(),
        sandbox_id="sbx_1",
        workspace_root="/host/user-data",
    )

    async def fake_run_command_in_sandbox(**_kwargs):
        return command_result

    monkeypatch.setattr(we_ops, "run_command_in_sandbox", fake_run_command_in_sandbox)

    with pytest.raises(RuntimeError, match=expected_error):
        asyncio.run(we_ops._sandbox_text_line_count(
            session=session,
            path="/mnt/user-data/workspace/report.txt",
            entry=SimpleNamespace(size=4, modified_at="2026-08-10T00:00:00Z"),
        ))


def test_sandbox_file_edit_uses_replace_contents_and_persists_result(monkeypatch, tmp_path):
    workspace_root = _workspace_root(tmp_path)
    target = workspace_root / "workspace" / "report.txt"
    target.write_text("status=old", encoding="utf-8")
    monkeypatch.setattr(we_paths, "CHAT_DATA_HOME", str(tmp_path / "chat-data"))

    class FakeFiles:
        async def replace_contents(self, entries):
            assert len(entries) == 1
            entry = entries[0]
            assert entry.path == "/mnt/user-data/workspace/report.txt"
            assert entry.old_content == "old"
            assert entry.new_content == "new"
            remote[entry.path] = remote[entry.path].replace(
                entry.old_content.encode("utf-8"), entry.new_content.encode("utf-8")
            )

    remote = {"/mnt/user-data/workspace/report.txt": b"status=old"}
    session_ref = SandboxExecutionSessionRef()
    session_ref.current = SandboxExecutionSession(
        sandbox=SimpleNamespace(files=FakeFiles()),
        sandbox_id="sbx_1",
        workspace_root=str(workspace_root),
        application_manifest=we_sync._mutable_application_manifest(workspace_root),
    )

    async def fake_resolve(**_kwargs):
        return "/mnt/user-data/workspace/report.txt"

    async def fake_read(**kwargs):
        return remote[kwargs["path"]]

    monkeypatch.setattr(we_ops, "_resolve_sandbox_real_path", fake_resolve)
    monkeypatch.setattr(we_ops, "_read_sandbox_file_bytes", fake_read)
    # persist 下载走 sync 模块自己的绑定，需一并打桩
    monkeypatch.setattr(we_sync, "_read_sandbox_file_bytes", fake_read)
    monkeypatch.setattr(we_api, "_import_opensandbox_content_replace_entry",
        lambda: lambda **kwargs: SimpleNamespace(**kwargs),
    )

    payload = asyncio.run(we_ops.sandbox_replace_text_file(
        session_ref=session_ref,
        file_path="/mnt/user-data/workspace/report.txt",
        old_content="old",
        new_content="new",
    ))

    assert payload["operation"] == "edit"
    assert payload["replaced_count"] == 1
    assert target.read_text(encoding="utf-8") == "status=new"


@pytest.mark.parametrize(
    ("writer", "file_path", "expected_mode"),
    [
        (we_ops.sandbox_write_text_file, "/mnt/user-data/workspace/report.txt", 666),
        (
            we_ops.sandbox_write_tool_result,
            "outputs/.tool-offload/result.txt",
            444,
        ),
    ],
)
def test_sandbox_writers_use_expected_file_mode(
    writer,
    file_path,
    expected_mode,
    monkeypatch,
):
    captured = {}
    session_ref = SandboxExecutionSessionRef()
    session_ref.current = SandboxExecutionSession(
        sandbox=SimpleNamespace(files=object()),
        sandbox_id="sbx_1",
        workspace_root="/host/user-data",
    )

    async def fake_resolve(**_kwargs):
        return f"/mnt/user-data/{file_path}"

    async def fake_write(**kwargs):
        captured.update(kwargs)

    async def fake_read(**_kwargs):
        raise FileNotFoundError

    async def fake_persist(**_kwargs):
        return None

    monkeypatch.setattr(we_ops, "_resolve_sandbox_real_path", fake_resolve)
    monkeypatch.setattr(we_ops, "_write_sandbox_file", fake_write)
    monkeypatch.setattr(we_ops, "_read_sandbox_file_bytes", fake_read)
    monkeypatch.setattr(we_ops, "_persist_sandbox_paths", fake_persist)
    monkeypatch.setattr(we_ops, "_create_sandbox_directories",
        lambda **_kwargs: asyncio.sleep(0),
    )

    kwargs = {
        "session_ref": session_ref,
        "file_path": file_path,
        "content": "content",
    }
    if writer is we_ops.sandbox_write_text_file:
        kwargs["mode"] = "overwrite"
    asyncio.run(writer(**kwargs))

    assert captured["mode"] == expected_mode


def test_sandbox_file_copy_reads_source_and_persists_destination(monkeypatch, tmp_path):
    workspace_root = _workspace_root(tmp_path)
    monkeypatch.setattr(we_paths, "CHAT_DATA_HOME", str(tmp_path / "chat-data"))
    remote = {"/mnt/user-data/uploads/source.docx": b"\x00docx"}

    class FakeFiles:
        async def read_bytes(self, path):
            return remote[path]

        async def write_file(self, path, data, mode=None):
            remote[path] = data

    session_ref = SandboxExecutionSessionRef()
    session_ref.current = SandboxExecutionSession(
        sandbox=SimpleNamespace(files=FakeFiles()),
        sandbox_id="sbx_copy",
        workspace_root=str(workspace_root),
        application_manifest=we_sync._mutable_application_manifest(workspace_root),
    )

    async def fake_kind(session, path, entry=None):
        if path == "/mnt/user-data/uploads/source.docx":
            return "file"
        if path == "/mnt/user-data/workspace":
            return "dir"
        raise FileNotFoundError(f"路径不存在: {path}")

    monkeypatch.setattr(we_ops, "_sandbox_path_kind", fake_kind)

    payload = asyncio.run(we_ops.sandbox_copy_path(
        session_ref=session_ref,
        source_path="/mnt/user-data/uploads/source.docx",
        destination_path="/mnt/user-data/workspace/copy.docx",
    ))

    assert payload["type"] == "file"
    assert payload["bytes"] == len(b"\x00docx")
    assert payload["source_path"].endswith("/uploads/source.docx")
    assert remote["/mnt/user-data/workspace/copy.docx"] == b"\x00docx"
    assert (workspace_root / "workspace" / "copy.docx").read_bytes() == b"\x00docx"


def test_sandbox_file_copy_rejects_tool_offload_source_and_existing_destination(monkeypatch, tmp_path):
    workspace_root = _workspace_root(tmp_path)
    monkeypatch.setattr(we_paths, "CHAT_DATA_HOME", str(tmp_path / "chat-data"))
    session_ref = SandboxExecutionSessionRef()
    session_ref.current = SandboxExecutionSession(
        sandbox=SimpleNamespace(files=SimpleNamespace()),
        sandbox_id="sbx_copy_guard",
        workspace_root=str(workspace_root),
        application_manifest=we_sync._mutable_application_manifest(workspace_root),
    )

    with pytest.raises(ValueError, match="tool-offload"):
        asyncio.run(we_ops.sandbox_copy_path(
            session_ref=session_ref,
            source_path="/mnt/user-data/outputs/.tool-offload/r.json",
            destination_path="/mnt/user-data/workspace/e.json",
        ))

    async def fake_kind(session, path, entry=None):
        return "file"

    monkeypatch.setattr(we_ops, "_sandbox_path_kind", fake_kind)
    with pytest.raises(ValueError, match="目标路径已存在"):
        asyncio.run(we_ops.sandbox_copy_path(
            session_ref=session_ref,
            source_path="/mnt/user-data/uploads/a.bin",
            destination_path="/mnt/user-data/workspace/b.bin",
        ))


def test_sandbox_file_edit_rejects_missing_or_nonunique_content(monkeypatch):
    session_ref = SandboxExecutionSessionRef()
    session_ref.current = SandboxExecutionSession(
        sandbox=SimpleNamespace(files=object()),
        sandbox_id="sbx_1",
        workspace_root="/tmp/chat-data/threads/u/c/user-data",
    )

    async def fake_resolve(**_kwargs):
        return "/mnt/user-data/workspace/report.txt"

    async def fake_read(**_kwargs):
        return b"old old"

    monkeypatch.setattr(we_ops, "_resolve_sandbox_real_path", fake_resolve)
    monkeypatch.setattr(we_ops, "_read_sandbox_file_bytes", fake_read)

    with pytest.raises(ValueError, match="匹配多处"):
        asyncio.run(we_ops.sandbox_replace_text_file(
            session_ref=session_ref,
            file_path="/mnt/user-data/workspace/report.txt",
            old_content="old",
            new_content="new",
        ))
    with pytest.raises(ValueError, match="未在文件中找到"):
        asyncio.run(we_ops.sandbox_replace_text_file(
            session_ref=session_ref,
            file_path="/mnt/user-data/workspace/report.txt",
            old_content="missing",
            new_content="new",
        ))


def test_sandbox_file_glob_uses_native_files_search_and_includes_tool_results(monkeypatch):
    captured = {}

    class FakeSearchEntry:
        def __init__(self, *, path, pattern):
            self.path = path
            self.pattern = pattern

    class FakeFiles:
        async def search(self, entry):
            captured["entry"] = entry
            return [
                SimpleNamespace(path="/mnt/user-data/outputs/z-last.json"),
                SimpleNamespace(path="/mnt/user-data/outputs/.tool-offload/query.json"),
                SimpleNamespace(path="/mnt/user-data/outputs/a-first.json"),
                SimpleNamespace(path="/tmp/outside.json"),
            ]

    session_ref = SandboxExecutionSessionRef()
    session_ref.current = SandboxExecutionSession(
        sandbox=SimpleNamespace(files=FakeFiles()),
        sandbox_id="sbx_1",
        workspace_root="/tmp/chat-data/threads/u/c/user-data",
    )

    async def fake_resolve(**_kwargs):
        return "/mnt/user-data/outputs"

    monkeypatch.setattr(we_ops, "_resolve_sandbox_real_path", fake_resolve)
    monkeypatch.setattr(we_ops, "_import_opensandbox_filesystem_models",
        lambda: (None, FakeSearchEntry, None),
    )

    results = asyncio.run(we_ops.sandbox_search_files(
        session_ref=session_ref,
        path="/mnt/user-data/outputs",
        pattern="*.json",
        max_results=100,
    ))

    assert captured["entry"].path == "/mnt/user-data/outputs"
    assert captured["entry"].pattern == "*.json"
    assert results == [
        {"path": "/mnt/user-data/outputs/.tool-offload/query.json", "type": "file"},
        {"path": "/mnt/user-data/outputs/a-first.json", "type": "file"},
        {"path": "/mnt/user-data/outputs/z-last.json", "type": "file"},
    ]


def test_sandbox_list_dir_delegates_through_real_path(monkeypatch):
    captured = {}

    class FakeDirectoryListEntry:
        def __init__(self, *, path, depth):
            self.path = path
            self.depth = depth

    class FakeFiles:
        directories = {
            "/mnt/skills/demo-skill",
            "/mnt/skills/demo-skill/references",
            "/mnt/user-data/workspace",
        }
        files = {
            "/mnt/skills/demo-skill/SKILL.md",
            "/mnt/skills/demo-skill/references/guide.md",
            "/mnt/user-data/workspace/a.txt",
        }

        async def get_file_info(self, paths):
            return {
                path: SimpleNamespace(path=path, size=1)
                for path in paths
                if path in self.directories or path in self.files
            }

        async def list_directory(self, entry):
            captured.setdefault("directory_entries", []).append(entry)
            if entry.depth == 0:
                if entry.path in self.files:
                    raise RuntimeError("ENOTDIR")
                return []
            entries_by_directory = {
                "/mnt/skills/demo-skill": [
                    "/mnt/skills/demo-skill/SKILL.md",
                    "/mnt/skills/demo-skill/references",
                ],
                "/mnt/skills/demo-skill/references": [
                    "/mnt/skills/demo-skill/references/guide.md",
                ],
                "/mnt/user-data/workspace": [
                    "/mnt/user-data/workspace/a.txt",
                    "/mnt/user-data/outputs/.tool-offload/query.json",
                ],
            }
            return [
                SimpleNamespace(path=path, size=1, modified_at="2026-01-01 00:00")
                for path in entries_by_directory.get(entry.path, [])
            ]

    session_ref = SandboxExecutionSessionRef()
    session_ref.current = SandboxExecutionSession(
        sandbox=SimpleNamespace(files=FakeFiles()),
        sandbox_id="sbx_1",
        workspace_root="/host/user-data",
    )
    resolved_paths = []

    async def fake_resolve(**kwargs):
        resolved_paths.append(kwargs["file_path"])
        path = kwargs["file_path"]
        return "/mnt/skills/demo-skill" if path.startswith("/mnt/skills") else "/mnt/user-data/workspace"

    monkeypatch.setattr(we_ops, "_resolve_sandbox_real_path", fake_resolve)
    monkeypatch.setattr(we_ops, "_import_opensandbox_directory_list_entry",
        lambda: FakeDirectoryListEntry,
    )

    entries = asyncio.run(we_ops.sandbox_list_dir(
        session_ref=session_ref, path="/mnt/user-data/workspace", max_depth=2,
    ))

    assert resolved_paths == ["/mnt/user-data/workspace"]
    assert entries[0]["path"] == "/mnt/user-data/workspace/a.txt"
    assert entries[0]["type"] == "file"
    assert any(
        entry.path == "/mnt/user-data/workspace" and entry.depth == 2
        for entry in captured["directory_entries"]
    )


def test_local_bash_can_read_only_loaded_skill_path(tmp_path):
    # 运行域技能直接来自本会话快照目录，不再经 register_runtime_skill 逐条登记。
    workspace_root = _workspace_root(tmp_path)
    skill_dir = workspace_root.parent / "runtime-skills" / "data-analyzing"
    references = skill_dir / "references"
    references.mkdir(parents=True)
    (references / "guide.md").write_text("guide text\n", encoding="utf-8")
    runtime = ConversationWorkspaceRuntime(
        workspace_root=str(workspace_root),
        user_id="user_a",
        backend="local",
    )
    assert (runtime.runtime_skills_dir / "data-analyzing").is_dir()

    result = asyncio.run(
        bash_run.run_bash_command(
            command="cat /mnt/skills/data-analyzing/references/guide.md",
            cwd="/mnt/user-data/workspace",
            workspace_runtime=runtime,
        )
    )

    assert result["success"] is True
    assert result["stdout"].strip() == "guide text"
    with pytest.raises(ValueError, match="当前会话目录"):
        asyncio.run(
            bash_run.run_bash_command(
                command="cat /mnt/skills/other-skill/SKILL.md",
                workspace_runtime=runtime,
            )
        )


def test_local_bash_can_read_hidden_tool_result(tmp_path):
    workspace_root = _workspace_root(tmp_path)
    result_file = workspace_root / "outputs" / ".tool-offload" / "result_1.json"
    result_file.parent.mkdir(parents=True)
    result_file.write_text('{"rows":[1]}', encoding="utf-8")
    runtime = ConversationWorkspaceRuntime(
        workspace_root=str(workspace_root),
        user_id="user_a",
        backend="local",
    )

    result = asyncio.run(
        bash_run.run_bash_command(
            command="cat /mnt/user-data/outputs/.tool-offload/result_1.json",
            workspace_runtime=runtime,
        )
    )

    assert result["success"] is True
    assert result["stdout"] == '{"rows":[1]}'


def test_sandbox_bash_uses_files_api_snapshot_paths(monkeypatch, tmp_path):
    # Files API 快照固定映射为 /mnt/user-data 和 /mnt/skills；命令中的技能路径直接透传。
    snapshot_dir = tmp_path / "skills-snapshot"
    snapshot_dir.mkdir()
    calls = []

    async def fake_run_command_in_sandbox(*, session, command, timeout_seconds):
        calls.append(command)
        return 0, "ok\n", ""

    runtime = ConversationWorkspaceRuntime(
        workspace_root=str(_workspace_root(tmp_path)),
        user_id="user_a",
        backend="sandbox",
    )
    assert isinstance(runtime.backend, SandboxWorkspaceBackend)
    runtime.backend.session_ref.current = SandboxExecutionSession(
        sandbox=object(),
        sandbox_id="sbx_1",
        workspace_root=runtime.workspace_root,
        runtime_skills_dir=snapshot_dir,
    )
    monkeypatch.setattr(
        "apps.chat_data_app.agent.workspace.workspace_sandbox_backend.run_command_in_sandbox",
        fake_run_command_in_sandbox,
    )
    monkeypatch.setattr(
        "apps.chat_data_app.agent.workspace.workspace_sandbox_backend.sync_sandbox_working_trees",
        lambda _session: asyncio.sleep(0),
    )

    result = asyncio.run(
        bash_run.run_bash_command(
            command="cat /mnt/skills/data-analyzing/SKILL.md",
            cwd="/mnt/user-data/workspace",
            workspace_runtime=runtime,
        )
    )

    assert result["success"] is True
    assert result["cwd"] == "/mnt/user-data/workspace"
    assert "/mnt/skills/data-analyzing/SKILL.md" in calls[0]


def test_sandbox_commands_run_as_unprivileged_user(monkeypatch):
    captured = {}

    class FakeRunCommandOpts:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class FakeCommands:
        async def run(self, command, *, opts):
            captured["command"] = command
            captured["opts"] = opts
            return SimpleNamespace(id="", logs=SimpleNamespace(stdout=[], stderr=[]), error=None)

    monkeypatch.setattr(we_api, "_import_opensandbox_sdk",
        lambda: (None, None, FakeRunCommandOpts, None),
    )
    session = SandboxExecutionSession(
        sandbox=SimpleNamespace(commands=FakeCommands()),
        sandbox_id="sbx_1",
        workspace_root="/host/user-data",
    )

    exit_code, stdout, stderr = asyncio.run(
        we_api.run_command_in_sandbox(
            session=session,
            command="id",
            timeout_seconds=5,
        )
    )

    assert (exit_code, stdout, stderr) == (0, "", "")
    assert captured["uid"] == 65534
    assert captured["gid"] == 65534
    assert captured["envs"] == {"HOME": "/tmp", "LANG": "C.UTF-8"}


def test_prepare_workspace_execution_session_uploads_snapshot_without_volumes(tmp_path, monkeypatch):
    captured = {}

    class FakeSandbox:
        id = "sbx_1"

        @staticmethod
        async def create(**kwargs):
            captured.update(kwargs)
            return FakeSandbox()

    class FakeConnectionConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    uploaded = {}

    created_directories = []

    class FakeFiles:
        async def create_directories(self, entries):
            created_directories.extend((entry.path, entry.mode) for entry in entries)

        async def write_file(self, path, data, *, mode):
            uploaded[path] = (bytes(data), mode)

    chat_data_home = tmp_path / "chat-data"
    workspace_root = _workspace_root(tmp_path)
    monkeypatch.setattr(we_paths, "CHAT_DATA_HOME", str(chat_data_home))
    monkeypatch.setenv("OPENSANDBOX_URL", "http://opensandbox.example.com")
    monkeypatch.setenv("OPENSANDBOX_NETWORK_DEFAULT_ACTION", "allow")
    monkeypatch.setenv("OPENSANDBOX_TIMEOUT_SECONDS", "600")
    monkeypatch.setenv("OPENSANDBOX_REQUEST_TIMEOUT_SECONDS", "7")
    monkeypatch.setenv("OPENSANDBOX_CREATE_TIMEOUT_SECONDS", "80")
    monkeypatch.setattr(we_session, "_import_opensandbox_sdk",
        lambda: (FakeSandbox, FakeConnectionConfig, None, (None, None)),
    )
    FakeSandbox.files = FakeFiles()
    (workspace_root / "workspace" / "report.txt").write_text("report", encoding="utf-8")
    (workspace_root / "uploads" / "source.csv").write_text("id\n1\n", encoding="utf-8")
    semantic_file = workspace_root / "workspace" / "semantic" / "sc_order.yaml"
    semantic_file.parent.mkdir(parents=True)
    semantic_file.write_text("semantic_model: sc_order\n", encoding="utf-8")
    runtime_skills_dir = workspace_root.parent / "runtime-skills"
    (runtime_skills_dir / "demo-skill").mkdir(parents=True)
    (runtime_skills_dir / "demo-skill" / "SKILL.md").write_text("# Demo", encoding="utf-8")

    session = asyncio.run(we_session.prepare_workspace_execution_session(
        str(workspace_root), runtime_skills_dir=runtime_skills_dir
    ))

    assert session.sandbox_id == "sbx_1"
    assert "network_policy" not in captured
    assert "volumes" not in captured
    assert captured["timeout"] == timedelta(seconds=600)
    assert captured["connection_config"].kwargs["request_timeout"] == timedelta(seconds=7)
    assert uploaded["/mnt/user-data/workspace/report.txt"] == (b"report", 666)
    assert uploaded["/mnt/user-data/workspace/semantic/sc_order.yaml"] == (
        b"semantic_model: sc_order\n",
        444,
    )
    assert uploaded["/mnt/user-data/uploads/source.csv"] == (b"id\n1\n", 444)
    assert uploaded["/mnt/skills/demo-skill/SKILL.md"] == (b"# Demo", 444)
    permission_modes = {mode for _, mode in created_directories} | {
        mode for _, mode in uploaded.values()
    }
    assert permission_modes == {444, 555, 666, 777}
    assert permission_modes.isdisjoint({292, 365, 438, 511})


def test_prepare_workspace_execution_session_reports_unreachable_control_plane(tmp_path, monkeypatch):
    class FakeSandbox:
        @staticmethod
        async def create(**_kwargs):
            request = httpx.Request("POST", "http://opensandbox.invalid/v1/sandboxes")
            raise httpx.ConnectError("connection failed", request=request)

    class FakeConnectionConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    chat_data_home = tmp_path / "chat-data"
    workspace_root = _workspace_root(tmp_path)
    monkeypatch.setattr(we_paths, "CHAT_DATA_HOME", str(chat_data_home))
    monkeypatch.setenv("OPENSANDBOX_URL", "http://opensandbox.invalid")
    monkeypatch.setenv("OPENSANDBOX_NETWORK_DEFAULT_ACTION", "allow")
    monkeypatch.setattr(we_session, "_import_opensandbox_sdk",
        lambda: (FakeSandbox, FakeConnectionConfig, None, (None, None)),
    )

    with pytest.raises(ValueError, match="OpenSandbox 服务不可达"):
        asyncio.run(we_session.prepare_workspace_execution_session(str(workspace_root)))


def test_prepare_workspace_execution_session_has_total_create_timeout(tmp_path, monkeypatch):
    captured = {}

    class FakeSandbox:
        @staticmethod
        async def create(**_kwargs):
            return None

    class FakeConnectionConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    async def fail_on_wait(awaitable, *, timeout):
        captured["timeout"] = timeout
        awaitable.close()
        raise TimeoutError

    chat_data_home = tmp_path / "chat-data"
    workspace_root = _workspace_root(tmp_path)
    monkeypatch.setattr(we_paths, "CHAT_DATA_HOME", str(chat_data_home))
    monkeypatch.setenv("OPENSANDBOX_URL", "http://opensandbox.example.com")
    monkeypatch.setenv("OPENSANDBOX_NETWORK_DEFAULT_ACTION", "allow")
    monkeypatch.setenv("OPENSANDBOX_READY_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("OPENSANDBOX_REQUEST_TIMEOUT_SECONDS", "10")
    monkeypatch.delenv("OPENSANDBOX_CREATE_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setattr(we_session, "_import_opensandbox_sdk",
        lambda: (FakeSandbox, FakeConnectionConfig, None, (None, None)),
    )
    monkeypatch.setattr(we_session.asyncio, "wait_for", fail_on_wait)

    with pytest.raises(ValueError, match="OpenSandbox 创建超时（100 秒）"):
        asyncio.run(we_session.prepare_workspace_execution_session(str(workspace_root)))

    assert captured["timeout"] == 100


def test_sync_sandbox_uploads_only_transfers_new_upload_files(tmp_path, monkeypatch):
    workspace_root = _workspace_root(tmp_path)
    monkeypatch.setattr(we_paths, "CHAT_DATA_HOME", str(workspace_root.parents[3]))
    initial_upload = workspace_root / "uploads" / "old.txt"
    initial_upload.write_text("old", encoding="utf-8")
    uploaded_paths = []

    class FakeFiles:
        async def write_file(self, path, data, *, mode):
            uploaded_paths.append((path, bytes(data), mode))

    session = SandboxExecutionSession(
        sandbox=SimpleNamespace(files=FakeFiles()),
        sandbox_id="sbx_1",
        workspace_root=str(workspace_root),
        uploads_manifest=we_sync._tree_manifest(workspace_root / "uploads", "uploads"),
    )
    (workspace_root / "workspace" / "unchanged.txt").write_text("workspace", encoding="utf-8")
    (workspace_root / "uploads" / "new.txt").write_text("new", encoding="utf-8")

    async def skip_create_directories(**_kwargs):
        return None

    async def no_remote_files(**_kwargs):
        return [], []

    monkeypatch.setattr(we_sync, "_create_sandbox_directories", skip_create_directories)
    monkeypatch.setattr(we_sync, "_walk_sandbox_tree", no_remote_files)

    asyncio.run(we_sync._sync_sandbox_uploads(session))

    assert uploaded_paths == [("/mnt/user-data/uploads/new.txt", b"new", 444)]


def test_mutable_application_manifest_excludes_hidden_results_and_semantic_models(tmp_path):
    workspace_root = _workspace_root(tmp_path)
    (workspace_root / "workspace" / "report.txt").write_text("report", encoding="utf-8")
    (workspace_root / "workspace" / "semantic").mkdir(parents=True)
    (workspace_root / "workspace" / "semantic" / "model.yaml").write_text("model", encoding="utf-8")
    (workspace_root / "outputs" / ".tool-offload").mkdir(parents=True)
    (workspace_root / "outputs" / ".tool-offload" / "result.json").write_text(
        "result", encoding="utf-8"
    )

    manifest = we_sync._mutable_application_manifest(workspace_root)

    assert set(manifest) == {"workspace/report.txt"}


def test_cached_sandbox_is_reused_only_while_application_trees_match(tmp_path, monkeypatch):
    workspace_root = _workspace_root(tmp_path)
    monkeypatch.setattr(we_paths, "CHAT_DATA_HOME", str(workspace_root.parents[3]))
    target = workspace_root / "workspace" / "report.txt"
    target.write_text("v1", encoding="utf-8")
    session = SandboxExecutionSession(
        sandbox=object(),
        sandbox_id="sbx_1",
        workspace_root=str(workspace_root),
        application_manifest=we_sync._mutable_application_manifest(workspace_root),
    )
    monkeypatch.setattr(we_sync, "_file_fingerprint",
        lambda _path: (_ for _ in ()).throw(AssertionError("cache validation must not hash files")),
    )

    assert asyncio.run(we_session._cached_session_matches_application(session)) is True

    target.write_text("v2", encoding="utf-8")
    stat = target.stat()
    os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

    assert asyncio.run(we_session._cached_session_matches_application(session)) is False


def test_validate_backend_accepts_absolute_chat_data_home(monkeypatch):
    # sandbox 模式下 CHAT_DATA_HOME 为绝对路径时应通过后端校验
    monkeypatch.setenv("CHAT_DATA_HOME", "/data/sandbox/workspace/chat_data")
    we_session._validate_workspace_backend_config()

    chart = we_ops._artifact_payload_from_bytes(
        visible_path="/mnt/user-data/outputs/sales.chart.json",
        data=(
            b'{"version":"1.0","chart_type":"line","data":[{"month":"2026-01","sales":1}],'
            b'"encoding":{"x":"month","y":["sales"]}}'
        ),
    )
    assert chart["artifact_type"] == "chart_spec"
    skill = we_ops._artifact_payload_from_bytes(
        visible_path="/mnt/user-data/outputs/sales-analysis/SKILL.md",
        data=(
            b"---\nname: sales-analysis\ntitle: Sales Analysis\n"
            b"description: Analyze sales.\n---\n# Sales Analysis\n"
        ),
    )
    assert skill == {
        "file_path": "/mnt/user-data/outputs/sales-analysis/SKILL.md",
        "artifact_type": "skill",
        "renderer": "skill",
        "artifact_version": "v1",
        "name": "sales-analysis",
        "skill_title": "Sales Analysis",
        "description": "Analyze sales.",
    }
    assert we_ops._artifact_payload_from_bytes(
        visible_path="/mnt/user-data/outputs/nested/sales-analysis/SKILL.md",
        data=b"---\nname: sales-analysis\n---\n# Sales Analysis\n",
    ) == {}
    assert we_api.truncate_output("abcdef", limit=3) == ("abc\n...[truncated]", True)


# ---------------------------------------------------------------------------
# WorkspacePathMapping 回归测试
# 历史 bug：裸别名 /uploads 会误匹配物理根内部的 server/uploads 段，导致路径
# 自引用反复展开（指数膨胀）；且正则里 [\\w] 写成双反斜杠使 \w 失效，导致
# user-data/workspace 也被别名误改。以下用例钉死 translate 正确行为，并覆盖
# 反向 mask（输出遮蔽）与技能路径翻译。
# ---------------------------------------------------------------------------

_CLEAN_ROOT = "/tmp/fxcjg_test_data/threads/u1/conv_a/user-data"  # 不含 /mnt/user-data/
_BAD_ROOT = "/tmp/fxcjg_test_data/mnt/user-data/uploads/threads/u1/conv_a/user-data"


def _translate(cmd: str) -> str:
    return WorkspacePathMapping(workspace_root=_CLEAN_ROOT).translate(cmd)


def test_path_mapping_clean_config_no_explosion():
    out = _translate("ls -la /mnt/user-data/workspace")
    assert out == f"ls -la {_CLEAN_ROOT}/workspace"
    assert "/mnt/user-data/" not in out
    assert len(out) < 400


def test_path_mapping_bare_alias_translated():
    assert _translate("cd /workspace && ls -la") == f"cd {_CLEAN_ROOT}/workspace && ls -la"


def test_path_mapping_mid_command_translated():
    assert _translate("cd /workspace && pwd") == f"cd {_CLEAN_ROOT}/workspace && pwd"


def test_path_mapping_subdir_preserved():
    assert _translate("cat /workspace/sub/x.py") == f"cat {_CLEAN_ROOT}/workspace/sub/x.py"


def test_path_mapping_outputs_and_uploads():
    assert _translate("ls /mnt/user-data/outputs") == f"ls {_CLEAN_ROOT}/outputs"
    assert _translate("ls /mnt/user-data/uploads") == f"ls {_CLEAN_ROOT}/uploads"


def test_path_mapping_plain_command_untouched():
    assert _translate("echo hello && pwd") == "echo hello && pwd"


def test_path_mapping_no_partial_match():
    # /workspaceX 不应被当成 /workspace 别名误吞
    assert _translate("ls /mnt/user-data/workspaceX") == "ls /mnt/user-data/workspaceX"


def test_path_mapping_bad_root_raises():
    with pytest.raises(ValueError, match="配置异常"):
        WorkspacePathMapping(workspace_root=_BAD_ROOT)


def test_path_mapping_mask_restores_virtual_paths():
    mapping = WorkspacePathMapping(workspace_root=_CLEAN_ROOT)
    assert mapping.mask(f"pwd -> {_CLEAN_ROOT}/workspace/sub/x.py") == "pwd -> /mnt/user-data/workspace/sub/x.py"
    assert mapping.mask(f"ls {_CLEAN_ROOT}/outputs") == "ls /mnt/user-data/outputs"
    assert mapping.mask(f"ls {_CLEAN_ROOT}/uploads") == "ls /mnt/user-data/uploads"


def test_path_mapping_skill_translate_and_mask_roundtrip(tmp_path):
    # 运行域技能：本会话快照目录/<name> <-> 扁平虚拟 /mnt/skills/<name>。
    snapshot_dir = tmp_path / "skills-snapshot" / "data-analyzing"
    snapshot_dir.mkdir(parents=True)
    mapping = WorkspacePathMapping(workspace_root=_CLEAN_ROOT, runtime_skills_dir=snapshot_dir.parent)
    cmd = "cat /mnt/skills/data-analyzing/references/guide.md"
    translated = mapping.translate(cmd)
    assert translated == f"cat {snapshot_dir}/references/guide.md"
    # 反向遮蔽后应还原为原始虚拟路径
    assert mapping.mask(translated) == cmd


def _rg_match_line(file_path: str, text: str, line_no: int) -> str:
    """Build a single rg --json match line for testing."""
    import json

    return json.dumps({
        "type": "match",
        "data": {
            "path": {"text": file_path},
            "lines": {"text": text + "\n"},
            "line_number": line_no,
            "absolute_offset": 0,
            "submatches": [{"match": {"text": "keyword"}}],
        },
    })


def test_parse_rg_output_single_file_target():
    """file_grep on a single file must not discard matches.

    Regression: parse_rg_output stripped a ``target + '/'`` prefix from
    each match path.  When target was a file (not a directory) the prefix
    had a trailing slash that rg's path.text never includes, so every
    match was silently filtered out.
    """
    from apps.chat_data_app.agent.workspace.workspace_search import parse_rg_output

    target = "/mnt/user-data/outputs/dashboard.dashboard.json"
    stdout = _rg_match_line(target, '{"metric": "订单数量"}', 1)
    matches, has_more = parse_rg_output(stdout, target, max_results=200, max_chars=50000)
    assert len(matches) == 1
    assert matches[0]["file"] == ""
    assert matches[0]["line_number"] == 1
    assert "订单数量" in matches[0]["content"]
    assert not has_more


def test_parse_rg_output_directory_target():
    """file_grep on a directory still strips the prefix correctly."""
    from apps.chat_data_app.agent.workspace.workspace_search import parse_rg_output

    target = "/mnt/user-data/outputs"
    file_path = f"{target}/dashboard.dashboard.json"
    stdout = _rg_match_line(file_path, '{"metric": "销售额"}', 3)
    matches, has_more = parse_rg_output(stdout, target, max_results=200, max_chars=50000)
    assert len(matches) == 1
    assert matches[0]["file"] == "dashboard.dashboard.json"
    assert matches[0]["line_number"] == 3
    assert "销售额" in matches[0]["content"]
    assert not has_more
