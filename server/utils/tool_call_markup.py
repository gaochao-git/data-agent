"""清理模型退化成正文的裸 tool-call XML 标记。

Qwen 系在长上下文/工具轮次可能把 tool call 输出成正文：
``<tool_call>...``、``<function=...>``、``<parameter=...>``，或者只留下
``</parameter></function></tool_call>`` 尾巴。结构化 tool_calls 已经由模型
协议解析；这些残留不应进入用户可见内容、SSE 增量或历史回放。
"""

from __future__ import annotations

import re
from typing import Any

_TOOL_CALL_BLOCK_RE = re.compile(r"<tool_call\b[^>]*>.*?(?:</tool_call\s*>|$)", re.DOTALL | re.IGNORECASE)
_TOOL_MARKUP_TAG_RE = re.compile(r"</?(?:tool_call|function|parameter)(?:\s*[^<>]*)?>", re.IGNORECASE)


def sanitize_tool_call_markup(text: str) -> str:
    """剥掉正文里的裸 tool-call 标记；普通文本保持不变。"""
    if not text:
        return text
    cleaned = _TOOL_CALL_BLOCK_RE.sub("", str(text))
    return _TOOL_MARKUP_TAG_RE.sub("", cleaned)


def has_tool_call_markup(text: str) -> bool:
    """是否检测到裸 tool-call 标记（用于告警/重试判定）。"""
    return bool(text) and bool(_TOOL_CALL_BLOCK_RE.search(str(text)) or _TOOL_MARKUP_TAG_RE.search(str(text)))


def sanitize_message_payload(payload: Any) -> dict[str, Any]:
    """净化 AIMessage.model_dump() 的 content / reasoning 字段。"""
    data = dict(payload) if isinstance(payload, dict) else {}
    if "content" in data:
        data["content"] = sanitize_message_content(data.get("content"))
    additional = data.get("additional_kwargs")
    if isinstance(additional, dict):
        cleaned = dict(additional)
        for key in ("reasoning_content", "reasoning", "reasoning_text"):
            value = cleaned.get(key)
            if isinstance(value, str):
                cleaned[key] = sanitize_tool_call_markup(value)
        data["additional_kwargs"] = cleaned
    return data


def sanitize_message_content(content: Any) -> Any:
    """处理字符串或 LangChain multimodal content block 列表。"""
    if isinstance(content, str):
        return sanitize_tool_call_markup(content)
    if isinstance(content, list):
        cleaned: list[Any] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                cleaned.append({**block, "text": sanitize_tool_call_markup(block["text"])})
            else:
                cleaned.append(block)
        return cleaned
    return content


__all__ = [
    "has_tool_call_markup",
    "sanitize_message_content",
    "sanitize_message_payload",
    "sanitize_tool_call_markup",
]
