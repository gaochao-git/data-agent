from __future__ import annotations

import json
import os
import re
from urllib.parse import unquote
from typing import Any, AsyncGenerator

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.datastructures import FormData, UploadFile as StarletteUploadFile

from apps.complaint_app.core.versioning import load_manifest
from apps.complaint_app.services.analysis_agent.match_strategy import normalize_match_strategy
from apps.complaint_app.services.complaint_api_service import (
    build_base_id,
    build_base_id_for_file_ids,
    build_base_id_for_uploaded_pdf,
    build_reply_draft_input_hash,
    ensure_analysis_filename,
    normalize_analysis_file_extension,
    normalize_file_ids,
    generate_reply_content_from_inputs,
    get_reply_model,
    extract_supplement_material_sentence_from_summary,
    build_extract_phase_result,
    build_summary_text_from_structured_payload,
    load_review_report_text,
    load_cached_reply_by_input,
    REPLY_DRAFT_PROMPT_VERSION,
    REPLY_DRAFT_SKILL_VERSION,
    ensure_user_id,
    extract_texts_and_save,
    generate_summary_and_save,
    extract_structured_payload_from_summary_bundle,
    has_meaningful_summary_structured_payload,
    load_complaint_analysis_v2_list,
    load_issue_analysis_list,
    load_latest_issue_analysis,
    load_latest_summary_bundle,
    load_latest_text,
    load_reply_payload,
    render_reply_docx,
    resolve_pdf_file,
    normalize_reply_appeal_responses,
    normalize_reply_draft_mode,
    normalize_reply_text_field,
    save_uploaded_pdf,
    save_reply_result,
    stream_issue_analysis,
)
from apps.complaint_app.services.expert_upload_service import (
    get_upload,
    list_uploads,
    resolve_download_path,
)
from apps.complaint_app.services.trace_page_service import build_complaint_trace_payload, render_complaint_trace_html
from apps.complaint_app.services.analysis_agent.log_utils import log_preview_json
from apps.complaint_app.services.complaint_analysis_v2 import (
    stream_complaint_analysis_v2,
    stream_mock_complaint_analysis_v2,
)
from apps.complaint_app.services.pipeline_base import ComplaintSSEEvent
from utils.logger import get_logger
from utils.llm_prompt_metrics import llm_prompt_metrics_context
from utils.storage_paths import COMPLAINTS_BASE_DIR
from utils.storage_paths import ComplaintScopeContext, complaint_scope_read_chain, resolve_complaint_scope
from utils.timing import log_phase_timing, log_route_timing, timing_elapsed_ms, timing_now

router = APIRouter(prefix="/api", tags=["complaint"])
logger = get_logger(__name__)


def _load_ready_text_summary(user_id: str, base_id: str, scope_context: ComplaintScopeContext | None = None) -> tuple[str, int, str, int] | None:
    """读取可复用的 text 和 summary 产物。"""
    text_content, text_version = load_latest_text(user_id, base_id, scope_context=scope_context)
    if not text_content.strip() or text_version <= 0:
        return None
    summary_bundle, summary_version = load_latest_summary_bundle(user_id, base_id, scope_context=scope_context)
    structured_payload = extract_structured_payload_from_summary_bundle(summary_bundle)
    if summary_version <= 0 or not has_meaningful_summary_structured_payload(structured_payload):
        return None
    summary_text = str(summary_bundle.get("summary_markdown") or summary_bundle.get("_raw") or "").strip()
    if not summary_text:
        summary_text = build_summary_text_from_structured_payload(structured_payload)
    return text_content, text_version, summary_text, summary_version


def _load_ready_text(user_id: str, base_id: str, scope_context: ComplaintScopeContext | None = None) -> tuple[str, int] | None:
    """读取可复用的 text 产物。"""
    text_content, text_version = load_latest_text(user_id, base_id, scope_context=scope_context)
    if not text_content.strip() or text_version <= 0:
        return None
    return text_content, text_version


def _load_ready_summary(user_id: str, base_id: str, scope_context: ComplaintScopeContext | None = None) -> tuple[str, int] | None:
    """读取可复用的 summary 产物。"""
    summary_bundle, summary_version = load_latest_summary_bundle(user_id, base_id, scope_context=scope_context)
    structured_payload = extract_structured_payload_from_summary_bundle(summary_bundle)
    if summary_version <= 0 or not has_meaningful_summary_structured_payload(structured_payload):
        return None
    summary_text = str(summary_bundle.get("summary_markdown") or summary_bundle.get("_raw") or "").strip()
    if not summary_text:
        summary_text = build_summary_text_from_structured_payload(structured_payload)
    return summary_text, summary_version


def _build_extract_phase_sse_from_summary(user_id: str, base_id: str, summary_version: int, match_strategy: str, scope_context: ComplaintScopeContext | None = None) -> tuple[str, dict[str, Any]] | None:
    """从已保存摘要构造举报事项提取阶段 SSE 数据。"""
    summary_bundle, bundle_version = load_latest_summary_bundle(user_id, base_id, scope_context=scope_context)
    if int(bundle_version or 0) != int(summary_version or 0):
        return None
    structured_payload = extract_structured_payload_from_summary_bundle(summary_bundle)
    if not has_meaningful_summary_structured_payload(structured_payload):
        return None
    complaints = structured_payload.get("complaints") if isinstance(structured_payload.get("complaints"), list) else []
    profile = structured_payload.get("profile") if isinstance(structured_payload.get("profile"), dict) else {}
    incident = str(structured_payload.get("incident") or "").strip()
    appeals = [str(item or "").strip() for item in profile.get("appeals") or [] if str(item or "").strip()]
    phase_result = build_extract_phase_result(complaints, profile, incident)
    message = f"系统已提取 {len(complaints)} 个举报事项和 {len(appeals)} 个诉求，下一步将逐项核对事实和法律适用性。"
    return message, {"phase_node": "complaint_extract_items", "match_strategy": match_strategy, "phase_result": phase_result}


