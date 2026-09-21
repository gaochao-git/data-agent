"""Shared semantic request context for chat-data conversation agents."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, TypedDict

from utils.logger import get_logger

from ...datasource.manager import get_datasource_manager
from ...datasource.target_context import (
    append_selected_execution_targets_to_context,
    get_execution_target_member_id,
    get_execution_target_name,
    get_execution_target_org_code,
    normalize_member_datasource_ids,
)
from .semantic_model_repository import (
    load_runtime_semantic_config,
)

logger = get_logger(__name__)


@dataclass(frozen=True)
class SemanticConversationContext:
    """The common semantic, authorization and prompt inputs for one chat turn."""

    datasource_id: str | None
    datasource_meta: dict[str, Any] | None
    semantic_config_id: str | None
    semantic_config: dict[str, Any] | None
    execution_targets: list[dict[str, Any]]
    available_member_datasource_ids: list[str]
    matched_member_datasource_ids: list[str]
    selected_member_datasource_ids: list[str]
    datasource_group_context: str
    semantic_config_error: str = ""

    @property
    def has_selected_semantic_config(self) -> bool:
        return self.semantic_config is not None


class AgentPromptContext(TypedDict):
    """Shared prompt inputs derived from one semantic conversation context."""

    datasource: dict[str, Any] | None
    datasource_group_context: str
    selected_semantic_config: dict[str, str] | None


def build_agent_prompt_context(context: SemanticConversationContext) -> AgentPromptContext:
    """Build the common prompt contract consumed by both conversation agents."""
    datasource_meta = context.datasource_meta or {}
    datasource: dict[str, Any] | None = None
    if context.datasource_id:
        if context.datasource_meta is None:
            datasource = {"missing": True}
        else:
            datasource = {
                "source_type": datasource_meta.get("source_type") or "",
            }

    return {
        "datasource": datasource,
        "datasource_group_context": context.datasource_group_context,
        "selected_semantic_config": _selected_semantic_config_prompt_ref(context),
    }


def _selected_semantic_config_prompt_ref(
    context: SemanticConversationContext,
) -> dict[str, str] | None:
    """Expose only a selected-config reference to the system prompt layer."""
    if context.semantic_config is None:
        return None
    config_id = str(context.semantic_config_id or context.semantic_config.get("id") or "").strip()
    config_name = str(
        context.semantic_config.get("name")
        or context.semantic_config.get("semantic_config_name")
        or config_id
    ).strip()
    return {"id": config_id, "name": config_name}


def _normalize_context_inputs(
    datasource_id: str | None,
    semantic_config_id: str | None,
    user_id: str | None,
    username: str | None,
    member_datasource_ids: list[str] | None,
) -> tuple[str | None, str | None, str, str, list[str]]:
    """归一化上下文入参：ID 去空白空转 None，成员列表标准化。"""
    return (
        str(datasource_id or "").strip() or None,
        str(semantic_config_id or "").strip() or None,
        str(user_id or "").strip(),
        str(username or "").strip(),
        normalize_member_datasource_ids(member_datasource_ids),
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
    member_datasource_ids: list[str] | None,
    user_id: str | None,
    username: str | None,
    user_query: str | None,
) -> SemanticConversationContext:
    """Prepare the single semantic context contract used by both conversation agents."""
    normalized_datasource_id, normalized_config_id, normalized_user_id, normalized_username, selected_member_ids = (
        _normalize_context_inputs(datasource_id, semantic_config_id, user_id, username, member_datasource_ids)
    )

    if not normalized_datasource_id:
        return _empty_context(
            semantic_config_id=normalized_config_id,
            selected_member_datasource_ids=selected_member_ids,
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

    execution_targets = await _load_authorized_execution_targets(
        manager=manager,
        datasource_id=normalized_datasource_id,
        datasource_meta=datasource_meta,
        member_datasource_ids=selected_member_ids,
        user_id=normalized_user_id,
        username=normalized_username,
    )
    available_member_ids = _target_member_ids(execution_targets)
    matched_member_ids = match_execution_targets_from_query(execution_targets, user_query)
    base_query_context = str((datasource_meta or {}).get("query_context") or "").strip()
    datasource_group_context = append_selected_execution_targets_to_context(
        base_query_context,
        execution_targets,
        selected_member_ids,
    )

    return SemanticConversationContext(
        datasource_id=normalized_datasource_id,
        datasource_meta=datasource_meta,
        semantic_config_id=normalized_config_id,
        semantic_config=resolved_config,
        execution_targets=execution_targets,
        available_member_datasource_ids=available_member_ids,
        matched_member_datasource_ids=matched_member_ids,
        selected_member_datasource_ids=selected_member_ids,
        datasource_group_context=datasource_group_context,
        semantic_config_error=semantic_error,
    )


async def _load_semantic_inputs(
    *,
    datasource_meta_task,
    selected_config_task,
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


async def _load_authorized_execution_targets(
    *,
    manager: Any,
    datasource_id: str,
    datasource_meta: dict[str, Any] | None,
    member_datasource_ids: list[str],
    user_id: str,
    username: str,
) -> list[dict[str, Any]]:
    if not member_datasource_ids:
        return []
    try:
        if datasource_meta is not None and hasattr(manager, "authorize_execution_datasources_async"):
            targets = await manager.authorize_execution_datasources_async(
                datasource_meta,
                member_datasource_ids,
                user_id,
                username,
            )
        else:
            targets = await manager.get_authorized_execution_datasources_async(
                datasource_id,
                member_datasource_ids,
                user_id=user_id,
                username=username,
            )
    except Exception as exc:
        # 授权查询失败必须 fail-close：静默返回空列表会让查询在缺失
        # 执行对象范围的情况下继续跑，用户无感知地丢失行级范围。
        logger.warning("读取当前用户执行对象失败: datasource_id=%s error=%s", datasource_id, exc)
        raise ValueError("读取执行对象权限失败，请稍后重试") from exc
    return [dict(item) for item in targets or [] if isinstance(item, dict)]


def _empty_context(
    *,
    semantic_config_id: str | None,
    selected_member_datasource_ids: list[str],
    semantic_config_error: str,
) -> SemanticConversationContext:
    return SemanticConversationContext(
        datasource_id=None,
        datasource_meta=None,
        semantic_config_id=semantic_config_id,
        semantic_config=None,
        execution_targets=[],
        available_member_datasource_ids=[],
        matched_member_datasource_ids=[],
        selected_member_datasource_ids=selected_member_datasource_ids,
        datasource_group_context="",
        semantic_config_error=semantic_config_error,
    )


def match_execution_targets_from_query(
    execution_targets: list[dict[str, Any]],
    user_query: str | None,
) -> list[str]:
    """Match explicit organization names in the user query against authorized targets."""
    if _query_requests_all_execution_targets(user_query):
        return _target_member_ids(execution_targets)

    text = str(user_query or "").strip().casefold()
    if not text:
        return []
    matches: list[tuple[int, int, int, str]] = []
    for index, target in enumerate(execution_targets, start=1):
        member_id = get_execution_target_member_id(target)
        if not member_id:
            continue
        terms = (
            get_execution_target_name(target, index),
            get_execution_target_org_code(target),
            member_id,
        )
        for term in dict.fromkeys(term for term in terms if term):
            normalized_term = term.casefold()
            start = text.find(normalized_term)
            while start >= 0:
                matches.append((start, start + len(normalized_term), len(normalized_term), member_id))
                start = text.find(normalized_term, start + 1)

    selected: list[tuple[int, int, int, str]] = []
    selected_spans: list[tuple[int, int]] = []
    for match in sorted(matches, key=lambda item: (-item[2], item[0])):
        span = (match[0], match[1])
        if any(span[0] < selected_span[1] and selected_span[0] < span[1] for selected_span in selected_spans):
            continue
        selected.append(match)
        selected_spans.append(span)
    return list(dict.fromkeys(match[3] for match in sorted(selected, key=lambda item: item[0])))


def _query_requests_all_execution_targets(user_query: str | None) -> bool:
    text = str(user_query or "").strip()
    keywords = ("全部机构", "所有机构", "各机构", "每个机构", "全部执行对象", "所有执行对象")
    return any(keyword in text for keyword in keywords)


def _target_member_ids(execution_targets: list[dict[str, Any]]) -> list[str]:
    return list(dict.fromkeys(
        member_id
        for target in execution_targets
        if (member_id := get_execution_target_member_id(target))
    ))
