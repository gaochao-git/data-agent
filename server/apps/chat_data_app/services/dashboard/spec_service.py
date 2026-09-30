"""AI 看板规格校验、SQL 生成与写入服务。"""

from __future__ import annotations

import inspect
from datetime import datetime
from typing import Any

from utils.logger import get_logger

from ...chart_types import get_chart_type_label, is_supported_chart_type_input, normalize_chart_type
from ..datasource.manager import get_datasource_manager
from ..semantic.core.query_plan import is_composite_metric, is_derived_metric
from ..semantic.run.semantic_config_service import get_semantic_config_service
from ..semantic.run.semantic_model_repository import load_semantic_model
from ..semantic.run.semantic_query_service import (
    SemanticAIQueryAdapter,
    SemanticCompileOptions,
    compile_semantic_ai_dsl,
)
from ..setting_service import get_runtime_setting
from ..sql_run.executor import get_executor, resolve_query_timeout
from .service import PanelWritePayload, get_dashboard_service

logger = get_logger(__name__)

DASHBOARD_ALLOWED_KPI_ICONS = {
    "fa-users",
    "fa-user",
    "fa-yen-sign",
    "fa-dollar-sign",
    "fa-coins",
    "fa-chart-line",
    "fa-percent",
    "fa-file-invoice",
    "fa-shopping-cart",
    "fa-heart",
    "fa-eye",
    "fa-clock",
    "fa-database",
    "fa-server",
    "fa-bolt",
}
DASHBOARD_ALLOWED_STYLE_COLORS = {
    "#00e5ff",
    "#7c4dff",
    "#00dc75",
    "#ffdc3d",
    "#ff3d00",
    "#1677ff",
    "#36cfc9",
    "#52c41a",
    "#faad14",
    "#f5222d",
    "#722ed1",
    "#eb2f96",
    "#8c8c8c",
}
DASHBOARD_ALLOWED_LINE_STYLES = {"smooth", "straight", "smooth-no-fill", "straight-no-fill"}


def _truncate_text(value: str, max_length: int) -> str:
    """裁剪文本长度并去除首尾空白。"""
    text = (value or "").strip()
    if not text:
        return ""
    return text[:max_length].strip()


def normalize_dashboard_spec_chart_type(chart_type: Any) -> str:
    """规整 AI 看板卡片图表类型。"""
    return normalize_chart_type(chart_type)


def _normalize_dimension_dict(item: dict[str, Any]) -> dict[str, Any] | None:
    """规整单个 dict 形态维度；无有效名称返回 None。"""
    name = str(item.get("name") or item.get("dimension") or "").strip()
    if not name:
        return None
    next_item: dict[str, Any] = {"name": name}
    time_grain = str(item.get("time_grain") or item.get("grain") or "").strip()
    if time_grain:
        next_item["time_grain"] = time_grain
    return next_item


def normalize_dashboard_spec_dimensions(dimensions: Any) -> list[Any]:
    """规整 AI 看板规格中的维度参数。"""
    result: list[Any] = []
    for item in dimensions or []:
        if isinstance(item, dict):
            normalized = _normalize_dimension_dict(item)
            if normalized is not None:
                result.append(normalized)
        else:
            text = str(item or "").strip()
            if text:
                result.append(text)
    return result


def normalize_dashboard_spec_metrics(metrics: Any) -> list[str]:
    """规整 AI 看板规格中的指标参数，兼容 name/metric/field 对象写法。"""
    result: list[str] = []
    for item in metrics or []:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("metric") or item.get("field") or "").strip()
        else:
            name = str(item or "").strip()
        if name:
            result.append(name)
    return result


def _normalize_filter_node(node: Any) -> Any:
    """兼容模型常见的 field/op 写法，统一为语义层过滤格式。"""
    if not isinstance(node, dict):
        return node
    if "and" in node:
        return {"and": [_normalize_filter_node(item) for item in node.get("and") or [] if isinstance(item, dict)]}
    if "or" in node:
        return {"or": [_normalize_filter_node(item) for item in node.get("or") or [] if isinstance(item, dict)]}
    normalized = dict(node)
    if "dimension" not in normalized and normalized.get("field"):
        normalized["dimension"] = normalized.get("field")
    if "operator" not in normalized and normalized.get("op"):
        normalized["operator"] = normalized.get("op")
    normalized.pop("field", None)
    normalized.pop("op", None)
    return normalized