def _resolve_complaint_scope_context(scope: str | None = None, org_id: str | None = None, entity_id: str | None = None, reporter_name: str | None = None) -> ComplaintScopeContext:
    """解析举报件 scope 入参。"""
    scope = scope if isinstance(scope, str) else None
    org_id = org_id if isinstance(org_id, str) else None
    entity_id = entity_id if isinstance(entity_id, str) else None
    reporter_name = reporter_name if isinstance(reporter_name, str) else None
    try:
        return resolve_complaint_scope(scope, org_id, entity_id, reporter_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class ComplaintAnalysisRequest(BaseModel):
    user_id: str | int | None = Field(default=None, description="用户标识")
    org_id: str | None = Field(default=None, description="组织标识")
    entity_id: str | None = Field(default=None, description="被举报主体稳定标识")
    reporter_name: str | None = Field(default=None, description="举报人姓名")
    scope: str | None = Field(default=None, description="存储范围：default|org|entity")
    file_id: str | None = Field(default=None, description="文件标识")
    file_ids: list[str] | None = Field(default=None, description="多个文件标识")
    force: bool = Field(default=False, description="是否强制重跑")
    use_uploaded_pdf: bool = Field(default=False, description="是否使用上传文件")
    match_strategy: str | None = Field(default=None, description="匹配策略：two_stage|legacy|law_skill")
    reported_entities: list[str] | None = Field(default=None, description="被举报机构或人员列表")


class ComplaintAnalysisListRequest(BaseModel):
    user_id: str | int | None = Field(default=None, description="用户标识")
    org_id: str | None = Field(default=None, description="组织标识")
    entity_id: str | None = Field(default=None, description="被举报主体稳定标识")
    reporter_name: str | None = Field(default=None, description="举报人姓名")
    scope: str | None = Field(default=None, description="存储范围：default|org|entity")
    file_id: str | None = Field(default=None, description="文件标识")
    file_ids: list[str] | None = Field(default=None, description="多个文件标识")
    page: int = Field(default=1, description="页码")
    page_size: int = Field(default=50, description="每页条数")


class ReplyDraftRequest(BaseModel):
    user_id: str | int | None = Field(default=None, description="用户标识")
    org_id: str | None = Field(default=None, description="组织标识")
    entity_id: str | None = Field(default=None, description="被举报主体稳定标识")
    reporter_name: str | None = Field(default=None, description="举报人姓名")
    scope: str | None = Field(default=None, description="存储范围：default|org|entity")
    file_id: str | None = Field(default=None, description="文件标识")
    file_ids: list[str] | str | None = Field(default=None, description="多个文件标识")
    force: bool = Field(default=False, description="是否强制重跑")
    verification_content: str = Field(..., description="调查核实情况")
    violation_found: bool = Field(..., description="是否存在违规")
    disposal_measures: str = Field(default="", description="处理措施")
    appeal_responses: list[dict[str, Any]] = Field(default_factory=list, description="逐条诉求答复分类")
    draft_mode: str | None = Field(default=None, description="文书生成模式：prompt|skill，默认 skill")
    review_report: str | None = Field(default=None, description="调查报告文件标识，支持 doc/docx")


class ReplyDraftSaveRequest(BaseModel):
    user_id: str | int | None = Field(default=None, description="用户标识")
    org_id: str | None = Field(default=None, description="组织标识")
    entity_id: str | None = Field(default=None, description="被举报主体稳定标识")
    reporter_name: str | None = Field(default=None, description="举报人姓名")
    scope: str | None = Field(default=None, description="存储范围：default|org|entity")
    file_id: str | None = Field(default=None, description="文件标识")
    file_ids: list[str] | str | None = Field(default=None, description="多个文件标识")
    title: str = Field(..., description="文书标题")
    content: str = Field(..., description="文书正文")
    source_versions: dict[str, Any] | None = Field(default=None, description="来源版本信息")


def _uploads_base_dir() -> str:
    return os.path.join(COMPLAINTS_BASE_DIR, "expert_experience", "complaint-analysis-uploaded")


def _build_sse_payload(task: str, status: str, message: str, progress: int, base_id: str, user_id: str, extra: dict[str, Any] | None = None) -> str:
    return ComplaintSSEEvent(base_id=base_id, task=task, status=status, message=message, progress=progress, extra=extra or {}).to_sse(user_id=user_id)


def _safe_filename(name: str) -> str:
    text = str(name or "").strip() or "答复意见书"
    text = re.sub(r'[\\/:*?"<>|]+', "_", text)
    return text if text.lower().endswith(".docx") else f"{text}.docx"


def _build_empty_reply_draft_response(base_id: str, message: str) -> dict[str, Any]:
    return {"base_id": base_id, "title": "", "content": "", "source_versions": {}, "source": "", "saved_at": "", "reply_version": 0, "message": str(message or "").strip() or "未找到文书草稿", "exists": False}


def _build_empty_complaint_analysis_list_response(user_id: str, base_id: str, file_ids: list[str] | None, page: int, page_size: int, message: str) -> dict[str, Any]:
    return {"base_id": base_id, "items": [], "total": 0, "page": max(int(page or 1), 1), "page_size": min(max(int(page_size or 50), 1), 100), "user_id": user_id, "file_ids": file_ids or [], "message": str(message or "").strip() or "未找到举报事项分析历史", "exists": False}


def _model_dump(model: BaseModel) -> dict[str, Any]:
    """兼容 Pydantic v1/v2 的模型转字典。"""
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _complaint_timing_extra(user_id: str = "", base_id: str = "", file_id: str = "", extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {"user_id": str(user_id or "").strip(), "base_id": str(base_id or "").strip(), "file_id": str(file_id or "").strip()}
    if isinstance(extra, dict):
        payload.update({k: v for k, v in extra.items() if v is not None and v != ""})
    return payload


def _scope_timing_extra(scope_context: ComplaintScopeContext, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = scope_context.to_meta()
    if isinstance(extra, dict):
        payload.update({k: v for k, v in extra.items() if v is not None and v != ""})
    return payload


def _complaint_scope_timing_extra(user_id: str, base_id: str, file_id: str, scope_context: ComplaintScopeContext, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """组合举报件耗时日志的用户、文件与 scope 字段。"""
    payload = scope_context.to_meta()
    if isinstance(extra, dict):
        payload.update(extra)
    return _complaint_timing_extra(user_id, base_id, file_id, payload)


def _parse_bool_flag(value: Any, default: bool = False) -> bool:
    text = str(value).strip().lower() if value is not None else ""
    if not text:
        return bool(default)
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    raise HTTPException(status_code=400, detail=f"布尔参数非法: {value}")


def _is_upload_file(value: Any) -> bool:
    return isinstance(value, (UploadFile, StarletteUploadFile))


def _parse_uploaded_pdf(form: FormData) -> tuple[str | None, bytes | None]:
    file_obj = form.get("file") or form.get("pdf_file")
    if file_obj is None:
        return None, None
    if not _is_upload_file(file_obj):
        raise HTTPException(status_code=400, detail="file 字段必须是文件")
    filename = str(getattr(file_obj, "filename", "") or "").strip()
    content = file_obj.file.read()
    try:
        file_obj.file.seek(0)
    except Exception:
        pass
    return filename, bytes(content or b"")


def _parse_form_file_ids(form: FormData) -> list[str] | None:
    """从表单中解析 file_ids，支持重复字段、JSON 数组或逗号分隔。"""
    values = [str(value or "").strip() for value in form.getlist("file_ids") if str(value or "").strip()]
    if not values:
        raw = str(form.get("file_ids") or "").strip()
        if not raw:
            return None
        values = [raw]
    if len(values) == 1:
        raw = values[0]
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
                if not isinstance(parsed, list):
                    raise ValueError("file_ids 必须是数组")
                return [str(item or "").strip() for item in parsed if str(item or "").strip()]
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"file_ids 解析失败: {exc}")
        if "," in raw:
            return [item.strip() for item in raw.split(",") if item.strip()]
    return values


def _resolve_analysis_file_ids(payload: ComplaintAnalysisRequest) -> list[str]:
    """统一解析举报分析请求中的单/多文件标识。"""
    file_ids = normalize_file_ids(payload.file_ids)
    file_id = str(payload.file_id or "").strip()
    if file_ids:
        if file_id and file_id not in file_ids:
            raise HTTPException(status_code=400, detail="file_id 与 file_ids 不能同时指向不同文件")
        return file_ids
    return normalize_file_ids([file_id])


def _resolve_analysis_list_file_ids(payload: ComplaintAnalysisListRequest) -> list[str]:
    """统一解析举报分析历史列表请求中的单/多文件标识。"""
    file_ids = normalize_file_ids(payload.file_ids)
    file_id = str(payload.file_id or "").strip()
    if file_ids:
        if file_id and file_id not in file_ids:
            raise HTTPException(status_code=400, detail="file_id 与 file_ids 不能同时指向不同文件")
        return file_ids
    return normalize_file_ids([file_id])


def _parse_reply_file_ids_value(file_ids: list[str] | str | None) -> list[str]:
    """解析答复意见书接口中的 file_ids，兼容数组和逗号字符串。"""
    if file_ids is None:
        return []
    values = file_ids if isinstance(file_ids, list) else [file_ids]
    result: list[str] = []
    for value in values:
        raw = _unquote_file_id_text(str(value or "").strip())
        if not raw:
            continue
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
                if not isinstance(parsed, list):
                    raise ValueError("file_ids 必须是数组")
                result.extend(str(item or "").strip() for item in parsed if str(item or "").strip())
                continue
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"file_ids 解析失败: {exc}")
        result.extend(item.strip() for item in raw.split(",") if item.strip())
    return normalize_file_ids(result)


def _unquote_file_id_text(value: str) -> str:
    """兼容前端传入的 URL 编码或二次编码 file_id。"""
    text = str(value or "").strip()
    for _ in range(2):
        decoded = unquote(text)
        if decoded == text:
            break
        text = decoded
    return text


def _resolve_reply_draft_file_ids(payload: ReplyDraftRequest) -> list[str]:
    """统一解析答复意见书生成请求中的单/多文件标识。"""
    file_ids = _parse_reply_file_ids_value(payload.file_ids)
    file_id = _unquote_file_id_text(str(payload.file_id or "").strip())
    if file_ids:
        if file_id and file_id not in file_ids:
            raise HTTPException(status_code=400, detail="file_id 与 file_ids 不能同时指向不同文件")
        return file_ids
    return normalize_file_ids([file_id])


def _resolve_reply_save_file_ids(payload: ReplyDraftSaveRequest) -> list[str]:
    """统一解析答复意见书保存请求中的单/多文件标识。"""
    file_ids = _parse_reply_file_ids_value(payload.file_ids)
    file_id = _unquote_file_id_text(str(payload.file_id or "").strip())
    if file_ids:
        if file_id and file_id not in file_ids:
            raise HTTPException(status_code=400, detail="file_id 与 file_ids 不能同时指向不同文件")
        return file_ids
    return normalize_file_ids([file_id])


def _resolve_reply_query_file_ids(file_id: str | None, file_ids: list[str] | None) -> list[str]:
    """统一解析答复意见书查询请求中的单/多文件标识。"""
    values: list[str] = []
    for value in file_ids or []:
        raw = _unquote_file_id_text(str(value or "").strip())
        if not raw:
            continue
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
                if not isinstance(parsed, list):
                    raise ValueError("file_ids 必须是数组")
                values.extend(str(item or "").strip() for item in parsed if str(item or "").strip())
                continue
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"file_ids 解析失败: {exc}")
        values.extend(item.strip() for item in raw.split(",") if item.strip())
    normalized = normalize_file_ids(values)
    single_file_id = _unquote_file_id_text(str(file_id or "").strip())
    if normalized:
        if single_file_id and single_file_id not in normalized:
            raise HTTPException(status_code=400, detail="file_id 与 file_ids 不能同时指向不同文件")
        return normalized
    return normalize_file_ids([single_file_id])


def _build_analysis_payload_from_form(form: FormData) -> ComplaintAnalysisRequest:
    user_id = str(form.get("user_id") or "").strip() or None
    file_id = str(form.get("file_id") or "").strip() or None
    file_ids = _parse_form_file_ids(form)
    force = _parse_bool_flag(form.get("force"), default=False)
    use_uploaded_pdf = _parse_bool_flag(form.get("use_uploaded_pdf"), default=False)
    match_strategy = str(form.get("match_strategy") or "").strip() or None
    org_id = str(form.get("org_id") or "").strip() or None
    entity_id = str(form.get("entity_id") or "").strip() or None
    reporter_name = str(form.get("reporter_name") or "").strip() or None
    scope = str(form.get("scope") or "").strip() or None
    reported_entities_raw = str(form.get("reported_entities") or "").strip()
    reported_entities = None
    if reported_entities_raw:
        try:
            parsed = json.loads(reported_entities_raw) if reported_entities_raw.startswith("[") else None
            reported_entities = parsed if isinstance(parsed, list) else [item.strip() for item in re.split(r"[,，;；、\n]+", reported_entities_raw) if item.strip()]
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"reported_entities 解析失败: {exc}")
    return ComplaintAnalysisRequest(user_id=user_id, org_id=org_id, entity_id=entity_id, reporter_name=reporter_name, scope=scope, file_id=file_id, file_ids=file_ids, force=force, use_uploaded_pdf=use_uploaded_pdf, match_strategy=match_strategy, reported_entities=reported_entities)


