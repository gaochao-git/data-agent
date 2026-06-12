from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, Optional, Tuple

from apps.complaint_app.core.async_openai_client import get_async_openai_client
from apps.complaint_app.core.llm.llm_client import extract_stream_deltas
from apps.complaint_app.core.model_config import get_summary_model
from utils.logger import get_logger
from utils.llm_thinking import build_thinking_extra_body
from utils.llm_prompt_metrics import record_llm_output_metrics, record_llm_prompt_metrics

logger = get_logger(__name__)

SUMMARY_FAILURE_TEXT = "提取摘要失败，请重试或联系管理员"
SUMMARY_HISTORY_SECTION_TITLE = "举报人历史事件回溯"
SUMMARY_HISTORY_PLACEHOLDER = "无"
SUMMARY_SIMILAR_SECTION_TITLE = "不同举报人相似举报回溯"

REQUIRED_SUMMARY_SECTIONS = [
    (SUMMARY_HISTORY_SECTION_TITLE, SUMMARY_HISTORY_PLACEHOLDER),
    (SUMMARY_SIMILAR_SECTION_TITLE, SUMMARY_HISTORY_PLACEHOLDER),
]

SUMMARY_LLM_MODEL = get_summary_model()
SUMMARY_BUNDLE_SCHEMA = "v2"

_SUMMARY_SYS_PROMPT_PATH = Path(__file__).with_name("sys_prompt.md")
_SUMMARY_SYS_PROMPT_CACHE: str | None = None
_DEFAULT_STRUCTURED = {"_schema": "v1", "sections": {}, "_raw": None}


def _summary_stream_log_enabled() -> bool:
    """控制摘要阶段流式增量日志，默认关闭。"""
    return str(os.getenv("COMPLAINT_SUMMARY_STREAM_LOG", "")).strip().lower() in {"1", "true", "yes", "y", "on"}


def _record_summary_prompt(stage: str, messages: list[dict[str, Any]], trace_context: dict[str, Any] | None = None) -> str:
    """记录摘要阶段实际发送给模型的 prompt 大小。"""
    metadata = {"business_stage": "summary_generation", **(trace_context or {})}
    return record_llm_prompt_metrics(stage, model=SUMMARY_LLM_MODEL, messages=messages, metadata=metadata)


def _record_summary_output(stage: str, call_id: str, output_text: str, first_token_ms: int, started_at: float, trace_context: dict[str, Any] | None = None) -> None:
    """记录摘要阶段模型输出大小与耗时。"""
    metadata = {"business_stage": "summary_generation", **(trace_context or {})}
    record_llm_output_metrics(stage, model=SUMMARY_LLM_MODEL, output_text=output_text, first_token_ms=first_token_ms, llm_duration_ms=int((time.monotonic() - started_at) * 1000), metadata=metadata, call_id=call_id)


def _load_sys_prompt() -> str:
    """Load and cache the system prompt for summary generation."""
    global _SUMMARY_SYS_PROMPT_CACHE
    if _SUMMARY_SYS_PROMPT_CACHE is not None:
        return _SUMMARY_SYS_PROMPT_CACHE
    try:
        _SUMMARY_SYS_PROMPT_CACHE = _SUMMARY_SYS_PROMPT_PATH.read_text(encoding="utf-8")
    except Exception as exc:
        logger.error("读取摘要系统提示失败: %s", exc)
        _SUMMARY_SYS_PROMPT_CACHE = ""
    return _SUMMARY_SYS_PROMPT_CACHE


def _strip_code_fences(text: str) -> str:
    """Remove leading/trailing code fences if present."""
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines:
            lines = lines[1:]
        while lines and lines[-1].strip() == "```":
            lines.pop()
        cleaned = "\n".join(lines).strip()
    return cleaned


def _log_model_output(text: str, *, stage: str) -> None:
    """Log raw model output for manual inspection."""
    if text is None:
        return
    if stage.endswith("_delta") and not _summary_stream_log_enabled():
        return
    logger.info("summary model output stage=%s content=%s", stage, text)


