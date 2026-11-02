import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from utils.common_util import load_runtime_env, merge_no_proxy

_SERVER_DIR = Path(__file__).resolve().parent
_RUNTIME_ENV = load_runtime_env(_SERVER_DIR)
# 运行环境信息等 lifespan 中日志初始化完成后再记录，避免 import 期 print 绕过
# 统一格式/脱敏，也避免多 worker 时 stdout 与文件日志重复。
# 必须在导入应用模块前合并：部分模块导入期就会创建 HTTP 客户端/读取代理配置。
merge_no_proxy()

from apps.chat_data_app import router as chat_data_router  # noqa: E402
from apps.chat_data_app.agent.context.context_budget import validate_context_budget_startup_config  # noqa: E402
from apps.chat_data_app.agent.run.agent_run_runtime import agent_run_runtime  # noqa: E402
from apps.chat_data_app.agent.run.agent_schedule_runtime import agent_schedule_runtime  # noqa: E402
from middleware.api_logging import APIAccessLogMiddleware
from utils.checkpoint import probe_checkpoint_health, setup_checkpointer
from utils.database import get_engine  # noqa: E402
from utils.logger import get_logger
from utils.response import chatdata_http_exception_handler, chatdata_validation_exception_handler
from utils.sqlite_compat import apply_sqlite_compat  # noqa: E402

# 用 pysqlite3 替换 sqlite3，解决 deterministic=True 在旧编译 Python 上报错的问题
apply_sqlite_compat()

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    from apps.chat_data_app.services.llm_config import refresh_registry_cache
    from apps.chat_data_app.services.sql_run.runtime import interrupt_stale_query_tasks
    from utils.database import close_database
    from utils.logger import LoggerManager, configure_third_party_loggers

    # 进程内重启（reload/测试）时恢复上次被 stop 的异步日志 listener，
    # 然后再接管第三方 logger，避免已有 QueueHandler 指向已停止的队列。
    LoggerManager.ensure_async_listeners()
    configure_third_party_loggers()
    logger.info(
        "运行环境加载完成: env=%s loaded=%s ips=%s",
        _RUNTIME_ENV.active_env,
        ",".join(_RUNTIME_ENV.loaded_files) or "-",
        ",".join(_RUNTIME_ENV.local_ips) or "-",
    )
    schedule_started = False
    run_started = False
    try:
        # 模型注册表启动预热必须最先执行：DB 是唯一真值，后续 context budget 校验
        # 等启动逻辑都要读注册表；预热失败时服务路径明确报错而非回落配置文件
        await refresh_registry_cache()
        try:
            await interrupt_stale_query_tasks()
        except Exception:
            # 对账失败意味着任务状态无法正确收口，保持 fail-fast，但补充明确上下文。
            logger.error(
                "启动对账失败：查询任务状态无法收口，拒绝启动；"
                "请检查 chat_data_query_task/query_execution 表与数据库连接",
                exc_info=True,
            )
            raise
        validate_context_budget_startup_config()
        await setup_checkpointer()
        await agent_run_runtime.start()
        run_started = True
        await agent_schedule_runtime.start()
        schedule_started = True
        yield
    finally:
        # 启动失败/正常退出都按相反顺序收口，单步失败不阻断后续清理。
        if schedule_started:
            try:
                await agent_schedule_runtime.stop()
            except Exception:
                logger.warning("agent_schedule_runtime 停止失败", exc_info=True)
        if run_started:
            try:
                await agent_run_runtime.stop()
            except Exception:
                logger.warning("agent_run_runtime 停止失败", exc_info=True)
        LoggerManager.stop_async_listeners()
        try:
            await close_database()
        except Exception:
            logger.warning("数据库关闭失败", exc_info=True)


def _env_flag(key: str, default: bool = True) -> bool:
    """Parse boolean-like env flags such as 'true', '1', 'yes'."""
    value = os.getenv(key)
    if value is None:
        return default
    trimmed = value.strip()
    if not trimmed:
        return default
    return trimmed.lower() in {"1", "true", "yes", "y", "on"}


app = FastAPI(title="data-agent", lifespan=lifespan)
# Starlette 的 handler 泛型签名比实际注册粒度宽（按异常类分发后入参必然匹配），框架类型噪音
app.add_exception_handler(StarletteHTTPException, chatdata_http_exception_handler)  # type: ignore[arg-type]
app.add_exception_handler(RequestValidationError, chatdata_validation_exception_handler)  # type: ignore[arg-type]
# add_middleware 后加的在外层：CORS 必须最外层，保证异常/错误响应也带 CORS 头。
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(APIAccessLogMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["Content-Type", "Authorization", "X-Service-Token", "X-User-Id", "X-Username", "Last-Event-ID"],
    expose_headers=["X-ChatData-Run-Id"],
)
app.include_router(chat_data_router)


@app.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    """存活探针：只看进程是否可响应，不检查外部依赖。"""
    return {"status": "ok"}


_ready_checkpoint_cache: tuple[float, bool] | None = None


async def _checkpoint_ready() -> bool:
    """checkpoint 健康检查带 5 秒缓存，避免探针高频建连。"""
    global _ready_checkpoint_cache
    now = time.monotonic()
    if _ready_checkpoint_cache is not None and now - _ready_checkpoint_cache[0] < 5:
        return _ready_checkpoint_cache[1]
    healthy = await probe_checkpoint_health()
    _ready_checkpoint_cache = (now, healthy)
    return healthy


@app.get("/ready", include_in_schema=False)
async def ready() -> JSONResponse:
    """就绪探针：同时检查业务库与 checkpoint 库（checkpoint 结果缓存 5 秒）。"""
    database_healthy = True
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        database_healthy = False
        logger.error("就绪探针失败：业务数据库不可用", exc_info=True)
    checkpoint_healthy = await _checkpoint_ready()
    if not database_healthy or not checkpoint_healthy:
        return JSONResponse(
            {
                "status": "unavailable",
                "database": "ok" if database_healthy else "unavailable",
                "checkpoint": "ok" if checkpoint_healthy else "unavailable",
            },
            status_code=503,
        )
    return JSONResponse({"status": "ok"})


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    reload_enabled = _env_flag("FASTAPI_RELOAD", False)
    target_app = "main:app" if reload_enabled else app
    logger.info("[startup] host=0.0.0.0 port=%s reload=%s", port, reload_enabled)
    uvicorn.run(target_app, host="0.0.0.0", port=port, reload=reload_enabled, access_log=False, log_level="warning")
