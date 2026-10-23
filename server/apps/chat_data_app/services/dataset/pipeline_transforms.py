"""Dataset Pipeline transform：纯函数实现，不访问数据库，便于扩展与单测。

MVP 支持 filter / aggregate 两种 transform，新增 transform 只需：
1. 在 ``TRANSFORM_TYPES`` 注册类型名；
2. 增加 ``_normalize_xxx_config`` 与 ``_apply_xxx``；
3. 在 ``normalize_transform_config`` / ``apply_transform`` 分发。
"""

from __future__ import annotations

import operator as operator_module
from typing import Any

TRANSFORM_FILTER = "filter"
TRANSFORM_AGGREGATE = "aggregate"
TRANSFORM_TYPES = (TRANSFORM_FILTER, TRANSFORM_AGGREGATE)

FILTER_OPERATORS = ("eq", "ne", "gt", "gte", "lt", "lte", "contains")
AGGREGATE_FUNCTIONS = ("sum", "avg", "min", "max", "count")

_COMPARE_FUNCTIONS = {
    "gt": operator_module.gt,
    "gte": operator_module.ge,
    "lt": operator_module.lt,
    "lte": operator_module.le,
}


class TransformError(ValueError):
    """Transform 配置或执行错误。"""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _column_names(columns: list[dict[str, Any]] | None) -> list[str]:
    names: list[str] = []
    for item in columns or []:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name"))
        if name:
            names.append(name)
    return names


def _require_column(columns: list[dict[str, Any]] | None, value: Any, field: str) -> str:
    column = _text(value)
    if not column:
        raise TransformError(f"{field} 不能为空")
    if columns is not None and column not in _column_names(columns):
        raise TransformError(f"{field} 引用了不存在的列: {column}")
    return column


def _normalize_filter_config(config: dict[str, Any], columns: list[dict[str, Any]] | None) -> dict[str, Any]:
    raw_conditions = _as_list(config.get("conditions")) or [config]
    conditions: list[dict[str, Any]] = []
    for raw in raw_conditions:
        if not isinstance(raw, dict):
            raise TransformError("filter.conditions 必须是对象数组")
        column = _require_column(columns, raw.get("column"), "filter.column")
        operator = _text(raw.get("operator")) or "eq"
        if operator not in FILTER_OPERATORS:
            raise TransformError(f"filter.operator 不合法: {operator}")
        if "value" not in raw:
            raise TransformError("filter.value 不能为空")
        conditions.append({"column": column, "operator": operator, "value": raw.get("value")})
    if len(conditions) > 20:
        raise TransformError("filter.conditions 不能超过 20 个")
    return {"transform_type": TRANSFORM_FILTER, "conditions": conditions}


def _normalize_aggregate_config(config: dict[str, Any], columns: list[dict[str, Any]] | None) -> dict[str, Any]:
    group_by = [_require_column(columns, item, "aggregate.group_by") for item in _as_list(config.get("group_by"))]
    if len(set(group_by)) != len(group_by):
        raise TransformError("aggregate.group_by 不能重复")
    metrics: list[dict[str, str]] = []
    aliases: set[str] = set()
    for raw in _as_list(config.get("metrics")):
        if not isinstance(raw, dict):
            raise TransformError("aggregate.metrics 必须是对象数组")
        function = _text(raw.get("function")) or "count"
        if function not in AGGREGATE_FUNCTIONS:
            raise TransformError(f"aggregate.function 不合法: {function}")
        column = _require_column(columns, raw.get("column"), "aggregate.metric.column")
        alias = _text(raw.get("alias")) or f"{function}_{column}"
        if alias in aliases or alias in group_by:
            raise TransformError(f"aggregate.metric.alias 重复: {alias}")
        aliases.add(alias)
        metrics.append({"column": column, "function": function, "alias": alias})
    if not metrics:
        raise TransformError("aggregate.metrics 不能为空")
    return {"transform_type": TRANSFORM_AGGREGATE, "group_by": group_by, "metrics": metrics}


