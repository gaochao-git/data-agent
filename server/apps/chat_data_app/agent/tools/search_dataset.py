"""Search the runtime semantic dataset catalog for the current question."""

from __future__ import annotations

import json
from typing import Annotated, Any

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from ...schema import SelectSemanticConfigInput
from ...services.context_budget import ToolResultBudget, estimate_text_tokens
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
    tool_result_budget: ToolResultBudget | None = None,
    datasource_group_context: str | None = None,
):
    """Create the model-visible dataset search tool for one conversation."""
    bound_datasource_id = str(datasource_id or "").strip() or None
    bound_user_id = str(user_id or "").strip()
    bound_group_context = str(datasource_group_context or "").strip()

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
                directory = await semantic_tools.list_runtime_semantic_config_directory(current_datasource_id, bound_user_id)
                return _serialize_result(semantic_tools._semantic_config_directory_to_tool_payload(directory))

            selection = await select_semantic_config_for_query(
                datasource_id=current_datasource_id,
                user_query=user_query,
                model_id=model_id,
                user_id=bound_user_id,
                datasource_group_context=bound_group_context or semantic_tools.extract_datasource_group_context(state),
            )
            selected_ids = [
                str(config_id).strip()
                for config_id in (selection or {}).get("semantic_config_ids") or []
                if str(config_id).strip()
            ]
            semantic_configs: dict[str, dict[str, Any]] = {}
            omitted_ids: list[str] = []
            first_config_exceeds_budget = False
            remaining_tokens = (
                tool_result_budget.remaining_for_tool_result(list((state or {}).get("messages") or []))
                if tool_result_budget is not None
                else None
            )
            for config_id in selected_ids:
                config = await semantic_tools._load_runtime_semantic_config_for_user(
                    current_datasource_id,
                    config_id,
                    bound_user_id,
                )
                if not config:
                    omitted_ids.append(config_id)
                    continue
                tool_config = semantic_tools._semantic_config_for_selection_tool(config)
                candidate_payload = semantic_tools._selection_to_tool_payload(
                    selection,
                    {**semantic_configs, config_id: tool_config},
                )
                if (
                    remaining_tokens is not None
                    and estimate_text_tokens(_serialize_result(candidate_payload)) > remaining_tokens
                ):
                    omitted_ids.append(config_id)
                    first_config_exceeds_budget = not semantic_configs
                    if first_config_exceeds_budget:
                        break
                    continue
                semantic_configs[config_id] = tool_config

            payload = semantic_tools._selection_to_tool_payload(selection, semantic_configs)
            if first_config_exceeds_budget or (
                selected_ids and not semantic_configs and remaining_tokens is not None
            ):
                payload = {
                    "success": False,
                    "semantic_config_ids": [],
                    "semantic_configs": {},
                    "message": "相关指标集的运行时配置超过当前模型可用上下文，请缩小指标集或手动选择指标集后重试。",
                }
            if remaining_tokens is not None:
                logger.info(
                    "指标集运行时配置上下文预算: model=%s hard_input_limit=%s static_tokens=%s message_tokens=%s available_tokens=%s candidates=%s kept=%s omitted=%s",
                    model_id or "-",
                    tool_result_budget.budget.hard_input_limit_tokens,
                    tool_result_budget.static_input_tokens,
                    tool_result_budget.budget.hard_input_limit_tokens - tool_result_budget.static_input_tokens - remaining_tokens,
                    remaining_tokens,
                    len(selected_ids),
                    len(semantic_configs),
                    omitted_ids,
                )
            return _serialize_result(payload)
        except Exception as exc:
            logger.error("search_dataset 执行失败: %s", exc, exc_info=True)
            return f"search_dataset 执行失败: {exc}"

    return search_dataset


__all__ = ["create_search_dataset_tool"]
