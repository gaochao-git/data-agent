"""
chat_data_app 的 completion handler

语义模式：
- 当用户开启语义模式时，传入 semantic_config_id
- 系统加载对应语义配置，动态添加 semantic_to_sql_tool 工具
- LLM 根据语义配置中的指标/维度，自动调用工具生成 SQL
"""

import json
import asyncio
import time
from typing import Dict, Any, Optional

from fastapi import Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, AIMessage

from utils.logger import get_logger
from utils import conversation
from .agent import (
    create_chat_data_agent,
    get_checkpointer,
)
from .agent import analyze_data_agent
from .agent.prompts.chat_data_builder import build_chat_data_system_prompt
from .agent.skills import build_builtin_skills_prompt, format_skills_prompt
from .services.datasource_manager import get_datasource_manager
from .services.context_budget import sanitize_context_usage_for_response
from .services.data_analyze_context import build_data_analyze_context
from .services.execution_target_context import append_selected_execution_targets_to_context
from .services.data_analyze_workspace import ensure_data_analyze_workspace, write_workspace_json
from .services.skill_service import skill_service
from .services.semantic_tools import (
    format_semantic_config_directory_for_prompt,
    list_runtime_semantic_config_directory,
)
from .services.workspace_execution import (
    SandboxExecutionSessionRef,
    attach_workspace_execution_session,
    cleanup_workspace_execution_session,
    get_workspace_execution_session,
)

logger = get_logger(__name__)

AGENT_ID = "chat_data"
DATA_ANALYZE_AGENT_ID = "chat_data_data_analyze"
REASONING_TOOL_DISPLAY_NAMES = {
    "select_semantic_config_tool": "选择指标集",
    "dsl2sql_tool": "生成 SQL",
    "semantic_query_tool": "查询数据",
    "calculator_tool": "精确计算",
    "skill_load": "加载技能",
    "file_read": "读取文件",
    "file_glob": "查找文件",
    "file_grep": "搜索文件",
    "file_edit": "写入文件",
    "sandbox_run": "运行脚本",
    "present_artifacts": "展示文件",
    "output_artifact": "生成文件",
}


def _get_timestamp() -> str:
    """获取当前时间戳字符串，格式：YYYY-MM-DD HH:MM:SS"""
    return time.strftime('%Y-%m-%d %H:%M:%S')


async def prepare_data_analyze_workspace(conversation_id: str, data_context: dict[str, Any]) -> tuple[dict[str, str], str]:
    """异步准备 DataAnalyze 工作目录和上下文文件。"""
    return await asyncio.to_thread(_prepare_data_analyze_workspace_sync, conversation_id, data_context)


def _prepare_data_analyze_workspace_sync(conversation_id: str, data_context: dict[str, Any]) -> tuple[dict[str, str], str]:
    """同步创建 DataAnalyze 工作目录并写入上下文文件。"""
    workspace_paths = ensure_data_analyze_workspace(conversation_id)
    data_context_file = write_workspace_json(workspace_paths["root"], "workspace/data_context.json", data_context)
    return workspace_paths, data_context_file


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


def _build_user_message(content: str, semantic_selection_event: dict[str, Any] | None = None, **extra_kwargs: Any) -> HumanMessage:
    """构造带服务端时间戳和前端选择留痕的用户消息。"""
    additional_kwargs = {"timestamp": _get_timestamp()}
    if semantic_selection_event:
        additional_kwargs["semantic_config_selection"] = semantic_selection_event
    additional_kwargs.update({key: value for key, value in extra_kwargs.items() if value})
    return HumanMessage(content=content, additional_kwargs=additional_kwargs)