def _build_analysis_payload_from_json(payload: Any) -> ComplaintAnalysisRequest:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体无效")
    return ComplaintAnalysisRequest(**payload)


def _validate_analysis_payload(payload: ComplaintAnalysisRequest, uploaded_filename: str | None, uploaded_content: bytes | None) -> None:
    ensure_user_id(payload.user_id)
    _resolve_complaint_scope_context(payload.scope, payload.org_id, payload.entity_id, payload.reporter_name)
    try:
        normalize_match_strategy(payload.match_strategy)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"match_strategy 非法: {exc}")
    if payload.use_uploaded_pdf:
        if normalize_file_ids(payload.file_ids):
            raise HTTPException(status_code=400, detail="use_uploaded_pdf=true 时暂不支持 file_ids")
        if not uploaded_content:
            raise HTTPException(status_code=400, detail="use_uploaded_pdf=true 时必须上传 PDF/PNG/JPG/JPEG 文件")
        ensure_analysis_filename(uploaded_filename or "")
        return
    file_ids = _resolve_analysis_file_ids(payload)
    if not file_ids:
        raise HTTPException(status_code=400, detail="use_uploaded_pdf=false 时 file_id 或 file_ids 不能为空")
    for file_id in file_ids:
        normalize_analysis_file_extension(file_id)


def _validate_analysis_list_payload(payload: ComplaintAnalysisListRequest) -> list[str]:
    ensure_user_id(payload.user_id)
    _resolve_complaint_scope_context(payload.scope, payload.org_id, payload.entity_id, payload.reporter_name)
    file_ids = _resolve_analysis_list_file_ids(payload)
    if not file_ids:
        raise HTTPException(status_code=400, detail="file_id 或 file_ids 不能为空")
    for file_id in file_ids:
        normalize_analysis_file_extension(file_id)
    return file_ids


def _is_unsupported_analysis_file_type_error(exc: HTTPException) -> bool:
    return int(getattr(exc, "status_code", 0) or 0) in {400, 415} and str(getattr(exc, "detail", "") or "").strip() == "仅支持 PDF/PNG/JPG/JPEG 文件"


def _resolve_analysis_base_id(payload: ComplaintAnalysisRequest, uploaded_content: bytes | None) -> str:
    if payload.use_uploaded_pdf:
        return build_base_id_for_uploaded_pdf(payload.file_id, uploaded_content or b"")
    return build_base_id_for_file_ids(_resolve_analysis_file_ids(payload))


def _build_empty_complaint_analysis_result(base_id: str) -> dict[str, Any]:
    return {
        "api_kind": "complaint_analysis",
        "base_id": base_id,
        "complaint_profile": {"reporter": "", "report_date": "", "reported_institution": "", "title_base": ""},
        "items_and_appeals": {"complaint_items": [], "appeals": []},
        "law_basis": [],
        "document_checklist": [],
        "verification_points": [],
        "conclusion_text": "",
        "versions": {"text_version": 0, "summary_version": 0, "analysis_version": 0, "classification_version": 0},
    }


