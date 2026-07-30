import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.agent.tools import bash_run
from apps.chat_data_app.services import workspace_execution
from apps.chat_data_app.services.conversation_workspace import (
    ConversationWorkspaceRuntime,
    SandboxWorkspaceBackend,
    WorkspacePathMapping,
)
from apps.chat_data_app.services.workspace_execution import (
    SandboxExecutionSession,
    SandboxExecutionSessionRef,
    get_host_workspace_path,
    sandbox_visible_path,
)


def _workspace_root(tmp_path: Path) -> Path:
    # 新布局：threads/<uid>/<cid>/{user-data/{workspace,outputs,uploads}, runtime-skills/}
    root = tmp_path / "chat-data" / "threads" / "user_a" / "conv_a" / "user-data"
    for name in ("workspace", "outputs", "uploads"):
        (root / name).mkdir(parents=True, exist_ok=True)
    (root.parent / "runtime-skills").mkdir(parents=True, exist_ok=True)
    return root


def test_get_host_workspace_path_uses_application_path_when_host_home_is_empty(tmp_path, monkeypatch):
    chat_data_home = tmp_path / "chat-data"
    workspace_root = _workspace_root(tmp_path)
    monkeypatch.setattr(workspace_execution, "CHAT_DATA_HOME", str(chat_data_home))

    path = get_host_workspace_path(str(workspace_root))

    assert path == str(Path(workspace_root).resolve())


def test_get_host_workspace_path_translates_to_sandbox_host_home(tmp_path, monkeypatch):
    app_root = tmp_path / "application"
    chat_data_home = app_root / "chat-data"
    workspace_root = _workspace_root(app_root)
    host_home = tmp_path / "sandbox-host" / "shared" / "chat-data"
    monkeypatch.setattr(workspace_execution, "CHAT_DATA_HOME", str(chat_data_home))
    monkeypatch.setenv("OPENSANDBOX_HOST_CHAT_DATA_HOME", str(host_home))

    assert get_host_workspace_path(str(workspace_root)) == str(
        host_home / "threads" / "user_a" / "conv_a" / "user-data"
    )


def test_validate_backend_requires_absolute_chat_data_home(monkeypatch):
    # sandbox 模式下 CHAT_DATA_HOME 必须是绝对路径，且须位于服务端 allowed_host_paths 之下
    monkeypatch.setenv("CHAT_DATA_HOME", "uploads/chat_data")
    with pytest.raises(ValueError, match="绝对路径"):
        workspace_execution._validate_workspace_backend_config()

    monkeypatch.setenv("CHAT_DATA_HOME", "/data/sandbox/workspace/chat_data")
    monkeypatch.delenv("OPENSANDBOX_HOST_CHAT_DATA_HOME", raising=False)
    workspace_execution._validate_workspace_backend_config()
    monkeypatch.setenv("OPENSANDBOX_HOST_CHAT_DATA_HOME", "relative-host-path")
    with pytest.raises(ValueError, match="OPENSANDBOX_HOST_CHAT_DATA_HOME"):
        workspace_execution._validate_workspace_backend_config()
    monkeypatch.setenv("OPENSANDBOX_HOST_CHAT_DATA_HOME", "/sandbox/../host/chat-data")
    with pytest.raises(ValueError, match="OPENSANDBOX_HOST_CHAT_DATA_HOME"):
        workspace_execution._validate_workspace_backend_config()


def test_get_host_workspace_path_rejects_path_outside_chat_data_home(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_execution, "CHAT_DATA_HOME", str(tmp_path / "chat-data"))

    with pytest.raises(ValueError, match="CHAT_DATA_HOME"):
        get_host_workspace_path(str(tmp_path / "outside"))


