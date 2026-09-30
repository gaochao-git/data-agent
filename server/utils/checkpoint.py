"""
Checkpoint 管理模块
提供 LangGraph checkpointer 的配置和初始化

支持的后端：
- sqlite (默认): 本地 SQLite 数据库
- mysql: MySQL 数据库

通过环境变量配置：
- CHECKPOINTER_TYPE: sqlite | mysql (默认 sqlite)

SQLite 配置：
- 使用 CHAT_DATA_HOME/checkpoints.db

MySQL 配置：
- CHECKPOINTER_HOST: 数据库节点，格式 host:port，多个节点用逗号分隔
- CHECKPOINTER_DB: 数据库名
- CHECKPOINTER_USER: 用户名
- CHECKPOINTER_PASSWORD: 密码
"""

import os
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from types import TracebackType
from typing import TYPE_CHECKING

from utils.alarm import DB_FAIL_CHECKPOINT, emit_database_connection_alarm
from utils.logger import get_logger
from utils.storage_paths import CHAT_DATA_HOME

from .db_failover import DatabaseEndpoint, DatabaseEndpointSelector, is_mysql_connection_error, parse_database_endpoints

if TYPE_CHECKING:
    import aiosqlite
    from langgraph.checkpoint.mysql.aio import AIOMySQLSaver
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

logger = get_logger(__name__)

# ============ 配置 ============

CHECKPOINTER_TYPE = os.getenv("CHECKPOINTER_TYPE", "sqlite").lower()

# SQLite 配置
DB_PATH = os.path.join(CHAT_DATA_HOME, "checkpoints.db")

# MySQL 配置
MYSQL_HOST = os.getenv("CHECKPOINTER_HOST", "")
MYSQL_DB = os.getenv("CHECKPOINTER_DB", "langgraph_memory")
MYSQL_USER = os.getenv("CHECKPOINTER_USER", "root")
MYSQL_ENDPOINTS = parse_database_endpoints(MYSQL_HOST) if CHECKPOINTER_TYPE == "mysql" else []
_endpoint_selector = DatabaseEndpointSelector(MYSQL_ENDPOINTS) if MYSQL_ENDPOINTS else None


def _get_mysql_password() -> str:
    """获取 MySQL 密码（SM4 解密）"""
    from utils.crypto import decrypt_password

    return decrypt_password(os.getenv("CHECKPOINTER_PASSWORD", ""))


# ============ Checkpointer 工厂 ============


def get_checkpointer() -> AbstractAsyncContextManager:
    """
    获取异步 checkpointer 上下文管理器

    根据 CHECKPOINTER_TYPE 环境变量返回对应的 checkpointer：
    - sqlite: AsyncSqliteSaver
    - mysql: AIOMySQLSaver

    使用方式：
        async with get_checkpointer() as checkpointer:
            graph = get_graph(checkpointer)
            await graph.ainvoke(...)
    """
    if CHECKPOINTER_TYPE == "sqlite":
        return _get_sqlite_checkpointer()
    elif CHECKPOINTER_TYPE == "mysql":
        return _get_mysql_checkpointer()
    else:
        raise ValueError(f"不支持的 CHECKPOINTER_TYPE: {CHECKPOINTER_TYPE}")


class _SqliteCheckpointerContext:
    """
    SQLite Checkpointer 上下文管理器

    解决 langgraph-checkpoint-sqlite 3.0.0 的兼容性问题：
    aiosqlite 连接对象没有 is_alive 方法
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.conn: "aiosqlite.Connection | None" = None
        self.checkpointer: "AsyncSqliteSaver | None" = None

    async def __aenter__(self) -> "AsyncSqliteSaver":
        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        conn = await aiosqlite.connect(self.db_path)
        # 添加 is_alive 方法，解决 langgraph-checkpoint-sqlite 3.0.0 兼容性问题
        conn.is_alive = lambda: True  # type: ignore[method-assign]
        self.conn = conn
        checkpointer = AsyncSqliteSaver(conn)
        self.checkpointer = checkpointer
        return checkpointer

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        if self.conn:
            await self.conn.close()
        return False


def _get_sqlite_checkpointer() -> _SqliteCheckpointerContext:
    """获取 SQLite checkpointer"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return _SqliteCheckpointerContext(DB_PATH)


class _MySQLCheckpointerFailoverContext:
    """Wrap MySQL checkpointer contexts and retry connection-class failures."""

    def __init__(self, context_factory: Callable[[DatabaseEndpoint], AbstractAsyncContextManager]) -> None:
        self.context_factory = context_factory
        self.context: AbstractAsyncContextManager | None = None
        self.endpoint: DatabaseEndpoint | None = None

    async def __aenter__(self) -> "AIOMySQLSaver":
        if _endpoint_selector is None:
            raise RuntimeError("MySQL checkpointer 未配置可用节点")
        last_error = None
        for _ in range(len(_endpoint_selector.endpoints)):
            endpoint = _endpoint_selector.current()
            context = self.context_factory(endpoint)
            try:
                checkpointer = await context.__aenter__()
                self.context = context
                self.endpoint = endpoint
                return checkpointer
            except Exception as err:
                last_error = err
                if not is_mysql_connection_error(err):
                    raise
                _advance_checkpointer_endpoint(endpoint, err)
        if last_error is not None:
            raise last_error
        raise RuntimeError("MySQL checkpointer 未配置可用节点")

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        if self.context is None:
            return False
        try:
            return await self.context.__aexit__(exc_type, exc_val, exc_tb)
        finally:
            if self.endpoint is not None and exc_val is not None and is_mysql_connection_error(exc_val):
                _advance_checkpointer_endpoint(self.endpoint, exc_val)


