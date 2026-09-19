"""MySQL 慢查询日志分析脚本"""
from mcp_servers.common.db_client import get_db_client
from mcp_servers.common.ssh_client import get_ssh_client
from mcp_servers.common.config import mcp_server_tmp_dir as LOCAL_TMP_DIR
from mcp_servers.common.validation import validate_path

import os
import re
import subprocess
import time as time_module

def execute(db_host: str, port: int = 3306, since: str = None, until: str = None,
            min_time: float = 1.0, limit: int = 10, timeout: int = 60) -> dict:
    """分析 MySQL 慢查询日志"""
    try:
        slow_log_info = _get_slow_log_config(db_host, port)
        if not slow_log_info["success"]:
            return slow_log_info

        if slow_log_info["slow_query_log"] != "ON":
            return {
                "success": False,
                "db_host": db_host,
                "port": port,
                "error": "慢查询日志未开启。请执行: SET GLOBAL slow_query_log = 'ON'"
            }

        slow_log_file = slow_log_info["slow_query_log_file"]
        if not slow_log_file:
            return {
                "success": False,
                "db_host": db_host,
                "port": port,
                "error": "无法获取慢查询日志路径"
            }

        valid, err_msg = validate_path(slow_log_file)
        if not valid:
            return {
                "success": False,
                "db_host": db_host,
                "port": port,
                "error": f"慢查询日志路径不安全: {err_msg}"
            }

        with get_ssh_client(db_host) as ssh:
            check_cmd = f"sudo test -f '{slow_log_file}' && echo 'exists' || echo 'not_found'"
            stdout, _ = ssh.exec_command(check_cmd, timeout=10)
            if "not_found" in stdout:
                return {
                    "success": False,
                    "db_host": db_host,
                    "port": port,
                    "slow_log_file": slow_log_file,
                    "error": f"慢查询日志文件不存在: {slow_log_file}"
                }

            size_cmd = f"sudo stat -c%s '{slow_log_file}' 2>/dev/null || echo 0"
            stdout, _ = ssh.exec_command(size_cmd, timeout=10)
            file_size = int(stdout.strip()) if stdout.strip().isdigit() else 0
            if file_size == 0:
                return {
                    "success": True,
                    "db_host": db_host,
                    "port": port,
                    "slow_log_file": slow_log_file,
                    "file_size": 0,
                    "message": "慢查询日志为空，没有慢查询记录",
                    "output": ""
                }

            result = _analyze_with_pt_digest(
                ssh, db_host, port, slow_log_file, since, until, min_time, limit, timeout, file_size
            )

            result["db_host"] = db_host
            result["port"] = port
            result["slow_log_file"] = slow_log_file
            result["file_size"] = file_size
            result["file_size_human"] = _format_size(file_size)
            result["long_query_time"] = slow_log_info["long_query_time"]
            return result

    except Exception as e:
        return {
            "success": False,
            "db_host": db_host,
            "port": port,
            "error": str(e)
        }

def _get_slow_log_config(db_host: str, port: int) -> dict:
    try:
        with get_db_client(db_host, port=port) as client:
            rows = client.execute("SHOW GLOBAL VARIABLES LIKE 'slow_query%'")
            variables = {row['Variable_name']: row['Value'] for row in rows}

            rows = client.execute("SHOW GLOBAL VARIABLES LIKE 'long_query_time'")
            if rows:
                variables['long_query_time'] = rows[0]['Value']

            return {
                "success": True,
                "slow_query_log": variables.get('slow_query_log', 'OFF'),
                "slow_query_log_file": variables.get('slow_query_log_file', ''),
                "long_query_time": variables.get('long_query_time', '10')
            }
    except Exception as e:
        return {"success": False, "error": f"获取慢查询配置失败: {str(e)}"}

def _analyze_with_pt_digest(ssh, db_host: str, port: int, slow_log_file: str,
                            since: str, until: str, min_time: float, limit: int, timeout: int,
                            file_size: int, max_size_mb: int = 10) -> dict:
    timestamp = int(time_module.time())
    remote_temp_file = f"/tmp/slow_extract_{timestamp}.log"
    safe_host = re.sub(r'[^a-zA-Z0-9._-]', '_', db_host)
    local_temp_file = os.path.join(LOCAL_TMP_DIR, f"{safe_host}_{port}_slowlog_{timestamp}.log")

    try:
        extract_cmd = f"sudo tail -c {max_size_mb}M '{slow_log_file}' > {remote_temp_file} && chmod 644 {remote_temp_file} && stat -c%s {remote_temp_file}"
        stdout, stderr = ssh.exec_command(extract_cmd, timeout=60)

        if not stdout or not stdout.strip().isdigit():
            return {
                "success": False,
                "error": f"提取日志失败: {stderr or stdout or '未知错误'}"
            }

        extracted_size = int(stdout.strip())
        if extracted_size == 0:
            return {
                "success": False,
                "error": "提取日志失败，内容为空"
            }

        try:
            sftp = ssh.get_sftp()
            sftp.get(remote_temp_file, local_temp_file)
            sftp.close()
        except Exception as e:
            return {
                "success": False,
                "error": f"SFTP 下载失败: {str(e)}"
            }

        truncated_bytes = 0
        with open(local_temp_file, 'r', encoding='utf-8') as f:
            file_content = f.read()

        first_time_pos = file_content.find('# Time:')
        if first_time_pos == -1:
            return {
                "success": False,
                "error": "提取的日志中没有找到有效的慢查询记录"
            }
        elif first_time_pos > 0:
            truncated_bytes = first_time_pos
            file_content = file_content[first_time_pos:]

        header = f"""# ============== 慢查询日志分析 ==============
# 原始文件: {slow_log_file}
# 原始大小: {_format_size(file_size)}
# 提取大小: {_format_size(extracted_size)}
# 提示: 想查看更早的日志请使用 grep 命令
# ============================================

"""
        with open(local_temp_file, 'w', encoding='utf-8') as f:
            f.write(header + file_content)

        cmd_parts = ["pt-query-digest"]
        if since:
            cmd_parts.extend(["--since", since])
        if until:
            cmd_parts.extend(["--until", until])
        if min_time and min_time > 0:
            cmd_parts.extend(["--filter", f"$event->{{Query_time}} >= {min_time}"])
        cmd_parts.extend(["--limit", str(limit)])
        cmd_parts.append(local_temp_file)

        proc_result = subprocess.run(cmd_parts, capture_output=True, text=True, timeout=timeout)
        output_content = proc_result.stdout.strip()
        if not output_content:
            output_content = proc_result.stderr.strip() if proc_result.stderr else "分析完成，没有输出"

        result_dict = {
            "success": True,
            "command": " ".join(cmd_parts),
            "extracted_size": extracted_size,
            "extracted_size_human": _format_size(extracted_size),
            "output": output_content
        }
        if truncated_bytes > 0:
            result_dict["truncated_bytes"] = truncated_bytes
            result_dict["truncated_note"] = f"已删除开头 {truncated_bytes} 字节的残缺数据"
        return result_dict

    finally:
        if os.path.exists(local_temp_file):
            os.remove(local_temp_file)
        remote_filename = os.path.basename(remote_temp_file)
        ssh.exec_command(f"cd /tmp && rm -f {remote_filename}", timeout=10)

def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f}KB"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / (1024 ** 2):.1f}MB"
    return f"{size_bytes / (1024 ** 3):.1f}GB"
