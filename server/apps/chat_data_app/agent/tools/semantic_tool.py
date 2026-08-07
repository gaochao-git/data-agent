"""Search and inspect authorized runtime semantic models."""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from ...schema import SemanticToolInput
from ...services import semantic_tools
from ...services.semantic_config_selector import select_semantic_config_for_query
from ...services.semantic_materializer import materialize_runtime_semantic_models
from utils.logger import get_logger

logger = get_logger(__name__)


def _serialize_result(payload: dict[str, Any]) -> str:
    return "[semantic_tool]\n" + json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _materialized_configs(materialized: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(config_id).strip(): config
        for config_id, config in (materialized.get("configs") or {}).items()
        if str(config_id).strip() and isinstance(config, dict)
    }


def _materialized_directory(materialized: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("id") or "").strip(): item
        for item in materialized.get("directory") or []
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }


async def _materialize_one(
    *,
    workspace_runtime: Any,
    datasource_id: str,
    user_id: str,
    source_type: str,
    semantic_config_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if workspace_runtime is None:
        raise ValueError("当前请求缺少会话工作区")
    materialized = await materialize_runtime_semantic_models(
        workspace_runtime=workspace_runtime,
        datasource_id=datasource_id,
        user_id=user_id,
        source_type=source_type,
        semantic_config_ids=[semantic_config_id],
    )
    config = _materialized_configs(materialized).get(semantic_config_id)
    reference = _materialized_directory(materialized).get(semantic_config_id)
    if not config or not reference:
        raise ValueError(f"未找到当前用户可用的语义模型 {semantic_config_id}")
    return config, reference


async def _search_models(
    *,
    query: str,
    datasource_id: str,
    user_id: str,
    model_id: str | None,
    datasource_group_context: str,
    source_type: str,
    workspace_runtime: Any,
) -> dict[str, Any]:
    if semantic_tools._is_semantic_config_directory_query(query):
        directory = await semantic_tools.list_runtime_semantic_config_directory(datasource_id, user_id)
        payload = semantic_tools._semantic_config_directory_to_tool_payload(directory)
        payload["operation"] = "search_models"
        return payload

    selection = await select_semantic_config_for_query(
        datasource_id=datasource_id,
        user_query=query,
        model_id=model_id,
        user_id=user_id,
        datasource_group_context=datasource_group_context,
    )
    selected_ids = [
        str(config_id).strip()
        for config_id in (selection or {}).get("semantic_config_ids") or []
        if str(config_id).strip()
    ]
    if selected_ids and workspace_runtime is None:
        raise ValueError("当前请求缺少会话工作区")
    materialized = await materialize_runtime_semantic_models(
        workspace_runtime=workspace_runtime,
        datasource_id=datasource_id,
        user_id=user_id,
        source_type=source_type,
        semantic_config_ids=selected_ids,
    )
    payload = semantic_tools._selection_to_tool_payload(
        selection,
        _materialized_directory(materialized),
    )
    payload["operation"] = "search_models"
    return payload


def _list_fields_payload(
    *,
    config: dict[str, Any],
    reference: dict[str, Any],
    semantic_config_id: str,
    field_type: Literal["all", "metric", "dimension"],
    offset: int,
    limit: int,
) -> dict[str, Any]:
    fields = semantic_tools.build_semantic_field_index(config, field_type=field_type)
    page = fields[offset:offset + limit]
    next_offset = offset + len(page)
    has_more = next_offset < len(fields)
    return {
        "success": True,
        "operation": "list_fields",
        "semantic_config_id": semantic_config_id,
        "semantic_config": reference,
        "field_type": field_type,
        "metrics_total": len(config.get("metrics") or []),
        "dimensions_total": len(config.get("dimensions") or []),
        "total": len(fields),
        "offset": offset,
        "limit": limit,
        "has_more": has_more,
        "next_offset": next_offset if has_more else None,
        "fields": page,
        "message": (
            "已返回字段轻量索引。需要确认字段完整定义、关系或原始表列时，"
            "使用 semantic_config.path 配合 file_grep/file_read。"
        ),
    }


def create_semantic_tool(
    *,
    model_id: str | None = None,
    datasource_id: str | None = None,
    user_id: str | None = None,
    datasource_group_context: str | None = None,
    source_type: str | None = None,
    workspace_runtime: Any | None = None,
):
    """Create the model-visible semantic model tool for one conversation."""
    bound_datasource_id = str(datasource_id or "").strip() or None
    bound_user_id = str(user_id or "").strip()
    bound_group_context = str(datasource_group_context or "").strip()
    bound_source_type = str(source_type or "").strip()

    @tool("semantic_tool", args_schema=SemanticToolInput)
    async def semantic_tool(
        operation: str,
        query: str | None = None,
        semantic_config_id: str | None = None,
        field_type: str = "all",
        offset: int = 0,
        limit: int = 100,
        state: Annotated[dict, InjectedState] = None,
    ) -> str:
        """搜索语义模型，或分页列出模型中的指标和维度。

        - search_models：在当前用户可见的全部启用语义模型中，按完整业务意图检索并返回一个或多个候选，
          同时把候选 Ossie YAML 写入会话工作区。同一业务问题只需调用一次，不要按指标、维度或子问题拆成多个并行搜索。
        - list_fields：传 semantic_config_id；可用 field_type=all/metric/dimension 和 offset/limit 分页。
        字段完整定义和模型结构不由本工具返回；使用返回的 path 配合 file_grep/file_read。
        """
        current_datasource_id = bound_datasource_id or semantic_tools.extract_datasource_id(state)
        if not current_datasource_id:
            return "semantic_tool 执行失败: 当前会话未绑定数据源"
        try:
            if operation == "search_models":
                user_query = str(query or semantic_tools.extract_user_query(state) or "").strip()
                if not user_query:
                    return "semantic_tool 执行失败: search_models 缺少用户问题"
                payload = await _search_models(
                    query=user_query,
                    datasource_id=current_datasource_id,
                    user_id=bound_user_id,
                    model_id=model_id,
                    datasource_group_context=(
                        bound_group_context
                        or semantic_tools.extract_datasource_group_context(state)
                    ),
                    source_type=bound_source_type,
                    workspace_runtime=workspace_runtime,
                )
                return _serialize_result(payload)

            normalized_config_id = str(semantic_config_id or "").strip()
            config, reference = await _materialize_one(
                workspace_runtime=workspace_runtime,
                datasource_id=current_datasource_id,
                user_id=bound_user_id,
                source_type=bound_source_type,
                semantic_config_id=normalized_config_id,
            )
            if operation == "list_fields":
                return _serialize_result(_list_fields_payload(
                    config=config,
                    reference=reference,
                    semantic_config_id=normalized_config_id,
                    field_type=field_type,
                    offset=offset,
                    limit=limit,
                ))
            return f"semantic_tool 执行失败: 不支持的操作 {operation}"
        except Exception as exc:
            logger.error("semantic_tool 执行失败: %s", exc, exc_info=True)
            return f"semantic_tool 执行失败: {exc}"

    return semantic_tool


__all__ = ["create_semantic_tool"]
