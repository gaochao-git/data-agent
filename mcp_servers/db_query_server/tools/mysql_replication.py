"""MySQL 复制状态检查脚本"""
from mcp_servers.common.db_client import get_db_client

def execute(db_host: str, port: int = 3306) -> dict:
    """检查 MySQL 复制状态（主从复制 + 半同步）

    Args:
        db_host: 数据库主机标识
        port: 数据库端口，默认 3306

    Returns:
        复制状态信息
    """
    try:
        with get_db_client(db_host, port=port) as client:
            result = {
                "success": True,
                "db_host": db_host,
                "port": port,
                "role": "unknown",
                "read_only": None,
                "slave_hosts": None,
                "slave_status": None,
                "semi_sync_master": None,
                "semi_sync_slave": None
            }

            # 检查 read_only 状态（判断主从角色的重要依据）
            try:
                ro_rows = client.execute("SELECT @@read_only as read_only, @@super_read_only as super_read_only")
                if ro_rows:
                    result["read_only"] = {
                        "read_only": ro_rows[0].get('read_only'),
                        "super_read_only": ro_rows[0].get('super_read_only')
                    }
            except:
                pass

            # 查看下挂的从库（主库视角）
            try:
                # MySQL 8.0.22+ 使用 SHOW REPLICAS
                slave_hosts = client.execute("SHOW REPLICAS")
                if slave_hosts:
                    result["slave_hosts"] = slave_hosts
            except:
                pass

            if not result["slave_hosts"]:
                try:
                    # MySQL 5.7 / 8.0 使用 SHOW SLAVE HOSTS
                    slave_hosts = client.execute("SHOW SLAVE HOSTS")
                    if slave_hosts:
                        result["slave_hosts"] = slave_hosts
                except:
                    pass

            # 尝试获取从库状态
            slave_status = None
            try:
                # MySQL 8.0.22+ 使用 REPLICA
                rows = client.execute("SHOW REPLICA STATUS")
                if rows:
                    slave_status = rows[0]
            except:
                pass

            if not slave_status:
                try:
                    # MySQL 5.7 / 8.0 使用 SLAVE
                    rows = client.execute("SHOW SLAVE STATUS")
                    if rows:
                        slave_status = rows[0]
                except:
                    pass

            if slave_status:
                result["role"] = "slave"
                result["slave_status"] = {
                    "io_running": slave_status.get('Slave_IO_Running') or slave_status.get('Replica_IO_Running'),
                    "sql_running": slave_status.get('Slave_SQL_Running') or slave_status.get('Replica_SQL_Running'),
                    "seconds_behind": slave_status.get('Seconds_Behind_Master') or slave_status.get('Seconds_Behind_Source'),
                    "last_io_error": slave_status.get('Last_IO_Error') or slave_status.get('Last_IO_Error'),
                    "last_sql_error": slave_status.get('Last_SQL_Error') or slave_status.get('Last_SQL_Error'),
                    "master_host": slave_status.get('Master_Host') or slave_status.get('Source_Host'),
                    "master_port": slave_status.get('Master_Port') or slave_status.get('Source_Port'),
                }
            elif result["slave_hosts"]:
                result["role"] = "master"
            else:
                result["role"] = "standalone"

            # 检查半同步复制状态（主库）
            try:
                master_semi = client.execute("SHOW STATUS LIKE 'Rpl_semi_sync_master%'")
                if master_semi:
                    semi_dict = {row['Variable_name']: row['Value'] for row in master_semi}
                    result["semi_sync_master"] = {
                        "status": semi_dict.get('Rpl_semi_sync_master_status'),
                        "clients": semi_dict.get('Rpl_semi_sync_master_clients'),
                        "no_tx": semi_dict.get('Rpl_semi_sync_master_no_tx'),
                        "yes_tx": semi_dict.get('Rpl_semi_sync_master_yes_tx'),
                    }
            except:
                pass

            # 检查半同步复制状态（从库）
            try:
                slave_semi = client.execute("SHOW STATUS LIKE 'Rpl_semi_sync_slave%'")
                if slave_semi:
                    semi_dict = {row['Variable_name']: row['Value'] for row in slave_semi}
                    result["semi_sync_slave"] = {
                        "status": semi_dict.get('Rpl_semi_sync_slave_status'),
                    }
            except:
                pass

            return result

    except Exception as e:
        return {
            "success": False,
            "db_host": db_host,
            "port": port,
            "error": str(e)
        }
