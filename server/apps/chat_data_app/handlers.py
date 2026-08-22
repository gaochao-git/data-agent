"""ChatData completion handler."""

import json
import asyncio
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Dict, Any, Optional
from uuid import uuid4

from fastapi import Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    ToolMessage,
    ToolMessageChunk,
)
from langgraph.types import Command

from utils.alarm import emit_llm_connection_alarm_if_needed, emit_llm_first_token_timeout_alarm
from utils.logger import get_logger
from utils import conversation
from .agent import (
    create_data_agent,
    AgentRunControls,
    resolve_agent_runtime_options,
    build_unified_system_prompt,
    get_checkpointer,
)
from .agent.skills import format_skills_prompt
from .services.agent_run.context_budget import ContextInputLimitExceededError, sanitize_context_usage_for_response
from .services.agent_run.conversation_runtime import stream_agent_turn
from .services.agent_run.agent_inbox_service import AgentRunLeaseLostError, agent_inbox_service
from .services.agent_run.conversation_event_service import conversation_event_service
from .services.agent_run.agent_run_event_service import agent_run_event_service
from .services.agent_run.agent_run_runtime import agent_run_runtime
from .services.agent_run.conversation_context import build_conversation_prompt_context
from .services.runtime_config import LLM_FIRST_TOKEN_TIMEOUT_SECONDS
from .services.agent_run.conversation_workspace import ConversationWorkspaceRuntime, ensure_conversation_workspace
from .services.agent_run.message_compose import (
    build_file_attachment_text,
    build_multimodal_message_content,
)
from .services.agent_run.uploaded_file_service import get_file_info
from .services.agent_run.runtime_skill_snapshot import (
    build_runtime_skill_snapshot_async,
    list_snapshot_skill_metadata,
)
from .agent.middleware.steering import SteeringMessageTracker
from .services.semantic.run.semantic_conversation_context import (
    build_agent_prompt_context,
    prepare_semantic_conversation_context,
)

logger = get_logger(__name__)

AGENT_ID = "chat_data"

# 用户输入超长时自动转为文件，阈值单位：字符数
USER_INPUT_OFFLOAD_THRESHOLD = 4000
USER_INPUT_PREVIEW_CHARS = 200

REASONING_TOOL_DISPLAY_NAMES = {
    "semantic_search": "语义检索",
    "search_dataset": "检索语义模型",
    "dsl2sql_tool": "生成 SQL",
    "semantic_query_tool": "查询数据",
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
    "dir_create": "创建目录",
    "dir_list": "列出目录",
    "dir_delete": "删除目录",
    "bash_run": "运行脚本",
    "officecli_run": "运行 OfficeCLI",
    "get_current_time": "获取当前时间",
    "present_artifacts": "展示文件",
    "ask_user_question": "询问用户",
}


class AgentRunStreamFailedError(RuntimeError):
    """SSE 已发送用户可见错误，外层只需按失败状态收口 Run。"""


def _get_timestamp() -> str:
    """获取当前时间戳字符串，格式：YYYY-MM-DD HH:MM:SS"""
    return time.strftime('%Y-%m-%d %H:%M:%S')


async def prepare_conversation_workspace(conversation_id: str, user_id: str = "system") -> dict[str, str]:
    """异步准备 ChatData 会话工作目录。"""
    return await asyncio.to_thread(_prepare_conversation_workspace_sync, conversation_id, user_id)


def _prepare_conversation_workspace_sync(conversation_id: str, user_id: str = "system") -> dict[str, str]:
    """同步创建 ChatData 会话工作目录。"""
    return ensure_conversation_workspace(conversation_id, user_id)


def _format_sse(event: str, data: dict | str) -> str:
    """
    格式化 SSE 事件

    Args:
        event: 事件类型 (message, tool, error, done)
        data: 事件数据（字典会被 JSON 序列化，字符串直接使用）

    Returns:
        符合 SSE 规范的字符串: "event: {event}\ndata: {data}\n\n"
    """
    if isinstance(data, dict):
        data_str = json.dumps(data, ensure_ascii=False)
    else:
        data_str = data
    return f"event: {event}\ndata: {data_str}\n\n"



def _build_user_message(
    content: str | list[dict[str, Any]],
    semantic_selection_event: dict[str, Any] | None = None,
    *,
    message_id: str | None = None,
    **extra_kwargs: Any,
) -> HumanMessage:
    """构造带服务端时间戳和前端选择留痕的用户消息。

    content 为纯字符串或多模态 content blocks（``[text_block, image_block...]``）。
    """
    reasoning_turn_id = message_id or str(uuid4())
    additional_kwargs = {
        "timestamp": _get_timestamp(),
        "reasoning_turn_id": reasoning_turn_id,
    }
    if semantic_selection_event:
        additional_kwargs["semantic_config_selection"] = semantic_selection_event
    additional_kwargs.update({key: value for key, value in extra_kwargs.items() if value})
    return HumanMessage(id=reasoning_turn_id, content=content, additional_kwargs=additional_kwargs)


def _build_agent_context_state(
    *,
    user_id: str | None,
    operator_name: str | None,
    conversation_id: str,
    **extra_state: Any,
) -> dict[str, Any]:
    """构造单一 Agent 的状态字段。"""
    state = {
        "user_id": user_id,
        "operator_name": operator_name or "",
        "conversation_id": conversation_id,
    }
    state.update(extra_state)
    return state


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


def _extract_reasoning_content(message_chunk: Any, msg_data: dict[str, Any] | None = None) -> str:
    """从 OpenAI compatible 流式 chunk 中提取思考内容。"""
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

    for candidate in candidates:
        for key in ("reasoning_content", "reasoning", "reasoning_text"):
            value = candidate.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def _format_reasoning_for_display(content: str) -> str:
    """将思考流中的内部工具名替换为更短的展示名，不影响真实工具调用。"""
    text = str(content or "")
    for tool_name, display_name in REASONING_TOOL_DISPLAY_NAMES.items():
        text = text.replace(tool_name, display_name)
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


