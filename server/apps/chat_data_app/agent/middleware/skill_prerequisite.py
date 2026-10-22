"""工具调用前的技能前置门禁：required_skills 未加载则拒绝执行。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from ..tools.skill import SKILL_TOOL_NAME


def _state_value(state: Any, field: str) -> Any:
    if isinstance(state, dict):
        return state.get(field)
    return getattr(state, field, None)


def loaded_skill_names(state: Any) -> set[str]:
    """读取已加载技能名（仅来自显式 state，不从工具输出文本反推）。"""
    raw = _state_value(state, "loaded_skills")
    if not isinstance(raw, (list, tuple, set)):
        return set()
    return {str(name).strip() for name in raw if str(name or "").strip()}


def _skill_name_from_request(request: Any) -> str:
    tool_call = getattr(request, "tool_call", None)
    if not isinstance(tool_call, dict):
        return ""
    args = tool_call.get("args")
    if not isinstance(args, dict):
        return ""
    return str(args.get("name") or "").strip()


def _with_loaded_skill(result: Any, skill_name: str) -> Any:
    """把 load_skill 的成功结果包装成同时写 loaded_skills 的 Command。"""
    if not skill_name:
        return result
    if isinstance(result, Command):
        update = dict(result.update or {})
        update["loaded_skills"] = [skill_name]
        return Command(update=update, goto=result.goto, resume=result.resume)
    if isinstance(result, ToolMessage) and result.status != "error":
        return Command(update={"messages": [result], "loaded_skills": [skill_name]})
    return result


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

    def _is_skill_load_request(self, request: Any) -> bool:
        tool_call = getattr(request, "tool_call", None)
        return isinstance(tool_call, dict) and str(tool_call.get("name") or "") == SKILL_TOOL_NAME

    def wrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]]
    ) -> ToolMessage | Command[Any]:
        missing = self._missing_skills(request)
        if missing is not None:
            return self._reject(request, *missing)
        result = handler(request)
        if self._is_skill_load_request(request):
            return _with_loaded_skill(result, _skill_name_from_request(request))
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        missing = self._missing_skills(request)
        if missing is not None:
            return self._reject(request, *missing)
        result = await handler(request)
        if self._is_skill_load_request(request):
            return _with_loaded_skill(result, _skill_name_from_request(request))
        return result


__all__ = ["SkillPrerequisiteMiddleware", "loaded_skill_names"]
