from __future__ import annotations

import html
import json
import mimetypes
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

import uvicorn
from fastapi import APIRouter, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse


APP_DIR = Path(__file__).resolve().parent
DEFAULT_STORAGE_DIR = APP_DIR
STORAGE_DIR = Path(os.getenv("UPLOAD_PAGE_STORAGE_DIR") or str(DEFAULT_STORAGE_DIR)).expanduser().resolve()
LOG_DIR = APP_DIR.parent / "logs"
PID_FILE = LOG_DIR / "upload_page.pid"
LOG_FILE = LOG_DIR / "upload_page.log"
ROOT_FILE = LOG_DIR / "upload_page.root"
PUBLIC_BASE_URL = str(os.getenv("UPLOAD_PAGE_BASE_URL") or "").strip().rstrip("/")
BASE_PATH = "/" + str(os.getenv("UPLOAD_PAGE_BASE_PATH") or "").strip().strip("/")
BASE_PATH = "" if BASE_PATH == "/" else BASE_PATH
PUBLIC_ROUTE_PREFIX = BASE_PATH or "/ai"
HOST = "0.0.0.0"
PORT = 10081
TEXT_EXTENSIONS = {".py", ".md", ".txt", ".json", ".jsonl", ".yaml", ".yml", ".log", ".sh", ".ttl", ".sparql"}
IMAGE_EXTENSIONS = {".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp"}
INLINE_PREVIEW_EXTENSIONS = TEXT_EXTENSIONS | {".pdf"}
app = FastAPI(title="文件页面", version="1.0.0")
router = APIRouter(prefix=BASE_PATH)


def route_with_base(path: str) -> str:
    normalized = "/" + str(path or "").lstrip("/")
    return f"{BASE_PATH}{normalized}" if BASE_PATH else normalized


def browser_route(path: str, route_prefix: str = PUBLIC_ROUTE_PREFIX) -> str:
    normalized = str(path or "").lstrip("/")
    prefix = "/" + str(route_prefix or "").strip().strip("/")
    prefix = "" if prefix == "/" else prefix
    return f"{prefix}/{normalized}" if normalized else f"{prefix}/"


def normalize_relative_dir(value: str = "") -> str:
    raw = str(value or "").strip().replace("\\", "/").strip("/")
    parts = [part for part in raw.split("/") if part]
    if any(part in {".", ".."} for part in parts):
        raise HTTPException(status_code=400, detail="非法目录")
    return "/".join(parts)


def resolve_display_dir(relative_dir: str = "") -> Path:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    normalized = normalize_relative_dir(relative_dir)
    target = STORAGE_DIR.joinpath(*([part for part in normalized.split("/") if part] or []))
    try:
        target.relative_to(STORAGE_DIR)
    except ValueError:
        raise HTTPException(status_code=400, detail="非法目录")
    if not target.exists():
        raise HTTPException(status_code=404, detail="目录不存在")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="目标不是目录")
    return target


def list_current_dir_entries(relative_dir: str = "", flat_mode: bool = False) -> list[Path]:
    current_dir = resolve_display_dir(relative_dir)
    if not flat_mode:
        return sorted(list(current_dir.iterdir()), key=lambda item: item.name.lower())
    entries: dict[str, Path] = {}
    for root, _, filenames in os.walk(current_dir, followlinks=True):
        root_path = Path(root)
        for child in root_path.iterdir():
            if child == current_dir:
                continue
            relative_name = child.relative_to(current_dir).as_posix()
            entries[relative_name] = child
        for filename in filenames:
            file_path = root_path / filename
            if file_path.is_file():
                relative_name = file_path.relative_to(current_dir).as_posix()
                entries[relative_name] = file_path
    return [entries[key] for key in sorted(entries.keys(), key=str.lower)]


def describe_entry(item: Path) -> str:
    if item.is_symlink() and item.is_dir():
        return "目录软连接"
    if item.is_symlink() and item.is_file():
        return "文件软连接"
    if item.is_dir():
        return "目录"
    return "文件"


def build_dir_link(relative_dir: str = "", flat_mode: bool = False) -> str:
    normalized = normalize_relative_dir(relative_dir)
    query_parts: list[str] = []
    if normalized:
        query_parts.append(f"path={quote(normalized)}")
    if flat_mode:
        query_parts.append("flat=1")
    base_index = browser_route("/")
    return base_index if not query_parts else f"{base_index}?{'&'.join(query_parts)}"


