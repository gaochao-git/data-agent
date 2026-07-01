import os
import sys
from pathlib import Path
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from utils.lang_util.common_util import load_runtime_env

_SERVER_DIR = Path(__file__).resolve().parent
_RUNTIME_ENV = load_runtime_env(_SERVER_DIR)
print(
    f"[env] active_env={_RUNTIME_ENV.active_env} "
    f"loaded={','.join(_RUNTIME_ENV.loaded_files) or '-'} ips={','.join(_RUNTIME_ENV.local_ips) or '-'}"
)

from middleware.api_logging import APIAccessLogMiddleware
from utils.logger import get_logger
from apps.chat_data_app import router as chat_data_router  # noqa: E402
from utils.lang_util.conversation_router import router as conversation_router  # noqa: E402
from utils.lang_util.checkpoint import setup_checkpointer  # noqa: E402
from utils.file_service import file_router  # noqa: E402

os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1,dashscope.aliyuncs.com"
# 用 pysqlite3 替换 sqlite3，解决 deterministic=True 在旧 SQLite 上报错的问题
# pysqlite3-binary 自带新版 SQLite，aiosqlite 底层会使用被替换的 sqlite3 模块
try:
    import pysqlite3
    sys.modules["sqlite3"] = pysqlite3
except ImportError:
    pass  # macOS 等平台无法安装 pysqlite3-binary，使用系统自带的 sqlite3

logger = get_logger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    from utils.lang_util.database import close_database

    await setup_checkpointer()
    yield
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
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["Content-Type", "Authorization"],
)
app.add_middleware(APIAccessLogMiddleware)
app.include_router(chat_data_router)
app.include_router(conversation_router)
app.include_router(file_router)


@app.get("/healthz")
@app.get("/api/healthz")
def healthz():
    """健康检查。"""
    return {"status": "ok"}


def _build_uvicorn_log_config():
    try:
        from uvicorn.config import LOGGING_CONFIG as DEFAULT
    except Exception:
        return None
    fmt = os.getenv("UVICORN_ACCESS_FORMAT", "%(asctime)s %(levelname)s %(name)s - %(message)s")
    datefmt = os.getenv("UVICORN_ACCESS_DATEFMT", "%Y-%m-%d %H:%M:%S")
    config = {
        "version": DEFAULT.get("version", 1),
        "disable_existing_loggers": DEFAULT.get("disable_existing_loggers", False),
        "formatters": dict(DEFAULT.get("formatters", {})),
        "handlers": DEFAULT.get("handlers", {}),
        "loggers": DEFAULT.get("loggers", {}),
        "root": DEFAULT.get("root", {}),
    }
    access_fmt = dict(config["formatters"].get("access", {}))
    access_fmt["fmt"] = fmt
    access_fmt["datefmt"] = datefmt
    config["formatters"]["access"] = access_fmt
    return config


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    reload_enabled = _env_flag("FASTAPI_RELOAD", False)
    access_log_enabled = _env_flag("UVICORN_ACCESS_LOG", False)
    log_level = os.getenv("UVICORN_LOG_LEVEL", "info")
    target_app = "main:app" if reload_enabled else app
    logger.info("[startup] host=0.0.0.0 port=%s reload=%s access_log=%s log_level=%s", port, reload_enabled, access_log_enabled, log_level)
    uvicorn.run(
        target_app,
        host="0.0.0.0",
        port=port,
        reload=reload_enabled,
        access_log=access_log_enabled,
        log_level=log_level,
        log_config=_build_uvicorn_log_config(),
    )
