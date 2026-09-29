"""ChatData completion handler."""

import json
import asyncio
import re
import time

import openai
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from functools import partial
from typing import Dict, Any, List, Optional
from uuid import uuid4

from fastapi import Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    ToolMessage,
    ToolMessageChunk,
)
from utils.alarm import emit_llm_connection_alarm_if_needed, emit_llm_first_token_timeout_alarm
from utils.logger import get_logger
from utils.tool_call_args import coerce_tool_call_args
from .services import conversation_service as conversation
from .agent import (
    create_data_agent,
    AgentRunControls,
    DatasourceContext,
    IdentityContext,
    ModelInvocationConfig,
    resolve_agent_runtime_options,
    get_subagent_hot_defaults,
    build_unified_system_prompt,
    get_checkpointer,
)
from .agent.context.context_budget import ContextInputLimitExceededError, sanitize_context_usage_for_response
from .agent.run.conversation_runtime import (
    TurnLifecycleHooks,
    TurnPersistenceHooks,
    stream_agent_turn,
)
from .agent.run.agent_inbox_service import AgentRunLeaseLostError, agent_inbox_service
from .agent.run.conversation_event_service import conversation_event_service
from .agent.run.agent_run_event_service import agent_run_event_service
from .agent.run.agent_run_runtime import agent_run_runtime
from .agent.run.chat_data_stream_request import ChatDataStreamRequest
from .agent.run.completion_options import parse_completion_run_options, parse_file_selections
from .agent.middleware.steering import SteeringMessageTracker
from .agent.workspace.runtime import is_system_result_workspace_path, resolve_workspace_path
from .agent.workspace.uploaded_file_parser import get_image_size_limit
from .agent.workspace.workspace_path_policy import USER_DATA_VIRTUAL_ROOT, WORKSPACE_PATH_POLICY
from .services.runtime_config import LLM_FIRST_TOKEN_TIMEOUT_SECONDS
from .services.setting_service import get_runtime_setting
from .services.memory.conversation_memory_service import (
    after_agent_turn_with_memory,
    render_session_memory_block,
)
from .services.agent_run.conversation_workspace import ConversationWorkspaceRuntime
from .services.agent_run.message_compose import (
    build_file_attachment_text,
    select_vision_attachments,
)
# 运行准备与历史持久化层（源模块：agent_run_support），以下均为 handlers 内部直接使用的符号。
from .agent_run_support import (
    USER_INPUT_OFFLOAD_THRESHOLD,
    _AgentRunPreparationError,
    _AgentRunStreamCallbacks,
    _LatestModelSnapshotWriter,
    _get_timestamp,
    _load_runtime_skills_xml,
    _mark_agent_resume_checkpointed,
    _merge_message_snapshots,
    _offload_oversized_user_input,
    _persist_agent_model_snapshot,
    _persist_agent_partial_history,
    _persist_agent_turn_history_snapshot,
    _persist_agent_user_message,
    _persist_latest_ai_message_timestamp,
    _prepare_agent_semantic_context,
    _prepare_agent_workspace_runtime,
    _resolve_agent_initial_state,
    _resolve_checkpointed_turn_state,
    _resolve_file_attachments,
    _write_agent_model_snapshot,
)
# 工具参数流式进度与 SSE 格式化（源模块：tool_stream_progress，agent_stream 行数红线拆出）。
# re-export 保持既有模块属性引用（含测试）不变。
from .tool_stream_progress import (
    _STREAMING_LINE_COUNT_TOOLS,
    _count_text_lines,
    _extract_streaming_progress_inputs,
    _extract_streaming_text_field,
    _format_sse,
    _format_tool_progress_event,
    next_stream_stats_event,
)

logger = get_logger(__name__)

AGENT_ID = "chat_data"

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


class AgentRunStreamFailedError(RuntimeError):
    """SSE 已发送用户可见错误，外层只需按失败状态收口 Run。"""


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
    return "智能问数执行失败，请稍后重试。"


def _file_selection_mention_suffix(file_selections: List[str]) -> str:
    """用户在输入框 @ 引用工作区文件时，给 Human 消息追加模型可见的引用短句。

    前端 @ 菜单数据源是会话工作区（uploads/outputs/workspace）文件树，chip 随
    消息传 file_selections（相对路径列表）；引用的感知完全靠这行短句，模型从
    对话即可看到本轮用户点名了哪些文件。未引用时不追加任何内容。
    """
    if not file_selections:
        return ""
    references = "、".join(f"{USER_DATA_VIRTUAL_ROOT}/{path}" for path in file_selections)
    return f"\n\n[引用文件：{references}]"


def _build_user_message(
    content: str | list[dict[str, Any]],
    file_selections: List[str] | None = None,
    *,
    message_id: str | None = None,
    **extra_kwargs: Any,
) -> HumanMessage:
    """构造带服务端时间戳和前端选择留痕的用户消息。

    content 为纯字符串（正文 + 附件占位行）；图片附件经 additional_kwargs
    的 ``vision_attachments`` 路径标记由 middleware 在请求层装载。
    用户 @ 引用工作区文件时在正文末尾追加引用短句（多选逐一列出），模型从对话即可看到本轮引用。
    """
    mention_suffix = _file_selection_mention_suffix(file_selections or [])
    if mention_suffix and isinstance(content, str):
        content = f"{content}{mention_suffix}"
    reasoning_turn_id = message_id or str(uuid4())
    additional_kwargs = {
        "timestamp": _get_timestamp(),
        "reasoning_turn_id": reasoning_turn_id,
    }
    if file_selections:
        additional_kwargs["file_selections"] = list(file_selections)
    additional_kwargs.update({key: value for key, value in extra_kwargs.items() if value})
    return HumanMessage(id=reasoning_turn_id, content=content, additional_kwargs=additional_kwargs)


def _message_end_payload(usage: Any | None = None) -> dict[str, Any]:
    """构造消息结束事件，timestamp 表示服务端确认完成的时间。"""
    payload: dict[str, Any] = {"timestamp": _get_timestamp()}
    if usage:
        payload["usage"] = sanitize_context_usage_for_response(usage)
    return payload


def _latest_assistant_timestamp(state: Any) -> str:
    """从 checkpoint state 中读取最后一条 AI 消息的持久化时间。"""
    messages = (getattr(state, "values", None) or {}).get("messages") or []
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        additional_kwargs = getattr(message, "additional_kwargs", None) or {}
        timestamp = str(additional_kwargs.get("timestamp") or "").strip()
        if timestamp:
            return timestamp
        break
    return ""


