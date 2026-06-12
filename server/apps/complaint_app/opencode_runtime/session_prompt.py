from __future__ import annotations

import asyncio
import inspect
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, Iterable, List, Tuple

from apps.complaint_app.core.llm.llm_client import AsyncLLMClient, ExtraBody, extract_stream_deltas
from utils.logger import get_logger

from .session import Message, MessagePart, Session
from .agents.agents import AgentRegistry
from .tools.base_tool import Tool, ToolContext, ToolExecutor
from .tools.registry import ToolRegistry
from .trace_logger import trace_detail_logger, trace_enabled, trace_full_messages_enabled

logger = get_logger(__name__)


def _trace_enabled() -> bool:
    return trace_enabled()


def _trace_full_messages_enabled() -> bool:
    return trace_full_messages_enabled()


def _session_trace_logger(trace_context: dict[str, Any] | None = None):
    return trace_detail_logger(trace_context)


def _trace_tag(session: Session | None = None, trace_context: dict[str, Any] | None = None) -> str:
    """构造日志 trace 字段。"""
    context = {}
    if session is not None and isinstance(session.metadata, dict):
        context.update(session.metadata)
    if isinstance(trace_context, dict):
        context.update(trace_context)
    parts = [
        f"request_id={str(context.get('request_id') or '-')}",
        f"analysis_id={str(context.get('analysis_id') or '-')}",
        f"base_id={str(context.get('base_id') or '-')}",
        f"user_id={str(context.get('user_id') or '-')}",
        f"file_id={str(context.get('file_id') or '-')}",
    ]
    if session is not None and session.session_id:
        parts.append(f"session_id={session.session_id}")
    for key in ("stage", "skill_name", "match_strategy", "scope", "scope_path", "org_id", "entity_id", "reporter_name", "issue_analysis_version", "complaint_index", "complaint_id", "complaint_hash"):
        value = str(context.get(key) or "").strip()
        if value:
            parts.append(f"{key}={value}")
    return " ".join(parts)


@dataclass
class ToolCallResult:
    """工具调用结果记录。"""

    call_id: str
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    output: Any = None


@dataclass
class PromptResult:
    """一次 prompt 执行的结果。"""

    message: Message
    tool_results: List[ToolCallResult] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)


def _get_attr(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _normalize_schema(schema: Any) -> Dict[str, Any]:
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    if "type" in schema:
        return schema
    properties: Dict[str, Any] = {}
    for key, desc in schema.items():
        properties[str(key)] = {"type": "string", "description": str(desc)}
    return {"type": "object", "properties": properties}


def _tool_def(tool: Tool) -> Dict[str, Any]:
    schema = _normalize_schema(tool.metadata.get("parameters"))
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": schema,
        },
    }


def _stringify_tool_output(result: Any) -> str:
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as exc:
        logger.warning("[session_prompt] stringify_tool_output_failed type=%s err=%s", type(result).__name__, exc)
        return str(result)


def _messages_total_chars(messages: Iterable[Dict[str, Any]]) -> int:
    """估算发送给模型的消息字符量。"""
    total = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        total += len(str(message.get("content") or ""))
        tool_calls = message.get("tool_calls")
        if tool_calls:
            total += len(json.dumps(tool_calls, ensure_ascii=False, default=str))
    return total


def _compact_tool_args(args_raw: Any) -> Dict[str, Any]:
    if isinstance(args_raw, str) and args_raw:
        try:
            parsed = json.loads(args_raw)
        except Exception:
            return {"args_chars": len(args_raw)}
        args_raw = parsed
    if not isinstance(args_raw, dict):
        return {}
    compact: Dict[str, Any] = {}
    for key in ("filename", "sentence", "query", "source_text", "command"):
        if key in args_raw:
            value = str(args_raw.get(key) or "")
            compact[key] = value[:120] if key != "source_text" else f"chars={len(value)}"
    return compact


def _compact_tool_content(content: str) -> Dict[str, Any]:
    compact: Dict[str, Any] = {"content_chars": len(content or "")}
    try:
        parsed = json.loads(content or "{}")
    except Exception:
        return compact
    if not isinstance(parsed, dict):
        return compact
    metadata = parsed.get("metadata") if isinstance(parsed.get("metadata"), dict) else {}
    output = parsed.get("output")
    for key in ("ok", "filename", "error", "truncated", "root"):
        if key in metadata:
            compact[key] = metadata.get(key)
    if isinstance(output, str):
        compact["output_chars"] = len(output)
    return compact


