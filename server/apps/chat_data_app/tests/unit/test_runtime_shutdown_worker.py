"""停机 worker 配置回归：请求排空与 Run 收口必须先于 gunicorn 宽限期结束。"""

from chatdata_uvicorn_worker import ChatDataUvicornWorker
from shutdown_config import REQUEST_DRAIN_TIMEOUT_SECONDS, RUN_SHUTDOWN_DRAIN_SECONDS


def test_worker_sets_bounded_request_drain_before_lifespan_shutdown():
    assert ChatDataUvicornWorker.CONFIG_KWARGS["timeout_graceful_shutdown"] == REQUEST_DRAIN_TIMEOUT_SECONDS
    assert 0 < REQUEST_DRAIN_TIMEOUT_SECONDS < 30


def test_shutdown_budget_fits_default_gunicorn_grace():
    """请求排空 + Run 收口必须明显小于 gunicorn 默认 30s 宽限期。"""
    assert 0 < RUN_SHUTDOWN_DRAIN_SECONDS < 30
    assert REQUEST_DRAIN_TIMEOUT_SECONDS + RUN_SHUTDOWN_DRAIN_SECONDS < 30