def _normalize_enable_thinking(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return None


async def _persist_latest_ai_message_timestamp(agent: Any, config: dict[str, Any], state: Any | None) -> Any | None:
    """为 create_agent 产生的最新 AIMessage 补充完成时间，保持历史消息时间口径一致。"""
    values = getattr(state, "values", None) if state is not None else None
    messages = values.get("messages") if isinstance(values, dict) else []
    latest_ai_message = None
    for message in reversed(messages or []):
        if isinstance(message, AIMessage):
            latest_ai_message = message
            break
    if latest_ai_message is None:
        return state
    additional_kwargs = getattr(latest_ai_message, "additional_kwargs", None) or {}
    if additional_kwargs.get("timestamp") or not hasattr(agent, "aupdate_state"):
        return state
    try:
        patched_message = latest_ai_message.model_copy(deep=True) if hasattr(latest_ai_message, "model_copy") else latest_ai_message.copy(deep=True)
        patched_message.additional_kwargs = dict(additional_kwargs)
        patched_message.additional_kwargs["timestamp"] = _get_timestamp()
        await agent.aupdate_state(config, {"messages": [patched_message]}, as_node="model")
        return await agent.aget_state(config) if hasattr(agent, "aget_state") else state
    except Exception as exc:
        logger.warning("保存 DataAnalyze 助手消息时间失败: %s", exc)
        return state


async def _persist_agent_turn_history(
    stream_adapter: "_AgentSseStreamAdapter",
    *,
    conversation_id: str,
    user_id: str,
    username: str | None,
    turn_id: str,
    state_override: dict[str, Any] | None = None,
) -> None:
    """将本轮完整消息、工具结果和思考分段追加到独立历史。"""
    values = state_override
    if values is None:
        values = getattr(getattr(stream_adapter, "state", None), "values", None)
    state_messages = values.get("messages") if isinstance(values, dict) else []
    if not isinstance(state_messages, list):
        return
    messages = _merge_archived_turn_messages(
        getattr(stream_adapter, "archived_turn_messages", []),
        state_messages,
        turn_id=turn_id,
    )
    turn_ids = [
        message_id
        for message in messages
        if isinstance(message, HumanMessage)
        and (message_id := _stream_message_id(message))
    ]
    if turn_id and turn_id not in turn_ids:
        turn_ids.insert(0, turn_id)
    usage = (values or {}).get("token_usage")
    latest_usage = getattr(stream_adapter, "latest_token_usage", None)
    if isinstance(latest_usage, dict) and (latest_usage.get("breakdown") or latest_usage.get("llm_calls")):
        usage = latest_usage
    for current_turn_id in turn_ids:
        # token_usage 是会话级单行快照（event_key 固定），只随最后一轮写一次即可。
        is_last_turn = current_turn_id == turn_ids[-1]
        await conversation_event_service.persist_turn(
            conversation_id=conversation_id,
            user_id=user_id,
            operator_name=username or user_id,
            turn_id=current_turn_id,
            messages=messages,
            reasoning_segments=stream_adapter.reasoning_segments,
            context_compression_events=(values or {}).get("context_compression_events") or [],
            token_usage=sanitize_context_usage_for_response(usage) if is_last_turn else None,
        )


class _LatestModelSnapshotWriter:
    """Serialize snapshot writes while coalescing an obsolete pending snapshot."""

    def __init__(
        self,
        write_snapshot: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        task_name: str,
    ):
        self._write_snapshot = write_snapshot
        self._task_name = task_name
        self._pending: dict[str, Any] | None = None
        self._task: asyncio.Task[None] | None = None

    def submit(self, state: dict[str, Any]) -> None:
        snapshot = dict(state or {})
        if isinstance(snapshot.get("messages"), list):
            snapshot["messages"] = list(snapshot["messages"])
        if isinstance(snapshot.get("context_compression_events"), list):
            snapshot["context_compression_events"] = list(snapshot["context_compression_events"])
        self._pending = snapshot
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name=self._task_name)

    async def flush(self) -> None:
        while self._task is not None:
            task = self._task
            await task
            if self._task is task and self._pending is None:
                return

    async def _run(self) -> None:
        while self._pending is not None:
            snapshot = self._pending
            self._pending = None
            try:
                await self._write_snapshot(snapshot)
            except Exception:
                logger.warning("模型调用完成后的会话历史快照写入失败", exc_info=True)


def _merge_archived_turn_messages(
    archived_messages: list[BaseMessage],
    state_messages: list[BaseMessage],
    *,
    turn_id: str,
) -> list[BaseMessage]:
    """Merge pre-compression snapshots with the final checkpoint turn."""
    current_state_turn = _messages_from_turn(state_messages, turn_id)
    if not archived_messages:
        return current_state_turn or state_messages
    return _merge_message_snapshots(archived_messages, current_state_turn)


def _messages_from_turn(messages: list[BaseMessage], turn_id: str) -> list[BaseMessage]:
    for index, message in enumerate(messages):
        if isinstance(message, HumanMessage) and _stream_message_id(message) == turn_id:
            return list(messages[index:])
    return []


def _merge_message_snapshots(
    existing: list[BaseMessage],
    incoming: list[BaseMessage],
) -> list[BaseMessage]:
    """Merge monotonic turn snapshots while retaining original full payloads."""
    merged = list(existing)
    seen = {
        key
        for index, message in enumerate(merged)
        if (key := _stream_message_key(message, index)) is not None
    }
    for index, message in enumerate(incoming):
        key = _stream_message_key(message, index)
        if key is not None and key in seen:
            continue
        merged.append(message)
        if key is not None:
            seen.add(key)
    return merged


