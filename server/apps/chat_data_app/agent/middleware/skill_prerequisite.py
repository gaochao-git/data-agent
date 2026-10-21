"""工具调用前的技能前置门禁：required_skills 未加载则拒绝执行。"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from ..tools.skill import SKILL_TOOL_NAME

_LOADED_SKILL_PATTERN = re.compile(r'<skill_content\s+name="([^"]+)"')


def _state_messages(state: Any) -> list[Any]:
    if isinstance(state, dict):
        messages = state.get("messages")
        return list(messages) if isinstance(messages, list) else []
    messages = getattr(state, "messages", None)
    return list(messages) if isinstance(messages, list) else []


def loaded_skill_names(state: Any) -> set[str]:
    """从工具历史恢复已加载技能；技能内容被压缩掉后门禁会重新关闭。"""
    names: set[str] = set()
    for message in _state_messages(state):
        if not isinstance(message, ToolMessage):
            continue
        if str(message.name or "") != SKILL_TOOL_NAME or message.status == "error":
            continue
        content = message.content if isinstance(message.content, str) else str(message.content)
        names.update(_LOADED_SKILL_PATTERN.findall(content))
    return names


class SkillPrerequisiteMiddleware(AgentMiddleware):
    """按工具映射检查 required_skills；缺技能时返回可自愈错误，不执行工具。"""

    def __init__(self, required_skills: dict[str, tuple[str, ...]]) -> None:
        self.required_skills = {
            str(tool): tuple(str(skill).strip() for skill in skills if str(skill).strip())
            for tool, skills in required_skills.items()
            if skills
        }

    def _missing_skills(self, request: Any) -> tuple[str, list[str]] | None:
        tool_call = getattr(request, "tool_call", None)
        if not isinstance(tool_call, dict):
            return None
        tool_name = str(tool_call.get("name") or "")
        required = self.required_skills.get(tool_name)
        if not required:
            return None
        loaded = loaded_skill_names(getattr(request, "state", None))
        missing = [skill for skill in required if skill not in loaded]
        return (tool_name, missing) if missing else None

    def _reject(self, request: Any, tool_name: str, missing: list[str]) -> ToolMessage:
        tool_call = getattr(request, "tool_call", {})
        skill_text = "、".join(f'load_skill("{skill}")' for skill in missing)
        return ToolMessage(
            content=f"调用 {tool_name} 前必须先加载技能：请先调用 {skill_text}。",
            name=tool_name,
            tool_call_id=str((tool_call or {}).get("id") or ""),
            status="error",
        )

    def wrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]]
    ) -> ToolMessage | Command[Any]:
        missing = self._missing_skills(request)
        if missing is None:
            return handler(request)
        return self._reject(request, *missing)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        missing = self._missing_skills(request)
        if missing is None:
            return await handler(request)
        return self._reject(request, *missing)


__all__ = ["SkillPrerequisiteMiddleware", "loaded_skill_names"]
