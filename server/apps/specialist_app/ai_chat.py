"""Proxy `/api/ai_chat` requests to the payment specialist agent."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Callable
from importlib import import_module
from typing import Any, Dict, Tuple

from .conversation_history import (
    get_conversation_detail_for_user as history_get_conversation_detail_for_user,
    list_conversations_for_user as history_list_conversations_for_user,
    resolve_conversation_id,
    resolve_user_id,
)

_MODULE_BASE = __package__ or "apps.specialist_app"
_IMPLEMENTATION_ENV = "PAYMENT_SPECIALIST_AGENT_IMPL"
_DEFAULT_IMPL = "payment_specialist"
_IMPLEMENTATIONS: Dict[str, Tuple[str, str]] = {
    "payment_specialist": (f"{_MODULE_BASE}.payment_specialist.agent_chat_core", "chat_with_react_as_function_call"),
    "payment_specialist_deepagent": (f"{_MODULE_BASE}.payment_specialist_deepagent.agent_chat_core", "chat_with_deepagent"),
    "payment_specialist_workflow": (f"{_MODULE_BASE}.payment_specialist_workflow.agent_chat_core", "chat_with_workflow"),
}


def _load_chat_impl() -> Callable[[str, str, str | None, str | None], AsyncIterator[str]]:
    """
    Import and return the configured chat coroutine generator.

    The env var falls back to the original `payment_specialist` implementation
    when unset or invalid.
    """

    impl_key = os.getenv(_IMPLEMENTATION_ENV, _DEFAULT_IMPL).strip().lower() or _DEFAULT_IMPL
    module_path, func_name = _IMPLEMENTATIONS.get(impl_key, _IMPLEMENTATIONS[_DEFAULT_IMPL])
    module = import_module(module_path)

    # Ensure shared tool registration executes regardless of implementation.
    import_module(f"{_MODULE_BASE}.gpts_regulation")

    chat_func = getattr(module, func_name, None)
    if chat_func is None:
        raise AttributeError(f"{module_path} missing `{func_name}`")
    return chat_func


_chat_impl = _load_chat_impl()


async def _iter_ai_chat_stream(payload: Dict[str, Any], current_user: Any) -> AsyncIterator[str]:
    question = str(payload.get("question") or "").strip()

    conversation_id = resolve_conversation_id(
        current_user,
        str(payload.get("conversation_id") or "").strip(),
    )
    user_id = resolve_user_id(current_user)
    model_name = os.getenv("OPENAI_ANALYSIS_MODEL")
    if current_user:
        print(
            f"[AI Chat] user_id={getattr(current_user, 'user_id', '')} "
            f"username={getattr(current_user, 'username', '')} "
            f"conversation_id={conversation_id}"
        )
    print(f"conversation_idconversation_idconversation_idconversation_id:{conversation_id}")
    async_iter = _chat_impl(
        question,
        conversation_id,
        model_name,
        user_id,
    )
    try:
        async for chunk in async_iter:
            if chunk is not None:
                yield chunk
    except asyncio.CancelledError:
        await async_iter.aclose()
        raise
    finally:
        try:
            await async_iter.aclose()
        except Exception:
            pass


def stream_ai_chat_response(
    payload: Dict[str, Any],
    origin: str = "",
    current_user: Any = None,
) -> Tuple[AsyncIterator[str], Dict[str, str]]:
    """Return shared stream generator + headers for any framework."""

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
    }
    if origin:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"
        headers["Vary"] = "Origin"

    return _iter_ai_chat_stream(payload, current_user), headers


def list_conversations_for_user(current_user):
    """Conversation listing is shared regardless of implementation."""

    return history_list_conversations_for_user(current_user)


def get_conversation_detail_for_user(current_user, conversation_id: str):
    """Conversation detail lookup is shared regardless of implementation."""

    return history_get_conversation_detail_for_user(current_user, conversation_id)


__all__ = [
    "stream_ai_chat_response",
    "list_conversations_for_user",
    "get_conversation_detail_for_user",
]
