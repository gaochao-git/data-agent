"""Conversation prompt context built on the shared semantic conversation context."""

from __future__ import annotations

from typing import Any

from .semantic_conversation_context import (
    AgentPromptContext,
)


def _truncate(value: Any, max_length: int = 120) -> str:
    """Trim text before it is included in an analysis prompt."""
    text = str(value or "").strip()
    return text if len(text) <= max_length else f"{text[:max_length]}..."


def _selected_semantic_config_item(config: dict[str, Any]) -> dict[str, Any]:
    """Build the selected-config reference allowed in the system prompt."""
    return {
        "id": config.get("id") or config.get("semantic_config_id") or "",
        "name": config.get("name") or config.get("semantic_config_name") or "",
        "selected": True,
    }


def _build_conversation_context_notes(has_selected_semantic_config: bool) -> list[str]:
    notes = ["每次语义查询必须在工具参数中明确传入当前用户可用的 member_datasource_ids。"]
    if has_selected_semantic_config:
        return [
            "当前手动选择仅作为本轮优先参考，不代表锁定，也不注入完整指标、维度或表结构。",
            *notes,
            "需要具体指标、维度、枚举、关联或表列时，先调用 search_dataset 获取实际语义配置。",
        ]
    return [
        "未手动选择指标集时仅注入指标集轻量目录；业务数据分析必须先选择指标集，再使用工具返回的完整指标和维度。",
        *notes,
        "用户没有明确口径时，先选择最匹配的指标集；候选不唯一或口径不清时先澄清。",
    ]


async def build_conversation_prompt_context(
    prompt_context: AgentPromptContext,
) -> dict[str, Any]:
    """Add bounded data metadata to the single Agent prompt contract."""
    datasource = prompt_context.get("datasource")
    if datasource is None:
        return _empty_conversation_context(
            prompt_context,
            "当前未选择数据源，可先按通用分析框架生成分析思路、指标假设和后续工具步骤。",
        )
    if datasource.get("missing"):
        return _empty_conversation_context(
            prompt_context,
            "未找到当前数据源，可先按通用分析框架生成分析思路、指标假设和后续工具步骤。",
        )

    selected_config = prompt_context.get("selected_semantic_config")
    datasource_id = str(datasource.get("id") or "").strip()
    return {
        "datasource": {
            "id": datasource_id,
            "name": datasource.get("group_name") or datasource.get("name") or "",
            "description": _truncate(datasource.get("ds_description") or datasource.get("description") or "", 160),
            "source_type": datasource.get("source_type") or "",
        },
        "execution_targets": list(prompt_context.get("execution_targets") or []),
        "datasource_group_context": str(prompt_context.get("datasource_group_context") or ""),
        "tables": [],
        "semantic_config_directory_info": str(prompt_context.get("semantic_config_directory_info") or ""),
        "semantic_configs": [_selected_semantic_config_item(selected_config)] if selected_config else [],
        "notes": _build_conversation_context_notes(selected_config is not None),
    }


def _empty_conversation_context(prompt_context: AgentPromptContext, message: str) -> dict[str, Any]:
    return {
        "datasource": prompt_context.get("datasource"),
        "execution_targets": list(prompt_context.get("execution_targets") or []),
        "datasource_group_context": str(prompt_context.get("datasource_group_context") or ""),
        "tables": [],
        "semantic_config_directory_info": str(prompt_context.get("semantic_config_directory_info") or ""),
        "semantic_configs": [],
        "notes": [message],
    }
