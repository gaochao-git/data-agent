"""Agent 工具：data2image —— 把结果文件渲染成折线图 PNG，供视觉分析降上下文。

与 ``read_image`` 的分工（转图与读图分两步各自管各自）：
- 本工具只做「结果文件 → 折线图 PNG → outputs 存档」，不把 base64 塞进工具结果
  （同 read_image 的设计：避免 checkpoint 膨胀）；
- 生成后模型用 ``read_image`` 读取图片，由 ``ImageInjectionMiddleware`` 注入视觉上下文。

来源无关：不关心文件是 SQL 查询卸载、接口返回还是脚本产物，只看数据形状——
行字典数组 / ChatData 结果集（columns+data）/ CSV 表格，由模型指定 X/Y 字段名。
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from apps.chat_data_app.agent.workspace.runtime import ConversationWorkspaceRuntime, resolve_workspace_path
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


def _safe_filename_stem(title: str) -> str:
    """标题转文件名安全片段；清洗后为空则回退 'data'。"""
    cleaned = _FILENAME_SAFE_PATTERN.sub("_", title.strip())[:40].strip("_")
    return cleaned or "data"


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
        把工作区内的结果文件（JSON/CSV 表格）渲染成折线图 PNG 存到 outputs，供视觉分析降低上下文消耗。

        典型用法：data_analyzing_tool 查询结果已自动保存为
        /mnt/user-data/workspace/.system/query_result/<result_id>.json（工具回执里的 result_path），
        直接把该路径传给本工具的 file_path 即可，**不要先读取或重写数据文件**——
        本工具在服务端读完整文件，能解析嵌套结果集（columns+data / results 信封 / 行数组 / CSV）。
        这样原始数据全程不进上下文，是本工具的核心价值。

        适用：大结果文件的时间趋势分析或多指标随时间的对比。
        不适用：需要精确取值或逐行核对的问题——请继续用 file_read 分页读取。
        生成成功后用 read_image 读取图片进行视觉分析（转图与读图分两步）。

        X 轴只接受时间列，格式必须为：YYYY、YYYY-MM、YYYY-MM-DD、YYYY-MM-DD HH:MM[:SS]、
        斜杠分隔的 YYYY/MM、YYYY/MM/DD 或 YYYY/MM/DD HH:MM[:SS]，或 ISO 8601
        （T 分隔、YYYYMMDD）。不支持毫秒/小数秒、时区（Z 或 ±HH:MM）、UNIX 时间戳（秒/毫秒）、
        中文日期、DD/MM/YYYY、季度（YYYY-Qn）和自然语言日期。

        Args:
            file_path: 结果文件路径（/mnt/user-data/workspace/.system/query_result/、/mnt/user-data/{workspace,outputs}/ 下的 JSON 或 CSV），支持嵌套结果集，无需预先提取。
            x_field: X 轴时间字段名，必填；必须符合上述时间格式，无法解析会报错并列出可用格式。
            y_fields: Y 轴字段名列表（数值列），1~5 个；超过 5 条不自动拆图，会报错并要求分批调用。
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
        return tool_result_payload("data2image", {"success": False, "error": "y_fields 不能为空；请传 1~5 个数值列名"})
    if len(cleaned_fields) > _MAX_Y_FIELDS:
        return tool_result_payload(
            "data2image",
            {
                "success": False,
                "error": f"y_fields 一次最多 {_MAX_Y_FIELDS} 个字段；请拆成多次调用，每张图最多 5 条曲线",
            },
        )
    if not str(x_field or "").strip():
        return tool_result_payload("data2image", {"success": False, "error": "x_field 不能为空；请传时间列名"})

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
        return tool_result_payload("data2image", {"success": False, "error": str(exc)})
    except OSError as exc:
        logger.error("data2image 读文件失败: %s", exc, exc_info=True)
        return tool_result_payload("data2image", {"success": False, "error": "结果文件读取失败"})

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
        return tool_result_payload("data2image", {"success": False, "error": f"图表 PNG 写入 outputs 失败: {exc}"})

    skipped_total = sum(int(item.get("skipped_rows", 0)) for item in series)
    logger.info(
        "[data2image] 生成 %d 张折线图: file=%s series=%d skipped_rows=%d",
        len(saved_images),
        file_path,
        len(series),
        skipped_total,
    )
    return tool_result_payload(
        "data2image",
        {
            "success": True,
            "images": saved_images,
            "skipped_rows": skipped_total,
            "next_step": "用 read_image 读取上述 file_path，看图分析趋势形态后回答用户；需要精确数值时按图上范围渐近取数：用 file_path 分页读取原始结果文件，或用 SQL 对对应区间重新精确查询",
        },
    )


def _read_result_text(workspace_runtime: ConversationWorkspaceRuntime, file_path: str) -> str:
    """读取结果文件文本；仅会话工作区可读，带大小上限。"""
    from apps.chat_data_app.agent.workspace.chart_render import ChartRenderError

    root = workspace_runtime.workspace_root
    physical = resolve_workspace_path(root, file_path, writable=False)
    if physical.suffix.lower() not in {".json", ".csv"}:
        raise ChartRenderError(f"仅支持 JSON/CSV 结果文件，当前后缀: {physical.suffix or '(无)'}")
    size_bytes = physical.stat().st_size
    if size_bytes > _MAX_RESULT_FILE_BYTES:
        raise ChartRenderError(f"结果文件过大（{size_bytes} 字节 > {_MAX_RESULT_FILE_BYTES}），请先缩减数据范围")
    return physical.read_text(encoding="utf-8")