async def _stream_mock_complaint_analysis(payload: ComplaintAnalysisRequest, uploaded_content: bytes | None = None, message: str = "仅支持 PDF/PNG/JPG/JPEG 文件") -> AsyncGenerator[str, None]:
    user_id = ensure_user_id(payload.user_id)
    base_id = _resolve_analysis_base_id(payload, uploaded_content)
    match_strategy = normalize_match_strategy(payload.match_strategy)
    route_started_at = timing_now()
    route_success = False
    file_id = str(payload.file_id or "")
    final_message = str(message or "").strip() or "仅支持 PDF/PNG/JPG/JPEG 文件"
    extra = {
        "result": _build_empty_complaint_analysis_result(base_id),
        "issue_analysis_version": 0,
        "cache_hit": False,
        "match_strategy": match_strategy,
        "versions": {"text": 0, "summary": 0, "issue_analysis": 0},
        "unsupported_file_type": True,
    }
    logger.info("[complaint_api] unsupported_file_type route=/api/complaint/complaint-analysis user_id=%s base_id=%s file_id=%s use_uploaded_pdf=%s detail=%s", user_id, base_id, file_id, payload.use_uploaded_pdf, final_message)

    try:
        yield _build_sse_payload("complaint_analysis", "started", "开始处理举报件", 0, base_id, user_id, extra={"match_strategy": match_strategy})
        route_success = True
        yield _build_sse_payload("complaint_analysis", "completed", final_message, 100, base_id, user_id, extra=extra)
    finally:
        log_route_timing(logger, "complaint_app", "POST /api/complaint/complaint-analysis", timing_elapsed_ms(route_started_at), route_success, _complaint_timing_extra(user_id, base_id, file_id, {"match_strategy": match_strategy, "unsupported_file_type": True}))


async def _stream_complaint_analysis(payload: ComplaintAnalysisRequest, uploaded_filename: str | None = None, uploaded_content: bytes | None = None) -> AsyncGenerator[str, None]:
    user_id = ensure_user_id(payload.user_id)
    base_id = _resolve_analysis_base_id(payload, uploaded_content)
    scope_context = _resolve_complaint_scope_context(payload.scope, payload.org_id, payload.entity_id, payload.reporter_name)
    default_scope_context = _resolve_complaint_scope_context("default", None, None)
    match_strategy = normalize_match_strategy(payload.match_strategy)
    route_started_at = timing_now()
    route_success = False
    file_ids = [str(payload.file_id or "")] if payload.use_uploaded_pdf else _resolve_analysis_file_ids(payload)
    file_id = ",".join(file_ids)
    active_phase = ""
    phase_started_at = 0.0
    ready_text = _load_ready_text(user_id, base_id, scope_context=default_scope_context)
    logger.info("[complaint_api] request_input route=/api/complaint/complaint-analysis user_id=%s base_id=%s scope=%s org_id=%s entity_id=%s payload=%s upload_filename=%s has_uploaded_content=%s", user_id, base_id, scope_context.scope, scope_context.org_id, scope_context.entity_id, log_preview_json(_model_dump(payload)), str(uploaded_filename or ""), bool(uploaded_content))

    try:
        yield _build_sse_payload("complaint_analysis", "started", "开始处理举报件", 0, base_id, user_id, extra={"match_strategy": match_strategy})

        if ready_text is not None:
            text_content, text_version = ready_text
            logger.info("[complaint_api] reuse_text user_id=%s base_id=%s text_version=%s force=%s", user_id, base_id, text_version, payload.force)
            log_phase_timing(logger, "complaint_app", "POST /api/complaint/complaint-analysis", "extract_text", 0, True, _complaint_timing_extra(user_id, base_id, file_id, {"text_version": text_version, "cache_hit": True, "skip_reason": "text_ready"}))
        else:
            active_phase = "prepare_original"
            phase_started_at = timing_now()
            if payload.use_uploaded_pdf:
                original_path = await save_uploaded_pdf(user_id, base_id, uploaded_filename or f"{base_id}.pdf", uploaded_content or b"", scope_context=default_scope_context)
                original_files = [(str(payload.file_id or uploaded_filename or base_id), original_path)]
            else:
                original_files = []
                for item_file_id in file_ids:
                    original_files.append((item_file_id, await resolve_pdf_file(item_file_id, user_id, base_id, force=False, scope_context=default_scope_context)))
            log_phase_timing(logger, "complaint_app", "POST /api/complaint/complaint-analysis", "prepare_original", timing_elapsed_ms(phase_started_at), True, _complaint_timing_extra(user_id, base_id, file_id, {"use_uploaded_pdf": payload.use_uploaded_pdf, "file_count": len(original_files)}))

            active_phase = "extract_text"
            phase_started_at = timing_now()
            text_content, text_version = await extract_texts_and_save(user_id, base_id, original_files, force=False, scope_context=default_scope_context)
            log_phase_timing(logger, "complaint_app", "POST /api/complaint/complaint-analysis", "extract_text", timing_elapsed_ms(phase_started_at), True, _complaint_timing_extra(user_id, base_id, file_id, {"text_version": text_version, "file_count": len(original_files)}))

        ready_summary = None if payload.force else _load_ready_summary(user_id, base_id, scope_context=scope_context)
        if ready_summary is not None:
            summary_text, summary_version = ready_summary
            logger.info("[complaint_api] reuse_summary user_id=%s base_id=%s summary_version=%s force=%s summary_chars=%s", user_id, base_id, summary_version, payload.force, len(summary_text or ""))
            log_phase_timing(logger, "complaint_app", "POST /api/complaint/complaint-analysis", "generate_summary", 0, True, _complaint_timing_extra(user_id, base_id, file_id, {"summary_version": summary_version, "cache_hit": True, "skip_reason": "summary_ready"}))
        else:
            yield _build_sse_payload("complaint_analysis", "processing", "正在生成摘要", 10, base_id, user_id)
            active_phase = "generate_summary"
            phase_started_at = timing_now()
            if payload.reported_entities:
                summary_text, summary_version = await generate_summary_and_save(user_id, base_id, text_content, text_version, force=payload.force, reported_entities=payload.reported_entities, scope_context=scope_context)
            else:
                summary_text, summary_version = await generate_summary_and_save(user_id, base_id, text_content, text_version, force=payload.force, scope_context=scope_context)
            log_phase_timing(logger, "complaint_app", "POST /api/complaint/complaint-analysis", "generate_summary", timing_elapsed_ms(phase_started_at), True, _complaint_timing_extra(user_id, base_id, file_id, {"summary_version": summary_version}))
        analysis_result: dict[str, Any] | None = None
        issue_version = 0
        cache_hit = False
        extract_phase_sent = False
        early_extract_phase = _build_extract_phase_sse_from_summary(user_id, base_id, summary_version, match_strategy, scope_context=scope_context)
        if early_extract_phase is not None:
            early_message, early_extra = early_extract_phase
            extract_phase_sent = True
            yield _build_sse_payload("complaint_analysis", "processing", early_message, 30, base_id, user_id, extra=early_extra)
        active_phase = "issue_analysis"
        phase_started_at = timing_now()
        with llm_prompt_metrics_context(request_id=base_id, user_id=user_id, base_id=base_id, file_id=file_id, business_stage="complaint_issue_analysis"):
            async for issue_event in stream_issue_analysis(user_id, base_id, summary_text, text_version, summary_version, force=payload.force, match_strategy=match_strategy, file_id=file_id, reported_entities=payload.reported_entities, scope_context=scope_context):
                kind = str(issue_event.get("kind") or "")
                if kind in {"stage", "law_rationality_item"}:
                    progress = int(issue_event.get("progress") or 0)
                    message = str(issue_event.get("message") or "")
                    phase_node = str(issue_event.get("phase_node") or "")
                    if extract_phase_sent and kind == "stage" and progress == 30 and phase_node == "complaint_extract_items":
                        continue
                    phase_result = issue_event.get("phase_result") if isinstance(issue_event.get("phase_result"), dict) else None
                    extra = {"phase_node": phase_node, "match_strategy": match_strategy}
                    if int(issue_event.get("issue_analysis_version") or 0) > 0:
                        extra["issue_analysis_version"] = int(issue_event.get("issue_analysis_version") or 0)
                    if phase_result:
                        extra["phase_result"] = phase_result
                    yield _build_sse_payload("complaint_analysis", "processing", message, progress, base_id, user_id, extra=extra)
                    continue
                if kind == "cache_hit":
                    cache_hit = True
                if kind in {"cache_hit", "completed"}:
                    result = issue_event.get("result")
                    if isinstance(result, dict):
                        analysis_result = result
                        issue_version = int(issue_event.get("issue_version") or 0)
        if not isinstance(analysis_result, dict) or issue_version <= 0:
            raise HTTPException(status_code=500, detail="举报事项分析结果为空")
        log_phase_timing(logger, "complaint_app", "POST /api/complaint/complaint-analysis", "issue_analysis", timing_elapsed_ms(phase_started_at), True, _complaint_timing_extra(user_id, base_id, file_id, {"issue_version": issue_version, "issue_analysis_version": issue_version, "cache_hit": cache_hit, "match_strategy": match_strategy}))
        active_phase = ""

        default_manifest = load_manifest(user_id, base_id, scope_context=default_scope_context)
        scope_manifest = load_manifest(user_id, base_id, scope_context=scope_context)
        extra = {
            "result": analysis_result,
            "issue_analysis_version": issue_version,
            "cache_hit": cache_hit,
            "match_strategy": match_strategy,
            "scope_context": scope_context.to_meta(),
            "versions": {
                "text": int(default_manifest.get("text", {}).get("current") or 0),
                "summary": int(default_manifest.get("summary", {}).get("current") or 0),
                "law_rationality": int(scope_manifest.get("law_rationality", {}).get("current") or 0),
                "issue_analysis": int(scope_manifest.get("issue_analysis", {}).get("current") or 0),
            },
        }
        logger.info("[complaint_api] response_output route=/api/complaint/complaint-analysis user_id=%s base_id=%s response=%s", user_id, base_id, log_preview_json(extra))
        route_success = True
        yield _build_sse_payload("complaint_analysis", "completed", "生成完成", 100, base_id, user_id, extra=extra)
    except HTTPException as exc:
        if active_phase:
            log_phase_timing(logger, "complaint_app", "POST /api/complaint/complaint-analysis", active_phase, timing_elapsed_ms(phase_started_at), False, _complaint_timing_extra(user_id, base_id, file_id, {"match_strategy": match_strategy}))
        logger.error("[complaint_api] response_error route=/api/complaint/complaint-analysis user_id=%s base_id=%s detail=%s", user_id, base_id, str(exc.detail))
        yield _build_sse_payload("complaint_analysis", "error", str(exc.detail), 0, base_id, user_id, extra={"error": str(exc.detail), "match_strategy": match_strategy})
    except Exception as exc:
        if active_phase:
            log_phase_timing(logger, "complaint_app", "POST /api/complaint/complaint-analysis", active_phase, timing_elapsed_ms(phase_started_at), False, _complaint_timing_extra(user_id, base_id, file_id, {"match_strategy": match_strategy}))
        logger.error("[complaint_api] response_error route=/api/complaint/complaint-analysis user_id=%s base_id=%s err=%s", user_id, base_id, exc)
        yield _build_sse_payload("complaint_analysis", "error", f"处理失败: {exc}", 0, base_id, user_id, extra={"error": str(exc), "match_strategy": match_strategy})
    finally:
        log_route_timing(logger, "complaint_app", "POST /api/complaint/complaint-analysis", timing_elapsed_ms(route_started_at), route_success, _complaint_timing_extra(user_id, base_id, file_id, {"match_strategy": match_strategy}))


