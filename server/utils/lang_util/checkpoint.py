"""
Checkpoint 管理模块
提供 LangGraph checkpointer 的配置和初始化

支持的后端：
- sqlite (默认): 本地 SQLite 数据库
- mysql: MySQL 数据库

通过环境变量配置：
- CHECKPOINTER_TYPE: sqlite | mysql (默认 sqlite)

SQLite 配置：
- 使用 DATA_ROOT/checkpoints.db

MySQL 配置：
- CHECKPOINTER_HOST: 数据库主机
- CHECKPOINTER_PORT: 数据库端口
- CHECKPOINTER_DB: 数据库名
- CHECKPOINTER_USER: 用户名
- CHECKPOINTER_PASSWORD: 密码
"""

import os

from .db_failover import DatabaseEndpointSelector, is_mysql_connection_error, parse_database_endpoints
from utils.logger import get_logger
from utils.storage_paths import DATA_ROOT

logger = get_logger(__name__)

# ============ 配置 ============

CHECKPOINTER_TYPE = os.getenv("CHECKPOINTER_TYPE", "sqlite").lower()

# SQLite 配置
DB_PATH = os.path.join(DATA_ROOT, "checkpoints.db")

# MySQL 配置
MYSQL_PORT = int(os.getenv("CHECKPOINTER_PORT", "3306"))
MYSQL_HOST = os.getenv("CHECKPOINTER_HOST") or f"localhost:{MYSQL_PORT}"
MYSQL_DB = os.getenv("CHECKPOINTER_DB", "langgraph_memory")
MYSQL_USER = os.getenv("CHECKPOINTER_USER", "root")
MYSQL_ENDPOINTS = parse_database_endpoints(MYSQL_HOST, MYSQL_PORT)
_endpoint_selector = DatabaseEndpointSelector(MYSQL_ENDPOINTS)


def _get_mysql_password() -> str:
    """获取 MySQL 密码（SM4 解密）"""
    from utils.crypto import decrypt_password
    return decrypt_password(os.getenv("CHECKPOINTER_PASSWORD", ""))


# ============ Checkpointer 工厂 ============

def get_checkpointer():
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
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = None
        self.checkpointer = None

    async def __aenter__(self):
        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        self.conn = await aiosqlite.connect(self.db_path)
        # 添加 is_alive 方法，解决兼容性问题
        self.conn.is_alive = lambda: True
        self.checkpointer = AsyncSqliteSaver(self.conn)
        return self.checkpointer

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            await self.conn.close()
        return False


def _get_sqlite_checkpointer():
    """获取 SQLite checkpointer"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return _SqliteCheckpointerContext(DB_PATH)


class _MySQLCheckpointerFailoverContext:
    """Wrap MySQL checkpointer contexts to advance endpoint after connection failure."""

    def __init__(self, context, endpoint):
        self.context = context
        self.endpoint = endpoint

    async def __aenter__(self):
        try:
            return await self.context.__aenter__()
        except Exception as err:
            _advance_checkpointer_endpoint(self.endpoint, err)
            raise

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            return await self.context.__aexit__(exc_type, exc_val, exc_tb)
        finally:
            if exc_val is not None and is_mysql_connection_error(exc_val):
                _advance_checkpointer_endpoint(self.endpoint, exc_val)


def _advance_checkpointer_endpoint(failed_endpoint, reason: Exception) -> None:
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


def _get_mysql_checkpointer():
    """获取 MySQL checkpointer"""
    try:
        from langgraph.checkpoint.mysql.aio import AIOMySQLSaver
    except ImportError:
        raise ImportError(
            "使用 MySQL checkpointer 需要安装: pip install langgraph-checkpoint-mysql aiomysql"
        )

    password = _get_mysql_password()
    endpoint = _endpoint_selector.current()
    conn_string = (
        f"mysql+aiomysql://{MYSQL_USER}:{password}"
        f"@{endpoint.host}:{endpoint.port}/{MYSQL_DB}"
    )
    try:
        return _MySQLCheckpointerFailoverContext(AIOMySQLSaver.from_conn_string(conn_string), endpoint)
    except Exception as err:
        _advance_checkpointer_endpoint(endpoint, err)
        raise


# ============ 初始化函数 ============

async def setup_checkpointer():
    """
    初始化 checkpointer 和业务表

    - SQLite: 启用 WAL 模式提高并发性能
    - MySQL: 调用 checkpointer.setup() 创建 checkpoint 表
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
        logger.info("跳过 MySQL checkpointer 自动初始化")

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
