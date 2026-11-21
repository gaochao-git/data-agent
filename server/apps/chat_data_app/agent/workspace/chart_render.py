"""结果文件转折线图的纯绘图内核：``data2image`` 工具的渲染层。

职责边界：把表格类结果文件（JSON/CSV）归一化为数值曲线序列，用 matplotlib(Agg)
渲染为带图例的纯趋势 PNG 字节。只画线图（单/多曲线），不做其他图型；
不做异常点标注——预判会锚定模型的视觉分析，异常识别交给模型看图自判，
需要精确值时由模型按图上范围走 file_read 渐近检索（图=形态概览，数据=精度）。

与 Agent 工具的分工：``data2image`` 只负责准入校验、文件读取与产物落盘；
本模块不做路径校验、不做 I/O 决策，输入即行数据与列名，输出即 PNG 字节与统计。
模型拿到图片路径后用 ``read_image`` 读图做视觉分析（转图与读图分两步各自管各自）。
"""

from __future__ import annotations

import csv
import io
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # 必须在 Figure/canvas 导入前切换后端：服务器无显示环境
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.font_manager as font_manager  # noqa: E402
from matplotlib.axes import Axes  # noqa: E402
from matplotlib.backends.backend_agg import FigureCanvasAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.font_manager import FontProperties  # noqa: E402

__all__ = [
    "ChartRenderError",
    "load_series_from_text",
    "render_line_charts",
]

# 单张图曲线数上限：超过直接报错，由智能体决定如何分批调用
MAX_LINES_PER_CHART = 5
# 单图尺寸（英寸）与 DPI：12x6@120 ≈ 1440x720，read_image 5MB 上限内
FIG_SIZE = (12.0, 6.0)
FIG_DPI = 120
# 日期轴最多 13 个刻度：12 个月加上年边界，保证月粒度的 01~12 不被隔月抽稀
MAX_DATE_TICKS = 13
# 曲线样式：Okabe-Ito 色盲友好配色 + 线型双重编码，避免仅靠红绿辨线。
# 稀疏数据加 marker；线宽略高于默认值，保证图片被视觉模型缩放后仍可辨认。
_SERIES_COLORS = ("#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7")
_SERIES_LINESTYLES = ("-", "--", "-.", ":", (0, (7, 2, 1, 2)))
_SERIES_MARKERS = ("o", "s", "^", "D", "v")
_SERIES_LINEWIDTH = 2.0
_SPARSE_SERIES_MAX_POINTS = 30
_ZERO_BASELINE_GID = "data2image-zero-baseline"

# matplotlib 的 rcParams 与字体缓存是进程级全局状态；渲染在线程池执行，
# 用模块锁把同一进程内的并发渲染串行化，避免 rc_context 互相覆盖。
_RENDER_LOCK = threading.Lock()

# 各平台常见中文字体候选：图例/标题含中文时逐个尝试，全部缺失则回退英文标签
_CJK_FONT_CANDIDATES = (
    "PingFang SC",
    "Hiragino Sans GB",
    "Microsoft YaHei",
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "Arial Unicode MS",
    "STHeiti",
    "SimHei",
)

_DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y-%m",
    "%Y/%m",
    "%Y%m%d",
    "%Y",
)
_TIME_FORMAT_HINT = (
    "支持 YYYY、YYYY-MM、YYYY-MM-DD、YYYY-MM-DD HH:MM[:SS]、斜杠分隔的 "
    "YYYY/MM、YYYY/MM/DD 与 YYYY/MM/DD HH:MM[:SS]，以及 ISO 8601（T 分隔、YYYYMMDD）；"
    "不支持毫秒/小数秒、时区（Z 或 ±HH:MM）、UNIX 时间戳（秒/毫秒）、中文日期、DD/MM/YYYY、"
    "季度（YYYY-Qn）和自然语言日期"
)

# 月份统一使用两位数字，避免中英文混排时出现 Mar/May/Nov；日、时、分、秒也保持数字格式。
_DATE_FORMATS = ("%Y", "%m", "%d", "%H:%M", "%H:%M", "%S")
_DATE_ZERO_FORMATS = ("", "%Y", "%m", "%m-%d", "%H:%M", "%H:%M")
_DATE_OFFSET_FORMATS = ("", "%Y", "%Y-%m", "%Y-%m-%d", "%Y-%m-%d", "%Y-%m-%d %H:%M")


class ChartRenderError(ValueError):
    """图表渲染入参或数据不满足要求。"""


