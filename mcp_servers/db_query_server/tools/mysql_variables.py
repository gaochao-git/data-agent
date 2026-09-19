"""MySQL 变量查看脚本"""
from mcp_servers.common.db_client import get_db_client

import re

# 白名单：只允许字母、数字、下划线、减号
SAFE_PATTERN = re.compile(r'^[a-zA-Z0-9_-]*$')

def execute(db_host: str, port: int = 3306, pattern: str = "") -> dict:
    """查看 MySQL 配置变量（SHOW VARIABLES）

    Args:
        db_host: 数据库主机标识
        port: 数据库端口，默认 3306
        pattern: 过滤模式（如 timeout、buffer），为空则返回常用配置

    Returns:
        配置变量信息
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
                sql = f"SHOW GLOBAL VARIABLES LIKE '%{pattern}%'"
                rows = client.execute(sql)
                variables = {row['Variable_name']: row['Value'] for row in rows}
                return {
                    "success": True,
                    "db_host": db_host,
                    "port": port,
                    "pattern": pattern,
                    "variables": variables
                }
            else:
                # 返回常用配置
                rows = client.execute("SHOW GLOBAL VARIABLES")
                variables = {row['Variable_name']: row['Value'] for row in rows}

                return {
                    "success": True,
                    "db_host": db_host,
                    "port": port,
                    "connections": {
                        "max_connections": variables.get('max_connections'),
                        "wait_timeout": variables.get('wait_timeout'),
                        "interactive_timeout": variables.get('interactive_timeout'),
                        "connect_timeout": variables.get('connect_timeout'),
                    },
                    "innodb": {
                        "buffer_pool_size": variables.get('innodb_buffer_pool_size'),
                        "log_file_size": variables.get('innodb_log_file_size'),
                        "flush_log_at_trx_commit": variables.get('innodb_flush_log_at_trx_commit'),
                        "lock_wait_timeout": variables.get('innodb_lock_wait_timeout'),
                    },
                    "slow_query": {
                        "slow_query_log": variables.get('slow_query_log'),
                        "slow_query_log_file": variables.get('slow_query_log_file'),
                        "long_query_time": variables.get('long_query_time'),
                    },
                    "replication": {
                        "server_id": variables.get('server_id'),
                        "log_bin": variables.get('log_bin'),
                        "binlog_format": variables.get('binlog_format'),
                        "gtid_mode": variables.get('gtid_mode'),
                    },
                    "charset": {
                        "character_set_server": variables.get('character_set_server'),
                        "collation_server": variables.get('collation_server'),
                    },
                }

    except Exception as e:
        return {
            "success": False,
            "db_host": db_host,
            "port": port,
            "error": str(e)
        }
