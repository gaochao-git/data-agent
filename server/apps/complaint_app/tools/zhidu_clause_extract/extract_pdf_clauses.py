from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import tempfile
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SERVER_ROOT = Path(__file__).resolve().parents[4]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from apps.complaint_app.core.llm.llm_client import AsyncLLMClient, ModelConfig
from apps.complaint_app.core.model_config import get_analysis_model, get_ocr_llm_model
from apps.complaint_app.tools.image_text_extractor_llm import load_cached_ocr_text
from apps.complaint_app.tools.image_text_extractor_llm import recognize_image_async as recognize_image_async_with_prompt
from apps.complaint_app.utils import _ocr_max_concurrency
from utils.llm_thinking import build_thinking_extra_body
from utils.logger import get_logger

try:
    import fitz  # type: ignore
except ImportError:  # pragma: no cover
    fitz = None

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover
    PdfReader = None

try:
    from pdf2image import convert_from_path
except ImportError:  # pragma: no cover
    convert_from_path = None

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


logger = get_logger(__name__)

CHAPTER_NO_PATTERN = r"第[一二三四五六七八九十百千零〇\d]+章"
SECTION_NO_PATTERN = r"第[一二三四五六七八九十百千零〇\d]+节"
ARTICLE_NO_PATTERN = r"第[一二三四五六七八九十百千零〇]+条"
BODY_START_RE = re.compile(rf"(附件(?:\s*[：:])?|第一章|第一条|第1\.1条)")
BODY_END_RE = re.compile(r"(?m)^\s*(附表[一二三四五六七八九十百千零〇\d]*|目录清册|移交清单|登记表|审批表|流程图|附录)\b")
CHAPTER_RE = re.compile(rf"(?m)^\s*({CHAPTER_NO_PATTERN})\s*([^\n]*)")
SECTION_RE = re.compile(rf"(?m)^\s*({SECTION_NO_PATTERN})\s*([^\n]*)")
ARTICLE_RE = re.compile(rf"(?m)^\s*({ARTICLE_NO_PATTERN})(?=\s|[【\[]|$)\s*([^\n]*)")
ITEM_RE = re.compile(r"(?m)^\s*([（(][一二三四五六七八九十百千零〇\d]+[)）])\s*([^\n]*)")
ARTICLE_REFERENCE_SUFFIX_RE = re.compile(r"^第(?:[（(][一二三四五六七八九十百千零〇\d]+[)）]|[一二三四五六七八九十百千零〇\d]+(?:款|项|目))")
SPACE_RE = re.compile(r"[ \t\u3000\xa0]+")
BLANK_LINE_RE = re.compile(r"\n{3,}")
PDF_SUFFIX = ".pdf"
OCR_TEXT_LENGTH_THRESHOLD = 50
LLM_CLAUSE_SYSTEM_PROMPT = """你正在做制度正文的结构化切条。

输入给你的是制度正文文本。
你的任务是识别真实的章/节/条结构，并输出条款列表。

要求：
1. 条款边界只以中文条号标题为准：第一条、第二条、第九条、第三十二条这类“第X条”。
2. 一个 article 从该中文条号标题开始，到下一个中文“第X条”标题之前结束。
3. article_no 只能填写该条原文里真实出现的中文“第X条”条号，必须直接摘自原文。
4. 不允许自行编号、补全编号、规范化编号，也不允许把任何非“第X条”的编号形式改写成“第X条”。
5. article_text 必须完整保留该条从条号开始到下一个条号前的全部原文内容。
6. 若能识别章/节信息，一并输出；识别不到则留空字符串。
7. 若文本中不存在中文“第X条”条号标题，则 articles 输出空数组。
8. article_text 必须保持原文内容；若原文中出现引号，统一输出为中文双引号“”，不要输出英文双引号 "。
9. 输出必须是严格 JSON，不要输出解释。

输出格式：
{
  "document_title": "",
  "articles": [
    {
      "chapter_no": "",
      "chapter_title": "",
      "section_no": "",
      "section_title": "",
      "article_no": "",
      "article_text": ""
    }
  ]
}
"""

ZHIDU_OCR_PROMPT = "请从我给你的这张制度文档图片中逐字提取所有文字内容；不要补充、修改、总结或省略；尽量保持图片中的段落顺序、换行、空格数量与缩进形式；无法识别的内容用[[unclear]]标记；不要解释，不要添加任何前后缀。"


@dataclass
class PageText:
    page_no: int
    text: str


@dataclass
class BodyResult:
    body_text: str
    body_pages: list[int]
    body_page_texts: list[str]
    start_anchor: str
    end_anchor: str | None


@dataclass
class DocumentResult:
    doc_id: str
    category: str
    title: str
    file_name: str
    file_path: str
    total_pages: int
    body_result: BodyResult


def build_document_result(category: str, file_name: str, pdf_path: Path, title: str, total_pages: int, body_result: BodyResult) -> DocumentResult:
    return DocumentResult(
        doc_id=build_doc_id(category, file_name),
        category=category,
        title=title,
        file_name=file_name,
        file_path=str(pdf_path),
        total_pages=total_pages,
        body_result=body_result,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="批量抽取制度 PDF 正文并切分条款。")
    parser.add_argument("--catalog-json", default="", help="制度目录 JSON 路径，可选")
    parser.add_argument("--pdf-dir", default="", help="PDF 根目录，支持递归查找")
    parser.add_argument("--output-dir", required=True, help="输出目录")
    parser.add_argument("--input-mode", choices=["scan", "catalog"], default="scan", help="scan=按PDF目录全量扫描，catalog=按目录JSON驱动")
    parser.add_argument("--category", default="", help="只处理指定分类")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 份文件，0 表示全部")
    parser.add_argument("--ocr-pdf-list", default="", help="强制走 OCR 的 PDF 文件名清单，一行一个文件名")
    parser.add_argument("--llm-pdf-list", default="", help="指定走 OCR + LLM 结构切条的 PDF 文件名清单；不传时默认全部 PDF 走该链路")
    parser.add_argument("--llm-model", default="", help="LLM 结构切条模型，默认读取 OPENAI_ANALYSIS_MODEL")
    parser.add_argument("--llm-business-retries", type=int, default=3, help="LLM 结构切条 JSON 解析失败时的业务重试次数")
    parser.add_argument("--llm-chunk-pages", type=int, default=3, help="LLM 结构切条时每块包含的页数")
    parser.add_argument("--llm-chunk-overlap-pages", type=int, default=1, help="LLM 结构切条时相邻块重叠页数")
    parser.add_argument("--rerun-failed-jsonl", default="", help="按 failed_documents.jsonl 重跑失败文档，并回写更新 output-dir 下的结果文件")
    parser.add_argument("--rerun-doc-id", default="", help="指定 doc_id，从 raw_texts 重新切条并原地替换 documents.jsonl/clauses.jsonl 中该文档")
    parser.add_argument("--rerun-doc-id-list", default="", help="指定 doc_id 清单文件，一行一个 doc_id，从 raw_texts 重新切条并原地替换")
    parser.add_argument("--rerun-file-name", default="", help="指定原始文件名，从 raw_texts 重新切条并原地替换 documents.jsonl/clauses.jsonl 中该文档")
    parser.add_argument("--raw-text-only", action="store_true", help="只提取正文全文 txt，不做条款切分")
    parser.add_argument("--clauses-from-raw-text", action="store_true", help="直接从 output-dir/raw_text_manifest.jsonl 读取全文并做条款提取")
    parser.add_argument("--clauses-from-raw-text-use-page-chunks", action="store_true", help="clauses-from-raw-text 模式下复用页级 OCR 缓存，按 llm-chunk-pages 分块切条")
    parser.add_argument("--resume", dest="resume", action="store_true", help="断点续跑，跳过已成功处理过的文档（默认开启）")
    parser.add_argument("--no-resume", dest="resume", action="store_false", help="关闭断点续跑，强制全量重跑")
    parser.set_defaults(resume=True)
    parser.add_argument("--debug", action="store_true", help="输出调试日志")
    return parser


# 加载环境变量，便于读取 server/.env 中的模型配置。
def load_env_files() -> None:
    if load_dotenv is None:
        return
    load_dotenv()
    server_env = SERVER_ROOT / ".env"
    if server_env.is_file():
        load_dotenv(server_env, override=False)