def load_series_from_text(text: str, *, file_name: str, x_field: str, y_fields: list[str]) -> list[dict[str, Any]]:
    """从结果文件文本加载曲线序列。

    支持三种形状（按序探测）：行字典数组、ChatData 结果集（columns+data）、
    包一层 data/results 信封的同形状载荷；CSV 按 UTF-8（兼容 BOM）表头解析。
    X 轴必须是可解析的时间列；无法解析时直接报错，不回退类目轴。
    返回按 ``y_fields`` 顺序排列的序列列表，全部非数值的序列跳过并报可读错误。
    """
    rows = _load_rows(text, file_name=file_name)
    if not rows:
        raise ChartRenderError("结果文件里没有找到数据行；请确认 file_path 指向含表格数据的 JSON 或 CSV 文件")
    _assert_fields_present(rows, x_field=x_field, y_fields=y_fields)
    series: list[dict[str, Any]] = []
    for y_field in y_fields:
        points, skipped = _coerce_points(rows, x_field=x_field, y_field=y_field)
        if points:
            series.append({"name": y_field, "points": _sort_time_points(points), "skipped_rows": skipped})
    if not series:
        raise ChartRenderError(f"字段 {y_fields} 在数据行里没有可绘制的数值；请检查 y_fields 是否为数值列")
    return series


def render_line_charts(
    series: list[dict[str, Any]],
    *,
    title: str,
    out_prefix: str,
) -> list[dict[str, Any]]:
    """把曲线序列渲染为单张 PNG；超过 ``MAX_LINES_PER_CHART`` 直接报错。

    不自动拆图：曲线过多时返回可读错误，由智能体决定分几次读取数据并分别调用。
    返回单元素列表 ``[{"file_stem", "png_bytes", "series_names", "stats"}]``，
    落盘由调用方决定。
    """
    if not series:
        raise ChartRenderError("没有可渲染的曲线序列")
    if len(series) > MAX_LINES_PER_CHART:
        raise ChartRenderError(f"单张图最多 {MAX_LINES_PER_CHART} 条曲线，当前 {len(series)} 条；请拆成多次调用")
    font_family = _resolve_cjk_font()
    return [
        {
            "file_stem": out_prefix,
            "png_bytes": _render_group_png(series, title=title, font_family=font_family),
            "series_names": [item["name"] for item in series],
            "stats": [_series_stats(item) for item in series],
        }
    ]


def _load_rows(text: str, *, file_name: str) -> list[dict[str, Any]]:
    """按后缀分发 JSON/CSV 解析，统一返回行字典列表。"""
    if Path(file_name).suffix.lower() == ".csv":
        return _load_rows_from_csv(text)
    return _load_rows_from_json(text)


def _load_rows_from_json(text: str) -> list[dict[str, Any]]:
    """解析 JSON 载荷为行字典列表；信封形状逐层探测。"""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ChartRenderError(f"结果文件不是合法 JSON: {exc}") from exc
    for candidate in _iter_row_payloads(payload, depth=0):
        rows = _rows_from_payload(candidate)
        if rows:
            return rows
    return []


def _iter_row_payloads(payload: Any, *, depth: int) -> list[Any]:
    """枚举可能的行载荷：顶层、data/rows/items 信封、results 成员的 data。"""
    if depth > 2 or payload is None:
        return []
    candidates = [payload]
    if isinstance(payload, dict):
        candidates.extend(_envelope_payloads(payload, depth=depth))
    return candidates


def _envelope_payloads(payload: dict, *, depth: int) -> list[Any]:
    """信封下探：data/rows/items 键与 results 成员的 data，最多两层。"""
    payloads: list[Any] = []
    for key in ("data", "rows", "items"):
        if key in payload:
            payloads.extend(_iter_row_payloads(payload[key], depth=depth + 1))
    for member in payload.get("results") or []:
        if isinstance(member, dict):
            payloads.extend(_iter_row_payloads(member.get("data"), depth=depth + 1))
    return payloads


def _rows_from_payload(candidate: Any) -> list[dict[str, Any]]:
    """把候选载荷转换为行字典列表；行数组或 columns+data 两种形状。"""
    if isinstance(candidate, list) and candidate and all(isinstance(row, dict) for row in candidate):
        return list(candidate)
    if isinstance(candidate, dict) and isinstance(candidate.get("data"), list):
        rows = candidate["data"]
        if rows and all(isinstance(row, dict) for row in rows):
            return list(rows)
        columns = _column_names(candidate.get("columns"))
        if columns and rows and all(isinstance(row, list) for row in rows):
            return [dict(zip(columns, row, strict=False)) for row in rows]
    return []


def _column_names(columns: Any) -> list[str]:
    """提取列名列表；columns 元素支持字符串或 {name/field/key/title} 对象。"""
    names: list[str] = []
    for column in columns or []:
        if isinstance(column, str):
            names.append(column)
        elif isinstance(column, dict):
            value = column.get("name") or column.get("field") or column.get("key") or column.get("title")
            if value:
                names.append(str(value))
    return names


