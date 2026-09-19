"""系统信息脚本"""
from mcp_servers.common.ssh_client import get_ssh_client

def execute(host: str, timeout: int = 30) -> dict:
    """获取系统基本信息

    Args:
        host: 目标主机
        timeout: 超时时间

    Returns:
        系统信息
    """
    try:
        with get_ssh_client(host) as client:
            # 一次性获取多项信息
            cmd = """CPU_USAGE=$(sh -c '
read _ user1 nice1 system1 idle1 iowait1 irq1 softirq1 steal1 _ _ < /proc/stat
total1=$((user1 + nice1 + system1 + idle1 + iowait1 + irq1 + softirq1 + steal1))
idle_total1=$((idle1 + iowait1))
sleep 1
read _ user2 nice2 system2 idle2 iowait2 irq2 softirq2 steal2 _ _ < /proc/stat
total2=$((user2 + nice2 + system2 + idle2 + iowait2 + irq2 + softirq2 + steal2))
idle_total2=$((idle2 + iowait2))
total_diff=$((total2 - total1))
idle_diff=$((idle_total2 - idle_total1))
if [ "$total_diff" -le 0 ]; then
  echo "N/A"
else
  awk "BEGIN {printf \\"%.2f%%\\", (1 - $idle_diff / $total_diff) * 100}"
fi
') && \
echo "=== 主机信息 ===" && hostname && \
echo "" && echo "=== 系统版本 ===" && cat /etc/os-release 2>/dev/null | grep -E "^(NAME|VERSION)=" | head -2 && \
echo "" && echo "=== 内核版本 ===" && uname -r && \
echo "" && echo "=== CPU 信息 ===" && grep "model name" /proc/cpuinfo | head -1 && echo "核心数: $(nproc)" && echo "CPU使用率: ${CPU_USAGE}" && \
echo "" && echo "=== 内存使用 ===" && free -h && \
echo "" && echo "=== 磁盘使用 ===" && df -h | grep -v tmpfs | grep -v devtmpfs && \
echo "" && echo "=== 网卡信息 ===" && ip -4 addr show 2>/dev/null | grep -E "^[0-9]+:|inet " && \
echo "" && echo "=== 默认网关 ===" && ip route show default 2>/dev/null | head -1 && \
echo "" && echo "=== DNS ===" && cat /etc/resolv.conf 2>/dev/null | grep nameserver"""

            stdout, stderr = client.exec_command(cmd, timeout)

            return {
                "success": True,
                "host": host,
                "output": stdout.strip()
            }

    except Exception as e:
        error_msg = str(e) or type(e).__name__
        return {
            "success": False,
            "host": host,
            "error": error_msg
        }
