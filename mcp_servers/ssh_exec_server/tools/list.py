"""目录列表脚本"""
from mcp_servers.common.ssh_client import get_ssh_client
from mcp_servers.common.validation import validate_path

import shlex

def execute(
    host: str,
    path: str = "/tmp",
    sort_by: str = "name",
    limit: int = 50,
    timeout: int = 30,
) -> dict:
    """列出目录内容

    Args:
        host: 目标主机
        path: 目录路径
        sort_by: 排序方式（name/size/time）
        limit: 最大返回条数
        timeout: 超时时间

    Returns:
        目录内容
    """
    try:
        # 参数验证
        is_valid, error_msg = validate_path(path)
        if not is_valid:
            return {"success": False, "host": host, "error": error_msg}
        if limit < 1 or limit > 200:
            return {"success": False, "host": host, "error": "limit 必须在 1-200 之间"}

        with get_ssh_client(host) as client:
            # 根据排序方式构建 ls 参数
            if sort_by == "size":
                ls_opts = "-lhS"
            elif sort_by == "time":
                ls_opts = "-lht"
            else:  # name
                ls_opts = "-lh"

            safe_path = shlex.quote(path)
            cmd = f"ls {ls_opts} {safe_path} 2>/dev/null | head -n {limit}"
            stdout, stderr = client.exec_command(cmd, timeout)

            return {
                "success": True,
                "host": host,
                "path": path,
                "output": stdout.strip() if stdout.strip() else "(空目录)"
            }

    except Exception as e:
        error_msg = str(e) or type(e).__name__
        return {
            "success": False,
            "host": host,
            "error": error_msg
        }