async def _stream_reply_draft(payload: ReplyDraftRequest) -> AsyncGenerator[str, None]:
    user_id = ensure_user_id(payload.user_id)
    scope_context = _resolve_complaint_scope_context(payload.scope, payload.org_id, payload.entity_id, payload.reporter_name)
    base_id = ""
    file_ids: list[str] = []
    file_id = ""
    route_started_at = timing_now()
    route_success = False
    active_phase = ""
    phase_started_at = 0.0
    error_type = ""
    error_detail = ""

    try:
        file_ids = _resolve_reply_draft_file_ids(payload)
        if not file_ids:
            raise HTTPException(status_code=400, detail="file_id 或 file_ids 不能为空")
        base_id = build_base_id_for_file_ids(file_ids)
        file_id = ",".join(file_ids)
        logger.info("[complaint_api] request_input route=/api/complaint/reply-draft user_id=%s base_id=%s scope=%s org_id=%s entity_id=%s payload=%s", user_id, base_id, scope_context.scope, scope_context.org_id, scope_context.entity_id, log_preview_json(_model_dump(payload)))
        yield _build_sse_payload("reply_draft", "started", "开始生成答复意见书", 0, base_id, user_id, extra={"scope_context": scope_context.to_meta()})
        active_phase = "load_issue_analysis"
        phase_started_at = timing_now()
        verification_content = normalize_reply_text_field(payload.verification_content, "verification_content")
        disposal_measures = str(payload.disposal_measures or "").strip()
        appeal_responses = normalize_reply_appeal_responses(payload.appeal_responses)
        draft_mode = normalize_reply_draft_mode(payload.draft_mode)
        analysis_result, issue_version = load_latest_issue_analysis(user_id, base_id, scope_context=scope_context)
        if not isinstance(analysis_result, dict) or issue_version <= 0:
            scope_chain = ",".join(item.scope_path for item in complaint_scope_read_chain(scope_context))
            logger.error("[complaint_api] reply_draft_issue_analysis_missing user_id=%s base_id=%s file_id=%s scope=%s org_id=%s entity_id=%s scope_chain=%s issue_version=%s", user_id, base_id, file_id, scope_context.scope, scope_context.org_id, scope_context.entity_id, scope_chain, issue_version)
            raise HTTPException(status_code=400, detail="请先完成举报分析，再生成答复意见书")
        log_phase_timing(logger, "complaint_app", "POST /api/complaint/reply-draft", "load_issue_analysis", timing_elapsed_ms(phase_started_at), True, _complaint_scope_timing_extra(user_id, base_id, file_id, scope_context, {"issue_version": issue_version}))
        model = get_reply_model()
        supplement_material_sentence = extract_supplement_material_sentence_from_summary(user_id, base_id, scope_context=_resolve_complaint_scope_context("default", None, None))
        review_report_text = await load_review_report_text(str(payload.review_report or "").strip(), user_id, base_id, scope_context=_resolve_complaint_scope_context("default", None, None))
        input_hash = build_reply_draft_input_hash(file_id, issue_version, verification_content, payload.violation_found, disposal_measures, appeal_responses, model, draft_mode=draft_mode, supplement_material_sentence=supplement_material_sentence, review_report=str(payload.review_report or "").strip(), review_report_text=review_report_text, reporter_name=payload.reporter_name or "")
        yield _build_sse_payload("reply_draft", "processing", "正在整理案件信息", 20, base_id, user_id)

        if not payload.force:
            active_phase = "cache_lookup"
            phase_started_at = timing_now()
            cached_reply, cached_reply_version = load_cached_reply_by_input(user_id, base_id, input_hash, model, scope_context=scope_context)
            log_phase_timing(logger, "complaint_app", "POST /api/complaint/reply-draft", "cache_lookup", timing_elapsed_ms(phase_started_at), True, _complaint_scope_timing_extra(user_id, base_id, file_id, scope_context, {"cache_hit": isinstance(cached_reply, dict), "reply_version": cached_reply_version if isinstance(cached_reply, dict) else 0}))
            if isinstance(cached_reply, dict):
                active_phase = ""
                route_success = True
                yield _build_sse_payload("reply_draft", "completed", "生成完成", 100, base_id, user_id, extra={"result": cached_reply, "reply_version": cached_reply_version, "cache_hit": True, "scope_context": scope_context.to_meta()})
                return

        yield _build_sse_payload("reply_draft", "processing", "正在组织文书结构", 40, base_id, user_id)
        yield _build_sse_payload("reply_draft", "processing", "正在生成答复内容", 60, base_id, user_id)
        active_phase = "generate_reply"
        phase_started_at = timing_now()
        with llm_prompt_metrics_context(request_id=base_id, user_id=user_id, base_id=base_id, file_id=file_id, business_stage="complaint_reply_draft"):
            final_title, final_content = await generate_reply_content_from_inputs(analysis_result, verification_content, payload.violation_found, disposal_measures, appeal_responses, draft_mode=draft_mode, supplement_material_sentence=supplement_material_sentence, review_report_text=review_report_text, reporter_name=payload.reporter_name or "")
        log_phase_timing(logger, "complaint_app", "POST /api/complaint/reply-draft", "generate_reply", timing_elapsed_ms(phase_started_at), True, _complaint_scope_timing_extra(user_id, base_id, file_id, scope_context, {"model": model, "draft_mode": draft_mode, "has_review_report": bool(review_report_text)}))
        yield _build_sse_payload("reply_draft", "processing", "正在校验并保存草稿", 80, base_id, user_id)
        active_phase = "save_reply"
        phase_started_at = timing_now()
        saved_payload, reply_version = await save_reply_result(user_id, base_id, final_title, final_content, {"issue_analysis_version": issue_version}, source="auto", meta_extra={"input_hash": input_hash, "prompt_version": REPLY_DRAFT_PROMPT_VERSION, "model": model, "draft_mode": draft_mode, "skill_version": REPLY_DRAFT_SKILL_VERSION if draft_mode == "skill" else None, "review_report": str(payload.review_report or "").strip(), "review_report_text_chars": len(review_report_text or "")}, scope_context=scope_context)
        log_phase_timing(logger, "complaint_app", "POST /api/complaint/reply-draft", "save_reply", timing_elapsed_ms(phase_started_at), True, _complaint_scope_timing_extra(user_id, base_id, file_id, scope_context, {"reply_version": reply_version}))
        active_phase = ""

        route_success = True
        yield _build_sse_payload(
            "reply_draft",
            "completed",
            "生成完成",
            100,
            base_id,
            user_id,
            extra={
                "result": saved_payload,
                "reply_version": reply_version,
                "cache_hit": False,
                "scope_context": scope_context.to_meta(),
            },
        )
    except HTTPException as exc:
        error_type = "HTTPException"
        error_detail = str(exc.detail)
        if active_phase:
            log_phase_timing(logger, "complaint_app", "POST /api/complaint/reply-draft", active_phase, timing_elapsed_ms(phase_started_at), False, _complaint_scope_timing_extra(user_id, base_id, file_id, scope_context, {"error_type": error_type, "error_detail": error_detail}))
        logger.error("[complaint_api] response_error route=/api/complaint/reply-draft user_id=%s base_id=%s file_id=%s phase=%s scope=%s org_id=%s entity_id=%s detail=%s", user_id, base_id, file_id, active_phase, scope_context.scope, scope_context.org_id, scope_context.entity_id, error_detail)
        yield _build_sse_payload("reply_draft", "error", str(exc.detail), 0, base_id, user_id, extra={"error": str(exc.detail)})
    except Exception as exc:
        error_type = exc.__class__.__name__
        error_detail = str(exc)
        if active_phase:
            log_phase_timing(logger, "complaint_app", "POST /api/complaint/reply-draft", active_phase, timing_elapsed_ms(phase_started_at), False, _complaint_scope_timing_extra(user_id, base_id, file_id, scope_context, {"error_type": error_type, "error_detail": error_detail}))
        logger.error("[complaint_api] response_error route=/api/complaint/reply-draft user_id=%s base_id=%s file_id=%s phase=%s scope=%s org_id=%s entity_id=%s err=%s", user_id, base_id, file_id, active_phase, scope_context.scope, scope_context.org_id, scope_context.entity_id, exc)
        yield _build_sse_payload("reply_draft", "error", f"处理失败: {exc}", 0, base_id, user_id, extra={"error": str(exc)})
    finally:
        log_route_timing(logger, "complaint_app", "POST /api/complaint/reply-draft", timing_elapsed_ms(route_started_at), route_success, _complaint_scope_timing_extra(user_id, base_id, file_id, scope_context, {"active_phase": active_phase, "error_type": error_type, "error_detail": error_detail}))


