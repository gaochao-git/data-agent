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
WORDFIT_OCR_PDF_MAX_WORKERS = max(1, int(os.getenv("WORDFIT_OCR_PDF_MAX_WORKERS", "4")))
WORDFIT_OCR_ENABLE_THINKING = (os.getenv("WORDFIT_OCR_ENABLE_THINKING", "").strip().lower() in {"1", "true", "yes", "y", "on"})
WORDFIT_OCR_MAX_WAIT_SECONDS = max(0.0, float(os.getenv("WORDFIT_OCR_MAX_WAIT_SECONDS", "10") or "10"))

_TEXT_FALLBACK_DIR = (
    os.getenv("OCR_TEXT_FALLBACK_DIR") or os.path.join("uploads", "texts")
)
os.makedirs(OCR_CACHE_DIR, exist_ok=True)
os.makedirs(_TEXT_FALLBACK_DIR, exist_ok=True)

_cache = Cache(OCR_CACHE_DIR)
_client: Optional[OpenAI] = None


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

def _invoke_llm(image_b64: str, mime_type: str = "image/png", prompt: Optional[str] = None, image_path: Optional[str] = None, page_index: Optional[int] = None, page_started_at_text: Optional[str] = None, persist_target_path: Optional[str] = None) -> str:
    client = _get_client()
    extra_body = build_thinking_extra_body(OCR_LLM_MODEL, WORDFIT_OCR_ENABLE_THINKING)
    
    user_content = [
        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_b64}"}}
    ]
    if prompt:
        user_content.append({"type": "text", "text": prompt})
        
    payload = {
        "model": OCR_LLM_MODEL,
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
            aggregated = []
            timeout_reached = False
            try:
                for chunk in stream:
                    text = _extract_stream_chunk_text(chunk)
                    if text:
                        aggregated.append(text)
                    if WORDFIT_OCR_MAX_WAIT_SECONDS > 0 and (time.monotonic() - started_at) >= WORDFIT_OCR_MAX_WAIT_SECONDS:
                        timeout_reached = True
                        timeout_at = datetime.now()
                        page_text = "".join(aggregated).strip()
                        logger.warning("[OCR] llm stream timeout after %.2fs model=%s file=%s page=%s started_at=%s timeout_at=%s ocr_elapsed_ms=%s text_len=%s saved_path=%s", WORDFIT_OCR_MAX_WAIT_SECONDS, OCR_LLM_MODEL, image_path, page_index, page_started_at_text, timeout_at.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], int((time.monotonic() - started_at) * 1000), len(page_text), persist_target_path)
                        break
            finally:
                close = getattr(stream, "close", None)
                if callable(close):
                    close()
            text_content = "".join(aggregated).strip()
            if timeout_reached:
                return text_content
            if text_content:
                return text_content
            raise RuntimeError("LLM 流式调用未返回有效内容")
        except Exception as exc:  # pylint: disable=broad-except
            print(f"[DEBUG OCR-LLM] 调用失败 (尝试 {attempt + 1}/{max_retries}): {exc}")
            if attempt >= max_retries - 1:
                raise
            time.sleep(retry_delay)
            retry_delay *= 2

    raise RuntimeError("LLM 调用失败")


def _resolve_text_dir(image_path: str) -> Path:
    path = Path(image_path).resolve()
    parts = path.parts
    if "complaints" in parts:
        idx = parts.index("complaints")
        if idx + 1 < len(parts):
            text_dir = Path(*parts[: idx + 2], "texts")
            text_dir.mkdir(parents=True, exist_ok=True)
            return text_dir

    fallback = Path(_TEXT_FALLBACK_DIR).resolve()
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def _persist_text(image_path: str, text: str) -> str:
    text_dir = _resolve_text_dir(image_path)
    image_stem = Path(image_path).stem
    target_path = text_dir / f"{image_stem}_ocr.txt"
    normalized = text.rstrip("\n")
    with open(target_path, "w", encoding="utf-8") as file_obj:
        if normalized:
            file_obj.write(normalized + "\n")
        else:
            file_obj.write("")
    return str(target_path)


def _persist_page_text(image_path: str, page_index: int, text: str) -> str:
    text_dir = _resolve_text_dir(image_path)
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


def _process_pdf_page(image_path: str, page_index: int, prompt: Optional[str] = None) -> tuple[int, str, str, str, int, int, int]:
    page_started_at = timing_now()
    page_started_at_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    persist_target_path = str(_resolve_text_dir(image_path) / f"{Path(image_path).stem}_page_{page_index + 1}_ocr.txt")
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
    page_text = _invoke_llm(
        img_b64,
        mime_type="image/png",
        prompt=prompt,
        image_path=image_path,
        page_index=page_index + 1,
        page_started_at_text=page_started_at_text,
        persist_target_path=persist_target_path,
    )
    saved_path = _persist_page_text(image_path, page_index, page_text)
    ocr_duration_ms = timing_elapsed_ms(ocr_started_at)
    page_duration_ms = timing_elapsed_ms(page_started_at)
    return page_index, page_text, saved_path, page_started_at_text, render_duration_ms, ocr_duration_ms, page_duration_ms


