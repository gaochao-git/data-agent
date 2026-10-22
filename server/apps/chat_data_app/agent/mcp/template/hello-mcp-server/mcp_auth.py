"""Bearer Token 鉴权（MCP SDK 原生 TokenVerifier + AuthSettings）。

单独成文件；server.py 只负责组装与启动。
"""

from __future__ import annotations

import hmac
import os

from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl


class StaticBearerTokenVerifier(TokenVerifier):
    """最小静态 token 校验。"""

    def __init__(self, expected_token: str) -> None:
        self._expected = expected_token

    async def verify_token(self, token: str) -> AccessToken | None:
        if not self._expected or not hmac.compare_digest(token, self._expected):
            return None
        return AccessToken(
            token=token,
            client_id="hello-mcp-server",
            scopes=[],
        )


def build_bearer_auth(token: str | None = None) -> tuple[AuthSettings, StaticBearerTokenVerifier]:
    """返回 MCP SDK 原生鉴权所需的 AuthSettings 与 TokenVerifier。"""
    expected = token if token is not None else os.getenv("MCP_AUTH_TOKEN", "")
    expected = expected.strip()
    # AuthSettings 要求两个非空 URL；静态 token 不校验资源，
    # 因此这里只是 MCP SDK 的占位值，不暴露给模板使用者配置。
    auth = AuthSettings(
        issuer_url=AnyHttpUrl("http://localhost"),
        resource_server_url=AnyHttpUrl("http://localhost"),
        validate_token_resource=False,
    )
    return auth, StaticBearerTokenVerifier(expected)


__all__ = ["StaticBearerTokenVerifier", "build_bearer_auth"]
