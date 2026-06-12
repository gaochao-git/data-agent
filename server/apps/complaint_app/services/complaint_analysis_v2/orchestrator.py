from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from typing import Any, AsyncGenerator

from fastapi import HTTPException

from apps.complaint_app.core.llm.llm_client import AsyncLLMClient
from apps.complaint_app.core.model_config import get_summary_model
from apps.complaint_app.core.versioning import load_manifest, next_version, save_manifest, stage_dir, update_manifest, write_meta
from apps.complaint_app.services.analysis_agent.log_utils import log_preview_json, log_preview_text
from apps.complaint_app.services.complaint_api_service import (
    build_complaint_analysis_v2_final_markdown,
    build_complaint_analysis_v2_reference_content,
    ensure_user_id,
    extract_texts_and_save,
    load_latest_text,
    normalize_reported_entities,
    normalize_reply_reporter,
    resolve_pdf_file,
    save_uploaded_pdf,
    write_json_file,
    write_text_file,
)
from apps.complaint_app.opencode_runtime.session import Session
from apps.complaint_app.opencode_runtime.session_prompt import SessionPrompt
from apps.complaint_app.opencode_runtime.trace_logger import trace_detail_log_path, trace_enabled, trace_index_logger
from apps.complaint_app.opencode_runtime.tools.base_tool import ToolExecutor
from apps.complaint_app.services.complaint_analysis_v2.prompt_loader import (
    build_analyzer_messages,
    build_scenario_locator_messages,
    build_stage_input_hash,
    build_summary_messages,
    prompt_files_for_stage,
    prompt_versions_for_stage,
)
from apps.complaint_app.services.complaint_analysis_v2.sse import format_named_sse_with_history
from apps.complaint_app.services.complaint_analysis_v2.tool_runtime import search_law_tool
from utils.llm_thinking import build_thinking_extra_body
from utils.llm_prompt_metrics import llm_prompt_metrics_context
from utils.logger import get_logger
from utils.storage_paths import ComplaintScopeContext, resolve_complaint_scope
from utils.timing import log_phase_timing, log_route_timing, timing_elapsed_ms, timing_now

logger = get_logger(__name__)

ROUTE_NAME = "POST /api/complants/complaint-analysis-v2"
SUMMARY_STAGE = "summary_v2"
ANALYSIS_STAGE = "analysis_v2"
FINAL_STAGE = "complaint_analysis_v2"
DEFAULT_SUMMARY_MODEL = get_summary_model()
DEFAULT_ANALYSIS_MODEL = get_summary_model()
DEFAULT_FINAL_MODEL = DEFAULT_ANALYSIS_MODEL