def json_preview_back_link(filepath: str) -> str:
    relative_path = normalize_relative_dir(filepath)
    path_parts = relative_path.split("/") if relative_path else []
    parent_relative_dir = "/".join(path_parts[:-1])
    if not parent_relative_dir:
        return browser_route("/")
    return f"{browser_route('/')}?path={quote(parent_relative_dir)}"


def render_json_node_html(payload) -> str:
    if isinstance(payload, dict):
        if not payload:
            return '<span class="json-leaf">{}</span>'
        items: list[str] = []
        for key, value in payload.items():
            items.append(f'<div class="json-item"><span class="json-key">"{html.escape(str(key))}"</span>: {render_json_node_html(value)}</div>')
        return f'<details class="json-tree" open><summary>{{...}}</summary><div class="json-children">{"".join(items)}</div></details>'
    if isinstance(payload, list):
        if not payload:
            return '<span class="json-leaf">[]</span>'
        items: list[str] = []
        for index, value in enumerate(payload):
            items.append(f'<div class="json-item"><span class="json-key">[{index}]</span>: {render_json_node_html(value)}</div>')
        return f'<details class="json-tree" open><summary>[...]</summary><div class="json-children">{"".join(items)}</div></details>'
    return f'<span class="json-leaf">{html.escape(json.dumps(payload, ensure_ascii=False))}</span>'


