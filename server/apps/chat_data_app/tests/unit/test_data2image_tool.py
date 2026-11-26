import io
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "dummy")

from PIL import Image

from apps.chat_data_app.agent.tools import data2image as data2image_module
from apps.chat_data_app.agent.workspace import chart_render as chart_render_module
from apps.chat_data_app.agent.workspace import runtime as workspace_runtime_module
from apps.chat_data_app.agent.workspace.chart_render import _parse_x, load_series_from_text, render_line_charts
from apps.chat_data_app.services.agent_run import conversation_workspace


def _init_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace_runtime_module, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setattr("utils.storage_paths.THREADS_DIR", tmp_path / "threads")
    return conversation_workspace.ensure_conversation_workspace("conv_a", "user_a")


def _runtime(paths):
    return conversation_workspace.ConversationWorkspaceRuntime(
        workspace_root=paths["root"],
        user_id="user_a",
        backend="local",
    )


def _payload(tool_result: str) -> dict:
    """去掉工具回执的 ``[data2image]`` 前缀行后解析 JSON。"""
    _, _, body = tool_result.partition("\n")
    return json.loads(body)


def _rows_json(rows: list[dict]) -> str:
    return json.dumps(rows, ensure_ascii=False)


_ROWS = [
    {"日期": "2026-11-01", "订单量": 120, "退款量": 3},
    {"日期": "2026-11-02", "订单量": 150, "退款量": 2},
    {"日期": "2026-11-03", "订单量": 90, "退款量": 8},
    {"日期": "2026-11-04", "订单量": 9000, "退款量": 5},
]


def _write_workspace_file(paths, name: str, content: str) -> str:
    target = Path(paths["workspace"]) / name
    target.write_text(content, encoding="utf-8")
    return f"/mnt/user-data/workspace/{name}"


def _write_result_file(paths, name: str, content: str) -> str:
    target = Path(paths["workspace"]) / ".system" / "query_result" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"/mnt/user-data/workspace/.system/query_result/{name}"


def _assert_valid_png(png_bytes: bytes) -> None:
    assert png_bytes.startswith(b"\x89PNG")
    image = Image.open(io.BytesIO(png_bytes))
    assert image.format == "PNG"
    assert image.size[0] >= 800 and image.size[1] >= 400


# ---------------------------------------------------------------------------
# 纯绘图内核
# ---------------------------------------------------------------------------


def test_load_series_supports_rows_columns_and_csv():
    rows_payload = _rows_json(_ROWS)
    series = load_series_from_text(rows_payload, file_name="a.json", x_field="日期", y_fields=["订单量"])
    assert [item["name"] for item in series] == ["订单量"]
    assert len(series[0]["points"]) == 4

    columns_payload = json.dumps({"columns": ["日期", "订单量"], "data": [["2026-11-01", 10], ["2026-11-02", 20]]})
    series = load_series_from_text(columns_payload, file_name="a.json", x_field="日期", y_fields=["订单量"])
    assert series[0]["points"] == [("2026-11-01", 10.0), ("2026-11-02", 20.0)]

    csv_text = "日期,订单量\n2026-11-01,5\n2026-11-02,7\n"
    series = load_series_from_text(csv_text, file_name="a.csv", x_field="日期", y_fields=["订单量"])
    assert series[0]["points"] == [("2026-11-01", 5.0), ("2026-11-02", 7.0)]


def test_render_line_charts_single_and_multi_curve():
    series = load_series_from_text(_rows_json(_ROWS), file_name="a.json", x_field="日期", y_fields=["订单量"])
    charts = render_line_charts(series, title="单曲线", out_prefix="chart")
    assert len(charts) == 1
    _assert_valid_png(charts[0]["png_bytes"])

    multi = load_series_from_text(_rows_json(_ROWS), file_name="a.json", x_field="日期", y_fields=["订单量", "退款量"])
    charts = render_line_charts(multi, title="多曲线", out_prefix="chart")
    assert len(charts) == 1
    assert charts[0]["series_names"] == ["订单量", "退款量"]
    _assert_valid_png(charts[0]["png_bytes"])


