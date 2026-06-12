from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable, Dict, Iterable, Optional, Tuple

import httpx
import openai

from apps.complaint_app.core.async_openai_client import get_async_openai_client
from utils.logger import get_current_log_dir, get_logger, resolve_log_path
from utils.llm_prompt_metrics import extract_usage_tokens, record_llm_output_metrics, record_llm_prompt_metrics

logger = get_logger(__name__)

CancelChecker = Callable[[], bool]
ChatMessage = Dict[str, Any]
ToolSpec = Dict[str, Any]
ExtraBody = Dict[str, Any]

_MESSAGES_LOGGER: Optional[logging.Logger] = None
_MESSAGES_LOGGER_PATH: Optional[str] = None


def _resolve_messages_log_path() -> str:
    raw = os.getenv("LLM_MESSAGES_LOG", "").strip()
    if raw:
        return resolve_log_path(raw)
    return resolve_log_path(os.path.join(get_current_log_dir(), "llm_messages.log"))


def _get_messages_logger() -> Optional[logging.Logger]:
    path = _resolve_messages_log_path()
    global _MESSAGES_LOGGER, _MESSAGES_LOGGER_PATH
    if _MESSAGES_LOGGER is not None and _MESSAGES_LOGGER_PATH == path:
        return _MESSAGES_LOGGER
    log = logging.getLogger("llm_messages")
    log.setLevel(logging.INFO)
    log.propagate = False
    if log.handlers:
        for handler in list(log.handlers):
            log.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    log.addHandler(handler)
    _MESSAGES_LOGGER = log
    _MESSAGES_LOGGER_PATH = path
    return log


_MAX_TEXT_LEN = 800
_MAX_URL_LEN = 160


def _compact_text(value: Any, limit: int = _MAX_TEXT_LEN) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}... [truncated {len(text) - limit} chars]"


def _compact_content(content: Any) -> str:
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if "text" in item:
                    parts.append(_compact_text(item.get("text")))
                elif "value" in item:
                    parts.append(_compact_text(item.get("value")))
                elif "image_url" in item:
                    image_url = item.get("image_url")
                    url = image_url.get("url") if isinstance(image_url, dict) else str(image_url)
                    parts.append(f"[image_url:{_compact_text(url, _MAX_URL_LEN)}]")
                else:
                    parts.append(_compact_text(json.dumps(item, ensure_ascii=False, default=str), 200))
            else:
                parts.append(_compact_text(item, 200))
        return " | ".join([p for p in parts if p])
    return _compact_text(content)


def _as_dict(obj: Any) -> Any:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return obj


def _compact_tool_calls(tool_calls: Any) -> list[str]:
    if not tool_calls:
        return []
    lines: list[str] = []
    for item in tool_calls:
        call = _as_dict(item) if item is not None else {}
        fn = call.get("function") or {}
        fn = _as_dict(fn)
        name = fn.get("name") or call.get("name") or ""
        args = fn.get("arguments")
        if isinstance(args, str):
            args_text = _compact_text(args, 400)
        else:
            args_text = _compact_text(json.dumps(args, ensure_ascii=False, default=str), 400)
        suffix = f" args={args_text}" if args_text else ""
        lines.append(f"- {name}{suffix}".strip())
    return lines


def _format_messages_block(
    *,
    title: str,
    client_name: str,
    model: Optional[str],
    call_id: str,
    messages: Iterable[ChatMessage],
) -> str:
    header = f"===== {title} [{call_id}] client={client_name} model={model or ''} ====="
    lines = [header]
    for msg in messages:
        if not isinstance(msg, dict):
            lines.append(f"- unknown: {_compact_text(msg)}")
            continue
        role = msg.get("role") or "unknown"
        name = msg.get("name") or msg.get("tool_name") or ""
        label = f"{role}({name})" if name else role
        content = _compact_content(msg.get("content"))
        if content:
            lines.append(f"- {label}: {content}")
        else:
            lines.append(f"- {label}")
        tool_calls = _compact_tool_calls(msg.get("tool_calls"))
        if tool_calls:
            lines.append("  tool_calls:")
            lines.extend([f"  {line}" for line in tool_calls])
    lines.append("===== END =====")
    return "\n".join(lines)