def test_build_volumes_mounts_shared_workspace_and_readonly_skills_snapshot(tmp_path, monkeypatch):
    class FakeHost:
        def __init__(self, *, path):
            self.path = path

    class FakeVolume:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    chat_data_home = tmp_path / "chat-data"
    workspace_root = _workspace_root(tmp_path)
    # 运行域技能统一挂载为单个只读快照卷（来自本会话 runtime-skills 快照目录）。
    runtime_skills_dir = chat_data_home / "threads" / "user_a" / "conv_a" / "runtime-skills"
    (runtime_skills_dir / "my-skill").mkdir(parents=True)
    (runtime_skills_dir / "dashboard-generation").mkdir(parents=True)

    monkeypatch.setattr(workspace_execution, "CHAT_DATA_HOME", str(chat_data_home))
    monkeypatch.setattr(
        workspace_execution,
        "_import_opensandbox_sdk",
        lambda: (None, None, None, (FakeHost, None, None, FakeVolume)),
    )

    volumes = workspace_execution._build_volumes(str(workspace_root), runtime_skills_dir)

    # 固定两卷：user-data 整卷可写。
    assert [(item.kwargs["name"], item.kwargs["mountPath"], item.kwargs["readOnly"]) for item in volumes[:1]] == [
        ("user-data", "/mnt/user-data", False),
    ]
    assert [item.kwargs["host"].path for item in volumes[:1]] == [
        str(workspace_root),
    ]
    # 第二卷：扁平 /mnt/skills 只读快照。
    assert [(item.kwargs["name"], item.kwargs["mountPath"], item.kwargs["readOnly"]) for item in volumes[1:]] == [
        ("skills", "/mnt/skills", True),
    ]
    # host_home 未设置时，host 路径即快照目录本身。
    assert volumes[1].kwargs["host"].path == str(runtime_skills_dir)


def test_build_volumes_omits_skills_volume_when_snapshot_missing(tmp_path, monkeypatch):
    class FakeHost:
        def __init__(self, *, path):
            self.path = path

    class FakeVolume:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    chat_data_home = tmp_path / "chat-data"
    workspace_root = _workspace_root(tmp_path)
    runtime_skills_dir = chat_data_home / "threads" / "user_a" / "conv_a" / "runtime-skills-missing"

    monkeypatch.setattr(workspace_execution, "CHAT_DATA_HOME", str(chat_data_home))
    monkeypatch.setattr(
        workspace_execution,
        "_import_opensandbox_sdk",
        lambda: (None, None, None, (FakeHost, None, None, FakeVolume)),
    )

    volumes = workspace_execution._build_volumes(str(workspace_root), runtime_skills_dir)

    assert len(volumes) == 1
    assert [item.kwargs["mountPath"] for item in volumes] == [
        "/mnt/user-data",
    ]


def test_sandbox_visible_path_allows_workspace_and_flat_skills(tmp_path):
    # 运行域技能统一为扁平 /mnt/skills/<name>，不再按类别/挂载逐条校验；
    # sandbox_visible_path 仅做路径级放行与越界校验，文件存在性由运行时快照层保证。
    assert sandbox_visible_path(
        "/mnt/skills/dashboard-generation/references/guide.md"
    ) == "/mnt/skills/dashboard-generation/references/guide.md"
    assert sandbox_visible_path("outputs%2Fdashboard.dashboard.json") == "/mnt/user-data/outputs/dashboard.dashboard.json"
    assert sandbox_visible_path("outputs/.tool-results/result_1.json") == "/mnt/user-data/outputs/.tool-results/result_1.json"
    with pytest.raises(ValueError, match="不允许通过文件编辑工具"):
        sandbox_visible_path("outputs/.tool-results/result_1.json", writable=True)
    # 扁平模型下无逐技能门控：未知技能路径在路径层被放行（越权由运行时快照文件不存在拦截）。
    assert sandbox_visible_path("/mnt/skills/other-skill/SKILL.md") == "/mnt/skills/other-skill/SKILL.md"
    with pytest.raises(ValueError, match="路径越界"):
        sandbox_visible_path("outputs%2F..%2Fsecret.txt")
    # 技能路径只读，写入被拒。
    with pytest.raises(ValueError, match="只允许写入"):
        sandbox_visible_path(
            "/mnt/skills/dashboard-generation/SKILL.md",
            writable=True,
        )


