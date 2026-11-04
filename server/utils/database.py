"""
SQLAlchemy 数据库引擎和会话管理

业务表统一使用 MySQL，通过 FXCJG_DB_* 环境变量配置
"""

import asyncio
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from threading import RLock

from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from utils.alarm import DB_FAIL_CHATDATA, emit_database_connection_alarm
from utils.logger import get_logger

from .db_failover import DatabaseEndpoint, DatabaseEndpointSelector, is_mysql_connection_error, parse_database_endpoints

logger = get_logger(__name__)

# 当前事务持有的 ORM 会话：由 transaction_scope 绑定，事务内嵌套的 get_session /
# session_scope 复用同一会话，避免「事务里偷偷开第二个连接」导致原子性失效或嵌套锁死。
# 同时记录绑定时的 asyncio Task：ContextVar 会复制到子任务，子任务并发使用同一
# AsyncSession 会破坏会话状态，必须显式拒绝而不是悄悄串数据。
_ACTIVE_SESSION: ContextVar[AsyncSession | None] = ContextVar("chat_data_active_session", default=None)
_ACTIVE_SESSION_OWNER: ContextVar[object | None] = ContextVar("chat_data_active_session_owner", default=None)


def get_active_session() -> AsyncSession | None:
    """返回当前事务绑定中的会话；无事务时返回 None。"""
    return _ACTIVE_SESSION.get()


def bind_active_session(session: AsyncSession) -> tuple[object | None, object | None]:
    """把会话绑定到当前事务上下文，返回用于恢复的两个 token。"""
    return _ACTIVE_SESSION.set(session), _ACTIVE_SESSION_OWNER.set(asyncio.current_task())


def reset_active_session(tokens: tuple[object | None, object | None]) -> None:
    """事务结束时恢复上下文绑定。"""
    session_token, owner_token = tokens
    _ACTIVE_SESSION.reset(session_token)  # type: ignore[arg-type]
    _ACTIVE_SESSION_OWNER.reset(owner_token)  # type: ignore[arg-type]


# ============ 配置 ============


def _get_env(name: str) -> str:
    """读取业务库配置。"""
    value = os.getenv(name)
    if value:
        return value
    raise ValueError(f"缺少业务数据库配置: {name}")


DATABASE_TYPE = "mysql"

# MySQL 配置
MYSQL_HOST = _get_env("FXCJG_DB_HOST")
MYSQL_DB = _get_env("FXCJG_DB")
MYSQL_USER = _get_env("FXCJG_DB_USER")
MYSQL_ENDPOINTS = parse_database_endpoints(MYSQL_HOST)
_endpoint_selector = DatabaseEndpointSelector(MYSQL_ENDPOINTS)

# 业务库连接池按进程创建。当前生产默认 4 个 worker，因此总连接上限为
# 4 * (10 + 20)；Run、事件持久化和普通 API 共用这组连接。
DB_POOL_SIZE = 10
DB_MAX_OVERFLOW = 20
DB_POOL_TIMEOUT_SECONDS = 10
DB_POOL_RECYCLE_SECONDS = 1800


def _get_mysql_password() -> str:
    """获取 MySQL 密码（SM4 解密）"""
    from utils.crypto import decrypt_password

    return decrypt_password(_get_env("FXCJG_DB_PASSWORD"))


# ============ 引擎创建 ============


def get_database_url() -> URL:
    """获取数据库连接 URL"""
    password = _get_mysql_password()
    endpoint = _endpoint_selector.current()
    return URL.create(
        drivername="mysql+aiomysql",
        username=MYSQL_USER,
        password=password,
        host=endpoint.host,
        port=endpoint.port,
        database=MYSQL_DB,
        query={"charset": "utf8mb4"},
    )


def create_engine() -> AsyncEngine:
    """创建异步数据库引擎"""
    return create_async_engine(
        get_database_url(),
        pool_size=DB_POOL_SIZE,
        max_overflow=DB_MAX_OVERFLOW,
        pool_timeout=DB_POOL_TIMEOUT_SECONDS,
        pool_recycle=DB_POOL_RECYCLE_SECONDS,
        pool_pre_ping=True,
        echo=False,
    )


def _is_connection_error(err: Exception) -> bool:
    """判断异常是否表示当前数据库连接已失效。"""
    return is_mysql_connection_error(err)


async def _dispose_engine() -> None:
    """关闭当前数据库引擎并清空会话工厂。"""
    global _engine, _session_factory
    # 先摘除全局引用，再等待 dispose。等待期间新请求会创建并使用新引擎，
    # 不会和正在释放的旧连接池共享状态。
    with _engine_state_lock:
        engine = _engine
        _engine = None
        _session_factory = None
    if engine is not None:
        await engine.dispose()


