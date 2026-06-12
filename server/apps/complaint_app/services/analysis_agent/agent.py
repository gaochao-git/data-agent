from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List
import asyncio
import hashlib
import json
import os
import re
import time
import uuid

from apps.complaint_app.core.async_openai_client import get_async_openai_client
from apps.complaint_app.core.llm.llm_client import extract_stream_deltas
from apps.complaint_app.core.model_config import get_complaint_extract_items_model, get_complaint_match_experience_model, get_complaint_match_experience_thinking
from apps.complaint_app.services.summary.llm_summary import _load_sys_prompt
from utils.llm_thinking import build_thinking_extra_body
from apps.complaint_app.services.analysis_agent.log_utils import build_trace_tag, log_preview_json, log_preview_text
from apps.complaint_app.services.analysis_agent.law_skill_runner import analyze_complaint_items_with_law_skill
from apps.complaint_app.services.analysis_agent.law_rationality_runner import analyze_complaint_item_with_law_rationality, build_law_rationality_fallback
from apps.complaint_app.services.analysis_agent.match_strategy import MATCH_STRATEGY_LAW_SKILL, MATCH_STRATEGY_LEGACY, MATCH_STRATEGY_TWO_STAGE, normalize_match_strategy
from apps.complaint_app.services.analysis_agent.stream_guard import get_stream_heartbeat_seconds, get_stream_timeout_seconds, read_next_stream_chunk
from apps.complaint_app.services.analysis_agent.two_stage_matcher import complaint_match_experience_two_stage_stream
from apps.complaint_app.experience_loader import (
    get_expert_experience_cached,
    find_expert_experience_for_second_category,
)
from utils.logger import get_logger
from utils.llm_prompt_metrics import record_llm_output_metrics, record_llm_prompt_metrics
from utils.timing import log_phase_timing, timing_elapsed_ms, timing_now


class ComplaintStep(str, Enum):
    EXTRACT_ITEMS = "complaint_extract_items"
    LAW_RATIONALITY = "complaint_law_rationality"
    MATCH_EXPERIENCE = "complaint_match_experience"
    LAW_ANALYSIS = "complaint_law_analysis"
    FINAL_CONCLUSION = "complaint_final_conclusion"


logger = get_logger(__name__)
COMPLAINT_ANALYSIS_ROUTE = "POST /api/complaint/complaint-analysis"


def _log_preview_text(text: str, limit: int = 8192) -> str:
    """输出日志用的文本预览。"""
    return log_preview_text(text, limit=limit)


def _has_prefetched_extract_payload(payload: dict[str, Any] | None) -> bool:
    """判断是否已有可复用的事项抽取结果。"""
    if not isinstance(payload, dict):
        return False
    complaints = payload.get("complaints")
    if isinstance(complaints, list) and complaints:
        return True
    return False