def test_sandbox_artifact_payload_rejects_hidden_tool_result():
    session_ref = SandboxExecutionSessionRef()
    session_ref.current = SandboxExecutionSession(
        sandbox=object(),
        sandbox_id="sbx_1",
        workspace_root="/host/user-data",
        host_workspace_path="/host/user-data",
    )

    payload = asyncio.run(workspace_execution.sandbox_artifact_payload(
        session_ref=session_ref,
        file_path="/mnt/user-data/outputs/.tool-results/result_1.json",
    ))

    assert payload == {}


def test_sandbox_path_requires_a_skill_name():
    with pytest.raises(ValueError, match="技能路径必须包含技能名"):
        sandbox_visible_path("/mnt/skills")


def test_sandbox_read_text_file_uses_resolved_path_and_returns_visible_path(monkeypatch):
    session_ref = SandboxExecutionSessionRef()
    session_ref.current = SandboxExecutionSession(
        sandbox=object(),
        sandbox_id="sbx_1",
        workspace_root="/host/user-data",
        host_workspace_path="/host/user-data",
    )
    captured = {}

    async def fake_resolve(**kwargs):
        captured["resolve"] = kwargs
        return "/mnt/user-data/workspace/report.txt"

    async def fake_run(**kwargs):
        captured["run"] = kwargs
        return {
            "exists": True,
            "is_dir": False,
            "is_binary": False,
            "lines": ["row"],
            "total_lines": None,
            "start_line": 1,
            "end_line": 1,
            "next_offset": None,
            "has_more_lines": False,
            "has_more": False,
            "truncated_lines": [],
        }

    monkeypatch.setattr(workspace_execution, "_resolve_sandbox_real_path", fake_resolve)
    monkeypatch.setattr(workspace_execution, "run_sandbox_python_json", fake_run)

    result = asyncio.run(workspace_execution.sandbox_read_text_file(
        session_ref=session_ref,
        file_path="workspace/report.txt",
        offset=0,
        limit=200,
        max_chars=25000,
    ))

    assert captured["resolve"]["file_path"] == "workspace/report.txt"
    assert captured["run"]["payload"] == {
        "path": "/mnt/user-data/workspace/report.txt",
        "offset": 0,
        "limit": 200,
        "max_chars": 25000,
    }
    assert result["path"] == "/mnt/user-data/workspace/report.txt"
    assert result["has_more_lines"] is False