def _log_summary_preview(text: str, limit: int = 5000) -> str:
    """输出摘要阶段日志预览。"""
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return f"{value[:limit]}...(truncated)"


def _split_think_answer(text: str) -> Tuple[str, str]:
    cleaned = _strip_code_fences(text)
    if "</think>" in cleaned:
        thought, answer = cleaned.split("</think>", 1)
        thought = thought.replace("<think>", "").strip()
        answer = answer.strip()
        return thought, answer
    return "", cleaned.strip()


def _extract_first_json_object(text: str) -> Dict[str, Any]:
    """从文本中提取第一个 JSON 对象。"""
    cleaned = _strip_code_fences(str(text or ""))
    if not cleaned:
        return {}
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        pass
    start_idx = cleaned.find("{")
    end_idx = cleaned.rfind("}")
    if start_idx < 0 or end_idx <= start_idx:
        return {}
    fragment = cleaned[start_idx : end_idx + 1]
    try:
        parsed = json.loads(fragment)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _normalize_string_list(value: Any) -> list[str]:
    """规范化字符串数组。"""
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            items.append(text)
    return items


def _normalize_summary_profile(value: Any) -> Dict[str, Any]:
    """规范化举报画像字段。"""
    if not isinstance(value, dict):
        return {"reporter": "", "report_date": "", "reported_institution": "", "appeals": [], "title_base": ""}
    return {
        "reporter": str(value.get("reporter") or "").strip(),
        "report_date": str(value.get("report_date") or "").strip(),
        "reported_institution": str(value.get("reported_institution") or "").strip(),
        "appeals": _normalize_string_list(value.get("appeals")),
        "title_base": str(value.get("title_base") or "").strip(),
    }


def _normalize_law_refs(value: Any) -> list[dict[str, str]]:
    """规范化用户提及的法律依据。"""
    if not isinstance(value, list):
        return []
    refs: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            raw_text = str(item.get("raw_text") or item.get("raw") or item.get("text") or "").strip()
            normalized_law = str(item.get("normalized_law") or item.get("law") or item.get("name") or "").strip()
            cited_clause_text = str(item.get("cited_clause_text") or item.get("clause") or item.get("article") or "").strip()
        else:
            raw_text = str(item or "").strip()
            normalized_law = ""
            cited_clause_text = ""
        if raw_text or normalized_law or cited_clause_text:
            refs.append({"raw_text": raw_text, "normalized_law": normalized_law, "cited_clause_text": cited_clause_text})
    return refs


def _normalize_summary_complaints(value: Any) -> list[dict[str, Any]]:
    """规范化举报事项列表。"""
    if not isinstance(value, list):
        return []
    complaints: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            label = str(item.get("label") or "").strip()
            description = str(item.get("description") or "").strip()
            claim_text = str(item.get("claim_text") or "").strip()
            original_quotes = _normalize_string_list(item.get("original_quotes"))
            evidence_materials = _normalize_string_list(item.get("evidence_materials"))
            user_cited_laws = _normalize_law_refs(item.get("user_cited_laws"))
        else:
            label = str(item or "").strip()
            description = label
            claim_text = ""
            original_quotes = []
            evidence_materials = []
            user_cited_laws = []
        if not label and description:
            label = description
        if not label and claim_text:
            label = claim_text
        if not description and label:
            description = label
        if not claim_text:
            claim_text = description or label
        if label or description:
            complaints.append({
                "label": label,
                "description": description,
                "claim_text": claim_text,
                "original_quotes": original_quotes,
                "evidence_materials": evidence_materials,
                "user_cited_laws": user_cited_laws,
            })
    return complaints


