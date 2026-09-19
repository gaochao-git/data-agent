"""MySQL 锁等待检查脚本"""
from mcp_servers.common.db_client import get_db_client

def execute(db_host: str, port: int = 3306) -> dict:
    """检查 MySQL 锁等待情况

    Args:
        db_host: 数据库主机标识
        port: 数据库端口，默认 3306

    Returns:
        锁等待信息
    """
    try:
        with get_db_client(db_host, port=port) as client:
            # 获取 MySQL 版本
            version_rows = client.execute("SELECT VERSION() as version")
            version = version_rows[0]['version'] if version_rows else ''
            is_mysql8 = version.startswith('8.')

            # 查看锁等待详情（阻塞者和等待者信息）
            lock_waits = []
            if is_mysql8:
                # MySQL 8.0: 使用 performance_schema.data_lock_waits
                try:
                    lock_waits = client.execute("""
                        SELECT
                            r.trx_id AS waiting_trx_id,
                            r.trx_mysql_thread_id AS waiting_thread,
                            r.trx_mysql_thread_id AS waiting_connection_id,
                            r.trx_query AS waiting_query,
                            b.trx_id AS blocking_trx_id,
                            b.trx_mysql_thread_id AS blocking_thread,
                            b.trx_mysql_thread_id AS blocking_connection_id,
                            b.trx_query AS blocking_query,
                            TIMESTAMPDIFF(SECOND, r.trx_wait_started, NOW()) AS wait_seconds
                        FROM performance_schema.data_lock_waits w
                        JOIN information_schema.innodb_trx b
                            ON b.trx_id = w.BLOCKING_ENGINE_TRANSACTION_ID
                        JOIN information_schema.innodb_trx r
                            ON r.trx_id = w.REQUESTING_ENGINE_TRANSACTION_ID
                        LIMIT 50
                    """)
                except:
                    pass
            else:
                # MySQL 5.7: 使用 information_schema.innodb_lock_waits
                try:
                    lock_waits = client.execute("""
                        SELECT
                            r.trx_id AS waiting_trx_id,
                            r.trx_mysql_thread_id AS waiting_thread,
                            r.trx_mysql_thread_id AS waiting_connection_id,
                            r.trx_query AS waiting_query,
                            b.trx_id AS blocking_trx_id,
                            b.trx_mysql_thread_id AS blocking_thread,
                            b.trx_mysql_thread_id AS blocking_connection_id,
                            b.trx_query AS blocking_query,
                            TIMESTAMPDIFF(SECOND, r.trx_wait_started, NOW()) AS wait_seconds
                        FROM information_schema.innodb_lock_waits w
                        JOIN information_schema.innodb_trx b ON b.trx_id = w.blocking_trx_id
                        JOIN information_schema.innodb_trx r ON r.trx_id = w.requesting_trx_id
                        LIMIT 50
                    """)
                except:
                    pass

            # 查看持有锁的事务
            trx_rows = client.execute("""
                SELECT trx_mysql_thread_id as trx_thread_id,
                       trx_id, trx_state, trx_started,
                       TIMESTAMPDIFF(SECOND, trx_started, NOW()) as running_seconds,
                       trx_tables_locked, trx_rows_locked, trx_query
                FROM information_schema.innodb_trx
                ORDER BY trx_started
                LIMIT 50
            """)

            # 获取 InnoDB 状态中的死锁信息
            innodb_status = ""
            try:
                status_rows = client.execute("SHOW ENGINE INNODB STATUS")
                if status_rows:
                    innodb_status = status_rows[0].get('Status', '')
                    # 提取死锁部分
                    if 'LATEST DETECTED DEADLOCK' in innodb_status:
                        start = innodb_status.find('LATEST DETECTED DEADLOCK')
                        end = innodb_status.find('------------', start + 100)
                        if end > start:
                            innodb_status = innodb_status[start:end]
                        else:
                            innodb_status = innodb_status[start:start+2000]
                    else:
                        innodb_status = "No deadlock detected"
            except:
                innodb_status = "Unable to get INNODB STATUS"

            return {
                "success": True,
                "db_host": db_host,
                "port": port,
                "mysql_version": version,
                "lock_waits_count": len(lock_waits),
                "lock_waits": lock_waits,
                "transactions_with_locks": trx_rows,
                "deadlock_info": innodb_status
            }

    except Exception as e:
        return {
            "success": False,
            "db_host": db_host,
            "port": port,
            "error": str(e)
        }
