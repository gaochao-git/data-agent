"""OpenAI-style OCR implementation with diskcache-based deduplication."""

from __future__ import annotations

import argparse
import base64
import json
import hashlib
import os
import time
from pathlib import Path
from typing import Dict, Optional

from diskcache import Cache
from dotenv import load_dotenv
from openai import OpenAI

from apps.complaint_app.core.llm.llm_client import AsyncLLMClient, ModelConfig
from utils.text_cleanup import cleanup_markdown_blank_lines
from apps.complaint_app.core.model_config import get_ocr_llm_model
from utils.storage_paths import DATA_ROOT
from utils.logger import get_logger

logger = get_logger(__name__)

load_dotenv()

OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OCR_LLM_MODEL = get_ocr_llm_model()
OCR_CACHE_DIR = os.getenv("OCR_CACHE_DIR") or os.path.join(DATA_ROOT, "diskcache")
OCR_LLM_TIMEOUT = int(os.getenv("OCR_LLM_TIMEOUT", "600"))
OCR_LLM_TEMPERATURE = float(os.getenv("OCR_LLM_TEMPERATURE", "0.2"))
OCR_LLM_TOP_P = float(os.getenv("OCR_LLM_TOP_P", "0.85"))

_TEXT_FALLBACK_DIR = (
    os.getenv("OCR_TEXT_FALLBACK_DIR") or os.path.join(DATA_ROOT, "texts")
)
os.makedirs(OCR_CACHE_DIR, exist_ok=True)
os.makedirs(_TEXT_FALLBACK_DIR, exist_ok=True)

_cache = Cache(OCR_CACHE_DIR)
_client: Optional[OpenAI] = None
_async_llm_client: Optional[AsyncLLMClient] = None


def _resolve_ocr_model(model: Optional[str] = None) -> str:
    return str(model or "").strip() or OCR_LLM_MODEL


def _get_client() -> OpenAI:
    global _client  # pylint: disable=global-statement

    if _client is None:
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY 未配置，无法执行OCR识别")
    
        _client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
    return _client


def _get_async_llm_client() -> AsyncLLMClient:
    global _async_llm_client
    if _async_llm_client is None:
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY 未配置，无法执行OCR识别")
        _async_llm_client = AsyncLLMClient(
            config=ModelConfig(
                default_model=OCR_LLM_MODEL,
                default_params={
                    "temperature": OCR_LLM_TEMPERATURE,
                    "top_p": OCR_LLM_TOP_P,
                    "timeout": OCR_LLM_TIMEOUT,
                },
            )
        )
    return _async_llm_client


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

def _build_ocr_messages(image_b64: str, prompt: Optional[str]) -> list[dict]:
    prompt_text = prompt or ""
    return [
        {"role": "system", "content": [{"type": "text", "text": sys_prmpt}]},
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                {"type": "text", "text": prompt_text},
            ],
        },
    ]

sys_prmpt="""“你是一个0CR文本提取器。
你的唯一任务是：从图片中提取文字，输出纯文本。你的输出规则分两类：
# 如果图片内容包括“银行流水/交易明细类表格”需要以Markdown的形式输出。特征包括：
- 多行多列的紧凑表格
- 列内内容主要是日期、时间、数字、金额、符号(如 +/-)
- 即使没有表头，也呈现交易流水的结构（多行记录) 
# 如果图不是交易流水类结构(例如申请表、登记表、合同、举报信件、普通表格等),一律输出纯文本:
- 只允许使用换行符、空格表示段落和缩进。
- 严禁使用任何Markdown语言(包括不限于:标题#、列表-、星号*、代码块```、表格竖线|、表格边框等)。
- 如果你输出了Markdown,我的程序会认为你出错并丢弃结。
- 不要解释，不要加前后缀文字，只输出图片中的文字内容。"""

def _invoke_llm(image_b64: str, prompt: Optional[str] = None, model: Optional[str] = None) -> str:
    client = _get_client()
    payload = {
        "model": _resolve_ocr_model(model),
        "messages": _build_ocr_messages(image_b64, prompt),
        "stream": False,
        "timeout": OCR_LLM_TIMEOUT,
        "temperature": OCR_LLM_TEMPERATURE,
        "top_p": OCR_LLM_TOP_P,
    }

    retry_delay = 1.0
    max_retries = 3
    for attempt in range(max_retries):
        try:
            completion = client.chat.completions.create(**payload)
            return _extract_composed_text(completion)
        except Exception as exc:  # pylint: disable=broad-except
            logger.info("[DEBUG OCR-LLM] 调用失败 (尝试 %s/%s): %s", attempt + 1, max_retries, exc)
            if attempt >= max_retries - 1:
                raise
            time.sleep(retry_delay)
            retry_delay *= 2

    raise RuntimeError("LLM 调用失败")

async def _invoke_llm_async(image_b64: str, prompt: Optional[str] = None, model: Optional[str] = None) -> str:
    client = _get_async_llm_client()
    messages = _build_ocr_messages(image_b64, prompt)
    completion = await client.chat(messages=messages, model=_resolve_ocr_model(model))
    return _extract_composed_text(completion)


def _resolve_text_dir(image_path: str) -> Path:
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


def _load_cached_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            return file_obj.read()
    except FileNotFoundError:
        return None