def _normalize_reply_draft_context(value: Any) -> Dict[str, Any]:
    """规范化答复意见书上下文字段。"""
    if not isinstance(value, dict):
        return {"supplement_material": {"exists": False, "count_hint": 0, "dates": [], "sentence": "", "evidence_spans": []}}
    supplement = value.get("supplement_material") if isinstance(value.get("supplement_material"), dict) else {}
    evidence_spans: list[dict[str, str]] = []
    for row in supplement.get("evidence_spans") or []:
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "").strip()
        date = str(row.get("date") or "").strip()
        keyword = str(row.get("keyword") or "").strip()
        if text or date or keyword:
            evidence_spans.append({"text": text, "date": date, "keyword": keyword})
    return {
        "supplement_material": {
            "exists": bool(supplement.get("exists")),
            "count_hint": int(supplement.get("count_hint") or 0),
            "dates": _normalize_string_list(supplement.get("dates")),
            "sentence": str(supplement.get("sentence") or "").strip(),
            "evidence_spans": evidence_spans,
        }
    }


def normalize_summary_structured_payload(value: Any) -> Dict[str, Any]:
    """规范化摘要阶段输出的结构化字段。"""
    if not isinstance(value, dict):
        return {"complaints": [], "incident": "", "profile": {"reporter": "", "report_date": "", "reported_institution": "", "appeals": [], "title_base": ""}, "reply_draft_context": _normalize_reply_draft_context({})}
    return {
        "complaints": _normalize_summary_complaints(value.get("complaints")),
        "incident": str(value.get("incident") or "").strip(),
        "profile": _normalize_summary_profile(value.get("profile")),
        "reply_draft_context": _normalize_reply_draft_context(value.get("reply_draft_context")),
    }


def _build_summary_bundle_prompt() -> str:
    """构建摘要+结构化双载荷提示词。"""
    return (
        f"{_load_sys_prompt()}\n\n"
        "你必须只输出一个 JSON 对象，禁止输出 Markdown 正文、解释、代码块。"
        "JSON 格式必须严格为："
        "{\"summary_markdown\":\"\",\"structured_payload\":{\"complaints\":[{\"label\":\"\",\"description\":\"\",\"claim_text\":\"\",\"original_quotes\":[\"\"],\"evidence_materials\":[\"\"],\"user_cited_laws\":[{\"raw_text\":\"\",\"normalized_law\":\"\",\"cited_clause_text\":\"\"}]}],\"incident\":\"\",\"profile\":{\"reporter\":\"\",\"report_date\":\"\",\"reported_institution\":\"\",\"appeals\":[\"\"],\"title_base\":\"\"}}}。"
        "要求："
        "1. summary_markdown 字段内填写完整 Markdown 摘要正文。"
        "2. structured_payload.complaints 提取举报事项；label 为 8-20 字短标签，description/claim_text 为 1-2 句完整事项表述。"
        "3. structured_payload.incident 提取事情经过。"
        "4. structured_payload.profile 内提取举报人、举报日期、被举报机构、诉求、标题基础。"
        "5. 缺失字段返回空字符串或空数组，禁止编造。"
        "6. 不要输出任何 JSON 以外的内容。"
    )



def _build_summary_structured_only_prompt() -> str:
    """构建仅输出结构化字段的提示词。"""
    return _load_sys_prompt()

def _resolve_summary_bundle(raw_answer: str, raw_thought: str) -> Tuple[str, Dict[str, Any], str]:
    """解析摘要双载荷输出。"""
    final_answer, final_thought = _resolve_summary_result(raw_answer, raw_thought)
    parsed = _extract_first_json_object(final_answer)
    if parsed:
        summary_markdown = str(parsed.get("summary_markdown") or "").strip()
        structured_payload = normalize_summary_structured_payload(parsed.get("structured_payload"))
        return summary_markdown, structured_payload, final_thought
    return final_answer, normalize_summary_structured_payload({}), final_thought


