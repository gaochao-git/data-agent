"""ChatData chart type registry."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

ChartType = Literal["table", "kpi", "bar", "horizontal_bar", "line", "area", "pie", "map", "combo", "funnel", "graph"]


class ChartTypeItem(TypedDict):
    key: ChartType
    label: str
    description: str
    data_shape: str
    echarts: bool
    supports_dual_axis: bool
    supports_series_type: bool


CHART_TYPE_ITEMS: tuple[ChartTypeItem, ...] = (
    {
        "key": "table",
        "label": "表格",
        "description": "明细数据、多字段精确查看",
        "data_shape": "多列明细或聚合结果",
        "echarts": False,
        "supports_dual_axis": False,
        "supports_series_type": False,
    },
    {
        "key": "kpi",
        "label": "指标卡",
        "description": "单个或少量核心指标",
        "data_shape": "单行单值或少量指标值",
        "echarts": False,
        "supports_dual_axis": False,
        "supports_series_type": False,
    },
    {
        "key": "bar",
        "label": "柱状图",
        "description": "分类对比、排行",
        "data_shape": "分类字段 + 数值字段",
        "echarts": True,
        "supports_dual_axis": False,
        "supports_series_type": False,
    },
    {
        "key": "horizontal_bar",
        "label": "条形图",
        "description": "名称较长的分类对比或 TopN 排行",
        "data_shape": "分类字段 + 数值字段",
        "echarts": True,
        "supports_dual_axis": False,
        "supports_series_type": False,
    },
    {
        "key": "line",
        "label": "折线图",
        "description": "时间趋势、连续变化",
        "data_shape": "时间字段 + 一个或多个数值字段",
        "echarts": True,
        "supports_dual_axis": False,
        "supports_series_type": False,
    },
    {
        "key": "area",
        "label": "面积图",
        "description": "趋势规模和累计感",
        "data_shape": "时间字段 + 数值字段",
        "echarts": True,
        "supports_dual_axis": False,
        "supports_series_type": False,
    },
    {
        "key": "pie",
        "label": "饼图",
        "description": "占比分布，分类不宜过多",
        "data_shape": "分类字段 + 正数值字段",
        "echarts": True,
        "supports_dual_axis": False,
        "supports_series_type": False,
    },
    {
        "key": "map",
        "label": "地图",
        "description": "地区、城市、省份等空间分布",
        "data_shape": "地区字段 + 数值字段",
        "echarts": True,
        "supports_dual_axis": False,
        "supports_series_type": False,
    },
    {
        "key": "combo",
        "label": "柱线组合图",
        "description": "同一维度下两个量纲指标柱线组合展示",
        "data_shape": "维度字段 + 2个数值字段",
        "echarts": True,
        "supports_dual_axis": True,
        "supports_series_type": True,
    },
    {
        "key": "funnel",
        "label": "漏斗图",
        "description": "阶段转化、阶段递减、流程流失分析",
        "data_shape": "阶段字段 + 数值字段，通常按阶段顺序递减",
        "echarts": True,
        "supports_dual_axis": False,
        "supports_series_type": False,
    },
    {
        "key": "graph",
        "label": "关系图",
        "description": "实体关系、依赖网络、起点终点连接",
        "data_shape": "起点字段 + 终点字段，可选关系强度数值字段",
        "echarts": True,
        "supports_dual_axis": False,
        "supports_series_type": False,
    },
)

CHART_TYPES = tuple(item["key"] for item in CHART_TYPE_ITEMS)
CHART_TYPE_SET = set(CHART_TYPES)
CHART_TYPE_LABELS = {item["key"]: item["label"] for item in CHART_TYPE_ITEMS}
CHART_TYPES_REQUIRING_DATA: frozenset[ChartType] = frozenset({
    "kpi", "bar", "horizontal_bar", "line", "area", "pie", "map", "combo", "funnel", "graph"
})
CHART_TYPES_REQUIRING_X: frozenset[ChartType] = frozenset({
    "bar", "horizontal_bar", "line", "area", "pie", "map", "combo", "funnel", "graph"
})
CHART_TYPES_REQUIRING_Y: frozenset[ChartType] = frozenset({
    "kpi", "bar", "horizontal_bar", "line", "area", "pie", "map", "combo", "funnel", "graph"
})


def normalize_chart_type(chart_type: Any) -> ChartType:
    """Normalize chart type, falling back to table."""
    normalized = str(chart_type or "table").strip().lower()
    return normalized if normalized in CHART_TYPE_SET else "table"


def is_supported_chart_type(chart_type: Any) -> bool:
    """Return whether the normalized chart type is supported."""
    normalized = str(chart_type or "").strip().lower()
    return normalized in CHART_TYPE_SET


def is_supported_chart_type_input(chart_type: Any) -> bool:
    """Return whether the raw chart type is supported."""
    return is_supported_chart_type(chart_type)


def get_chart_type_label(chart_type: Any) -> str:
    """Return display label for a chart type."""
    return CHART_TYPE_LABELS.get(normalize_chart_type(chart_type), "图表")


def chart_type_requires_data(chart_type: Any) -> bool:
    """Return whether chart spec data rows are required."""
    return normalize_chart_type(chart_type) in CHART_TYPES_REQUIRING_DATA


def chart_type_requires_x(chart_type: Any) -> bool:
    """Return whether chart spec encoding.x is required."""
    return normalize_chart_type(chart_type) in CHART_TYPES_REQUIRING_X


def chart_type_requires_y(chart_type: Any) -> bool:
    """Return whether chart spec encoding.y is required."""
    return normalize_chart_type(chart_type) in CHART_TYPES_REQUIRING_Y


def chart_type_max_y_fields(chart_type: Any) -> int | None:
    """Return max supported encoding.y fields for a chart type, or None if unlimited."""
    if normalize_chart_type(chart_type) == "combo":
        return 2
    return None


def get_chart_type_items() -> tuple[ChartTypeItem, ...]:
    """Return immutable chart type registry items."""
    return CHART_TYPE_ITEMS


def get_chart_type_prompt_text() -> str:
    """Build chart capability prompt text from the registry."""
    lines = [
        "| chart_type | 中文名 | 适用场景 | 数据形态 |",
        "|---|---|---|---|",
    ]
    for item in CHART_TYPE_ITEMS:
        lines.append(f"| `{item['key']}` | {item['label']} | {item['description']} | {item['data_shape']} |")
    return "\n".join(lines)