def _build_agent_context_state(
    *,
    datasource_id: str | None,
    semantic_config_id: str | None,
    user_id: str | None,
    operator_name: str | None,
    conversation_id: str,
    **extra_state: Any,
) -> dict[str, Any]:
    """构造问数和分析共享的 Agent 状态字段。"""
    state = {
        "conversation_datasource_id": datasource_id,
        "datasource_id": datasource_id,
        "semantic_config_id": semantic_config_id,
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
        if getattr(message, "type", None) != "ai":
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


def _append_reasoning_segment(segments: list[dict[str, Any]], content: str, tool_call_count: int) -> None:
    content = _format_reasoning_for_display(content)
    if not content:
        return
    if segments and segments[-1].get("tool_call_count") == tool_call_count:
        segments[-1]["content"] = f"{segments[-1].get('content') or ''}{content}"
        return
    segments.append({
        "id": f"reasoning-{len(segments)}",
        "content": content,
        "tool_call_count": tool_call_count,
    })


def _visible_tool_count(tool_names: dict[str, str]) -> int:
    """计算已出现且前端可见的工具数量，用于恢复思考与工具的时序。"""
    return sum(1 for name in tool_names.values() if name != "load_skill_tool")


def _is_data_analyze_visible_stream_message(message_chunk: Any, metadata: dict[str, Any]) -> bool:
    """过滤 DataAnalyze 单 Agent 流，避免工具内部模型调用进入主对话。"""
    node_name = str(metadata.get("langgraph_node") or "")
    if node_name.startswith("__"):
        return False
    if getattr(message_chunk, "type", None) == "tool":
        return True
    if node_name in {"model", "data_analyze_agent"}:
        return True
    return str(metadata.get("lc_agent_name") or "") == "data_analyze_agent"


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


async def _persist_reasoning_segments(
    agent: Any,
    config: dict[str, Any],
    segments: list[dict[str, Any]],
    as_node: str,
    persisted_count: int = 0,
) -> int:
    pending_segments = segments[persisted_count:]
    if not pending_segments or not hasattr(agent, "aupdate_state"):
        return persisted_count
    try:
        await agent.aupdate_state(config, {"reasoning_segments": pending_segments}, as_node=as_node)
        return len(segments)
    except Exception as exc:
        logger.warning("保存模型思考分段失败: %s", exc)
        return persisted_count


async def _persist_latest_ai_message_timestamp(agent: Any, config: dict[str, Any], state: Any | None) -> Any | None:
    """为 create_agent 产生的最新 AIMessage 补充完成时间，保持历史消息时间口径一致。"""
    values = getattr(state, "values", None) if state is not None else None
    messages = values.get("messages") if isinstance(values, dict) else []
    latest_ai_message = None
    for message in reversed(messages or []):
        if getattr(message, "type", None) == "ai":
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


async def _finish_workspace_execution_session(
    session_ref: SandboxExecutionSessionRef,
    user_id: str,
    *,
    success: bool,
) -> None:
    """按 DataAnalyze 执行结果归还或销毁沙箱 session。"""
    session = session_ref.current
    session_ref.current = None
    if session is None:
        return
    if success:
        try:
            await attach_workspace_execution_session(session, user_id)
        except Exception:
            await cleanup_workspace_execution_session(session)
            raise
        return
    await cleanup_workspace_execution_session(session)


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
    if text.startswith("[dashboard_write]"):
        try:
            payload = json.loads(text.replace("[dashboard_write]", "", 1).strip())
            return payload.get("success") is False
        except Exception:
            return False
    return "执行失败" in text or text.lower().startswith("error:")


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
        if tool_call_id:
            msg_data["accumulated_args"] = _parse_tool_args_buffer(
                self.args_buffer.get(tool_call_id, ""),
                self.args_fallback.get(tool_call_id),
            )
            self.args_buffer.pop(tool_call_id, None)
            self.args_fallback.pop(tool_call_id, None)
        self.active_tool_call_id = _finish_running_tool(self.running_tool_ids, self.active_tool_call_id, tool_call_id)
        return msg_data

    def _running_event(self, tool_call_id: str, tool_name: str, args: dict[str, Any] | None) -> list[str]:
        _register_running_tool(self.running_tool_ids, tool_call_id)
        if tool_call_id in self.emitted_running_tool_ids:
            return []
        self.emitted_running_tool_ids.add(tool_call_id)
        return [_format_tool_event(
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            status="running",
            args=args or {},
        )]


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
        reasoning_as_node: str = "model",
        include_empty_tool_messages: bool = False,
        on_first_message=None,
        debug_label: str | None = None,
    ):
        self.agent = agent
        self.initial_state = initial_state
        self.config = config
        self.request = request
        self.visible_message_filter = visible_message_filter
        self.disconnect_log_message = disconnect_log_message
        self.disconnect_log_id = disconnect_log_id
        self.reasoning_as_node = reasoning_as_node
        self.include_empty_tool_messages = include_empty_tool_messages
        self.on_first_message = on_first_message
        self.debug_label = debug_label
        self.tool_stream_state = _ToolStreamState()
        self.reasoning_segments: list[dict[str, Any]] = []
        self.persisted_reasoning_count = 0
        self.latest_token_usage: dict[str, Any] | None = None
        self.state: Any | None = None
        self.disconnected = False
        self._first_message_seen = False

    async def stream_events(self):
        """输出 token_usage、thinking、tool、message 事件。"""
        async for event in self.agent.astream(
            self.initial_state,
            config=self.config,
            stream_mode=["messages", "custom"],
        ):
            if await self._handle_disconnect():
                return

            event_kind, payload = self._parse_stream_event(event)
            if event_kind == "custom":
                token_usage_event = self._handle_custom_payload(payload)
                if token_usage_event:
                    yield token_usage_event
                continue
            if event_kind != "messages":
                continue

            message_chunk, metadata = payload
            if self.on_first_message and not self._first_message_seen:
                self._first_message_seen = True
                self.on_first_message()
            if self.visible_message_filter and not self.visible_message_filter(message_chunk, metadata):
                continue

            async for sse_event in self._handle_message_chunk(message_chunk, metadata):
                yield sse_event

    async def finish(self, *, persist_latest_ai_timestamp: bool = False, prefer_latest_usage: bool = False) -> dict[str, Any]:
        """持久化思考分段并构造 message_end payload。"""
        self.persisted_reasoning_count = await _persist_reasoning_segments(
            self.agent,
            self.config,
            self.reasoning_segments,
            as_node=self.reasoning_as_node,
            persisted_count=self.persisted_reasoning_count,
        )
        self.state = await self.agent.aget_state(self.config) if hasattr(self.agent, "aget_state") else None
        if persist_latest_ai_timestamp:
            self.state = await _persist_latest_ai_message_timestamp(self.agent, self.config, self.state)
        usage = self._resolve_end_usage(prefer_latest_usage)
        return _message_end_payload_from_state(self.state, usage)

    async def _handle_disconnect(self) -> bool:
        if not self.request or not await self.request.is_disconnected():
            return False
        logger.info(self.disconnect_log_message, self.disconnect_log_id)
        self.persisted_reasoning_count = await _persist_reasoning_segments(
            self.agent,
            self.config,
            self.reasoning_segments,
            as_node=self.reasoning_as_node,
            persisted_count=self.persisted_reasoning_count,
        )
        self.disconnected = True
        return True

    def _handle_custom_payload(self, payload: Any) -> str | None:
        if not isinstance(payload, dict) or payload.get("type") != "token_usage" or not payload.get("usage"):
            return None
        self.latest_token_usage = sanitize_context_usage_for_response(payload.get("usage"))
        return _format_sse("token_usage", self.latest_token_usage)

    async def _handle_message_chunk(self, message_chunk: Any, metadata: dict[str, Any]):
        has_content = bool(getattr(message_chunk, "content", None))
        has_tool_calls = bool(getattr(message_chunk, "tool_calls", None))
        has_tool_call_chunks = bool(getattr(message_chunk, "tool_call_chunks", None))
        msg_type = getattr(message_chunk, "type", None)
        thinking_event = _format_thinking_event(message_chunk)
        has_visible_payload = has_content or has_tool_calls or has_tool_call_chunks or thinking_event
        if not has_visible_payload and not (self.include_empty_tool_messages and msg_type == "tool"):
            return
        if thinking_event:
            _append_reasoning_segment(
                self.reasoning_segments,
                _extract_reasoning_content(message_chunk),
                self.tool_stream_state.visible_tool_count(),
            )
            yield thinking_event
            if not has_content and not has_tool_calls and not has_tool_call_chunks and msg_type != "tool":
                return
        if self.debug_label and msg_type == "ai" and has_content:
            logger.debug("[%s] %s", self.debug_label, str(message_chunk.content)[:200])
        if has_tool_calls:
            for tool_event in self.tool_stream_state.handle_tool_calls(getattr(message_chunk, "tool_calls", []) or []):
                yield tool_event
        if has_tool_call_chunks:
            for tool_event in self.tool_stream_state.handle_tool_call_chunks(getattr(message_chunk, "tool_call_chunks", []) or []):
                yield tool_event
        if msg_type == "tool":
            yield _format_sse("tool", self.tool_stream_state.build_tool_message_payload(message_chunk))
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
        return state_usage if state_usage is not None else self.latest_token_usage

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
    msg_type = getattr(message_chunk, "type", "ai")
    if msg_type == "ai" and node_name != "model":
        logger.debug("[过滤] 跳过来自 %s 节点的 AI 消息", node_name)
        return False
    return True


