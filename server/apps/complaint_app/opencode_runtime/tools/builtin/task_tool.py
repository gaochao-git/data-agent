from __future__ import annotations

import os
import re
import uuid
from typing import Any, Dict, List

from apps.complaint_app.core.model_config import get_chat_model
from utils.logger import get_logger

from ...agents.agents import AgentRegistry, check_permission, subagent_lines
from ...permission import PermissionDecision
from ...session import Session
from ...session_prompt import SessionPrompt
from ..base_tool import Tool, ToolContext

logger = get_logger(__name__)

_SOURCE_TEXT_PATTERN = re.compile(r"<source_text>.*?</source_text>", re.DOTALL | re.IGNORECASE)


def _trace_enabled() -> bool:
    return os.getenv("OPENCODE_RUNTIME_TRACE", "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _redact_source_text(text: str) -> str:
    if not text:
        return text
    if "<source_text>" not in text:
        return text
    return _SOURCE_TEXT_PATTERN.sub("<source_text>[omitted]</source_text>", text)


def _build_description() -> str:
    """构建 task 工具描述，包含子 Agent 列表。"""
    lines: List[str] = [
        "Call a specialized subagent to handle a task.",
        "",
        "Available subagents:",
    ]
    lines.extend(subagent_lines() or ["- (none)"])
    return "\n".join(lines).strip()


def registry() -> Tool:
    """返回内置 task 工具实例。"""
    description = _build_description()

    async def _run_subagent(agent: "AgentInfo", *, description_text: str, prompt: str, session_id: str, ) -> Dict[str, Any]:
        """执行子 Agent，返回模型输出。"""
        session = Session(session_id=session_id)
        runner = SessionPrompt()
        model_name = agent.model.model_id if agent.model and agent.model.model_id else None
        if not model_name:
            model_name = get_chat_model()
        if _trace_enabled():
            logger.info("[task_tool] subagent_prompt name=%s model=%s system_prompt=%s user_prompt=%s", agent.name, model_name, agent.prompt or "", _redact_source_text(prompt))
        result = await runner.prompt(
            session=session,
            user_input=prompt,
            system_prompt=agent.prompt,
            model=model_name,
            agent_name=agent.name,
            record_messages=True,
        )
        output_text = ""
        if result.message and result.message.parts:
            output_text = result.message.parts[-1].content
        if _trace_enabled():
            logger.info("[task_tool] subagent_output name=%s session_id=%s output=%s", agent.name, session_id, output_text or "")
        return {
            "title": description_text or agent.name,
            "output": output_text,
            "metadata": {
                "session_id": session_id,
                "subagent": agent.name,
            },
        }

    def _handler(payload: Dict[str, object], context: ToolContext):
        """执行 task 工具：选择子 Agent 并生成任务结果。"""
        data = payload or {}
        description_text = str(data.get("description") or "").strip()
        prompt = str(data.get("prompt") or "").strip()
        source_text = str(data.get("source_text") or "").strip()
        subagent_type = str(data.get("subagent_type") or "").strip()
        session_id = str(data.get("session_id") or "").strip() or uuid.uuid4().hex

        if not description_text:
            raise ValueError("description is required")
        if not prompt:
            raise ValueError("prompt is required")
        if not subagent_type:
            raise ValueError("subagent_type is required")

        if source_text:
            prompt = "\n\n".join(
                [
                    prompt,
                    "<source_text>",
                    source_text,
                    "</source_text>",
                ]
            ).strip()

        caller = AgentRegistry.get(context.agent_name) if context.agent_name else None
        decision = check_permission(caller, "task")
        if decision == PermissionDecision.DENY:
            raise PermissionError("task tool access denied")
        if decision == PermissionDecision.ASK:
            raise PermissionError("task tool access requires approval")

        agent = AgentRegistry.get(subagent_type)
        if not agent:
            available = ", ".join([item.name for item in AgentRegistry.subagents()]) or "none"
            raise ValueError(f'Subagent "{subagent_type}" not found. Available: {available}')
        if agent.mode == "primary":
            raise ValueError(f'Subagent "{subagent_type}" is not a subagent')

        if subagent_type:
            return _run_subagent(
                agent,
                description_text=description_text,
                prompt=prompt,
                session_id=session_id,
            )

        output_lines: List[str] = [
            f"## Task: {description_text}",
            f"**Subagent**: {agent.name}",
            "",
            prompt,
            "",
            "<task_metadata>",
            f"session_id: {session_id}",
            "</task_metadata>",
        ]

        return {
            "title": description_text,
            "output": "\n".join(output_lines).strip(),
            "metadata": {
                "session_id": session_id,
                "subagent": agent.name,
            },
        }

    return Tool(
        name="task",
        description=description,
        _executor=_handler,
        metadata={
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "A short (3-5 words) description of the task",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "The task for the agent to perform",
                    },
                    "source_text": {
                        "type": "string",
                        "description": "Source text used by the subagent (optional)",
                    },
                    "subagent_type": {
                        "type": "string",
                        "description": "The type of specialized agent to use for this task",
                    },
                    "session_id": {
                        "type": "string",
                        "description": "Existing task session to continue",
                    },
                    "command": {
                        "type": "string",
                        "description": "The command that triggered this task",
                    },
                },
                "required": ["description", "prompt", "subagent_type"],
            }
        },
    )