def test_sandbox_patch_uses_shared_plan_and_files_api(monkeypatch):
    class FakeFiles:
        pass

    session_ref = SandboxExecutionSessionRef()
    session_ref.current = SandboxExecutionSession(
        sandbox=SimpleNamespace(files=FakeFiles()),
        sandbox_id="sbx_1",
        workspace_root="/host/user-data",
        host_workspace_path="/host/user-data",
    )
    calls = {"reads": [], "writes": [], "deletes": [], "rpc": []}

    async def fake_resolve(**kwargs):
        return "/mnt/user-data/" + kwargs["file_path"].lstrip("/")

    async def fake_read(**kwargs):
        calls["reads"].append(kwargs["path"])
        if kwargs["path"].endswith("remove.txt"):
            return b"remove\n"
        if kwargs["path"].endswith("update.txt"):
            return b"old\n"
        raise FileNotFoundError(kwargs["path"])

    async def fake_write(**kwargs):
        calls["writes"].append((kwargs["path"], kwargs["data"]))

    async def fake_delete(**kwargs):
        calls["deletes"].append(kwargs["paths"])

    async def unexpected_rpc(**kwargs):
        calls["rpc"].append(kwargs)
        raise AssertionError("sandbox patch must not use a patch RPC script")

    monkeypatch.setattr(workspace_execution, "_resolve_sandbox_real_path", fake_resolve)
    monkeypatch.setattr(workspace_execution, "_read_sandbox_file_bytes_with_fallback", fake_read)
    monkeypatch.setattr(workspace_execution, "_write_sandbox_file", fake_write)
    monkeypatch.setattr(workspace_execution, "_delete_sandbox_files", fake_delete)
    monkeypatch.setattr(workspace_execution, "run_sandbox_python_json", unexpected_rpc)

    from apps.chat_data_app.services.workspace_patch import AddOp, DeleteOp, Hunk, UpdateOp

    actions = asyncio.run(workspace_execution.sandbox_apply_patch_ops(
        session_ref=session_ref,
        ops=[
            AddOp(path="workspace/add.txt", lines=["new"]),
            UpdateOp(path="workspace/update.txt", hunks=[Hunk(lines=["-old", "+updated"])]),
            DeleteOp(path="workspace/remove.txt"),
        ],
        dry_run=False,
    ))

    assert actions == [
        "add workspace/add.txt",
        "update workspace/update.txt",
        "delete workspace/remove.txt",
    ]
    assert calls["writes"] == [
        ("/mnt/user-data/workspace/add.txt", b"new\n"),
        ("/mnt/user-data/workspace/update.txt", b"updated\n"),
    ]
    assert calls["deletes"] == [["/mnt/user-data/workspace/remove.txt"]]
    assert calls["rpc"] == []


def test_sandbox_patch_dry_run_does_not_persist(monkeypatch):
    session_ref = SandboxExecutionSessionRef()
    session_ref.current = SandboxExecutionSession(
        sandbox=SimpleNamespace(files=object()),
        sandbox_id="sbx_1",
        workspace_root="/host/user-data",
        host_workspace_path="/host/user-data",
    )

    async def fake_resolve(**kwargs):
        return "/mnt/user-data/workspace/add.txt"

    async def missing_file(**_kwargs):
        raise FileNotFoundError("missing")

    async def unexpected_persist(**_kwargs):
        raise AssertionError("dry run must not persist changes")

    monkeypatch.setattr(workspace_execution, "_resolve_sandbox_real_path", fake_resolve)
    monkeypatch.setattr(workspace_execution, "_read_sandbox_file_bytes_with_fallback", missing_file)
    monkeypatch.setattr(workspace_execution, "_write_sandbox_file", unexpected_persist)
    monkeypatch.setattr(workspace_execution, "_delete_sandbox_files", unexpected_persist)

    from apps.chat_data_app.services.workspace_patch import AddOp

    assert asyncio.run(workspace_execution.sandbox_apply_patch_ops(
        session_ref=session_ref,
        ops=[AddOp(path="workspace/add.txt", lines=["new"])],
        dry_run=True,
    )) == ["add workspace/add.txt"]


def test_sandbox_patch_accepts_sdk_not_found_for_add(monkeypatch):
    session_ref = SandboxExecutionSessionRef()
    session_ref.current = SandboxExecutionSession(
        sandbox=SimpleNamespace(files=object()),
        sandbox_id="sbx_1",
        workspace_root="/host/user-data",
        host_workspace_path="/host/user-data",
    )

    class NotFoundError(Exception):
        status_code = 404

    async def fake_resolve(**_kwargs):
        return "/mnt/user-data/workspace/add.txt"

    async def missing_file(**_kwargs):
        raise NotFoundError("not found")

    monkeypatch.setattr(workspace_execution, "_resolve_sandbox_real_path", fake_resolve)
    monkeypatch.setattr(workspace_execution, "_read_sandbox_file_bytes_with_fallback", missing_file)

    from apps.chat_data_app.services.workspace_patch import AddOp

    assert asyncio.run(workspace_execution.sandbox_apply_patch_ops(
        session_ref=session_ref,
        ops=[AddOp(path="workspace/add.txt", lines=["new"])],
        dry_run=True,
    )) == ["add workspace/add.txt"]


