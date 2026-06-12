"""
格式化工具

提供语义查询参数的格式化功能。
"""

from typing import List, Dict, Optional


def format_params_for_display(
    metrics: List[str],
    dimensions: List[str],
    filters: List[Dict],
    having: List[Dict],
    order_by: List[Dict] = None,
    limit: int = None,
    offset: int = None
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
        filter_displays = []
        for f in filters:
            dim = f.get('dimension', '')
            op = f.get('operator', '=')
            val = f.get('value', '')
            filter_displays.append(f"{dim} {op} {val}")
        params_display.append(f"**数据筛选**: {', '.join(filter_displays)}")

    if having:
        having_displays = []
        for h in having:
            metric = h.get('metric', '')
            op = h.get('operator', '>')
            val = h.get('value', '')
            having_displays.append(f"{metric} {op} {val}")
        params_display.append(f"**分组过滤**: {', '.join(having_displays)}")

    if order_by:
        order_displays = []
        for o in order_by:
            field = o.get('field', '')
            direction = o.get('direction', 'DESC').upper()
            direction_text = '降序' if direction == 'DESC' else '升序'
            order_displays.append(f"{field} {direction_text}")
        params_display.append(f"**排序**: {', '.join(order_displays)}")

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


def format_error_message(error: str, context: Optional[str] = None) -> str:
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