def build_json_preview_page(title: str, filepath: str, body_html: str) -> str:
    back_link = json_preview_back_link(filepath)
    return f"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 20px; color: #222; }}
    .toolbar {{ margin-bottom: 16px; display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }}
    .toolbar a {{ color: #1677ff; text-decoration: none; }}
    .toolbar button {{ padding: 6px 12px; border: 1px solid #d1d5db; border-radius: 8px; background: #fff; cursor: pointer; }}
    .json-row {{ display: grid; grid-template-columns: 64px 1fr; gap: 12px; padding: 12px 0; border-bottom: 1px solid #eee; }}
    .json-line {{ color: #888; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
    .json-summary {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; background: #f7f7f7; border-radius: 6px; padding: 8px 10px; word-break: break-all; }}
    .json-invalid {{ color: #c62828; font-size: 12px; margin-bottom: 6px; }}
    .json-tree {{ margin-top: 8px; }}
    .json-children {{ margin-left: 18px; padding-left: 12px; border-left: 2px solid #eee; }}
    .json-item {{ margin: 6px 0; }}
    .json-key {{ color: #14532d; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
    .json-leaf {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
    pre {{ margin: 8px 0 0; padding: 10px 12px; background: #fafafa; border: 1px solid #eee; border-radius: 8px; overflow: auto; }}
    .json-empty {{ color: #666; }}
  </style>
</head>
<body>
  <div class="toolbar">
    <a href="{back_link}">返回</a>
    <code>{html.escape(filepath)}</code>
    <button type="button" id="expand-all-btn">全部展开</button>
    <button type="button" id="collapse-all-btn">全部收起</button>
  </div>
  {body_html}
  <script>
    document.getElementById('expand-all-btn')?.addEventListener('click', function() {{
      document.querySelectorAll('details').forEach(function(item) {{ item.open = true; }});
    }});
    document.getElementById('collapse-all-btn')?.addEventListener('click', function() {{
      document.querySelectorAll('details').forEach(function(item) {{ item.open = false; }});
    }});
  </script>
</body>
</html>
"""


def build_toggle_preview_page(title: str, filepath: str, structured_html: str, raw_html: str) -> str:
    back_link = json_preview_back_link(filepath)
    return f"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 20px; color: #222; }}
    .toolbar {{ margin-bottom: 16px; display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }}
    .toolbar a {{ color: #1677ff; text-decoration: none; }}
    .toolbar button {{ padding: 6px 12px; border: 1px solid #d1d5db; border-radius: 8px; background: #fff; cursor: pointer; }}
    .section {{ display: none; }}
    .section.active {{ display: block; }}
    .ttl-subject, .sparql-block {{ border: 1px solid #eee; border-radius: 8px; margin: 12px 0; padding: 12px; }}
    .ttl-subject-title, .sparql-title {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-weight: 600; margin-bottom: 8px; }}
    .ttl-row {{ display: grid; grid-template-columns: 240px 1fr; gap: 12px; padding: 6px 0; border-top: 1px solid #f3f4f6; }}
    .ttl-row:first-of-type {{ border-top: 0; }}
    .ttl-predicate, .sparql-title {{ color: #14532d; }}
    .ttl-object, .ttl-subject-title, pre, .sparql-body {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
    pre, .sparql-body {{ margin: 0; padding: 12px; background: #fafafa; border: 1px solid #eee; border-radius: 8px; overflow: auto; white-space: pre-wrap; word-break: break-word; }}
    .empty {{ color: #666; }}
  </style>
</head>
<body>
  <div class="toolbar">
    <a href="{back_link}">返回</a>
    <code>{html.escape(filepath)}</code>
    <button type="button" id="structured-btn">结构化展示</button>
    <button type="button" id="raw-btn">原文展示</button>
  </div>
  <div id="structured-section" class="section active">{structured_html}</div>
  <div id="raw-section" class="section"><pre>{raw_html}</pre></div>
  <script>
    const structuredBtn = document.getElementById('structured-btn');
    const rawBtn = document.getElementById('raw-btn');
    const structuredSection = document.getElementById('structured-section');
    const rawSection = document.getElementById('raw-section');
    structuredBtn?.addEventListener('click', function() {{
      structuredSection.classList.add('active');
      rawSection.classList.remove('active');
    }});
    rawBtn?.addEventListener('click', function() {{
      rawSection.classList.add('active');
      structuredSection.classList.remove('active');
    }});
  </script>
</body>
</html>
"""


def parse_ttl_subject_groups(raw_text: str) -> tuple[list[tuple[str, list[tuple[str, str]]]], list[str]]:
    prefix_lines: list[str] = []
    groups: list[tuple[str, list[tuple[str, str]]]] = []
    current_subject = ""
    current_pairs: list[tuple[str, str]] = []
    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("@prefix") or line.upper().startswith("PREFIX "):
            prefix_lines.append(line)
            continue
        if not current_subject:
            if " " not in line:
                prefix_lines.append(line)
                continue
            subject, rest = line.split(" ", 1)
            current_subject = subject.strip()
            line = rest.strip()
        if line.endswith("."):
            line = line[:-1].strip()
            is_end = True
        else:
            is_end = False
        segments = [segment.strip() for segment in line.split(";") if segment.strip()]
        for segment in segments:
            if " " in segment:
                predicate, obj = segment.split(" ", 1)
                current_pairs.append((predicate.strip(), obj.strip()))
        if is_end:
            groups.append((current_subject, current_pairs))
            current_subject = ""
            current_pairs = []
    if current_subject and current_pairs:
        groups.append((current_subject, current_pairs))
    return groups, prefix_lines


def build_ttl_preview_html(filepath: str, target: Path) -> str:
    raw_text = target.read_text(encoding="utf-8")
    groups, prefix_lines = parse_ttl_subject_groups(raw_text)
    parts: list[str] = []
    if prefix_lines:
        parts.append(f'<div class="sparql-block"><div class="sparql-title">PREFIX</div><div class="sparql-body">{html.escape(chr(10).join(prefix_lines))}</div></div>')
    for subject, pairs in groups:
        rows = "".join(
            f'<div class="ttl-row"><div class="ttl-predicate">{html.escape(predicate)}</div><div class="ttl-object">{html.escape(obj)}</div></div>'
            for predicate, obj in pairs
        )
        parts.append(f'<div class="ttl-subject"><div class="ttl-subject-title">{html.escape(subject)}</div>{rows}</div>')
    structured_html = "".join(parts) or '<div class="empty">未解析出结构化内容</div>'
    return build_toggle_preview_page(target.name, filepath, structured_html, html.escape(raw_text))


def split_sparql_sections(raw_text: str) -> list[tuple[str, str]]:
    section_titles = ["PREFIX", "SELECT", "CONSTRUCT", "ASK", "DESCRIBE", "WHERE", "GROUP BY", "ORDER BY", "LIMIT", "OFFSET"]
    sections: list[tuple[str, str]] = []
    current_title = "QUERY"
    current_lines: list[str] = []
    for raw_line in raw_text.splitlines():
        line = raw_line.rstrip()
        upper = line.strip().upper()
        matched_title = next((title for title in section_titles if upper.startswith(title)), None)
        if matched_title and current_lines:
            sections.append((current_title, "\n".join(current_lines).strip()))
            current_title = matched_title
            current_lines = [line]
            continue
        if matched_title and not current_lines:
            current_title = matched_title
            current_lines = [line]
            continue
        current_lines.append(line)
    if current_lines:
        sections.append((current_title, "\n".join(current_lines).strip()))
    return [(title, body) for title, body in sections if body]


def build_sparql_preview_html(filepath: str, target: Path) -> str:
    raw_text = target.read_text(encoding="utf-8")
    sections = split_sparql_sections(raw_text)
    structured_html = "".join(
        f'<div class="sparql-block"><div class="sparql-title">{html.escape(title)}</div><pre class="sparql-body">{html.escape(body)}</pre></div>'
        for title, body in sections
    ) or '<div class="empty">未解析出结构化内容</div>'
    return build_toggle_preview_page(target.name, filepath, structured_html, html.escape(raw_text))


def build_jsonl_preview_html(filepath: str, target: Path) -> str:
    rows: list[str] = []
    with target.open("r", encoding="utf-8") as handle:
        for line_no, raw_line in enumerate(handle, start=1):
            line_text = raw_line.rstrip("\n")
            if not line_text.strip():
                continue
            try:
                payload = json.loads(line_text)
                summary = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                content_html = f'<div class="json-summary">{html.escape(summary[:240])}</div>{render_json_node_html(payload)}'
            except Exception:
                content_html = f'<div class="json-invalid">非法JSON</div><pre>{html.escape(line_text)}</pre>'
            rows.append(
                f"<div class=\"json-row\">"
                f"<div class=\"json-line\">{line_no}</div>"
                f"<div class=\"json-body\">{content_html}</div>"
                f"</div>"
            )
    body_html = "".join(rows) or '<div class="json-empty">文件内容为空</div>'
    return build_json_preview_page(target.name, filepath, body_html)


def build_json_preview_html(filepath: str, target: Path) -> str:
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        body_html = f'<div class="json-row"><div class="json-line">1</div><div class="json-body">{render_json_node_html(payload)}</div></div>'
    except Exception:
        raw_text = target.read_text(encoding="utf-8")
        body_html = f'<div class="json-invalid">非法JSON</div><pre>{html.escape(raw_text)}</pre>'
    return build_json_preview_page(target.name, filepath, body_html)


def markdown_asset_url(base_dir: str, target: str) -> str:
    raw = str(target or "").strip()
    if raw.startswith(("http://", "https://")):
        return raw
    try:
        if raw.startswith("/"):
            relative = normalize_relative_dir(raw)
        else:
            relative = normalize_relative_dir("/".join(part for part in [base_dir, raw] if part))
    except HTTPException:
        return "#"
    return browser_route(f"/view/{relative}")


def render_markdown_inline(text: str, base_dir: str = "") -> str:
    escaped = html.escape(text)
    escaped = re.sub(
        r"!\[([^\]]*)\]\(([^)\s]+)\)",
        lambda match: f'<img class="md-image" src="{html.escape(markdown_asset_url(base_dir, html.unescape(match.group(2))))}" alt="{match.group(1)}">',
        escaped,
    )
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", escaped)
    escaped = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" target="_blank" rel="noopener noreferrer">\1</a>', escaped)
    return escaped


def flush_markdown_list(parts: list[str], list_items: list[str]) -> None:
    if list_items:
        parts.append(f'<ul>{"".join(list_items)}</ul>')
        list_items.clear()


def render_markdown_table(lines: list[str], base_dir: str = "") -> str:
    header = [render_markdown_inline(cell.strip(), base_dir) for cell in lines[0].strip().strip("|").split("|")]
    body_rows = lines[2:]
    head_html = "".join(f"<th>{cell}</th>" for cell in header)
    row_html_parts: list[str] = []
    for row in body_rows:
        cells = [render_markdown_inline(cell.strip(), base_dir) for cell in row.strip().strip("|").split("|")]
        row_html_parts.append(f'<tr>{"".join(f"<td>{cell}</td>" for cell in cells)}</tr>')
    return f'<table class="md-table"><thead><tr>{head_html}</tr></thead><tbody>{"".join(row_html_parts)}</tbody></table>'


def is_markdown_table_divider(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell or "") for cell in cells)


def render_markdown_html(raw_text: str, base_dir: str = "") -> str:
    parts: list[str] = []
    list_items: list[str] = []
    lines = raw_text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            flush_markdown_list(parts, list_items)
            index += 1
            continue
        if stripped.startswith("```"):
            flush_markdown_list(parts, list_items)
            code_lines: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code_lines.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            parts.append(f'<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>')
            continue
        if index + 1 < len(lines) and "|" in line and is_markdown_table_divider(lines[index + 1]):
            flush_markdown_list(parts, list_items)
            table_lines = [line, lines[index + 1]]
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                table_lines.append(lines[index])
                index += 1
            parts.append(render_markdown_table(table_lines, base_dir))
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if heading:
            flush_markdown_list(parts, list_items)
            level = len(heading.group(1))
            parts.append(f"<h{level}>{render_markdown_inline(heading.group(2), base_dir)}</h{level}>")
            index += 1
            continue
        if stripped.startswith(">"):
            flush_markdown_list(parts, list_items)
            quote_text = stripped.lstrip(">").strip()
            parts.append(f"<blockquote>{render_markdown_inline(quote_text, base_dir)}</blockquote>")
            index += 1
            continue
        list_match = re.match(r"^[-*+]\s+(.+)$", stripped)
        if list_match:
            list_items.append(f"<li>{render_markdown_inline(list_match.group(1), base_dir)}</li>")
            index += 1
            continue
        flush_markdown_list(parts, list_items)
        paragraph_lines = [stripped]
        index += 1
        while index < len(lines):
            next_stripped = lines[index].strip()
            if not next_stripped or next_stripped.startswith(("```", ">")) or re.match(r"^(#{1,6})\s+", next_stripped) or re.match(r"^[-*+]\s+", next_stripped):
                break
            if index + 1 < len(lines) and "|" in lines[index] and is_markdown_table_divider(lines[index + 1]):
                break
            paragraph_lines.append(next_stripped)
            index += 1
        parts.append(f"<p>{render_markdown_inline(' '.join(paragraph_lines), base_dir)}</p>")
    flush_markdown_list(parts, list_items)
    return "".join(parts) or '<div class="empty">文件内容为空</div>'


def build_markdown_preview_html(filepath: str, target: Path) -> str:
    raw_text = target.read_text(encoding="utf-8")
    parent_dir = "/".join(normalize_relative_dir(filepath).split("/")[:-1])
    rendered_html = render_markdown_html(raw_text, parent_dir)
    raw_html = html.escape(raw_text)
    return f"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(target.name)}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 20px; color: #222; }}
    .toolbar {{ margin-bottom: 16px; display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }}
    .toolbar a {{ color: #1677ff; text-decoration: none; }}
    .toolbar button {{ padding: 6px 12px; border: 1px solid #d1d5db; border-radius: 8px; background: #fff; cursor: pointer; }}
    .section {{ display: none; }}
    .section.active {{ display: block; }}
    .markdown-body {{ max-width: 920px; line-height: 1.68; }}
    .markdown-body h1, .markdown-body h2, .markdown-body h3 {{ margin: 24px 0 12px; line-height: 1.3; }}
    .markdown-body h1 {{ padding-bottom: 8px; border-bottom: 1px solid #e5e7eb; }}
    .markdown-body p {{ margin: 12px 0; }}
    .markdown-body blockquote {{ margin: 12px 0; padding: 8px 14px; color: #555; border-left: 4px solid #d1d5db; background: #fafafa; }}
    .markdown-body code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; background: #f3f4f6; padding: 2px 5px; border-radius: 4px; }}
    .markdown-body pre {{ padding: 12px; background: #f6f8fa; border: 1px solid #e5e7eb; border-radius: 8px; overflow: auto; }}
    .markdown-body pre code {{ background: transparent; padding: 0; }}
    .markdown-body ul {{ padding-left: 24px; }}
    .markdown-body .md-image {{ display: block; max-width: 100%; height: auto; margin: 16px 0; border: 1px solid #e5e7eb; border-radius: 8px; background: #fff; }}
    .md-table {{ border-collapse: collapse; margin: 12px 0; width: 100%; }}
    .md-table th, .md-table td {{ border: 1px solid #d1d5db; padding: 8px 10px; text-align: left; }}
    .md-table th {{ background: #f7f7f7; }}
    pre.raw {{ margin: 0; padding: 12px; background: #fafafa; border: 1px solid #eee; border-radius: 8px; overflow: auto; white-space: pre-wrap; word-break: break-word; }}
    .empty {{ color: #666; }}
  </style>
</head>
<body>
  <div class="toolbar">
    <a href="{json_preview_back_link(filepath)}">返回</a>
    <code>{html.escape(filepath)}</code>
    <button type="button" id="preview-btn">格式预览</button>
    <button type="button" id="raw-btn">原文展示</button>
  </div>
  <div id="preview-section" class="section active"><article class="markdown-body">{rendered_html}</article></div>
  <div id="raw-section" class="section"><pre class="raw">{raw_html}</pre></div>
  <script>
    const previewBtn = document.getElementById('preview-btn');
    const rawBtn = document.getElementById('raw-btn');
    const previewSection = document.getElementById('preview-section');
    const rawSection = document.getElementById('raw-section');
    previewBtn?.addEventListener('click', function() {{
      previewSection.classList.add('active');
      rawSection.classList.remove('active');
    }});
    rawBtn?.addEventListener('click', function() {{
      rawSection.classList.add('active');
      previewSection.classList.remove('active');
    }});
  </script>
</body>
</html>
"""


def build_index_html(base_url: str, relative_dir: str = "", flat_mode: bool = False, message: str = "") -> str:
    resolved_base_url = PUBLIC_BASE_URL or base_url.rstrip("/")
    normalized_relative_dir = normalize_relative_dir(relative_dir)
    current_dir = resolve_display_dir(relative_dir)
    current_relative_dir = normalized_relative_dir
    rows: list[str] = []
    if current_relative_dir:
        parts = current_relative_dir.split("/")
        parent_relative_dir = "/".join(parts[:-1])
        rows.append(
            f"<tr>"
            f"<td><a href=\"{build_dir_link(parent_relative_dir, flat_mode)}\">..</a></td>"
            f"<td>返回上一级</td>"
            f"<td>-</td>"
            f"<td>-</td>"
            f"<td>-</td>"
            f"<td>-</td>"
            f"</tr>"
        )
    for item in list_current_dir_entries(current_relative_dir, flat_mode):
        relative_name = item.relative_to(current_dir if flat_mode else current_dir).as_posix()
        entry_type = describe_entry(item)
        size = item.stat().st_size if item.is_file() else "-"
        if not flat_mode and item.is_dir():
            next_dir = item.relative_to(STORAGE_DIR).as_posix()
            name_cell = f'<a href="{build_dir_link(next_dir, flat_mode)}">{html.escape(item.name)}</a>'
        else:
            name_cell = html.escape(relative_name)
        file_route_path = item.relative_to(STORAGE_DIR).as_posix()
        preview_link = f'<a href="{browser_route(f"/view/{quote(file_route_path)}")}">查看</a>' if item.is_file() and item.suffix.lower() in INLINE_PREVIEW_EXTENSIONS else "-"
        download_cell = "-"
        wget_cell = "-"
        if item.is_file():
            download_url = f"{resolved_base_url}{browser_route(f'/download/{quote(file_route_path)}')}"
            wget_cmd = f"wget -O {Path(file_route_path).name} '{download_url}'"
            wget_cmd_attr = html.escape(wget_cmd, quote=True)
            download_cell = f'<a href="{browser_route(f"/download/{quote(file_route_path)}")}">下载</a>'
            wget_cell = f'<code>{html.escape(wget_cmd)}</code> <button type="button" class="copy-btn" data-copy="{wget_cmd_attr}" title="复制wget命令">📋</button>'
        rows.append(
            f"<tr>"
            f"<td>{name_cell}</td>"
            f"<td>{entry_type}</td>"
            f"<td>{size}</td>"
            f"<td>{download_cell}</td>"
            f"<td>{preview_link}</td>"
            f"<td>{wget_cell}</td>"
            f"</tr>"
        )
    table_rows = "".join(rows) or '<tr><td colspan="6">当前目录没有内容</td></tr>'
    notice = f'<div class="notice">{html.escape(message)}</div>' if message else ""
    current_display = current_relative_dir or "."
    toggle_link = build_dir_link(current_relative_dir, not flat_mode)
    toggle_label = "退出平铺模式" if flat_mode else "平铺模式"
    return f"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>文件页面</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #222; }}
    h1 {{ margin-bottom: 8px; }}
    .hint {{ color: #666; margin-bottom: 20px; }}
    .notice {{ margin: 12px 0 20px; padding: 10px 12px; background: #eef6ff; border: 1px solid #c6dcff; border-radius: 8px; }}
    form {{ margin-bottom: 24px; padding: 16px; border: 1px solid #ddd; border-radius: 10px; max-width: 720px; }}
    input[type=file] {{ margin-right: 12px; }}
    button {{ padding: 8px 14px; border: 0; border-radius: 8px; background: #1677ff; color: #fff; cursor: pointer; }}
    .toolbar {{ margin-bottom: 16px; display: flex; gap: 12px; align-items: center; }}
    .toolbar a {{ text-decoration: none; color: #1677ff; }}
    .copy-btn {{ padding: 4px 8px; margin-left: 8px; background: #f3f4f6; color: #222; border: 1px solid #d1d5db; }}
    .copy-btn:hover {{ background: #e5e7eb; }}
    table {{ border-collapse: collapse; width: 100%; max-width: 960px; }}
    th, td {{ border: 1px solid #ddd; padding: 10px; text-align: left; }}
    th {{ background: #f7f7f7; }}
    code {{ background: #f5f5f5; padding: 2px 6px; border-radius: 4px; }}
  </style>
</head>
<body>
  <h1>文件页面</h1>
  <div class="hint">文件会上传到目录：<code>{html.escape(str(STORAGE_DIR))}</code></div>
  <div class="toolbar">
    <span>当前目录：<code>{html.escape(current_display)}</code></span>
    <a href="{toggle_link}">{toggle_label}</a>
  </div>
  {notice}
  <form action="{browser_route('/submit')}" method="post" enctype="multipart/form-data">
    <input type="hidden" name="path" value="{html.escape(current_relative_dir, quote=True)}">
    <input type="file" name="file" required>
    <button type="submit">提交</button>
  </form>
  <table>
    <thead>
      <tr>
        <th>文件名</th>
        <th>类型</th>
        <th>大小(bytes)</th>
        <th>下载</th>
        <th>预览</th>
        <th>wget命令</th>
      </tr>
    </thead>
    <tbody>{table_rows}</tbody>
  </table>
  <script>
    async function copyText(text) {{
      if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {{
        await navigator.clipboard.writeText(text);
        return;
      }}
      const textarea = document.createElement('textarea');
      textarea.value = text;
      textarea.setAttribute('readonly', 'readonly');
      textarea.style.position = 'fixed';
      textarea.style.left = '-9999px';
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand('copy');
      document.body.removeChild(textarea);
    }}

    document.querySelectorAll('.copy-btn').forEach(function(button) {{
      button.addEventListener('click', async function() {{
        const text = button.getAttribute('data-copy') || '';
        await copyText(text);
        const original = button.textContent;
        button.textContent = '✓';
        setTimeout(function() {{ button.textContent = original; }}, 1200);
      }});
    }});
  </script>
</body>
</html>
"""


def resolve_target_file(name: str) -> Path:
    raw_name = str(name or "").strip().replace("\\", "/").lstrip("/")
    parts = [part for part in raw_name.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        raise HTTPException(status_code=400, detail="文件名不能为空")
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    target = STORAGE_DIR.joinpath(*parts)
    try:
        target.relative_to(STORAGE_DIR)
    except ValueError:
        raise HTTPException(status_code=400, detail="非法文件名")
    return target


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, message: str = "") -> HTMLResponse:
    relative_dir = normalize_relative_dir(request.query_params.get("path", ""))
    flat_mode = str(request.query_params.get("flat", "")).strip() == "1"
    return HTMLResponse(build_index_html(str(request.base_url), relative_dir, flat_mode, message))


@router.get("/upload", response_class=HTMLResponse)
async def upload_index(request: Request, message: str = "") -> HTMLResponse:
    relative_dir = normalize_relative_dir(request.query_params.get("path", ""))
    flat_mode = str(request.query_params.get("flat", "")).strip() == "1"
    return HTMLResponse(build_index_html(str(request.base_url), relative_dir, flat_mode, message))


@router.post("/submit")
async def submit_file(request: Request, file: UploadFile = File(...)) -> RedirectResponse:
    form = await request.form()
    relative_dir = normalize_relative_dir(str(form.get("path") or ""))
    target_dir = resolve_display_dir(relative_dir)
    target = target_dir / Path(file.filename or "").name
    content = await file.read()
    target.write_bytes(content)
    return RedirectResponse(url=f"{build_dir_link(relative_dir, False)}&message=上传成功：{quote(target.name)}" if "?" in build_dir_link(relative_dir, False) else f"{build_dir_link(relative_dir, False)}?message=上传成功：{quote(target.name)}", status_code=303)


@router.get("/download/{filepath:path}")
async def download(filepath: str) -> FileResponse:
    target = resolve_target_file(filepath)
    if not target.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(target, filename=target.name, media_type=media_type)


@router.get("/view/{filepath:path}")
async def view_file(filepath: str):
    target = resolve_target_file(filepath)
    if not target.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    if target.suffix.lower() == ".pdf":
        return FileResponse(target, filename=target.name, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{target.name}"'})
    if target.suffix.lower() in IMAGE_EXTENSIONS:
        media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return FileResponse(target, filename=target.name, media_type=media_type, headers={"Content-Disposition": f'inline; filename="{target.name}"'})
    if target.suffix.lower() == ".ttl":
        return HTMLResponse(build_ttl_preview_html(filepath, target))
    if target.suffix.lower() == ".sparql":
        return HTMLResponse(build_sparql_preview_html(filepath, target))
    if target.suffix.lower() == ".json":
        return HTMLResponse(build_json_preview_html(filepath, target))
    if target.suffix.lower() == ".jsonl":
        return HTMLResponse(build_jsonl_preview_html(filepath, target))
    if target.suffix.lower() == ".md":
        return HTMLResponse(build_markdown_preview_html(filepath, target))
    if target.suffix.lower() not in TEXT_EXTENSIONS:
        raise HTTPException(status_code=400, detail="该文件类型不支持预览")
    return PlainTextResponse(target.read_text(encoding="utf-8"))


app.include_router(router)
if not BASE_PATH:
    app.include_router(router, prefix="/ai")


def read_pid() -> int | None:
    try:
        return int(PID_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def read_root_dir() -> Path:
    try:
        raw_root = ROOT_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        raw_root = ""
    if raw_root:
        return Path(raw_root).expanduser().resolve()
    return STORAGE_DIR


def resolve_root_arg(value: str = "") -> Path:
    raw_root = str(value or "").strip()
    if not raw_root:
        env_root = str(os.getenv("UPLOAD_PAGE_STORAGE_DIR") or "").strip()
        return Path(env_root).expanduser().resolve() if env_root else read_root_dir()
    root_dir = Path(raw_root).expanduser().resolve()
    if not root_dir.exists():
        raise SystemExit(f"目录不存在：{root_dir}")
    if not root_dir.is_dir():
        raise SystemExit(f"目标不是目录：{root_dir}")
    return root_dir


def set_storage_dir(root_dir: Path) -> None:
    global STORAGE_DIR
    STORAGE_DIR = root_dir.expanduser().resolve()


def is_process_running(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def start_background(root_arg: str = "") -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    root_dir = resolve_root_arg(root_arg)
    pid = read_pid()
    if is_process_running(pid):
        print(f"upload_page 已运行，pid={pid}，root={read_root_dir()}")
        return
    if PID_FILE.exists():
        PID_FILE.unlink()
    env = os.environ.copy()
    env["UPLOAD_PAGE_STORAGE_DIR"] = str(root_dir)
    with LOG_FILE.open("ab") as log:
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "serve"],
            cwd=str(APP_DIR.parent.parent),
            stdout=log,
            stderr=log,
            stdin=subprocess.DEVNULL,
            env=env,
            start_new_session=True,
        )
    PID_FILE.write_text(str(process.pid), encoding="utf-8")
    ROOT_FILE.write_text(str(root_dir), encoding="utf-8")
    time.sleep(0.5)
    if not is_process_running(process.pid):
        raise SystemExit(f"upload_page 启动失败，请查看日志：{LOG_FILE}")
    print(f"upload_page 已后台启动，pid={process.pid}，root={root_dir}，url=http://localhost:{PORT}/ai/upload")


def stop_background() -> None:
    pid = read_pid()
    if not is_process_running(pid):
        if PID_FILE.exists():
            PID_FILE.unlink()
        print("upload_page 未运行")
        return
    os.kill(pid, signal.SIGTERM)
    for _ in range(20):
        if not is_process_running(pid):
            break
        time.sleep(0.2)
    if is_process_running(pid):
        raise SystemExit(f"upload_page 停止超时，pid={pid}")
    if PID_FILE.exists():
        PID_FILE.unlink()
    print(f"upload_page 已停止，pid={pid}")


def print_status() -> None:
    pid = read_pid()
    if is_process_running(pid):
        print(f"upload_page 运行中，pid={pid}，root={read_root_dir()}，url=http://localhost:{PORT}/ai/upload")
        return
    print(f"upload_page 未运行，root={read_root_dir()}")


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "serve"
    root_arg = sys.argv[2] if len(sys.argv) > 2 else ""
    if command == "serve":
        if root_arg:
            set_storage_dir(resolve_root_arg(root_arg))
        uvicorn.run(app, host=HOST, port=PORT, log_level="info")
        return
    if command == "start":
        start_background(root_arg)
        return
    if command == "stop":
        stop_background()
        return
    if command == "restart":
        stop_background()
        start_background(root_arg)
        return
    if command == "status":
        print_status()
        return
    raise SystemExit("用法：python3 server/apps/upload_page.py [serve|start|stop|restart|status] [目录]")


if __name__ == "__main__":
    main()
