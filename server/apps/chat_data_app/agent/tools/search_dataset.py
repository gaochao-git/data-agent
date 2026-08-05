"""Search the runtime semantic dataset catalog for the current question."""

from __future__ import annotations

import json
from typing import Annotated, Any

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from ...schema import SelectSemanticConfigInput
from ...services import semantic_tools
from ...services.semantic_config_selector import select_semantic_config_for_query
from utils.logger import get_logger

logger = get_logger(__name__)


def _serialize_result(payload: dict[str, Any]) -> str:
    """Keep the tool protocol marker stable for the current frontend parser."""
    return "[search_dataset]\n" + json.dumps(payload, ensure_ascii=False, indent=2)


def create_search_dataset_tool(
    *,
    model_id: str | None = None,
    datasource_id: str | None = None,
    user_id: str | None = None,
    datasource_group_context: str | None = None,
    semantic_snapshot: dict[str, Any] | None = None,
):
    """Create the model-visible dataset search tool for one conversation."""
    bound_datasource_id = str(datasource_id or "").strip() or None
    bound_user_id = str(user_id or "").strip()
    bound_group_context = str(datasource_group_context or "").strip()
    bound_snapshot = semantic_snapshot or {}
    has_snapshot = bool(bound_snapshot.get("snapshot_id"))

    @tool("search_dataset", args_schema=SelectSemanticConfigInput)
    async def search_dataset(
        query: str | None = None,
        state: Annotated[dict, InjectedState] = None,
    ) -> str:
        """Search enabled semantic datasets relevant to the user's business question."""
        current_datasource_id = bound_datasource_id or semantic_tools.extract_datasource_id(state)
        user_query = str(query or semantic_tools.extract_user_query(state) or "").strip()
        if not current_datasource_id:
            return "search_dataset 执行失败: 当前会话未绑定数据源"
        if not user_query:
            return "search_dataset 执行失败: 缺少用户问题"
        try:
            if semantic_tools._is_semantic_config_directory_query(user_query):
                directory = bound_snapshot.get("directory")
                if directory is None:
                    directory = await semantic_tools.list_runtime_semantic_config_directory(current_datasource_id, bound_user_id)
                return _serialize_result(semantic_tools._semantic_config_directory_to_tool_payload(directory))

            selection = await select_semantic_config_for_query(
                datasource_id=current_datasource_id,
                user_query=user_query,
                model_id=model_id,
                user_id=bound_user_id,
                datasource_group_context=bound_group_context or semantic_tools.extract_datasource_group_context(state),
                semantic_configs=(
                    list((bound_snapshot.get("configs") or {}).values())
                    if has_snapshot
                    else None
                ),
            )
            selected_ids = [
                str(config_id).strip()
                for config_id in (selection or {}).get("semantic_config_ids") or []
                if str(config_id).strip()
            ]
            semantic_configs: dict[str, dict[str, Any]] = {}
            for config_id in selected_ids:
                config = (bound_snapshot.get("configs") or {}).get(config_id)
                if config is None:
                    # The conversation snapshot is the only model-visible semantic view.
                    # Do not re-load a newer config without an Ossie YAML path.
                    if has_snapshot:
                        continue
                    config = await semantic_tools._load_runtime_semantic_config_for_user(
                        current_datasource_id,
                        config_id,
                        bound_user_id,
                    )
                if not config:
                    continue
                directory_item = next(
                    (item for item in bound_snapshot.get("directory") or [] if item.get("id") == config_id),
                    {},
                )
                semantic_configs[config_id] = {
                    "id": config_id,
                    "name": directory_item.get("name") or config.get("name") or config_id,
                    "description": directory_item.get("description") or config.get("description") or "",
                    "metrics_total": directory_item.get("metrics_total", len(config.get("metrics") or [])),
                    "dimensions_total": directory_item.get("dimensions_total", len(config.get("dimensions") or [])),
                    "path": (bound_snapshot.get("paths") or {}).get(config_id, ""),
                }

            payload = semantic_tools._selection_to_tool_payload(selection, semantic_configs)
            return _serialize_result(payload)
        except Exception as exc:
            logger.error("search_dataset 执行失败: %s", exc, exc_info=True)
            return f"search_dataset 执行失败: {exc}"

    return search_dataset


__all__ = ["create_search_dataset_tool"]
