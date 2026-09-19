"""MySQL 长事务检查脚本"""
from mcp_servers.common.db_client import get_db_client

def execute(db_host: str, port: int = 3306, min_seconds: int = 1) -> dict:
    """检查 MySQL 长时间运行的事务

    Args:
        db_host: 数据库主机标识
        port: 数据库端口，默认 3306
        min_seconds: 最小运行时间（秒），默认 1

    Returns:
        长事务信息
    """
    try:
        # 参数验证：确保是正整数
        min_seconds = max(1, int(min_seconds))

        with get_db_client(db_host, port=port) as client:
            # 查看长时间运行的事务
            long_trx = client.execute(f"""
                SELECT trx_id, trx_state, trx_started,
                       TIMESTAMPDIFF(SECOND, trx_started, NOW()) as running_seconds,
                       trx_mysql_thread_id, trx_query
                FROM information_schema.innodb_trx
                WHERE TIMESTAMPDIFF(SECOND, trx_started, NOW()) > {min_seconds}
                ORDER BY trx_started
                LIMIT 50
            """)

            # 查看事务对应的连接详情
            trx_with_process = client.execute(f"""
                SELECT t.trx_mysql_thread_id as thread_id,
                       t.trx_state,
                       t.trx_started,
                       TIMESTAMPDIFF(SECOND, t.trx_started, NOW()) as running_seconds,
                       p.user, p.host, p.db, p.command, p.time, p.state, p.info
                FROM information_schema.innodb_trx t
                JOIN information_schema.processlist p ON t.trx_mysql_thread_id = p.id
                WHERE TIMESTAMPDIFF(SECOND, t.trx_started, NOW()) > {min_seconds}
                ORDER BY t.trx_started
                LIMIT 50
            """)

            # 所有活跃事务数
            all_trx = client.execute("SELECT COUNT(*) as count FROM information_schema.innodb_trx")
            total_trx = all_trx[0]['count'] if all_trx else 0

            return {
                "success": True,
                "db_host": db_host,
                "port": port,
                "min_seconds": min_seconds,
                "total_active_transactions": total_trx,
                "long_transactions_count": len(long_trx),
                "long_transactions": trx_with_process
            }

    except Exception as e:
        return {
            "success": False,
            "db_host": db_host,
            "port": port,
            "error": str(e)
        }
