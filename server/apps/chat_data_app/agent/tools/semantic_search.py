"""Search and inspect authorized runtime semantic models."""

from __future__ import annotations

import json
from typing import Annotated, Any, Dict, List, Literal, Optional

from langchain_core.messages import HumanMessage

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from ...schema import SemanticToolInput
from ...services.semantic_model_search import search_semantic_models
from ...services.semantic_materializer import materialize_runtime_semantic_models
from utils.logger import get_logger

logger = get_logger(__name__)


def extract_user_query(state: Optional[Dict]) -> Optional[str]:
    if not state or "messages" not in state:
        return None
    for message in reversed(state["messages"]):
        if isinstance(message, HumanMessage):
            return message.content
    return None


def extract_datasource_id(state: Optional[Dict]) -> Optional[str]:
    if not state:
        return None
    execution_context = state.get("execution_context")
    if isinstance(execution_context, dict):
        datasource_id = str(execution_context.get("datasource_id") or "").strip()
        if datasource_id:
            return datasource_id
    datasource_id = str(state.get("datasource_id") or "").strip()
    return datasource_id or None


def extract_datasource_group_context(state: Optional[Dict]) -> str:
    return str((state or {}).get("datasource_group_context") or "").strip()


def _compact_field_text(value: Any, max_length: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= max_length else text[:max_length] + "..."


def _field_index_item(item: Dict[str, Any], field_type: Literal["metric", "dimension"]) -> Dict[str, Any]:
    result = {
        "field_type": field_type,
        "name": str(item.get("name") or "").strip(),
        "description": _compact_field_text(item.get("description") or item.get("metadata_supplement") or "", 240),
        "logical_type": str(item.get("logical_type") or "").strip(),
    }
    return {key: value for key, value in result.items() if value}


def build_semantic_field_index(config: Dict[str, Any], *, field_type: Literal["all", "metric", "dimension"] = "all") -> List[Dict[str, Any]]:
    fields: List[Dict[str, Any]] = []
    if field_type in {"all", "metric"}:
        fields.extend(_field_index_item(item, "metric") for item in config.get("metrics") or [] if isinstance(item, dict) and item.get("name"))
    if field_type in {"all", "dimension"}:
        fields.extend(_field_index_item(item, "dimension") for item in config.get("dimensions") or [] if isinstance(item, dict) and item.get("name"))
    return fields


def _selection_to_tool_payload(selection: Optional[Dict[str, Any]], semantic_configs: Dict[str, Dict[str, Any]] | None = None) -> Dict[str, Any]:
    if not selection:
        return {"success": False, "semantic_config_ids": [], "semantic_configs": {}, "message": "当前数据源组没有可用于智能问数的指标集，请先维护并启用指标集"}
    ids = [str(config_id).strip() for config_id in selection.get("semantic_config_ids") or [] if str(config_id).strip()]
    summaries = semantic_configs or {}
    returned_ids = [config_id for config_id in ids if config_id in summaries]
    return {
        "success": True,
        "semantic_config_ids": returned_ids,
        "semantic_configs": {config_id: summaries[config_id] for config_id in returned_ids},
        "message": "已返回相关语义模型的轻量引用。先对候选模型调用 list_fields；需要确认字段完整定义、关系、原始表列或模型整体结构时，使用每项 path 配合 file_grep/file_read。" if returned_ids else "未检索到相关指标集。请根据用户问题说明当前指标体系的限制，或向用户澄清业务范围。",
    }


def _serialize_result(payload: dict[str, Any]) -> str:
    return "[semantic_search]\n" + json.dumps(payload, ensure_ascii=False, indent=2, default=str)


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
    selection = await search_semantic_models(
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
    payload = _selection_to_tool_payload(
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
    fields = build_semantic_field_index(config, field_type=field_type)
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


def create_semantic_search(
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

    @tool("semantic_search", args_schema=SemanticToolInput)
    async def semantic_search(
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
        current_datasource_id = bound_datasource_id or extract_datasource_id(state)
        if not current_datasource_id:
            return "semantic_search 执行失败: 当前会话未绑定数据源"
        try:
            if operation == "search_models":
                user_query = str(query or extract_user_query(state) or "").strip()
                if not user_query:
                    return "semantic_search 执行失败: search_models 缺少用户问题"
                payload = await _search_models(
                    query=user_query,
                    datasource_id=current_datasource_id,
                    user_id=bound_user_id,
                    model_id=model_id,
                    datasource_group_context=(
                        bound_group_context
                        or extract_datasource_group_context(state)
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
            return f"semantic_search 执行失败: 不支持的操作 {operation}"
        except Exception as exc:
            logger.error("semantic_search 执行失败: %s", exc, exc_info=True)
            return f"semantic_search 执行失败: {exc}"

    return semantic_search


__all__ = ["create_semantic_search"]
