"""Tool retry guard for DataAnalyze agent."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import StructuredTool

MAX_FAILURES_PER_SAME_CALL = 2
FAILURE_MARKERS = (
    "执行失败",
    "failed",
    "error",
    "exception",
    "traceback",
    "找不到",
    "不存在",
)


def _normalize_for_key(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _normalize_for_key(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_normalize_for_key(item) for item in value]
    if isinstance(value, set):
        return sorted(_normalize_for_key(item) for item in value)
    return value


def _tool_call_key(tool_name: str, args: dict[str, Any]) -> str:
    payload = {
        "tool": tool_name,
        "args": _normalize_for_key(args or {}),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _strip_tool_prefix(result: str) -> str:
    text = str(result or "")
    if text.startswith("[") and "]\n" in text:
        return text.split("]\n", 1)[1]
    return text


def is_failed_tool_result(result: Any) -> bool:
    if not isinstance(result, str):
        return False
    text = result.strip()
    if not text:
        return False
    payload_text = _strip_tool_prefix(text)
    try:
        payload = json.loads(payload_text)
    except Exception:
        payload = None
    if isinstance(payload, dict) and "success" in payload:
        return payload.get("success") is False
    lowered = text.lower()
    return any(marker in lowered for marker in FAILURE_MARKERS)


def create_tool_retry_guard(tools: list[Any]) -> list[Any]:
    """Block repeated identical failing tool calls in one agent run."""
    failure_counts: dict[str, int] = {}
    guarded_tools: list[Any] = []

    for original_tool in tools:
        tool_name = str(getattr(original_tool, "name", "") or "")
        args_schema = getattr(original_tool, "args_schema", None)
        description = str(getattr(original_tool, "description", "") or "")
        if not tool_name or args_schema is None or not hasattr(original_tool, "ainvoke"):
            guarded_tools.append(original_tool)
            continue

        async def guarded_coroutine(_original_tool=original_tool, _tool_name=tool_name, **kwargs):
            call_key = _tool_call_key(_tool_name, kwargs)
            failed_count = failure_counts.get(call_key, 0)
            if failed_count >= MAX_FAILURES_PER_SAME_CALL:
                return (
                    f"{_tool_name} 调用被阻断：同一个工具使用相同参数已连续失败 {failed_count} 次。"
                    "请不要继续重复调用；必须修改参数、换文件路径、先读取当前文件内容、换工具，"
                    "或停止该分支并向用户说明失败原因。"
                )
            result = await _original_tool.ainvoke(kwargs)
            if is_failed_tool_result(result):
                failure_counts[call_key] = failed_count + 1
            else:
                failure_counts.pop(call_key, None)
            return result

        guarded_tools.append(StructuredTool.from_function(
            coroutine=guarded_coroutine,
            name=tool_name,
            description=description,
            args_schema=args_schema,
        ))

    return guarded_tools