def _tool_output_for_full_log(name: str, output_text: str, limit: int = 800) -> str:
    if name != "document_read" or len(output_text or "") <= limit:
        return output_text
    return json.dumps({"preview": output_text[:limit], "chars": len(output_text), "truncated": True}, ensure_ascii=False)


def _preview_text(value: Any, limit: int = 500) -> dict[str, Any]:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value or "")
    return {"preview": text[:limit], "chars": len(text), "truncated": len(text) > limit}


def _extract_message_reasoning(message: Any) -> str:
    for field in ("reasoning_content", "reasoning", "analysis", "thoughts", "think"):
        value = _get_attr(message, field, "")
        if value:
            return str(value)
    return ""


def _tool_call_content(name: str, status: str) -> str:
    if status == "start":
        if name == "search_law":
            return "使用四方模式技能进行举报事项分析"
        if name == "document_read":
            return "检索相关法律"
        return "调用工具"
    if name == "search_law":
        return "举报事项分析工具执行完成"
    if name == "document_read":
        return "检索相关法律工具执行完成"
    return "工具执行完成"


def _compact_tool_result(name: str, result: Any) -> dict[str, Any]:
    if name == "document_read" and isinstance(result, dict):
        metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
        output = str(result.get("output") or "")
        return {
            "title": str(result.get("title") or name),
            "filename": str(metadata.get("filename") or ""),
            "ok": bool(metadata.get("ok")) if "ok" in metadata else None,
            "truncated": bool(metadata.get("truncated")),
            "output_preview": output[:500],
            "output_chars": len(output),
        }
    if name == "search_law" and isinstance(result, dict):
        articles = result.get("articles") if isinstance(result.get("articles"), list) else []
        compact_articles = []
        for item in articles[:5]:
            if not isinstance(item, dict):
                continue
            compact_articles.append({
                "law_name": str(item.get("law_name") or ""),
                "article": str(item.get("article") or item.get("article_no") or ""),
                "content_preview": str(item.get("content") or item.get("article_text") or "")[:300],
                "relevance": str(item.get("relevance") or ""),
            })
        audit = result.get("audit") if isinstance(result.get("audit"), dict) else {}
        audit_tool_calls = audit.get("tool_calls") if isinstance(audit.get("tool_calls"), list) else []
        return {
            "query": str(result.get("query") or ""),
            "summary": str(result.get("summary") or ""),
            "article_count": len(articles),
            "articles": compact_articles,
            "audit": {
                "tool_rounds": int(audit.get("tool_rounds") or 0),
                "parallel_rounds": int(audit.get("parallel_rounds") or 0),
                "tool_call_count": len(audit_tool_calls),
            },
        }
    return _preview_text(result)


def _tool_call_parent_id(trace_context: dict[str, Any] | None) -> str:
    context = trace_context if isinstance(trace_context, dict) else {}
    return str(context.get("tool_run_id") or context.get("parent_tool_run_id") or "")


def _log_tool_call_event(*, trace_context: dict[str, Any] | None, status: str, name: str, call_id: str, reasoning: str = "", tool_calls: list[dict[str, Any]] | None = None, result: Any = None) -> None:
    payload: dict[str, Any] = {
        "phase": "举报事项分析",
        "type": "tool_call",
        "status": status,
        "tool_name": name,
        "tool_run_id": call_id,
        "parent_tool_run_id": _tool_call_parent_id(trace_context),
        "content": _tool_call_content(name, status),
        "metadata": {},
    }
    if status == "start":
        payload["metadata"] = {"reasoning": reasoning or "", "tool_calls": tool_calls or []}
    elif status == "done":
        payload["metadata"] = {"result": _compact_tool_result(name, result)}
    logger.info("[tool_call_sse] event=thinking data=%s", json.dumps(payload, ensure_ascii=False, default=str))


def _messages_compact_summary(messages: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """生成 messages 摘要，避免把长正文和工具输出刷进日志。"""
    summary: List[Dict[str, Any]] = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "")
        content = str(message.get("content") or "")
        item: Dict[str, Any] = {"index": index, "role": role, "content_chars": len(content)}
        if role == "tool":
            item["name"] = str(message.get("name") or "")
            item["tool_call_id"] = str(message.get("tool_call_id") or "")
            item["tool_result"] = _compact_tool_content(content)
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            compact_calls = []
            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                fn = call.get("function") if isinstance(call.get("function"), dict) else {}
                compact_calls.append(
                    {
                        "id": str(call.get("id") or ""),
                        "name": str(fn.get("name") or call.get("name") or ""),
                        "args": _compact_tool_args(fn.get("arguments")),
                    }
                )
            item["tool_calls"] = compact_calls
        summary.append(item)
    return summary


