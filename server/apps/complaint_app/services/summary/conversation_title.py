from __future__ import annotations

import re
import asyncio
from typing import AsyncGenerator

from utils.logger import get_logger
from apps.complaint_app.core.async_openai_client import get_async_openai_client
from apps.complaint_app.core.model_config import get_title_model

logger = get_logger(__name__)

OPENAI_TITLE_MODEL = get_title_model()


def _cleanup_text_for_title(text: str, limit: int = 5000) -> str:
    cleaned = (text or "").replace("\u3000", " ")
    cleaned = re.sub(r"[\r\n]+", "\n", cleaned)
    cleaned = re.sub(r"[\u200B-\u200D\uFEFF]", "", cleaned)
    if len(cleaned) > limit:
        cleaned = cleaned[:limit]
    return cleaned


def _truncate_title(title: str, max_len: int = 25) -> tuple[str, str]:
    trimmed = (title or "").strip().replace("\n", "").replace("\r", "")
    if len(trimmed) <= max_len:
        return trimmed, trimmed
    return trimmed[:max_len], f"{trimmed[:max_len]}…"


async def generate_conversation_title(text: str) -> str | None:
    client = get_async_openai_client()
    if client is None:
        logger.error("Async OpenAI client is not available for conversation title generation")
        return None
    snippet = _cleanup_text_for_title(text, 5000)
    if not snippet:
        logger.error("Conversation title generation aborted: empty snippet")
        return None
    system_prompt = (
        "你是标题生成助手，请基于举报材料生成中文标题，格式严格为：关于【举报人】举报【被举报机构】【事项简述】。"
        "若缺字段用“未知举报人/未知机构/未知事项”填充；标题不超过25个中文字符；不要引号、前后空格或换行。"
    )
    user_prompt = f"举报材料如下：\n{snippet}\n\n请输出标题："
    for attempt in range(3):
        try:
            resp = await client.chat.completions.create(
                model=OPENAI_TITLE_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=60,
            )
            content = resp.choices[0].message.content if resp and resp.choices else ""
            if isinstance(content, list):
                content = "".join([seg.get("text", "") if isinstance(seg, dict) else str(seg) for seg in content])
            title = str(content or "").strip()
            if title:
                capped, _preview = _truncate_title(title, 25)
                return capped
        except Exception:
            if attempt >= 2:
                logger.error("Conversation title generation failed after retries", exc_info=True, extra={"snippet_len": len(snippet)})
                return None
            await asyncio.sleep(1 + attempt)
    logger.error("Conversation title generation returned empty result", extra={"snippet_len": len(snippet)})
    return None


async def generate_conversation_title_stream(text: str) -> AsyncGenerator[str, None]:
    """
    流式生成会话标题，逐步返回累积的标题片段。
    """
    client = get_async_openai_client()
    if client is None:
        logger.error("Async OpenAI client is not available for conversation title streaming")
        return

    snippet = _cleanup_text_for_title(text, 5000)
    if not snippet:
        logger.error("Conversation title streaming aborted: empty snippet")
        return

    system_prompt = (
        "你是标题生成助手，请基于举报材料生成中文标题，格式严格为：关于【举报人】举报【被举报机构】【事项简述】。"
        "若缺字段用“未知举报人/未知机构/未知事项”填充；标题不超过25个中文字符；不要引号、前后空格或换行。"
    )
    user_prompt = f"举报材料如下：\n{snippet}\n\n请输出标题："

    accumulated = ""
    try:
        stream = await client.chat.completions.create(
            model=OPENAI_TITLE_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=60,
            stream=True,
        )
        async for chunk in stream:
            if not chunk or not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            new_part = ""
            if hasattr(delta, "content") and delta.content:
                if isinstance(delta.content, list):
                    new_part = "".join([seg.get("text", "") if isinstance(seg, dict) else str(seg) for seg in delta.content])
                else:
                    new_part = str(delta.content)
            if new_part:
                accumulated += new_part
                yield accumulated
    except Exception:
        logger.error("Streaming conversation title failed", exc_info=True)
        return

    if accumulated:
        capped, _ = _truncate_title(accumulated, 25)
        yield capped