def _log_messages(kind: str, *, client_name: str, model: Optional[str], messages: Iterable[ChatMessage], call_id: str) -> None:
    log = _get_messages_logger()
    if not log:
        return
    try:
        log.info(_format_messages_block(title=kind, client_name=client_name, model=model, call_id=call_id, messages=messages))
    except Exception:
        log.info("%s messages log failed", client_name, exc_info=True)


def _extract_response_message(resp: Any) -> Dict[str, Any]:
    choices = getattr(resp, "choices", None) or []
    if not choices:
        return {}
    choice = choices[0]
    message = getattr(choice, "message", None) or {}
    return _as_dict(message) if message else {}


def _response_text_chars(resp: Any) -> str:
    message = _extract_response_message(resp)
    content = message.get("content") if isinstance(message, dict) else ""
    return _normalize_text(content)


def _format_response_block(
    *,
    title: str,
    client_name: str,
    model: Optional[str],
    call_id: str,
    response_message: Dict[str, Any],
) -> str:
    header = f"===== {title} [{call_id}] client={client_name} model={model or ''} ====="
    lines = [header]
    if response_message:
        role = response_message.get("role") or "assistant"
        name = response_message.get("name") or response_message.get("tool_name") or ""
        label = f"{role}({name})" if name else role
        content = _compact_content(response_message.get("content"))
        if content:
            lines.append(f"- {label}: {content}")
        else:
            lines.append(f"- {label}")
        tool_calls = _compact_tool_calls(response_message.get("tool_calls"))
        if tool_calls:
            lines.append("  tool_calls:")
            lines.extend([f"  {line}" for line in tool_calls])
    lines.append("===== END =====")
    return "\n".join(lines)


def _log_response(kind: str, *, client_name: str, model: Optional[str], response: Any, call_id: str) -> None:
    log = _get_messages_logger()
    if not log:
        return
    try:
        msg = _extract_response_message(response)
        log.info(_format_response_block(title=kind, client_name=client_name, model=model, call_id=call_id, response_message=msg))
    except Exception:
        log.info("%s response log failed", client_name, exc_info=True)


def _accumulate_stream_tool_calls(delta: Any, accumulator: Dict[int, Dict[str, Any]]) -> None:
    tool_calls = getattr(delta, "tool_calls", None)
    if not tool_calls:
        return
    for item in tool_calls:
        call = _as_dict(item)
        try:
            idx = int(call.get("index") or 0)
        except Exception:
            idx = 0
        entry = accumulator.get(idx)
        if not entry:
            entry = {"id": call.get("id") or "", "type": call.get("type") or "function", "function": {"name": "", "arguments": ""}}
            accumulator[idx] = entry
        if call.get("id"):
            entry["id"] = call.get("id")
        fn = call.get("function") or {}
        fn = _as_dict(fn)
        name = fn.get("name") if isinstance(fn, dict) else ""
        if name:
            entry["function"]["name"] = name
        args = fn.get("arguments") if isinstance(fn, dict) else ""
        if args:
            entry["function"]["arguments"] = (entry["function"].get("arguments") or "") + str(args)


def _finalize_stream_tool_calls(accumulator: Dict[int, Dict[str, Any]]) -> list[Dict[str, Any]]:
    if not accumulator:
        return []
    return [accumulator[key] for key in sorted(accumulator.keys())]


def _log_stream_response(
    kind: str,
    *,
    client_name: str,
    model: Optional[str],
    response_message: Dict[str, Any],
    call_id: str,
) -> None:
    log = _get_messages_logger()
    if not log:
        return
    try:
        log.info(
            _format_response_block(
                title=kind,
                client_name=client_name,
                model=model,
                call_id=call_id,
                response_message=response_message,
            )
        )
    except Exception:
        log.info("%s stream response log failed", client_name, exc_info=True)