def normalize_dashboard_spec_filters(filters: Any) -> list[dict[str, Any]]:
    """规整 AI 看板规格中的 filters 参数。"""
    if not isinstance(filters, list):
        return []
    return [_normalize_filter_node(item) for item in filters if isinstance(item, dict)]


def normalize_dashboard_spec_having(having: Any) -> list[dict[str, Any]]:
    """规整 AI 看板规格中的 having 参数。"""
    if not isinstance(having, list):
        return []
    result: list[dict[str, Any]] = []
    for item in having:
        if not isinstance(item, dict):
            continue
        normalized = dict(item)
        if "metric" not in normalized and normalized.get("field"):
            normalized["metric"] = normalized.get("field")
        if "operator" not in normalized and normalized.get("op"):
            normalized["operator"] = normalized.get("op")
        normalized.pop("field", None)
        normalized.pop("op", None)
        result.append(normalized)
    return result


def normalize_dashboard_spec_order_by(order_by: Any) -> list[dict[str, Any]]:
    """规整 AI 看板规格中的 order_by 参数。"""
    if not isinstance(order_by, list):
        return []
    result: list[dict[str, Any]] = []
    for item in order_by:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or item.get("metric") or item.get("dimension") or "").strip()
        if not field:
            continue
        direction = str(item.get("direction") or item.get("order") or "DESC").strip().upper()
        result.append({"field": field, "direction": direction})
    return result


def normalize_semantic_dsl_payload(dsl: dict[str, Any]) -> dict[str, Any]:
    """规整模型生成 DSL，使其符合智能问数语义工具格式。"""
    payload = dict(dsl or {})
    payload["metrics"] = normalize_dashboard_spec_metrics(payload.get("metrics") or [])
    payload["dimensions"] = normalize_dashboard_spec_dimensions(payload.get("dimensions") or [])
    payload["filters"] = normalize_dashboard_spec_filters(payload.get("filters") or [])
    payload["having"] = normalize_dashboard_spec_having(payload.get("having") or [])
    payload["order_by"] = normalize_dashboard_spec_order_by(payload.get("order_by") or [])
    payload["limit"] = payload.get("limit")
    payload["offset"] = payload.get("offset")
    return payload


def _is_aggregate_metric(metric: dict[str, Any]) -> bool:
    # 派生指标编译时展开为基础原子指标的聚合表达式；复合指标代入成员聚合表达式，均按聚合指标处理
    if is_derived_metric(metric) or is_composite_metric(metric):
        return True
    expression = str(metric.get("expression") or "").strip().upper()
    return expression.startswith(("COUNT(", "SUM(", "AVG(", "MAX(", "MIN("))


def _selected_semantic_metrics(
    metrics: list[Any], semantic_config: dict[str, Any]
) -> tuple[dict[Any, dict[str, Any]], list[Any]]:
    """按名字索引语义指标定义，并筛出 DSL 选中的那些。"""
    metrics_map = {item.get("name"): item for item in semantic_config.get("metrics") or [] if item.get("name")}
    selected = [metrics_map.get(name) for name in metrics if name in metrics_map]
    return metrics_map, selected


def _aggregate_metric_names(metrics: list[Any], metrics_map: dict[Any, dict[str, Any]]) -> list[Any]:
    """metrics 中聚合指标的名字列表（保持原顺序）。"""
    return [name for name in metrics if metrics_map.get(name) and _is_aggregate_metric(metrics_map[name])]