def _stream_message_key(message: BaseMessage, index: int) -> tuple[Any, ...] | None:
    message_id = _stream_message_id(message)
    if message_id:
        return ("id", message_id)
    if isinstance(message, ToolMessage):
        tool_call_id = str(getattr(message, "tool_call_id", "") or "").strip()
        return ("tool", tool_call_id) if tool_call_id else None
    if isinstance(message, AIMessage):
        tool_call_ids = tuple(
            str((tool_call or {}).get("id") or "").strip()
            for tool_call in getattr(message, "tool_calls", None) or []
            if str((tool_call or {}).get("id") or "").strip()
        )
        if tool_call_ids:
            return ("ai_tools", *tool_call_ids)
    if isinstance(message, HumanMessage):
        return ("human", str(message.content or ""))
    if isinstance(message, AIMessage):
        return ("ai", str(message.content or ""))
    return (message.__class__.__name__, str(getattr(message, "content", "") or ""), index)


def _stream_message_id(message: BaseMessage) -> str:
    return str(getattr(message, "id", "") or "").strip()


def _checkpoint_message_ids(checkpoint_tuple: Any | None) -> set[str]:
    """Read stable message IDs from a LangGraph checkpoint tuple."""
    checkpoint = getattr(checkpoint_tuple, "checkpoint", None)
    channel_values = checkpoint.get("channel_values") if isinstance(checkpoint, dict) else None
    messages = channel_values.get("messages") if isinstance(channel_values, dict) else None
    if not isinstance(messages, list):
        return set()
    return {
        message_id
        for message in messages
        if (message_id := _stream_message_id(message))
    }


def _checkpoint_resume_status(checkpoint_tuple: Any | None) -> bool | None:
    """Return whether a resume interrupt is still pending in the checkpoint.

    ``None`` means the checkpointer implementation did not expose pending
    writes, so callers can fall back to the durable Run marker used by older
    checkpoints. An explicit interrupt write is the authoritative indication
    that ``Command(resume=...)`` still needs to be submitted.
    """
    pending_writes = getattr(checkpoint_tuple, "pending_writes", None)
    if pending_writes is None:
        return None
    has_pending_interrupt = any(
        isinstance(write, (tuple, list))
        and len(write) >= 2
        and str(write[1]) == "__interrupt__"
        for write in pending_writes
    )
    return not has_pending_interrupt


async def _persist_partial_agent_turn_history(
    stream_adapter: "_AgentSseStreamAdapter | None",
    *,
    conversation_id: str,
    user_id: str,
    username: str | None,
    turn_id: str,
) -> None:
    """流中断时尽力保存已写入 checkpoint 的可见历史，不影响原始异常。"""
    if stream_adapter is None:
        return
    try:
        if stream_adapter.state is None and hasattr(stream_adapter.agent, "aget_state"):
            stream_adapter.state = await stream_adapter.agent.aget_state(stream_adapter.config)
        await _persist_agent_turn_history(
            stream_adapter,
            conversation_id=conversation_id,
            user_id=user_id,
            username=username,
            turn_id=turn_id,
        )
    except Exception:
        logger.warning("流异常后的会话历史补写失败: conversation_id=%s", conversation_id, exc_info=True)


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


def _normalize_semantic_selection_payload(value: Any) -> Dict[str, Any] | None:
    """标准化前端传入的指标集选择留痕。"""
    if not isinstance(value, dict):
        return None
    semantic_config_id = str(value.get("semantic_config_id") or "").strip()
    if not semantic_config_id:
        return None
    matched_metrics = value.get("matched_metrics")
    matched_dimensions = value.get("matched_dimensions")
    return {
        "semantic_config_id": semantic_config_id,
        "semantic_config_name": str(value.get("semantic_config_name") or semantic_config_id).strip(),
        "confidence": str(value.get("confidence") or "").strip(),
        "reason": str(value.get("reason") or "").strip(),
        "matched_metrics": [str(item).strip() for item in matched_metrics if str(item).strip()] if isinstance(matched_metrics, list) else [],
        "matched_dimensions": [str(item).strip() for item in matched_dimensions if str(item).strip()] if isinstance(matched_dimensions, list) else [],
        "auto_selected": bool(value.get("auto_selected")),
        "user_selected": bool(value.get("user_selected")),
    }


