"""Single result-budget boundary for every ChatData tool call."""

from __future__ import annotations

import json
import uuid
from typing import Any

from langchain.agents.middleware import wrap_tool_call
from langchain_core.messages import ToolMessage

from utils.logger import get_logger
from utils.tool_result_store import ToolResultRecord
from ...services.context_budget import ToolResultBudget, estimate_text_tokens
from ...services.conversation_workspace import ConversationWorkspaceRuntime
from ...services.query_result import query_result_status, query_result_succeeded
from ...services.runtime_config import TOOL_RESULT_OFFLOAD_SIZE
from ..tool_results import (
    build_query_result_response,
    parse_internal_tool_result,
)

logger = get_logger(__name__)

_READ_PAGINATED_TOOLS = frozenset({"file_read", "skill_load"})
_RESULT_SAFETY_MARGIN_TOKENS = 512


def create_tool_result_middleware(tool_result_budget: ToolResultBudget):
    """Create the sole boundary that turns raw tool output into model output."""

    @wrap_tool_call(name="ToolResultMiddleware")
    async def tool_result_middleware(request, handler):
        result = await handler(request)
        if not isinstance(result, ToolMessage) or result.status == "error":
            return result
        return await _finalize_tool_message(result, request, tool_result_budget)

    return tool_result_middleware


async def _finalize_tool_message(
    message: ToolMessage,
    request: Any,
    tool_result_budget: ToolResultBudget,
) -> ToolMessage:
    tool_name = str(message.name or "").strip()
    internal = parse_internal_tool_result(message.content)
    if internal is not None:
        content = await _finalize_query_result(internal, request, tool_result_budget)
        return _replace_tool_message_content(message, content)
    if tool_name in _READ_PAGINATED_TOOLS:
        return message
    content = _tool_message_text(message.content)
    if content is None or not _requires_externalization(content, request, tool_result_budget):
        return message
    externalized = await _externalize_generic_text(content, tool_name, request, tool_result_budget)
    return _replace_tool_message_content(message, externalized)


async def _finalize_query_result(
    internal: dict[str, Any],
    request: Any,
    tool_result_budget: ToolResultBudget,
) -> str:
    query_result = dict(internal["payload"])
    meta = dict(internal["meta"])
    status = query_result_status(query_result)
    public_meta = {
        "query_mode": str(meta.get("query_mode") or ""),
        "confidence": meta.get("confidence"),
    }
    public_meta = {key: value for key, value in public_meta.items() if value is not None and value != ""}
    if not query_result_succeeded(query_result):
        public_meta["sql"] = query_result.get("sql") or ""
        return build_query_result_response(
            status=status,
            error=str(query_result.get("error") or "未知错误"),
            meta=public_meta,
        )

    save_as = str(meta.get("save_as") or "").strip()
    if save_as:
        return await _save_query_result_to_workspace(
            query_result,
            public_meta=public_meta,
            save_as=save_as,
            request=request,
        )

    raw_result = json.dumps(query_result, ensure_ascii=False)
    if not _requires_externalization(raw_result, request, tool_result_budget):
        return build_query_result_response(status=status, data=query_result, meta=public_meta)

    try:
        record = await _write_private_tool_result(
            content=json.dumps(query_result, ensure_ascii=False, indent=2),
            suffix=".json",
            request=request,
        )
    except Exception as exc:
        logger.error("卸载查询工具结果失败: %s", exc, exc_info=True)
        return _query_result_fallback(
            query_result,
            public_meta=public_meta,
            raw_result=raw_result,
            request=request,
            tool_result_budget=tool_result_budget,
        )

    public_meta.update({
        "result_id": record.result_id,
        "result_path": record.virtual_path,
        "total_count": query_result.get("total_count", 0),
        "execution_time_ms": query_result.get("execution_time_ms"),
        "result_size_chars": len(raw_result),
        "llm_analysis_skipped": True,
        "message": "查询结果已超过上下文预算，完整数据未进入模型上下文；前端会自动加载完整结果供用户查看。",
    })
    if query_result.get("sql"):
        public_meta["sql"] = query_result["sql"]
    logger.info(
        "查询工具结果已卸载: result_id=%s size_chars=%s rows=%s",
        record.result_id,
        len(raw_result),
        query_result.get("total_count", 0),
    )
    return build_query_result_response(status=status, data=None, meta=public_meta)