def _chat_timeout_seconds() -> float:
    """读取每轮非流式模型调用超时时间。"""
    raw = os.getenv("OPENCODE_PROMPT_CHAT_TIMEOUT_SECONDS", "90").strip()
    try:
        value = float(raw)
    except ValueError:
        value = 90.0
    return max(value, 1.0)


def _message_to_text(message: Message) -> str:
    parts: List[str] = []
    for part in message.parts:
        if message.role == "tool":
            if part.content:
                parts.append(part.content)
            continue
        if part.kind == "tool":
            header = f"[tool:{part.tool_name or ''}]".strip()
            if part.content:
                parts.append(f"{header}\n{part.content}" if header else part.content)
            else:
                parts.append(header)
        else:
            if part.content:
                parts.append(part.content)
    return "\n".join([p for p in parts if p])


def _build_messages(session: Session, *, system_prompt: str | None, user_input: str | None) -> List[Dict[str, Any]]:
    messages: List[Dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    awaiting_tool = False
    for msg in session.messages:
        content = _message_to_text(msg)
        if msg.role == "assistant":
            tool_calls = {}
            if isinstance(msg.metadata, dict):
                tool_calls = msg.metadata.get("tool_calls") or []
            if not content and not tool_calls:
                continue
            entry = {"role": "assistant", "content": content or ""}
            if tool_calls:
                entry["tool_calls"] = tool_calls
            messages.append(entry)
            awaiting_tool = bool(tool_calls)
            continue
        if msg.role == "tool":
            if not awaiting_tool:
                continue
            tool_call_id = ""
            tool_name = ""
            if isinstance(msg.metadata, dict):
                tool_call_id = str(msg.metadata.get("tool_call_id") or "")
                tool_name = str(msg.metadata.get("tool") or "")
            if not tool_call_id and msg.parts:
                for part in msg.parts:
                    tool_call_id = tool_call_id or str(getattr(part, "tool_call_id", "") or "")
                    tool_name = tool_name or str(getattr(part, "tool_name", "") or "")
            entry = {"role": "tool", "content": content or ""}
            if tool_call_id:
                entry["tool_call_id"] = tool_call_id
            if tool_name:
                entry["name"] = tool_name
            messages.append(entry)
            continue
        if not content:
            continue
        messages.append({"role": msg.role, "content": content})
    if user_input:
        messages.append({"role": "user", "content": user_input})
    return messages


def _parse_tool_calls(message: Any) -> List[Dict[str, Any]]:
    tool_calls = _get_attr(message, "tool_calls", None)
    if tool_calls:
        return [tc if isinstance(tc, dict) else _get_attr(tc, "__dict__", tc) for tc in tool_calls]
    function_call = _get_attr(message, "function_call", None)
    if function_call:
        call = function_call if isinstance(function_call, dict) else _get_attr(function_call, "__dict__", function_call)
        return [{"id": "", "type": "function", "function": call}]
    return []


def _build_tool_call_message(content: str, tool_calls: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "role": "assistant",
        "content": content or "",
        "tool_calls": tool_calls,
    }


def _build_tool_messages(tool_calls: List[Dict[str, Any]], tool_results: List[ToolCallResult]) -> List[Dict[str, Any]]:
    results_by_id = {item.call_id: item for item in tool_results}
    results_by_name = {item.name: item for item in tool_results}
    messages: List[Dict[str, Any]] = []
    for call in tool_calls:
        fn = call.get("function") or {}
        name = fn.get("name") or call.get("name")
        call_id = call.get("id") or ""
        result = results_by_id.get(call_id) or results_by_name.get(name or "")
        content = _stringify_tool_output(result.output) if result else ""
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call_id,
                "name": name,
                "content": content,
            }
        )
    return messages


def _parse_tool_call_items(tool_calls: List[Dict[str, Any]], *, trace_tag: str, round_no: int, log_prefix: str = "") -> List[Tuple[int, str, Dict[str, Any], str]]:
    parsed_calls: List[Tuple[int, str, Dict[str, Any], str]] = []
    for index, call in enumerate(tool_calls):
        if not isinstance(call, dict):
            call = _get_attr(call, "__dict__", call)
        if not isinstance(call, dict):
            continue
        fn = call.get("function") or {}
        if not isinstance(fn, dict):
            fn = _get_attr(fn, "__dict__", fn) or {}
        name = fn.get("name") if isinstance(fn, dict) else ""
        if not name:
            name = call.get("name") if isinstance(call, dict) else ""
        args_raw = fn.get("arguments") if isinstance(fn, dict) else _get_attr(fn, "arguments", None)
        call_id = call.get("id") or ""
        args: Dict[str, Any] = {}
        if isinstance(args_raw, str) and args_raw:
            try:
                args = json.loads(args_raw)
            except Exception as exc:
                logger.warning("[session_prompt] %s %sparse_tool_args_failed round=%s name=%s call_id=%s err=%s args=%s", trace_tag, log_prefix, round_no, name, call_id, exc, args_raw[:300])
                args = {}
        if name:
            parsed_calls.append((index, name, args, call_id))
    return parsed_calls


