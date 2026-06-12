from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import logging
import os
import shutil
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

SERVER_ROOT = Path(__file__).resolve().parents[3]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))


# 仅从 server/.env 注入 OCR 相关配置到当前脚本进程。
def load_script_env() -> None:
    env_path = SERVER_ROOT / ".env"
    env_values = dotenv_values(env_path)
    for key in (
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OCR_LLM_MODEL",
        "OCR_LLM_TIMEOUT",
        "OCR_LLM_TEMPERATURE",
        "OCR_LLM_TOP_P",
        "OCR_MAX_CONCURRENCY",
        "OCR_PROMPT",
    ):
        value = env_values.get(key)
        if value in (None, ""):
            os.environ.pop(key, None)
            continue
        os.environ[key] = str(value)


load_script_env()

from apps.complaint_app.core.llm.llm_client import AsyncLLMClient, ModelConfig
from apps.complaint_app.core.model_config import get_ocr_llm_model
from apps.complaint_app.utils import _ocr_max_concurrency
from utils.llm_thinking import build_thinking_extra_body
from utils.logger import get_logger

try:
    from pdf2image import convert_from_path
except ImportError:  # pragma: no cover
    convert_from_path = None

try:
    import fitz  # type: ignore
except ImportError:  # pragma: no cover
    fitz = None


logger = get_logger(__name__)
PDF_SUFFIX = ".pdf"
SPACE_RE = re.compile(r"[ \t\u3000\xa0]+")
BLANK_LINE_RE = re.compile(r"\n{3,}")
MANIFEST_FILE = "manifest.jsonl"
FAILED_FILE = "failed_documents.jsonl"
DEFAULT_OCR_PROMPT = "请从我给你的这张图片中提取所有的文字内容，保持按照原有的段落顺序和换行来组织文本即可；不要补充、修改或省略文字内容；如果是交易流水表格，需要使用markdown的方式表达。（不识别条形码）"
OCR_SYSTEM_PROMPT = "你是一个OCR文本提取器。你的唯一任务是从图片中提取文字并输出纯文本；不要解释，不要补充，不要改写。若图片是交易流水类表格，可用Markdown表格；其他内容禁止使用Markdown标题、列表或额外说明。"


@dataclass
class OcrPageResult:
    page_no: int
    image_path: str
    text: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="批量将 PDF 拆页转图并 OCR 汇总整份原文。")
    parser.add_argument("--pdf-dir", required=True, help="PDF 根目录，支持递归查找")
    parser.add_argument("--output-dir", required=True, help="输出目录")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 份文件，0 表示全部")
    parser.add_argument("--debug", action="store_true", help="输出调试日志")
    return parser


# 调整脚本日志级别。
def configure_logger(debug: bool) -> None:
    if debug:
        logger.setLevel(logging.DEBUG)


# 枚举目录下全部 PDF 文件，兼容大小写后缀。
def iter_pdf_files(pdf_dir: Path) -> list[Path]:
    return sorted([path for path in pdf_dir.rglob("*") if path.is_file() and path.suffix.lower() == PDF_SUFFIX], key=lambda path: str(path.relative_to(pdf_dir)))


# 生成稳定文档标识，避免同名 PDF 冲突。
def build_doc_slug(pdf_path: Path, pdf_dir: Path) -> str:
    relative_path = str(pdf_path.relative_to(pdf_dir))
    digest = hashlib.md5(relative_path.encode("utf-8")).hexdigest()[:8]
    base_name = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", pdf_path.stem).strip("._-") or "document"
    return f"{base_name}__{digest}"


# 轻量清洗 OCR 文本，保留段落和换行。
def normalize_ocr_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\ufeff", "")
    normalized = "\n".join(line.rstrip() for line in normalized.split("\n"))
    normalized = SPACE_RE.sub(" ", normalized)
    normalized = BLANK_LINE_RE.sub("\n\n", normalized)
    return normalized.strip()


# 写入 JSONL 记录。
def append_jsonl_row(output_path: Path, payload: dict) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as file_obj:
        file_obj.write(json.dumps(payload, ensure_ascii=False) + "\n")


