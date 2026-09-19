"""搜索文件内容脚本"""
from mcp_servers.common.ssh_client import get_ssh_client
from mcp_servers.common.validation import validate_path

def execute(host: str, file_path: str, pattern: str, max_lines: int = 1000,
            before_lines: int = 0, after_lines: int = 0, timeout: int = 30) -> dict:
    """搜索远程文件内容（支持正则表达式）

    Args:
        host: 目标主机
        file_path: 文件路径
        pattern: 搜索模式（支持正则）
        max_lines: 最大返回行数
        before_lines: 显示匹配前N行
        after_lines: 显示匹配后N行
        timeout: 超时时间

    Returns:
        搜索结果
    """
    try:
        # 参数验证
        valid, err_msg = validate_path(file_path)
        if not valid:
            return {"success": False, "error": f"文件路径不安全: {err_msg}"}

        if max_lines < 1 or max_lines > 1000:
            return {"success": False, "error": "max_lines 必须在 1-1000 之间"}

        if before_lines < 0 or before_lines > 20:
            return {"success": False, "error": "before_lines 必须在 0-20 之间"}

        if after_lines < 0 or after_lines > 20:
            return {"success": False, "error": "after_lines 必须在 0-20 之间"}

        with get_ssh_client(host) as client:
            safe_path = file_path.replace("'", "'\\''")
            safe_pattern = pattern.replace("'", "'\\''")

            # 1. 先统计总匹配行数（不受 max_lines 限制）
            count_cmd = f"sudo grep -E -c -i '{safe_pattern}' '{safe_path}' 2>/dev/null || echo 0"
            total_matches_str, _ = client.exec_command(count_cmd, timeout=timeout)
            total_matches = int(total_matches_str.strip()) if total_matches_str.strip().isdigit() else 0

            # 2. 构建 grep 命令获取内容（默认忽略大小写）
            grep_cmd = "grep -E -n -i"

            # 上下文行数
            if before_lines > 0 and after_lines > 0:
                if before_lines == after_lines:
                    grep_cmd += f" -C {before_lines}"
                else:
                    grep_cmd += f" -B {before_lines} -A {after_lines}"
            elif before_lines > 0:
                grep_cmd += f" -B {before_lines}"
            elif after_lines > 0:
                grep_cmd += f" -A {after_lines}"

            grep_cmd += f" '{safe_pattern}' '{safe_path}' | head -n {max_lines}"

            stdout, stderr = client.exec_command(f"sudo {grep_cmd} 2>/dev/null || true", timeout=timeout)

            returned_lines = len([l for l in stdout.split('\n') if l.strip()])
            truncated = total_matches > returned_lines

            result = {
                "success": True,
                "host": host,
                "file_path": file_path,
                "pattern": pattern,
                "total_matches": total_matches,
                "returned_lines": returned_lines,
                "output": stdout
            }

            # 如果有截断，添加提示信息
            if truncated:
                result["truncated"] = True
                result["truncated_info"] = f"共匹配 {total_matches} 行，仅返回前 {returned_lines} 行，还有 {total_matches - returned_lines} 行未显示"

            return result

    except Exception as e:
        return {
            "success": False,
            "host": host,
            "file_path": file_path,
            "pattern": pattern,
            "error": str(e)
        }
