"""Deterministic workflow implementation for payment specialist agent."""

from __future__ import annotations

import asyncio
import logging
import uuid
import os
from typing import Any, AsyncIterator

from ..conversation_history import (
    load_history,
    save_history,
    update_conversation_title,
)
from ..message_utils import create_message
from ..trace_utils import write_trace
from .stream_protocol import StreamProtocol
from .stream_manager import StreamManager
from .title_node import (
    TitleNodeRunner,
)
from .planning_node import PlanningNodeRunner
from .legal_research_subagent_node import LegalResearchSubAgentNodeRunner
from .specialist_main_agent_node import SpecialistMainAgentNodeRunner

logger = logging.getLogger(__name__)


async def chat_with_workflow(
    query: str,
    conversation_id: str,
    model_name: str | None,
    user_id: str | None = None,
) -> AsyncIterator[str]:
    normalized_user_id = str(user_id or "").strip()
    messages = load_history(conversation_id)
    is_first_chat = len(messages) == 0
    if not messages:
        messages.append(
            create_message(
                "system",
                "workflow_entrypoint",
                visible_for_display=False,
                display_type="system_prompt",
            )
        )
    user_message_entry = create_message("user", query, display_type="user")
    messages.append(user_message_entry)
    message_id = f"m_{uuid.uuid4().hex}"
    response_id = f"r_{uuid.uuid4().hex}"
    protocol = StreamProtocol(
        conversation_id=conversation_id,
        response_id=response_id,
        message_id=message_id,
        model_name=model_name or os.getenv("OPENAI_SUGGEST_MODEL", "qwen3-30b-a3b-instruct-2507"),
        assistant_visible_parts=[],
        thinking_trace=[],
    )

    async def main_stream() -> AsyncIterator[str]:
        # planning_runner = PlanningNodeRunner(protocol, messages)
        # async for chunk in planning_runner.stream():
        #     yield chunk
        # legal_runner = LegalResearchSubAgentNodeRunner(protocol, query)
        # async for chunk in legal_runner.stream():
        #     yield chunk
        main_agent_runner = SpecialistMainAgentNodeRunner(protocol, messages=messages)
        async for chunk in main_agent_runner.stream():
            yield chunk

        final_text = protocol.get_final_text()
        final_thinking = protocol.get_final_thinking()
        final_references = list(protocol.references or [])
        write_trace(
            conversation_id,
            "assistant_answer",
            {
                "message_id": message_id,
                "content": final_text,
                "thinking": final_thinking,
                "references": final_references,
            },
        )
        
        messages.append(
            create_message(
                "assistant",
                final_text,
                message_id=message_id,
                display_type="assistant_answer",
                thinking=final_thinking,
                extra={"references": final_references} if final_references else None,
            )
        )
        save_history()

    manager = StreamManager()
    manager.add(main_stream(), group="main")
    manager.finish_group("main")
    if is_first_chat:
        title_runner = TitleNodeRunner(protocol, query)
        async def title_stream() -> AsyncIterator[str]:
            async for chunk in title_runner.stream():
                yield chunk
            update_conversation_title(normalized_user_id, conversation_id, title_runner.final_title)
        manager.add(title_stream(), group="title")
        manager.finish_group("title")
    manager.close()

    yield protocol.ensure_message_start()

    async for chunk in manager.run():
        yield chunk

    yield protocol.build_event("message_end", finish_reason="stop")
    yield protocol.build_event("done", include_message_id=False)


__all__ = [
    "chat_with_workflow",
]