# 保存 OCR 汇总原文。
def write_text(output_path: Path, text: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")


# 使用 pdf2image、PyMuPDF 或 pdftoppm 将 PDF 拆页为 PNG。
def render_pdf_pages(pdf_path: Path, image_dir: Path) -> list[Path]:
    image_dir.mkdir(parents=True, exist_ok=True)
    image_paths: list[Path] = []
    if convert_from_path is not None:
        images = convert_from_path(str(pdf_path), dpi=150)
        for index, image in enumerate(images, start=1):
            image_path = image_dir / f"page_{index:03d}.png"
            image.save(image_path, format="PNG")
            image_paths.append(image_path)
        return image_paths
    if fitz is not None:
        document = fitz.open(str(pdf_path))
        try:
            for index, page in enumerate(document, start=1):
                matrix = fitz.Matrix(2, 2)
                pix = page.get_pixmap(matrix=matrix, alpha=False)
                image_path = image_dir / f"page_{index:03d}.png"
                pix.save(str(image_path))
                image_paths.append(image_path)
        finally:
            document.close()
        return image_paths
    pdftoppm_path = shutil.which("pdftoppm")
    if pdftoppm_path:
        prefix = image_dir / "page"
        subprocess.run([pdftoppm_path, "-png", str(pdf_path), str(prefix)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        generated = sorted(image_dir.glob("page-*.png"))
        for index, src_path in enumerate(generated, start=1):
            image_path = image_dir / f"page_{index:03d}.png"
            src_path.rename(image_path)
            image_paths.append(image_path)
        if image_paths:
            return image_paths
    raise RuntimeError("未安装 pdf2image、PyMuPDF 且系统无 pdftoppm，无法拆分 PDF 页面图片")


# 提取模型响应正文，兼容不同网关字段。
def extract_response_text(resp: Any) -> str:
    choices = getattr(resp, "choices", None) or []
    if not choices:
        return ""
    message = getattr(choices[0], "message", None)
    if message is None:
        return ""
    content = getattr(message, "content", None)
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text") or block.get("value")
                if text:
                    parts.append(str(text))
            elif isinstance(block, str):
                parts.append(block)
        if parts:
            return "".join(parts).strip()
    for field in ("reasoning_content", "reasoning", "analysis", "thoughts", "think"):
        value = getattr(message, field, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


# 构造 OCR 的多模态消息。
def build_ocr_messages(image_b64: str, prompt_text: str) -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": [{"type": "text", "text": OCR_SYSTEM_PROMPT}]},
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                {"type": "text", "text": prompt_text},
            ],
        },
    ]


# 调用 OCR 模型识别单张图片。
async def recognize_image_with_llm(llm: AsyncLLMClient, model: str, thinking_extra_body: dict[str, Any] | None, image_path: Path) -> str:
    image_bytes = image_path.read_bytes()
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    prompt_text = os.getenv("OCR_PROMPT") or DEFAULT_OCR_PROMPT
    messages = build_ocr_messages(image_b64, prompt_text)
    resp = await llm.chat(messages=messages, model=model, temperature=0.0, extra_body=thinking_extra_body)
    return normalize_ocr_text(extract_response_text(resp))


# 识别单页图片文本。
async def recognize_single_page(llm: AsyncLLMClient, model: str, thinking_extra_body: dict[str, Any] | None, image_path: Path, page_no: int, semaphore: asyncio.Semaphore) -> OcrPageResult:
    async with semaphore:
        text = await recognize_image_with_llm(llm, model, thinking_extra_body, image_path)
    return OcrPageResult(page_no=page_no, image_path=str(image_path), text=text)


# 按页 OCR 并返回有序结果。
async def recognize_pages_async(llm: AsyncLLMClient, model: str, thinking_extra_body: dict[str, Any] | None, image_paths: list[Path]) -> list[OcrPageResult]:
    semaphore = asyncio.Semaphore(_ocr_max_concurrency())
    tasks = [recognize_single_page(llm, model, thinking_extra_body, image_path, index, semaphore) for index, image_path in enumerate(image_paths, start=1)]
    page_results = await asyncio.gather(*tasks)
    return sorted(page_results, key=lambda item: item.page_no)


# 将逐页文本合并为整份原文。
def merge_page_texts(page_results: list[OcrPageResult]) -> str:
    merged_parts = [page.text for page in page_results if page.text]
    return "\n\n".join(merged_parts).strip()


# 处理单个 PDF。
async def process_pdf_async(llm: AsyncLLMClient, model: str, thinking_extra_body: dict[str, Any] | None, pdf_path: Path, pdf_dir: Path, output_dir: Path, manifest_path: Path, failed_path: Path) -> None:
    doc_slug = build_doc_slug(pdf_path, pdf_dir)
    raw_text_path = output_dir / "raw_texts" / f"{doc_slug}.txt"
    image_dir = output_dir / "pdf_page_images" / doc_slug
    try:
        image_paths = render_pdf_pages(pdf_path, image_dir)
        page_results = await recognize_pages_async(llm, model, thinking_extra_body, image_paths)
        merged_text = merge_page_texts(page_results)
        write_text(raw_text_path, merged_text)
        append_jsonl_row(
            manifest_path,
            {
                "doc_slug": doc_slug,
                "file_name": pdf_path.name,
                "file_path": str(pdf_path),
                "raw_text_path": str(raw_text_path),
                "image_dir": str(image_dir),
                "page_count": len(image_paths),
            },
        )
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("PDF OCR 提取失败 file=%s err=%s", pdf_path, exc)
        append_jsonl_row(
            failed_path,
            {
                "doc_slug": doc_slug,
                "file_name": pdf_path.name,
                "file_path": str(pdf_path),
                "error": str(exc),
            },
        )


# 清理索引文件，保留图片和文本目录内容按新结果覆盖。
def reset_index_files(output_dir: Path) -> tuple[Path, Path]:
    manifest_path = output_dir / MANIFEST_FILE
    failed_path = output_dir / FAILED_FILE
    for path in (manifest_path, failed_path):
        if path.exists():
            path.unlink()
        path.touch()
    return manifest_path, failed_path


async def async_main() -> int:
    args = build_parser().parse_args()
    configure_logger(args.debug)
    pdf_dir = Path(args.pdf_dir).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path, failed_path = reset_index_files(output_dir)
    pdf_files = iter_pdf_files(pdf_dir)
    if args.limit > 0:
        pdf_files = pdf_files[: args.limit]
    model = get_ocr_llm_model()
    thinking_extra_body = build_thinking_extra_body(model, False)
    llm = AsyncLLMClient(config=ModelConfig(default_model=model), name="pdf_raw_text_ocr_extract")
    for pdf_path in pdf_files:
        logger.info("处理 PDF OCR file=%s", pdf_path)
        await process_pdf_async(llm, model, thinking_extra_body, pdf_path, pdf_dir, output_dir, manifest_path, failed_path)
    print(f"输出目录: {output_dir}")
    print(f"原文目录: {output_dir / 'raw_texts'}")
    print(f"图片目录: {output_dir / 'pdf_page_images'}")
    print(f"索引文件: {manifest_path}")
    print(f"失败文件: {failed_path}")
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