def normalize_transform_config(
    config: Any,
    columns: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """校验并规整 transform 配置；columns=None 时只做结构校验（保存定义时使用）。"""
    if not isinstance(config, dict):
        raise TransformError("transform config 必须是对象")
    transform_type = _text(config.get("transform_type"))
    if transform_type not in TRANSFORM_TYPES:
        raise TransformError(f"不支持的 transform_type: {transform_type or '(空)'}")
    if transform_type == TRANSFORM_FILTER:
        return _normalize_filter_config(config, columns)
    return _normalize_aggregate_config(config, columns)


def _scalar(value: Any) -> Any:
    """把可比较标量尽量归一为数值；不可转换时保留字符串。"""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip()
    try:
        return float(text)
    except ValueError:
        return text


def _compare(actual: Any, operator: str, expected: Any) -> bool:
    if operator == "contains":
        return str(expected if expected is not None else "") in str(actual if actual is not None else "")
    left, right = _scalar(actual), _scalar(expected)
    if operator == "eq":
        return left == right
    if operator == "ne":
        return left != right
    both_numeric = isinstance(left, (int, float)) and isinstance(right, (int, float))
    left_value = left if both_numeric else str(left)
    right_value = right if both_numeric else str(right)
    if operator in _COMPARE_FUNCTIONS:
        return bool(_COMPARE_FUNCTIONS[operator](left_value, right_value))
    raise TransformError(f"filter.operator 不合法: {operator}")


def _matches(row: dict[str, Any], conditions: list[dict[str, Any]]) -> bool:
    for condition in conditions:
        if not _compare(row.get(condition["column"]), condition["operator"], condition["value"]):
            return False
    return True


def _apply_filter(rows: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    conditions = config["conditions"]
    return [row for row in rows if _matches(row, conditions)]


def _numeric_values(values: list[Any]) -> list[float]:
    numbers: list[float] = []
    for value in values:
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float)):
            numbers.append(float(value))
            continue
        try:
            numbers.append(float(str(value).strip()))
        except ValueError:
            continue
    return numbers


def _aggregate_value(values: list[Any], function: str) -> Any:
    if function == "count":
        return len([value for value in values if value is not None])
    numbers = _numeric_values(values)
    if function == "sum":
        return sum(numbers)
    if not numbers:
        return None
    if function == "avg":
        return sum(numbers) / len(numbers)
    if function == "min":
        return min(numbers)
    return max(numbers)


def _aggregate_columns(columns: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    column_by_name = {_text(item.get("name")): item for item in columns if isinstance(item, dict)}
    result: list[dict[str, Any]] = []
    for name in config["group_by"]:
        source = column_by_name.get(name) or {}
        result.append(
            {
                "name": name,
                "display_name": _text(source.get("display_name")) or name,
                "data_type": _text(source.get("data_type")) or "string",
                "physical_column": name,
            }
        )
    for metric in config["metrics"]:
        result.append(
            {
                "name": metric["alias"],
                "display_name": metric["alias"],
                "data_type": "number",
                "physical_column": "",
            }
        )
    return result


def _apply_aggregate(
    rows: list[dict[str, Any]],
    columns: list[dict[str, Any]],
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    group_by = config["group_by"]
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in rows:
        key = tuple(row.get(name) for name in group_by)
        groups.setdefault(key, []).append(row)
    if not groups and not group_by:
        groups[()] = []
    result: list[dict[str, Any]] = []
    for key, group_rows in groups.items():
        item: dict[str, Any] = {name: key[index] for index, name in enumerate(group_by)}
        for metric in config["metrics"]:
            item[metric["alias"]] = _aggregate_value(
                [row.get(metric["column"]) for row in group_rows], metric["function"]
            )
        result.append(item)
    return result, _aggregate_columns(columns, config)


def apply_transform(
    rows: list[dict[str, Any]],
    columns: list[dict[str, Any]],
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """执行单个 transform，返回 (输出行, 输出列定义)。"""
    if config.get("transform_type") == TRANSFORM_FILTER:
        return _apply_filter(rows, config), list(columns)
    return _apply_aggregate(rows, columns, config)


def apply_transforms(
    rows: list[dict[str, Any]],
    columns: list[dict[str, Any]],
    configs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按定义顺序执行 transform 链。"""
    current_rows = rows
    current_columns = columns
    for config in configs:
        current_rows, current_columns = apply_transform(current_rows, current_columns, config)
    return current_rows, current_columns


__all__ = [
    "AGGREGATE_FUNCTIONS",
    "FILTER_OPERATORS",
    "TRANSFORM_AGGREGATE",
    "TRANSFORM_FILTER",
    "TRANSFORM_TYPES",
    "TransformError",
    "apply_transform",
    "apply_transforms",
    "normalize_transform_config",
]
