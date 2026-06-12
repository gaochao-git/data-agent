"""
图表生成器 - 生成 ECharts 配置

提供各类图表的配置生成函数：
- generate_bar_config: 柱状图
- generate_line_config: 折线图
- generate_pie_config: 饼图
- generate_multi_bar_config: 分组柱状图
- generate_multi_line_config: 多折线图
"""


def generate_bar_config(x_data: list, y_data: list, title: str, x_field: str, y_field: str) -> dict:
    """生成柱状图配置"""
    return {
        "title": {"text": title, "left": "center"},
        "tooltip": {"trigger": "axis"},
        "xAxis": {
            "type": "category",
            "data": x_data,
            "name": x_field,
            "axisLabel": {"rotate": 30 if len(x_data) > 5 else 0}
        },
        "yAxis": {"type": "value", "name": y_field},
        "series": [{"type": "bar", "data": y_data, "name": y_field}],
        "grid": {"bottom": "15%"},
    }


def generate_line_config(x_data: list, y_data: list, title: str, x_field: str, y_field: str) -> dict:
    """生成折线图配置"""
    return {
        "title": {"text": title, "left": "center"},
        "tooltip": {"trigger": "axis"},
        "xAxis": {
            "type": "category",
            "data": x_data,
            "name": x_field,
            "axisLabel": {"rotate": 30 if len(x_data) > 5 else 0}
        },
        "yAxis": {"type": "value", "name": y_field},
        "series": [{"type": "line", "data": y_data, "name": y_field, "smooth": True}],
        "grid": {"bottom": "20%"},
        "dataZoom": [
            {
                "type": "inside",
                "xAxisIndex": 0,
                "start": 0,
                "end": 100,
            },
            {
                "type": "slider",
                "xAxisIndex": 0,
                "start": 0,
                "end": 100,
                "height": 20,
                "bottom": 5,
            },
        ],
        "toolbox": {
            "feature": {
                "restore": {"title": "重置"},
            },
            "right": 20,
            "top": 5,
        },
    }


def generate_pie_config(x_data: list, y_data: list, title: str, x_field: str, y_field: str) -> dict:
    """生成饼图配置"""
    pie_data = [{"name": str(x), "value": y} for x, y in zip(x_data, y_data)]
    return {
        "title": {"text": title, "left": "center"},
        "tooltip": {
            "trigger": "item",
            "formatter": "{b}: {c} ({d}%)"
        },
        "legend": {"orient": "vertical", "left": "left", "top": "middle"},
        "series": [{
            "type": "pie",
            "radius": ["40%", "70%"],
            "center": ["60%", "50%"],
            "data": pie_data,
            "label": {
                "show": True,
                "formatter": "{b}: {d}%"
            },
            "emphasis": {
                "label": {
                    "show": True,
                    "fontSize": 14,
                    "fontWeight": "bold",
                    "formatter": "{b}: {c} ({d}%)"
                }
            }
        }],
    }


def generate_multi_bar_config(
    data: list[dict],
    x_field: str,
    y_field: str,
    series_field: str,
    title: str = ""
) -> dict:
    """
    生成分组柱状图配置

    Args:
        data: 原始数据列表
        x_field: X轴字段名
        y_field: Y轴数值字段名
        series_field: 分组字段名
        title: 图表标题
    """
    # 1. 提取所有唯一的 X 轴值并排序
    x_values = sorted(set(str(row.get(x_field, "")) for row in data if row.get(x_field) is not None))

    # 2. 按 series_field 分组数据
    series_map: dict[str, dict] = {}
    for row in data:
        series_name = str(row.get(series_field, ""))
        x_val = str(row.get(x_field, ""))
        y_val = row.get(y_field, 0)

        if series_name not in series_map:
            series_map[series_name] = {}
        series_map[series_name][x_val] = y_val

    # 3. 构建 series 数据
    series = []
    for series_name in sorted(series_map.keys()):
        x_y_map = series_map[series_name]
        bar_data = [x_y_map.get(x, 0) for x in x_values]
        series.append({
            "name": series_name,
            "type": "bar",
            "data": bar_data,
        })

    return {
        "title": {"text": title, "left": "center"},
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
        "legend": {"data": sorted(series_map.keys()), "top": 30},
        "xAxis": {
            "type": "category",
            "data": x_values,
            "name": x_field,
            "axisLabel": {"rotate": 30 if len(x_values) > 5 else 0},
        },
        "yAxis": {"type": "value", "name": y_field},
        "series": series,
        "grid": {"top": 60, "bottom": "15%"},
    }


def generate_multi_line_config(
    data: list[dict],
    x_field: str,
    y_field: str,
    series_field: str,
    title: str = ""
) -> dict:
    """
    生成多折线图配置

    Args:
        data: 原始数据列表
        x_field: X轴字段名
        y_field: Y轴数值字段名
        series_field: 分组字段名
        title: 图表标题
    """
    # 1. 提取所有唯一的 X 轴值并排序
    x_values = sorted(set(str(row.get(x_field, "")) for row in data if row.get(x_field) is not None))

    # 2. 按 series_field 分组数据
    series_map: dict[str, dict] = {}
    for row in data:
        series_name = str(row.get(series_field, ""))
        x_val = str(row.get(x_field, ""))
        y_val = row.get(y_field, 0)

        if series_name not in series_map:
            series_map[series_name] = {}
        series_map[series_name][x_val] = y_val

    # 3. 构建 series 数据
    series = []
    for series_name in sorted(series_map.keys()):
        x_y_map = series_map[series_name]
        line_data = [x_y_map.get(x, None) for x in x_values]
        series.append({
            "name": series_name,
            "type": "line",
            "data": line_data,
            "smooth": True,
            "connectNulls": True,
        })

    return {
        "title": {"text": title, "left": "center"},
        "tooltip": {"trigger": "axis"},
        "legend": {"data": sorted(series_map.keys()), "top": 30},
        "xAxis": {
            "type": "category",
            "data": x_values,
            "name": x_field,
            "axisLabel": {"rotate": 30 if len(x_values) > 8 else 0},
        },
        "yAxis": {"type": "value", "name": y_field},
        "series": series,
        "grid": {"top": 60, "bottom": 60},
        "dataZoom": [
            {"type": "inside", "xAxisIndex": 0, "start": 0, "end": 100},
            {"type": "slider", "xAxisIndex": 0, "start": 0, "end": 100, "height": 20, "bottom": 5},
        ],
    }


# 图表生成器映射
CHART_GENERATORS = {
    "bar": generate_bar_config,
    "line": generate_line_config,
    "pie": generate_pie_config,
}


def get_generator(chart_type: str):
    """获取图表生成器"""
    return CHART_GENERATORS.get(chart_type)


def get_supported_types() -> list[str]:
    """获取支持的图表类型"""
    return list(CHART_GENERATORS.keys())
