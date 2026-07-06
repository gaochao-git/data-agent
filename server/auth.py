from dataclasses import dataclass
from typing import Optional

from fastapi import Request

from utils.crypto import decrypt_env_value


SERVICE_AUTH_HEADER = "X-Service-Token"
SERVICE_USER_ID_HEADER = "X-User-Id"
SERVICE_USERNAME_HEADER = "X-Username"
SERVICE_AUTH_TOKEN = decrypt_env_value("SERVICE_AUTH_TOKEN")


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


def authenticate_request(req: Request) -> AuthenticatedUser:
    """Resolve the authenticated user from the incoming request or raise."""
    if not SERVICE_AUTH_TOKEN:
        raise AuthenticationError("Missing or invalid service token.", status_code=401)
    token = req.headers.get(SERVICE_AUTH_HEADER)
    if not token or token != SERVICE_AUTH_TOKEN:
        raise AuthenticationError("Missing or invalid service token.", status_code=401)

    user_id = _normalize_header_text(req.headers.get(SERVICE_USER_ID_HEADER))
    username = _normalize_header_text(req.headers.get(SERVICE_USERNAME_HEADER))
    if not user_id or not username:
        raise AuthenticationError("Missing forwarded user headers.", status_code=401)

    return AuthenticatedUser(user_id=user_id, username=username)


__all__ = [
    "AuthenticatedUser",
    "AuthenticationError",
    "authenticate_request",
]
