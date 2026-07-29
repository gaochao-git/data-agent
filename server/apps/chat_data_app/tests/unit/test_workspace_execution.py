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
    RuntimeSkillMount,
    WorkspacePathMapping,
)
from apps.chat_data_app.services.workspace_execution import (
    SandboxExecutionSession,
    SandboxExecutionSessionRef,
    get_host_workspace_path,
    sandbox_visible_path,
)


def _workspace_root(tmp_path: Path) -> Path:
    root = tmp_path / "chat-data" / "users" / "user_a" / "threads" / "conv_a" / "user-data"
    for name in ("workspace", "outputs", "uploads"):
        (root / name).mkdir(parents=True, exist_ok=True)
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
        host_home / "users" / "user_a" / "threads" / "conv_a" / "user-data"
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


def test_get_host_user_skill_path_uses_chat_data_home_translation(tmp_path, monkeypatch):
    chat_data_home = tmp_path / "chat-data"
    host_home = tmp_path / "sandbox-host" / "chat-data"
    monkeypatch.setattr(workspace_execution, "CHAT_DATA_HOME", str(chat_data_home))
    monkeypatch.setenv("OPENSANDBOX_HOST_CHAT_DATA_HOME", str(host_home))
    source_dir = chat_data_home / "skills" / "user" / "my-skill"
    source_dir.mkdir(parents=True)

    assert workspace_execution._get_host_skill_path(source_dir, "user") == str(host_home / "skills" / "user" / "my-skill")


def test_get_host_user_skill_path_rejects_outside_chat_data_home(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_execution, "CHAT_DATA_HOME", str(tmp_path / "chat-data"))

    with pytest.raises(ValueError, match="CHAT_DATA_HOME/skills/user"):
        workspace_execution._get_host_skill_path(tmp_path / "outside" / "my-skill", "user")


def test_get_host_skill_path_system_uses_chat_data_home_translation(tmp_path, monkeypatch):
    chat_data_home = tmp_path / "chat-data"
    host_home = tmp_path / "sandbox-host" / "chat-data"
    system_skill = chat_data_home / "skills" / "system" / "dashboard-generation"
    system_skill.mkdir(parents=True)
    monkeypatch.setattr(workspace_execution, "CHAT_DATA_HOME", str(chat_data_home))
    monkeypatch.setenv("OPENSANDBOX_HOST_CHAT_DATA_HOME", str(host_home))

    assert workspace_execution._get_host_skill_path(system_skill, "system") == str(
        host_home / "skills" / "system" / "dashboard-generation"
    )


def test_build_volumes_mounts_shared_workspace_and_loaded_skills_readonly(tmp_path, monkeypatch):
    class FakeHost:
        def __init__(self, *, path):
            self.path = path

    class FakeVolume:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    chat_data_home = tmp_path / "chat-data"
    workspace_root = _workspace_root(tmp_path)
    user_root = tmp_path / "data" / "skills" / "user"
    user_skill = user_root / "my-skill" / "versions" / "v1"
    user_skill.mkdir(parents=True)
    system_skill = tmp_path / "system-skills" / "dashboard-generation"
    system_skill.mkdir(parents=True)

    monkeypatch.setattr(workspace_execution, "CHAT_DATA_HOME", str(chat_data_home))
    monkeypatch.setattr(
        workspace_execution,
        "_import_opensandbox_sdk",
        lambda: (None, None, None, (FakeHost, None, None, FakeVolume)),
    )
    monkeypatch.setattr(
        workspace_execution,
        "_get_host_skill_path",
        lambda source_dir, category: (
            f"/host/{'user-skills' if category == 'user' else 'system-skills'}/"
            f"{Path(source_dir).name}"
        ),
    )

    mounts = (
        SimpleNamespace(name="my-skill", category="user", source_dir=user_skill),
        SimpleNamespace(name="dashboard-generation", category="system", source_dir=system_skill),
    )
    volumes = workspace_execution._build_volumes(str(workspace_root), mounts)

    assert [(item.kwargs["name"], item.kwargs["mountPath"], item.kwargs["readOnly"]) for item in volumes[:3]] == [
        ("thread-workspace", "/mnt/user-data/workspace", False),
        ("thread-outputs", "/mnt/user-data/outputs", False),
        ("thread-uploads", "/mnt/user-data/uploads", False),
    ]
    assert [item.kwargs["host"].path for item in volumes[:3]] == [
        str(workspace_root / "workspace"),
        str(workspace_root / "outputs"),
        str(workspace_root / "uploads"),
    ]
    assert [(item.kwargs["mountPath"], item.kwargs["readOnly"]) for item in volumes[3:]] == [
        ("/mnt/skills/user/my-skill", True),
        ("/mnt/skills/system/dashboard-generation", True),
    ]


