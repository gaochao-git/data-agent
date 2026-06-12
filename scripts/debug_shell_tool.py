from __future__ import annotations

import argparse
import os
import sys


def _ensure_pythonpath() -> None:
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    server_path = os.path.join(root, "server")
    if server_path not in sys.path:
        sys.path.insert(0, server_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run opencode_runtime shell tool.")
    parser.add_argument("command", nargs="?", default="pwd", help="Shell command to execute")
    args = parser.parse_args()

    _ensure_pythonpath()
    from apps.complaint_app.opencode_runtime.tools.base_tool import ToolContext, ToolExecutor
    from apps.complaint_app.opencode_runtime.tools.registry import ToolRegistry

    shell_tool = next((tool for tool in ToolRegistry.registry() if tool.name == "shell"), None)
    if not shell_tool:
        available = ", ".join([tool.name for tool in ToolRegistry.registry()]) or "none"
        raise SystemExit(f'shell tool not found. Available: {available}')

    executor = ToolExecutor()
    executor.register(shell_tool)
    ctx = ToolContext(session_id="debug-session", message_id="debug-message", agent_name="debug")
    result = executor.execute("shell", context=ctx, payload={"command": args.command})
    if isinstance(result, dict):
        print(result.get("output", ""))
    else:
        print(result)


if __name__ == "__main__":
    main()
