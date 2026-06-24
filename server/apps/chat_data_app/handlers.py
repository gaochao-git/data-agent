"""
chat_data_app 的 completion handler

使用 LangGraph interrupt() 函数实现 Human-in-the-Loop:
- semantic_query_tool 内部调用 interrupt() 暂停等待用户确认
- 用户确认后通过 Command(resume=...) 恢复执行

语义模式：
- 当用户开启语义模式时，传入 semantic_config_id
- 系统加载对应语义配置，动态添加 semantic_to_sql_tool 工具
- LLM 根据语义配置中的指标/维度，自动调用工具生成 SQL
"""

import json
import asyncio
import time
import uuid
from typing import Dict, Any, Optional

from fastapi import Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, AIMessage
from langgraph.types import Command

from utils.logger import get_logger
from utils.lang_util import conversation
from .agent import (
    get_chat_data_graph,
    get_checkpointer,
    get_all_tables_async,
    get_tables_create_format,
    llm_select_tables,
)
from .agent import data_analyze_graph
from .agent.skills import build_builtin_skills_prompt, format_skills_prompt
from .services.datasource_manager import get_datasource_manager
from .services.data_analyze_context import build_data_analyze_context
from .services.data_analyze_workspace import ensure_data_analyze_workspace, write_workspace_json
from .services.skill_service import skill_service
from .services.skill_context_service import build_available_skills_prompt
from .services.semantic_tools import (
    format_semantic_config_directory_for_prompt,
    list_runtime_semantic_config_directory,
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


def _format_tool_event(
    tool_name: str,
    tool_call_id: str | None,
    status: str,  # "pending" | "running" | "completed"
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
        status: 状态 (pending, running, completed)
        args: 工具参数（pending 状态时使用）
        result: 工具执行结果（completed 状态时使用）
        message: 提示信息（pending 状态时使用）
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


def _build_semantic_selection_error(selection: Optional[Dict[str, Any]]) -> str:
    """构造智能选择指标集失败提示。"""
    if not selection:
        return "当前数据源组没有可用于智能问数的指标集，请先维护并启用指标集"
    candidates = selection.get("candidates") or []
    candidate_names = [
        str(item.get("name") or item.get("id") or "").strip()
        for item in candidates[:3]
        if isinstance(item, dict) and str(item.get("name") or item.get("id") or "").strip()
    ]
    if selection.get("cross_config_required"):
        reason = str(selection.get("cross_config_reason") or selection.get("reason") or "").strip()
        suffix = f"：{reason}" if reason else ""
        return f"当前问题涉及多个指标集，暂不支持跨指标集合并查询{suffix}。请拆成多个问题，或维护一个覆盖该场景的综合指标集"
    if selection.get("ambiguous"):
        reason = str(selection.get("ambiguity_reason") or selection.get("reason") or "").strip()
        if candidate_names:
            return f"该问题可由多个指标集回答，请明确选择：{'、'.join(candidate_names)}"
        return reason or "该问题可由多个指标集回答，请先明确选择要使用的指标集"
    if candidates:
        candidate_text = "；".join(
            f"{item.get('name') or item.get('id')}: {item.get('reason') or item.get('confidence') or '可能相关'}"
            for item in candidates[:3]
            if isinstance(item, dict)
        )
        return f"未能确定最适合的指标集，可参考候选：{candidate_text}"
    reason = str(selection.get("reason") or "").strip()
    return reason or "当前可用指标集暂不支持该问题，请补充相关指标或维度后再查询"


def _build_semantic_select_required_event(selection: Optional[Dict[str, Any]], user_query: str) -> Dict[str, Any]:
    """构造前端候选指标集选择事件。"""
    selection = selection or {}
    candidates = []
    for item in selection.get("candidates") or []:
        if not isinstance(item, dict):
            continue
        config_id = str(item.get("id") or "").strip()
        if not config_id:
            continue
        candidates.append({
            "id": config_id,
            "name": item.get("name") or config_id,
            "coverage": item.get("coverage") or "",
            "confidence": item.get("confidence") or "",
            "reason": item.get("reason") or "",
        })
    return {
        "query": user_query,
        "message": _build_semantic_selection_error(selection),
        "reason": selection.get("ambiguity_reason") or selection.get("cross_config_reason") or selection.get("reason") or "",
        "ambiguous": bool(selection.get("ambiguous")),
        "cross_config_required": bool(selection.get("cross_config_required")),
        "candidates": candidates,
    }


def _extract_select_semantic_config_payload(content: Any) -> Dict[str, Any] | None:
    """解析选择指标集工具返回内容。"""
    text = str(content or "").strip()
    if not text.startswith("[select_semantic_config_tool]"):
        return None
    raw = text.replace("[select_semantic_config_tool]", "", 1).strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("无法解析指标集选择工具返回: %s", raw[:200])
        return None
    return payload if isinstance(payload, dict) else None


def _semantic_selection_event_from_tool_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """将选择工具返回转换成前端已使用的指标集选择事件。"""
    selected_config_id = str(payload.get("selected_config_id") or "").strip()
    return {
        "semantic_config_id": selected_config_id,
        "semantic_config_name": payload.get("selected_config_name") or selected_config_id,
        "confidence": payload.get("confidence") or "",
        "reason": payload.get("reason") or payload.get("message") or "",
        "matched_metrics": payload.get("matched_metrics") or [],
        "matched_dimensions": payload.get("matched_dimensions") or [],
        "auto_selected": True,
    }


def _semantic_select_required_event_from_tool_payload(payload: Dict[str, Any], user_query: str) -> Dict[str, Any]:
    """将选择工具返回转换成前端候选指标集选择事件。"""
    selection = {
        "reason": payload.get("message") or payload.get("reason") or "",
        "ambiguity_reason": payload.get("reason") or payload.get("message") or "",
        "cross_config_reason": payload.get("reason") or payload.get("message") or "",
        "ambiguous": bool(payload.get("ambiguous")),
        "cross_config_required": bool(payload.get("cross_config_required")),
        "candidates": payload.get("candidates") or [],
    }
    return _build_semantic_select_required_event(selection, user_query)


def _should_emit_semantic_select_required(payload: Dict[str, Any]) -> bool:
    """多指标集 query_plan 由智能体继续拆分执行，不触发前端二选一候选框。"""
    if payload.get("selected"):
        return False
    if isinstance(payload.get("query_plan"), dict):
        return False
    return bool(payload.get("candidates"))


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


def _get_interrupt_info(state) -> dict | None:
    """
    从状态中提取中断信息（由工具内部的 interrupt() 触发）

    LangGraph interrupt 结构：
    - state.tasks[].interrupts[].value: interrupt() 传入的数据
    - state.values.messages[-1].tool_calls: 最后一条 AI 消息的工具调用

    Returns:
        如果有中断: {"tool_name", "tool_call_id", "sql", "message", ...}
        否则: None
    """
    if not state or not state.tasks:
        return None

    # 从 tasks 中找到第一个 interrupt
    for task in state.tasks:
        if not (hasattr(task, 'interrupts') and task.interrupts):
            continue

        interrupt = task.interrupts[0]  # 取第一个中断
        if not hasattr(interrupt, 'value'):
            continue

        value = interrupt.value
        if not isinstance(value, dict):
            continue

        # 从最后一条消息的 tool_calls 中获取 tool_call_id 和 tool_name
        tool_call_id = None
        tool_name = None
        messages = state.values.get("messages", []) if state.values else []
        if messages:
            last_msg = messages[-1]
            if hasattr(last_msg, 'tool_calls') and last_msg.tool_calls:
                tc = last_msg.tool_calls[0]
                tool_call_id = tc.get("id")
                tool_name = tc.get("name")

        # 直接透传 interrupt.value，补充 tool 信息
        return {
            **value,  # 包含 sql, message 等
            "tool_name": tool_name or "unknown_tool",
            "tool_call_id": tool_call_id,
        }

    return None


async def _get_semantic_config_async(
    datasource_id: str | None,
    semantic_config_id: str | None,
    user_query: str | None = None,
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
        if hasattr(ds_manager, "get_runtime_semantic_config_async"):
            cfg = await ds_manager.get_runtime_semantic_config_async(datasource_id, semantic_config_id)
        else:
            cfg = await ds_manager.get_semantic_config_async(datasource_id, semantic_config_id)
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


def _format_execution_target_info(execution_targets: list[dict], user_query: str | None = None) -> tuple[str, list[str]]:
    if not execution_targets:
        return "", []

    matched_member_ids = [
        _execution_target_member_id(target)
        for target in execution_targets
        if _query_requests_all_execution_targets(user_query) and _execution_target_member_id(target)
    ]
    if not matched_member_ids:
        matched_member_ids = _match_execution_targets_from_query(user_query, execution_targets)

    lines = ["## 可用执行对象"]
    if len(execution_targets) == 1:
        lines.append("当前可用范围只有 1 个可用执行对象：")
    else:
        lines.append(f"当前可用范围有 {len(execution_targets)} 个可用执行对象。请根据用户问题选择本次要查询的机构；如果用户未指定机构，默认查询第 1 个机构，不要默认全部执行。机构名称必须按完整名称或机构编码精确匹配，不能用前缀或包含关系替代：")

    for index, target in enumerate(execution_targets, start=1):
        member_name = _execution_target_name(target, index)
        org_code = _execution_target_org_code(target)
        member_datasource_id = _execution_target_member_id(target)
        if org_code and org_code != str(member_name).strip():
            line = f"{index}. 机构名称：{member_name}"
            if member_datasource_id:
                line += f"；成员数据源ID：{member_datasource_id}"
            lines.append(line)
            lines.append(f"   机构编码：{org_code}")
        else:
            line = f"{index}. 机构名称：{member_name}"
            if member_datasource_id:
                line += f"；成员数据源ID：{member_datasource_id}"
            lines.append(line)

    if matched_member_ids:
        matched_targets = [
            (index, target)
            for index, target in enumerate(execution_targets, start=1)
            if _execution_target_member_id(target) in matched_member_ids
        ]
        lines.append("")
        lines.append("## 用户问题中的执行对象匹配")
        lines.append("已按用户原文精确匹配到以下机构；本次查询必须只使用这些机构对应的 member_datasource_ids：")
        for index, target in matched_targets:
            member_name = _execution_target_name(target, index)
            member_datasource_id = _execution_target_member_id(target)
            org_code = _execution_target_org_code(target)
            line = f"- 机构名称：{member_name}"
            if org_code:
                line += f"；机构编码：{org_code}"
            if member_datasource_id:
                line += f"；member_datasource_id：{member_datasource_id}"
            lines.append(line)

    return "\n".join(lines), matched_member_ids


async def _get_execution_targets_async(
    datasource_id: str | None,
    member_datasource_ids: list[str] | None = None,
    include_all_available: bool = False,
) -> list[dict]:
    if not datasource_id:
        return []
    try:
        ds_manager = get_datasource_manager()
        if include_all_available:
            datasource = await ds_manager.get_datasource_async(datasource_id)
            if datasource and hasattr(ds_manager, "_build_execution_datasource_targets"):
                all_member_ids = [
                    _execution_target_member_id(member)
                    for member in datasource.get("member_sources") or []
                    if _execution_target_member_id(member)
                ]
                return ds_manager._build_execution_datasource_targets(datasource, all_member_ids)
        return await ds_manager.get_execution_datasources_async(
            datasource_id,
            member_datasource_ids,
        )
    except Exception as err:
        logger.warning(f"构造执行对象说明失败: {err}")
        return []


async def _build_execution_target_info_async(
    datasource_id: str | None,
    member_datasource_ids: list[str] | None = None,
    include_all_available: bool = False,
    user_query: str | None = None,
) -> str:
    """异步构造可用执行对象说明，避免同步读取数据源配置。"""
    execution_targets = await _get_execution_targets_async(datasource_id, member_datasource_ids, include_all_available)
    info, _matched_member_ids = _format_execution_target_info(execution_targets, user_query)
    return info


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
    context = await build_available_skills_prompt(user_id=user_id or "")
    normalized_skill_names = [
        str(item or "").strip()
        for item in skill_names or []
        if str(item or "").strip()
    ]
    if not normalized_skill_names:
        return context
    requested = "、".join(f"`{name}`" for name in dict.fromkeys(normalized_skill_names))
    requested_context = (
        "## 本轮指定业务知识技能\n"
        f"用户本轮明确指定以下业务知识技能：{requested}。确定唯一指标集后，先调用 `skill_load` 加载对应技能，再继续查询规划。"
    )
    return f"{context}\n\n{requested_context}".strip() if context else requested_context


async def _stream_sql_generation(
    thread_id: str,
    user_query: str,
    datasource_id: str | None = None,
    user_id: str | None = None,
    username: str | None = None,
    member_datasource_ids: list[str] | None = None,
    mode: str = "generate",
    semantic_config_id: str | None = None,
    temp_semantic_config: dict | None = None,
    request: Request | None = None,
    model_id: str | None = None,
    semantic_config_selection: dict | None = None,
    enable_thinking: bool | None = None,
    skill_names: list[str] | None = None,
):
    """流式执行 SQL 生成"""
    datasource_id = str(datasource_id or "").strip() or None
    semantic_config_id = str(semantic_config_id or "").strip() or None
    normalized_skill_names = []
    for item in skill_names or []:
        skill_name = str(item or "").strip()
        if skill_name and skill_name not in normalized_skill_names:
            normalized_skill_names.append(skill_name)
    logger.info(f"开始执行，thread_id: {thread_id}, mode: {mode}, semantic_config_id: {semantic_config_id}, temp_config: {bool(temp_semantic_config)}")

    # 获取语义配置
    # 优先级：temp_semantic_config > semantic_config_id
    semantic_config = None
    semantic_selection_event = _normalize_semantic_selection_payload(semantic_config_selection)
    if temp_semantic_config:
        # 使用临时配置（用于测试，不保存到数据库）
        semantic_config = temp_semantic_config
        logger.info(f"使用临时语义配置（测试模式）")
    elif semantic_config_id is not None:
        semantic_config = await _get_semantic_config_async(datasource_id, semantic_config_id, user_query)
        if semantic_config:
            logger.info(f"使用语义模式: {semantic_config.get('name', semantic_config.get('id'))}")
        else:
            logger.warning("query 模式未找到可用语义配置")
            if mode == "query":
                yield _format_sse("error", {"error": f"query 模式未找到可用语义配置: {semantic_config_id}"})
                yield _format_sse("done", "[DONE]")
                return
    elif mode == "query":
        logger.info("query 模式未手动选择指标集，将由模型按需调用选择工具")

    if mode == "query":
        logger.info("query 模式使用统一语义工具链")
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
                model_id=model_id,
                mode=mode,
                enable_thinking=enable_thinking,
                user_id=user_id,
                available_skills_context=await _build_query_skills_context(user_id=user_id, skill_names=normalized_skill_names) if mode == "query" else "",
            )

            # 语义模式不需要表结构，semantic_to_sql_tool 内部已封装
            # NL2SQL 模式需要加载表结构给 LLM
            tables_ddl_info = ""
            new_loaded_names = []

            if mode != "query" and not semantic_config:
                # NL2SQL 模式：加载表结构
                all_tables = await get_all_tables_async(datasource_id)

                # 获取历史状态，检查已加载的表
                loaded_table_names: set[str] = set()
                try:
                    state = await graph.aget_state(config)
                    if state and state.values:
                        loaded_table_names = set(state.values.get("loaded_table_names") or [])
                        logger.debug(f"历史已加载的表: {loaded_table_names}")
                except Exception as e:
                    logger.debug(f"获取历史状态失败（可能是新会话）: {e}")

                # 根据用户问题选择需要的表
                selected_table_names = await llm_select_tables(user_query, all_tables)
                logger.debug(f"本次需要的表: {selected_table_names}")

                # 计算需要新加载的表
                new_table_names = set(selected_table_names) - loaded_table_names
                logger.debug(f"需要新加载的表: {new_table_names}")

                # 构建本次传入的状态
                if new_table_names:
                    tables_ddl_info = get_tables_create_format(all_tables, list(new_table_names))
                    new_loaded_names = list(new_table_names)
                elif selected_table_names:
                    logger.debug("复用历史表结构信息")

            human_message_kwargs = {"timestamp": _get_timestamp()}
            if semantic_selection_event:
                human_message_kwargs["semantic_config_selection"] = semantic_selection_event
            if normalized_skill_names:
                human_message_kwargs["skill_names"] = normalized_skill_names

            semantic_config_directory_info = ""
            if mode == "query" and not semantic_config:
                semantic_config_directory_info = await _build_semantic_config_directory_info_async(datasource_id, user_id)
            execution_targets = await _get_execution_targets_async(
                datasource_id,
                member_datasource_ids,
                include_all_available=(mode == "query" and not member_datasource_ids),
            )
            all_execution_target_info, matched_member_datasource_ids = _format_execution_target_info(
                execution_targets,
                user_query if mode == "query" else None,
            )
            all_member_datasource_ids = [
                _execution_target_member_id(target)
                for target in execution_targets
                if _execution_target_member_id(target)
            ] if mode == "query" else []

            initial_state = {
                "messages": [HumanMessage(
                    content=user_query,
                    additional_kwargs=human_message_kwargs
                )],
                "tables_ddl_info": tables_ddl_info,
                "loaded_table_names": new_loaded_names,
                "mode": mode,
                "datasource_id": datasource_id,  # 传递数据源ID，用于真实SQL执行
                "semantic_config_id": semantic_config_id,
                "semantic_config": semantic_config,
                "user_id": user_id,
                "operator_name": username or "",
                "conversation_id": thread_id,
                "member_datasource_ids": member_datasource_ids or [],
                "available_member_datasource_ids": all_member_datasource_ids,
                "matched_member_datasource_ids": matched_member_datasource_ids,
                "require_explicit_member_selection": mode == "query",
                "execution_target_info": all_execution_target_info,
                "semantic_config_directory_info": semantic_config_directory_info,
            }

            # 用于跟踪已发送 running 状态的工具
            running_tool_ids: list[str] = []
            active_tool_call_id: str | None = None
            # 用于累积 tool_call_chunks 的 args（JSON 字符串拼接）
            tool_call_args_buffer: dict[str, str] = {}
            # 用于存储工具名称
            tool_call_names: dict[str, str] = {}
            reasoning_segments: list[dict[str, Any]] = []
            persisted_reasoning_count = 0

            # 流式执行
            # 使用 stream_mode="messages" 捕获 LLM 输出
            # 通过 metadata["langgraph_node"] 区分来源，过滤工具内部的 LLM 调用
            async for event in graph.astream(
                initial_state,
                config=config,
                stream_mode="messages"
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
                        _visible_tool_count(tool_call_names),
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

                # 检测 tool_calls 开始，发送 running 状态，并初始化 args buffer
                if has_tool_calls:
                    for tc in message_chunk.tool_calls:
                        tc_id = tc.get("id")
                        tc_name = tc.get("name")
                        # 只有当 id 和 name 都存在且未发送过时，发送 running 状态
                        if tc_id and tc_name and tc_id not in running_tool_ids:
                            _register_running_tool(running_tool_ids, tc_id)
                            tool_call_names[tc_id] = tc_name
                            tool_call_args_buffer[tc_id] = ""  # 初始化 args buffer
                            yield _format_tool_event(
                                tool_name=tc_name,
                                tool_call_id=tc_id,
                                status="running",
                            )

                # 累积 tool_call_chunks 的 args（JSON 字符串）
                # 注意：LangChain 流式模式下，只有第一个 chunk 有 id，后续 chunks 的 id 为 None
                # 需要使用 index 来关联，或者记住当前活跃的 tool_call_id
                if has_tool_call_chunks:
                    for chunk in message_chunk.tool_call_chunks:
                        chunk_id = chunk.get("id")
                        chunk_args = chunk.get("args", "")
                        chunk_name = chunk.get("name")

                        # 如果有 id，记录为当前活跃的 tool_call（只在首次出现时初始化 buffer）
                        if chunk_id:
                            tool_call_names[chunk_id] = chunk_name or tool_call_names.get(chunk_id, "")
                            # 只有新的 tool_call_id 才初始化 buffer
                            if chunk_id not in tool_call_args_buffer:
                                tool_call_args_buffer[chunk_id] = ""
                            _register_running_tool(running_tool_ids, chunk_id)

                        # 优先使用 chunk 自带 id，其次使用最近一次明确记录的活跃 tool_call_id
                        current_tool_call_id = _resolve_active_tool_call_id(
                            chunk_id,
                            active_tool_call_id,
                            running_tool_ids,
                        )
                        if chunk_id:
                            active_tool_call_id = current_tool_call_id

                        # 累积 args
                        if current_tool_call_id and chunk_args:
                            if current_tool_call_id not in tool_call_args_buffer:
                                tool_call_args_buffer[current_tool_call_id] = ""
                            tool_call_args_buffer[current_tool_call_id] += chunk_args

                # ToolMessage: 工具执行完成，发送带有完整 args 的消息
                if msg_type == 'tool':
                    tool_call_id = getattr(message_chunk, 'tool_call_id', '')
                    # 解析累积的 args JSON 字符串
                    accumulated_args = {}
                    if tool_call_id and tool_call_id in tool_call_args_buffer:
                        args_str = tool_call_args_buffer[tool_call_id]
                        if args_str:
                            try:
                                accumulated_args = json.loads(args_str)
                            except json.JSONDecodeError:
                                logger.warning(f"无法解析工具参数: {args_str[:100]}")
                        # 清理 buffer
                        del tool_call_args_buffer[tool_call_id]
                    active_tool_call_id = _finish_running_tool(running_tool_ids, active_tool_call_id, tool_call_id)

                    # 构造增强的消息，包含完整的 args 和时间戳
                    msg_data = message_chunk.model_dump()
                    msg_data["timestamp"] = _get_timestamp()
                    if accumulated_args:
                        # 添加累积的 args 到消息中（前端可以从这里获取）
                        msg_data["accumulated_args"] = accumulated_args
                        msg_data["tool_name"] = tool_call_names.get(tool_call_id, "")
                    tool_name = msg_data.get("tool_name") or tool_call_names.get(tool_call_id, "")
                    if tool_name == "select_semantic_config_tool":
                        selection_payload = _extract_select_semantic_config_payload(msg_data.get("content"))
                        if selection_payload:
                            if selection_payload.get("selected"):
                                yield _format_sse("semantic_config_selected", _semantic_selection_event_from_tool_payload(selection_payload))
                            elif _should_emit_semantic_select_required(selection_payload):
                                yield _format_sse("semantic_config_select_required", _semantic_select_required_event_from_tool_payload(selection_payload, user_query))
                    yield _format_sse(event_name, msg_data)
                else:
                    # AI 消息添加时间戳
                    msg_data = message_chunk.model_dump()
                    msg_data["timestamp"] = _get_timestamp()
                    yield _format_sse(event_name, msg_data)

            # 检查是否被 interrupt() 中断
            persisted_reasoning_count = await _persist_reasoning_segments(
                graph,
                config,
                reasoning_segments,
                as_node="agent",
                persisted_count=persisted_reasoning_count,
            )
            state = await graph.aget_state(config)
            token_usage = state.values.get("token_usage") if state and state.values else None
            interrupt_info = _get_interrupt_info(state)
            if interrupt_info:
                # 返回待确认的工具信息（status: pending）
                sql_preview = interrupt_info.get('args', {}).get('sql', '')[:50]
                logger.info(f"检测到工具中断，等待用户确认: {sql_preview}...")
                yield _format_tool_event(
                    tool_name=interrupt_info.get("tool_name", "unknown_tool"),
                    tool_call_id=interrupt_info.get("tool_call_id"),
                    status="pending",
                    args=interrupt_info.get("args"),
                    message=interrupt_info.get("message"),
                )
                end_payload = _message_end_payload_from_state(state, token_usage)
                yield _format_sse("message_end", end_payload)
                yield _format_sse("done", "[DONE]")
                return

        # 正常结束
        end_payload = _message_end_payload_from_state(state, token_usage)
        yield _format_sse("message_end", end_payload)
        yield _format_sse("done", "[DONE]")

    except Exception as e:
        logger.error(f"执行失败: {e}", exc_info=True)
        yield _format_sse("error", {"error": str(e)})
        yield _format_sse("done", "[DONE]")


async def _continue_execution(
    thread_id: str,
    modified_sql: str | None = None,
    action: str = "approve",
    semantic_config: dict | None = None,
    request: Request | None = None,
    model_id: str | None = None,
    enable_thinking: bool | None = None,
    user_id: str | None = None,
):
    """
    继续执行被 interrupt() 暂停的图

    Args:
        thread_id: 会话ID
        modified_sql: 修改后的 SQL（如果用户编辑了）
        action: 用户操作 "approve" 或 "reject"
        semantic_config: 语义配置（语义模式时需要，保持工具列表一致）
    """
    logger.info(f"继续执行，thread_id: {thread_id}, action: {action}, semantic_mode: {semantic_config is not None}")

    config = {
        "configurable": {
            "thread_id": thread_id,
            "checkpoint_ns": ""
        }
    }
    token_usage = None

    try:
        async with get_checkpointer() as checkpointer:
            # 传入语义配置，保持工具列表与原始执行一致
            graph = get_chat_data_graph(
                checkpointer=checkpointer,
                semantic_config=semantic_config,
                model_id=model_id,
                enable_thinking=enable_thinking,
                user_id=user_id,
                available_skills_context=await _build_query_skills_context(user_id=user_id) if semantic_config else "",
            )

            # 构建恢复数据
            resume_data = {"action": action}
            if modified_sql is not None:
                resume_data["sql"] = modified_sql

            # 用于跟踪已发送 running 状态的工具
            running_tool_ids: list[str] = []
            active_tool_call_id: str | None = None
            # 用于累积 tool_call_chunks 的 args（JSON 字符串拼接）
            tool_call_args_buffer: dict[str, str] = {}
            # 用于存储工具名称
            tool_call_names: dict[str, str] = {}
            reasoning_segments: list[dict[str, Any]] = []
            persisted_reasoning_count = 0

            # 使用 Command(resume=...) 恢复执行
            # 通过 metadata["langgraph_node"] 过滤工具内部的 LLM 调用
            async for event in graph.astream(
                Command(resume=resume_data),
                config=config,
                stream_mode="messages"
            ):
                # 检测客户端断连
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

                message_chunk, metadata = event

                # 过滤：只保留来自 agent 节点的 AI 消息（排除 tools 节点内部的 LLM 调用）
                node_name = metadata.get("langgraph_node", "")
                msg_type = getattr(message_chunk, 'type', 'ai')
                if msg_type == 'ai' and node_name != "agent":
                    logger.debug(f"[过滤] 跳过来自 {node_name} 节点的 AI 消息")
                    continue

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
                        _visible_tool_count(tool_call_names),
                    )
                    yield thinking_event
                    if not has_content and not has_tool_calls and not has_tool_call_chunks:
                        continue

                # 根据消息类型选择事件名
                if msg_type == 'tool':
                    event_name = "tool"
                else:
                    event_name = "message"

                # 检测 tool_calls 开始，发送 running 状态，并初始化 args buffer
                if has_tool_calls:
                    for tc in message_chunk.tool_calls:
                        tc_id = tc.get("id")
                        tc_name = tc.get("name")
                        if tc_id and tc_name and tc_id not in running_tool_ids:
                            _register_running_tool(running_tool_ids, tc_id)
                            tool_call_names[tc_id] = tc_name
                            tool_call_args_buffer[tc_id] = ""
                            yield _format_tool_event(
                                tool_name=tc_name,
                                tool_call_id=tc_id,
                                status="running",
                            )

                # 累积 tool_call_chunks 的 args（JSON 字符串）
                # 注意：LangChain 流式模式下，只有第一个 chunk 有 id，后续 chunks 的 id 为 None
                if has_tool_call_chunks:
                    for chunk in message_chunk.tool_call_chunks:
                        chunk_id = chunk.get("id")
                        chunk_args = chunk.get("args", "")
                        chunk_name = chunk.get("name")

                        if chunk_id:
                            tool_call_names[chunk_id] = chunk_name or tool_call_names.get(chunk_id, "")
                            # 只有新的 tool_call_id 才初始化 buffer
                            if chunk_id not in tool_call_args_buffer:
                                tool_call_args_buffer[chunk_id] = ""
                            _register_running_tool(running_tool_ids, chunk_id)

                        current_tool_call_id = _resolve_active_tool_call_id(
                            chunk_id,
                            active_tool_call_id,
                            running_tool_ids,
                        )
                        if chunk_id:
                            active_tool_call_id = current_tool_call_id

                        if current_tool_call_id and chunk_args:
                            if current_tool_call_id not in tool_call_args_buffer:
                                tool_call_args_buffer[current_tool_call_id] = ""
                            tool_call_args_buffer[current_tool_call_id] += chunk_args

                # ToolMessage: 工具执行完成，发送带有完整 args 的消息
                if msg_type == 'tool':
                    tool_call_id = getattr(message_chunk, 'tool_call_id', '')
                    accumulated_args = {}
                    if tool_call_id and tool_call_id in tool_call_args_buffer:
                        args_str = tool_call_args_buffer[tool_call_id]
                        if args_str:
                            try:
                                accumulated_args = json.loads(args_str)
                            except json.JSONDecodeError:
                                logger.warning(f"无法解析工具参数: {args_str[:100]}")
                        del tool_call_args_buffer[tool_call_id]
                    active_tool_call_id = _finish_running_tool(running_tool_ids, active_tool_call_id, tool_call_id)

                    # 构造增强的消息，包含完整的 args 和时间戳
                    msg_data = message_chunk.model_dump()
                    msg_data["timestamp"] = _get_timestamp()
                    if accumulated_args:
                        msg_data["accumulated_args"] = accumulated_args
                        msg_data["tool_name"] = tool_call_names.get(tool_call_id, "")
                    yield _format_sse(event_name, msg_data)
                else:
                    # AI 消息添加时间戳
                    msg_data = message_chunk.model_dump()
                    msg_data["timestamp"] = _get_timestamp()
                    yield _format_sse(event_name, msg_data)

            # 检查是否又被中断（可能有多个工具需要确认）
            persisted_reasoning_count = await _persist_reasoning_segments(
                graph,
                config,
                reasoning_segments,
                as_node="agent",
                persisted_count=persisted_reasoning_count,
            )
            state = await graph.aget_state(config)
            token_usage = state.values.get("token_usage") if state and state.values else None
            interrupt_info = _get_interrupt_info(state)
            if interrupt_info:
                sql_preview = interrupt_info.get('args', {}).get('sql', '')[:50]
                logger.info(f"检测到新的工具中断: {sql_preview}...")
                yield _format_tool_event(
                    tool_name=interrupt_info.get("tool_name", "unknown_tool"),
                    tool_call_id=interrupt_info.get("tool_call_id"),
                    status="pending",
                    args=interrupt_info.get("args"),
                    message=interrupt_info.get("message"),
                )
                end_payload = _message_end_payload_from_state(state, token_usage)
                yield _format_sse("message_end", end_payload)
                yield _format_sse("done", "[DONE]")
                return

        end_payload = _message_end_payload_from_state(state, token_usage)
        yield _format_sse("message_end", end_payload)
        yield _format_sse("done", "[DONE]")

    except Exception as e:
        logger.error(f"继续执行失败: {e}", exc_info=True)
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
    sql_generator 的 completion 处理器
    """
    options_data = options or {}
    datasource_id = str(options_data.get("datasource_id") or "").strip() or None
    mode = options_data.get("mode", "generate")
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

    # 确保 thread 记录存在
    await conversation.get_or_create_conversation("", AGENT_ID, conversation_id, user_id)

    return StreamingResponse(
        _stream_sql_generation(
            thread_id=conversation_id,
            user_query=query,
            datasource_id=datasource_id,
            user_id=user_id,
            username=username,
            member_datasource_ids=member_datasource_ids,
            mode=mode,
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
            data_context = await build_data_analyze_context(datasource_id, semantic_config_id)
            log_phase("build_data_context")
            workspace_paths, data_context_file = await prepare_data_analyze_workspace(conversation_id, data_context)
            log_phase("ensure_workspace")
            try:
                available_skills = await skill_service.list_skills_async(user_id=user_id, scope="runtime")
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
                "task_stages": [],
                "task_run_id": str(uuid.uuid4()),
                "datasource_id": datasource_id,
                "semantic_config_id": semantic_config_id,
                "member_datasource_ids": member_datasource_ids or [],
                "user_id": user_id,
                "operator_name": username or "",
                "conversation_id": conversation_id,
            }

            async with get_checkpointer() as checkpointer:
                agent = data_analyze_graph.create_data_analyze_flow_graph(
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
                    enable_thinking=enable_thinking,
                )
                log_phase("create_agent")
                tool_call_names: dict[str, str] = {}
                tool_call_args_buffer: dict[str, str] = {}
                tool_call_args_fallback: dict[str, dict[str, Any]] = {}
                tool_call_index_map: dict[int, str] = {}
                emitted_running_tool_ids: set[str] = set()
                active_tool_call_id: str | None = None
                reasoning_segments: list[dict[str, Any]] = []
                persisted_reasoning_count = 0
                first_chunk_logged = False
                latest_token_usage: dict[str, Any] | None = None
                async for stream_item in agent.astream(initial_state, config=config, stream_mode=["messages", "updates", "custom"], subgraphs=True):
                    if len(stream_item) == 3:
                        _subgraph_path, stream_mode, payload = stream_item
                    else:
                        stream_mode, payload = stream_item
                    if stream_mode == "updates":
                        for node_payload in (payload or {}).values():
                            for stage in node_payload.get("task_stages") or []:
                                yield _format_sse("task_stage", stage)
                        continue
                    if stream_mode == "custom":
                        if isinstance(payload, dict) and payload.get("type") == "token_usage":
                            usage = payload.get("usage")
                            if usage:
                                latest_token_usage = usage
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
                            as_node="execute",
                            persisted_count=persisted_reasoning_count,
                        )
                        return
                    node_name = metadata.get("langgraph_node")
                    if node_name and node_name.startswith("__"):
                        continue
                    if node_name in {"thinking", "plan", "done"}:
                        stage_name = "thinking" if node_name == "thinking" else ("plan" if node_name == "plan" else "done")
                        content = getattr(message_chunk, "content", None)
                        if content:
                            yield _format_sse("task_stage_delta", {
                                "stage": stage_name,
                                "status": "running",
                                "content": content,
                            })
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
                            _visible_tool_count(tool_call_names),
                        )
                        yield thinking_event
                        if not has_content and not has_tool_calls and not has_tool_call_chunks and msg_type != "tool":
                            continue
                    if has_tool_calls:
                        for tc in getattr(message_chunk, "tool_calls", []) or []:
                            tc_id = str(tc.get("id") or "").strip()
                            tc_name = str(tc.get("name") or "").strip()
                            tc_index = tc.get("index")
                            if tc_id and tc_name:
                                tool_call_names[tc_id] = tc_name
                                active_tool_call_id = tc_id
                                if isinstance(tc_index, int):
                                    tool_call_index_map[tc_index] = tc_id
                                if tc_id not in tool_call_args_buffer:
                                    tool_call_args_buffer[tc_id] = ""
                                if isinstance(tc.get("args"), dict):
                                    tool_call_args_fallback[tc_id] = tc.get("args") or {}
                                if tc_id not in emitted_running_tool_ids:
                                    emitted_running_tool_ids.add(tc_id)
                                    yield _format_tool_event(
                                        tool_name=tc_name,
                                        tool_call_id=tc_id,
                                        status="running",
                                        args=tc.get("args") or {},
                                    )
                    if has_tool_call_chunks:
                        for tc in getattr(message_chunk, "tool_call_chunks", []) or []:
                            tc_id = str(tc.get("id") or "").strip()
                            tc_name = str(tc.get("name") or "").strip()
                            tc_args = str(tc.get("args") or "")
                            tc_index = tc.get("index")
                            if tc_id and tc_name:
                                tool_call_names[tc_id] = tc_name
                            if tc_id:
                                active_tool_call_id = tc_id
                                if isinstance(tc_index, int):
                                    tool_call_index_map[tc_index] = tc_id
                                if tc_id not in tool_call_args_buffer:
                                    tool_call_args_buffer[tc_id] = ""
                                running_tool_name = tc_name or tool_call_names.get(tc_id, "")
                                if running_tool_name and tc_id not in emitted_running_tool_ids:
                                    emitted_running_tool_ids.add(tc_id)
                                    yield _format_tool_event(
                                        tool_name=running_tool_name,
                                        tool_call_id=tc_id,
                                        status="running",
                                        args={},
                                    )
                            current_tool_call_id = tc_id or (tool_call_index_map.get(tc_index) if isinstance(tc_index, int) else None) or active_tool_call_id
                            if current_tool_call_id and tc_args:
                                tool_call_args_buffer[current_tool_call_id] = tool_call_args_buffer.get(current_tool_call_id, "") + tc_args
                    msg_data = message_chunk.model_dump()
                    msg_data["timestamp"] = _get_timestamp()
                    if msg_type == "tool":
                        tool_call_id = str(getattr(message_chunk, "tool_call_id", "") or "")
                        msg_data["tool_name"] = tool_call_names.get(tool_call_id, msg_data.get("name") or "")
                        msg_data["status"] = "error" if _is_tool_result_error(msg_data.get("content")) else "completed"
                        if tool_call_id and (tool_call_id in tool_call_args_buffer or tool_call_id in tool_call_args_fallback):
                            msg_data["accumulated_args"] = _parse_tool_args_buffer(
                                tool_call_args_buffer.get(tool_call_id, ""),
                                tool_call_args_fallback.get(tool_call_id),
                            )
                            tool_call_args_buffer.pop(tool_call_id, None)
                            tool_call_args_fallback.pop(tool_call_id, None)
                        if active_tool_call_id == tool_call_id:
                            active_tool_call_id = None
                        if msg_data.get("tool_name") == "select_semantic_config_tool":
                            selection_payload = _extract_select_semantic_config_payload(msg_data.get("content"))
                            if selection_payload:
                                if selection_payload.get("selected"):
                                    yield _format_sse("semantic_config_selected", _semantic_selection_event_from_tool_payload(selection_payload))
                                elif _should_emit_semantic_select_required(selection_payload):
                                    yield _format_sse("semantic_config_select_required", _semantic_select_required_event_from_tool_payload(selection_payload, query))
                        yield _format_sse("tool", msg_data)
                    else:
                        yield _format_sse("message", msg_data)
                log_phase("agent_stream_done")
                persisted_reasoning_count = await _persist_reasoning_segments(
                    agent,
                    config,
                    reasoning_segments,
                    as_node="execute",
                    persisted_count=persisted_reasoning_count,
                )
                state = await agent.aget_state(config) if hasattr(agent, "aget_state") else None
                end_payload = _message_end_payload_from_state(state, latest_token_usage)
                yield _format_sse("message_end", end_payload)
            yield _format_sse("done", "[DONE]")
        except Exception as e:
            logger.error("DataAnalyze 对话失败: conversation_id=%s error=%s", conversation_id, e, exc_info=True)
            yield _format_sse("task_stage", {
                "stage": "execute",
                "status": "error",
                "title": "任务执行",
                "content": f"任务执行失败：{str(e)}",
            })
            yield _format_sse("error", {"error": str(e)})
            yield _format_sse("done", "[DONE]")

    return StreamingResponse(
        generate_stream(),
        media_type="text/event-stream",
        headers={'Cache-Control': 'no-cache', 'Connection': 'keep-alive'}
    )


async def sql_resume_handler(
    conversation_id: str,
    action: str = "approve",
    data: Optional[Dict[str, Any]] = None
) -> StreamingResponse:
    """
    恢复被中断的 SQL 生成流程（Human-in-the-Loop 工具确认）

    符合公共 resume 接口签名:
        async def handler(conversation_id, action, data) -> StreamingResponse

    Args:
        conversation_id: 会话ID
        action: 用户操作 "approve" | "edit" | "reject"
        data: 修改后的数据，如：
            - {"sql": "SELECT ..."}（action=edit 时使用）
            - {"datasource_id": "...", "semantic_config_id": "..."}（语义模式时需要）
    """
    data = data or {}

    # 从 data 中提取参数
    modified_sql = data.get("sql")
    datasource_id = data.get("datasource_id")
    semantic_config_id = data.get("semantic_config_id")
    enable_thinking = _normalize_enable_thinking(data.get("enable_thinking"))
    user_id = str(data.get("user_id") or "system").strip() or "system"

    # 获取语义配置（如果有）
    semantic_config = await _get_semantic_config_async(datasource_id, semantic_config_id)

    # edit 和 approve 都会执行，只是 edit 时使用修改后的 SQL
    # 将 edit 转换为 approve（底层逻辑一致）
    effective_action = "approve" if action == "edit" else action

    return StreamingResponse(
        _continue_execution(
            thread_id=conversation_id,
            modified_sql=modified_sql,
            action=effective_action,
            semantic_config=semantic_config,
            enable_thinking=enable_thinking,
            user_id=user_id,
        ),
        media_type="text/event-stream",
        headers={'Cache-Control': 'no-cache', 'Connection': 'keep-alive'}
    )
