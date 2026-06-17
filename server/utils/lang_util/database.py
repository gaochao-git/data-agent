"""
SQLAlchemy 数据库引擎和会话管理

业务表统一使用 MySQL，通过 FXCJG_DB_* 环境变量配置
"""

import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession, AsyncEngine
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool

from .db_failover import DatabaseEndpointSelector, is_mysql_connection_error, parse_database_endpoints
from utils.logger import get_logger

logger = get_logger(__name__)

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
MYSQL_PORT = int(os.getenv("FXCJG_DB_PORT", "3306"))
MYSQL_DB = _get_env("FXCJG_DB")
MYSQL_USER = _get_env("FXCJG_DB_USER")
MYSQL_ENDPOINTS = parse_database_endpoints(MYSQL_HOST, MYSQL_PORT)
_endpoint_selector = DatabaseEndpointSelector(MYSQL_ENDPOINTS)


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
        poolclass=NullPool,
        pool_pre_ping=True,
        echo=False,
    )


def _is_connection_error(err: Exception) -> bool:
    """判断异常是否表示当前数据库连接已失效。"""
    return is_mysql_connection_error(err)


async def _dispose_engine() -> None:
    """关闭当前数据库引擎并清空会话工厂。"""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


async def _failover_database_endpoint(failed_endpoint, reason: Exception | None = None) -> None:
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


async def _probe_database_endpoint(endpoint) -> bool:
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
    raise ValueError("所有业务库节点均不可用")


# 全局引擎和会话工厂
_engine: AsyncEngine = None
_session_factory: async_sessionmaker[AsyncSession] = None


def get_engine() -> AsyncEngine:
    """获取数据库引擎（单例）"""
    global _engine
    if _engine is None:
        _engine = create_engine()
        logger.info("数据库引擎已创建: %s endpoint=%s", DATABASE_TYPE, _endpoint_selector.current().label())
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """获取会话工厂（单例）"""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """获取数据库会话（上下文管理器）"""
    endpoint = _endpoint_selector.current()
    factory = get_session_factory()
    async with factory() as session:
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


async def init_database():
    """
    初始化业务数据库引擎

    业务表统一使用 MySQL，表需手动创建。
    """
    await select_available_database_endpoint()
    get_engine()
    logger.info("mysql 数据库引擎初始化完成（表需手动创建）")


async def close_database():
    """关闭数据库连接"""
    global _engine, _session_factory
    if _engine:
        await _engine.dispose()
        _engine = None
        _session_factory = None
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