def _parse_tool_args_buffer(args_buffer: str, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    """解析流式工具参数，解析失败时回退到已知结构化参数。"""
    fallback_args = fallback or {}
    if not args_buffer:
        return fallback_args
    try:
        parsed = json.loads(args_buffer)
        return parsed if isinstance(parsed, dict) else fallback_args
    except json.JSONDecodeError:
        logger.warning("无法解析工具参数，使用兜底参数: %s", args_buffer[:200])
        return fallback_args


def _is_tool_result_error(content: Any) -> bool:
    """根据工具返回文本识别失败状态，便于前端展示。"""
    text = str(content or "").strip()
    if not text:
        return False
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict) and payload.get("type") == "query_result":
        return str(payload.get("status") or "").strip().lower() in {"failed", "cancelled"}
    return "执行失败" in text or "工具错误" in text or text.lower().startswith("error:")


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

    def visible_tool_count(self) -> int:
        return _visible_tool_count(self.tool_names)

    def handle_tool_calls(self, tool_calls: Any) -> list[str]:
        events: list[str] = []
        for tool_call in tool_calls or []:
            if not isinstance(tool_call, dict):
                continue
            tool_call_id = str(tool_call.get("id") or "").strip()
            tool_name = str(tool_call.get("name") or "").strip()
            tool_index = tool_call.get("index")
            if not tool_call_id or not tool_name:
                continue
            self.tool_names[tool_call_id] = tool_name
            self.active_tool_call_id = tool_call_id
            if isinstance(tool_index, int):
                self.index_map[tool_index] = tool_call_id
            self.args_buffer.setdefault(tool_call_id, "")
            if isinstance(tool_call.get("args"), dict):
                self.args_fallback[tool_call_id] = tool_call.get("args") or {}
            events.extend(self._running_event(tool_call_id, tool_name, tool_call.get("args") if isinstance(tool_call.get("args"), dict) else None))
        return events

    def handle_tool_call_chunks(self, tool_call_chunks: Any) -> list[str]:
        events: list[str] = []
        for chunk in tool_call_chunks or []:
            if not isinstance(chunk, dict):
                continue
            chunk_id = str(chunk.get("id") or "").strip()
            chunk_name = str(chunk.get("name") or "").strip()
            chunk_args = str(chunk.get("args") or "")
            chunk_index = chunk.get("index")
            if chunk_id:
                if chunk_name:
                    self.tool_names[chunk_id] = chunk_name
                self.active_tool_call_id = chunk_id
                if isinstance(chunk_index, int):
                    self.index_map[chunk_index] = chunk_id
                self.args_buffer.setdefault(chunk_id, "")
                running_tool_name = chunk_name or self.tool_names.get(chunk_id, "")
                if running_tool_name:
                    events.extend(self._running_event(chunk_id, running_tool_name, {}))

            current_tool_call_id = (
                chunk_id
                or (self.index_map.get(chunk_index) if isinstance(chunk_index, int) else None)
                or _resolve_active_tool_call_id(None, self.active_tool_call_id, self.running_tool_ids)
            )
            if current_tool_call_id and chunk_args:
                self.args_buffer[current_tool_call_id] = self.args_buffer.get(current_tool_call_id, "") + chunk_args
        return events

    def build_tool_message_payload(self, message_chunk: Any) -> dict[str, Any]:
        tool_call_id = str(getattr(message_chunk, "tool_call_id", "") or "").strip()
        msg_data = message_chunk.model_dump()
        msg_data["type"] = "tool"
        msg_data["timestamp"] = _get_timestamp()
        msg_data["tool_name"] = self.tool_names.get(tool_call_id, msg_data.get("name") or "")
        msg_data["status"] = "error" if _is_tool_result_error(msg_data.get("content")) else "completed"
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
        self.active_tool_call_id = _finish_running_tool(self.running_tool_ids, self.active_tool_call_id, tool_call_id)
        return msg_data

    def build_completion_event(self, tool_payload: dict[str, Any]) -> str:
        """将工具结果映射为完成或失败的统一生命周期事件。"""
        status = str(tool_payload.get("status") or "completed")
        event_type = "tool_failed" if status == "error" else "tool_completed"
        return _format_tool_lifecycle_event(event_type, tool_payload)

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
        }
        return [
            _format_tool_lifecycle_event("tool_started", payload),
            _format_tool_event(**payload),
        ]


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
        stream_started = False
        try:
            async for event in self.agent.astream(
                self.initial_state,
                config=self.config,
                stream_mode=["messages", "custom"],
            ):
                if not stream_started:
                    stream_started = True
                    self.graph_started = True
                    if self.on_stream_started:
                        await self.on_stream_started()
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
                if self.visible_message_filter and not self.visible_message_filter(message_chunk, metadata):
                    continue
                if self._has_visible_payload(message_chunk):
                    self._record_first_visible_payload(started_at)

                async for sse_event in self._handle_message_chunk(message_chunk, metadata):
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
        if payload.get("type") == "token_usage" and payload.get("usage"):
            self.latest_token_usage = sanitize_context_usage_for_response(payload.get("usage"))
            return [_format_sse("token_usage", self.latest_token_usage)]
        if payload.get("type") == "context_compression" and isinstance(payload.get("event"), dict):
            archived_messages = payload.get("archived_turn_messages")
            if isinstance(archived_messages, list):
                self.archived_turn_messages = _merge_message_snapshots(
                    self.archived_turn_messages,
                    archived_messages,
                )
            return [_format_sse("context_compression", payload["event"])]
        if payload.get("type") == "inbox_admitted" and isinstance(payload.get("message_ids"), list):
            message_ids = [str(message_id) for message_id in payload["message_ids"] if str(message_id)]
            return [_format_sse("inbox_admitted", {"message_ids": message_ids})] if message_ids else []
        if payload.get("type") == "tool_progress":
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
        return []

    def _alarm_context(self) -> dict[str, Any]:
        configurable = self.config.get("configurable") if isinstance(self.config, dict) else {}
        return {
            "thread_id": (configurable or {}).get("thread_id") or "",
            "checkpoint_ns": (configurable or {}).get("checkpoint_ns") or "",
            "debug_label": self.debug_label or "",
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
        has_content = bool(getattr(message_chunk, "content", None))
        has_tool_calls = bool(getattr(message_chunk, "tool_calls", None))
        has_tool_call_chunks = bool(getattr(message_chunk, "tool_call_chunks", None))
        is_ai_message = isinstance(message_chunk, (AIMessage, AIMessageChunk))
        is_tool_message = isinstance(message_chunk, (ToolMessage, ToolMessageChunk))
        thinking_event = _format_thinking_event(message_chunk)
        has_visible_payload = has_content or has_tool_calls or has_tool_call_chunks or thinking_event
        if not has_visible_payload and not (self.include_empty_tool_messages and is_tool_message):
            return
        if thinking_event:
            _append_reasoning_segment(
                self.reasoning_segments,
                _extract_reasoning_content(message_chunk),
                self.tool_stream_state.visible_tool_count(),
                self.reasoning_turn_id,
            )
            yield thinking_event
            if not has_content and not has_tool_calls and not has_tool_call_chunks and not is_tool_message:
                return
        if has_tool_calls:
            for tool_event in self.tool_stream_state.handle_tool_calls(getattr(message_chunk, "tool_calls", []) or []):
                yield tool_event
        if has_tool_call_chunks:
            for tool_event in self.tool_stream_state.handle_tool_call_chunks(getattr(message_chunk, "tool_call_chunks", []) or []):
                yield tool_event
        if is_tool_message:
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
    thread_id: str,
    user_query: str,
    datasource_id: str | None = None,
    user_id: str | None = None,
    username: str | None = None,
    member_datasource_ids: list[str] | None = None,
    semantic_config_id: str | None = None,
    request: Request | None = None,
    model_id: str | None = None,
    semantic_config_selection: dict | None = None,
    enable_thinking: bool | None = None,
    skill_names: list[str] | None = None,
    options: Optional[Dict[str, Any]] = None,
    file_ids: list[str] | None = None,
    run_id: str | None = None,
    adopted_messages: list[HumanMessage] | None = None,
    resume_answer: dict[str, Any] | None = None,
    resume_started: bool = False,
    resume_turn_id: str | None = None,
    turn_id: str | None = None,
    recovering: bool = False,
    owner_token: str | None = None,
):
    """流式执行单一 ChatData Agent。"""
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
    terminal_status: str | None = None
    try:
        async for event in _run_chat_data_agent_stream(
            thread_id=thread_id,
            user_query=user_query,
            datasource_id=datasource_id,
            user_id=user_id,
            username=username,
            member_datasource_ids=member_datasource_ids,
            semantic_config_id=semantic_config_id,
            request=request,
            model_id=model_id,
            semantic_config_selection=semantic_config_selection,
            enable_thinking=enable_thinking,
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
            log_phase=log_phase,
        ):
            yield event
        terminal_status = "finished"
    except asyncio.CancelledError as exc:
        terminal_status = "cancelled" if exc.args and exc.args[0] == "agent_run_cancelled" else None
        raise
    except AgentRunStreamFailedError:
        terminal_status = "failed"
    except Exception:
        terminal_status = "failed"
        # Preparation can fail before _run_chat_data_agent_stream enters its
        # own guarded section. The background runtime still needs a terminal
        # SSE pair so subscribers do not wait until their timeout.
        yield _format_sse("error", {"error": "智能问数执行失败，请稍后重试。"})
        yield _format_sse("done", "[DONE]")
        raise
    finally:
        if heartbeat_task:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task
        try:
            if run_id and terminal_status == "finished" and owner_token:
                await agent_inbox_service.complete_run(
                    run_id,
                    user_id or "system",
                    username or user_id or "system",
                    owner_token,
                )
            elif run_id and terminal_status in {"cancelled", "failed"}:
                await agent_inbox_service.finish_run(
                    run_id,
                    terminal_status or "cancelled",
                    user_id or "system",
                    username or user_id or "system",
                    owner_token,
                )
        except AgentRunLeaseLostError:
            logger.info("智能问数 Run 租约已丢失，跳过旧 Worker 收口: run_id=%s", run_id)


