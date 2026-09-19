"""工具输出的截断与调试日志（自 fullstack-langgraph 平移）。"""

import os
from datetime import datetime
from pathlib import Path

from mcp_servers.common.config import CHAT_DATA_HOME


def _log_dir() -> Path:
    log_path = Path(CHAT_DATA_HOME) / "logs" / "mcp_servers"
    log_path.mkdir(parents=True, exist_ok=True)
    return log_path


def debug_log(msg: str):
    """按天追加调试日志（仅诊断排障用，非业务日志）。"""
    log_file = _log_dir() / f"{datetime.now().strftime('%Y-%m-%d')}.log"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {msg}\n")


def mcp_server_tmp_dir() -> Path:
    """兼容别名：临时目录统一由 config 提供。"""
    from mcp_servers.common.config import mcp_server_tmp_dir as _tmp_dir

    return _tmp_dir()


def truncate_output(
    output: str,
    max_size_kb: int = 25,
    max_lines: int = None,
    preserve_header: bool = False
) -> dict:
    """通用输出截断函数，防止工具返回过大内容打爆 LLM 上下文

    Args:
        output: 原始输出内容
        max_size_kb: 最大输出大小（KB），默认 25KB
        max_lines: 最大行数限制，None 表示不限制
        preserve_header: 是否保留首行作为表头（不计入限制）

    Returns:
        dict: {
            "content": str,              # 截断后的内容
            "truncated": bool,           # 是否发生截断
            "metadata": {
                "total_lines": int,      # 原始总行数
                "returned_lines": int,   # 返回行数
                "output_size_kb": float, # 输出大小（KB）
                "truncated_by": str      # 截断原因："size"|"lines"|"both"|None
            },
            "warning": str|None          # 截断警告信息
        }
    """
    MAX_SIZE_BYTES = max_size_kb * 1024

    lines = output.strip().split('\n') if output.strip() else []
    total_lines = len(lines)

    if total_lines == 0:
        return {
            "content": "",
            "truncated": False,
            "metadata": {
                "total_lines": 0,
                "returned_lines": 0,
                "output_size_kb": 0.0,
                "truncated_by": None
            },
            "warning": None
        }

    # 处理表头
    header = None
    content_lines = lines

    if preserve_header and lines:
        header = lines[0]
        content_lines = lines[1:]

    # 第一步：按行数截断
    truncated_by_lines = False
    if max_lines is not None and len(content_lines) > max_lines:
        content_lines = content_lines[:max_lines]
        truncated_by_lines = True

    # 重新组装（带表头）
    if header:
        assembled_output = header + '\n' + '\n'.join(content_lines)
    else:
        assembled_output = '\n'.join(content_lines)

    # 第二步：按字节数截断
    truncated_by_size = False
    output_bytes = len(assembled_output.encode('utf-8'))

    if output_bytes > MAX_SIZE_BYTES:
        truncated_by_size = True
        byte_budget = MAX_SIZE_BYTES
        truncated_lines = []

        # 预留表头空间
        if header:
            header_bytes = len(header.encode('utf-8')) + 1
            byte_budget -= header_bytes
            truncated_lines.append(header)

        # 逐行累加到预算内
        for line in content_lines:
            line_bytes = len(line.encode('utf-8')) + 1
            if byte_budget - line_bytes < 0:
                break
            truncated_lines.append(line)
            byte_budget -= line_bytes

        assembled_output = '\n'.join(truncated_lines)
        actual_lines = len(truncated_lines) - (1 if header else 0)
    else:
        actual_lines = len(content_lines)

    # 确定截断原因
    truncated = truncated_by_lines or truncated_by_size
    if truncated_by_lines and truncated_by_size:
        truncated_by = "both"
    elif truncated_by_lines:
        truncated_by = "lines"
    elif truncated_by_size:
        truncated_by = "size"
    else:
        truncated_by = None

    # 生成警告信息
    warning = None
    if truncated:
        reasons = []
        if truncated_by_lines:
            reasons.append(f"行数超过 {max_lines} 限制")
        if truncated_by_size:
            reasons.append(f"大小超过 {max_size_kb}KB 限制")

        content_total = total_lines - (1 if header else 0)
        warning = (
            f"输出已截断（{', '.join(reasons)}）| "
            f"原始 {content_total} 行，返回 {actual_lines} 行 | "
            f"建议使用更精确的过滤条件"
        )

    return {
        "content": assembled_output,
        "truncated": truncated,
        "metadata": {
            "total_lines": total_lines - (1 if header else 0),
            "returned_lines": actual_lines,
            "output_size_kb": round(len(assembled_output.encode('utf-8')) / 1024, 2),
            "truncated_by": truncated_by
        },
        "warning": warning
    }
