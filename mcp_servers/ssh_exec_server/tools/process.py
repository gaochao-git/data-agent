"""进程列表脚本"""
from mcp_servers.common.ssh_client import get_ssh_client
from mcp_servers.common.validation import validate_filter

from typing import Optional

def execute(host: str, filter: Optional[str] = None, sort_by: Optional[str] = None, timeout: int = 30) -> dict:
    """查看进程列表

    Args:
        host: 目标主机
        filter: 过滤关键词（如 mysqld、nginx）
        sort_by: 排序方式（cpu/mem），使用 top 实时采样
        timeout: 超时时间

    Returns:
        进程列表
    """
    try:
        # 参数验证
        if filter:
            is_valid, error_msg = validate_filter(filter)
            if not is_valid:
                return {"success": False, "host": host, "error": error_msg}

        with get_ssh_client(host) as client:
            # 如果指定了 sort_by，使用 top 实时采样
            if sort_by == "cpu":
                cmd = """top -b -n 2 -d 2 | awk '/^top -/{count++} count==2 && /^ *PID/{found=1; print; next} count==2 && found && NF>0{print}' | head -21"""
                title = "=== Top CPU 进程 (实时采样) ==="
            elif sort_by == "mem":
                cmd = """top -b -n 1 -o %MEM | awk '/^ *PID/{found=1; print; next} found && NF>0{print}' | head -21"""
                title = "=== Top 内存进程 ==="
            else:
                # 默认使用 ps 显示进程列表（含完整命令行）
                if filter:
                    # 过滤特定进程
                    cmd = f"ps -ef | head -1 && ps -ef | grep -v grep | grep '{filter}'"
                    title = f"=== 进程列表 (过滤: {filter}) ==="
                else:
                    # 显示所有进程（按 CPU 排序，前20）
                    cmd = "ps aux --sort=-%cpu | head -21"
                    title = "=== 进程列表 ==="

            stdout, stderr = client.exec_command(cmd, timeout)

            output = stdout.strip()
            if not output or (filter and "PID" in output and output.count('\n') == 0):
                output = "(未找到匹配进程)"

            return {
                "success": True,
                "host": host,
                "output": title + "\n" + output
            }

    except Exception as e:
        error_msg = str(e) or type(e).__name__
        return {
            "success": False,
            "host": host,
            "error": error_msg
        }
