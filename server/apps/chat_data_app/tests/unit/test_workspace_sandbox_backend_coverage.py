"""SandboxWorkspaceBackend 覆盖率补充：适配转发、会话缓存与命令执行边界。

全部通过 monkeypatch 替换模块级 sandbox_* 函数与 SDK 调用，不访问真实 OpenSandbox。
"""

import asyncio
from types import SimpleNamespace

import pytest

from apps.chat_data_app.agent.workspace import workspace_sandbox_backend as backend_mod
from apps.chat_data_app.agent.workspace.workspace_sandbox_api import SandboxExecutionSessionRef
from apps.chat_data_app.agent.workspace.workspace_sandbox_backend import SandboxWorkspaceBackend


class FakeRuntime:
    def __init__(self):
        self.workspace_root = "/host/user-data"
        self.user_id = "u1"
        self.runtime_skills_dir = None
        self.snapshot_calls = 0

    async def _ensure_snapshot_async(self):
        self.snapshot_calls += 1


def _backend_with_session():
    backend = SandboxWorkspaceBackend()
    backend.session_ref.current = SimpleNamespace(sandbox_id="sbx")
    return backend


def _record(monkeypatch, name, payload):
    calls = []

    async def fake(**kwargs):
        calls.append(kwargs)
        return payload

    monkeypatch.setattr(backend_mod, name, fake)
    return calls


def test_all_adapter_methods_forward_to_workspace_ops(monkeypatch):
    backend = _backend_with_session()
    runtime = FakeRuntime()

    calls = {}
    for name in (
        "sandbox_read_text_file",
        "sandbox_write_text_file",
        "sandbox_write_internal_text_files",
        "sandbox_delete_internal_text_file",
        "sandbox_write_tool_result",
        "sandbox_replace_text_file",
        "sandbox_artifact_payload",
        "sandbox_render_office_html",
        "sandbox_list_dir",
        "sandbox_create_dir",
        "sandbox_delete_path",
        "sandbox_move_path",
        "sandbox_copy_path",
        "sandbox_search_files",
        "sandbox_grep_text",
    ):
        calls[name] = _record(monkeypatch, name, {"func": name})

    assert asyncio.run(backend.read_text_file(runtime, file_path="/a.txt", offset=1, limit=2, max_chars=3)) == {
        "func": "sandbox_read_text_file"
    }
    assert asyncio.run(backend.write_text_file(runtime, file_path="/a.txt", content="x", mode="overwrite")) == {
        "func": "sandbox_write_text_file"
    }
    assert asyncio.run(backend.write_internal_text_files(runtime, entries=[("/semantic/a.yaml", "x")])) == {
        "func": "sandbox_write_internal_text_files"
    }
    assert asyncio.run(backend.delete_internal_text_files(runtime, path="/semantic/a.yaml")) == {
        "func": "sandbox_delete_internal_text_file"
    }
    assert asyncio.run(backend.write_tool_result(runtime, file_path="/outputs/r.json", content="{}")) == {
        "func": "sandbox_write_tool_result"
    }
    assert asyncio.run(backend.replace_text_file(runtime, file_path="/a.txt", old_content="a", new_content="b")) == {
        "func": "sandbox_replace_text_file"
    }
    assert asyncio.run(backend.artifact_payload(runtime, file_path="/outputs/r.json")) == {
        "func": "sandbox_artifact_payload"
    }
    assert asyncio.run(backend.render_office_html(runtime, file_path="/a.docx", max_chars=10)) == {
        "func": "sandbox_render_office_html"
    }
    assert asyncio.run(backend.list_dir(runtime, path="/workspace", max_depth=1)) == {"func": "sandbox_list_dir"}
    assert asyncio.run(backend.create_dir(runtime, path="/workspace/d")) == {"func": "sandbox_create_dir"}
    assert asyncio.run(backend.delete_path(runtime, path="/workspace/a", recursive=False, expected_type="file")) == {
        "func": "sandbox_delete_path"
    }
    assert asyncio.run(backend.move_path(runtime, source_path="/a", destination_path="/b")) == {
        "func": "sandbox_move_path"
    }
    assert asyncio.run(backend.copy_path(runtime, source_path="/a", destination_path="/b")) == {
        "func": "sandbox_copy_path"
    }
    assert asyncio.run(backend.search_files(runtime, path="/workspace", pattern="*.csv", max_results=1)) == {
        "func": "sandbox_search_files"
    }
    assert asyncio.run(
        backend.grep_text(
            runtime,
            path="/workspace",
            patterns=["x"],
            glob="**/*",
            max_results=1,
            max_chars=10,
            use_regex=True,
            ignore_case=True,
            context_lines=2,
            output_mode="count",
        )
    ) == {"func": "sandbox_grep_text"}

    assert all(call["session_ref"] is backend.session_ref for call in (c[0] for c in calls.values()))
    assert calls["sandbox_read_text_file"][0] == {
        "session_ref": backend.session_ref,
        "file_path": "/a.txt",
        "offset": 1,
        "limit": 2,
        "max_chars": 3,
    }
    grep_kwargs = calls["sandbox_grep_text"][0]
    assert grep_kwargs["use_regex"] is True and grep_kwargs["ignore_case"] is True
    assert grep_kwargs["context_lines"] == 2 and grep_kwargs["output_mode"] == "count"
    assert runtime.snapshot_calls == 0