def _timing_extra(user_id: str, base_id: str, file_id: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {"user_id": str(user_id or "").strip(), "base_id": str(base_id or "").strip(), "file_id": str(file_id or "").strip()}
    if isinstance(extra, dict):
        payload.update({k: v for k, v in extra.items() if v is not None})
    return payload


def _build_call_id(stage_name: str, base_id: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{stage_name}_{base_id}_{timestamp}_{uuid.uuid4().hex[:8]}"


def _final_llm_enabled() -> bool:
    """兼容历史配置：final 阶段已改为基于 analysis 结果直接组装。"""
    return False


async def _load_json(path: str) -> dict[str, Any]:
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


async def _load_text(path: str) -> str:
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except Exception:
        return ""


async def _load_stage_result(user_id: str, base_id: str, stage: str, version: int, scope_context: ComplaintScopeContext | None = None) -> tuple[dict[str, Any], str]:
    final_json = await _load_json(os.path.join(stage_dir(user_id, base_id, stage, version, scope_context=scope_context), "final.json"))
    final_md = await _load_text(os.path.join(stage_dir(user_id, base_id, stage, version, scope_context=scope_context), "final.md"))
    return final_json, final_md


async def _load_latest_stage(user_id: str, base_id: str, stage: str, scope_context: ComplaintScopeContext | None = None) -> tuple[dict[str, Any], str, int]:
    manifest = load_manifest(user_id, base_id, scope_context=scope_context)
    version = int(manifest.get(stage, {}).get("current") or 0)
    if version <= 0:
        return {}, "", 0
    payload, text = await _load_stage_result(user_id, base_id, stage, version, scope_context=scope_context)
    if not payload and not text:
        return {}, "", 0
    return payload, text, version


async def _save_stage_result(
    user_id: str,
    base_id: str,
    *,
    stage: str,
    markdown: str,
    result_payload: dict[str, Any],
    base_versions: dict[str, Any],
    meta_extra: dict[str, Any],
    scope_context: ComplaintScopeContext | None = None,
) -> tuple[dict[str, Any], int]:
    manifest = load_manifest(user_id, base_id, scope_context=scope_context)
    version = next_version(manifest, stage)
    version_dir = stage_dir(user_id, base_id, stage, version, scope_context=scope_context)
    await write_text_file(os.path.join(version_dir, "final.md"), markdown)
    await write_json_file(os.path.join(version_dir, "final.json"), result_payload)
    write_meta(user_id, base_id, stage, version, source="auto", base_versions=base_versions, extra=meta_extra, scope_context=scope_context, updated_by=user_id)
    manifest = update_manifest(manifest, stage, version)
    save_manifest(user_id, base_id, manifest, scope_context=scope_context)
    return result_payload, version


async def _stream_stage_model(
    *,
    stage_name: str,
    model: str,
    messages: list[dict[str, str]],
    base_id: str,
    user_id: str,
    file_id: str,
) -> AsyncGenerator[str, None]:
    llm_call_id = _build_call_id(stage_name, base_id)
    logger.info(
        "[complaint_api] llm_request route=%s stage=%s call_id=%s user_id=%s base_id=%s file_id=%s model=%s message_count=%s system_chars=%s user_chars=%s",
        ROUTE_NAME,
        stage_name,
        llm_call_id,
        user_id,
        base_id,
        file_id,
        model,
        len(messages),
        len(str(messages[0].get("content") or "")) if messages else 0,
        len(str(messages[-1].get("content") or "")) if messages else 0,
    )
    client = AsyncLLMClient(name=f"complaint_analysis_v2_{stage_name}", metric_stage=f"complaint_analysis_v2_{stage_name}", metric_context={"business_stage": "complaint_analysis_v2", "base_id": base_id, "user_id": user_id, "file_id": file_id, "stage": stage_name, "call_id": llm_call_id})
    extra_body = build_thinking_extra_body(model, False)
    chunks: list[str] = []
    async for chunk in client.stream_chat(messages=messages, model=model, temperature=0.2, extra_body=extra_body):
        choices = getattr(chunk, "choices", None) or []
        if not choices:
            continue
        delta = getattr(choices[0], "delta", None)
        if not delta:
            continue
        content = getattr(delta, "content", "") or ""
        if isinstance(content, list):
            text = "".join(str(item.get("text") or item.get("value") or "") if isinstance(item, dict) else str(item or "") for item in content)
        else:
            text = str(content or "")
        if not text:
            continue
        chunks.append(text)
        yield text
    final_text = "".join(chunks).strip()
    logger.info(
        "[complaint_api] llm_response route=%s stage=%s call_id=%s user_id=%s base_id=%s file_id=%s model=%s output_chars=%s",
        ROUTE_NAME,
        stage_name,
        llm_call_id,
        user_id,
        base_id,
        file_id,
        model,
        len(final_text),
    )
    if not final_text:
        raise HTTPException(status_code=500, detail=f"{stage_name} 阶段模型返回为空")


async def _call_stage_model(
    *,
    stage_name: str,
    model: str,
    messages: list[dict[str, str]],
    base_id: str,
    user_id: str,
    file_id: str,
    call_id: str | None = None,
) -> str:
    llm_call_id = str(call_id or _build_call_id(stage_name, base_id))
    logger.info(
        "[complaint_api] llm_request route=%s stage=%s call_id=%s user_id=%s base_id=%s file_id=%s model=%s message_count=%s system_chars=%s user_chars=%s",
        ROUTE_NAME,
        stage_name,
        llm_call_id,
        user_id,
        base_id,
        file_id,
        model,
        len(messages),
        len(str(messages[0].get("content") or "")) if messages else 0,
        len(str(messages[-1].get("content") or "")) if messages else 0,
    )
    client = AsyncLLMClient(name=f"complaint_analysis_v2_{stage_name}", metric_stage=f"complaint_analysis_v2_{stage_name}", metric_context={"business_stage": "complaint_analysis_v2", "base_id": base_id, "user_id": user_id, "file_id": file_id, "stage": stage_name, "call_id": llm_call_id})
    extra_body = build_thinking_extra_body(model, False)
    response = await client.chat(messages=messages, model=model, temperature=0.2, extra_body=extra_body)
    choices = getattr(response, "choices", None) or []
    if not choices:
        raise HTTPException(status_code=500, detail=f"{stage_name} 阶段模型无返回")
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", "") if message else ""
    if isinstance(content, list):
        text = "".join(str(item.get("text") or item.get("value") or "") if isinstance(item, dict) else str(item or "") for item in content)
    else:
        text = str(content or "")
    text = text.strip()
    logger.info(
        "[complaint_api] llm_response route=%s stage=%s call_id=%s user_id=%s base_id=%s file_id=%s model=%s output_chars=%s",
        ROUTE_NAME,
        stage_name,
        llm_call_id,
        user_id,
        base_id,
        file_id,
        model,
        len(text),
    )
    if not text:
        raise HTTPException(status_code=500, detail=f"{stage_name} 阶段模型返回为空")
    return text


async def _resolve_original_files(payload: Any, *, user_id: str, base_id: str, uploaded_filename: str | None, uploaded_content: bytes | None) -> list[tuple[str, str]]:
    default_scope_context = resolve_complaint_scope("default", None, None)
    if payload.use_uploaded_pdf:
        original_path = await save_uploaded_pdf(user_id, base_id, uploaded_filename or f"{base_id}.pdf", uploaded_content or b"", scope_context=default_scope_context)
        return [(str(payload.file_id or uploaded_filename or base_id), original_path)]
    file_ids = [str(item).strip() for item in (payload.file_ids or []) if str(item).strip()]
    if not file_ids and str(payload.file_id or "").strip():
        file_ids = [str(payload.file_id or "").strip()]
    result: list[tuple[str, str]] = []
    for file_id in file_ids:
        result.append((file_id, await resolve_pdf_file(file_id, user_id, base_id, force=False, scope_context=default_scope_context)))
    return result


async def _ensure_text_content(payload: Any, *, user_id: str, base_id: str, file_id: str, uploaded_filename: str | None, uploaded_content: bytes | None) -> tuple[str, int, bool]:
    default_scope_context = resolve_complaint_scope("default", None, None)
    cached_text, cached_version = load_latest_text(user_id, base_id, scope_context=default_scope_context)
    if cached_text.strip():
        logger.info("[complaint_api] reuse_text route=%s user_id=%s base_id=%s file_id=%s text_version=%s cache_hit=true skip_ocr=true", ROUTE_NAME, user_id, base_id, file_id, cached_version)
        return cached_text, cached_version, True
    logger.info("[complaint_api] reuse_text_miss route=%s user_id=%s base_id=%s file_id=%s cache_hit=false skip_ocr=false", ROUTE_NAME, user_id, base_id, file_id)
    original_files = await _resolve_original_files(payload, user_id=user_id, base_id=base_id, uploaded_filename=uploaded_filename, uploaded_content=uploaded_content)
    text_content, text_version = await extract_texts_and_save(user_id, base_id, original_files, force=False, scope_context=default_scope_context)
    logger.info("[complaint_api] extract_text_done route=%s user_id=%s base_id=%s file_id=%s text_version=%s cache_hit=false skip_ocr=false", ROUTE_NAME, user_id, base_id, file_id, text_version)
    return text_content, text_version, False


async def _maybe_load_cached_stage(
    *,
    user_id: str,
    base_id: str,
    stage: str,
    input_hash: str,
    model: str,
    scope_context: ComplaintScopeContext | None = None,
) -> tuple[dict[str, Any] | None, str, int]:
    payload, markdown, version = await _load_latest_stage(user_id, base_id, stage, scope_context=scope_context)
    if version <= 0 or not isinstance(payload, dict):
        return None, "", 0
    meta = await _load_json(os.path.join(stage_dir(user_id, base_id, stage, version, scope_context=scope_context), "meta.json"))
    if str(meta.get("input_hash") or "") != str(input_hash or ""):
        return None, "", 0
    if str(meta.get("model") or "") != str(model or ""):
        return None, "", 0
    return payload, markdown, version


async def _run_summary_stage(
    *,
    user_id: str,
    base_id: str,
    file_id: str,
    complaint_text: str,
    text_version: int,
    reporter_name: str,
    reported_entities: list[str] | None,
    force: bool,
    streamed_markdown: str | None = None,
    scope_context: ComplaintScopeContext | None = None,
) -> tuple[dict[str, Any], str, int, bool]:
    model = DEFAULT_SUMMARY_MODEL
    input_hash = build_stage_input_hash(
        SUMMARY_STAGE,
        {
            "text_version": int(text_version or 0),
            "reporter_name": normalize_reply_reporter(reporter_name),
            "reported_entities": normalize_reported_entities(reported_entities),
            "prompt_versions": prompt_versions_for_stage(SUMMARY_STAGE),
            "model": model,
        },
    )
    markdown = str(streamed_markdown or "").strip()
    if not markdown and not force:
        cached_payload, cached_markdown, cached_version = await _maybe_load_cached_stage(user_id=user_id, base_id=base_id, stage=SUMMARY_STAGE, input_hash=input_hash, model=model, scope_context=scope_context)
        if cached_payload is not None and cached_markdown.strip():
            logger.info("[complaint_api] reuse_summary route=%s user_id=%s base_id=%s file_id=%s summary_version=%s cache_hit=true", ROUTE_NAME, user_id, base_id, file_id, cached_version)
            return cached_payload, cached_markdown, cached_version, True
    if not markdown:
        messages = build_summary_messages(complaint_text=complaint_text, reporter_name=reporter_name, reported_entities=reported_entities)
        markdown = await _call_stage_model(stage_name="summary", model=model, messages=messages, base_id=base_id, user_id=user_id, file_id=file_id)
    payload = {
        "api_kind": "complaint_analysis_v2_summary",
        "base_id": base_id,
        "summary_markdown": markdown,
        "reporter_name": str(reporter_name or "").strip(),
        "reported_entities": normalize_reported_entities(reported_entities),
        "prompt_files": prompt_files_for_stage(SUMMARY_STAGE),
    }
    payload, version = await _save_stage_result(
        user_id,
        base_id,
        stage=SUMMARY_STAGE,
        markdown=markdown,
        result_payload=payload,
        base_versions={"base_text_version": text_version},
        meta_extra={"task": "summary_v2", "input_hash": input_hash, "model": model, "prompt_versions": prompt_versions_for_stage(SUMMARY_STAGE)},
        scope_context=scope_context,
    )
    return payload, markdown, version, False


async def _run_scenario_locator_skill(
    *,
    user_id: str,
    base_id: str,
    file_id: str,
    complaint_text: str,
    summary_markdown: str,
    reporter_name: str,
    reported_entities: list[str] | None,
    trace_context: dict[str, Any],
) -> list[dict[str, Any]]:
    """Phase A: 场景定位技能——按场景分组 emit search_law，返回所有检索结果。"""
    messages = build_scenario_locator_messages(
        complaint_text=complaint_text,
        summary_markdown=summary_markdown,
        reporter_name=reporter_name,
        reported_entities=reported_entities,
    )
    call_id = str(trace_context.get("analysis_id") or "")
    session = Session(session_id=f"scenario_locator_{base_id}", metadata=trace_context)
    executor = ToolExecutor()
    executor.register(search_law_tool())
    prompt = SessionPrompt(
        llm_client=AsyncLLMClient(
            name="complaint_scenario_locator",
            metric_stage="complaint_scenario_locator",
            metric_context={
                "business_stage": "complaint_analysis_v2",
                "base_id": base_id,
                "user_id": user_id,
                "file_id": file_id,
                "stage": "scenario_locator",
            },
        ),
        tool_executor=executor,
    )
    logger.info(
        "[complaint_api] llm_request route=%s stage=%s call_id=%s user_id=%s base_id=%s file_id=%s model=%s enabled_tools=%s system_chars=%s user_chars=%s",
        ROUTE_NAME, "scenario_locator", call_id, user_id, base_id, file_id, DEFAULT_ANALYSIS_MODEL, "search_law",
        len(str(messages[0].get("content") or "")) if messages else 0,
        len(str(messages[-1].get("content") or "")) if messages else 0,
    )
    law_results: list[dict[str, Any]] = []
    message_end: dict[str, Any] | None = None
    async for event in prompt.stream(
        session=session,
        user_input=messages[-1]["content"],
        system_prompt=messages[0]["content"],
        model=DEFAULT_ANALYSIS_MODEL,
        allowed_tools=["search_law"],
        tool_choice="auto",
        record_messages=False,
        trace_context=trace_context,
    ):
        if str(event.get("event") or "") == "message_end":
            message_end = event
    for tool_result in (message_end or {}).get("tool_results") or []:
        if not isinstance(tool_result, dict):
            continue
        if str(tool_result.get("name") or "") != "search_law":
            continue
        output = tool_result.get("output")
        if isinstance(output, dict):
            law_results.append(output)
    logger.info(
        "[complaint_api] llm_response route=%s stage=%s call_id=%s user_id=%s base_id=%s file_id=%s scenario_count=%s",
        ROUTE_NAME, "scenario_locator", call_id, user_id, base_id, file_id, len(law_results),
    )
    return law_results


async def _stream_analysis_stage(
    *,
    user_id: str,
    base_id: str,
    file_id: str,
    complaint_text: str,
    text_version: int,
    summary_version: int,
    summary_markdown: str,
    reporter_name: str,
    reported_entities: list[str] | None,
    force: bool,
    scope_context: ComplaintScopeContext | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    model = DEFAULT_ANALYSIS_MODEL
    input_hash = build_stage_input_hash(
        ANALYSIS_STAGE,
        {
            "text_version": int(text_version or 0),
            "summary_version": int(summary_version or 0),
            "summary_markdown_hash": summary_markdown,
            "prompt_versions": prompt_versions_for_stage(ANALYSIS_STAGE),
            "model": model,
            "enabled_tools": ["search_law"],
        },
    )
    if not force:
        cached_payload, cached_markdown, cached_version = await _maybe_load_cached_stage(user_id=user_id, base_id=base_id, stage=ANALYSIS_STAGE, input_hash=input_hash, model=model, scope_context=scope_context)
        if cached_payload is not None and cached_markdown.strip():
            logger.info("[complaint_api] reuse_analysis route=%s user_id=%s base_id=%s file_id=%s analysis_version=%s cache_hit=true enabled_tools=search_law", ROUTE_NAME, user_id, base_id, file_id, cached_version)
            yield {"kind": "cached", "payload": cached_payload, "markdown": cached_markdown, "version": cached_version}
            return

    analysis_call_id = _build_call_id("analysis", base_id)
    analysis_trace_context = {"request_id": base_id, "analysis_id": analysis_call_id, "base_id": base_id, "user_id": user_id, "file_id": file_id, "stage": "analysis_v2"}
    if trace_enabled():
        trace_index_logger().info("[complaint_api] analysis_trace_index route=%s user_id=%s base_id=%s file_id=%s analysis_id=%s trace_file=%s", ROUTE_NAME, user_id, base_id, file_id, analysis_call_id, trace_detail_log_path(analysis_trace_context))

    yield {"kind": "phase_status", "phase": "scenario_locator", "status": "start"}
    law_results = await _run_scenario_locator_skill(
        user_id=user_id,
        base_id=base_id,
        file_id=file_id,
        complaint_text=complaint_text,
        summary_markdown=summary_markdown,
        reporter_name=reporter_name,
        reported_entities=reported_entities,
        trace_context=analysis_trace_context,
    )
    if not law_results:
        logger.error("[complaint_api] analysis_v2_search_law_missing route=%s call_id=%s user_id=%s base_id=%s file_id=%s", ROUTE_NAME, analysis_call_id, user_id, base_id, file_id)
        raise HTTPException(status_code=500, detail="场景定位阶段未调用 search_law")
    yield {"kind": "phase_status", "phase": "scenario_locator", "status": "done", "scenario_count": len(law_results)}

    law_results_json = json.dumps(law_results, ensure_ascii=False, indent=2)
    analyzer_messages = build_analyzer_messages(
        complaint_text=complaint_text,
        summary_markdown=summary_markdown,
        law_results_json=law_results_json,
        reporter_name=reporter_name,
        reported_entities=reported_entities,
    )
    analyzer_session = Session(session_id=f"issue_analyzer_{base_id}", metadata=analysis_trace_context)
    analyzer_prompt = SessionPrompt(
        llm_client=AsyncLLMClient(
            name="complaint_issue_analyzer",
            metric_stage="complaint_issue_analyzer",
            metric_context={
                "business_stage": "complaint_analysis_v2",
                "base_id": base_id,
                "user_id": user_id,
                "file_id": file_id,
                "stage": "issue_analyzer",
            },
        ),
    )
    logger.info("[complaint_api] llm_request route=%s stage=%s call_id=%s user_id=%s base_id=%s file_id=%s model=%s system_chars=%s user_chars=%s", ROUTE_NAME, "issue_analyzer", analysis_call_id, user_id, base_id, file_id, model, len(str(analyzer_messages[0].get("content") or "")) if analyzer_messages else 0, len(str(analyzer_messages[-1].get("content") or "")) if analyzer_messages else 0)
    chunks: list[str] = []
    async for event in analyzer_prompt.stream(
        session=analyzer_session,
        user_input=analyzer_messages[-1]["content"],
        system_prompt=analyzer_messages[0]["content"],
        model=model,
        allowed_tools=[],
        record_messages=True,
        trace_context=analysis_trace_context,
    ):
        if str(event.get("event") or "") == "content_delta":
            delta = str(event.get("delta") or "")
            if delta:
                chunks.append(delta)
                yield {"kind": "delta", "delta": delta}
            continue
    markdown = "".join(chunks).strip()
    logger.info("[complaint_api] llm_response route=%s stage=%s call_id=%s user_id=%s base_id=%s file_id=%s model=%s output_chars=%s", ROUTE_NAME, "issue_analyzer", analysis_call_id, user_id, base_id, file_id, model, len(markdown))
    if not markdown:
        raise HTTPException(status_code=500, detail="analysis 阶段模型返回为空")

    all_articles: list[dict[str, Any]] = []
    law_summaries: list[str] = []
    law_audits: list[dict[str, Any]] = []
    for law_result in law_results:
        if not isinstance(law_result, dict):
            continue
        articles = law_result.get("articles")
        if isinstance(articles, list):
            all_articles.extend([item for item in articles if isinstance(item, dict)])
        summary = str(law_result.get("summary") or "").strip()
        if summary:
            law_summaries.append(summary)
        audit = law_result.get("audit")
        if isinstance(audit, dict):
            law_audits.append(audit)
    reference_content = build_complaint_analysis_v2_reference_content({"law_search": {"articles": all_articles}})
    law_summary_text = "\n---\n".join(law_summaries)

    payload = {
        "api_kind": "complaint_analysis_v2_issue_analysis",
        "base_id": base_id,
        "analysis_markdown": markdown,
        "law_search_results": law_results,
        "reference_content": reference_content,
        "prompt_files": prompt_files_for_stage(ANALYSIS_STAGE),
    }
    payload, version = await _save_stage_result(
        user_id,
        base_id,
        stage=ANALYSIS_STAGE,
        markdown=markdown,
        result_payload=payload,
        base_versions={"base_text_version": text_version, "summary_v2_version": summary_version},
        meta_extra={"task": "analysis_v2", "input_hash": input_hash, "model": model, "prompt_versions": prompt_versions_for_stage(ANALYSIS_STAGE), "enabled_tools": ["search_law"], "law_search_summary": law_summary_text, "law_search_audit": law_audits, "scenario_count": len(law_results)},
        scope_context=scope_context,
    )
    yield {"kind": "completed", "payload": payload, "markdown": markdown, "version": version}


async def _save_final_stage_result(
    *,
    user_id: str,
    base_id: str,
    text_version: int,
    summary_version: int,
    analysis_version: int,
    summary_markdown: str,
    analysis_markdown: str,
    reference_content: list[dict[str, Any]],
    final_markdown: str,
    scope_context: ComplaintScopeContext,
    model: str,
) -> tuple[dict[str, Any], int, bool]:
    input_hash = build_stage_input_hash(
        FINAL_STAGE,
        {
            "summary_version": int(summary_version or 0),
            "analysis_version": int(analysis_version or 0),
            "analysis_markdown_hash": analysis_markdown,
            "prompt_versions": prompt_versions_for_stage(FINAL_STAGE),
            "model": model,
            "scope_context": scope_context.to_meta(),
        },
    )
    result = {
        "api_kind": "complaint_analysis_v2",
        "base_id": base_id,
        "summary_markdown": summary_markdown,
        "analysis_markdown": analysis_markdown,
        "reference_content": reference_content,
        "final_markdown": final_markdown,
        "scope_context": scope_context.to_meta(),
        "versions": {
            "text_version": int(text_version or 0),
            "summary_v2_version": int(summary_version or 0),
            "analysis_v2_version": int(analysis_version or 0),
        },
        "prompt_files": prompt_files_for_stage(FINAL_STAGE),
    }
    result, version = await _save_stage_result(
        user_id,
        base_id,
        stage=FINAL_STAGE,
        markdown=final_markdown,
        result_payload=result,
        base_versions={"base_text_version": text_version, "summary_v2_version": summary_version, "analysis_v2_version": analysis_version},
        meta_extra={"task": "complaint_analysis_v2", "input_hash": input_hash, "model": model, "prompt_versions": prompt_versions_for_stage(FINAL_STAGE)},
        scope_context=scope_context,
    )
    return result, version, False


async def stream_mock_complaint_analysis_v2(payload: Any, *, base_id: str, user_id: str, message: str = "仅支持 PDF/PNG/JPG/JPEG 文件") -> AsyncGenerator[str, None]:
    route_started_at = timing_now()
    route_success = False
    final_message = str(message or "").strip() or "仅支持 PDF/PNG/JPG/JPEG 文件"
    try:
        yield format_named_sse_with_history("Start", {"base_id": base_id, "user_id": user_id, "route": ROUTE_NAME}, user_id=user_id, base_id=base_id)
        yield format_named_sse_with_history("Answer", {"phase": "validation", "message": final_message, "unsupported_file_type": True}, user_id=user_id, base_id=base_id)
        route_success = True
        yield format_named_sse_with_history("Done", {"base_id": base_id, "result": {"api_kind": "complaint_analysis_v2", "base_id": base_id, "summary_markdown": "", "analysis_markdown": "", "final_markdown": "", "versions": {"text_version": 0, "summary_v2_version": 0, "analysis_v2_version": 0, "complaint_analysis_v2_version": 0}}, "unsupported_file_type": True, "status": "completed"}, user_id=user_id, base_id=base_id)
    finally:
        log_route_timing(logger, "complaint_app", ROUTE_NAME, timing_elapsed_ms(route_started_at), route_success, _timing_extra(user_id, base_id, str(getattr(payload, 'file_id', '') or ''), {"unsupported_file_type": True}))


async def stream_complaint_analysis_v2(
    payload: Any,
    *,
    user_id: str,
    base_id: str,
    scope_context: ComplaintScopeContext,
    uploaded_filename: str | None = None,
    uploaded_content: bytes | None = None,
) -> AsyncGenerator[str, None]:
    route_started_at = timing_now()
    route_success = False
    active_phase = ""
    phase_started_at = 0.0
    resolved_user_id = ensure_user_id(user_id)
    file_ids = [str(payload.file_id or "")] if getattr(payload, "use_uploaded_pdf", False) else [str(item).strip() for item in (payload.file_ids or []) if str(item).strip()] or ([str(payload.file_id or "").strip()] if str(payload.file_id or "").strip() else [])
    file_id = ",".join(file_ids)
    reporter_name = str(getattr(payload, "reporter_name", "") or "").strip()
    reported_entities = normalize_reported_entities(getattr(payload, "reported_entities", None))
    logger.info("[complaint_api] request_input route=%s user_id=%s base_id=%s scope=%s org_id=%s entity_id=%s payload=%s upload_filename=%s has_uploaded_content=%s", ROUTE_NAME, resolved_user_id, base_id, scope_context.scope, scope_context.org_id, scope_context.entity_id, log_preview_json(payload.model_dump() if hasattr(payload, 'model_dump') else payload.dict() if hasattr(payload, 'dict') else {}), str(uploaded_filename or ""), bool(uploaded_content))
    try:
        yield format_named_sse_with_history("Start", {"base_id": base_id, "user_id": resolved_user_id, "scope_context": scope_context.to_meta(), "file_ids": file_ids, "route": ROUTE_NAME, "content": "正在思考..."}, user_id=resolved_user_id, base_id=base_id)

        active_phase = "extract_text"
        phase_started_at = timing_now()
        complaint_text, text_version, text_cache_hit = await _ensure_text_content(payload, user_id=resolved_user_id, base_id=base_id, file_id=file_id, uploaded_filename=uploaded_filename, uploaded_content=uploaded_content)
        log_phase_timing(logger, "complaint_app", ROUTE_NAME, active_phase, timing_elapsed_ms(phase_started_at), True, _timing_extra(resolved_user_id, base_id, file_id, {"text_version": text_version, "cache_hit": text_cache_hit}))

        active_phase = "summary_v2"
        phase_started_at = timing_now()
        summary_messages = build_summary_messages(complaint_text=complaint_text, reporter_name=reporter_name, reported_entities=reported_entities)
        summary_chunks: list[str] = []
        async for chunk in _stream_stage_model(stage_name="summary", model=DEFAULT_SUMMARY_MODEL, messages=summary_messages, base_id=base_id, user_id=resolved_user_id, file_id=file_id):
            summary_chunks.append(chunk)
            yield format_named_sse_with_history("thinking", {"phase": "举报摘要总结", "content": chunk}, user_id=resolved_user_id, base_id=base_id)
        summary_markdown = "".join(summary_chunks).strip()
        with llm_prompt_metrics_context(request_id=base_id, user_id=resolved_user_id, base_id=base_id, file_id=file_id, business_stage="complaint_analysis_v2_summary"):
            summary_payload, _summary_markdown_unused, summary_version, summary_cache_hit = await _run_summary_stage(user_id=resolved_user_id, base_id=base_id, file_id=file_id, complaint_text=complaint_text, text_version=text_version, reporter_name=reporter_name, reported_entities=reported_entities, force=bool(getattr(payload, "force", False)), streamed_markdown=summary_markdown, scope_context=scope_context)
        summary_markdown = summary_markdown or str(summary_payload.get("summary_markdown") or "").strip()
        log_phase_timing(logger, "complaint_app", ROUTE_NAME, active_phase, timing_elapsed_ms(phase_started_at), True, _timing_extra(resolved_user_id, base_id, file_id, {"summary_v2_version": summary_version, "cache_hit": summary_cache_hit}))
        yield format_named_sse_with_history("thinking", {"phase": "举报摘要总结", "content": "", "status": "done"}, user_id=resolved_user_id, base_id=base_id)

        active_phase = "analysis_v2"
        phase_started_at = timing_now()
        yield format_named_sse_with_history("thinking", {"phase": "举报事项分析", "content": "", "status": "start"}, user_id=resolved_user_id, base_id=base_id)
        with llm_prompt_metrics_context(request_id=base_id, user_id=resolved_user_id, base_id=base_id, file_id=file_id, business_stage="complaint_analysis_v2_issue_analysis"):
            analysis_payload = None
            analysis_markdown = ""
            analysis_version = 0
            analysis_cache_hit = False
            async for item in _stream_analysis_stage(user_id=resolved_user_id, base_id=base_id, file_id=file_id, complaint_text=complaint_text, text_version=text_version, summary_version=summary_version, summary_markdown=summary_markdown, reporter_name=reporter_name, reported_entities=reported_entities, force=bool(getattr(payload, "force", False)), scope_context=scope_context):
                if item.get("kind") == "phase_status":
                    phase_key = str(item.get("phase") or "")
                    status_value = str(item.get("status") or "")
                    sse_phase_name = "场景识别与法规定位" if phase_key == "scenario_locator" else phase_key
                    status_payload: dict[str, Any] = {"phase": sse_phase_name, "content": "", "status": status_value}
                    scenario_count = item.get("scenario_count")
                    if scenario_count is not None:
                        status_payload["scenario_count"] = scenario_count
                    yield format_named_sse_with_history("thinking", status_payload, user_id=resolved_user_id, base_id=base_id)
                    continue
                if item.get("kind") == "delta":
                    delta = str(item.get("delta") or "")
                    if delta:
                        yield format_named_sse_with_history("thinking", {"phase": "举报事项分析", "content": delta}, user_id=resolved_user_id, base_id=base_id)
                    continue
                if item.get("kind") == "cached":
                    analysis_payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
                    analysis_markdown = str(item.get("markdown") or "")
                    analysis_version = int(item.get("version") or 0)
                    analysis_cache_hit = True
                    break
                if item.get("kind") == "completed":
                    analysis_payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
                    analysis_markdown = str(item.get("markdown") or "")
                    analysis_version = int(item.get("version") or 0)
                    analysis_cache_hit = False
                    break
        log_phase_timing(logger, "complaint_app", ROUTE_NAME, active_phase, timing_elapsed_ms(phase_started_at), True, _timing_extra(resolved_user_id, base_id, file_id, {"analysis_v2_version": analysis_version, "cache_hit": analysis_cache_hit, "enabled_tools": "search_law"}))
        reference_content = analysis_payload.get("reference_content") if isinstance(analysis_payload, dict) and isinstance(analysis_payload.get("reference_content"), list) else build_complaint_analysis_v2_reference_content(analysis_payload or {})
        yield format_named_sse_with_history("Reference", {"phase": "analysis", "content": reference_content}, user_id=resolved_user_id, base_id=base_id)

        active_phase = "complaint_analysis_v2"
        phase_started_at = timing_now()
        final_llm_enabled = _final_llm_enabled()
        final_markdown = build_complaint_analysis_v2_final_markdown(analysis_markdown)
        if not final_markdown:
            raise HTTPException(status_code=500, detail="final 阶段结果为空")
        yield format_named_sse_with_history("Answer", {"phase": "final", "content": final_markdown}, user_id=resolved_user_id, base_id=base_id)
        if final_llm_enabled:
            logger.info("[complaint_api] final_llm_skipped route=%s user_id=%s base_id=%s reason=%s", ROUTE_NAME, resolved_user_id, base_id, "analysis_derived")
        final_payload, final_version, final_cache_hit = await _save_final_stage_result(user_id=resolved_user_id, base_id=base_id, text_version=text_version, summary_version=summary_version, analysis_version=analysis_version, summary_markdown=summary_markdown, analysis_markdown=analysis_markdown, reference_content=reference_content, final_markdown=final_markdown, scope_context=scope_context, model=DEFAULT_FINAL_MODEL)
        log_phase_timing(logger, "complaint_app", ROUTE_NAME, active_phase, timing_elapsed_ms(phase_started_at), True, _timing_extra(resolved_user_id, base_id, file_id, {"complaint_analysis_v2_version": final_version, "cache_hit": final_cache_hit, "final_llm_enabled": False}))
        active_phase = ""

        final_result = {
            **final_payload,
            "final_markdown": final_markdown,
            "versions": {
                **(final_payload.get("versions") or {}),
                "complaint_analysis_v2_version": int(final_version or 0),
            },
        }
        yield format_named_sse_with_history("Reference", {"phase": "final", "content": reference_content}, user_id=resolved_user_id, base_id=base_id)
        logger.info("[complaint_api] response_output route=%s user_id=%s base_id=%s response=%s", ROUTE_NAME, resolved_user_id, base_id, log_preview_json(final_result))
        route_success = True
        yield format_named_sse_with_history("Done", {"base_id": base_id, "result": final_result, "cache_hit": bool(text_cache_hit and summary_cache_hit and analysis_cache_hit and final_cache_hit), "scope_context": scope_context.to_meta(), "status": "completed"}, user_id=resolved_user_id, base_id=base_id)
    except HTTPException as exc:
        if active_phase:
            log_phase_timing(logger, "complaint_app", ROUTE_NAME, active_phase, timing_elapsed_ms(phase_started_at), False, _timing_extra(resolved_user_id, base_id, file_id))
        logger.error("[complaint_api] response_error route=%s user_id=%s base_id=%s detail=%s", ROUTE_NAME, resolved_user_id, base_id, str(exc.detail))
        yield format_named_sse_with_history("Done", {"base_id": base_id, "error": str(exc.detail), "scope_context": scope_context.to_meta(), "status": "error"}, user_id=resolved_user_id, base_id=base_id)
    except Exception as exc:
        if active_phase:
            log_phase_timing(logger, "complaint_app", ROUTE_NAME, active_phase, timing_elapsed_ms(phase_started_at), False, _timing_extra(resolved_user_id, base_id, file_id))
        logger.error("[complaint_api] response_error route=%s user_id=%s base_id=%s err=%s", ROUTE_NAME, resolved_user_id, base_id, exc)
        yield format_named_sse_with_history("Done", {"base_id": base_id, "error": f"处理失败: {exc}", "scope_context": scope_context.to_meta(), "status": "error"}, user_id=resolved_user_id, base_id=base_id)
    finally:
        log_route_timing(logger, "complaint_app", ROUTE_NAME, timing_elapsed_ms(route_started_at), route_success, _timing_extra(resolved_user_id, base_id, file_id, {"scope": scope_context.scope, "org_id": scope_context.org_id, "entity_id": scope_context.entity_id}))