def remove_mixed_raw_metrics_for_aggregate_dsl(
    dsl: dict[str, Any], semantic_config: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """聚合 DSL 中出现原始值和聚合指标混用时，优先保留聚合指标。"""
    payload = normalize_semantic_dsl_payload(dsl)
    metrics = payload.get("metrics") or []
    metrics_map, selected = _selected_semantic_metrics(metrics, semantic_config)
    has_agg = any(item and _is_aggregate_metric(item) for item in selected)
    has_raw = any(item and not _is_aggregate_metric(item) for item in selected)
    if not (has_agg and has_raw):
        return payload, []
    aggregate_metric_names = _aggregate_metric_names(metrics, metrics_map)
    removed = [name for name in metrics if name not in aggregate_metric_names]
    payload["metrics"] = aggregate_metric_names
    return payload, [f"已移除聚合卡片中不能混用的原始值指标：{', '.join(removed)}"]


async def get_dashboard_spec_semantic_config(
    datasource_id: str, semantic_config_id: str | None, user_id: str | None = None
) -> dict[str, Any]:
    """读取看板 DSL 校验和 SQL 生成所需语义配置。"""
    try:
        return await load_semantic_model(
            datasource_id,
            semantic_config_id,
            user_id=user_id,
            mode="full",
            manager=get_datasource_manager(),
            semantic_service=get_semantic_config_service(),
        )
    except ValueError as exc:
        if str(exc) == "缺少语义模型ID":
            raise ValueError("缺少语义模型ID，无法生成看板 SQL") from exc
        if str(exc) == "未找到可用语义配置":
            raise ValueError("未找到可用语义配置，无法生成看板 SQL") from exc
        raise


def resolve_dashboard_card_semantic_config_id(
    card: dict[str, Any],
    default_semantic_config_id: str | None = None,
) -> str:
    """解析卡片语义模型；新规格优先使用卡片级 ID，旧规格回退到顶层默认值。"""
    card_id = str((card or {}).get("semantic_config_id") or "").strip()
    return card_id or str(default_semantic_config_id or "").strip()


def build_panel_sql_from_dashboard_card(
    card: dict[str, Any],
    generator: SemanticAIQueryAdapter,
    warnings: list[str] | None = None,
    max_rows: int | None = None,
) -> str:
    """将看板卡片 DSL 转为面板 SQL。

    卡片字段合并走 `_merge_card_dsl_payload` 单一入口；混合指标剔除的警告通过
    `warnings` 列表输出（传 None 表示不收集）。预检（build_dashboard_panel_plans）
    与 SQL 生成共用同一份合并结果，避免两套合并逻辑产生口径分歧。
    max_rows 为行数上限热值（async 调用方从配置变量表解析后传入；未传走适配器兜底）。
    """
    dsl = card.get("dsl") if isinstance(card.get("dsl"), dict) else {}
    if dsl.get("compare") or card.get("compare"):
        raise ValueError("DSL2SQL 不再支持 compare 同比/环比；请拆成基础 DSL 查询；复杂同比/环比请使用智能分析")
    payload = _merge_card_dsl_payload(dsl, card)
    payload, normalize_warnings = remove_mixed_raw_metrics_for_aggregate_dsl(
        payload, getattr(generator, "semantic_config", {})
    )
    if warnings is not None:
        warnings.extend(normalize_warnings)
    result = compile_semantic_ai_dsl(
        getattr(generator, "semantic_config", {}),
        payload,
        generator=generator,
        options=SemanticCompileOptions(max_rows=max_rows),
    )
    if not result.get("success"):
        raise ValueError(str(result.get("error") or "SQL 生成失败"))
    return str(result.get("sql") or "").strip()


def _merge_card_dsl_payload(dsl: dict[str, Any], card: dict[str, Any]) -> dict[str, Any]:
    """卡片级字段兜底合并的唯一入口：dsl 字段缺省时回退 card 顶层字段（含 limit/offset）。"""
    return {
        **dsl,
        "metrics": dsl.get("metrics") or card.get("metrics") or [],
        "dimensions": dsl.get("dimensions") or card.get("dimensions") or [],
        "filters": dsl.get("filters") or card.get("filters") or [],
        "having": dsl.get("having") or card.get("having") or [],
        "order_by": dsl.get("order_by") or card.get("order_by") or [],
        "limit": dsl.get("limit") if dsl.get("limit") is not None else card.get("limit"),
        "offset": dsl.get("offset") if dsl.get("offset") is not None else card.get("offset"),
    }


def validate_dashboard_card_structure(card: dict[str, Any], index: int) -> list[str]:
    """严格校验看板卡片结构，避免模型漏掉布局、图表类型或 DSL。"""
    errors: list[str] = []
    title = card.get("title") or f"卡片{index + 1}"
    if not is_supported_chart_type_input(card.get("chart_type")):
        errors.append(f"卡片「{title}」chart_type 缺失或不支持")
    dsl = card.get("dsl")
    if not isinstance(dsl, dict):
        errors.append(f"卡片「{title}」缺少 dsl 对象")
    elif dsl.get("compare") or card.get("compare"):
        errors.append(f"卡片「{title}」不支持 compare 同比/环比 SQL；请拆成基础 DSL 查询；复杂同比/环比请使用智能分析")
    layout = card.get("layout")
    if not isinstance(layout, dict):
        errors.append(f"卡片「{title}」缺少 layout 对象")
        return errors
    errors.extend(_validate_card_layout(title, layout))
    return errors


def _validate_card_layout(title: str, layout: dict[str, Any]) -> list[str]:
    """校验卡片 layout 数值合法性；字段缺失或不合法时不做范围检查。"""
    errors: list[str] = []
    values: dict[str, int] = {}
    for key in ("x", "y", "w", "h"):
        value = layout.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"卡片「{title}」layout.{key} 必须是数字")
            continue
        if int(value) != value:
            errors.append(f"卡片「{title}」layout.{key} 必须是整数")
            continue
        values[key] = int(value)
    if len(values) != 4:
        return errors
    if values["x"] < 0 or values["x"] > 11:
        errors.append(f"卡片「{title}」layout.x 必须在 0-11 之间")
    if values["y"] < 0:
        errors.append(f"卡片「{title}」layout.y 必须大于等于 0")
    if values["w"] < 1 or values["w"] > 12:
        errors.append(f"卡片「{title}」layout.w 必须在 1-12 之间")
    if values["h"] < 1:
        errors.append(f"卡片「{title}」layout.h 必须大于等于 1")
    if values["x"] + values["w"] > 12:
        errors.append(f"卡片「{title}」layout.x + layout.w 不能超过 12")
    return errors