async def _get_semantic_config_async(
    datasource_id: str | None,
    semantic_config_id: str | None,
    user_query: str | None = None,
    user_id: str | None = None,
) -> dict | None:
    """异步获取语义配置，避免在 FastAPI 事件循环中同步访问存储。"""
    if not semantic_config_id or not datasource_id:
        return None

    try:
        ds_manager = get_datasource_manager()
        datasource = await ds_manager.get_datasource_async(datasource_id)
        if not datasource:
            return None

        source_type = datasource.get("source_type", "mysql")
        normalized_user_id = str(user_id or "").strip()
        if hasattr(ds_manager, "get_runtime_semantic_config_async"):
            cfg = await ds_manager.get_runtime_semantic_config_async(datasource_id, semantic_config_id, normalized_user_id) if normalized_user_id else await ds_manager.get_runtime_semantic_config_async(datasource_id, semantic_config_id)
        else:
            cfg = await ds_manager.get_semantic_config_async(datasource_id, semantic_config_id, normalized_user_id) if normalized_user_id else await ds_manager.get_semantic_config_async(datasource_id, semantic_config_id)
        if not cfg:
            logger.warning(f"未找到指定的语义配置: {semantic_config_id}")
            return None
        if not cfg.get("enabled", True):
            logger.warning(f"指定的语义配置未启用: {semantic_config_id}")
            return None

        result = cfg.copy()
        if 'relationships' in result and 'joins' not in result:
            result['joins'] = result['relationships']
        if source_type:
            result['source_type'] = source_type
        if semantic_config_id and not result.get("id"):
            result["id"] = semantic_config_id
        logger.info(f"加载语义配置: {result.get('name', semantic_config_id)}")
        return result
    except Exception as e:
        logger.warning(f"获取语义配置异常: {e}")
        return None


