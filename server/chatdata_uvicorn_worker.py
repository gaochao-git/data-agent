"""ChatData 使用的 Uvicorn Worker：为长连接请求设置有界排空时间。"""

from __future__ import annotations

from uvicorn_worker import UvicornWorker

from shutdown_config import REQUEST_DRAIN_TIMEOUT_SECONDS


class ChatDataUvicornWorker(UvicornWorker):
    CONFIG_KWARGS = {
        **UvicornWorker.CONFIG_KWARGS,
        "timeout_graceful_shutdown": REQUEST_DRAIN_TIMEOUT_SECONDS,
    }
