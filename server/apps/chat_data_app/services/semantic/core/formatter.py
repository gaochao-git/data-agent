"""
格式化工具

提供语义查询参数的格式化功能。
"""

from typing import Any


def _format_expression_operand(operand: Any) -> str:
    if not isinstance(operand, dict):
        return ""
    operand_type = str(operand.get("type") or "").lower()
    if operand_type in {"dimension", "metric"}:
        return str(operand.get("name") or "")
    if operand_type == "literal":
        return str(operand.get("value"))
    if operand_type == "binary":
        left = _format_expression_operand(operand.get("left"))
        right = _format_expression_operand(operand.get("right"))
        operator = str(operand.get("operator") or "")
        return f"{left} {operator} {right}".strip()
    return ""


def _format_filter_displays(filters: list[dict]) -> list[str]:
    """把过滤条件格式化为「维度 操作符 值」片段。"""
    return [f"{f.get('dimension', '')} {f.get('operator', '=')} {f.get('value', '')}" for f in filters]


def _format_having_displays(having: list[dict]) -> list[str]:
    """把 HAVING 条件格式化为片段；表达式操作数递归格式化。"""
    having_displays = []
    for h in having:
        if isinstance(h.get("left"), dict) and "right" in h:
            left = _format_expression_operand(h.get("left"))
            op = h.get("operator", ">")
            right = _format_expression_operand(h.get("right"))
            having_displays.append(f"{left} {op} {right}".strip())
            continue
        metric = h.get("metric", "")
        op = h.get("operator", ">")
        val = h.get("value", "")
        having_displays.append(f"{metric} {op} {val}")
    return having_displays


def _format_order_displays(order_by: list[dict]) -> list[str]:
    """把排序条件格式化为「字段 升序/降序」片段。"""
    order_displays = []
    for o in order_by:
        field = o.get("field", "")
        direction = o.get("direction", "DESC").upper()
        direction_text = "降序" if direction == "DESC" else "升序"
        order_displays.append(f"{field} {direction_text}")
    return order_displays


def format_params_for_display(
    metrics: list[str],
    dimensions: list[str],
    filters: list[dict],
    having: list[dict],
    order_by: list[dict] | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> str:
    """
    格式化语义参数为前端展示的文本块

    Args:
        metrics: 指标列表
        dimensions: 维度列表
        filters: 过滤条件列表
        having: HAVING 条件列表
        order_by: 排序条件列表
        limit: 限制条数
        offset: 跳过条数

    Returns:
        格式化的参数块字符串
    """
    params_display = []

    if metrics:
        metrics_display = ", ".join([f"**{m}**" for m in metrics])
        params_display.append(f"**指标**: {metrics_display}")

    if dimensions:
        dims_display = ", ".join([f"**{d}**" for d in dimensions])
        params_display.append(f"**维度**: {dims_display}")

    if filters:
        params_display.append(f"**数据筛选**: {', '.join(_format_filter_displays(filters))}")

    if having:
        params_display.append(f"**分组过滤**: {', '.join(_format_having_displays(having))}")

    if order_by:
        params_display.append(f"**排序**: {', '.join(_format_order_displays(order_by))}")

    if limit:
        params_display.append(f"**限制条数**: {limit}")

    if offset is not None:
        params_display.append(f"**跳过条数**: {offset}")

    return "\n".join(params_display) if params_display else "无参数"


def format_sql_for_display(sql: str, max_length: int = 500) -> str:
    """
    格式化 SQL 用于显示

    Args:
        sql: SQL 语句
        max_length: 最大长度

    Returns:
        格式化后的 SQL
    """
    if len(sql) > max_length:
        return sql[:max_length] + "..."
    return sql


def format_error_message(error: str, context: str | None = None) -> str:
    """
    格式化错误消息

    Args:
        error: 错误消息
        context: 上下文信息

    Returns:
        格式化后的错误消息
    """
    if context:
        return f"错误：{error}\n上下文：{context}"
    return f"错误：{error}"
