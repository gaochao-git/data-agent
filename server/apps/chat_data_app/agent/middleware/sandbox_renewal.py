"""模型调用边界的 OpenSandbox 续期 middleware。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import ModelCallResult, ModelRequest, wrap_model_call


def create_sandbox_renewal_middleware(renewal: Callable[[], Awaitable[None]]) -> Any:
    """每次模型调用前、返回后各续期一次，覆盖模型思考与紧随其后的工具执行。"""

    @wrap_model_call(name="SandboxRenewalMiddleware")
    async def sandbox_renewal_middleware(
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelCallResult]],
    ) -> ModelCallResult:
        await renewal()
        try:
            return await handler(request)
        finally:
            await renewal()

    return sandbox_renewal_middleware
