"""agent_stream 的思考流 / 工具事件 / 工具流状态机 / 工具参数运行态助手（行数红线拆出）。

包含 _ToolStreamState（LangGraph 流式工具调用状态机）与其消费的纯函数/常量：
agent_stream 以 re-export 方式引用，既有模块属性引用（含测试的
``agent_stream._xxx`` 直接调用）不受影响。本模块不得反向依赖 agent_stream，
避免循环导入。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from utils.logger import get_logger
from utils.tool_call_args import coerce_tool_call_args

from .agent.mcp.manager import get_mcp_tool_display_name
from .agent_run_support import _get_timestamp
from .sse_protocol import (
    SSE_EVENT_THINKING,
    SSE_EVENT_TOOL,
    SSE_EVENT_TOOL_COMPLETED,
    SSE_EVENT_TOOL_FAILED,
    SSE_EVENT_TOOL_STARTED,
)
from .tool_stream_progress import (
    _STREAMING_LINE_COUNT_TOOLS,
    _extract_streaming_progress_inputs,
    _format_sse,
    _format_tool_progress_event,
)

logger = get_logger(__name__)

REASONING_TOOL_DISPLAY_NAMES = {
    "data_analyzing_tool": "数据分析",
    "calculator_tool": "精确计算",
    "file_read": "读取文件",
    "file_edit": "修改文件",
    "file_write": "写入文件",
    "file_move": "移动文件",
    "file_copy": "复制文件",
    "file_delete": "删除文件",
    "parse_document": "解析文档",
    "file_glob": "查找文件",
    "file_grep": "搜索文本",
    "file_diff": "对比文件",
    "dir_create": "创建目录",
    "dir_list": "列出目录",
    "dir_delete": "删除目录",
    "bash_run": "运行脚本",
    "officecli_run": "运行 OfficeCLI",
    "get_current_time": "获取当前时间",
    "present_artifacts": "展示文件",
    "ask_user_question": "询问用户",
    "task": "派发子智能体",
}


def _collect_reasoning_candidates(message_chunk: Any, msg_data: dict[str, Any] | None) -> list[Any]:
    """按优先级收集可能携带思考内容的字典候选。"""
    candidates: list[Any] = []
    for attr in ("additional_kwargs", "response_metadata"):
        value = getattr(message_chunk, attr, None)
        if isinstance(value, dict):
            candidates.append(value)
    if isinstance(msg_data, dict):
        for key in ("additional_kwargs", "response_metadata"):
            value = msg_data.get(key)
            if isinstance(value, dict):
                candidates.append(value)
        candidates.append(msg_data)
    return candidates


def _extract_reasoning_from_candidates(candidates: list[Any]) -> str:
    """从候选字典中按固定 key 顺序提取首个非空思考内容。"""
    for candidate in candidates:
        for key in ("reasoning_content", "reasoning", "reasoning_text"):
            value = candidate.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def _extract_reasoning_content(message_chunk: Any, msg_data: dict[str, Any] | None = None) -> str:
    """从 OpenAI compatible 流式 chunk 中提取思考内容。"""
    return _extract_reasoning_from_candidates(_collect_reasoning_candidates(message_chunk, msg_data))


def _compile_reasoning_tool_name_patterns() -> list[tuple[re.Pattern[str], str]]:
    """按 ASCII 词边界编译工具名匹配，避免短名命中长词里的子串。

    ``task`` 是四字母常见英文词，朴素 ``str.replace`` 会把 ``subtask`` /
    ``task_id`` / ``tasks`` 一起替换掉。此处要求工具名两侧都不是 ASCII 词字符
    （字母、数字、下划线）；中文、标点、反引号等照常命中。
    """
    return [
        (re.compile(rf"(?<![0-9A-Za-z_]){re.escape(tool_name)}(?![0-9A-Za-z_])"), display_name)
        for tool_name, display_name in REASONING_TOOL_DISPLAY_NAMES.items()
    ]


_REASONING_TOOL_NAME_PATTERNS = _compile_reasoning_tool_name_patterns()


def _format_reasoning_for_display(content: str) -> str:
    """将思考流中的内部工具名替换为更短的展示名，不影响真实工具调用。"""
    text = str(content or "")
    for pattern, display_name in _REASONING_TOOL_NAME_PATTERNS:
        text = pattern.sub(display_name, text)
    return text


def _format_thinking_event(message_chunk: Any, msg_data: dict[str, Any] | None = None) -> str | None:
    reasoning_content = _format_reasoning_for_display(_extract_reasoning_content(message_chunk, msg_data))
    if not reasoning_content:
        return None
    return _format_sse(
        SSE_EVENT_THINKING,
        {
            "content": reasoning_content,
            "timestamp": _get_timestamp(),
        },
    )


def _append_reasoning_segment(
    segments: list[dict[str, Any]],
    content: str,
    tool_call_count: int,
    reasoning_turn_id: str | None = None,
) -> None:
    content = _format_reasoning_for_display(content)
    if not content:
        return
    if (
        segments
        and segments[-1].get("tool_call_count") == tool_call_count
        and segments[-1].get("reasoning_turn_id") == reasoning_turn_id
    ):
        segments[-1]["content"] = f"{segments[-1].get('content') or ''}{content}"
        return
    segment = {
        "id": f"reasoning-{reasoning_turn_id}-{len(segments)}" if reasoning_turn_id else f"reasoning-{len(segments)}",
        "content": content,
        "tool_call_count": tool_call_count,
    }
    if reasoning_turn_id:
        segment["reasoning_turn_id"] = reasoning_turn_id
    segments.append(segment)


def _visible_tool_count(tool_names: dict[str, str]) -> int:
    """计算已出现的工具数量，用于恢复思考与工具的时序。"""
    return len(tool_names)


def _format_tool_event(
    tool_name: str,
    tool_call_id: str | None,
    status: str,  # "running" | "completed"
    args: dict | None = None,
    result: str | None = None,
    message: str | None = None,
    timestamp: str | None = None,
) -> str:
    """
    格式化统一的 tool 事件

    Args:
        tool_name: 工具名称
        tool_call_id: 工具调用 ID
        status: 状态 (running, completed)
        args: 工具参数
        result: 工具执行结果（completed 状态时使用）
        message: 提示信息
        timestamp: 时间戳（格式：YYYY-MM-DD HH:MM:SS）
    """
    data: dict[str, Any] = {
        "type": SSE_EVENT_TOOL,
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "status": status,
        "timestamp": timestamp or _get_timestamp(),
    }
    if args is not None:
        data["args"] = args
    if result is not None:
        data["result"] = result
        data["content"] = result
    if message is not None:
        data["message"] = message
    return _format_sse(SSE_EVENT_TOOL, data)


def _format_tool_lifecycle_event(
    event_type: str,
    payload: dict[str, Any],
) -> str:
    """发送工具生命周期事件，保留旧 ``tool`` 事件供现有客户端兼容。"""
    data = dict(payload)
    data["type"] = event_type
    data.setdefault("timestamp", _get_timestamp())
    return _format_sse(event_type, data)


def _coerce_tool_args(value: Any) -> dict[str, Any] | None:
    """工具调用参数规整（字符串/双层编码 → dict），统一收口在 utils.tool_call_args。"""
    return coerce_tool_call_args(value)


def _parse_tool_args_buffer(args_buffer: str, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    """解析流式工具参数，解析失败时回退到已知结构化参数。"""
    fallback_args = fallback or {}
    if not args_buffer:
        return fallback_args
    parsed = _coerce_tool_args(args_buffer)
    if parsed is None:
        logger.warning("无法解析工具参数，使用兜底参数: %s", args_buffer[:200])
        return fallback_args
    return parsed


def _is_tool_result_error(content: Any, native_status: Any = None) -> bool:
    """识别工具结果失败状态，便于前端展示。

    成败只看两个显式协议信号，不做任何文本猜测：
    1. ``ToolMessage.status == "error"``——工具 raise 后由 ToolErrorMiddleware
       等失败路径显式标记；
    2. query_result envelope 的 ``failed``/``cancelled``——SQL 执行的业务失败。
    """
    if str(native_status or "").strip().lower() == "error":
        return True
    text = str(content or "").strip()
    if not text:
        return False
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return False
    return (
        isinstance(payload, dict)
        and payload.get("type") == "query_result"
        and str(payload.get("status") or "").strip().lower() in {"failed", "cancelled"}
    )


def _register_running_tool(running_tool_ids: list[str], tool_call_id: str) -> None:
    """登记运行中的工具，保持插入顺序。"""
    if tool_call_id and tool_call_id not in running_tool_ids:
        running_tool_ids.append(tool_call_id)


def _resolve_active_tool_call_id(
    chunk_id: str | None,
    active_tool_call_id: str | None,
    running_tool_ids: list[str],
) -> str | None:
    """解析当前 chunk 应归属的 tool_call_id。"""
    normalized_chunk_id = str(chunk_id or "").strip() or None
    if normalized_chunk_id:
        return normalized_chunk_id
    if active_tool_call_id:
        return active_tool_call_id
    if running_tool_ids:
        return running_tool_ids[-1]
    return None


def _finish_running_tool(
    running_tool_ids: list[str],
    active_tool_call_id: str | None,
    tool_call_id: str | None,
) -> str | None:
    """工具完成后清理运行态，并返回新的活跃 tool_call_id。"""
    normalized_tool_call_id = str(tool_call_id or "").strip()
    if normalized_tool_call_id and normalized_tool_call_id in running_tool_ids:
        running_tool_ids.remove(normalized_tool_call_id)
    if active_tool_call_id == normalized_tool_call_id:
        return running_tool_ids[-1] if running_tool_ids else None
    return active_tool_call_id


class _ToolStreamState:
    """维护 LangGraph 流式工具调用状态，统一 running 和完成事件字段。"""

    def __init__(self) -> None:
        self.tool_names: dict[str, str] = {}
        self.args_buffer: dict[str, str] = {}
        self.args_fallback: dict[str, dict[str, Any]] = {}
        self.index_map: dict[int, str] = {}
        self.running_tool_ids: list[str] = []
        self.emitted_running_tool_ids: set[str] = set()
        self.active_tool_call_id: str | None = None
        # 已推送的实时行数 (added, removed)，用于行数未变化时去重，避免 SSE 事件洪峰
        self.progress_line_counts: dict[str, tuple[int, int]] = {}
        # 已推送的流式 intent，用于 intent 未变化时去重
        self.progress_intents: dict[str, str] = {}

    def visible_tool_count(self) -> int:
        return _visible_tool_count(self.tool_names)

    def _handle_single_tool_call(self, tool_call: Any) -> list[str]:
        """登记单个完整 tool_call，返回其 running 事件。"""
        tool_call_id = str(tool_call.get("id") or "").strip()
        tool_name = str(tool_call.get("name") or "").strip()
        tool_index = tool_call.get("index")
        if not tool_call_id or not tool_name:
            return []
        self.tool_names[tool_call_id] = tool_name
        self.active_tool_call_id = tool_call_id
        if isinstance(tool_index, int):
            self.index_map[tool_index] = tool_call_id
        self.args_buffer.setdefault(tool_call_id, "")
        raw_args = tool_call.get("args")
        coerced_args = _coerce_tool_args(raw_args)
        if coerced_args is not None:
            self.args_fallback[tool_call_id] = coerced_args
        events = self._running_event(tool_call_id, tool_name, coerced_args)
        if isinstance(raw_args, str) and raw_args.strip():
            # 字符串形态的 args（含双层编码）进 buffer，完成后由 _parse_tool_args_buffer 统一解析。
            self.args_buffer[tool_call_id] = self.args_buffer.get(tool_call_id, "") + raw_args
        return events

    def handle_tool_calls(self, tool_calls: Any) -> list[str]:
        events: list[str] = []
        for tool_call in tool_calls or []:
            if not isinstance(tool_call, dict):
                continue
            events.extend(self._handle_single_tool_call(tool_call))
        return events

    def _register_tool_call_chunk(
        self,
        chunk_id: str,
        chunk_name: str,
        chunk_index: Any,
    ) -> str | None:
        """登记 chunk 的身份信息，返回本次应播报的 running 工具名（无则 None）。"""
        if not chunk_id:
            return None
        if chunk_name:
            self.tool_names[chunk_id] = chunk_name
        self.active_tool_call_id = chunk_id
        if isinstance(chunk_index, int):
            self.index_map[chunk_index] = chunk_id
        self.args_buffer.setdefault(chunk_id, "")
        running_tool_name = chunk_name or self.tool_names.get(chunk_id, "")
        return running_tool_name or None

    def _resolve_chunk_tool_call_id(self, chunk_id: str, chunk_index: Any) -> str | None:
        return (
            chunk_id
            or (self.index_map.get(chunk_index) if isinstance(chunk_index, int) else None)
            or _resolve_active_tool_call_id(None, self.active_tool_call_id, self.running_tool_ids)
        )

    def _streaming_progress_events(self, tool_call_id: str, tool_name: str, args_text: str) -> list[str]:
        """工具生成 args 期间推送进度事件：intent 预渲染（所有工具）+ 行数统计（file_write/file_edit）。

        - intent：args 里 intent 字符串闭合后推送一次（部分前缀不推，避免逐 chunk 洪峰）。
        - 行数：整段替换口径，added_field 当前行数为写入行数，removed_field（file_edit）为被替换行数；
          计数与上次一致时不重复推送。
        - 两类信息任一变化就合并推一个事件，行数字段仅行数统计工具携带。
        """
        counts, intent = _extract_streaming_progress_inputs(
            args_text,
            _STREAMING_LINE_COUNT_TOOLS.get(tool_name),
        )

        counts_changed = counts is not None and self.progress_line_counts.get(tool_call_id) != counts
        intent_changed = bool(intent) and self.progress_intents.get(tool_call_id) != intent
        if not counts_changed and not intent_changed:
            return []
        if counts is not None:
            self.progress_line_counts[tool_call_id] = counts
        if intent:
            self.progress_intents[tool_call_id] = intent
        # 行数未变化的合并事件不携带行数字段，前端保留已有值
        event_counts: tuple[int, int] | tuple[None, None] = (
            counts if counts_changed and counts is not None else (None, None)
        )
        return [
            _format_tool_progress_event(
                tool_call_id,
                tool_name,
                event_counts[0],
                event_counts[1],
                intent,
            )
        ]

    def handle_tool_call_chunks(self, tool_call_chunks: Any) -> list[str]:
        events: list[str] = []
        for chunk in tool_call_chunks or []:
            if not isinstance(chunk, dict):
                continue
            chunk_id = str(chunk.get("id") or "").strip()
            chunk_name = str(chunk.get("name") or "").strip()
            chunk_args = str(chunk.get("args") or "")
            chunk_index = chunk.get("index")
            running_tool_name = self._register_tool_call_chunk(chunk_id, chunk_name, chunk_index)
            if running_tool_name:
                events.extend(self._running_event(chunk_id, running_tool_name, {}))

            current_tool_call_id = self._resolve_chunk_tool_call_id(chunk_id, chunk_index)
            if current_tool_call_id and chunk_args:
                self.args_buffer[current_tool_call_id] = self.args_buffer.get(current_tool_call_id, "") + chunk_args
                # file_write / file_edit 生成 content 期间实时统计行数，前端工具卡片显示 "+N -M"
                tool_name = self.tool_names.get(current_tool_call_id, "")
                events.extend(
                    self._streaming_progress_events(
                        current_tool_call_id,
                        tool_name,
                        self.args_buffer[current_tool_call_id],
                    )
                )
        return events

    def build_tool_message_payload(self, message_chunk: Any) -> dict[str, Any]:
        tool_call_id = str(getattr(message_chunk, "tool_call_id", "") or "").strip()
        msg_data = message_chunk.model_dump()
        msg_data["type"] = "tool"
        msg_data["timestamp"] = _get_timestamp()
        msg_data["tool_name"] = self.tool_names.get(tool_call_id, msg_data.get("name") or "")
        # MCP 工具展示名随结果事件下发（tool_started 已带；这里补齐完成/失败事件）
        msg_data["display_name"] = get_mcp_tool_display_name(str(msg_data["tool_name"] or ""))
        msg_data["status"] = (
            "error" if _is_tool_result_error(msg_data.get("content"), msg_data.get("status")) else "completed"
        )
        # ToolTimingMiddleware 盖在 additional_kwargs 里的执行起止时间，提升为顶层字段供前端展示。
        tool_additional_kwargs = msg_data.get("additional_kwargs") or {}
        msg_data["started_at_ms"] = tool_additional_kwargs.get("started_at_ms")
        msg_data["ended_at_ms"] = tool_additional_kwargs.get("ended_at_ms")
        if tool_call_id:
            msg_data["accumulated_args"] = _parse_tool_args_buffer(
                self.args_buffer.get(tool_call_id, ""),
                self.args_fallback.get(tool_call_id),
            )
            self.args_buffer.pop(tool_call_id, None)
            self.args_fallback.pop(tool_call_id, None)
            self.progress_line_counts.pop(tool_call_id, None)
            self.progress_intents.pop(tool_call_id, None)
        self.active_tool_call_id = _finish_running_tool(self.running_tool_ids, self.active_tool_call_id, tool_call_id)
        return msg_data

    def build_completion_event(self, tool_payload: dict[str, Any]) -> str:
        """将工具结果映射为完成或失败的统一生命周期事件。"""
        status = str(tool_payload.get("status") or "completed")
        event_type = SSE_EVENT_TOOL_FAILED if status == "error" else SSE_EVENT_TOOL_COMPLETED
        data = dict(tool_payload)
        # 前端 onToolLifecycle 只读 args 字段，补齐一份与 accumulated_args 同源，直播侧才能展示参数。
        data["args"] = tool_payload.get("accumulated_args") or {}
        return _format_tool_lifecycle_event(event_type, data)

    def _running_event(self, tool_call_id: str, tool_name: str, args: dict[str, Any] | None) -> list[str]:
        _register_running_tool(self.running_tool_ids, tool_call_id)
        if tool_call_id in self.emitted_running_tool_ids:
            return []
        self.emitted_running_tool_ids.add(tool_call_id)
        payload = {
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            # MCP 工具的用户可见展示名（plugin.json tools.<name>.label）；内置工具为空串由前端映射表兜底
            "display_name": get_mcp_tool_display_name(tool_name),
            "status": "running",
            "args": args or {},
            # 直播侧 tool_started 占位即带开始时间（epoch 毫秒）；结果事件以 ToolTimingMiddleware 盖章值为准。
            "started_at_ms": int(time.time() * 1000),
        }
        return [
            _format_tool_lifecycle_event(SSE_EVENT_TOOL_STARTED, payload),
            _format_tool_event(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                status="running",
                args=args or {},
            ),
        ]