def _validate_spec_cards(cards: list[Any], default_semantic_config_id: str) -> list[str]:
    """逐卡校验结构，并检查卡片级语义模型可解析。"""
    errors: list[str] = []
    for index, card in enumerate(cards[:12]):
        if not isinstance(card, dict):
            errors.append(f"卡片{index + 1} 必须是对象")
            continue
        if not resolve_dashboard_card_semantic_config_id(card, default_semantic_config_id):
            errors.append(f"卡片「{card.get('title') or index + 1}」缺少 semantic_config_id")
        errors.extend(validate_dashboard_card_structure(card, index))
    return errors


def validate_dashboard_spec_structure(
    dashboard_spec: dict[str, Any],
    default_semantic_config_id: str | None = None,
) -> list[str]:
    """严格校验看板规格结构。"""
    errors: list[str] = []
    spec = dashboard_spec or {}
    if not str(spec.get("dashboard_name") or "").strip():
        errors.append("dashboard_name 必填")
    cards = spec.get("cards") if isinstance(spec.get("cards"), list) else []
    if not cards:
        errors.append("cards 必须是非空数组")
        return errors
    default_semantic_config_id = (
        str(spec.get("semantic_config_id") or "").strip() or str(default_semantic_config_id or "").strip()
    )
    has_card_semantic_config_id = any(
        isinstance(card, dict) and str(card.get("semantic_config_id") or "").strip() for card in cards[:12]
    )
    if not default_semantic_config_id and not has_card_semantic_config_id:
        errors.append("semantic_config_id 必填：请在每张卡片中指定，或提供旧规格的顶层默认值")
    errors.extend(_validate_spec_cards(cards, default_semantic_config_id))
    return errors


def _resolve_panel_generator(
    generators: dict[str, SemanticAIQueryAdapter],
    semantic_config_generators: dict[str, SemanticAIQueryAdapter] | None,
    sql_generator: SemanticAIQueryAdapter | None,
    semantic_config_id: str,
) -> SemanticAIQueryAdapter:
    generator = generators.get(semantic_config_id) if semantic_config_generators is not None else sql_generator
    if generator is None:
        raise ValueError("缺少 semantic_config_id 对应的语义模型")
    return generator


def _failed_panel_plan(
    card: dict[str, Any], index: int, default_semantic_config_id: str | None, error: str
) -> dict[str, Any]:
    return {
        "index": index,
        "card": card,
        "sql": "",
        "chart_type": normalize_dashboard_spec_chart_type(card.get("chart_type")),
        "semantic_config_id": resolve_dashboard_card_semantic_config_id(card, default_semantic_config_id),
        "error": error,
    }


