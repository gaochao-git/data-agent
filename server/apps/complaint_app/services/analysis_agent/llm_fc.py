from __future__ import annotations

import json
import inspect
import os
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from apps.complaint_app.core.async_openai_client import get_async_openai_client
from apps.complaint_app.services.chats.context import load_latest_text, strip_tables_enabled
from apps.complaint_app.services.chats.state import is_cancelled
from apps.complaint_app.utils import _strip_markdown_tables
from utils.logger import get_logger

logger = get_logger(__name__)


def load_system_prompt(context_block: str | None = None) -> str:
    path = Path(__file__).with_name("fc_system_prompt.md")
    try:
        prompt = path.read_text(encoding="utf-8")
    except Exception:
        return ""
    if context_block is not None:
        return prompt.replace("context_block", context_block)
    return prompt


def _estimate_token_count(text: str) -> int:
    return len(text)


def _load_context_block(user_id: str | None, base_id: str | None) -> str:
    include_original = str(os.getenv("FC_CONTEXT_INCLUDE_ORIGINAL_TEXT", "true")).lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    if not include_original or not user_id or not base_id:
        return "（举报件原文未提供）"
    raw_text = load_latest_text(user_id, base_id)
    if not raw_text or not raw_text.strip():
        return "（举报件原文未提供）"
    if strip_tables_enabled():
        raw_text = _strip_markdown_tables(raw_text)
    max_tokens = os.getenv("FC_CONTEXT_MAX_TOKENS", "32000").strip()
    try:
        max_tokens_int = int(max_tokens)
    except Exception:
        max_tokens_int = 32000
    if max_tokens_int > 0:
        token_count = _estimate_token_count(raw_text)
        if token_count > max_tokens_int:
            logger.info(
                "skip original text in fc context: tokens=%s limit=%s base_id=%s",
                token_count,
                max_tokens_int,
                base_id,
            )
            return "（举报件原文过长，已省略）"
    return raw_text.strip()


async def function_calling_blocking(
    model: str,
    question: str,
    *,
    tools: List[Dict[str, Any]],
    function_mapper: Dict[str, Any],
    api_key: str,
    base_url: str,
    context_messages: Optional[List[Dict[str, Any]]] = None,
    user_id: Optional[str] = None,
    base_id: Optional[str] = None,
) -> None:
    client = get_async_openai_client()
    context_block = _load_context_block(user_id, base_id)
    messages: List[Dict[str, Any]] = [{"role": "system", "content": load_system_prompt(context_block)}]
    if context_messages:
        messages.extend([msg for msg in context_messages if msg.get("content")])
    messages.append({"role": "user", "content": question})
    completion = await client.chat.completions.create(model=model, messages=messages, tools=tools)
    assistant_message = completion.choices[0].message
    logger.info("blocking tool_call message: %s", assistant_message.model_dump_json())
    tool_call = assistant_message.tool_calls[0]
    function_name = tool_call.function.name
    arguments = json.loads(tool_call.function.arguments or "{}")
    function = function_mapper[function_name]
    function_output = function(arguments) if arguments else function()
    if inspect.iscoroutine(function_output):
        function_output = await function_output
    messages.append(assistant_message)
    messages.append({"role": "tool", "content": function_output, "tool_call_id": tool_call.id})
    final_completion = await client.chat.completions.create(model=model, messages=messages, tools=tools)
    logger.info("blocking final answer: %s", final_completion.choices[0].message.content)


async def function_calling_streaming(
    model: str,
    question: str,
    *,
    tools: List[Dict[str, Any]],
    function_mapper: Dict[str, Any],
    api_key: str,
    base_url: str,
    context_messages: Optional[List[Dict[str, Any]]] = None,
    user_id: Optional[str] = None,
    base_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    client = get_async_openai_client()
    context_block = _load_context_block(user_id, base_id)
    messages: List[Dict[str, Any]] = [{"role": "system", "content": load_system_prompt(context_block)}]
    if context_messages:
        messages.extend([msg for msg in context_messages if msg.get("content")])
    messages.append({"role": "user", "content": question})
    tool_calls: List[Dict[str, Any]] = []
    content_chunks: List[str] = []
    tool_calls_seen = False
    try:
        stream = await client.chat.completions.create(model=model, messages=messages, tools=tools, stream=True)
    except Exception as e:
        logger.exception("function_calling_streaming create failed: %s", e)
        yield {"type": "final", "tool_invoked": False, "tool_name": "", "tool_args": {}, "fallback_content": ""}
        return
    async for chunk in stream:
        if conversation_id and is_cancelled(conversation_id):
            break
        choice = chunk.choices[0] if chunk and chunk.choices else None
        delta = getattr(choice, "delta", None)
        if not delta:
            continue
        if delta.tool_calls:
            tool_calls_seen = True
            for call in delta.tool_calls:
                while len(tool_calls) <= call.index:
                    tool_calls.append({"id": "", "function": {"name": "", "arguments": ""}})
                if call.id:
                    tool_calls[call.index]["id"] = call.id
                if call.function and call.function.name:
                    tool_calls[call.index]["function"]["name"] = call.function.name
                if call.function and call.function.arguments:
                    tool_calls[call.index]["function"]["arguments"] += call.function.arguments
        content = getattr(delta, "content", None)
        if isinstance(content, str) and content:
            content_chunks.append(content)
            if not tool_calls_seen:
                yield {"type": "content_delta", "delta": content}

    fallback_content = "".join(content_chunks).strip()
    if not tool_calls:
        yield {"type": "final", "tool_invoked": False, "tool_name": "", "tool_args": {}, "fallback_content": fallback_content}
        return
    tool_call = tool_calls[0]
    function_name = tool_call["function"]["name"]
    try:
        arguments = json.loads(tool_call["function"]["arguments"] or "{}")
    except Exception:
        arguments = {}
    yield {"type": "final", "tool_invoked": True, "tool_name": function_name, "tool_args": arguments, "fallback_content": fallback_content}
