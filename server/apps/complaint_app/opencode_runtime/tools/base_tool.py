from __future__ import annotations

"""Tool system skeleton.

- Tool: tool definition (name/description/executor).
- ToolContext: execution context (session/message/agent metadata) for audit/auth/logging.
- ToolExecutor: execute a tool with permission checks and a unified call path.

  For example:

  def my_tool(payload, context):
      return {"ok": True}

  executor = ToolExecutor()
  executor.register(Tool(name="my_tool", _executor=my_tool))

  ctx = ToolContext(session_id="s1", message_id="m1", agent_name="build")
  result = executor.execute("my_tool", context=ctx, payload={"x": 1})
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Optional

from ..permission import PermissionDecision, PermissionEngine


ToolHandler = Callable[..., Any]


@dataclass
class Tool:
    """工具定义（名称/描述/执行器）。"""
    name: str
    description: str = ""
    _executor: ToolHandler | None = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def execute(self, *args, **kwargs) -> Any:
        """执行工具的核心逻辑（调用内部 _executor）。"""
        if not self._executor:
            raise RuntimeError(f"Tool executor not set: {self.name}")
        return self._executor(*args, **kwargs)


@dataclass
class ToolContext:
    """工具执行上下文。"""
    session_id: str
    message_id: str
    agent_name: str | None = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class ToolExecutor:
    """工具执行器：含注册表与权限检查。"""
    def __init__(self, permission_engine: PermissionEngine | None = None) -> None:
        self._tools: Dict[str, Tool] = {}
        self._permission_engine = permission_engine or PermissionEngine()

    def register(self, tool: Tool) -> None:
        """注册一个可执行工具。"""
        if not tool or not tool.name:
            raise ValueError("Tool name is required")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        """按名称获取工具。"""
        return self._tools.get(name)

    def list(self) -> Iterable[Tool]:
        """返回所有已注册工具。"""
        return list(self._tools.values())

    def remove(self, name: str) -> None:
        """移除已注册工具。"""
        if name in self._tools:
            del self._tools[name]

    def execute(self, name: str, *, context: ToolContext, payload: Dict[str, Any] | None = None) -> Any:
        """执行指定工具，包含权限检查。"""
        tool = self.get(name)
        if not tool:
            raise KeyError(f"Tool not found: {name}")
        decision = self._permission_engine.check(name, context)
        if decision == PermissionDecision.DENY:
            raise PermissionError(f"Tool access denied: {name}")
        if decision == PermissionDecision.ASK:
            raise PermissionError(f"Tool access requires approval: {name}")
        return tool.execute(payload=payload or {}, context=context)