def test_render_line_charts_rejects_more_than_five_curves():
    rows = [
        {"日期": f"2026-11-{index:02d}", **{f"m{idx}": index * 10 + idx for idx in range(1, 8)}}
        for index in range(1, 6)
    ]
    five_series = load_series_from_text(
        _rows_json(rows),
        file_name="a.json",
        x_field="日期",
        y_fields=[f"m{idx}" for idx in range(1, 6)],
    )
    charts = render_line_charts(five_series, title="五条曲线", out_prefix="chart")
    assert len(charts) == 1
    assert len(charts[0]["series_names"]) == 5

    seven_series = load_series_from_text(
        _rows_json(rows),
        file_name="a.json",
        x_field="日期",
        y_fields=[f"m{idx}" for idx in range(1, 8)],
    )
    with pytest.raises(ValueError, match="最多 5 条曲线"):
        render_line_charts(seven_series, title="七条曲线", out_prefix="chart")


# ---------------------------------------------------------------------------
# 时间格式白名单矩阵（_parse_x / _TIME_FORMAT_HINT 契约）
# ---------------------------------------------------------------------------

SUPPORTED_X_FORMATS = [
    "2026",  # YYYY
    "2026-11",  # YYYY-MM
    "2026-11-05",  # YYYY-MM-DD
    "2026-11-05 10:30:00",  # YYYY-MM-DD HH:MM:SS
    "2026-11-05 10:30",  # YYYY-MM-DD HH:MM
    "2026/11",  # YYYY/MM
    "2026/11/05",  # YYYY/MM/DD
    "2026/11/05 10:30:00",  # 斜杠 + 完整
    "2026/11/05 10:30",  # 斜杠 + HH:MM
    "2026-11-05T10:30:00",  # ISO 8601（T 分隔）
    "20261105",  # YYYYMMDD 紧凑
]

UNSUPPORTED_X_FORMATS = [
    "2026-11-05 10:30:00.123",  # 毫秒/小数秒
    "2026-11-05T10:30:00Z",  # 时区 Z
    "2026-11-05 10:30:00+08:00",  # 时区偏移
    "1783000000",  # UNIX 秒
    "1783000000000",  # UNIX 毫秒
    "2026年11月5日",  # 中文日期
    "05/11/2026",  # DD/MM/YYYY
    "2026-Q4",  # 季度
    "上周三",  # 自然语言
]


@pytest.mark.parametrize("raw_x", SUPPORTED_X_FORMATS)
def test_parse_x_accepts_whitelisted_formats(raw_x):
    assert _parse_x(raw_x) is not None


@pytest.mark.parametrize("raw_x", UNSUPPORTED_X_FORMATS)
def test_parse_x_rejects_non_whitelisted_formats(raw_x):
    assert _parse_x(raw_x) is None


def test_load_series_rejects_non_time_x_field_with_format_hint():
    """x_field 非时间列时报错携带格式白名单，模型可自我纠正。"""
    rows = [{"状态": "已完成", "订单量": 10}, {"状态": "已取消", "订单量": 5}]
    with pytest.raises(ValueError, match="必须是时间列"):
        load_series_from_text(_rows_json(rows), file_name="a.json", x_field="状态", y_fields=["订单量"])


def test_render_charts_keep_large_peak_stats():
    rows = [{"日期": f"2026-11-{index:02d}", "订单量": 100} for index in range(1, 11)]
    rows[9]["订单量"] = 5000
    series = load_series_from_text(_rows_json(rows), file_name="a.json", x_field="日期", y_fields=["订单量"])
    charts = render_line_charts(series, title="大峰值", out_prefix="chart")
    assert charts[0]["stats"][0]["max"] == 5000
    _assert_valid_png(charts[0]["png_bytes"])


def test_load_series_missing_field_reports_available_columns():
    with pytest.raises(ValueError, match="可用字段"):
        load_series_from_text(_rows_json(_ROWS), file_name="a.json", x_field="日期", y_fields=["不存在"])


def test_load_series_sorts_unordered_time_x_axis():
    rows = [
        {"日期": "2026-11-03", "订单量": 3},
        {"日期": "2026-11-01", "订单量": 1},
        {"日期": "2026-11-02", "订单量": 2},
    ]
    series = load_series_from_text(_rows_json(rows), file_name="a.json", x_field="日期", y_fields=["订单量"])
    assert series[0]["points"] == [("2026-11-01", 1.0), ("2026-11-02", 2.0), ("2026-11-03", 3.0)]


