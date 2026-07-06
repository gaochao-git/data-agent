"""DataAnalyze dsl2sql_tool."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from utils.logger import get_logger
from ...services.dashboard_spec_service import (
    get_dashboard_spec_semantic_config,
    remove_mixed_raw_metrics_for_aggregate_dsl,
    validate_dashboard_spec_payload,
)
from ...services.semantic_sql_generator import SemanticSQLGenerator
from .common import json_dumps, normalize_json_like_payload

logger = get_logger(__name__)


def normalize_dsl_payload(value: Any) -> Any:
    """兼容模型把 DSL 作为 JSON 字符串或二次转义字符串传入。"""
    return normalize_json_like_payload(value)


def create_dsl2sql_tool(datasource_id: str | None = None, semantic_config_id: str | None = None, user_id: str | None = None, operator_name: str | None = None):
    bound_datasource_id = str(datasource_id or "").strip()
    bound_semantic_config_id = str(semantic_config_id or "").strip() or None
    bound_user_id = str(user_id or "system").strip() or "system"
    bound_operator_name = str(operator_name or "").strip() or None

    @tool("dsl2sql_tool")
    async def dsl2sql_tool(dsl: Any, user_query: str = "", semantic_config_id: str | None = None) -> str:
        """使用与智能问数一致的语义层规则校验单个 DSL 或批量卡片 DSL，并转换为 SQL。"""
        try:
            if not bound_datasource_id:
                return "dsl2sql_tool 执行失败: 当前会话未绑定数据源"
            effective_semantic_config_id = str(semantic_config_id or bound_semantic_config_id or "").strip() or None
            if not effective_semantic_config_id:
                return "dsl2sql_tool 执行失败: 缺少指标集ID，请先调用 select_semantic_config_tool 或手动选择指标集"
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
                    "valid_count": result.get("valid_count") or 0,
                    "warnings": result.get("warnings") or [],
                    "panels": result.get("panels") or [],
                })
            if isinstance(dsl, list):
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
                    "valid_count": result.get("valid_count") or 0,
                    "warnings": result.get("warnings") or [],
                    "panels": result.get("panels") or [],
                })
            if not isinstance(dsl, dict):
                return "dsl2sql_tool 执行失败: dsl 必须是对象或数组"
            semantic_config = await get_dashboard_spec_semantic_config(bound_datasource_id, effective_semantic_config_id, user_id=bound_user_id)
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
            generator = SemanticSQLGenerator(semantic_config)
            result = generator.generate_sql(
                metrics=payload.get("metrics") or [],
                dimensions=payload.get("dimensions") or [],
                filters=payload.get("filters") or [],
                having=payload.get("having") or [],
                order_by=payload.get("order_by") or [],
                limit=payload.get("limit"),
                offset=payload.get("offset"),
                user_query=user_query or None,
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
