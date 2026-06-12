from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from datetime import datetime
from typing import Any, AsyncGenerator
from zoneinfo import ZoneInfo

import httpx
from docx import Document
from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from PIL import Image

from apps.complaint_app.core.async_openai_client import get_async_openai_client
from apps.complaint_app.core.model_config import get_complaint_extract_items_model, get_complaint_final_conclusion_model
from apps.complaint_app.core.versioning import load_manifest, next_version, save_manifest, stage_dir, update_manifest, write_meta
from apps.complaint_app.services.analysis_agent.log_utils import build_trace_tag, log_preview_json, log_preview_text
from apps.complaint_app.services.analysis_agent.match_strategy import normalize_match_strategy
from apps.complaint_app.services.analysis_agent.agent import run_complaint_analysis
from apps.complaint_app.services.summary.llm_summary import generate_summary_structured_payload, normalize_summary_structured_payload
from apps.complaint_app.services.text.complaint_files_handler import process_image, process_pdf
from apps.complaint_app.tools.doc_markdown import convert_doc_to_markdown, replace_images_with_ocr
from apps.complaint_app.tools.image_text_extractor import recognize_image_async
from apps.complaint_app.utils import _ocr_max_concurrency
from utils.llm_thinking import build_thinking_extra_body
from utils.llm_prompt_metrics import extract_usage_tokens, llm_prompt_metrics_context, record_llm_output_metrics, record_llm_prompt_metrics
from utils.logger import get_logger
from utils.storage_paths import ComplaintScopeContext, complaint_scope_read_chain

logger = get_logger(__name__)
REPLY_DRAFT_PROMPT_VERSION = "reply_draft_template_v5"
REPLY_DRAFT_DATE_TZ = "Asia/Shanghai"
REPLY_DRAFT_TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "docs", "答复意见书模板.docx")
REPLY_DRAFT_MODE_PROMPT = "prompt"
REPLY_DRAFT_MODE_SKILL = "skill"
REPLY_DRAFT_ALLOWED_MODES = {REPLY_DRAFT_MODE_PROMPT, REPLY_DRAFT_MODE_SKILL}
REPLY_DRAFT_SKILL_VERSION = "complaint_reply_draft_skill_v1"
ANALYSIS_ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg"}
ANALYSIS_ALLOWED_FILE_EXTENSIONS = {"pdf", *ANALYSIS_ALLOWED_IMAGE_EXTENSIONS}
REVIEW_REPORT_ALLOWED_EXTENSIONS = {"doc", "docx"}


def build_base_id(file_id: str) -> str:
    """将 file_id 映射为稳定 base_id。"""
    raw = str(file_id or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="file_id 不能为空")
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def normalize_file_ids(file_ids: list[str] | tuple[str, ...] | None) -> list[str]:
    """规范化多文件 ID，去空、去重并排序以保证组合哈希稳定。"""
    normalized: set[str] = set()
    for value in file_ids or []:
        raw = str(value or "").strip()
        if raw:
            normalized.add(raw)
    return sorted(normalized)


def build_base_id_for_file_ids(file_ids: list[str] | tuple[str, ...]) -> str:
    """按文件 ID 集合生成稳定合案 base_id。"""
    normalized = normalize_file_ids(file_ids)
    if not normalized:
        raise HTTPException(status_code=400, detail="file_ids 不能为空")
    if len(normalized) == 1:
        return build_base_id(normalized[0])
    raw = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def ensure_user_id(user_id: Any) -> str:
    """校验并返回 user_id。"""
    value = "" if user_id is None else str(user_id).strip()
    if not value:
        raise HTTPException(status_code=400, detail="user_id 不能为空")
    return value


def normalize_reply_reporter(value: Any) -> str:
    """清洗答复意见书收件人称谓。"""
    text = str(value or "").strip()
    if not text:
        return "你"
    text = re.sub(r"-fys-.+$", "", text, flags=re.IGNORECASE).strip()
    if not text:
        return "你"
    text = re.sub(r"\s*[（(]\s*(?:身份证(?:号|号码)?[:：]?\s*)?[0-9Xx*]{15,20}\s*[）)]", "", text).strip()
    return text or "你"


def build_base_id_for_uploaded_pdf(file_id: str | None, content: bytes) -> str:
    """上传模式下生成稳定 base_id（优先 file_id，否则按文件内容哈希）。"""
    raw = str(file_id or "").strip()
    if raw:
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    if not isinstance(content, (bytes, bytearray)) or not content:
        raise HTTPException(status_code=400, detail="上传 PDF 不能为空")
    return hashlib.sha1(bytes(content)).hexdigest()[:16]


def get_reply_model() -> str:
    """读取文书生成模型，未配置回退到结论模型。"""
    return (os.getenv("COMPLAINT_REPLY_DRAFT_MODEL") or "").strip() or get_complaint_final_conclusion_model()


def sanitize_file_id(file_id: str) -> str:
    """规范化 file_id 并阻断路径穿越。"""
    raw = str(file_id or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="file_id 不能为空")
    normalized = raw.replace("\\", "/").lstrip("/")
    parts = [part for part in normalized.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        raise HTTPException(status_code=400, detail="file_id 非法")
    return "/".join(parts)


def normalize_analysis_file_extension(value: str) -> str:
    """解析并校验举报分析原始文件扩展名。"""
    raw = str(value or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="文件标识不能为空")
    normalized = raw.replace("\\", "/")
    filename = os.path.basename(normalized)
    if not filename or "." not in filename:
        raise HTTPException(status_code=400, detail="仅支持 PDF/PNG/JPG/JPEG 文件")
    ext = filename.rsplit(".", 1)[1].lower()
    if ext not in ANALYSIS_ALLOWED_FILE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="仅支持 PDF/PNG/JPG/JPEG 文件")
    return ext


def build_download_target_path(user_id: str, base_id: str, safe_file_id: str, file_ext: str, scope_context: ComplaintScopeContext | None = None) -> str:
    """构造下载文件本地保存路径。"""
    filename = os.path.basename(safe_file_id)
    if not filename:
        filename = f"{base_id}.{file_ext}"
    if "." not in filename:
        filename = f"{filename}.{file_ext}"
    return os.path.join(stage_dir(user_id, base_id, "original", scope_context=scope_context), filename)


def build_original_target_path(user_id: str, base_id: str, filename: str, file_ext: str, scope_context: ComplaintScopeContext | None = None) -> str:
    """构造 original 目录下的目标原始文件路径。"""
    safe_name = os.path.basename(str(filename or "").strip()) or f"{base_id}.{file_ext}"
    if "." not in safe_name:
        safe_name = f"{safe_name}.{file_ext}"
    return os.path.join(stage_dir(user_id, base_id, "original", scope_context=scope_context), safe_name)


def normalize_review_report_extension(value: str) -> str:
    """解析并校验调查报告文件扩展名。"""
    raw = str(value or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="review_report 不能为空")
    normalized = raw.replace("\\", "/")
    filename = os.path.basename(normalized)
    if not filename or "." not in filename:
        raise HTTPException(status_code=400, detail="review_report 仅支持 DOC/DOCX 文件")
    ext = filename.rsplit(".", 1)[1].lower()
    if ext not in REVIEW_REPORT_ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="review_report 仅支持 DOC/DOCX 文件")
    return ext


def build_review_report_target_path(user_id: str, base_id: str, safe_file_id: str, file_ext: str, scope_context: ComplaintScopeContext | None = None) -> str:
    """构造调查报告下载保存路径。"""
    filename = os.path.basename(safe_file_id) or f"review_report.{file_ext}"
    if "." not in filename:
        filename = f"{filename}.{file_ext}"
    digest = hashlib.sha1(str(safe_file_id or filename).encode("utf-8")).hexdigest()[:8]
    return os.path.join(stage_dir(user_id, base_id, "reply_review", scope_context=scope_context), f"{digest}_{filename}")


def ensure_analysis_file_path(path: str) -> str:
    """校验举报分析原始文件扩展名。"""
    return normalize_analysis_file_extension(path)


def ensure_analysis_filename(filename: str) -> str:
    """校验上传文件名并返回扩展名。"""
    try:
        return normalize_analysis_file_extension(filename)
    except HTTPException as exc:
        raise HTTPException(status_code=415, detail=str(exc.detail))


def validate_analysis_file_content(path: str, file_ext: str) -> None:
    """校验原始文件内容与扩展名一致。"""
    ext = str(file_ext or "").strip().lower()
    if ext == "pdf":
        validate_pdf_file_content(path)
        return
    if ext in ANALYSIS_ALLOWED_IMAGE_EXTENSIONS:
        validate_image_file_content(path, ext)
        return
    raise HTTPException(status_code=400, detail="仅支持 PDF/PNG/JPG/JPEG 文件")


def validate_pdf_file_content(path: str) -> None:
    """校验文件内容是否为 PDF。"""
    try:
        with open(path, "rb") as handle:
            header = handle.read(5)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"读取文件失败: {exc}")
    if header != b"%PDF-":
        raise HTTPException(status_code=400, detail="下载文件不是有效的 PDF")


def validate_image_file_content(path: str, file_ext: str) -> None:
    """校验文件内容是否为可读取图片。"""
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            image.load()
            fmt = str(image.format or "").strip().upper()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"下载文件不是有效的图片: {exc}")
    if file_ext == "png" and fmt != "PNG":
        raise HTTPException(status_code=400, detail="下载文件与 PNG 扩展名不匹配")
    if file_ext in {"jpg", "jpeg"} and fmt != "JPEG":
        raise HTTPException(status_code=400, detail="下载文件与 JPG/JPEG 扩展名不匹配")


def extract_usable_ocr_text(ocr_result: dict[str, Any] | None) -> str:
    """提取可用于后续摘要的 OCR 文本，跳过错误提示。"""
    text = str((ocr_result or {}).get("text_content") or "").strip()
    if not text:
        return ""
    failure_prefixes = ("OCR识别失败：", "错误：文件不存在")
    if text.startswith(failure_prefixes):
        return ""
    return text


async def recognize_single_analysis_image(image_path: str, index: int, sem: asyncio.Semaphore, ocr_model: str | None = None) -> dict[str, Any]:
    """限并发执行单张举报分析图片 OCR。"""
    async with sem:
        ocr_result = await recognize_image_async(image_path, force=False, model=ocr_model)
    return {"index": index, "image_path": image_path, "ocr_result": ocr_result}


async def recognize_analysis_images(image_paths: list[str], base_id: str, ocr_model: str | None = None) -> list[str]:
    """按 text_pipeline 的并发限制执行 OCR，并保持页序稳定。"""
    if not image_paths:
        return []
    sem = asyncio.Semaphore(min(len(image_paths), _ocr_max_concurrency()))
    tasks = [asyncio.create_task(recognize_single_analysis_image(image_path, index, sem, ocr_model)) for index, image_path in enumerate(image_paths)]
    collected: list[tuple[int, str]] = []
    for task in asyncio.as_completed(tasks):
        result = await task
        image_path = str(result.get("image_path") or "")
        ocr_result = result.get("ocr_result") if isinstance(result.get("ocr_result"), dict) else {}
        text = extract_usable_ocr_text(ocr_result)
        if text:
            collected.append((int(result.get("index") or 0), text))
            continue
        raw_text = str((ocr_result or {}).get("text_content") or "").strip()
        if raw_text:
            logger.warning("[complaint_api] skip invalid ocr text base_id=%s image=%s reason=%s", base_id, os.path.basename(image_path), log_preview_text(raw_text))
    collected.sort(key=lambda item: item[0])
    return [text for _, text in collected if text]


def get_debug_pdf_path() -> str:
    """读取开发模式本地 PDF 覆盖路径。"""
    return str(os.getenv("COMPLAINT_API_DEBUG_PDF_PATH") or "").strip()


async def write_text_file(path: str, content: str) -> None:
    """异步写入文本文件。"""
    await run_in_threadpool(_write_text_file_sync, path, content)


async def write_json_file(path: str, payload: dict[str, Any]) -> None:
    """异步写入 JSON 文件。"""
    await run_in_threadpool(_write_json_file_sync, path, payload)


def find_existing_original_file(user_id: str, base_id: str, file_id: str = "", scope_context: ComplaintScopeContext | None = None) -> str | None:
    """查找已存在的原始文件。"""
    original_dir = stage_dir(user_id, base_id, "original", scope_context=scope_context)
    if not os.path.isdir(original_dir):
        return None
    if str(file_id or "").strip():
        safe_file_id = sanitize_file_id(file_id)
        file_ext = normalize_analysis_file_extension(safe_file_id)
        target_path = build_download_target_path(user_id, base_id, safe_file_id, file_ext, scope_context=scope_context)
        if os.path.isfile(target_path):
            return target_path
        return None
    candidates = sorted(
        [
            os.path.join(original_dir, name)
            for name in os.listdir(original_dir)
            if os.path.isfile(os.path.join(original_dir, name))
            and name.rsplit(".", 1)[-1].lower() in ANALYSIS_ALLOWED_FILE_EXTENSIONS
        ]
    )
    return candidates[-1] if candidates else None


async def resolve_pdf_file(file_id: str, user_id: str, base_id: str, force: bool = False, scope_context: ComplaintScopeContext | None = None) -> str:
    """解析并下载举报分析原始文件。"""
    if not force:
        existing = find_existing_original_file(user_id, base_id, file_id, scope_context=scope_context)
        if existing and os.path.isfile(existing):
            return existing

    debug_pdf_path = get_debug_pdf_path()
    if debug_pdf_path:
        logger.info("[complaint_api] use debug pdf path=%s file_id=%s", log_preview_text(debug_pdf_path), log_preview_text(file_id))
        if not os.path.isfile(debug_pdf_path):
            raise HTTPException(status_code=404, detail=f"本地调试 PDF 不存在: {debug_pdf_path}")
        file_ext = ensure_analysis_file_path(debug_pdf_path)
        target_path = build_original_target_path(user_id, base_id, os.path.basename(debug_pdf_path), file_ext, scope_context=scope_context)
        await run_in_threadpool(_copy_file_sync, debug_pdf_path, target_path)
        ensure_analysis_file_path(target_path)
        await run_in_threadpool(validate_analysis_file_content, target_path, file_ext)
        return target_path

    safe_file_id = sanitize_file_id(file_id)
    file_ext = normalize_analysis_file_extension(safe_file_id)
    target_path = build_download_target_path(user_id, base_id, safe_file_id, file_ext, scope_context=scope_context)
    api_url = os.getenv("FILE_DOWNLOAD_API_URL", "http://localhost:8000/download")
    api_user = os.getenv("FILE_DOWNLOAD_USER", "user")
    api_token = os.getenv("FILE_DOWNLOAD_TOKEN", "123456")
    api_cluster = os.getenv("FILE_DOWNLOAD_CLUSTER", "12")

    params = {"filePath": file_id, "user": api_user, "clusterList": api_cluster, "token": api_token}
    logger.info("[complaint_api] downloading file file_id=%s url=%s", log_preview_text(file_id), log_preview_text(api_url))

    try:
        async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
            last_exc: Exception | None = None
            for _ in range(2):
                try:
                    response = await client.get(api_url, params=params)
                    response.raise_for_status()
                    os.makedirs(os.path.dirname(target_path), exist_ok=True)
                    await run_in_threadpool(_write_binary_file_sync, target_path, response.content)
                    ensure_analysis_file_path(target_path)
                    await run_in_threadpool(validate_analysis_file_content, target_path, file_ext)
                    return target_path
                except Exception as exc:
                    last_exc = exc
            raise last_exc or RuntimeError("download failed")
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[complaint_api] download failed file_id=%s err=%s", log_preview_text(file_id), exc)
        raise HTTPException(status_code=404, detail=f"无法下载或定位 file_id 对应文件: {exc}")


