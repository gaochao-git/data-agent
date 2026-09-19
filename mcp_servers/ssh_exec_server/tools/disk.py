"""磁盘使用分析脚本"""
from mcp_servers.common.ssh_client import get_ssh_client
from mcp_servers.common.validation import validate_path

def execute(host: str, path: str = "/", detail: bool = False, io: bool = False, timeout: int = 30) -> dict:
    """分析磁盘使用情况

    Args:
        host: 目标主机
        path: 分析路径
        detail: 详细分析（目录大小排名 + 大文件查找）
        io: 查看磁盘IO统计（util、iowait、top IO进程）
        timeout: 超时时间

    Returns:
        磁盘使用信息
    """
    try:
        # 参数验证
        is_valid, error_msg = validate_path(path)
        if not is_valid:
            return {"success": False, "host": host, "error": error_msg}

        with get_ssh_client(host) as client:
            result_parts = []
            safe_path = path.rstrip('/') if path != '/' else '/'

            # 低优先级执行，尽量减少对线上业务影响
            priority_prefix = "nice -n 19"
            ionice_check, _ = client.exec_command("command -v ionice >/dev/null 2>&1 && echo yes || echo no", timeout)
            if ionice_check.strip() == "yes":
                priority_prefix = "ionice -c3 nice -n 19"

            timeout_check, _ = client.exec_command("command -v timeout >/dev/null 2>&1 && echo yes || echo no", timeout)
            has_timeout = timeout_check.strip() == "yes"

            def wrap_heavy_cmd(raw_cmd: str, cmd_timeout: int = 20) -> str:
                # raw_cmd 由固定模板拼接，不包含外部 shell 元字符
                if has_timeout:
                    return f'{priority_prefix} timeout {cmd_timeout}s sh -c "{raw_cmd}"'
                return f'{priority_prefix} sh -c "{raw_cmd}"'

            # IO 模式：显示磁盘IO统计和top IO进程
            if io:
                # 检查 iostat 是否可用
                check_out, _ = client.exec_command("which iostat 2>/dev/null", timeout)
                if not check_out.strip():
                    return {
                        "success": False,
                        "host": host,
                        "error": "iostat 命令不可用，请安装 sysstat 包：yum install sysstat 或 apt install sysstat"
                    }

                # 执行 iostat -xd 2 1：扩展统计，2秒采样1次
                iostat_out, _ = client.exec_command("iostat -xd 2 1", timeout)
                result_parts.append("=== 磁盘IO统计 ===")
                result_parts.append(iostat_out.strip())

                # 显示当前 iowait
                vmstat_out, _ = client.exec_command("vmstat 1 2 | tail -1 | awk '{print $16}'", timeout)
                iowait = vmstat_out.strip()
                if iowait:
                    result_parts.append(f"\n=== CPU iowait ===")
                    result_parts.append(f"当前 iowait: {iowait}%")

                # 显示 top IO 进程
                # 优先用 pidstat，否则用采样两次计算差值的方式（类似 iotop 原理）
                pidstat_check, _ = client.exec_command("which pidstat 2>/dev/null", timeout)
                if pidstat_check.strip():
                    # pidstat -d 采样磁盘IO，2秒采样1次
                    pidstat_out, _ = client.exec_command("pidstat -d 2 1 2>/dev/null", timeout)
                    if pidstat_out.strip():
                        result_parts.append(f"\n=== Top IO 进程 ===")
                        result_parts.append(pidstat_out.strip())
                else:
                    # 备用方案：采样两次 /proc/<pid>/io，计算差值得到速率（iotop 原理）
                    iotop_cmd = """
TMP1=$(mktemp)
TMP2=$(mktemp)
# 第一次采样
for pid in $(ls /proc 2>/dev/null | grep -E '^[0-9]+$'); do
    if [ -r /proc/$pid/io ] 2>/dev/null; then
        comm=$(cat /proc/$pid/comm 2>/dev/null)
        rb=$(grep -w read_bytes /proc/$pid/io 2>/dev/null | awk '{print $2}')
        wb=$(grep -w write_bytes /proc/$pid/io 2>/dev/null | awk '{print $2}')
        [ -n "$rb" ] && echo "$pid $comm $rb $wb" >> $TMP1
    fi
done
sleep 2
# 第二次采样
for pid in $(ls /proc 2>/dev/null | grep -E '^[0-9]+$'); do
    if [ -r /proc/$pid/io ] 2>/dev/null; then
        comm=$(cat /proc/$pid/comm 2>/dev/null)
        rb=$(grep -w read_bytes /proc/$pid/io 2>/dev/null | awk '{print $2}')
        wb=$(grep -w write_bytes /proc/$pid/io 2>/dev/null | awk '{print $2}')
        [ -n "$rb" ] && echo "$pid $comm $rb $wb" >> $TMP2
    fi
done
# 计算差值
echo "PID      COMMAND              READ/s       WRITE/s"
while read pid2 comm2 rb2 wb2; do
    line1=$(grep "^$pid2 " $TMP1 2>/dev/null)
    if [ -n "$line1" ]; then
        rb1=$(echo "$line1" | awk '{print $3}')
        wb1=$(echo "$line1" | awk '{print $4}')
        rd=$(( (rb2 - rb1) / 2 ))
        wd=$(( (wb2 - wb1) / 2 ))
        if [ $rd -gt 0 ] || [ $wd -gt 0 ]; then
            printf "%-8s %-20s %-12s %s\\n" "$pid2" "$comm2" "${rd}B/s" "${wd}B/s"
        fi
    fi
done < $TMP2 | sort -t' ' -k3 -rn | head -10
rm -f $TMP1 $TMP2
"""
                    iotop_out, _ = client.exec_command(iotop_cmd, timeout)
                    if iotop_out.strip():
                        result_parts.append(f"\n=== Top IO 进程 (2秒采样) ===")
                        result_parts.append(iotop_out.strip())

                return {
                    "success": True,
                    "host": host,
                    "output": "\n".join(result_parts)
                }

            # 默认模式：获取磁盘分区使用情况
            df_out, _ = client.exec_command("df -hT", timeout)
            result_parts.append("=== 磁盘分区 ===")
            result_parts.append(df_out.strip())

            # 只有启用 detail 时才分析目录和大文件
            if detail:
                # 阶段1：轻量目录概览（只看一层）
                du_scan_cmd = (
                    f"du -x -h --max-depth=1 '{safe_path}' 2>/dev/null | "
                    "grep -vE '^0[[:space:]]' | sort -hr | head -20"
                )
                du_cmd = wrap_heavy_cmd(du_scan_cmd, cmd_timeout=20)
                du_out, du_err = client.exec_command(du_cmd, timeout)
                result_parts.append("\n=== 目录大小 TOP10 ===")
                if du_out.strip():
                    top10 = "\n".join(du_out.strip().split('\n')[:10])
                    result_parts.append(top10)
                else:
                    result_parts.append("(无)")
                if "timed out" in du_err.lower():
                    result_parts.append("⚠️ 目录扫描超时，已返回部分结果（低优先级保护模式）")

                # 阶段2：受限大文件扫描（不跨文件系统 + 限制深度）
                find_scan_cmd = (
                    f"find '{safe_path}' -xdev -maxdepth 3 -type f -size +1G "
                    "-not -path '/proc/*' -not -path '/sys/*' -not -path '/dev/*' "
                    "-not -path '/run/*' -not -path '/data/3306/mybackup/gfs/*' "
                    "2>/dev/null | head -50 | xargs -r ls -lh 2>/dev/null | head -10"
                )
                find_cmd = wrap_heavy_cmd(find_scan_cmd, cmd_timeout=20)
                find_out, find_err = client.exec_command(find_cmd, timeout)
                result_parts.append("\n=== 大文件 (>1GB) ===")
                result_parts.append(find_out.strip() if find_out.strip() else "(无)")
                if "timed out" in find_err.lower():
                    result_parts.append("⚠️ 大文件扫描超时，建议缩小 path 后重试（低优先级保护模式）")

            return {
                "success": True,
                "host": host,
                "output": "\n".join(result_parts)
            }

    except Exception as e:
        error_msg = str(e) or type(e).__name__
        return {
            "success": False,
            "host": host,
            "error": error_msg
        }
