from dataclasses import dataclass
from typing import Optional

from fastapi import Request
from fastapi.security import APIKeyHeader

from utils.crypto import decrypt_env_value


SERVICE_AUTH_HEADER = "X-Service-Token"
SERVICE_USER_ID_HEADER = "X-User-Id"
SERVICE_USERNAME_HEADER = "X-Username"
SERVICE_AUTH_TOKEN = decrypt_env_value("SERVICE_AUTH_TOKEN")
service_token_header = APIKeyHeader(
    name=SERVICE_AUTH_HEADER,
    scheme_name="ServiceToken",
    auto_error=False,
)


class AuthenticationError(Exception):
    """Raised when service-forward authentication fails."""

    def __init__(self, message: str, status_code: int = 401) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: str
    username: str


def _normalize_header_text(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        return value.encode("latin-1").decode("utf-8").strip()
    except UnicodeError:
        return value.strip()


def validate_service_token(token: Optional[str]) -> None:
    """Validate a service-to-service token value."""
    if not SERVICE_AUTH_TOKEN:
        raise AuthenticationError("Missing or invalid service token.", status_code=401)
    if not token or token != SERVICE_AUTH_TOKEN:
        raise AuthenticationError("Missing or invalid service token.", status_code=401)


def get_forwarded_user(req: Request) -> AuthenticatedUser:
    """Resolve the end-user identity forwarded by the trusted service."""
    user_id = _normalize_header_text(req.headers.get(SERVICE_USER_ID_HEADER))
    username = _normalize_header_text(req.headers.get(SERVICE_USERNAME_HEADER))
    if not user_id or not username:
        raise AuthenticationError("Missing forwarded user headers.", status_code=401)

    return AuthenticatedUser(user_id=user_id, username=username)


__all__ = [
    "AuthenticatedUser",
    "AuthenticationError",
    "service_token_header",
    "validate_service_token",
    "get_forwarded_user",
]
