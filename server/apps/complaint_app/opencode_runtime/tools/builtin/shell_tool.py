
from __future__ import annotations

import os
from typing import Any, Dict, Optional
from utils.logger import get_logger

logger = get_logger(__name__)

_shell_import_error: Exception | None = None
try:
    from langchain_community.tools.shell.tool import ShellTool
except Exception as exc:
    logger.warning("[shell_tool] import shell.tool ShellTool failed err=%s", exc)
    _shell_import_error = exc
    try:
        from langchain_community.tools import ShellTool
    except Exception as inner_exc:
        logger.warning("[shell_tool] import tools ShellTool failed err=%s", inner_exc)
        _shell_import_error = inner_exc
        ShellTool = None

from ..base_tool import Tool, ToolContext


def _shell_output_max_chars() -> int:
    """读取 shell 工具输出最大字符数。"""
    raw = os.getenv("OPENCODE_SHELL_OUTPUT_MAX_CHARS", "12000").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 12000
    return max(value, 1000)


def _truncate_shell_output(output: Any) -> tuple[str, bool, int]:
    """截断 shell 输出，避免工具结果撑大模型上下文。"""
    text = str(output or "")
    original_len = len(text)
    max_chars = _shell_output_max_chars()
    if original_len <= max_chars:
        return text, False, original_len
    omitted = original_len - max_chars
    return f"{text[:max_chars]}\n\n[output truncated: omitted {omitted} chars]", True, original_len


def registry() -> Tool:
    """返回内置 shell 工具实例。"""
    shell: Optional[object] = ShellTool() if ShellTool else None

    def _handler(payload: Dict[str, Any], context: ToolContext):
        if shell is None:
            if _shell_import_error:
                logger.error("[shell_tool] shell import failed err=%s", _shell_import_error)
            return {
                "title": "shell",
                "output": "Error: ShellTool is unavailable. Ensure langchain-community is installed and provides ShellTool.",
                "metadata": {"ok": False, "error": str(_shell_import_error or "ShellTool unavailable")},
            }
        command = str((payload or {}).get("command") or "").strip()
        if not command:
            return {
                "title": "shell",
                "output": "Error: command is required.",
                "metadata": {"ok": False, "error": "command is required"},
            }
        allowed_tools = ["rg", "sed", "ls", "find", "grep"]
        tool = command.split()[0]
        if tool not in allowed_tools:
            return {
                "title": "shell",
                "output": "Error: Command not allowed.",
                "metadata": {"command": command, "allowed": False},
            }
        try:
            if hasattr(shell, "run"):
                output = shell.run(command)
            else:
                output = shell.invoke(command)
        except Exception as exc:
            logger.exception("[shell_tool] command_failed command=%s err=%s", command, exc)
            return {
                "title": "shell",
                "output": f"Error: shell command failed: {exc}",
                "metadata": {"command": command, "ok": False, "error": str(exc)},
            }
        output_text, truncated, original_len = _truncate_shell_output(output)
        return {
            "title": "shell",
            "output": output_text,
            "metadata": {"command": command, "ok": True, "truncated": truncated, "original_len": original_len},
        }

    return Tool(
        name="shell",
        description="Execute a shell command and return its output. READ ONLY operations allowed.",
        _executor=_handler,
        metadata={
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute",
                    }
                },
                "required": ["command"],
            }
        },
    )