def recognize_image(image_path: str, force: bool = False, prompt: Optional[str] = None, progress_callback=None) -> Dict[str, Optional[str]]:
    logger.info(f"[OCR] 开始处理文件: {image_path}, force={force}")
    recognize_started_at = timing_now()
    try:
        if not os.path.exists(image_path):
            error_msg = f"错误：文件不存在 {os.path.basename(image_path)}"
            logger.error(f"[OCR] {error_msg}")
            return {
                "sha256": None,
                "text_content": error_msg,
                "cache_hit": False,
            }

        with open(image_path, "rb") as file_obj:
            image_bytes = file_obj.read()

        digest = hashlib.sha256(image_bytes).hexdigest()
        cache_key = f"ocr:sha256:{digest}"
        logger.info(f"[OCR] 文件哈希: {digest}, 缓存键: {cache_key}")

        if not force:
            cached_path = _cache.get(cache_key)
            if cached_path:
                text_content = _load_cached_text(cached_path)
                if text_content is not None:
                    logger.info(f"[OCR] 缓存命中，使用缓存结果: {cached_path}")
                    logger.info("[timing] app=word_fit_app kind=ocr_file file=%s duration_ms=%s model=%s thinking=%s cache_hit=%s mime_type=%s", image_path, timing_elapsed_ms(recognize_started_at), OCR_LLM_MODEL, WORDFIT_OCR_ENABLE_THINKING, True, "cache")
                    return {
                        "sha256": digest,
                        "text_content": text_content,
                        "cache_hit": True,
                    }
            logger.info(f"[OCR] 缓存未命中，准备调用模型")

        # Determine MIME type
        mime_type, _ = mimetypes.guess_type(image_path)
        if not mime_type:
            mime_type = "image/png"  # Default
        logger.info(f"[OCR] 文件MIME类型: {mime_type}")

        recognized_text = ""

        if mime_type == "application/pdf":
            # PDF Handling: Convert pages to images using PyMuPDF (fitz)
            # This is necessary because many LLMs do not accept PDF base64 directly
            try:
                logger.info(f"[OCR] 开始处理PDF文件: {image_path}")
                doc = fitz.open(image_path)
                page_count = len(doc)
                doc.close()
                logger.info(f"[OCR] PDF页数: {page_count}")
                page_texts = [""] * page_count
                max_workers = min(page_count, WORDFIT_OCR_PDF_MAX_WORKERS)
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = [
                        executor.submit(_process_pdf_page, image_path, page_index, prompt)
                        for page_index in range(page_count)
                    ]
                    for future in as_completed(futures):
                        page_index, page_text, saved_path, page_started_at_text, render_duration_ms, ocr_duration_ms, page_duration_ms = future.result()
                        page_texts[page_index] = page_text
                        logger.info("[OCR] 第 %s 页处理完成 file=%s page=%s started_at=%s finished_at=%s render_ms=%s ocr_ms=%s duration_ms=%s text_len=%s saved_path=%s", page_index + 1, image_path, page_index + 1, page_started_at_text, datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], render_duration_ms, ocr_duration_ms, page_duration_ms, len(page_text), saved_path)
                        if progress_callback is not None:
                            progress_callback(sum(1 for text in page_texts if text != ""), page_count)
                recognized_text = "\n\n".join(page_texts)
                logger.info(f"[OCR] PDF处理完成，总页数: {page_count}")
                logger.info("[timing] app=word_fit_app kind=ocr_file file=%s duration_ms=%s model=%s thinking=%s cache_hit=%s mime_type=%s pages=%s workers=%s", image_path, timing_elapsed_ms(recognize_started_at), OCR_LLM_MODEL, WORDFIT_OCR_ENABLE_THINKING, False, mime_type, page_count, max_workers)
                
            except Exception as e:
                error_msg = f"PDF处理失败: {str(e)}"
                logger.error(f"[OCR] {error_msg}")
                return {
                    "sha256": digest,
                    "text_content": error_msg,
                    "cache_hit": False,
                }
        else:
            # Standard Image Handling
            logger.info(f"[OCR] 开始处理图片文件: {image_path}")
            image_b64 = base64.b64encode(image_bytes).decode("utf-8")
            image_started_at_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            persist_target_path = str(_resolve_text_dir(image_path) / f"{Path(image_path).stem}_ocr.txt")
            recognized_text = _invoke_llm(
                image_b64,
                mime_type=mime_type,
                prompt=prompt,
                image_path=image_path,
                page_index=1,
                page_started_at_text=image_started_at_text,
                persist_target_path=persist_target_path,
            )
            recognized_text = cleanup_markdown_blank_lines(recognized_text)
            saved_path = _persist_text(image_path, recognized_text)
            logger.info("[OCR] 图片处理完成 file=%s page=%s started_at=%s finished_at=%s ocr_ms=%s duration_ms=%s text_len=%s saved_path=%s", image_path, 1, image_started_at_text, datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], timing_elapsed_ms(recognize_started_at), timing_elapsed_ms(recognize_started_at), len(recognized_text), saved_path)
            if progress_callback is not None:
                progress_callback(1, 1)
            logger.info("[timing] app=word_fit_app kind=ocr_file file=%s duration_ms=%s model=%s thinking=%s cache_hit=%s mime_type=%s pages=%s workers=%s", image_path, timing_elapsed_ms(recognize_started_at), OCR_LLM_MODEL, WORDFIT_OCR_ENABLE_THINKING, False, mime_type, 1, 1)
            _cache.set(cache_key, saved_path)
            logger.info(f"[OCR] 结果已保存到: {saved_path}, 并缓存")
            return {"sha256": digest, "text_content": recognized_text, "cache_hit": False}

        # Cleanup and persist
        recognized_text = cleanup_markdown_blank_lines(recognized_text)
        saved_path = _persist_text(image_path, recognized_text)
        _cache.set(cache_key, saved_path)
        logger.info(f"[OCR] 结果已保存到: {saved_path}, 并缓存")

        return {
            "sha256": digest,
            "text_content": recognized_text,
            "cache_hit": False,
        }

    except Exception as exc:  # pylint: disable=broad-except
        error_msg = f"OCR识别失败：{exc} - {os.path.basename(image_path)}"
        logger.error(f"[OCR] {error_msg}", exc_info=True)
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
    args = parser.parse_args()

    result = recognize_image(args.image_path, force=args.force)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
