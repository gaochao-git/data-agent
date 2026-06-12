"""
SQLAlchemy 数据库引擎和会话管理

业务表统一使用 MySQL，通过 FXCJG_DB_* 环境变量配置
"""

import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession, AsyncEngine
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool

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
MYSQL_PORT = int(_get_env("FXCJG_DB_PORT"))
MYSQL_DB = _get_env("FXCJG_DB")
MYSQL_USER = _get_env("FXCJG_DB_USER")


def _get_mysql_password() -> str:
    """获取 MySQL 密码（SM4 解密）"""
    from utils.crypto import decrypt_password
    return decrypt_password(_get_env("FXCJG_DB_PASSWORD"))


# ============ 引擎创建 ============

def get_database_url() -> URL:
    """获取数据库连接 URL"""
    password = _get_mysql_password()
    return URL.create(
        drivername="mysql+aiomysql",
        username=MYSQL_USER,
        password=password,
        host=MYSQL_HOST,
        port=MYSQL_PORT,
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


# 全局引擎和会话工厂
_engine: AsyncEngine = None
_session_factory: async_sessionmaker[AsyncSession] = None


def get_engine() -> AsyncEngine:
    """获取数据库引擎（单例）"""
    global _engine
    if _engine is None:
        _engine = create_engine()
        logger.info(f"数据库引擎已创建: {DATABASE_TYPE}")
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
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_database():
    """
    初始化业务数据库引擎

    业务表统一使用 MySQL，表需手动创建。
    """
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
    return {
        "type": "mysql",
        "host": MYSQL_HOST,
        "port": MYSQL_PORT,
        "database": MYSQL_DB,
        "user": MYSQL_USER,
        "password": "(已设置)",
    }
