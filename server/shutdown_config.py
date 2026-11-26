"""进程停机时间预算：HTTP 请求排空与 Agent Run 收口。

uvicorn 的停机顺序是「等待请求任务结束 → lifespan.shutdown」。SSE 请求会持续到
Run 结束，请求排空必须有上限，否则 gunicorn 宽限期到点后进程被 SIGKILL，
lifespan 里的 Agent Run 收口不会执行。

两段时间之和必须明显小于 ``GUNICORN_GRACEFUL_TIMEOUT``（默认 30s）。
"""

from __future__ import annotations

# 等待在途 HTTP 请求（含 SSE）结束的上限，超时后 uvicorn 取消请求任务并进入 lifespan.shutdown。
REQUEST_DRAIN_TIMEOUT_SECONDS = 5
# lifespan.shutdown 内等待执行中 Run 收口（落 interrupted、发 done、释放 owner）的上限。
RUN_SHUTDOWN_DRAIN_SECONDS = 5