# 读取制度 LLM 切条模型，优先级：命令行 > ZHIDU_CLAUSE_LLM_MODEL > OPENAI_ANALYSIS_MODEL。
def get_clause_llm_model(cli_model: str) -> str:
    explicit = str(cli_model or "").strip()
    if explicit:
        return explicit
    env_model = str((os.getenv("ZHIDU_CLAUSE_LLM_MODEL") or "")).strip()
    if env_model:
        return env_model
    return get_analysis_model()


# 调整脚本日志级别。
def configure_logger(debug: bool) -> None:
    if debug:
        logger.setLevel(logging.DEBUG)


# 读取制度目录 JSON。
def load_catalog(catalog_path: Path) -> dict[str, list[str]]:
    raw_data = json.loads(catalog_path.read_text(encoding="utf-8"))
    if not isinstance(raw_data, dict):
        raise ValueError("catalog json 顶层必须是对象")
    catalog: dict[str, list[str]] = {}
    for category, file_names in raw_data.items():
        if not isinstance(category, str):
            continue
        if not isinstance(file_names, list):
            continue
        cleaned_names = [str(file_name).strip() for file_name in file_names if str(file_name).strip()]
        catalog[category.strip()] = cleaned_names
    return catalog


# 读取文件名白名单，一行一个文件名。
def load_named_pdf_list(list_path: Path) -> set[str]:
    names: set[str] = set()
    for raw_line in list_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        names.add(line.lower())
    return names


def load_doc_id_list(list_path: Path) -> set[str]:
    doc_ids: set[str] = set()
    for raw_line in list_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        doc_ids.add(line)
    return doc_ids


# 统一读取响应消息字段值。
def message_field(message: Any, field: str) -> Any:
    if isinstance(message, dict):
        return message.get(field)
    value = getattr(message, field, None)
    if value is not None:
        return value
    if hasattr(message, "model_dump"):
        try:
            dumped = message.model_dump()
            if isinstance(dumped, dict):
                return dumped.get(field)
        except Exception as err:
            logger.warning("message.model_dump 读取字段失败 field=%s err=%s", field, err)
            return None
    return None


# 兼容 content 为字符串或数组的场景。
def normalize_message_content(value: Any) -> str:
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


# 按 JSON 报错位置截取附近文本，便于快速定位问题片段。
def build_json_error_context(text: str, line_no: int, column_no: int, context_chars: int = 200) -> str:
    source = str(text or "")
    if line_no <= 0 or column_no <= 0:
        return source[: context_chars * 2]
    lines = source.splitlines()
    if line_no > len(lines):
        return source[: context_chars * 2]
    absolute_offset = sum(len(lines[index]) + 1 for index in range(line_no - 1)) + column_no - 1
    start = max(0, absolute_offset - context_chars)
    end = min(len(source), absolute_offset + context_chars)
    return source[start:end]


# 从模型响应中提取最终文本，content 为空时回退 reasoning 字段。
def extract_message_text(resp: Any) -> str:
    choices = getattr(resp, "choices", None) or []
    if not choices:
        return ""
    message = getattr(choices[0], "message", None)
    if not message:
        return ""
    content = normalize_message_content(message_field(message, "content")).strip()
    if content:
        return content
    for field in ("reasoning_content", "reasoning", "analysis", "thoughts", "think"):
        fallback = normalize_message_content(message_field(message, field)).strip()
        if fallback:
            logger.warning("制度切条 content 为空，回退字段=%s", field)
            return fallback
    return ""


# 从模型返回文本中截取 JSON 片段，并返回最后一次解析错误。
def extract_json_fragment(text: str) -> tuple[str, str]:
    source = str(text or "").strip()
    if not source:
        return "", "模型返回空文本"
    lower_source = source.lower()
    if lower_source.startswith("```json"):
        source = source[7:].lstrip()
    if source.endswith("```"):
        source = source[:-3].rstrip()
    try:
        json.loads(source)
        return source, ""
    except Exception as err:
        logger.error("制度 LLM 返回 JSON 解析失败 err=%s raw=%s", err, source[:2000])
        return "", str(err)


# 解析 LLM 切条结果。
def parse_llm_clause_payload(text: str) -> dict[str, Any]:
    source = str(text or "").strip()
    if not source:
        raise ValueError("模型返回空文本")
    lower_source = source.lower()
    if lower_source.startswith("```json"):
        source = source[7:].lstrip()
    if source.endswith("```"):
        source = source[:-3].rstrip()
    try:
        payload = json.loads(source)
    except json.JSONDecodeError as err:
        context = build_json_error_context(source, err.lineno, err.colno)
        logger.error("制度 LLM 返回 JSON 解析失败 line=%s column=%s pos=%s msg=%s context=%s", err.lineno, err.colno, err.pos, err.msg, context)
        raise
    except Exception as err:
        logger.error("制度 LLM 返回 JSON 解析失败 err=%s raw=%s", err, source[:2000])
        raise
    if not isinstance(payload, dict):
        raise ValueError("模型返回 JSON 顶层必须是对象")
    raw_articles = payload.get("articles")
    if not isinstance(raw_articles, list):
        raise ValueError("articles 必须是数组")
    articles: list[dict[str, str]] = []
    for item in raw_articles:
        if not isinstance(item, dict):
            continue
        article_no = str(item.get("article_no") or "").strip()
        article_text = str(item.get("article_text") or "").strip()
        if not article_no or not article_text:
            continue
        if not re.fullmatch(ARTICLE_NO_PATTERN, article_no):
            logger.error("醒目校验失败：LLM 切条输出了非中文真实条号 article_no=%s article_text_preview=%s", article_no, article_text[:200])
            raise ValueError(f"LLM 切条输出非中文真实条号: {article_no}")
        articles.append(
            {
                "chapter_no": str(item.get("chapter_no") or "").strip(),
                "chapter_title": str(item.get("chapter_title") or "").strip(),
                "section_no": str(item.get("section_no") or "").strip(),
                "section_title": str(item.get("section_title") or "").strip(),
                "article_no": article_no,
                "article_text": article_text,
            }
        )
    return {"document_title": str(payload.get("document_title") or "").strip(), "articles": articles}


# 构造 LLM 切条用户提示。
def build_llm_clause_user_prompt(document_title: str, body_text: str, chunk_page_nos: list[int]) -> str:
    page_text = f"页码范围：{chunk_page_nos[0]}-{chunk_page_nos[-1]}" if chunk_page_nos else "页码范围：未知"
    return f"制度标题：{document_title}\n{page_text}\n\n制度正文如下：\n{body_text}"