def load_cached_ocr_text(image_path: str, *, model: Optional[str] = None) -> Dict[str, Optional[str]]:
    try:
        if not os.path.exists(image_path):
            return {
                "sha256": None,
                "text_content": None,
                "cache_hit": False,
            }

        with open(image_path, "rb") as file_obj:
            image_bytes = file_obj.read()

        digest = hashlib.sha256(image_bytes).hexdigest()
        model_name = _resolve_ocr_model(model)
        cache_keys = [f"ocr:sha256:{digest}:model:{model_name}", f"ocr:sha256:{digest}"]
        cached_path = None
        cache_key = ""
        for candidate_key in cache_keys:
            candidate_path = _cache.get(candidate_key)
            if candidate_path:
                cached_path = candidate_path
                cache_key = candidate_key
                break
        if not cached_path:
            return {
                "sha256": digest,
                "text_content": None,
                "cache_hit": False,
            }
        cached_text = _load_cached_text(cached_path)
        if cached_text is None:
            _cache.delete(cache_key)
            return {
                "sha256": digest,
                "text_content": None,
                "cache_hit": False,
            }
        sanitized_text = cleanup_markdown_blank_lines(cached_text)
        local_path = _persist_text(image_path, sanitized_text)
        if local_path != cached_path:
            _cache.set(cache_key, local_path)
        return {
            "sha256": digest,
            "text_content": sanitized_text,
            "cache_hit": True,
        }
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("[DEBUG OCR-LLM] 读取缓存失败 image=%s err=%s", image_path, exc)
        raise


def recognize_image(image_path: str, *, force: bool = False, prompt: Optional[str] = None, model: Optional[str] = None) -> Dict[str, Optional[str]]:
    try:
        if not os.path.exists(image_path):
            return {
                "sha256": None,
                "text_content": f"错误：文件不存在 {os.path.basename(image_path)}",
                "cache_hit": False,
            }

        with open(image_path, "rb") as file_obj:
            image_bytes = file_obj.read()

        digest = hashlib.sha256(image_bytes).hexdigest()
        model_name = _resolve_ocr_model(model)
        cache_key = f"ocr:sha256:{digest}:model:{model_name}"

        if not force:
            cached_path = _cache.get(cache_key)
            if cached_path:
                cached_text = _load_cached_text(cached_path)
                if cached_text is None:
                    _cache.delete(cache_key)
                else:
                    sanitized_text = cleanup_markdown_blank_lines(cached_text)
                    local_path = _persist_text(image_path, sanitized_text)
                    if local_path != cached_path:
                        _cache.set(cache_key, local_path)
                    return {
                        "sha256": digest,
                        "text_content": sanitized_text,
                        "cache_hit": True,
                    }

        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        recognized_text = _invoke_llm(image_b64, prompt=prompt, model=model_name)
        sanitized_text = cleanup_markdown_blank_lines(recognized_text)
        local_path = _persist_text(image_path, sanitized_text)
        _cache.set(cache_key, local_path)

        return {
            "sha256": digest,
            "text_content": sanitized_text,
            "cache_hit": False,
        }

    except Exception as exc:  # pylint: disable=broad-except
        logger.info("[DEBUG OCR-LLM] 处理失败: %s", exc)
        return {
            "sha256": None,
            "text_content": f"OCR识别失败：{exc} - {os.path.basename(image_path)}",
            "cache_hit": False,
        }

async def recognize_image_async(image_path: str, *, force: bool = False, prompt: Optional[str] = None, model: Optional[str] = None) -> Dict[str, Optional[str]]:
    """异步 OCR 识别单张图片。"""
    try:
        if not os.path.exists(image_path):
            return {
                "sha256": None,
                "text_content": f"错误：文件不存在 {os.path.basename(image_path)}",
                "cache_hit": False,
            }

        with open(image_path, "rb") as file_obj:
            image_bytes = file_obj.read()

        digest = hashlib.sha256(image_bytes).hexdigest()
        model_name = _resolve_ocr_model(model)
        cache_key = f"ocr:sha256:{digest}:model:{model_name}"

        if not force:
            cached_path = _cache.get(cache_key)
            if cached_path:
                cached_text = _load_cached_text(cached_path)
                if cached_text is None:
                    _cache.delete(cache_key)
                else:
                    sanitized_text = cleanup_markdown_blank_lines(cached_text)
                    local_path = _persist_text(image_path, sanitized_text)
                    if local_path != cached_path:
                        _cache.set(cache_key, local_path)
                    return {
                        "sha256": digest,
                        "text_content": sanitized_text,
                        "cache_hit": True,
                    }

        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        recognized_text = await _invoke_llm_async(image_b64, prompt=prompt, model=model_name)
        sanitized_text = cleanup_markdown_blank_lines(recognized_text)
        local_path = _persist_text(image_path, sanitized_text)
        _cache.set(cache_key, local_path)

        return {
            "sha256": digest,
            "text_content": sanitized_text,
            "cache_hit": False,
        }

    except Exception as exc:  # pylint: disable=broad-except
        logger.info("[DEBUG OCR-LLM] 处理失败: %s", exc)
        return {
            "sha256": None,
            "text_content": f"OCR识别失败：{exc} - {os.path.basename(image_path)}",
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
    logger.info("ocr_result: %s", json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