async def resolve_review_report_file(review_report: str, user_id: str, base_id: str, scope_context: ComplaintScopeContext | None = None) -> str:
    """下载调查报告文件，失败时抛出异常供上层降级。"""
    safe_file_id = sanitize_file_id(review_report)
    file_ext = normalize_review_report_extension(safe_file_id)
    target_path = build_review_report_target_path(user_id, base_id, safe_file_id, file_ext, scope_context=scope_context)
    if os.path.isfile(target_path):
        return target_path

    api_url = os.getenv("FILE_DOWNLOAD_API_URL", "http://localhost:8000/download")
    api_user = os.getenv("FILE_DOWNLOAD_USER", "user")
    api_token = os.getenv("FILE_DOWNLOAD_TOKEN", "123456")
    api_cluster = os.getenv("FILE_DOWNLOAD_CLUSTER", "12")
    params = {"filePath": review_report, "user": api_user, "clusterList": api_cluster, "token": api_token}
    logger.info("[complaint_api] downloading review_report file_id=%s url=%s", log_preview_text(review_report), log_preview_text(api_url))
    try:
        async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
            response = await client.get(api_url, params=params)
            response.raise_for_status()
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            await run_in_threadpool(_write_binary_file_sync, target_path, response.content)
        return target_path
    except Exception as exc:
        logger.error("[complaint_api] review_report download failed file_id=%s err=%s", log_preview_text(review_report), exc)
        raise


def read_review_report_text_sync(path: str) -> str:
    """读取调查报告 doc/docx 内容。"""
    ext = os.path.splitext(str(path or ""))[1].lower()
    if ext not in {".doc", ".docx"}:
        raise ValueError("review_report 仅支持 DOC/DOCX 文件")
    with tempfile.TemporaryDirectory(prefix="complaint_review_") as temp_dir:
        markdown_path, media_dir = convert_doc_to_markdown(path, temp_dir)
        content = replace_images_with_ocr(markdown_path, media_dir)
    return re.sub(r"\s+", "\n", str(content or "")).strip()


async def load_review_report_text(review_report: str, user_id: str, base_id: str, scope_context: ComplaintScopeContext | None = None) -> str:
    """下载并读取调查报告内容，失败时返回空字符串。"""
    value = str(review_report or "").strip()
    if not value:
        return ""
    try:
        path = await resolve_review_report_file(value, user_id, base_id, scope_context=scope_context)
        text = await run_in_threadpool(read_review_report_text_sync, path)
        logger.info("[complaint_api] review_report_loaded user_id=%s base_id=%s file_id=%s chars=%s", user_id, base_id, log_preview_text(value), len(text or ""))
        return text
    except Exception as exc:
        logger.error("[complaint_api] review_report_load_failed user_id=%s base_id=%s file_id=%s err=%s", user_id, base_id, log_preview_text(value), exc)
        return ""


async def save_uploaded_pdf(user_id: str, base_id: str, filename: str, content: bytes, scope_context: ComplaintScopeContext | None = None) -> str:
    """保存上传原始文件到 original 目录并返回路径。"""
    file_ext = ensure_analysis_filename(filename)
    if not isinstance(content, (bytes, bytearray)) or not content:
        raise HTTPException(status_code=400, detail="上传文件不能为空")
    safe_name = os.path.basename(str(filename or "").strip()) or f"{base_id}.{file_ext}"
    if "." not in safe_name:
        safe_name = f"{safe_name}.{file_ext}"
    path = os.path.join(stage_dir(user_id, base_id, "original", scope_context=scope_context), safe_name)
    await run_in_threadpool(_write_binary_file_sync, path, bytes(content))
    ensure_analysis_file_path(path)
    await run_in_threadpool(validate_analysis_file_content, path, file_ext)
    return path