def _resolve_summary_structured_only(raw_answer: str, raw_thought: str) -> Tuple[Dict[str, Any], str]:
    """解析仅结构化摘要输出。"""
    final_answer, final_thought = _resolve_summary_result(raw_answer, raw_thought)
    parsed = _extract_first_json_object(final_answer)
    if parsed:
        payload = parsed.get("structured_payload") if isinstance(parsed.get("structured_payload"), dict) else parsed
        return normalize_summary_structured_payload(payload), final_thought
    return normalize_summary_structured_payload({}), final_thought


def _resolve_summary_result(raw_answer: str, raw_thought: str) -> Tuple[str, str]:
    """归并摘要正文与思考内容，兼容正文落在 reasoning 字段的返回。"""
    answer_text = str(raw_answer or "").strip()
    thought_text = str(raw_thought or "").strip()
    if answer_text:
        thought, answer = _split_think_answer(answer_text)
        return answer or SUMMARY_FAILURE_TEXT, thought or thought_text
    if thought_text:
        logger.warning("summary final_answer empty, fallback to reasoning content")
        thought, answer = _split_think_answer(thought_text)
        return answer or thought_text or SUMMARY_FAILURE_TEXT, ""
    return SUMMARY_FAILURE_TEXT, ""


async def generate_summary_structured(
    text_content: str,
    max_retries: int = 3,
    retry_delay: float = 1.0,
    enable_thinking: bool = False,
) -> Tuple[str, str]:
    """Call the LLM to produce summary markdown (and optional thought) with retries."""
    if not text_content or not str(text_content).strip():
        return "", ""

    try:
        client = get_async_openai_client()
    except Exception as exc:
        logger.error("初始化摘要模型客户端失败: %s", exc, exc_info=True)
        return "", ""

    system_prompt = _load_sys_prompt()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": text_content},
    ]
    logger.info("summary input model=%s enable_thinking=%s user_content=%s", SUMMARY_LLM_MODEL, enable_thinking, _log_summary_preview(text_content))

    delay = retry_delay
    for attempt in range(max_retries):
        try:
            extra_body = build_thinking_extra_body(SUMMARY_LLM_MODEL, enable_thinking)
            metric_stage = "complaint_summary_generation"
            call_id = _record_summary_prompt(metric_stage, messages)
            metric_started_at = time.monotonic()
            stream = await client.chat.completions.create(
                model=SUMMARY_LLM_MODEL,
                messages=messages,
                temperature=0.2,
                extra_body=extra_body,
                stream=True,
            )
            thought_chunks: list[str] = []
            answer_chunks: list[str] = []
            first_token_ms = 0
            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk and chunk.choices else None
                if not delta:
                    continue
                content_delta, reasoning_delta, reasoning_field = extract_stream_deltas(delta)
                if isinstance(reasoning_delta, str) and reasoning_delta:
                    if not first_token_ms:
                        first_token_ms = int((time.monotonic() - metric_started_at) * 1000)
                    if _summary_stream_log_enabled():
                        logger.info("summary thinking field=%s", reasoning_field)
                    _log_model_output(reasoning_delta, stage="reasoning_delta")
                    thought_chunks.append(reasoning_delta)
                if isinstance(content_delta, str) and content_delta:
                    if not first_token_ms:
                        first_token_ms = int((time.monotonic() - metric_started_at) * 1000)
                    _log_model_output(content_delta, stage="content_delta")
                    answer_chunks.append(content_delta)
            raw_answer = "".join(answer_chunks)
            _log_model_output(raw_answer, stage="final_answer")
            raw_thought = "".join(thought_chunks)
            _record_summary_output(metric_stage, call_id, raw_answer or raw_thought, first_token_ms, metric_started_at)
            logger.info("summary raw_answer=%s", _log_summary_preview(raw_answer))
            logger.info("summary raw_thought=%s", _log_summary_preview(raw_thought))
            final_answer, final_thought = _resolve_summary_result(raw_answer, raw_thought)
            logger.info("summary resolved_answer=%s", _log_summary_preview(final_answer))
            logger.info("summary resolved_thought=%s", _log_summary_preview(final_thought))
            return final_answer, final_thought
        except Exception as exc:
            logger.error(
                "摘要模型调用失败 (尝试 %s/%s): %s",
                attempt + 1,
                max_retries,
                exc,
                exc_info=attempt >= max_retries - 1,
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(delay)
                delay *= 2

    return SUMMARY_FAILURE_TEXT, ""


async def generate_summary_bundle(text_content: str, max_retries: int = 3, retry_delay: float = 1.0, enable_thinking: bool = False) -> Tuple[str, Dict[str, Any], str]:
    """一次生成摘要 Markdown 与结构化字段。"""
    if not text_content or not str(text_content).strip():
        return "", normalize_summary_structured_payload({}), ""

    try:
        client = get_async_openai_client()
    except Exception as exc:
        logger.error("初始化摘要模型客户端失败: %s", exc, exc_info=True)
        return "", normalize_summary_structured_payload({}), ""

    messages = [
        {"role": "system", "content": _build_summary_bundle_prompt()},
        {"role": "user", "content": text_content},
    ]
    logger.info("summary_bundle input model=%s enable_thinking=%s user_content=%s", SUMMARY_LLM_MODEL, enable_thinking, _log_summary_preview(text_content))

    delay = retry_delay
    for attempt in range(max_retries):
        try:
            extra_body = build_thinking_extra_body(SUMMARY_LLM_MODEL, enable_thinking)
            metric_stage = "complaint_summary_bundle_generation"
            call_id = _record_summary_prompt(metric_stage, messages)
            metric_started_at = time.monotonic()
            stream = await client.chat.completions.create(
                model=SUMMARY_LLM_MODEL,
                messages=messages,
                temperature=0.2,
                extra_body=extra_body,
                stream=True,
            )
            thought_chunks: list[str] = []
            answer_chunks: list[str] = []
            first_token_ms = 0
            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk and chunk.choices else None
                if not delta:
                    continue
                content_delta, reasoning_delta, reasoning_field = extract_stream_deltas(delta)
                if isinstance(reasoning_delta, str) and reasoning_delta:
                    if not first_token_ms:
                        first_token_ms = int((time.monotonic() - metric_started_at) * 1000)
                    if _summary_stream_log_enabled():
                        logger.info("summary_bundle thinking field=%s", reasoning_field)
                    _log_model_output(reasoning_delta, stage="bundle_reasoning_delta")
                    thought_chunks.append(reasoning_delta)
                if isinstance(content_delta, str) and content_delta:
                    if not first_token_ms:
                        first_token_ms = int((time.monotonic() - metric_started_at) * 1000)
                    _log_model_output(content_delta, stage="bundle_content_delta")
                    answer_chunks.append(content_delta)
            raw_answer = "".join(answer_chunks)
            raw_thought = "".join(thought_chunks)
            _record_summary_output(metric_stage, call_id, raw_answer or raw_thought, first_token_ms, metric_started_at)
            logger.info("summary_bundle raw_answer=%s", _log_summary_preview(raw_answer))
            logger.info("summary_bundle raw_thought=%s", _log_summary_preview(raw_thought))
            summary_markdown, structured_payload, final_thought = _resolve_summary_bundle(raw_answer, raw_thought)
            logger.info("summary_bundle resolved_markdown=%s", _log_summary_preview(summary_markdown))
            logger.info("summary_bundle resolved_structured=%s", json.dumps(structured_payload, ensure_ascii=False))
            return summary_markdown, structured_payload, final_thought
        except Exception as exc:
            logger.error(
                "摘要双载荷模型调用失败 (尝试 %s/%s): %s",
                attempt + 1,
                max_retries,
                exc,
                exc_info=attempt >= max_retries - 1,
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(delay)
                delay *= 2

    return SUMMARY_FAILURE_TEXT, normalize_summary_structured_payload({}), ""


async def generate_summary_structured_payload(text_content: str, max_retries: int = 3, retry_delay: float = 1.0, enable_thinking: bool = False, user_id: str = "", base_id: str = "") -> Tuple[Dict[str, Any], str]:
    """仅生成结构化摘要字段。"""
    if not text_content or not str(text_content).strip():
        return normalize_summary_structured_payload({}), ""

    try:
        client = get_async_openai_client()
    except Exception as exc:
        logger.error("初始化摘要模型客户端失败: %s", exc, exc_info=True)
        return normalize_summary_structured_payload({}), ""

    messages = [
        {"role": "system", "content": _build_summary_structured_only_prompt()},
        {"role": "user", "content": text_content},
    ]
    trace_context = {"user_id": str(user_id or ""), "base_id": str(base_id or "")}
    logger.info("summary_structured_only input metrics user_id=%s base_id=%s model=%s enable_thinking=%s input_chars=%s", user_id or "", base_id or "", SUMMARY_LLM_MODEL, enable_thinking, sum(len(str(message.get("content") or "")) for message in messages))

    delay = retry_delay
    for attempt in range(max_retries):
        try:
            extra_body = build_thinking_extra_body(SUMMARY_LLM_MODEL, enable_thinking)
            metric_stage = "complaint_summary_structured_generation"
            call_id = _record_summary_prompt(metric_stage, messages, trace_context)
            metric_started_at = time.monotonic()
            metric_started_wall = time.time()
            stream = await client.chat.completions.create(
                model=SUMMARY_LLM_MODEL,
                messages=messages,
                temperature=0.2,
                extra_body=extra_body,
                stream=True,
            )
            thought_chunks: list[str] = []
            answer_chunks: list[str] = []
            first_token_ms = 0
            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk and chunk.choices else None
                if not delta:
                    continue
                content_delta, reasoning_delta, reasoning_field = extract_stream_deltas(delta)
                if isinstance(reasoning_delta, str) and reasoning_delta:
                    if not first_token_ms:
                        first_token_ms = int((time.monotonic() - metric_started_at) * 1000)
                    if _summary_stream_log_enabled():
                        logger.info("summary_structured_only thinking user_id=%s base_id=%s field=%s", user_id or "", base_id or "", reasoning_field)
                    _log_model_output(reasoning_delta, stage="structured_only_reasoning_delta")
                    thought_chunks.append(reasoning_delta)
                if isinstance(content_delta, str) and content_delta:
                    if not first_token_ms:
                        first_token_ms = int((time.monotonic() - metric_started_at) * 1000)
                    _log_model_output(content_delta, stage="structured_only_content_delta")
                    answer_chunks.append(content_delta)
            raw_answer = "".join(answer_chunks)
            raw_thought = "".join(thought_chunks)
            _record_summary_output(metric_stage, call_id, raw_answer or raw_thought, first_token_ms, metric_started_at, trace_context)
            logger.info("summary_structured_only metrics user_id=%s base_id=%s input_chars=%s output_chars=%s thought_chars=%s start_time=%s end_time=%s duration_ms=%s first_token_ms=%s", user_id or "", base_id or "", sum(len(str(message.get("content") or "")) for message in messages), len(raw_answer), len(raw_thought), time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(metric_started_wall)), time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()), int((time.monotonic() - metric_started_at) * 1000), first_token_ms)
            logger.info("summary_structured_only raw_thought user_id=%s base_id=%s content=%s", user_id or "", base_id or "", _log_summary_preview(raw_thought))
            structured_payload, final_thought = _resolve_summary_structured_only(raw_answer, raw_thought)
            logger.info("summary_structured_only resolved_structured user_id=%s base_id=%s payload=%s", user_id or "", base_id or "", json.dumps(structured_payload, ensure_ascii=False))
            return structured_payload, final_thought
        except Exception as exc:
            logger.error(
                "结构化摘要模型调用失败 user_id=%s base_id=%s (尝试 %s/%s): %s",
                user_id or "",
                base_id or "",
                attempt + 1,
                max_retries,
                exc,
                exc_info=attempt >= max_retries - 1,
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(delay)
                delay *= 2

    return normalize_summary_structured_payload({}), ""


async def stream_summary_markdown(
    text_content: str,
    *,
    enable_thinking: bool = False,
    max_retries: int = 3,
    retry_delay: float = 1.0,
) -> AsyncGenerator[dict[str, str], None]:
    if not text_content or not str(text_content).strip():
        return

    try:
        client = get_async_openai_client()
    except Exception as exc:
        logger.error("初始化摘要模型客户端失败: %s", exc, exc_info=True)
        return

    system_prompt = _load_sys_prompt()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": text_content},
    ]

    delay = retry_delay
    for attempt in range(max_retries):
        try:
            think_parse_mode = "answer"
            think_buffer = ""

            def _split_think_stream(chunk: str) -> Tuple[str, str]:
                nonlocal think_parse_mode, think_buffer
                if not chunk:
                    return "", ""
                buffer = think_buffer + chunk
                thought = ""
                answer = ""
                i = 0
                while i < len(buffer):
                    if buffer.startswith("<think>", i):
                        think_parse_mode = "thought"
                        i += len("<think>")
                        continue
                    if buffer.startswith("</think>", i):
                        think_parse_mode = "answer"
                        i += len("</think>")
                        continue
                    if buffer[i] == "<" and (
                        buffer.startswith("<think", i) or buffer.startswith("</think", i)
                    ) and len(buffer) - i < len("</think>"):
                        break
                    if think_parse_mode == "thought":
                        thought += buffer[i]
                    else:
                        answer += buffer[i]
                    i += 1
                think_buffer = buffer[i:]
                return thought, answer

            extra_body = build_thinking_extra_body(SUMMARY_LLM_MODEL, enable_thinking)
            metric_stage = "complaint_summary_stream_generation"
            call_id = _record_summary_prompt(metric_stage, messages)
            metric_started_at = time.monotonic()
            stream = await client.chat.completions.create(
                model=SUMMARY_LLM_MODEL,
                messages=messages,
                temperature=0.2,
                extra_body=extra_body,
                stream=True,
            )
            answer_chunks: list[str] = []
            thought_chunks: list[str] = []
            first_token_ms = 0
            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk and chunk.choices else None
                if not delta:
                    continue
                content_delta, reasoning_delta, reasoning_field = extract_stream_deltas(delta)
                payload: dict[str, str] = {}
                if isinstance(reasoning_delta, str) and reasoning_delta:
                    if not first_token_ms:
                        first_token_ms = int((time.monotonic() - metric_started_at) * 1000)
                    # logger.info("summary thinking field=%s", reasoning_field)
                    payload["reasoning_delta"] = reasoning_delta
                    thought_chunks.append(reasoning_delta)
                if isinstance(content_delta, str) and content_delta:
                    if not first_token_ms:
                        first_token_ms = int((time.monotonic() - metric_started_at) * 1000)
                    _log_model_output(content_delta, stage="content_delta")
                    thought_delta, answer_delta = _split_think_stream(content_delta)
                    if thought_delta:
                        payload["reasoning_delta"] = f"{payload.get('reasoning_delta', '')}{thought_delta}"
                    if answer_delta:
                        payload["content_delta"] = answer_delta
                        answer_chunks.append(answer_delta)
                if payload:
                    yield payload
            _record_summary_output(metric_stage, call_id, "".join(answer_chunks) or "".join(thought_chunks), first_token_ms, metric_started_at)
            if think_buffer:
                if think_parse_mode == "thought":
                    yield {"reasoning_delta": think_buffer}
                else:
                    yield {"content_delta": think_buffer}
            return
        except Exception as exc:
            logger.error(
                "摘要模型调用失败 (尝试 %s/%s): %s",
                attempt + 1,
                max_retries,
                exc,
                exc_info=attempt >= max_retries - 1,
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(delay)
                delay *= 2


def json_to_markdown(summary_json: dict) -> str:
    """Render structured summary JSON to Markdown with safe fallbacks."""
    if not isinstance(summary_json, dict):
        return SUMMARY_FAILURE_TEXT

    def _escape(s: str) -> str:
        try:
            return (s or "").replace("\r\n", "\n").replace("\u00A0", " ")
        except Exception:
            return str(s)

    lines = []
    sections = summary_json.get("sections") if isinstance(summary_json.get("sections"), dict) else None
    data_items = sections.items() if sections else summary_json.items()

    for key, value in data_items:
        if key in ("_schema", "_raw"):
            continue
        lines.append(f"## {key}")
        if isinstance(value, list):
            if not value:
                lines.append("- 未提供")
            else:
                for it in value:
                    if isinstance(it, dict):
                        for k, v in it.items():
                            lines.append(f"- **{k}**: { _escape(str(v)) }")
                    else:
                        lines.append(f"- { _escape(str(it)) }")
        elif isinstance(value, dict):
            if not value:
                lines.append("未提供")
            else:
                for k, v in value.items():
                    if isinstance(v, list):
                        if not v:
                            lines.append(f"- **{k}**: 未提供")
                        else:
                            lines.append("")
                            lines.append(f"**{k}**:")
                            for it in v:
                                if isinstance(it, dict):
                                    for kk, vv in it.items():
                                        lines.append(f"- **{kk}**: { _escape(str(vv)) }")
                                else:
                                    lines.append(f"- { _escape(str(it)) }")
                    elif isinstance(v, dict):
                        if not v:
                            lines.append(f"- **{k}**: 未提供")
                        else:
                            lines.append(f"- {k}:")
                            for kk, vv in v.items():
                                lines.append(f"  - **{kk}**: { _escape(str(vv)) }")
                    else:
                        text = _escape(str(v))
                        lines.append(f"- **{k}**: { text.strip() if text.strip() else '未提供' }")
        else:
            text = _escape(str(value))
            lines.append(text if text.strip() else "未提供")
        lines.append("")

    if not lines:
        raw = summary_json.get("_raw")
        return str(raw or SUMMARY_FAILURE_TEXT)

    return "\n".join(lines).strip()


def ensure_summary_history_section(
    summary_json: Any,
    summary_markdown: Optional[str] = None,
) -> Dict[str, Any]:
    """Ensure required history sections exist in summary JSON."""
    if not isinstance(summary_json, dict):
        summary_json = {"_schema": "v1", "sections": {}, "_raw": summary_markdown}

    if "_schema" not in summary_json:
        summary_json["_schema"] = "v1"

    if summary_markdown is not None and summary_json.get("_raw") is None:
        summary_json["_raw"] = summary_markdown

    sections = summary_json.get("sections")
    if not isinstance(sections, dict):
        sections = {}
        summary_json["sections"] = sections

    for title, placeholder in REQUIRED_SUMMARY_SECTIONS:
        section_value = sections.get(title)
        if isinstance(section_value, str):
            if section_value.strip():
                continue
            sections[title] = placeholder
            continue
        if not section_value:
            sections[title] = placeholder

    return summary_json


def ensure_summary_history_markdown(summary_markdown: Any) -> str:
    """Ensure required history sections exist in summary Markdown."""
    if not isinstance(summary_markdown, str):
        summary_markdown = ""

    result = summary_markdown.rstrip()
    normalized = result.replace("\r\n", "\n")

    for title, placeholder in REQUIRED_SUMMARY_SECTIONS:
        header_line = f"## {title}"
        if header_line in normalized:
            continue
        if result:
            result = f"{result}\n\n{header_line}\n{placeholder}\n"
        else:
            result = f"{header_line}\n{placeholder}\n"
        normalized = result.replace("\r\n", "\n")

    return result if result else summary_markdown
