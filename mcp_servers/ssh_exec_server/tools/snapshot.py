"""现场快照采集脚本"""
from mcp_servers.common.ssh_client import get_ssh_client
from mcp_servers.common.output import truncate_output
from mcp_servers.common.validation import validate_host

import re
import time
from typing import Any, Dict, List, Optional

SAFE_SINCE_PATTERN = re.compile(r"^[a-zA-Z0-9 :._-]+$")
SAFE_SERVICE_PATTERN = re.compile(r"^[a-zA-Z0-9_.@-]+$")

def _q_single(text: str) -> str:
    return "'" + text.replace("'", "'\"'\"'") + "'"

def _build_snapshot_commands(since: Optional[str], service: Optional[str]) -> List[Dict[str, Any]]:
    since_clause = ""
    if since:
        since_clause = f" --since {_q_single(since)}"

    service_clause = ""
    if service:
        service_clause = f" -u {service}"

    commands = [
        {
            "id": "basic",
            "name": "主机基础信息",
            "timeout_s": 10,
            "cmd": "echo '=== Host ==='; hostname; echo; date; echo; uptime",
        },
        {
            "id": "cpu_mem",
            "name": "CPU/内存概览",
            "timeout_s": 12,
            "cmd": "echo '=== CPU ==='; nproc; echo; echo '=== Mem ==='; free -h",
        },
        {
            "id": "disk_fs",
            "name": "文件系统使用率",
            "timeout_s": 12,
            "cmd": "df -hT | grep -v tmpfs | grep -v devtmpfs | head -50",
        },
        {
            "id": "top_cpu",
            "name": "CPU Top 进程",
            "timeout_s": 12,
            "cmd": "ps aux --sort=-%cpu | head -20",
        },
        {
            "id": "top_mem",
            "name": "内存 Top 进程",
            "timeout_s": 12,
            "cmd": "ps aux --sort=-%mem | head -20",
        },
        {
            "id": "net_summary",
            "name": "网络连接概览",
            "timeout_s": 12,
            "cmd": "ss -s 2>/dev/null; echo; ss -tlnp 2>/dev/null | head -30",
        },
        {
            "id": "journal_err",
            "name": "近期错误日志",
            "timeout_s": 15,
            "cmd": f"sudo journalctl --no-pager -p err..alert -n 200{since_clause}{service_clause}",
        },
    ]

    commands.extend(
        [
            {
                "id": "io_wait",
                "name": "IO 指标采样",
                "timeout_s": 16,
                "cmd": """iostat -xd 1 2 2>/dev/null | awk '
/^Device:/ {
    seen++;
    if (seen == 2) {
        print;
        next;
    }
}
seen == 2 {
    print;
}
' || vmstat 1 2""",
            },
            {
                "id": "conn_state",
                "name": "连接状态统计",
                "timeout_s": 12,
                "cmd": "ss -tan 2>/dev/null | awk 'NR>1 {print $1}' | sort | uniq -c | sort -rn",
            },
        ]
    )
    return commands

def _make_summary(host: str, sections: List[Dict[str, Any]]) -> Dict[str, Any]:
    warnings: List[str] = []
    health = "ok"
    fs_hotspots = []

    disk_section = next((s for s in sections if s["id"] == "disk_fs"), None)
    if disk_section:
        for line in disk_section.get("stdout", "").splitlines():
            if "%" not in line:
                continue
            parts = line.split()
            if len(parts) < 7:
                continue
            use_text = parts[5]
            if use_text.endswith("%") and use_text[:-1].isdigit():
                use_val = int(use_text[:-1])
                if use_val >= 85:
                    fs_hotspots.append({"mount": parts[6], "use_percent": use_val})

    if fs_hotspots:
        hotspots_text = ", ".join([f"{x['mount']}={x['use_percent']}%" for x in fs_hotspots[:5]])
        warnings.append(f"文件系统高使用率: {hotspots_text}")

    journal_section = next((s for s in sections if s["id"] == "journal_err"), None)
    if journal_section:
        lines = [l for l in journal_section.get("stdout", "").splitlines() if l.strip()]
        if lines and "No entries" not in journal_section.get("stdout", ""):
            warnings.append(f"近期存在错误日志，返回 {len(lines)} 行（已截断保护）")

    timed_out_sections = [s["name"] for s in sections if s.get("timed_out")]
    if timed_out_sections:
        warnings.append(f"以下模块采集超时（已保护性中断）: {', '.join(timed_out_sections)}")

    if warnings:
        health = "warning"

    return {
        "host": host,
        "health": health,
        "warnings": warnings,
        "section_count": len(sections),
    }