def _execution_target_member_id(target: dict) -> str:
    return str(target.get("member_datasource_id") or target.get("id") or "").strip()


def _execution_target_org_code(target: dict) -> str:
    source_config = target.get("source_config") or {}
    return str(target.get("org_code") or source_config.get("org_code") or "").strip()


def _execution_target_name(target: dict, index: int) -> str:
    source_config = target.get("source_config") or {}
    return str(
        target.get("member_name")
        or target.get("org_name")
        or source_config.get("org_name")
        or _execution_target_member_id(target)
        or f"执行对象{index}"
    ).strip()


def _execution_target_terms(target: dict, index: int) -> list[str]:
    terms = [
        _execution_target_name(target, index),
        _execution_target_org_code(target),
        _execution_target_member_id(target),
    ]
    return [term for term in dict.fromkeys(terms) if term]


def _spans_overlap(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


def _match_execution_targets_from_query(user_query: str | None, execution_targets: list[dict]) -> list[str]:
    """从用户原文中精确匹配执行对象，长名称优先，避免前缀机构误命中。"""
    text = str(user_query or "").strip()
    if not text or not execution_targets:
        return []

    lowered_text = text.casefold()
    matches: list[tuple[int, int, int, str]] = []
    for index, target in enumerate(execution_targets, start=1):
        member_id = _execution_target_member_id(target)
        if not member_id:
            continue
        for term in _execution_target_terms(target, index):
            lowered_term = term.casefold()
            start = lowered_text.find(lowered_term)
            while start >= 0:
                end = start + len(lowered_term)
                matches.append((start, end, len(lowered_term), member_id))
                start = lowered_text.find(lowered_term, start + 1)

    selected: list[tuple[int, int, int, str]] = []
    selected_spans: list[tuple[int, int]] = []
    for item in sorted(matches, key=lambda match: (-match[2], match[0])):
        span = (item[0], item[1])
        if any(_spans_overlap(span, selected_span) for selected_span in selected_spans):
            continue
        selected.append(item)
        selected_spans.append(span)

    ordered_member_ids = [
        item[3]
        for item in sorted(selected, key=lambda match: match[0])
    ]
    return list(dict.fromkeys(ordered_member_ids))


def _query_requests_all_execution_targets(user_query: str | None) -> bool:
    text = str(user_query or "").strip()
    if not text:
        return False
    keywords = ("全部机构", "所有机构", "各机构", "每个机构", "全部执行对象", "所有执行对象")
    return any(keyword in text for keyword in keywords)


def _match_execution_targets_from_query_only(
    execution_targets: list[dict],
    user_query: str | None,
) -> list[str]:
    """仅从用户原文解析执行对象匹配，不含前端显式选择。"""
    if _query_requests_all_execution_targets(user_query):
        return [
            _execution_target_member_id(target)
            for target in execution_targets
            if _execution_target_member_id(target)
        ]
    return _match_execution_targets_from_query(user_query, execution_targets)


def _normalize_member_datasource_ids(member_datasource_ids: list[str] | None) -> list[str]:
    return [
        str(item).strip()
        for item in member_datasource_ids or []
        if str(item).strip()
    ]


def _append_user_selected_execution_targets_to_context(
    query_context: str,
    execution_targets: list[dict],
    member_datasource_ids: list[str],
) -> str:
    """用户显式选择执行对象时，追加到数据源组智能问数说明末尾。"""
    return append_selected_execution_targets_to_context(query_context, execution_targets, member_datasource_ids)


async def _get_execution_targets_async(
    datasource_id: str | None,
    member_datasource_ids: list[str] | None = None,
) -> list[dict]:
    if not datasource_id:
        return []
    normalized_member_ids = _normalize_member_datasource_ids(member_datasource_ids)
    if not normalized_member_ids:
        return []
    try:
        ds_manager = get_datasource_manager()
        return await ds_manager.get_execution_datasources_async(
            datasource_id,
            normalized_member_ids,
        )
    except Exception as err:
        logger.warning(f"构造执行对象说明失败: {err}")
        return []


async def _build_semantic_config_directory_info_async(
    datasource_id: str | None,
    user_id: str | None = None,
    enabled: bool = True,
) -> str:
    """异步构造当前可见指标集轻量目录说明。"""
    if not enabled or not datasource_id:
        return ""
    try:
        directory = await list_runtime_semantic_config_directory(datasource_id, user_id)
        return format_semantic_config_directory_for_prompt(directory)
    except Exception as err:
        logger.warning(f"构造指标集目录说明失败: {err}")
        return ""


async def _build_query_skills_context(user_id: str | None = None, skill_names: list[str] | None = None) -> str:
    """构造智能问数业务知识技能上下文，显式指定的技能必须让模型稳定可见。"""
    _ = user_id
    normalized_skill_names = [
        str(item or "").strip()
        for item in skill_names or []
        if str(item or "").strip()
    ]
    if not normalized_skill_names:
        return ""
    requested = "、".join(f"`{name}`" for name in dict.fromkeys(normalized_skill_names))
    return (
        "## 本轮指定业务知识技能\n"
        f"用户本轮明确指定以下业务知识技能：{requested}。确定唯一指标集后，只读取这些指定技能，再继续查询规划；不要读取未指定技能。"
    )


async def _stream_sql_generation(
    thread_id: str,
    user_query: str,
    datasource_id: str | None = None,
    user_id: str | None = None,
    username: str | None = None,
    member_datasource_ids: list[str] | None = None,
    semantic_config_id: str | None = None,
    temp_semantic_config: dict | None = None,
    request: Request | None = None,
    model_id: str | None = None,
    semantic_config_selection: dict | None = None,
    enable_thinking: bool | None = None,
    skill_names: list[str] | None = None,
):
    """流式执行智能问数。"""
    datasource_id = str(datasource_id or "").strip() or None
    semantic_config_id = str(semantic_config_id or "").strip() or None
    normalized_skill_names = []
    for item in skill_names or []:
        skill_name = str(item or "").strip()
        if skill_name and skill_name not in normalized_skill_names:
            normalized_skill_names.append(skill_name)
    logger.info(f"开始执行智能问数，thread_id: {thread_id}, semantic_config_id: {semantic_config_id}, temp_config: {bool(temp_semantic_config)}")

    # 获取语义配置
    # 优先级：temp_semantic_config > semantic_config_id
    semantic_config = None
    semantic_selection_event = _normalize_semantic_selection_payload(semantic_config_selection)
    if temp_semantic_config:
        # 使用临时配置（用于测试，不保存到数据库）
        semantic_config = temp_semantic_config
        logger.info(f"使用临时语义配置（测试模式）")
    elif semantic_config_id is not None:
        semantic_config = await _get_semantic_config_async(datasource_id, semantic_config_id, user_query, user_id)
        if semantic_config:
            logger.info(f"使用语义模式: {semantic_config.get('name', semantic_config.get('id'))}")
        else:
            logger.warning("智能问数未找到可用指标集")
            yield _format_sse("error", {"error": f"智能问数未找到可用指标集: {semantic_config_id}"})
            yield _format_sse("done", "[DONE]")
            return
    else:
        logger.info("智能问数未手动选择指标集，将由模型按需调用选择工具")

    logger.info("智能问数使用统一语义工具链")
    if semantic_config and not semantic_config_id:
        semantic_config_id = str(semantic_config.get("id") or "").strip() or None

    config = {
        "configurable": {
            "thread_id": thread_id,
            "checkpoint_ns": ""
        }
    }

    try:
        async with get_checkpointer() as checkpointer:
            if normalized_skill_names:
                user_message = _build_user_message(
                    user_query,
                    semantic_selection_event,
                    skill_names=normalized_skill_names,
                )
            else:
                user_message = _build_user_message(user_query, semantic_selection_event)

            semantic_config_directory_info = ""
            if not semantic_config:
                semantic_config_directory_info = await _build_semantic_config_directory_info_async(datasource_id, user_id)
            datasource_group_context = ""
            if datasource_id:
                try:
                    datasource = await get_datasource_manager().get_datasource_async(datasource_id)
                    datasource_group_context = str((datasource or {}).get("query_context") or "").strip()
                except Exception as err:
                    logger.warning("读取数据源组智能问数说明失败: %s", err)
            explicit_member_datasource_ids = _normalize_member_datasource_ids(member_datasource_ids)
            all_execution_targets = await _get_execution_targets_async(
                datasource_id,
                explicit_member_datasource_ids,
            )
            available_member_datasource_ids = [
                _execution_target_member_id(target)
                for target in all_execution_targets
                if _execution_target_member_id(target)
            ]
            selected_member_datasource_ids = available_member_datasource_ids
            matched_member_datasource_ids = _match_execution_targets_from_query_only(all_execution_targets, user_query)
            if selected_member_datasource_ids:
                datasource_group_context = _append_user_selected_execution_targets_to_context(
                    datasource_group_context,
                    all_execution_targets,
                    selected_member_datasource_ids,
                )
            initial_state = _build_agent_context_state(
                datasource_id=datasource_id,
                semantic_config_id=semantic_config_id,
                user_id=user_id,
                operator_name=username,
                conversation_id=thread_id,
                messages=[user_message],
                execution_context={
                    "datasource_id": datasource_id,
                    "available_member_datasource_ids": available_member_datasource_ids,
                    "matched_member_datasource_ids": matched_member_datasource_ids,
                },
                semantic_config=semantic_config,
                prompt_context={
                    "datasource_group_context": datasource_group_context,
                    "semantic_config_directory_info": semantic_config_directory_info,
                },
            )
            system_prompt = build_chat_data_system_prompt(
                initial_state,
                semantic_config=semantic_config,
                available_skills_context=await _build_query_skills_context(
                    user_id=user_id,
                    skill_names=normalized_skill_names,
                ),
            )

            # 创建智能问数 agent，传入语义配置（如果有）
            agent = create_chat_data_agent(
                checkpointer=checkpointer,
                semantic_config=semantic_config,
                datasource_id=datasource_id,
                system_prompt=system_prompt,
                model_id=model_id,
                enable_thinking=enable_thinking,
                user_id=user_id,
            )

            stream_adapter = _AgentSseStreamAdapter(
                agent=agent,
                initial_state=initial_state,
                config=config,
                request=request,
                visible_message_filter=_is_chat_data_visible_stream_message,
                disconnect_log_id=thread_id,
                debug_label="AI Content from model",
            )
            async for sse_event in stream_adapter.stream_events():
                yield sse_event
            if stream_adapter.disconnected:
                return

            end_payload = await stream_adapter.finish()

        yield _format_sse("message_end", end_payload)
        yield _format_sse("done", "[DONE]")

    except Exception as e:
        logger.error(f"执行失败: {e}", exc_info=True)
        yield _format_sse("error", {"error": str(e)})
        yield _format_sse("done", "[DONE]")


async def sql_completion_handler(
    conversation_id: str,
    query: str,
    options: Optional[Dict[str, Any]] = None,
    user_id: str = "system",
    username: str | None = None,
    request: Request | None = None
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

    # 保持懒创建会话的既有行为；数据源组由下方 agent state 首次绑定。
    await conversation.get_or_create_conversation("", AGENT_ID, conversation_id, user_id)

    return StreamingResponse(
        _stream_sql_generation(
            thread_id=conversation_id,
            user_query=query,
            datasource_id=datasource_id,
            user_id=user_id,
            username=username,
            member_datasource_ids=member_datasource_ids,
            semantic_config_id=semantic_config_id,
            request=request,
            model_id=model_id,
            semantic_config_selection=semantic_config_selection,
            enable_thinking=enable_thinking,
            skill_names=skill_names,
        ),
        media_type="text/event-stream",
        headers={'Cache-Control': 'no-cache', 'Connection': 'keep-alive'}
    )


async def data_analyze_completion_handler(
    conversation_id: str,
    query: str,
    options: Optional[Dict[str, Any]] = None,
    user_id: str = "system",
    username: str | None = None,
    request: Request | None = None
) -> StreamingResponse:
    """DataAnalyze 智能体 completion 处理器。"""
    options_data = options or {}
    model_id = options_data.get("model_id")
    enable_thinking = _normalize_enable_thinking(options_data.get("enable_thinking"))
    datasource_id = str(options_data.get("datasource_id") or "").strip() or None
    semantic_config_id = str(options_data.get("semantic_config_id") or "").strip() or None
    member_datasource_ids = options_data.get("member_datasource_ids") or []
    semantic_config_selection = options_data.get("semantic_config_selection")

    async def generate_stream():
        nonlocal semantic_config_id
        started_at = time.perf_counter()
        last_phase_at = started_at

        def log_phase(phase: str) -> None:
            nonlocal last_phase_at
            now = time.perf_counter()
            logger.info(
                "DataAnalyze 阶段耗时: conversation_id=%s phase=%s elapsed_ms=%d total_ms=%d",
                conversation_id,
                phase,
                int((now - last_phase_at) * 1000),
                int((now - started_at) * 1000),
            )
            last_phase_at = now

        try:
            await conversation.get_or_create_conversation("", DATA_ANALYZE_AGENT_ID, conversation_id, user_id)
            log_phase("get_or_create_conversation")
            log_phase("skip_preload_skill")
            semantic_selection_event = _normalize_semantic_selection_payload(semantic_config_selection)
            data_context_kwargs = {"user_id": user_id}
            if member_datasource_ids:
                data_context_kwargs["member_datasource_ids"] = member_datasource_ids
            data_context = await build_data_analyze_context(datasource_id, semantic_config_id, **data_context_kwargs)
            log_phase("build_data_context")
            workspace_paths, data_context_file = await prepare_data_analyze_workspace(conversation_id, data_context)
            log_phase("ensure_workspace")
            try:
                available_skills = await skill_service.list_skills_async(user_id=user_id, scope="runtime", agent="analyze")
                available_skills_xml = format_skills_prompt([dict(item) for item in available_skills])
            except Exception as err:
                logger.warning("DataAnalyze 技能列表加载失败，回退代码目录: %s", err)
                available_skills_xml = build_builtin_skills_prompt()
            system_prompt = analyze_data_agent.build_data_analyze_system_prompt(
                skill=None,
                data_context=data_context,
                workspace_root=workspace_paths["root"],
                data_context_file=data_context_file,
                available_skills_xml=available_skills_xml,
            )
            log_phase("build_system_prompt")
            config = {"configurable": {"thread_id": conversation_id, "checkpoint_ns": ""}}
            initial_state = _build_agent_context_state(
                datasource_id=datasource_id,
                semantic_config_id=semantic_config_id,
                user_id=user_id,
                operator_name=username,
                conversation_id=conversation_id,
                messages=[_build_user_message(query, semantic_selection_event)],
                member_datasource_ids=member_datasource_ids or [],
            )

            async with get_checkpointer() as checkpointer:
                execution_session_ref = SandboxExecutionSessionRef()
                agent = analyze_data_agent.create_data_analyze_agent(
                    workspace_root=workspace_paths["root"],
                    system_prompt=system_prompt,
                    model_id=model_id,
                    checkpointer=checkpointer,
                    datasource_id=datasource_id,
                    semantic_config_id=semantic_config_id,
                    conversation_id=conversation_id,
                    member_datasource_ids=member_datasource_ids,
                    user_id=user_id,
                    operator_name=username,
                    execution_session_ref=execution_session_ref,
                    enable_thinking=enable_thinking,
                )
                log_phase("create_agent")
                if request and await request.is_disconnected():
                    logger.info("DataAnalyze 客户端已断开，停止流式输出: %s", conversation_id)
                    return
                execution_session_ref.current = await get_workspace_execution_session(workspace_paths["root"], user_id)
                log_phase("get_workspace_execution_session")
                session_success = False
                stream_adapter = _AgentSseStreamAdapter(
                    agent=agent,
                    initial_state=initial_state,
                    config=config,
                    request=request,
                    visible_message_filter=_is_data_analyze_visible_stream_message,
                    disconnect_log_message="DataAnalyze 客户端已断开，停止流式输出: %s",
                    disconnect_log_id=conversation_id,
                    include_empty_tool_messages=True,
                    on_first_message=lambda: log_phase("first_model_chunk"),
                )
                try:
                    async for sse_event in stream_adapter.stream_events():
                        yield sse_event
                    if stream_adapter.disconnected:
                        session_success = True
                        return
                    session_success = True
                except asyncio.CancelledError:
                    session_success = True
                    raise
                except Exception:
                    raise
                finally:
                    await _finish_workspace_execution_session(execution_session_ref, user_id, success=session_success)
                log_phase("agent_stream_done")
                end_payload = await stream_adapter.finish(
                    persist_latest_ai_timestamp=True,
                    prefer_latest_usage=True,
                )
                yield _format_sse("message_end", end_payload)
            yield _format_sse("done", "[DONE]")
        except Exception as e:
            logger.error("DataAnalyze 对话失败: conversation_id=%s error=%s", conversation_id, e, exc_info=True)
            yield _format_sse("error", {"error": str(e)})
            yield _format_sse("done", "[DONE]")

    return StreamingResponse(
        generate_stream(),
        media_type="text/event-stream",
        headers={'Cache-Control': 'no-cache', 'Connection': 'keep-alive'}
    )
