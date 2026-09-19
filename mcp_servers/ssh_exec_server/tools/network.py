"""网络流量分析和诊断脚本"""
from mcp_servers.common.ssh_client import get_ssh_client
from mcp_servers.common.validation import validate_host

import re
import time
from datetime import datetime

def _format_bytes(bytes_val: int) -> str:
    """格式化字节数为人类可读格式"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_val < 1024:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024
    return f"{bytes_val:.1f} PB"

def execute(host: str, timeout: int = 30) -> dict:
    """分析网络流量和连接

    Args:
        host: 目标主机
        timeout: 超时时间

    Returns:
        网络流量信息
    """
    try:
        with get_ssh_client(host) as client:
            result_parts = []

            # 记录开始时间
            start_time = datetime.now()

            # 1. 统一采样（网卡 + 连接，只等待一次）
            interval = 3
            skip_prefixes = ('lo', 'veth', 'docker', 'br-', 'virbr')

            def parse_dev(output):
                stats = {}
                for line in output.strip().split('\n')[2:]:
                    if ':' not in line:
                        continue
                    parts = line.split(':')
                    iface = parts[0].strip()
                    if iface.startswith(skip_prefixes):
                        continue
                    values = parts[1].split()
                    if len(values) >= 9:
                        stats[iface] = (int(values[0]), int(values[8]))  # rx, tx
                return stats

            def parse_conn_stats(output):
                """解析 ss -tin 输出，返回 {peer_ip: [bytes_recv, bytes_sent, conn_count]}"""
                stats = {}
                lines = output.strip().split('\n')
                i = 0
                while i < len(lines):
                    line = lines[i].strip()
                    if not line or line.startswith('State') or line.startswith('Netid'):
                        i += 1
                        continue

                    if line.startswith(('ESTAB', 'TIME-WAIT', 'CLOSE-WAIT', 'FIN-WAIT', 'SYN-SENT')):
                        parts = line.split()
                        if len(parts) >= 5:
                            peer_addr = parts[4].rsplit(':', 1)[0]

                            if i + 1 < len(lines):
                                detail = lines[i + 1]
                                bytes_recv = 0
                                bytes_sent = 0
                                for part in detail.split():
                                    if part.startswith('bytes_received:'):
                                        bytes_recv = int(part.split(':')[1])
                                    elif part.startswith('bytes_acked:'):
                                        bytes_sent = int(part.split(':')[1])

                                if not peer_addr.startswith(('127.', '0.0.0.')):
                                    if peer_addr not in stats:
                                        stats[peer_addr] = [0, 0, 0]
                                    stats[peer_addr][0] += bytes_recv
                                    stats[peer_addr][1] += bytes_sent
                                    stats[peer_addr][2] += 1
                                i += 1
                    i += 1
                return stats

            # 第一次采样（网卡 + 连接）
            dev_out1, _ = client.exec_command("cat /proc/net/dev", timeout)
            conn_out1, _ = client.exec_command("ss -tin 2>/dev/null", timeout)
            dev_stats1 = parse_dev(dev_out1)
            conn_stats1 = parse_conn_stats(conn_out1)

            # 等待
            time.sleep(interval)

            # 第二次采样（网卡 + 连接）
            dev_out2, _ = client.exec_command("cat /proc/net/dev", timeout)
            conn_out2, _ = client.exec_command("ss -tin 2>/dev/null", timeout)
            dev_stats2 = parse_dev(dev_out2)
            conn_stats2 = parse_conn_stats(conn_out2)

            # 网卡流量
            result_parts.append(f"=== 网卡流量（{interval}秒采样）===")
            result_parts.append(f"{'网卡':<10} {'接收速率':<14} {'发送速率':<14} {'累计接收':<12} {'累计发送':<12}")

            for iface in dev_stats2:
                if iface in dev_stats1:
                    rx_rate = (dev_stats2[iface][0] - dev_stats1[iface][0]) // interval
                    tx_rate = (dev_stats2[iface][1] - dev_stats1[iface][1]) // interval
                    rx_total = dev_stats2[iface][0]
                    tx_total = dev_stats2[iface][1]
                    result_parts.append(
                        f"{iface:<10} {_format_bytes(rx_rate)+'/s':<14} {_format_bytes(tx_rate)+'/s':<14} "
                        f"{_format_bytes(rx_total):<12} {_format_bytes(tx_total):<12}"
                    )

            # 计算连接速率
            inbound_rates = {}   # {peer_ip: (rate, conn_count)}
            outbound_rates = {}

            for ip, (recv2, sent2, cnt2) in conn_stats2.items():
                recv1, sent1, _ = conn_stats1.get(ip, (0, 0, 0))
                recv_rate = (recv2 - recv1) // interval
                sent_rate = (sent2 - sent1) // interval
                if recv_rate > 0:
                    inbound_rates[ip] = (recv_rate, cnt2)
                if sent_rate > 0:
                    outbound_rates[ip] = (sent_rate, cnt2)

            result_parts.append(f"\n=== 入站流量 TOP5（{interval}秒采样）===")
            if inbound_rates:
                top5_in = sorted(inbound_rates.items(), key=lambda x: -x[1][0])[:5]
                result_parts.append(f"{'连接数':<8} {'速率':<14} {'来源IP'}")
                for ip, (rate, cnt) in top5_in:
                    result_parts.append(f"{cnt:<8} {_format_bytes(rate)+'/s':<14} {ip}")
            else:
                result_parts.append("(无入站流量)")

            result_parts.append(f"\n=== 出站流量 TOP5（{interval}秒采样）===")
            if outbound_rates:
                top5_out = sorted(outbound_rates.items(), key=lambda x: -x[1][0])[:5]
                result_parts.append(f"{'连接数':<8} {'速率':<14} {'目标IP'}")
                for ip, (rate, cnt) in top5_out:
                    result_parts.append(f"{cnt:<8} {_format_bytes(rate)+'/s':<14} {ip}")
            else:
                result_parts.append("(无出站流量)")

            # 4. 监听端口
            listen_cmd = "ss -tlnp 2>/dev/null | head -20"
            listen_out, _ = client.exec_command(listen_cmd, timeout)
            result_parts.append("\n=== 监听端口 ===")
            result_parts.append(listen_out.strip())

            # 添加统计时间到开头
            end_time = datetime.now()
            time_header = f"统计时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')} - {end_time.strftime('%H:%M:%S')}\n"

            return {
                "success": True,
                "host": host,
                "output": time_header + "\n".join(result_parts)
            }

    except Exception as e:
        error_msg = str(e) or type(e).__name__
        return {
            "success": False,
            "host": host,
            "error": error_msg
        }

def ping(host: str, target: str, count: int = 3, timeout: int = 30) -> dict:
    """Ping 连通性检测

    Args:
        host: 执行 ping 的主机
        target: 目标地址（IP 或域名）
        count: ping 次数，默认 3
        timeout: 超时时间

    Returns:
        ping 结果
    """
    try:
        # 安全校验
        valid, err = validate_host(target)
        if not valid:
            return {"success": False, "error": err}

        if count < 1 or count > 20:
            return {"success": False, "error": "count 必须在 1-20 之间"}

        with get_ssh_client(host) as client:
            # ping 命令
            cmd = f"ping -c {count} -W 2 {target} 2>&1"
            stdout, _ = client.exec_command(cmd, timeout=timeout)

            # 解析结果
            output = stdout.strip()

            # 判断是否成功
            success = "bytes from" in output.lower() or "ttl=" in output.lower()

            # 提取统计信息
            stats = {}
            # 匹配: 4 packets transmitted, 4 received, 0% packet loss
            loss_match = re.search(r'(\d+)\s+packets transmitted.*?(\d+)\s+received.*?(\d+(?:\.\d+)?%)\s+packet loss', output)
            if loss_match:
                stats["transmitted"] = int(loss_match.group(1))
                stats["received"] = int(loss_match.group(2))
                stats["packet_loss"] = loss_match.group(3)

            # 匹配: rtt min/avg/max/mdev = 0.123/0.456/0.789/0.012 ms
            rtt_match = re.search(r'rtt min/avg/max/mdev\s*=\s*([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)', output)
            if rtt_match:
                stats["rtt_min"] = f"{rtt_match.group(1)} ms"
                stats["rtt_avg"] = f"{rtt_match.group(2)} ms"
                stats["rtt_max"] = f"{rtt_match.group(3)} ms"

            return {
                "success": True,
                "host": host,
                "target": target,
                "reachable": success,
                "stats": stats,
                "output": output
            }

    except Exception as e:
        return {
            "success": False,
            "host": host,
            "target": target,
            "error": str(e) or type(e).__name__
        }

def port_check(host: str, target: str, port: int, timeout: int = 10) -> dict:
    """端口连通性检测

    Args:
        host: 执行检测的主机
        target: 目标地址
        port: 目标端口
        timeout: 超时时间

    Returns:
        端口检测结果
    """
    try:
        # 安全校验
        valid, err = validate_host(target)
        if not valid:
            return {"success": False, "error": err}

        if port < 1 or port > 65535:
            return {"success": False, "error": "端口必须在 1-65535 之间"}

        with get_ssh_client(host) as client:
            # 使用 Python socket 探测端口
            cmd = f"python3 -c \"import socket; socket.create_connection(('{target}', {port}), timeout=2).close(); print('PORT_OPEN')\" 2>&1 || echo 'PORT_CLOSED'"
            stdout, _ = client.exec_command(cmd, timeout=timeout)
            output = stdout.strip()
            is_open = "PORT_OPEN" in output

            return {
                "success": True,
                "host": host,
                "target": target,
                "port": port,
                "is_open": is_open,
                "output": f"{target}:{port} {'开放' if is_open else '不可达'}"
            }

    except Exception as e:
        return {
            "success": False,
            "host": host,
            "target": target,
            "port": port,
            "error": str(e) or type(e).__name__
        }

def conn_stat(host: str, timeout: int = 30) -> dict:
    """TCP 连接状态统计

    Args:
        host: 目标主机
        timeout: 超时时间

    Returns:
        连接状态统计
    """
    try:
        with get_ssh_client(host) as client:
            result_parts = []

            # 1. 连接状态统计（ss -s）
            stat_cmd = "ss -s 2>/dev/null"
            stat_out, _ = client.exec_command(stat_cmd, timeout)
            result_parts.append("=== 连接概览 ===")
            result_parts.append(stat_out.strip())

            # 2. 各状态详细统计
            state_cmd = "ss -tan 2>/dev/null | awk 'NR>1 {print $1}' | sort | uniq -c | sort -rn"
            state_out, _ = client.exec_command(state_cmd, timeout)
            result_parts.append("\n=== 各状态连接数 ===")
            result_parts.append(state_out.strip() if state_out.strip() else "(无连接)")

            # 3. TIME_WAIT 来源 TOP（常见问题）
            tw_cmd = "ss -tan state time-wait 2>/dev/null | awk 'NR>1 {print $4}' | cut -d: -f1 | sort | uniq -c | sort -rn | head -5"
            tw_out, _ = client.exec_command(tw_cmd, timeout)
            if tw_out.strip():
                result_parts.append("\n=== TIME_WAIT 来源 TOP5 ===")
                result_parts.append(tw_out.strip())

            # 4. CLOSE_WAIT 详情（可能连接泄漏）
            cw_cmd = "ss -tanp state close-wait 2>/dev/null | head -20"
            cw_out, _ = client.exec_command(cw_cmd, timeout)
            if cw_out.strip() and "State" in cw_out:
                result_parts.append("\n=== CLOSE_WAIT 连接（可能泄漏）===")
                result_parts.append(cw_out.strip())

            return {
                "success": True,
                "host": host,
                "output": "\n".join(result_parts)
            }

    except Exception as e:
        return {
            "success": False,
            "host": host,
            "error": str(e) or type(e).__name__
        }