def test_sandbox_visible_path_allows_only_registered_skill_mounts(tmp_path):
    source_dir = tmp_path / "dashboard-generation"
    source_dir.mkdir()
    mounts = (SimpleNamespace(name="dashboard-generation", category="system", source_dir=source_dir),)

    assert sandbox_visible_path(
        "/mnt/skills/system/dashboard-generation/references/guide.md",
        runtime_skill_mounts=mounts,
    ) == "/mnt/skills/system/dashboard-generation/references/guide.md"
    assert sandbox_visible_path("outputs%2Fdashboard.dashboard.json") == "/mnt/user-data/outputs/dashboard.dashboard.json"
    assert sandbox_visible_path("outputs/.tool-results/result_1.json") == "/mnt/user-data/outputs/.tool-results/result_1.json"
    with pytest.raises(ValueError, match="不允许通过文件编辑工具"):
        sandbox_visible_path("outputs/.tool-results/result_1.json", writable=True)
    with pytest.raises(ValueError, match="当前已加载的技能路径"):
        sandbox_visible_path("/mnt/skills/system/other-skill/SKILL.md", runtime_skill_mounts=mounts)
    with pytest.raises(ValueError, match="路径越界"):
        sandbox_visible_path("outputs%2F..%2Fsecret.txt")
    with pytest.raises(ValueError, match="只允许写入"):
        sandbox_visible_path(
            "/mnt/skills/system/dashboard-generation/SKILL.md",
            writable=True,
            runtime_skill_mounts=mounts,
        )

    user_mount = (SimpleNamespace(name="sssss", category="user", source_dir=source_dir),)
    assert sandbox_visible_path(
        "/mnt/skills/user/sssss/SKILL.md",
        runtime_skill_mounts=user_mount,
    ) == "/mnt/skills/user/sssss/SKILL.md"
    with pytest.raises(ValueError, match="当前已加载的技能路径"):
        sandbox_visible_path("/mnt/skills/public/dashboard-generation/SKILL.md", runtime_skill_mounts=mounts)


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


def test_local_bash_can_read_only_loaded_skill_path(tmp_path):
    workspace_root = _workspace_root(tmp_path)
    skill_dir = tmp_path / "dashboard-generation"
    references = skill_dir / "references"
    references.mkdir(parents=True)
    (references / "guide.md").write_text("guide text\n", encoding="utf-8")
    runtime = ConversationWorkspaceRuntime(
        workspace_root=str(workspace_root),
        user_id="user_a",
        backend="local",
    )
    asyncio.run(
        runtime.register_runtime_skill(
            name="dashboard-generation",
            category="system",
            source_dir=skill_dir,
        )
    )

    result = asyncio.run(
        bash_run.run_bash_command(
            command="cat /mnt/skills/system/dashboard-generation/references/guide.md",
            cwd="/mnt/user-data/workspace",
            workspace_runtime=runtime,
        )
    )

    assert result["success"] is True
    assert result["stdout"] == "guide text\n"
    with pytest.raises(ValueError, match="当前会话目录"):
        asyncio.run(
            bash_run.run_bash_command(
                command="cat /mnt/skills/system/other-skill/SKILL.md",
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


def test_sandbox_bash_uses_current_runtime_skill_mount(monkeypatch, tmp_path):
    source_dir = tmp_path / "dashboard-generation"
    source_dir.mkdir()
    mounts = (SimpleNamespace(name="dashboard-generation", category="system", source_dir=source_dir),)
    calls = []

    async def fake_run_command_in_sandbox(*, session, command, timeout_seconds):
        calls.append(command)
        return 0, "ok\n", ""

    runtime = ConversationWorkspaceRuntime(
        workspace_root=str(_workspace_root(tmp_path)),
        user_id="user_a",
        backend="sandbox",
    )
    runtime.runtime_skills["system/dashboard-generation"] = mounts[0]
    runtime.sandbox_session_ref.current = SandboxExecutionSession(
        sandbox=object(),
        sandbox_id="sbx_1",
        workspace_root=runtime.workspace_root,
        host_workspace_path="/host/workspace",
        runtime_skill_mounts=mounts,
    )
    monkeypatch.setattr(bash_run, "run_command_in_sandbox", fake_run_command_in_sandbox)

    result = asyncio.run(
        bash_run.run_bash_command(
            command="cat /mnt/skills/system/dashboard-generation/SKILL.md",
            cwd="/mnt/user-data/workspace",
            workspace_runtime=runtime,
        )
    )

    assert result["success"] is True
    assert result["cwd"] == "/mnt/user-data/workspace"
    assert "/mnt/skills/system/dashboard-generation/SKILL.md" in calls[0]


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
    assert len(captured["volumes"]) == 3
    assert [item.kwargs["mountPath"] for item in captured["volumes"]] == [
        "/mnt/user-data/workspace",
        "/mnt/user-data/outputs",
        "/mnt/user-data/uploads",
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

_CLEAN_ROOT = "/tmp/fxcjg_test_data/users/u1/threads/t1/user-data"  # 不含 /mnt/user-data/
_BAD_ROOT = "/tmp/fxcjg_test_data/mnt/user-data/uploads/users/u1/threads/t1/user-data"


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


def test_path_mapping_skill_translate_and_mask_roundtrip():
    skill_dir = Path("/srv/skills/system/dashboard-generation")
    mount = RuntimeSkillMount(name="dashboard-generation", category="system", source_dir=skill_dir)
    mapping = WorkspacePathMapping(workspace_root=_CLEAN_ROOT, skill_mounts=(mount,))
    cmd = "cat /mnt/skills/system/dashboard-generation/references/guide.md"
    translated = mapping.translate(cmd)
    assert translated == f"cat {skill_dir}/references/guide.md"
    # 反向遮蔽后应还原为原始虚拟路径
    assert mapping.mask(translated) == cmd
