"""生成速率公共口径：实时滑窗采样与完成态解码吞吐。"""

from __future__ import annotations

import re
from collections import deque

SPEED_WINDOW_MS = 2000
SPEED_EMIT_INTERVAL_MS = 500
SPEED_MIN_SPAN_MS = 300

# 与前端 estimateTokensFromText 同口径：CJK 字符 1 token/字，其余非空白 4 字符/token
_SPEED_CJK_CHAR_REGEX = re.compile(r"[\u3400-\u9fff\uf900-\ufaff\u3000-\u303f\uff01-\uff5e]")


def estimate_stream_tokens(text: str) -> int:
    """流式文本的 token 粗估，仅用于生成中的实时速率展示。"""
    cjk_count = 0
    other_count = 0
    for char in text:
        if char.isspace():
            continue
        if _SPEED_CJK_CHAR_REGEX.match(char):
            cjk_count += 1
        else:
            other_count += 1
    return cjk_count + (other_count + 3) // 4


def decode_duration_ms(
    *,
    first_token_wall_ms: int | None,
    stream_ended_wall_ms: int | None,
    tool_duration_ms: int = 0,
) -> int | None:
    """首 token → 流结束的墙钟，扣除窗口内工具执行时长；TTFT 不进分母。"""
    if first_token_wall_ms is None or stream_ended_wall_ms is None:
        return None
    duration_ms = (int(stream_ended_wall_ms) - int(first_token_wall_ms)) - max(int(tool_duration_ms), 0)
    return duration_ms if duration_ms > 0 else None


def decode_tokens_per_second(*, output_tokens: int, duration_ms: int | None) -> int | None:
    """完成态解码吞吐：输出 token ÷ 解码窗口。"""
    if output_tokens <= 0 or duration_ms is None or duration_ms <= 0:
        return None
    return round(output_tokens / (duration_ms / 1000))


class GenerationSpeedSampler:
    """实时速率滑窗：按可见文本粗估 token，节流产出当前窗口速率。"""

    def __init__(self) -> None:
        self.samples: deque[tuple[int, int]] = deque()
        self.last_emit_ms = 0
        self.first_visible_token_wall_ms: int | None = None

    def observe(self, text: str, *, now_ms: int) -> int:
        """记录一段可见输出；返回该段粗估 token 数。"""
        tokens = estimate_stream_tokens(text)
        if tokens <= 0:
            return 0
        if self.first_visible_token_wall_ms is None:
            self.first_visible_token_wall_ms = now_ms
        self.samples.append((now_ms, tokens))
        return tokens

    def maybe_snapshot(self, *, now_ms: int) -> dict[str, float | int] | None:
        """按节流间隔计算当前窗口速率；静默或间隔未到返回 None。"""
        if now_ms - self.last_emit_ms < SPEED_EMIT_INTERVAL_MS:
            return None
        window_start_ms = now_ms - SPEED_WINDOW_MS
        while self.samples and self.samples[0][0] <= window_start_ms:
            self.samples.popleft()
        if not self.samples:
            return None
        window_tokens = sum(tokens for _, tokens in self.samples)
        earliest_ms = min(timestamp for timestamp, _ in self.samples)
        span_ms = max(now_ms - earliest_ms, SPEED_MIN_SPAN_MS)
        self.last_emit_ms = now_ms
        return {
            "tokens_per_second": round(window_tokens / (span_ms / 1000), 1),
            "window_ms": SPEED_WINDOW_MS,
        }