def _advance_checkpointer_endpoint(failed_endpoint: DatabaseEndpoint, reason: Exception) -> None:
    if _endpoint_selector is None:
        raise ValueError("MySQL checkpointer 需要配置 CHECKPOINTER_HOST，格式为 host:port")
    previous, current, advanced = _endpoint_selector.advance_if_current(failed_endpoint)
    if not advanced:
        logger.warning(
            "MySQL checkpointer 连接节点已由其他请求切换: failed=%s current=%s reason=%s",
            failed_endpoint.label(),
            current.label(),
            reason,
        )
        return
    logger.warning(
        "MySQL checkpointer 连接节点切换: %s -> %s, reason=%s",
        previous.label(),
        current.label(),
        reason,
    )


def _get_mysql_checkpointer() -> _MySQLCheckpointerFailoverContext:
    """获取 MySQL checkpointer"""
    return _MySQLCheckpointerFailoverContext(_build_mysql_checkpointer_context_factory())


def _build_mysql_checkpointer_context_factory() -> Callable[[DatabaseEndpoint], AbstractAsyncContextManager]:
    """构造 MySQL checkpointer 上下文工厂。"""
    try:
        from langgraph.checkpoint.mysql.aio import AIOMySQLSaver
    except ImportError:
        raise ImportError("使用 MySQL checkpointer 需要安装: pip install langgraph-checkpoint-mysql aiomysql") from None

    password = _get_mysql_password()
    if _endpoint_selector is None:
        raise ValueError("MySQL checkpointer 需要配置 CHECKPOINTER_HOST，格式为 host:port")

    def context_factory(endpoint: DatabaseEndpoint) -> AbstractAsyncContextManager:
        conn_string = f"mysql+aiomysql://{MYSQL_USER}:{password}@{endpoint.host}:{endpoint.port}/{MYSQL_DB}"
        return AIOMySQLSaver.from_conn_string(conn_string)

    return context_factory


async def _probe_mysql_checkpointer_endpoint(
    endpoint: DatabaseEndpoint, context_factory: Callable[[DatabaseEndpoint], AbstractAsyncContextManager]
) -> bool:
    """探测单个 checkpoint 库节点是否可用。"""
    try:
        async with context_factory(endpoint):
            pass
        return True
    except Exception as err:
        if not is_mysql_connection_error(err):
            logger.error("checkpoint库节点探测遇到非连接类错误: %s, error=%s", endpoint.label(), err)
            raise
        logger.warning("checkpoint库节点探测失败: %s, error=%s", endpoint.label(), err)
        return False


async def select_available_checkpointer_endpoint() -> None:
    """启动时按顺序选择一个可用 checkpoint 库节点。"""
    if _endpoint_selector is None:
        raise ValueError("MySQL checkpointer 需要配置 CHECKPOINTER_HOST，格式为 host:port")
    context_factory = _build_mysql_checkpointer_context_factory()
    for endpoint in _endpoint_selector.endpoints:
        if await _probe_mysql_checkpointer_endpoint(endpoint, context_factory):
            _endpoint_selector.set_current(endpoint)
            logger.info("checkpoint库当前连接节点: %s", endpoint.label())
            return
    emit_database_connection_alarm(
        "checkpoint库启动探测失败",
        reason_code=DB_FAIL_CHECKPOINT,
        ext1={"endpoints": _endpoint_selector.labels()},
    )
    raise ValueError("所有 checkpoint 库节点均不可用")


# ============ 初始化函数 ============


async def setup_checkpointer() -> None:
    """
    初始化 checkpointer 和业务表

    - SQLite: 启用 WAL 模式提高并发性能
    - MySQL: 启动时探测可用 checkpoint 库节点
    - 所有类型: 使用 SQLAlchemy 创建业务表

    建议在应用启动时调用一次。
    """
    from .database import init_database

    if CHECKPOINTER_TYPE == "sqlite":
        # SQLite: 先启用 WAL 模式（持久化到数据库文件）
        import aiosqlite

        async with aiosqlite.connect(DB_PATH) as conn:
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.commit()
        logger.info("SQLite WAL 模式已启用")
    elif CHECKPOINTER_TYPE == "mysql":
        await select_available_checkpointer_endpoint()

    # 使用 SQLAlchemy 初始化业务表
    await init_database()


# ============ 工具函数 ============


def get_checkpoint_info() -> dict:
    """获取当前 checkpoint 配置信息"""
    if CHECKPOINTER_TYPE == "sqlite":
        return {
            "type": "sqlite",
            "path": DB_PATH,
        }
    elif CHECKPOINTER_TYPE == "mysql":
        if _endpoint_selector is None:
            return {
                "type": "mysql",
                "host": "",
                "port": None,
                "endpoints": [],
                "database": MYSQL_DB,
                "user": MYSQL_USER,
                "password": "(已设置)",
            }
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
    else:
        return {"type": CHECKPOINTER_TYPE}
