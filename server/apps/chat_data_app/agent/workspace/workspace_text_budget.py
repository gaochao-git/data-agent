"""file_read 正文预算的共享分配算法（local 与 sandbox 后端共用）。"""

from __future__ import annotations

MAX_FILE_READ_LINE_CHARS = 500


def allocate_file_read_line_budgets(line_lengths: list[int], max_chars: int) -> list[int]:
    """按源顺序分配正文预算，单行最多保留固定字符数。"""
    budgets: list[int] = []
    remaining = max(0, int(max_chars))
    for original_length in line_lengths:
        separator_chars = 1 if budgets else 0
        if remaining < separator_chars:
            break
        desired_chars = min(max(0, int(original_length)), MAX_FILE_READ_LINE_CHARS)
        available_chars = remaining - separator_chars
        if desired_chars > available_chars:
            if available_chars <= 0 and desired_chars > 0:
                break
            budgets.append(max(0, available_chars))
            break
        budgets.append(desired_chars)
        remaining -= desired_chars + separator_chars
    return budgets