def _load_rows_from_csv(text: str) -> list[dict[str, Any]]:
    """解析 CSV 文本为行字典列表；兼容 BOM，按表头映射。"""
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    return [dict(row) for row in reader]


def _assert_fields_present(rows: list[dict[str, Any]], *, x_field: str, y_fields: list[str]) -> None:
    """校验 X/Y 字段在数据行中存在；报错信息携带可用字段帮模型自我纠正。"""
    available = sorted({key for row in rows[:50] for key in row})
    missing = [field for field in [x_field, *y_fields] if field not in available]
    if missing:
        raise ChartRenderError(f"字段 {missing} 不在结果数据中；可用字段: {available[:20]}")


def _coerce_points(rows: list[dict[str, Any]], *, x_field: str, y_field: str) -> tuple[list[tuple[str, float]], int]:
    """把行数据整理为 (原始X, 数值Y) 点序列；非数值行跳过并计数。"""
    points: list[tuple[str, float]] = []
    skipped = 0
    for row in rows:
        value = _to_float(row.get(y_field))
        if value is None:
            skipped += 1
            continue
        points.append((str(row.get(x_field, "")), value))
    return points, skipped


def _sort_time_points(points: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """校验 X 全为时间并按时间升序排列，避免 SQL 无 ORDER BY 时折线乱窜。"""
    dates = [_parse_x(raw_x) for raw_x, _ in points]
    invalid = [raw_x for (raw_x, _), value in zip(points, dates, strict=True) if value is None]
    if invalid:
        preview = "、".join(repr(value[:30]) for value in invalid[:3])
        suffix = " 等" if len(invalid) > 3 else ""
        raise ChartRenderError(f"x_field 必须是时间列；无法解析: {preview}{suffix}。{_TIME_FORMAT_HINT}")
    if len(points) < 2:
        return points
    try:
        order = sorted(range(len(points)), key=lambda index: dates[index])  # type: ignore[arg-type,return-value]
        return [points[index] for index in order]
    except TypeError as exc:
        raise ChartRenderError("x_field 不能同时包含带时区和不带时区的时间值") from exc


def _to_float(value: Any) -> float | None:
    """宽松数值转换；None/空串/非数值返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _series_stats(item: dict[str, Any]) -> dict[str, Any]:
    """曲线统计摘要：随图返回，视觉不可用时模型仍有数值可答。"""
    values = [value for _, value in item.get("points", [])]
    if not values:
        return {"series": item["name"], "data_points": 0}
    return {
        "series": item["name"],
        "data_points": len(values),
        "min": min(values),
        "max": max(values),
        "avg": round(sum(values) / len(values), 4),
    }


def _resolve_cjk_font() -> str | None:
    """选一个可用的中文字体。

    坐标轴标签固定为中文（「数值」「序号」），所以每次渲染都必须解析字体，
    不能只看曲线名——英文列名 + 中文标题同样会把标题渲染成方块。
    """
    available = {family.name for family in font_manager.fontManager.ttflist}
    for candidate in _CJK_FONT_CANDIDATES:
        if candidate in available:
            return candidate
    return None


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in str(text or ""))


def _render_group_png(group: list[dict[str, Any]], *, title: str, font_family: str | None) -> bytes:
    """渲染一组曲线为 PNG 字节；无可用中文字体时整体回退英文标题与轴标签。"""
    font = FontProperties(family=font_family) if font_family else None
    # rcParams 是进程级全局状态：模块锁串行化同一进程内的并发渲染，
    # rc_context 保证异常路径也恢复原值。
    with _RENDER_LOCK, matplotlib.rc_context({"axes.unicode_minus": False}):
        figure = Figure(figsize=FIG_SIZE, dpi=FIG_DPI)
        FigureCanvasAgg(figure)
        axis = figure.add_subplot(1, 1, 1)
        for index, item in enumerate(group):
            _plot_series(axis, item, index)
        _draw_zero_baseline(axis, group)
        _decorate_axis(axis, title=title, font=font)
        axis.legend(loc="best", prop=font)
        buffer = io.BytesIO()
        figure.savefig(buffer, format="png", bbox_inches="tight")
        return buffer.getvalue()


def _parse_x(raw_x: str) -> datetime | None:
    """X 值的日期时间解析；兼容空格/T 分隔与斜杠日期。"""
    text = str(raw_x).strip()
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _build_date_formatter(locator: mdates.DateLocator) -> mdates.ConciseDateFormatter:
    """构建日期刻度格式化器；月份使用 01~12，不输出英文月份缩写。"""
    return mdates.ConciseDateFormatter(
        locator,
        formats=_DATE_FORMATS,
        zero_formats=_DATE_ZERO_FORMATS,
        offset_formats=_DATE_OFFSET_FORMATS,
    )


def _x_datetimes(axis: Axes) -> list[datetime]:
    """读取轴上已绘制的 X 值并还原为 datetime；非日期值忽略。"""
    values: list[datetime] = []
    for line in axis.lines:
        if line.get_gid() == _ZERO_BASELINE_GID:
            continue
        for raw_x in line.get_xdata():  # type: ignore[union-attr]  # matplotlib get_xdata 桩类型宽松，运行时为数值/日期数组
            if isinstance(raw_x, datetime):
                values.append(raw_x.replace(tzinfo=None))
                continue
            try:
                values.append(mdates.num2date(float(raw_x)).replace(tzinfo=None))  # type: ignore[arg-type]  # 同上
            except (TypeError, ValueError):
                continue
    return values


def _is_same_year_month_points(points: list[datetime]) -> bool:
    """判断是否为同一年的月粒度数据：每个点都在月初，且覆盖多个月。"""
    if len(points) < 2:
        return False
    if len({point.year for point in points}) != 1:
        return False
    if len({(point.year, point.month) for point in points}) < 2:
        return False
    return all(point.day == 1 and point.hour == 0 and point.minute == 0 and point.second == 0 for point in points)


def _plot_series(axis: Axes, item: dict[str, Any], index: int) -> None:
    """画单条时间序列曲线；颜色、线型与稀疏点标记共同编码身份。"""
    points = item.get("points", [])
    ys = [value for _, value in points]
    sparse = len(points) <= _SPARSE_SERIES_MAX_POINTS
    style_index = index % len(_SERIES_COLORS)
    axis.plot(
        _parsed_dates(points),  # type: ignore[arg-type]  # matplotlib 桩类型不含 datetime 轴，运行时经 mdates 支持
        ys,
        color=_SERIES_COLORS[style_index],
        linestyle=_SERIES_LINESTYLES[style_index],
        linewidth=_SERIES_LINEWIDTH,
        marker=_SERIES_MARKERS[style_index] if sparse else None,
        markersize=4.5,
        label=item["name"],
        zorder=3,
    )


def _draw_zero_baseline(axis: Axes, group: list[dict[str, Any]]) -> None:
    """数据跨越正负值时画零基线；全正或全负时不额外干扰趋势判断。"""
    values = [value for item in group for _, value in item.get("points", [])]
    if not values or not (min(values) < 0 < max(values)):
        return
    line = axis.axhline(0, color="#666666", linewidth=0.9, linestyle=(0, (4, 2)), alpha=0.8, zorder=2)
    line.set_gid(_ZERO_BASELINE_GID)


def _parsed_dates(points: list[tuple[str, float]]) -> list[datetime]:
    """X 值批量转 datetime；入口已校验时间格式，此处防御不可解析值。"""
    parsed: list[datetime] = []
    for raw_x, _ in points:
        value = _parse_x(raw_x)
        if value is None:  # 不可达：加载阶段已全量校验
            raise ChartRenderError(f"无法解析时间值: {raw_x}")
        parsed.append(value)
    return parsed


def _decorate_axis(
    axis: Axes,
    *,
    title: str,
    font: FontProperties | None = None,
) -> None:
    """坐标轴与标题装饰；X 轴固定按真实时间刻度渲染，月份显示为两位数字。"""
    title_text = title or ("数据趋势图" if font is not None else "Trend Chart")
    if font is None and _has_cjk(title_text):
        title_text = "Trend Chart"
    axis.set_title(title_text)
    axis.set_ylabel("数值" if font is not None else "Value")
    axis.grid(True, alpha=0.3)
    # 时间轴不留横向默认 5% 空白：月底日数据会因此越到下个月，产生错误月份偏移标签。
    axis.margins(x=0)
    points = _x_datetimes(axis)
    if _is_same_year_month_points(points):
        axis.xaxis.set_major_locator(mdates.MonthLocator())
        axis.xaxis.set_major_formatter(mdates.DateFormatter("%m"))
        year = points[0].year
        axis.set_xlabel(f"{year}年" if font is not None else str(year))
    else:
        locator = mdates.AutoDateLocator(maxticks=MAX_DATE_TICKS)
        axis.xaxis.set_major_locator(locator)
        axis.xaxis.set_major_formatter(_build_date_formatter(locator))
    _apply_font(axis, font)


def _apply_font(axis: Axes, font: FontProperties | None) -> None:
    """把中文字体应用到标题、轴标签、刻度和日期偏移文本。"""
    if font is None:
        return
    for artist in (axis.title, axis.xaxis.label, axis.yaxis.label, axis.xaxis.get_offset_text()):
        artist.set_fontproperties(font)
    for label in axis.get_xticklabels() + axis.get_yticklabels():
        label.set_fontproperties(font)