def test_session_ref_caches_initialization_failure(monkeypatch):
    backend = SandboxWorkspaceBackend()

    async def failing_session(*_args, **_kwargs):
        raise RuntimeError("")

    monkeypatch.setattr(backend_mod, "get_workspace_execution_session", failing_session)
    runtime = FakeRuntime()

    with pytest.raises(RuntimeError):
        asyncio.run(backend._session_ref(runtime))
    assert backend._session_error == "OpenSandbox 执行环境初始化失败"
    assert runtime.snapshot_calls == 1

    # 初始化失败后不再重复打 SDK，直接复用缓存的错误信息
    with pytest.raises(ValueError, match="OpenSandbox 执行环境初始化失败"):
        asyncio.run(backend._session_ref(runtime))
    assert backend._session_error
    assert runtime.snapshot_calls == 1


def test_session_ref_returns_session_created_by_racing_lock(monkeypatch):
    backend = SandboxWorkspaceBackend()
    runtime = FakeRuntime()

    class RacingLock:
        async def __aenter__(self):
            backend.session_ref.current = SimpleNamespace(sandbox_id="raced")
            return self

        async def __aexit__(self, *_exc):
            return False

    backend._session_lock = RacingLock()
    assert asyncio.run(backend._session_ref(runtime)) is backend.session_ref
    assert backend.session_ref.current.sandbox_id == "raced"
    assert runtime.snapshot_calls == 0


def test_validate_bash_paths_rejects_out_of_boundary_absolute_paths():
    backend = SandboxWorkspaceBackend()
    runtime = FakeRuntime()

    asyncio.run(backend.validate_bash_paths(runtime, command="cat /mnt/user-data/workspace/a.txt"))
    with pytest.raises(ValueError, match="bash_run 只允许访问"):
        asyncio.run(backend.validate_bash_paths(runtime, command="cat /etc/passwd"))


def test_validate_bash_paths_rejects_semantic_snapshot():
    """与 local 后端同口径：语义快照只读，bash 命令一律不开放。"""
    backend = SandboxWorkspaceBackend()
    runtime = FakeRuntime()

    asyncio.run(backend.validate_bash_paths(runtime, command="cat /mnt/user-data/workspace/a.txt"))
    asyncio.run(backend.validate_bash_paths(runtime, command="cat /mnt/user-data/workspace/semantic-backup/a.txt"))
    with pytest.raises(ValueError, match="bash_run 只允许访问"):
        asyncio.run(
            backend.validate_bash_paths(runtime, command="echo x > /mnt/user-data/workspace/semantic/model.yaml")
        )


def test_run_bash_rejects_semantic_snapshot_cwd():
    backend = SandboxWorkspaceBackend()
    with pytest.raises(ValueError, match="只读语义模型快照"):
        asyncio.run(
            backend.run_bash(
                FakeRuntime(),
                command="ls",
                cwd="/mnt/user-data/workspace/semantic",
                timeout_seconds=10,
            )
        )


