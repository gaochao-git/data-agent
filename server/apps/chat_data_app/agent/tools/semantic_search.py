"""Search and inspect authorized runtime semantic models."""

from __future__ import annotations

from typing import Annotated, Any, Dict, List, Literal, Optional

from langchain_core.messages import HumanMessage

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from ...schema import SemanticToolInput
from apps.chat_data_app.services.semantic.run.semantic_model_search import search_semantic_models
from apps.chat_data_app.services.semantic.run.semantic_materializer import (
    SEMANTIC_SNAPSHOT_DIR,
    enabled_candidate_ids,
    materialize_runtime_semantic_models,
)
from apps.chat_data_app.services.semantic.run.semantic_config_service import get_semantic_config_service
from .common import tool_result_payload



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
        "message": "已返回相关语义模型的轻量引用；字段口径确认与检索方式以 semantic-query 技能为准。" if returned_ids else "未检索到相关指标集。请根据用户问题说明当前指标体系的限制，或向用户澄清业务范围。",
    }


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
    # 全量候选只拉一次：既算物化范围，又直接喂给 LLM 打分器，
    # 避免打分器内部再拉一次库（search_semantic_models 传 semantic_configs 时不自查）。
    runtime_configs = await get_semantic_config_service().get_runtime_configs_async(datasource_id, user_id)
    candidate_ids = enabled_candidate_ids(runtime_configs)
    selection = await search_semantic_models(
        datasource_id=datasource_id,
        user_query=query,
        model_id=model_id,
        user_id=user_id,
        datasource_group_context=datasource_group_context,
        semantic_configs=runtime_configs,
    )
    selected_ids = [
        str(config_id).strip()
        for config_id in (selection or {}).get("semantic_config_ids") or []
        if str(config_id).strip()
    ]
    if (selected_ids or candidate_ids) and workspace_runtime is None:
        raise ValueError("当前请求缺少会话工作区")
    # 全量物化：LLM 只负责召回 top5，精读层可对全部启用模型 file_grep/file_read；
    # 范围是完整启用集，可顺带清理停用/删除模型的残留 YAML。
    materialized = await materialize_runtime_semantic_models(
        workspace_runtime=workspace_runtime,
        datasource_id=datasource_id,
        user_id=user_id,
        source_type=source_type,
        semantic_config_ids=candidate_ids,
        prune_stale=True,
    )
    payload = _selection_to_tool_payload(
        selection,
        _materialized_directory(materialized),
    )
    if payload.get("success"):
        payload["semantic_dir"] = SEMANTIC_SNAPSHOT_DIR
        payload["materialized_total"] = len(materialized.get("directory") or [])
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
            "已返回字段轻量索引。字段完整定义与口径确认以 semantic-query 技能为准。"
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
        intent: str = "",
        state: Annotated[dict, InjectedState] = None,
    ) -> str:
        """搜索语义模型，或分页列出模型中的指标和维度。

        - search_models：在当前用户可见的全部启用语义模型中按完整业务意图检索，返回候选模型轻量引用，
          并确保候选模型的 Ossie YAML 已物化到会话工作区 semantic/ 目录。
        - list_fields：传 semantic_config_id；可用 field_type=all/metric/dimension 和 offset/limit 分页。
        本工具只做检索辅助；语义模型检索流程、候选确认与字段口径确认一律以 `semantic-query` 技能为准，本工具不重复定义。
        """
        current_datasource_id = bound_datasource_id or extract_datasource_id(state)
        if not current_datasource_id:
            raise ValueError("当前会话未绑定数据源")
        if operation == "search_models":
            user_query = str(query or extract_user_query(state) or "").strip()
            if not user_query:
                raise ValueError("search_models 缺少用户问题")
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
            return tool_result_payload("semantic_search", payload)

        normalized_config_id = str(semantic_config_id or "").strip()
        config, reference = await _materialize_one(
            workspace_runtime=workspace_runtime,
            datasource_id=current_datasource_id,
            user_id=bound_user_id,
            source_type=bound_source_type,
            semantic_config_id=normalized_config_id,
        )
        if operation == "list_fields":
            return tool_result_payload("semantic_search", _list_fields_payload(
                config=config,
                reference=reference,
                semantic_config_id=normalized_config_id,
                field_type=field_type,
                offset=offset,
                limit=limit,
            ))
        raise ValueError(f"不支持的操作 {operation}")

    return semantic_search


__all__ = ["create_semantic_search"]
