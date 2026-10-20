"""工具参数流式进度事件的纯函数集合。

从 agent_stream.py 拆出（该文件顶在 2000 行红线）：工具参数流式进度事件
（行数统计 / intent 预渲染）。SSE 序列化与帧名已收敛到协议层 ``sse_protocol``，
本模块只保留 ``_format_sse`` 别名以维持既有调用点。实时生成速率由 agent_stream 的
generation_speed 滑窗采样负责（原 stream_stats 参数字符上报已随前端本地估算下线）。
"""

from typing import Any

from .agent_run_support import _get_timestamp
from .sse_protocol import SSE_EVENT_TOOL_PROGRESS
from .sse_protocol import format_sse as _format_sse

# 需要实时显示生成行数的工具：added_field 计 "+N"（写入行数），
# removed_field 计 "-M"（被替换移除行数，file_edit 整段替换口径）。
_STREAMING_LINE_COUNT_TOOLS: dict[str, dict[str, str]] = {
    "file_write": {"added_field": "content"},
    "file_edit": {"added_field": "new_content", "removed_field": "old_content"},
}


def _extract_streaming_text_field(args_text: str, field_name: str) -> tuple[str | None, bool]:
    """从生成中的 args JSON 里提取指定字符串字段的当前值（可能不完整）。

    模型流式输出工具参数时，args 是 ``{"file_path":"...","content":"..."}`` 这样的
    JSON 片段。在 content 生成过程中 JSON 尚未闭合，无法用 json.loads 解析；
    这里用状态机直接定位 ``"<field_name>":"`` 之后的字符串值，处理转义字符。
    返回 ``(value, closed)``：``closed`` 表示字符串已闭合（值最终确定）。
    """
    marker = f'"{field_name}"'
    idx = args_text.find(marker)
    if idx < 0:
        return None, False
    rest = args_text[idx + len(marker) :]
    colon = rest.find(":")
    if colon < 0:
        return None, False
    rest = rest[colon + 1 :].lstrip()
    if not rest.startswith('"'):
        return None, False
    out: list[str] = []
    i = 1
    closed = False
    while i < len(rest):
        c = rest[i]
        if c == "\\" and i + 1 < len(rest):
            nxt = rest[i + 1]
            mapping = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/"}
            out.append(mapping.get(nxt, nxt))
            i += 2
        elif c == '"':
            closed = True  # 字符串闭合
            break
        else:
            out.append(c)
            i += 1
    return "".join(out), closed


def _count_text_lines(text: str) -> int:
    """统计文本行数（空文本 0 行；以换行结尾不计额外空行）。"""
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _format_tool_progress_event(
    tool_call_id: str,
    tool_name: str,
    content_lines: int | None,
    removed_lines: int | None,
    intent: str | None = None,
) -> str:
    """工具执行中推送进度事件：file_write/file_edit 的实时行数（+N / -M）、通用 intent 预渲染。

    行数与 intent 字段按需携带：非行数统计工具的事件只带 intent，避免前端把 0 当成有效行数。
    """
    data: dict[str, Any] = {
        "type": SSE_EVENT_TOOL_PROGRESS,
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "timestamp": _get_timestamp(),
    }
    if content_lines is not None:
        data["content_lines"] = content_lines
    if removed_lines is not None:
        data["removed_lines"] = removed_lines
    if intent:
        data["intent"] = intent
    return _format_sse(SSE_EVENT_TOOL_PROGRESS, data)


def _extract_streaming_progress_inputs(
    args_text: str,
    fields: dict[str, str] | None,
) -> tuple[tuple[int, int] | None, str | None]:
    """从流式 args 中计算行数统计与闭合后的 intent；纯计算，不涉及任何状态。

    - 行数：整段替换口径，added_field 当前行数为写入行数，removed_field（file_edit）为被替换行数；
      非 行数统计工具（fields 为空）返回 (None, intent)。
    - intent：字符串未闭合时丢弃，避免部分前缀被当作最终值推送。
    """
    added: str | None = None
    removed: str | None = None
    if fields:
        added, _ = _extract_streaming_text_field(args_text, fields["added_field"])
        if fields.get("removed_field"):
            removed, _ = _extract_streaming_text_field(args_text, fields["removed_field"])
        else:
            removed = ""
    counts: tuple[int, int] | None = None
    if added is not None or removed is not None:
        counts = (_count_text_lines(added or ""), _count_text_lines(removed or ""))
    intent, intent_closed = _extract_streaming_text_field(args_text, "intent")
    if not intent_closed:
        intent = None
    return counts, intent