def build_dashboard_panel_plans(
    cards: list[Any],
    sql_generator: SemanticAIQueryAdapter | None = None,
    *,
    semantic_config_generators: dict[str, SemanticAIQueryAdapter] | None = None,
    default_semantic_config_id: str | None = None,
    max_rows: int | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """预生成看板面板 SQL，避免无有效卡片时先创建空看板。

    max_rows 为行数上限热值（async 调用方从配置变量表解析后传入；未传走适配器兜底）。
    """
    panel_plans: list[dict[str, Any]] = []
    warnings: list[str] = []
    generators = semantic_config_generators or {}
    for index, card in enumerate(cards[:12]):
        if not isinstance(card, dict):
            continue
        try:
            semantic_config_id = resolve_dashboard_card_semantic_config_id(card, default_semantic_config_id)
            generator = _resolve_panel_generator(
                generators, semantic_config_generators, sql_generator, semantic_config_id
            )
            card_warnings: list[str] = []
            sql = build_panel_sql_from_dashboard_card(card, generator, warnings=card_warnings, max_rows=max_rows)
            for warning in card_warnings:
                warnings.append(f"卡片「{card.get('title') or index + 1}」{warning}")
            if not sql:
                raise ValueError("SQL 为空")
            panel_plans.append(
                {
                    "index": index,
                    "card": card,
                    "sql": sql,
                    "chart_type": normalize_dashboard_spec_chart_type(card.get("chart_type")),
                    "semantic_config_id": semantic_config_id,
                    "error": "",
                }
            )
        except Exception as err:
            error = str(err) or "SQL 生成失败"
            warnings.append(f"卡片「{card.get('title') or index + 1}」创建失败：{error}")
            panel_plans.append(_failed_panel_plan(card, index, default_semantic_config_id, error))
    return panel_plans, warnings


async def execute_dashboard_panel_sql_preflight(
    datasource_id: str, sql: str, user_id: str = "system", username: str | None = None
) -> tuple[bool, str]:
    """写入看板前实际执行 SQL，避免不可执行面板进入看板。"""
    ds_manager = get_datasource_manager()
    execution_targets = await ds_manager.get_authorized_execution_datasources_async(
        datasource_id,
        None,
        user_id=user_id,
        username=username,
    )
    if not execution_targets:
        return False, "当前数据源组下没有可执行的数据源"
    execution_target = execution_targets[0]
    executor = get_executor(execution_target)
    try:
        execute_kwargs = {
            "sql": sql,
            "limit": 1,
            "timeout": min(await resolve_query_timeout(execution_target.get("source_type")), 30),
            "user_id": user_id,
            "datasource_id": datasource_id,
        }
        if "username" in inspect.signature(executor.execute).parameters:
            execute_kwargs["username"] = username
        result = await executor.execute(**execute_kwargs)
        if result.get("success"):
            return True, ""
        return False, str(result.get("error") or "SQL 预执行失败")
    except Exception as err:
        logger.error(
            "看板面板 SQL 预执行异常: datasource_id=%s sql=%s error=%s", datasource_id, sql, err, exc_info=True
        )
        return False, str(err) or "SQL 预执行异常"
    finally:
        await executor.close()


async def filter_executable_dashboard_panel_plans(
    panel_plans: list[dict[str, Any]],
    *,
    datasource_id: str,
    user_id: str = "system",
    username: str | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """标记写入前无法执行的面板 SQL，保留原卡片用于看板占位展示。"""
    marked_plans: list[dict[str, Any]] = []
    warnings: list[str] = []
    for plan in panel_plans:
        card = plan.get("card") if isinstance(plan.get("card"), dict) else {}
        if plan.get("error"):
            marked_plans.append(plan)
            continue
        ok, error = await execute_dashboard_panel_sql_preflight(
            datasource_id, str(plan.get("sql") or ""), user_id=user_id, username=username
        )
        if ok:
            marked_plans.append(plan)
        else:
            warnings.append(f"卡片「{card.get('title') or int(plan.get('index') or 0) + 1}」预执行失败：{error}")
            marked = dict(plan)
            marked["error"] = error or "SQL 预执行失败"
            marked_plans.append(marked)
    return marked_plans, warnings


def _collect_spec_semantic_config_ids(cards: list[Any], default_semantic_config_id: str | None) -> list[str]:
    semantic_config_ids: list[str] = []
    for card in cards[:12]:
        if not isinstance(card, dict):
            continue
        config_id = resolve_dashboard_card_semantic_config_id(card, default_semantic_config_id)
        if config_id and config_id not in semantic_config_ids:
            semantic_config_ids.append(config_id)
    return semantic_config_ids


async def _build_spec_generators(
    datasource_id: str,
    semantic_config_ids: list[str],
    user_id: str,
) -> dict[str, SemanticAIQueryAdapter]:
    semantic_config_generators: dict[str, SemanticAIQueryAdapter] = {}
    # 行数上限热值：适配器不再落回导入期 DEFAULT_ROW_LIMIT（否则后台改表对本链路不生效，
    # 与 CHATDATA_ROW_LIMIT 的 EFFECT_IMMEDIATE 标注矛盾）
    row_limit = await get_runtime_setting("CHATDATA_ROW_LIMIT")
    for config_id in semantic_config_ids:
        semantic_config = await get_dashboard_spec_semantic_config(datasource_id, config_id, user_id=user_id)
        semantic_config_generators[config_id] = SemanticAIQueryAdapter(semantic_config, max_rows=row_limit)
    return semantic_config_generators


def _summarize_dashboard_validation(
    panel_plans: list[dict[str, Any]],
    warnings: list[str],
    semantic_config_ids: list[str],
) -> dict[str, Any]:
    return {
        "valid": any(not plan.get("error") for plan in panel_plans),
        "valid_count": len([plan for plan in panel_plans if not plan.get("error")]),
        "semantic_config_ids": semantic_config_ids,
        "warnings": warnings,
        "panels": [
            {
                "index": int(plan["index"]),
                "title": str(plan["card"].get("title") or f"分析卡片{int(plan['index']) + 1}"),
                "chart_type": plan["chart_type"],
                "semantic_config_id": plan.get("semantic_config_id") or "",
                "sql": plan["sql"],
                "error": plan.get("error") or "",
            }
            for plan in panel_plans
        ],
    }


async def validate_dashboard_spec_payload(
    *,
    dashboard_spec: dict[str, Any],
    datasource_id: str,
    semantic_config_id: str | None = None,
    execute_sql: bool = False,
    user_id: str = "system",
    username: str | None = None,
) -> dict[str, Any]:
    """校验看板规格并返回每张有效卡片的 SQL。"""
    datasource_id = str(datasource_id or "").strip()
    if not datasource_id:
        raise ValueError("缺少数据源ID，无法校验看板")
    spec = dashboard_spec or {}
    if spec.get("need_clarify"):
        raise ValueError("当前方案仍需补充确认，暂不能写入看板")
    cards = spec.get("cards") if isinstance(spec.get("cards"), list) else []
    if not cards:
        raise ValueError("看板规格缺少卡片，无法创建看板")
    default_semantic_config_id = str(semantic_config_id or spec.get("semantic_config_id") or "").strip() or None
    structure_errors = validate_dashboard_spec_structure(spec, default_semantic_config_id=default_semantic_config_id)
    if structure_errors:
        return {
            "valid": False,
            "valid_count": 0,
            "warnings": structure_errors,
            "panels": [],
        }
    semantic_config_ids = _collect_spec_semantic_config_ids(cards, default_semantic_config_id)
    semantic_config_generators = await _build_spec_generators(datasource_id, semantic_config_ids, user_id)
    panel_plans, warnings = build_dashboard_panel_plans(
        cards,
        semantic_config_generators=semantic_config_generators,
        default_semantic_config_id=default_semantic_config_id,
        max_rows=await get_runtime_setting("CHATDATA_ROW_LIMIT"),
    )
    if execute_sql and panel_plans:
        panel_plans, execution_warnings = await filter_executable_dashboard_panel_plans(
            panel_plans,
            datasource_id=datasource_id,
            user_id=user_id,
            username=username,
        )
        warnings.extend(execution_warnings)
    return _summarize_dashboard_validation(panel_plans, warnings, semantic_config_ids)


def _chart_type_label(chart_type: str) -> str:
    """将图表类型转换为中文标签。"""
    return get_chart_type_label(chart_type)


def _build_panel_name_fallback(chart_type: str, max_length: int = 24) -> str:
    """构建兜底面板标题。"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    fallback = f"{_chart_type_label(chart_type)}_{timestamp}"
    return _truncate_text(fallback, max_length)


def _coerce_int(value: Any, default: int) -> int:
    """把模型输出的布局数字规整为整数。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp_int(value: int, min_value: int, max_value: int) -> int:
    """限制整数范围，避免非法布局写入看板。"""
    return max(min_value, min(max_value, value))


def normalize_dashboard_card_layout(card: dict[str, Any], index: int, chart_type: str) -> dict[str, int]:
    """规整 AI 看板规格中的卡片布局，未指定时保持原有两列默认布局。"""
    layout = card.get("layout") if isinstance(card.get("layout"), dict) else {}
    default_h = 3 if chart_type == "kpi" else 4
    x = _coerce_int(layout.get("x", card.get("layout_x", (index % 2) * 6)), (index % 2) * 6)
    y = _coerce_int(layout.get("y", card.get("layout_y", (index // 2) * 4)), (index // 2) * 4)
    w = _coerce_int(layout.get("w", card.get("layout_w", 6)), 6)
    h = _coerce_int(layout.get("h", card.get("layout_h", default_h)), default_h)
    w = _clamp_int(w, 1, 12)
    x = _clamp_int(x, 0, 11)
    if x + w > 12:
        w = 12 - x
    return {
        "x": x,
        "y": max(0, y),
        "w": w,
        "h": _clamp_int(h, 1, 12),
    }


def _normalize_dashboard_style_color(value: Any) -> str | None:
    """校验模型输出的看板样式颜色。"""
    color = str(value or "").strip()
    return color if color.lower() in DASHBOARD_ALLOWED_STYLE_COLORS else None


def normalize_dashboard_card_chart_config(card: dict[str, Any]) -> dict[str, Any]:
    """提取模型可选择的前端图表样式配置。"""
    chart_config = card.get("chart_config") if isinstance(card.get("chart_config"), dict) else {}
    result: dict[str, Any] = {
        "purpose": card.get("purpose") or "",
        "diagnosis": card.get("diagnosis") or "",
    }
    kpi_icon = str(chart_config.get("kpiIcon") or "").strip()
    if kpi_icon in DASHBOARD_ALLOWED_KPI_ICONS:
        result["kpiIcon"] = kpi_icon
    kpi_color = _normalize_dashboard_style_color(chart_config.get("kpiColor"))
    if kpi_color:
        result["kpiColor"] = kpi_color
    chart_color = _normalize_dashboard_style_color(chart_config.get("chartColor"))
    if chart_color:
        result["chartColor"] = chart_color
    line_style = str(chart_config.get("lineStyle") or "").strip()
    if line_style in DASHBOARD_ALLOWED_LINE_STYLES:
        result["lineStyle"] = line_style
    for key in ("xField", "yField", "yField2", "seriesField"):
        value = chart_config.get(key)
        if isinstance(value, str) and value.strip():
            result[key] = value.strip()
    return result


def _shorten_dashboard_panel_error(error: Any, max_length: int = 180) -> str:
    """压缩看板面板错误，避免大段堆栈进入卡片展示。"""
    text = " ".join(str(error or "").replace("\\n", " ").split())
    if not text:
        return ""
    return text[:max_length].rstrip() + ("..." if len(text) > max_length else "")


def _build_dashboard_panel_chart_config(card: dict[str, Any], error: str = "") -> dict[str, Any]:
    """构造面板图表配置，并记录无效卡片的短错误。"""
    config = normalize_dashboard_card_chart_config(card)
    short_error = _shorten_dashboard_panel_error(error)
    if short_error:
        config["creation_error"] = short_error
        config["creation_status"] = "invalid_sql"
    return config


async def _add_panel_from_card(
    service: Any,
    dashboard: dict[str, Any],
    card: dict[str, Any],
    plan: dict[str, Any] | None,
    *,
    chart_type: str,
    layout: dict[str, Any],
    panel_error: str,
    conversation_id: str | None,
    datasource_id: str,
    user_id: str,
    username: str | None,
) -> dict[str, Any] | None:
    return await service.add_panel(
        dashboard_id=dashboard["id"],
        payload=PanelWritePayload(
            name=_truncate_text(str(card.get("title") or "分析卡片"), 24) or _build_panel_name_fallback(chart_type, 24),
            sql=str((plan or {}).get("sql") or ""),
            chart_type=chart_type,
            chart_config=_build_dashboard_panel_chart_config(card, panel_error),
            layout_x=layout["x"],
            layout_y=layout["y"],
            layout_w=layout["w"],
            layout_h=layout["h"],
            source_thread_id=conversation_id,
            datasource_id=datasource_id,
        ),
        user_id=str(user_id or "system"),
        operator_name=str(username or ""),
    )


async def _create_dashboard_panels(
    service: Any,
    dashboard: dict[str, Any],
    spec: dict[str, Any],
    validation: dict[str, Any],
    *,
    conversation_id: str | None,
    datasource_id: str,
    user_id: str,
    username: str | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """按校验结果逐卡片创建面板；单卡失败记入 warnings 不中断。"""
    warnings = list(validation["warnings"])
    panels: list[dict[str, Any]] = []
    valid_sql_by_index = {int(item["index"]): item for item in validation["panels"]}
    cards = spec.get("cards") if isinstance(spec.get("cards"), list) else []
    for index, card in enumerate(cards[:12]):
        plan = valid_sql_by_index.get(index)
        if not isinstance(card, dict):
            continue
        chart_type = str((plan or {}).get("chart_type") or normalize_dashboard_spec_chart_type(card.get("chart_type")))
        layout = normalize_dashboard_card_layout(card, index, chart_type)
        panel_error = str((plan or {}).get("error") or "")
        try:
            panel = await _add_panel_from_card(
                service,
                dashboard,
                card,
                plan,
                chart_type=chart_type,
                layout=layout,
                panel_error=panel_error,
                conversation_id=conversation_id,
                datasource_id=datasource_id,
                user_id=user_id,
                username=username,
            )
            if panel:
                panels.append(panel)
        except Exception as err:
            warnings.append(f"卡片「{card.get('title') or index + 1}」创建失败：{err}")
    return panels, warnings


async def _verify_dashboard_persisted(
    service: Any,
    dashboard: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any] | None:
    """回读看板确认持久化；失败时删除并返回 None。"""
    persisted_dashboard = await service.get_dashboard(dashboard["id"])
    persisted_panels = (persisted_dashboard or {}).get("panels") or []
    if not persisted_dashboard or not persisted_panels:
        warnings.append("看板写入后回读失败，未确认数据已持久化")
        await service.delete_dashboard(dashboard["id"])
        return None
    persisted_dashboard["panels"] = persisted_panels
    return persisted_dashboard


async def create_dashboard_from_spec_payload(
    *,
    dashboard_spec: dict[str, Any],
    datasource_id: str,
    semantic_config_id: str | None = None,
    conversation_id: str | None = None,
    user_id: str = "system",
    username: str | None = None,
) -> dict[str, Any]:
    """根据 AI 看板规格创建大屏和面板。"""
    spec = dict(dashboard_spec or {})
    effective_semantic_config_id = str(semantic_config_id or spec.get("semantic_config_id") or "").strip() or None
    if effective_semantic_config_id:
        spec["semantic_config_id"] = effective_semantic_config_id
    validation = await validate_dashboard_spec_payload(
        dashboard_spec=spec,
        datasource_id=datasource_id,
        semantic_config_id=effective_semantic_config_id,
        execute_sql=False,
        user_id=user_id,
        username=username,
    )
    if not validation["valid"]:
        return {"dashboard": None, "panels": [], "warnings": validation["warnings"]}

    service = get_dashboard_service()
    dashboard_name = _truncate_text(str(spec.get("dashboard_name") or "分析看板"), 64) or "分析看板"
    dashboard_desc = _truncate_text(str(spec.get("analysis_target") or spec.get("topic") or "AI 生成分析看板"), 255)
    dashboard = await service.create_dashboard(
        name=dashboard_name,
        user_id=str(user_id or "system"),
        description=dashboard_desc,
        datasource_id=datasource_id,
        theme="light",
        operator_name=str(username or ""),
    )

    panels, warnings = await _create_dashboard_panels(
        service,
        dashboard,
        spec,
        validation,
        conversation_id=conversation_id,
        datasource_id=datasource_id,
        user_id=user_id,
        username=username,
    )

    if not panels:
        await service.delete_dashboard(dashboard["id"])
        return {"dashboard": None, "panels": [], "warnings": warnings}

    persisted_dashboard = await _verify_dashboard_persisted(service, dashboard, warnings)
    if persisted_dashboard is None:
        return {"dashboard": None, "panels": [], "warnings": warnings}

    return {
        "dashboard": persisted_dashboard,
        "panels": persisted_dashboard["panels"],
        "warnings": warnings,
    }