def _append_user_message(session: Session, user_input: str) -> Message:
    msg = Message(
        message_id=str(uuid.uuid4()),
        role="user",
        parts=[MessagePart(kind="text", content=user_input)],
    )
    session.add_message(msg)
    return msg


def _append_tool_message(session: Session, *, name: str, output: Any, call_id: str) -> Message:
    msg = Message(
        message_id=str(uuid.uuid4()),
        role="tool",
        parts=[
            MessagePart(
                kind="tool",
                content=_stringify_tool_output(output),
                tool_name=name,
                tool_call_id=call_id or None,
            )
        ],
        metadata={"tool": name, "tool_call_id": call_id},
    )
    session.add_message(msg)
    return msg


def _append_assistant_message(session: Session, content: str, tool_calls: List[Dict[str, Any]] | None = None) -> Message:
    msg = Message(
        message_id=str(uuid.uuid4()),
        role="assistant",
        parts=[MessagePart(kind="text", content=content or "")],
        metadata={"tool_calls": tool_calls} if tool_calls else {},
    )
    session.add_message(msg)
    return msg


def _extract_stream_tool_calls(delta: Any, accumulator: Dict[int, Dict[str, Any]]) -> None:
    tool_calls = _get_attr(delta, "tool_calls", None)
    if not tool_calls:
        return
    for item in tool_calls:
        call = item if isinstance(item, dict) else _get_attr(item, "__dict__", item)
        idx = int(call.get("index") or 0)
        entry = accumulator.get(idx)
        if not entry:
            entry = {"id": call.get("id") or "", "type": call.get("type") or "function", "function": {"name": "", "arguments": ""}}
            accumulator[idx] = entry
        if call.get("id"):
            entry["id"] = call.get("id")
        fn = call.get("function") or {}
        name = fn.get("name") if isinstance(fn, dict) else _get_attr(fn, "name", "")
        if name:
            entry["function"]["name"] = name
        args = fn.get("arguments") if isinstance(fn, dict) else _get_attr(fn, "arguments", "")
        if args:
            entry["function"]["arguments"] = (entry["function"].get("arguments") or "") + str(args)


