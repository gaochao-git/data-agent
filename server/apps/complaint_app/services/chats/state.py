"""In-memory cancellation tokens for streaming chats."""

from __future__ import annotations

import asyncio
from typing import Dict

_cancel_tokens: Dict[str, asyncio.Event] = {}


def create_cancel_token(conversation_id: str) -> asyncio.Event:
    event = asyncio.Event()
    _cancel_tokens[conversation_id] = event
    return event


def cancel_conversation(conversation_id: str) -> bool:
    event = _cancel_tokens.get(conversation_id)
    if not event:
        return False
    event.set()
    return True


def pop_cancel_token(conversation_id: str) -> None:
    _cancel_tokens.pop(conversation_id, None)


def is_cancelled(conversation_id: str) -> bool:
    event = _cancel_tokens.get(conversation_id)
    return bool(event and event.is_set())

