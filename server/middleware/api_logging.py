from __future__ import annotations

import base64
import binascii
import time
import uuid
from datetime import datetime
from urllib.parse import parse_qs

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from middleware.log_redaction import dump_masked_payload
from utils.logger import clear_log_context, get_access_logger, get_alarm_logger, get_logger, set_log_context

logger = get_logger(__name__)
access_logger = get_access_logger()
alarm_logger = get_alarm_logger()

DOWNLOAD_CONTENT_TYPES = {
    "application/octet-stream",
    "application/pdf",
    "application/zip",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _build_request_id() -> str:
    return uuid.uuid4().hex[:16]


def _decode_bytes(value: bytes) -> str:
    return value.decode("utf-8", errors="ignore")


def _decode_username_header_value(value: bytes) -> bytes:
    try:
        return base64.b64decode(value.strip(), validate=True)
    except (binascii.Error, ValueError):
        return value


def _decode_username_header(scope: Scope) -> None:
    raw_headers = list(scope.get("headers", []))
    scope["headers"] = [
        (key, _decode_username_header_value(value)) if key.lower() == b"x-username" else (key, value)
        for key, value in raw_headers
    ]


def _scope_headers(scope: Scope) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key, value in scope.get("headers", []):
        headers[_decode_bytes(key).lower()] = _decode_bytes(value)
    return headers


def _scope_query(scope: Scope) -> dict[str, str]:
    query_string = _decode_bytes(scope.get("query_string", b""))
    parsed = parse_qs(query_string, keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


def _client_ip(scope: Scope, headers: dict[str, str]) -> str:
    forwarded = str(headers.get("x-forwarded-for", "")).strip()
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = scope.get("client")
    if isinstance(client, tuple) and client:
        return str(client[0] or "")
    return ""


def _upsert_header(headers: list[tuple[bytes, bytes]], key: bytes, value: bytes) -> list[tuple[bytes, bytes]]:
    result: list[tuple[bytes, bytes]] = []
    lowered = key.lower()
    replaced = False
    for item_key, item_value in headers:
        if item_key.lower() == lowered:
            if not replaced:
                result.append((key, value))
                replaced = True
            continue
        result.append((item_key, item_value))
    if not replaced:
        result.append((key, value))
    return result


def _is_streaming_response(content_type: str, disposition: str) -> bool:
    lowered_type = str(content_type or "").lower()
    lowered_disposition = str(disposition or "").lower()
    if "text/event-stream" in lowered_type:
        return True
    if "attachment" in lowered_disposition:
        return True
    return any(item in lowered_type for item in DOWNLOAD_CONTENT_TYPES)


def _format_start_time(started_at: float) -> str:
    return datetime.fromtimestamp(started_at).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _access_ext1(client_ip: str, user_id: str | None) -> str:
    parts = [f"client_ip:{client_ip or '-'}"]
    if user_id:
        parts.append(f"user_id:{user_id}")
    return ";".join(parts)


class _ResponseLogState:
    def __init__(self, request_id: str, method: str, path: str, query: dict[str, str], client_ip: str, user_id: str | None, started_at: float):
        self.request_id = request_id
        self.method = method
        self.path = path
        self.query = query
        self.client_ip = client_ip
        self.user_id = user_id
        self.started_at = started_at
        self.status_code = 500
        self.content_type = ""
        self.content_length = ""
        self.disposition = ""
        self.response_logged = False

    def _base_extra(self, elapsed_ms: int, error_code: str) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "request_type": self.method,
            "start_time": _format_start_time(self.started_at),
            "cost_time": f"{elapsed_ms}ms",
            "error_code": error_code,
            "ext1": _access_ext1(self.client_ip, self.user_id),
        }

    def log_response(self) -> None:
        if self.response_logged:
            return
        elapsed_ms = int((time.time() - self.started_at) * 1000)
        is_streaming = _is_streaming_response(self.content_type, self.disposition)
        response_payload = {
            "request_id": self.request_id,
            "method": self.method,
            "path": self.path,
            "status_code": self.status_code,
            "elapsed_ms": elapsed_ms,
            "content_type": self.content_type,
            "content_length": self.content_length,
            "body": "[omitted]",
            "body_omitted_reason": "streaming_or_download" if is_streaming else "body_capture_disabled",
        }
        error_code = str(self.status_code) if self.status_code >= 400 else "0"
        extra = self._base_extra(elapsed_ms=elapsed_ms, error_code=error_code)
        extra["ext2"] = dump_masked_payload(response_payload)
        access_logger.info("api_access", extra=extra)
        self.response_logged = True

    def log_error(self, exc: Exception) -> None:
        if self.response_logged:
            return
        elapsed_ms = int((time.time() - self.started_at) * 1000)
        error_payload = {
            "request_id": self.request_id,
            "method": self.method,
            "path": self.path,
            "query": self.query,
            "elapsed_ms": elapsed_ms,
            "error": str(exc),
        }
        extra = self._base_extra(elapsed_ms=elapsed_ms, error_code="500")
        extra["ext2"] = dump_masked_payload(error_payload)
        access_logger.info("api_access_error", extra=extra)
        alarm_logger.error(
            f"{self.method} {self.path} failed: {exc}",
            extra={
                "request_id": self.request_id,
                "request_type": self.method,
                "error_code": "500",
                "ext1": _access_ext1(self.client_ip, self.user_id),
                "ext2": dump_masked_payload(error_payload),
            },
        )
        self.response_logged = True


class APIAccessLogMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        _decode_username_header(scope)
        headers = _scope_headers(scope)
        request_id = _build_request_id()
        started_at = time.time()
        user_id = headers.get("x-user-id")
        set_log_context(request_id=request_id, user_id=user_id)

        response_state = _ResponseLogState(
            request_id=request_id,
            method=str(scope.get("method", "")),
            path=str(scope.get("path", "")),
            query=_scope_query(scope),
            client_ip=_client_ip(scope, headers),
            user_id=user_id,
            started_at=started_at,
        )

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                raw_headers = list(message.get("headers", []))
                raw_headers = _upsert_header(raw_headers, b"x-request-id", request_id.encode("utf-8"))
                message["headers"] = raw_headers
                header_map = {_decode_bytes(key).lower(): _decode_bytes(value) for key, value in raw_headers}
                response_state.status_code = int(message.get("status", 500))
                response_state.content_type = header_map.get("content-type", "")
                response_state.content_length = header_map.get("content-length", "")
                response_state.disposition = header_map.get("content-disposition", "")
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
            response_state.log_response()
        except Exception as exc:
            response_state.log_error(exc)
            logger.exception("[api_access] unhandled_error request_id=%s", request_id)
            raise
        finally:
            clear_log_context()