@router.get("/complaint/complaint-analysis-uploaded")
def list_complaint_analysis_uploads():
    started_at = timing_now()
    try:
        payload = list_uploads(_uploads_base_dir())
        log_route_timing(logger, "complaint_app", "GET /api/complaint/complaint-analysis-uploaded", timing_elapsed_ms(started_at), True)
        return payload
    except Exception:
        log_route_timing(logger, "complaint_app", "GET /api/complaint/complaint-analysis-uploaded", timing_elapsed_ms(started_at), False)
        raise


@router.get("/complaint/complaint-analysis-uploaded/{upload_id}")
def get_complaint_analysis_upload(upload_id: str):
    started_at = timing_now()
    try:
        payload, status = get_upload(_uploads_base_dir(), upload_id)
        log_route_timing(logger, "complaint_app", "GET /api/complaint/complaint-analysis-uploaded/{upload_id}", timing_elapsed_ms(started_at), int(status) < 500, {"upload_id": upload_id, "status_code": status})
        return JSONResponse(payload, status_code=status)
    except Exception:
        log_route_timing(logger, "complaint_app", "GET /api/complaint/complaint-analysis-uploaded/{upload_id}", timing_elapsed_ms(started_at), False, {"upload_id": upload_id})
        raise


@router.get("/complaint/complaint-analysis-uploaded/{upload_id}/download")
def download_complaint_analysis_upload(upload_id: str):
    started_at = timing_now()
    try:
        path, status = resolve_download_path(_uploads_base_dir(), upload_id)
        if status != 200 or not path:
            log_route_timing(logger, "complaint_app", "GET /api/complaint/complaint-analysis-uploaded/{upload_id}/download", timing_elapsed_ms(started_at), False, {"upload_id": upload_id, "status_code": status})
            raise HTTPException(status_code=404, detail="原始文件不存在")
        log_route_timing(logger, "complaint_app", "GET /api/complaint/complaint-analysis-uploaded/{upload_id}/download", timing_elapsed_ms(started_at), True, {"upload_id": upload_id})
        return FileResponse(path, filename=os.path.basename(path), media_type="application/octet-stream")
    except HTTPException:
        raise
    except Exception:
        log_route_timing(logger, "complaint_app", "GET /api/complaint/complaint-analysis-uploaded/{upload_id}/download", timing_elapsed_ms(started_at), False, {"upload_id": upload_id})
        raise


@router.post("/complaint/complaint-analysis")
async def complaint_analysis(request: Request):
    content_type = request.headers.get("content-type", "")
    uploaded_filename = None
    uploaded_content = None
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        payload = _build_analysis_payload_from_form(form)
        uploaded_filename, uploaded_content = _parse_uploaded_pdf(form)
    else:
        try:
            raw_payload = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"请求体解析失败: {exc}")
        payload = _build_analysis_payload_from_json(raw_payload)
    try:
        _validate_analysis_payload(payload, uploaded_filename, uploaded_content)
    except HTTPException as exc:
        if _is_unsupported_analysis_file_type_error(exc):
            return StreamingResponse(_stream_mock_complaint_analysis(payload, uploaded_content, str(exc.detail)), media_type="text/event-stream")
        raise
    return StreamingResponse(_stream_complaint_analysis(payload, uploaded_filename, uploaded_content), media_type="text/event-stream")


