import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.agent.tools import sandbox_run
from apps.chat_data_app.services import workspace_execution
from apps.chat_data_app.services.conversation_workspace import ConversationWorkspaceRuntime
from apps.chat_data_app.services.workspace_execution import (
    SandboxExecutionSession,
    SandboxExecutionSessionRef,
    get_host_workspace_path,
    sandbox_read_text_file,
    sandbox_visible_path,
)


def _workspace_root(tmp_path: Path) -> Path:
    root = tmp_path / "chat-data" / "users" / "user_a" / "threads" / "conv_a" / "user-data"
    for name in ("workspace", "outputs", "uploads"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def test_get_host_workspace_path_translates_chat_data_home(tmp_path, monkeypatch):
    chat_data_home = tmp_path / "chat-data"
    workspace_root = _workspace_root(tmp_path)
    host_home = Path("/srv/chat-data")
    monkeypatch.setattr(workspace_execution, "CHAT_DATA_HOME", str(chat_data_home))
    monkeypatch.setenv("CHAT_DATA_HOST_HOME", str(host_home))

    path = get_host_workspace_path(str(workspace_root))

    assert path == "/srv/chat-data/users/user_a/threads/conv_a/user-data"


def test_get_host_workspace_path_rejects_path_outside_chat_data_home(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_execution, "CHAT_DATA_HOME", str(tmp_path / "chat-data"))

    with pytest.raises(ValueError, match="允许的根目录"):
        get_host_workspace_path(str(tmp_path / "outside"))


def test_build_volumes_mounts_shared_workspace_and_loaded_skills_readonly(tmp_path, monkeypatch):
    class FakeHost:
        def __init__(self, *, path):
            self.path = path

    class FakeVolume:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    chat_data_home = tmp_path / "chat-data"
    workspace_root = _workspace_root(tmp_path)
    custom_root = tmp_path / "data" / "chat_data_skills" / "user"
    custom_skill = custom_root / "my-skill" / "versions" / "v1"
    custom_skill.mkdir(parents=True)
    system_skill = tmp_path / "system-skills" / "dashboard-generation"
    system_skill.mkdir(parents=True)

    monkeypatch.setattr(workspace_execution, "CHAT_DATA_HOME", str(chat_data_home))
    monkeypatch.setattr(workspace_execution, "DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("CHAT_DATA_HOST_HOME", "/host/chat-data")
    monkeypatch.setenv("OPENSANDBOX_HOST_USER_SKILLS_ROOT", "/host/user-skills")
    monkeypatch.setenv("OPENSANDBOX_HOST_SYSTEM_SKILLS_ROOT", "/host/system-skills")
    monkeypatch.setattr(
        workspace_execution,
        "_import_opensandbox_sdk",
        lambda: (None, None, None, (FakeHost, None, None, FakeVolume)),
    )
    monkeypatch.setattr(
        workspace_execution,
        "_get_host_skill_path",
        lambda source_dir, category: (
            f"/host/{'user-skills' if category == 'custom' else 'system-skills'}/"
            f"{Path(source_dir).name}"
        ),
    )

    mounts = (
        SimpleNamespace(name="my-skill", category="custom", source_dir=custom_skill),
        SimpleNamespace(name="dashboard-generation", category="public", source_dir=system_skill),
    )
    volumes = workspace_execution._build_volumes(str(workspace_root), mounts)

    assert volumes[0].kwargs == {
        "name": "thread-user-data",
        "host": volumes[0].kwargs["host"],
        "mountPath": "/mnt/user-data",
        "readOnly": False,
    }
    assert volumes[0].kwargs["host"].path == "/host/chat-data/users/user_a/threads/conv_a/user-data"
    assert [(item.kwargs["mountPath"], item.kwargs["readOnly"]) for item in volumes[1:]] == [
        ("/mnt/skills/custom/my-skill", True),
        ("/mnt/skills/public/dashboard-generation", True),
    ]


def test_sandbox_visible_path_allows_only_registered_skill_mounts(tmp_path):
    source_dir = tmp_path / "dashboard-generation"
    source_dir.mkdir()
    mounts = (SimpleNamespace(name="dashboard-generation", category="public", source_dir=source_dir),)

    assert sandbox_visible_path(
        "/mnt/skills/public/dashboard-generation/references/guide.md",
        runtime_skill_mounts=mounts,
    ) == "/mnt/skills/public/dashboard-generation/references/guide.md"
    assert sandbox_visible_path("outputs%2Fdashboard.dashboard.json") == "/mnt/user-data/outputs/dashboard.dashboard.json"
    with pytest.raises(ValueError, match="当前已加载的技能路径"):
        sandbox_visible_path("/mnt/skills/public/other-skill/SKILL.md", runtime_skill_mounts=mounts)
    with pytest.raises(ValueError, match="路径越界"):
        sandbox_visible_path("outputs%2F..%2Fsecret.txt")
    with pytest.raises(ValueError, match="只允许写入"):
        sandbox_visible_path(
            "/mnt/skills/public/dashboard-generation/SKILL.md",
            writable=True,
            runtime_skill_mounts=mounts,
        )

    legacy_mount = (SimpleNamespace(name="sssss", category="custom", source_dir=source_dir),)
    assert sandbox_visible_path(
        "/mnt/skills/custom/sssss/SKILL.md",
        runtime_skill_mounts=legacy_mount,
    ) == "/mnt/skills/custom/sssss/SKILL.md"


def test_sandbox_read_loaded_skill_uses_readonly_mount(monkeypatch, tmp_path):
    source_dir = tmp_path / "dashboard-generation"
    source_dir.mkdir()
    mounts = (SimpleNamespace(name="dashboard-generation", category="public", source_dir=source_dir),)

    class FakeFiles:
        async def read_bytes(self, path):
            assert path == "/mnt/skills/public/dashboard-generation/references/guide.md"
            return b"first\nsecond\n"

    session_ref = SandboxExecutionSessionRef()
    session_ref.current = SandboxExecutionSession(
        sandbox=SimpleNamespace(files=FakeFiles()),
        sandbox_id="sbx_1",
        workspace_root="/tmp/workspace",
        host_workspace_path="/host/workspace",
        runtime_skill_mounts=mounts,
    )

    result = asyncio.run(
        sandbox_read_text_file(
            session_ref=session_ref,
            file_path="/mnt/skills/public/dashboard-generation/references/guide.md",
            offset=1,
            limit=1,
        )
    )

    assert result["lines"] == ["second"]
    assert result["path"] == "/mnt/skills/public/dashboard-generation/references/guide.md"


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
            category="public",
            source_dir=skill_dir,
        )
    )

    result = asyncio.run(
        sandbox_run.run_sandbox_command(
            command="cat /mnt/skills/public/dashboard-generation/references/guide.md",
            cwd="/mnt/user-data/workspace",
            workspace_runtime=runtime,
        )
    )

    assert result["success"] is True
    assert result["stdout"] == "guide text\n"
    with pytest.raises(ValueError, match="当前会话目录"):
        asyncio.run(
            sandbox_run.run_sandbox_command(
                command="cat /mnt/skills/public/other-skill/SKILL.md",
                workspace_runtime=runtime,
            )
        )