def _no_session_ref(backend, monkeypatch):
    async def no_session(_runtime):
        return SandboxExecutionSessionRef()

    monkeypatch.setattr(backend, "_session_ref", no_session)


def test_run_bash_requires_prepared_session(monkeypatch):
    backend = SandboxWorkspaceBackend()
    _no_session_ref(backend, monkeypatch)
    with pytest.raises(ValueError, match="OpenSandbox 执行环境未准备"):
        asyncio.run(backend.run_bash(FakeRuntime(), command="ls", cwd=None, timeout_seconds=10))


def test_run_bash_wraps_sync_failure(monkeypatch):
    backend = _backend_with_session()
    session = SimpleNamespace(operation_lock=asyncio.Lock(), sandbox_id="sbx")

    async def fake_session_ref(_runtime):
        ref = SandboxExecutionSessionRef()
        ref.current = session
        return ref

    async def fake_run(**_kwargs):
        return 0, "ok", ""

    async def failing_sync(_session):
        raise RuntimeError("sync down")

    monkeypatch.setattr(backend, "_session_ref", fake_session_ref)
    monkeypatch.setattr(backend_mod, "run_command_in_sandbox", fake_run)
    monkeypatch.setattr(backend_mod, "sync_sandbox_working_trees", failing_sync)

    with pytest.raises(ValueError, match="Bash 执行后的工作区同步失败: sync down"):
        asyncio.run(backend.run_bash(FakeRuntime(), command="ls", cwd="/mnt/user-data/workspace", timeout_seconds=10))


def test_run_officecli_rejects_skills_cwd():
    backend = SandboxWorkspaceBackend()
    with pytest.raises(ValueError, match="不允许将 cwd 设置为技能目录"):
        asyncio.run(
            backend.run_officecli(
                FakeRuntime(),
                args=["create", "/mnt/user-data/workspace/a.docx"],
                cwd="/mnt/skills/demo",
                timeout_seconds=10,
            )
        )


def test_run_officecli_requires_prepared_session(monkeypatch):
    backend = SandboxWorkspaceBackend()
    _no_session_ref(backend, monkeypatch)
    with pytest.raises(ValueError, match="OpenSandbox 执行环境未准备"):
        asyncio.run(
            backend.run_officecli(
                FakeRuntime(),
                args=["create", "/mnt/user-data/workspace/a.docx"],
                cwd=None,
                timeout_seconds=10,
            )
        )


def _officecli_session(monkeypatch):
    backend = SandboxWorkspaceBackend()
    session = SimpleNamespace(operation_lock=asyncio.Lock(), sandbox_id="sbx_office")

    async def fake_session_ref(_runtime):
        ref = SandboxExecutionSessionRef()
        ref.current = session
        return ref

    async def fake_run(**_kwargs):
        return 0, "done", ""

    monkeypatch.setattr(backend, "_session_ref", fake_session_ref)
    monkeypatch.setattr(backend_mod, "run_command_in_sandbox", fake_run)
    return backend


def test_run_officecli_syncs_changes_for_mutating_command(monkeypatch):
    backend = _officecli_session(monkeypatch)
    observed = {"created": ["/mnt/user-data/outputs/a.docx"], "updated": [], "deleted": [], "truncated": False}

    async def fake_sync(_session):
        return observed

    monkeypatch.setattr(backend_mod, "sync_sandbox_working_trees", fake_sync)
    result = asyncio.run(
        backend.run_officecli(
            FakeRuntime(),
            args=["create", "/mnt/user-data/workspace/a.docx"],
            cwd="/mnt/user-data/workspace",
            timeout_seconds=10,
        )
    )
    assert result["exit_code"] == 0
    assert result["changed_files"] == observed