def _write_binary_file_sync(path: str, content: bytes) -> None:
    """同步写入二进制文件。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(content)


def _copy_file_sync(source_path: str, target_path: str) -> None:
    """同步复制文件。"""
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    shutil.copyfile(source_path, target_path)


def _write_text_file_sync(path: str, content: str) -> None:
    """同步写入文本文件。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def _write_json_file_sync(path: str, payload: dict[str, Any]) -> None:
    """同步写入 JSON 文件。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)


def load_latest_text(user_id: str, base_id: str, scope_context: ComplaintScopeContext | None = None) -> tuple[str, int]:
    """读取最新文本版本。"""
    manifest = load_manifest(user_id, base_id, scope_context=scope_context)
    text_version = int(manifest.get("text", {}).get("current") or 0)
    if text_version <= 0:
        return "", 0
    text_path = os.path.join(stage_dir(user_id, base_id, "text", text_version, scope_context=scope_context), "final.txt")
    if not os.path.isfile(text_path):
        return "", 0
    try:
        with open(text_path, "r", encoding="utf-8") as handle:
            return handle.read(), text_version
    except Exception:
        return "", 0


async def save_reused_text_content(user_id: str, base_id: str, text_content: str, file_ids: list[str] | None = None, source_request_id: str = "", scope_context: ComplaintScopeContext | None = None) -> tuple[str, int]:
    """保存外部链路已提取文本，供举报分析复用。"""
    text = str(text_content or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="复用文本不能为空")
    cached_text, cached_version = load_latest_text(user_id, base_id, scope_context=scope_context)
    if cached_text.strip():
        return cached_text, cached_version

    manifest = load_manifest(user_id, base_id, scope_context=scope_context)
    text_version = next_version(manifest, "text")
    text_path = os.path.join(stage_dir(user_id, base_id, "text", text_version, scope_context=scope_context), "final.txt")
    await write_text_file(text_path, text)
    extra = {"task": 1, "reused_from": "word_fit_extract"}
    normalized_file_ids = normalize_file_ids(file_ids or [])
    if normalized_file_ids:
        extra["file_ids"] = normalized_file_ids
    if str(source_request_id or "").strip():
        extra["source_request_id"] = str(source_request_id or "").strip()
    write_meta(user_id, base_id, "text", text_version, source="word_fit_extract", extra=extra, scope_context=scope_context, updated_by=user_id)
    manifest = update_manifest(manifest, "text", text_version)
    save_manifest(user_id, base_id, manifest, scope_context=scope_context)
    return text, text_version


async def extract_text_and_save(user_id: str, base_id: str, original_path: str, force: bool = False, ocr_model: str | None = None, scope_context: ComplaintScopeContext | None = None) -> tuple[str, int]:
    """提取文本并保存 text 版本。"""
    if not force:
        cached_text, cached_version = load_latest_text(user_id, base_id, scope_context=scope_context)
        if cached_text.strip():
            return cached_text, cached_version

    full_text, extraction_engine = await extract_text_from_original_path_with_source(user_id, base_id, original_path, base_id, ocr_model=ocr_model, scope_context=scope_context)
    manifest = load_manifest(user_id, base_id, scope_context=scope_context)
    text_version = next_version(manifest, "text")
    text_path = os.path.join(stage_dir(user_id, base_id, "text", text_version, scope_context=scope_context), "final.txt")
    await write_text_file(text_path, full_text)
    write_meta(user_id, base_id, "text", text_version, source=text_source_for_extraction_engine(extraction_engine), extra={"task": 1, "extraction_engine": extraction_engine}, scope_context=scope_context, updated_by=user_id)
    manifest = update_manifest(manifest, "text", text_version)
    save_manifest(user_id, base_id, manifest, scope_context=scope_context)
    return full_text, text_version


async def extract_text_from_original_path(user_id: str, base_id: str, original_path: str, image_prefix: str, ocr_model: str | None = None, scope_context: ComplaintScopeContext | None = None) -> str:
    """从单个原始文件提取文本，不读写 text 版本缓存。"""
    text, _extraction_engine = await extract_text_from_original_path_with_source(user_id, base_id, original_path, image_prefix, ocr_model=ocr_model, scope_context=scope_context)
    return text


async def extract_text_from_original_path_with_source(user_id: str, base_id: str, original_path: str, image_prefix: str, ocr_model: str | None = None, scope_context: ComplaintScopeContext | None = None) -> tuple[str, str]:
    """从原始文件提取文本，并返回实际使用的提取引擎。"""
    try:
        full_text = await extract_text_from_original_path_with_wordfit(user_id, base_id, original_path, image_prefix, ocr_model=ocr_model, scope_context=scope_context)
        if full_text.strip():
            return full_text, "word_fit_extract"
    except Exception as exc:
        logger.error("[complaint_api] wordfit_extract_text_failed user_id=%s base_id=%s scope=%s org_id=%s entity_id=%s file=%s err=%s", user_id, base_id, scope_context.scope if scope_context else "", scope_context.org_id if scope_context else "", scope_context.entity_id if scope_context else "", original_path, exc)
    return await extract_text_from_original_path_legacy(user_id, base_id, original_path, image_prefix, ocr_model=ocr_model, scope_context=scope_context), "complaint_legacy"


def text_source_for_extraction_engine(extraction_engine: str) -> str:
    """根据实际文本提取引擎生成版本来源。"""
    return "word_fit_extract" if extraction_engine == "word_fit_extract" else "auto"


async def extract_text_from_original_path_with_wordfit(user_id: str, base_id: str, original_path: str, image_prefix: str, ocr_model: str | None = None, scope_context: ComplaintScopeContext | None = None) -> str:
    """复用字段提取链路读取本地原始文件文本。"""
    from apps.word_fit_app.agent.nodes import build_file_content_from_path_for_extract

    file_ext = ensure_analysis_file_path(original_path)
    file_id = str(image_prefix or "").strip() or os.path.basename(original_path)
    state = {
        "messages": [],
        "file_ids": [file_id],
        "extract_fields": [],
        "locate_enabled": False,
        "analysis_scene": "complaint_analysis",
        "analysis_user_id": user_id,
        "analysis_match_strategy": "",
        "force": True,
        "extract_model": None,
        "ocr_model": ocr_model,
        "ocr_metrics_batch_id": "",
        "conversation_id": f"complaint-analysis-{base_id}",
        "base_id": base_id,
        "scope": scope_context.scope if scope_context else "",
        "org_id": scope_context.org_id if scope_context else "",
        "entity_id": scope_context.entity_id if scope_context else "",
        "scope_path": scope_context.scope_path if scope_context else "",
    }
    text = await build_file_content_from_path_for_extract(file_id, original_path, state)
    if is_wordfit_extract_failure_text(text):
        raise RuntimeError(text)
    full_text = str(text or "").strip()
    if not full_text:
        raise HTTPException(status_code=500, detail="文件文本提取失败")
    logger.info("[complaint_api] wordfit_extract_text_done user_id=%s base_id=%s scope=%s org_id=%s entity_id=%s file=%s file_ext=%s chars=%s", user_id, base_id, scope_context.scope if scope_context else "", scope_context.org_id if scope_context else "", scope_context.entity_id if scope_context else "", original_path, file_ext, len(full_text))
    return full_text


def is_wordfit_extract_failure_text(text: str) -> bool:
    """判断字段提取读取结果是否为失败占位文本。"""
    value = str(text or "").strip()
    return value.startswith("OCR failed:") or value.startswith("Doc read failed:") or value.startswith("File not found:") or value == "File path not resolved or file not found."


async def extract_text_from_original_path_legacy(user_id: str, base_id: str, original_path: str, image_prefix: str, ocr_model: str | None = None, scope_context: ComplaintScopeContext | None = None) -> str:
    """使用举报分析原有图片切分与 OCR 逻辑提取文本。"""
    file_ext = ensure_analysis_file_path(original_path)
    if file_ext == "pdf":
        image_paths = await run_in_threadpool(process_pdf, original_path, user_id, base_id, image_prefix, scope_context)
    else:
        image_paths = await run_in_threadpool(process_image, original_path, user_id, base_id, file_ext, image_prefix, scope_context)
    text_parts = await recognize_analysis_images(image_paths, base_id, ocr_model=ocr_model)
    full_text = "\n\n".join(text_parts).strip()
    if not full_text:
        raise HTTPException(status_code=500, detail="文件文本提取失败")
    return full_text


async def extract_texts_and_save(user_id: str, base_id: str, original_files: list[tuple[str, str]], force: bool = False, ocr_model: str | None = None, scope_context: ComplaintScopeContext | None = None) -> tuple[str, int]:
    """提取多个原始文件文本，按材料边界合并后保存 text 版本。"""
    if len(original_files) == 1:
        return await extract_text_and_save(user_id, base_id, original_files[0][1], force=force, ocr_model=ocr_model, scope_context=scope_context)
    manifest = load_manifest(user_id, base_id, scope_context=scope_context)
    if not force:
        cached_text, cached_version = load_latest_text(user_id, base_id, scope_context=scope_context)
        if cached_text.strip():
            return cached_text, cached_version

    merged_parts: list[str] = []
    extraction_engines: list[str] = []
    for index, (file_id, original_path) in enumerate(original_files, start=1):
        prefix_hash = hashlib.sha1(f"{index}:{file_id}".encode("utf-8")).hexdigest()[:8]
        image_prefix = f"{base_id}_{index}_{prefix_hash}"
        text, extraction_engine = await extract_text_from_original_path_with_source(user_id, base_id, original_path, image_prefix, ocr_model=ocr_model, scope_context=scope_context)
        extraction_engines.append(extraction_engine)
        merged_parts.append(f"【材料{index}：{file_id}】\n{text}")

    full_text = "\n\n".join(merged_parts).strip()
    if not full_text:
        raise HTTPException(status_code=500, detail="文件文本提取失败")

    text_version = next_version(manifest, "text")
    text_path = os.path.join(stage_dir(user_id, base_id, "text", text_version, scope_context=scope_context), "final.txt")
    await write_text_file(text_path, full_text)
    extraction_engine = "word_fit_extract" if extraction_engines and all(item == "word_fit_extract" for item in extraction_engines) else "mixed"
    write_meta(user_id, base_id, "text", text_version, source=text_source_for_extraction_engine(extraction_engine), extra={"task": 1, "file_ids": [item[0] for item in original_files], "extraction_engine": extraction_engine, "extraction_engines": extraction_engines}, scope_context=scope_context, updated_by=user_id)
    manifest = update_manifest(manifest, "text", text_version)
    save_manifest(user_id, base_id, manifest, scope_context=scope_context)
    return full_text, text_version


def load_latest_summary(user_id: str, base_id: str, scope_context: ComplaintScopeContext | None = None) -> tuple[str, int]:
    """读取最新摘要版本。"""
    manifest = load_manifest(user_id, base_id, scope_context=scope_context)
    summary_version = int(manifest.get("summary", {}).get("current") or 0)
    if summary_version <= 0:
        return "", 0
    summary_path = os.path.join(stage_dir(user_id, base_id, "summary", summary_version, scope_context=scope_context), "final.md")
    if not os.path.isfile(summary_path):
        return "", 0
    try:
        with open(summary_path, "r", encoding="utf-8") as handle:
            return handle.read(), summary_version
    except Exception:
        return "", 0


def load_latest_summary_bundle(user_id: str, base_id: str, scope_context: ComplaintScopeContext | None = None) -> tuple[dict[str, Any], int]:
    """读取最新摘要双载荷。"""
    manifest = load_manifest(user_id, base_id, scope_context=scope_context)
    summary_version = int(manifest.get("summary", {}).get("current") or 0)
    if summary_version <= 0:
        return {}, 0
    summary_path = os.path.join(stage_dir(user_id, base_id, "summary", summary_version, scope_context=scope_context), "final.json")
    if not os.path.isfile(summary_path):
        return {}, 0
    try:
        with open(summary_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}, summary_version
    except Exception:
        return {}, 0


def extract_structured_payload_from_summary_bundle(bundle: dict[str, Any] | None) -> dict[str, Any]:
    """从摘要双载荷中提取结构化字段。"""
    if not isinstance(bundle, dict):
        return normalize_summary_structured_payload({})
    return normalize_summary_structured_payload(bundle.get("structured_payload"))


def has_meaningful_summary_structured_payload(value: dict[str, Any] | None) -> bool:
    """判断摘要结构化字段是否可用于后续分析。"""
    payload = normalize_summary_structured_payload(value)
    if payload.get("complaints"):
        return True
    if str(payload.get("incident") or "").strip():
        return True
    profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
    return any(str(profile.get(key) or "").strip() for key in ("reporter", "report_date", "reported_institution", "title_base")) or bool(profile.get("appeals"))


def build_summary_text_from_structured_payload(value: dict[str, Any] | None) -> str:
    """将结构化摘要转成事项分析可读文本。"""
    payload = normalize_summary_structured_payload(value)
    lines: list[str] = []
    incident = str(payload.get("incident") or "").strip()
    if incident:
        lines.append(f"事情经过：{incident}")
    profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
    profile_parts = []
    for label, key in (("举报人", "reporter"), ("举报日期", "report_date"), ("被举报机构", "reported_institution"), ("标题", "title_base")):
        text = str(profile.get(key) or "").strip()
        if text:
            profile_parts.append(f"{label}：{text}")
    appeals = [str(item or "").strip() for item in profile.get("appeals") or [] if str(item or "").strip()] if isinstance(profile.get("appeals"), list) else []
    if appeals:
        profile_parts.append("诉求：" + "；".join(appeals))
    if profile_parts:
        lines.append("基本信息：" + "；".join(profile_parts))
    complaints = payload.get("complaints") if isinstance(payload.get("complaints"), list) else []
    for index, item in enumerate(complaints, start=1):
        if not isinstance(item, dict):
            continue
        parts = [str(item.get("label") or "").strip(), str(item.get("description") or "").strip(), str(item.get("claim_text") or "").strip()]
        quote_items = [str(value or "").strip() for value in item.get("original_quotes") or [] if str(value or "").strip()] if isinstance(item.get("original_quotes"), list) else []
        evidence_items = [str(value or "").strip() for value in item.get("evidence_materials") or [] if str(value or "").strip()] if isinstance(item.get("evidence_materials"), list) else []
        if quote_items:
            parts.append("原文摘录：" + "；".join(quote_items[:5]))
        if evidence_items:
            parts.append("证据材料：" + "；".join(evidence_items[:5]))
        text = "；".join(part for part in parts if part)
        if text:
            lines.append(f"举报事项{index}：{text}")
    return "\n".join(lines).strip()


def build_supplement_material_context(text_content: str) -> dict[str, Any]:
    """从原文提取补充材料信息供答复意见书使用。"""
    text = str(text_content or "")
    keyword_pattern = re.compile(r"(?:二次|多次|再次|补充|追加)?补充材料|二次材料|多次材料")
    date_pattern = re.compile(r"(?:(?:20)?\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?|\d{1,2}月\d{1,2}日)")
    matches = list(keyword_pattern.finditer(text))
    dates: list[str] = []
    evidence_spans: list[dict[str, str]] = []
    for match in matches:
        start = max(match.start() - 80, 0)
        end = min(match.end() + 80, len(text))
        span = re.sub(r"\s+", " ", text[start:end]).strip()
        sentence_start = max(text.rfind("。", 0, match.start()), text.rfind("；", 0, match.start()), text.rfind("\n", 0, match.start())) + 1
        sentence_end_candidates = [idx for idx in [text.find("。", match.end()), text.find("；", match.end()), text.find("\n", match.end())] if idx >= 0]
        sentence_end = min(sentence_end_candidates) if sentence_end_candidates else min(match.end() + 80, len(text))
        sentence_text = text[sentence_start:sentence_end]
        span_dates = date_pattern.findall(sentence_text)
        for value in span_dates:
            date_text = str(value or "").strip()
            if date_text and date_text not in dates:
                dates.append(date_text)
        evidence_spans.append({"text": span, "date": span_dates[0] if span_dates else "", "keyword": match.group(0)})
    exists = bool(matches)
    sentence = build_supplement_material_sentence(exists, dates)
    return {"supplement_material": {"exists": exists, "count_hint": len(matches), "dates": dates, "sentence": sentence, "evidence_spans": evidence_spans[:5]}}


def build_supplement_material_sentence(exists: bool, dates: list[str]) -> str:
    """生成可直接拼接到收信句后的补充材料话术。"""
    clean_dates = [str(value or "").strip() for value in dates or [] if str(value or "").strip()]
    if clean_dates:
        if len(clean_dates) == 1:
            return f"，于{clean_dates[0]}收到你的补充材料"
        return f"，分别于{'、'.join(clean_dates)}收到你的补充材料"
    if exists:
        return "，并收到你的补充材料"
    return ""


def merge_reply_draft_context(structured_payload: dict[str, Any], text_content: str) -> dict[str, Any]:
    """将答复意见书上下文写入摘要结构化载荷。"""
    payload = normalize_summary_structured_payload(structured_payload)
    payload["reply_draft_context"] = build_supplement_material_context(text_content)
    return payload


def extract_supplement_material_sentence_from_summary(user_id: str, base_id: str, scope_context: ComplaintScopeContext | None = None) -> str:
    """从最新摘要载荷读取补充材料话术。"""
    summary_bundle, _summary_version = load_latest_summary_bundle(user_id, base_id, scope_context=scope_context)
    payload = extract_structured_payload_from_summary_bundle(summary_bundle)
    reply_context = payload.get("reply_draft_context") if isinstance(payload.get("reply_draft_context"), dict) else {}
    supplement = reply_context.get("supplement_material") if isinstance(reply_context.get("supplement_material"), dict) else {}
    return str(supplement.get("sentence") or "").strip()


def normalize_reported_entities(values: Any) -> list[str]:
    """规范化被举报机构或人员列表。"""
    if values is None:
        return []
    if isinstance(values, str):
        raw_items = re.split(r"[,，;；、\n]+", values)
    elif isinstance(values, list):
        raw_items = []
        for item in values:
            if isinstance(item, dict):
                raw_items.append(str(item.get("name") or item.get("title") or item.get("label") or "").strip())
            else:
                raw_items.append(str(item or "").strip())
    else:
        raw_items = [str(values or "").strip()]
    result: list[str] = []
    for item in raw_items:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _text_matches_entity(text: str, entity: str) -> bool:
    """判断文本是否明确提及被举报机构或人员。"""
    content = re.sub(r"\s+", "", str(text or ""))
    name = re.sub(r"\s+", "", str(entity or ""))
    return bool(name and name in content)


def _assign_entities(text: str, entities: list[str]) -> list[str]:
    """按文本提及情况归属主体，无法区分时归属全部主体。"""
    clean_entities = normalize_reported_entities(entities)
    if not clean_entities:
        return []
    matched = [entity for entity in clean_entities if _text_matches_entity(text, entity)]
    return matched or clean_entities


async def generate_summary_and_save(user_id: str, base_id: str, text_content: str, text_version: int, force: bool = False, reported_entities: list[str] | None = None, scope_context: ComplaintScopeContext | None = None) -> tuple[str, int]:
    """生成结构化摘要并保存 summary 版本。"""
    if not force:
        cached_summary, cached_version = load_latest_summary(user_id, base_id, scope_context=scope_context)
        if cached_summary.strip():
            cached_bundle, bundle_version = load_latest_summary_bundle(user_id, base_id, scope_context=scope_context)
            raw_payload = cached_bundle.get("structured_payload") if isinstance(cached_bundle.get("structured_payload"), dict) else {}
            if bundle_version == cached_version and "reply_draft_context" not in raw_payload:
                structured_payload = merge_reply_draft_context(raw_payload, text_content)
                summary_dir = stage_dir(user_id, base_id, "summary", bundle_version, scope_context=scope_context)
                await write_json_file(os.path.join(summary_dir, "final.json"), {**cached_bundle, "structured_payload": structured_payload})
            return cached_summary, cached_version
        cached_bundle, bundle_version = load_latest_summary_bundle(user_id, base_id, scope_context=scope_context)
        if bundle_version > 0 and has_meaningful_summary_structured_payload(extract_structured_payload_from_summary_bundle(cached_bundle)):
            raw_payload = cached_bundle.get("structured_payload") if isinstance(cached_bundle.get("structured_payload"), dict) else {}
            if "reply_draft_context" not in raw_payload:
                structured_payload = merge_reply_draft_context(raw_payload, text_content)
                summary_dir = stage_dir(user_id, base_id, "summary", bundle_version, scope_context=scope_context)
                await write_json_file(os.path.join(summary_dir, "final.json"), {**cached_bundle, "structured_payload": structured_payload})
            return cached_summary, bundle_version

    with llm_prompt_metrics_context(request_id=base_id, user_id=user_id, base_id=base_id, business_stage="summary_generation"):
        entity_hint = normalize_reported_entities(reported_entities)
        prompt_text = text_content
        if entity_hint:
            prompt_text = "本次请求传入的被举报机构或人员列表如下，请按这些主体组织摘要、举报事项和诉求；无法明确归属的事项或诉求应归属全部主体：\n" + "\n".join(f"- {item}" for item in entity_hint) + "\n\n举报材料原文：\n" + str(text_content or "")
        structured_payload, _summary_thought = await generate_summary_structured_payload(prompt_text, enable_thinking=False, user_id=user_id, base_id=base_id)
    structured_payload = merge_reply_draft_context(structured_payload, text_content)
    if not has_meaningful_summary_structured_payload(structured_payload):
        raise HTTPException(status_code=500, detail="摘要结构化提取失败")
    summary_markdown = build_summary_text_from_structured_payload(structured_payload)
    logger.info("generate_summary_and_save user_id=%s base_id=%s structured_payload=%s", user_id, base_id, log_preview_json(structured_payload))

    manifest = load_manifest(user_id, base_id, scope_context=scope_context)
    summary_version = next_version(manifest, "summary")
    summary_dir = stage_dir(user_id, base_id, "summary", summary_version, scope_context=scope_context)
    await write_text_file(os.path.join(summary_dir, "final.md"), summary_markdown)
    await write_json_file(os.path.join(summary_dir, "final.json"), {"_schema": "v2", "structured_payload": structured_payload})
    write_meta(user_id, base_id, "summary", summary_version, source="auto", base_versions={"base_document_version": text_version}, extra={"task": 2}, scope_context=scope_context, updated_by=user_id)
    manifest = update_manifest(manifest, "summary", summary_version)
    save_manifest(user_id, base_id, manifest, scope_context=scope_context)
    return summary_markdown, summary_version


def load_latest_issue_analysis(user_id: str, base_id: str, scope_context: ComplaintScopeContext | None = None) -> tuple[dict[str, Any] | None, int]:
    """读取最新举报事项分析结果。"""
    if scope_context is None:
        manifest = load_manifest(user_id, base_id)
        issue_version = int(manifest.get("issue_analysis", {}).get("current") or 0)
        if issue_version <= 0:
            return None, 0
        issue_path = os.path.join(stage_dir(user_id, base_id, "issue_analysis", issue_version), "final.json")
        if not os.path.isfile(issue_path):
            return None, 0
        try:
            with open(issue_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                return data, issue_version
        except Exception as exc:
            logger.error("[complaint_api] load_latest_issue_analysis_failed user_id=%s base_id=%s version=%s err=%s", user_id, base_id, issue_version, exc)
        return None, 0
    for read_scope in complaint_scope_read_chain(scope_context):
        manifest = load_manifest(user_id, base_id, scope_context=read_scope)
        issue_version = int(manifest.get("issue_analysis", {}).get("current") or 0)
        if issue_version <= 0:
            continue
        issue_path = os.path.join(stage_dir(user_id, base_id, "issue_analysis", issue_version, scope_context=read_scope), "final.json")
        if not os.path.isfile(issue_path):
            continue
        try:
            with open(issue_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                data.setdefault("scope_context", read_scope.to_meta())
                return data, issue_version
        except Exception as exc:
            logger.error("[complaint_api] load_latest_issue_analysis_failed user_id=%s base_id=%s scope=%s version=%s err=%s", user_id, base_id, read_scope.scope_path, issue_version, exc)
    return None, 0


def build_running_issue_analysis_item(user_id: str, base_id: str, summary_version: int, structured_payload: dict[str, Any], scope_context: ComplaintScopeContext | None = None) -> dict[str, Any]:
    """用摘要阶段产物构造未完成的举报事项分析列表项。"""
    payload = normalize_summary_structured_payload(structured_payload)
    profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
    complaints = payload.get("complaints") if isinstance(payload.get("complaints"), list) else []
    incident = str(payload.get("incident") or "").strip()

    complaint_items: list[dict[str, Any]] = []
    for idx, item in enumerate(complaints, start=1):
        if isinstance(item, dict):
            text = str(item.get("description") or item.get("label") or "").strip()
            label = str(item.get("label") or f"事项{idx}").strip()
        else:
            text = str(item or "").strip()
            label = f"事项{idx}"
        if not text:
            continue
        detail: dict[str, Any] = {"id": f"complaint_{idx}", "label": label, "content": text}
        if isinstance(item, dict):
            detail.update({
                "claim_text": str(item.get("claim_text") or text).strip(),
                "original_quotes": [str(value).strip() for value in item.get("original_quotes") or [] if str(value).strip()],
                "evidence_materials": [str(value).strip() for value in item.get("evidence_materials") or [] if str(value).strip()],
                "user_cited_laws": item.get("user_cited_laws") if isinstance(item.get("user_cited_laws"), list) else [],
            })
        complaint_items.append(detail)

    appeals = [{"id": f"appeal_{idx}", "content": str(text).strip()} for idx, text in enumerate(profile.get("appeals") or [], start=1) if str(text).strip()]
    manifest = load_manifest(user_id, base_id, scope_context=scope_context)
    return {
        "api_kind": "complaint_analysis",
        "base_id": base_id,
        "status": "running",
        "issue_analysis_version": 0,
        "created_at": "",
        "incident": incident,
        "complaint_profile": {
            "reporter": str(profile.get("reporter") or ""),
            "report_date": str(profile.get("report_date") or ""),
            "reported_institution": str(profile.get("reported_institution") or ""),
            "title_base": str(profile.get("title_base") or ""),
        },
        "items_and_appeals": {
            "complaint_items": complaint_items,
            "appeals": appeals,
        },
        "law_basis": [],
        "document_checklist": [],
        "verification_points": [],
        "conclusion_text": "",
        "versions": {
            "text_version": int(manifest.get("text", {}).get("current") or 0),
            "summary_version": int(summary_version or 0),
            "analysis_version": 0,
            "classification_version": int(manifest.get("classification", {}).get("current") or 0),
        },
    }


def load_law_rationality_list_payload(user_id: str, base_id: str, issue_payload: dict[str, Any], issue_version: int, scope_context: ComplaintScopeContext | None = None) -> dict[str, Any]:
    """读取列表接口需要展示的合理性分析结果。"""
    versions = issue_payload.get("versions") if isinstance(issue_payload.get("versions"), dict) else {}
    try:
        law_rationality_version = int(versions.get("law_rationality_version") or 0)
    except Exception:
        law_rationality_version = 0
    if law_rationality_version <= 0:
        return {"law_rationality_version": 0, "items": []}
    final_path = os.path.join(stage_dir(user_id, base_id, "law_rationality", law_rationality_version, scope_context=scope_context), "final.json")
    if not os.path.isfile(final_path):
        logger.error("[complaint_api] law_rationality_missing user_id=%s base_id=%s issue_version=%s law_rationality_version=%s path=%s", user_id, base_id, issue_version, law_rationality_version, final_path)
        return {"law_rationality_version": law_rationality_version, "items": []}
    try:
        with open(final_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception as exc:
        logger.error("[complaint_api] law_rationality_read_failed user_id=%s base_id=%s issue_version=%s law_rationality_version=%s err=%s", user_id, base_id, issue_version, law_rationality_version, exc)
        return {"law_rationality_version": law_rationality_version, "items": []}
    source_items = payload.get("items") if isinstance(payload, dict) and isinstance(payload.get("items"), list) else []
    return {"law_rationality_version": law_rationality_version, "items": [build_law_rationality_phase_result(item if isinstance(item, dict) else {}) for item in source_items]}


def attach_law_rationality_to_complaint_items(issue_payload: dict[str, Any], law_rationality: dict[str, Any]) -> dict[str, Any]:
    """把合理性结论补到列表查询的举报事项上。"""
    items_and_appeals = issue_payload.get("items_and_appeals") if isinstance(issue_payload.get("items_and_appeals"), dict) else {}
    complaint_items = items_and_appeals.get("complaint_items") if isinstance(items_and_appeals.get("complaint_items"), list) else []
    rationality_items = law_rationality.get("items") if isinstance(law_rationality.get("items"), list) else []
    rationality_by_id = {str(item.get("complaint_id") or "").strip(): item for item in rationality_items if isinstance(item, dict)}
    rationality_by_index = {int(item.get("index") or 0): item for item in rationality_items if isinstance(item, dict)}
    for idx, complaint in enumerate(complaint_items):
        if not isinstance(complaint, dict):
            continue
        complaint_id = str(complaint.get("id") or f"complaint_{idx + 1}").strip()
        rationality_item = rationality_by_id.get(complaint_id) or rationality_by_index.get(idx)
        if not isinstance(rationality_item, dict):
            continue
        complaint["reasonable_status"] = str(rationality_item.get("reasonable_status") or "").strip()
    return issue_payload


def load_issue_analysis_list(user_id: str, base_id: str, page: int = 1, page_size: int = 50, scope_context: ComplaintScopeContext | None = None) -> dict[str, Any]:
    """按版本倒序读取指定举报件的历史举报事项分析结果。"""
    if scope_context is None:
        manifest = load_manifest(user_id, base_id)
        current_version = int(manifest.get("issue_analysis", {}).get("current") or 0)
        summary_bundle, summary_version = load_latest_summary_bundle(user_id, base_id)
        structured_payload = extract_structured_payload_from_summary_bundle(summary_bundle)
        page = max(int(page or 1), 1)
        page_size = min(max(int(page_size or 50), 1), 100)
        records: list[dict[str, Any]] = []
        for version in range(current_version, 0, -1):
            issue_dir = stage_dir(user_id, base_id, "issue_analysis", version)
            issue_path = os.path.join(issue_dir, "final.json")
            if not os.path.isfile(issue_path):
                continue
            try:
                with open(issue_path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except Exception as exc:
                logger.error("[complaint_api] load_issue_analysis_list read_failed user_id=%s base_id=%s version=%s err=%s", user_id, base_id, version, exc)
                continue
            if not isinstance(data, dict):
                logger.error("[complaint_api] load_issue_analysis_list invalid_payload user_id=%s base_id=%s version=%s", user_id, base_id, version)
                continue
            item = dict(data)
            item["issue_analysis_version"] = version
            item["created_at"] = load_issue_analysis_created_at(issue_dir, issue_path)
            item["law_rationality"] = load_law_rationality_list_payload(user_id, base_id, item, version)
            item = attach_law_rationality_to_complaint_items(item, item["law_rationality"])
            records.append(item)
        latest_issue_summary_version = 0
        if records:
            versions = records[0].get("versions") if isinstance(records[0].get("versions"), dict) else {}
            latest_issue_summary_version = int(versions.get("summary_version") or 0)
        if summary_version > 0 and has_meaningful_summary_structured_payload(structured_payload) and summary_version > latest_issue_summary_version:
            records.insert(0, build_running_issue_analysis_item(user_id, base_id, summary_version, structured_payload))
        total = len(records)
        start = (page - 1) * page_size
        end = start + page_size
        return {"base_id": base_id, "items": records[start:end], "total": total, "page": page, "page_size": page_size}
    read_scope = complaint_scope_read_chain(scope_context)[0]
    for candidate_scope in complaint_scope_read_chain(scope_context):
        manifest = load_manifest(user_id, base_id, scope_context=candidate_scope)
        if int(manifest.get("issue_analysis", {}).get("current") or 0) > 0:
            read_scope = candidate_scope
            break
    manifest = load_manifest(user_id, base_id, scope_context=read_scope)
    current_version = int(manifest.get("issue_analysis", {}).get("current") or 0)
    summary_bundle, summary_version = load_latest_summary_bundle(user_id, base_id, scope_context=read_scope)
    structured_payload = extract_structured_payload_from_summary_bundle(summary_bundle)
    page = max(int(page or 1), 1)
    page_size = min(max(int(page_size or 50), 1), 100)
    records: list[dict[str, Any]] = []
    for version in range(current_version, 0, -1):
        issue_dir = stage_dir(user_id, base_id, "issue_analysis", version, scope_context=read_scope)
        issue_path = os.path.join(issue_dir, "final.json")
        if not os.path.isfile(issue_path):
            continue
        try:
            with open(issue_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception as exc:
            logger.error("[complaint_api] load_issue_analysis_list read_failed user_id=%s base_id=%s version=%s err=%s", user_id, base_id, version, exc)
            continue
        if not isinstance(data, dict):
            logger.error("[complaint_api] load_issue_analysis_list invalid_payload user_id=%s base_id=%s version=%s", user_id, base_id, version)
            continue
        item = dict(data)
        item["issue_analysis_version"] = version
        item["created_at"] = load_issue_analysis_created_at(issue_dir, issue_path)
        item.setdefault("scope_context", read_scope.to_meta())
        item["law_rationality"] = load_law_rationality_list_payload(user_id, base_id, item, version, scope_context=read_scope)
        item = attach_law_rationality_to_complaint_items(item, item["law_rationality"])
        records.append(item)
    latest_issue_summary_version = 0
    if records:
        versions = records[0].get("versions") if isinstance(records[0].get("versions"), dict) else {}
        latest_issue_summary_version = int(versions.get("summary_version") or 0)
    if summary_version > 0 and has_meaningful_summary_structured_payload(structured_payload) and summary_version > latest_issue_summary_version:
        records.insert(0, build_running_issue_analysis_item(user_id, base_id, summary_version, structured_payload, scope_context=read_scope))
    total = len(records)
    start = (page - 1) * page_size
    end = start + page_size
    return {"base_id": base_id, "scope_context": read_scope.to_meta(), "items": records[start:end], "total": total, "page": page, "page_size": page_size}


def load_complaint_analysis_v2_created_at(stage_dir_path: str, final_json_path: str) -> str:
    """读取 v2 分析版本创建时间，输出北京时间 YYYY-MM-DD HH:mm:ss。"""
    meta_path = os.path.join(stage_dir_path, "meta.json")
    if os.path.isfile(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as handle:
                meta = json.load(handle)
            if isinstance(meta, dict) and str(meta.get("ts") or "").strip():
                return format_list_created_at_display(str(meta.get("ts") or "").strip())
        except Exception as exc:
            logger.error("[complaint_api] load_complaint_analysis_v2_created_at read_meta_failed path=%s err=%s", meta_path, exc)
    try:
        return datetime.fromtimestamp(os.path.getmtime(final_json_path), ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
    except Exception as exc:
        logger.error("[complaint_api] load_complaint_analysis_v2_created_at mtime_failed path=%s err=%s", final_json_path, exc)
    return ""


def format_list_created_at_display(value: str) -> str:
    """将列表展示时间转为北京时间格式 YYYY-MM-DD HH:mm:ss。"""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        if text.endswith("Z"):
            dt = datetime.fromisoformat(text[:-1] + "+00:00")
        else:
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                return dt.strftime("%Y-%m-%d %H:%M:%S")
        return dt.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return text


def _load_json_payload(path: str) -> dict[str, Any]:
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_text_payload(path: str) -> str:
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except Exception:
        return ""


def build_complaint_analysis_v2_reference_content(analysis_payload: dict[str, Any]) -> list[dict[str, Any]]:
    """基于法规检索结果构造引用结构：每部法律对应多个条款。"""
    law_search = analysis_payload.get("law_search") if isinstance(analysis_payload.get("law_search"), dict) else {}
    articles = law_search.get("articles") if isinstance(law_search.get("articles"), list) else []
    grouped: list[dict[str, Any]] = []
    index_by_law: dict[str, int] = {}
    for item in articles:
        if not isinstance(item, dict):
            continue
        law_name = str(item.get("law_name") or "").strip()
        article = str(item.get("article") or "").strip()
        if not law_name:
            continue
        group_index = index_by_law.get(law_name)
        if group_index is None:
            grouped.append({"law_name": law_name, "articles": []})
            group_index = len(grouped) - 1
            index_by_law[law_name] = group_index
        if article and article not in grouped[group_index]["articles"]:
            grouped[group_index]["articles"].append(article)
    return grouped


def _extract_markdown_section(markdown: str, heading: str) -> str:
    """提取指定三级标题下、下一个三级标题前的正文。"""
    lines = str(markdown or "").splitlines()
    target = f"### {heading}".strip()
    start_index = -1
    for idx, line in enumerate(lines):
        if line.strip() == target:
            start_index = idx + 1
            break
    if start_index < 0:
        return ""
    collected: list[str] = []
    for line in lines[start_index:]:
        if line.strip().startswith("### "):
            break
        collected.append(line)
    return "\n".join(collected).strip()


def _extract_first_markdown_table(section_text: str) -> str:
    """提取段落中的第一张 Markdown 表格。"""
    table_lines: list[str] = []
    collecting = False
    for line in str(section_text or "").splitlines():
        if line.lstrip().startswith("|"):
            table_lines.append(line.rstrip())
            collecting = True
            continue
        if collecting:
            break
    return "\n".join([line for line in table_lines if line.strip()]).strip()


def build_complaint_analysis_v2_final_markdown(analysis_markdown: str) -> str:
    """从分析结果中提取 3.3 表格和 3.4 核实思路，拼成最终 Markdown。"""
    materials_section = _extract_markdown_section(analysis_markdown, "3.3 调阅材料清单汇总")
    materials_content = _extract_first_markdown_table(materials_section) or materials_section
    verification_content = _extract_markdown_section(analysis_markdown, "3.4 后续工作建议")
    parts: list[str] = []
    if materials_content:
        parts.append(f"### 调阅材料清单\n\n{materials_content.strip()}")
    if verification_content:
        parts.append(f"### 核实思路\n\n{verification_content.strip()}")
    return "\n\n".join([part for part in parts if part]).strip()


def load_complaint_analysis_v2_list(user_id: str, base_id: str, page: int = 1, page_size: int = 50, scope_context: ComplaintScopeContext | None = None) -> dict[str, Any]:
    """读取 complaint-analysis-v2 历史，按阶段 content 组织返回。"""
    read_scope = complaint_scope_read_chain(scope_context)[0] if scope_context is not None else None
    if scope_context is not None:
        for candidate_scope in complaint_scope_read_chain(scope_context):
            manifest = load_manifest(user_id, base_id, scope_context=candidate_scope)
            if int(manifest.get("complaint_analysis_v2", {}).get("current") or 0) > 0:
                read_scope = candidate_scope
                break
    manifest = load_manifest(user_id, base_id, scope_context=read_scope)
    current_version = int(manifest.get("complaint_analysis_v2", {}).get("current") or 0)
    page = max(int(page or 1), 1)
    page_size = min(max(int(page_size or 50), 1), 100)
    records: list[dict[str, Any]] = []
    for version in range(current_version, 0, -1):
        final_dir = stage_dir(user_id, base_id, "complaint_analysis_v2", version, scope_context=read_scope)
        final_json_path = os.path.join(final_dir, "final.json")
        if not os.path.isfile(final_json_path):
            continue
        final_payload = _load_json_payload(final_json_path)
        if not isinstance(final_payload, dict) or not final_payload:
            continue
        versions = final_payload.get("versions") if isinstance(final_payload.get("versions"), dict) else {}
        summary_version = int(versions.get("summary_v2_version") or 0)
        analysis_version = int(versions.get("analysis_v2_version") or 0)
        summary_payload = _load_json_payload(os.path.join(stage_dir(user_id, base_id, "summary_v2", summary_version, scope_context=read_scope), "final.json")) if summary_version > 0 else {}
        summary_md = _load_text_payload(os.path.join(stage_dir(user_id, base_id, "summary_v2", summary_version, scope_context=read_scope), "final.md")) if summary_version > 0 else ""
        analysis_payload = _load_json_payload(os.path.join(stage_dir(user_id, base_id, "analysis_v2", analysis_version, scope_context=read_scope), "final.json")) if analysis_version > 0 else {}
        analysis_md = _load_text_payload(os.path.join(stage_dir(user_id, base_id, "analysis_v2", analysis_version, scope_context=read_scope), "final.md")) if analysis_version > 0 else ""
        summary_content = str(summary_payload.get("summary_markdown") or summary_md or "").strip()
        analysis_content = str(analysis_payload.get("analysis_markdown") or analysis_md or "").strip()
        reference_content = build_complaint_analysis_v2_reference_content(analysis_payload)
        final_content = str(final_payload.get("final_markdown") or _load_text_payload(os.path.join(final_dir, "final.md")) or build_complaint_analysis_v2_final_markdown(analysis_content)).strip()
        item = {
            "base_id": base_id,
            "complaint_analysis_v2_version": version,
            "created_at": load_complaint_analysis_v2_created_at(final_dir, final_json_path),
            "summary": {"content": summary_content},
            "analysis": {"content": analysis_content},
            "reference": {"content": reference_content},
            "final": {"content": final_content},
            "versions": {
                "text_version": int(versions.get("text_version") or 0),
                "summary_v2_version": summary_version,
                "analysis_v2_version": analysis_version,
                "complaint_analysis_v2_version": version,
            },
        }
        records.append(item)
    total = len(records)
    start = (page - 1) * page_size
    end = start + page_size
    return {
        "base_id": base_id,
        "scope_context": read_scope.to_meta() if read_scope is not None else (scope_context.to_meta() if scope_context is not None else {}),
        "items": records[start:end],
        "total": total,
        "page": page,
        "page_size": page_size,
        "message": "查询成功" if total > 0 else "未找到举报分析V2历史",
        "exists": total > 0,
    }


def load_issue_analysis_created_at(issue_dir: str, issue_path: str) -> str:
    """读取分析版本创建时间，优先使用 meta.ts。"""
    meta_path = os.path.join(issue_dir, "meta.json")
    if os.path.isfile(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as handle:
                meta = json.load(handle)
            if isinstance(meta, dict) and str(meta.get("ts") or "").strip():
                return format_list_created_at(str(meta.get("ts") or "").strip())
        except Exception as exc:
            logger.error("[complaint_api] load_issue_analysis_created_at read_meta_failed path=%s err=%s", meta_path, exc)
    try:
        return datetime.fromtimestamp(os.path.getmtime(issue_path), ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%dT%H:%M:%S")
    except Exception as exc:
        logger.error("[complaint_api] load_issue_analysis_created_at mtime_failed path=%s err=%s", issue_path, exc)
    return ""


def format_list_created_at(value: str) -> str:
    """将列表展示时间转为北京时间格式，不带时区后缀。"""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        if text.endswith("Z"):
            dt = datetime.fromisoformat(text[:-1] + "+00:00")
        else:
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                return dt.strftime("%Y-%m-%dT%H:%M:%S")
        return dt.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%dT%H:%M:%S")
    except Exception:
        return text


def extract_first_json(raw: str) -> dict[str, Any]:
    """从文本中提取第一个 JSON 对象。"""
    text = str(raw or "").strip()
    if not text:
        return {}
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        fragment = text[start : end + 1]
        try:
            parsed = json.loads(fragment)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


async def extract_profile_and_appeals(summary_text: str) -> dict[str, Any]:
    """从摘要抽取举报画像与诉求信息。"""
    content = str(summary_text or "").strip()
    if not content:
        return {"reporter": "", "report_date": "", "reported_institution": "", "appeals": [], "title_base": ""}

    prompt = (
        "你是举报信息结构化助手。请从输入文本中抽取举报画像与诉求，并且只输出一个 JSON 对象。"
        "输出格式必须严格为："
        "{\"reporter\":\"\",\"report_date\":\"\",\"reported_institution\":\"\",\"appeals\":[\"\"],\"title_base\":\"\"}。"
        "抽取规则："
        "1. reporter：提取实名举报人姓名；优先识别“举报人”“本人姓名”“投诉人”“反映人”等字段后的姓名；不要输出“举报人”这类字段名。"
        "2. report_date：只提取明确的举报日期/来信日期/投诉日期；如果文本只有事件发生日期，没有明确举报日期，则返回空字符串。"
        "3. reported_institution：提取被举报机构全称，优先识别“举报”“反映”“投诉”的对象；不要输出“被举报机构”这类字段名。"
        "4. appeals：提取明确诉求，返回字符串数组；没有明确诉求则返回空数组。"
        "5. title_base：生成一个简短标题基础，如“王某举报某机构信用卡违规发卡问题”；没有足够信息可留空。"
        "6. 缺失字段返回空字符串，不要编造，不要把示例字段名当作结果输出。"
        "7. 不要输出 Markdown，不要输出解释，不要输出代码块，只返回 JSON 对象本身。"
    )
    try:
        client = get_async_openai_client()
        model = get_complaint_extract_items_model()
        request_messages = [{"role": "system", "content": prompt}, {"role": "user", "content": content}]
        logger.info("[complaint_api] extract_profile_and_appeals llm_input model=%s messages=%s", model, log_preview_json(request_messages))
        metric_stage = "complaint_reply_profile_extract"
        call_id = record_llm_prompt_metrics(metric_stage, model=model, messages=request_messages, metadata={"business_stage": "complaint_reply_draft"})
        started_at = time.monotonic()
        response = await client.chat.completions.create(
            model=model,
            messages=request_messages,
            temperature=0.1,
        )
        record_llm_output_metrics(metric_stage, model=model, output_text=extract_reply_message_text(response), llm_duration_ms=int((time.monotonic() - started_at) * 1000), usage=extract_usage_tokens(response), metadata={"business_stage": "complaint_reply_draft"}, call_id=call_id)
        choices = response.choices if hasattr(response, "choices") else []
        message = choices[0].message if choices else None
        raw = getattr(message, "content", "") if message else ""
        logger.info("[complaint_api] extract_profile_and_appeals llm_output_raw=%s", log_preview_text(raw))
        parsed = extract_first_json(raw)
        logger.info("[complaint_api] extract_profile_and_appeals llm_output_json=%s", log_preview_json(parsed))
        appeals = parsed.get("appeals") if isinstance(parsed.get("appeals"), list) else []
        result = {
            "reporter": str(parsed.get("reporter") or "").strip(),
            "report_date": str(parsed.get("report_date") or "").strip(),
            "reported_institution": str(parsed.get("reported_institution") or "").strip(),
            "appeals": [str(item).strip() for item in appeals if str(item).strip()],
            "title_base": str(parsed.get("title_base") or "").strip(),
        }
        logger.info("[complaint_api] extract_profile_and_appeals output=%s", log_preview_json(result))
        return result
    except Exception as exc:
        logger.warning("[complaint_api] extract profile/appeals failed: %s", exc)
        return {"reporter": "", "report_date": "", "reported_institution": "", "appeals": [], "title_base": ""}


def dedupe_list(items: list[str]) -> list[str]:
    """列表去重并保持顺序。"""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def limit_document_checklist_total_chars(checklist: list[dict[str, Any]], max_chars: int = 1000) -> list[dict[str, Any]]:
    """限制调阅材料清单 items 总字数。"""
    if max_chars <= 0:
        return []
    used = 0
    limited: list[dict[str, Any]] = []
    for group in checklist or []:
        if not isinstance(group, dict):
            continue
        items = group.get("items") if isinstance(group.get("items"), list) else []
        limited_items: list[str] = []
        for item in items:
            text = str(item or "").strip()
            if not text:
                continue
            remaining = max_chars - used
            if remaining <= 0:
                break
            if len(text) > remaining:
                text = text[:remaining].strip()
            if not text:
                continue
            limited_items.append(text)
            used += len(text)
        if limited_items:
            limited_group = dict(group)
            limited_group["items"] = limited_items
            limited.append(limited_group)
        if used >= max_chars:
            break
    return limited


def normalize_law_name(value: Any) -> str:
    """规范化最终输出的法律名称。"""
    text = str(value or "").strip()
    return re.sub(r"\.txt$", "", text, flags=re.IGNORECASE).strip()


def remove_law_line_reference(value: Any) -> str:
    """移除最终展示中的法规库行号定位信息。"""
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"[（(]\s*(?:约|大致)?第?\s*\d+\s*(?:[-~—－至到]\s*\d+\s*)?行\s*[）)]", "", text)
    text = re.sub(r"(?:约|大致)?第?\s*\d+\s*(?:[-~—－至到]\s*\d+\s*)?行", "", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_law_article(value: Any) -> str:
    """规范化法律条款名，过滤泛化条款。"""
    text = remove_law_line_reference(value)
    if not text:
        return ""
    compact = re.sub(r"\s+", "", text)
    if compact in {"相关条款", "相关规定", "相关法规", "相关法规条款", "相关法律条款", "相关法律法规", "相关法律法规条款"}:
        return ""
    if not re.search(r"第[一二三四五六七八九十百千万零〇\d]+[条款项目章节]", compact):
        return ""
    return text


def aggregate_law_and_checks(law_results: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """聚合法律依据、调阅清单、核实要点。"""
    law_map: dict[str, dict[str, str]] = {}
    checklist_map: dict[str, list[str]] = {}
    verification_map: dict[str, list[str]] = {}

    for row in law_results or []:
        categories = row.get("categories") if isinstance(row, dict) else []
        if not isinstance(categories, list):
            continue
        for category in categories:
            if not isinstance(category, dict):
                continue
            first_category = str(category.get("一级分类") or "其他").strip() or "其他"
            second_category = str(category.get("二级分类") or "").strip()
            if first_category == "本地法规分析" and second_category:
                category_name = second_category
            else:
                category_name = f"{first_category}/{second_category}" if second_category else first_category

            laws = category.get("法律法规及其原文") if isinstance(category.get("法律法规及其原文"), list) else []
            for law_item in laws:
                if not isinstance(law_item, dict):
                    continue
                law_name = normalize_law_name(remove_law_line_reference(law_item.get("law")))
                item = normalize_law_article(law_item.get("item") or law_item.get("article") or law_item.get("items") or law_item.get("条款") or law_item.get("time"))
                item_text = remove_law_line_reference(law_item.get("item_text"))
                if not law_name or not item:
                    continue
                if law_name not in law_map:
                    law_map[law_name] = {}
                law_map[law_name][item] = item_text

            checklist_items = category.get("调阅材料") if isinstance(category.get("调阅材料"), list) else []
            checklist_map.setdefault(category_name, [])
            checklist_map[category_name].extend([str(item).strip() for item in checklist_items if str(item).strip()])

            verification_items = category.get("核查思路") if isinstance(category.get("核查思路"), list) else []
            verification_map.setdefault(category_name, [])
            verification_map[category_name].extend([str(item).strip() for item in verification_items if str(item).strip()])

    law_basis: list[dict[str, Any]] = []
    for law_name, items in law_map.items():
        law_basis.append({"law_name": law_name, "articles": [{"item": k, "item_text": v} for k, v in items.items()]})

    checklist: list[dict[str, Any]] = []
    for category_name, items in checklist_map.items():
        checklist.append({"category": category_name, "items": dedupe_list(items), "source": "expert"})
    checklist = limit_document_checklist_total_chars(checklist, 1000)

    verification: list[dict[str, Any]] = []
    for category_name, items in verification_map.items():
        for text in dedupe_list(items):
            verification.append({"category": category_name, "point": text, "source": "expert"})

    return law_basis, checklist, verification


def law_skill_analysis_for_item(law_results: list[dict[str, Any]], index: int) -> dict[str, Any]:
    """读取单个举报事项的本地法规 skill 完整分析。"""
    if index < 0 or index >= len(law_results or []):
        return {}
    row = law_results[index] if isinstance(law_results[index], dict) else {}
    categories = row.get("categories") if isinstance(row.get("categories"), list) else []
    for category in categories:
        if not isinstance(category, dict):
            continue
        analysis = category.get("law_skill_analysis")
        if isinstance(analysis, dict) and analysis:
            return analysis
    return {}


def build_reported_entity_groups(entities: list[str], complaint_items: list[dict[str, Any]], appeals: list[dict[str, Any]], incident: str) -> list[dict[str, Any]]:
    """按被举报机构或人员组织摘要、举报事项和诉求。"""
    groups: list[dict[str, Any]] = []
    for entity in normalize_reported_entities(entities):
        entity_complaints = [item for item in complaint_items if entity in (item.get("reported_entities") or [])]
        entity_appeals = [item for item in appeals if entity in (item.get("reported_entities") or [])]
        summary_parts = [str(item.get("content") or item.get("claim_text") or item.get("label") or "").strip() for item in entity_complaints]
        summary = "；".join(part for part in summary_parts if part) or str(incident or "").strip()
        groups.append({"name": entity, "summary": summary, "complaint_items": entity_complaints, "appeals": entity_appeals})
    return groups


def build_extract_phase_result(complaints: list[dict[str, Any]], profile: dict[str, Any] | None = None, incident: str = "") -> dict[str, Any]:
    """构建举报事项提取阶段的 SSE 精简产物。"""
    profile = profile if isinstance(profile, dict) else {}
    complaint_items: list[dict[str, Any]] = []
    for idx, item in enumerate(complaints or [], start=1):
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or f"事项{idx}").strip()
        content = str(item.get("description") or item.get("label") or "").strip()
        if not content:
            continue
        complaint_items.append({
            "id": f"complaint_{idx}",
            "label": label,
            "content": content,
            "claim_text": str(item.get("claim_text") or content).strip(),
            "original_quotes": [str(text).strip() for text in item.get("original_quotes") or [] if str(text).strip()],
            "evidence_materials": [str(text).strip() for text in item.get("evidence_materials") or [] if str(text).strip()],
            "user_cited_laws": item.get("user_cited_laws") if isinstance(item.get("user_cited_laws"), list) else [],
        })
    appeals = [{"id": f"appeal_{idx}", "content": str(text).strip()} for idx, text in enumerate(profile.get("appeals") or [], start=1) if str(text).strip()]
    return {
        "incident": str(incident or "").strip(),
        "complaint_profile": {
            "reporter": str(profile.get("reporter") or ""),
            "report_date": str(profile.get("report_date") or ""),
            "reported_institution": str(profile.get("reported_institution") or ""),
            "title_base": str(profile.get("title_base") or ""),
        },
        "items_and_appeals": {
            "complaint_items": complaint_items,
            "appeals": appeals,
        },
    }


def build_match_phase_result(law_results: list[dict[str, Any]]) -> dict[str, Any]:
    """构建经验匹配阶段的 SSE 精简产物。"""
    matched_items: list[dict[str, Any]] = []
    for idx, row in enumerate(law_results or [], start=1):
        if not isinstance(row, dict):
            continue
        complaint = str(row.get("complaint") or "").strip()
        categories = row.get("categories") if isinstance(row.get("categories"), list) else []
        simplified_categories: list[dict[str, Any]] = []
        for category in categories:
            if not isinstance(category, dict):
                continue
            simplified_categories.append({
                "level1": "" if str(category.get("一级分类") or "").strip() == "本地法规分析" else str(category.get("一级分类") or ""),
                "level2": str(category.get("二级分类") or ""),
                "is_payment_scope": str(category.get("是否属于支付结算") or ""),
                "basic_fact_analysis": category.get("基础事实分析") if isinstance(category.get("基础事实分析"), dict) else {},
                "legal_existence_checks": category.get("法律存在性核验") if isinstance(category.get("法律存在性核验"), list) else [],
                "law_applicability_analysis": category.get("法律适用性分析") if isinstance(category.get("法律适用性分析"), list) else [],
                "reasonableness_conclusion": str(category.get("合理性结论") or ""),
                "check_points": [str(item).strip() for item in category.get("核查思路") or [] if str(item).strip()],
                "document_checklist": [str(item).strip() for item in category.get("调阅材料") or [] if str(item).strip()],
            })
        matched_items.append({"complaint_id": f"complaint_{idx}", "complaint": complaint, "matched_categories": simplified_categories})
    return {"matched_items": matched_items}


def build_law_rationality_phase_result(item: dict[str, Any]) -> dict[str, Any]:
    """构建单事项合理性分析 SSE 产物。"""
    index = int(item.get("index") or 0)
    return {"index": index, "complaint_id": str(item.get("complaint_id") or f"complaint_{index + 1}").strip(), "reasonable_status": str(item.get("reasonable_status") or "").strip(), "conclusion": str(item.get("conclusion") or "").strip()}


def build_conclusion_phase_result(conclusion_text: str) -> dict[str, Any]:
    """构建综合结论阶段的 SSE 精简产物。"""
    return {"conclusion_text": str(conclusion_text or "").strip()}


def get_issue_stage_by_node(node_id: str) -> tuple[int, str] | None:
    """按分析节点映射阶段进度。"""
    node = str(node_id or "").strip()
    if node == "complaint_extract_items":
        return 30, "系统正在阅读举报材料，提取举报事项、诉求和被举报主体信息。"
    if node == "complaint_law_rationality":
        return 40, "系统正在逐项核对举报事项的法律存在性和事实相关性。"
    if node in {"complaint_match_experience", "complaint_law_analysis"}:
        return 60, "系统正在基于本地法律库，分析每个举报事项给出调阅材料"
    if node == "complaint_final_conclusion":
        return 80, "系统正在基于本地法律库，分析每个举报事项给出核实思路"
    return None


def build_entity_phase_summary(entities: list[str], complaints: list[dict[str, Any]], appeals: list[str], incident: str) -> dict[str, Any]:
    """构建按主体分组的阶段结果。"""
    complaint_items: list[dict[str, Any]] = []
    for idx, item in enumerate(complaints or [], start=1):
        if isinstance(item, dict):
            label = str(item.get("label") or f"事项{idx}").strip()
            text = str(item.get("description") or item.get("claim_text") or item.get("label") or "").strip()
        else:
            label = f"事项{idx}"
            text = str(item or "").strip()
        complaint_items.append({"id": f"complaint_{idx}", "label": label, "content": text, "reported_entities": _assign_entities(" ".join([label, text]), entities)})
    appeal_items = [{"id": f"appeal_{idx}", "content": str(text or "").strip(), "reported_entities": _assign_entities(str(text or ""), entities)} for idx, text in enumerate(appeals or [], start=1)]
    return {"reported_entities": entities, "reported_entity_groups": build_reported_entity_groups(entities, complaint_items, appeal_items, incident)}


async def stream_issue_analysis_events(summary_text: str, match_strategy: str, trace_context: dict[str, Any] | None = None, prefetched_extract_payload: dict[str, Any] | None = None) -> AsyncGenerator[dict[str, Any], None]:
    """流式产出举报事项分析节点事件。"""
    emitted_progress: set[int] = set()
    trace_tag = build_trace_tag(trace_context, match_strategy)
    logger.info("[complaint_api] %s stream_issue_analysis_events start summary_len=%s", trace_tag, len(str(summary_text or "")))
    async for event in run_complaint_analysis("请基于举报件内容生成举报事项分析结果", summary_text, match_strategy=match_strategy, trace_context=trace_context, prefetched_extract_payload=prefetched_extract_payload):
        event_type = str(event.get("event") or "")
        node_id = str(event.get("node_id") or "")

        if event_type == "node_start":
            if match_strategy == "two_stage" and node_id in {"complaint_match_experience", "complaint_law_analysis"}:
                stage = None
            elif match_strategy == "law_skill" and node_id in {"complaint_match_experience", "complaint_law_analysis"}:
                stage = (60, "系统正在基于本地法律库，分析每个举报事项给出调阅材料")
            else:
                stage = get_issue_stage_by_node(node_id)
            if stage:
                progress, message = stage
                if progress not in emitted_progress:
                    emitted_progress.add(progress)
                    yield {"kind": "stage", "progress": progress, "message": message, "phase_node": node_id}

        if event_type == "node_delta" and match_strategy == "two_stage" and node_id == "complaint_match_experience":
            line_id = str(event.get("line_id") or "").strip()
            if line_id == "complaint_match-two-stage-1" and 50 not in emitted_progress:
                emitted_progress.add(50)
                yield {"kind": "stage", "progress": 50, "message": "系统正在判断每个举报事项属于哪类业务场景，例如商户、扣款、账户或支付结算问题。", "phase_node": "complaint_match_experience_stage1"}
            if line_id == "complaint_match-two-stage-2" and 60 not in emitted_progress:
                emitted_progress.add(60)
                yield {"kind": "stage", "progress": 60, "message": "系统正在细化具体违规情形，并核对可引用的法律依据。", "phase_node": "complaint_match_experience_stage2"}

        if event_type == "node_end" and node_id == "complaint_extract_items":
            content = event.get("content") if isinstance(event.get("content"), dict) else {}
            complaints = content.get("complaints") if isinstance(content.get("complaints"), list) else []
            profile = content.get("profile") if isinstance(content.get("profile"), dict) else {}
            incident = str(content.get("incident") or "").strip()
            entities = normalize_reported_entities(str((trace_context or {}).get("reported_entities") or ""))
            appeals = [str(item or "").strip() for item in profile.get("appeals") or [] if str(item or "").strip()]
            yield {"kind": "complaints", "complaints": complaints, "profile": profile, "incident": incident}
            yield {"kind": "stage", "progress": 30, "message": f"系统已提取 {len(complaints)} 个举报事项和 {len(appeals)} 个诉求，下一步将逐项核对事实和法律适用性。", "phase_node": node_id, "phase_result": build_extract_phase_result(complaints, profile, incident)}
            if entities:
                entity_summary = build_entity_phase_summary(entities, complaints, appeals, incident)
                group_parts = [f"{group.get('name')}涉及{len(group.get('complaint_items') or [])}个举报事项、{len(group.get('appeals') or [])}个诉求" for group in entity_summary.get("reported_entity_groups") or [] if isinstance(group, dict)]
                detail_text = "；".join(group_parts)
                message = f"系统已识别 {len(entities)} 个被举报机构或人员，并将举报事项和诉求按主体进行归属。"
                if detail_text:
                    message = f"{message}{detail_text}。"
                yield {"kind": "stage", "progress": 35, "message": message, "phase_node": node_id, "phase_result": entity_summary}

        if event_type == "rationality_item" and node_id == "complaint_law_rationality":
            content = event.get("content") if isinstance(event.get("content"), dict) else {}
            result = build_law_rationality_phase_result(content)
            yield {"kind": "law_rationality_item", "progress": 45, "message": f"第 {int(result.get('index') or 0) + 1} 个举报事项合理性分析完成", "phase_node": node_id, "phase_result": result, "law_rationality_item": content}

        if event_type == "node_end" and node_id == "complaint_law_rationality":
            content = event.get("content") if isinstance(event.get("content"), dict) else {}
            items = content.get("items") if isinstance(content.get("items"), list) else []
            yield {"kind": "law_rationality", "law_rationality": items}

        if event_type == "node_end" and node_id == "complaint_law_analysis":
            content = event.get("content") if isinstance(event.get("content"), dict) else {}
            laws = content.get("laws") if isinstance(content.get("laws"), list) else []
            yield {"kind": "laws", "laws": laws}
            done_message = "系统已完成本地法规适用性分析，并整理出核实思路和调阅材料。" if match_strategy == "law_skill" else "系统已完成经验匹配，并补充可引用的法律依据。"
            yield {"kind": "stage", "progress": 60, "message": done_message, "phase_node": "complaint_match_experience", "phase_result": build_match_phase_result(laws)}

        if event_type == "content_delta":
            delta = str(event.get("delta") or "")
            if delta:
                yield {"kind": "conclusion_delta", "delta": delta}

        if event_type == "node_end" and node_id == "complaint_final_conclusion":
            content = event.get("content") if isinstance(event.get("content"), dict) else {}
            conclusion_text = str(content.get("conclusion_text") or "").strip()
            if conclusion_text:
                yield {"kind": "stage", "progress": 80, "message": "系统已完成综合分析，形成基础事实、法律适用性和合理性结论。", "phase_node": node_id, "phase_result": build_conclusion_phase_result(conclusion_text)}
    logger.info("[complaint_api] %s stream_issue_analysis_events end", trace_tag)


async def assemble_issue_analysis_result(user_id: str, base_id: str, text_version: int, summary_version: int, complaints: list[dict[str, Any]], law_results: list[dict[str, Any]], final_chunks: list[str], profile: dict[str, Any] | None = None, incident: str = "", reported_entities: list[str] | None = None, scope_context: ComplaintScopeContext | None = None, law_rationality_version: int = 0) -> dict[str, Any]:
    """组装举报事项分析输出结构。"""
    profile = profile if isinstance(profile, dict) else {}
    law_basis, checklist, verification_points = aggregate_law_and_checks(law_results)

    entity_names = normalize_reported_entities(reported_entities)
    complaint_items: list[dict[str, Any]] = []
    for idx, item in enumerate(complaints, start=1):
        if isinstance(item, dict):
            text = str(item.get("description") or item.get("label") or "").strip()
            label = str(item.get("label") or f"事项{idx}").strip()
        else:
            text = str(item or "").strip()
            label = f"事项{idx}"
        if text:
            detail: dict[str, Any] = {"id": f"complaint_{idx}", "label": label, "content": text}
            if entity_names:
                detail["reported_entities"] = _assign_entities(" ".join([label, text, str(item.get("claim_text") if isinstance(item, dict) else "")]), entity_names)
            if isinstance(item, dict):
                detail.update({
                    "claim_text": str(item.get("claim_text") or text).strip(),
                    "original_quotes": [str(value).strip() for value in item.get("original_quotes") or [] if str(value).strip()],
                    "evidence_materials": [str(value).strip() for value in item.get("evidence_materials") or [] if str(value).strip()],
                    "user_cited_laws": item.get("user_cited_laws") if isinstance(item.get("user_cited_laws"), list) else [],
                })
            law_skill_analysis = law_skill_analysis_for_item(law_results, idx - 1)
            if law_skill_analysis:
                detail["law_skill_analysis"] = law_skill_analysis
            complaint_items.append(detail)

    appeals = []
    for idx, text in enumerate(profile.get("appeals") or [], start=1):
        appeal = {"id": f"appeal_{idx}", "content": text}
        if entity_names:
            appeal["reported_entities"] = _assign_entities(str(text or ""), entity_names)
        appeals.append(appeal)
    manifest = load_manifest(user_id, base_id, scope_context=scope_context)
    classification_version = int(manifest.get("classification", {}).get("current") or 0)
    return {
        "api_kind": "complaint_analysis",
        "base_id": base_id,
        "incident": str(incident or "").strip(),
        "complaint_profile": {
            "reporter": str(profile.get("reporter") or ""),
            "report_date": str(profile.get("report_date") or ""),
            "reported_institution": str(profile.get("reported_institution") or ""),
            "title_base": str(profile.get("title_base") or ""),
        },
        "items_and_appeals": {
            "complaint_items": complaint_items,
            "appeals": appeals,
        },
        **({"reported_entities": entity_names, "reported_entity_groups": build_reported_entity_groups(entity_names, complaint_items, appeals, incident)} if entity_names else {}),
        "law_basis": law_basis,
        "document_checklist": checklist,
        "verification_points": verification_points,
        "conclusion_text": "".join(final_chunks).strip(),
        "scope_context": scope_context.to_meta() if scope_context is not None else {},
        "versions": {
            "text_version": text_version,
            "summary_version": summary_version,
            "analysis_version": 0,
            "classification_version": classification_version,
            "law_rationality_version": int(law_rationality_version or 0),
        },
    }


def next_issue_analysis_version(user_id: str, base_id: str, scope_context: ComplaintScopeContext | None = None) -> int:
    """计算下一版举报事项分析版本号。"""
    return next_version(load_manifest(user_id, base_id, scope_context=scope_context), "issue_analysis")


async def save_issue_analysis_result(user_id: str, base_id: str, text_version: int, summary_version: int, result: dict[str, Any], issue_version: int | None = None, scope_context: ComplaintScopeContext | None = None) -> tuple[dict[str, Any], int]:
    """保存举报事项分析结果并更新版本。"""
    manifest = load_manifest(user_id, base_id, scope_context=scope_context)
    issue_version = int(issue_version or next_version(manifest, "issue_analysis"))
    if scope_context is not None:
        result = {**result, "scope_context": scope_context.to_meta()}
    issue_path = os.path.join(stage_dir(user_id, base_id, "issue_analysis", issue_version, scope_context=scope_context), "final.json")
    await write_json_file(issue_path, result)
    write_meta(user_id, base_id, "issue_analysis", issue_version, source="auto", base_versions={"base_text_version": text_version, "base_summary_version": summary_version}, extra={"task": "issue_analysis"}, scope_context=scope_context, updated_by=user_id)
    manifest = update_manifest(manifest, "issue_analysis", issue_version)
    save_manifest(user_id, base_id, manifest, scope_context=scope_context)
    return result, issue_version


async def save_law_rationality_result(user_id: str, base_id: str, text_version: int, summary_version: int, issue_version: int, items: list[dict[str, Any]], scope_context: ComplaintScopeContext | None = None) -> tuple[dict[str, Any], int]:
    """保存合理性分析结果并更新版本。"""
    manifest = load_manifest(user_id, base_id, scope_context=scope_context)
    law_rationality_version = next_version(manifest, "law_rationality")
    payload = {
        "api_kind": "complaint_law_rationality",
        "base_id": base_id,
        "scope_context": scope_context.to_meta() if scope_context is not None else {},
        "summary_version": int(summary_version or 0),
        "items": items,
    }
    final_path = os.path.join(stage_dir(user_id, base_id, "law_rationality", law_rationality_version, scope_context=scope_context), "final.json")
    await write_json_file(final_path, payload)
    write_meta(user_id, base_id, "law_rationality", law_rationality_version, source="auto", base_versions={"base_text_version": text_version, "base_summary_version": summary_version, "issue_analysis_version": issue_version}, extra={"task": "law_rationality"}, scope_context=scope_context, updated_by=user_id)
    manifest = update_manifest(manifest, "law_rationality", law_rationality_version)
    save_manifest(user_id, base_id, manifest, scope_context=scope_context)
    return payload, law_rationality_version


async def stream_issue_analysis(user_id: str, base_id: str, summary_text: str, text_version: int, summary_version: int, force: bool = False, match_strategy: str | None = None, file_id: str = "", reported_entities: list[str] | None = None, scope_context: ComplaintScopeContext | None = None) -> AsyncGenerator[dict[str, Any], None]:
    """流式执行举报事项分析并返回阶段与结果。"""
    resolved_strategy = normalize_match_strategy(match_strategy)
    entity_names = normalize_reported_entities(reported_entities)
    trace_context = {"base_id": base_id, "user_id": user_id, "file_id": file_id, "reported_entities": ",".join(entity_names), "match_strategy": resolved_strategy}
    if scope_context is not None:
        trace_context.update(scope_context.to_meta())
    trace_tag = build_trace_tag(trace_context, resolved_strategy)
    logger.info("[complaint_api] %s stream_issue_analysis start force=%s text_version=%s summary_version=%s summary_preview=%s", trace_tag, force, text_version, summary_version, log_preview_text(summary_text))
    if not force:
        cached, cached_version = load_latest_issue_analysis(user_id, base_id, scope_context=scope_context)
        if isinstance(cached, dict):
            logger.info("[complaint_api] %s stream_issue_analysis cache_hit issue_version=%s", trace_tag, cached_version)
            yield {"kind": "cache_hit", "result": cached, "issue_version": int(cached_version)}
            return
    issue_version = next_issue_analysis_version(user_id, base_id, scope_context=scope_context)
    trace_context["issue_analysis_version"] = issue_version
    trace_tag = build_trace_tag(trace_context, resolved_strategy)

    summary_bundle, bundle_version = load_latest_summary_bundle(user_id, base_id, scope_context=scope_context)
    prefetched_extract_payload = {}
    if bundle_version == summary_version:
        prefetched_extract_payload = extract_structured_payload_from_summary_bundle(summary_bundle)

    complaints: list[dict[str, Any]] = []
    law_rationality_items: list[dict[str, Any]] = []
    law_results: list[dict[str, Any]] = []
    final_chunks: list[str] = []
    profile: dict[str, Any] = {}
    incident = ""
    async for item in stream_issue_analysis_events(summary_text, resolved_strategy, trace_context, prefetched_extract_payload=prefetched_extract_payload):
        kind = str(item.get("kind") or "")
        if kind == "stage":
            item["match_strategy"] = resolved_strategy
            item["issue_analysis_version"] = issue_version
            logger.info("[complaint_api] %s stage progress=%s phase_node=%s message=%s", trace_tag, item.get("progress"), item.get("phase_node"), item.get("message"))
            yield item
            continue
        if kind == "law_rationality_item":
            item["match_strategy"] = resolved_strategy
            item["issue_analysis_version"] = issue_version
            phase_result = item.get("phase_result") if isinstance(item.get("phase_result"), dict) else {}
            logger.info("[complaint_api] %s law_rationality_item index=%s complaint_id=%s", trace_tag, phase_result.get("index"), phase_result.get("complaint_id"))
            yield item
            continue
        if kind == "complaints":
            complaints = item.get("complaints") if isinstance(item.get("complaints"), list) else []
            profile = item.get("profile") if isinstance(item.get("profile"), dict) else {}
            incident = str(item.get("incident") or "").strip()
            logger.info("[complaint_api] %s complaints_extracted count=%s reporter=%s institution=%s", trace_tag, len(complaints), str(profile.get("reporter") or ""), str(profile.get("reported_institution") or ""))
            continue
        if kind == "law_rationality":
            law_rationality_items = item.get("law_rationality") if isinstance(item.get("law_rationality"), list) else []
            logger.info("[complaint_api] %s law_rationality_done count=%s", trace_tag, len(law_rationality_items))
            continue
        if kind == "laws":
            law_results = item.get("laws") if isinstance(item.get("laws"), list) else []
            logger.info("[complaint_api] %s law_analysis_done count=%s", trace_tag, len(law_results))
            continue
        if kind == "conclusion_delta":
            delta = str(item.get("delta") or "")
            if delta:
                final_chunks.append(delta)
    logger.info("[complaint_api] %s stream_collect_done complaints=%s law_rationality=%s laws=%s conclusion_len=%s", trace_tag, len(complaints), len(law_rationality_items), len(law_results), len("".join(final_chunks)))

    _, law_rationality_version = await save_law_rationality_result(user_id, base_id, text_version, summary_version, issue_version, law_rationality_items, scope_context=scope_context)
    result = await assemble_issue_analysis_result(user_id, base_id, text_version, summary_version, complaints, law_results, final_chunks, profile=profile, incident=incident, reported_entities=entity_names, scope_context=scope_context, law_rationality_version=law_rationality_version)
    saved_result, issue_version = await save_issue_analysis_result(user_id, base_id, text_version, summary_version, result, issue_version=issue_version, scope_context=scope_context)
    logger.info("[complaint_api] %s stream_issue_analysis completed issue_version=%s", trace_tag, issue_version)
    yield {"kind": "completed", "result": saved_result, "issue_version": int(issue_version)}


async def build_issue_analysis(user_id: str, base_id: str, summary_text: str, text_version: int, summary_version: int, force: bool = False, match_strategy: str | None = None, file_id: str = "", reported_entities: list[str] | None = None, scope_context: ComplaintScopeContext | None = None) -> tuple[dict[str, Any], int]:
    """构建并保存举报事项分析结果。"""
    with llm_prompt_metrics_context(request_id=base_id, user_id=user_id, base_id=base_id, file_id=file_id, business_stage="complaint_issue_analysis"):
        async for item in stream_issue_analysis(user_id, base_id, summary_text, text_version, summary_version, force=force, match_strategy=match_strategy, file_id=file_id, reported_entities=reported_entities, scope_context=scope_context):
            kind = str(item.get("kind") or "")
            if kind not in {"cache_hit", "completed"}:
                continue
            result = item.get("result") if isinstance(item.get("result"), dict) else None
            issue_version = int(item.get("issue_version") or 0)
            if isinstance(result, dict) and issue_version > 0:
                return result, issue_version
    raise HTTPException(status_code=500, detail="举报事项分析结果为空")


def load_latest_reply(user_id: str, base_id: str, scope_context: ComplaintScopeContext | None = None) -> tuple[dict[str, Any] | None, int]:
    """读取最新文书草稿。"""
    if scope_context is None:
        manifest = load_manifest(user_id, base_id)
        reply_version = int(manifest.get("reply", {}).get("current") or 0)
        if reply_version <= 0:
            return None, 0
        reply_path = os.path.join(stage_dir(user_id, base_id, "reply", reply_version), "final.json")
        if not os.path.isfile(reply_path):
            return None, 0
        try:
            with open(reply_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                return data, reply_version
        except Exception as exc:
            logger.error("[complaint_api] load_latest_reply_failed user_id=%s base_id=%s version=%s err=%s", user_id, base_id, reply_version, exc)
        return None, 0
    for read_scope in complaint_scope_read_chain(scope_context):
        manifest = load_manifest(user_id, base_id, scope_context=read_scope)
        reply_version = int(manifest.get("reply", {}).get("current") or 0)
        if reply_version <= 0:
            continue
        reply_path = os.path.join(stage_dir(user_id, base_id, "reply", reply_version, scope_context=read_scope), "final.json")
        if not os.path.isfile(reply_path):
            continue
        try:
            with open(reply_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                data.setdefault("scope_context", read_scope.to_meta())
                return data, reply_version
        except Exception as exc:
            logger.error("[complaint_api] load_latest_reply_failed user_id=%s base_id=%s scope=%s version=%s err=%s", user_id, base_id, read_scope.scope_path, reply_version, exc)
    return None, 0


def load_reply_meta(user_id: str, base_id: str, version: int, scope_context: ComplaintScopeContext | None = None) -> dict[str, Any]:
    """读取文书 meta 信息。"""
    meta_path = os.path.join(stage_dir(user_id, base_id, "reply", version, scope_context=scope_context), "meta.json")
    if not os.path.isfile(meta_path):
        return {}
    try:
        with open(meta_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def build_reply_title() -> str:
    """生成固定答复意见书标题。"""
    return "答复意见书"


def normalize_reply_case_title(value: Any) -> str:
    """兼容旧版按案件标题生成文书标题的清洗逻辑。"""
    text = str(value or "").strip()
    text = re.sub(r"[。；;，,、\s]+$", "", text).strip()
    if text and not text.startswith("关于"):
        text = f"关于{text}"
    return text


def build_reply_document_date() -> str:
    """生成答复落款日期。"""
    now = datetime.now(ZoneInfo(REPLY_DRAFT_DATE_TZ))
    return f"{now.year}年{now.month}月{now.day}日"


def normalize_reply_text_field(value: Any, field_name: str) -> str:
    """规范化文书输入文本字段。"""
    text = str(value or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail=f"{field_name} 不能为空")
    return text


def normalize_reply_draft_mode(value: Any) -> str:
    """规范化答复意见书生成模式。"""
    mode = str(value or "").strip().lower() or REPLY_DRAFT_MODE_SKILL
    if mode not in REPLY_DRAFT_ALLOWED_MODES:
        raise HTTPException(status_code=400, detail=f"draft_mode 非法: {mode}")
    return mode


def normalize_reply_appeal_responses(appeal_responses: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    """规范化诉求答复分类输入。"""
    if not isinstance(appeal_responses, list) or not appeal_responses:
        return []
    result: list[dict[str, str]] = []
    for row in appeal_responses:
        if not isinstance(row, dict):
            continue
        appeal_id = str(row.get("appeal_id") or "").strip()
        appeal_content = str(row.get("appeal_content") or "").strip()
        response_type = str(row.get("type") or "").strip()
        custom_text = str(row.get("custom_text") or "").strip()
        if not appeal_id and not appeal_content:
            raise HTTPException(status_code=400, detail="appeal_responses 缺少 appeal_id 或 appeal_content")
        if not response_type and not custom_text:
            raise HTTPException(status_code=400, detail="appeal_responses 缺少 type 或 custom_text")
        result.append({"appeal_id": appeal_id, "appeal_content": appeal_content, "type": response_type, "custom_text": custom_text})
    if not result:
        return []
    return result


def build_reply_draft_input_hash(file_id: str, issue_version: int, verification_content: str, violation_found: bool, disposal_measures: str, appeal_responses: list[dict[str, str]], model: str, draft_mode: str = REPLY_DRAFT_MODE_SKILL, supplement_material_sentence: str = "", review_report: str = "", review_report_text: str = "", reporter_name: str = "") -> str:
    """生成文书输入缓存指纹。"""
    payload = {
        "file_id": str(file_id or "").strip(),
        "issue_version": int(issue_version or 0),
        "verification_content": str(verification_content or "").strip(),
        "violation_found": bool(violation_found),
        "disposal_measures": str(disposal_measures or "").strip(),
        "supplement_material_sentence": str(supplement_material_sentence or "").strip(),
        "review_report": str(review_report or "").strip(),
        "review_report_text_hash": hashlib.sha1(str(review_report_text or "").strip().encode("utf-8")).hexdigest() if str(review_report_text or "").strip() else "",
        "reporter_name": normalize_reply_reporter(reporter_name),
        "appeal_responses": appeal_responses if isinstance(appeal_responses, list) else [],
        "prompt_version": REPLY_DRAFT_PROMPT_VERSION,
        "model": str(model or "").strip(),
    }
    resolved_mode = normalize_reply_draft_mode(draft_mode)
    if resolved_mode != REPLY_DRAFT_MODE_PROMPT:
        payload["draft_mode"] = resolved_mode
        payload["skill_version"] = REPLY_DRAFT_SKILL_VERSION
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def load_cached_reply_by_input(user_id: str, base_id: str, input_hash: str, model: str, scope_context: ComplaintScopeContext | None = None) -> tuple[dict[str, Any] | None, int]:
    """按输入指纹命中自动生成的文书缓存。"""
    cached_reply, cached_version = load_latest_reply(user_id, base_id, scope_context=scope_context) if scope_context is not None else load_latest_reply(user_id, base_id)
    if not isinstance(cached_reply, dict) or cached_version <= 0:
        return None, 0
    meta_scope = scope_context
    if isinstance(cached_reply.get("scope_context"), dict):
        scope_meta = cached_reply.get("scope_context") or {}
        meta_scope = ComplaintScopeContext(str(scope_meta.get("scope") or "default"), str(scope_meta.get("org_id") or ""), str(scope_meta.get("entity_id") or ""), tuple(str(scope_meta.get("scope_path") or "default").split("/")), str(scope_meta.get("reporter_name") or ""))
    meta = load_reply_meta(user_id, base_id, cached_version, scope_context=meta_scope) if meta_scope is not None else load_reply_meta(user_id, base_id, cached_version)
    if str(meta.get("source") or "") != "auto":
        return None, 0
    if str(meta.get("input_hash") or "") != str(input_hash or ""):
        return None, 0
    if str(meta.get("prompt_version") or "") != REPLY_DRAFT_PROMPT_VERSION:
        return None, 0
    if str(meta.get("model") or "") != str(model or ""):
        return None, 0
    return cached_reply, cached_version


def _strip_reply_code_fence(raw: str) -> str:
    text = str(raw or "").strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    while lines and lines[-1].strip() == "```":
        lines.pop()
    return "\n".join(lines).strip()


def _extract_reply_json_fragment_at(text: str, start_idx: int) -> str:
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


def _extract_first_reply_json_fragment(raw: str) -> str:
    text = _strip_reply_code_fence(raw)
    for idx, ch in enumerate(text):
        if ch != "{":
            continue
        candidate = _extract_reply_json_fragment_at(text, idx)
        if candidate:
            return candidate
    return text


def parse_reply_draft_model_output(raw: str) -> dict[str, Any]:
    """解析模型输出的文书 JSON。"""
    text = _extract_first_reply_json_fragment(raw)
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def normalize_reply_message_content(value: Any) -> str:
    """归一化 OpenAI 风格 message 字段。"""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("text") or item.get("value")
                if text:
                    parts.append(str(text))
            elif item is not None:
                parts.append(str(item))
        return "".join(parts)
    return "" if value is None else str(value)


def extract_reply_message_text(resp: Any) -> str:
    """从模型响应中提取最终文本，兼容 GLM 非思考模式的多字段返回。"""
    choices = getattr(resp, "choices", None) or []
    if not choices:
        return ""
    message = getattr(choices[0], "message", None)
    if not message:
        return ""
    content = normalize_reply_message_content(getattr(message, "content", "")).strip()
    if content:
        return content
    for field in ("reasoning_content", "reasoning", "analysis", "thoughts", "think"):
        fallback = normalize_reply_message_content(getattr(message, field, "")).strip()
        if fallback:
            logger.warning("[complaint_api] reply_draft content empty, fallback field=%s", field)
            return fallback
    return ""


def build_reply_complaint_summary(complaint_items: list[dict[str, Any]]) -> str:
    """汇总举报事项，生成正文中的事项综述。"""
    parts: list[str] = []
    for row in complaint_items or []:
        if not isinstance(row, dict):
            continue
        text = str(row.get("content") or row.get("description") or row.get("label") or "").strip()
        if text:
            parts.append(text)
    merged = "；".join(dedupe_list(parts))
    return merged or "你反映相关机构存在违规问题"


def build_reply_receipt_sentence(report_date: str) -> str:
    """按举报日期生成收信固定话术。"""
    text = str(report_date or "").strip()
    if text:
        return f"本单位于{text}收到你的举报信"
    return "本单位已收到你的举报信"


def has_reply_appeal_responses(appeals: list[dict[str, Any]]) -> bool:
    """判断是否已提供逐条诉求答复材料。"""
    return any(str(row.get("type") or "").strip() != "pending" or str(row.get("custom_text") or "").strip() for row in appeals or [] if isinstance(row, dict))


def build_reply_appeal_items(analysis_result: dict[str, Any], appeal_responses: list[dict[str, str]]) -> list[dict[str, str]]:
    """合并分析结果中的诉求与前端传入的答复分类。"""
    appeals_section = analysis_result.get("items_and_appeals") if isinstance(analysis_result.get("items_and_appeals"), dict) else {}
    source_appeals = appeals_section.get("appeals") if isinstance(appeals_section.get("appeals"), list) else []
    appeal_map: dict[str, str] = {}
    for row in source_appeals:
        if not isinstance(row, dict):
            continue
        appeal_id = str(row.get("id") or "").strip()
        appeal_content = str(row.get("content") or "").strip()
        if appeal_id and appeal_content:
            appeal_map[appeal_id] = appeal_content
    if not appeal_responses:
        return [{"appeal_id": appeal_id, "appeal_content": appeal_content, "type": "pending", "custom_text": ""} for appeal_id, appeal_content in appeal_map.items()]

    result: list[dict[str, str]] = []
    for row in appeal_responses:
        appeal_id = str(row.get("appeal_id") or "").strip()
        appeal_content = str(row.get("appeal_content") or "").strip() or appeal_map.get(appeal_id, "")
        if not appeal_content:
            raise HTTPException(status_code=400, detail=f"未找到诉求内容: {appeal_id or 'unknown'}")
        result.append({
            "appeal_id": appeal_id,
            "appeal_content": appeal_content,
            "type": str(row.get("type") or "").strip(),
            "custom_text": str(row.get("custom_text") or "").strip(),
        })
    return result


def build_reply_generation_context(analysis_result: dict[str, Any], appeal_responses: list[dict[str, str]], supplement_material_sentence: str = "", review_report_text: str = "", reporter_name: str = "") -> dict[str, Any]:
    """从举报分析结果中提取答复意见书上下文。"""
    profile = analysis_result.get("complaint_profile") if isinstance(analysis_result.get("complaint_profile"), dict) else {}
    items_and_appeals = analysis_result.get("items_and_appeals") if isinstance(analysis_result.get("items_and_appeals"), dict) else {}
    complaint_items = items_and_appeals.get("complaint_items") if isinstance(items_and_appeals.get("complaint_items"), list) else []
    appeals = build_reply_appeal_items(analysis_result, appeal_responses)
    reporter = normalize_reply_reporter(reporter_name) if str(reporter_name or "").strip() else normalize_reply_reporter(profile.get("reporter"))
    return {
        "reporter": reporter,
        "report_date": str(profile.get("report_date") or "").strip(),
        "receipt_sentence": build_reply_receipt_sentence(str(profile.get("report_date") or "").strip()),
        "supplement_material_sentence": str(supplement_material_sentence or "").strip(),
        "review_report_text": str(review_report_text or "").strip(),
        "reported_institution": str(profile.get("reported_institution") or "").strip() or "相关机构",
        "complaint_summary": build_reply_complaint_summary(complaint_items),
        "complaint_items": complaint_items,
        "appeals": appeals,
        "verification_points": analysis_result.get("verification_points") if isinstance(analysis_result.get("verification_points"), list) else [],
        "law_basis": analysis_result.get("law_basis") if isinstance(analysis_result.get("law_basis"), list) else [],
        "conclusion_text": str(analysis_result.get("conclusion_text") or "").strip(),
    }


def validate_reply_draft_content(content: str, expected_title: str = "", violation_found: bool = False, disposal_measures: str = "", require_appeal_placeholder: bool = False) -> None:
    """校验答复正文结构。"""
    text = str(content or "").strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    title = str(expected_title or "").strip()
    if title and (not lines or lines[0] != title):
        raise ValueError("reply content 缺少正文标题")
    compact = re.sub(r"\s+", "", text)
    required_sections = [
        ("编号", "编号："),
        ("现答复如下", "现答复如下"),
        ("一、关于", "一、关于"),
        ("二、关于你举报信中的其他诉求", "二、关于你举报信中的其他诉求"),
        ("感谢语", "感谢你对我行工作的支持。"),
        ("落款机关占位", "{请补充落款机关}"),
    ]
    missing = [label for label, pattern in required_sections if re.sub(r"\s+", "", pattern) not in compact]
    if missing:
        raise ValueError(f"reply content 缺少段落: {','.join(missing)}")
    if require_appeal_placeholder and re.sub(r"\s+", "", "{请补充需要答复的内容}") not in compact:
        raise ValueError("reply content 缺少诉求答复占位")
    salutation_lines = [line for line in lines[1:5] if line.endswith("：") or line.endswith(":")]
    for line in salutation_lines:
        if re.search(r"(先生|女士|同志|尊敬的)", line):
            raise ValueError("reply content 抬头包含多余称谓")

def ensure_reply_content_title(content: str, expected_title: str) -> str:
    """确保正文第一行包含固定标题。"""
    text = str(content or "").strip()
    title = str(expected_title or "").strip()
    if not title:
        return text
    lines = [line.rstrip() for line in text.splitlines()]
    first_non_empty = ""
    for line in lines:
        stripped = line.strip()
        if stripped:
            first_non_empty = stripped
            break
    if first_non_empty == title:
        return text
    return f"{title}\n\n{text}" if text else title


def build_reply_draft_prompt(expected_title: str, generation_context: dict[str, Any], verification_content: str, violation_found: bool, disposal_measures: str, document_date: str) -> tuple[str, str]:
    """组装文书生成提示词。"""
    has_appeal_responses = has_reply_appeal_responses(generation_context.get("appeals") or [])
    system_prompt = (
        "你是金融监管机构举报答复意见书撰写助手。"
        "你必须严格根据输入变量生成一个 JSON 对象，只包含 title 和 content 两个字段。"
        "title 必须与 expected_title 完全一致。"
        "content 必须严格采用正式公文风格，不要输出 Markdown，不要输出解释。"
        "正文必须尽量遵循 content_template 的结构，标题为“答复意见书”。"
        "正文必须包括：编号、抬头、收信及办理过程说明、举报事项总括、现答复如下、调查事项部分、其他诉求部分、感谢语、落款机关占位和日期。"
        "正文必须保留固定占位符“{请补充文书编号}”“{请补充落款机关}”。"
        "正文必须保留固定句式“现答复如下：”“二、关于你举报信中的其他诉求”“感谢你对我行工作的支持。”。"
        "抬头必须严格使用 reporter 的原值加中文冒号，例如“张三：”或“举报人：”；不得添加“先生”“女士”“同志”“尊敬的”等任何称谓、敬语或其他描述。"
        "你可以基于 complaint_summary、complaint_items、reported_institution 归纳生成举报事项总括句、调查部分一级标题、每个举报事项的小标题和简述。"
        "答复对象就是举报人本人，正文不得使用“举报人反映/举报人称”等第三人称表述，应改为“你反映/你称/你举报”等第二人称表述。"
        "每个举报事项应在“一、关于...的调查”下单独成段，使用中文括号序号如“（一）”。"
        "verification_content、violation_found、disposal_measures 是全案级输入，不是逐举报事项输入；不得默认套用到每一个举报事项。"
        "撰写每个举报事项段落前，必须根据 complaint_items 的事项内容，语义判断 verification_content、conclusion_text、law_basis、disposal_measures 哪些材料能对应到该事项。"
        "能语义对应的材料，可以整理进该事项段落，但不得改变原意、不得新增事实；无法对应到该事项的，不得强行套用全案材料，必须写“{请补充关于该事项的调查核实及法律适用意见}”。"
        "将语义对应材料写入某个举报事项段落时，必须融合改写，不得机械拼接“事项简述 + 全案材料摘录”。"
        "应将 complaint_items 中的事项内容，与语义匹配到的 verification_content、conclusion_text、law_basis、disposal_measures 重组为连贯答复。"
        "如果匹配材料本身包含“关于你反映的……问题”“你主张……”“你反映……”等事项引入语，不要直接照搬；应结合当前事项标题和事项内容重新组织表达。"
        "如果 complaint_items 的事项内容已经足够清楚，匹配材料只提供核查结论或处置意见，正文应直接承接为“经核查……”“根据核查情况……”，或合并改写为“针对你反映……的问题，经核查……”。"
        "如果 complaint_items 的事项内容较模糊，而匹配材料能补充具体问题，可以吸收匹配材料中的事项描述，与原事项合并改写，形成完整上下文。"
        "融合改写不得删除实质信息；不得删除关键主体、交易行为、金额、时间、法规名称、适用或不适用判断、处置措施等内容，也不得新增事实或改变原意。"
        "violation_found 只是全案判断，不能自动推成每个举报事项的违规或不违规结论。"
        "如果 violation_found=true 且 disposal_measures 非空，只有在处置措施能语义对应到某个事项或对应核实材料时才写入相关事项段落；不得为了包含处置措施而放到无依据事项下。"
        "如果 violation_found=false，不得写成已责令整改、已行政处罚或已采取其他处置措施；也不得自动写成每个事项均未违规，除非核实材料能语义对应。"
        "appeals 中每一项都必须在“二、关于你举报信中的其他诉求”下单独成段，必须引用或标题化该诉求原文。"
        "若某项带 custom_text，优先按 custom_text 写；否则根据 type 整理成自然、正式的一段监管答复。"
        "如果 has_appeal_responses=false 且 review_report_text 非空，必须逐条比对 appeals 与 review_report_text：能找到对应调查核实或处理答复内容的，整理为正式答复；找不到对应内容的，下一行固定写“{请补充需要答复的内容}”。"
        "如果 has_appeal_responses=false 且 review_report_text 为空，表示当前没有逐条诉求答复材料。此时不得自行编造诉求答复，必须根据 appeals 中的诉求原文撰写“二、关于你举报信中的其他诉求”部分：每条使用中文括号序号如“（一）”，下一行固定写“{请补充需要答复的内容}”。"
        "不得编造法律依据、机构名称、核实结论、处理措施。"
        "不要输出 Markdown 代码块，不要输出解释。"
    )
    user_payload = {
        "expected_title": expected_title,
        "reporter": generation_context.get("reporter"),
        "report_date": generation_context.get("report_date"),
        "receipt_sentence": generation_context.get("receipt_sentence"),
        "supplement_material_sentence": generation_context.get("supplement_material_sentence"),
        "reported_institution": generation_context.get("reported_institution"),
        "complaint_summary": generation_context.get("complaint_summary"),
        "complaint_items": generation_context.get("complaint_items"),
        "appeals": generation_context.get("appeals"),
        "has_appeal_responses": has_appeal_responses,
        "review_report_text": generation_context.get("review_report_text"),
        "verification_points": generation_context.get("verification_points"),
        "law_basis": generation_context.get("law_basis"),
        "conclusion_text": generation_context.get("conclusion_text"),
        "verification_content": verification_content,
        "violation_found": bool(violation_found),
        "disposal_measures": disposal_measures,
        "document_date": document_date,
        "content_template": (
            "答复意见书\n\n"
            "编号：{请补充文书编号}\n\n"
            "{收件人称谓}：\n"
            "{receipt_sentence}{supplement_material_sentence}。你举报{举报事项总括句}。根据属地监管原则，我行依法对你举报的事项进行了调查，现答复如下：\n\n"
            "一、关于{举报事项调查总标题}的调查\n\n"
            "（一）关于{第一个举报事项标题}\n"
            "{第一个举报事项简述}\n"
            "{请补充关于该事项的调查核实及法律适用意见}\n\n"
            "二、关于你举报信中的其他诉求\n\n"
            "（一）关于{第一个诉求标题}。\n"
            "{请补充需要答复的内容}\n\n"
            "感谢你对我行工作的支持。\n\n"
            "{请补充落款机关}\n"
            "{document_date}"
        ),
    }
    return system_prompt, json.dumps(user_payload, ensure_ascii=False, indent=2)


def build_reply_draft_skill_payload(expected_title: str, generation_context: dict[str, Any], verification_content: str, violation_found: bool, disposal_measures: str, document_date: str) -> dict[str, Any]:
    """组装答复意见书 skill 输入。"""
    has_appeal_responses = has_reply_appeal_responses(generation_context.get("appeals") or [])
    return {
        "expected_title": expected_title,
        "skill_version": REPLY_DRAFT_SKILL_VERSION,
        "case_context": {
            "reporter": generation_context.get("reporter"),
            "report_date": generation_context.get("report_date"),
            "receipt_sentence": generation_context.get("receipt_sentence"),
            "supplement_material_sentence": generation_context.get("supplement_material_sentence"),
            "reported_institution": generation_context.get("reported_institution"),
            "complaint_summary": generation_context.get("complaint_summary"),
            "complaint_items": generation_context.get("complaint_items"),
            "appeals": generation_context.get("appeals"),
            "verification_points": generation_context.get("verification_points"),
            "law_basis": generation_context.get("law_basis"),
            "conclusion_text": generation_context.get("conclusion_text"),
        },
        "manual_inputs": {
            "verification_content": verification_content,
            "violation_found": bool(violation_found),
            "disposal_measures": disposal_measures,
        },
        "has_appeal_responses": has_appeal_responses,
        "review_report_text": generation_context.get("review_report_text"),
        "must_include_texts": [],
        "document_date": document_date,
        "content_template": (
            "答复意见书\n\n"
            "编号：{请补充文书编号}\n\n"
            "{收件人称谓}：\n"
            "{receipt_sentence}{supplement_material_sentence}。你举报{举报事项总括句}。根据属地监管原则，我行依法对你举报的事项进行了调查，现答复如下：\n\n"
            "一、关于{举报事项调查总标题}的调查\n\n"
            "（一）关于{第一个举报事项标题}\n"
            "{第一个举报事项简述}\n"
            "{请补充关于该事项的调查核实及法律适用意见}\n\n"
            "二、关于你举报信中的其他诉求\n\n"
            "（一）关于{第一个诉求标题}。\n"
            "{请补充需要答复的内容}\n\n"
            "感谢你对我行工作的支持。\n\n"
            "{请补充落款机关}\n"
            "{document_date}"
        ),
    }


async def generate_reply_content_with_skill(expected_title: str, generation_context: dict[str, Any], verification_content: str, violation_found: bool, disposal_measures: str, document_date: str, model: str) -> dict[str, Any]:
    """通过答复意见书 skill 生成文书 JSON。"""
    from apps.complaint_app.services.reply_draft_skill_runner import generate_reply_draft_with_skill

    payload = build_reply_draft_skill_payload(expected_title, generation_context, verification_content, violation_found, disposal_measures, document_date)
    return await generate_reply_draft_with_skill(payload, model)


async def generate_reply_content_from_inputs(analysis_result: dict[str, Any] | str, verification_content: str, violation_found: bool | str = False, disposal_measures: str = "", appeal_responses: list[dict[str, str]] | None = None, draft_mode: str = REPLY_DRAFT_MODE_SKILL, supplement_material_sentence: str = "", review_report_text: str = "", reporter_name: str = "") -> tuple[str, str]:
    """基于分析结果与前端补充变量生成答复标题与正文。"""
    if isinstance(analysis_result, str):
        case_title = normalize_reply_case_title(analysis_result)
        expected_title = f"{case_title}的答复意见书" if case_title else build_reply_title()
        raw = await call_llm_text("", "", model=get_reply_model(), temperature=0.2)
        parsed = parse_reply_draft_model_output(raw)
        content = str(parsed.get("content") or "").strip()
        return expected_title, content
    resolved_mode = normalize_reply_draft_mode(draft_mode)
    expected_title = build_reply_title()
    document_date = build_reply_document_date()
    generation_context = build_reply_generation_context(analysis_result, appeal_responses or [], supplement_material_sentence=supplement_material_sentence, review_report_text=review_report_text, reporter_name=reporter_name)
    require_appeal_placeholder = not has_reply_appeal_responses(generation_context.get("appeals") or []) and not str(review_report_text or "").strip()
    model = get_reply_model()
    last_error = ""
    for attempt in range(2):
        if resolved_mode == REPLY_DRAFT_MODE_SKILL:
            parsed = await generate_reply_content_with_skill(expected_title, generation_context, verification_content, violation_found, disposal_measures, document_date, model)
        else:
            system_prompt, user_content = build_reply_draft_prompt(expected_title, generation_context, verification_content, violation_found, disposal_measures, document_date)
            raw = await call_llm_text(system_prompt, user_content, model=model, temperature=0.2 if attempt == 0 else 0.0)
            parsed = parse_reply_draft_model_output(raw)
        content = ensure_reply_content_title(str(parsed.get("content") or "").strip(), expected_title)
        if not content:
            last_error = "reply content 为空"
            continue
        try:
            validate_reply_draft_content(content, expected_title=expected_title, violation_found=violation_found, disposal_measures=disposal_measures, require_appeal_placeholder=require_appeal_placeholder)
        except Exception as exc:
            last_error = str(exc)
            logger.warning("[complaint_api] reply_draft_validate_failed mode=%s attempt=%s err=%s content_preview=%s", resolved_mode, attempt + 1, last_error, content[:1200])
            continue
        return expected_title, content
    raise HTTPException(status_code=500, detail=f"答复意见书生成失败: {last_error or '模型输出格式无效'}")


async def call_llm_text(system_prompt: str, user_content: str, model: str, temperature: float = 0.2) -> str:
    """调用大模型生成文本。"""
    client = get_async_openai_client()
    extra_body = build_thinking_extra_body(model, False)
    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_content}]
    metric_stage = "complaint_reply_draft_prompt"
    call_id = record_llm_prompt_metrics(metric_stage, model=model, messages=messages, metadata={"business_stage": "complaint_reply_draft"})
    started_at = time.monotonic()
    response = await client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        extra_body=extra_body,
    )
    output_text = extract_reply_message_text(response)
    record_llm_output_metrics(metric_stage, model=model, output_text=output_text, llm_duration_ms=int((time.monotonic() - started_at) * 1000), usage=extract_usage_tokens(response), metadata={"business_stage": "complaint_reply_draft"}, call_id=call_id)
    return output_text


async def save_reply_result(user_id: str, base_id: str, title: str, content: str, source_versions: dict[str, Any], source: str, meta_extra: dict[str, Any] | None = None, scope_context: ComplaintScopeContext | None = None) -> tuple[dict[str, Any], int]:
    """保存文书草稿并更新版本。"""
    manifest = load_manifest(user_id, base_id, scope_context=scope_context)
    reply_version = next_version(manifest, "reply")
    normalized_source_versions = source_versions if isinstance(source_versions, dict) else {}
    if scope_context is not None:
        normalized_source_versions = {**normalized_source_versions, "scope_context": scope_context.to_meta()}
    payload = {
        "base_id": base_id,
        "title": str(title or "").strip(),
        "content": str(content or "").strip(),
        "source_versions": normalized_source_versions,
        "source": source,
        "saved_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scope_context": scope_context.to_meta() if scope_context is not None else {},
    }
    reply_path = os.path.join(stage_dir(user_id, base_id, "reply", reply_version, scope_context=scope_context), "final.json")
    await write_json_file(reply_path, payload)
    meta_payload = {"task": "reply_draft"}
    if isinstance(meta_extra, dict):
        meta_payload.update({k: v for k, v in meta_extra.items() if v is not None})
    write_meta(user_id, base_id, "reply", reply_version, source=source, base_versions=normalized_source_versions, extra=meta_payload, scope_context=scope_context, updated_by=user_id)
    manifest = update_manifest(manifest, "reply", reply_version)
    save_manifest(user_id, base_id, manifest, scope_context=scope_context)
    return payload, reply_version


async def save_reply_draft_by_file_id(user_id: str, file_id: str, title: str, content: str, source_versions: dict[str, Any] | None = None) -> dict[str, Any]:
    """按 file_id 保存用户草稿。"""
    user_id = ensure_user_id(user_id)
    base_id = build_base_id(file_id)
    payload, reply_version = await save_reply_result(user_id, base_id, title, content, source_versions or {}, source="user")
    saved_path = os.path.join(stage_dir(user_id, base_id, "reply", reply_version), "final.json")
    logger.info("[complaint_api] reply_draft_saved user_id=%s base_id=%s reply_version=%s path=%s", user_id, base_id, reply_version, saved_path)
    return {"base_id": base_id, "reply_version": reply_version, "saved_at": payload.get("saved_at")}


def load_reply_payload(user_id: str, base_id: str, version: str, scope_context: ComplaintScopeContext | None = None) -> tuple[dict[str, Any], int]:
    """读取指定版本或最新版本文书。"""
    if scope_context is None:
        manifest = load_manifest(user_id, base_id)
        if str(version or "").lower() == "latest":
            target_version = int(manifest.get("reply", {}).get("current") or 0)
        else:
            try:
                target_version = int(version)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"version 非法: {exc}")
        if target_version <= 0:
            raise HTTPException(status_code=404, detail="未找到可下载的文书草稿")
        reply_path = os.path.join(stage_dir(user_id, base_id, "reply", target_version), "final.json")
        if not os.path.isfile(reply_path):
            raise HTTPException(status_code=404, detail="文书草稿不存在")
        try:
            with open(reply_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"读取文书草稿失败: {exc}")
        if not isinstance(payload, dict):
            raise HTTPException(status_code=500, detail="文书草稿格式错误")
        return payload, target_version
    for read_scope in complaint_scope_read_chain(scope_context):
        manifest = load_manifest(user_id, base_id, scope_context=read_scope)
        if str(version or "").lower() == "latest":
            target_version = int(manifest.get("reply", {}).get("current") or 0)
        else:
            try:
                target_version = int(version)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"version 非法: {exc}")
        if target_version <= 0:
            continue
        reply_path = os.path.join(stage_dir(user_id, base_id, "reply", target_version, scope_context=read_scope), "final.json")
        if not os.path.isfile(reply_path):
            continue
        try:
            with open(reply_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"读取文书草稿失败: {exc}")
        if not isinstance(payload, dict):
            raise HTTPException(status_code=500, detail="文书草稿格式错误")
        payload.setdefault("scope_context", read_scope.to_meta())
        return payload, target_version
    raise HTTPException(status_code=404, detail="未找到可下载的文书草稿")


async def render_reply_docx(user_id: str, base_id: str, payload: dict[str, Any], version: int, scope_context: ComplaintScopeContext | None = None) -> str:
    """将草稿渲染为 docx 文件并返回路径。"""
    title = str(payload.get("title") or "答复意见书").strip() or "答复意见书"
    content = str(payload.get("content") or "").strip()
    output_dir = stage_dir(user_id, base_id, "reply", version, scope_context=scope_context)
    output_path = os.path.join(output_dir, "final.docx")
    await run_in_threadpool(_build_docx_sync, output_path, title, content)
    return output_path


def _build_docx_sync(output_path: str, title: str, content: str) -> None:
    """同步构建 docx 文件。"""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    doc = Document(REPLY_DRAFT_TEMPLATE_PATH) if os.path.isfile(REPLY_DRAFT_TEMPLATE_PATH) else Document()
    _clear_docx_body(doc)
    for paragraph in str(content or "").split("\n"):
        line = paragraph.strip()
        if line:
            doc.add_paragraph(line)
    doc.save(output_path)


def _clear_docx_body(doc: Document) -> None:
    """清空模板正文，保留页面节设置。"""
    body = doc._body._element
    section_props = body.sectPr
    for child in list(body):
        body.remove(child)
    if section_props is not None:
        body.append(section_props)
