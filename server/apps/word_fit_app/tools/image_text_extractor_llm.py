"""OpenAI-style OCR implementation with diskcache-based deduplication."""

from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import json
import hashlib
import mimetypes
import os
import threading
import time
from pathlib import Path
from typing import Dict, Optional

import fitz  # PyMuPDF
from diskcache import Cache
from dotenv import load_dotenv
from openai import OpenAI

from utils.logger import get_logger
from utils.llm_thinking import build_thinking_extra_body
from utils.text_cleanup import cleanup_markdown_blank_lines
from utils.timing import timing_elapsed_ms, timing_now
from apps.complaint_app.core.model_config import get_ocr_llm_model

logger = get_logger(__name__)

load_dotenv()

OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OCR_LLM_MODEL = get_ocr_llm_model()
OCR_CACHE_DIR = os.getenv("OCR_CACHE_DIR") or os.path.join("uploads", "diskcache")
OCR_LLM_TIMEOUT = int(os.getenv("OCR_LLM_TIMEOUT", "600"))
OCR_LLM_TEMPERATURE = float(os.getenv("OCR_LLM_TEMPERATURE", "0.2"))
OCR_LLM_TOP_P = float(os.getenv("OCR_LLM_TOP_P", "0.85"))
WORDFIT_OCR_PDF_MAX_WORKERS = max(1, int(os.getenv("WORDFIT_OCR_PDF_MAX_WORKERS", "20")))
WORDFIT_OCR_ENABLE_THINKING = (os.getenv("WORDFIT_OCR_ENABLE_THINKING", "").strip().lower() in {"1", "true", "yes", "y", "on"})
WORDFIT_OCR_MAX_WAIT_SECONDS = max(0.0, float(os.getenv("WORDFIT_OCR_MAX_WAIT_SECONDS", "10") or "10"))

_TEXT_FALLBACK_DIR = (
    os.getenv("OCR_TEXT_FALLBACK_DIR") or os.path.join("uploads", "texts")
)
_OCR_METRICS_DIR = os.getenv("WORDFIT_OCR_METRICS_DIR") or os.path.join("uploads", "ocr_metrics")
os.makedirs(OCR_CACHE_DIR, exist_ok=True)
os.makedirs(_TEXT_FALLBACK_DIR, exist_ok=True)

_cache = Cache(OCR_CACHE_DIR)
_client: Optional[OpenAI] = None
_metrics_lock = threading.Lock()


def _get_client() -> OpenAI:
    global _client  # pylint: disable=global-statement

    if _client is None:
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY 未配置，无法执行OCR识别")
    
        _client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
    return _client


def _extract_composed_text(completion) -> str:
    choices = getattr(completion, "choices", None) or []
    if not choices:
        return ""
    message = choices[0].message
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()

    # 新版 SDK 可能返回结构化内容
    aggregated = []
    for block in content:
        if isinstance(block, dict):
            text = block.get("text") or block.get("value")
            if text:
                aggregated.append(text)
        elif isinstance(block, str):
            aggregated.append(block)
    return "".join(aggregated).strip()


def _extract_stream_chunk_text(chunk) -> str:
    choices = getattr(chunk, "choices", None) or []
    if not choices:
        return ""
    delta = getattr(choices[0], "delta", None)
    if delta is None:
        return ""

    content = getattr(delta, "content", "")
    if isinstance(content, str):
        return content

    aggregated = []
    for block in content or []:
        if isinstance(block, dict):
            text = block.get("text") or block.get("value")
            if text:
                aggregated.append(text)
        elif isinstance(block, str):
            aggregated.append(block)
    return "".join(aggregated)


sys_prmpt="""你是一个OCR文本提取器。
你的核心任务是：从图片中尽量忠实地提取原文内容，保持原有段落、顺序和换行。

总规则：
1. 默认输出纯文本，不要解释，不要前后缀，不要总结，不要改写。
2. 不要根据字段列表输出字段 key-value、JSON、表单填空结果或冒号分隔的结构化内容。
3. 如果图片里没有某些业务字段，不要自己补字段名，也不要输出空字段。
4. 对正文主体内容要优先完整保留，不能因为字段列表而省略正文。
5. 字段列表如果存在，只能作为辅助参考，不能改变“提取原文”这个主任务。

表格规则：
1. 与正文理解明显相关的表格，尽量按原文提取，可用纯文本或必要时用Markdown表格表达。
2. 对明显无关的超长流水表、交易明细、账单对账单，可压缩无关部分，不要重建整张表。
3. 只有在整张图片几乎全部是与正文无关的流水/明细表时，才可输出空字符串。

输出格式：
1. 输出图片中的原文内容本身。
2. 不输出代码块，不输出额外说明。
3. 除非图片原文中本来就有，否则不要输出字段名或英文 key。"""

