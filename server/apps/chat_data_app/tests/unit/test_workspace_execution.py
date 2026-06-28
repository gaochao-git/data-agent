import asyncio
import os
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.services import workspace_execution
from apps.chat_data_app.services.workspace_execution import (
    SandboxExecutionSession,
    SandboxExecutionSessionRef,
    get_host_workspace_path,
    list_workspace_files_from_sandbox,
    release_workspace_execution_session,
    sandbox_visible_path,
    sandbox_read_text_file,
)
from apps.chat_data_app.agent.tools import sandbox_run


def test_get_host_workspace_path_defaults_to_fxcjg_project(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENSANDBOX_HOST_WORKSPACE_ROOT", raising=False)
    monkeypatch.delenv("OPENSANDBOX_HOST_PROJECT_NAME", raising=False)
    workspace_root = tmp_path / "Thread_ID_With.UPPER"
    workspace_root.mkdir()

    path = get_host_workspace_path(str(workspace_root), user_id="User_01")

    assert path.startswith("/data/opensandbox-workspaces/fxcjg/user-01/")
    assert path.endswith("/thread-id-with-upper")


def test_build_volumes_uses_single_writable_host_mount(tmp_path, monkeypatch):
    class FakeHost:
        def __init__(self, *, path):
            self.path = path

    class FakeVolume:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeNetworkPolicy:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeNetworkRule:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setenv("OPENSANDBOX_HOST_WORKSPACE_ROOT", "/data/opensandbox-workspaces")
    monkeypatch.setenv("OPENSANDBOX_HOST_PROJECT_NAME", "fxcjg")
    monkeypatch.setattr(workspace_execution, "_import_opensandbox_sdk", lambda: (None, None, None, (FakeHost, FakeNetworkPolicy, FakeNetworkRule, FakeVolume)))
    workspace_root = tmp_path / "thread-1"
    workspace_root.mkdir()

    volumes = workspace_execution._build_volumes(str(workspace_root), user_id="gaochao")

    assert len(volumes) == 1
    assert volumes[0].kwargs["name"] == "thread-user-data"
    assert volumes[0].kwargs["mountPath"] == "/mnt/user-data"
    assert volumes[0].kwargs["readOnly"] is False
    assert volumes[0].kwargs["host"].path.startswith("/data/opensandbox-workspaces/fxcjg/gaochao/")
    assert volumes[0].kwargs["host"].path.endswith("/thread-1")


def test_build_network_policy_defaults_to_deny(monkeypatch):
    captured = {}

    class FakeNetworkPolicy:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.delenv("OPENSANDBOX_NETWORK_DEFAULT_ACTION", raising=False)
    monkeypatch.setattr(workspace_execution, "_import_opensandbox_sdk", lambda: (None, None, None, (None, FakeNetworkPolicy, None, None)))

    policy = workspace_execution._build_network_policy()

    assert policy is not None
    assert captured["defaultAction"] == "deny"
    assert captured["egress"] is None


def test_build_network_policy_adds_allow_targets(monkeypatch):
    captured = {}
    rules = []

    class FakeNetworkPolicy:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class FakeNetworkRule:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            rules.append(kwargs)

    monkeypatch.setenv("OPENSANDBOX_NETWORK_DEFAULT_ACTION", "deny")
    monkeypatch.setenv("OPENSANDBOX_NETWORK_ALLOW_TARGETS", "pypi.org, *.pythonhosted.org\npypi.org")
    monkeypatch.setattr(workspace_execution, "_import_opensandbox_sdk", lambda: (None, None, None, (None, FakeNetworkPolicy, FakeNetworkRule, None)))

    policy = workspace_execution._build_network_policy()

    assert policy is not None
    assert captured["defaultAction"] == "deny"
    assert captured["egress"] is not None
    assert rules == [
        {"action": "allow", "target": "pypi.org"},
        {"action": "allow", "target": "*.pythonhosted.org"},
    ]


def test_build_network_policy_allows_no_policy(monkeypatch):
    monkeypatch.setenv("OPENSANDBOX_NETWORK_DEFAULT_ACTION", "allow")

    assert workspace_execution._build_network_policy() is None


def test_build_network_policy_invalid_values_fall_back_to_deny(monkeypatch):
    captured = {}

    class FakeNetworkPolicy:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("OPENSANDBOX_NETWORK_DEFAULT_ACTION", "no-policy")
    monkeypatch.setattr(workspace_execution, "_import_opensandbox_sdk", lambda: (None, None, None, (None, FakeNetworkPolicy, None, None)))

    policy = workspace_execution._build_network_policy()

    assert policy is not None
    assert captured["defaultAction"] == "deny"


def test_prepare_workspace_execution_omits_network_policy_when_allow(tmp_path, monkeypatch):
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

    class FakeNetworkPolicy:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeNetworkRule:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    async def fake_ensure_workspace_dirs(*, session):
        return None

    async def fake_seed_workspace_if_needed(*, session):
        return None

    monkeypatch.setenv("WORKSPACE_EXECUTION_BACKEND", "opensandbox")
    monkeypatch.setenv("OPENSANDBOX_WORKSPACE_SYNC_MODE", "remote-host-volume")
    monkeypatch.setenv("OPENSANDBOX_URL", "http://opensandbox.example.com")
    monkeypatch.setenv("OPENSANDBOX_NETWORK_DEFAULT_ACTION", "allow")
    monkeypatch.setattr(workspace_execution, "_import_opensandbox_sdk", lambda: (FakeSandbox, FakeConnectionConfig, None, (FakeHost, FakeNetworkPolicy, FakeNetworkRule, FakeVolume)))
    monkeypatch.setattr(workspace_execution, "_ensure_workspace_dirs", fake_ensure_workspace_dirs)
    monkeypatch.setattr(workspace_execution, "_seed_workspace_if_needed", fake_seed_workspace_if_needed)

    workspace_root = tmp_path / "thread-1"
    workspace_root.mkdir()
    session = asyncio.run(workspace_execution.prepare_workspace_execution_session(str(workspace_root), user_id="u1"))

    assert session.sandbox_id == "sbx_1"
    assert "network_policy" not in captured


def test_validate_workspace_backend_rejects_non_remote_host_volume(monkeypatch):
    monkeypatch.setenv("WORKSPACE_EXECUTION_BACKEND", "opensandbox")
    monkeypatch.setenv("OPENSANDBOX_WORKSPACE_SYNC_MODE", "pvc-volume")

    try:
        workspace_execution._validate_workspace_backend_config()
    except ValueError as exc:
        assert "remote-host-volume" in str(exc)
    else:
        raise AssertionError("expected invalid sync mode to be rejected")


def test_run_sandbox_python_json_uses_configured_python_bin(monkeypatch):
    calls = []

    async def fake_run_command_in_sandbox(*, session, command, timeout_seconds):
        calls.append(command)
        return 0, '{"ok": true}', ""

    class FakeSandbox:
        pass

    monkeypatch.setenv("OPENSANDBOX_PYTHON_BIN", "/opt/python/bin/python3")
    monkeypatch.setattr(workspace_execution, "run_command_in_sandbox", fake_run_command_in_sandbox)
    session = SandboxExecutionSession(sandbox=FakeSandbox(), sandbox_id="sbx_1", workspace_root="/tmp/thread", host_workspace_path="/data/opensandbox-workspaces/fxcjg/system/20260519/thread")

    result = asyncio.run(workspace_execution.run_sandbox_python_json(session=session, script_body="print(json.dumps({'ok': True}))", payload={}))

    assert result == {"ok": True}
    assert "/opt/python/bin/python3" in calls[0]


def test_sandbox_python_bin_rejects_shell_fragment(monkeypatch):
    monkeypatch.setenv("OPENSANDBOX_PYTHON_BIN", "python3; rm -rf /")

    try:
        workspace_execution._sandbox_python_bin()
    except ValueError as exc:
        assert "OPENSANDBOX_PYTHON_BIN" in str(exc)
    else:
        raise AssertionError("expected unsafe python bin to be rejected")


def test_release_workspace_execution_session_closes_without_kill():
    calls = []

    class FakeSandbox:
        async def kill(self):
            calls.append("kill")

        async def close(self):
            calls.append("close")

    session = SandboxExecutionSession(sandbox=FakeSandbox(), sandbox_id="sbx_1", workspace_root="/tmp/thread", host_workspace_path="/data/opensandbox-workspaces/fxcjg/system/20260519/thread")

    asyncio.run(release_workspace_execution_session(session))

    assert calls == ["close"]


def test_connection_config_uses_server_proxy_env(monkeypatch):
    captured = {}

    class FakeConnectionConfig:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("OPENSANDBOX_URL", "http://opensandbox.example.com")
    monkeypatch.setenv("OPENSANDBOX_API_KEY", "test-key")
    monkeypatch.setenv("OPENSANDBOX_USE_SERVER_PROXY", "true")
    monkeypatch.setattr(workspace_execution, "_import_opensandbox_sdk", lambda: (None, FakeConnectionConfig, None, None))

    workspace_execution._connection_config(60)

    assert captured["domain"] == "http://opensandbox.example.com"
    assert captured["api_key"] == "test-key"
    assert captured["use_server_proxy"] is True


def test_sandbox_visible_path_accepts_url_encoded_relative_path():
    path = sandbox_visible_path("outputs%2Fdashboard.dashboard.json")

    assert path == f"{workspace_execution.sandbox_outputs_dir()}/dashboard.dashboard.json"


def test_sandbox_visible_path_rejects_encoded_parent_escape():
    try:
        sandbox_visible_path("outputs%2F..%2Fsecret.txt")
    except ValueError as exc:
        assert "路径越界" in str(exc)
    else:
        raise AssertionError("expected encoded parent escape to be rejected")


def test_connection_config_defaults_server_proxy_to_false(monkeypatch):
    captured = {}

    class FakeConnectionConfig:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("OPENSANDBOX_URL", "http://opensandbox.example.com")
    monkeypatch.delenv("OPENSANDBOX_USE_SERVER_PROXY", raising=False)
    monkeypatch.setattr(workspace_execution, "_import_opensandbox_sdk", lambda: (None, FakeConnectionConfig, None, None))

    workspace_execution._connection_config(60)

    assert captured["use_server_proxy"] is False


def test_seed_tar_contains_only_workspace_visible_dirs(tmp_path):
    root = tmp_path / "thread-1"
    (root / "workspace").mkdir(parents=True)
    (root / "workspace" / "demo.py").write_text("print('ok')\n", encoding="utf-8")
    (root / "uploads").mkdir()
    (root / "uploads" / "input.csv").write_text("a,b\n", encoding="utf-8")

    data = workspace_execution._build_seed_tar_bytes(root)

    import tarfile
    import io

    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        names = sorted(member.name for member in tar.getmembers())

    assert "workspace/demo.py" in names
    assert "uploads/input.csv" in names
    assert all(not name.startswith("/") for name in names)
    assert all("work/git_repo" not in name for name in names)
    assert all("runtime-skills" not in name for name in names)


def test_sandbox_read_uses_opensandbox_files_api(monkeypatch):
    calls = {"read_bytes": 0, "python": 0}

    async def fake_run_sandbox_python_json(**kwargs):
        calls["python"] += 1
        return {}

    class FakeFiles:
        async def read_bytes(self, path):
            calls["read_bytes"] += 1
            assert path == "/mnt/user-data/workspace/demo.txt"
            return b"a\nb\nc\n"

    class FakeSandbox:
        files = FakeFiles()

    monkeypatch.setattr(workspace_execution, "run_sandbox_python_json", fake_run_sandbox_python_json)
    session_ref = SandboxExecutionSessionRef()
    session_ref.current = SandboxExecutionSession(sandbox=FakeSandbox(), sandbox_id="sbx_1", workspace_root="/tmp/thread", host_workspace_path="/data/opensandbox-workspaces/fxcjg/system/20260519/thread")

    result = asyncio.run(sandbox_read_text_file(session_ref=session_ref, file_path="/workspace/demo.txt", offset=1, limit=1))

    assert result["lines"] == ["b"]
    assert calls["read_bytes"] == 1
    assert calls["python"] == 0


def test_sandbox_run_allows_ls_and_maps_cwd(monkeypatch):
    calls = []

    async def fake_run_command_in_sandbox(*, session, command, timeout_seconds):
        calls.append({"command": command, "timeout_seconds": timeout_seconds})
        return 0, "total 0\n", ""

    class FakeSandbox:
        pass

    session_ref = SandboxExecutionSessionRef()
    session_ref.current = SandboxExecutionSession(sandbox=FakeSandbox(), sandbox_id="sbx_1", workspace_root="/tmp/thread", host_workspace_path="/data/opensandbox-workspaces/fxcjg/system/20260519/thread")
    monkeypatch.setattr(sandbox_run, "run_command_in_sandbox", fake_run_command_in_sandbox)

    result = asyncio.run(sandbox_run.run_sandbox_command(command="ls -la /mnt/user-data/workspace", cwd="/mnt/user-data/workspace", session_ref=session_ref))

    assert result["success"] is True
    assert result["cwd"] == "/mnt/user-data/workspace"
    assert "ls -la /mnt/user-data/workspace" in calls[0]["command"]
    assert sandbox_run._normalize_cwd(None) == "/mnt/user-data/workspace"


def test_sandbox_run_rejects_background_command():
    try:
        sandbox_run._validate_command("python3 app.py &")
    except ValueError as exc:
        assert "后台进程" in str(exc)
    else:
        raise AssertionError("expected background command to be rejected")

    with pytest.raises(ValueError, match="command is required"):
        sandbox_run._validate_command("")
    with pytest.raises(ValueError, match="空字节"):
        sandbox_run._validate_command("echo bad\x00")


def test_sandbox_run_rejects_paths_outside_workspace():
    try:
        sandbox_run._validate_command_paths("cat /etc/passwd")
    except ValueError as exc:
        assert "当前会话目录" in str(exc)
    else:
        raise AssertionError("expected absolute system path to be rejected")


def test_sandbox_run_rejects_parent_path_escape():
    try:
        sandbox_run._validate_command_paths("cat ../secret.txt")
    except ValueError as exc:
        assert "当前会话目录" in str(exc)
    else:
        raise AssertionError("expected parent path escape to be rejected")

    with pytest.raises(ValueError, match="解析失败"):
        sandbox_run._validate_command_paths("cat 'unterminated")

    sandbox_run._validate_command_paths("cat /mnt/user-data/workspace/a.txt /mnt/user-data/outputs/out.txt")


def test_list_workspace_files_attaches_output_artifacts(monkeypatch):
    calls = []
    workspace_execution._WORKSPACE_EXECUTION_SESSION_CACHE.clear()
    workspace_execution._WORKSPACE_EXECUTION_SESSION_LOCKS.clear()
    chart_payload = {
        "version": "1.0",
        "title": "销售趋势",
        "chart_type": "line",
        "data": [{"month": "2026-01", "sales": 120}],
        "encoding": {"x": "month", "y": ["sales"]},
    }
    dashboard_payload = {
        "dashboard_name": "销售看板",
        "cards": [{"title": "销售趋势", "chart_type": "line"}],
    }

    async def fake_prepare_workspace_execution_session(workspace_root, user_id="system"):
        return SandboxExecutionSession(sandbox=object(), sandbox_id="sbx_1", workspace_root=workspace_root, host_workspace_path="/data/opensandbox-workspaces/fxcjg/system/20260519/thread")

    async def fake_cleanup_workspace_execution_session(session):
        calls.append("cleanup")
        return None

    async def fake_release_workspace_execution_session(session):
        calls.append("release")
        return None

    async def fake_run_sandbox_python_json(**kwargs):
        return {
            "files": [{
                "path": "/mnt/user-data/outputs/sales.chart.json",
                "relative_path": "/mnt/user-data/outputs/sales.chart.json",
                "directory": "outputs",
                "name": "sales.chart.json",
                "size": 1,
                "mtime": 1,
                "source": "outputs",
                "content_hex": __import__("json").dumps(chart_payload, ensure_ascii=False).encode("utf-8").hex(),
            }, {
                "path": "/mnt/user-data/outputs/sales.dashboard.json",
                "relative_path": "/mnt/user-data/outputs/sales.dashboard.json",
                "directory": "outputs",
                "name": "sales.dashboard.json",
                "size": 1,
                "mtime": 2,
                "source": "outputs",
                "content_hex": __import__("json").dumps(dashboard_payload, ensure_ascii=False).encode("utf-8").hex(),
            }]
        }

    monkeypatch.setattr(workspace_execution, "prepare_workspace_execution_session", fake_prepare_workspace_execution_session)
    monkeypatch.setattr(workspace_execution, "cleanup_workspace_execution_session", fake_cleanup_workspace_execution_session)
    monkeypatch.setattr(workspace_execution, "release_workspace_execution_session", fake_release_workspace_execution_session)
    monkeypatch.setattr(workspace_execution, "run_sandbox_python_json", fake_run_sandbox_python_json)

    files = asyncio.run(list_workspace_files_from_sandbox(workspace_root="/tmp/thread", user_id="system"))

    assert files[0]["artifact_type"] == "chart_spec"
    assert files[0]["renderer"] == "chart"
    assert files[0]["chart_spec"]["title"] == "销售趋势"
    assert "content_hex" not in files[0]
    assert files[1]["artifact_type"] == "dashboard_spec"
    assert files[1]["renderer"] == "dashboard"
    assert files[1]["dashboard_spec"]["dashboard_name"] == "销售看板"
    assert "content_hex" not in files[1]
    assert calls == []
    workspace_execution._WORKSPACE_EXECUTION_SESSION_CACHE.clear()
    workspace_execution._WORKSPACE_EXECUTION_SESSION_LOCKS.clear()


def test_list_workspace_files_reuses_cached_session(tmp_path, monkeypatch):
    workspace_execution._WORKSPACE_EXECUTION_SESSION_CACHE.clear()
    workspace_execution._WORKSPACE_EXECUTION_SESSION_LOCKS.clear()
    prepare_calls = []
    run_sessions = []

    async def fake_prepare_workspace_execution_session(workspace_root, user_id="system"):
        prepare_calls.append((workspace_root, user_id))
        return SandboxExecutionSession(sandbox=object(), sandbox_id="sbx_1", workspace_root=workspace_root, host_workspace_path="/data/opensandbox-workspaces/fxcjg/system/20260519/thread")

    async def fake_run_sandbox_python_json(**kwargs):
        run_sessions.append(kwargs["session"])
        return {"files": []}

    monkeypatch.setattr(workspace_execution, "prepare_workspace_execution_session", fake_prepare_workspace_execution_session)
    monkeypatch.setattr(workspace_execution, "run_sandbox_python_json", fake_run_sandbox_python_json)

    files1 = asyncio.run(list_workspace_files_from_sandbox(workspace_root=str(tmp_path), user_id="system"))
    files2 = asyncio.run(list_workspace_files_from_sandbox(workspace_root=str(tmp_path), user_id="system"))

    assert files1 == []
    assert files2 == []
    assert len(prepare_calls) == 1
    assert run_sessions[0] is run_sessions[1]
    workspace_execution._WORKSPACE_EXECUTION_SESSION_CACHE.clear()
    workspace_execution._WORKSPACE_EXECUTION_SESSION_LOCKS.clear()


def test_workspace_execution_config_and_path_guard_edges(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENSANDBOX_HOST_WORKSPACE_ROOT", "/")
    try:
        workspace_execution.get_host_workspace_path(str(tmp_path), user_id="u1")
    except ValueError as exc:
        assert "OPENSANDBOX_HOST_WORKSPACE_ROOT" in str(exc)
    else:
        raise AssertionError("invalid host root should be rejected")

    monkeypatch.setenv("WORKSPACE_EXECUTION_BACKEND", "local")
    try:
        workspace_execution._validate_workspace_backend_config()
    except ValueError as exc:
        assert "WORKSPACE_EXECUTION_BACKEND=local" in str(exc)
    else:
        raise AssertionError("invalid backend should be rejected")

    monkeypatch.setenv("OPENSANDBOX_TIMEOUT_SECONDS", "bad")
    assert workspace_execution._env_int("OPENSANDBOX_TIMEOUT_SECONDS", 600) == 600
    monkeypatch.setenv("OPENSANDBOX_TIMEOUT_SECONDS", "40")
    assert workspace_execution._workspace_session_ttl_seconds() == 30
    monkeypatch.setenv("OPENSANDBOX_TIMEOUT_SECONDS", "1000")
    assert workspace_execution._workspace_session_ttl_seconds() == 300

    monkeypatch.setenv("OPENSANDBOX_PYTHON_BIN", "")
    assert workspace_execution._sandbox_python_bin() == "python3"
    monkeypatch.delenv("OPENSANDBOX_URL", raising=False)
    monkeypatch.setattr(workspace_execution, "_import_opensandbox_sdk", lambda: (None, object, None, None))
    try:
        workspace_execution._connection_config(60)
    except ValueError as exc:
        assert "OPENSANDBOX_URL" in str(exc)
    else:
        raise AssertionError("missing opensandbox url should be rejected")

    assert workspace_execution.sandbox_visible_path("") == workspace_execution.sandbox_thread_root()
    assert workspace_execution.sandbox_path_to_visible(workspace_execution.sandbox_workspace_dir()) == f"{workspace_execution.sandbox_thread_root()}/workspace"
    for path, message in [
        ("bad\x00path", "空字节"),
        ("/etc/passwd", "当前会话目录"),
        ("logs/app.log", "workspace/outputs/uploads/runtime-skills"),
        ("uploads/input.csv", "只允许写入 workspace/ 或 outputs/"),
    ]:
        try:
            workspace_execution.sandbox_visible_path(path, writable=path.startswith("uploads"))
        except ValueError as exc:
            assert message in str(exc)
        else:
            raise AssertionError(f"{path} should be rejected")


def test_workspace_date_part_reads_existing_marker_and_falls_back(tmp_path):
    workspace_root = tmp_path / "thread"
    workspace_root.mkdir()
    marker = workspace_root / ".sandbox_workspace_meta.json"
    marker.write_text('{"date": "20260102"}', encoding="utf-8")
    assert workspace_execution._workspace_date_part(str(workspace_root)) == "20260102"

    marker.write_text("{bad json", encoding="utf-8")
    date_part = workspace_execution._workspace_date_part(str(workspace_root))
    assert len(date_part) == 8
    assert marker.exists()


def test_workspace_session_cache_expiry_attach_and_invalidate(tmp_path, monkeypatch):
    workspace_execution._WORKSPACE_EXECUTION_SESSION_CACHE.clear()
    workspace_execution._WORKSPACE_EXECUTION_SESSION_LOCKS.clear()
    events = []

    class FakeSandbox:
        async def close(self):
            events.append("close")

        async def kill(self):
            events.append("kill")

    old_session = SandboxExecutionSession(sandbox=FakeSandbox(), sandbox_id="old", workspace_root=str(tmp_path), host_workspace_path="/host/old")
    new_session = SandboxExecutionSession(sandbox=FakeSandbox(), sandbox_id="new", workspace_root=str(tmp_path), host_workspace_path="/host/new")

    async def fake_prepare(workspace_root, user_id="system"):
        events.append(("prepare", workspace_root, user_id))
        return new_session

    monkeypatch.setenv("OPENSANDBOX_HOST_WORKSPACE_ROOT", str(tmp_path / "host"))
    monkeypatch.setattr(workspace_execution, "prepare_workspace_execution_session", fake_prepare)
    cache_key = workspace_execution._workspace_session_cache_key(str(tmp_path), "u1")
    workspace_execution._WORKSPACE_EXECUTION_SESSION_CACHE[cache_key] = workspace_execution.CachedSandboxExecutionSession(old_session, expires_at=0)

    resolved = asyncio.run(workspace_execution.get_workspace_execution_session(str(tmp_path), user_id="u1"))
    assert resolved is new_session
    assert "close" in events
    assert ("prepare", str(tmp_path), "u1") in events

    replacement_session = SandboxExecutionSession(sandbox=FakeSandbox(), sandbox_id="replacement", workspace_root=str(tmp_path), host_workspace_path="/host/replacement")
    asyncio.run(workspace_execution.attach_workspace_execution_session(replacement_session, user_id="u1"))
    assert workspace_execution._WORKSPACE_EXECUTION_SESSION_CACHE[cache_key].session is replacement_session

    asyncio.run(workspace_execution.invalidate_workspace_execution_session(replacement_session, user_id="u1"))
    assert cache_key not in workspace_execution._WORKSPACE_EXECUTION_SESSION_CACHE
    assert "kill" in events
    workspace_execution._WORKSPACE_EXECUTION_SESSION_CACHE.clear()
    workspace_execution._WORKSPACE_EXECUTION_SESSION_LOCKS.clear()


def test_workspace_session_cache_hit_attach_none_and_cleanup_errors(tmp_path, monkeypatch):
    workspace_execution._WORKSPACE_EXECUTION_SESSION_CACHE.clear()
    workspace_execution._WORKSPACE_EXECUTION_SESSION_LOCKS.clear()
    events = []

    class FailingSandbox:
        async def close(self):
            events.append("close")
            raise RuntimeError("close failed")

        async def kill(self):
            events.append("kill")
            raise RuntimeError("kill failed")

    async def fake_prepare(*_args, **_kwargs):
        raise AssertionError("fresh session should not be prepared when cache is valid")

    monkeypatch.setenv("OPENSANDBOX_HOST_WORKSPACE_ROOT", str(tmp_path / "host"))
    monkeypatch.setattr(workspace_execution, "prepare_workspace_execution_session", fake_prepare)
    session = SandboxExecutionSession(sandbox=FailingSandbox(), sandbox_id="cached", workspace_root=str(tmp_path), host_workspace_path="/host/cached")
    cache_key = workspace_execution._workspace_session_cache_key(str(tmp_path), "u1")
    workspace_execution._WORKSPACE_EXECUTION_SESSION_CACHE[cache_key] = workspace_execution.CachedSandboxExecutionSession(session, expires_at=10**9)

    resolved = asyncio.run(workspace_execution.get_workspace_execution_session(str(tmp_path), user_id="u1"))
    asyncio.run(workspace_execution.attach_workspace_execution_session(None, user_id="u1"))
    asyncio.run(workspace_execution.invalidate_workspace_execution_session(None, user_id="u1"))
    asyncio.run(workspace_execution.cleanup_workspace_execution_session(session))
    asyncio.run(workspace_execution.release_workspace_execution_session(session))

    assert resolved is session
    assert cache_key not in workspace_execution._WORKSPACE_EXECUTION_SESSION_CACHE
    assert events == ["kill", "close", "close"]
    workspace_execution._WORKSPACE_EXECUTION_SESSION_CACHE.clear()
    workspace_execution._WORKSPACE_EXECUTION_SESSION_LOCKS.clear()


def test_sandbox_file_api_helpers_and_read_file_edges(monkeypatch):
    writes = []

    class FakeFiles:
        async def write_file(self, path, data, mode=0o644):
            writes.append((path, data, mode))

        async def read_bytes(self, path):
            if path.endswith("missing.txt"):
                raise FileNotFoundError(path)
            if path.endswith("dir"):
                raise IsADirectoryError(path)
            if path.endswith("binary.bin"):
                return b"\x00\x01"
            if "中文.dashboard.json" in path:
                raise RuntimeError("SDK download failed")
            return b"line1\nline2\n"

    class FakeSandbox:
        files = FakeFiles()

    session_ref = SandboxExecutionSessionRef()
    session_ref.current = SandboxExecutionSession(sandbox=FakeSandbox(), sandbox_id="sbx", workspace_root="/tmp/thread", host_workspace_path="/host/thread")

    asyncio.run(workspace_execution._write_sandbox_file(sandbox=FakeSandbox(), path="/tmp/a.txt", data=b"abc"))
    assert writes == [("/tmp/a.txt", b"abc", 0o644)]
    assert asyncio.run(workspace_execution._read_sandbox_file_bytes(sandbox=FakeSandbox(), path="/workspace/a.txt")) == b"line1\nline2\n"

    async def fake_run_sandbox_python_json(**kwargs):
        assert kwargs["payload"]["path"] == "/mnt/user-data/outputs/中文.dashboard.json"
        return {"content_b64": "5Lit5paH"}

    monkeypatch.setattr(workspace_execution, "run_sandbox_python_json", fake_run_sandbox_python_json)
    assert asyncio.run(workspace_execution._read_sandbox_file_bytes_with_fallback(
        session=session_ref.current,
        path="/mnt/user-data/outputs/中文.dashboard.json",
    )) == "中文".encode("utf-8")

    assert asyncio.run(workspace_execution.sandbox_read_text_file(session_ref=session_ref, file_path="/workspace/missing.txt", offset=0, limit=1))["exists"] is False
    assert asyncio.run(workspace_execution.sandbox_read_text_file(session_ref=session_ref, file_path="/workspace/dir", offset=0, limit=1))["is_dir"] is True
    assert asyncio.run(workspace_execution.sandbox_read_text_file(session_ref=session_ref, file_path="/workspace/binary.bin", offset=0, limit=1))["is_binary"] is True

    try:
        workspace_execution.require_sandbox_session(SandboxExecutionSessionRef())
    except ValueError as exc:
        assert "OpenSandbox 执行环境未准备" in str(exc)
    else:
        raise AssertionError("missing session should be rejected")

    try:
        asyncio.run(workspace_execution._write_sandbox_file(sandbox=object(), path="/tmp/a.txt", data=b"abc"))
    except ValueError as exc:
        assert "write_file" in str(exc)
    else:
        raise AssertionError("missing write_file should be rejected")

    try:
        asyncio.run(workspace_execution._read_sandbox_file_bytes(sandbox=object(), path="/tmp/a.txt"))
    except ValueError as exc:
        assert "read_bytes" in str(exc)
    else:
        raise AssertionError("missing read_bytes should be rejected")


def test_import_sdk_missing_and_command_status_lookup_failure(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "opensandbox":
            raise ImportError("missing opensandbox")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    try:
        workspace_execution._import_opensandbox_sdk()
    except ValueError as exc:
        assert "OpenSandbox Python SDK" in str(exc)
    else:
        raise AssertionError("missing opensandbox should be rejected")

    class FakeRunCommandOpts:
        def __init__(self, timeout):
            self.timeout = timeout

    class FakeCommands:
        async def run(self, command, opts):
            return SimpleNamespace(id="cmd-lookup-fail", logs=SimpleNamespace(stdout=[], stderr=[]), error=None)

        async def get_command_status(self, execution_id):
            raise RuntimeError("status unavailable")

    class FakeSandbox:
        commands = FakeCommands()

    monkeypatch.setattr(workspace_execution, "_import_opensandbox_sdk", lambda: (None, None, FakeRunCommandOpts, None))
    session = SandboxExecutionSession(sandbox=FakeSandbox(), sandbox_id="sbx", workspace_root="/tmp/thread", host_workspace_path="/host/thread")

    exit_code, _stdout, stderr = asyncio.run(workspace_execution.run_command_in_sandbox(session=session, command="echo ok", timeout_seconds=5))

    assert exit_code == 1
    assert "status unavailable" in stderr


def test_command_output_and_json_runner_error_branches(monkeypatch):
    execution = SimpleNamespace(
        id="cmd-1",
        logs=SimpleNamespace(stdout=[SimpleNamespace(text="ok")], stderr=[SimpleNamespace(text="warn")]),
        error=SimpleNamespace(name="RuntimeError", value="boom"),
    )
    exit_code, stdout, stderr = workspace_execution._extract_command_output(execution, command_status=None)
    assert exit_code == 1
    assert stdout == "ok"
    assert "RuntimeError: boom" in stderr

    execution_without_status = SimpleNamespace(id="cmd-2", logs=SimpleNamespace(stdout=[], stderr=[]), error=None)
    exit_code, _stdout, stderr = workspace_execution._extract_command_output(execution_without_status, command_status=None, status_lookup_error="lost")
    assert exit_code == 1
    assert "lost" in stderr

    async def fake_run_command_error(**kwargs):
        return 2, "stdout", "stderr"

    async def fake_run_command_empty(**kwargs):
        return 0, "", ""

    async def fake_run_command_invalid(**kwargs):
        return 0, "not-json", ""

    session = SandboxExecutionSession(sandbox=object(), sandbox_id="sbx", workspace_root="/tmp/thread", host_workspace_path="/host/thread")
    monkeypatch.setattr(workspace_execution, "run_command_in_sandbox", fake_run_command_error)
    try:
        asyncio.run(workspace_execution.run_sandbox_python_json(session=session, script_body="", payload={}))
    except ValueError as exc:
        assert "stderr" in str(exc)
    else:
        raise AssertionError("non-zero command should fail")

    monkeypatch.setattr(workspace_execution, "run_command_in_sandbox", fake_run_command_empty)
    try:
        asyncio.run(workspace_execution.run_sandbox_python_json(session=session, script_body="", payload={}))
    except ValueError as exc:
        assert "empty stdout" in str(exc)
    else:
        raise AssertionError("empty stdout should fail")

    monkeypatch.setattr(workspace_execution, "run_command_in_sandbox", fake_run_command_invalid)
    try:
        asyncio.run(workspace_execution.run_sandbox_python_json(session=session, script_body="", payload={}))
    except ValueError as exc:
        assert "invalid JSON" in str(exc)
    else:
        raise AssertionError("invalid json should fail")


def test_run_command_in_sandbox_uses_status_lookup(monkeypatch):
    class FakeRunCommandOpts:
        def __init__(self, timeout):
            self.timeout = timeout

    class FakeCommands:
        async def run(self, command, opts):
            assert command == "echo ok"
            assert opts.timeout.total_seconds() == 5
            return SimpleNamespace(id="cmd-1", logs=SimpleNamespace(stdout=[SimpleNamespace(text="ok")], stderr=[]), error=None)

        async def get_command_status(self, execution_id):
            assert execution_id == "cmd-1"
            return SimpleNamespace(exit_code=0)

    class FakeSandbox:
        commands = FakeCommands()

    monkeypatch.setattr(workspace_execution, "_import_opensandbox_sdk", lambda: (None, None, FakeRunCommandOpts, None))
    session = SandboxExecutionSession(sandbox=FakeSandbox(), sandbox_id="sbx", workspace_root="/tmp/thread", host_workspace_path="/host/thread")

    assert asyncio.run(workspace_execution.run_command_in_sandbox(session=session, command="echo ok", timeout_seconds=5)) == (0, "ok", "")


def test_seed_and_ensure_workspace_command_paths(tmp_path, monkeypatch):
    commands = []
    written = []

    class FakeFiles:
        async def write_file(self, path, data, mode=0o644):
            written.append((path, data, mode))

    class FakeSandbox:
        files = FakeFiles()

    session = SandboxExecutionSession(sandbox=FakeSandbox(), sandbox_id="sbx", workspace_root=str(tmp_path), host_workspace_path="/host/thread")

    async def fake_run_command_marker_exists(*, session, command, timeout_seconds):
        commands.append(command)
        return 0, "", ""

    monkeypatch.setattr(workspace_execution, "run_command_in_sandbox", fake_run_command_marker_exists)
    asyncio.run(workspace_execution._seed_workspace_if_needed(session=session))
    assert not written
    assert any("test -f" in command for command in commands)

    commands.clear()

    async def fake_run_command_seed_success(*, session, command, timeout_seconds):
        commands.append(command)
        if "test -f" in command:
            return 1, "", ""
        return 0, "ok", ""

    monkeypatch.setattr(workspace_execution, "run_command_in_sandbox", fake_run_command_seed_success)
    asyncio.run(workspace_execution._seed_workspace_if_needed(session=session))
    assert written and written[0][0].startswith("/tmp/fxcjg-host-seed-")
    assert "tar --no-same-owner" in commands[-1]

    async def fake_run_command_fail(*, session, command, timeout_seconds):
        return 1, "", "mkdir failed"

    monkeypatch.setattr(workspace_execution, "run_command_in_sandbox", fake_run_command_fail)
    try:
        asyncio.run(workspace_execution._ensure_workspace_dirs(session=session))
    except ValueError as exc:
        assert "初始化 OpenSandbox 工作目录失败" in str(exc)
    else:
        raise AssertionError("ensure dirs failure should raise")


def test_artifact_payload_parsing_and_sandbox_lookup(monkeypatch):
    graph_payload = {
        "version": "1.0",
        "chart_type": "graph",
        "data": {"nodes": [{"id": "a"}], "edges": [{"source": "a", "target": "b"}]},
    }
    table_artifact = workspace_execution._artifact_payload_from_bytes(visible_path="/mnt/user-data/outputs/table.table.csv", data=b"a,b\n")
    doc_artifact = workspace_execution._artifact_payload_from_bytes(visible_path="/mnt/user-data/outputs/report.docx", data=b"doc")
    graph_artifact = workspace_execution._artifact_payload_from_bytes(visible_path="/mnt/user-data/outputs/graph.chart.json", data=__import__("json").dumps(graph_payload).encode("utf-8"))
    invalid_chart = workspace_execution._artifact_payload_from_bytes(
        visible_path="/mnt/user-data/outputs/bad.chart.json",
        data=b'{"version":"1.0","chart_type":"line","data":[{"x":1}],"encoding":{"x":"missing","y":["y"]}}',
    )

    assert table_artifact["renderer"] == "table"
    assert doc_artifact["artifact_type"] == "document"
    assert graph_artifact["chart_spec"]["chart_type"] == "graph"
    assert invalid_chart == {}
    assert workspace_execution._artifact_kind_from_path("demo.unknown") == ""

    class FakeFiles:
        async def read_bytes(self, path):
            if path.endswith("missing.chart.json"):
                raise FileNotFoundError(path)
            return __import__("json").dumps(graph_payload).encode("utf-8")

    class FakeSandbox:
        files = FakeFiles()

    session_ref = SandboxExecutionSessionRef()
    session_ref.current = SandboxExecutionSession(sandbox=FakeSandbox(), sandbox_id="sbx", workspace_root="/tmp/thread", host_workspace_path="/host/thread")

    assert asyncio.run(workspace_execution.sandbox_artifact_payload(session_ref=session_ref, file_path="/workspace/demo.chart.json")) == {}
    assert asyncio.run(workspace_execution.sandbox_artifact_payload(session_ref=session_ref, file_path="/outputs/graph.chart.json"))["renderer"] == "chart"
    assert asyncio.run(workspace_execution.sandbox_artifact_payload(session_ref=session_ref, file_path="/outputs/missing.chart.json")) == {}


def test_artifact_payload_additional_renderers_and_invalid_chart_edges():
    invalid_json = workspace_execution._artifact_payload_from_bytes(visible_path="/mnt/user-data/outputs/bad.json", data=b"{bad")
    xlsx_artifact = workspace_execution._artifact_payload_from_bytes(visible_path="/mnt/user-data/outputs/report.xlsx", data=b"xlsx")
    pdf_artifact = workspace_execution._artifact_payload_from_bytes(visible_path="/mnt/user-data/outputs/report.pdf", data=b"pdf")
    image_artifact = workspace_execution._artifact_payload_from_bytes(visible_path="/mnt/user-data/outputs/screenshot.png", data=b"png")
    fallback_dashboard = workspace_execution._artifact_payload_from_bytes(
        visible_path="/mnt/user-data/outputs/dashboard.json",
        data=b'{"cards":[{"title":"A"}]}',
    )
    fallback_chart = workspace_execution._artifact_payload_from_bytes(
        visible_path="/mnt/user-data/outputs/chart.json",
        data=b'{"version":"1.0","chart_type":"line","data":[{"x":"1","y":2}],"encoding":{"x":"x","y":["y"]}}',
    )

    assert invalid_json == {}
    assert xlsx_artifact["renderer"] == "spreadsheet"
    assert pdf_artifact["renderer"] == "pdf"
    assert image_artifact["renderer"] == "image"
    assert fallback_dashboard["renderer"] == "dashboard"
    assert fallback_chart["renderer"] == "chart"

    for payload in [
        [],
        {"version": "1.0", "chart_type": "unknown", "data": [], "encoding": {}},
        {"version": "0.9", "chart_type": "line", "data": [], "encoding": {}},
        {"version": "1.0", "chart_type": "line", "data": [], "encoding": {"x": "x", "y": ["y"]}},
        {"version": "1.0", "chart_type": "line", "data": [{"x": 1, "y": 2}], "encoding": None},
        {"version": "1.0", "chart_type": "line", "data": [{"x": 1}], "encoding": {"x": "x", "y": []}},
        {"version": "1.0", "chart_type": "line", "data": [{"y": 2}], "encoding": {"x": "x", "y": ["y"]}},
        {"version": "1.0", "chart_type": "line", "data": [{"x": 1}], "encoding": {"x": "x", "y": ["missing"]}},
        {"version": "1.0", "chart_type": "graph", "data": {"nodes": [], "edges": []}},
    ]:
        assert workspace_execution._parse_chart_spec_payload(payload) is None


def test_sandbox_write_patch_upload_and_read_workspace(monkeypatch, tmp_path):
    writes = []
    commands = []
    invalidated = []

    class FakeFiles:
        async def write_file(self, path, data, mode=0o644):
            writes.append((path, data, mode))

        async def read_bytes(self, path):
            if path.endswith("large.txt"):
                return b"123456"
            return "内容".encode("utf-8")

    class FakeSandbox:
        files = FakeFiles()

    session = SandboxExecutionSession(sandbox=FakeSandbox(), sandbox_id="sbx", workspace_root=str(tmp_path), host_workspace_path="/host/thread")
    session_ref = SandboxExecutionSessionRef()
    session_ref.current = session

    async def fake_run_sandbox_python_json(**kwargs):
        payload = kwargs.get("payload") or {}
        if "ops" in payload:
            return {"actions": [f"{op['kind']} {op.get('visible_path') or op['path']}" for op in payload["ops"]]}
        if "content" in payload:
            return {"bytes": len(str(payload.get("content") or "").encode("utf-8"))}
        return {"ok": True}

    async def fake_run_command_in_sandbox(*, session, command, timeout_seconds):
        commands.append(command)
        return 0, "ok", ""

    async def fake_get_workspace_execution_session(workspace_root, user_id="system"):
        return session

    async def fake_invalidate(session, user_id="system"):
        invalidated.append((session.sandbox_id, user_id))

    monkeypatch.setattr(workspace_execution, "run_sandbox_python_json", fake_run_sandbox_python_json)
    monkeypatch.setattr(workspace_execution, "run_command_in_sandbox", fake_run_command_in_sandbox)
    monkeypatch.setattr(workspace_execution, "get_workspace_execution_session", fake_get_workspace_execution_session)
    monkeypatch.setattr(workspace_execution, "invalidate_workspace_execution_session", fake_invalidate)

    overwrite = asyncio.run(workspace_execution.sandbox_write_text_file(session_ref=session_ref, file_path="/workspace/a.txt", content="你好", mode="overwrite"))
    append = asyncio.run(workspace_execution.sandbox_write_text_file(session_ref=session_ref, file_path="/workspace/a.txt", content="追加", mode="append"))
    actions = asyncio.run(workspace_execution.sandbox_apply_patch_ops(session_ref=session_ref, ops=[{"kind": "add", "path": "/workspace/new.txt", "visible_path": "/workspace/new.txt", "lines": ["a"]}], dry_run=True))
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# skill", encoding="utf-8")
    uploaded = asyncio.run(workspace_execution.upload_skill_directory_to_sandbox(session_ref=session_ref, skill_name="demo-skill", source_dir=skill_dir))
    read_result = asyncio.run(workspace_execution.read_workspace_file_from_sandbox(workspace_root=str(tmp_path), file_path="/workspace/a.txt", user_id="u1", max_bytes=10))

    assert overwrite["bytes"] == len("你好".encode("utf-8"))
    assert append["bytes"] == len("追加".encode("utf-8"))
    assert writes[0][1] == "你好".encode("utf-8")
    assert actions == ["add /workspace/new.txt"]
    assert uploaded.endswith("/runtime-skills/demo-skill")
    assert "tar --no-same-owner" in commands[-1]
    assert read_result["content"] == "内容"

    try:
        asyncio.run(workspace_execution.upload_skill_directory_to_sandbox(session_ref=session_ref, skill_name="Demo Skill", source_dir=skill_dir))
    except ValueError as exc:
        assert "非法技能名称" in str(exc)
    else:
        raise AssertionError("invalid skill name should be rejected")

    try:
        asyncio.run(workspace_execution.read_workspace_file_from_sandbox(workspace_root=str(tmp_path), file_path="/workspace/large.txt", user_id="u1", max_bytes=3))
    except ValueError as exc:
        assert "文件过大" in str(exc)
    else:
        raise AssertionError("large file should be rejected")
    assert invalidated == [("sbx", "u1")]


def test_list_workspace_files_skips_bad_items_and_invalidates_on_failure(monkeypatch, tmp_path):
    session = SandboxExecutionSession(sandbox=object(), sandbox_id="sbx", workspace_root=str(tmp_path), host_workspace_path="/host/thread")
    invalidated = []

    async def fake_get_workspace_execution_session(workspace_root, user_id="system"):
        return session

    async def fake_invalidate(session, user_id="system"):
        invalidated.append((session.sandbox_id, user_id))

    async def fake_run_with_bad_items(**_kwargs):
        return {
            "files": [
                "bad",
                {"path": "/mnt/user-data/outputs/bad.chart.json", "source": "outputs", "content_hex": "not-hex"},
                {"path": "/mnt/user-data/outputs/table.table.csv", "source": "outputs"},
                {"path": "/mnt/user-data/workspace/a.txt", "source": "workspace"},
            ]
        }

    monkeypatch.setattr(workspace_execution, "get_workspace_execution_session", fake_get_workspace_execution_session)
    monkeypatch.setattr(workspace_execution, "invalidate_workspace_execution_session", fake_invalidate)
    monkeypatch.setattr(workspace_execution, "run_sandbox_python_json", fake_run_with_bad_items)

    files = asyncio.run(workspace_execution.list_workspace_files_from_sandbox(workspace_root=str(tmp_path), user_id="u1"))

    assert len(files) == 3
    assert files[1]["renderer"] == "table"
    assert invalidated == []

    async def fake_run_failure(**_kwargs):
        raise RuntimeError("list failed")

    monkeypatch.setattr(workspace_execution, "run_sandbox_python_json", fake_run_failure)

    try:
        asyncio.run(workspace_execution.list_workspace_files_from_sandbox(workspace_root=str(tmp_path), user_id="u1"))
    except RuntimeError as exc:
        assert "list failed" in str(exc)
    else:
        raise AssertionError("list failure should raise")

    assert invalidated == [("sbx", "u1")]


def test_upload_skill_directory_reports_command_failure(monkeypatch, tmp_path):
    class FakeFiles:
        async def write_file(self, path, data, mode=0o644):
            return None

    class FakeSandbox:
        files = FakeFiles()

    async def fake_run_command_in_sandbox(*, session, command, timeout_seconds):
        return 1, "", "tar failed"

    monkeypatch.setattr(workspace_execution, "run_command_in_sandbox", fake_run_command_in_sandbox)
    session_ref = SandboxExecutionSessionRef()
    session_ref.current = SandboxExecutionSession(sandbox=FakeSandbox(), sandbox_id="sbx", workspace_root=str(tmp_path), host_workspace_path="/host/thread")
    source_dir = tmp_path / "skill"
    source_dir.mkdir()

    try:
        asyncio.run(workspace_execution.upload_skill_directory_to_sandbox(session_ref=session_ref, skill_name="demo-skill", source_dir=source_dir))
    except ValueError as exc:
        assert "上传技能到 OpenSandbox 失败" in str(exc)
    else:
        raise AssertionError("upload command failure should raise")


def test_truncate_output_marks_long_text():
    text, truncated = workspace_execution.truncate_output("abcdef", limit=3)

    assert text == "abc\n...[truncated]"
    assert truncated is True
    assert workspace_execution.truncate_output("abc", limit=3) == ("abc", False)


def test_workspace_execution_remaining_config_and_sdk_branches(tmp_path, monkeypatch):
    import types

    opensandbox_module = types.ModuleType("opensandbox")
    config_module = types.ModuleType("opensandbox.config")
    execd_module = types.ModuleType("opensandbox.models.execd")
    sandboxes_module = types.ModuleType("opensandbox.models.sandboxes")
    opensandbox_module.Sandbox = type("FakeSandbox", (), {})
    config_module.ConnectionConfig = type("FakeConnectionConfig", (), {})
    execd_module.RunCommandOpts = type("FakeRunCommandOpts", (), {})
    sandboxes_module.Host = type("FakeHost", (), {})
    sandboxes_module.NetworkPolicy = type("FakeNetworkPolicy", (), {})
    sandboxes_module.NetworkRule = type("FakeNetworkRule", (), {})
    sandboxes_module.Volume = type("FakeVolume", (), {})
    monkeypatch.setitem(sys.modules, "opensandbox", opensandbox_module)
    monkeypatch.setitem(sys.modules, "opensandbox.config", config_module)
    monkeypatch.setitem(sys.modules, "opensandbox.models.execd", execd_module)
    monkeypatch.setitem(sys.modules, "opensandbox.models.sandboxes", sandboxes_module)

    Sandbox, ConnectionConfig, RunCommandOpts, models = workspace_execution._import_opensandbox_sdk()

    assert Sandbox is opensandbox_module.Sandbox
    assert ConnectionConfig is config_module.ConnectionConfig
    assert RunCommandOpts is execd_module.RunCommandOpts
    assert models[0] is sandboxes_module.Host

    missing_root = tmp_path / "missing-thread"
    date_part = workspace_execution._workspace_date_part(str(missing_root))
    assert len(date_part) == 8

    workspace_execution._WORKSPACE_EXECUTION_SESSION_LOCKS.clear()
    first = workspace_execution._workspace_session_lock("cache-a")
    second = workspace_execution._workspace_session_lock("cache-a")
    assert first is second

    monkeypatch.setenv("OPENSANDBOX_PYTHON_BIN", "   ")
    assert workspace_execution._sandbox_python_bin() == "python3"
    workspace_execution._WORKSPACE_EXECUTION_SESSION_LOCKS.clear()


def test_workspace_tar_builders_skip_excluded_nested_paths(tmp_path):
    thread_root = tmp_path / "thread"
    (thread_root / "workspace" / "node_modules").mkdir(parents=True)
    (thread_root / "workspace" / "node_modules" / "skip.js").write_text("skip", encoding="utf-8")
    (thread_root / "workspace" / "keep").mkdir()
    (thread_root / "workspace" / "keep" / ".cache").mkdir()
    (thread_root / "workspace" / "keep" / ".cache" / "skip.txt").write_text("skip", encoding="utf-8")
    (thread_root / "workspace" / "keep" / "ok.txt").write_text("ok", encoding="utf-8")

    seed_data = workspace_execution._build_seed_tar_bytes(thread_root)
    directory_data = workspace_execution._build_directory_tar_bytes(thread_root / "workspace")

    import io
    import tarfile

    with tarfile.open(fileobj=io.BytesIO(seed_data), mode="r:gz") as tar:
        seed_names = sorted(member.name for member in tar.getmembers())
    with tarfile.open(fileobj=io.BytesIO(directory_data), mode="r:gz") as tar:
        directory_names = sorted(member.name for member in tar.getmembers())

    assert "workspace/keep/ok.txt" in seed_names
    assert "keep/ok.txt" in directory_names
    assert all("node_modules" not in name for name in seed_names + directory_names)
    assert all(".cache" not in name for name in seed_names + directory_names)


def test_workspace_prepare_and_command_edge_branches(tmp_path, monkeypatch):
    captured = {}

    class FakeSandbox:
        id = "sbx-deny"

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

    class FakeNetworkPolicy:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeNetworkRule:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    async def fake_ensure_workspace_dirs(*, session):
        return None

    async def fake_seed_workspace_if_needed(*, session):
        return None

    monkeypatch.setenv("WORKSPACE_EXECUTION_BACKEND", "opensandbox")
    monkeypatch.setenv("OPENSANDBOX_WORKSPACE_SYNC_MODE", "remote-host-volume")
    monkeypatch.setenv("OPENSANDBOX_URL", "http://opensandbox.example.com")
    monkeypatch.setenv("OPENSANDBOX_NETWORK_DEFAULT_ACTION", "deny")
    monkeypatch.setattr(workspace_execution, "_import_opensandbox_sdk", lambda: (FakeSandbox, FakeConnectionConfig, None, (FakeHost, FakeNetworkPolicy, FakeNetworkRule, FakeVolume)))
    monkeypatch.setattr(workspace_execution, "_ensure_workspace_dirs", fake_ensure_workspace_dirs)
    monkeypatch.setattr(workspace_execution, "_seed_workspace_if_needed", fake_seed_workspace_if_needed)

    workspace_root = tmp_path / "thread"
    workspace_root.mkdir()
    session = asyncio.run(workspace_execution.prepare_workspace_execution_session(str(workspace_root), user_id="u1"))

    assert session.sandbox_id == "sbx-deny"
    assert isinstance(captured["network_policy"], FakeNetworkPolicy)

    class FakeRunCommandOpts:
        def __init__(self, timeout):
            self.timeout = timeout

    class FakeCommandsNoId:
        async def run(self, command, opts):
            return SimpleNamespace(id="", logs=SimpleNamespace(stdout=[SimpleNamespace(text="ok")], stderr=[]), error=None)

    class FakeSandboxNoId:
        commands = FakeCommandsNoId()

    monkeypatch.setattr(workspace_execution, "_import_opensandbox_sdk", lambda: (None, None, FakeRunCommandOpts, None))
    no_id_session = SandboxExecutionSession(sandbox=FakeSandboxNoId(), sandbox_id="sbx", workspace_root=str(workspace_root), host_workspace_path="/host/thread")
    assert asyncio.run(workspace_execution.run_command_in_sandbox(session=no_id_session, command="echo ok", timeout_seconds=5)) == (0, "ok", "")

    empty_error_execution = SimpleNamespace(
        logs=SimpleNamespace(stdout=[], stderr=[]),
        error=SimpleNamespace(name=" ", value=""),
    )
    assert workspace_execution._extract_command_output(empty_error_execution, command_status=None) == (1, "", "")


def test_seed_workspace_failure_and_path_normalization(tmp_path, monkeypatch):
    commands = []

    class FakeFiles:
        async def write_file(self, path, data, mode=0o644):
            return None

    class FakeSandbox:
        files = FakeFiles()

    session = SandboxExecutionSession(sandbox=FakeSandbox(), sandbox_id="sbx", workspace_root=str(tmp_path), host_workspace_path="/host/thread")

    async def fake_run_command_seed_failure(*, session, command, timeout_seconds):
        commands.append(command)
        if "test -f" in command:
            return 1, "", ""
        if "tar --no-same-owner" not in command:
            return 0, "", ""
        return 1, "stdout failed", ""

    monkeypatch.setattr(workspace_execution, "run_command_in_sandbox", fake_run_command_seed_failure)

    try:
        asyncio.run(workspace_execution._seed_workspace_if_needed(session=session))
    except ValueError as exc:
        assert "初始化 OpenSandbox host 工作区失败" in str(exc)
        assert "stdout failed" in str(exc)
    else:
        raise AssertionError("seed command failure should raise")

    assert any("test -f" in command for command in commands)
    assert workspace_execution.sandbox_visible_path("././workspace/a.txt") == f"{workspace_execution.sandbox_workspace_dir()}/a.txt"
    assert workspace_execution.sandbox_path_to_visible(f"{workspace_execution.sandbox_outputs_dir()}/chart.json") == f"{workspace_execution.sandbox_thread_root()}/outputs/chart.json"
    asyncio.run(workspace_execution.cleanup_workspace_execution_session(None))
    asyncio.run(workspace_execution.release_workspace_execution_session(None))


def test_chart_spec_parser_covers_required_x_y_and_row_field_edges():
    assert workspace_execution._parse_chart_spec_payload({
        "version": "1.0",
        "chart_type": "line",
        "data": [{"y": 1}],
        "encoding": {"y": ["y"]},
    }) is None

    assert workspace_execution._parse_chart_spec_payload({
        "version": "1.0",
        "chart_type": "line",
        "data": ["not-a-row"],
        "encoding": {"x": "x", "y": ["y"]},
    }) is None

    valid_without_data_rows = {
        "version": "1.0",
        "chart_type": "bar",
        "data": [{"x": "A", "y": 1}, "ignored-row"],
        "encoding": {"x": "x", "y": ["y"]},
    }
    parsed = workspace_execution._parse_chart_spec_payload(valid_without_data_rows)

    assert parsed is valid_without_data_rows
    assert parsed["chart_type"] == "bar"


def test_workspace_execution_remaining_cache_path_and_chart_branches(tmp_path, monkeypatch):
    workspace_root = tmp_path / "thread"
    workspace_root.mkdir()
    marker = workspace_root / ".sandbox_workspace_meta.json"
    marker.write_text('{"date": "not-a-date"}', encoding="utf-8")

    assert len(workspace_execution._workspace_date_part(str(workspace_root))) == 8
    assert workspace_execution.sandbox_path_to_visible("/outside/session.txt") == "/outside/session.txt"

    table_without_rows = {
        "version": "1.0",
        "chart_type": "table",
        "data": [],
        "encoding": {},
    }
    table_without_y_list = {
        "version": "1.0",
        "chart_type": "table",
        "data": [{"name": "A"}],
        "encoding": {"x": "missing"},
    }

    assert workspace_execution._parse_chart_spec_payload(table_without_rows) is table_without_rows
    assert workspace_execution._parse_chart_spec_payload(table_without_y_list) is table_without_y_list

    workspace_execution._WORKSPACE_EXECUTION_SESSION_CACHE.clear()
    workspace_execution._WORKSPACE_EXECUTION_SESSION_LOCKS.clear()

    class FakeSandbox:
        async def close(self):
            return None

        async def kill(self):
            return None

    session = SandboxExecutionSession(sandbox=FakeSandbox(), sandbox_id="cached", workspace_root=str(workspace_root), host_workspace_path="/host/thread")
    cache_key = workspace_execution._workspace_session_cache_key(str(workspace_root), "u1")
    workspace_execution._WORKSPACE_EXECUTION_SESSION_CACHE[cache_key] = workspace_execution.CachedSandboxExecutionSession(session, expires_at=50)

    monotonic_calls = []

    def fake_monotonic():
        monotonic_calls.append(None)
        return 100 if len(monotonic_calls) == 1 else 1

    monkeypatch.setattr(workspace_execution, "time", SimpleNamespace(monotonic=fake_monotonic))

    async def fail_prepare(*_args, **_kwargs):
        raise AssertionError("lock-inner cache hit should not prepare a new session")

    monkeypatch.setattr(workspace_execution, "prepare_workspace_execution_session", fail_prepare)

    assert asyncio.run(workspace_execution.get_workspace_execution_session(str(workspace_root), user_id="u1")) is session
    asyncio.run(workspace_execution.attach_workspace_execution_session(session, user_id="u1"))

    other_session = SandboxExecutionSession(sandbox=FakeSandbox(), sandbox_id="other", workspace_root=str(workspace_root), host_workspace_path="/host/other")
    other_key = workspace_execution._workspace_session_cache_key(str(workspace_root), "other-user")
    workspace_execution._WORKSPACE_EXECUTION_SESSION_CACHE[other_key] = workspace_execution.CachedSandboxExecutionSession(other_session, expires_at=50)
    asyncio.run(workspace_execution.invalidate_workspace_execution_session(session, user_id="missing-user"))

    assert other_key in workspace_execution._WORKSPACE_EXECUTION_SESSION_CACHE
    workspace_execution._WORKSPACE_EXECUTION_SESSION_CACHE.clear()
    workspace_execution._WORKSPACE_EXECUTION_SESSION_LOCKS.clear()


def test_workspace_tar_builders_continue_on_excluded_walk_entries(tmp_path, monkeypatch):
    root = tmp_path / "thread"
    workspace_dir = root / "workspace"
    workspace_dir.mkdir(parents=True)
    excluded_dir = workspace_dir / ".cache"
    excluded_dir.mkdir()
    excluded_file = workspace_dir / ".git"
    excluded_file.write_text("skip", encoding="utf-8")
    keep_file = workspace_dir / "keep.txt"
    keep_file.write_text("keep", encoding="utf-8")

    real_walk = workspace_execution.os.walk

    def fake_seed_walk(source):
        if Path(source) == workspace_dir:
            yield str(workspace_dir), [".cache"], [".git", "keep.txt"]
            yield str(excluded_dir), [], ["nested.txt"]
        else:
            yield from real_walk(source)

    monkeypatch.setattr(workspace_execution.os, "walk", fake_seed_walk)
    seed_data = workspace_execution._build_seed_tar_bytes(root)
    directory_data = workspace_execution._build_directory_tar_bytes(workspace_dir)

    import io
    import tarfile

    with tarfile.open(fileobj=io.BytesIO(seed_data), mode="r:gz") as tar:
        seed_names = sorted(member.name for member in tar.getmembers())
    with tarfile.open(fileobj=io.BytesIO(directory_data), mode="r:gz") as tar:
        directory_names = sorted(member.name for member in tar.getmembers())

    assert "workspace/keep.txt" in seed_names
    assert "keep.txt" in directory_names
    assert all(".cache" not in name for name in seed_names + directory_names)
    assert all(".git" not in name for name in seed_names + directory_names)
