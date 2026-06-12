from __future__ import annotations

import asyncio
import json
import re
import time
from functools import partial
from pathlib import Path
from typing import Any, AsyncGenerator

from apps.complaint_app.core.async_openai_client import get_async_openai_client
from apps.complaint_app.core.llm.llm_client import extract_stream_deltas
from apps.complaint_app.core.model_config import get_complaint_match_experience_model, get_complaint_match_experience_thinking
from apps.complaint_app.experience_loader import find_expert_experience_for_second_category, get_expert_experience_cached
from apps.complaint_app.services.analysis_agent.log_utils import build_trace_tag, log_preview_json, log_preview_text
from apps.complaint_app.services.analysis_agent.stream_guard import get_stream_heartbeat_seconds, get_stream_timeout_seconds, read_next_stream_chunk
from utils.llm_thinking import build_thinking_extra_body
from utils.logger import get_logger
from utils.llm_prompt_metrics import record_llm_output_metrics, record_llm_prompt_metrics
from utils.timing import log_phase_timing, timing_elapsed_ms, timing_now

logger = get_logger(__name__)
COMPLAINT_ANALYSIS_ROUTE = "POST /api/complaint/complaint-analysis"
PROMPT_BASE_DIR = Path(__file__).resolve().parents[2] / "tools" / "prompts"
FIRST_SYSTEM_PROMPT_PATH = PROMPT_BASE_DIR / "complaint_expert_classification_first_system.txt"
SECOND_SYSTEM_PROMPT_PATH = PROMPT_BASE_DIR / "complaint_expert_classification_second_system.txt"
DEFAULT_FIRST_SYSTEM_PROMPT = "你是举报事项分类助手。请根据输入内容完成一级分类。只返回JSON数组，字段：分类、匹配度、分类理由。"
DEFAULT_SECOND_SYSTEM_PROMPT = "你是举报事项分类助手。请基于给定候选完成二级分类。只返回JSON数组，字段：一级分类、二级分类、匹配度、分类匹配逻辑说明。"
DEFAULT_FIRST_USER_PROMPT = "举报信背景：{{#sys.story#}}\n举报事项：{{#sys.jb_item#}}\n候选一级分类：{{#sys.first_level_categories#}}"
DEFAULT_SECOND_USER_PROMPT = "举报信背景：{{#sys.story#}}\n举报事项：{{#sys.jb_item#}}\n候选二级分类：{{#sys.second_level_categories#}}"
TWO_STAGE_STAGE_FIRST = "first"
TWO_STAGE_STAGE_SECOND = "second"


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _load_prompt_text(path: Path, fallback: str) -> str:
    try:
        if path.is_file():
            content = path.read_text(encoding="utf-8").strip()
            if content:
                return content
    except Exception as exc:
        logger.warning("[two_stage_match] load_prompt_failed path=%s err=%s", path, exc)
    return fallback


def _render_template(text: str, variables: dict[str, str]) -> str:
    rendered = re.sub(r"\{\{#([^#]+)#\}\}", partial(_render_template_match, variables=variables), text or "")
    return re.sub(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}", partial(_render_template_match, variables=variables), rendered)


def _render_template_match(match: re.Match, variables: dict[str, str]) -> str:
    expr = _to_text(match.group(1))
    key = expr.split(".")[-1] if expr else ""
    return variables.get(key, "")


def _strip_think_content(raw: str) -> str:
    text = str(raw or "")
    while True:
        start_idx = text.find("<think>")
        if start_idx == -1:
            break
        end_idx = text.find("</think>", start_idx + len("<think>"))
        if end_idx == -1:
            text = text.replace("<think>", "")
            break
        text = text[:start_idx] + text[end_idx + len("</think>"):]
    if "</think>" in text:
        text = text.split("</think>")[-1]
    return text.replace("<think>", "").replace("</think>", "").strip()


def _strip_code_fence(raw: str) -> str:
    text = str(raw or "").strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    while lines and lines[-1].strip() == "```":
        lines.pop()
    return "\n".join(lines).strip()


def _extract_json_fragment_at(text: str, start_idx: int) -> str:
    opener = text[start_idx]
    if opener not in "{[":
        return ""
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_string = False
    escaped = False
    for idx in range(start_idx, len(text)):
        ch = text[idx]
        if in_string:
            if escaped:
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == opener:
            depth += 1
            continue
        if ch == closer:
            depth -= 1
            if depth == 0:
                return text[start_idx:idx + 1]
    return text[start_idx:].strip()