def _resolve_ocr_model(model: Optional[str] = None) -> str:
    """返回本次 OCR 使用的模型名。"""
    return str(model or "").strip() or OCR_LLM_MODEL


def _invoke_llm(image_b64: str, mime_type: str = "image/png", prompt: Optional[str] = None, image_path: Optional[str] = None, page_index: Optional[int] = None, page_started_at_text: Optional[str] = None, persist_target_path: Optional[str] = None, model: Optional[str] = None, base_id: Optional[str] = None, user_id: Optional[str] = None) -> dict[str, object]:
    client = _get_client()
    model_name = _resolve_ocr_model(model)
    extra_body = build_thinking_extra_body(model_name, WORDFIT_OCR_ENABLE_THINKING)
    prompt_chars = len(sys_prmpt) + len(prompt or "")
    
    user_content = [
        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_b64}"}}
    ]
    if prompt:
        user_content.append({"type": "text", "text": prompt})
        
    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "system",
                "content": [
                    {"type": "text", "text": sys_prmpt},
                ],
            },
            {
                "role": "user",
                "content": user_content,
            }
        ],
        "timeout": OCR_LLM_TIMEOUT,
        "temperature": OCR_LLM_TEMPERATURE,
        "top_p": OCR_LLM_TOP_P,
    }
    if extra_body:
        payload["extra_body"] = extra_body

    retry_delay = 1.0
    max_retries = 3
    for attempt in range(max_retries):
        try:
            stream = client.chat.completions.create(**payload, stream=True)
            started_at = time.monotonic()
            first_token_ms: Optional[int] = None
            aggregated = []
            timeout_reached = False
            try:
                for chunk in stream:
                    text = _extract_stream_chunk_text(chunk)
                    if text:
                        if first_token_ms is None:
                            first_token_ms = int((time.monotonic() - started_at) * 1000)
                        aggregated.append(text)
                    if WORDFIT_OCR_MAX_WAIT_SECONDS > 0 and (time.monotonic() - started_at) >= WORDFIT_OCR_MAX_WAIT_SECONDS:
                        timeout_reached = True
                        timeout_at = datetime.now()
                        page_text = "".join(aggregated).strip()
                        logger.warning("[OCR] llm stream timeout after %.2fs user_id=%s base_id=%s model=%s file=%s page=%s started_at=%s timeout_at=%s ocr_elapsed_ms=%s text_len=%s saved_path=%s", WORDFIT_OCR_MAX_WAIT_SECONDS, user_id or "", base_id or "", model_name, image_path, page_index, page_started_at_text, timeout_at.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], int((time.monotonic() - started_at) * 1000), len(page_text), persist_target_path)
                        break
            finally:
                close = getattr(stream, "close", None)
                if callable(close):
                    close()
            text_content = "".join(aggregated).strip()
            llm_duration_ms = int((time.monotonic() - started_at) * 1000)
            result = {
                "text": text_content,
                "model": model_name,
                "prompt_chars": prompt_chars,
                "image_b64_chars": len(image_b64),
                "output_chars": len(text_content),
                "first_token_ms": first_token_ms or 0,
                "llm_duration_ms": llm_duration_ms,
            }
            if timeout_reached:
                return result
            if text_content:
                return result
            raise RuntimeError("LLM 流式调用未返回有效内容")
        except Exception as exc:  # pylint: disable=broad-except
            print(f"[DEBUG OCR-LLM] 调用失败 (尝试 {attempt + 1}/{max_retries}): {exc}")
            if attempt >= max_retries - 1:
                raise
            time.sleep(retry_delay)
            retry_delay *= 2

    raise RuntimeError("LLM 调用失败")


def _safe_path_segment(value: str, default: str, max_length: int = 80) -> str:
    segment = _safe_metric_segment(value).strip("._-")
    if not segment:
        segment = default
    return segment[:max_length]


def _file_text_segment(image_path: str) -> str:
    path = Path(image_path)
    stem = _safe_path_segment(path.stem, "file", max_length=60)
    path_hash = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:12]
    return f"{stem}-{path_hash}"


