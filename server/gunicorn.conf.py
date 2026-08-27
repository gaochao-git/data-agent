"""Gunicorn configuration for production ASGI deployment."""

import os
import sys
from pathlib import Path

_SERVER_DIR = Path(__file__).resolve().parent
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

def _env_int(name: str, default: int) -> int:
    value = str(os.getenv(name) or "").strip()
    if not value:
        return default
    parsed = int(value)
    if parsed < 1:
        raise ValueError(f"{name} must be greater than 0")
    return parsed


chdir = str(_SERVER_DIR)
bind = f"0.0.0.0:{os.getenv('PORT', '5001')}"
backlog = _env_int("GUNICORN_BACKLOG", 2048)

workers = _env_int("GUNICORN_WORKERS", 4)
worker_class = "uvicorn_worker.UvicornWorker"
worker_connections = _env_int("GUNICORN_WORKER_CONNECTIONS", 1000)

# Agent, LLM, SQL, and sandbox requests can be long-running.
timeout = _env_int("GUNICORN_TIMEOUT", 900)
graceful_timeout = _env_int("GUNICORN_GRACEFUL_TIMEOUT", 30)
keepalive = _env_int("GUNICORN_KEEPALIVE", 5)

# Agent Run 在 worker 进程内以 asyncio task 长时间运行；按请求数回收 worker
# 会在长任务未完成时触发计划内中断。内存治理交给进程级监控和发布重启。
max_requests = 0
max_requests_jitter = 0
preload_app = False

accesslog = None
errorlog = "-"
loglevel = os.getenv("GUNICORN_LOG_LEVEL", "warning")

proc_name = os.getenv("GUNICORN_PROC_NAME", "data-agent")

limit_request_line = _env_int("GUNICORN_LIMIT_REQUEST_LINE", 4094)
limit_request_fields = _env_int("GUNICORN_LIMIT_REQUEST_FIELDS", 100)
limit_request_field_size = _env_int("GUNICORN_LIMIT_REQUEST_FIELD_SIZE", 8190)


def when_ready(server):
    server.log.info("Gunicorn ready: bind=%s workers=%s worker_class=%s", bind, workers, worker_class)


def post_fork(server, worker):
    server.log.info("Worker spawned: pid=%s", worker.pid)


def post_worker_init(worker):
    worker.log.info("Worker initialized: pid=%s", worker.pid)


def worker_int(worker):
    worker.log.info("Worker received INT or QUIT: pid=%s", worker.pid)


def worker_abort(worker):
    worker.log.info("Worker aborted: pid=%s", worker.pid)