@router.post("/complaint/complaint-analysis-v2")
async def complaint_analysis_v2(request: Request):
    content_type = request.headers.get("content-type", "")
    uploaded_filename = None
    uploaded_content = None
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        payload = _build_analysis_payload_from_form(form)
        uploaded_filename, uploaded_content = _parse_uploaded_pdf(form)
    else:
        try:
            raw_payload = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"请求体解析失败: {exc}")
        payload = _build_analysis_payload_from_json(raw_payload)
    try:
        _validate_analysis_payload(payload, uploaded_filename, uploaded_content)
    except HTTPException as exc:
        if _is_unsupported_analysis_file_type_error(exc):
            user_id = ensure_user_id(payload.user_id)
            base_id = _resolve_analysis_base_id(payload, uploaded_content)
            return StreamingResponse(stream_mock_complaint_analysis_v2(payload, base_id=base_id, user_id=user_id, message=str(exc.detail)), media_type="text/event-stream")
        raise
    user_id = ensure_user_id(payload.user_id)
    base_id = _resolve_analysis_base_id(payload, uploaded_content)
    scope_context = _resolve_complaint_scope_context(payload.scope, payload.org_id, payload.entity_id, payload.reporter_name)
    return StreamingResponse(stream_complaint_analysis_v2(payload, user_id=user_id, base_id=base_id, scope_context=scope_context, uploaded_filename=uploaded_filename, uploaded_content=uploaded_content), media_type="text/event-stream")


@router.post("/complaint/complaint-analysis-v2/list")
async def complaint_analysis_v2_list(payload: ComplaintAnalysisListRequest):
    started_at = timing_now()
    user_id = str(payload.user_id or "").strip()
    file_ids: list[str] = []
    base_id = ""
    file_id = ""
    try:
        user_id = ensure_user_id(payload.user_id)
        scope_context = _resolve_complaint_scope_context(payload.scope, payload.org_id, payload.entity_id, payload.reporter_name)
        file_ids = _validate_analysis_list_payload(payload)
        base_id = build_base_id_for_file_ids(file_ids)
        file_id = ",".join(file_ids)
        result = load_complaint_analysis_v2_list(user_id, base_id, page=payload.page, page_size=payload.page_size, scope_context=scope_context)
        result["user_id"] = user_id
        result["file_ids"] = file_ids
        log_route_timing(logger, "complaint_app", "POST /api/complaint/complaint-analysis-v2/list", timing_elapsed_ms(started_at), True, _complaint_timing_extra(user_id, base_id, file_id, _scope_timing_extra(scope_context, {"total": result.get("total"), "page": result.get("page"), "page_size": result.get("page_size")})))
        return JSONResponse(result, status_code=200)
    except HTTPException as exc:
        logger.error("[complaint_api] analysis_v2_list_query_error user_id=%s base_id=%s file_id=%s detail=%s", user_id, base_id, file_id, str(exc.detail))
        log_route_timing(logger, "complaint_app", "POST /api/complaint/complaint-analysis-v2/list", timing_elapsed_ms(started_at), False, _complaint_timing_extra(user_id, base_id, file_id, {"status_code": exc.status_code}))
        return JSONResponse({"base_id": base_id, "items": [], "total": 0, "page": max(int(payload.page or 1), 1), "page_size": min(max(int(payload.page_size or 50), 1), 100), "user_id": user_id, "file_ids": file_ids, "scope_context": scope_context.to_meta(), "message": str(exc.detail), "exists": False}, status_code=200)
    except Exception as exc:
        logger.error("[complaint_api] analysis_v2_list_query_error user_id=%s base_id=%s file_id=%s err=%s", user_id, base_id, file_id, exc)
        log_route_timing(logger, "complaint_app", "POST /api/complaint/complaint-analysis-v2/list", timing_elapsed_ms(started_at), False, _complaint_timing_extra(user_id, base_id, file_id))
        return JSONResponse({"base_id": base_id, "items": [], "total": 0, "page": max(int(payload.page or 1), 1), "page_size": min(max(int(payload.page_size or 50), 1), 100), "user_id": user_id, "file_ids": file_ids, "scope_context": {}, "message": "查询举报分析V2历史失败", "exists": False}, status_code=200)


@router.post("/complaint/complaint-analysis/list")
async def complaint_analysis_list(payload: ComplaintAnalysisListRequest):
    started_at = timing_now()
    user_id = str(payload.user_id or "").strip()
    file_ids: list[str] = []
    base_id = ""
    file_id = ""
    try:
        user_id = ensure_user_id(payload.user_id)
        scope_context = _resolve_complaint_scope_context(payload.scope, payload.org_id, payload.entity_id, payload.reporter_name)
        file_ids = _validate_analysis_list_payload(payload)
        base_id = build_base_id_for_file_ids(file_ids)
        file_id = ",".join(file_ids)
        result = load_issue_analysis_list(user_id, base_id, page=payload.page, page_size=payload.page_size, scope_context=scope_context)
        result["user_id"] = user_id
        result["file_ids"] = file_ids
        log_route_timing(logger, "complaint_app", "POST /api/complaint/complaint-analysis/list", timing_elapsed_ms(started_at), True, _complaint_timing_extra(user_id, base_id, file_id, _scope_timing_extra(scope_context, {"total": result.get("total"), "page": result.get("page"), "page_size": result.get("page_size")})))
        return JSONResponse(result, status_code=200)
    except HTTPException as exc:
        logger.error("[complaint_api] analysis_list_query_error user_id=%s base_id=%s file_id=%s detail=%s", user_id, base_id, file_id, str(exc.detail))
        log_route_timing(logger, "complaint_app", "POST /api/complaint/complaint-analysis/list", timing_elapsed_ms(started_at), False, _complaint_timing_extra(user_id, base_id, file_id, {"status_code": exc.status_code}))
        return JSONResponse(_build_empty_complaint_analysis_list_response(user_id, base_id, file_ids, payload.page, payload.page_size, str(exc.detail)), status_code=200)
    except Exception as exc:
        logger.error("[complaint_api] analysis_list_query_error user_id=%s base_id=%s file_id=%s err=%s", user_id, base_id, file_id, exc)
        log_route_timing(logger, "complaint_app", "POST /api/complaint/complaint-analysis/list", timing_elapsed_ms(started_at), False, _complaint_timing_extra(user_id, base_id, file_id))
        return JSONResponse(_build_empty_complaint_analysis_list_response(user_id, base_id, file_ids, payload.page, payload.page_size, "查询举报事项分析历史失败"), status_code=200)


@router.get("/complaint/trace")
async def complaint_trace(base_id: str | None = Query(None, description="举报件base_id"), file_id: str | None = Query(None, description="文件ID"), file_ids: list[str] | None = Query(None, description="多个文件ID，支持重复参数或逗号分隔"), user_id: str = Query(..., description="用户标识"), issue_analysis_version: int | None = Query(None, description="举报事项分析版本，不传默认当前版本"), format: str = Query("html", description="html 或 json"), org_id: str | None = Query(None, description="组织标识"), entity_id: str | None = Query(None, description="被举报主体稳定标识"), reporter_name: str | None = Query(None, description="举报人姓名"), scope: str | None = Query(None, description="存储范围：default|org|entity")):
    user_id = ensure_user_id(user_id)
    scope_context = _resolve_complaint_scope_context(scope, org_id, entity_id, reporter_name)
    resolved_base_id = str(base_id or "").strip()
    resolved_file_ids = [] if resolved_base_id else _resolve_reply_query_file_ids(file_id, file_ids)
    if not resolved_base_id and not resolved_file_ids:
        raise HTTPException(status_code=400, detail="file_id 或 file_ids 不能为空")
    if str(format or "").lower() == "json":
        return JSONResponse(build_complaint_trace_payload(user_id, resolved_file_ids, resolved_base_id, issue_analysis_version, scope_context=scope_context), status_code=200)
    return HTMLResponse(render_complaint_trace_html(user_id, resolved_file_ids, resolved_base_id, issue_analysis_version, scope_context=scope_context), status_code=200)


