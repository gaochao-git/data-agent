"""agent_stream 的思考流 / 工具事件 / 工具流状态机 / 工具参数运行态助手（行数红线拆出）。

包含 _ToolStreamState（LangGraph 流式工具调用状态机）与其消费的纯函数/常量：
agent_stream 以 re-export 方式引用，既有模块属性引用（含测试的
``agent_stream._xxx`` 直接调用）不受影响。本模块不得反向依赖 agent_stream，
避免循环导入。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any
from uuid import UUID

import openai
from langchain_core.callbacks import AsyncCallbackHandler
from langgraph.errors import GraphRecursionError

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
    "subagent": "子智能体",
}


class _GraphStepTracker(AsyncCallbackHandler):
    """从 LangGraph 节点回调提取本轮真实引擎步数（同一步的并行节点只计一次）。"""

    def __init__(self) -> None:
        self.supersteps = 0
        self._first_step: int | None = None

    def wrap_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """复制一份 astream 配置并追加步数回调，不改动原始 config。"""
        stream_config = dict(config)
        callbacks = config.get("callbacks")
        if callbacks is None:
            stream_config["callbacks"] = [self]
        elif isinstance(callbacks, (list, tuple)):
            stream_config["callbacks"] = [*callbacks, self]
        else:
            stream_config["callbacks"] = [callbacks, self]
        return stream_config

    async def on_chain_start(
        self,
        serialized: dict[str, Any],
        inputs: dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        _ = serialized, inputs, run_id, parent_run_id, tags, kwargs
        if not isinstance(metadata, dict) or not metadata.get("langgraph_node"):
            return
        # 子图 callback 的 namespace 含 ``|``，不能用它的 step 覆盖主图步数。
        if "|" in str(metadata.get("langgraph_checkpoint_ns") or ""):
            return
        step = metadata.get("langgraph_step")
        if not isinstance(step, int):
            return
        if self._first_step is None:
            self._first_step = step
        self.supersteps = max(self.supersteps, step - self._first_step + 1)


def _graph_recursion_failure_message(exc: Exception) -> str | None:
    """将 LangGraph 超步中断转成可展示文案；其他异常返回 None。"""
    if not isinstance(exc, GraphRecursionError):
        return None
    match = re.search(r"Recursion limit of (\d+)", str(exc))
    if match:
        return f"本轮执行已达到 {match.group(1)} 步上限，任务被中止。请缩小任务范围、拆分任务，或新开会话后重试。"
    return "本轮执行已达到步数上限，任务被中止。请缩小任务范围、拆分任务，或新开会话后重试。"


def _is_omlx_prefill_memory_rejected(exc: Exception) -> bool:
    """识别 oMLX 内存护栏拒绝（prefill_memory_exceeded）。

    openai SDK 已把 400 响应体解析进 ``APIStatusError.body``，按结构取 code；
    body 缺失或形状变化时按 message 关键字兜底。命中时重试无效——上下文
    体量不变则服务端仍会拒绝。
    """
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        payload = error if isinstance(error, dict) else body
        code = str(payload.get("omlx_code") or payload.get("code") or "")
        if code == "prefill_memory_exceeded":
            return True
    return "prefill_memory_exceeded" in str(exc)


def _extract_status_error_message(exc: Exception) -> str | None:
    """从 APIStatusError 提取服务商 message 文本用于用户透出。

    只取 body.error.message（body 为字符串时取原文），剥离 request_id 噪音；
    空值或超长（>200 字符，防服务商透出大段内部诊断）返回 None，由调用方走
    兜底文案。永不透出堆栈与响应原文全文。
    """
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        payload = error if isinstance(error, dict) else body
        message = str(payload.get("message") or "").strip()
    elif isinstance(body, str):
        message = body.strip()
    else:
        return None
    message = re.sub(r"\s*\(request_id[:：][^)]*\)", "", message, flags=re.IGNORECASE).strip()
    return message if 0 < len(message) <= 200 else None


def _model_failure_user_message(exc: Exception) -> str:
    """将模型服务异常映射为用户可读文案；不向用户透出原始错误细节。"""
    if isinstance(exc, openai.APITimeoutError):
        return "模型服务响应超时，请点击继续重试。"
    if isinstance(exc, openai.AuthenticationError):
        return "模型服务认证失败，请检查模型服务 API Key 配置后重试。"
    if isinstance(exc, openai.RateLimitError):
        return "模型服务当前繁忙，请稍后点击继续重试，或更换其他模型。"
    if isinstance(exc, openai.APIConnectionError):
        return "模型服务连接中断，请点击继续重试。"
    if isinstance(exc, TimeoutError):
        # 含 langchain-openai StreamChunkTimeoutError（其为内置 TimeoutError 子类）：
        # TCP 连接存活但长时间无任何流式内容返回（模型服务挂起/过载），由分块超时看门狗主动中断
        return "模型服务长时间未返回内容，请点击继续重试，或稍后再试。"
    if _is_omlx_prefill_memory_rejected(exc):
        # oMLX 内存护栏拒绝的 400：上下文体量超服务端推理内存上限，重试无效，
        # 引导用户缩减上下文而不是反复点击重试
        return "本轮对话上下文超出模型服务的内存上限，请新开会话，或减少本条消息引用的文件后重试。"
    if isinstance(exc, openai.APIStatusError):
        # 非枚举状态码（402 余额不足、403 配额、404 模型下线、5xx 网关错等）：
        # 透出服务商 message（提取+护栏），取不到时走兜底。具体子类
        # （超时/认证/限流）已在上方命中，不会进入本分支。
        detail = _extract_status_error_message(exc)
        if detail:
            return f"模型服务返回错误：{detail}"
    return _graph_recursion_failure_message(exc) or "智能问数执行失败，请稍后重试。"


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


def _args_signature(args: dict[str, Any]) -> str:
    """参数内容指纹：只用于判断前端当前版本是否变化，不保留整份参数（可能含大文件内容）。"""
    payload = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.blake2b(payload.encode("utf-8"), digest_size=16).hexdigest()


def _parse_tool_args_buffer(args_buffer: str, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    """解析流式工具参数，并用已知结构化参数补齐流式碎片缺省的字段。

    fallback 来自完整 tool_call 的结构化 args，可能是比流式 buffer 更新的权威快照；
    流式 buffer 解析成功时也不能直接替换它，否则模型后补的字段（例如 timeout_seconds）
    会在完成事件里丢失。同名键以流式值为准，只补 buffer 里没有的键。
    """
    fallback_args = fallback or {}
    if not args_buffer:
        return fallback_args
    parsed = _coerce_tool_args(args_buffer)
    if parsed is None:
        logger.warning("无法解析工具参数，使用兜底参数: %s", args_buffer[:200])
        return fallback_args
    return {**fallback_args, **parsed}


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
        # values 快照里最终 AIMessage.tool_calls 的参数：执行侧真正使用的口径。
        # 流式 buffer / fallback 都可能只是中间版本，完成事件必须以这份为准。
        self.authoritative_args: dict[str, dict[str, Any]] = {}
        # 已经下发给前端的参数指纹，用于判断是否需要补发替换；不保留参数正文。
        self.sent_args_signature: dict[str, str] = {}
        self.index_map: dict[int, str] = {}
        self.running_tool_ids: list[str] = []
        self.emitted_running_tool_ids: set[str] = set()
        self.active_tool_call_id: str | None = None
        # 已推送的实时行数 (added, removed)，用于行数未变化时去重，避免 SSE 事件洪峰
        self.progress_line_counts: dict[str, tuple[int, int]] = {}
        # 已推送的流式 intent，用于 intent 未变化时去重
        self.progress_intents: dict[str, str] = {}
        # 工具开始运行的后端时间（tool_started 事件时刻）：完成事件缺失 ToolTimingMiddleware
        # 盖章时（如被技能前置/审批等外层中间件拦下的失败）用它兜底，保证每个工具都有耗时。
        self.tool_started_at: dict[str, int] = {}
        # 已下发完成事件的工具：values 快照补发时据此去重，避免同一结果重复入 UI。
        self.finished_tool_ids: set[str] = set()
        # 已被判定「模型最终没有真正调用」的占位工具，避免重复下发取消事件。
        self.cancelled_tool_ids: set[str] = set()

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
        # subagent 工具携带的子图 token 用量：随完成事件下发，前端任务卡/过程面板展示
        subagent_token_usage = tool_additional_kwargs.get("subagent_token_usage")
        if isinstance(subagent_token_usage, dict) and subagent_token_usage:
            msg_data["subagent_token_usage"] = subagent_token_usage
        # subagent 本次派发的图推进次数：随完成事件下发，历史回放走 tool_result artifacts。
        subagent_supersteps = tool_additional_kwargs.get("subagent_supersteps")
        if isinstance(subagent_supersteps, int) and subagent_supersteps > 0:
            msg_data["subagent_supersteps"] = subagent_supersteps
        subagent_recursion_limit = tool_additional_kwargs.get("subagent_recursion_limit")
        if isinstance(subagent_recursion_limit, int) and subagent_recursion_limit > 0:
            msg_data["subagent_recursion_limit"] = subagent_recursion_limit
        if tool_call_id:
            self.finished_tool_ids.add(tool_call_id)
            self.cancelled_tool_ids.discard(tool_call_id)
            msg_data["accumulated_args"] = self.authoritative_args.get(tool_call_id) or _parse_tool_args_buffer(
                self.args_buffer.get(tool_call_id, ""), self.args_fallback.get(tool_call_id)
            )
            self.args_buffer.pop(tool_call_id, None)
            self.args_fallback.pop(tool_call_id, None)
            # 完成事件里的参数就是前端当前看到的版本；保留它用于后续 values 快照
            # 比对。若最终 AIMessage 比 ToolMessage 晚到（after_model 等中间件场景），
            # 仍可补发一次替换，且同一快照不会重复下发。
            self.sent_args_signature[tool_call_id] = _args_signature(msg_data["accumulated_args"])
            self.authoritative_args.pop(tool_call_id, None)
            self.progress_line_counts.pop(tool_call_id, None)
            self.progress_intents.pop(tool_call_id, None)
        self.active_tool_call_id = _finish_running_tool(self.running_tool_ids, self.active_tool_call_id, tool_call_id)
        return msg_data

    @staticmethod
    def _latest_ai_index(messages: list[Any]) -> int:
        for index in range(len(messages) - 1, -1, -1):
            if getattr(messages[index], "type", "") == "ai":
                return index
        return -1

    def _missing_tool_events(self, messages: list[Any], latest_ai_index: int) -> list[str]:
        """补发只出现在 values 快照里的 ToolMessage。"""
        events: list[str] = []
        for message in messages[latest_ai_index + 1 :]:
            if getattr(message, "type", "") != "tool":
                continue
            tool_call_id = str(getattr(message, "tool_call_id", "") or "").strip()
            if (
                not tool_call_id
                or tool_call_id in self.finished_tool_ids
                or tool_call_id not in self.emitted_running_tool_ids
            ):
                continue
            payload = self.build_tool_message_payload(message)
            events.append(self.build_completion_event(payload))
            events.append(_format_sse(SSE_EVENT_TOOL, payload))
        return events

    @staticmethod
    def _actual_tool_ids(latest_ai: Any) -> set[str]:
        actual_ids = {
            str(tool_call.get("id") or "").strip()
            for tool_call in (getattr(latest_ai, "tool_calls", None) or [])
            if isinstance(tool_call, dict)
        }
        actual_ids.discard("")
        return actual_ids

    def _orphan_tool_ids(self, actual_ids: set[str]) -> list[str]:
        return [
            tool_call_id
            for tool_call_id in list(self.running_tool_ids)
            if tool_call_id not in actual_ids
            and tool_call_id not in self.finished_tool_ids
            and tool_call_id not in self.cancelled_tool_ids
        ]

    def reconcile_state_messages(self, messages: Any) -> list[str]:
        """用 LangGraph values 快照收口工具生命周期。

        流式协议会先播报 tool_call_chunks 的 running 占位，但模型可能在同一条
        回复里重新规划，最终 AIMessage 并不包含这些调用（后端从未执行，也就没有
        ToolMessage）。另外 ``after_model`` 等中间件生成的拒绝 ToolMessage 可能
        只出现在 values 里、没有进 messages 流。两种缺口都会让前端工具卡一直转。

        这里以快照为准做两件事：补发漏掉的 ToolMessage 完成事件；把未进入最新
        AIMessage.tool_calls 的 running 占位以失败事件收口（文案明确「未执行」）。
        """
        if not isinstance(messages, list) or not messages:
            return []

        latest_ai_index = self._latest_ai_index(messages)
        if latest_ai_index < 0:
            return []

        events = self._publish_authoritative_tool_args(messages[latest_ai_index])
        events.extend(self._missing_tool_events(messages, latest_ai_index))
        actual_ids = self._actual_tool_ids(messages[latest_ai_index])
        for tool_call_id in self._orphan_tool_ids(actual_ids):
            events.append(self._cancel_orphaned_tool(tool_call_id))
        return events

    def _publish_authoritative_tool_args(self, latest_ai: Any) -> list[str]:
        """把最终 AIMessage 的完整参数补发给已播报的工具，作为整份替换快照。

        流式 args_buffer 只包含模型逐字生成的片段，模型在调用参数之外补写的字段
        （例如由服务端补默认值、或分片丢字）不会进入 buffer；完成事件若只信 buffer，
        工具详情就会展示缺字段的参数。values 快照里的 AIMessage.tool_calls 是最终
        口径；这里与已下发参数比较，变化时补一条带 replace_args 的 tool_progress，
        前端整份替换参数，既不保留流式中间版本的多余字段，也不改变生命周期状态。
        """
        events: list[str] = []
        for tool_call in getattr(latest_ai, "tool_calls", None) or []:
            if not isinstance(tool_call, dict):
                continue
            tool_call_id = str(tool_call.get("id") or "").strip()
            args = _coerce_tool_args(tool_call.get("args"))
            if not tool_call_id or not args or tool_call_id not in self.emitted_running_tool_ids:
                continue
            signature = _args_signature(args)
            if self.sent_args_signature.get(tool_call_id) == signature:
                self.authoritative_args[tool_call_id] = args
                continue
            self.authoritative_args[tool_call_id] = args
            self.args_fallback[tool_call_id] = args
            self.sent_args_signature[tool_call_id] = signature
            events.append(
                _format_tool_progress_event(
                    tool_call_id,
                    self.tool_names.get(tool_call_id, ""),
                    None,
                    None,
                    None,
                    args,
                    replace_args=True,
                )
            )
        return events

    def _cancel_orphaned_tool(self, tool_call_id: str) -> str:
        """关闭「播报过但模型最终没有真正调用」的占位工具卡。"""
        tool_name = self.tool_names.get(tool_call_id, "tool_call")
        started_at_ms = self.tool_started_at.get(tool_call_id)
        ended_at_ms = int(time.time() * 1000)
        payload = {
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "display_name": get_mcp_tool_display_name(tool_name),
            "status": "error",
            "args": self.authoritative_args.get(tool_call_id) or self.args_fallback.get(tool_call_id) or {},
            "content": "本次工具调用未执行：模型重新规划了调用。",
            "message": "未执行",
            "started_at_ms": started_at_ms,
            "ended_at_ms": max(ended_at_ms, started_at_ms or ended_at_ms),
        }
        if tool_call_id in self.running_tool_ids:
            self.running_tool_ids.remove(tool_call_id)
        self.tool_started_at.pop(tool_call_id, None)
        self.progress_line_counts.pop(tool_call_id, None)
        self.progress_intents.pop(tool_call_id, None)
        self.args_buffer.pop(tool_call_id, None)
        self.args_fallback.pop(tool_call_id, None)
        self.authoritative_args.pop(tool_call_id, None)
        self.sent_args_signature.pop(tool_call_id, None)
        self.cancelled_tool_ids.add(tool_call_id)
        return _format_tool_lifecycle_event(SSE_EVENT_TOOL_FAILED, payload)

    def build_completion_event(self, tool_payload: dict[str, Any]) -> str:
        """将工具结果映射为完成或失败的统一生命周期事件。"""
        status = str(tool_payload.get("status") or "completed")
        event_type = SSE_EVENT_TOOL_FAILED if status == "error" else SSE_EVENT_TOOL_COMPLETED
        data = dict(tool_payload)
        # 前端 onToolLifecycle 只读 args 字段，补齐一份与 accumulated_args 同源，直播侧才能展示参数。
        data["args"] = tool_payload.get("accumulated_args") or {}
        self._fill_missing_timing(data)
        return _format_tool_lifecycle_event(event_type, data)

    def _fill_missing_timing(self, data: dict[str, Any]) -> None:
        """补齐工具完成事件的起止时间（后端计时，前端只展示不计算）。

        正常路径由 ToolTimingMiddleware 盖章；被外层中间件（技能前置/审批/MCP 拦截等）
        提前拒绝时 ToolMessage 上没有时间，这里用 tool_started 事件时刻兜底。
        """
        tool_call_id = str(data.get("tool_call_id") or "")
        started_at_ms = data.get("started_at_ms") or self.tool_started_at.get(tool_call_id)
        ended_at_ms = data.get("ended_at_ms")
        if not started_at_ms:
            return
        if not ended_at_ms:
            ended_at_ms = int(time.time() * 1000)
        data["started_at_ms"] = started_at_ms
        data["ended_at_ms"] = max(int(ended_at_ms), int(started_at_ms))
        if tool_call_id:
            self.tool_started_at.pop(tool_call_id, None)

    def _running_event(self, tool_call_id: str, tool_name: str, args: dict[str, Any] | None) -> list[str]:
        _register_running_tool(self.running_tool_ids, tool_call_id)
        if tool_call_id in self.emitted_running_tool_ids:
            return []
        self.emitted_running_tool_ids.add(tool_call_id)
        self.sent_args_signature[tool_call_id] = _args_signature(args or {})
        started_at_ms = int(time.time() * 1000)
        self.tool_started_at.setdefault(tool_call_id, started_at_ms)
        payload = {
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            # MCP 工具的用户可见展示名（plugin.json tools.<name>.label）；内置工具为空串由前端映射表兜底
            "display_name": get_mcp_tool_display_name(tool_name),
            "status": "running",
            "args": args or {},
            # 直播侧 tool_started 占位即带开始时间（epoch 毫秒）；结果事件以 ToolTimingMiddleware 盖章值为准。
            "started_at_ms": started_at_ms,
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