def _extract_first_json_fragment(raw: str) -> str:
    text = str(raw or "")
    fallback = ""
    for idx, ch in enumerate(text):
        if ch not in "{[":
            continue
        candidate = _extract_json_fragment_at(text, idx)
        if not candidate:
            continue
        if not fallback:
            fallback = candidate
        try:
            json.loads(candidate)
            return candidate
        except Exception:
            continue
    return fallback


def _prepare_json_text(raw: str) -> str:
    stripped = _strip_code_fence(_strip_think_content(raw))
    fragment = _extract_first_json_fragment(stripped)
    return fragment or stripped


def _parse_json_any(raw: str) -> Any:
    text = _prepare_json_text(raw)
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _parse_json_list_response(raw: str) -> list[dict[str, Any]]:
    parsed = _parse_json_any(raw)
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    if isinstance(parsed, dict):
        for key in ("obj", "items", "data", "results"):
            value = parsed.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _extract_first_candidates(first_stage_list: list[dict[str, Any]]) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for item in first_stage_list:
        label = _to_text(item.get("分类") or item.get("一级分类") or item.get("first_category"))
        if not label or label in seen:
            continue
        seen.add(label)
        candidates.append(label)
    return candidates


def _extract_second_level_items(value: Any) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    items = value if isinstance(value, list) else ([value] if isinstance(value, dict) else [])
    for item in items:
        if not isinstance(item, dict):
            continue
        second_label = _to_text(item.get("overview") or item.get("二级分类") or item.get("second_category"))
        second_desc = _to_text(item.get("second_category_description") or item.get("二级分类说明") or item.get("二级分类的描述") or item.get("description"))
        if not second_label:
            continue
        dedup_key = (second_label, second_desc)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        result.append({"二级分类": second_label, "二级分类的描述": second_desc})
    return result


def _extract_first_level_meta(value: Any) -> tuple[str, str]:
    items = value if isinstance(value, list) else ([value] if isinstance(value, dict) else [])
    first_desc = ""
    glossary = ""
    for item in items:
        if not isinstance(item, dict):
            continue
        if not first_desc:
            first_desc = _to_text(item.get("first_category_description") or item.get("一级分类说明"))
        if not glossary:
            glossary = _to_text(item.get("glossary") or item.get("名词辨析"))
        if first_desc and glossary:
            break
    return first_desc, glossary


def _build_cate_know_grouped(first_keys: list[str], expert_experience_json: dict[str, Any], trace_tag: str, complaint: str) -> tuple[list[dict[str, Any]], list[str]]:
    grouped: list[dict[str, Any]] = []
    second_labels: list[str] = []
    seen_second: set[str] = set()
    total_n = len(first_keys)
    for idx, first_key in enumerate(first_keys, start=1):
        value = expert_experience_json.get(first_key)
        if value is None:
            logger.warning("[two_stage_match] %s complaint=%s second_stage_prepare_missing_experience seq=%s/%s first=%s", trace_tag, complaint, idx, total_n, first_key)
        second_list = _extract_second_level_items(value)
        first_desc, glossary = _extract_first_level_meta(value)
        grouped.append({"一级分类": first_key, "一级分类描述": first_desc, "相关名词解释": glossary, "二级分类列表": second_list})
        for item in second_list:
            second_label = _to_text(item.get("二级分类"))
            if not second_label or second_label in seen_second:
                continue
            seen_second.add(second_label)
            second_labels.append(second_label)
    return grouped, second_labels


