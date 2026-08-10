"""DataAnalyze dsl2sql_tool."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from utils.logger import get_logger
from ...services.dashboard.spec_service import (
    remove_mixed_raw_metrics_for_aggregate_dsl,
    validate_dashboard_spec_payload,
)
from ...services.semantic.run.semantic_model_repository import load_semantic_model
from ...services.semantic.run.semantic_query_service import SemanticCompileOptions, compile_semantic_ai_dsl
from .common import json_dumps, normalize_json_like_payload

logger = get_logger(__name__)


def normalize_dsl_payload(value: Any) -> Any:
    """兼容模型把 DSL 作为 JSON 字符串或二次转义字符串传入。"""
    return normalize_json_like_payload(value)


def create_dsl2sql_tool(datasource_id: str | None = None, user_id: str | None = None, operator_name: str | None = None):
    bound_datasource_id = str(datasource_id or "").strip()
    bound_user_id = str(user_id or "system").strip() or "system"
    bound_operator_name = str(operator_name or "").strip() or None

    @tool("dsl2sql_tool")
    async def dsl2sql_tool(dsl: Any, semantic_config_id: str | None = None, user_query: str = "") -> str:
        """DSL 模式的校验工具：校验单个查询 DSL，或批量校验完整 dashboard_spec 的卡片 DSL。

        只在 DSL 模式使用；SQL 模式直接调用 semantic_query_tool 的顶层 sql，不调用本工具。

        看板场景必须把包含 dashboard_name 和 cards 的完整 dashboard_spec
        作为一次 dsl 传入；每张卡片必须有 semantic_config_id，或由旧规格顶层字段提供默认值。
        工具会返回 mode=batch、valid_count、warnings 和逐卡 panels。
        不要为看板中的每张卡片分别调用本工具。
        """
        try:
            if not bound_datasource_id:
                return "dsl2sql_tool 执行失败: 当前会话未绑定数据源"
            effective_semantic_config_id = str(semantic_config_id or "").strip() or None
            dsl = normalize_dsl_payload(dsl)
            if isinstance(dsl, dict) and isinstance(dsl.get("cards"), list):
                result = await validate_dashboard_spec_payload(
                    dashboard_spec=dsl,
                    datasource_id=bound_datasource_id,
                    semantic_config_id=effective_semantic_config_id,
                    user_query=user_query or None,
                    user_id=bound_user_id,
                    username=bound_operator_name,
                )
                return "[dsl2sql_tool]\n" + json_dumps({
                    "success": bool(result.get("valid")),
                    "mode": "batch",
                    "datasource_id": bound_datasource_id,
                    "semantic_config_id": effective_semantic_config_id,
                    "semantic_config_ids": result.get("semantic_config_ids") or [],
                    "valid_count": result.get("valid_count") or 0,
                    "warnings": result.get("warnings") or [],
                    "panels": result.get("panels") or [],
                })
            if isinstance(dsl, list):
                if not effective_semantic_config_id and not any(
                    isinstance(card, dict) and str(card.get("semantic_config_id") or "").strip()
                    for card in dsl
                ):
                    return "dsl2sql_tool 执行失败: 批量看板卡片缺少 semantic_config_id，请先调用 semantic_search 搜索语义模型"
                result = await validate_dashboard_spec_payload(
                    dashboard_spec={"cards": dsl},
                    datasource_id=bound_datasource_id,
                    semantic_config_id=effective_semantic_config_id,
                    user_query=user_query or None,
                    user_id=bound_user_id,
                    username=bound_operator_name,
                )
                return "[dsl2sql_tool]\n" + json_dumps({
                    "success": bool(result.get("valid")),
                    "mode": "batch",
                    "datasource_id": bound_datasource_id,
                    "semantic_config_id": effective_semantic_config_id,
                    "semantic_config_ids": result.get("semantic_config_ids") or [],
                    "valid_count": result.get("valid_count") or 0,
                    "warnings": result.get("warnings") or [],
                    "panels": result.get("panels") or [],
                })
            if not isinstance(dsl, dict):
                return "dsl2sql_tool 执行失败: dsl 必须是对象或数组"
            if not effective_semantic_config_id:
                return "dsl2sql_tool 执行失败: 缺少 semantic_config_id，请先调用 semantic_search 搜索语义模型后显式传入语义模型ID"
            semantic_config = await load_semantic_model(
                bound_datasource_id,
                effective_semantic_config_id,
                user_id=bound_user_id,
                mode="full",
            )
            payload, warnings = remove_mixed_raw_metrics_for_aggregate_dsl(dsl or {}, semantic_config)
            if payload.get("compare"):
                return "[dsl2sql_tool]\n" + json_dumps({
                    "success": False,
                    "mode": "single",
                    "datasource_id": bound_datasource_id,
                    "semantic_config_id": effective_semantic_config_id,
                    "dsl": payload,
                    "sql": None,
                    "error": "错误：DSL2SQL 不再支持 compare 同比/环比；请拆成基础 DSL 查询；复杂同比/环比请使用智能分析",
                    "warnings": warnings,
                })
            result = compile_semantic_ai_dsl(
                semantic_config,
                payload,
                options=SemanticCompileOptions(user_query=user_query or None),
            )
            return "[dsl2sql_tool]\n" + json_dumps({
                "success": bool(result.get("success")),
                "mode": "single",
                "datasource_id": bound_datasource_id,
                "semantic_config_id": effective_semantic_config_id,
                "dsl": payload,
                "sql": result.get("sql"),
                "error": result.get("error"),
                "warnings": warnings,
            })
        except Exception as exc:
            logger.error("dsl2sql_tool 执行失败: %s", exc, exc_info=True)
            return f"dsl2sql_tool 执行失败: {exc}"

    return dsl2sql_tool
