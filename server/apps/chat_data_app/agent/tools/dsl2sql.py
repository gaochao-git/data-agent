"""DataAnalyze dsl2sql_tool."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from apps.chat_data_app.services.dashboard.spec_service import (
    remove_mixed_raw_metrics_for_aggregate_dsl,
    validate_dashboard_spec_payload,
)
from apps.chat_data_app.services.runtime_config import SEMANTIC_MODEL_DISCOVERY_HINT
from apps.chat_data_app.services.semantic.run.semantic_model_repository import load_semantic_model
from apps.chat_data_app.services.semantic.run.semantic_query_service import compile_semantic_ai_dsl
from .common import normalize_json_like_payload, tool_result_payload



def normalize_dsl_payload(value: Any) -> Any:
    """兼容模型把 DSL 作为 JSON 字符串或二次转义字符串传入。"""
    return normalize_json_like_payload(value)


def _batch_result_payload(
    result: dict[str, Any],
    datasource_id: str,
    semantic_config_id: str | None,
) -> dict[str, Any]:
    """dashboard_spec 批量校验的统一结果结构（字段顺序即输出顺序）。"""
    return {
        "success": bool(result.get("valid")),
        "mode": "batch",
        "datasource_id": datasource_id,
        "semantic_config_id": semantic_config_id,
        "semantic_config_ids": result.get("semantic_config_ids") or [],
        "valid_count": result.get("valid_count") or 0,
        "warnings": result.get("warnings") or [],
        "panels": result.get("panels") or [],
    }


def _single_result_payload(
    *,
    datasource_id: str,
    semantic_config_id: str | None,
    dsl: Any,
    sql: Any,
    error: Any,
    warnings: list[Any],
    success: bool,
) -> dict[str, Any]:
    """单条 DSL 校验的统一结果结构（字段顺序即输出顺序）。"""
    return {
        "success": success,
        "mode": "single",
        "datasource_id": datasource_id,
        "semantic_config_id": semantic_config_id,
        "dsl": dsl,
        "sql": sql,
        "error": error,
        "warnings": warnings,
    }


def create_dsl2sql_tool(datasource_id: str | None = None, user_id: str | None = None, operator_name: str | None = None):
    bound_datasource_id = str(datasource_id or "").strip()
    bound_user_id = str(user_id or "system").strip() or "system"
    bound_operator_name = str(operator_name or "").strip() or None

    @tool("dsl2sql_tool", parse_docstring=True)
    async def dsl2sql_tool(dsl: Any, semantic_config_id: str | None = None, intent: str = "") -> str:
        """DSL 模式的校验工具：校验单个查询 DSL，或批量校验完整 dashboard_spec 的卡片 DSL。

        只在 DSL 模式使用；SQL 模式直接调用 semantic_query_tool 的顶层 sql，不调用本工具。

        看板场景必须把包含 dashboard_name 和 cards 的完整 dashboard_spec
        作为一次 dsl 传入；每张卡片必须有 semantic_config_id，或由旧规格顶层字段提供默认值。
        工具会返回 mode=batch、valid_count、warnings 和逐卡 panels。
        不要为看板中的每张卡片分别调用本工具。

        Args:
            dsl: 单个查询 DSL 对象，或包含 dashboard_name 与 cards 的完整 dashboard_spec。
            semantic_config_id: 语义模型配置 ID；单个卡片未携带时的默认值。
            intent: 一句中文说明本次调用目的（不超过50字，写目的不写动作），面向用户展示。
        """
        if not bound_datasource_id:
            raise ValueError("当前会话未绑定数据源")
        effective_semantic_config_id = str(semantic_config_id or "").strip() or None
        dsl = normalize_dsl_payload(dsl)
        if isinstance(dsl, dict) and isinstance(dsl.get("cards"), list):
            result = await validate_dashboard_spec_payload(
                dashboard_spec=dsl,
                datasource_id=bound_datasource_id,
                semantic_config_id=effective_semantic_config_id,
                user_id=bound_user_id,
                username=bound_operator_name,
            )
            return tool_result_payload(
                "dsl2sql_tool",
                _batch_result_payload(result, bound_datasource_id, effective_semantic_config_id),
            )
        if isinstance(dsl, list):
            if not effective_semantic_config_id and not any(
                isinstance(card, dict) and str(card.get("semantic_config_id") or "").strip()
                for card in dsl
            ):
                raise ValueError(f"批量看板卡片缺少 semantic_config_id，请先{SEMANTIC_MODEL_DISCOVERY_HINT}")
            result = await validate_dashboard_spec_payload(
                dashboard_spec={"cards": dsl},
                datasource_id=bound_datasource_id,
                semantic_config_id=effective_semantic_config_id,
                user_id=bound_user_id,
                username=bound_operator_name,
            )
            return tool_result_payload(
                "dsl2sql_tool",
                _batch_result_payload(result, bound_datasource_id, effective_semantic_config_id),
            )
        if not isinstance(dsl, dict):
            raise ValueError("dsl 必须是对象或数组")
        if not effective_semantic_config_id:
            raise ValueError(f"缺少 semantic_config_id，请先{SEMANTIC_MODEL_DISCOVERY_HINT}后显式传入语义模型ID")
        semantic_config = await load_semantic_model(
            bound_datasource_id,
            effective_semantic_config_id,
            user_id=bound_user_id,
            mode="full",
        )
        payload, warnings = remove_mixed_raw_metrics_for_aggregate_dsl(dsl or {}, semantic_config)
        if payload.get("compare"):
            return tool_result_payload(
                "dsl2sql_tool",
                _single_result_payload(
                    datasource_id=bound_datasource_id,
                    semantic_config_id=effective_semantic_config_id,
                    dsl=payload,
                    sql=None,
                    error="错误：DSL2SQL 不再支持 compare 同比/环比；请拆成基础 DSL 查询；复杂同比/环比请使用智能分析",
                    warnings=warnings,
                    success=False,
                ),
            )
        result = compile_semantic_ai_dsl(
            semantic_config,
            payload,
        )
        return tool_result_payload(
            "dsl2sql_tool",
            _single_result_payload(
                datasource_id=bound_datasource_id,
                semantic_config_id=effective_semantic_config_id,
                dsl=payload,
                sql=result.get("sql"),
                error=result.get("error"),
                warnings=warnings,
                success=bool(result.get("success")),
            ),
        )

    return dsl2sql_tool