def _resolve_base_text_dir(image_path: str) -> Path:
    path = Path(image_path).resolve()
    parts = path.parts
    if "complaints" in parts:
        idx = parts.index("complaints")
        for stage_name in ("original", "images"):
            if stage_name in parts[idx + 1:]:
                stage_idx = parts.index(stage_name, idx + 1)
                text_dir = Path(*parts[:stage_idx], "ocr_texts")
                text_dir.mkdir(parents=True, exist_ok=True)
                return text_dir
        if idx + 1 < len(parts):
            text_dir = Path(*parts[: idx + 2], "ocr_texts")
            text_dir.mkdir(parents=True, exist_ok=True)
            return text_dir

    fallback = Path(_TEXT_FALLBACK_DIR).resolve()
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def _resolve_text_dir(image_path: str, base_id: Optional[str] = None, request_id: Optional[str] = None) -> Path:
    base_dir = _resolve_base_text_dir(image_path)
    if "complaints" in Path(image_path).resolve().parts:
        return base_dir
    base_segment = _safe_path_segment(str(base_id or ""), "no_base_id")
    request_segment = _safe_path_segment(str(request_id or "") or _request_id_from_path(image_path), "no_request")
    text_dir = base_dir / base_segment / request_segment / _file_text_segment(image_path)
    text_dir.mkdir(parents=True, exist_ok=True)
    return text_dir


def _persist_text(image_path: str, text: str, base_id: Optional[str] = None, request_id: Optional[str] = None) -> str:
    text_dir = _resolve_text_dir(image_path, base_id=base_id, request_id=request_id)
    image_stem = Path(image_path).stem
    target_path = text_dir / f"{image_stem}_ocr.txt"
    normalized = text.rstrip("\n")
    with open(target_path, "w", encoding="utf-8") as file_obj:
        if normalized:
            file_obj.write(normalized + "\n")
        else:
            file_obj.write("")
    return str(target_path)


def _persist_page_text(image_path: str, page_index: int, text: str, base_id: Optional[str] = None, request_id: Optional[str] = None) -> str:
    text_dir = _resolve_text_dir(image_path, base_id=base_id, request_id=request_id)
    image_stem = Path(image_path).stem
    target_path = text_dir / f"{image_stem}_page_{page_index + 1}_ocr.txt"
    normalized = text.rstrip("\n")
    with open(target_path, "w", encoding="utf-8") as file_obj:
        if normalized:
            file_obj.write(normalized + "\n")
        else:
            file_obj.write("")
    return str(target_path)


def _load_cached_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            return file_obj.read()
    except FileNotFoundError:
        return None


def _safe_metric_segment(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in str(value or "").strip())


def _request_id_from_path(image_path: str) -> str:
    normalized = str(image_path or "").replace("\\", "/")
    marker = "/runs/"
    if marker not in normalized:
        return ""
    tail = normalized.split(marker, 1)[1]
    return tail.split("/", 1)[0].strip()


def _write_ocr_metric(metrics_batch_id: Optional[str], request_id: Optional[str], payload: dict[str, object]) -> None:
    batch = _safe_metric_segment(str(metrics_batch_id or ""))
    resolved_request_id = _safe_metric_segment(str(request_id or "")) or _safe_metric_segment(_request_id_from_path(str(payload.get("file_path") or "")))
    if not batch or not resolved_request_id:
        return
    target_dir = Path(_OCR_METRICS_DIR).resolve() / batch
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{resolved_request_id}.jsonl"
    row = {"request_id": resolved_request_id, **payload}
    with _metrics_lock:
        with open(target_path, "a", encoding="utf-8") as file_obj:
            file_obj.write(json.dumps(row, ensure_ascii=False) + "\n")
    logger.info("[OCR] metrics written user_id=%s base_id=%s path=%s type=%s request_id=%s", payload.get("user_id") or "", payload.get("base_id") or "", target_path, payload.get("type") or "", resolved_request_id)


def _process_pdf_page(image_path: str, page_index: int, prompt: Optional[str] = None, model: Optional[str] = None, base_id: Optional[str] = None, user_id: Optional[str] = None) -> tuple[int, str, str, str, int, int, int, dict[str, object]]:
    page_started_at = timing_now()
    page_started_at_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    persist_target_path = str(_resolve_text_dir(image_path, base_id=base_id) / f"{Path(image_path).stem}_page_{page_index + 1}_ocr.txt")
    render_started_at = timing_now()
    doc = fitz.open(image_path)
    try:
        page = doc.load_page(page_index)
        pix = page.get_pixmap(matrix=fitz.Matrix(1.3, 1.3))
        img_bytes = pix.tobytes("png")
    finally:
        doc.close()
    render_duration_ms = timing_elapsed_ms(render_started_at)

    img_b64 = base64.b64encode(img_bytes).decode("utf-8")
    ocr_started_at = timing_now()
    llm_result = _invoke_llm(
        img_b64,
        mime_type="image/png",
        prompt=prompt,
        image_path=image_path,
        page_index=page_index + 1,
        page_started_at_text=page_started_at_text,
        persist_target_path=persist_target_path,
        model=model,
        base_id=base_id,
        user_id=user_id,
    )
    page_text = str(llm_result.get("text") or "")
    saved_path = _persist_page_text(image_path, page_index, page_text, base_id=base_id)
    ocr_duration_ms = timing_elapsed_ms(ocr_started_at)
    page_duration_ms = timing_elapsed_ms(page_started_at)
    return page_index, page_text, saved_path, page_started_at_text, render_duration_ms, ocr_duration_ms, page_duration_ms, llm_result