@router.post("/complaint/reply-draft")
async def complaint_reply_draft(payload: ReplyDraftRequest):
    return StreamingResponse(_stream_reply_draft(payload), media_type="text/event-stream")


@router.post("/complaint/reply-draft/save")
async def complaint_reply_draft_save(payload: ReplyDraftSaveRequest):
    started_at = timing_now()
    user_id = str(payload.user_id or "").strip()
    base_id = ""
    file_ids: list[str] = []
    file_id = ""
    try:
        user_id = ensure_user_id(payload.user_id)
        scope_context = _resolve_complaint_scope_context(payload.scope, payload.org_id, payload.entity_id, payload.reporter_name)
        file_ids = _resolve_reply_save_file_ids(payload)
        if not file_ids:
            raise HTTPException(status_code=400, detail="file_id 或 file_ids 不能为空")
        base_id = build_base_id_for_file_ids(file_ids)
        file_id = ",".join(file_ids)
        saved_payload, reply_version = await save_reply_result(user_id, base_id, payload.title, payload.content, payload.source_versions or {}, source="user", scope_context=scope_context)
        result = {"base_id": base_id, "reply_version": reply_version, "saved_at": saved_payload.get("saved_at"), "scope_context": scope_context.to_meta()}
        log_route_timing(logger, "complaint_app", "POST /api/complaint/reply-draft/save", timing_elapsed_ms(started_at), True, _complaint_timing_extra(user_id, base_id, file_id, {"reply_version": result.get("reply_version")}))
        return JSONResponse(result, status_code=200)
    except Exception:
        log_route_timing(logger, "complaint_app", "POST /api/complaint/reply-draft/save", timing_elapsed_ms(started_at), False, _complaint_timing_extra(user_id, base_id, file_id))
        raise


@router.get("/complaint/reply-draft")
async def complaint_reply_draft_get(file_id: str | None = Query(None, description="文件ID"), file_ids: list[str] | None = Query(None, description="多个文件ID，支持重复参数或逗号分隔"), user_id: str = Query(..., description="用户标识"), version: str = Query("latest", description="文书版本，默认 latest"), org_id: str | None = Query(None, description="组织标识"), entity_id: str | None = Query(None, description="被举报主体稳定标识"), reporter_name: str | None = Query(None, description="举报人姓名"), scope: str | None = Query(None, description="存储范围：default|org|entity")):
    started_at = timing_now()
    user_id = ensure_user_id(user_id)
    scope_context = _resolve_complaint_scope_context(scope, org_id, entity_id, reporter_name)
    resolved_file_ids = _resolve_reply_query_file_ids(file_id, file_ids)
    if not resolved_file_ids:
        raise HTTPException(status_code=400, detail="file_id 或 file_ids 不能为空")
    base_id = build_base_id_for_file_ids(resolved_file_ids)
    resolved_file_id = ",".join(resolved_file_ids)
    logger.info("[complaint_api] reply_draft_query_input user_id=%s raw_file_id=%s raw_file_ids=%s resolved_file_ids=%s base_id=%s version=%s", user_id, file_id, log_preview_json(file_ids), log_preview_json(resolved_file_ids), base_id, version)
    try:
        payload, target_version = load_reply_payload(user_id, base_id, version, scope_context=scope_context)
        logger.info("[complaint_api] reply_draft_query user_id=%s base_id=%s version=%s reply_version=%s", user_id, base_id, version, target_version)
        log_route_timing(logger, "complaint_app", "GET /api/complaint/reply-draft", timing_elapsed_ms(started_at), True, _complaint_timing_extra(user_id, base_id, resolved_file_id, {"version": version, "reply_version": target_version}))
        return JSONResponse({**payload, "reply_version": target_version}, status_code=200)
    except HTTPException as exc:
        if exc.status_code != 404:
            log_route_timing(logger, "complaint_app", "GET /api/complaint/reply-draft", timing_elapsed_ms(started_at), False, _complaint_timing_extra(user_id, base_id, resolved_file_id, {"version": version, "status_code": exc.status_code}))
            raise
        logger.info("[complaint_api] reply_draft_query_empty user_id=%s base_id=%s file_id=%s version=%s detail=%s", user_id, base_id, resolved_file_id, version, str(exc.detail))
        log_route_timing(logger, "complaint_app", "GET /api/complaint/reply-draft", timing_elapsed_ms(started_at), True, _complaint_timing_extra(user_id, base_id, resolved_file_id, {"version": version, "exists": False}))
        return JSONResponse(_build_empty_reply_draft_response(base_id, str(exc.detail)), status_code=200)


@router.get("/complaint/reply-draft/download")
async def complaint_reply_draft_download(file_id: str | None = Query(None, description="文件ID"), file_ids: list[str] | None = Query(None, description="多个文件ID，支持重复参数或逗号分隔"), user_id: str = Query(..., description="用户标识"), version: str = Query("latest", description="文书版本，默认 latest"), org_id: str | None = Query(None, description="组织标识"), entity_id: str | None = Query(None, description="被举报主体稳定标识"), reporter_name: str | None = Query(None, description="举报人姓名"), scope: str | None = Query(None, description="存储范围：default|org|entity")):
    started_at = timing_now()
    user_id = ensure_user_id(user_id)
    scope_context = _resolve_complaint_scope_context(scope, org_id, entity_id, reporter_name)
    resolved_file_ids = _resolve_reply_query_file_ids(file_id, file_ids)
    if not resolved_file_ids:
        raise HTTPException(status_code=400, detail="file_id 或 file_ids 不能为空")
    base_id = build_base_id_for_file_ids(resolved_file_ids)
    resolved_file_id = ",".join(resolved_file_ids)
    logger.info("[complaint_api] reply_draft_download_input user_id=%s raw_file_id=%s raw_file_ids=%s resolved_file_ids=%s base_id=%s version=%s", user_id, file_id, log_preview_json(file_ids), log_preview_json(resolved_file_ids), base_id, version)
    try:
        payload, target_version = load_reply_payload(user_id, base_id, version, scope_context=scope_context)
        payload_scope = payload.get("scope_context") if isinstance(payload.get("scope_context"), dict) else scope_context.to_meta()
        render_scope = _resolve_complaint_scope_context(payload_scope.get("scope"), payload_scope.get("org_id"), payload_scope.get("entity_id"), payload_scope.get("reporter_name"))
        docx_path = await render_reply_docx(user_id, base_id, payload, target_version, scope_context=render_scope)
        log_route_timing(logger, "complaint_app", "GET /api/complaint/reply-draft/download", timing_elapsed_ms(started_at), True, _complaint_timing_extra(user_id, base_id, resolved_file_id, {"version": version, "reply_version": target_version}))
        return FileResponse(docx_path, filename=_safe_filename(payload.get("title") or "答复意见书"), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    except Exception:
        log_route_timing(logger, "complaint_app", "GET /api/complaint/reply-draft/download", timing_elapsed_ms(started_at), False, _complaint_timing_extra(user_id, base_id, resolved_file_id, {"version": version}))
        raise


@router.get("/files/{full_path:path}")
def serve_uploaded_file(full_path: str):
    started_at = timing_now()
    target_path = os.path.join(COMPLAINTS_BASE_DIR, full_path)
    if not os.path.isfile(target_path):
        log_route_timing(logger, "complaint_app", "GET /api/files/{full_path:path}", timing_elapsed_ms(started_at), False, {"full_path": full_path})
        raise HTTPException(status_code=404, detail="文件不存在")
    log_route_timing(logger, "complaint_app", "GET /api/files/{full_path:path}", timing_elapsed_ms(started_at), True, {"full_path": full_path})
    return FileResponse(target_path)
