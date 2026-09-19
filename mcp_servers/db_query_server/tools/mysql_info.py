"""MySQL 基本信息脚本"""
from mcp_servers.common.db_client import get_db_client

def execute(db_host: str, port: int = 3306) -> dict:
    """获取 MySQL 基本信息

    Args:
        db_host: 数据库主机标识
        port: 数据库端口，默认 3306

    Returns:
        数据库基本信息
    """
    try:
        with get_db_client(db_host, port=port) as client:
            # 获取版本
            version_rows = client.execute("SELECT VERSION() as version")
            version = version_rows[0]['version'] if version_rows else ''

            # 获取运行时间
            uptime_rows = client.execute("SHOW GLOBAL STATUS LIKE 'Uptime'")
            uptime = int(uptime_rows[0]['Value']) if uptime_rows else 0

            # 获取数据库列表
            db_rows = client.execute("SHOW DATABASES")
            databases = [row['Database'] for row in db_rows]

            # 获取关键配置
            var_rows = client.execute("SHOW GLOBAL VARIABLES")
            variables = {row['Variable_name']: row['Value'] for row in var_rows}

            # 获取当前连接数
            status_rows = client.execute("SHOW GLOBAL STATUS LIKE 'Threads_connected'")
            current_connections = int(status_rows[0]['Value']) if status_rows else 0

            return {
                "success": True,
                "db_host": db_host,
                "port": port,
                "version": version,
                "uptime_seconds": uptime,
                "uptime_days": round(uptime / 86400, 1),
                "current_connections": current_connections,
                "max_connections": variables.get('max_connections'),
                "datadir": variables.get('datadir'),
                "socket": variables.get('socket'),
                "server_id": variables.get('server_id'),
                "slow_query_log_file": variables.get('slow_query_log_file'),
                "log_error": variables.get('log_error'),
                "databases": databases,
                "database_count": len(databases),
            }

    except Exception as e:
        return {
            "success": False,
            "db_host": db_host,
            "port": port,
            "error": str(e)
        }
