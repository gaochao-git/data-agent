"""Conversation prompt context built on the shared semantic conversation context."""

from __future__ import annotations

from typing import Any

from .semantic_conversation_context import (
    AgentPromptContext,
)


def _selected_semantic_config_item(config: dict[str, Any]) -> dict[str, Any]:
    """Build the selected-config reference allowed in the system prompt."""
    return {
        "id": config.get("id") or config.get("semantic_config_id") or "",
        "name": config.get("name") or config.get("semantic_config_name") or "",
        "selected": True,
    }


async def build_conversation_prompt_context(
    prompt_context: AgentPromptContext,
) -> dict[str, Any]:
    """Add bounded data metadata to the single Agent prompt contract."""
    datasource = prompt_context.get("datasource")
    if datasource is None:
        return _empty_conversation_context(prompt_context)
    if datasource.get("missing"):
        return _empty_conversation_context(prompt_context)

    selected_config = prompt_context.get("selected_semantic_config")
    return {
        "datasource": {
            "source_type": datasource.get("source_type") or "",
        },
        "datasource_group_context": str(prompt_context.get("datasource_group_context") or ""),
        "semantic_config_directory_info": str(prompt_context.get("semantic_config_directory_info") or ""),
        "semantic_configs": [_selected_semantic_config_item(selected_config)] if selected_config else [],
    }


def _empty_conversation_context(prompt_context: AgentPromptContext) -> dict[str, Any]:
    datasource = prompt_context.get("datasource")
    return {
        "datasource": {
            "source_type": (datasource or {}).get("source_type") or "",
        },
        "datasource_group_context": str(prompt_context.get("datasource_group_context") or ""),
        "semantic_config_directory_info": str(prompt_context.get("semantic_config_directory_info") or ""),
        "semantic_configs": [],
    }
