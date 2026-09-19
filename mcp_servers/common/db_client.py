"""MySQL 只读诊断客户端（pymysql）：自 fullstack-langgraph 平移，默认账号改读 data-agent 环境变量。"""

from typing import Optional

import pymysql

from mcp_servers.common.config import MCP_DIAG_DB_PASSWORD, MCP_DIAG_DB_USER
from mcp_servers.common.output import debug_log


class DBClient:
    """数据库客户端管理 - 从 config.yaml 读取默认配置"""

    def __init__(self, host: str, port: int = 3306, user: Optional[str] = None,
                 password: Optional[str] = None, database: str = ""):
        self.host = host
        self.port = port
        self.user = user if user is not None else MCP_DIAG_DB_USER
        self.password = password if password is not None else MCP_DIAG_DB_PASSWORD
        self.database = database
        self._conn = None

    def connect(self):
        """建立数据库连接"""
        if self._conn is not None:
            return self._conn

        self._conn = pymysql.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            database=self.database,
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=5,
            read_timeout=15,
        )
        return self._conn

    def execute(self, sql: str, params=None) -> list:
        """执行 SQL"""
        debug_log(f"[MySQL {self.host}:{self.port}] {sql}")
        conn = self.connect()
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()

    def close(self):
        """关闭连接"""
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


def get_db_client(db_host: str, port: int = 3306) -> DBClient:
    """获取数据库客户端"""
    return DBClient(db_host, port=port)