def test_run_officecli_wraps_sync_failure(monkeypatch):
    backend = _officecli_session(monkeypatch)

    async def failing_sync(_session):
        raise RuntimeError("sync down")

    monkeypatch.setattr(backend_mod, "sync_sandbox_working_trees", failing_sync)
    with pytest.raises(ValueError, match="officecli 执行后的工作区同步失败: sync down"):
        asyncio.run(
            backend.run_officecli(
                FakeRuntime(),
                args=["create", "/mnt/user-data/workspace/a.docx"],
                cwd=None,
                timeout_seconds=10,
            )
        )


def test_renew_sandbox_async_skips_without_active_session(monkeypatch):
    """模型调用前没有 sandbox 时不创建、不续期。"""
    monkeypatch.setenv("OPENSANDBOX_RENEW_ENABLED", "true")
    backend = SandboxWorkspaceBackend()
    asyncio.run(backend.renew_sandbox_async())
    assert backend.session_ref.current is None


def test_renew_sandbox_async_renews_active_session(monkeypatch):
    """模型调用前后续期：按 OPENSANDBOX_TIMEOUT_SECONDS 续到 now+900。"""
    monkeypatch.setenv("OPENSANDBOX_RENEW_ENABLED", "true")
    monkeypatch.setenv("OPENSANDBOX_TIMEOUT_SECONDS", "900")
    backend = SandboxWorkspaceBackend()
    calls: list = []

    async def _renew(timeout):
        calls.append(timeout)

    backend.session_ref.current = SimpleNamespace(sandbox=SimpleNamespace(renew=_renew))
    asyncio.run(backend.renew_sandbox_async())
    assert len(calls) == 1
    assert calls[0].total_seconds() == 900


def test_renew_sandbox_async_can_be_disabled(monkeypatch):
    monkeypatch.setenv("OPENSANDBOX_RENEW_ENABLED", "false")
    backend = SandboxWorkspaceBackend()
    calls: list = []

    async def _renew(timeout):
        calls.append(timeout)

    backend.session_ref.current = SimpleNamespace(sandbox=SimpleNamespace(renew=_renew))
    asyncio.run(backend.renew_sandbox_async())
    assert calls == []


def test_renew_sandbox_async_failure_is_logged_not_raised(monkeypatch):
    monkeypatch.setenv("OPENSANDBOX_RENEW_ENABLED", "true")
    backend = SandboxWorkspaceBackend()
    calls: list = []

    async def _renew(timeout):
        calls.append(timeout)
        raise RuntimeError("control plane unavailable")

    backend.session_ref.current = SimpleNamespace(sandbox=SimpleNamespace(renew=_renew))
    asyncio.run(backend.renew_sandbox_async())
    assert len(calls) == 1


def test_renew_before_tool_is_throttled_and_session_ref_triggers_it(monkeypatch):
    """工具执行前续期：首次（上次续期较旧）续一次，30s 内第二次跳过。"""
    monkeypatch.setenv("OPENSANDBOX_RENEW_ENABLED", "true")
    monkeypatch.setenv("OPENSANDBOX_TIMEOUT_SECONDS", "900")
    backend = SandboxWorkspaceBackend()
    calls: list = []

    async def _renew(timeout):
        calls.append(timeout)

    backend.session_ref.current = SimpleNamespace(sandbox=SimpleNamespace(renew=_renew))
    asyncio.run(backend._session_ref(FakeRuntime()))
    assert len(calls) == 1
    asyncio.run(backend._session_ref(FakeRuntime()))
    assert len(calls) == 1


def test_read_sandbox_lifetime_seconds_default_and_env(monkeypatch):
    """创建与续期共用同一配置读取入口。"""
    from apps.chat_data_app.agent.workspace.workspace_session import read_sandbox_lifetime_seconds

    monkeypatch.delenv("OPENSANDBOX_TIMEOUT_SECONDS", raising=False)
    assert read_sandbox_lifetime_seconds() == 900
    monkeypatch.setenv("OPENSANDBOX_TIMEOUT_SECONDS", "600")
    assert read_sandbox_lifetime_seconds() == 600