@pytest.mark.parametrize(
    "value",
    [
        "2026",
        "2026-11",
        "2026/11",
        "2026-11-01",
        "2026/11/01",
        "2026-11-01 10:20",
        "2026-11-01 10:20:30",
        "2026/11/01 10:20:30",
        "2026-11-01T10:20:30",
        "20261101",
    ],
)
def test_load_series_accepts_supported_time_formats(value):
    rows = [{"时间": value, "指标": 1}]
    series = load_series_from_text(_rows_json(rows), file_name="a.json", x_field="时间", y_fields=["指标"])
    assert series[0]["points"] == [(value, 1.0)]


@pytest.mark.parametrize(
    "rows",
    [
        [{"x": "10", "v": 1}, {"x": "2", "v": 2}],
        [{"名称": "华东", "v": 1}, {"名称": "华北", "v": 2}],
        [{"月份": "2026年11月", "v": 1}],
        [{"时间": "1710000000", "v": 1}],
        [{"时间": "11/01/2026", "v": 1}],
        [{"时间": "2026-11-01T10:20:30.123", "v": 1}],
        [{"时间": "2026-11-01 10:20:30.123", "v": 1}],
        [{"时间": "2026-11-01T10:20:30+08:00", "v": 1}],
        [{"时间": "2026-11-01T10:20:30Z", "v": 1}],
        [{"时间": "20261101102030", "v": 1}],
    ],
)
def test_load_series_rejects_non_time_x_axis(rows):
    with pytest.raises(ValueError, match="必须是时间列"):
        load_series_from_text(_rows_json(rows), file_name="a.json", x_field=next(iter(rows[0])), y_fields=["v"])


def test_load_series_rejects_partially_invalid_time_axis():
    rows = [{"日期": "2026-11-01", "v": 1}, {"日期": "未知", "v": 2}]
    with pytest.raises(ValueError, match="必须是时间列.*未知"):
        load_series_from_text(_rows_json(rows), file_name="a.json", x_field="日期", y_fields=["v"])


def test_render_chinese_title_without_missing_glyph_warnings():
    """中文标题与轴标签必须找到中文字体，不能回退成方块。"""
    import warnings

    rows = [{"date": f"2026-11-{day:02d}", "revenue": day * 10} for day in range(1, 6)]
    series = load_series_from_text(_rows_json(rows), file_name="a.json", x_field="date", y_fields=["revenue"])
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        charts = render_line_charts(series, title="销售趋势分析", out_prefix="chart")
    assert not [item for item in caught if "missing from font" in str(item.message)]
    _assert_valid_png(charts[0]["png_bytes"])


def test_time_axis_uses_numeric_months():
    """单年月粒度刻度必须显示 01…12，不能出现 Mar/May 等英文缩写。"""
    from datetime import datetime

    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    figure = Figure(figsize=chart_render_module.FIG_SIZE, dpi=chart_render_module.FIG_DPI)
    FigureCanvasAgg(figure)
    axis = figure.add_subplot(1, 1, 1)
    dates = [datetime(2026, month, 1) for month in range(1, 13)]
    axis.plot(dates, list(range(len(dates))))

    chart_render_module._decorate_axis(axis, title="月份刻度", font=None)
    figure.canvas.draw()

    labels = [label.get_text() for label in axis.get_xticklabels() if label.get_text()]
    assert labels == [f"{month:02d}" for month in range(1, 13)]
    assert axis.get_xlabel() == "2026"
    assert not any(label in {"Mar", "May", "Jul", "Sep", "Nov"} for label in labels)
    assert all(label.get_rotation() == 90 for label in axis.get_xticklabels())


def test_daily_axis_at_month_end_does_not_show_next_month_offset():
    """月底日数据不能因默认留白越到下个月；年月放在 xlabel，日刻度只显示 DD。"""
    from datetime import datetime

    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    figure = Figure(figsize=chart_render_module.FIG_SIZE, dpi=chart_render_module.FIG_DPI)
    FigureCanvasAgg(figure)
    axis = figure.add_subplot(1, 1, 1)
    axis.plot([datetime(2026, 1, day) for day in range(1, 32)], list(range(31)))

    chart_render_module._decorate_axis(axis, title="月底", font=None)
    figure.canvas.draw()

    labels = [label.get_text() for label in axis.get_xticklabels() if label.get_text()]
    assert axis.get_xlabel() == "2026-01"
    assert axis.xaxis.get_offset_text().get_text() == ""
    assert labels == [f"{day:02d}" for day in range(1, 32, 2)]
    assert all(label.get_rotation() == 90 for label in axis.get_xticklabels())


