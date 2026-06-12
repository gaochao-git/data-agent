"""LangChain DeepAgent-style entrypoint wired to the existing chat pipeline."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import AsyncIterator

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI

from ..conversation_history import load_history, save_history
from ..message_utils import create_message
from .tools import get_legal

logger = logging.getLogger(__name__)


async def chat_with_deepagent(
    query: str,
    conversation_id: str,
    model_name: str | None,
    user_id: str | None = None,
) -> AsyncIterator[str]:
    messages = load_history(conversation_id)
    if not messages:
        messages.append(
            create_message(
                "system",
                "deepagent_entrypoint",
                visible_for_display=False,
                display_type="system_prompt",
            )
        )

    user_message_entry = create_message("user", query, display_type="user")
    messages.append(user_message_entry)

    message_id = f"m_{uuid.uuid4().hex}"
    seq = 0
    sum_content_parts: list[str] = []

    def build_event(event_name, *, include_message_id=True, **payload):
        nonlocal seq
        seq += 1
        event_payload = {
            "event": event_name,
            "seq": seq,
            "created": int(time.time() * 1000),
        }
        if include_message_id:
            event_payload["message_id"] = message_id
        if conversation_id:
            event_payload["conversation_id"] = conversation_id
        for k, v in list(payload.items()):
            if v is None:
                payload.pop(k)
        event_payload.update(payload)
        return f"data: {json.dumps(event_payload, ensure_ascii=False)}\n\n"

    llm = ChatOpenAI(
        model=model_name or os.getenv("OPENAI_ANALYSIS_MODEL"),
        temperature=0,
        streaming=True,
    )
    agent = create_agent(
        llm,
        tools=[get_legal],
        system_prompt="""
你是一个AI专家系统-意图识别助手，负责根据用户的问题判断是否需要进一步的法规检索。
如果需要检索，尝试将用户的问题转译成法律检索工具需要的关键信息，然后调用检索工具；
如果不需要检索，请直接输出最终回复。保持中文简洁表达。
    """,
    )

    yield build_event(
            "message_start",
            role="assistant",
            model=model_name or os.getenv("OPENAI_ANALYSIS_MODEL") or "gpt-4o-mini",
        )

    def extract_content_text(chunk) -> str:
        def content_from_obj(obj) -> str:
            if hasattr(obj, "content"):
                content_val = getattr(obj, "content") or ""
            elif isinstance(obj, dict):
                content_val = obj.get("content") or ""
            else:
                return ""
            if isinstance(content_val, list):
                parts: list[str] = []
                for part in content_val:
                    if isinstance(part, dict) and "text" in part:
                        parts.append(part.get("text") or "")
                    else:
                        parts.append(str(part))
                return "".join(parts)
            return str(content_val)
        if isinstance(chunk, tuple):
            if len(chunk) == 2 and isinstance(chunk[0], str):
                payload = chunk[1]
                if isinstance(payload, (list, tuple)):
                    for item in payload:
                        text = content_from_obj(item)
                        if text:
                            return text
                text = content_from_obj(payload)
                if text:
                    return text

            for item in chunk:
                text = content_from_obj(item)
                if text:
                    return text

        return content_from_obj(chunk)

    try:
        async for chunk in agent.astream(
            {"messages": [{"role": "user", "content": query}]},
            stream_mode=["messages", "custom"],
        ):
            logger.debug("deepagent stream chunk=%s", chunk)
            content_text = extract_content_text(chunk)
            if not content_text:
                continue

            sum_content_parts.append(content_text)
            yield build_event(
                "content_delta",
                index=0,
                delta=content_text,
                role="assistant",
            )
    except Exception:
        logger.exception("DeepAgent streaming failed conversation_id=%s", conversation_id)
        raise

    final_text = "".join(sum_content_parts).strip()
    messages.append(
        create_message(
            "assistant",
            final_text,
            message_id=message_id,
            display_type="assistant_answer",
            thinking=[],
        )
    )
    save_history()

    display_payload = {
        "visible": True,
        "type": "assistant_answer",
        "content": final_text,
        "thinking": [],
    }
    yield build_event(
        "message_end",
        finish_reason="stop",
        display=display_payload,
    )
    yield build_event("done", include_message_id=False)


__all__ = ["chat_with_deepagent"]
