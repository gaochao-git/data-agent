"""Tests for the officecli_run tool and the run_officecli backend capability."""

import stat
from pathlib import Path

import pytest

from apps.chat_data_app.agent.tools.officecli_run import (
    _validate_args,
    create_officecli_run_tool,
    run_officecli_command,
)
from apps.chat_data_app.agent.workspace import runtime as cw_runtime_module
from apps.chat_data_app.agent.workspace import (
    workspace_sandbox_backend as sandbox_backend_module,
)
from apps.chat_data_app.agent.workspace.officecli_adapter import command_path_values, parse_officecli_args
from apps.chat_data_app.services.agent_run import (
    conversation_workspace as cw,  # noqa: F401  # 测试按模块属性引用 conversation_workspace，属有意重导出
)
from apps.chat_data_app.services.agent_run.conversation_workspace import (
    ConversationWorkspaceRuntime,
    SandboxWorkspaceBackend,
    ensure_conversation_workspace,
)


def _write_fake_officecli(tmp_path, log_path) -> str:
    """Return a fake `officecli` that echoes a marker and logs its argv to log_path.

    We log to an explicit file (not stdout) because the test data root is bind-mounted
    at /mnt/user-data, so the child's cwd/pwd may surface the virtual path even though
    the physical argv was passed correctly. Each argv element is logged on one line so
    tests can assert exact argument boundaries (a path split by a space would show up
    as two ARG lines).
    """
    bin_path = tmp_path / "officecli"
    bin_path.write_text(
        "#!/bin/sh\n"
        "echo OFFICECLI_CALLED\n"
        f'echo "ENV_HOME:$HOME" >> "{log_path}" 2>/dev/null\n'
        f'echo "ENV_SECRET:${{FXCJG_DB_PASSWORD:-<unset>}}" >> "{log_path}" 2>/dev/null\n'
        f'for a in "$@"; do echo "ARG:$a" >> "{log_path}" 2>/dev/null; done\n'
    )
    bin_path.chmod(bin_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(bin_path)


def _local_runtime(tmp_path):
    paths = ensure_conversation_workspace("officecli-thread", "u-officecli")
    return ConversationWorkspaceRuntime(
        workspace_root=paths["root"], user_id="u-officecli", backend="local"
    )


@pytest.mark.asyncio
async def test_validate_args_rejects_banned_subcommands():
    for banned in ("plugins", "watch", "unwatch", "open", "close", "mcp", "install", "skills"):
        with pytest.raises(ValueError):
            _validate_args([banned, "x.docx"])
    # option as first element is rejected (except global flags)
    with pytest.raises(ValueError):
        _validate_args(["--weird-flag"])
    # global flags are allowed as first element (verify availability)
    assert _validate_args(["--version"]) == ["--version"]
    assert _validate_args(["--help"]) == ["--help"]
    # empty / non-list rejected
    with pytest.raises(ValueError):
        _validate_args([])
    with pytest.raises(ValueError):
        _validate_args("view x.docx")


@pytest.mark.asyncio
async def test_validate_args_strips_binary_name_and_keeps_subcommand():
    assert _validate_args(["officecli", "view", "x.docx"]) == ["view", "x.docx"]
    assert _validate_args(["view", "x.docx"]) == ["view", "x.docx"]


def test_officecli_schema_extracts_file_roles_without_selectors():
    dump = parse_officecli_args([
        "dump", "/mnt/user-data/uploads/source.docx", "/body", "--out",
        "/mnt/user-data/workspace/dump.json",
    ])
    assert command_path_values(dump, "/mnt/user-data/workspace") == [
        (1, "/mnt/user-data/uploads/source.docx"),
        (4, "/mnt/user-data/workspace/dump.json"),
    ]
    assert 4 in dump.write_indices

    merge = parse_officecli_args([
        "merge", "template.docx", "out.docx", "--data", "/mnt/user-data/workspace/data.json",
    ])
    assert command_path_values(merge, "/mnt/user-data/workspace") == [
        (1, "/mnt/user-data/workspace/template.docx"),
        (2, "/mnt/user-data/workspace/out.docx"),
        (4, "/mnt/user-data/workspace/data.json"),
    ]
    assert 1 not in merge.write_indices
    assert 2 in merge.write_indices

    picture = parse_officecli_args([
        "add", "report.docx", "/body/p[1]", "--type", "picture", "--prop",
        "src=/mnt/user-data/uploads/a.png",
    ])
    assert any(path.endswith("/uploads/a.png") for _, path in command_path_values(picture, "/mnt/user-data/workspace"))

    background = parse_officecli_args([
        "add", "deck.pptx", "/", "--type", "slide", "--prop",
        "background=image:/mnt/user-data/workspace/hero.jpg",
    ])
    assert any(path.endswith("/workspace/hero.jpg") for _, path in command_path_values(background, "/mnt/user-data/workspace"))


def test_officecli_schema_marks_refresh_mutating_and_selectors_non_files():
    refresh = parse_officecli_args(["refresh", "/mnt/user-data/workspace/report.docx"])
    assert refresh.mutates is True
    query = parse_officecli_args(["query", "/mnt/user-data/workspace/report.docx", "/slide[1]/shape[2]"])
    assert command_path_values(query, "/mnt/user-data/workspace") == [
        (1, "/mnt/user-data/workspace/report.docx")
    ]

    validate = parse_officecli_args(["validate", "/mnt/user-data/uploads/report.docx"])
    assert validate.mutates is False
    assert command_path_values(validate, "/mnt/user-data/workspace") == [
        (1, "/mnt/user-data/uploads/report.docx")
    ]


def test_officecli_schema_reports_missing_file_before_path_validation():
    with pytest.raises(ValueError, match="缺少文件路径.*文档选择器"):
        parse_officecli_args(["get", "/body/p[1]", "/body/p[2]"])


@pytest.mark.asyncio
async def test_local_run_officecli_executes_binary_with_translated_paths(monkeypatch, tmp_path):
    log_path = tmp_path / "args.log"
    fake_bin = _write_fake_officecli(tmp_path, log_path)
    monkeypatch.setattr(cw_runtime_module.shutil, "which", lambda *a, **k: fake_bin)
    runtime = _local_runtime(tmp_path)
    (Path(runtime.workspace_root) / "workspace" / "report.docx").write_bytes(b"")

    result = await runtime.run_officecli(
        args=["get", "/mnt/user-data/workspace/report.docx", "/body"],
        cwd="/mnt/user-data/workspace",
        timeout_seconds=30,
    )

    assert result["backend"] == "local"
    assert result["exit_code"] == 0
    assert "OFFICECLI_CALLED" in result["stdout"]
    # virtual path translated to physical; selector /body passed unchanged
    log_text = log_path.read_text()
    assert "/body" in log_text
    physical = str(Path(runtime.workspace_root) / "workspace" / "report.docx")
    assert physical in log_text
    assert "/mnt/user-data/workspace/report.docx" not in log_text


@pytest.mark.asyncio
async def test_local_run_officecli_keeps_arg_boundaries_when_root_has_spaces(monkeypatch, tmp_path):
    """Regression: physical roots containing spaces must not split a path into two argv items."""
    log_path = tmp_path / "args.log"
    fake_bin = _write_fake_officecli(tmp_path, log_path)
    monkeypatch.setattr(cw_runtime_module.shutil, "which", lambda *a, **k: fake_bin)

    spaced_root = tmp_path / "chat data" / "user-data"
    (spaced_root / "workspace").mkdir(parents=True)
    (spaced_root / "workspace" / "my report.docx").write_bytes(b"")
    runtime = ConversationWorkspaceRuntime(
        workspace_root=str(spaced_root), user_id="u-officecli", backend="local"
    )

    result = await runtime.run_officecli(
        args=["get", "/mnt/user-data/workspace/my report.docx", "/body"],
        cwd="/mnt/user-data/workspace",
        timeout_seconds=30,
    )

    assert result["exit_code"] == 0
    physical = str(spaced_root / "workspace" / "my report.docx")
    arg_lines = [line for line in log_path.read_text().splitlines() if line.startswith("ARG:")]
    assert arg_lines == ["ARG:get", f"ARG:{physical}", "ARG:/body"]


@pytest.mark.asyncio
async def test_local_run_officecli_does_not_leak_server_environment(monkeypatch, tmp_path):
    """The child must not inherit server secrets, and HOME must stay off the visible workspace."""
    log_path = tmp_path / "args.log"
    fake_bin = _write_fake_officecli(tmp_path, log_path)
    monkeypatch.setattr(cw_runtime_module.shutil, "which", lambda *a, **k: fake_bin)
    monkeypatch.setenv("FXCJG_DB_PASSWORD", "super-secret")
    runtime = _local_runtime(tmp_path)

    await runtime.run_officecli(args=["--version"], cwd="/mnt/user-data/workspace", timeout_seconds=30)

    log_text = log_path.read_text()
    assert "ENV_SECRET:<unset>" in log_text
    assert "super-secret" not in log_text
    assert f"ENV_HOME:{runtime.workspace_root}" in log_text


@pytest.mark.asyncio
async def test_local_run_officecli_times_out(monkeypatch, tmp_path):
    slow_bin = tmp_path / "officecli"
    slow_bin.write_text("#!/bin/sh\nsleep 10\n")
    slow_bin.chmod(slow_bin.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setattr(cw_runtime_module.shutil, "which", lambda *a, **k: str(slow_bin))
    runtime = _local_runtime(tmp_path)

    result = await runtime.run_officecli(
        args=["--version"], cwd="/mnt/user-data/workspace", timeout_seconds=1
    )
    assert result["timed_out"] is True
    assert result["exit_code"] == 124


@pytest.mark.asyncio
async def test_local_run_officecli_rejects_relative_parent_escape(monkeypatch, tmp_path):
    """`..` never reaches the child, otherwise cwd-relative args could leave the workspace."""
    log_path = tmp_path / "args.log"
    fake_bin = _write_fake_officecli(tmp_path, log_path)
    monkeypatch.setattr(cw_runtime_module.shutil, "which", lambda *a, **k: fake_bin)
    runtime = _local_runtime(tmp_path)

    with pytest.raises(ValueError, match="officecli_run"):
        await runtime.run_officecli(
            args=["get", "../../../../etc/passwd", "/body"],
            cwd="/mnt/user-data/workspace",
            timeout_seconds=30,
        )
    assert not log_path.exists()


@pytest.mark.asyncio
async def test_local_run_officecli_rejects_path_escape_and_skills(monkeypatch, tmp_path):
    log_path = tmp_path / "args.log"
    fake_bin = _write_fake_officecli(tmp_path, log_path)
    monkeypatch.setattr(cw_runtime_module.shutil, "which", lambda *a, **k: fake_bin)
    runtime = _local_runtime(tmp_path)

    with pytest.raises(ValueError):
        await runtime.run_officecli(
            args=["get", "/etc/passwd", "/body"], cwd="/mnt/user-data/workspace", timeout_seconds=30
        )
    with pytest.raises(ValueError):
        await runtime.run_officecli(
            args=["get", "/mnt/skills/officecli/SKILL.md", "/body"],
            cwd="/mnt/user-data/workspace",
            timeout_seconds=30,
        )


@pytest.mark.asyncio
async def test_local_run_officecli_selector_not_treated_as_path(monkeypatch, tmp_path):
    log_path = tmp_path / "args.log"
    fake_bin = _write_fake_officecli(tmp_path, log_path)
    monkeypatch.setattr(cw_runtime_module.shutil, "which", lambda *a, **k: fake_bin)
    runtime = _local_runtime(tmp_path)

    # /slide[1] is a DOM selector, not a host path -> must not raise.
    result = await runtime.run_officecli(
        args=["query", "/mnt/user-data/workspace/deck.pptx", "/slide[1]/shape[2]"],
        cwd="/mnt/user-data/workspace",
        timeout_seconds=30,
    )
    assert result["exit_code"] == 0
    assert "/slide[1]/shape[2]" in log_path.read_text()


@pytest.mark.asyncio
async def test_sandbox_run_officecli_passes_command_and_validates_bounds(monkeypatch, tmp_path):
    captured = {}

    class FakeSandbox:
        id = "sbx-officecli"

    class FakeSession:
        sandbox_id = "sbx-officecli"
        sandbox = FakeSandbox()
        operation_lock = __import__("asyncio").Lock()

    async def fake_get_session(*_args, **_kwargs):
        return FakeSession()

    async def fake_run(*, session, command, timeout_seconds):
        captured["command"] = command
        return 0, "sandbox-out", ""

    async def fake_sync(*_args, **_kwargs):
        return None

    monkeypatch.setattr(sandbox_backend_module, "get_workspace_execution_session", fake_get_session)
    monkeypatch.setattr(sandbox_backend_module, "run_command_in_sandbox", fake_run)
    monkeypatch.setattr(sandbox_backend_module, "sync_sandbox_working_trees", fake_sync)

    backend = SandboxWorkspaceBackend()
    runtime = _local_runtime(tmp_path)

    result = await backend.run_officecli(
        runtime,
        args=["view", "/mnt/user-data/workspace/report.docx", "outline"],
        cwd="/mnt/user-data/workspace",
        timeout_seconds=30,
    )
    assert result["backend"] == "opensandbox"
    assert captured["command"].startswith("cd /mnt/user-data/workspace && officecli ")
    assert "/mnt/user-data/workspace/report.docx" in captured["command"]

    # out-of-bounds absolute path must be rejected before reaching the sandbox
    with pytest.raises(ValueError):
        await backend.run_officecli(
            runtime,
            args=["get", "/etc/passwd", "/body"],
            cwd="/mnt/user-data/workspace",
            timeout_seconds=30,
        )
    # skill paths are rejected on both backends alike
    with pytest.raises(ValueError, match="技能路径"):
        await backend.run_officecli(
            runtime,
            args=["get", "/mnt/skills/officecli/SKILL.md", "/body"],
            cwd="/mnt/user-data/workspace",
            timeout_seconds=30,
        )


@pytest.mark.asyncio
async def test_tool_wrapper_accepts_intent_metadata(monkeypatch, tmp_path):
    """intent 为展示元数据：工具可正常调用，且不会透传到 officecli argv。"""
    log_path = tmp_path / "args.log"
    fake_bin = _write_fake_officecli(tmp_path, log_path)
    monkeypatch.setattr(cw_runtime_module.shutil, "which", lambda *a, **k: fake_bin)
    runtime = _local_runtime(tmp_path)

    tool = create_officecli_run_tool(runtime)
    ok = await tool.ainvoke({
        "command_args": ["--version"],
        "intent": "确认工作区 OfficeCLI 可用",
    })
    assert "[officecli_run]" in ok
    assert "OFFICECLI_CALLED" in ok

    argv_text = log_path.read_text()
    assert "确认工作区 OfficeCLI 可用" not in argv_text


@pytest.mark.asyncio
async def test_tool_wrapper_surfaces_denied_subcommand_and_success(monkeypatch, tmp_path):
    log_path = tmp_path / "args.log"
    fake_bin = _write_fake_officecli(tmp_path, log_path)
    monkeypatch.setattr(cw_runtime_module.shutil, "which", lambda *a, **k: fake_bin)
    runtime = _local_runtime(tmp_path)

    tool = create_officecli_run_tool(runtime)
    # 拒绝的子命令现在以 ValueError 抛出，不再返回错误文本
    with pytest.raises(ValueError, match="禁止子命令"):
        await tool.ainvoke({"command_args": ["watch", "x.docx"]})

    ok = await tool.ainvoke({"command_args": ["--version"]})
    assert "[officecli_run]" in ok
    assert "OFFICECLI_CALLED" in ok


@pytest.mark.asyncio
async def test_run_officecli_command_success_shape(monkeypatch, tmp_path):
    log_path = tmp_path / "args.log"
    fake_bin = _write_fake_officecli(tmp_path, log_path)
    monkeypatch.setattr(cw_runtime_module.shutil, "which", lambda *a, **k: fake_bin)
    runtime = _local_runtime(tmp_path)

    payload = await run_officecli_command(
        args=["--version"], cwd="/mnt/user-data/workspace", timeout_seconds=30, workspace_runtime=runtime
    )
    assert payload["success"] is True
    assert payload["backend"] == "local"
    assert "next_required_action" in payload


@pytest.mark.asyncio
async def test_uploads_is_read_only_for_mutating_subcommands(tmp_path):
    """修改类子命令禁止写入/修改 uploads；报错需提示先 file_copy 到 workspace。"""
    runtime = _local_runtime(tmp_path)
    with pytest.raises(ValueError, match="uploads"):
        await run_officecli_command(
            args=["set", "/mnt/user-data/uploads/original.docx", "/body/p[1]", "--prop", "bold=true"],
            cwd="/mnt/user-data/workspace",
            timeout_seconds=30,
            workspace_runtime=runtime,
        )
    with pytest.raises(ValueError, match="file_copy"):
        await run_officecli_command(
            args=["save", "/mnt/user-data/uploads/original.docx"],
            cwd="/mnt/user-data/workspace",
            timeout_seconds=30,
            workspace_runtime=runtime,
        )


@pytest.mark.asyncio
async def test_uploads_cwd_rejects_mutating_subcommands(tmp_path):
    """cwd 落在 uploads 时，相对写入（如 create）同样被拦。"""
    runtime = _local_runtime(tmp_path)
    with pytest.raises(ValueError, match="uploads"):
        await run_officecli_command(
            args=["create", "new.docx"],
            cwd="/mnt/user-data/uploads",
            timeout_seconds=30,
            workspace_runtime=runtime,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("subcommand_args", [("view", "outline"), ("validate",)])
async def test_uploads_reads_allowed_for_readonly_subcommands(monkeypatch, tmp_path, subcommand_args):
    """view/validate 可读取 uploads，调用应正常到达后端。"""
    log_path = tmp_path / "args.log"
    fake_bin = _write_fake_officecli(tmp_path, log_path)
    monkeypatch.setattr(cw_runtime_module.shutil, "which", lambda *a, **k: fake_bin)
    runtime = _local_runtime(tmp_path)
    (Path(runtime.workspace_root) / "uploads" / "original.docx").write_bytes(b"")

    result = await run_officecli_command(
        args=[subcommand_args[0], "/mnt/user-data/uploads/original.docx", *subcommand_args[1:]],
        cwd="/mnt/user-data/workspace",
        timeout_seconds=30,
        workspace_runtime=runtime,
    )
    assert result["success"] is True


@pytest.mark.asyncio
async def test_dump_from_uploads_to_workspace_is_allowed(monkeypatch, tmp_path):
    """dump 源读 uploads、输出写 workspace 属正常流程，不应拦截。"""
    log_path = tmp_path / "args.log"
    fake_bin = _write_fake_officecli(tmp_path, log_path)
    monkeypatch.setattr(cw_runtime_module.shutil, "which", lambda *a, **k: fake_bin)
    runtime = _local_runtime(tmp_path)
    (Path(runtime.workspace_root) / "uploads" / "src.docx").write_bytes(b"")

    result = await run_officecli_command(
        args=["dump", "/mnt/user-data/uploads/src.docx", "--out=/mnt/user-data/workspace/out.json"],
        cwd="/mnt/user-data/workspace",
        timeout_seconds=30,
        workspace_runtime=runtime,
    )
    assert result["success"] is True


@pytest.mark.asyncio
async def test_dump_out_into_uploads_is_rejected(tmp_path):
    runtime = _local_runtime(tmp_path)
    with pytest.raises(ValueError, match="file_copy"):
        await run_officecli_command(
            args=["dump", "/mnt/user-data/workspace/src.docx", "--out=/mnt/user-data/uploads/out.json"],
            cwd="/mnt/user-data/workspace",
            timeout_seconds=30,
            workspace_runtime=runtime,
        )


@pytest.mark.asyncio
async def test_tool_wrapper_surfaces_uploads_violation(tmp_path):
    """uploads 违规以 ValueError 抛出，报错含 file_copy 指引。"""
    runtime = _local_runtime(tmp_path)
    tool = create_officecli_run_tool(runtime)
    with pytest.raises(ValueError, match="uploads") as excinfo:
        await tool.ainvoke({"command_args": ["add", "/mnt/user-data/uploads/x.docx", "/body", "--type", "p"]})
    assert "file_copy" in str(excinfo.value)


@pytest.mark.asyncio
async def test_save_stdout_to_writes_untruncated_stdout_to_workspace(monkeypatch, tmp_path):
    """save_stdout_to 把完整 stdout 原样写入 workspace，返回保存路径。"""
    log_path = tmp_path / "args.log"
    fake_bin = _write_fake_officecli(tmp_path, log_path)
    monkeypatch.setattr(cw_runtime_module.shutil, "which", lambda *a, **k: fake_bin)
    runtime = _local_runtime(tmp_path)

    payload = await run_officecli_command(
        args=["--version"], cwd="/mnt/user-data/workspace", timeout_seconds=30,
        workspace_runtime=runtime, save_stdout_to="/mnt/user-data/workspace/diff_demo.txt",
    )
    assert payload["success"] is True
    assert payload["saved_stdout_to"]
    physical = Path(runtime.workspace_root) / "workspace" / "diff_demo.txt"
    assert physical.exists()
    assert "OFFICECLI_CALLED" in physical.read_text()


@pytest.mark.asyncio
async def test_save_stdout_to_rejects_relative_path(tmp_path):
    """必须传 /mnt/user-data/workspace/ 开头的完整路径，相对路径不自动拼接。"""
    runtime = _local_runtime(tmp_path)
    with pytest.raises(ValueError, match="路径必须以"):
        await run_officecli_command(
            args=["--version"], cwd="/mnt/user-data/workspace", timeout_seconds=30,
            workspace_runtime=runtime, save_stdout_to="diff_demo.txt",
        )


@pytest.mark.asyncio
async def test_save_stdout_to_accepts_workspace_absolute_path(monkeypatch, tmp_path):
    log_path = tmp_path / "args.log"
    fake_bin = _write_fake_officecli(tmp_path, log_path)
    monkeypatch.setattr(cw_runtime_module.shutil, "which", lambda *a, **k: fake_bin)
    runtime = _local_runtime(tmp_path)

    payload = await run_officecli_command(
        args=["--version"], cwd="/mnt/user-data/workspace", timeout_seconds=30,
        workspace_runtime=runtime, save_stdout_to="/mnt/user-data/workspace/diff_abs.txt",
    )
    assert payload["success"] is True
    assert (Path(runtime.workspace_root) / "workspace" / "diff_abs.txt").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_path,expected", [
    ("/mnt/user-data/outputs/x.txt", "只允许写入"),
    ("/mnt/user-data/uploads/x.txt", "只允许写入"),
    ("/mnt/skills/officecli/x.txt", "只允许写入"),
    ("/etc/x.txt", "路径必须以"),
    ("diff_demo.txt", "路径必须以"),
    ("workspace/../outputs/x.txt", "路径必须以"),
    ("/mnt/user-data/workspace/../outputs/x.txt", "越界"),
    ("/mnt/user-data/workspace/semantic/model.yaml", "只读快照"),
])
async def test_save_stdout_to_rejects_non_workspace_paths(tmp_path, bad_path, expected):
    """路径校验复用 WORKSPACE_PATH_POLICY：相对形式/越界/semantic 快照等口径与全局一致。"""
    runtime = _local_runtime(tmp_path)
    with pytest.raises(ValueError, match=expected):
        await run_officecli_command(
            args=["--version"], cwd="/mnt/user-data/workspace", timeout_seconds=30,
            workspace_runtime=runtime, save_stdout_to=bad_path,
        )


@pytest.mark.asyncio
async def test_save_stdout_to_skipped_when_command_fails(monkeypatch, tmp_path):
    """命令失败时 stdout 不可信：跳过转存，走 success=False 主路径（stderr 完整返回）。"""
    fail_bin = tmp_path / "officecli"
    fail_bin.write_text("#!/bin/sh\necho boom >&2\nexit 3\n")
    fail_bin.chmod(fail_bin.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setattr(cw_runtime_module.shutil, "which", lambda *a, **k: str(fail_bin))
    runtime = _local_runtime(tmp_path)

    payload = await run_officecli_command(
        args=["--version"], cwd="/mnt/user-data/workspace", timeout_seconds=30,
        workspace_runtime=runtime, save_stdout_to="/mnt/user-data/workspace/diff_fail.txt",
    )
    assert payload["success"] is False
    assert payload["saved_stdout_to"] == ""
    assert "boom" in payload["stderr"]
    assert not (Path(runtime.workspace_root) / "workspace" / "diff_fail.txt").exists()


@pytest.mark.asyncio
async def test_save_stdout_to_write_failure_keeps_command_result(monkeypatch, tmp_path):
    """转存写入失败不改变命令成功结论：stdout/stderr 照常返回，payload 回显失败原因。"""
    log_path = tmp_path / "args.log"
    fake_bin = _write_fake_officecli(tmp_path, log_path)
    monkeypatch.setattr(cw_runtime_module.shutil, "which", lambda *a, **k: fake_bin)
    runtime = _local_runtime(tmp_path)

    async def _broken_write(**kwargs):
        raise IsADirectoryError("目标是已存在的目录，无法覆盖为文件")

    monkeypatch.setattr(runtime, "write_text_file", _broken_write)
    payload = await run_officecli_command(
        args=["--version"], cwd="/mnt/user-data/workspace", timeout_seconds=30,
        workspace_runtime=runtime, save_stdout_to="/mnt/user-data/workspace/diff_dir",
    )
    assert payload["success"] is True
    assert payload["saved_stdout_to"] == ""
    assert "转存失败" in payload["saved_stdout_error"]
    assert "OFFICECLI_CALLED" in payload["stdout"]


@pytest.mark.asyncio
async def test_save_stdout_to_writes_untruncated_stdout_over_limit(monkeypatch, tmp_path):
    """stdout 超过返回截断上限时，转存文件仍是完整原文（转存的核心价值）。"""
    big_marker = "L" * 20000  # > MAX_OUTPUT_CHARS(12000)，payload stdout 会被截断
    big_bin = tmp_path / "officecli"
    big_bin.write_text(f"#!/bin/sh\nprintf '%s' '{big_marker}'\n")
    big_bin.chmod(big_bin.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setattr(cw_runtime_module.shutil, "which", lambda *a, **k: str(big_bin))
    runtime = _local_runtime(tmp_path)

    payload = await run_officecli_command(
        args=["--version"], cwd="/mnt/user-data/workspace", timeout_seconds=30,
        workspace_runtime=runtime, save_stdout_to="/mnt/user-data/workspace/diff_big.txt",
    )
    assert payload["success"] is True
    assert payload["stdout_truncated"] is True
    assert len(payload["stdout"]) < 20000
    saved_text = (Path(runtime.workspace_root) / "workspace" / "diff_big.txt").read_text()
    assert saved_text == big_marker
