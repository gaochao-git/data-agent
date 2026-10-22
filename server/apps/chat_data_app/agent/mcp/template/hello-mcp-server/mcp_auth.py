"""Bearer Token 鉴权（MCP SDK 原生 TokenVerifier + AuthSettings）。

单独成文件，便于替换为 OAuth/JWT；server.py 只负责组装与启动。
"""

from __future__ import annotations

import hmac
import os

from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl

DEFAULT_RESOURCE_URL = "http://127.0.0.1:8099"


class StaticBearerTokenVerifier(TokenVerifier):
    """最小静态 token 校验；生产可替换为 OAuth/JWT verifier。"""

    def __init__(self, expected_token: str) -> None:
        self._expected = expected_token

    async def verify_token(self, token: str) -> AccessToken | None:
        if not self._expected or not hmac.compare_digest(token, self._expected):
            return None
        return AccessToken(
            token=token,
            client_id="hello-mcp-server",
            scopes=[],
            resource=os.getenv("MCP_RESOURCE_URL", DEFAULT_RESOURCE_URL).rstrip("/"),
        )


def build_bearer_auth(token: str | None = None) -> tuple[AuthSettings, StaticBearerTokenVerifier]:
    """返回 MCP SDK 原生鉴权所需的 AuthSettings 与 TokenVerifier。"""
    expected = token if token is not None else os.getenv("MCP_AUTH_TOKEN", "")
    expected = expected.strip()
    resource = os.getenv("MCP_RESOURCE_URL", DEFAULT_RESOURCE_URL).rstrip("/")
    auth = AuthSettings(
        issuer_url=AnyHttpUrl(resource),
        resource_server_url=AnyHttpUrl(resource),
        validate_token_resource=False,
    )
    return auth, StaticBearerTokenVerifier(expected)


__all__ = ["StaticBearerTokenVerifier", "build_bearer_auth"]
