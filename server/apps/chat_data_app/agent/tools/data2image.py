"""Agent 工具：data2image —— 把结果文件渲染成折线图 PNG，供视觉分析降上下文。

与 ``read_image`` 的分工（转图与读图分两步各自管各自）：
- 本工具只做「结果文件 → 折线图 PNG → outputs 存档」，不把 base64 塞进工具结果
  （同 read_image 的设计：避免 checkpoint 膨胀）；
- 生成后模型用 ``read_image`` 读取图片，由 ``ImageInjectionMiddleware`` 注入视觉上下文。

输入只接受系统工具生成的结果文件（query_result/tool-offload），不接受模型生成、
改写或上传的中间数据文件：模型只传 result_path，不把行数据带进上下文。
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from apps.chat_data_app.agent.workspace.runtime import ConversationWorkspaceRuntime, resolve_workspace_path
from apps.chat_data_app.agent.workspace.workspace_path_policy import WORKSPACE_PATH_POLICY
from utils.logger import get_logger

from .common import tool_result_payload

logger = get_logger(__name__)

# 结果文件读取上限：超过按错误返回（转图场景远用不到这么大的文件，防 OOM）
_MAX_RESULT_FILE_BYTES = 30 * 1024 * 1024
# 单次最多转换的字段数：超过请分批调用，不自动拆图
_MAX_Y_FIELDS = 5
# 存档目录：outputs 让用户在文件面板直接看到分析图
_OUTPUT_DIR = "/mnt/user-data/outputs"
# 文件名清洗：保留安全字符与中文（APFS/ext4 均支持中文文件名），其余替换为下划线
_FILENAME_SAFE_PATTERN = re.compile(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+")
_QUERY_RETRY_NEXT_STEP = (
    "不要读取、改写、新建中间数据文件，也不要自己生成行数据。"
    "请调整 SQL，让查询结果直接包含一个时间列（x_field）和 1~5 个数值列（y_fields），"
    "重新执行 data_analyzing_tool 后，用新回执的 result_path 再次调用 data2image。"
)


def _safe_filename_stem(title: str) -> str:
    """标题转文件名安全片段；清洗后为空则回退 'data'。"""
    cleaned = _FILENAME_SAFE_PATTERN.sub("_", title.strip())[:40].strip("_")
    return cleaned or "data"


def _failure(error: str, *, retry_query: bool = True) -> str:
    """统一的失败回执；查询结果不满足时明确要求调整 SQL，而不是改数据。"""
    payload: dict[str, Any] = {"success": False, "error": error}
    if retry_query:
        payload["next_step"] = _QUERY_RETRY_NEXT_STEP
    return tool_result_payload("data2image", payload)


def create_data2image_tool(workspace_runtime: ConversationWorkspaceRuntime) -> list:
    """构建 data2image 单一工具。"""

    @tool("data2image", parse_docstring=True)
    async def data2image(
        file_path: str,
        x_field: str,
        y_fields: list[str],
        title: str = "",
        intent: str = "",
    ) -> str:
        """
        把系统查询结果文件（JSON/CSV 表格）渲染成折线图 PNG 存到 outputs，供视觉分析降低上下文消耗。

        只接受系统工具生成的结果文件：/mnt/user-data/workspace/.system/query_result/ 或
        tool-offload/ 下、由工具回执给出的 result_path。禁止先用 file_read 读取数据、
        再用 file_write/file_edit 生成或改写中间 JSON/CSV，再把新文件传进来。
        本工具在服务端直接读 result_path，原始数据不进入上下文。

        查询结果必须直接满足绘图要求，否则本工具不渲染：x_field 是实际存在的时间列，
        y_fields 是 1~5 个实际存在的数值列。字段名、时间格式或数值类型不匹配时，
        应调整 SQL（别名、日期格式化、聚合或类型转换）后重新查询，而不是改写查询结果。
        生成成功后用 read_image 读取图片；需要精确取值或逐行核对时继续用 file_read。

        X 轴只接受时间列，格式必须为：YYYY、YYYY-MM、YYYY-MM-DD、YYYY-MM-DD HH:MM[:SS]、
        斜杠分隔的 YYYY/MM、YYYY/MM/DD 或 YYYY/MM/DD HH:MM[:SS]，或 ISO 8601
        （T 分隔、YYYYMMDD）。不支持毫秒/小数秒、时区（Z 或 ±HH:MM）、UNIX 时间戳（秒/毫秒）、
        中文日期、DD/MM/YYYY、季度（YYYY-Qn）和自然语言日期。

        Args:
            file_path: 系统工具回执中的 result_path，必须位于 workspace/.system/{query_result,tool-offload}/，JSON 或 CSV。
            x_field: X 轴时间字段名，必填；必须符合上述时间格式，无法解析会报错并列出可用格式。
            y_fields: Y 轴字段名列表（数值列），1~5 个；不满足时调整 SQL 后重新查询。
            title: 图表标题。
            intent: 一句中文说明本次调用目的（不超过50字，写目的不写动作），面向用户展示。
        """
        return await _data2image_impl(
            workspace_runtime,
            file_path=file_path,
            x_field=x_field,
            y_fields=y_fields,
            title=title,
        )

    return [data2image]


async def _data2image_impl(
    workspace_runtime: ConversationWorkspaceRuntime,
    *,
    file_path: str,
    x_field: str,
    y_fields: list[str],
    title: str,
) -> str:
    """data2image 主实现：读文件 → 归一化 → 渲染 → 落盘 outputs → 返回路径与统计。"""
    cleaned_fields = [str(field or "").strip() for field in (y_fields or [])]
    cleaned_fields = [field for field in cleaned_fields if field]
    if not cleaned_fields:
        return _failure("y_fields 不能为空；查询结果需直接包含 1~5 个数值列")
    if len(cleaned_fields) > _MAX_Y_FIELDS:
        return _failure(f"单张图最多 {_MAX_Y_FIELDS} 个数值列；请调整 SQL 控制返回列数")
    if not str(x_field or "").strip():
        return _failure("x_field 不能为空；查询结果需直接包含时间列")

    def _build_charts() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """同步 IO + CPU 渲染，交给线程池执行，避免阻塞事件循环。"""
        # 惰性 import 放在线程体内：matplotlib 首次 import 约 0.5s、常驻约 65MB；
        # 工具未调用时不应在 worker 启动阶段付费，首次调用也不能阻塞事件循环。
        from apps.chat_data_app.agent.workspace.chart_render import (
            load_series_from_text,
            render_line_charts,
        )

        text = _read_result_text(workspace_runtime, file_path)
        series = load_series_from_text(
            text,
            file_name=Path(file_path).name,
            x_field=str(x_field).strip(),
            y_fields=cleaned_fields,
        )
        charts = render_line_charts(series, title=str(title or "").strip(), out_prefix=_safe_filename_stem(title))
        return series, charts

    try:
        series, charts = await asyncio.to_thread(_build_charts)
    except ValueError as exc:
        return _failure(str(exc))
    except OSError as exc:
        logger.error("data2image 读文件失败: %s", exc, exc_info=True)
        return _failure("结果文件读取失败")

    saved_images: list[dict[str, Any]] = []
    try:
        # 毫秒级时间戳：同一秒内多次转换同名标题不会互相覆盖
        stamp = datetime.now().strftime("%H%M%S%f")[:-3]
        for chart in charts:
            target = f"{_OUTPUT_DIR}/{chart['file_stem']}_{stamp}.png"
            write_result = await workspace_runtime.write_binary_file(file_path=target, data=chart["png_bytes"])
            saved_images.append(
                {
                    "file_path": str(write_result.get("file_path") or target),
                    "series_names": chart["series_names"],
                    "stats": chart["stats"],
                }
            )
    except (OSError, ValueError) as exc:
        logger.error("data2image 图片落盘失败: %s", exc, exc_info=True)
        return _failure(f"图表 PNG 写入 outputs 失败: {exc}", retry_query=False)

    skipped_points_total = sum(int(item.get("skipped_points", 0)) for item in series)
    logger.info(
        "[data2image] 生成 %d 张折线图: file=%s series=%d skipped_points=%d",
        len(saved_images),
        file_path,
        len(series),
        skipped_points_total,
    )
    return tool_result_payload(
        "data2image",
        {
            "success": True,
            "images": saved_images,
            "skipped_points": skipped_points_total,
            "next_step": "用 read_image 读取上述 file_path，看图分析趋势形态后回答用户；需要精确数值时按图上范围渐近取数：用 file_path 分页读取原始结果文件，或用 SQL 对对应区间重新精确查询",
        },
    )


def _read_result_text(workspace_runtime: ConversationWorkspaceRuntime, file_path: str) -> str:
    """读取系统结果文件文本；拒绝模型生成/上传的文件，带大小上限。"""
    from apps.chat_data_app.agent.workspace.chart_render import ChartRenderError

    logical_path = WORKSPACE_PATH_POLICY.parse(file_path)
    if not logical_path.is_system_result:
        raise ChartRenderError(
            "data2image 只接受系统工具生成的结果文件（workspace/.system/query_result/ 或 tool-offload/）；"
            "请直接使用查询回执里的 result_path，不要生成或改写中间数据文件"
        )
    root = workspace_runtime.workspace_root
    physical = resolve_workspace_path(root, file_path, writable=False)
    if physical.suffix.lower() not in {".json", ".csv"}:
        raise ChartRenderError(f"仅支持 JSON/CSV 结果文件，当前后缀: {physical.suffix or '(无)'}")
    size_bytes = physical.stat().st_size
    if size_bytes > _MAX_RESULT_FILE_BYTES:
        raise ChartRenderError(f"结果文件过大（{size_bytes} 字节 > {_MAX_RESULT_FILE_BYTES}），请调整 SQL 缩减结果范围")
    return physical.read_text(encoding="utf-8")