def _finalize_tool_calls(accumulator: Dict[int, Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [accumulator[key] for key in sorted(accumulator.keys())]


class SessionPrompt:
    """最小版 SessionPrompt：组装 messages、调用 LLM、执行工具。"""

    def __init__(self, *, llm_client: AsyncLLMClient | None = None, tool_executor: ToolExecutor | None = None) -> None:
        self._llm = llm_client or AsyncLLMClient()
        self._executor = tool_executor or ToolExecutor()
        if tool_executor is None:
            for tool in ToolRegistry.registry():
                self._executor.register(tool)

    def _resolve_tools(self, agent_name: str | None) -> List[Tool]:
        """根据 agent 类型筛选可用工具（子 agent 默认不暴露工具）。"""
        tools = list(self._executor.list())
        if agent_name:
            agent = AgentRegistry.get(agent_name)
            if agent and agent.allowed_tools is not None:
                allowed = {name for name in agent.allowed_tools if name}
                return [tool for tool in tools if tool.name in allowed]
        return tools

    def _is_parallel_safe_tool(self, name: str) -> bool:
        """只允许显式声明并发安全的工具并发执行。"""
        if name == "shell":
            return True
        tool = self._executor.get(name)
        return bool(tool and isinstance(tool.metadata, dict) and tool.metadata.get("parallel_safe") is True)

    async def _execute_tool_call(self, *, session: Session, agent_name: str | None, trace_context: dict[str, Any] | None, index: int, name: str, args: Dict[str, Any], call_id: str) -> Tuple[int, str, str, Dict[str, Any], Any]:
        metadata = dict(trace_context or {})
        metadata["parent_tool_run_id"] = _tool_call_parent_id(trace_context)
        metadata["tool_run_id"] = call_id or ""
        context = ToolContext(session_id=session.session_id, message_id=call_id or "tool", agent_name=agent_name, metadata=metadata)
        tool = self._executor.get(name)
        if tool and not inspect.iscoroutinefunction(tool._executor):
            result = await asyncio.to_thread(self._executor.execute, name, context=context, payload=args)
        else:
            result = self._executor.execute(name, context=context, payload=args)
            if hasattr(result, "__await__"):
                result = await result
        return (index, call_id, name, args, result)

    async def _execute_tool_calls(self, *, parsed_calls: List[Tuple[int, str, Dict[str, Any], str]], session: Session, agent_name: str | None, trace_context: dict[str, Any] | None, trace_tag: str, round_no: int, log_prefix: str = "") -> tuple[list[Tuple[int, str, str, Dict[str, Any], Any]], bool]:
        if not parsed_calls:
            return [], False
        can_parallel = len(parsed_calls) > 1 and all(self._is_parallel_safe_tool(name) for _, name, _, _ in parsed_calls)
        if not can_parallel:
            executed = []
            for index, name, args, call_id in parsed_calls:
                executed.append(await self._execute_tool_call(session=session, agent_name=agent_name, trace_context=trace_context, index=index, name=name, args=args, call_id=call_id))
            return executed, False
        max_parallel = int(os.getenv("OPENCODE_MAX_PARALLEL_TOOLS", "3") or "3")
        max_parallel = max(1, max_parallel)
        semaphore = asyncio.Semaphore(max_parallel)
        if _trace_enabled():
            _session_trace_logger(trace_context).info("[session_prompt] %s %sparallel_tool_execution round=%s call_count=%s max_parallel=%s", trace_tag, log_prefix, round_no, len(parsed_calls), max_parallel)

        async def _run_with_semaphore(index: int, name: str, args: Dict[str, Any], call_id: str) -> Tuple[int, str, str, Dict[str, Any], Any]:
            async with semaphore:
                started_at = time.time()
                if _trace_enabled():
                    _session_trace_logger(trace_context).info("[session_prompt] %s %stool_call_start round=%s index=%s name=%s call_id=%s", trace_tag, log_prefix, round_no, index, name, call_id)
                result = await self._execute_tool_call(session=session, agent_name=agent_name, trace_context=trace_context, index=index, name=name, args=args, call_id=call_id)
                if _trace_enabled():
                    _session_trace_logger(trace_context).info("[session_prompt] %s %stool_call_done round=%s index=%s name=%s call_id=%s elapsed=%.3fs", trace_tag, log_prefix, round_no, index, name, call_id, time.time() - started_at)
                return result

        tasks = [asyncio.create_task(_run_with_semaphore(index, name, args, call_id)) for index, name, args, call_id in parsed_calls]
        executed = await asyncio.gather(*tasks)
        executed.sort(key=lambda item: item[0])
        return executed, True

    async def prompt(self, *, session: Session, user_input: str, system_prompt: str | None = None, model: str | None = None, agent_name: str | None = None, allowed_tools: list[str] | None = None, max_tool_rounds: int = 1, record_messages: bool = True, trace_context: dict[str, Any] | None = None, extra_body: ExtraBody | None = None, ) -> PromptResult:
        """执行一次 prompt，支持单轮工具调用。"""
        trace_tag = _trace_tag(session, trace_context)
        tools = self._resolve_tools(agent_name)
        if allowed_tools is not None:
            allowed = {name for name in allowed_tools if name}
            tools = [tool for tool in tools if tool.name in allowed]
        tool_defs = [_tool_def(tool) for tool in tools]
        tools_param = tool_defs if tool_defs else None
        if _trace_enabled():
            _session_trace_logger(trace_context).info("[session_prompt] %s tools=%s", trace_tag, [tool.name for tool in tools])
        if record_messages:
            _append_user_message(session, user_input)
            messages = _build_messages(session, system_prompt=system_prompt, user_input=None)
        else:
            messages = _build_messages(session, system_prompt=system_prompt, user_input=user_input)
        if _trace_enabled():
            _session_trace_logger(trace_context).info("[session_prompt] %s request_messages_compact=%s", trace_tag, json.dumps(_messages_compact_summary(messages), ensure_ascii=False))
            if _trace_full_messages_enabled():
                _session_trace_logger(trace_context).info("[session_prompt] %s request_messages=%s", trace_tag, json.dumps(messages, ensure_ascii=False))
        tool_results: List[ToolCallResult] = []
        content = ""
        tool_calls: List[Dict[str, Any]] = []
        rounds = 0
        parallel_rounds = 0

        while True:
            round_no = rounds + 1
            timeout_seconds = _chat_timeout_seconds()
            started_at = time.time()
            if _trace_enabled():
                _session_trace_logger(trace_context).info("[session_prompt] %s llm_request_start round=%s messages_count=%s approx_chars=%s timeout_seconds=%s messages=%s", trace_tag, round_no, len(messages), _messages_total_chars(messages), timeout_seconds, json.dumps(_messages_compact_summary(messages), ensure_ascii=False))
            try:
                response = await asyncio.wait_for(self._llm.chat(messages=messages, model=model, tools=tools_param, extra_body=extra_body), timeout=timeout_seconds)
            except asyncio.TimeoutError as exc:
                logger.error("[session_prompt] %s llm_request_timeout round=%s messages_count=%s approx_chars=%s timeout_seconds=%s", trace_tag, round_no, len(messages), _messages_total_chars(messages), timeout_seconds)
                raise TimeoutError(f"session prompt llm request timeout after {timeout_seconds:.0f}s at round {round_no}") from exc
            ended_at = time.time()
            if _trace_enabled():
                _session_trace_logger(trace_context).info("[session_prompt] %s llm_request_done round=%s elapsed=%.3fs", trace_tag, round_no, ended_at - started_at)
            choices = _get_attr(response, "choices", [])
            if not choices:
                assistant = Message(message_id=str(uuid.uuid4()), role="assistant", parts=[])
                return PromptResult(message=assistant, stats={"tool_rounds": rounds, "parallel_rounds": parallel_rounds, "shell_call_count": 0})

            choice = choices[0]
            msg = _get_attr(choice, "message", {})
            content = _get_attr(msg, "content", "") or ""
            reasoning = _extract_message_reasoning(msg)
            tool_calls = _parse_tool_calls(msg)
            if not tools:
                tool_calls = []
            if _trace_enabled():
                round_no = rounds + 1
                _session_trace_logger(trace_context).info("[session_prompt] %s response round=%s input_chars=%s output_chars=%s tool_call_count=%s tool_calls_chars=%s start_time=%s end_time=%s duration_ms=%s content=%s tool_calls=%s", trace_tag, round_no, _messages_total_chars(messages), len(content), len(tool_calls), len(json.dumps(tool_calls, ensure_ascii=False, default=str)), time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started_at)), time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ended_at)), int((ended_at - started_at) * 1000), content, json.dumps(tool_calls, ensure_ascii=False, default=str))

            if not tool_calls:
                break

            assistant_tool_msg = _build_tool_call_message(content, tool_calls)
            if record_messages:
                _append_assistant_message(session, content, tool_calls=tool_calls)
            tool_messages: List[Dict[str, Any]] = []
            parsed_calls = _parse_tool_call_items(tool_calls, trace_tag=trace_tag, round_no=rounds + 1)
            for _, name, _args, call_id in parsed_calls:
                _log_tool_call_event(trace_context=trace_context, status="start", name=name, call_id=call_id, reasoning=reasoning, tool_calls=tool_calls)

            if _trace_enabled():
                round_no = rounds + 1
                for index, name, args, call_id in parsed_calls:
                    command = ""
                    if name == "shell" and isinstance(args, dict):
                        command = str(args.get("command") or "")
                    _session_trace_logger(trace_context).info("[session_prompt] %s tool_call round=%s index=%s name=%s call_id=%s args=%s command=%s", trace_tag, round_no, index, name, call_id, json.dumps(args if isinstance(args, dict) else {}, ensure_ascii=False, default=str), command)

            executed, used_parallel = await self._execute_tool_calls(parsed_calls=parsed_calls, session=session, agent_name=agent_name, trace_context=trace_context, trace_tag=trace_tag, round_no=rounds + 1)
            if used_parallel:
                parallel_rounds += 1
            for _, call_id, name, args, result in executed:
                tool_results.append(ToolCallResult(call_id=call_id, name=name, arguments=args, output=result))
                _log_tool_call_event(trace_context=trace_context, status="done", name=name, call_id=call_id, result=result)
                tool_messages.append({"role": "tool", "tool_call_id": call_id, "name": name, "content": _stringify_tool_output(result), })
                if record_messages:
                    _append_tool_message(session, name=name, output=result, call_id=call_id)
                if _trace_enabled():
                    round_no = rounds + 1
                    output_for_log = _stringify_tool_output(result)
                    _session_trace_logger(trace_context).info("[session_prompt] %s tool_result round=%s name=%s call_id=%s output_compact=%s", trace_tag, round_no, name, call_id, json.dumps(_compact_tool_content(output_for_log), ensure_ascii=False))
                    if _trace_full_messages_enabled():
                        _session_trace_logger(trace_context).info("[session_prompt] %s tool_result_full round=%s name=%s call_id=%s output=%s", trace_tag, round_no, name, call_id, _tool_output_for_full_log(name, output_for_log))

            messages = messages + [assistant_tool_msg] + tool_messages
            rounds += 1
            if rounds >= max(1, max_tool_rounds):
                if _trace_enabled():
                    _session_trace_logger(trace_context).info("[session_prompt] %s tool_round_limit_reached rounds=%s max_tool_rounds=%s", trace_tag, rounds, max(1, max_tool_rounds))
                break

        if record_messages:
            assistant = _append_assistant_message(session, content, tool_calls=tool_calls if tool_calls else None)
        else:
            assistant = Message(
                message_id=str(uuid.uuid4()),
                role="assistant",
                parts=[MessagePart(kind="text", content=content)],
            )
        if _trace_enabled():
            _session_trace_logger(trace_context).info("[session_prompt] %s final_assistant_message content=%s", trace_tag, content)
        shell_call_count = len([item for item in tool_results if item.name == "shell"])
        return PromptResult(message=assistant, tool_results=tool_results, stats={"tool_rounds": rounds, "parallel_rounds": parallel_rounds, "shell_call_count": shell_call_count})

    async def stream(self, *, session: Session, user_input: str, system_prompt: str | None = None, model: str | None = None, agent_name: str | None = None, allowed_tools: list[str] | None = None, tool_choice: Any | None = None, record_messages: bool = True, trace_context: dict[str, Any] | None = None, extra_body: ExtraBody | None = None, ) -> AsyncGenerator[Dict[str, Any], None]:
        """流式执行 prompt（文本 delta + 最终结果），支持单轮工具调用。"""
        trace_tag = _trace_tag(session, trace_context)
        tools = self._resolve_tools(agent_name)
        if allowed_tools is not None:
            allowed = {name for name in allowed_tools if name}
            tools = [tool for tool in tools if tool.name in allowed]
        tool_defs = [_tool_def(tool) for tool in tools]
        tools_param = tool_defs if tool_defs else None
        if _trace_enabled():
            _session_trace_logger(trace_context).info("[session_prompt] %s stream tools=%s", trace_tag, [tool.name for tool in tools])
        if record_messages:
            _append_user_message(session, user_input)
            messages = _build_messages(session, system_prompt=system_prompt, user_input=None)
        else:
            messages = _build_messages(session, system_prompt=system_prompt, user_input=user_input)
        if _trace_enabled():
            _session_trace_logger(trace_context).info("[session_prompt] %s stream request_messages_compact=%s", trace_tag, json.dumps(_messages_compact_summary(messages), ensure_ascii=False))
            if _trace_full_messages_enabled():
                _session_trace_logger(trace_context).info("[session_prompt] %s stream request_messages=%s", trace_tag, json.dumps(messages, ensure_ascii=False))

        content_chunks: List[str] = []
        reasoning_chunks: List[str] = []
        tool_acc: Dict[int, Dict[str, Any]] = {}

        async for chunk in self._llm.stream_chat(messages=messages, model=model, tools=tools_param, tool_choice=tool_choice, extra_body=extra_body):
            choices = _get_attr(chunk, "choices", [])
            if not choices:
                continue
            delta = _get_attr(choices[0], "delta", None)
            if not delta:
                continue
            text_delta, reasoning_delta, reasoning_field = extract_stream_deltas(delta)
            if reasoning_delta:
                reasoning_chunks.append(reasoning_delta)
                if _trace_enabled():
                    _session_trace_logger(trace_context).info("[session_prompt] %s stream reasoning_delta field=%s reasoning_content=%s", trace_tag, reasoning_field, reasoning_delta)
            if text_delta:
                content_chunks.append(text_delta)
                if _trace_enabled():
                    _session_trace_logger(trace_context).info("[session_prompt] %s stream content_delta content=%s", trace_tag, text_delta)
                if tool_choice is None:
                    yield {"event": "content_delta", "delta": text_delta}
            _extract_stream_tool_calls(delta, tool_acc)

        content = "".join(content_chunks)
        tool_calls = _finalize_tool_calls(tool_acc)
        if not tools:
            tool_calls = []
        if _trace_enabled():
            _session_trace_logger(trace_context).info("[session_prompt] %s stream round=1 done content_chars=%s reasoning_chars=%s tool_call_count=%s tool_calls=%s tools_enabled=%s tools=%s", trace_tag, len(content), len("".join(reasoning_chunks)), len(tool_calls), json.dumps(tool_calls, ensure_ascii=False, default=str), bool(tools_param), [tool.name for tool in tools])
        tool_results: List[ToolCallResult] = []

        if tool_calls:
            assistant_tool_msg = _build_tool_call_message(content, tool_calls)
            if record_messages:
                _append_assistant_message(session, content, tool_calls=tool_calls)
            parsed_calls = _parse_tool_call_items(tool_calls, trace_tag=trace_tag, round_no=1, log_prefix="stream_")
            reasoning = "".join(reasoning_chunks)
            for _, name, _args, call_id in parsed_calls:
                _log_tool_call_event(trace_context=trace_context, status="start", name=name, call_id=call_id, reasoning=reasoning, tool_calls=tool_calls)
            executed, _used_parallel = await self._execute_tool_calls(parsed_calls=parsed_calls, session=session, agent_name=agent_name, trace_context=trace_context, trace_tag=trace_tag, round_no=1, log_prefix="stream_")
            for _, call_id, name, args, result in executed:
                tool_results.append(ToolCallResult(call_id=call_id, name=name, arguments=args, output=result))
                _log_tool_call_event(trace_context=trace_context, status="done", name=name, call_id=call_id, result=result)
                if record_messages:
                    _append_tool_message(session, name=name, output=result, call_id=call_id)
                if _trace_enabled():
                    output_for_log = _stringify_tool_output(result)
                    _session_trace_logger(trace_context).info("[session_prompt] %s stream tool_result name=%s call_id=%s output_compact=%s", trace_tag, name, call_id, json.dumps(_compact_tool_content(output_for_log), ensure_ascii=False))
                    if _trace_full_messages_enabled():
                        _session_trace_logger(trace_context).info("[session_prompt] %s stream tool_result_full name=%s call_id=%s output=%s", trace_tag, name, call_id, _tool_output_for_full_log(name, output_for_log))

            tool_messages = _build_tool_messages(tool_calls, tool_results)
            follow_messages = messages + [assistant_tool_msg] + tool_messages
            if _trace_enabled():
                _session_trace_logger(trace_context).info("[session_prompt] %s stream follow_messages_compact=%s", trace_tag, json.dumps(_messages_compact_summary(follow_messages), ensure_ascii=False))
                if _trace_full_messages_enabled():
                    _session_trace_logger(trace_context).info("[session_prompt] %s stream follow_messages=%s", trace_tag, json.dumps(follow_messages, ensure_ascii=False))
            follow_chunks: List[str] = []
            follow_reasoning_chunks: List[str] = []
            follow_tool_acc: Dict[int, Dict[str, Any]] = {}
            async for chunk in self._llm.stream_chat(messages=follow_messages, model=model, tools=tools_param, extra_body=extra_body):
                choices = _get_attr(chunk, "choices", [])
                if not choices:
                    continue
                delta = _get_attr(choices[0], "delta", None)
                if not delta:
                    continue
                text_delta, reasoning_delta, reasoning_field = extract_stream_deltas(delta)
                if reasoning_delta:
                    follow_reasoning_chunks.append(reasoning_delta)
                    if _trace_enabled():
                        _session_trace_logger(trace_context).info("[session_prompt] %s stream follow_reasoning_delta field=%s reasoning_content=%s", trace_tag, reasoning_field, reasoning_delta)
                if text_delta:
                    follow_chunks.append(text_delta)
                    if _trace_enabled():
                        _session_trace_logger(trace_context).info("[session_prompt] %s stream follow_content_delta content=%s", trace_tag, text_delta)
                    yield {"event": "content_delta", "delta": text_delta}
                _extract_stream_tool_calls(delta, follow_tool_acc)
            content = "".join(follow_chunks)
            follow_tool_calls = _finalize_tool_calls(follow_tool_acc)
            if _trace_enabled():
                _session_trace_logger(trace_context).info("[session_prompt] %s stream round=2 done content_chars=%s reasoning_chars=%s tool_call_count=%s tool_calls=%s tools_enabled=%s content=%s", trace_tag, len(content), len("".join(follow_reasoning_chunks)), len(follow_tool_calls), json.dumps(follow_tool_calls, ensure_ascii=False, default=str), bool(tools_param), content)

        if record_messages:
            assistant = _append_assistant_message(session, content, tool_calls=tool_calls if tool_calls else None)
        else:
            assistant = Message(message_id=str(uuid.uuid4()), role="assistant", parts=[MessagePart(kind="text", content=content)], )
        if _trace_enabled():
            _session_trace_logger(trace_context).info("[session_prompt] %s stream final_assistant_message content=%s", trace_tag, content)

        yield {"event": "message_end", "message": assistant.to_dict(), "tool_results": [item.__dict__ for item in tool_results], }


__all__ = ["SessionPrompt", "PromptResult", "ToolCallResult"]