def test_dense_multi_year_axis_uses_more_matplotlib_ticks():
    """长跨度的月粒度刻度应稳定收敛到 25 个，并保持数字月份。"""
    from datetime import datetime, timedelta

    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    figure = Figure(figsize=chart_render_module.FIG_SIZE, dpi=chart_render_module.FIG_DPI)
    FigureCanvasAgg(figure)
    axis = figure.add_subplot(1, 1, 1)
    dates = [datetime(2024, 1, 1) + timedelta(days=index) for index in range(1000)]
    axis.plot(dates, list(range(len(dates))))

    chart_render_module._decorate_axis(axis, title="多年", font=None)
    figure.canvas.draw()

    labels = [label.get_text() for label in axis.get_xticklabels() if label.get_text()]
    assert len(labels) == 25
    assert labels[0] == "2024"
    assert not any(label in {"Mar", "May", "Jul", "Sep", "Nov"} for label in labels)
    assert all(label.get_rotation() == 90 for label in axis.get_xticklabels())


def test_plot_series_uses_color_linestyle_and_sparse_markers():
    """五条曲线同时用配色与线型编码，稀疏数据再加不同 marker。"""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    figure = Figure(figsize=chart_render_module.FIG_SIZE, dpi=chart_render_module.FIG_DPI)
    FigureCanvasAgg(figure)
    axis = figure.add_subplot(1, 1, 1)
    points = [("2026-11-01", 1.0), ("2026-11-02", 2.0), ("2026-11-03", 3.0)]
    for index in range(5):
        chart_render_module._plot_series(axis, {"name": f"指标{index}", "points": points}, index)

    colors = [line.get_color().lower() for line in axis.lines]
    assert colors == [color.lower() for color in chart_render_module._SERIES_COLORS]
    dash_patterns = [repr(getattr(line, "_unscaled_dash_pattern", None)) for line in axis.lines]
    assert len(set(dash_patterns)) == 5
    assert [line.get_marker() for line in axis.lines] == list(chart_render_module._SERIES_MARKERS)
    assert all(line.get_linewidth() == chart_render_module._SERIES_LINEWIDTH for line in axis.lines)


def test_plot_series_omits_markers_for_dense_series():
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    figure = Figure(figsize=chart_render_module.FIG_SIZE, dpi=chart_render_module.FIG_DPI)
    FigureCanvasAgg(figure)
    axis = figure.add_subplot(1, 1, 1)
    points = [(f"2026-01-{day:02d}", float(day)) for day in range(1, 32)]
    chart_render_module._plot_series(axis, {"name": "密集指标", "points": points}, 0)

    assert axis.lines[0].get_marker() in {None, "", "None"}


def test_draw_zero_baseline_only_when_values_cross_zero():
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    figure = Figure(figsize=chart_render_module.FIG_SIZE, dpi=chart_render_module.FIG_DPI)
    FigureCanvasAgg(figure)
    axis = figure.add_subplot(1, 1, 1)

    chart_render_module._draw_zero_baseline(axis, [{"name": "全正", "points": [("2026-11-01", 1.0)]}])
    assert len(axis.lines) == 0

    chart_render_module._draw_zero_baseline(
        axis,
        [{"name": "跨零", "points": [("2026-11-01", -1.0), ("2026-11-02", 1.0)]}],
    )
    assert len(axis.lines) == 1
    assert axis.lines[0].get_color().lower() == "#666666"


def test_zero_baseline_does_not_pollute_time_axis_detection():
    """零基线是辅助线，不能被识别成 1970 年的时间数据。"""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    figure = Figure(figsize=chart_render_module.FIG_SIZE, dpi=chart_render_module.FIG_DPI)
    FigureCanvasAgg(figure)
    axis = figure.add_subplot(1, 1, 1)
    points = [(f"2026-{month:02d}", float(month - 6)) for month in range(1, 13)]
    chart_render_module._plot_series(axis, {"name": "跨零指标", "points": points}, 0)
    chart_render_module._draw_zero_baseline(axis, [{"name": "跨零指标", "points": points}])

    dates = chart_render_module._x_datetimes(axis)

    assert len(dates) == 12
    assert {point.year for point in dates} == {2026}


