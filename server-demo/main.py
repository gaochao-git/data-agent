import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_SERVER_ENV_FILE = Path(__file__).resolve().parent / ".env"
if _SERVER_ENV_FILE.exists():
    if load_dotenv(dotenv_path=_SERVER_ENV_FILE):
        print(f"[env] Loaded environment from {_SERVER_ENV_FILE}")
    else:
        print(f"[env] Failed to load environment from {_SERVER_ENV_FILE}")

if load_dotenv():
    print("[env] Loaded environment from default search paths")

os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1,dashscope.aliyuncs.com"

import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from middleware.api_logging import APIAccessLogMiddleware
from utils.logger import get_logger
# 用 pysqlite3 替换 sqlite3，解决 deterministic=True 在旧 SQLite 上报错的问题
# pysqlite3-binary 自带新版 SQLite，aiosqlite 底层会使用被替换的 sqlite3 模块
try:
    import pysqlite3
    sys.modules["sqlite3"] = pysqlite3
except ImportError:
    pass  # macOS 等平台无法安装 pysqlite3-binary，使用系统自带的 sqlite3

logger = get_logger(__name__)

from auth import (  # noqa: E402
    JWT_COOKIE_NAME,
    JWT_COOKIE_SAMESITE,
    JWT_COOKIE_SECURE,
    JWT_EXPIRATION_MINUTES,
    generate_user_token,
)
from apps.inspection_app import violation_router  # noqa: E402
from apps.specialist_app.routes import router as specialist_router  # noqa: E402
from apps.chat_data_app import router as chat_data_router  # noqa: E402
#from apps.word_fit_app import router as word_fit_router  # noqa: E402
from apps.chat_assistant import router as chat_assistant_router  # noqa: E402
import apps.general_app  # noqa: E402, F401  # 注册 general agent
from utils.lang_util.conversation_router import router as conversation_router  # noqa: E402
from utils.lang_util.checkpoint import setup_checkpointer  # noqa: E402
from utils.file_service import file_router  # noqa: E402
#from routes.chat_routes import router as chat_router  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    from utils.lang_util.database import close_database

    await setup_checkpointer()
    #AgentRegistry.registry()
    #ToolRegistry.registry()
    try:
        models = load_models_info()
        logger.info("[startup] preloaded models_info count=%s", len(models))
    except Exception as exc:
        logger.warning("[startup] preload models_info failed: %s", exc)
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


app = FastAPI(title="FXCJG FastAPI (experimental)", lifespan=lifespan)

ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000,http://192.168.*:3000,http://10.*:3000,http://172.*:3000",
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["Content-Type", "Authorization"],
)
app.add_middleware(APIAccessLogMiddleware)
app.include_router(violation_router)
app.include_router(specialist_router)
app.include_router(chat_data_router)
#app.include_router(word_fit_router)
app.include_router(chat_assistant_router)
app.include_router(conversation_router)
app.include_router(file_router)
#app.include_router(chat_router)


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


@app.get("/api/inner_login")
def inner_login(userid: str = "", username: str = ""):
    """Issue a long-lived JWT token and set it as an HTTP-only cookie."""
    user_id = (userid or "").strip()
    user_name = (username or "").strip()

    if not user_id or not user_name:
        return JSONResponse({"error": "userid 和 username 为必填参数"}, status_code=400)

    token, expires_at = generate_user_token(user_id, user_name)
    response = JSONResponse(
        {
            "message": "ok",
            "user_id": user_id,
            "user_name": user_name,
            "expires_at": expires_at.isoformat(),
        }
    )
    response.set_cookie(
        JWT_COOKIE_NAME,
        token,
        httponly=True,
        secure=JWT_COOKIE_SECURE,
        samesite=JWT_COOKIE_SAMESITE,
        max_age=JWT_EXPIRATION_MINUTES * 60,
        expires=expires_at,
        path="/",
    )
    return response


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5002))
    reload_enabled = _env_flag("FASTAPI_RELOAD", False)
    access_log_enabled = _env_flag("UVICORN_ACCESS_LOG", True)
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
