import os
import sys
from fastapi import Request

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import auth


def _request(headers: dict[str, str]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [
                (name.lower().encode("latin-1"), value.encode("utf-8"))
                for name, value in headers.items()
            ],
        }
    )


def test_service_request_uses_user_headers(monkeypatch):
    monkeypatch.setattr(auth, "SERVICE_AUTH_TOKEN", "service-token")
    req = _request(
        {
            "X-Service-Token": "service-token",
            "X-User-Id": "u-123",
            "X-Username": "张三",
        }
    )

    user = auth.authenticate_request(req)

    assert user.user_id == "u-123"
    assert user.username == "张三"


def test_service_request_accepts_username_header(monkeypatch):
    monkeypatch.setattr(auth, "SERVICE_AUTH_TOKEN", "service-token")
    req = _request(
        {
            "X-Service-Token": "service-token",
            "X-User-Id": "u-123",
            "X-Username": "张三",
        }
    )

    user = auth.authenticate_request(req)

    assert user.user_id == "u-123"
    assert user.username == "张三"


def test_service_request_keeps_chinese_username_ending_with_control_byte(monkeypatch):
    monkeypatch.setattr(auth, "SERVICE_AUTH_TOKEN", "service-token")
    req = _request(
        {
            "X-Service-Token": "service-token",
            "X-User-Id": "u-123",
            "X-Username": "总行业务操作员高超",
        }
    )

    user = auth.authenticate_request(req)

    assert user.user_id == "u-123"
    assert user.username == "总行业务操作员高超"


def test_service_request_without_user_headers_is_rejected(monkeypatch):
    monkeypatch.setattr(auth, "SERVICE_AUTH_TOKEN", "service-token")
    req = _request({"X-Service-Token": "service-token"})

    try:
        auth.authenticate_request(req)
    except auth.AuthenticationError as exc:
        assert exc.message == "Missing forwarded user headers."
    else:
        raise AssertionError("Expected AuthenticationError")


def test_request_without_service_token_is_rejected(monkeypatch):
    monkeypatch.setattr(auth, "SERVICE_AUTH_TOKEN", "service-token")
    req = _request({"X-User-Id": "u-123", "X-Username": "张三"})

    try:
        auth.authenticate_request(req)
    except auth.AuthenticationError as exc:
        assert exc.message == "Missing or invalid service token."
    else:
        raise AssertionError("Expected AuthenticationError")