def test_sandbox_load_skill_and_list_dir_delegate_through_real_path(monkeypatch):
    session_ref = SandboxExecutionSessionRef()
    session_ref.current = SandboxExecutionSession(
        sandbox=object(),
        sandbox_id="sbx_1",
        workspace_root="/host/user-data",
        host_workspace_path="/host/user-data",
    )
    resolved_paths = []

    async def fake_resolve(**kwargs):
        resolved_paths.append(kwargs["file_path"])
        path = kwargs["file_path"]
        return "/mnt/skills/demo-skill" if path.startswith("/mnt/skills") else "/mnt/user-data/workspace"

    async def fake_run(**kwargs):
        if kwargs["payload"].get("name"):
            return {"name": "demo-skill", "title": "Demo", "description": "", "skill_md": "# Demo", "references": [], "scripts": [], "assets": []}
        return {"entries": [{"type": "file", "path": "/mnt/user-data/workspace/a.txt", "size": 1, "mtime": "2026-01-01 00:00"}]}

    monkeypatch.setattr(workspace_execution, "_resolve_sandbox_real_path", fake_resolve)
    monkeypatch.setattr(workspace_execution, "run_sandbox_python_json", fake_run)

    skill = asyncio.run(workspace_execution.sandbox_load_skill_payload(
        session_ref=session_ref, skill_name="demo-skill", max_chars=100,
    ))
    entries = asyncio.run(workspace_execution.sandbox_list_dir(
        session_ref=session_ref, path="workspace", max_depth=2,
    ))

    assert resolved_paths == ["/mnt/skills/demo-skill", "workspace"]
    assert skill["visible_path"] == "/mnt/skills/demo-skill"
    assert entries[0]["path"] == "/mnt/user-data/workspace/a.txt"


