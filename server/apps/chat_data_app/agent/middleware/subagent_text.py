"""子智能体文本处理：报告清洗、流式增量与工具调用标记剥离。"""

from __future__ import annotations

import re
from typing import Any

# 模型在无工具轮次把「本来要发的工具调用」当正文输出的残留标记（Qwen 系
# 在 tools=[] 请求下的已知退化形态）：<tool_call>...</tool_call> 整块，或只有
# 开头没有结尾的悬空块。
TOOL_CALL_MARKUP_RE = re.compile(r"<tool_call\b[^>]*>.*?(?:</tool_call>|$)", re.DOTALL)
TOOL_CALL_OPENER = "<tool_call"
TOOL_CALL_CLOSER = "</tool_call>"


def sanitize_report_text(text: str) -> str:
    """剥掉正文里的裸工具调用标记，避免垃圾 XML 混进回传主智能体的报告。"""
    return TOOL_CALL_MARKUP_RE.sub("", str(text or "")).strip()


class StreamingToolCallMarkupFilter:
    """流式剥除正文增量里的裸 ``<tool_call>`` 标记（整段版 ``sanitize_report_text``
    的流式对应物）。

    硬线摘工具轮次模型会把待发工具调用退化成正文标记（Qwen 系已知形态），
    该文本会经 ``subagent_message_delta`` 逐 token 流给前端——标记可能被切成
    任意小的碎片（``<`` / ``<tool`` / ``_call>``…），必须跨增量缓冲匹配。

    状态机三态：
    - normal：正常透传，遇到 ``<`` 进入 partial 判定；
    - partial：缓冲尾巴可能是 opener 前缀，暂扣不发；确认非 opener 后原样补发；
    - inside：确认进入标记块，静默吞掉直到 closer 完整出现（closer 自身可能
      被切碎，同样用后缀缓冲容忍）。

    消息结束 ``flush``：partial 未决说明只是普通 ``<`` 文本，原样补发；inside
    未闭合按悬空块处理吞到末尾（与整段版口径一致）。已知取舍：flush 出的
    残片不再作为增量补发（最多丢 opener 前缀长度的普通文本，且仅发生在
    标记被切碎的退化轮次），换取直播内容零垃圾 XML。
    """

    def __init__(self) -> None:
        self._inside = False
        self._pending = ""

    @staticmethod
    def _partial_suffix(text: str, token: str) -> str:
        """返回 text 末尾与 token 前缀重合的最长后缀（可能被切碎的 token 暂扣）。"""
        for length in range(min(len(text), len(token) - 1), 0, -1):
            if text.endswith(token[:length]):
                return text[-length:]
        return ""

    def feed(self, delta: str) -> str:
        out: list[str] = []
        text = self._pending + delta if self._pending else delta
        self._pending = ""
        while text:
            if self._inside:
                idx = text.find(TOOL_CALL_CLOSER)
                if idx == -1:
                    self._pending = self._partial_suffix(text, TOOL_CALL_CLOSER)
                    text = ""
                else:
                    text = text[idx + len(TOOL_CALL_CLOSER) :]
                    self._inside = False
                continue
            idx = text.find("<")
            if idx == -1:
                out.append(text)
                break
            out.append(text[:idx])
            text = text[idx:]
            if TOOL_CALL_OPENER.startswith(text):
                # 可能是 opener 的前缀（如 "<"、"<tool"），暂扣等下一个增量裁决
                self._pending = text
                text = ""
            elif text.startswith(TOOL_CALL_OPENER):
                self._inside = True
                text = text[len(TOOL_CALL_OPENER) :]
            else:
                # 与 opener 无关的普通 "<" 文本，透传后继续扫描
                out.append("<")
                text = text[1:]
        return "".join(out)

    def flush(self) -> str:
        pending, self._pending = self._pending, ""
        if self._inside:
            return ""  # 悬空标记块：吞到末尾
        if len(pending) >= len(TOOL_CALL_OPENER):
            return ""  # 正好停在 opener 全串上，按标记块开头处理
        return pending


def plain_text(content: Any) -> str:
    """把消息 content 归一化成纯文本（多模态 list 按行拼接）。"""
    if isinstance(content, list):
        return "\n".join(part if isinstance(part, str) else str(part) for part in content)
    return content if isinstance(content, str) else str(content or "")


def chunk_text_delta(content: Any) -> str:
    """从流式 chunk 的 content 里取纯文本增量。

    ``str`` 直接返回；content blocks 形式只取 ``text`` 块，其余块（工具调用
    等结构化载荷）不参与文本增量，避免 dict 被 ``str()`` 成调试噪音。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return ""


def chunk_reasoning_text(chunk: Any) -> str:
    """从流式 chunk 提取思考增量。

    口径与 handlers ``_extract_reasoning_content`` 一致（OpenAI compatible
    的 ``reasoning_content`` / ``reasoning`` / ``reasoning_text``），但此处
    面向 middleware 层、不依赖 handlers；展示名替换只在 handlers 出口做。
    """
    for attr in ("additional_kwargs", "response_metadata"):
        value = getattr(chunk, attr, None)
        if not isinstance(value, dict):
            continue
        for key in ("reasoning_content", "reasoning", "reasoning_text"):
            reasoning = value.get(key)
            if isinstance(reasoning, str) and reasoning:
                return reasoning
    return ""
