import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

import jwt
from fastapi import Request


class AuthenticationError(Exception):
    """Raised when JWT authentication fails."""

    def __init__(self, message: str, status_code: int = 401) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _parse_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY") or os.getenv("SECRET_KEY") or "dev-secret-key"
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_COOKIE_NAME = os.getenv("JWT_COOKIE_NAME", "inner_token")
JWT_COOKIE_SECURE = _parse_bool_env("JWT_COOKIE_SECURE", False)
JWT_COOKIE_SAMESITE = os.getenv("JWT_COOKIE_SAMESITE", "Lax")
SERVICE_AUTH_HEADER = "X-Service-Token"
SERVICE_AUTH_TOKEN = os.getenv("SERVICE_AUTH_TOKEN") or ""

_DEFAULT_EXPIRATION_MINUTES = 60 * 24 * 30  # 30 days
try:
    JWT_EXPIRATION_MINUTES = int(
        os.getenv("JWT_EXPIRATION_MINUTES", str(_DEFAULT_EXPIRATION_MINUTES))
    )
    if JWT_EXPIRATION_MINUTES <= 0:
        raise ValueError
except (TypeError, ValueError):
    JWT_EXPIRATION_MINUTES = _DEFAULT_EXPIRATION_MINUTES


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: str
    username: str
    issued_at: datetime
    expires_at: datetime
    claims: Dict[str, Any]


_SERVICE_USER_ISSUED_AT = datetime.now(tz=timezone.utc)
_SERVICE_USER = AuthenticatedUser(
    user_id="service",
    username="service",
    issued_at=_SERVICE_USER_ISSUED_AT,
    expires_at=_SERVICE_USER_ISSUED_AT + timedelta(days=365 * 10),
    claims={"service_auth": True},
)


def generate_user_token(user_id: str, username: str) -> Tuple[str, datetime]:
    """
    Create a JWT for the supplied user identifiers.
    Returns the token string and its UTC expiry timestamp.
    """
    now = datetime.now(tz=timezone.utc)
    expires_at = now + timedelta(minutes=JWT_EXPIRATION_MINUTES)
    payload = {
        "sub": str(user_id),
        "name": str(username),
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    return token, expires_at


def _decode_token(token: str) -> Dict[str, Any]:
    try:
        return jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Login expired, please sign in again.", status_code=401) from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Invalid login token.", status_code=401) from exc


def _build_user_from_claims(claims: Dict[str, Any]) -> AuthenticatedUser:
    user_id = str(claims.get("sub") or "")
    username = str(claims.get("name") or "")
    if not user_id or not username:
        raise AuthenticationError("Token is missing required user information.", status_code=401)

    issued_at = datetime.fromtimestamp(
        int(claims.get("iat", 0)), tz=timezone.utc
    ) if claims.get("iat") else datetime.now(tz=timezone.utc)
    expires_at = datetime.fromtimestamp(
        int(claims.get("exp", 0)), tz=timezone.utc
    ) if claims.get("exp") else issued_at + timedelta(minutes=JWT_EXPIRATION_MINUTES)

    return AuthenticatedUser(
        user_id=user_id,
        username=username,
        issued_at=issued_at,
        expires_at=expires_at,
        claims=claims,
    )


def get_token_from_request(req: Request) -> Optional[str]:
    """Extract JWT token from cookies or Authorization header."""
    cookie_token = req.cookies.get(JWT_COOKIE_NAME)
    if cookie_token:
        return cookie_token

    auth_header = req.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header.split(" ", 1)[1].strip() or None

    return None


def _is_service_request(req: Request) -> bool:
    if not SERVICE_AUTH_TOKEN:
        return False
    token = req.headers.get(SERVICE_AUTH_HEADER)
    return bool(token) and token == SERVICE_AUTH_TOKEN


def authenticate_request(req: Request) -> AuthenticatedUser:
    """Resolve the authenticated user from the incoming request or raise."""
    if _is_service_request(req):
        return _SERVICE_USER
    token = get_token_from_request(req)
    if not token:
        raise AuthenticationError("Missing login credentials. Please log in first.", status_code=401)
    claims = _decode_token(token)
    return _build_user_from_claims(claims)


__all__ = [
    "AuthenticatedUser",
    "AuthenticationError",
    "JWT_COOKIE_NAME",
    "JWT_COOKIE_SECURE",
    "JWT_COOKIE_SAMESITE",
    "JWT_EXPIRATION_MINUTES",
    "generate_user_token",
    "authenticate_request",
    "get_token_from_request",
]