@dataclass(frozen=True)
class RetryPolicy:
    """重试策略配置。"""

    max_retries: int = 2
    base_delay: float = 0.5
    max_delay: float = 8.0
    retryable_statuses: tuple[int, ...] = (408, 409, 429, 500, 502, 503, 504)

    def next_delay(self, attempt: int) -> float:
        """根据重试次数计算指数退避等待时间（秒）。"""
        delay = self.base_delay * (2 ** attempt)
        return min(delay, self.max_delay)

    def should_retry(self, exc: Exception) -> bool:
        """判断异常是否满足重试条件。"""
        if isinstance(exc, json.JSONDecodeError):
            return True
        if isinstance(exc, (httpx.TimeoutException, httpx.RequestError)):
            return True
        if isinstance(exc, getattr(openai, "APITimeoutError", ())):
            return True
        if isinstance(exc, getattr(openai, "APIConnectionError", ())):
            return True
        if isinstance(exc, getattr(openai, "RateLimitError", ())):
            return True
        if isinstance(exc, getattr(openai, "APIStatusError", ())):
            status_code = getattr(exc, "status_code", None)
            return status_code in self.retryable_statuses
        return False


@dataclass
class ModelConfig:
    """LLM 默认配置（模型与默认参数）。"""

    default_model: str = ""
    default_params: Dict[str, Any] = field(default_factory=dict)


