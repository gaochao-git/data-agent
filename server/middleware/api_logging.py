from __future__ import annotations

import os
import base64
import binascii
import time
import uuid
from urllib.parse import parse_qs

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from middleware.log_redaction import dump_masked_payload, mask_headers
from utils.logger import get_logger

logger = get_logger(__name__)

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


def _normalize_header_text(value: str | None) -> str:
    if not value:
        return ""
    text = value.strip()
    try:
        return text.encode("latin-1").decode("utf-8")
    except UnicodeError:
        return text


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


def _header_summary(headers: dict[str, str]) -> dict[str, str]:
    selected = {
        "user-agent": headers.get("user-agent", ""),
        "authorization": headers.get("authorization", ""),
        "cookie": headers.get("cookie", ""),
        "x-service-token": headers.get("x-service-token", ""),
        "x-username": _normalize_header_text(headers.get("x-username", "")),
        "content-type": headers.get("content-type", ""),
        "content-length": headers.get("content-length", ""),
    }
    return {key: value for key, value in mask_headers(selected).items() if str(value or "").strip()}


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


class _ResponseLogState:
    def __init__(self, request_id: str, method: str, path: str, started_at: float):
        self.request_id = request_id
        self.method = method
        self.path = path
        self.started_at = started_at
        self.status_code = 500
        self.content_type = ""
        self.content_length = ""
        self.disposition = ""
        self.response_logged = False

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
        logger.info("[api_access] response=%s", dump_masked_payload(response_payload))
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
        request_payload = {
            "request_id": request_id,
            "method": scope.get("method", ""),
            "path": scope.get("path", ""),
            "query": _scope_query(scope),
            "headers": _header_summary(headers),
            "content_type": headers.get("content-type", ""),
            "content_length": headers.get("content-length", ""),
            "client_ip": _client_ip(scope, headers),
            "body": "[omitted]",
            "body_omitted_reason": "body_capture_disabled",
        }
        logger.info("[api_access] request=%s", dump_masked_payload(request_payload))

        response_state = _ResponseLogState(request_id, str(scope.get("method", "")), str(scope.get("path", "")), started_at)

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
            elapsed_ms = int((time.time() - started_at) * 1000)
            error_payload = {
                "request_id": request_id,
                "method": scope.get("method", ""),
                "path": scope.get("path", ""),
                "elapsed_ms": elapsed_ms,
                "error": str(exc),
            }
            logger.error("[api_access] error=%s", dump_masked_payload(error_payload))
            raise