def _build_ocr_cache_entry(image_path: str, model: Optional[str], model_name: str) -> tuple[str | None, str | None, str | None]:
    if not os.path.exists(image_path):
        return None, None, None
    with open(image_path, "rb") as file_obj:
        image_bytes = file_obj.read()
    digest = hashlib.sha256(image_bytes).hexdigest()
    cache_key = f"ocr:sha256:{digest}" if not model else f"ocr:model:{model_name}:sha256:{digest}"
    return digest, cache_key, mimetypes.guess_type(image_path)[0] or "image/png"


def recognize_pdf_files_concurrently(pdf_paths: list[str], force: bool = False, prompt: Optional[str] = None, progress_callback=None, model: Optional[str] = None, metrics_batch_id: Optional[str] = None, request_id: Optional[str] = None, base_id: Optional[str] = None, user_id: Optional[str] = None, image_progress_as_page: bool = False) -> dict[str, Dict[str, Optional[str]]]:
    """并发识别多个 PDF 的所有页面，并按文件返回 OCR 文本。"""
    model_name = _resolve_ocr_model(model)
    recognize_started_at = timing_now()
    results: dict[str, Dict[str, Optional[str]]] = {}
    pending: dict[str, dict[str, object]] = {}
    total_pages = 0

    for image_path in pdf_paths:
        digest, cache_key, mime_type = _build_ocr_cache_entry(image_path, model, model_name)
        if not digest or not cache_key:
            error_msg = f"错误：文件不存在 {os.path.basename(image_path)}"
            logger.error("[OCR] %s user_id=%s base_id=%s file=%s", error_msg, user_id or "", base_id or "", image_path)
            results[image_path] = {"sha256": None, "text_content": error_msg, "cache_hit": False}
            continue
        logger.info("[OCR] 文件哈希 user_id=%s base_id=%s file=%s digest=%s cache_key=%s", user_id or "", base_id or "", image_path, digest, cache_key)

        if not force:
            cached_path = _cache.get(cache_key)
            if cached_path:
                text_content = _load_cached_text(cached_path)
                if text_content is not None:
                    logger.info("[OCR] 缓存命中 user_id=%s base_id=%s file=%s cached_path=%s", user_id or "", base_id or "", image_path, cached_path)
                    _write_ocr_metric(metrics_batch_id, request_id, {"type": "ocr_file", "user_id": user_id or "", "base_id": base_id or "", "file_path": image_path, "duration_ms": 0, "model": model_name, "thinking": WORDFIT_OCR_ENABLE_THINKING, "cache_hit": True, "mime_type": "cache", "pages": 0, "workers": 0})
                    results[image_path] = {"sha256": digest, "text_content": text_content, "cache_hit": True}
                    continue
        if mime_type != "application/pdf":
            results[image_path] = recognize_image(image_path, force, prompt, progress_callback if image_progress_as_page else None, model, metrics_batch_id, request_id, base_id, user_id)
            continue

        try:
            doc = fitz.open(image_path)
            try:
                page_count = len(doc)
            finally:
                doc.close()
        except Exception as exc:  # pylint: disable=broad-except
            error_msg = f"PDF处理失败: {str(exc)}"
            logger.error("[OCR] %s user_id=%s base_id=%s file=%s", error_msg, user_id or "", base_id or "", image_path)
            results[image_path] = {"sha256": digest, "text_content": error_msg, "cache_hit": False}
            continue
        if page_count <= 0:
            error_msg = "PDF处理失败: PDF页数为空"
            logger.error("[OCR] %s user_id=%s base_id=%s file=%s", error_msg, user_id or "", base_id or "", image_path)
            results[image_path] = {"sha256": digest, "text_content": error_msg, "cache_hit": False}
            continue
        logger.info("[OCR] PDF页数 user_id=%s base_id=%s file=%s pages=%s", user_id or "", base_id or "", image_path, page_count)
        pending[image_path] = {"digest": digest, "cache_key": cache_key, "page_count": page_count, "page_texts": [""] * page_count}
        total_pages += page_count

    if not pending:
        return {path: results[path] for path in pdf_paths if path in results}

    completed_pages = 0
    max_workers = max(1, min(total_pages, WORDFIT_OCR_PDF_MAX_WORKERS))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_process_pdf_page, image_path, page_index, prompt, model_name, base_id, user_id): (image_path, page_index)
            for image_path, item in pending.items()
            for page_index in range(int(item["page_count"]))
        }
        for future in as_completed(futures):
            image_path, fallback_page_index = futures[future]
            page_finished_at_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            try:
                page_index, page_text, saved_path, page_started_at_text, render_duration_ms, ocr_duration_ms, page_duration_ms, llm_result = future.result()
            except Exception as page_exc:
                page_index = fallback_page_index
                page_text = f"OCR识别失败：{page_exc}"
                saved_path = _persist_page_text(image_path, page_index, page_text, base_id=base_id, request_id=request_id)
                logger.error("[OCR] 第 %s 页处理失败 user_id=%s base_id=%s file=%s page=%s finished_at=%s err=%s saved_path=%s", page_index + 1, user_id or "", base_id or "", image_path, page_index + 1, page_finished_at_text, page_exc, saved_path)
                _write_ocr_metric(metrics_batch_id, request_id, {"type": "ocr_page", "user_id": user_id or "", "base_id": base_id or "", "file_path": image_path, "page": page_index + 1, "finished_at": page_finished_at_text, "duration_ms": 0, "text_len": len(page_text), "saved_path": saved_path, "record_type": "pdf_page", "error": str(page_exc)})
            else:
                logger.info("[OCR] 第 %s 页处理完成 user_id=%s base_id=%s file=%s page=%s started_at=%s finished_at=%s render_ms=%s ocr_ms=%s duration_ms=%s text_len=%s saved_path=%s", page_index + 1, user_id or "", base_id or "", image_path, page_index + 1, page_started_at_text, page_finished_at_text, render_duration_ms, ocr_duration_ms, page_duration_ms, len(page_text), saved_path)
                _write_ocr_metric(metrics_batch_id, request_id, {"type": "ocr_page", "user_id": user_id or "", "base_id": base_id or "", "file_path": image_path, "page": page_index + 1, "started_at": page_started_at_text, "finished_at": page_finished_at_text, "render_ms": render_duration_ms, "ocr_ms": ocr_duration_ms, "duration_ms": page_duration_ms, "text_len": len(page_text), "saved_path": saved_path, "record_type": "pdf_page"})
                _write_ocr_metric(metrics_batch_id, request_id, {"type": "ocr_llm", "user_id": user_id or "", "base_id": base_id or "", "file_path": image_path, "page": page_index + 1, "model": llm_result.get("model") or model_name, "prompt_chars": llm_result.get("prompt_chars") or 0, "image_b64_chars": llm_result.get("image_b64_chars") or 0, "output_chars": llm_result.get("output_chars") or len(page_text), "first_token_ms": llm_result.get("first_token_ms") or 0, "llm_duration_ms": llm_result.get("llm_duration_ms") or ocr_duration_ms})
            pending[image_path]["page_texts"][page_index] = page_text
            completed_pages += 1
            if progress_callback is not None:
                progress_callback(completed_pages, total_pages)

    for image_path, item in pending.items():
        recognized_text = cleanup_markdown_blank_lines("\n\n".join(item["page_texts"]))
        if not recognized_text.strip():
            recognized_text = "PDF处理失败: PDF所有页面OCR均未返回有效内容"
            logger.error("[OCR] %s user_id=%s base_id=%s file=%s", recognized_text, user_id or "", base_id or "", image_path)
        saved_path = _persist_text(image_path, recognized_text, base_id=base_id, request_id=request_id)
        _cache.set(str(item["cache_key"]), saved_path)
        file_duration_ms = timing_elapsed_ms(recognize_started_at)
        logger.info("[timing] app=word_fit_app kind=ocr_file user_id=%s base_id=%s file=%s duration_ms=%s model=%s thinking=%s cache_hit=%s mime_type=%s pages=%s workers=%s", user_id or "", base_id or "", image_path, file_duration_ms, model_name, WORDFIT_OCR_ENABLE_THINKING, False, "application/pdf", item["page_count"], max_workers)
        _write_ocr_metric(metrics_batch_id, request_id, {"type": "ocr_file", "user_id": user_id or "", "base_id": base_id or "", "file_path": image_path, "duration_ms": file_duration_ms, "model": model_name, "thinking": WORDFIT_OCR_ENABLE_THINKING, "cache_hit": False, "mime_type": "application/pdf", "pages": item["page_count"], "workers": max_workers})
        results[image_path] = {"sha256": str(item["digest"]), "text_content": recognized_text, "cache_hit": False}

    return {path: results[path] for path in pdf_paths if path in results}