# ---------------------------------------------------------------------------
# data2image 工具
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_data2image_success_saves_png_to_outputs(monkeypatch, tmp_path):
    paths = _init_workspace(monkeypatch, tmp_path)
    file_path = _write_result_file(paths, "result.json", _rows_json(_ROWS))

    tool = data2image_module.create_data2image_tool(_runtime(paths))[0]
    result = _payload(
        await tool.ainvoke(
            {
                "file_path": file_path,
                "x_field": "日期",
                "y_fields": ["订单量", "退款量"],
                "title": "订单趋势",
            }
        )
    )

    assert result["success"] is True
    assert len(result["images"]) == 1
    for image in result["images"]:
        assert image["file_path"].startswith("/mnt/user-data/outputs/")
        assert image["file_path"].endswith(".png")
        physical = Path(paths["outputs"]) / Path(image["file_path"]).name
        _assert_valid_png(physical.read_bytes())
    assert "read_image" in result["next_step"]


@pytest.mark.asyncio
async def test_data2image_reads_tool_offload_json(monkeypatch, tmp_path):
    paths = _init_workspace(monkeypatch, tmp_path)
    offload_dir = Path(paths["workspace"]) / ".system" / "tool-offload"
    offload_dir.mkdir(parents=True, exist_ok=True)
    (offload_dir / "result-1.json").write_text(_rows_json(_ROWS), encoding="utf-8")

    tool = data2image_module.create_data2image_tool(_runtime(paths))[0]
    result = _payload(
        await tool.ainvoke(
            {
                "file_path": "/mnt/user-data/workspace/.system/tool-offload/result-1.json",
                "x_field": "日期",
                "y_fields": ["订单量"],
            }
        )
    )

    assert result["success"] is True
    assert result["images"][0]["file_path"].startswith("/mnt/user-data/outputs/")


@pytest.mark.asyncio
async def test_data2image_rejects_empty_and_missing_fields(monkeypatch, tmp_path):
    paths = _init_workspace(monkeypatch, tmp_path)
    file_path = _write_result_file(paths, "result.json", _rows_json(_ROWS))
    tool = data2image_module.create_data2image_tool(_runtime(paths))[0]

    empty_fields = _payload(await tool.ainvoke({"file_path": file_path, "x_field": "日期", "y_fields": []}))
    assert empty_fields["success"] is False

    missing_field = _payload(await tool.ainvoke({"file_path": file_path, "x_field": "日期", "y_fields": ["不存在"]}))
    assert missing_field["success"] is False
    assert "可用字段" in missing_field["error"]
    assert "调整 SQL" in missing_field["next_step"]
    assert list(Path(paths["outputs"]).iterdir()) == []

    missing_x = _payload(await tool.ainvoke({"file_path": file_path, "x_field": "", "y_fields": ["订单量"]}))
    assert missing_x["success"] is False


@pytest.mark.asyncio
async def test_data2image_rejects_more_than_five_y_fields(monkeypatch, tmp_path):
    paths = _init_workspace(monkeypatch, tmp_path)
    rows = [{"日期": "2026-11-01", **{f"m{index}": index for index in range(1, 7)}}]
    file_path = _write_result_file(paths, "result.json", _rows_json(rows))
    tool = data2image_module.create_data2image_tool(_runtime(paths))[0]

    result = _payload(
        await tool.ainvoke(
            {"file_path": file_path, "x_field": "日期", "y_fields": [f"m{index}" for index in range(1, 7)]}
        )
    )

    assert result["success"] is False
    assert "最多 5 个数值列" in result["error"]
    assert "调整 SQL" in result["next_step"]


