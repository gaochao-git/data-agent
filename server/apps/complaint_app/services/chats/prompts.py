"""Prompt builders for complaint chat (system prompt sourced from sys_prompts.md)."""

from __future__ import annotations

import os

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_SYS_PROMPT_PATH = os.path.join(_BASE_DIR, "sys_prompts.md")

_DEFAULT_SYSTEM_PROMPT = (
    "你是一个帮助分析举报件的助手，请基于提供的上下文（摘要、原文、专家分析等）回答用户的追问，保持简洁、准确，"
    "必要时引用上下文，不要编造未给出的信息。"
)


def _load_system_prompt() -> str:
    try:
        with open(_SYS_PROMPT_PATH, "r", encoding="utf-8") as handle:
            return handle.read()
    except Exception:
        return _DEFAULT_SYSTEM_PROMPT


_SYSTEM_PROMPT_TEMPLATE = _load_system_prompt()


def build_chat_messages(context_block: str, user_question: str) -> list[dict]:
    """Construct chat messages for the LLM using external system prompt template."""
    context_block = context_block or "{}"
    system_prompt = _SYSTEM_PROMPT_TEMPLATE.replace("context_block", context_block)
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_question},
    ]


__all__ = ["build_chat_messages"]