def recognize_image(image_path: str, force: bool = False, prompt: Optional[str] = None, progress_callback=None, model: Optional[str] = None, metrics_batch_id: Optional[str] = None, request_id: Optional[str] = None, base_id: Optional[str] = None, user_id: Optional[str] = None) -> Dict[str, Optional[str]]:
    model_name = _resolve_ocr_model(model)
    logger.info("[OCR] 开始处理文件 user_id=%s base_id=%s file=%s force=%s model=%s", user_id or "", base_id or "", image_path, force, model_name)
    recognize_started_at = timing_now()
    try:
        if not os.path.exists(image_path):
            error_msg = f"错误：文件不存在 {os.path.basename(image_path)}"
            logger.error("[OCR] %s user_id=%s base_id=%s file=%s", error_msg, user_id or "", base_id or "", image_path)
            return {
                "sha256": None,
                "text_content": error_msg,
                "cache_hit": False,
            }

        with open(image_path, "rb") as file_obj:
            image_bytes = file_obj.read()

        digest = hashlib.sha256(image_bytes).hexdigest()
        cache_key = f"ocr:sha256:{digest}" if not model else f"ocr:model:{model_name}:sha256:{digest}"
        logger.info("[OCR] 文件哈希 user_id=%s base_id=%s file=%s digest=%s cache_key=%s", user_id or "", base_id or "", image_path, digest, cache_key)

        if not force:
            cached_path = _cache.get(cache_key)
            if cached_path:
                text_content = _load_cached_text(cached_path)
                if text_content is not None:
                    logger.info("[OCR] 缓存命中 user_id=%s base_id=%s file=%s cached_path=%s", user_id or "", base_id or "", image_path, cached_path)
                    file_duration_ms = timing_elapsed_ms(recognize_started_at)
                    logger.info("[timing] app=word_fit_app kind=ocr_file user_id=%s base_id=%s file=%s duration_ms=%s model=%s thinking=%s cache_hit=%s mime_type=%s", user_id or "", base_id or "", image_path, file_duration_ms, model_name, WORDFIT_OCR_ENABLE_THINKING, True, "cache")
                    _write_ocr_metric(metrics_batch_id, request_id, {"type": "ocr_file", "user_id": user_id or "", "base_id": base_id or "", "file_path": image_path, "duration_ms": file_duration_ms, "model": model_name, "thinking": WORDFIT_OCR_ENABLE_THINKING, "cache_hit": True, "mime_type": "cache", "pages": 0, "workers": 0})
                    return {
                        "sha256": digest,
                        "text_content": text_content,
                        "cache_hit": True,
                    }
            logger.info("[OCR] 缓存未命中 user_id=%s base_id=%s file=%s", user_id or "", base_id or "", image_path)

        # Determine MIME type
        mime_type, _ = mimetypes.guess_type(image_path)
        if not mime_type:
            mime_type = "image/png"  # Default
        logger.info("[OCR] 文件MIME类型 user_id=%s base_id=%s file=%s mime_type=%s", user_id or "", base_id or "", image_path, mime_type)

        recognized_text = ""

        if mime_type == "application/pdf":
            # PDF Handling: Convert pages to images using PyMuPDF (fitz)
            # This is necessary because many LLMs do not accept PDF base64 directly
            try:
                logger.info("[OCR] 开始处理PDF文件 user_id=%s base_id=%s file=%s", user_id or "", base_id or "", image_path)
                doc = fitz.open(image_path)
                page_count = len(doc)
                doc.close()
                logger.info("[OCR] PDF页数 user_id=%s base_id=%s file=%s pages=%s", user_id or "", base_id or "", image_path, page_count)
                page_texts = [""] * page_count
                max_workers = min(page_count, WORDFIT_OCR_PDF_MAX_WORKERS)
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = {
                        executor.submit(_process_pdf_page, image_path, page_index, prompt, model_name, base_id, user_id): page_index
                        for page_index in range(page_count)
                    }
                    for future in as_completed(futures):
                        page_finished_at_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                        try:
                            page_index, page_text, saved_path, page_started_at_text, render_duration_ms, ocr_duration_ms, page_duration_ms, llm_result = future.result()
                        except Exception as page_exc:
                            page_index = futures[future]
                            page_text = f"OCR识别失败：{page_exc}"
                            saved_path = _persist_page_text(image_path, page_index, page_text, base_id=base_id, request_id=request_id)
                            page_texts[page_index] = page_text
                            logger.error("[OCR] 第 %s 页处理失败 user_id=%s base_id=%s file=%s page=%s finished_at=%s err=%s saved_path=%s", page_index + 1, user_id or "", base_id or "", image_path, page_index + 1, page_finished_at_text, page_exc, saved_path)
                            _write_ocr_metric(metrics_batch_id, request_id, {"type": "ocr_page", "user_id": user_id or "", "base_id": base_id or "", "file_path": image_path, "page": page_index + 1, "finished_at": page_finished_at_text, "duration_ms": 0, "text_len": len(page_text), "saved_path": saved_path, "record_type": "pdf_page", "error": str(page_exc)})
                            if progress_callback is not None:
                                progress_callback(sum(1 for text in page_texts if text != ""), page_count)
                            continue
                        page_texts[page_index] = page_text
                        logger.info("[OCR] 第 %s 页处理完成 user_id=%s base_id=%s file=%s page=%s started_at=%s finished_at=%s render_ms=%s ocr_ms=%s duration_ms=%s text_len=%s saved_path=%s", page_index + 1, user_id or "", base_id or "", image_path, page_index + 1, page_started_at_text, page_finished_at_text, render_duration_ms, ocr_duration_ms, page_duration_ms, len(page_text), saved_path)
                        _write_ocr_metric(metrics_batch_id, request_id, {"type": "ocr_page", "user_id": user_id or "", "base_id": base_id or "", "file_path": image_path, "page": page_index + 1, "started_at": page_started_at_text, "finished_at": page_finished_at_text, "render_ms": render_duration_ms, "ocr_ms": ocr_duration_ms, "duration_ms": page_duration_ms, "text_len": len(page_text), "saved_path": saved_path, "record_type": "pdf_page"})
                        _write_ocr_metric(metrics_batch_id, request_id, {"type": "ocr_llm", "user_id": user_id or "", "base_id": base_id or "", "file_path": image_path, "page": page_index + 1, "model": llm_result.get("model") or model_name, "prompt_chars": llm_result.get("prompt_chars") or 0, "image_b64_chars": llm_result.get("image_b64_chars") or 0, "output_chars": llm_result.get("output_chars") or len(page_text), "first_token_ms": llm_result.get("first_token_ms") or 0, "llm_duration_ms": llm_result.get("llm_duration_ms") or ocr_duration_ms})
                        if progress_callback is not None:
                            progress_callback(sum(1 for text in page_texts if text != ""), page_count)
                recognized_text = "\n\n".join(page_texts)
                if not recognized_text.strip():
                    raise RuntimeError("PDF所有页面OCR均未返回有效内容")
                logger.info("[OCR] PDF处理完成 user_id=%s base_id=%s file=%s pages=%s", user_id or "", base_id or "", image_path, page_count)
                file_duration_ms = timing_elapsed_ms(recognize_started_at)
                logger.info("[timing] app=word_fit_app kind=ocr_file user_id=%s base_id=%s file=%s duration_ms=%s model=%s thinking=%s cache_hit=%s mime_type=%s pages=%s workers=%s", user_id or "", base_id or "", image_path, file_duration_ms, model_name, WORDFIT_OCR_ENABLE_THINKING, False, mime_type, page_count, max_workers)
                _write_ocr_metric(metrics_batch_id, request_id, {"type": "ocr_file", "user_id": user_id or "", "base_id": base_id or "", "file_path": image_path, "duration_ms": file_duration_ms, "model": model_name, "thinking": WORDFIT_OCR_ENABLE_THINKING, "cache_hit": False, "mime_type": mime_type, "pages": page_count, "workers": max_workers})
                
            except Exception as e:
                error_msg = f"PDF处理失败: {str(e)}"
                logger.error("[OCR] %s user_id=%s base_id=%s file=%s", error_msg, user_id or "", base_id or "", image_path)
                return {
                    "sha256": digest,
                    "text_content": error_msg,
                    "cache_hit": False,
                }
        else:
            # Standard Image Handling
            logger.info("[OCR] 开始处理图片文件 user_id=%s base_id=%s file=%s", user_id or "", base_id or "", image_path)
            image_b64 = base64.b64encode(image_bytes).decode("utf-8")
            image_started_at_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            persist_target_path = str(_resolve_text_dir(image_path, base_id=base_id, request_id=request_id) / f"{Path(image_path).stem}_ocr.txt")
            llm_result = _invoke_llm(
                image_b64,
                mime_type=mime_type,
                prompt=prompt,
                image_path=image_path,
                page_index=1,
                page_started_at_text=image_started_at_text,
                persist_target_path=persist_target_path,
                model=model_name,
                base_id=base_id,
                user_id=user_id,
            )
            recognized_text = str(llm_result.get("text") or "")
            recognized_text = cleanup_markdown_blank_lines(recognized_text)
            saved_path = _persist_text(image_path, recognized_text, base_id=base_id, request_id=request_id)
            image_finished_at_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            image_duration_ms = timing_elapsed_ms(recognize_started_at)
            logger.info("[OCR] 图片处理完成 user_id=%s base_id=%s file=%s page=%s started_at=%s finished_at=%s ocr_ms=%s duration_ms=%s text_len=%s saved_path=%s", user_id or "", base_id or "", image_path, 1, image_started_at_text, image_finished_at_text, image_duration_ms, image_duration_ms, len(recognized_text), saved_path)
            _write_ocr_metric(metrics_batch_id, request_id, {"type": "ocr_page", "user_id": user_id or "", "base_id": base_id or "", "file_path": image_path, "page": 1, "started_at": image_started_at_text, "finished_at": image_finished_at_text, "render_ms": 0, "ocr_ms": image_duration_ms, "duration_ms": image_duration_ms, "text_len": len(recognized_text), "saved_path": saved_path, "record_type": "image"})
            _write_ocr_metric(metrics_batch_id, request_id, {"type": "ocr_llm", "user_id": user_id or "", "base_id": base_id or "", "file_path": image_path, "page": 1, "model": llm_result.get("model") or model_name, "prompt_chars": llm_result.get("prompt_chars") or 0, "image_b64_chars": llm_result.get("image_b64_chars") or 0, "output_chars": llm_result.get("output_chars") or len(recognized_text), "first_token_ms": llm_result.get("first_token_ms") or 0, "llm_duration_ms": llm_result.get("llm_duration_ms") or image_duration_ms})
            if progress_callback is not None:
                progress_callback(1, 1)
            logger.info("[timing] app=word_fit_app kind=ocr_file user_id=%s base_id=%s file=%s duration_ms=%s model=%s thinking=%s cache_hit=%s mime_type=%s pages=%s workers=%s", user_id or "", base_id or "", image_path, image_duration_ms, model_name, WORDFIT_OCR_ENABLE_THINKING, False, mime_type, 1, 1)
            _write_ocr_metric(metrics_batch_id, request_id, {"type": "ocr_file", "user_id": user_id or "", "base_id": base_id or "", "file_path": image_path, "duration_ms": image_duration_ms, "model": model_name, "thinking": WORDFIT_OCR_ENABLE_THINKING, "cache_hit": False, "mime_type": mime_type, "pages": 1, "workers": 1})
            _cache.set(cache_key, saved_path)
            logger.info("[OCR] 结果已保存并缓存 user_id=%s base_id=%s file=%s saved_path=%s", user_id or "", base_id or "", image_path, saved_path)
            return {"sha256": digest, "text_content": recognized_text, "cache_hit": False}

        # Cleanup and persist
        recognized_text = cleanup_markdown_blank_lines(recognized_text)
        saved_path = _persist_text(image_path, recognized_text, base_id=base_id, request_id=request_id)
        _cache.set(cache_key, saved_path)
        logger.info("[OCR] 结果已保存并缓存 user_id=%s base_id=%s file=%s saved_path=%s", user_id or "", base_id or "", image_path, saved_path)

        return {
            "sha256": digest,
            "text_content": recognized_text,
            "cache_hit": False,
        }

    except Exception as exc:  # pylint: disable=broad-except
        error_msg = f"OCR识别失败：{exc} - {os.path.basename(image_path)}"
        logger.error("[OCR] %s user_id=%s base_id=%s file=%s", error_msg, user_id or "", base_id or "", image_path, exc_info=True)
        return {
            "sha256": None,
            "text_content": error_msg,
            "cache_hit": False,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="调用 LLM OCR 模块识别单张图片")
    parser.add_argument("image_path", help="待识别图片的绝对或相对路径")
    parser.add_argument(
        "--force",
        action="store_true",
        help="忽略缓存，强制调用模型重新识别",
    )
    parser.add_argument("--model", default="", help="可选：临时覆盖 OCR 模型名")
    args = parser.parse_args()

    result = recognize_image(args.image_path, force=args.force, model=args.model or None)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
