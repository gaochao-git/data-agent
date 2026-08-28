"""bash_run 工具测试——覆盖命令校验分支、超时钳制与工具包装的成功/异常路径。"""

from __future__ import annotations

import json

import pytest

from apps.chat_data_app.agent.tools import bash_run as bash_run_module
from apps.chat_data_app.agent.tools.bash_run import (
    MAX_TIMEOUT_SECONDS,
    _validate_command,
    create_bash_run_tool,
    run_bash_command,
)


class _FakeRuntime:
    """只实现 bash_run 依赖的两个运行时方法，避免起真实子进程。"""

    def __init__(self, execution: dict):
        self._execution = execution
        self.calls: list[tuple] = []

    async def validate_bash_paths(self, *, command: str) -> None:
        self.calls.append(("validate", command))

    async def run_bash(self, *, command: str, cwd, timeout_seconds: int) -> dict:
        self.calls.append(("run", command, cwd, timeout_seconds))
        return dict(self._execution)


def _execution(**overrides) -> dict:
    payload = {
        "backend": "local",
        "sandbox_id": None,
        "cwd": "/mnt/user-data/workspace",
        "exit_code": 0,
        "timed_out": False,
        "stdout": "hi",
        "stderr": "",
    }
    payload.update(overrides)
    return payload


def test_validate_command_strips_whitespace():
    assert _validate_command("  echo hi  ") == "echo hi"


def test_validate_command_rejects_empty():
    with pytest.raises(ValueError, match="required"):
        _validate_command("")
    with pytest.raises(ValueError, match="required"):
        _validate_command(None)


def test_validate_command_rejects_null_byte():
    with pytest.raises(ValueError, match="空字节"):
        _validate_command("echo \x00 hi")


@pytest.mark.parametrize(
    "command",
    [
        "sudo ls",
        "systemctl start nginx",
        "service nginx start",
        "su root",
        "nohup run.sh",
        "setsid run.sh",
        "run.sh disown",
    ],
)
def test_validate_command_rejects_privileged_and_service_commands(command):
    with pytest.raises(ValueError):
        _validate_command(command)


def test_validate_command_rejects_background_ampersand():
    with pytest.raises(ValueError):
        _validate_command("python3 train.py &")


@pytest.mark.asyncio
async def test_run_bash_command_success_and_timeout_clamp():
    runtime = _FakeRuntime(_execution())
    result = await run_bash_command(
        command="echo hi",
        cwd="/mnt/user-data/workspace",
        timeout_seconds=0,
        workspace_runtime=runtime,
    )
    assert result["success"] is True
    assert result["timeout_seconds"] == 1
    assert result["next_required_action"].startswith("如果命令生成了 workspace 中间产物")

    runtime2 = _FakeRuntime(_execution())
    result2 = await run_bash_command(
        command="echo hi", timeout_seconds=9999, workspace_runtime=runtime2
    )
    assert result2["timeout_seconds"] == MAX_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_run_bash_command_failure_hints_retry():
    runtime = _FakeRuntime(_execution(exit_code=1, stderr="boom"))
    result = await run_bash_command(command="false", workspace_runtime=runtime)
    assert result["success"] is False
    assert result["next_required_action"] == "修复命令、脚本或输入文件后重新执行。"

    timed_out = _FakeRuntime(_execution(exit_code=0, timed_out=True))
    result2 = await run_bash_command(command="sleep 100", workspace_runtime=timed_out)
    assert result2["success"] is False


@pytest.mark.asyncio
async def test_bash_run_tool_success(monkeypatch):
    async def _fake_run(*, command, cwd, timeout_seconds, workspace_runtime):
        return {
            "success": True,
            "backend": "local",
            "sandbox_id": None,
            "cwd": "/mnt/user-data/workspace",
            "command": command,
            "exit_code": 0,
            "timed_out": False,
            "timeout_seconds": timeout_seconds,
            "stdout": "hi",
            "stderr": "",
            "stdout_truncated": False,
            "stderr_truncated": False,
            "next_required_action": "ok",
        }

    monkeypatch.setattr(bash_run_module, "run_bash_command", _fake_run)
    tool = create_bash_run_tool(_FakeRuntime(_execution()))
    result = await tool.ainvoke({"command": "echo hi"})
    assert result.startswith("[bash_run]\n")
    payload = json.loads(result.split("\n", 1)[1])
    assert payload["success"] is True
    assert payload["exit_code"] == 0


@pytest.mark.asyncio
async def test_bash_run_tool_failure_wrapped(monkeypatch):
    async def _fake_run(*, command, cwd, timeout_seconds, workspace_runtime):
        raise RuntimeError("boom")

    monkeypatch.setattr(bash_run_module, "run_bash_command", _fake_run)
    tool = create_bash_run_tool(_FakeRuntime(_execution()))
    result = await tool.ainvoke({"command": "echo hi"})
    assert result == "bash_run 执行失败: boom"
