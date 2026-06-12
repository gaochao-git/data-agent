from __future__ import annotations

import os
import sys


def _ensure_pythonpath() -> None:
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    server_path = os.path.join(root, "server")
    if server_path not in sys.path:
        sys.path.insert(0, server_path)


def main() -> None:
    _ensure_pythonpath()
    from apps.complaint_app.opencode_runtime.tools.registry import ToolRegistry

    tools = ToolRegistry.registry()
    print("Tools count:", len(tools))
    for tool in tools:
        print("-", tool)
    # print("=====")
    # print(ToolRegistry._registry_cache)


if __name__ == "__main__":
    main()