def test_session_ref_rebuilds_when_renew_fails(monkeypatch):
    """续期失败（沙箱已过期）时丢弃旧 session，重新创建后再执行工具。"""
    new_session = SimpleNamespace(sandbox_id="new", sandbox=SimpleNamespace(renew=None))
    old_session = SimpleNamespace(sandbox_id="old", sandbox=SimpleNamespace(renew=None))

    async def _get_session(*_args, **_kwargs):
        return new_session

    async def _invalidate(_session):
        return None

    monkeypatch.setattr(backend_mod, "get_workspace_execution_session", _get_session)
    monkeypatch.setattr(backend_mod, "invalidate_workspace_execution_session", _invalidate)

    backend = SandboxWorkspaceBackend()
    backend.session_ref.current = old_session

    async def _fail_renew():
        return False

    monkeypatch.setattr(backend, "_renew_before_tool", _fail_renew)
    result = asyncio.run(backend._session_ref(FakeRuntime()))
    assert result.current is new_session


def test_renew_sandbox_async_skips_when_sandbox_has_no_renew(monkeypatch):
    """session 没有 renew 能力时安全跳过，不抛异常。"""
    monkeypatch.setenv("OPENSANDBOX_RENEW_ENABLED", "true")
    backend = SandboxWorkspaceBackend()
    backend.session_ref.current = SimpleNamespace(sandbox=SimpleNamespace())
    asyncio.run(backend.renew_sandbox_async())


def test_renew_sandbox_async_throttles_model_calls(monkeypatch):
    """模型调用频繁时 30s 内只续一次，避免控制面请求放大。"""
    monkeypatch.setenv("OPENSANDBOX_RENEW_ENABLED", "true")
    backend = SandboxWorkspaceBackend()
    calls: list = []

    async def _renew(timeout):
        calls.append(timeout)

    backend.session_ref.current = SimpleNamespace(sandbox=SimpleNamespace(renew=_renew))
    asyncio.run(backend.renew_sandbox_async())
    asyncio.run(backend.renew_sandbox_async())
    assert len(calls) == 1


def test_renew_failure_warning_is_deduplicated(monkeypatch):
    """连续续期失败只告警一次，避免日志刷屏。"""
    monkeypatch.setenv("OPENSANDBOX_RENEW_ENABLED", "true")
    backend = SandboxWorkspaceBackend()
    calls: list = []

    async def _renew(timeout):
        calls.append(timeout)
        raise RuntimeError("control plane unavailable")

    backend.session_ref.current = SimpleNamespace(sandbox=SimpleNamespace(renew=_renew))
    warnings: list[str] = []
    monkeypatch.setattr(backend_mod.logger, "warning", lambda message, *args, **kwargs: warnings.append(str(message)))
    for _ in range(2):
        backend._last_renew_at = 0.0
        asyncio.run(backend.renew_sandbox_async())
    assert len(calls) == 2
    assert sum("续期失败" in message for message in warnings) == 1


def test_renew_failure_keeps_sandbox_when_get_info_succeeds(monkeypatch):
    """续期失败但 get_info 探活成功：不销毁沙箱，返回可用。"""
    monkeypatch.setenv("OPENSANDBOX_RENEW_ENABLED", "true")
    backend = SandboxWorkspaceBackend()
    info_calls: list = []

    async def _renew(timeout):
        raise RuntimeError("control plane 500")

    async def _get_info():
        info_calls.append(True)

    backend.session_ref.current = SimpleNamespace(sandbox=SimpleNamespace(renew=_renew, get_info=_get_info))
    asyncio.run(backend.renew_sandbox_async())
    assert info_calls == [True]
    assert backend.session_ref.current is not None


def test_reset_session_recovers_pending_persistence_before_invalidate(monkeypatch):
    """重建前必须先回写 pending 变更，再销毁旧沙箱。"""
    backend = SandboxWorkspaceBackend()
    calls: list[str] = []

    async def _recover(_session):
        calls.append("recover")

    async def _invalidate(_session):
        calls.append("invalidate")

    monkeypatch.setattr(backend_mod, "recover_pending_persistence", _recover)
    monkeypatch.setattr(backend_mod, "invalidate_workspace_execution_session", _invalidate)
    backend.session_ref.current = SimpleNamespace(sandbox=SimpleNamespace())
    asyncio.run(backend._reset_session())
    assert calls == ["recover", "invalidate"]