async def _heartbeat_agent_run(
    run_id: str,
    owner_task: asyncio.Task | None,
    owner_token: str | None = None,
) -> None:
    """保持运行租约；停止请求落到其他 worker 时协作取消本地执行。"""
    while True:
        await asyncio.sleep(2)
        try:
            heartbeat_status = await agent_inbox_service.heartbeat_run(run_id, owner_token)
            if heartbeat_status == "active" and owner_token:
                await agent_run_event_service.renew_owner(run_id, owner_token)
        except AgentRunLeaseLostError:
            if owner_task and not owner_task.done():
                owner_task.cancel("agent_run_lease_lost")
            return
        except Exception:
            logger.warning("ChatData Agent Run 心跳续约失败，下一周期重试: run_id=%s", run_id, exc_info=True)
            continue
        if heartbeat_status == "cancelling":
            if owner_task and not owner_task.done():
                owner_task.cancel("agent_run_cancelled")
            return
        if heartbeat_status != "active":
            # waiting_user/finished are normal transitions performed by this
            # execution. Stop renewing without cancelling the stream, which
            # still needs to publish question_requested/done. A true takeover
            # keeps status active and fails the owner-token assertion above.
            return


async def _run_chat_data_agent_stream(
    *,
    thread_id: str,
    user_query: str,
    datasource_id: str | None,
    user_id: str | None,
    username: str | None,
    member_datasource_ids: list[str] | None,
    semantic_config_id: str | None,
    request: Request | None,
    model_id: str | None,
    semantic_config_selection: dict | None,
    enable_thinking: bool | None,
    skill_names: list[str],
    options: Optional[Dict[str, Any]],
    file_ids: list[str] | None,
    run_id: str | None,
    adopted_messages: list[HumanMessage] | None = None,
    resume_answer: dict[str, Any] | None = None,
    resume_started: bool = False,
    resume_turn_id: str | None = None,
    turn_id: str | None = None,
    recovering: bool = False,
    owner_token: str | None = None,
    log_phase,
):
    """执行已注册 active run 的 Agent 流，生命周期由外层统一收口。"""
    normalized_skill_names = skill_names
    semantic_selection_event = _normalize_semantic_selection_payload(semantic_config_selection)
    semantic_context = await prepare_semantic_conversation_context(
        datasource_id=datasource_id,
        semantic_config_id=semantic_config_id,
        member_datasource_ids=member_datasource_ids,
        user_id=user_id,
        username=username,
        user_query=user_query,
    )
    if semantic_context.semantic_config_error:
        logger.warning("智能问数指标集准备失败: %s", semantic_context.semantic_config_error)
        yield _format_sse("error", {"error": semantic_context.semantic_config_error})
        yield _format_sse("done", "[DONE]")
        raise AgentRunStreamFailedError from None
    datasource_id = semantic_context.datasource_id
    if datasource_id and semantic_context.datasource_meta is not None:
        bound_datasource_id = await conversation.resolve_datasource_binding(
            thread_id,
            user_id or "system",
            datasource_id,
        )
        if bound_datasource_id != datasource_id:
            yield _format_sse("error", {"error": "当前会话已绑定其他数据源组，请新建会话后再切换。"})
            yield _format_sse("done", "[DONE]")
            raise AgentRunStreamFailedError from None
    semantic_config_id = semantic_context.semantic_config_id
    if semantic_context.semantic_config:
        logger.info(
            "智能问数使用手选指标集: %s",
            semantic_context.semantic_config.get("name") or semantic_context.semantic_config.get("id"),
        )
    else:
        logger.info("智能问数未手动选择指标集，将由模型按需调用选择工具")
    prompt_context = build_agent_prompt_context(semantic_context)
    log_phase("prepare_semantic_context")

    runtime_options = resolve_agent_runtime_options(
        user_id=user_id,
        override=(options or {}).get("runtime_options"),
    )
    workspace_paths = await prepare_conversation_workspace(thread_id, user_id or "system")
    workspace_runtime = ConversationWorkspaceRuntime(
        workspace_root=workspace_paths["root"],
        user_id=user_id or "system",
        backend=runtime_options.workspace_backend,
    )
    data_context = await build_conversation_prompt_context(prompt_context)
    available_skills: list[dict[str, Any]] = []
    available_skills_xml = format_skills_prompt([])
    # runtime-skills 是模型实际读取、file_read 解析及沙箱挂载的唯一技能真相。
    # 先构建/复用会话快照，再从同一目录生成系统提示词技能清单，不能回退管理域。
    try:
        snapshot_dir = await build_runtime_skill_snapshot_async(user_id or "system", thread_id)
        available_skills = await asyncio.to_thread(list_snapshot_skill_metadata, snapshot_dir)
        available_skills_xml = format_skills_prompt(available_skills)
    except Exception as err:
        logger.warning("会话技能快照加载失败，本轮不提供技能: %s", err)
    workspace_runtime.available_skill_names = [str(s.get("name") or "") for s in (available_skills or [])]

    config = {
        "configurable": {
            "thread_id": thread_id,
            "checkpoint_ns": ""
        }
    }

    stream_adapter: _AgentSseStreamAdapter | None = None
    user_message: HumanMessage | None = None
    steering_tracker = SteeringMessageTracker()
    stream_adapter_ref: dict[str, _AgentSseStreamAdapter] = {}

    # 解析 file_ids 对应的文件信息，用于追加到用户消息。
    # 上传与解析已解耦：只要求文件存在且归属当前会话，不再要求预解析完成。
    file_attachments: list[dict[str, Any]] = []
    if file_ids:
        for fid in file_ids:
            info = await get_file_info(fid, user_id=user_id or "system", thread_id=thread_id)
            if not info:
                logger.warning("关联文件不存在或无权限，已忽略: file_id=%s thread_id=%s", fid, thread_id)
                continue
            if info.get("thread_id") and thread_id and info["thread_id"] != thread_id:
                logger.warning("关联文件会话归属不匹配，已忽略: file_id=%s", fid)
                continue
            file_attachments.append({
                "file_id": fid,
                "original_filename": info["original_filename"],
                "file_type": info["file_type"],
                "file_size": info["file_size"],
                "virtual_path": f"/mnt/user-data/uploads/{info['storage_dir']}",
            })

    # 用户输入超长时自动转为文件写入 workspace outputs
    user_message_content = user_query
    if len(user_query) > USER_INPUT_OFFLOAD_THRESHOLD:
        offload_name = f"input-{time.strftime('%Y%m%d-%H%M%S')}.txt"
        offload_path = f"outputs/{offload_name}"
        await workspace_runtime.write_text_file(
            file_path=offload_path,
            content=user_query,
            mode="overwrite",
        )
        visible_path = f"/mnt/user-data/outputs/{offload_name}"
        preview = user_query[:USER_INPUT_PREVIEW_CHARS]
        user_message_content = (
            f"{preview}...\n\n"
            f"[用户输入较长（共 {len(user_query)} 字符），完整内容已保存到 {visible_path}，"
            "请用 file_read 分批读取后再回答。]"
        )
        file_attachments.append({
            "original_filename": offload_name,
            "parsed_path": visible_path,
        })
        logger.info("用户输入超长已转文件: conversation_id=%s chars=%d path=%s", thread_id, len(user_query), visible_path)

    try:
        async with get_checkpointer() as checkpointer:
            log_phase("open_checkpointer")
            # 构造用户消息内容：原始内容 + 文件引用（本轮模型带 vlm 能力时图片升级为 content blocks）
            message_content, _ = await build_multimodal_message_content(
                user_message_content,
                file_attachments,
                user_id=user_id or "system",
                thread_id=thread_id,
                model_id=model_id,
            )
            # 附件引用文本覆盖全部附件（含已升级为图片块的）：占位行随消息持久化，
            # 历史会话回放靠它合成附件卡片；对模型也只是多一行路径信息，无副作用。
            attachment_text = build_file_attachment_text(file_attachments)
            if attachment_text:
                if isinstance(message_content, list):
                    text_block = next(
                        (block for block in message_content if block.get("type") == "text"), None
                    )
                    if text_block is None:
                        text_block = {"type": "text", "text": ""}
                        message_content.insert(0, text_block)
                    text_block["text"] += attachment_text
                else:
                    message_content = f"{message_content}{attachment_text}"

            message_kwargs: dict[str, Any] = {}
            if normalized_skill_names:
                message_kwargs["skill_names"] = normalized_skill_names
            checkpoint_tuple = await checkpointer.aget_tuple(config) if recovering else None
            checkpoint_message_ids = _checkpoint_message_ids(checkpoint_tuple)
            current_turn_checkpointed = bool(turn_id and turn_id in checkpoint_message_ids)
            checkpoint_resume_status = _checkpoint_resume_status(checkpoint_tuple)
            resume_checkpointed = bool(
                resume_answer is not None
                and recovering
                and checkpoint_tuple is not None
                and (
                    checkpoint_resume_status is True
                    or (checkpoint_resume_status is None and resume_started)
                )
            )
            if recovering and current_turn_checkpointed and run_id and owner_token:
                await agent_inbox_service.reconcile_recovered_messages(
                    run_id,
                    checkpoint_message_ids,
                    owner_token,
                )
                # Checkpointed messages are already part of graph state; any
                # claimed-but-uncheckpointed messages were returned to pending
                # for the steering middleware to inject exactly once.
                adopted_messages = []
            user_message = None
            if resume_answer is None and not current_turn_checkpointed and (user_query.strip() or not adopted_messages):
                user_message = _build_user_message(
                    message_content,
                    semantic_selection_event,
                    message_id=turn_id,
                    **message_kwargs,
                )
            if resume_checkpointed:
                initial_state = None
            elif resume_answer is not None:
                initial_state = Command(resume=resume_answer)
            elif current_turn_checkpointed:
                initial_state = None
            else:
                initial_state = _build_agent_context_state(
                    user_id=user_id,
                    operator_name=username,
                    conversation_id=thread_id,
                    messages=[*(adopted_messages or []), *([user_message] if user_message else [])],
                    execution_context={
                        "datasource_id": datasource_id,
                        "available_member_datasource_ids": semantic_context.available_member_datasource_ids,
                        "matched_member_datasource_ids": semantic_context.matched_member_datasource_ids,
                    },
                    workspace={
                        "workspace_root": workspace_paths["root"],
                        "workspace_backend": runtime_options.workspace_backend,
                    },
                )
            system_prompt = build_unified_system_prompt(
                runtime_options,
                data_context=data_context,
                workspace_root=workspace_paths["root"],
                datasource_group_context=prompt_context["datasource_group_context"],
                semantic_config_directory_info=prompt_context.get("semantic_config_directory_info"),
                available_skills_xml=available_skills_xml,
            )
            log_phase("build_system_prompt")

            async def admit_inbox_messages(messages: list[HumanMessage]) -> None:
                for message in messages:
                    await conversation_event_service.persist_user_message(
                        conversation_id=thread_id,
                        user_id=user_id or "system",
                        operator_name=username or user_id or "system",
                        message=message,
                    )

            async def write_model_snapshot(state: dict[str, Any]) -> None:
                adapter = stream_adapter_ref.get("value")
                if adapter is None:
                    return
                await _persist_agent_turn_history(
                    adapter,
                    conversation_id=thread_id,
                    user_id=user_id,
                    username=username,
                    turn_id=resume_turn_id or turn_id or str(user_message.id if user_message else (adopted_messages[0].id if adopted_messages else "") or ""),
                    state_override=state,
                )

            model_snapshot_writer = _LatestModelSnapshotWriter(
                write_model_snapshot,
                task_name=f"chatdata-model-snapshot-{thread_id}",
            )

            async def persist_model_snapshot(state: dict[str, Any]) -> None:
                """Queue the latest snapshot so model-to-tool transition stays immediate."""
                model_snapshot_writer.submit(state)

            async def flush_model_snapshots() -> None:
                """Wait until the newest submitted snapshot is durable."""
                await model_snapshot_writer.flush()

            agent = create_data_agent(
                runtime_options,
                checkpointer=checkpointer,
                datasource_id=datasource_id,
                system_prompt=system_prompt,
                model_id=model_id,
                enable_thinking=enable_thinking,
                user_id=user_id,
                operator_name=username,
                datasource_group_context=prompt_context["datasource_group_context"],
                workspace_runtime=workspace_runtime,
                source_type=str((semantic_context.datasource_meta or {}).get("source_type") or ""),
                run_controls=AgentRunControls(
                    run_id=run_id,
                    owner_token=owner_token,
                    inbox_service=agent_inbox_service,
                    admit_inbox_messages=admit_inbox_messages,
                    steering_tracker=steering_tracker,
                ),
                persist_model_snapshot=persist_model_snapshot,
            )
            log_phase("create_agent")
            stream_adapter = _AgentSseStreamAdapter(
                agent=agent,
                initial_state=initial_state,
                config=config,
                request=request,
                visible_message_filter=_is_chat_data_visible_stream_message,
                disconnect_log_id=thread_id,
                debug_label="AI Content from model",
                include_empty_tool_messages=True,
                on_first_message=lambda: log_phase("first_model_chunk"),
                on_stream_started=(
                    (lambda: agent_inbox_service.mark_question_resume_started(run_id, owner_token))
                    if resume_answer is not None and run_id
                    else None
                ),
                reasoning_turn_id=resume_turn_id or turn_id or (
                    str(user_message.additional_kwargs.get("reasoning_turn_id") or "") if user_message else None
                ),
            )
            stream_adapter_ref["value"] = stream_adapter
            if recovering:
                stream_adapter.queue_event(_format_sse("run_restarted", {
                    "run_id": run_id,
                    "turn_id": turn_id,
                }))
            async def persist_user_message() -> None:
                if user_message is None:
                    return
                await conversation_event_service.persist_user_message(
                    conversation_id=thread_id,
                    user_id=user_id,
                    operator_name=username or user_id,
                    message=user_message,
                )
                log_phase("persist_user_message")

            async def persist_turn_history() -> None:
                await flush_model_snapshots()
                await _persist_agent_turn_history(
                    stream_adapter,
                    conversation_id=thread_id,
                    user_id=user_id,
                    username=username,
                    turn_id=resume_turn_id or turn_id or str(user_message.id if user_message else (adopted_messages[0].id if adopted_messages else "") or ""),
                )

            async def persist_partial_history() -> None:
                await flush_model_snapshots()
                await _persist_partial_agent_turn_history(
                    stream_adapter,
                    conversation_id=thread_id,
                    user_id=user_id,
                    username=username,
                    turn_id=resume_turn_id or turn_id or str(user_message.id if user_message else (adopted_messages[0].id if adopted_messages else "") or ""),
                )
                # A stream can fail after LangGraph has checkpointed the
                # answer but before the normal finish hook. Inspecting the
                # durable state here prevents the old question from being
                # resurrected on the next worker.
                if (
                    resume_answer is not None
                    and run_id
                    and stream_adapter.graph_started
                    and stream_adapter.state is not None
                    and not stream_adapter.pending_interrupt()
                ):
                    await agent_inbox_service.mark_question_resume_started(run_id, owner_token)

            async def mark_resume_checkpointed() -> None:
                if (
                    resume_answer is not None
                    and run_id
                    and stream_adapter.graph_started
                    and not stream_adapter.pending_interrupt()
                ):
                    await agent_inbox_service.mark_question_resume_started(run_id, owner_token)

            async def log_agent_stream_done() -> None:
                log_phase("agent_stream_done")

            async def mark_waiting_for_user(question: dict[str, Any]) -> None:
                if not run_id:
                    return
                question["turn_id"] = resume_turn_id or turn_id or str(user_message.id if user_message else (adopted_messages[0].id if adopted_messages else "") or "")
                await agent_inbox_service.mark_waiting_for_user(
                    run_id,
                    question,
                    user_id or "system",
                    username or user_id or "system",
                    owner_token,
                )

            async def confirm_consumed_messages() -> None:
                if run_id:
                    await agent_inbox_service.mark_consumed(run_id, steering_tracker.drain(), owner_token)

            async def before_stream() -> bool:
                if request and await request.is_disconnected():
                    logger.info("客户端已断开，停止流式输出: %s", thread_id)
                    if resume_answer is not None and run_id:
                        await agent_inbox_service.restore_question_answer(
                            run_id,
                            user_id or "system",
                            username or user_id or "system",
                            owner_token,
                        )
                    return False
                return True

            async def cancel_disconnected_run() -> None:
                # A subscriber disconnect is not a user cancellation. The
                # background Runtime continues and can be re-subscribed.
                return None

            # 上一轮异常遗留的排队消息由本轮接管，作为新一轮的首批输入：
            # 先持久化并通知前端上屏，再随 initial_state 进入本轮对话。
            await admit_inbox_messages(adopted_messages or [])
            if adopted_messages:
                adopted_ids = [str(message.id or "") for message in adopted_messages]
                steering_tracker.record(adopted_ids)
                stream_adapter.queue_event(_format_sse("inbox_admitted", {
                    "message_ids": adopted_ids,
                }))

            async def finalize_resources(success: bool) -> None:
                _ = success

            async for sse_event in stream_agent_turn(
                stream_adapter=stream_adapter,
                format_sse=_format_sse,
                persist_user_message=persist_user_message,
                persist_turn_history=persist_turn_history,
                persist_partial_history=persist_partial_history,
                before_stream=before_stream,
                after_finish=mark_resume_checkpointed,
                finalize_resources=finalize_resources,
                after_turn=log_agent_stream_done,
                after_stream=confirm_consumed_messages,
                on_interrupt=mark_waiting_for_user,
                on_disconnect=cancel_disconnected_run,
            ):
                yield sse_event

    except ContextInputLimitExceededError as exc:
        logger.info("智能问数上下文超出输入容量: conversation_id=%s", thread_id)
        yield _format_sse("error", {"error": str(exc)})
        yield _format_sse("done", "[DONE]")
        raise AgentRunStreamFailedError from None
    except Exception:
        logger.error("智能问数执行失败: conversation_id=%s", thread_id, exc_info=True)
        yield _format_sse("error", {"error": "智能问数执行失败，请稍后重试。"})
        yield _format_sse("done", "[DONE]")
        raise AgentRunStreamFailedError from None