async def _failover_database_endpoint(failed_endpoint: DatabaseEndpoint, reason: Exception | None = None) -> None:
    """切换业务库节点；当前请求不重试，由下一次请求使用新节点。"""
    previous, current, advanced = _endpoint_selector.advance_if_current(failed_endpoint)
    if not advanced:
        logger.warning(
            "业务库连接节点已由其他请求切换: failed=%s current=%s reason=%s",
            failed_endpoint.label(),
            current.label(),
            str(reason) if reason else "",
        )
        return
    await _dispose_engine()
    logger.warning(
        "业务库连接节点切换: %s -> %s, reason=%s",
        previous.label(),
        current.label(),
        str(reason) if reason else "",
    )


async def _probe_database_endpoint(endpoint: DatabaseEndpoint) -> bool:
    """探测单个业务库节点是否可用。"""
    password = _get_mysql_password()
    url = URL.create(
        drivername="mysql+aiomysql",
        username=MYSQL_USER,
        password=password,
        host=endpoint.host,
        port=endpoint.port,
        database=MYSQL_DB,
        query={"charset": "utf8mb4"},
    )
    engine = create_async_engine(
        url,
        poolclass=NullPool,
        pool_pre_ping=True,
        echo=False,
    )
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as err:
        if not _is_connection_error(err):
            logger.error("业务库节点探测遇到非连接类错误: %s, error=%s", endpoint.label(), err)
            raise
        logger.warning("业务库节点探测失败: %s, error=%s", endpoint.label(), err)
        return False
    finally:
        await engine.dispose()


async def select_available_database_endpoint() -> None:
    """启动时按顺序选择一个可用业务库节点。"""
    for endpoint in _endpoint_selector.endpoints:
        if await _probe_database_endpoint(endpoint):
            _endpoint_selector.set_current(endpoint)
            logger.info("业务库当前连接节点: %s", endpoint.label())
            return
    emit_database_connection_alarm(
        "业务库启动探测失败",
        reason_code=DB_FAIL_CHATDATA,
        ext1={"endpoints": _endpoint_selector.labels()},
    )
    raise ValueError("所有业务库节点均不可用")


# 全局引擎和会话工厂
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_engine_state_lock = RLock()


def get_engine() -> AsyncEngine:
    """获取数据库引擎（单例）"""
    global _engine
    with _engine_state_lock:
        if _engine is None:
            _engine = create_engine()
        return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """获取会话工厂（单例）"""
    global _session_factory
    with _engine_state_lock:
        if _session_factory is None:
            _session_factory = async_sessionmaker(
                bind=get_engine(),
                class_=AsyncSession,
                expire_on_commit=False,
            )
        return _session_factory


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """获取数据库会话（上下文管理器）。

    事务上下文内（transaction_scope 已绑定会话）直接复用同一会话，不再新开连接；
    若在事务的子 asyncio 任务中并发调用则拒绝，避免多个协程共享 AsyncSession。
    """
    active_session = _ACTIVE_SESSION.get()
    if active_session is not None:
        owner = _ACTIVE_SESSION_OWNER.get()
        current_task = asyncio.current_task()
        if owner is not None and current_task is not None and owner is not current_task:
            raise RuntimeError("事务内禁止并发使用同一数据库会话：请在事务外并发，或让子任务自行开启会话")
        yield active_session
        return

    endpoint = _endpoint_selector.current()
    factory = get_session_factory()
    async with factory() as session:
        # 每个 get_session 块即一个工作单元：块内嵌套的 get_session / session_scope /
        # transaction_scope 都复用同一会话与事务，避免「事务里再开第二个连接」。
        tokens = bind_active_session(session)
        try:
            yield session
            await session.commit()
        except Exception as err:
            should_failover = _is_connection_error(err)
            try:
                await session.rollback()
            except Exception as rollback_err:
                should_failover = should_failover or _is_connection_error(rollback_err)
                logger.warning("数据库会话 rollback 失败: %s", rollback_err)
            if should_failover:
                await _failover_database_endpoint(endpoint, err)
            raise
        finally:
            reset_active_session(tokens)


async def init_database() -> None:
    """
    初始化业务数据库引擎

    业务表统一使用 MySQL，表需手动创建。
    """
    await select_available_database_endpoint()
    get_engine()


async def close_database() -> None:
    """关闭数据库连接"""
    with _engine_state_lock:
        has_engine = _engine is not None
    if has_engine:
        await _dispose_engine()
        logger.info("数据库连接已关闭")


def get_database_info() -> dict:
    """获取数据库配置信息"""
    endpoint = _endpoint_selector.current()
    return {
        "type": "mysql",
        "host": endpoint.host,
        "port": endpoint.port,
        "endpoints": _endpoint_selector.labels(),
        "database": MYSQL_DB,
        "user": MYSQL_USER,
        "password": "(已设置)",
    }