@pytest.mark.asyncio
async def test_data2image_rejects_non_time_x_axis(monkeypatch, tmp_path):
    paths = _init_workspace(monkeypatch, tmp_path)
    rows = [{"地区": "华东", "订单量": 10}, {"地区": "华北", "订单量": 20}]
    file_path = _write_result_file(paths, "result.json", _rows_json(rows))
    tool = data2image_module.create_data2image_tool(_runtime(paths))[0]

    result = _payload(await tool.ainvoke({"file_path": file_path, "x_field": "地区", "y_fields": ["订单量"]}))

    assert "YYYY-MM" in tool.description
    assert "UNIX 时间戳" in tool.description
    assert "不支持毫秒/小数秒" in tool.description
    assert "时区（Z 或 ±HH:MM）" in tool.description
    assert result["success"] is False
    assert "必须是时间列" in result["error"]
    assert "YYYY-MM" in result["error"]
    assert "季度（YYYY-Qn）" in result["error"]
    assert "不支持毫秒/小数秒" in result["error"]
    assert "时区（Z 或 ±HH:MM）" in result["error"]
    assert "调整 SQL" in result["next_step"]
    assert list(Path(paths["outputs"]).iterdir()) == []


@pytest.mark.asyncio
async def test_data2image_rejects_model_generated_workspace_file(monkeypatch, tmp_path):
    """模型自建中间数据文件会被拒绝，必须回到 SQL 重新查询。"""
    paths = _init_workspace(monkeypatch, tmp_path)
    file_path = _write_workspace_file(paths, "model-generated.json", _rows_json(_ROWS))
    tool = data2image_module.create_data2image_tool(_runtime(paths))[0]

    result = _payload(await tool.ainvoke({"file_path": file_path, "x_field": "日期", "y_fields": ["订单量"]}))

    assert result["success"] is False
    assert "只接受系统工具生成的结果文件" in result["error"]
    assert "不要读取、改写、新建中间数据文件" in result["next_step"]
    assert "调整 SQL" in result["next_step"]
    assert list(Path(paths["outputs"]).iterdir()) == []


@pytest.mark.asyncio
async def test_data2image_rejects_escape_and_bad_suffix(monkeypatch, tmp_path):
    paths = _init_workspace(monkeypatch, tmp_path)
    tool = data2image_module.create_data2image_tool(_runtime(paths))[0]

    outside = _payload(await tool.ainvoke({"file_path": "/etc/passwd", "x_field": "a", "y_fields": ["b"]}))
    assert outside["success"] is False

    for name, content in (("result.md", "# 数据"), ("result.txt", "日期,订单量\n2026-11-01,1")):
        bad_suffix = _payload(
            await tool.ainvoke(
                {
                    "file_path": _write_result_file(paths, name, content),
                    "x_field": "日期",
                    "y_fields": ["订单量"],
                }
            )
        )
        assert bad_suffix["success"] is False
        assert "仅支持 JSON/CSV" in bad_suffix["error"]


@pytest.mark.asyncio
async def test_data2image_counts_skipped_non_numeric_points(monkeypatch, tmp_path):
    paths = _init_workspace(monkeypatch, tmp_path)
    rows = [
        {"日期": "2026-11-01", "订单量": 10},
        {"日期": "2026-11-02", "订单量": "N/A"},
        {"日期": "2026-11-03", "订单量": 30},
    ]
    file_path = _write_result_file(paths, "result.json", _rows_json(rows))
    tool = data2image_module.create_data2image_tool(_runtime(paths))[0]

    result = _payload(await tool.ainvoke({"file_path": file_path, "x_field": "日期", "y_fields": ["订单量"]}))

    assert result["success"] is True
    assert result["skipped_points"] == 1
    assert result["images"][0]["stats"][0]["data_points"] == 2


@pytest.mark.asyncio
async def test_data2image_counts_invalid_points_across_series(monkeypatch, tmp_path):
    """多列共享同一行时按「无效点」累计，不再用有歧义的 skipped_rows。"""
    paths = _init_workspace(monkeypatch, tmp_path)
    rows = [
        {"日期": "2026-11-01", "订单量": "N/A", "退款量": "N/A"},
        {"日期": "2026-11-02", "订单量": 10, "退款量": 2},
    ]
    file_path = _write_result_file(paths, "result.json", _rows_json(rows))
    tool = data2image_module.create_data2image_tool(_runtime(paths))[0]

    result = _payload(await tool.ainvoke({"file_path": file_path, "x_field": "日期", "y_fields": ["订单量", "退款量"]}))

    assert result["success"] is True
    assert result["skipped_points"] == 2
    assert [item["data_points"] for item in result["images"][0]["stats"]] == [1, 1]
