"""读取文件内容脚本"""
from mcp_servers.common.ssh_client import get_ssh_client
from mcp_servers.common.validation import validate_path

from datetime import datetime, timezone

def _format_size_human(size_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(max(0, size_bytes))
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return "0 B"

def _format_mtime(epoch_seconds: int) -> str:
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).isoformat().replace("+00:00", "Z")

def execute(host: str, file_path: str, offset: int = 0, limit: int = 100, timeout: int = 30) -> dict:
    """读取远程文件内容（支持分页）

    Args:
        host: 目标主机
        file_path: 文件绝对路径
        offset: 起始行（从0开始）
        limit: 读取行数
        timeout: 超时时间

    Returns:
        文件内容和元数据
    """
    try:
        # 参数验证
        valid, err_msg = validate_path(file_path)
        if not valid:
            return {"success": False, "error": f"文件路径不安全: {err_msg}"}

        if offset < 0:
            return {"success": False, "error": "offset 必须大于等于 0"}

        if limit < 1 or limit > 2000:
            return {"success": False, "error": "limit 必须在 1-2000 之间"}

        with get_ssh_client(host) as client:
            safe_path = file_path.replace("'", "'\\''")

            # 获取文件大小和修改时间
            stat_cmd = f"stat -c '%s|%Y' '{safe_path}'"
            stat_stdout, stat_stderr = client.exec_command(f"sudo {stat_cmd}", timeout=timeout)
            if stat_stderr:
                return {"success": False, "error": stat_stderr}

            stat_parts = (stat_stdout or "").strip().split("|", 1)
            file_size_bytes = int(stat_parts[0]) if len(stat_parts) >= 1 and stat_parts[0].isdigit() else 0
            mtime_epoch = int(stat_parts[1]) if len(stat_parts) == 2 and stat_parts[1].isdigit() else 0

            # 获取文件总行数
            wc_cmd = f"wc -l '{safe_path}' | awk '{{print $1}}'"
            stdout, stderr = client.exec_command(f"sudo {wc_cmd}", timeout=timeout)
            total_lines = int(stdout.strip()) if stdout.strip().isdigit() else 0

            if offset >= total_lines:
                return {
                    "success": False,
                    "error": f"offset {offset} 超出文件行数（共 {total_lines} 行）",
                    "total_lines": total_lines
                }

            # 计算读取范围
            start_line = offset + 1
            end_line = min(offset + limit, total_lines)

            # 读取指定行范围
            sed_cmd = f"sed -n '{start_line},{end_line}p' '{safe_path}' | nl -ba -v {start_line}"
            stdout, stderr = client.exec_command(f"sudo {sed_cmd}", timeout=timeout)

            if stderr:
                return {"success": False, "error": stderr}

            lines_returned = len([l for l in stdout.split('\n') if l.strip()])
            has_more = end_line < total_lines

            return {
                "success": True,
                "host": host,
                "file_path": file_path,
                "file_size_human": _format_size_human(file_size_bytes),
                "mtime": _format_mtime(mtime_epoch) if mtime_epoch > 0 else "",
                "total_lines": total_lines,
                "offset": offset,
                "limit": limit,
                "lines_returned": lines_returned,
                "start_line": start_line,
                "end_line": end_line,
                "has_more": has_more,
                "output": stdout
            }

    except Exception as e:
        return {
            "success": False,
            "host": host,
            "file_path": file_path,
            "error": str(e)
        }
