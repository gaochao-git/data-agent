"""MySQL 状态查看脚本"""
from mcp_servers.common.db_client import get_db_client

import re

# 白名单：只允许字母、数字、下划线、减号
SAFE_PATTERN = re.compile(r'^[a-zA-Z0-9_-]*$')

def execute(db_host: str, port: int = 3306, pattern: str = "") -> dict:
    """查看 MySQL 状态变量（SHOW STATUS）

    Args:
        db_host: 数据库主机标识
        port: 数据库端口，默认 3306
        pattern: 过滤模式（如 Threads、Conn），为空则返回常用指标

    Returns:
        状态变量信息
    """
    try:
        # 参数验证
        if pattern and not SAFE_PATTERN.match(pattern):
            return {
                "success": False,
                "error": "pattern 只允许字母、数字、下划线"
            }

        with get_db_client(db_host, port=port) as client:
            if pattern:
                # 按模式过滤
                sql = f"SHOW GLOBAL STATUS LIKE '%{pattern}%'"
                rows = client.execute(sql)
                status = {row['Variable_name']: row['Value'] for row in rows}
                return {
                    "success": True,
                    "db_host": db_host,
                    "port": port,
                    "pattern": pattern,
                    "variables": status
                }
            else:
                # 返回常用指标
                rows = client.execute("SHOW GLOBAL STATUS")
                status = {row['Variable_name']: row['Value'] for row in rows}

                return {
                    "success": True,
                    "db_host": db_host,
                    "port": port,
                    "connections": {
                        "current": int(status.get('Threads_connected', 0)),
                        "max_used": int(status.get('Max_used_connections', 0)),
                        "aborted_clients": int(status.get('Aborted_clients', 0)),
                        "aborted_connects": int(status.get('Aborted_connects', 0)),
                    },
                    "threads": {
                        "running": int(status.get('Threads_running', 0)),
                        "created": int(status.get('Threads_created', 0)),
                        "cached": int(status.get('Threads_cached', 0)),
                    },
                    "queries": {
                        "questions": int(status.get('Questions', 0)),
                        "slow_queries": int(status.get('Slow_queries', 0)),
                        "selects": int(status.get('Com_select', 0)),
                        "inserts": int(status.get('Com_insert', 0)),
                        "updates": int(status.get('Com_update', 0)),
                        "deletes": int(status.get('Com_delete', 0)),
                    },
                    "innodb": {
                        "buffer_pool_reads": int(status.get('Innodb_buffer_pool_reads', 0)),
                        "buffer_pool_read_requests": int(status.get('Innodb_buffer_pool_read_requests', 0)),
                        "row_lock_waits": int(status.get('Innodb_row_lock_waits', 0)),
                        "row_lock_time": int(status.get('Innodb_row_lock_time', 0)),
                    },
                    "uptime_seconds": int(status.get('Uptime', 0)),
                }

    except Exception as e:
        return {
            "success": False,
            "db_host": db_host,
            "port": port,
            "error": str(e)
        }