def test_local_bash_can_read_only_loaded_skill_path(tmp_path):
    # 运行域技能直接来自本会话快照目录，不再经 register_runtime_skill 逐条登记。
    workspace_root = _workspace_root(tmp_path)
    skill_dir = workspace_root.parent / "runtime-skills" / "dashboard-generation"
    references = skill_dir / "references"
    references.mkdir(parents=True)
    (references / "guide.md").write_text("guide text\n", encoding="utf-8")
    runtime = ConversationWorkspaceRuntime(
        workspace_root=str(workspace_root),
        user_id="user_a",
        backend="local",
    )
    assert (runtime.runtime_skills_dir / "dashboard-generation").is_dir()

    result = asyncio.run(
        bash_run.run_bash_command(
            command="cat /mnt/skills/dashboard-generation/references/guide.md",
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
    result_file = workspace_root / "outputs" / ".tool-results" / "result_1.json"
    result_file.parent.mkdir(parents=True)
    result_file.write_text('{"rows":[1]}', encoding="utf-8")
    runtime = ConversationWorkspaceRuntime(
        workspace_root=str(workspace_root),
        user_id="user_a",
        backend="local",
    )

    result = asyncio.run(
        bash_run.run_bash_command(
            command="cat /mnt/user-data/outputs/.tool-results/result_1.json",
            workspace_runtime=runtime,
        )
    )

    assert result["success"] is True
    assert result["stdout"] == '{"rows":[1]}'


def test_sandbox_bash_uses_fixed_skills_volume(monkeypatch, tmp_path):
    # 沙箱只挂载两个固定卷：/mnt/user-data（可写）+ /mnt/skills（只读快照）；
    # 不再逐技能登记挂载，命令中的 /mnt/skills/<name> 直接透传。
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
        host_workspace_path="/host/workspace",
        runtime_skills_dir=snapshot_dir,
    )
    monkeypatch.setattr(
        "apps.chat_data_app.services.conversation_workspace.run_command_in_sandbox",
        fake_run_command_in_sandbox,
    )

    result = asyncio.run(
        bash_run.run_bash_command(
            command="cat /mnt/skills/dashboard-generation/SKILL.md",
            cwd="/mnt/user-data/workspace",
            workspace_runtime=runtime,
        )
    )

    assert result["success"] is True
    assert result["cwd"] == "/mnt/user-data/workspace"
    assert "/mnt/skills/dashboard-generation/SKILL.md" in calls[0]


def test_prepare_workspace_execution_session_uses_no_copy_or_seed_step(tmp_path, monkeypatch):
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

    class FakeHost:
        def __init__(self, *, path):
            self.path = path

    class FakeVolume:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    chat_data_home = tmp_path / "chat-data"
    workspace_root = _workspace_root(tmp_path)
    monkeypatch.setattr(workspace_execution, "CHAT_DATA_HOME", str(chat_data_home))
    monkeypatch.setenv("OPENSANDBOX_URL", "http://opensandbox.example.com")
    monkeypatch.setenv("OPENSANDBOX_NETWORK_DEFAULT_ACTION", "allow")
    monkeypatch.setattr(
        workspace_execution,
        "_import_opensandbox_sdk",
        lambda: (FakeSandbox, FakeConnectionConfig, None, (FakeHost, None, None, FakeVolume)),
    )

    session = asyncio.run(workspace_execution.prepare_workspace_execution_session(str(workspace_root)))

    assert session.sandbox_id == "sbx_1"
    assert "network_policy" not in captured
    assert len(captured["volumes"]) == 1
    assert [item.kwargs["mountPath"] for item in captured["volumes"]] == [
        "/mnt/user-data",
    ]
    assert all(".tool-results" not in item.kwargs["host"].path for item in captured["volumes"])


def test_workspace_execution_config_and_output_helpers(monkeypatch):
    # 沙箱后端与工作区同步模式已内部固定，这里校验其它配置项。
    monkeypatch.setenv("OPENSANDBOX_PYTHON_BIN", "python3; rm -rf /")
    with pytest.raises(ValueError, match="OPENSANDBOX_PYTHON_BIN"):
        workspace_execution._sandbox_python_bin()


def test_validate_backend_accepts_absolute_chat_data_home(monkeypatch):
    # sandbox 模式下 CHAT_DATA_HOME 为绝对路径时应通过后端校验
    monkeypatch.setenv("CHAT_DATA_HOME", "/data/sandbox/workspace/chat_data")
    workspace_execution._validate_workspace_backend_config()

    chart = workspace_execution._artifact_payload_from_bytes(
        visible_path="/mnt/user-data/outputs/sales.chart.json",
        data=(
            b'{"version":"1.0","chart_type":"line","data":[{"month":"2026-01","sales":1}],'
            b'"encoding":{"x":"month","y":["sales"]}}'
        ),
    )
    assert chart["artifact_type"] == "chart_spec"
    skill = workspace_execution._artifact_payload_from_bytes(
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
    assert workspace_execution._artifact_payload_from_bytes(
        visible_path="/mnt/user-data/outputs/nested/sales-analysis/SKILL.md",
        data=b"---\nname: sales-analysis\n---\n# Sales Analysis\n",
    ) == {}
    assert workspace_execution.truncate_output("abcdef", limit=3) == ("abc\n...[truncated]", True)


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
    snapshot_dir = tmp_path / "skills-snapshot" / "dashboard-generation"
    snapshot_dir.mkdir(parents=True)
    mapping = WorkspacePathMapping(workspace_root=_CLEAN_ROOT, runtime_skills_dir=snapshot_dir.parent)
    cmd = "cat /mnt/skills/dashboard-generation/references/guide.md"
    translated = mapping.translate(cmd)
    assert translated == f"cat {snapshot_dir}/references/guide.md"
    # 反向遮蔽后应还原为原始虚拟路径
    assert mapping.mask(translated) == cmd