async def _save_query_result_to_workspace(
    query_result: dict[str, Any],
    *,
    public_meta: dict[str, Any],
    save_as: str,
    request: Any,
) -> str:
    state = _request_state(request)
    workspace = state.get("workspace") if isinstance(state.get("workspace"), dict) else {}
    workspace_root = str(workspace.get("workspace_root") or "").strip()
    if not workspace_root:
        return build_query_result_response(
            status=query_result_status(query_result),
            error="当前会话未初始化工作目录，无法保存结果",
            meta=public_meta,
        )
    runtime = ConversationWorkspaceRuntime(
        workspace_root=workspace_root,
        user_id=str(state.get("user_id") or "system"),
        backend="sandbox" if workspace.get("workspace_backend") == "sandbox" else "local",
    )
    try:
        write_result = await runtime.write_text_file(
            file_path=save_as,
            content=json.dumps(query_result, ensure_ascii=False, indent=2),
            mode="overwrite",
        )
    except Exception as exc:
        logger.error("保存查询结果到工作区失败: path=%s error=%s", save_as, exc, exc_info=True)
        return build_query_result_response(
            status=query_result_status(query_result),
            error="保存查询结果失败，请检查目标路径后重试",
            meta=public_meta,
        )
    public_meta.update({
        "result_path": write_result.get("file_path") or save_as,
        "total_count": query_result.get("total_count"),
        "execution_time_ms": query_result.get("execution_time_ms"),
        "sql": query_result.get("sql") or "",
        "next_required_action": "使用 file_read 抽查文件，或用 bash_run 读取 result_path 生成后续产物。",
    })
    return build_query_result_response(
        status=query_result_status(query_result),
        data=None,
        error=query_result.get("error"),
        meta=public_meta,
    )


def _query_result_fallback(
    query_result: dict[str, Any],
    *,
    public_meta: dict[str, Any],
    raw_result: str,
    request: Any,
    tool_result_budget: ToolResultBudget,
) -> str:
    clipped = _clip_text_to_budget(raw_result, _inline_token_limit(request, tool_result_budget))
    public_meta.update({
        "sql": query_result.get("sql") or "",
        "total_count": query_result.get("total_count", 0),
        "result_size_chars": len(raw_result),
        "result_externalization_failed": True,
        "message": "查询结果过大且暂时无法保存完整结果；模型仅收到受限摘要，请缩小查询范围后重试。",
        "preview": clipped,
    })
    return build_query_result_response(
        status=query_result_status(query_result),
        data=None,
        meta=public_meta,
    )


async def _externalize_generic_text(
    content: str,
    tool_name: str,
    request: Any,
    tool_result_budget: ToolResultBudget,
) -> str:
    try:
        record = await _write_private_tool_result(
            content=content,
            suffix=".txt",
            request=request,
        )
    except Exception as exc:
        logger.error("卸载工具结果失败: tool=%s error=%s", tool_name, exc, exc_info=True)
        return _clip_text_to_budget(content, _inline_token_limit(request, tool_result_budget))
    return _build_offloaded_tool_result_message(
        tool_name=tool_name,
        result_path=record.virtual_path,
        result_size_chars=len(content),
        preview=None if tool_name == "search_dataset" else _clip_text_to_budget(
            content,
            min(512, _inline_token_limit(request, tool_result_budget)),
        ),
    )


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


async def _write_private_tool_result(
    *,
    content: str,
    suffix: str,
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
    )
    runtime = ConversationWorkspaceRuntime(
        workspace_root=workspace_root,
        user_id=user_id,
        backend="sandbox" if workspace.get("workspace_backend") == "sandbox" else "local",
    )
    await runtime.write_tool_result(file_path=record.virtual_path, content=content)
    return record


def _requires_externalization(content: str, request: Any, tool_result_budget: ToolResultBudget) -> bool:
    if len(content) > TOOL_RESULT_OFFLOAD_SIZE:
        return True
    return estimate_text_tokens(content) > _inline_token_limit(request, tool_result_budget)


def _inline_token_limit(request: Any, tool_result_budget: ToolResultBudget) -> int:
    messages = list(_request_state(request).get("messages") or [])
    remaining = tool_result_budget.remaining_for_tool_result(messages)
    return max(0, remaining - _RESULT_SAFETY_MARGIN_TOKENS)


def _clip_text_to_budget(content: str, token_limit: int) -> str:
    if not content:
        return content
    if token_limit <= 0:
        return ""
    if estimate_text_tokens(content) <= token_limit:
        return content
    marker = "\n\n[... 工具结果已截断 ...]\n\n"
    char_limit = min(len(content), max(1, token_limit * 4))
    while char_limit > 0:
        if char_limit >= len(content):
            candidate = content
        elif char_limit > len(marker) + 2:
            head_size = max(1, (char_limit - len(marker)) * 2 // 3)
            tail_size = max(1, char_limit - len(marker) - head_size)
            candidate = f"{content[:head_size]}{marker}{content[-tail_size:]}"
        else:
            candidate = content[:char_limit]
        if estimate_text_tokens(candidate) <= token_limit:
            return candidate
        char_limit -= max(1, char_limit // 8)
    return ""


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


def _replace_tool_message_content(message: ToolMessage, content: str) -> ToolMessage:
    return message.model_copy(update={"content": content})
