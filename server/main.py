import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from utils.common_util import load_runtime_env

_SERVER_DIR = Path(__file__).resolve().parent
_RUNTIME_ENV = load_runtime_env(_SERVER_DIR)
print(
    f"env={_RUNTIME_ENV.active_env} loaded={','.join(_RUNTIME_ENV.loaded_files) or '-'} ips={','.join(_RUNTIME_ENV.local_ips) or '-'}"
)

from apps.chat_data_app import router as chat_data_router  # noqa: E402
from apps.chat_data_app.agent.context.context_budget import validate_context_budget_startup_config  # noqa: E402
from apps.chat_data_app.agent.run.agent_run_runtime import agent_run_runtime  # noqa: E402
from apps.chat_data_app.agent.run.agent_schedule_runtime import agent_schedule_runtime  # noqa: E402
from middleware.api_logging import APIAccessLogMiddleware
from utils.checkpoint import setup_checkpointer  # noqa: E402
from utils.logger import get_logger
from utils.response import chatdata_http_exception_handler, chatdata_validation_exception_handler
from utils.sqlite_compat import apply_sqlite_compat  # noqa: E402

os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1,dashscope.aliyuncs.com"
# 用 pysqlite3 替换 sqlite3，解决 deterministic=True 在旧编译 Python 上报错的问题
apply_sqlite_compat()

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    from apps.chat_data_app.services.llm_config import refresh_registry_cache
    from utils.database import close_database

    # 模型注册表启动预热必须最先执行：DB 是唯一真值，后续 context budget 校验
    # 等启动逻辑都要读注册表；预热失败时服务路径明确报错而非回落配置文件
    await refresh_registry_cache()
    validate_context_budget_startup_config()
    await setup_checkpointer()
    await agent_run_runtime.start()
    await agent_schedule_runtime.start()
    yield
    await agent_schedule_runtime.stop()
    await agent_run_runtime.stop()
    await close_database()


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
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["Content-Type", "Authorization", "X-Service-Token", "X-User-Id", "X-Username", "Last-Event-ID"],
    expose_headers=["X-ChatData-Run-Id"],
)
app.add_middleware(APIAccessLogMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.include_router(chat_data_router)


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    reload_enabled = _env_flag("FASTAPI_RELOAD", False)
    target_app = "main:app" if reload_enabled else app
    logger.info("[startup] host=0.0.0.0 port=%s reload=%s", port, reload_enabled)
    uvicorn.run(target_app, host="0.0.0.0", port=port, reload=reload_enabled, access_log=False, log_level="warning")