class AsyncLLMClient:
    """异步 OpenAI 风格客户端封装，支持重试、日志与取消。"""

    def __init__(self, *, config: Optional[ModelConfig] = None, cancel_checker: Optional[CancelChecker] = None, name: str = "llm_client", metric_stage: str | None = None, metric_context: dict[str, Any] | None = None) -> None:
        self.config = config or ModelConfig()
        self.client = get_async_openai_client()
        self.retry_policy = RetryPolicy()
        self.cancel_checker = cancel_checker
        self.name = name
        self.metric_stage = metric_stage or name
        self.metric_context = metric_context or {}

    def _merge_params(self, *, model: Optional[str], params: Dict[str, Any]) -> Dict[str, Any]:
        # Default params first, then per-call overrides.
        merged = dict(self.config.default_params or {})
        merged.update({k: v for k, v in (params or {}).items() if v is not None})
        if model:
            merged["model"] = model
        elif self.config.default_model:
            merged["model"] = self.config.default_model
        return merged

    async def _call_with_retry(self, func, *, stage: str, **kwargs):
        attempt = 0
        while True:
            try:
                return await func(**kwargs)
            except Exception as exc:
                if attempt >= self.retry_policy.max_retries or not self.retry_policy.should_retry(exc):
                    logger.error("%s failed after %s attempts: %s", stage, attempt + 1, exc, exc_info=True)
                    raise
                delay = self.retry_policy.next_delay(attempt)
                logger.warning("%s failed (attempt %s), retry in %.2fs: %s", stage, attempt + 1, delay, exc)
                await asyncio.sleep(delay)
                attempt += 1

    async def chat(self, *, messages: Iterable[ChatMessage], model: Optional[str] = None, tools: Optional[Iterable[ToolSpec]] = None, extra_body: Optional[ExtraBody] = None, **params: Any):
        """非流式对话调用。

        参数:
            messages: 对话消息列表（OpenAI-style）。
            model: 覆盖默认模型名。
            tools: function calling 工具定义。
            extra_body: 附加请求体（如思考开关）。
            **params: 其他 OpenAI 参数（temperature/max_tokens 等）。
        返回:
            OpenAI SDK 的完整响应对象。
        """
        payload = self._merge_params(model=model, params=params)
        payload["messages"] = list(messages)
        payload.setdefault("stream", False)
        if tools is not None:
            payload["tools"] = list(tools)
        if extra_body is not None:
            payload["extra_body"] = extra_body
        call_id = uuid.uuid4().hex[:8]
        _log_messages("REQUEST chat", client_name=self.name, model=payload.get("model"), messages=payload["messages"], call_id=call_id)
        record_llm_prompt_metrics(self.metric_stage, model=payload.get("model"), messages=payload["messages"], tools=payload.get("tools"), metadata=self.metric_context, call_id=call_id)
        start = time.monotonic()
        resp = await self._call_with_retry(
            self.client.chat.completions.create,
            stage=f"{self.name}.chat",
            **payload,
        )
        elapsed = time.monotonic() - start
        logger.info("%s.chat ok model=%s elapsed=%.3fs", self.name, payload.get("model"), elapsed)
        record_llm_output_metrics(self.metric_stage, model=payload.get("model"), output_text=_response_text_chars(resp), first_token_ms=0, llm_duration_ms=int(elapsed * 1000), usage=extract_usage_tokens(resp), metadata=self.metric_context, call_id=call_id)
        _log_response("RESPONSE chat", client_name=self.name, model=payload.get("model"), response=resp, call_id=call_id)
        return resp

    async def stream_chat(self, *, messages: Iterable[ChatMessage], model: Optional[str] = None, cancel_checker: Optional[CancelChecker] = None, tools: Optional[Iterable[ToolSpec]] = None, extra_body: Optional[ExtraBody] = None, **params: Any) -> AsyncGenerator[Any, None]:
        """流式对话调用（返回异步生成器）。

        参数:
            messages: 对话消息列表（OpenAI-style）。
            model: 覆盖默认模型名。
            cancel_checker: 取消检查函数，返回 True 表示中断流。
            tools: function calling 工具定义。
            extra_body: 附加请求体（如思考开关）。
            **params: 其他 OpenAI 参数（temperature/max_tokens 等）。
        返回:
            异步生成器，逐条产出流式响应片段。
        """
        payload = self._merge_params(model=model, params=params)
        payload["messages"] = list(messages)
        payload["stream"] = True
        if tools is not None:
            payload["tools"] = list(tools)
        if extra_body is not None:
            payload["extra_body"] = extra_body
        call_id = uuid.uuid4().hex[:8]
        _log_messages("REQUEST stream_chat", client_name=self.name, model=payload.get("model"), messages=payload["messages"], call_id=call_id)
        record_llm_prompt_metrics(self.metric_stage, model=payload.get("model"), messages=payload["messages"], tools=payload.get("tools"), metadata=self.metric_context, call_id=call_id)
        start = time.monotonic()
        stream = await self._call_with_retry(
            self.client.chat.completions.create,
            stage=f"{self.name}.stream_chat",
            **payload,
        )
        checker = cancel_checker or self.cancel_checker
        content_chunks: list[str] = []
        tool_acc: Dict[int, Dict[str, Any]] = {}
        first_token_ms = 0
        try:
            async for chunk in stream:
                if checker and checker():
                    break
                try:
                    choices = getattr(chunk, "choices", None) or []
                    if choices:
                        delta = getattr(choices[0], "delta", None)
                    else:
                        delta = None
                    if delta:
                        text_delta, _, _ = extract_stream_deltas(delta)
                        if text_delta:
                            if not first_token_ms:
                                first_token_ms = int((time.monotonic() - start) * 1000)
                            content_chunks.append(text_delta)
                        _accumulate_stream_tool_calls(delta, tool_acc)
                except Exception:
                    pass
                yield chunk
        except Exception as exc:
            elapsed = time.monotonic() - start
            logger.error("%s.stream_chat error model=%s elapsed=%.3fs received_chars=%s err=%s", self.name, payload.get("model"), elapsed, len("".join(content_chunks)), exc, exc_info=True)
            raise
        finally:
            elapsed = time.monotonic() - start
            logger.info("%s.stream_chat end model=%s elapsed=%.3fs", self.name, payload.get("model"), elapsed)
            response_message = {
                "role": "assistant",
                "content": "".join(content_chunks),
                "tool_calls": _finalize_stream_tool_calls(tool_acc),
            }
            record_llm_output_metrics(self.metric_stage, model=payload.get("model"), output_text=response_message["content"], first_token_ms=first_token_ms, llm_duration_ms=int(elapsed * 1000), metadata=self.metric_context, call_id=call_id)
            _log_stream_response(
                "RESPONSE stream_chat",
                client_name=self.name,
                model=payload.get("model"),
                response_message=response_message,
                call_id=call_id,
            )


def _normalize_text(value: Any) -> str:
    """Best-effort normalization for string or list-based content blocks."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("text") or item.get("value")
                if text:
                    parts.append(str(text))
            elif item is not None:
                parts.append(str(item))
        return "".join(parts)
    return "" if value is None else str(value)


def extract_stream_deltas(delta: Any) -> Tuple[str, str, str]:
    """提取流式增量文本与思考字段：(content_delta, reasoning_delta, reasoning_field)。"""
    content = _normalize_text(getattr(delta, "content", ""))
    for field in ("reasoning_content", "analysis", "thoughts", "think"):
        value = getattr(delta, field, None)
        if isinstance(value, str) and value:
            return content, value, field
    return content, "", ""


__all__ = ["AsyncLLMClient", "ModelConfig", "RetryPolicy", "extract_stream_deltas"]