def _build_issue_phase_timing_extra(trace_context: dict[str, Any] | None, match_strategy: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """构建举报事项分析内部耗时日志附加字段。"""
    payload = {
        "user_id": str((trace_context or {}).get("user_id") or ""),
        "base_id": str((trace_context or {}).get("base_id") or ""),
        "file_id": str((trace_context or {}).get("file_id") or ""),
        "issue_analysis_version": str((trace_context or {}).get("issue_analysis_version") or ""),
        "match_strategy": str(match_strategy or ""),
    }
    payload.update(extra or {})
    return payload


def _log_issue_phase_timing(phase: str, started_at: float, success: bool, trace_context: dict[str, Any] | None, match_strategy: str, extra: dict[str, Any] | None = None) -> None:
    """记录举报事项分析内部阶段耗时。"""
    log_phase_timing(logger, "complaint_app", COMPLAINT_ANALYSIS_ROUTE, phase, timing_elapsed_ms(started_at), success, _build_issue_phase_timing_extra(trace_context, match_strategy, extra))


def _log_two_stage_phase_completion(stage_key: str, started_at: float | None, success: bool, trace_context: dict[str, Any] | None, match_strategy: str) -> None:
    """记录 two-stage 匹配子阶段耗时。"""
    if started_at is None:
        return
    _log_issue_phase_timing(stage_key, started_at, success, trace_context, match_strategy)


def _build_law_rationality_payload(complaints: list[Any], idx: int, incident: str, reported_entities: list[str] | None = None) -> dict[str, Any]:
    """构建单事项合理性分析 skill 输入。"""
    item = complaints[idx] if 0 <= idx < len(complaints) else {}
    if isinstance(item, dict):
        complaint_id = str(item.get("id") or f"complaint_{idx + 1}")
        complaint = {
            "label": str(item.get("label") or f"事项{idx + 1}").strip(),
            "description": str(item.get("description") or "").strip(),
            "claim_text": str(item.get("claim_text") or item.get("description") or item.get("label") or "").strip(),
            "original_quotes": _normalize_text_list(item.get("original_quotes")),
            "evidence_materials": _normalize_text_list(item.get("evidence_materials")),
            "user_cited_laws": _normalize_user_cited_laws(item.get("user_cited_laws")),
        }
    else:
        complaint_id = f"complaint_{idx + 1}"
        complaint = {"label": f"事项{idx + 1}", "description": str(item or "").strip(), "claim_text": str(item or "").strip(), "original_quotes": [], "evidence_materials": [], "user_cited_laws": []}
    return {"index": idx, "complaint_id": complaint_id, "complaint": complaint, "incident": str(incident or "").strip(), "reported_entities": [str(entity).strip() for entity in reported_entities or [] if str(entity).strip()]}


def _law_rationality_max_concurrent() -> int:
    """读取合理性分析 skill 最大并发数。"""
    raw = os.getenv("COMPLAINT_LAW_RATIONALITY_MAX_CONCURRENCY", "100").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 100
    return max(value, 1)


def _law_rationality_item_trace_context(trace_context: dict[str, Any] | None, idx: int, payload: dict[str, Any], complaint_text: str) -> dict[str, Any]:
    """构建单个举报事项合理性分析日志追踪字段。"""
    complaint_id = str(payload.get("complaint_id") or f"complaint_{idx + 1}").strip()
    complaint_hash = hashlib.sha1(str(complaint_text or complaint_id).encode("utf-8")).hexdigest()[:12]
    item_context = dict(trace_context or {})
    item_context.update({
        "match_strategy": normalize_match_strategy((trace_context or {}).get("match_strategy")),
        "complaint_index": str(idx),
        "complaint_id": complaint_id,
        "complaint_hash": complaint_hash,
    })
    return item_context


async def _run_law_rationality_item(complaints: list[Any], idx: int, item: Any, incident: str, trace_tag: str, sem: asyncio.Semaphore, trace_context: dict[str, Any] | None = None, reported_entities: list[str] | None = None) -> dict[str, Any]:
    """执行单个举报事项的合理性分析 skill。"""
    complaint_text = _complaint_item_text(item)
    payload = _build_law_rationality_payload(complaints, idx, incident, reported_entities=reported_entities)
    item_trace_context = _law_rationality_item_trace_context(trace_context, idx, payload, complaint_text)
    item_trace_tag = build_trace_tag(item_trace_context, "law_rationality")
    payload["trace_context"] = item_trace_context
    logger.info("[complaint_law_rationality] %s item_start complaint=%s", item_trace_tag, log_preview_text(complaint_text, limit=500))
    async with sem:
        try:
            analysis = await analyze_complaint_item_with_law_rationality(payload)
        except Exception as exc:
            logger.error("[complaint_law_rationality] %s item_failed complaint=%s err=%s", item_trace_tag, log_preview_text(complaint_text, limit=500), exc)
            analysis = build_law_rationality_fallback(payload, f"合理性分析失败: {exc}")
    logger.info("[complaint_law_rationality] %s item_done result=%s", item_trace_tag, log_preview_json(analysis, limit=4000))
    return analysis


async def complaint_law_rationality_stream(complaints: list[Any], incident: str, trace_context: dict[str, Any] | None = None, reported_entities: list[str] | None = None) -> AsyncGenerator[dict[str, Any], None]:
    """并发调用合理性分析 skill，按完成顺序输出结果。"""
    trace_tag = build_trace_tag(trace_context, "law_rationality")
    complaint_items = list(complaints or [])
    if not complaint_items:
        return
    max_concurrent = min(_law_rationality_max_concurrent(), len(complaint_items))
    logger.info("[complaint_law_rationality] %s stream_start complaints=%s max_concurrent=%s", trace_tag, len(complaint_items), max_concurrent)
    sem = asyncio.Semaphore(max_concurrent)
    tasks = [asyncio.create_task(_run_law_rationality_item(complaint_items, idx, item, incident, trace_tag, sem, trace_context, reported_entities=reported_entities)) for idx, item in enumerate(complaint_items)]
    try:
        for task in asyncio.as_completed(tasks):
            yield await task
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
    logger.info("[complaint_law_rationality] %s stream_end", trace_tag)


async def run_complaint_analysis(user_question: str, latest_summary_or_items: str, match_strategy: str | None = None, trace_context: dict[str, Any] | None = None, prefetched_extract_payload: dict[str, Any] | None = None) -> AsyncGenerator[dict[str, Any], None]:
    now_ms = lambda: int(time.time() * 1000)
    response_id = str(uuid.uuid4())
    message_id = str(uuid.uuid4())
    conversation_id = str(uuid.uuid4())
    resolved_strategy = normalize_match_strategy(match_strategy)
    trace_tag = build_trace_tag(trace_context, resolved_strategy)
    logger.info("[complaint_analysis] %s start question_len=%s summary_len=%s", trace_tag, len(str(user_question or "")), len(str(latest_summary_or_items or "")))
    complaints: list[dict[str, Any]] = []
    match_results: list[dict[str, Any]] = []
    law_results: list[dict[str, Any]] = []
    profile: dict[str, Any] = _empty_complaint_profile()
    error_step = ""

    extract_started = timing_now()
    yield {"event": "node_start", "node_id": ComplaintStep.EXTRACT_ITEMS.value, "type": "thinking", "title": "识别举报事项", "message_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "created": now_ms()}
    incident = ""
    try:
        extract_payload = prefetched_extract_payload if _has_prefetched_extract_payload(prefetched_extract_payload) else {}
        complaints = extract_payload.get("complaints") if isinstance(extract_payload.get("complaints"), list) else []
        incident = str(extract_payload.get("incident") or "").strip()
        profile = extract_payload.get("profile") if isinstance(extract_payload.get("profile"), dict) else _empty_complaint_profile()
        if complaints:
            yield {"event": "node_delta", "node_id": ComplaintStep.EXTRACT_ITEMS.value, "delta": f"识别到 {len(complaints)} 条举报事项。", "line_id": "complaint_items-total", "message_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "created": now_ms()}
        else:
            error_step = ComplaintStep.EXTRACT_ITEMS.value
            yield {"event": "node_delta", "node_id": ComplaintStep.EXTRACT_ITEMS.value, "delta": "当前 scope 的结构化摘要缺少举报事项，无法继续分析。", "line_id": "complaint_items-empty", "message_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "created": now_ms()}
        yield {"event": "node_end", "node_id": ComplaintStep.EXTRACT_ITEMS.value, "status": "completed", "title": "识别举报事项", "message_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "created": now_ms(), "content": {"complaints": complaints, "incident": incident, "profile": profile}}
        _log_issue_phase_timing("issue_analysis_extract_items", extract_started, bool(complaints), trace_context, resolved_strategy, {"complaint_count": len(complaints), "source": "summary_bundle" if _has_prefetched_extract_payload(prefetched_extract_payload) else "missing_summary_bundle"})
        logger.info("[complaint_analysis] %s extract_done complaints=%s incident_len=%s reporter=%s institution=%s elapsed_ms=%s", trace_tag, len(complaints), len(str(incident or "")), str(profile.get("reporter") or ""), str(profile.get("reported_institution") or ""), timing_elapsed_ms(extract_started))
    except Exception as exc:
        logger.error("[complaint_analysis] %s complaint_extract_items failed err=%s", trace_tag, exc)
        _log_issue_phase_timing("issue_analysis_extract_items", extract_started, False, trace_context, resolved_strategy)
        error_step = ComplaintStep.EXTRACT_ITEMS.value
        yield {"event": "node_delta", "node_id": ComplaintStep.EXTRACT_ITEMS.value, "delta": "举报事项提取失败，请稍后重试。", "line_id": "complaint_items-error", "message_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "created": now_ms()}
        yield {"event": "node_end", "node_id": ComplaintStep.EXTRACT_ITEMS.value, "status": "completed", "title": "识别举报事项", "message_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "created": now_ms(), "content": {"complaints": [], "incident": "", "profile": _empty_complaint_profile()}}
    if error_step:
        logger.warning("[complaint_analysis] %s stop_after_step step=%s", trace_tag, error_step)
        yield {"event": "content_delta", "delta": "当前 scope 的结构化摘要缺少举报事项，暂无法完成分析。"}
        return

    law_rationality_started = timing_now()
    reported_entities = [str(entity).strip() for entity in str((trace_context or {}).get("reported_entities") or "").split(",") if str(entity).strip()]
    yield {"event": "node_start", "node_id": ComplaintStep.LAW_RATIONALITY.value, "type": "thinking", "title": "合理性分析", "message_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "created": now_ms()}
    try:
        yield {"event": "node_delta", "node_id": ComplaintStep.LAW_RATIONALITY.value, "delta": "我会逐项核对举报人提及法律是否存在，以及该法律与举报事实是否相关。", "line_id": "complaint_law_rationality-start", "message_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "created": now_ms()}
        rationality_results = [{} for _ in complaints]
        async for item in complaint_law_rationality_stream(complaints, incident, trace_context=trace_context, reported_entities=reported_entities):
            idx = int(item.get("index") or 0)
            if 0 <= idx < len(rationality_results):
                rationality_results[idx] = item
            conclusion = str(item.get("conclusion") or "").strip()
            yield {"event": "node_delta", "node_id": ComplaintStep.LAW_RATIONALITY.value, "delta": f"第 {idx + 1} 个举报事项合理性分析完成", "line_id": f"complaint_law_rationality-{idx + 1}", "message_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "created": now_ms(), "content": item}
            yield {"event": "rationality_item", "node_id": ComplaintStep.LAW_RATIONALITY.value, "content": item, "message_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "created": now_ms()}
            if conclusion:
                logger.info("[complaint_analysis] %s law_rationality_item_done index=%s complaint_id=%s conclusion=%s", trace_tag, idx, str(item.get("complaint_id") or ""), log_preview_text(conclusion, limit=1000))
        yield {"event": "node_end", "node_id": ComplaintStep.LAW_RATIONALITY.value, "status": "completed", "title": "合理性分析", "message_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "created": now_ms(), "content": {"items": rationality_results}}
        _log_issue_phase_timing("issue_analysis_law_rationality", law_rationality_started, True, trace_context, resolved_strategy, {"result_count": len(rationality_results)})
        logger.info("[complaint_analysis] %s law_rationality_done rows=%s elapsed_ms=%s output=%s", trace_tag, len(rationality_results), timing_elapsed_ms(law_rationality_started), log_preview_json(rationality_results))
    except Exception as exc:
        logger.error("[complaint_analysis] %s complaint_law_rationality failed err=%s", trace_tag, exc)
        _log_issue_phase_timing("issue_analysis_law_rationality", law_rationality_started, False, trace_context, resolved_strategy)
        error_step = ComplaintStep.LAW_RATIONALITY.value
        yield {"event": "node_delta", "node_id": ComplaintStep.LAW_RATIONALITY.value, "delta": "合理性分析失败，请稍后重试。", "line_id": "complaint_law_rationality-error", "message_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "created": now_ms()}
        yield {"event": "node_end", "node_id": ComplaintStep.LAW_RATIONALITY.value, "status": "completed", "title": "合理性分析", "message_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "created": now_ms(), "content": {"items": []}}
    if error_step:
        logger.warning("[complaint_analysis] %s stop_after_step step=%s", trace_tag, error_step)
        yield {"event": "content_delta", "delta": "当前无法完成举报事项合理性分析，请稍后重试。"}
        return

    complaint_texts = [_complaint_item_text(item) for item in complaints]
    match_started = timing_now()
    match_stage1_started_at: float | None = None
    match_stage2_started_at: float | None = None
    match_title = "本地法规分析" if resolved_strategy == MATCH_STRATEGY_LAW_SKILL else "经验库匹配"
    match_start_message = "我会基于举报事项、用户提及法律和证据材料，逐项检索本地法规库并判断适用性。" if resolved_strategy == MATCH_STRATEGY_LAW_SKILL else "我会基于举报事项的内容，在专家经验库里查找最合适的一级分类和二级分类。"
    yield {"event": "node_start", "node_id": ComplaintStep.MATCH_EXPERIENCE.value, "type": "thinking", "title": match_title, "message_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "created": now_ms()}
    try:
        yield {"event": "node_delta", "node_id": ComplaintStep.MATCH_EXPERIENCE.value, "delta": match_start_message, "line_id": "complaint_match-start", "message_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "created": now_ms()}
        match_results = [{"complaint": text, "matched": []} for text in complaint_texts]
        async for item in complaint_match_experience_router_stream(complaints, incident, match_strategy=resolved_strategy, trace_context=trace_context, law_rationality_items=rationality_results):
            item_kind = str(item.get("kind") or "").strip()
            if resolved_strategy == MATCH_STRATEGY_TWO_STAGE and item_kind == "stage":
                stage = str(item.get("stage") or "").strip()
                if stage == "first":
                    if match_stage1_started_at is None:
                        match_stage1_started_at = timing_now()
                    yield {"event": "node_delta", "node_id": ComplaintStep.MATCH_EXPERIENCE.value, "delta": "正在判断举报属于哪类问题（如商户、扣款、账户）", "line_id": "complaint_match-two-stage-1", "message_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "created": now_ms()}
                    continue
                if stage == "second":
                    if match_stage1_started_at is not None and match_stage2_started_at is None:
                        _log_two_stage_phase_completion("issue_analysis_match_experience_stage1", match_stage1_started_at, True, trace_context, resolved_strategy)
                    if match_stage2_started_at is None:
                        match_stage2_started_at = timing_now()
                    yield {"event": "node_delta", "node_id": ComplaintStep.MATCH_EXPERIENCE.value, "delta": "正在细化具体违规情形并补充法律依据", "line_id": "complaint_match-two-stage-2", "message_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "created": now_ms()}
                    continue
            idx = int(item.get("index") or 0)
            complaint = str(item.get("complaint") or "").strip()
            matched = item.get("matched") or []
            skipped_reason = str(item.get("skipped_reason") or "").strip()
            if 0 <= idx < len(match_results):
                match_results[idx] = {"complaint": complaint, "matched": matched}
            line_id = f"complaint_match-{idx + 1}"
            if complaint:
                if skipped_reason:
                    yield {"event": "node_delta", "node_id": ComplaintStep.MATCH_EXPERIENCE.value, "delta": f"举报事项：“{complaint}” {skipped_reason}", "line_id": line_id, "message_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "created": now_ms()}
                elif isinstance(matched, list) and matched:
                    top = matched[0] if isinstance(matched[0], dict) else {}
                    primary = str(top.get("first_category") or top.get("一级分类") or "").strip()
                    secondary = str(top.get("overview") or top.get("二级分类") or "").strip()
                    score = str(top.get("匹配度") or "").strip()
                    reason = str(top.get("分类理由") or top.get("分类匹配逻辑说明") or "").strip()
                    label = " / ".join([p for p in [primary, secondary] if p])
                    suffix = f"，匹配度为 **{score}**" if score else ""
                    if reason:
                        suffix = f"{suffix}，理由：{reason}" if suffix else f"，理由：{reason}"
                    delta_text = f"举报事项：“{complaint}” 已匹配到 **{label}{suffix}**" if label else f"举报事项：“{complaint}” 已匹配到分类 **{suffix}**"
                    yield {"event": "node_delta", "node_id": ComplaintStep.MATCH_EXPERIENCE.value, "delta": delta_text, "line_id": line_id, "message_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "created": now_ms()}
                else:
                    yield {"event": "node_delta", "node_id": ComplaintStep.MATCH_EXPERIENCE.value, "delta": f"举报事项：“{complaint}” **暂未在经验库中找到专家经验。**", "line_id": line_id, "message_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "created": now_ms()}
        if resolved_strategy == MATCH_STRATEGY_TWO_STAGE:
            if match_stage2_started_at is not None:
                _log_two_stage_phase_completion("issue_analysis_match_experience_stage2", match_stage2_started_at, True, trace_context, resolved_strategy)
            elif match_stage1_started_at is not None:
                _log_two_stage_phase_completion("issue_analysis_match_experience_stage1", match_stage1_started_at, True, trace_context, resolved_strategy)
        yield {"event": "node_end", "node_id": ComplaintStep.MATCH_EXPERIENCE.value, "status": "completed", "title": match_title, "message_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "created": now_ms(), "content": {"matches": match_results, "match_strategy": resolved_strategy}}
        _log_issue_phase_timing("issue_analysis_match_experience", match_started, True, trace_context, resolved_strategy, {"complaint_count": len(complaint_texts)})
        logger.info("[complaint_analysis] %s match_done strategy=%s rows=%s elapsed_ms=%s output=%s", trace_tag, resolved_strategy, len(match_results), timing_elapsed_ms(match_started), log_preview_json(match_results))
    except Exception as exc:
        logger.error("[complaint_analysis] %s complaint_match_experience failed strategy=%s err=%s", trace_tag, resolved_strategy, exc)
        if resolved_strategy == MATCH_STRATEGY_TWO_STAGE:
            if match_stage2_started_at is not None:
                _log_two_stage_phase_completion("issue_analysis_match_experience_stage2", match_stage2_started_at, False, trace_context, resolved_strategy)
            elif match_stage1_started_at is not None:
                _log_two_stage_phase_completion("issue_analysis_match_experience_stage1", match_stage1_started_at, False, trace_context, resolved_strategy)
        _log_issue_phase_timing("issue_analysis_match_experience", match_started, False, trace_context, resolved_strategy, {"complaint_count": len(complaint_texts)})
        error_step = ComplaintStep.MATCH_EXPERIENCE.value
        yield {"event": "node_delta", "node_id": ComplaintStep.MATCH_EXPERIENCE.value, "delta": "经验库匹配失败，请稍后重试。", "line_id": "complaint_match-error", "message_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "created": now_ms()}
        yield {"event": "node_end", "node_id": ComplaintStep.MATCH_EXPERIENCE.value, "status": "completed", "title": "经验库匹配", "message_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "created": now_ms(), "content": {"matches": [], "match_strategy": resolved_strategy}}
    if error_step:
        logger.warning("[complaint_analysis] %s stop_after_step step=%s", trace_tag, error_step)
        yield {"event": "content_delta", "delta": "当前无法完成举报事项分析，请稍后重试。"}
        return

    law_started = timing_now()
    yield {"event": "node_start", "node_id": ComplaintStep.LAW_ANALYSIS.value, "type": "thinking", "title": "法律条文分析", "message_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "created": now_ms()}
    try:
        law_results = await complaint_law_analysis(match_results, trace_context=trace_context, match_strategy=resolved_strategy)
        for idx, item in enumerate(law_results or []):
            categories = item.get("categories") or []
            law_count = 0
            for cat in categories:
                laws = (cat or {}).get("法律法规及其原文") if isinstance(cat, dict) else []
                if isinstance(laws, list):
                    law_count += len(laws)
            line_id = f"complaint_law-{idx + 1}"
            delta_text = f"事项 {idx + 1} 在专家经验库中找到 {law_count} 条法律条文" if categories else f"事项 {idx + 1} 未匹配到专家经验，暂无可引用的法律条文"
            yield {"event": "node_delta", "node_id": ComplaintStep.LAW_ANALYSIS.value, "delta": delta_text, "line_id": line_id, "message_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "created": now_ms()}
        yield {"event": "node_end", "node_id": ComplaintStep.LAW_ANALYSIS.value, "status": "completed", "title": "法律条文分析", "message_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "created": now_ms(), "content": {"laws": law_results}}
        _log_issue_phase_timing("issue_analysis_law_analysis", law_started, True, trace_context, resolved_strategy, {"result_count": len(law_results)})
        logger.info("[complaint_analysis] %s law_done rows=%s elapsed_ms=%s output=%s", trace_tag, len(law_results), timing_elapsed_ms(law_started), log_preview_json(law_results))
    except Exception as exc:
        logger.error("[complaint_analysis] %s complaint_law_analysis failed err=%s", trace_tag, exc)
        _log_issue_phase_timing("issue_analysis_law_analysis", law_started, False, trace_context, resolved_strategy)
        error_step = ComplaintStep.LAW_ANALYSIS.value
        yield {"event": "node_delta", "node_id": ComplaintStep.LAW_ANALYSIS.value, "delta": "法律条文分析失败，请稍后重试。", "line_id": "complaint_law-error", "message_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "created": now_ms()}
        yield {"event": "node_end", "node_id": ComplaintStep.LAW_ANALYSIS.value, "status": "completed", "title": "法律条文分析", "message_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "created": now_ms(), "content": {"laws": []}}
    if error_step:
        logger.warning("[complaint_analysis] %s stop_after_step step=%s", trace_tag, error_step)
        yield {"event": "content_delta", "delta": "当前无法完成举报事项分析，请稍后重试。"}
        return

    final_started = timing_now()
    yield {"event": "node_start", "node_id": ComplaintStep.FINAL_CONCLUSION.value, "type": "thinking", "title": "综合结论", "message_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "created": now_ms()}
    yield {"event": "node_delta", "node_id": ComplaintStep.FINAL_CONCLUSION.value, "delta": "已完成调阅材料和核实思路分析，正在组装结构化结果。", "line_id": "complaint_summary-assemble", "message_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "created": now_ms()}
    yield {"event": "node_end", "node_id": ComplaintStep.FINAL_CONCLUSION.value, "status": "completed", "title": "综合结论", "message_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "created": now_ms(), "content": {"conclusion_text": ""}}
    _log_issue_phase_timing("issue_analysis_final_conclusion", final_started, True, trace_context, resolved_strategy, {"analysis_count": len(law_results or []), "skipped_llm": True})
    logger.info("[complaint_analysis] %s final_done skipped_llm=true elapsed_ms=%s", trace_tag, timing_elapsed_ms(final_started))
    logger.info("[complaint_analysis] %s end", trace_tag)


async def complaint_analysis(query: str, source_text: str, match_strategy: str | None = None, trace_context: dict[str, Any] | None = None) -> str:
    chunks: list[str] = []
    async for event in run_complaint_analysis(query, source_text, match_strategy=match_strategy, trace_context=trace_context):
        if event.get("event") == "content_delta":
            delta = event.get("delta")
            if isinstance(delta, str) and delta:
                chunks.append(delta)
    return "".join(chunks)


async def complaint_extract_items_payload(text: str) -> dict[str, Any]:
    """一次抽取举报事项、事情经过与举报画像。"""
    try:
        client = get_async_openai_client()
        model = get_complaint_extract_items_model()
        prompt = (
            f"{_load_sys_prompt()}\n\n"
            "你必须只输出一个 JSON 对象字符串，禁止输出解释、代码块或其它文字。"
            "JSON 格式必须严格为："
            "{\"complaints\":[{\"label\":\"事项短标签\",\"description\":\"完整事项表述\",\"claim_text\":\"客观中立的举报表述\",\"original_quotes\":[\"原文短句\"],\"evidence_materials\":[\"证据材料\"],\"user_cited_laws\":[{\"raw_text\":\"原文法律表述\",\"normalized_law\":\"归一化法律名称\",\"cited_clause_text\":\"条款或义务原文\"}]}],\"incident\":\"事情经过\",\"profile\":{\"reporter\":\"\",\"report_date\":\"\",\"reported_institution\":\"\",\"appeals\":[\"\"],\"title_base\":\"\"}}"
            "要求：incident 提取事情经过；profile 内提取举报人、举报日期、被举报机构、诉求、标题基础；缺失字段返回空字符串或空数组，不要编造。"
        )
        content = f"输入内容：{(text or '').strip()}"
        logger.info("complaint_extract_items input model=%s content=%s", model, _log_preview_text(content))
        extra_body = build_thinking_extra_body(model, False)
        messages = [{"role": "system", "content": prompt}, {"role": "user", "content": content}]
        metric_stage = "complaint_issue_extract_items"
        call_id = record_llm_prompt_metrics(metric_stage, model=model, messages=messages, metadata={"business_stage": "complaint_issue_analysis"})
        stream = await client.chat.completions.create(model=model, messages=messages, temperature=0.2, extra_body=extra_body, stream=True)
        started_at = time.time()
        timeout_seconds = get_stream_timeout_seconds()
        heartbeat_seconds = get_stream_heartbeat_seconds()
        stream_iter = stream.__aiter__()
        chunk_count = 0
        last_chunk_at = started_at
        next_heartbeat_at = started_at + heartbeat_seconds if heartbeat_seconds > 0 else 0.0
        answer_chunks: list[str] = []
        reasoning_chunks: list[str] = []
        first_token_ms = 0
        while True:
            try:
                chunk, stream_end = await read_next_stream_chunk(stream_iter, timeout_seconds)
            except asyncio.TimeoutError as exc:
                idle_ms = int((time.time() - last_chunk_at) * 1000)
                logger.error("complaint_extract_items stream_timeout model=%s timeout_seconds=%s idle_ms=%s chunk_count=%s", model, timeout_seconds, idle_ms, chunk_count)
                raise TimeoutError("complaint_extract_items stream timeout") from exc
            if stream_end:
                break
            chunk_count += 1
            now = time.time()
            last_chunk_at = now
            if heartbeat_seconds > 0 and now >= next_heartbeat_at:
                logger.info("complaint_extract_items stream_heartbeat model=%s elapsed_ms=%s chunk_count=%s", model, int((now - started_at) * 1000), chunk_count)
                next_heartbeat_at = now + heartbeat_seconds
            delta = chunk.choices[0].delta if chunk and chunk.choices else None
            if not delta:
                continue
            content_delta, reasoning_delta, _reasoning_field = extract_stream_deltas(delta)
            if isinstance(content_delta, str) and content_delta:
                if not first_token_ms:
                    first_token_ms = int((time.time() - started_at) * 1000)
                answer_chunks.append(content_delta)
            if isinstance(reasoning_delta, str) and reasoning_delta:
                if not first_token_ms:
                    first_token_ms = int((time.time() - started_at) * 1000)
                reasoning_chunks.append(reasoning_delta)
        logger.info("complaint_extract_items stream_stats model=%s answer_len=%s reasoning_len=%s chunk_count=%s", model, len("".join(answer_chunks)), len("".join(reasoning_chunks)), chunk_count)
        record_llm_output_metrics(metric_stage, model=model, output_text="".join(answer_chunks) or "".join(reasoning_chunks), first_token_ms=first_token_ms, llm_duration_ms=int((time.time() - started_at) * 1000), metadata={"business_stage": "complaint_issue_analysis"}, call_id=call_id)
        logger.info("complaint_extract_items answer_output=%s", _log_preview_text("".join(answer_chunks)))
        logger.info("complaint_extract_items reasoning_output=%s", _log_preview_text("".join(reasoning_chunks)))
        raw_result = "".join(answer_chunks).strip()
        if not raw_result:
            raw_result = "".join(reasoning_chunks).strip()
            if raw_result:
                logger.warning("complaint_extract_items content empty, fallback to reasoning content")
        logger.info("complaint_extract_items raw_result_preview=%s", _log_preview_text(raw_result))
        payload = _parse_complaint_extract_payload(raw_result)
        complaints = payload.get("complaints") if isinstance(payload.get("complaints"), list) else []
        incident = str(payload.get("incident") or "").strip()
        profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else _empty_complaint_profile()
        logger.info("complaint_extract_items parsed_result complaints_count=%s incident_present=%s reporter=%s report_date=%s institution=%s appeals_count=%s title_base=%s", len(complaints), bool(incident), str(profile.get("reporter") or ""), str(profile.get("report_date") or ""), str(profile.get("reported_institution") or ""), len(profile.get("appeals") or []), str(profile.get("title_base") or ""))
        logger.info("complaints: %s", log_preview_json([item.get("label") or item.get("description") for item in complaints]))
        return payload
    except Exception as exc:
        raise exc


async def complaint_extract_items(text: str) -> tuple[list[dict[str, Any]], str]:
    """兼容旧调用，仅返回举报事项与事情经过。"""
    payload = await complaint_extract_items_payload(text)
    complaints = payload.get("complaints") if isinstance(payload.get("complaints"), list) else []
    incident = str(payload.get("incident") or "").strip()
    return complaints, incident


async def _match_experience_single(complaint: str, *, prompt: str, model: str, sem: asyncio.Semaphore, incident: str, trace_context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    async with sem:
        client = get_async_openai_client()
        incident_text = str(incident or "").strip()
        if incident_text:
            content = f"事情经过：{incident_text}\n举报事项：{complaint}"
        else:
            content = f"举报事项：{complaint}"
        extra_body = build_thinking_extra_body(model, get_complaint_match_experience_thinking())
        trace_tag = build_trace_tag(trace_context, MATCH_STRATEGY_LEGACY)
        started_at = time.time()
        try:
            logger.info("[complaint_match_experience] %s run_single_start model=%s complaint=%s content=%s extra_body=%s", trace_tag, model, complaint, _log_preview_text(content), log_preview_json(extra_body))
            messages = [{"role": "system", "content": prompt}, {"role": "user", "content": content}]
            metric_stage = "complaint_issue_match_experience"
            call_id = record_llm_prompt_metrics(metric_stage, model=model, messages=messages, metadata={"business_stage": "complaint_issue_analysis", **(trace_context or {})})
            stream = await client.chat.completions.create(model=model, messages=messages, temperature=0.2, extra_body=extra_body, stream=True)
        except Exception as exc:
            logger.error("[complaint_match_experience] %s llm_call_error complaint=%s err=%s", trace_tag, complaint, exc)
            raise
        timeout_seconds = get_stream_timeout_seconds()
        heartbeat_seconds = get_stream_heartbeat_seconds()
        stream_iter = stream.__aiter__()
        chunk_count = 0
        last_chunk_at = started_at
        next_heartbeat_at = started_at + heartbeat_seconds if heartbeat_seconds > 0 else 0.0
        answer_chunks: list[str] = []
        reasoning_chunks: list[str] = []
        seen_first = False
        first_chunk_ms = -1
        while True:
            try:
                chunk, stream_end = await read_next_stream_chunk(stream_iter, timeout_seconds)
            except asyncio.TimeoutError as exc:
                idle_ms = int((time.time() - last_chunk_at) * 1000)
                logger.error("[complaint_match_experience] %s stream_timeout complaint=%s timeout_seconds=%s idle_ms=%s chunk_count=%s", trace_tag, complaint, timeout_seconds, idle_ms, chunk_count)
                raise TimeoutError("complaint_match_experience stream timeout") from exc
            if stream_end:
                break
            chunk_count += 1
            now = time.time()
            last_chunk_at = now
            if heartbeat_seconds > 0 and now >= next_heartbeat_at:
                logger.info("[complaint_match_experience] %s stream_heartbeat complaint=%s elapsed_ms=%s chunk_count=%s", trace_tag, complaint, int((now - started_at) * 1000), chunk_count)
                next_heartbeat_at = now + heartbeat_seconds
            delta = chunk.choices[0].delta if chunk and chunk.choices else None
            if not delta:
                continue
            content_delta, reasoning_delta, reasoning_field = extract_stream_deltas(delta)
            if not seen_first and ((isinstance(content_delta, str) and content_delta) or (isinstance(reasoning_delta, str) and reasoning_delta)):
                first_chunk_ms = int((time.time() - started_at) * 1000)
                logger.info("[complaint_match_experience] %s run_single_first_chunk complaint=%s reasoning_field=%s first_chunk_ms=%s", trace_tag, complaint, reasoning_field, first_chunk_ms)
                seen_first = True
            if isinstance(content_delta, str) and content_delta:
                answer_chunks.append(content_delta)
            if isinstance(reasoning_delta, str) and reasoning_delta:
                reasoning_chunks.append(reasoning_delta)
        logger.info("[complaint_match_experience] %s stream_stats complaint=%s answer_len=%s reasoning_len=%s elapsed_ms=%s chunk_count=%s", trace_tag, complaint, len("".join(answer_chunks)), len("".join(reasoning_chunks)), int((time.time() - started_at) * 1000), chunk_count)
        record_llm_output_metrics(metric_stage, model=model, output_text="".join(answer_chunks) or "".join(reasoning_chunks), first_token_ms=max(0, first_chunk_ms), llm_duration_ms=int((time.time() - started_at) * 1000), metadata={"business_stage": "complaint_issue_analysis", **(trace_context or {})}, call_id=call_id)
        logger.info("[complaint_match_experience] %s answer_output complaint=%s content=%s", trace_tag, complaint, _log_preview_text("".join(answer_chunks)))
        logger.info("[complaint_match_experience] %s reasoning_output complaint=%s content=%s", trace_tag, complaint, _log_preview_text("".join(reasoning_chunks)))
        raw = "".join(answer_chunks).strip()
        if not raw:
            raw = "".join(reasoning_chunks).strip()
            if raw:
                logger.warning("[complaint_match_experience] %s content empty, fallback to reasoning complaint=%s", trace_tag, complaint)
        logger.info("[complaint_match_experience] %s run_single_end complaint=%s raw=%s", trace_tag, complaint, _log_preview_text(raw))
        parsed = _parse_json_array(raw)
        logger.info("[complaint_match_experience] %s parsed_count complaint=%s count=%s", trace_tag, complaint, len(parsed))
        if not parsed:
            logger.warning("[complaint_match_experience] %s parse_empty complaint=%s raw=%s", trace_tag, complaint, _log_preview_text(raw))
        return parsed


async def _legacy_match_task_with_index(idx: int, complaint: str, *, prompt: str, model: str, sem: asyncio.Semaphore, incident: str, trace_context: dict[str, Any] | None = None) -> tuple[int, str, list[dict[str, Any]], str]:
    try:
        result = await _match_experience_single(complaint, prompt=prompt, model=model, sem=sem, incident=incident, trace_context=trace_context)
        return idx, complaint, result, ""
    except Exception as exc:
        return idx, complaint, [], str(exc)


async def complaint_match_experience_stream(complaints: list[str], incident: str, trace_context: dict[str, Any] | None = None) -> AsyncGenerator[dict[str, Any], None]:
    model = get_complaint_match_experience_model()
    experience = get_expert_experience_cached()
    prompt = _build_classification_prompt()
    experience_data = _load_experience_data(experience)
    sem = asyncio.Semaphore(min(4, max(1, len(complaints or []))))
    trace_tag = build_trace_tag(trace_context, MATCH_STRATEGY_LEGACY)
    logger.info("[complaint_match_experience] %s stream_start complaints=%s model=%s thinking=%s", trace_tag, len(complaints or []), model, get_complaint_match_experience_thinking())
    task_inputs = []
    for idx, item in enumerate(complaints or []):
        text = str(item or "").strip()
        if not text:
            continue
        task_inputs.append((idx, text))

    tasks = [asyncio.create_task(_legacy_match_task_with_index(idx, complaint, prompt=prompt, model=model, sem=sem, incident=incident, trace_context=trace_context)) for idx, complaint in task_inputs]
    for coro in asyncio.as_completed(tasks):
        idx, complaint, result, task_error = await coro
        if task_error:
            logger.error("[complaint_match_experience] %s task_error complaint=%s err=%s", trace_tag, complaint, task_error)
            yield {"index": idx, "complaint": complaint, "matched": []}
            continue
        matched = _match_experience_entries(result, experience_data)
        logger.info("[complaint_match_experience] %s task_done idx=%s complaint=%s matched_count=%s", trace_tag, idx, complaint, len(matched))
        yield {"index": idx, "complaint": complaint, "matched": matched}
    logger.info("[complaint_match_experience] %s stream_end", trace_tag)


async def complaint_match_experience(complaints: list[str], incident: str, trace_context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    async for item in complaint_match_experience_stream(complaints, incident, trace_context=trace_context):
        idx = int(item.get("index") or 0)
        if len(matches) <= idx:
            matches.extend([{}] * (idx + 1 - len(matches)))
        matches[idx] = {"complaint": item.get("complaint"), "matched": item.get("matched") or []}
    return matches


def _remove_law_line_reference(value: Any) -> str:
    """移除法规库行号定位信息。"""
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"[（(]\s*(?:约|大致)?第?\s*\d+\s*(?:[-~—－至到]\s*\d+\s*)?行\s*[）)]", "", text)
    text = re.sub(r"(?:约|大致)?第?\s*\d+\s*(?:[-~—－至到]\s*\d+\s*)?行", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_law_article(value: Any) -> str:
    """规范化法律条款名，过滤泛化条款。"""
    text = _remove_law_line_reference(value)
    if not text:
        return ""
    compact = re.sub(r"\s+", "", text)
    if compact in {"相关条款", "相关规定", "相关法规", "相关法规条款", "相关法律条款", "相关法律法规", "相关法律法规条款"}:
        return ""
    if not re.search(r"第[一二三四五六七八九十百千万零〇\d]+[条款项目章节]", compact):
        return ""
    return text


def _build_law_skill_payload(complaints: list[Any], idx: int, incident: str, law_rationality_item: dict[str, Any] | None = None) -> dict[str, Any]:
    """构建单事项本地法规 skill 输入。"""
    item = complaints[idx] if 0 <= idx < len(complaints) else {}
    if isinstance(item, dict):
        complaint_id = str(item.get("id") or f"complaint_{idx + 1}")
        label = str(item.get("label") or f"事项{idx + 1}").strip()
        claim_text = str(item.get("claim_text") or item.get("description") or item.get("label") or "").strip()
        original_quotes = _normalize_text_list(item.get("original_quotes"))
        evidence_materials = _normalize_text_list(item.get("evidence_materials"))
        user_cited_laws = _normalize_user_cited_laws(item.get("user_cited_laws"))
    else:
        complaint_id = f"complaint_{idx + 1}"
        label = f"事项{idx + 1}"
        claim_text = str(item or "").strip()
        original_quotes = []
        evidence_materials = []
        user_cited_laws = []
    briefs = []
    for brief_idx, brief_item in enumerate(complaints or [], start=1):
        briefs.append({"id": str((brief_item or {}).get("id") or f"complaint_{brief_idx}") if isinstance(brief_item, dict) else f"complaint_{brief_idx}", "label": str((brief_item or {}).get("label") or f"事项{brief_idx}") if isinstance(brief_item, dict) else f"事项{brief_idx}"})
    return {
        "case_profile": {"reporter": "", "reported_institutions": [], "case_summary": str(incident or "")},
        "complaint_item": {
            "id": complaint_id,
            "label": label,
            "claim_text": claim_text,
            "original_quotes": original_quotes,
            "evidence_materials": evidence_materials,
            "user_cited_laws": user_cited_laws,
        },
        "law_rationality": law_rationality_item if isinstance(law_rationality_item, dict) else {},
        "all_complaint_items_brief": briefs,
    }


def _build_law_skill_batch_payload(complaints: list[Any], runnable_items: list[tuple[int, Any, dict[str, Any] | None]], incident: str) -> dict[str, Any]:
    """构建批量本地法规 skill 输入。"""
    items: list[dict[str, Any]] = []
    for idx, item, law_rationality_item in runnable_items:
        single_payload = _build_law_skill_payload(complaints, idx, incident, law_rationality_item=law_rationality_item)
        complaint_item = single_payload.get("complaint_item") if isinstance(single_payload.get("complaint_item"), dict) else {}
        batch_item = dict(complaint_item)
        batch_item["index"] = idx
        batch_item["law_rationality"] = law_rationality_item if isinstance(law_rationality_item, dict) else {}
        items.append(batch_item)
    return {
        "case_profile": {"reporter": "", "reported_institutions": [], "case_summary": str(incident or "")},
        "complaint_items": items,
    }


def _law_skill_law_summary(analysis: dict[str, Any]) -> dict[str, Any]:
    """提取本地法规 skill 命中的法律摘要。"""
    user_laws: list[dict[str, Any]] = []
    possible_laws: list[dict[str, Any]] = []
    for item in analysis.get("user_cited_laws_analysis") or []:
        if not isinstance(item, dict):
            continue
        user_laws.append({
            "raw_text": str(item.get("raw_text") or ""),
            "normalized_law": str(item.get("normalized_law") or ""),
            "evidence_match_level": str(item.get("evidence_match_level") or ""),
            "matched_clauses": item.get("matched_clauses") if isinstance(item.get("matched_clauses"), list) else [],
        })
    for item in analysis.get("possible_laws") or []:
        if not isinstance(item, dict):
            continue
        possible_laws.append({
            "law": str(item.get("law") or ""),
            "reason": str(item.get("reason") or ""),
            "matched_clauses": item.get("matched_clauses") if isinstance(item.get("matched_clauses"), list) else [],
        })
    return {
        "basic_fact_analysis": analysis.get("basic_fact_analysis") if isinstance(analysis.get("basic_fact_analysis"), dict) else {},
        "legal_existence_checks": analysis.get("legal_existence_checks") if isinstance(analysis.get("legal_existence_checks"), list) else [],
        "law_applicability_analysis": analysis.get("law_applicability_analysis") if isinstance(analysis.get("law_applicability_analysis"), list) else [],
        "reasonableness_conclusion": str(analysis.get("reasonableness_conclusion") or ""),
        "user_cited_laws": user_laws,
        "possible_laws": possible_laws,
    }


def _build_law_skill_fallback_analysis(complaint_id: str, complaint_text: str, reason: str) -> dict[str, Any]:
    """构建本地法规分析单项兜底结果。"""
    return {
        "complaint_id": complaint_id,
        "neutral_claim": complaint_text,
        "basic_fact_analysis": {"fact_summary": complaint_text, "business_scene": "", "key_evidence": [], "missing_facts": [reason]},
        "legal_existence_checks": [],
        "law_applicability_analysis": [],
        "verification_points": [reason],
        "document_checklist": [],
        "reasonableness_conclusion": "本地法规分析失败，暂无法判断举报事项合理性。",
        "preliminary_conclusion": reason,
        "user_cited_laws_analysis": [],
        "possible_laws": [],
    }


def _law_rationality_matched_clauses(law_rationality_item: dict[str, Any] | None) -> list[dict[str, str]]:
    """从合理性分析结果提取已知法律条款。"""
    if not isinstance(law_rationality_item, dict):
        return []
    clauses: list[dict[str, str]] = []
    for item in law_rationality_item.get("related_articles") or []:
        if not isinstance(item, dict):
            continue
        law = _remove_law_line_reference(item.get("law") or "")
        article = _normalize_law_article(item.get("article") or "")
        text_summary = _remove_law_line_reference(item.get("text_summary") or "")
        if law and article:
            clauses.append({"law": law, "article": article, "text_summary": text_summary, "source": "law_rationality"})
    return clauses


def _law_rationality_possible_laws(law_rationality_item: dict[str, Any] | None) -> list[dict[str, Any]]:
    """把合理性分析中的法律线索转成兼容法律依据结构。"""
    if not isinstance(law_rationality_item, dict):
        return []
    clauses = _law_rationality_matched_clauses(law_rationality_item)
    laws: list[dict[str, Any]] = []
    if clauses:
        by_law: dict[str, list[dict[str, str]]] = {}
        for clause in clauses:
            by_law.setdefault(str(clause.get("law") or ""), []).append(clause)
        for law, matched_clauses in by_law.items():
            if law:
                laws.append({"law": law, "reason": "前置合理性分析已识别为相关法律条款。", "matched_clauses": matched_clauses, "source": "law_rationality"})
        return laws
    for law in _normalize_text_list(law_rationality_item.get("candidate_laws")):
        laws.append({"law": law, "reason": "前置合理性分析给出的候选法律。", "matched_clauses": [], "source": "law_rationality"})
    return laws


def _complete_law_skill_analysis(analysis: dict[str, Any], complaint_id: str, complaint_text: str, law_rationality_item: dict[str, Any] | None) -> dict[str, Any]:
    """用已知事项和法律线索补齐兼容输出字段。"""
    result = dict(analysis or {})
    result["complaint_id"] = str(result.get("complaint_id") or complaint_id)
    result["neutral_claim"] = str(result.get("neutral_claim") or complaint_text)
    result["document_checklist"] = _to_str_list(result.get("document_checklist"))
    result["verification_points"] = _to_str_list(result.get("verification_points"))
    result["basic_fact_analysis"] = result.get("basic_fact_analysis") if isinstance(result.get("basic_fact_analysis"), dict) else {"fact_summary": complaint_text, "business_scene": "", "key_evidence": [], "missing_facts": []}
    result["evidence_summary"] = result.get("evidence_summary") if isinstance(result.get("evidence_summary"), list) else []
    result["legal_existence_checks"] = result.get("legal_existence_checks") if isinstance(result.get("legal_existence_checks"), list) else []
    result["law_applicability_analysis"] = result.get("law_applicability_analysis") if isinstance(result.get("law_applicability_analysis"), list) else []
    result["user_cited_laws_analysis"] = result.get("user_cited_laws_analysis") if isinstance(result.get("user_cited_laws_analysis"), list) else []
    result["possible_laws"] = _law_rationality_possible_laws(law_rationality_item)
    result["reasonableness_conclusion"] = str((law_rationality_item or {}).get("conclusion") or result.get("reasonableness_conclusion") or "")
    result["preliminary_conclusion"] = str(result.get("preliminary_conclusion") or "基于前置合理性分析给出的法律线索，已生成调阅材料和核实思路。")
    return result


def _limit_law_skill_document_checklists(analyses: list[dict[str, Any]], max_chars: int = 1000) -> list[dict[str, Any]]:
    """限制所有事项调阅材料总字数。"""
    used = 0
    for analysis in analyses:
        items = _to_str_list(analysis.get("document_checklist"))
        limited_items: list[str] = []
        for item in items:
            remaining = max_chars - used
            if remaining <= 0:
                break
            text = item[:remaining].strip() if len(item) > remaining else item
            if not text:
                continue
            limited_items.append(text)
            used += len(text)
        analysis["document_checklist"] = limited_items
    return analyses


def _build_law_skill_match_result(idx: int, complaint_text: str, analysis: dict[str, Any]) -> dict[str, Any]:
    """将本地法规分析结果包装成经验匹配兼容结构。"""
    matched = [{
        "source": "law_skill",
        "first_category": "",
        "overview": str(analysis.get("neutral_claim") or complaint_text),
        "匹配度": "待核实",
        "分类理由": str(analysis.get("preliminary_conclusion") or ""),
        "analysis": analysis,
    }]
    return {"index": idx, "complaint": complaint_text, "matched": matched}


async def _run_law_skill_match_batch(complaints: list[Any], runnable_items: list[tuple[int, Any, dict[str, Any] | None]], incident: str, trace_tag: str, trace_context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """批量执行合理举报事项的本地法规 skill 分析。"""
    payload = _build_law_skill_batch_payload(complaints, runnable_items, incident)
    batch_trace_context = dict(trace_context or {})
    batch_trace_context.update({"match_strategy": MATCH_STRATEGY_LAW_SKILL, "complaint_count": str(len(runnable_items))})
    payload["trace_context"] = batch_trace_context
    logger.info("[complaint_law_skill] %s batch_start runnable=%s", trace_tag, len(runnable_items))
    try:
        batch_analysis = await analyze_complaint_items_with_law_skill(payload)
    except Exception as exc:
        logger.error("[complaint_law_skill] %s batch_failed err=%s", trace_tag, exc)
        batch_analysis = {"items": []}
    raw_items = batch_analysis.get("items") if isinstance(batch_analysis.get("items"), list) else []
    analysis_by_id = {str(item.get("complaint_id") or "").strip(): item for item in raw_items if isinstance(item, dict)}
    analysis_by_index = {int(item.get("index") or 0): item for item in raw_items if isinstance(item, dict) and str(item.get("index") or "").strip()}
    completed_items: list[tuple[int, str, str, dict[str, Any]]] = []
    for idx, item, law_rationality_item in runnable_items:
        complaint_text = _complaint_item_text(item)
        complaint_id = str(item.get("id") or f"complaint_{idx + 1}").strip() if isinstance(item, dict) else f"complaint_{idx + 1}"
        analysis = analysis_by_id.get(complaint_id) or analysis_by_index.get(idx)
        if not isinstance(analysis, dict):
            analysis = _build_law_skill_fallback_analysis(complaint_id, complaint_text, "核查材料和核实思路生成未返回该举报事项结果，需人工复核。")
        analysis = _complete_law_skill_analysis(analysis, complaint_id, complaint_text, law_rationality_item)
        completed_items.append((idx, complaint_id, complaint_text, analysis))
    _limit_law_skill_document_checklists([item[3] for item in completed_items], 1000)
    results: list[dict[str, Any]] = []
    for idx, complaint_id, complaint_text, analysis in completed_items:
        results.append(_build_law_skill_match_result(idx, complaint_text, analysis))
        logger.info("[complaint_law_skill] %s batch_item_done index=%s complaint_id=%s law_summary=%s", trace_tag, idx, complaint_id, log_preview_json(_law_skill_law_summary(analysis), limit=4000))
    logger.info("[complaint_law_skill] %s batch_done result_count=%s", trace_tag, len(results))
    return results


async def complaint_law_skill_match_stream(complaints: list[Any], incident: str, trace_context: dict[str, Any] | None = None, law_rationality_items: list[dict[str, Any]] | None = None) -> AsyncGenerator[dict[str, Any], None]:
    """批量调用本地法规分析 skill，输出兼容经验匹配结构的结果。"""
    trace_tag = build_trace_tag(trace_context, MATCH_STRATEGY_LAW_SKILL)
    complaint_items = list(complaints or [])
    runnable_items: list[tuple[int, Any, dict[str, Any] | None]] = []
    for idx, item in enumerate(complaint_items):
        law_rationality_item = law_rationality_items[idx] if isinstance(law_rationality_items, list) and idx < len(law_rationality_items) and isinstance(law_rationality_items[idx], dict) else None
        if isinstance(law_rationality_item, dict) and str(law_rationality_item.get("reasonable_status") or "").strip() == "不合理":
            complaint_text = _complaint_item_text(item)
            logger.info("[complaint_law_skill] %s skip_before_skill index=%s complaint=%s rationality=%s", trace_tag, idx, log_preview_text(complaint_text, limit=500), log_preview_json(law_rationality_item, limit=2000))
            yield {"index": idx, "complaint": complaint_text, "matched": [], "skipped_reason": "合理性分析结论为不合理，跳过本地法规深入分析。"}
            continue
        runnable_items.append((idx, item, law_rationality_item))
    if not runnable_items:
        logger.info("[complaint_law_skill] %s stream_end no_runnable_items", trace_tag)
        return
    logger.info("[complaint_law_skill] %s stream_start complaints=%s runnable=%s batch=1", trace_tag, len(complaint_items), len(runnable_items))
    for result in await _run_law_skill_match_batch(complaint_items, runnable_items, incident, trace_tag, trace_context):
        yield result
    logger.info("[complaint_law_skill] %s stream_end", trace_tag)


async def complaint_match_experience_router_stream(complaints: list[Any], incident: str, match_strategy: str | None = None, trace_context: dict[str, Any] | None = None, law_rationality_items: list[dict[str, Any]] | None = None) -> AsyncGenerator[dict[str, Any], None]:
    resolved_strategy = normalize_match_strategy(match_strategy)
    trace_tag = build_trace_tag(trace_context, resolved_strategy)
    logger.info("[complaint_match_router] %s router_stream_start complaints=%s", trace_tag, len(complaints or []))
    if resolved_strategy == MATCH_STRATEGY_LAW_SKILL:
        async for item in complaint_law_skill_match_stream(complaints, incident, trace_context=trace_context, law_rationality_items=law_rationality_items):
            yield item
        logger.info("[complaint_match_router] %s router_stream_end strategy=%s", trace_tag, resolved_strategy)
        return
    complaint_texts = [_complaint_item_text(item) for item in complaints]
    if resolved_strategy == MATCH_STRATEGY_TWO_STAGE:
        async for item in complaint_match_experience_two_stage_stream(complaint_texts, incident, trace_context=trace_context):
            yield item
        logger.info("[complaint_match_router] %s router_stream_end strategy=%s", trace_tag, resolved_strategy)
        return
    async for item in complaint_match_experience_stream(complaint_texts, incident, trace_context=trace_context):
        yield item
    logger.info("[complaint_match_router] %s router_stream_end strategy=%s", trace_tag, resolved_strategy)


async def complaint_match_experience_router(complaints: list[str], incident: str, match_strategy: str | None = None, trace_context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    async for item in complaint_match_experience_router_stream(complaints, incident, match_strategy=match_strategy, trace_context=trace_context):
        if str(item.get("kind") or "").strip() == "stage":
            continue
        idx = int(item.get("index") or 0)
        if len(matches) <= idx:
            matches.extend([{}] * (idx + 1 - len(matches)))
        matches[idx] = {"complaint": item.get("complaint"), "matched": item.get("matched") or []}
    return matches


def _skill_matched_clauses_to_law_items(analysis: dict[str, Any]) -> list[dict[str, str]]:
    """将 skill 命中的条款转成统一 law/item/item_text 结构。"""
    law_items: list[dict[str, str]] = []
    sources = []
    if isinstance(analysis.get("user_cited_laws_analysis"), list):
        sources.extend(analysis.get("user_cited_laws_analysis") or [])
    if isinstance(analysis.get("possible_laws"), list):
        sources.extend(analysis.get("possible_laws") or [])
    for source in sources:
        if not isinstance(source, dict):
            continue
        clauses = source.get("matched_clauses") if isinstance(source.get("matched_clauses"), list) else []
        for clause in clauses:
            if not isinstance(clause, dict):
                continue
            law = _remove_law_line_reference(clause.get("law") or source.get("normalized_law") or "")
            article = _normalize_law_article(clause.get("article") or clause.get("item") or clause.get("items") or clause.get("条款") or clause.get("time") or "")
            text_summary = _remove_law_line_reference(clause.get("text_summary") or clause.get("source") or "")
            if law and article:
                law_items.append({"law": law, "item": article, "item_text": text_summary})
    return law_items


def _law_skill_category_from_match(cls: dict[str, Any]) -> dict[str, Any]:
    """将 law_skill 分析结果转成统一分类结构。"""
    analysis = cls.get("analysis") if isinstance(cls.get("analysis"), dict) else {}
    return {
        "一级分类": "本地法规分析",
        "二级分类": str(analysis.get("neutral_claim") or cls.get("overview") or "单事项法律分析").strip(),
        "匹配度": "待核实",
        "分类理由": str(analysis.get("preliminary_conclusion") or cls.get("分类理由") or "").strip(),
        "是否属于支付结算": "待核实",
        "调阅材料": _to_str_list(analysis.get("document_checklist")),
        "核查思路": _to_str_list(analysis.get("verification_points")),
        "法律法规及其原文": _skill_matched_clauses_to_law_items(analysis),
        "基础事实分析": analysis.get("basic_fact_analysis") if isinstance(analysis.get("basic_fact_analysis"), dict) else {},
        "法律存在性核验": analysis.get("legal_existence_checks") if isinstance(analysis.get("legal_existence_checks"), list) else [],
        "法律适用性分析": analysis.get("law_applicability_analysis") if isinstance(analysis.get("law_applicability_analysis"), list) else [],
        "合理性结论": str(analysis.get("reasonableness_conclusion") or "").strip(),
        "user_cited_laws_analysis": analysis.get("user_cited_laws_analysis") if isinstance(analysis.get("user_cited_laws_analysis"), list) else [],
        "possible_laws": analysis.get("possible_laws") if isinstance(analysis.get("possible_laws"), list) else [],
        "law_skill_analysis": analysis,
    }


async def complaint_law_analysis(matches: list[dict[str, Any]], trace_context: dict[str, Any] | None = None, match_strategy: str | None = None) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    resolved_strategy = normalize_match_strategy(match_strategy)
    trace_tag = build_trace_tag(trace_context, resolved_strategy)
    logger.info("complaint_law_analysis %s input_count=%s", trace_tag, len(matches or []))
    for item in matches or []:
        complaint = str(item.get("complaint") or "").strip()
        matched = item.get("matched") or []
        if not isinstance(matched, list):
            matched = []
        logger.info("complaint_law_analysis %s complaint=%s matched_count=%s matched=%s", trace_tag, complaint, len(matched), _log_preview_text(json.dumps(matched, ensure_ascii=False)))
        category_infos: list[dict[str, Any]] = []
        for cls in matched:
            if not isinstance(cls, dict):
                continue
            if str(cls.get("source") or "") == "law_skill":
                category_infos.append(_law_skill_category_from_match(cls))
                continue
            p = str(cls.get("first_category") or cls.get("一级分类") or "其他").strip()
            s = str(cls.get("overview") or cls.get("二级分类") or "其他").strip()
            m = str(cls.get("匹配度") or "0").strip()
            r = str(cls.get("分类理由") or "").strip()
            flag = str(cls.get("in_scope") or cls.get("是否属于支付结算") or "否").strip()
            review_list = cls.get("checklist") or cls.get("调阅清单") or []
            check_list = cls.get("verification_points") or cls.get("核查材料") or []
            law_basis_list = cls.get("law_list") or cls.get("法律依据列表") or []
            review_list = _to_str_list(review_list)
            check_list = _to_str_list(check_list)
            law_basis_list = _to_str_list(law_basis_list)
            laws_for_cat: list[dict[str, str]] = []
            for law_entry in law_basis_list:
                key = str(law_entry).strip()
                if not key:
                    continue
                items = get_law_item(key)
                for law_item in items:
                    law_name = str(law_item.get("law") or "")
                    item_name = str(law_item.get("item") or "")
                    item_text = str(law_item.get("item_text") or "")
                    if not law_name or not item_name or not item_text:
                        continue
                    law_obj = {"law": law_name, "item": item_name, "item_text": item_text}
                    laws_for_cat.append(law_obj)
            category_infos.append(
                {
                    "一级分类": p,
                    "二级分类": s,
                    "匹配度": m,
                    "分类理由": r,
                    "是否属于支付结算": flag,
                    "调阅材料": review_list,
                    "核查思路": check_list,
                    "法律法规及其原文": laws_for_cat,
                }
            )
        logger.info("complaint_law_analysis %s complaint=%s category_infos_count=%s category_infos=%s", trace_tag, complaint, len(category_infos), _log_preview_text(json.dumps(category_infos, ensure_ascii=False)))
        results.append({"complaint": complaint, "categories": category_infos})
    logger.info("complaint_law_analysis %s output_count=%s output=%s", trace_tag, len(results), _log_preview_text(json.dumps(results, ensure_ascii=False)))
    return results


def _normalize_text_list(value: Any) -> list[str]:
    """规范化字符串列表。"""
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item or "").strip()]


def _normalize_user_cited_laws(value: Any) -> list[dict[str, str]]:
    """规范化举报人提及的法律依据。"""
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


def _normalize_complaint_item(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        label = str(item.get("label") or item.get("事项") or item.get("name") or "").strip()
        description = str(item.get("description") or item.get("content") or item.get("事项描述") or item.get("desc") or "").strip()
        claim_text = str(item.get("claim_text") or "").strip()
        if not description and label:
            description = label
        if not label and description:
            label = description
        if not claim_text:
            claim_text = description or label
        return {
            "label": label,
            "description": description,
            "claim_text": claim_text,
            "original_quotes": _normalize_text_list(item.get("original_quotes")),
            "evidence_materials": _normalize_text_list(item.get("evidence_materials")),
            "user_cited_laws": _normalize_user_cited_laws(item.get("user_cited_laws")),
        }
    text = str(item or "").strip()
    if not text:
        return {}
    return {"label": text, "description": text, "claim_text": text, "original_quotes": [], "evidence_materials": [], "user_cited_laws": []}


def _strip_code_fence(raw: str) -> str:
    text = (raw or "").strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    while lines and lines[-1].strip() == "```":
        lines.pop()
    return "\n".join(lines).strip()


def _strip_think_content(raw: str) -> str:
    text = str(raw or "")
    # Remove complete <think>...</think> blocks first.
    while True:
        start_idx = text.find("<think>")
        if start_idx == -1:
            break
        end_idx = text.find("</think>", start_idx + len("<think>"))
        if end_idx == -1:
            # Keep tail content to avoid truncating valid answer JSON.
            text = text.replace("<think>", "")
            break
        text = text[:start_idx] + text[end_idx + len("</think>"):]
    if "</think>" in text:
        text = text.split("</think>")[-1]
    return text.replace("<think>", "").replace("</think>", "").strip()


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
                return text[start_idx: idx + 1]
    return text[start_idx:].strip()


def _extract_first_json_fragment(raw: str) -> str:
    text = str(raw or "")
    first_candidate = ""
    for idx, ch in enumerate(text):
        if ch not in "{[":
            continue
        candidate = _extract_json_fragment_at(text, idx)
        if not candidate:
            continue
        if not first_candidate:
            first_candidate = candidate
        try:
            json.loads(candidate)
            return candidate
        except Exception:
            continue
    return first_candidate


def _prepare_json_text(raw: str) -> str:
    text = _strip_code_fence(_strip_think_content(raw))
    if not text:
        return ""
    json_fragment = _extract_first_json_fragment(text)
    return json_fragment or text


def _complaint_item_text(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("description") or item.get("label") or "").strip()
    return str(item or "").strip()


def _parse_complaints(raw: str) -> list[dict[str, Any]]:
    text = _prepare_json_text(raw)
    try:
        data = json.loads(text)
        if isinstance(data, list):
            items: list[dict[str, Any]] = []
            for item in data:
                normalized = _normalize_complaint_item(item)
                if normalized.get("label") or normalized.get("description"):
                    items.append(normalized)
            return items
        if isinstance(data, dict):
            items = data.get("complaints") or data.get("items") or []
            if isinstance(items, list):
                normalized_items: list[dict[str, Any]] = []
                for item in items:
                    normalized = _normalize_complaint_item(item)
                    if normalized.get("label") or normalized.get("description"):
                        normalized_items.append(normalized)
                return normalized_items
    except Exception:
        pass
    lines = [line.strip(" -•\t") for line in text.splitlines()]
    normalized_items: list[dict[str, Any]] = []
    for line in lines:
        if not line:
            continue
        normalized = _normalize_complaint_item(line)
        if normalized:
            normalized_items.append(normalized)
    return normalized_items


def _empty_complaint_profile() -> dict[str, Any]:
    """返回空的举报画像结构。"""
    return {"reporter": "", "report_date": "", "reported_institution": "", "appeals": [], "title_base": ""}


def _normalize_profile_appeals(value: Any) -> list[str]:
    """规范化举报诉求列表。"""
    if not isinstance(value, list):
        return []
    appeals: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            appeals.append(text)
    return appeals


def _normalize_complaint_profile(value: Any) -> dict[str, Any]:
    """规范化举报画像字段。"""
    if not isinstance(value, dict):
        return _empty_complaint_profile()
    return {
        "reporter": str(value.get("reporter") or value.get("举报人") or "").strip(),
        "report_date": str(value.get("report_date") or value.get("举报日期") or "").strip(),
        "reported_institution": str(value.get("reported_institution") or value.get("被举报机构") or "").strip(),
        "appeals": _normalize_profile_appeals(value.get("appeals") or value.get("诉求")),
        "title_base": str(value.get("title_base") or value.get("标题") or "").strip(),
    }


def _parse_complaint_extract_payload(raw: str) -> dict[str, Any]:
    """解析举报事项抽取结果并返回完整结构。"""
    text = _prepare_json_text(raw)
    logger.info("parse_complaint_extract_payload prepared_preview=%s", _log_preview_text(text))
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            complaints = _parse_complaints(json.dumps(data.get("complaints") or data.get("items") or []))
            incident = str(data.get("incident") or data.get("事情经过") or "").strip()
            profile = _normalize_complaint_profile(data.get("profile") or data.get("complaint_profile") or data)
            logger.info("parse_complaint_extract_payload json_dict complaints_count=%s incident_len=%s reporter=%s institution=%s", len(complaints), len(incident), str(profile.get("reporter") or ""), str(profile.get("reported_institution") or ""))
            return {"complaints": complaints, "incident": incident, "profile": profile}
        if isinstance(data, list):
            complaints = _parse_complaints(text)
            logger.info("parse_complaint_extract_payload json_list complaints_count=%s", len(complaints))
            return {"complaints": complaints, "incident": "", "profile": _empty_complaint_profile()}
    except Exception:
        pass
    complaints = _parse_complaints(text)
    logger.info("parse_complaint_extract_payload fallback complaints_count=%s", len(complaints))
    return {"complaints": complaints, "incident": "", "profile": _empty_complaint_profile()}


def _parse_complaint_extract_result(raw: str) -> tuple[list[dict[str, Any]], str]:
    """兼容旧逻辑，仅返回举报事项与事情经过。"""
    payload = _parse_complaint_extract_payload(raw)
    complaints = payload.get("complaints") if isinstance(payload.get("complaints"), list) else []
    incident = str(payload.get("incident") or "").strip()
    return complaints, incident


def _build_classification_prompt() -> str:
    path = Path(__file__).with_name("classification_prompt.md")
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _parse_json_array(raw: str) -> list[Any]:
    text = _prepare_json_text(raw)
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            items = data.get("data") or data.get("items") or data.get("results") or []
            if isinstance(items, list):
                return items
    except Exception as exc:
        logger.info("[parse_json_array] json_error err=%s raw=%s", exc, _log_preview_text(text))
    return []


def _to_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        parts = [p.strip() for p in value.replace("；", ";").split(";")]
        return [p for p in parts if p]
    return []


def _load_experience_data(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _normalize_secondary_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    return []


def _match_experience_entries(classification: Any, experience_data: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if not isinstance(classification, list):
        return entries

    def add_entry(first_category: str, entry: dict[str, Any], item: dict[str, Any] | None = None) -> None:
        merged = dict(entry)
        if first_category and "first_category" not in merged:
            merged["first_category"] = first_category
        if isinstance(item, dict):
            score = str(item.get("匹配度") or "").strip()
            if score and not str(merged.get("匹配度") or "").strip():
                merged["匹配度"] = score
            reason = str(item.get("分类理由") or item.get("分类匹配逻辑说明") or "").strip()
            if reason and not str(merged.get("分类理由") or "").strip():
                merged["分类理由"] = reason
            primary = str(item.get("一级分类") or "").strip()
            secondary = str(item.get("二级分类") or "").strip()
            if primary and not str(merged.get("一级分类") or "").strip():
                merged["一级分类"] = primary
            if secondary and not str(merged.get("二级分类") or "").strip():
                merged["二级分类"] = secondary
        entries.append(merged)

    def find_in_category(first_category: str, second_value: str, item: dict[str, Any]) -> None:
        items = experience_data.get(first_category)
        if not isinstance(items, list):
            return
        for entry in items:
            if not isinstance(entry, dict):
                continue
            overview = str(entry.get("overview") or "").strip()
            if overview and overview == second_value:
                add_entry(first_category, entry, item)

    def find_any(second_value: str, item: dict[str, Any]) -> None:
        for first_category, items in experience_data.items():
            if not isinstance(items, list):
                continue
            for entry in items:
                if not isinstance(entry, dict):
                    continue
                overview = str(entry.get("overview") or "").strip()
                if overview and overview == second_value:
                    add_entry(str(first_category), entry, item)

    for item in classification:
        if not isinstance(item, dict):
            continue
        first_category = str(item.get("一级分类") or "").strip()
        second_values = _normalize_secondary_values(item.get("二级分类"))
        if not second_values:
            continue
        for second_value in second_values:
            if first_category:
                find_in_category(first_category, second_value, item)
            else:
                find_any(second_value, item)
            if not entries:
                fallback = find_expert_experience_for_second_category(second_value)
                if isinstance(fallback, dict):
                    add_entry(str(fallback.get("first_category") or first_category), fallback, item)

    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for entry in entries:
        key = f"{entry.get('first_category')}-{entry.get('overview')}-{entry.get('row_no')}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped


def get_experience(primary_category: str, secondary_category: str) -> dict[str, Any]:
    match_entry = find_expert_experience_for_second_category(secondary_category or "")
    if not match_entry:
        return {"是否属于支付结算": "否", "调阅清单": [], "核查材料": [], "法律依据列表": []}
    in_scope = match_entry.get("is_in_scope") or "是"
    review_list = _to_str_list(match_entry.get("checklist") or [])
    check_materials = _to_str_list(match_entry.get("verification_points") or [])
    law_list = _to_str_list(match_entry.get("law_list") or [])
    return {"是否属于支付结算": in_scope, "调阅清单": review_list, "核查材料": check_materials, "法律依据列表": law_list}


def get_law_item(key: str) -> List[Dict[str, str]]:
    import urllib.parse
    import urllib.request

    base_base = os.getenv("POLICY_SERVICE_BASE_URL") or "http://39.97.229.91:8082"
    base_url = f"{base_base.rstrip('/')}/api/clause"
    query = urllib.parse.urlencode({"key": key})
    url = f"{base_url}?{query}"

    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        data = json.loads(raw)
    except Exception as e:
        # logger.error(f"get_law_item error: {e}")
        logger.info("get_law_item error: %s", e)
        return []

    matches = data.get("matches") or []
    # print(f"matches: {matches}")
    results: List[Dict[str, str]] = []
    for m in matches:
        query_info = m.get("query") or {}
        # print(f"query_info: {query_info}")
        law_name = str(query_info.get("title") or "")
        item_name = str(query_info.get("clause") or "")

        result_info = m.get("result") or {}
        item_text = str(result_info.get("article_text") or "")

        if not law_name or not item_name or not item_text:
            continue
        results.append(
            {
                "law": law_name,
                "item": item_name,
                "item_text": item_text,
            }
        )

    return results


def _build_law_refine_prompt(
    complaint_desc: str,
    category_infos: List[Dict[str, Any]],
    law_text_map: Dict[str, str],
) -> str:
    law_text_parts: List[str] = []
    for key, text in law_text_map.items():
        law_text_parts.append(f"- {key}：{text}")
    law_text_block = "\n".join(law_text_parts) if law_text_parts else "（暂无可用条文原文）"

    category_json = json.dumps(category_infos, ensure_ascii=False, indent=2)

    return f"""
你是一名金融法规与银行业务合规专家，将根据举报事项、经验库匹配结果以及相关法律条文原文，进行综合分析。"。

【举报事项描述】
{complaint_desc}

【初步分类结果】(JSON)
{category_json}

【可用法律条文原文】
{law_text_block}

【任务】
1. 在每个分类结果中，核实“调阅材料”“核查思路”是否合理，如有遗漏或不当请进行调整。
2. 根据条文原文，核查“法律法规及其原文”是否匹配举报事实，可适当增删或修正。
3. 保持“一级分类”“二级分类”“匹配度”“分类理由”“是否属于支付结算”字段不变或做轻微优化，不要引入新的字段。

【输出格式要求】
请直接返回 JSON 数组，每个元素对应一个精炼后的分类结果对象，字段包括：
- "一级分类": string
- "二级分类": string
- "匹配度": string
- "分类理由": string
- "是否属于支付结算": string
- "调阅材料": list[string]
- "核查思路": list[string]
- "法律法规及其原文": list[{{"law": string, "item": string, "item_text": string}}]

请直接返回 JSON 数组，不要附加额外说明。
    """.strip()