def execute(host: str, since: Optional[str] = "30m", service: Optional[str] = None, timeout: int = 90) -> dict:
    """采集现场快照，返回完整结果。

    如果结果过大，agent 层会自动归档到当前线程 workspace artifacts，
    后续可通过 workspace_run 按需继续分析。
    """
    try:
        valid, err = validate_host(host)
        if not valid:
            return {"success": False, "error": err}

        if since and not SAFE_SINCE_PATTERN.match(since):
            return {"success": False, "error": "since 参数包含不安全字符"}

        if service and not SAFE_SERVICE_PATTERN.match(service):
            return {"success": False, "error": "service 参数包含不安全字符"}

        if timeout < 20 or timeout > 300:
            return {"success": False, "error": "timeout 必须在 20-300 秒之间"}

        start_ts = time.time()
        sections: List[Dict[str, Any]] = []

        commands = _build_snapshot_commands(since=since, service=service)

        with get_ssh_client(host) as client:
            ionice_check, _ = client.exec_command("command -v ionice >/dev/null 2>&1 && echo yes || echo no", 8)
            timeout_check, _ = client.exec_command("command -v timeout >/dev/null 2>&1 && echo yes || echo no", 8)

            priority_prefix = "nice -n 19"
            if ionice_check.strip() == "yes":
                priority_prefix = "ionice -c3 nice -n 19"
            has_timeout_cmd = timeout_check.strip() == "yes"

            for item in commands:
                elapsed = time.time() - start_ts
                if elapsed >= timeout:
                    sections.append(
                        {
                            "id": item["id"],
                            "name": item["name"],
                            "command": item["cmd"],
                            "stdout": "",
                            "stderr": "skip: snapshot 总预算已用尽",
                            "duration_ms": 0,
                            "timed_out": True,
                            "truncated": False,
                        }
                    )
                    continue

                raw_cmd = item["cmd"]
                cmd_timeout = item["timeout_s"]
                if has_timeout_cmd:
                    wrapped_cmd = f"{priority_prefix} timeout {cmd_timeout}s sh -c {_q_single(raw_cmd)}"
                else:
                    wrapped_cmd = f"{priority_prefix} sh -c {_q_single(raw_cmd)}"

                cmd_start = time.time()
                stdout, stderr = client.exec_command(wrapped_cmd, timeout=max(cmd_timeout + 8, 12))
                duration_ms = int((time.time() - cmd_start) * 1000)

                trunc = truncate_output(stdout or "", max_size_kb=24, max_lines=220)
                stderr_trunc = truncate_output(stderr or "", max_size_kb=4, max_lines=60)
                stderr_text = stderr_trunc["content"] if stderr_trunc["content"] else ""
                timed_out = "timed out" in stderr_text.lower()

                sections.append(
                    {
                        "id": item["id"],
                        "name": item["name"],
                        "command": raw_cmd,
                        "stdout": trunc["content"],
                        "stderr": stderr_text,
                        "duration_ms": duration_ms,
                        "timed_out": timed_out,
                        "truncated": trunc["truncated"],
                        "truncated_meta": trunc["metadata"],
                    }
                )

        summary = _make_summary(host=host, sections=sections)

        return {
            "success": True,
            "host": host,
            "since": since,
            "service": service,
            "duration_ms": int((time.time() - start_ts) * 1000),
            "summary": summary,
            "sections": sections,
        }

    except Exception as e:
        return {"success": False, "host": host, "error": str(e)}
