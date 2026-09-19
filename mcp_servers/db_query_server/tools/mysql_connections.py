"""MySQL 连接数检查脚本"""
from mcp_servers.common.db_client import get_db_client

def execute(db_host: str, port: int = 3306) -> dict:
    """检查 MySQL 连接数

    Args:
        db_host: 数据库主机标识
        port: 数据库端口，默认 3306

    Returns:
        连接数信息
    """
    try:
        with get_db_client(db_host, port=port) as client:
            # 当前连接数
            status_rows = client.execute("SHOW STATUS LIKE 'Threads_connected'")
            threads_connected = int(status_rows[0]['Value']) if status_rows else 0

            # 最大连接数配置
            var_rows = client.execute("SHOW VARIABLES LIKE 'max_connections'")
            max_connections = int(var_rows[0]['Value']) if var_rows else 0

            # 连接来源分布
            distribution = client.execute("""
                SELECT user, host, COUNT(*) as count
                FROM information_schema.processlist
                GROUP BY user, host
                ORDER BY count DESC
                LIMIT 20
            """)

            # 正在执行的查询（执行时间 > 1秒）
            active = client.execute("""
                SELECT id, user, host, db, command, time, state,
                       LEFT(info, 200) as query
                FROM information_schema.processlist
                WHERE command != 'Sleep'
                  AND time > 1
                  AND info IS NOT NULL
                ORDER BY time DESC
                LIMIT 20
            """)

            # 使用率
            usage_pct = round(threads_connected / max_connections * 100, 1) if max_connections > 0 else 0

            return {
                "success": True,
                "db_host": db_host,
                "port": port,
                "current_connections": threads_connected,
                "max_connections": max_connections,
                "usage_percent": usage_pct,
                "distribution": distribution,
                "active": active
            }

    except Exception as e:
        return {
            "success": False,
            "db_host": db_host,
            "port": port,
            "error": str(e)
        }
