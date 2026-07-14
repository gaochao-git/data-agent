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
    get_chat_data_graph,
    get_checkpointer,
)
from .agent import data_analyze_graph
from .agent.skills import build_builtin_skills_prompt, format_skills_prompt
from .services.datasource_manager import get_datasource_manager
from .services.data_analyze_context import build_data_analyze_context
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
    "dsl2sql_tool": "生成SQL",
    "semantic_query_tool": "查询数据",
    "calculator_tool": "计算",
    "skill_load": "加载技能",
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


def _message_end_payload(usage: Any | None = None) -> dict[str, Any]:
    """构造消息结束事件，timestamp 表示服务端确认完成的时间。"""
    payload: dict[str, Any] = {"timestamp": _get_timestamp()}
    if usage:
        payload["usage"] = usage
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
    graph: Any,
    config: dict[str, Any],
    segments: list[dict[str, Any]],
    as_node: str,
    persisted_count: int = 0,
) -> int:
    pending_segments = segments[persisted_count:]
    if not pending_segments or not hasattr(graph, "aupdate_state"):
        return persisted_count
    try:
        await graph.aupdate_state(config, {"reasoning_segments": pending_segments}, as_node=as_node)
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
    selected_member_ids = _normalize_member_datasource_ids(member_datasource_ids)
    if not selected_member_ids:
        return str(query_context or "").strip()

    selected_id_set = set(selected_member_ids)
    selected_targets = [
        (index, target)
        for index, target in enumerate(execution_targets, start=1)
        if _execution_target_member_id(target) in selected_id_set
    ]
    if not selected_targets:
        return str(query_context or "").strip()

    lines: list[str] = []
    base_context = str(query_context or "").strip()
    if base_context:
        lines.extend([base_context, ""])
    lines.append("## 当前用户已选择的执行对象数据源")
    lines.append("本次查询必须只使用以下 member_datasource_ids：")
    for index, target in selected_targets:
        member_name = _execution_target_name(target, index)
        member_id = _execution_target_member_id(target)
        org_code = _execution_target_org_code(target)
        line = f"- {member_name}"
        if member_id:
            line += f"（member_datasource_id: {member_id}）"
        if org_code and org_code != member_name:
            line += f"；编码：{org_code}"
        lines.append(line)
    return "\n".join(lines)


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
    token_usage = None

    try:
        async with get_checkpointer() as checkpointer:
            # 构建 graph，传入语义配置（如果有）
            # 语义模式时，graph 会添加 semantic_to_sql_tool 工具
            graph = get_chat_data_graph(
                checkpointer=checkpointer,
                semantic_config=semantic_config,
                datasource_id=datasource_id,
                model_id=model_id,
                enable_thinking=enable_thinking,
                user_id=user_id,
                available_skills_context=await _build_query_skills_context(user_id=user_id, skill_names=normalized_skill_names),
            )

            human_message_kwargs = {"timestamp": _get_timestamp()}
            if semantic_selection_event:
                human_message_kwargs["semantic_config_selection"] = semantic_selection_event
            if normalized_skill_names:
                human_message_kwargs["skill_names"] = normalized_skill_names

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
            initial_state = {
                "messages": [HumanMessage(
                    content=user_query,
                    additional_kwargs=human_message_kwargs
                )],
                "execution_context": {
                    "datasource_id": datasource_id,
                    "available_member_datasource_ids": available_member_datasource_ids,
                    "matched_member_datasource_ids": matched_member_datasource_ids,
                },
                "conversation_datasource_id": datasource_id,
                "semantic_config_id": semantic_config_id,
                "semantic_config": semantic_config,
                "user_id": user_id,
                "operator_name": username or "",
                "conversation_id": thread_id,
                "prompt_context": {
                    "datasource_group_context": datasource_group_context,
                    "semantic_config_directory_info": semantic_config_directory_info,
                },
            }

            tool_stream_state = _ToolStreamState()
            reasoning_segments: list[dict[str, Any]] = []
            persisted_reasoning_count = 0

            # 流式执行
            # 使用 messages 捕获 LLM 输出，custom 转发模型入口上下文占用等辅助事件。
            # 通过 metadata["langgraph_node"] 区分来源，过滤工具内部的 LLM 调用。
            async for event in graph.astream(
                initial_state,
                config=config,
                stream_mode=["messages", "custom"],
            ):
                # 检测客户端断连，停止消费事件
                if request and await request.is_disconnected():
                    logger.info(f"客户端已断开，停止流式输出: {thread_id}")
                    persisted_reasoning_count = await _persist_reasoning_segments(
                        graph,
                        config,
                        reasoning_segments,
                        as_node="agent",
                        persisted_count=persisted_reasoning_count,
                    )
                    return

                if isinstance(event, tuple) and len(event) == 2 and event[0] == "custom":
                    payload = event[1]
                    if isinstance(payload, dict) and payload.get("type") == "token_usage" and payload.get("usage"):
                        token_usage = payload.get("usage")
                        yield _format_sse("token_usage", token_usage)
                    continue

                if isinstance(event, tuple) and len(event) == 2 and event[0] == "messages":
                    message_chunk, metadata = event[1]
                else:
                    message_chunk, metadata = event

                # 过滤：只保留来自 agent 节点的消息（排除 tools 节点内部的 LLM 调用）
                node_name = metadata.get("langgraph_node", "")
                msg_type = getattr(message_chunk, 'type', 'ai')
                # AI 消息：只接受来自 agent 节点的
                # Tool 消息：来自 tools 节点，需要保留
                if msg_type == 'ai' and node_name != "agent":
                    logger.debug(f"[过滤] 跳过来自 {node_name} 节点的 AI 消息")
                    continue

                # 检查是否有内容或工具调用
                has_content = hasattr(message_chunk, 'content') and message_chunk.content
                has_tool_calls = hasattr(message_chunk, 'tool_calls') and message_chunk.tool_calls
                has_tool_call_chunks = hasattr(message_chunk, 'tool_call_chunks') and message_chunk.tool_call_chunks
                thinking_event = _format_thinking_event(message_chunk)
                if not has_content and not has_tool_calls and not has_tool_call_chunks and not thinking_event:
                    continue
                if thinking_event:
                    _append_reasoning_segment(
                        reasoning_segments,
                        _extract_reasoning_content(message_chunk),
                        tool_stream_state.visible_tool_count(),
                    )
                    yield thinking_event
                    if not has_content and not has_tool_calls and not has_tool_call_chunks:
                        continue

                # 调试：打印 AI 消息内容
                if msg_type == 'ai' and has_content:
                    logger.debug(f"[AI Content from {node_name}] {message_chunk.content[:200]}")

                # 根据消息类型选择事件名
                msg_type = getattr(message_chunk, 'type', 'ai')
                if msg_type == 'tool':
                    event_name = "tool"
                else:
                    event_name = "message"

                if has_tool_calls:
                    for tool_event in tool_stream_state.handle_tool_calls(message_chunk.tool_calls):
                        yield tool_event

                if has_tool_call_chunks:
                    for tool_event in tool_stream_state.handle_tool_call_chunks(message_chunk.tool_call_chunks):
                        yield tool_event

                # ToolMessage: 工具执行完成，发送带有完整 args 的消息
                if msg_type == 'tool':
                    msg_data = tool_stream_state.build_tool_message_payload(message_chunk)
                    yield _format_sse(event_name, msg_data)
                else:
                    # AI 消息添加时间戳
                    msg_data = message_chunk.model_dump()
                    msg_data["timestamp"] = _get_timestamp()
                    yield _format_sse(event_name, msg_data)

            persisted_reasoning_count = await _persist_reasoning_segments(
                graph,
                config,
                reasoning_segments,
                as_node="agent",
                persisted_count=persisted_reasoning_count,
            )
            state = await graph.aget_state(config)
            token_usage = state.values.get("token_usage") if state and state.values else None

        # 正常结束
        end_payload = _message_end_payload_from_state(state, token_usage)
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

    # 保持懒创建会话的既有行为；数据源组由下方 graph state 首次绑定。
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
            system_prompt = data_analyze_graph.build_data_analyze_system_prompt(
                skill=None,
                data_context=data_context,
                workspace_root=workspace_paths["root"],
                data_context_file=data_context_file,
                available_skills_xml=available_skills_xml,
            )
            log_phase("build_system_prompt")
            config = {"configurable": {"thread_id": conversation_id, "checkpoint_ns": ""}}
            human_message_kwargs = {"timestamp": _get_timestamp()}
            if semantic_selection_event:
                human_message_kwargs["semantic_config_selection"] = semantic_selection_event
            initial_state = {
                "messages": [HumanMessage(content=query, additional_kwargs=human_message_kwargs)],
                "conversation_datasource_id": datasource_id,
                "datasource_id": datasource_id,
                "semantic_config_id": semantic_config_id,
                "member_datasource_ids": member_datasource_ids or [],
                "user_id": user_id,
                "operator_name": username or "",
                "conversation_id": conversation_id,
            }

            async with get_checkpointer() as checkpointer:
                execution_session_ref = SandboxExecutionSessionRef()
                agent = data_analyze_graph.create_data_analyze_agent(
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
                tool_stream_state = _ToolStreamState()
                reasoning_segments: list[dict[str, Any]] = []
                persisted_reasoning_count = 0
                first_chunk_logged = False
                latest_token_usage: dict[str, Any] | None = None
                session_success = False
                try:
                    async for stream_mode, payload in agent.astream(initial_state, config=config, stream_mode=["messages", "custom"]):
                        if stream_mode == "custom":
                            if isinstance(payload, dict) and payload.get("type") == "token_usage":
                                usage = payload.get("usage")
                                if usage:
                                    latest_token_usage = usage
                                    yield _format_sse("token_usage", usage)
                            continue
                        message_chunk, metadata = payload
                        if not first_chunk_logged:
                            first_chunk_logged = True
                            log_phase("first_model_chunk")
                        if request and await request.is_disconnected():
                            logger.info("DataAnalyze 客户端已断开，停止流式输出: %s", conversation_id)
                            persisted_reasoning_count = await _persist_reasoning_segments(
                                agent,
                                config,
                                reasoning_segments,
                                as_node="model",
                                persisted_count=persisted_reasoning_count,
                            )
                            session_success = True
                            return
                        if not _is_data_analyze_visible_stream_message(message_chunk, metadata):
                            continue
                        has_content = bool(getattr(message_chunk, "content", None))
                        has_tool_calls = bool(getattr(message_chunk, "tool_calls", None))
                        has_tool_call_chunks = bool(getattr(message_chunk, "tool_call_chunks", None))
                        msg_type = getattr(message_chunk, "type", None)
                        thinking_event = _format_thinking_event(message_chunk)
                        if not has_content and not has_tool_calls and not has_tool_call_chunks and msg_type != "tool" and not thinking_event:
                            continue
                        if thinking_event:
                            _append_reasoning_segment(
                                reasoning_segments,
                                _extract_reasoning_content(message_chunk),
                                tool_stream_state.visible_tool_count(),
                            )
                            yield thinking_event
                            if not has_content and not has_tool_calls and not has_tool_call_chunks and msg_type != "tool":
                                continue
                        if has_tool_calls:
                            for tool_event in tool_stream_state.handle_tool_calls(getattr(message_chunk, "tool_calls", []) or []):
                                yield tool_event
                        if has_tool_call_chunks:
                            for tool_event in tool_stream_state.handle_tool_call_chunks(getattr(message_chunk, "tool_call_chunks", []) or []):
                                yield tool_event
                        msg_data = message_chunk.model_dump()
                        msg_data["timestamp"] = _get_timestamp()
                        if msg_type == "tool":
                            msg_data = tool_stream_state.build_tool_message_payload(message_chunk)
                            yield _format_sse("tool", msg_data)
                        else:
                            yield _format_sse("message", msg_data)
                    session_success = True
                except asyncio.CancelledError:
                    session_success = True
                    raise
                except Exception:
                    raise
                finally:
                    await _finish_workspace_execution_session(execution_session_ref, user_id, success=session_success)
                log_phase("agent_stream_done")
                persisted_reasoning_count = await _persist_reasoning_segments(
                    agent,
                    config,
                    reasoning_segments,
                    as_node="model",
                    persisted_count=persisted_reasoning_count,
                )
                state = await agent.aget_state(config) if hasattr(agent, "aget_state") else None
                state = await _persist_latest_ai_message_timestamp(agent, config, state)
                state_token_usage = state.values.get("token_usage") if state and state.values else None
                end_payload = _message_end_payload_from_state(state, latest_token_usage or state_token_usage)
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