def test_sandbox_bash_uses_current_runtime_skill_mount(monkeypatch, tmp_path):
    source_dir = tmp_path / "dashboard-generation"
    source_dir.mkdir()
    mounts = (SimpleNamespace(name="dashboard-generation", category="public", source_dir=source_dir),)
    calls = []

    async def fake_run_command_in_sandbox(*, session, command, timeout_seconds):
        calls.append(command)
        return 0, "ok\n", ""

    runtime = ConversationWorkspaceRuntime(
        workspace_root=str(_workspace_root(tmp_path)),
        user_id="user_a",
        backend="sandbox",
    )
    runtime.runtime_skills["public/dashboard-generation"] = mounts[0]
    runtime.sandbox_session_ref.current = SandboxExecutionSession(
        sandbox=object(),
        sandbox_id="sbx_1",
        workspace_root=runtime.workspace_root,
        host_workspace_path="/host/workspace",
        runtime_skill_mounts=mounts,
    )
    monkeypatch.setattr(sandbox_run, "run_command_in_sandbox", fake_run_command_in_sandbox)

    result = asyncio.run(
        sandbox_run.run_sandbox_command(
            command="cat /mnt/skills/public/dashboard-generation/SKILL.md",
            cwd="/mnt/user-data/workspace",
            workspace_runtime=runtime,
        )
    )

    assert result["success"] is True
    assert result["cwd"] == "/mnt/user-data/workspace"
    assert "/mnt/skills/public/dashboard-generation/SKILL.md" in calls[0]


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
    monkeypatch.setenv("CHAT_DATA_HOST_HOME", "/host/chat-data")
    monkeypatch.setenv("WORKSPACE_EXECUTION_BACKEND", "opensandbox")
    monkeypatch.setenv("OPENSANDBOX_WORKSPACE_SYNC_MODE", "remote-host-volume")
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
    assert captured["volumes"][0].kwargs["host"].path.endswith("users/user_a/threads/conv_a/user-data")


def test_workspace_execution_config_and_output_helpers(monkeypatch):
    monkeypatch.setenv("WORKSPACE_EXECUTION_BACKEND", "local")
    with pytest.raises(ValueError, match="WORKSPACE_EXECUTION_BACKEND=local"):
        workspace_execution._validate_workspace_backend_config()

    monkeypatch.setenv("WORKSPACE_EXECUTION_BACKEND", "opensandbox")
    monkeypatch.setenv("OPENSANDBOX_WORKSPACE_SYNC_MODE", "pvc-volume")
    with pytest.raises(ValueError, match="remote-host-volume"):
        workspace_execution._validate_workspace_backend_config()

    monkeypatch.setenv("OPENSANDBOX_PYTHON_BIN", "python3; rm -rf /")
    with pytest.raises(ValueError, match="OPENSANDBOX_PYTHON_BIN"):
        workspace_execution._sandbox_python_bin()

    chart = workspace_execution._artifact_payload_from_bytes(
        visible_path="/mnt/user-data/outputs/sales.chart.json",
        data=(
            b'{"version":"1.0","chart_type":"line","data":[{"month":"2026-01","sales":1}],'
            b'"encoding":{"x":"month","y":["sales"]}}'
        ),
    )
    assert chart["artifact_type"] == "chart_spec"
    assert workspace_execution.truncate_output("abcdef", limit=3) == ("abc\n...[truncated]", True)
