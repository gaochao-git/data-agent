"""使用 journalctl 查询系统日志"""
from mcp_servers.common.ssh_client import get_ssh_client
from mcp_servers.common.validation import validate_filter

import re

# since/until 时间参数验证：只允许字母、数字、空格、冒号、点、横线
SAFE_TIME_PATTERN = re.compile(r'^[a-zA-Z0-9 :.\-]+$')

def execute(host: str, unit: str = None, priority: str = None,
            since: str = None, until: str = None, grep: str = None,
            lines: int = 500, output_format: str = "short",
            timeout: int = 30) -> dict:
    """使用 journalctl 查询系统日志

    Args:
        host: 目标主机
        unit: 服务单元名称，多个用逗号分隔（如 nginx,mysql）
        priority: 日志优先级，支持单个(err)或范围(warning..err)
            0=emerg, 1=alert, 2=crit, 3=err, 4=warning, 5=notice, 6=info, 7=debug
        since: 开始时间，支持多种格式：
            - "1 hour ago", "2 hours ago", "30 minutes ago"
            - "today", "yesterday"
            - "2025-01-12 09:00:00"
        until: 结束时间，格式同 since
        grep: 关键词过滤（支持正则）
        lines: 最大返回行数，默认 500
        output_format: 输出格式，short(默认)/json/verbose

    Returns:
        日志查询结果
    """
    try:
        # 参数验证
        if lines < 1 or lines > 5000:
            return {"success": False, "error": "lines 必须在 1-5000 之间"}

        valid_formats = ["short", "json", "verbose", "cat"]
        if output_format not in valid_formats:
            return {"success": False, "error": f"output_format 必须是 {valid_formats} 之一"}

        # 安全验证（防止命令注入）
        if unit:
            for u in unit.split(","):
                valid, err = validate_filter(u.strip())
                if not valid:
                    return {"success": False, "error": f"unit 参数不安全: {err}"}

        if priority:
            valid, err = validate_filter(priority)
            if not valid:
                return {"success": False, "error": f"priority 参数不安全: {err}"}

        if since and not SAFE_TIME_PATTERN.match(since):
            return {"success": False, "error": "since 参数包含不安全字符"}

        if until and not SAFE_TIME_PATTERN.match(until):
            return {"success": False, "error": "until 参数包含不安全字符"}

        # 构建 journalctl 命令
        cmd_parts = ["sudo", "journalctl", "--no-pager"]

        # 服务单元过滤
        if unit:
            for u in unit.split(","):
                u = u.strip()
                if u:
                    cmd_parts.extend(["-u", u])

        # 优先级过滤
        if priority:
            cmd_parts.extend(["-p", priority])

        # 时间范围
        if since:
            cmd_parts.extend(["--since", f'"{since}"'])
        if until:
            cmd_parts.extend(["--until", f'"{until}"'])

        # 输出格式
        cmd_parts.extend(["-o", output_format])

        # 行数限制
        cmd_parts.extend(["-n", str(lines)])

        # 关键词过滤（使用 grep -E）
        if grep:
            safe_grep = grep.replace("'", "'\\''")
            cmd_parts.append(f"| grep -E -i '{safe_grep}'")

        cmd = " ".join(cmd_parts)

        with get_ssh_client(host) as client:
            stdout, stderr = client.exec_command(cmd, timeout=timeout)

            # 检查 journalctl 是否可用
            if "command not found" in stderr or "journalctl: command not found" in stdout:
                return {
                    "success": False,
                    "host": host,
                    "error": "journalctl 不可用（可能是 CentOS 6）。请使用 grep 命令查询 /var/log/messages"
                }

            # 统计返回行数
            content = stdout.strip()
            returned_lines = len([l for l in content.split('\n') if l]) if content else 0

            result = {
                "success": True,
                "host": host,
                "command": cmd,
                "returned_lines": returned_lines,
                "output": content if content else "没有匹配的日志"
            }

            # 如果达到行数限制，提示可能有更多
            if returned_lines >= lines:
                result["truncated"] = True
                result["truncated_info"] = f"已返回 {lines} 行，可能还有更多日志。建议缩小时间范围或增加过滤条件"

            return result

    except Exception as e:
        return {
            "success": False,
            "host": host,
            "error": str(e)
        }

def list_units(host: str, timeout: int = 10) -> dict:
    """列出可用的服务单元

    Args:
        host: 目标主机
        timeout: 超时时间

    Returns:
        服务单元列表
    """
    try:
        with get_ssh_client(host) as client:
            # 获取有日志的服务单元
            cmd = "sudo journalctl --no-pager -F _SYSTEMD_UNIT 2>/dev/null | head -50"
            stdout, stderr = client.exec_command(cmd, timeout=timeout)

            if "command not found" in stderr or "command not found" in stdout:
                return {"success": False, "host": host, "error": "journalctl 不可用（可能是 CentOS 6）"}

            units = [u.strip() for u in stdout.strip().split('\n') if u.strip()]

            return {
                "success": True,
                "host": host,
                "units": units,
                "count": len(units)
            }

    except Exception as e:
        return {"success": False, "host": host, "error": str(e)}