def _format_cate_know_text(grouped: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in grouped:
        first_label = _to_text(item.get("一级分类"))
        first_desc = _to_text(item.get("一级分类描述"))
        glossary = _to_text(item.get("相关名词解释"))
        second_list = item.get("二级分类列表") if isinstance(item.get("二级分类列表"), list) else []
        lines.append(f"一级分类是{first_label}，它的描述是：{first_desc}，相关名词解释：{glossary}，它的二级分类及描述列表如下：{json.dumps(second_list, ensure_ascii=False)}")
    return "\n".join(lines)


def _merge_candidate_with_entry(entry: dict[str, Any], candidate: dict[str, Any], first_label: str, second_label: str) -> dict[str, Any]:
    merged = dict(entry)
    if first_label:
        merged.setdefault("first_category", first_label)
        merged.setdefault("一级分类", first_label)
    if second_label:
        merged.setdefault("overview", second_label)
        merged.setdefault("二级分类", second_label)
    score = _to_text(candidate.get("匹配度") or candidate.get("score"))
    reason = _to_text(candidate.get("分类匹配逻辑说明") or candidate.get("分类理由") or candidate.get("classification_criteria"))
    if score:
        merged["匹配度"] = score
    if reason:
        merged["分类理由"] = reason
    return merged


def _match_entries_from_candidates(candidates: list[dict[str, Any]], experience_data: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for item in candidates:
        first_label = _to_text(item.get("一级分类") or item.get("first_category") or item.get("分类"))
        second_label = _to_text(item.get("二级分类") or item.get("second_category") or item.get("overview"))
        if not second_label:
            continue
        matched = False
        if first_label:
            first_node = experience_data.get(first_label)
            first_items = first_node if isinstance(first_node, list) else ([first_node] if isinstance(first_node, dict) else [])
            for entry in first_items:
                if not isinstance(entry, dict):
                    continue
                overview = _to_text(entry.get("overview") or entry.get("second_category") or entry.get("二级分类"))
                if overview and overview == second_label:
                    entries.append(_merge_candidate_with_entry(entry, item, first_label, second_label))
                    matched = True
        if not matched:
            fallback = find_expert_experience_for_second_category(second_label)
            if isinstance(fallback, dict):
                fallback_first = _to_text(fallback.get("first_category") or first_label)
                entries.append(_merge_candidate_with_entry(fallback, item, fallback_first, second_label))
                matched = True
        if not matched:
            minimal = {
                "first_category": first_label,
                "一级分类": first_label,
                "overview": second_label,
                "二级分类": second_label,
                "匹配度": _to_text(item.get("匹配度") or item.get("score")),
                "分类理由": _to_text(item.get("分类匹配逻辑说明") or item.get("分类理由") or item.get("classification_criteria")),
            }
            entries.append(minimal)
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in entries:
        key = f"{_to_text(entry.get('first_category') or entry.get('一级分类'))}-{_to_text(entry.get('overview') or entry.get('二级分类'))}-{_to_text(entry.get('row_no'))}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped


def _load_experience_data() -> dict[str, Any]:
    raw = get_expert_experience_cached()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def _collect_stream_text(stream: Any, trace_tag: str, complaint: str, stage_label: str) -> tuple[str, str, int, int]:
    started_at = time.time()
    timeout_seconds = get_stream_timeout_seconds()
    heartbeat_seconds = get_stream_heartbeat_seconds()
    stream_iter = stream.__aiter__()
    first_chunk_ms = -1
    chunk_count = 0
    last_chunk_at = started_at
    next_heartbeat_at = started_at + heartbeat_seconds if heartbeat_seconds > 0 else 0.0
    answer_chunks: list[str] = []
    reasoning_chunks: list[str] = []
    while True:
        try:
            chunk, stream_end = await read_next_stream_chunk(stream_iter, timeout_seconds)
        except asyncio.TimeoutError as exc:
            idle_ms = int((time.time() - last_chunk_at) * 1000)
            logger.error("[two_stage_match] %s complaint=%s stage=%s stream_timeout timeout_seconds=%s idle_ms=%s chunk_count=%s", trace_tag, complaint, stage_label, timeout_seconds, idle_ms, chunk_count)
            raise TimeoutError(f"two_stage stream timeout stage={stage_label}") from exc
        if stream_end:
            break
        chunk_count += 1
        now = time.time()
        last_chunk_at = now
        if heartbeat_seconds > 0 and now >= next_heartbeat_at:
            # logger.info("[two_stage_match] %s complaint=%s stage=%s stream_heartbeat elapsed_ms=%s chunk_count=%s", trace_tag, complaint, stage_label, int((now - started_at) * 1000), chunk_count)
            next_heartbeat_at = now + heartbeat_seconds
        delta = chunk.choices[0].delta if chunk and chunk.choices else None
        if not delta:
            continue
        content_delta, reasoning_delta, reasoning_field = extract_stream_deltas(delta)
        if first_chunk_ms < 0 and ((isinstance(content_delta, str) and content_delta) or (isinstance(reasoning_delta, str) and reasoning_delta)):
            first_chunk_ms = int((time.time() - started_at) * 1000)
            # logger.info("[two_stage_match] %s complaint=%s stage=%s first_chunk_ms=%s reasoning_field=%s", trace_tag, complaint, stage_label, first_chunk_ms, reasoning_field)
        if isinstance(content_delta, str) and content_delta:
            answer_chunks.append(content_delta)
        if isinstance(reasoning_delta, str) and reasoning_delta:
            reasoning_chunks.append(reasoning_delta)
    elapsed_ms = int((time.time() - started_at) * 1000)
    answer_text = "".join(answer_chunks).strip()
    reasoning_text = "".join(reasoning_chunks).strip()
    logger.info("[two_stage_match] %s complaint=%s stage=%s stream_stats answer_len=%s reasoning_len=%s elapsed_ms=%s chunk_count=%s", trace_tag, complaint, stage_label, len(answer_text), len(reasoning_text), elapsed_ms, chunk_count)
    return answer_text, reasoning_text, first_chunk_ms, elapsed_ms


async def _run_first_stage_single(complaint: str, incident: str, model: str, first_prompt: str, first_labels: list[str], sem: asyncio.Semaphore, enable_thinking: bool, trace_context: dict[str, Any] | None = None) -> list[str]:
    async with sem:
        trace_tag = build_trace_tag(trace_context, "two_stage")
        incident_text = _to_text(incident)
        complaint_text = _to_text(complaint)
        if not complaint_text:
            logger.warning("[two_stage_match] %s empty complaint", trace_tag)
            return []

        client = get_async_openai_client()
        extra_body = build_thinking_extra_body(model, enable_thinking)
        first_categories_text = json.dumps(first_labels, ensure_ascii=False)
        variables_first = {"jb_item": complaint_text, "story": incident_text, "first_level_categories": first_categories_text}
        first_messages = [{"role": "system", "content": _render_template(first_prompt, variables_first)}, {"role": "user", "content": _render_template(DEFAULT_FIRST_USER_PROMPT, variables_first)}]
        logger.info("[two_stage_match] %s complaint=%s first_stage_request model=%s thinking=%s extra_body=%s messages=%s", trace_tag, complaint_text, model, enable_thinking, log_preview_json(extra_body), log_preview_json(first_messages))
        metric_stage = "complaint_issue_two_stage_first"
        call_id = record_llm_prompt_metrics(metric_stage, model=model, messages=first_messages, metadata={"business_stage": "complaint_issue_analysis", "match_strategy": "two_stage", **(trace_context or {})})
        first_stream = await client.chat.completions.create(model=model, messages=first_messages, temperature=0.0, extra_body=extra_body, stream=True)
        first_answer, first_reasoning, first_chunk_ms, first_elapsed_ms = await _collect_stream_text(first_stream, trace_tag, complaint_text, "first")
        record_llm_output_metrics(metric_stage, model=model, output_text=first_answer or first_reasoning, first_token_ms=max(0, first_chunk_ms), llm_duration_ms=first_elapsed_ms, metadata={"business_stage": "complaint_issue_analysis", "match_strategy": "two_stage", **(trace_context or {})}, call_id=call_id)
        first_raw = first_answer if first_answer else first_reasoning
        logger.info("[two_stage_match] %s complaint=%s first_stage_output answer=%s reasoning=%s", trace_tag, complaint_text, log_preview_text(first_answer), log_preview_text(first_reasoning))
        first_list = _parse_json_list_response(first_raw)
        first_candidates = _extract_first_candidates(first_list)
        logger.info("[two_stage_match] %s complaint=%s first_stage_parsed candidates=%s parsed=%s", trace_tag, complaint_text, log_preview_json(first_candidates), log_preview_json(first_list))
        if not first_candidates:
            logger.warning("[two_stage_match] %s complaint=%s no first candidates", trace_tag, complaint_text)
        return first_candidates


def _log_two_stage_item_timing(trace_context: dict[str, Any] | None, complaint_index: int, complaint: str, stage1_duration_ms: int, stage2_duration_ms: int, matched_count: int, success: bool) -> None:
    """记录单个举报事项二阶段匹配耗时。"""
    extra = dict(trace_context or {})
    extra["match_strategy"] = "two_stage"
    extra["complaint_index"] = int(complaint_index)
    extra["complaint"] = complaint
    extra["stage1_duration_ms"] = int(stage1_duration_ms)
    extra["stage2_duration_ms"] = int(stage2_duration_ms)
    extra["matched_count"] = int(matched_count)
    log_phase_timing(logger, "complaint_app", COMPLAINT_ANALYSIS_ROUTE, "issue_analysis_match_item", int(stage1_duration_ms) + int(stage2_duration_ms), success, extra)


async def _run_second_stage_single(complaint: str, incident: str, model: str, second_prompt: str, first_candidates: list[str], experience_data: dict[str, Any], sem: asyncio.Semaphore, enable_thinking: bool, trace_context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    async with sem:
        trace_tag = build_trace_tag(trace_context, "two_stage")
        incident_text = _to_text(incident)
        complaint_text = _to_text(complaint)
        if not complaint_text:
            logger.warning("[two_stage_match] %s empty complaint", trace_tag)
            return []
        if not first_candidates:
            logger.warning("[two_stage_match] %s complaint=%s skip second stage due to empty first candidates", trace_tag, complaint_text)
            return []

        client = get_async_openai_client()
        extra_body = build_thinking_extra_body(model, enable_thinking)
        grouped, second_labels = _build_cate_know_grouped(first_candidates, experience_data, trace_tag, complaint_text)
        second_categories_text = json.dumps(second_labels, ensure_ascii=False)
        variables_second = {"jb_item": complaint_text, "story": incident_text, "cate_know": _format_cate_know_text(grouped), "second_level_categories": second_categories_text}
        second_messages = [{"role": "system", "content": _render_template(second_prompt, variables_second)}, {"role": "user", "content": _render_template(DEFAULT_SECOND_USER_PROMPT, variables_second)}]
        logger.info("[two_stage_match] %s complaint=%s second_stage_request model=%s groups=%s second_candidates=%s messages=%s", trace_tag, complaint_text, model, len(grouped), len(second_labels), log_preview_json(second_messages))
        metric_stage = "complaint_issue_two_stage_second"
        call_id = record_llm_prompt_metrics(metric_stage, model=model, messages=second_messages, metadata={"business_stage": "complaint_issue_analysis", "match_strategy": "two_stage", **(trace_context or {})})
        second_stream = await client.chat.completions.create(model=model, messages=second_messages, temperature=0.0, extra_body=extra_body, stream=True)
        second_answer, second_reasoning, second_chunk_ms, second_elapsed_ms = await _collect_stream_text(second_stream, trace_tag, complaint_text, "second")
        record_llm_output_metrics(metric_stage, model=model, output_text=second_answer or second_reasoning, first_token_ms=max(0, second_chunk_ms), llm_duration_ms=second_elapsed_ms, metadata={"business_stage": "complaint_issue_analysis", "match_strategy": "two_stage", **(trace_context or {})}, call_id=call_id)
        second_raw = second_answer if second_answer else second_reasoning
        second_list = _parse_json_list_response(second_raw)
        matched = _match_entries_from_candidates(second_list, experience_data)
        logger.info("[two_stage_match] %s complaint=%s matched_count=%s matched=%s", trace_tag, complaint_text, len(matched), log_preview_json(matched))
        return matched


async def _run_first_stage_with_index(index: int, complaint: str, incident: str, model: str, first_prompt: str, first_labels: list[str], sem: asyncio.Semaphore, enable_thinking: bool, trace_context: dict[str, Any] | None = None) -> tuple[int, str, list[str], int, str]:
    """执行单个举报事项的一阶段匹配并返回耗时。"""
    started_at = timing_now()
    try:
        first_candidates = await _run_first_stage_single(complaint, incident, model, first_prompt, first_labels, sem, enable_thinking, trace_context)
        return index, complaint, first_candidates, timing_elapsed_ms(started_at), ""
    except Exception as exc:
        return index, complaint, [], timing_elapsed_ms(started_at), str(exc)


async def _run_second_stage_with_index(index: int, complaint: str, incident: str, model: str, second_prompt: str, first_candidates: list[str], experience_data: dict[str, Any], sem: asyncio.Semaphore, enable_thinking: bool, trace_context: dict[str, Any] | None = None) -> tuple[int, str, list[dict[str, Any]], int, str]:
    """执行单个举报事项的二阶段匹配并返回耗时。"""
    started_at = timing_now()
    try:
        matched = await _run_second_stage_single(complaint, incident, model, second_prompt, first_candidates, experience_data, sem, enable_thinking, trace_context)
        return index, complaint, matched, timing_elapsed_ms(started_at), ""
    except Exception as exc:
        return index, complaint, [], timing_elapsed_ms(started_at), str(exc)


async def complaint_match_experience_two_stage_stream(complaints: list[str], incident: str, trace_context: dict[str, Any] | None = None) -> AsyncGenerator[dict[str, Any], None]:
    trace_tag = build_trace_tag(trace_context, "two_stage")
    model = get_complaint_match_experience_model()
    enable_thinking = bool(get_complaint_match_experience_thinking())
    first_prompt = _load_prompt_text(FIRST_SYSTEM_PROMPT_PATH, DEFAULT_FIRST_SYSTEM_PROMPT)
    second_prompt = _load_prompt_text(SECOND_SYSTEM_PROMPT_PATH, DEFAULT_SECOND_SYSTEM_PROMPT)
    experience_data = _load_experience_data()
    first_labels = [str(key).strip() for key in experience_data.keys() if str(key).strip()]
    sem = asyncio.Semaphore(min(4, max(1, len(complaints or []))))
    logger.info("[two_stage_match] %s stream_start complaints=%s model=%s thinking=%s first_labels=%s", trace_tag, len(complaints or []), model, enable_thinking, len(first_labels))

    valid_complaints: list[tuple[int, str]] = []
    for idx, item in enumerate(complaints or []):
        complaint_text = _to_text(item)
        if not complaint_text:
            continue
        valid_complaints.append((idx, complaint_text))

    if not valid_complaints:
        logger.info("[two_stage_match] %s stream_end empty_complaints", trace_tag)
        return

    yield {"kind": "stage", "stage": TWO_STAGE_STAGE_FIRST}
    first_tasks: list[asyncio.Task] = []
    first_stage_outputs: dict[int, tuple[str, list[str], int, str]] = {}
    for idx, complaint_text in valid_complaints:
        task = asyncio.create_task(_run_first_stage_with_index(idx, complaint_text, incident, model, first_prompt, first_labels, sem, enable_thinking, trace_context))
        first_tasks.append(task)

    for task in asyncio.as_completed(first_tasks):
        idx, complaint_text, first_candidates, first_duration_ms, task_error = await task
        first_stage_outputs[idx] = (complaint_text, first_candidates, first_duration_ms, task_error)
        if task_error:
            logger.error("[two_stage_match] %s task_error complaint=%s err=%s", trace_tag, complaint_text, task_error)

    second_tasks: list[asyncio.Task] = []
    for idx, complaint_text in valid_complaints:
        first_complaint, first_candidates, first_duration_ms, first_task_error = first_stage_outputs.get(idx, (complaint_text, [], 0, ""))
        if first_task_error:
            _log_two_stage_item_timing(trace_context, idx, first_complaint or complaint_text, first_duration_ms, 0, 0, False)
            yield {"index": idx, "complaint": complaint_text, "matched": []}
            continue
        if not first_candidates:
            _log_two_stage_item_timing(trace_context, idx, first_complaint or complaint_text, first_duration_ms, 0, 0, True)
            yield {"index": idx, "complaint": complaint_text, "matched": []}
            continue
        second_tasks.append(asyncio.create_task(_run_second_stage_with_index(idx, complaint_text, incident, model, second_prompt, first_candidates, experience_data, sem, enable_thinking, trace_context)))

    if second_tasks:
        yield {"kind": "stage", "stage": TWO_STAGE_STAGE_SECOND}

    for task in asyncio.as_completed(second_tasks):
        idx, complaint_text, matched, second_duration_ms, task_error = await task
        first_duration_ms = first_stage_outputs.get(idx, (complaint_text, [], 0, ""))[2]
        if task_error:
            logger.error("[two_stage_match] %s task_error complaint=%s err=%s", trace_tag, complaint_text, task_error)
            _log_two_stage_item_timing(trace_context, idx, complaint_text, first_duration_ms, second_duration_ms, 0, False)
            yield {"index": idx, "complaint": complaint_text, "matched": []}
            continue
        _log_two_stage_item_timing(trace_context, idx, complaint_text, first_duration_ms, second_duration_ms, len(matched) if isinstance(matched, list) else 0, True)
        yield {"index": idx, "complaint": complaint_text, "matched": matched}

    logger.info("[two_stage_match] %s stream_end", trace_tag)
