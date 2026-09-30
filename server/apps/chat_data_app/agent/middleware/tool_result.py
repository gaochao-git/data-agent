"""Single result-budget boundary for every ChatData tool call."""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest, wrap_tool_call
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from apps.chat_data_app.agent.workspace.runtime import workspace_runtime_from_state
from apps.chat_data_app.services.runtime_config import TOOL_RESULT_OFFLOAD_SIZE
from apps.chat_data_app.services.sql_run.result import query_result_status, query_result_succeeded
from utils.logger import get_logger
from utils.tool_result_store import QUERY_RESULT_DIR_NAME, TOOL_OFFLOAD_DIR_NAME, ToolResultRecord

from ..tool_results import (
    build_query_result_response,
    build_tool_result_recovery,
    merge_tool_result_recovery_artifact,
    parse_internal_tool_result,
)

logger = get_logger(__name__)

_READ_PAGINATED_TOOLS = frozenset({"file_read"})


def create_tool_result_middleware() -> AgentMiddleware:
    """Create the sole boundary that turns raw tool output into model output."""

    @wrap_tool_call(name="ToolResultMiddleware")
    async def tool_result_middleware(
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        result = await handler(request)
        if not isinstance(result, ToolMessage) or result.status == "error":
            return result
        return await _finalize_tool_message(result, request)

    return tool_result_middleware


async def _finalize_tool_message(
    message: ToolMessage,
    request: Any,
) -> ToolMessage:
    tool_name = str(message.name or "").strip()
    internal = parse_internal_tool_result(message.content)
    if internal is not None:
        content, recovery = await _finalize_query_result(internal, request)
        return _replace_tool_message(message, content=content, recovery=recovery)
    if tool_name in _READ_PAGINATED_TOOLS:
        recovery = _read_tool_recovery(tool_name, request, message.content)
        return _replace_tool_message(message, recovery=recovery) if recovery else message
    text = _tool_message_text(message.content)
    if text is None:
        return message
    if len(text) <= TOOL_RESULT_OFFLOAD_SIZE:
        return message
    externalized, recovery = await _externalize_generic_text(text, tool_name, request)
    return _replace_tool_message(message, content=externalized, recovery=recovery)


async def _finalize_query_result(
    internal: dict[str, Any],
    request: Any,
) -> tuple[str, dict[str, Any] | None]:
    query_result = dict(internal["payload"])
    meta = dict(internal["meta"])
    status = query_result_status(query_result)
    public_meta: dict[str, Any] = {
        "query_mode": str(meta.get("query_mode") or ""),
    }
    public_meta = {key: value for key, value in public_meta.items() if value is not None and value != ""}
    if not query_result_succeeded(query_result):
        public_meta["sql"] = query_result.get("sql") or ""
        return build_query_result_response(
            status=status,
            error=str(query_result.get("error") or "未知错误"),
            meta=public_meta,
        ), None

    serialized_result = json.dumps(query_result, ensure_ascii=False, indent=2)
    result_size_chars = len(serialized_result)
    try:
        record = await _write_private_tool_result(
            content=serialized_result,
            suffix=".json",
            directory_name=QUERY_RESULT_DIR_NAME,
            request=request,
        )
    except Exception as exc:
        logger.error("保存查询工具结果恢复副本失败: %s", exc, exc_info=True)
        public_meta["result_persisted"] = False
        return build_query_result_response(
            status="failed",
            error="查询已执行，但结果保存失败，当前无法展示或继续分析",
            meta=public_meta,
        ), None

    recovery = build_tool_result_recovery(
        source_tool="data_analyzing_tool",
        result_path=record.virtual_path,
        result_size_chars=result_size_chars,
        result_id=record.result_id,
    )

    public_meta.update(
        {
            "result_id": record.result_id,
            "result_path": record.virtual_path,
            "total_count": query_result.get("total_count", 0),
            "execution_time_ms": query_result.get("execution_time_ms"),
            "result_size_chars": result_size_chars,
            "content_in_context": False,
            "message": "查询已完成，完整结果已保存；前端会自动加载结果。",
        }
    )
    if query_result.get("sql"):
        public_meta["sql"] = query_result["sql"]

    logger.info(
        "查询工具结果已保存: result_id=%s size_chars=%s rows=%s",
        record.result_id,
        result_size_chars,
        query_result.get("total_count", 0),
    )
    return build_query_result_response(status=status, data=None, meta=public_meta), recovery


async def _externalize_generic_text(
    content: str,
    tool_name: str,
    request: Any,
) -> tuple[str, dict[str, Any] | None]:
    try:
        record = await _write_private_tool_result(
            content=content,
            suffix=".txt",
            request=request,
        )
    except Exception as exc:
        logger.error("卸载工具结果失败: tool=%s error=%s", tool_name, exc, exc_info=True)
        return _build_truncated_tool_result_message(tool_name, len(content)), None
    recovery = build_tool_result_recovery(
        source_tool=tool_name,
        result_path=record.virtual_path,
        result_size_chars=len(content),
        result_id=record.result_id,
    )
    return _build_offloaded_tool_result_message(
        tool_name=tool_name,
        result_path=record.virtual_path,
        result_size_chars=len(content),
        preview=_clip_text_to_chars(content, 512),
    ), recovery


def _build_offloaded_tool_result_message(
    *,
    tool_name: str,
    result_path: str,
    result_size_chars: int,
    preview: str | None,
) -> str:
    """Return offload facts only; the system prompt owns their disposition."""
    lines = [
        "[tool_result_offloaded]",
        f"source_tool={tool_name}",
        f"result_path={result_path}",
        "content_in_context=false",
        f"result_size_chars={result_size_chars}",
    ]
    if preview:
        lines.extend(["", "preview:", preview])
    return "\n".join(lines)


def _build_truncated_tool_result_message(tool_name: str, result_size_chars: int) -> str:
    """Return facts only when an oversized result cannot be persisted."""
    required_lines = [
        "[tool_result_truncated]",
        "content_complete=false",
    ]
    required_content = "\n".join(required_lines)
    message_limit = max(TOOL_RESULT_OFFLOAD_SIZE, len(required_content))
    optional_lines = [
        f"source_tool={tool_name or '-'}",
        f"original_size_chars={max(int(result_size_chars), 0)}",
    ]
    lines = list(required_lines)
    for line in optional_lines:
        candidate = "\n".join([*lines, line])
        if len(candidate) <= message_limit:
            lines.append(line)
    return "\n".join(lines)


async def _write_private_tool_result(
    *,
    content: str,
    suffix: str,
    directory_name: str = TOOL_OFFLOAD_DIR_NAME,
    request: Any,
) -> ToolResultRecord:
    """Persist one hidden result through the active conversation backend.

    Sandbox runs must write the remote workspace first so subsequent tools read
    the same filesystem view. Agent requests without a workspace are invalid;
    allowing a direct local-store fallback would reintroduce a second write path.
    """
    conversation_id = _required_state_value(request, "conversation_id")
    user_id = _required_state_value(request, "user_id")
    workspace = _request_workspace(request)
    workspace_root = str(workspace.get("workspace_root") or "").strip()
    if not workspace_root:
        raise ValueError("当前请求缺少会话工作区，无法保存工具结果")

    record = ToolResultRecord(
        result_id=str(uuid.uuid4()),
        suffix=suffix,
        conversation_id=conversation_id,
        user_id=user_id,
        directory_name=directory_name,
    )
    runtime = workspace_runtime_from_state(_request_state(request))
    await runtime.write_tool_result(file_path=record.virtual_path, content=content)
    return record


def _clip_text_to_chars(content: str, char_limit: int) -> str:
    if not content:
        return content
    if char_limit <= 0:
        return ""
    if len(content) <= char_limit:
        return content
    marker = "\n\n[... 工具结果已截断 ...]\n\n"
    if char_limit <= len(marker) + 2:
        return content[:char_limit]
    head_size = max(1, (char_limit - len(marker)) * 2 // 3)
    tail_size = max(1, char_limit - len(marker) - head_size)
    return f"{content[:head_size]}{marker}{content[-tail_size:]}"


def _request_state(request: Any) -> dict[str, Any]:
    state = getattr(request, "state", None)
    return state if isinstance(state, dict) else {}


def _request_workspace(request: Any) -> dict[str, Any]:
    workspace = _request_state(request).get("workspace")
    return workspace if isinstance(workspace, dict) else {}


def _required_state_value(request: Any, field: str) -> str:
    value = str(_request_state(request).get(field) or "").strip()
    if not value:
        raise ValueError(f"缺少工具结果归属字段: {field}")
    return value


def _tool_message_text(content: Any) -> str | None:
    """Serialize structured tool content before applying the shared budget."""
    if isinstance(content, str):
        return content
    if isinstance(content, (dict, list)):
        return json.dumps(content, ensure_ascii=False, default=str)
    return None


def _read_tool_recovery(tool_name: str, request: Any, content: Any) -> dict[str, Any] | None:
    args = _request_tool_args(request)
    if tool_name == "file_read":
        result_path = str(args.get("file_path") or "").strip()
        if not result_path:
            return None
        return build_tool_result_recovery(
            source_tool=tool_name,
            result_path=result_path,
            result_size_chars=len(_tool_message_text(content) or ""),
            offset=_non_negative_int(args.get("offset"), default=0),
            limit=_positive_int(args.get("limit"), default=200),
        )
    return None


def _request_tool_args(request: Any) -> dict[str, Any]:
    tool_call = getattr(request, "tool_call", None)
    if not isinstance(tool_call, dict):
        return {}
    args = tool_call.get("args")
    return args if isinstance(args, dict) else {}


def _non_negative_int(value: Any, *, default: int) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return default


def _positive_int(value: Any, *, default: int) -> int:
    try:
        return max(int(value), 1)
    except (TypeError, ValueError):
        return default


def _replace_tool_message(
    message: ToolMessage,
    *,
    content: str | None = None,
    recovery: dict[str, Any] | None = None,
) -> ToolMessage:
    update: dict[str, Any] = {}
    if content is not None:
        update["content"] = content
    if recovery is not None:
        update["artifact"] = merge_tool_result_recovery_artifact(message.artifact, recovery)
    return message.model_copy(update=update) if update else message