# 调用模型做单个正文分块的结构切条。
async def call_llm_clause_parser(llm: AsyncLLMClient, model: str, thinking_extra_body: dict[str, Any] | None, document_title: str, body_text: str, chunk_page_nos: list[int], business_retries: int, log_raw_on_failure: bool = False) -> dict[str, Any]:
    user_prompt = build_llm_clause_user_prompt(document_title, body_text, chunk_page_nos)
    last_error: Exception | None = None
    last_raw_text = ""
    for attempt in range(max(1, business_retries)):
        messages = [
            {"role": "system", "content": LLM_CLAUSE_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        resp = await llm.chat(messages=messages, model=model, temperature=0.0, extra_body=thinking_extra_body)
        raw_text = extract_message_text(resp)
        last_raw_text = raw_text
        try:
            return parse_llm_clause_payload(raw_text)
        except Exception as err:  # pylint: disable=broad-except
            last_error = err
            logger.warning("制度 LLM 切条解析失败 attempt=%s title=%s pages=%s err=%s", attempt + 1, document_title, chunk_page_nos, err)
            if log_raw_on_failure:
                logger.error("制度 LLM 原始输出 title=%s pages=%s raw=%s", document_title, chunk_page_nos, raw_text)
    raise ValueError(f"制度 LLM 切条失败: {last_error}; 原始输出: {last_raw_text}")


# 仅按起始锚点截正文，保留后续全部页面，避免结束锚点误截断。
def extract_body_without_end(pages: list[PageText]) -> BodyResult:
    if not pages:
        return BodyResult(body_text="", body_pages=[], body_page_texts=[], start_anchor="EMPTY", end_anchor=None)
    start_page_index, start_offset, start_anchor = find_body_start(pages)
    body_parts: list[str] = []
    body_pages: list[int] = []
    body_page_texts: list[str] = []
    for page_index in range(start_page_index, len(pages)):
        page_text = pages[page_index].text
        if page_index == start_page_index and start_offset > 0:
            page_text = page_text[start_offset:]
        page_text = page_text.strip()
        if not page_text:
            continue
        body_parts.append(page_text)
        body_pages.append(pages[page_index].page_no)
        body_page_texts.append(page_text)
    body_text = "\n\n".join(body_parts).strip()
    return BodyResult(body_text=body_text, body_pages=body_pages, body_page_texts=body_page_texts, start_anchor=start_anchor, end_anchor=None)


# 将正文按页分块，支持相邻块重叠页。
def build_llm_page_chunks(body_result: BodyResult, chunk_pages: int, overlap_pages: int) -> list[dict[str, Any]]:
    if not body_result.body_pages or not body_result.body_page_texts:
        return []
    safe_chunk_pages = max(1, int(chunk_pages))
    safe_overlap_pages = max(0, int(overlap_pages))
    step = max(1, safe_chunk_pages - safe_overlap_pages)
    chunks: list[dict[str, Any]] = []
    start_index = 0
    while start_index < len(body_result.body_pages):
        end_index = min(start_index + safe_chunk_pages, len(body_result.body_pages))
        page_nos = body_result.body_pages[start_index:end_index]
        page_texts = body_result.body_page_texts[start_index:end_index]
        chunk_text = "\n\n".join(text for text in page_texts if text).strip()
        if chunk_text:
            chunks.append({"page_nos": page_nos, "body_text": chunk_text})
        if end_index >= len(body_result.body_pages):
            break
        start_index += step
    return chunks


# 合并多个分块的切条结果，按 article_no 去重并保留内容更完整的一条。
def merge_llm_clause_payloads(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    document_title = ""
    ordered_articles: list[dict[str, str]] = []
    article_index: dict[str, int] = {}
    for payload in payloads:
        if not document_title:
            document_title = str(payload.get("document_title") or "").strip()
        for article in payload.get("articles") or []:
            if not isinstance(article, dict):
                continue
            article_no = str(article.get("article_no") or "").strip()
            article_text = str(article.get("article_text") or "").strip()
            if not article_no or not article_text:
                continue
            if article_no not in article_index:
                article_index[article_no] = len(ordered_articles)
                ordered_articles.append(article)
                continue
            existing = ordered_articles[article_index[article_no]]
            existing_text = str(existing.get("article_text") or "").strip()
            if len(article_text) > len(existing_text):
                ordered_articles[article_index[article_no]] = article
    return {"document_title": document_title, "articles": ordered_articles}


# 标准化 LLM 返回的条文正文，确保条号在原文前缀中。
def normalize_llm_article_text(article_no: str, article_text: str) -> str:
    text = str(article_text or "").strip()
    if not text:
        return text
    if text.startswith(article_no):
        return text
    return f"{article_no} {text}".strip()


# 将 LLM 切条结果转为 clauses.jsonl 行结构。
def build_clause_rows_from_llm(document: DocumentResult, llm_payload: dict[str, Any]) -> list[dict]:
    clause_rows: list[dict] = []
    for item in llm_payload.get("articles") or []:
        if not isinstance(item, dict):
            continue
        article_no = str(item.get("article_no") or "").strip()
        article_text = normalize_llm_article_text(article_no, str(item.get("article_text") or ""))
        if not article_no or not article_text:
            continue
        clause_rows.append(
            {
                "doc_id": document.doc_id,
                "category": document.category,
                "title": document.title,
                "file_name": document.file_name,
                "file_path": document.file_path,
                "chapter_no": str(item.get("chapter_no") or "").strip(),
                "chapter_title": str(item.get("chapter_title") or "").strip(),
                "section_no": str(item.get("section_no") or "").strip(),
                "section_title": str(item.get("section_title") or "").strip(),
                "article_no": article_no,
                "article_text": article_text,
                "items": extract_items(article_text),
                "source_pages": find_source_pages(article_text, document.body_result),
            }
        )
    return clause_rows


# 构建文件名到分类的映射，便于扫描模式下补全分类信息。
def build_catalog_name_index(catalog: dict[str, list[str]]) -> dict[str, list[str]]:
    name_index: dict[str, list[str]] = {}
    for category, file_names in catalog.items():
        for file_name in file_names:
            name_index.setdefault(file_name.lower(), []).append(category)
    return name_index


# 枚举目录下全部 PDF 文件，兼容大小写后缀。
def iter_pdf_files(pdf_dir: Path) -> list[Path]:
    return sorted([path for path in pdf_dir.rglob("*") if path.is_file() and path.suffix.lower() == PDF_SUFFIX], key=lambda path: str(path.relative_to(pdf_dir)))


# 递归建立 PDF 文件名索引。
def build_pdf_index(pdf_dir: Path) -> dict[str, list[Path]]:
    pdf_index: dict[str, list[Path]] = {}
    for pdf_path in iter_pdf_files(pdf_dir):
        pdf_index.setdefault(pdf_path.name.lower(), []).append(pdf_path)
    return pdf_index


# 按目录顺序稳定列出 PDF 文件。
def scan_pdf_paths(pdf_dir: Path) -> list[Path]:
    return iter_pdf_files(pdf_dir)


# 根据分类和文件名定位 PDF 路径。
def resolve_pdf_path(file_name: str, category: str, pdf_index: dict[str, list[Path]]) -> Path | None:
    candidates = pdf_index.get(file_name.lower(), [])
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    category_hits = [path for path in candidates if category in str(path.parent)]
    if len(category_hits) == 1:
        return category_hits[0]
    if category_hits:
        return sorted(category_hits)[0]
    return sorted(candidates)[0]


# 使用稳定哈希生成文档 ID。
def build_doc_id(category: str, file_name: str) -> str:
    digest = hashlib.md5(f"{category}|{file_name}".encode("utf-8")).hexdigest()[:12]
    return f"zd_{digest}"


# 目录扫描模式下，尽量补全分类名称。
def infer_category(pdf_path: Path, pdf_dir: Path, catalog_name_index: dict[str, list[str]]) -> str:
    categories = catalog_name_index.get(pdf_path.name.lower(), [])
    if len(categories) == 1:
        return categories[0]
    if len(categories) > 1:
        parent_text = str(pdf_path.parent)
        matched = [category for category in categories if category in parent_text]
        if len(matched) == 1:
            return matched[0]
        return matched[0] if matched else categories[0]
    relative_parts = pdf_path.relative_to(pdf_dir).parts
    if len(relative_parts) > 1:
        return relative_parts[0]
    return "未分类"


# 清洗单页文本，保留章条换行。
def normalize_page_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\ufeff", "")
    normalized = SPACE_RE.sub(" ", normalized)
    lines = [line.strip() for line in normalized.split("\n")]
    normalized = "\n".join(line for line in lines if line)
    normalized = BLANK_LINE_RE.sub("\n\n", normalized)
    return normalized.strip()


# OCR 页文本尽量保留空格、缩进和换行，只做最小清洗。
def normalize_ocr_page_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\ufeff", "")
    normalized = "\n".join(line.rstrip() for line in normalized.split("\n"))
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip("\n")


# 通过 PyMuPDF 逐页提取文本。
def read_pdf_with_fitz(pdf_path: Path) -> list[PageText]:
    if fitz is None:
        return []
    document = fitz.open(pdf_path)
    pages: list[PageText] = []
    try:
        for index, page in enumerate(document):
            pages.append(PageText(page_no=index + 1, text=normalize_page_text(page.get_text("text"))))
    finally:
        document.close()
    return pages


# 回退使用 pypdf 提取文本。
def read_pdf_with_pypdf(pdf_path: Path) -> list[PageText]:
    if PdfReader is None:
        raise RuntimeError("未安装可用的 PDF 解析库，请安装 PyMuPDF 或 pypdf")
    reader = PdfReader(str(pdf_path))
    pages: list[PageText] = []
    for index, page in enumerate(reader.pages):
        pages.append(PageText(page_no=index + 1, text=normalize_page_text(page.extract_text() or "")))
    return pages


def should_fallback_to_ocr(pages: list[PageText]) -> bool:
    total_text_length = sum(len(page.text.strip()) for page in pages)
    return total_text_length < OCR_TEXT_LENGTH_THRESHOLD


async def recognize_single_pdf_page(image_path: Path, page_no: int, semaphore: asyncio.Semaphore) -> PageText:
    async with semaphore:
        result = await recognize_image_async_with_prompt(str(image_path), force=True, prompt=ZHIDU_OCR_PROMPT)
    text_content = normalize_ocr_page_text(str(result.get("text_content") or ""))
    return PageText(page_no=page_no, text=text_content)


# 图片型 PDF 转图片后做逐页 OCR。
async def read_pdf_with_ocr_async(pdf_path: Path) -> list[PageText]:
    with tempfile.TemporaryDirectory(prefix="zhidu_pdf_ocr_") as tmp_dir:
        image_paths: list[Path] = []
        pdf_digest = hashlib.md5(str(pdf_path.resolve()).encode("utf-8")).hexdigest()[:8]
        if convert_from_path is not None:
            images = convert_from_path(str(pdf_path), dpi=150)
            for index, image in enumerate(images, start=1):
                image_path = Path(tmp_dir) / f"{pdf_digest}_page_{index}.png"
                image.save(image_path, format="PNG")
                image_paths.append(image_path)
        elif fitz is not None:
            document = fitz.open(str(pdf_path))
            try:
                for index, page in enumerate(document, start=1):
                    image_path = Path(tmp_dir) / f"{pdf_digest}_page_{index}.png"
                    page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).save(str(image_path))
                    image_paths.append(image_path)
            finally:
                document.close()
        else:
            raise RuntimeError("未安装 pdf2image 且 PyMuPDF 不可用，无法对图片型 PDF 进行 OCR 回退")
        semaphore = asyncio.Semaphore(_ocr_max_concurrency())
        tasks = [recognize_single_pdf_page(image_path, index, semaphore) for index, image_path in enumerate(image_paths, start=1)]
        return await asyncio.gather(*tasks)


# 将 PDF 拆页后只复用已有 OCR 缓存，不发起新的模型请求。
def read_pdf_with_cached_ocr(pdf_path: Path) -> list[PageText]:
    with tempfile.TemporaryDirectory(prefix="zhidu_pdf_ocr_cache_") as tmp_dir:
        image_paths: list[Path] = []
        pdf_digest = hashlib.md5(str(pdf_path.resolve()).encode("utf-8")).hexdigest()[:8]
        if convert_from_path is not None:
            images = convert_from_path(str(pdf_path), dpi=150)
            for index, image in enumerate(images, start=1):
                image_path = Path(tmp_dir) / f"{pdf_digest}_page_{index}.png"
                image.save(image_path, format="PNG")
                image_paths.append(image_path)
        elif fitz is not None:
            document = fitz.open(str(pdf_path))
            try:
                for index, page in enumerate(document, start=1):
                    image_path = Path(tmp_dir) / f"{pdf_digest}_page_{index}.png"
                    page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).save(str(image_path))
                    image_paths.append(image_path)
            finally:
                document.close()
        else:
            raise RuntimeError("未安装 pdf2image 且 PyMuPDF 不可用，无法复用图片型 PDF 的 OCR 缓存")
        pages: list[PageText] = []
        for index, image_path in enumerate(image_paths, start=1):
            result = load_cached_ocr_text(str(image_path), model=get_ocr_llm_model())
            text_content = normalize_ocr_page_text(str(result.get("text_content") or ""))
            if not text_content:
                raise RuntimeError(f"OCR 缓存缺失 page={index} file={pdf_path}")
            pages.append(PageText(page_no=index, text=text_content))
        return pages


def read_pdf_with_ocr(pdf_path: Path) -> list[PageText]:
    return asyncio.run(read_pdf_with_ocr_async(pdf_path))


def read_pdf_pages(pdf_path: Path, force_ocr: bool = False) -> list[PageText]:
    if force_ocr:
        logger.info("命中 OCR 白名单，强制走 OCR 提取 file=%s", pdf_path.name)
        return read_pdf_with_ocr(pdf_path)
    pages = read_pdf_with_fitz(pdf_path)
    if pages and not should_fallback_to_ocr(pages):
        return pages
    if not pages:
        pages = read_pdf_with_pypdf(pdf_path)
    if pages and not should_fallback_to_ocr(pages):
        return pages
    logger.info("检测到疑似图片型 PDF，回退 OCR 提取 file=%s", pdf_path.name)
    return read_pdf_with_ocr(pdf_path)


# 查找正文起始位置。
def find_body_start(pages: list[PageText]) -> tuple[int, int, str]:
    for page_index, page in enumerate(pages):
        match = BODY_START_RE.search(page.text)
        if match:
            return page_index, match.start(), match.group(1)
    return 0, 0, "FULL_TEXT"


def count_article_hits(text: str) -> int:
    return len(ARTICLE_RE.findall(text))


# 查找正文结束位置，避免过早截断。
def find_body_end(pages: list[PageText], start_page_index: int) -> tuple[int, int | None, str | None]:
    article_hits = 0
    for page_index in range(start_page_index, len(pages)):
        article_hits += count_article_hits(pages[page_index].text)
        if article_hits < 3:
            continue
        match = BODY_END_RE.search(pages[page_index].text)
        if match:
            return page_index, match.start(), match.group(1)
    return len(pages) - 1, None, None


# 按页提取正文内容并记录页码。
def extract_body(pages: list[PageText]) -> BodyResult:
    if not pages:
        return BodyResult(body_text="", body_pages=[], body_page_texts=[], start_anchor="EMPTY", end_anchor=None)
    start_page_index, start_offset, start_anchor = find_body_start(pages)
    end_page_index, end_offset, end_anchor = find_body_end(pages, start_page_index)
    body_parts: list[str] = []
    body_pages: list[int] = []
    body_page_texts: list[str] = []
    for page_index in range(start_page_index, end_page_index + 1):
        page_text = pages[page_index].text
        if page_index == start_page_index and start_offset > 0:
            page_text = page_text[start_offset:]
        if page_index == end_page_index and end_offset is not None:
            page_text = page_text[:end_offset]
        page_text = page_text.strip()
        if not page_text:
            continue
        body_parts.append(page_text)
        body_pages.append(pages[page_index].page_no)
        body_page_texts.append(page_text)
    body_text = "\n\n".join(body_parts).strip()
    return BodyResult(body_text=body_text, body_pages=body_pages, body_page_texts=body_page_texts, start_anchor=start_anchor, end_anchor=end_anchor)


# 在真正的结构起点前补换行，避免把正文中的引用条款误切成新条款。
def prepare_text_for_split(body_text: str) -> str:
    prepared = body_text
    prepared = re.sub(rf"({CHAPTER_NO_PATTERN}\s*[^\n]{{0,30}}?)(?={SECTION_NO_PATTERN}|{ARTICLE_NO_PATTERN})", r"\1\n", prepared)
    prepared = re.sub(rf"({SECTION_NO_PATTERN}\s*[^\n]{{0,30}}?)(?={ARTICLE_NO_PATTERN})", r"\1\n", prepared)
    prepared = re.sub(rf"([。；;：:]\s*)(?={CHAPTER_NO_PATTERN}|{SECTION_NO_PATTERN}|{ARTICLE_NO_PATTERN})", r"\1\n", prepared)
    prepared = re.sub(r"([：:；;])\s*(?=([（(][一二三四五六七八九十百千零〇\d]+[)）]))", r"\1\n", prepared)
    prepared = BLANK_LINE_RE.sub("\n\n", prepared)
    return prepared.strip()


# 判断条号后续文本是否更像“引用条款”，避免误识别成新条款。
def is_reference_like_article_suffix(article_suffix: str) -> bool:
    normalized = SPACE_RE.sub("", article_suffix or "")
    return bool(ARTICLE_REFERENCE_SUFFIX_RE.match(normalized))


# 从条文中提取分项列表。
def extract_items(article_text: str) -> list[str]:
    items: list[str] = []
    for match in ITEM_RE.finditer(article_text):
        item_no = match.group(1).strip()
        item_text = match.group(2).strip()
        items.append(f"{item_no} {item_text}".strip())
    return items


# 根据条文片段映射来源页码。
def find_source_pages(article_text: str, body_result: BodyResult) -> list[int]:
    if not article_text:
        return body_result.body_pages[:1]
    snippet = article_text[:80]
    matched_pages = []
    for page_no, page_text in zip(body_result.body_pages, body_result.body_page_texts):
        if snippet and snippet in page_text:
            matched_pages.append(page_no)
    if matched_pages:
        return matched_pages
    return body_result.body_pages[:1]


def find_chapter_for_article(article_start: int, chapter_matches: list[re.Match[str]]) -> tuple[str, str]:
    chapter_no = ""
    chapter_title = ""
    for chapter_match in chapter_matches:
        if chapter_match.start() > article_start:
            break
        chapter_no = chapter_match.group(1).strip()
        chapter_title = chapter_match.group(2).strip()
    return chapter_no, chapter_title


def find_section_for_article(article_start: int, section_matches: list[re.Match[str]]) -> tuple[str, str]:
    section_no = ""
    section_title = ""
    for section_match in section_matches:
        if section_match.start() > article_start:
            break
        section_no = section_match.group(1).strip()
        section_title = section_match.group(2).strip()
    return section_no, section_title


# 查找当前条款之后最近的结构起点，避免把下一章/节挂到上一条里。
def find_next_structure_start(article_start: int, chapter_matches: list[re.Match[str]], section_matches: list[re.Match[str]]) -> int | None:
    candidates: list[int] = []
    for chapter_match in chapter_matches:
        if chapter_match.start() > article_start:
            candidates.append(chapter_match.start())
            break
    for section_match in section_matches:
        if section_match.start() > article_start:
            candidates.append(section_match.start())
            break
    if not candidates:
        return None
    return min(candidates)


# 按第X条切分正文。
def build_clause_rows(document: DocumentResult) -> list[dict]:
    prepared_text = prepare_text_for_split(document.body_result.body_text)
    article_matches = [match for match in ARTICLE_RE.finditer(prepared_text) if not is_reference_like_article_suffix(match.group(2))]
    chapter_matches = list(CHAPTER_RE.finditer(prepared_text))
    section_matches = list(SECTION_RE.finditer(prepared_text))
    if not article_matches:
        return []
    clause_rows: list[dict] = []
    for index, article_match in enumerate(article_matches):
        article_start = article_match.start()
        next_article_start = article_matches[index + 1].start() if index + 1 < len(article_matches) else len(prepared_text)
        next_structure_start = find_next_structure_start(article_start, chapter_matches, section_matches)
        article_end = next_article_start
        if next_structure_start is not None:
            article_end = min(article_end, next_structure_start)
        article_text = prepared_text[article_start:article_end].strip()
        chapter_no, chapter_title = find_chapter_for_article(article_start, chapter_matches)
        section_no, section_title = find_section_for_article(article_start, section_matches)
        clause_rows.append(
            {
                "doc_id": document.doc_id,
                "category": document.category,
                "title": document.title,
                "file_name": document.file_name,
                "file_path": document.file_path,
                "chapter_no": chapter_no,
                "chapter_title": chapter_title,
                "section_no": section_no,
                "section_title": section_title,
                "article_no": article_match.group(1).strip(),
                "article_text": article_text,
                "items": extract_items(article_text),
                "source_pages": find_source_pages(article_text, document.body_result),
            }
        )
    return clause_rows


def build_document_row(document: DocumentResult, clause_count: int) -> dict:
    body_result = document.body_result
    return {
        "doc_id": document.doc_id,
        "category": document.category,
        "title": document.title,
        "file_name": document.file_name,
        "file_path": document.file_path,
        "total_pages": document.total_pages,
        "body_pages": body_result.body_pages,
        "body_page_count": len(body_result.body_pages),
        "start_anchor": body_result.start_anchor,
        "end_anchor": body_result.end_anchor,
        "body_text_length": len(body_result.body_text),
        "clause_count": clause_count,
    }


# 从已落盘的 OCR 全文构造正文结果，供失败重跑时直接切条。
def build_body_result_from_raw_text(raw_text: str) -> BodyResult:
    normalized_text = str(raw_text or "").strip()
    if not normalized_text:
        return BodyResult(body_text="", body_pages=[], body_page_texts=[], start_anchor="RAW_TEXT", end_anchor=None)
    return BodyResult(body_text=normalized_text, body_pages=[1], body_page_texts=[normalized_text], start_anchor="RAW_TEXT", end_anchor=None)


def build_error_row(category: str, file_name: str, error: str) -> dict:
    return {"category": category, "file_name": file_name, "error": error}


def build_failure_row(process_index: int, category: str, file_name: str, file_path: str, error: str) -> dict:
    return {"process_index": process_index, "category": category, "file_name": file_name, "file_path": file_path, "error": error}


def build_raw_text_manifest_row(document: DocumentResult, raw_text_path: Path) -> dict:
    return {
        "doc_id": document.doc_id,
        "category": document.category,
        "title": document.title,
        "file_name": document.file_name,
        "file_path": document.file_path,
        "raw_text_path": str(raw_text_path),
        "total_pages": document.total_pages,
        "body_pages": document.body_result.body_pages,
        "body_text_length": len(document.body_result.body_text),
    }


def write_jsonl_rows(output_path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as file_obj:
        for row in rows:
            file_obj.write(json.dumps(row, ensure_ascii=False) + "\n")


# 读取 jsonl 全量记录。
def read_jsonl_rows(input_path: Path) -> list[dict]:
    if not input_path.exists():
        return []
    rows: list[dict] = []
    with input_path.open("r", encoding="utf-8") as file_obj:
        for raw_line in file_obj:
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception as err:
                logger.warning("读取 JSONL 失败 file=%s err=%s line=%s", input_path, err, line[:500])
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def load_raw_text_manifest_items(manifest_path: Path) -> list[tuple[str, str, Path, Path]]:
    rows = read_jsonl_rows(manifest_path)
    items: list[tuple[str, str, Path, Path]] = []
    seen: set[str] = set()
    for row in rows:
        file_name = str(row.get("file_name") or "").strip()
        file_path = str(row.get("file_path") or "").strip()
        raw_text_path = str(row.get("raw_text_path") or "").strip()
        category = str(row.get("category") or "").strip() or "未分类"
        if not file_name or not file_path or not raw_text_path or raw_text_path in seen:
            continue
        seen.add(raw_text_path)
        items.append((category, file_name, Path(file_path).expanduser().resolve(), Path(raw_text_path).expanduser().resolve()))
    return items


# 一次性重写 jsonl 文件。
def rewrite_jsonl_rows(output_path: Path, rows: list[dict]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as file_obj:
        for row in rows:
            file_obj.write(json.dumps(row, ensure_ascii=False) + "\n")


# 从 failed_documents.jsonl 构造重跑任务列表。
def load_rerun_failed_items(failed_jsonl_path: Path) -> list[tuple[str, str, Path]]:
    rows = read_jsonl_rows(failed_jsonl_path)
    items: list[tuple[str, str, Path]] = []
    seen: set[str] = set()
    for row in rows:
        file_path = str(row.get("file_path") or "").strip()
        category = str(row.get("category") or "").strip() or "未分类"
        file_name = str(row.get("file_name") or "").strip()
        if not file_path or not file_name or file_path in seen:
            continue
        seen.add(file_path)
        items.append((category, file_name, Path(file_path).expanduser().resolve()))
    return items


def filter_manifest_items_for_rerun(manifest_items: list[tuple[str, str, Path, Path]], rerun_doc_id: str, rerun_doc_ids: set[str], rerun_file_name: str) -> list[tuple[str, str, Path, Path]]:
    doc_id_filter = str(rerun_doc_id or "").strip()
    file_name_filter = str(rerun_file_name or "").strip()
    if not doc_id_filter and not rerun_doc_ids and not file_name_filter:
        return manifest_items
    matched = []
    for category, file_name, pdf_path, raw_text_path in manifest_items:
        doc_id = build_doc_id(category, file_name)
        if doc_id_filter and doc_id != doc_id_filter:
            continue
        if rerun_doc_ids and doc_id not in rerun_doc_ids:
            continue
        if file_name_filter and file_name != file_name_filter:
            continue
        matched.append((category, file_name, pdf_path, raw_text_path))
    return matched


def apply_doc_rerun_updates(output_dir: Path, updated_documents: list[dict], updated_clauses: list[dict], updated_doc_ids: set[str]) -> None:
    documents_path = output_dir / "documents.jsonl"
    clauses_path = output_dir / "clauses.jsonl"
    documents_rows = [row for row in read_jsonl_rows(documents_path) if str(row.get("doc_id") or "") not in updated_doc_ids]
    clauses_rows = [row for row in read_jsonl_rows(clauses_path) if str(row.get("doc_id") or "") not in updated_doc_ids]
    documents_rows.extend(updated_documents)
    clauses_rows.extend(updated_clauses)
    rewrite_jsonl_rows(documents_path, documents_rows)
    rewrite_jsonl_rows(clauses_path, clauses_rows)


# 失败重跑成功后，按 file_path 回写更新结果文件。
def apply_rerun_updates(output_dir: Path, updated_documents: list[dict], updated_clauses: list[dict], succeeded_paths: set[str]) -> None:
    documents_path = output_dir / "documents.jsonl"
    clauses_path = output_dir / "clauses.jsonl"
    errors_path = output_dir / "errors.jsonl"
    failed_path = output_dir / "failed_documents.jsonl"

    documents_rows = [row for row in read_jsonl_rows(documents_path) if str(row.get("file_path") or "") not in succeeded_paths]
    clauses_rows = [row for row in read_jsonl_rows(clauses_path) if str(row.get("file_path") or "") not in succeeded_paths]
    errors_rows = [row for row in read_jsonl_rows(errors_path) if str(row.get("file_path") or "") not in succeeded_paths]
    failed_rows = [row for row in read_jsonl_rows(failed_path) if str(row.get("file_path") or "") not in succeeded_paths]

    documents_rows.extend(updated_documents)
    clauses_rows.extend(updated_clauses)

    rewrite_jsonl_rows(documents_path, documents_rows)
    rewrite_jsonl_rows(clauses_path, clauses_rows)
    rewrite_jsonl_rows(errors_path, errors_rows)
    rewrite_jsonl_rows(failed_path, failed_rows)


def write_text(output_path: Path, text: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")


# 读取已落盘的 OCR 全文。
def read_text(input_path: Path) -> str:
    return input_path.read_text(encoding="utf-8")


def write_json(output_path: Path, payload: dict) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def iter_catalog_items(catalog: dict[str, list[str]], category_filter: str, limit: int) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for category, file_names in catalog.items():
        if category_filter and category != category_filter:
            continue
        for file_name in file_names:
            items.append((category, file_name))
            if limit > 0 and len(items) >= limit:
                return items
    return items


# 按 PDF 目录全量扫描，JSON 只作为分类补充信息。
def iter_scan_items(pdf_dir: Path, catalog_name_index: dict[str, list[str]], category_filter: str, limit: int) -> list[tuple[str, str, Path]]:
    items: list[tuple[str, str, Path]] = []
    for pdf_path in scan_pdf_paths(pdf_dir):
        category = infer_category(pdf_path, pdf_dir, catalog_name_index)
        if category_filter and category != category_filter:
            continue
        items.append((category, pdf_path.name, pdf_path))
        if limit > 0 and len(items) >= limit:
            break
    return items


def process_document(category: str, file_name: str, pdf_path: Path, force_ocr: bool = False) -> tuple[DocumentResult, list[dict]]:
    pages = read_pdf_pages(pdf_path, force_ocr=force_ocr)
    body_result = extract_body(pages)
    document = DocumentResult(
        doc_id=build_doc_id(category, file_name),
        category=category,
        title=pdf_path.stem,
        file_name=file_name,
        file_path=str(pdf_path),
        total_pages=len(pages),
        body_result=body_result,
    )
    clause_rows = build_clause_rows(document)
    return document, clause_rows


def process_document_raw_text_only(category: str, file_name: str, pdf_path: Path, force_ocr: bool = False) -> DocumentResult:
    pages = read_pdf_pages(pdf_path, force_ocr=force_ocr)
    body_result = extract_body(pages)
    return DocumentResult(
        doc_id=build_doc_id(category, file_name),
        category=category,
        title=pdf_path.stem,
        file_name=file_name,
        file_path=str(pdf_path),
        total_pages=len(pages),
        body_result=body_result,
    )


# 默认走 OCR + LLM 结构切条，按页分块提取后合并输出。
async def process_document_with_llm(category: str, file_name: str, pdf_path: Path, llm: AsyncLLMClient, model: str, thinking_extra_body: dict[str, Any] | None, business_retries: int, chunk_pages: int, overlap_pages: int, raw_text_path: Path | None = None, log_raw_on_failure: bool = False) -> tuple[DocumentResult, list[dict]]:
    pages = await read_pdf_with_ocr_async(pdf_path)
    body_result = extract_body_without_end(pages)
    if raw_text_path is not None:
        write_text(raw_text_path, body_result.body_text)
    chunks = build_llm_page_chunks(body_result, chunk_pages, overlap_pages)
    if not chunks:
        llm_payload = {"document_title": pdf_path.stem, "articles": []}
    else:
        payloads: list[dict[str, Any]] = []
        total_chunks = len(chunks)
        for chunk_index, chunk in enumerate(chunks, start=1):
            logger.info("LLM分块切条 chunk=%s/%s file=%s pages=%s", chunk_index, total_chunks, file_name, chunk["page_nos"])
            payload = await call_llm_clause_parser(llm, model, thinking_extra_body, pdf_path.stem, str(chunk["body_text"] or ""), list(chunk["page_nos"]), business_retries, log_raw_on_failure=log_raw_on_failure)
            payloads.append(payload)
        llm_payload = merge_llm_clause_payloads(payloads)
    title = str(llm_payload.get("document_title") or "").strip() or pdf_path.stem
    document = DocumentResult(
        doc_id=build_doc_id(category, file_name),
        category=category,
        title=title,
        file_name=file_name,
        file_path=str(pdf_path),
        total_pages=len(pages),
        body_result=body_result,
    )
    clause_rows = build_clause_rows_from_llm(document, llm_payload)
    return document, clause_rows


# 失败重跑时直接复用已落盘的 OCR 全文做条款提取。
async def process_document_with_llm_from_raw_text(category: str, file_name: str, pdf_path: Path, raw_text_path: Path, llm: AsyncLLMClient, model: str, thinking_extra_body: dict[str, Any] | None, business_retries: int, log_raw_on_failure: bool = False) -> tuple[DocumentResult, list[dict]]:
    if raw_text_path.exists():
        body_result = build_body_result_from_raw_text(read_text(raw_text_path))
    else:
        logger.info("重跑未找到 raw_text，开始复用页级 OCR 缓存 file=%s", pdf_path.name)
        try:
            pages = read_pdf_with_cached_ocr(pdf_path)
            body_result = extract_body_without_end(pages)
        except Exception as err:
            logger.warning("复用页级 OCR 缓存失败，回退重新 OCR file=%s err=%s", pdf_path.name, err)
            pages = await read_pdf_with_ocr_async(pdf_path)
            body_result = extract_body_without_end(pages)
        write_text(raw_text_path, body_result.body_text)
    payload = await call_llm_clause_parser(llm, model, thinking_extra_body, pdf_path.stem, body_result.body_text, [], business_retries, log_raw_on_failure=log_raw_on_failure)
    title = str(payload.get("document_title") or "").strip() or pdf_path.stem
    document = DocumentResult(
        doc_id=build_doc_id(category, file_name),
        category=category,
        title=title,
        file_name=file_name,
        file_path=str(pdf_path),
        total_pages=1 if body_result.body_text else 0,
        body_result=body_result,
    )
    clause_rows = build_clause_rows_from_llm(document, payload)
    return document, clause_rows


async def process_document_with_llm_raw_text_only(category: str, file_name: str, pdf_path: Path, raw_text_path: Path | None = None) -> DocumentResult:
    pages = await read_pdf_with_ocr_async(pdf_path)
    body_result = extract_body_without_end(pages)
    if raw_text_path is not None:
        write_text(raw_text_path, body_result.body_text)
    return DocumentResult(
        doc_id=build_doc_id(category, file_name),
        category=category,
        title=pdf_path.stem,
        file_name=file_name,
        file_path=str(pdf_path),
        total_pages=len(pages),
        body_result=body_result,
    )


async def process_document_clauses_from_raw_text(category: str, file_name: str, pdf_path: Path, raw_text_path: Path, llm: AsyncLLMClient, model: str, thinking_extra_body: dict[str, Any] | None, business_retries: int, use_page_chunks: bool = False, chunk_pages: int = 3, overlap_pages: int = 1, log_raw_on_failure: bool = False) -> tuple[DocumentResult, list[dict]]:
    if not raw_text_path.exists():
        raise RuntimeError(f"raw_text 不存在: {raw_text_path}")
    if use_page_chunks:
        logger.info("clauses-from-raw-text 启用页级分块切条 file=%s chunk_pages=%s overlap_pages=%s", file_name, chunk_pages, overlap_pages)
        try:
            pages = read_pdf_with_cached_ocr(pdf_path)
        except Exception as err:
            logger.warning("页级 OCR 缓存缺失或不可用，将回退使用 raw_text 整体切条（不分页） file=%s err=%s", pdf_path.name, err)
            body_result = build_body_result_from_raw_text(read_text(raw_text_path))
            payload = await call_llm_clause_parser(llm, model, thinking_extra_body, pdf_path.stem, body_result.body_text, [], business_retries, log_raw_on_failure=log_raw_on_failure)
        else:
            body_result = extract_body_without_end(pages)
            chunks = build_llm_page_chunks(body_result, chunk_pages, overlap_pages)
            if not chunks:
                payload = {"document_title": pdf_path.stem, "articles": []}
            else:
                payloads: list[dict[str, Any]] = []
                total_chunks = len(chunks)
                for chunk_index, chunk in enumerate(chunks, start=1):
                    logger.info("raw_text分块切条 chunk=%s/%s file=%s pages=%s", chunk_index, total_chunks, file_name, chunk["page_nos"])
                    payloads.append(await call_llm_clause_parser(llm, model, thinking_extra_body, pdf_path.stem, str(chunk["body_text"] or ""), list(chunk["page_nos"]), business_retries, log_raw_on_failure=log_raw_on_failure))
                payload = merge_llm_clause_payloads(payloads)
    else:
        body_result = build_body_result_from_raw_text(read_text(raw_text_path))
        payload = await call_llm_clause_parser(llm, model, thinking_extra_body, pdf_path.stem, body_result.body_text, [], business_retries, log_raw_on_failure=log_raw_on_failure)
    title = str(payload.get("document_title") or "").strip() or pdf_path.stem
    document = DocumentResult(
        doc_id=build_doc_id(category, file_name),
        category=category,
        title=title,
        file_name=file_name,
        file_path=str(pdf_path),
        total_pages=len(body_result.body_pages) if use_page_chunks else 1 if body_result.body_text else 0,
        body_result=body_result,
    )
    clause_rows = build_clause_rows_from_llm(document, payload)
    return document, clause_rows


def reset_output_files(output_dir: Path) -> tuple[Path, Path, Path, Path]:
    documents_path = output_dir / "documents.jsonl"
    clauses_path = output_dir / "clauses.jsonl"
    errors_path = output_dir / "errors.jsonl"
    failed_path = output_dir / "failed_documents.jsonl"
    for path in (documents_path, clauses_path, errors_path, failed_path):
        if path.exists():
            path.unlink()
        path.touch()
    return documents_path, clauses_path, errors_path, failed_path


# 读取已成功处理的文档路径，用于断点续跑。
def load_processed_file_paths(documents_path: Path) -> set[str]:
    if not documents_path.exists():
        return set()
    processed: set[str] = set()
    with documents_path.open("r", encoding="utf-8") as file_obj:
        for raw_line in file_obj:
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception as err:
                logger.warning("读取已处理文档索引失败 file=%s err=%s line=%s", documents_path, err, line[:500])
                continue
            file_path = str(payload.get("file_path") or "").strip()
            if file_path:
                processed.add(file_path)
    return processed


# 根据是否续跑初始化输出文件。
def prepare_output_files(output_dir: Path, resume: bool) -> tuple[Path, Path, Path, Path, set[str]]:
    documents_path = output_dir / "documents.jsonl"
    clauses_path = output_dir / "clauses.jsonl"
    errors_path = output_dir / "errors.jsonl"
    failed_path = output_dir / "failed_documents.jsonl"
    if resume:
        for path in (documents_path, clauses_path, errors_path, failed_path):
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.touch()
        return documents_path, clauses_path, errors_path, failed_path, load_processed_file_paths(documents_path)
    documents_path, clauses_path, errors_path, failed_path = reset_output_files(output_dir)
    return documents_path, clauses_path, errors_path, failed_path, set()


def build_catalog_audit(catalog: dict[str, list[str]], pdf_dir: Path, pdf_index: dict[str, list[Path]]) -> dict:
    catalog_names = sorted({file_name for file_names in catalog.values() for file_name in file_names})
    pdf_paths = scan_pdf_paths(pdf_dir)
    pdf_names = sorted({path.name for path in pdf_paths})
    catalog_only = sorted([name for name in catalog_names if name.lower() not in pdf_index])
    pdf_only = sorted([name for name in pdf_names if name not in catalog_names])
    duplicate_pdf_names = sorted([name for name, paths in pdf_index.items() if len(paths) > 1])
    return {
        "catalog_document_count": sum(len(file_names) for file_names in catalog.values()),
        "catalog_unique_name_count": len(catalog_names),
        "pdf_document_count": len(pdf_paths),
        "pdf_unique_name_count": len(pdf_names),
        "catalog_only_names": catalog_only,
        "pdf_only_names": pdf_only,
        "duplicate_pdf_names": duplicate_pdf_names,
    }


def main() -> int:
    args = build_parser().parse_args()
    configure_logger(args.debug)

    if args.raw_text_only and args.clauses_from_raw_text:
        raise ValueError("--raw-text-only 与 --clauses-from-raw-text 不能同时使用")
    output_dir = Path(args.output_dir).expanduser().resolve()
    raw_text_dir = output_dir / "raw_texts"
    raw_text_manifest_path = output_dir / "raw_text_manifest.txt"
    raw_text_manifest_jsonl_path = output_dir / "raw_text_manifest.jsonl"
    output_dir.mkdir(parents=True, exist_ok=True)

    load_env_files()

    catalog: dict[str, list[str]] = {}
    force_ocr_names: set[str] = set()
    force_llm_names: set[str] = set()
    rerun_doc_ids: set[str] = set()
    rerun_failed_mode = bool(str(args.rerun_failed_jsonl or "").strip())
    if str(args.rerun_doc_id_list or "").strip():
        rerun_doc_ids = load_doc_id_list(Path(args.rerun_doc_id_list).expanduser().resolve())
    rerun_doc_mode = bool(str(args.rerun_doc_id or "").strip() or rerun_doc_ids or str(args.rerun_file_name or "").strip())
    clauses_from_raw_text_mode = bool(args.clauses_from_raw_text)
    manifest_items: list[tuple[str, str, Path, Path]] = []
    if rerun_doc_mode and not clauses_from_raw_text_mode:
        raise ValueError("使用 --rerun-doc-id/--rerun-doc-id-list/--rerun-file-name 时必须同时启用 --clauses-from-raw-text")
    if clauses_from_raw_text_mode:
        manifest_items = load_raw_text_manifest_items(raw_text_manifest_jsonl_path)
        manifest_items = filter_manifest_items_for_rerun(manifest_items, args.rerun_doc_id, rerun_doc_ids, args.rerun_file_name)
        if rerun_doc_mode and not manifest_items:
            raise ValueError("未在 raw_text_manifest.jsonl 中找到指定文档")
        items = [(category, file_name, pdf_path) for category, file_name, pdf_path, _ in manifest_items]
    else:
        if not str(args.pdf_dir or "").strip():
            raise ValueError("未启用 --clauses-from-raw-text 时必须提供 --pdf-dir")
        pdf_dir = Path(args.pdf_dir).expanduser().resolve()
        if args.ocr_pdf_list.strip():
            force_ocr_names = load_named_pdf_list(Path(args.ocr_pdf_list).expanduser().resolve())
        if args.llm_pdf_list.strip():
            force_llm_names = load_named_pdf_list(Path(args.llm_pdf_list).expanduser().resolve())
        if args.catalog_json.strip():
            catalog_path = Path(args.catalog_json).expanduser().resolve()
            catalog = load_catalog(catalog_path)
        elif args.input_mode == "catalog":
            raise ValueError("input-mode=catalog 时必须提供 --catalog-json")
        catalog_name_index = build_catalog_name_index(catalog)
        pdf_index = build_pdf_index(pdf_dir)
        if catalog:
            write_json(output_dir / "catalog_audit.json", build_catalog_audit(catalog, pdf_dir, pdf_index))
        rerun_failed_jsonl = Path(args.rerun_failed_jsonl).expanduser().resolve() if str(args.rerun_failed_jsonl or "").strip() else output_dir / "failed_documents.jsonl"
        if rerun_failed_mode:
            items = load_rerun_failed_items(rerun_failed_jsonl)
        elif args.input_mode == "catalog":
            catalog_items = iter_catalog_items(catalog, args.category.strip(), args.limit)
            items = [(category, file_name, resolve_pdf_path(file_name, category, pdf_index)) for category, file_name in catalog_items]
        else:
            items = iter_scan_items(pdf_dir, catalog_name_index, args.category.strip(), args.limit)
    documents_path, clauses_path, errors_path, failed_path, processed_file_paths = prepare_output_files(output_dir, bool(args.resume) or rerun_failed_mode)
    if not force_llm_names:
        force_llm_names = {pdf_path.name.lower() for _, _, pdf_path in items if pdf_path is not None}

    llm_model = get_clause_llm_model(str(args.llm_model or ""))
    llm_thinking_extra_body = build_thinking_extra_body(llm_model, False)
    llm_client = AsyncLLMClient(config=ModelConfig(default_model=llm_model), name="zhidu_clause_extract_llm") if items else None

    logger.info("开始处理制度文件 mode=%s rerun_failed=%s 总数=%s llm_doc_count=%s", args.input_mode, rerun_failed_mode, len(items), len(force_llm_names))
    processed_count = 0
    clause_count = 0
    error_count = 0

    total_count = len(items)
    skip_count = 0
    raw_text_manifest_rows: list[str] = []
    raw_text_manifest_jsonl_rows: list[dict] = []
    rerun_updated_documents: list[dict] = []
    rerun_updated_clauses: list[dict] = []
    rerun_succeeded_paths: set[str] = set()
    rerun_doc_updated_documents: list[dict] = []
    rerun_doc_updated_clauses: list[dict] = []
    rerun_doc_updated_doc_ids: set[str] = set()
    for process_index, (category, file_name, pdf_path) in enumerate(items, start=1):
        logger.info("处理进度 %s/%s category=%s file=%s success=%s error=%s skip=%s", process_index, total_count, category, file_name, processed_count, error_count, skip_count)
        if pdf_path is None:
            logger.warning("未找到 PDF 文件 index=%s category=%s file=%s", process_index, category, file_name)
            write_jsonl_rows(errors_path, [build_error_row(category, file_name, "pdf_not_found")])
            write_jsonl_rows(failed_path, [build_failure_row(process_index, category, file_name, "", "pdf_not_found")])
            error_count += 1
            continue
        if not rerun_failed_mode and not rerun_doc_mode and str(pdf_path) in processed_file_paths:
            skip_count += 1
            logger.info("断点续跑跳过 index=%s/%s category=%s file=%s", process_index, total_count, category, file_name)
            continue
        doc_id = build_doc_id(category, file_name)
        raw_text_path = next((manifest_raw_text_path for manifest_category, manifest_file_name, manifest_pdf_path, manifest_raw_text_path in manifest_items if manifest_category == category and manifest_file_name == file_name and manifest_pdf_path == pdf_path), raw_text_dir / f"{doc_id}.txt")
        try:
            if clauses_from_raw_text_mode:
                if llm_client is None:
                    raise RuntimeError("LLM 客户端未初始化")
                document, clause_rows = asyncio.run(process_document_clauses_from_raw_text(category, file_name, pdf_path, raw_text_path, llm_client, llm_model, llm_thinking_extra_body, int(args.llm_business_retries), use_page_chunks=bool(args.clauses_from_raw_text_use_page_chunks), chunk_pages=int(args.llm_chunk_pages), overlap_pages=int(args.llm_chunk_overlap_pages), log_raw_on_failure=True))
            elif args.raw_text_only:
                if pdf_path.name.lower() in force_llm_names:
                    document = asyncio.run(process_document_with_llm_raw_text_only(category, file_name, pdf_path, raw_text_path=raw_text_path))
                else:
                    document = process_document_raw_text_only(category, file_name, pdf_path, force_ocr=pdf_path.name.lower() in force_ocr_names)
                clause_rows = []
            elif pdf_path.name.lower() in force_llm_names:
                if llm_client is None:
                    raise RuntimeError("LLM 客户端未初始化")
                if rerun_failed_mode:
                    document, clause_rows = asyncio.run(process_document_with_llm_from_raw_text(category, file_name, pdf_path, raw_text_path, llm_client, llm_model, llm_thinking_extra_body, int(args.llm_business_retries), log_raw_on_failure=True))
                else:
                    document, clause_rows = asyncio.run(process_document_with_llm(category, file_name, pdf_path, llm_client, llm_model, llm_thinking_extra_body, int(args.llm_business_retries), int(args.llm_chunk_pages), int(args.llm_chunk_overlap_pages), raw_text_path=raw_text_path, log_raw_on_failure=False))
            else:
                document, clause_rows = process_document(category, file_name, pdf_path, force_ocr=pdf_path.name.lower() in force_ocr_names)
        except Exception as err:  # pylint: disable=broad-except
            logger.error("处理 PDF 失败 index=%s category=%s file=%s err=%s", process_index, category, file_name, err)
            if rerun_failed_mode:
                print(f"[RERUN_FAILED] file={file_name} path={pdf_path} err={err}")
            write_jsonl_rows(errors_path, [build_error_row(category, file_name, str(err))])
            write_jsonl_rows(failed_path, [build_failure_row(process_index, category, file_name, str(pdf_path), str(err))])
            error_count += 1
            continue

        write_text(raw_text_path, document.body_result.body_text)
        raw_text_manifest_rows.append(str(raw_text_path))
        raw_text_manifest_jsonl_rows.append(build_raw_text_manifest_row(document, raw_text_path))
        if args.raw_text_only:
            processed_count += 1
            logger.info("全文提取完成 index=%s/%s category=%s file=%s raw_text=%s success=%s error=%s", process_index, total_count, category, file_name, raw_text_path, processed_count, error_count)
            continue
        document_row = build_document_row(document, len(clause_rows))
        document_row["process_index"] = process_index
        if rerun_failed_mode:
            rerun_updated_documents.append(document_row)
            rerun_updated_clauses.extend(clause_rows)
            rerun_succeeded_paths.add(str(pdf_path))
        elif rerun_doc_mode:
            rerun_doc_updated_documents.append(document_row)
            rerun_doc_updated_clauses.extend(clause_rows)
            rerun_doc_updated_doc_ids.add(str(document_row.get("doc_id") or ""))
        else:
            write_jsonl_rows(documents_path, [document_row])
            write_jsonl_rows(clauses_path, clause_rows)
        if not document.body_result.body_text:
            logger.warning("正文为空 index=%s category=%s file=%s", process_index, category, file_name)
            write_jsonl_rows(errors_path, [build_error_row(category, file_name, "body_text_empty")])
            write_jsonl_rows(failed_path, [build_failure_row(process_index, category, file_name, str(pdf_path), "body_text_empty")])
            error_count += 1
        if not clause_rows:
            logger.warning("未切分出条款 index=%s category=%s file=%s", process_index, category, file_name)
            write_jsonl_rows(errors_path, [build_error_row(category, file_name, "no_clause_found")])
            write_jsonl_rows(failed_path, [build_failure_row(process_index, category, file_name, str(pdf_path), "no_clause_found")])
            error_count += 1

        processed_count += 1
        clause_count += len(clause_rows)
        logger.info("处理完成 index=%s/%s category=%s file=%s clauses=%s success=%s error=%s", process_index, total_count, category, file_name, len(clause_rows), processed_count, error_count)

    print(f"输出目录: {output_dir}")
    print(f"处理模式: {args.input_mode}")
    if raw_text_manifest_rows and not rerun_doc_mode:
        write_text(raw_text_manifest_path, "\n".join(raw_text_manifest_rows) + "\n")
        rewrite_jsonl_rows(raw_text_manifest_jsonl_path, raw_text_manifest_jsonl_rows)
        print(f"全文清单: {raw_text_manifest_path}")
        print(f"全文清单JSONL: {raw_text_manifest_jsonl_path}")
    print(f"成功处理文档数: {processed_count}")
    print(f"输出条款数: {clause_count}")
    if rerun_failed_mode and rerun_succeeded_paths:
        apply_rerun_updates(output_dir, rerun_updated_documents, rerun_updated_clauses, rerun_succeeded_paths)
    if rerun_doc_mode and rerun_doc_updated_doc_ids:
        apply_doc_rerun_updates(output_dir, rerun_doc_updated_documents, rerun_doc_updated_clauses, rerun_doc_updated_doc_ids)
    print(f"错误数: {error_count}")
    print(f"跳过数: {skip_count}")
    print(f"重跑成功更新数: {len(rerun_succeeded_paths)}")
    if rerun_doc_mode:
        print(f"指定文档重切更新数: {len(rerun_doc_updated_doc_ids)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