async def sql_completion_handler(
    conversation_id: str,
    query: str,
    options: Optional[Dict[str, Any]] = None,
    user_id: str = "system",
    username: str | None = None,
    request: Request | None = None,
    file_ids: list[str] | None = None,
) -> StreamingResponse:
    """
    智能问数 completion 处理器。
    """
    options_data = options or {}
    datasource_id = str(options_data.get("datasource_id") or "").strip() or None
    semantic_config_id = str(options_data.get("semantic_config_id") or "").strip() or None  # 语义配置 ID（可选）
    model_id = options_data.get("model_id")  # 模型选择（可选）
    enable_thinking = _normalize_enable_thinking(options_data.get("enable_thinking"))
    member_datasource_ids = options_data.get("member_datasource_ids") or []
    semantic_config_selection = options_data.get("semantic_config_selection")
    raw_skill_names = options_data.get("skill_names") or []
    if isinstance(raw_skill_names, str):
        skill_names = [raw_skill_names]
    elif isinstance(raw_skill_names, list):
        skill_names = [str(item or "").strip() for item in raw_skill_names if str(item or "").strip()]
    else:
        skill_names = []

    # 保持懒创建会话；数据源组在生成流开始时写入业务会话表并校验。
    await conversation.get_or_create_conversation("", AGENT_ID, conversation_id, user_id)
    runtime_context = {
        "query": query,
        "datasource_id": datasource_id,
        "member_datasource_ids": member_datasource_ids,
        "semantic_config_id": semantic_config_id,
        "model_id": model_id,
        "semantic_config_selection": semantic_config_selection,
        "enable_thinking": enable_thinking,
        "skill_names": skill_names,
        "operator_name": username or user_id,
        "options": options_data,
        "file_ids": file_ids or [],
    }
    run_id, adopted_messages = await agent_inbox_service.start_run(
        conversation_id,
        user_id,
        username or user_id,
        runtime_context,
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