def _message_end_payload_from_state(state: Any, usage: Any | None = None) -> dict[str, Any]:
    """构造消息结束事件，优先使用已随 AIMessage 持久化的完成时间。"""
    payload = _message_end_payload(usage)
    persisted_timestamp = _latest_assistant_timestamp(state)
    if persisted_timestamp:
        payload["timestamp"] = persisted_timestamp
    return payload


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
    return _format_sse("thinking", {
        "content": reasoning_content,
        "timestamp": _get_timestamp(),
    })


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
    timestamp: str | None = None
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
    data = {
        "type": "tool",
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
    return _format_sse("tool", data)


def _format_tool_lifecycle_event(
    event_type: str,
    payload: dict[str, Any],
) -> str:
    """发送工具生命周期事件，保留旧 ``tool`` 事件供现有客户端兼容。"""
    data = dict(payload)
    data["type"] = event_type
    data.setdefault("timestamp", _get_timestamp())
    return _format_sse(event_type, data)


def _validate_workspace_file_selections(file_selections: List[str], workspace_root: str) -> List[str]:
    """校验 @ 引用文件：必须落在当前会话工作区白名单目录内且真实存在。

    白名单校验复用 WORKSPACE_PATH_POLICY（拒越界/裸别名），系统结果区
    （outputs/query_result、outputs/.tool-offload）不可引用；不存在的文件
    （@ 后被删除）丢弃。无效条目记日志后跳过，不阻断本轮。
    """
    if not file_selections:
        return []
    valid: List[str] = []
    for relative_path in file_selections:
        try:
            logical = WORKSPACE_PATH_POLICY.parse(f"{USER_DATA_VIRTUAL_ROOT}/{relative_path}")
            target = resolve_workspace_path(workspace_root, f"{USER_DATA_VIRTUAL_ROOT}/{logical.normalized}")
            if is_system_result_workspace_path(workspace_root, target):
                raise ValueError("系统结果区文件不可引用")
            if not target.is_file():
                raise FileNotFoundError(relative_path)
        except (ValueError, OSError) as err:
            logger.warning("忽略无效的引用文件 %r: %s", relative_path, err)
            continue
        if logical.normalized not in valid:
            valid.append(logical.normalized)
    return valid


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

    def __init__(self):
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
        # 本轮累计流式输出的 args 字符数（含全部工具调用）：前端实时生成速度的分子来源
        self.streamed_args_chars = 0
        # 已通过 stream_stats 事件上报的字符数（跨步长去重）
        self.reported_args_chars = 0

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
            self.streamed_args_chars += len(raw_args)
            stats_event = next_stream_stats_event(self.streamed_args_chars, self.reported_args_chars)
            if stats_event is not None:
                self.reported_args_chars, stats_sse = stats_event
                events.append(stats_sse)
        return events

    def handle_tool_calls(self, tool_calls: Any) -> list[str]:
        events: list[str] = []
        for tool_call in tool_calls or []:
            if not isinstance(tool_call, dict):
                continue
            events.extend(self._handle_single_tool_call(tool_call))
        return events

    def _register_tool_call_chunk(
        self, chunk_id: str, chunk_name: str, chunk_index: Any,
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
            args_text, _STREAMING_LINE_COUNT_TOOLS.get(tool_name),
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
        event_counts = counts if counts_changed else (None, None)
        return [_format_tool_progress_event(
            tool_call_id, tool_name, event_counts[0], event_counts[1], intent,
        )]

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
                self.streamed_args_chars += len(chunk_args)
                stats_event = next_stream_stats_event(self.streamed_args_chars, self.reported_args_chars)
                if stats_event is not None:
                    self.reported_args_chars, stats_sse = stats_event
                    events.append(stats_sse)
                # file_write / file_edit 生成 content 期间实时统计行数，前端工具卡片显示 "+N -M"
                tool_name = self.tool_names.get(current_tool_call_id, "")
                events.extend(self._streaming_progress_events(
                    current_tool_call_id, tool_name, self.args_buffer[current_tool_call_id],
                ))
        return events

    def build_tool_message_payload(self, message_chunk: Any) -> dict[str, Any]:
        tool_call_id = str(getattr(message_chunk, "tool_call_id", "") or "").strip()
        msg_data = message_chunk.model_dump()
        msg_data["type"] = "tool"
        msg_data["timestamp"] = _get_timestamp()
        msg_data["tool_name"] = self.tool_names.get(tool_call_id, msg_data.get("name") or "")
        msg_data["status"] = "error" if _is_tool_result_error(msg_data.get("content"), msg_data.get("status")) else "completed"
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
        event_type = "tool_failed" if status == "error" else "tool_completed"
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
            "status": "running",
            "args": args or {},
            # 直播侧 tool_started 占位即带开始时间（epoch 毫秒）；结果事件以 ToolTimingMiddleware 盖章值为准。
            "started_at_ms": int(time.time() * 1000),
        }
        return [
            _format_tool_lifecycle_event("tool_started", payload),
            _format_tool_event(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                status="running",
                args=args or {},
            ),
        ]


_SUBAGENT_TOOL_EVENT_TYPES = {
    "tool_started",
    "tool_completed",
    "tool_failed",
    "subagent_message",
    "subagent_message_delta",
    "subagent_thinking_delta",
    "subagent_context_usage",
}


@dataclass
class _MessageChunkFlags:
    """message_chunk 的载荷形态标记，供 _handle_message_chunk 分发。"""

    has_content: bool
    has_tool_calls: bool
    has_tool_call_chunks: bool
    is_ai_message: bool
    is_tool_message: bool
    thinking_event: str | None

    @classmethod
    def of(cls, message_chunk: Any) -> "_MessageChunkFlags":
        return cls(
            has_content=bool(getattr(message_chunk, "content", None)),
            has_tool_calls=bool(getattr(message_chunk, "tool_calls", None)),
            has_tool_call_chunks=bool(getattr(message_chunk, "tool_call_chunks", None)),
            is_ai_message=isinstance(message_chunk, (AIMessage, AIMessageChunk)),
            is_tool_message=isinstance(message_chunk, (ToolMessage, ToolMessageChunk)),
            thinking_event=_format_thinking_event(message_chunk),
        )

    @property
    def has_visible_payload(self) -> bool:
        return (
            self.has_content
            or self.has_tool_calls
            or self.has_tool_call_chunks
            or bool(self.thinking_event)
        )

    @property
    def thinking_only(self) -> bool:
        return (
            not self.has_content
            and not self.has_tool_calls
            and not self.has_tool_call_chunks
            and not self.is_tool_message
        )


class _AgentSseStreamAdapter:
    """统一 create_agent 的 SSE 流处理，保留各 Agent 的可见消息过滤钩子。"""

    def __init__(
        self,
        *,
        agent: Any,
        initial_state: dict[str, Any],
        config: dict[str, Any],
        request: Request | None = None,
        visible_message_filter=None,
        disconnect_log_message: str = "客户端已断开，停止流式输出: %s",
        disconnect_log_id: str = "",
        include_empty_tool_messages: bool = False,
        on_first_message=None,
        on_stream_started=None,
        debug_label: str | None = None,
        user_id: str | None = None,
        reasoning_turn_id: str | None = None,
    ):
        self.agent = agent
        self.initial_state = initial_state
        self.config = config
        self.request = request
        self.visible_message_filter = visible_message_filter
        self.disconnect_log_message = disconnect_log_message
        self.disconnect_log_id = disconnect_log_id
        self.include_empty_tool_messages = include_empty_tool_messages
        self.on_first_message = on_first_message
        self.on_stream_started = on_stream_started
        self.debug_label = debug_label
        self.user_id = user_id
        self.reasoning_turn_id = reasoning_turn_id
        self.tool_stream_state = _ToolStreamState()
        self.reasoning_segments: list[dict[str, Any]] = []
        self.archived_turn_messages: list[Any] = []
        self.latest_token_usage: dict[str, Any] | None = None
        self.state: Any | None = None
        self.disconnected = False
        self._first_visible_payload_seen = False
        self._first_token_timeout_alarm_emitted = False
        self._queued_events: list[str] = []
        self.graph_started = False

    def set_input_state(self, input_state: dict[str, Any]) -> None:
        """设置下一次 graph 调用的增量输入。"""
        self.initial_state = input_state

    def queue_event(self, event: str) -> None:
        """登记下一次 graph 调用前必须先发送的 SSE 事件。"""
        self._queued_events.append(event)

    async def stream_events(self):
        """输出 token_usage、thinking、tool、message 事件。"""
        while self._queued_events:
            yield self._queued_events.pop(0)
        started_at = time.perf_counter()
        timeout_task = asyncio.create_task(self._first_token_timeout_watchdog(started_at))
        try:
            async for sse_event in self._iter_agent_stream_events(started_at):
                yield sse_event
        except Exception as exc:
            if not self._first_visible_payload_seen:
                self._emit_first_token_timeout_if_needed(started_at, ext2={"error_type": exc.__class__.__name__, "error": str(exc)})
            emit_llm_connection_alarm_if_needed(
                exc,
                ext1=self._alarm_context(),
                ext2={"error_type": exc.__class__.__name__, "error": str(exc)},
            )
            raise
        finally:
            timeout_task.cancel()
            with suppress(asyncio.CancelledError):
                await timeout_task

    async def _iter_agent_stream_events(self, started_at: float):
        """消费 agent.astream 原始事件流并转成 SSE 事件；客户端断开时提前结束。"""
        stream_started = False
        async for event in self.agent.astream(
            self.initial_state,
            config=self.config,
            stream_mode=["messages", "custom"],
        ):
            if not stream_started:
                stream_started = True
                await self._mark_stream_started()
            if await self._handle_disconnect():
                return

            event_kind, payload = self._parse_stream_event(event)
            if event_kind == "custom":
                for sse_event in self._handle_custom_payload(payload):
                    yield sse_event
                continue
            if event_kind != "messages":
                continue

            message_chunk, metadata = payload
            async for sse_event in self._iter_message_chunk_events(message_chunk, metadata, started_at):
                yield sse_event

    async def _mark_stream_started(self) -> None:
        """流首个事件到达：置位标记并触发外部回调（仅一次）。"""
        self.graph_started = True
        if self.on_stream_started:
            await self.on_stream_started()

    async def _iter_message_chunk_events(self, message_chunk: Any, metadata: Any, started_at: float):
        """按可见性过滤并处理单个消息 chunk，产出 SSE 事件。"""
        if self.visible_message_filter and not self.visible_message_filter(message_chunk, metadata):
            return
        if self._has_visible_payload(message_chunk):
            self._record_first_visible_payload(started_at)
        async for sse_event in self._handle_message_chunk(message_chunk, metadata):
            yield sse_event

    async def finish(self, *, persist_latest_ai_timestamp: bool = False, prefer_latest_usage: bool = False) -> dict[str, Any]:
        """读取最终状态并构造 message_end payload。"""
        self.state = await self.agent.aget_state(self.config) if hasattr(self.agent, "aget_state") else None
        if persist_latest_ai_timestamp:
            self.state = await _persist_latest_ai_message_timestamp(self.agent, self.config, self.state)
        usage = self._resolve_end_usage(prefer_latest_usage)
        return _message_end_payload_from_state(self.state, usage)

    def pending_interrupt(self) -> dict[str, Any] | None:
        """Return the single durable LangGraph interrupt payload, if present."""
        interrupts = getattr(self.state, "interrupts", None) or ()
        if not interrupts:
            return None
        value = getattr(interrupts[0], "value", None)
        if not isinstance(value, dict):
            logger.warning("忽略非结构化 Agent interrupt: %r", value)
            return None
        return dict(value)

    async def _handle_disconnect(self) -> bool:
        if not self.request or not await self.request.is_disconnected():
            return False
        logger.info(self.disconnect_log_message, self.disconnect_log_id)
        self.disconnected = True
        return True

    def _handle_custom_payload(self, payload: Any) -> list[str]:
        if not isinstance(payload, dict):
            return []
        handlers = {
            "token_usage": self._handle_token_usage_payload,
            "context_compression": self._handle_context_compression_payload,
            "inbox_admitted": self._handle_inbox_admitted_payload,
            "tool_progress": self._handle_tool_progress_payload,
            "subagent_tool": self._handle_subagent_tool_payload,
        }
        handler = handlers.get(str(payload.get("type") or ""))
        if handler is None:
            return []
        return handler(payload)

    def _handle_token_usage_payload(self, payload: dict[str, Any]) -> list[str]:
        if payload.get("usage"):
            self.latest_token_usage = sanitize_context_usage_for_response(payload.get("usage"))
            return [_format_sse("token_usage", self.latest_token_usage)]
        return []

    def _handle_context_compression_payload(self, payload: dict[str, Any]) -> list[str]:
        if not isinstance(payload.get("event"), dict):
            return []
        archived_messages = payload.get("archived_turn_messages")
        if isinstance(archived_messages, list):
            self.archived_turn_messages = _merge_message_snapshots(
                self.archived_turn_messages,
                archived_messages,
            )
        return [_format_sse("context_compression", payload["event"])]

    def _handle_inbox_admitted_payload(self, payload: dict[str, Any]) -> list[str]:
        if not isinstance(payload.get("message_ids"), list):
            return []
        message_ids = [str(message_id) for message_id in payload["message_ids"] if str(message_id)]
        return [_format_sse("inbox_admitted", {"message_ids": message_ids})] if message_ids else []

    def _handle_tool_progress_payload(self, payload: dict[str, Any]) -> list[str]:
        tool_call_id = str(payload.get("tool_call_id") or "").strip()
        tool_name = str(payload.get("tool_name") or "").strip()
        message = str(payload.get("message") or "").strip()
        if not tool_call_id or not tool_name or not message:
            logger.warning("忽略无效工具进度事件: %s", payload)
            return []
        return [_format_tool_lifecycle_event("tool_progress", {
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "status": "running",
            "message": message,
            "progress": payload.get("progress"),
        })]

    def _handle_subagent_tool_payload(self, payload: dict[str, Any]) -> list[str]:
        if not isinstance(payload.get("event"), dict):
            return []
        event = payload["event"]
        event_type = str(event.get("event_type") or "").strip()
        if event_type not in _SUBAGENT_TOOL_EVENT_TYPES:
            logger.warning("忽略未知的子智能体工具事件类型: %s", event_type)
            return []
        if event_type == "subagent_message":
            # streamed 完整事件不带 content（前端已拼出全文），只补截断标记
            if not event.get("streamed") and not str(event.get("content") or "").strip():
                return []
        if event_type in {"subagent_message_delta", "subagent_thinking_delta"} and not str(event.get("delta") or ""):
            return []
        data = {key: value for key, value in event.items() if key != "event_type"}
        return [_format_tool_lifecycle_event(event_type, data)]

    def _alarm_context(self) -> dict[str, Any]:
        configurable = self.config.get("configurable") if isinstance(self.config, dict) else {}
        return {
            "thread_id": (configurable or {}).get("thread_id") or "",
            "checkpoint_ns": (configurable or {}).get("checkpoint_ns") or "",
            "debug_label": self.debug_label or "",
            "user_id": self.user_id or "",
        }

    def _has_visible_payload(self, message_chunk: Any) -> bool:
        has_content = bool(getattr(message_chunk, "content", None))
        has_tool_calls = bool(getattr(message_chunk, "tool_calls", None))
        has_tool_call_chunks = bool(getattr(message_chunk, "tool_call_chunks", None))
        is_tool_message = isinstance(message_chunk, (ToolMessage, ToolMessageChunk))
        return bool(
            has_content
            or has_tool_calls
            or has_tool_call_chunks
            or _format_thinking_event(message_chunk)
            or (self.include_empty_tool_messages and is_tool_message)
        )

    def _record_first_visible_payload(self, started_at: float) -> None:
        if self._first_visible_payload_seen:
            return
        self._first_visible_payload_seen = True
        self._emit_first_token_timeout_if_needed(started_at)
        if self.on_first_message:
            self.on_first_message()

    async def _first_token_timeout_watchdog(self, started_at: float) -> None:
        threshold_seconds = int(LLM_FIRST_TOKEN_TIMEOUT_SECONDS or 0)
        if threshold_seconds <= 0:
            return
        await asyncio.sleep(threshold_seconds)
        if not self._first_visible_payload_seen and not self.disconnected:
            self._emit_first_token_timeout_if_needed(started_at)

    def _emit_first_token_timeout_if_needed(self, started_at: float, *, ext2: Any = None) -> None:
        threshold_seconds = int(LLM_FIRST_TOKEN_TIMEOUT_SECONDS or 0)
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        if threshold_seconds <= 0 or elapsed_ms < threshold_seconds * 1000 or self._first_token_timeout_alarm_emitted:
            return
        self._first_token_timeout_alarm_emitted = True
        emit_llm_first_token_timeout_alarm(
            elapsed_ms=elapsed_ms,
            threshold_seconds=threshold_seconds,
            ext1=self._alarm_context(),
            ext2=ext2 or {"elapsed_ms": elapsed_ms, "threshold_seconds": threshold_seconds},
        )

    async def _handle_message_chunk(self, message_chunk: Any, metadata: dict[str, Any]):
        flags = _MessageChunkFlags.of(message_chunk)
        if not flags.has_visible_payload and not (self.include_empty_tool_messages and flags.is_tool_message):
            return
        if flags.thinking_event:
            _append_reasoning_segment(
                self.reasoning_segments,
                _extract_reasoning_content(message_chunk),
                self.tool_stream_state.visible_tool_count(),
                self.reasoning_turn_id,
            )
            yield flags.thinking_event
            if flags.thinking_only:
                return
        if flags.has_tool_calls:
            for tool_event in self.tool_stream_state.handle_tool_calls(getattr(message_chunk, "tool_calls", []) or []):
                yield tool_event
        if flags.has_tool_call_chunks:
            for tool_event in self.tool_stream_state.handle_tool_call_chunks(getattr(message_chunk, "tool_call_chunks", []) or []):
                yield tool_event
        if flags.is_tool_message:
            tool_payload = self.tool_stream_state.build_tool_message_payload(message_chunk)
            yield self.tool_stream_state.build_completion_event(tool_payload)
            yield _format_sse("tool", tool_payload)
            return
        msg_data = message_chunk.model_dump()
        msg_data["timestamp"] = _get_timestamp()
        yield _format_sse("message", msg_data)

    def _resolve_end_usage(self, prefer_latest_usage: bool) -> Any | None:
        state_usage = None
        if self.state and getattr(self.state, "values", None):
            state_usage = self.state.values.get("token_usage")
        if prefer_latest_usage:
            return self.latest_token_usage or state_usage
        # Keep legacy full checkpoints authoritative. New checkpoints contain
        # only compact counters, so use the in-memory full payload when present.
        if isinstance(state_usage, dict) and {"llm_calls", "breakdown"} & state_usage.keys():
            return state_usage
        return self.latest_token_usage or state_usage

    @staticmethod
    def _parse_stream_event(event: Any) -> tuple[str, Any]:
        if isinstance(event, tuple) and len(event) == 2:
            stream_mode, payload = event
            if stream_mode == "custom":
                return "custom", payload
            if stream_mode == "messages":
                return "messages", payload
            message_chunk, metadata = event
            return "messages", (message_chunk, metadata)
        return "unknown", event


def _is_chat_data_visible_stream_message(message_chunk: Any, metadata: dict[str, Any]) -> bool:
    """过滤智能问数 create_agent 流，避免工具内部模型调用进入主对话。"""
    node_name = str(metadata.get("langgraph_node") or "")
    if isinstance(message_chunk, (AIMessage, AIMessageChunk)):
        if node_name != "model":
            logger.debug("[过滤] 跳过来自 %s 节点的 AI 消息", node_name)
            return False
        return True
    if isinstance(message_chunk, (ToolMessage, ToolMessageChunk)):
        return node_name == "tools"
    # 增量输入的 HumanMessage 由前端用户气泡展示，不能再次作为助手正文输出。
    return False


async def _generate_chat_data_stream(
    req: ChatDataStreamRequest,
):
    """流式执行单一 ChatData Agent。"""
    # 入参对象在此拆成局部变量：函数体与拆分前保持一致，避免大范围表达式改写造成的
    # 语义漂移；签名的收窄已经让「一次执行的完整入参」成为可复用的一等公民。
    thread_id = req.thread_id
    user_query = req.user_query
    datasource_id = req.datasource_id
    user_id = req.user_id
    username = req.username
    member_datasource_ids = req.member_datasource_ids
    semantic_config_id = req.semantic_config_id
    request = req.request
    model_id = req.model_id
    file_selections = req.file_selections
    enable_thinking = req.enable_thinking
    thinking_effort = req.thinking_effort
    skill_names = req.skill_names
    options = req.options
    file_ids = req.file_ids
    run_id = req.run_id
    adopted_messages = req.adopted_messages
    resume_answer = req.resume_answer
    resume_started = req.resume_started
    resume_turn_id = req.resume_turn_id
    turn_id = req.turn_id
    recovering = req.recovering
    owner_token = req.owner_token
    stream_event_id_provider = req.stream_event_id_provider
    started_at = time.perf_counter()
    last_phase_at = started_at

    def log_phase(phase: str) -> None:
        nonlocal last_phase_at
        now = time.perf_counter()
        logger.info(
            "ChatData 阶段耗时: conversation_id=%s phase=%s elapsed_ms=%d total_ms=%d",
            thread_id,
            phase,
            int((now - last_phase_at) * 1000),
            int((now - started_at) * 1000),
        )
        last_phase_at = now

    datasource_id = str(datasource_id or "").strip() or None
    semantic_config_id = str(semantic_config_id or "").strip() or None
    normalized_skill_names = []
    for item in skill_names or []:
        skill_name = str(item or "").strip()
        if skill_name and skill_name not in normalized_skill_names:
            normalized_skill_names.append(skill_name)
    logger.info(f"开始执行智能问数，thread_id: {thread_id}, semantic_config_id: {semantic_config_id}")

    owner_task = asyncio.current_task()
    heartbeat_task = (
        asyncio.create_task(_heartbeat_agent_run(run_id, owner_task, owner_token))
        if run_id else None
    )
    # 生命周期契约：本生成器只产出执行事实（业务事件 / error / question_requested），
    # 不发 done、不做收口。Run 的终态判定、complete_run/finish_run 调用与结构化
    # done 发布统一由 AgentRunRuntime（唯一生命周期 owner）完成。
    try:
        async for event in _run_chat_data_agent_stream(
            req=ChatDataStreamRequest(
                thread_id=thread_id,
                user_query=user_query,
                datasource_id=datasource_id,
                user_id=user_id,
                username=username,
                member_datasource_ids=member_datasource_ids,
                semantic_config_id=semantic_config_id,
                request=request,
                model_id=model_id,
                file_selections=file_selections,
                enable_thinking=enable_thinking,
                thinking_effort=thinking_effort,
                skill_names=normalized_skill_names,
                options=options,
                file_ids=file_ids,
                run_id=run_id,
                adopted_messages=adopted_messages,
                resume_answer=resume_answer,
                resume_started=resume_started,
                resume_turn_id=resume_turn_id,
                turn_id=turn_id,
                recovering=recovering,
                owner_token=owner_token,
                stream_event_id_provider=stream_event_id_provider,
            ),
            log_phase=log_phase,
        ):
            yield event
    except AgentRunStreamFailedError:
        # 失败事实（error 事件）已由内层产出；异常必须继续抛给 Runtime，
        # 由唯一生命周期 owner 执行 finish_run("failed") 并发布 done {"status":"failed"}。
        # 若在此吞掉，Runtime 会把失败流当作正常结束走 complete_run，误标为 finished。
        logger.info("ChatData Agent 流式执行失败结束: conversation_id=%s", thread_id)
        raise
    except Exception as exc:
        # 进入内层守卫段之前（准备阶段）的失败：内层来不及产出 error 事实，
        # 这里补发一条；终态收口与 done 发布同样交给 Runtime。
        logger.error("智能问数执行准备失败: conversation_id=%s", thread_id, exc_info=True)
        yield _format_sse("error", {"error": _model_failure_user_message(exc)})
        raise
    finally:
        if heartbeat_task:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task


def _cancel_agent_owner_task(owner_task: asyncio.Task | None, reason: str) -> None:
    """owner 任务尚未结束时协作取消本地执行。"""
    if owner_task and not owner_task.done():
        owner_task.cancel(reason)


def _should_renew_heartbeat(heartbeat_status: str, owner_token: str | None) -> bool:
    """心跳为 active 且持有 owner token 时才续约。"""
    return heartbeat_status == "active" and owner_token


async def _heartbeat_agent_run(
    run_id: str,
    owner_task: asyncio.Task | None,
    owner_token: str | None = None,
) -> None:
    """保持运行租约；停止请求落到其他 worker 时协作取消本地执行。"""
    while True:
        # 租约 120s，5s 心跳已足够及时感知跨 worker 的取消/接管，
        # 同时把满载时的 inbox UPDATE 频率控制在个位数 QPS。
        await asyncio.sleep(5)
        try:
            heartbeat_status = await agent_inbox_service.heartbeat_run(run_id, owner_token)
            if _should_renew_heartbeat(heartbeat_status, owner_token):
                await agent_run_event_service.renew_owner(run_id, owner_token)
        except AgentRunLeaseLostError:
            _cancel_agent_owner_task(owner_task, "agent_run_lease_lost")
            return
        except Exception:
            logger.warning("ChatData Agent Run 心跳续约失败，下一周期重试: run_id=%s", run_id, exc_info=True)
            continue
        if heartbeat_status == "cancelling":
            _cancel_agent_owner_task(owner_task, "agent_run_cancelled")
            return
        if heartbeat_status != "active":
            # waiting_user/finished are normal transitions performed by this
            # execution. Stop renewing without cancelling the stream, which
            # still needs to publish question_requested/done. A true takeover
            # keeps status active and fails the owner-token assertion above.
            return


def _create_agent_stream_adapter(
    callbacks: _AgentRunStreamCallbacks,
    *,
    agent: Any,
    initial_state: Any,
    config: dict[str, Any],
    request: Request | None,
) -> "_AgentSseStreamAdapter":
    """装配本轮 Agent SSE 流适配器，挂到回调上下文并补发恢复重启事件。"""
    on_stream_started = (
        partial(agent_inbox_service.mark_question_resume_started, callbacks.run_id, callbacks.owner_token)
        if callbacks.resume_answer is not None and callbacks.run_id
        else None
    )
    adapter = _AgentSseStreamAdapter(
        agent=agent,
        initial_state=initial_state,
        config=config,
        request=request,
        visible_message_filter=_is_chat_data_visible_stream_message,
        disconnect_log_id=callbacks.thread_id,
        debug_label="AI Content from model",
        user_id=callbacks.user_id,
        include_empty_tool_messages=True,
        on_first_message=partial(callbacks.log_phase, "first_model_chunk"),
        on_stream_started=on_stream_started,
        reasoning_turn_id=callbacks.resume_turn_id or callbacks.turn_id or (
            str(callbacks.user_message.additional_kwargs.get("reasoning_turn_id") or "")
            if callbacks.user_message else None
        ),
    )
    callbacks.stream_adapter = adapter
    if callbacks.recovering:
        adapter.queue_event(_format_sse("run_restarted", {
            "run_id": callbacks.run_id,
            "turn_id": callbacks.turn_id,
        }))
    return adapter


async def _resolve_inbox_message_references(
    message: HumanMessage,
    *,
    workspace_root: str,
    user_id: str | None,
    thread_id: str,
) -> HumanMessage:
    """把排队消息 additional_kwargs 里的引用/附件解析为与普通发送同构的形态。

    「存引用、晚解析」的消费端：@ 引用在校验工作区白名单后追加引用短句，
    附件 file_id 解析为附件占位行与 vision 标记——与首条用户消息
    （_build_user_message + 附件文本）同一条代码语义。必须在 workspace
    就绪之后、进入 initial_state 之前调用（图输入与持久化共享解析结果）；
    失效条目记日志跳过，不阻断消息。
    """
    kwargs = getattr(message, "additional_kwargs", None) or {}
    selections = kwargs.get("file_selections") or []
    file_ids = kwargs.get("file_ids") or []
    if not selections and not file_ids:
        return message
    valid_selections = (
        _validate_workspace_file_selections(selections, workspace_root)
        if selections else []
    )
    file_attachments = (
        await _resolve_file_attachments(
            file_ids, user_id=user_id, thread_id=thread_id,
        )
        if file_ids else []
    )
    new_kwargs = dict(kwargs)
    if selections:
        new_kwargs["file_selections"] = valid_selections
    _, max_image_size = await get_image_size_limit()
    vision_attachments = select_vision_attachments(file_attachments, max_image_size=max_image_size)
    if vision_attachments:
        new_kwargs["vision_attachments"] = vision_attachments
    new_content = (
        f"{message.content}"
        f"{_file_selection_mention_suffix(valid_selections)}"
        f"{build_file_attachment_text(file_attachments)}"
    )
    return message.model_copy(update={"content": new_content, "additional_kwargs": new_kwargs})


async def _persist_admitted_inbox_messages(
    callbacks: _AgentRunStreamCallbacks,
    messages: list[HumanMessage],
) -> None:
    """把已解析的排队消息持久化为 admitted 用户历史（上屏与回放的依据）。"""
    for message in messages:
        await conversation_event_service.persist_user_message(
            conversation_id=callbacks.thread_id,
            user_id=callbacks.user_id or "system",
            operator_name=callbacks.username or callbacks.user_id or "system",
            message=message,
        )


async def _admit_agent_inbox_messages(
    callbacks: _AgentRunStreamCallbacks,
    messages: list[HumanMessage],
) -> None:
    """steering 中间件认领路径：解析随行引用/附件后持久化。

    就地替换列表元素：中间件随后把同一列表注入图 state，替换后图输入
    拿到的即是解析后的消息（与持久化内容一致）。
    """
    for index, message in enumerate(messages):
        messages[index] = await _resolve_inbox_message_references(
            message,
            workspace_root=callbacks.workspace_root,
            user_id=callbacks.user_id,
            thread_id=callbacks.thread_id,
        )
    await _persist_admitted_inbox_messages(callbacks, messages)


async def _log_agent_stream_done(callbacks: _AgentRunStreamCallbacks) -> None:
    callbacks.log_phase("agent_stream_done")


async def _mark_agent_waiting_for_user(
    callbacks: _AgentRunStreamCallbacks,
    question: dict[str, Any],
) -> None:
    if not callbacks.run_id:
        return
    question["turn_id"] = callbacks.effective_turn_id
    await agent_inbox_service.mark_waiting_for_user(
        callbacks.run_id,
        question,
        callbacks.user_id or "system",
        callbacks.username or callbacks.user_id or "system",
        callbacks.owner_token,
    )


async def _confirm_agent_consumed_messages(callbacks: _AgentRunStreamCallbacks) -> None:
    if callbacks.run_id:
        await agent_inbox_service.mark_consumed(
            callbacks.run_id, callbacks.steering_tracker.drain(), callbacks.owner_token,
        )


async def _before_agent_stream(callbacks: _AgentRunStreamCallbacks) -> bool:
    if callbacks.request and await callbacks.request.is_disconnected():
        logger.info("客户端已断开，停止流式输出: %s", callbacks.thread_id)
        if callbacks.resume_answer is not None and callbacks.run_id:
            await agent_inbox_service.restore_question_answer(
                callbacks.run_id,
                callbacks.user_id or "system",
                callbacks.username or callbacks.user_id or "system",
                callbacks.owner_token,
            )
        return False
    return True


async def _prepare_agent_prompt_bundle(
    semantic_materialize_note: str,
    user_id: str | None,
    thread_id: str,
    workspace_runtime: ConversationWorkspaceRuntime,
) -> tuple[Any, str]:
    """构建数据上下文与会话技能提示词清单。

    静态注入下线后，数据上下文区唯一有效字段是物化说明；恒传 dict 以兼容
    build_unified_system_prompt 的既有口径（无说明时为空 dict → 空文本）。
    """
    data_context: Any = {"semantic_materialize_note": semantic_materialize_note} if semantic_materialize_note else {}
    available_skills_xml = await _load_runtime_skills_xml(
        user_id or "system", thread_id, workspace_runtime,
    )
    return data_context, available_skills_xml


@dataclass(frozen=True)
class TurnUserInput:
    """本轮用户侧输入：消息素材与上一轮遗留的排队消息。"""

    user_query: str
    message_content: str
    file_selections: List[str]
    vision_attachments: list[dict[str, Any]]
    skill_names: list[str]
    adopted_messages: list[HumanMessage] | None = None


@dataclass(frozen=True)
class TurnResumeContext:
    """checkpoint 恢复相关的上下文（是否恢复、恢复答案、所属 turn/run 与租约 token）。"""

    recovering: bool
    resume_answer: dict[str, Any] | None
    resume_started: bool
    turn_id: str | None
    run_id: str | None
    owner_token: str | None


@dataclass(frozen=True)
class TurnRuntimeContext:
    """本轮执行所需的不变上下文：操作人、会话、数据源、语义、运行时与提示素材。"""

    user_id: str | None
    username: str | None
    thread_id: str
    datasource_id: str | None
    semantic_context: Any
    runtime_options: Any
    workspace_root: str
    data_context: Any
    available_skills_xml: str
    memory_block: str


async def _resolve_agent_turn_inputs(
    checkpointer: Any,
    config: dict[str, Any],
    *,
    user_input: TurnUserInput,
    resume: TurnResumeContext,
    runtime: TurnRuntimeContext,
) -> tuple[HumanMessage | None, Any, str, list[HumanMessage] | None]:
    """解析 checkpoint 恢复状态，构建用户消息、initial_state 与系统提示词。

    `adopted_messages` 可能在恢复对齐时被重置为空列表，须回传给主流程继续使用。
    """
    # 三个入参对象在此还原为局部变量：函数体与拆分前保持一致，避免表达式级改写。
    user_query = user_input.user_query
    message_content = user_input.message_content
    file_selections = user_input.file_selections
    vision_attachments = user_input.vision_attachments
    skill_names = user_input.skill_names
    adopted_messages = user_input.adopted_messages
    recovering = resume.recovering
    resume_answer = resume.resume_answer
    resume_started = resume.resume_started
    turn_id = resume.turn_id
    run_id = resume.run_id
    owner_token = resume.owner_token
    user_id = runtime.user_id
    username = runtime.username
    thread_id = runtime.thread_id
    datasource_id = runtime.datasource_id
    semantic_context = runtime.semantic_context
    runtime_options = runtime.runtime_options
    workspace_root = runtime.workspace_root
    data_context = runtime.data_context
    available_skills_xml = runtime.available_skills_xml
    memory_block = runtime.memory_block
    message_kwargs: dict[str, Any] = {}
    if skill_names:
        message_kwargs["skill_names"] = skill_names
    current_turn_checkpointed, resume_checkpointed, adopted_messages = (
        await _resolve_checkpointed_turn_state(
            checkpointer,
            config,
            recovering=recovering,
            resume_answer=resume_answer,
            resume_started=resume_started,
            turn_id=turn_id,
            run_id=run_id,
            owner_token=owner_token,
            adopted_messages=adopted_messages,
        )
    )
    user_message = None
    if resume_answer is None and not current_turn_checkpointed and (user_query.strip() or not adopted_messages):
        user_message = _build_user_message(
            message_content,
            file_selections,
            message_id=turn_id,
            vision_attachments=vision_attachments,
            **message_kwargs,
        )
    initial_state = _resolve_agent_initial_state(
        resume_checkpointed=resume_checkpointed,
        resume_answer=resume_answer,
        current_turn_checkpointed=current_turn_checkpointed,
        user_id=user_id,
        operator_name=username,
        conversation_id=thread_id,
        adopted_messages=adopted_messages,
        user_message=user_message,
        datasource_id=datasource_id,
        semantic_context=semantic_context,
        runtime_options=runtime_options,
        workspace_root=workspace_root,
    )
    system_prompt = build_unified_system_prompt(
        runtime_options,
        data_context=data_context,
        workspace_root=workspace_root,
        available_skills_xml=available_skills_xml,
        memory_block=memory_block,
    )
    return user_message, initial_state, system_prompt, adopted_messages


async def _admit_adopted_messages(
    callbacks: "_AgentRunStreamCallbacks",
    stream_adapter: "_AgentSseStreamAdapter",
    steering_tracker: SteeringMessageTracker,
    adopted_messages: list[HumanMessage] | None,
) -> None:
    """上一轮异常遗留的排队消息由本轮接管，作为新一轮的首批输入：
    先持久化并通知前端上屏，再随 initial_state 进入本轮对话。
    消息的随行引用/附件已在 workspace 就绪后统一解析（见主流程），此处只持久化。"""
    await _persist_admitted_inbox_messages(callbacks, adopted_messages or [])
    if adopted_messages:
        adopted_ids = [str(message.id or "") for message in adopted_messages]
        steering_tracker.record(adopted_ids)
        stream_adapter.queue_event(_format_sse("inbox_admitted", {
            "message_ids": adopted_ids,
        }))


async def _run_chat_data_agent_stream(
    *,
    req: ChatDataStreamRequest,
    log_phase,
):
    """执行已注册 active run 的 Agent 流，生命周期由外层统一收口。"""
    # 与 _generate_chat_data_stream 同一口径：入参对象在此还原为局部变量，函数体保持不变。
    thread_id = req.thread_id
    user_query = req.user_query
    datasource_id = req.datasource_id
    user_id = req.user_id
    username = req.username
    member_datasource_ids = req.member_datasource_ids
    semantic_config_id = req.semantic_config_id
    request = req.request
    model_id = req.model_id
    file_selections = req.file_selections
    enable_thinking = req.enable_thinking
    thinking_effort = req.thinking_effort
    skill_names = req.skill_names
    options = req.options
    file_ids = req.file_ids
    run_id = req.run_id
    adopted_messages = req.adopted_messages
    resume_answer = req.resume_answer
    resume_started = req.resume_started
    resume_turn_id = req.resume_turn_id
    turn_id = req.turn_id
    recovering = req.recovering
    owner_token = req.owner_token
    stream_event_id_provider = req.stream_event_id_provider
    selected_files = parse_file_selections(file_selections)
    try:
        semantic_context = await _prepare_agent_semantic_context(
            thread_id=thread_id,
            user_query=user_query,
            datasource_id=datasource_id,
            user_id=user_id,
            username=username,
            member_datasource_ids=member_datasource_ids,
            semantic_config_id=semantic_config_id,
        )
        datasource_id = semantic_context.datasource_id
        runtime_options = resolve_agent_runtime_options(
            user_id=user_id,
            override=(options or {}).get("runtime_options"),
            **await get_subagent_hot_defaults(),
        )
        workspace_paths, workspace_runtime, semantic_materialize_note = (
            await _prepare_agent_workspace_runtime(
                thread_id=thread_id,
                datasource_id=datasource_id,
                user_id=user_id,
                semantic_context=semantic_context,
                runtime_options=runtime_options,
            )
        )
    except _AgentRunPreparationError as exc:
        yield _format_sse("error", {"error": exc.message})
        raise AgentRunStreamFailedError from None
    log_phase("prepare_semantic_context")
    # 引用文件校验必须在 workspace 就绪之后：@ 菜单可引用的文件以工作区实际内容为准。
    valid_file_selections = _validate_workspace_file_selections(selected_files, workspace_paths["root"])

    # 排队消息（续跑接管）随行引用/附件的「晚解析」：workspace 就绪后、进入
    # initial_state 前完成，图输入与后续持久化共享同一份解析结果。
    if adopted_messages:
        for adopted_index, adopted_message in enumerate(adopted_messages):
            adopted_messages[adopted_index] = await _resolve_inbox_message_references(
                adopted_message,
                workspace_root=workspace_paths["root"],
                user_id=user_id,
                thread_id=thread_id,
            )

    data_context, available_skills_xml = await _prepare_agent_prompt_bundle(
        semantic_materialize_note, user_id, thread_id, workspace_runtime,
    )
    # 会话记忆注入段（每轮构建时读取）：由系统自动维护，主图 system prompt
    # 与子图只读段共用同一份渲染产物；空串时各消费方自动省略整段。
    memory_block = render_session_memory_block(
        workspace_paths["root"],
        conversation_id=thread_id,
    )

    config = {
        # 子智能体（subagents.py::build_subagent_invocation_config）读同一配置，
        # 主/子图口径同源；运行期经后台配置变量表读取（新会话装配时生效），
        # 不设置时 langgraph 默认仅 25 个 super-step。
        "recursion_limit": await get_runtime_setting("CHAT_DATA_AGENT_RECURSION_LIMIT"),
        "configurable": {
            "thread_id": thread_id,
            "checkpoint_ns": ""
        }
    }

    steering_tracker = SteeringMessageTracker()

    # 解析 file_ids 对应的文件信息，用于追加到用户消息。
    # 上传与解析已解耦：只要求文件存在且归属当前会话，不再要求预解析完成。
    file_attachments = await _resolve_file_attachments(
        file_ids, user_id=user_id, thread_id=thread_id,
    )

    # 用户输入超长时自动转为文件写入 workspace outputs
    user_message_content = user_query
    if len(user_query) > USER_INPUT_OFFLOAD_THRESHOLD:
        user_message_content, offload_attachment = await _offload_oversized_user_input(
            user_query, workspace_runtime, thread_id,
        )
        file_attachments.append(offload_attachment)

    try:
        async with get_checkpointer() as checkpointer:
            log_phase("open_checkpointer")
            # 用户消息保持纯文本（正文 + 附件占位行）；合规图片附件只打
            # vision_attachments 路径标记，由 ImageInjectionMiddleware 在模型
            # 请求层按当轮 vlm 能力位装载——base64 不进入 state/checkpoint/事件表。
            vision_attachments = select_vision_attachments(
                file_attachments, max_image_size=(await get_image_size_limit())[1]
            )
            attachment_text = build_file_attachment_text(file_attachments)
            message_content = f"{user_message_content}{attachment_text}"

            user_message, initial_state, system_prompt, adopted_messages = (
                await _resolve_agent_turn_inputs(
                    checkpointer,
                    config,
                    user_input=TurnUserInput(
                        user_query=user_query,
                        message_content=message_content,
                        file_selections=valid_file_selections,
                        vision_attachments=vision_attachments,
                        skill_names=skill_names,
                        adopted_messages=adopted_messages,
                    ),
                    resume=TurnResumeContext(
                        recovering=recovering,
                        resume_answer=resume_answer,
                        resume_started=resume_started,
                        turn_id=turn_id,
                        run_id=run_id,
                        owner_token=owner_token,
                    ),
                    runtime=TurnRuntimeContext(
                        user_id=user_id,
                        username=username,
                        thread_id=thread_id,
                        datasource_id=datasource_id,
                        semantic_context=semantic_context,
                        runtime_options=runtime_options,
                        workspace_root=workspace_paths["root"],
                        data_context=data_context,
                        available_skills_xml=available_skills_xml,
                        memory_block=memory_block,
                    ),
                )
            )
            log_phase("build_system_prompt")

            callbacks = _AgentRunStreamCallbacks(
                thread_id=thread_id,
                user_id=user_id,
                username=username,
                run_id=run_id,
                owner_token=owner_token,
                request=request,
                resume_answer=resume_answer,
                resume_turn_id=resume_turn_id,
                turn_id=turn_id,
                stream_event_id_provider=stream_event_id_provider,
                log_phase=log_phase,
                steering_tracker=steering_tracker,
                workspace_root=workspace_paths["root"],
                adopted_messages=adopted_messages,
                user_message=user_message,
                recovering=recovering,
            )
            model_snapshot_writer = _LatestModelSnapshotWriter(
                partial(_write_agent_model_snapshot, callbacks),
                task_name=f"chatdata-model-snapshot-{thread_id}",
            )
            callbacks.model_snapshot_writer = model_snapshot_writer

            agent = create_data_agent(
                runtime_options,
                checkpointer=checkpointer,
                conversation_id=thread_id,
                identity_context=IdentityContext(
                    user_id=user_id,
                    operator_name=username,
                ),
                datasource_context=DatasourceContext(
                    datasource_id=datasource_id,
                    source_type=str((semantic_context.datasource_meta or {}).get("source_type") or ""),
                ),
                model_invocation=ModelInvocationConfig(
                    system_prompt=system_prompt,
                    model_id=model_id,
                    enable_thinking=enable_thinking,
                    thinking_effort=thinking_effort,
                    available_skills_xml=available_skills_xml,
                    memory_block=memory_block,
                ),
                workspace_runtime=workspace_runtime,
                run_controls=AgentRunControls(
                    run_id=run_id,
                    owner_token=owner_token,
                    inbox_service=agent_inbox_service,
                    admit_inbox_messages=partial(_admit_agent_inbox_messages, callbacks),
                    steering_tracker=steering_tracker,
                ),
                persist_model_snapshot=partial(_persist_agent_model_snapshot, callbacks),
            )
            log_phase("create_agent")
            stream_adapter = _create_agent_stream_adapter(
                callbacks, agent=agent, initial_state=initial_state, config=config, request=request,
            )

            await _admit_adopted_messages(
                callbacks, stream_adapter, steering_tracker, adopted_messages,
            )

            async for sse_event in stream_agent_turn(
                stream_adapter=stream_adapter,
                format_sse=_format_sse,
                persistence=TurnPersistenceHooks(
                    persist_user_message=partial(_persist_agent_user_message, callbacks),
                    persist_turn_history=partial(_persist_agent_turn_history_snapshot, callbacks),
                    persist_partial_history=partial(_persist_agent_partial_history, callbacks),
                ),
                hooks=TurnLifecycleHooks(
                    before_stream=partial(_before_agent_stream, callbacks),
                    after_finish=partial(_mark_agent_resume_checkpointed, callbacks),
                    # 会话记忆收尾钩子下沉到服务层：先同步打点，再调度防抖抽取
                    # （仅整轮正常结束时触发；interrupt/disconnected 不抽取）。
                    after_turn=partial(
                        after_agent_turn_with_memory,
                        partial(_log_agent_stream_done, callbacks),
                        stream_adapter,
                        conversation_id=thread_id,
                        workspace_root=workspace_paths["root"],
                        user_id=user_id,
                        username=username,
                        model_id=model_id,
                    ),
                    after_stream=partial(_confirm_agent_consumed_messages, callbacks),
                    on_interrupt=partial(_mark_agent_waiting_for_user, callbacks),
                ),
            ):
                yield sse_event

    except ContextInputLimitExceededError as exc:
        logger.info("智能问数上下文超出输入容量: conversation_id=%s", thread_id)
        yield _format_sse("error", {"error": str(exc)})
        raise AgentRunStreamFailedError from None
    except Exception as exc:
        logger.error("智能问数执行失败: conversation_id=%s", thread_id, exc_info=True)
        yield _format_sse("error", {"error": _model_failure_user_message(exc)})
        raise AgentRunStreamFailedError from None


async def sql_completion_handler(
    conversation_id: str,
    query: str,
    options: Optional[Dict[str, Any]] = None,
    user_id: str = "system",
    username: str | None = None,
    request: Request | None = None,
    file_ids: list[str] | None = None,
    queued_message_id: str | None = None,
    preserve_queued_messages: bool = False,
) -> StreamingResponse:
    """
    智能问数 completion 处理器。
    """
    run_options = parse_completion_run_options(options)

    # 保持懒创建会话；数据源组在生成流开始时写入业务会话表并校验。
    await conversation.get_or_create_conversation(AGENT_ID, conversation_id, user_id)
    runtime_context = {
        # 明确执行队列消息时，模型输入来自 adopted_messages，不能再把
        # completion 的展示文本作为第二条当前用户消息注入。
        "query": "" if queued_message_id else query,
        "datasource_id": run_options.datasource_id,
        "member_datasource_ids": run_options.member_datasource_ids,
        "semantic_config_id": run_options.semantic_config_id,
        "model_id": run_options.model_id,
        "file_selections": run_options.file_selections,
        "enable_thinking": run_options.enable_thinking,
        "thinking_effort": run_options.thinking_effort,
        "skill_names": run_options.skill_names,
        "operator_name": username or user_id,
        "options": options or {},
        "file_ids": file_ids or [],
    }
    run_id, adopted_messages = await agent_inbox_service.start_run(
        conversation_id,
        user_id,
        username or user_id,
        runtime_context,
        adopt_pending_messages=not queued_message_id and not preserve_queued_messages,
        selected_message_id=queued_message_id,
    )

    agent_run_runtime.submit(
        run_id=run_id,
        conversation_id=conversation_id,
        user_id=user_id,
        operator_name=username or user_id,
    )
    return StreamingResponse(
        _stream_run_events(run_id, request=request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-ChatData-Run-Id": run_id,
        },
    )


async def _stream_run_events(
    run_id: str,
    last_event_id: str = "0-0",
    request: Request | None = None,
):
    """订阅后台 Run 的 Redis Stream 事件。"""
    async for event in agent_run_event_service.stream(
        run_id=run_id,
        last_event_id=last_event_id,
        is_disconnected=request.is_disconnected if request else None,
    ):
        yield f"id: {event['stream_id']}\n{event['payload']}"


async def resume_chat_data_question_handler(
    *,
    conversation_id: str,
    user_id: str,
    username: str,
    run_id: str,
    runtime_context: dict[str, Any],
    answer: dict[str, Any],
    turn_id: str,
    last_event_id: str,
    request: Request | None,
) -> StreamingResponse:
    """Queue a checkpoint resume and subscribe to its durable Run events."""
    _ = runtime_context, answer, turn_id

    async def resumed_event_stream():
        agent_run_runtime.submit(
            run_id=run_id,
            conversation_id=conversation_id,
            user_id=user_id,
            operator_name=username,
        )
        async for event in _stream_run_events(
            run_id,
            last_event_id=last_event_id,
            request=request,
        ):
            yield event

    return StreamingResponse(
        resumed_event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-ChatData-Run-Id": run_id,
        },
    )


# 启动装配：AgentRunRuntime 通过注入获得 Run 流生成器，服务层不反向 import 本模块。
agent_run_runtime.set_stream_generator(_generate_chat_data_stream)
