"""Shared semantic request context for chat-data conversation agents."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any

from utils.logger import get_logger

from ...datasource.manager import get_datasource_manager
from .semantic_model_repository import (
    load_runtime_semantic_config,
)

logger = get_logger(__name__)


@dataclass(frozen=True)
class SemanticConversationContext:
    """The common semantic and prompt inputs for one chat turn."""

    datasource_id: str | None
    datasource_meta: dict[str, Any] | None
    semantic_config_id: str | None
    semantic_config: dict[str, Any] | None
    semantic_config_error: str = ""


def _normalize_context_inputs(
    datasource_id: str | None,
    semantic_config_id: str | None,
    user_id: str | None,
) -> tuple[str | None, str | None, str]:
    """归一化上下文入参：ID 去空白空转 None。"""
    return (
        str(datasource_id or "").strip() or None,
        str(semantic_config_id or "").strip() or None,
        str(user_id or "").strip(),
    )


def _resolved_semantic_config_id(
    normalized_config_id: str | None,
    resolved_config: dict[str, Any] | None,
) -> str | None:
    """手选 ID 优先；未手选但解析出了配置时回填其 ID。"""
    if resolved_config and not normalized_config_id:
        return str(resolved_config.get("id") or resolved_config.get("semantic_config_id") or "").strip() or None
    return normalized_config_id


async def prepare_semantic_conversation_context(
    *,
    datasource_id: str | None,
    semantic_config_id: str | None,
    user_id: str | None,
) -> SemanticConversationContext:
    """Prepare the single semantic context contract used by both conversation agents."""
    normalized_datasource_id, normalized_config_id, normalized_user_id = _normalize_context_inputs(
        datasource_id, semantic_config_id, user_id
    )

    if not normalized_datasource_id:
        return _empty_context(
            semantic_config_id=normalized_config_id,
            semantic_config_error=f"未找到可用指标集: {normalized_config_id}" if normalized_config_id else "",
        )

    manager = get_datasource_manager()
    datasource_meta_task = manager.get_datasource_meta_async(normalized_datasource_id)
    selected_config_task = None
    if normalized_config_id:
        selected_config_task = load_runtime_semantic_config(
            normalized_datasource_id,
            normalized_config_id,
            normalized_user_id,
        )

    datasource_meta, resolved_config = await _load_semantic_inputs(
        datasource_meta_task=datasource_meta_task,
        selected_config_task=selected_config_task,
    )
    semantic_error = _semantic_config_error(
        semantic_config_id=normalized_config_id,
        resolved_config=resolved_config,
    )
    normalized_config_id = _resolved_semantic_config_id(normalized_config_id, resolved_config)

    return SemanticConversationContext(
        datasource_id=normalized_datasource_id,
        datasource_meta=datasource_meta,
        semantic_config_id=normalized_config_id,
        semantic_config=resolved_config,
        semantic_config_error=semantic_error,
    )


async def _load_semantic_inputs(
    *,
    datasource_meta_task: Coroutine[Any, Any, dict[str, Any] | None],
    selected_config_task: Coroutine[Any, Any, dict[str, Any] | None] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    tasks = [datasource_meta_task]
    if selected_config_task is not None:
        tasks.append(selected_config_task)
    results = await asyncio.gather(*tasks, return_exceptions=True)
    datasource_meta = _result_or_none(results[0], "读取数据源组元信息失败")
    resolved_config = None
    if selected_config_task is not None:
        resolved_config = _result_or_none(results[1], "加载手选指标集失败")
    return datasource_meta, resolved_config


def _result_or_none(result: Any, failure_message: str) -> Any | None:
    if not isinstance(result, Exception):
        return result
    logger.warning("%s: %s", failure_message, result)
    return None


def _semantic_config_error(
    *,
    semantic_config_id: str | None,
    resolved_config: dict[str, Any] | None,
) -> str:
    if not semantic_config_id:
        return ""
    if resolved_config is None:
        return f"未找到可用指标集: {semantic_config_id}"
    if not resolved_config.get("enabled", True):
        return f"当前指标集未启用: {semantic_config_id}"
    return ""


def _empty_context(
    *,
    semantic_config_id: str | None,
    semantic_config_error: str,
) -> SemanticConversationContext:
    return SemanticConversationContext(
        datasource_id=None,
        datasource_meta=None,
        semantic_config_id=semantic_config_id,
        semantic_config=None,
        semantic_config_error=semantic_config_error,
    )
