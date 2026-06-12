from __future__ import annotations

import html
import json
import os
import re
import subprocess
from datetime import datetime, timedelta
from typing import Any

from apps.complaint_app.core.versioning import load_manifest, stage_dir
from utils.logger import get_current_log_dir, get_current_log_file, resolve_log_path
from utils.storage_paths import ComplaintScopeContext, complaint_scope_read_chain, validate_base_id
from utils.wordfit_trace_storage import build_trace_base_id, normalize_trace_file_ids

_TIMING_KV_PATTERN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=([^ ]+)")
_TRACE_LOG_KEYS = {
    "request_id",
    "base_id",
    "user_id",
    "scope",
    "scope_path",
    "org_id",
    "entity_id",
    "reporter_name",
    "file_id",
    "issue_analysis_version",
    "issue_version",
    "match_strategy",
    "complaint_index",
    "complaint_id",
    "complaint_hash",
    "session_id",
    "attempt",
    "round",
    "tool_round",
    "name",
    "call_id",
    "elapsed",
    "duration_ms",
    "tool_call_count",
    "index",
    "messages_count",
    "input_chars",
    "output_chars",
    "call_count",
    "max_parallel",
}


def _read_json(path: str) -> dict[str, Any]:
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            payload = json.load(file_obj)
        return payload if isinstance(payload, dict) else {}
    except Exception as exc:
        return {"_read_error": str(exc), "_path": path}


def _read_text(path: str, limit: int = 20000) -> str:
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            return file_obj.read(limit)
    except Exception as exc:
        return f"读取失败: {exc}"


def _read_jsonl(path: str) -> list[dict[str, Any]]:
    if not os.path.isfile(path):
        return []
    rows: list[dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            for line in file_obj:
                text = line.strip()
                if not text:
                    continue
                payload = json.loads(text)
                if isinstance(payload, dict):
                    rows.append(payload)
    except Exception as exc:
        rows.append({"_read_error": str(exc), "_path": path})
    return rows


def _parse_datetime_text(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(text, fmt)
            if fmt == "%Y-%m-%dT%H:%M:%SZ":
                return parsed + timedelta(hours=8)
            return parsed
        except Exception:
            continue
    return None


def _filter_ocr_metrics_for_trace_window(rows: list[dict[str, Any]], record: dict[str, Any]) -> list[dict[str, Any]]:
    started_at = _parse_datetime_text(record.get("started_at"))
    ended_at = _parse_datetime_text(record.get("ended_at"))
    if not rows or not started_at or not ended_at:
        return rows
    window_start = started_at - timedelta(seconds=5)
    window_end = ended_at + timedelta(seconds=5)
    filtered: list[dict[str, Any]] = []
    include_next_without_time = False
    for row in rows:
        row_time = _parse_datetime_text(row.get("started_at") or row.get("finished_at"))
        if row_time is None:
            if include_next_without_time:
                filtered.append(row)
            continue
        include_next_without_time = window_start <= row_time <= window_end
        if include_next_without_time:
            filtered.append(row)
    return filtered or rows


def _load_ocr_metrics(record: dict[str, Any]) -> list[dict[str, Any]]:
    batch = str(record.get("ocr_metrics_batch_id") or "").strip()
    conversation_id = str(record.get("conversation_id") or "").strip()
    metrics_dir = str(os.getenv("WORDFIT_OCR_METRICS_DIR") or os.path.join("uploads", "ocr_metrics")).strip()
    if not batch or not conversation_id or not metrics_dir:
        return []
    rows = _read_jsonl(os.path.join(resolve_log_path(metrics_dir), batch, f"{conversation_id}.jsonl"))
    return _filter_ocr_metrics_for_trace_window(rows, record)


def _build_log_search_command(term: str) -> str:
    return f"rg '{term}' {_default_log_dir()}" if str(term or "").strip() else ""


def _default_log_dir() -> str:
    return resolve_log_path(get_current_log_dir())


def _current_log_file_path() -> str:
    return os.path.join(_default_log_dir(), get_current_log_file())


def _parse_timing_log_line(line: str) -> dict[str, Any]:
    values = {key: value.strip('"') for key, value in _TIMING_KV_PATTERN.findall(line)}
    extra: dict[str, Any] = {}
    extra_match = re.search(r"\bextra=(\{.*\})", line)
    if extra_match:
        try:
            loaded = json.loads(extra_match.group(1))
            extra = loaded if isinstance(loaded, dict) else {}
        except Exception:
            extra = {}
    return {
        "raw": line,
        "app": values.get("app", ""),
        "kind": values.get("kind", ""),
        "route": values.get("route", ""),
        "phase": values.get("phase", ""),
        "duration_ms": values.get("duration_ms", ""),
        "success": values.get("success", ""),
        "base_id": str(extra.get("base_id") or values.get("base_id") or ""),
        "file_id": str(extra.get("file_id") or values.get("file_id") or ""),
        "issue_analysis_version": str(extra.get("issue_analysis_version") or extra.get("issue_version") or values.get("issue_analysis_version") or values.get("issue_version") or ""),
        "user_id": str(extra.get("user_id") or values.get("user_id") or ""),
        "scope": str(extra.get("scope") or values.get("scope") or ""),
        "scope_path": str(extra.get("scope_path") or values.get("scope_path") or ""),
        "org_id": str(extra.get("org_id") or values.get("org_id") or ""),
        "entity_id": str(extra.get("entity_id") or values.get("entity_id") or ""),
        "conversation_id": str(extra.get("conversation_id") or values.get("conversation_id") or ""),
        "extra": extra,
    }


def _parse_trace_log_fields(line: str) -> dict[str, str]:
    """解析普通日志中的 key=value 追踪字段。"""
    values = {key: value.strip('"') for key, value in _TIMING_KV_PATTERN.findall(line)}
    return {key: str(values.get(key) or "") for key in _TRACE_LOG_KEYS if values.get(key) is not None}


def _parse_log_line(line: str) -> dict[str, Any]:
    item = _parse_timing_log_line(line) if "[timing]" in line else {"raw": line}
    for key, value in _parse_trace_log_fields(line).items():
        item.setdefault(key, value)
    item["is_timing"] = "[timing]" in line
    return item


def _parse_api_access_payload(line: str, marker: str) -> dict[str, Any]:
    idx = line.find(marker)
    if idx < 0:
        return {}
    text = line[idx + len(marker):].strip()
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _read_log_file_lines(path: str) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as file_obj:
            return file_obj.read().splitlines()
    except Exception:
        return []


def _build_request_intervals(lines: list[str]) -> list[dict[str, Any]]:
    requests: dict[str, dict[str, Any]] = {}
    intervals: list[dict[str, Any]] = []
    for idx, line in enumerate(lines, start=1):
        if "[api_access] request=" in line:
            payload = _parse_api_access_payload(line, "[api_access] request=")
            request_id = str(payload.get("request_id") or "").strip()
            if request_id:
                requests[request_id] = {"request_id": request_id, "start": idx, "end": idx, "request": payload, "response": {}}
            continue
        if "[api_access] response=" in line:
            payload = _parse_api_access_payload(line, "[api_access] response=")
            request_id = str(payload.get("request_id") or "").strip()
            if request_id and request_id in requests:
                item = requests[request_id]
                item["end"] = idx
                item["response"] = payload
                intervals.append(item)
            continue
        if "[api_access] error=" in line:
            payload = _parse_api_access_payload(line, "[api_access] error=")
            request_id = str(payload.get("request_id") or "").strip()
            if request_id and request_id in requests:
                item = requests[request_id]
                item["end"] = idx
                item["response"] = payload
                intervals.append(item)
    for item in requests.values():
        if item not in intervals:
            intervals.append(item)
    return sorted(intervals, key=lambda item: int(item.get("start") or 0))


def _find_interval(intervals: list[dict[str, Any]], line_no: int) -> dict[str, Any] | None:
    for item in intervals:
        start = int(item.get("start") or 0)
        end = int(item.get("end") or start)
        if start <= line_no <= end:
            return item
    return None


def _request_group_key(path: str, wordfit_index: int) -> str:
    if path == "/api/word-fit/extract":
        return "request1_wordfit_extract" if wordfit_index <= 1 else "request2_wordfit_extract"
    if path in {"/api/complaint/complaint-analysis", "/api/complaint/complaint-analysis/list"}:
        return "request3_complaint_analysis"
    if path.startswith("/api/complaint/reply-draft"):
        return "request4_reply_draft"
    return f"other:{path or 'unknown'}"


def _request_label(request_key: str, meta: dict[str, Any] | None = None) -> str:
    labels = {
        "request1_wordfit_extract": "extract接口：/api/word-fit/extract",
        "request2_wordfit_extract": "extract接口：/api/word-fit/extract",
        "request3_complaint_analysis": "举报事项分析接口",
        "request4_reply_draft": "答复建议书接口",
    }
    if request_key.startswith("other:"):
        return "未归属到接口请求区间的日志"
    label = labels.get(request_key, request_key)
    request = meta.get("request") if isinstance(meta, dict) else {}
    response = meta.get("response") if isinstance(meta, dict) else {}
    path = str((request or {}).get("path") or "")
    elapsed = str((response or {}).get("elapsed_ms") or "")
    status = str((response or {}).get("status_code") or "")
    suffix = " ".join(part for part in [path, f"status={status}" if status else "", f"elapsed={elapsed}ms" if elapsed else ""] if part)
    return f"{label} {suffix}".strip()


def _expand_logs_by_request(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_file: dict[str, list[int]] = {}
    for item in matches:
        file_path = str(item.get("file") or "")
        try:
            line_no = int(item.get("line") or 0)
        except Exception:
            line_no = 0
        if file_path and line_no > 0:
            by_file.setdefault(file_path, []).append(line_no)

    expanded: list[dict[str, Any]] = []
    seen_lines: set[tuple[str, int]] = set()
    ignored_lines: set[tuple[str, int]] = set()
    for file_path, line_numbers in by_file.items():
        lines = _read_log_file_lines(file_path)
        if not lines:
            continue
        intervals = _build_request_intervals(lines)
        selected_intervals: list[dict[str, Any]] = []
        seen_requests: set[str] = set()
        for line_no in line_numbers:
            interval = _find_interval(intervals, line_no)
            if not interval:
                continue
            request_id = str(interval.get("request_id") or "")
            if request_id and request_id not in seen_requests:
                seen_requests.add(request_id)
                selected_intervals.append(interval)

        wordfit_index = 0
        for interval in sorted(selected_intervals, key=lambda item: int(item.get("start") or 0)):
            request = interval.get("request") if isinstance(interval.get("request"), dict) else {}
            path = str(request.get("path") or "")
            if path == "/api/complaint/trace":
                start = int(interval.get("start") or 0)
                end = int(interval.get("end") or start)
                for line_no in range(start, min(end, len(lines)) + 1):
                    ignored_lines.add((file_path, line_no))
                continue
            if path == "/api/word-fit/extract":
                wordfit_index += 1
            group_key = _request_group_key(path, wordfit_index)
            start = int(interval.get("start") or 0)
            end = int(interval.get("end") or start)
            for line_no in range(start, min(end, len(lines)) + 1):
                key = (file_path, line_no)
                if key in seen_lines:
                    continue
                seen_lines.add(key)
                item = _parse_log_line(lines[line_no - 1])
                item["file"] = file_path
                item["line"] = str(line_no)
                item["request_group"] = group_key
                item["request_meta"] = {"request": interval.get("request") or {}, "response": interval.get("response") or {}}
                expanded.append(item)

        for line_no in line_numbers:
            key = (file_path, line_no)
            if key in seen_lines or key in ignored_lines or line_no < 1 or line_no > len(lines):
                continue
            item = _parse_log_line(lines[line_no - 1])
            item["file"] = file_path
            item["line"] = str(line_no)
            item["request_group"] = "other:outside_api_access"
            item["request_meta"] = {}
            expanded.append(item)
    return sorted(expanded, key=lambda item: (str(item.get("file") or ""), int(item.get("line") or 0)))


def _row_issue_analysis_version(row: dict[str, Any]) -> str:
    """读取日志行中的举报事项分析版本。"""
    extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
    return str(row.get("issue_analysis_version") or row.get("issue_version") or extra.get("issue_analysis_version") or extra.get("issue_version") or "").strip()


def _filter_logs_by_issue_analysis_version(rows: list[dict[str, Any]], issue_analysis_version: int | None) -> list[dict[str, Any]]:
    if not issue_analysis_version or issue_analysis_version <= 0:
        return rows
    expected = str(int(issue_analysis_version))
    return [row for row in rows if _row_issue_analysis_version(row) == expected]


def _filter_logs_by_user_id(rows: list[dict[str, Any]], user_id: str) -> list[dict[str, Any]]:
    expected = str(user_id or "").strip()
    if not expected:
        return rows
    result: list[dict[str, Any]] = []
    for row in rows:
        row_user = str(row.get("user_id") or "").strip()
        if not row_user or row_user == expected:
            result.append(row)
    return result


def _filter_logs_by_scope(rows: list[dict[str, Any]], scope_context: ComplaintScopeContext | None = None) -> list[dict[str, Any]]:
    if scope_context is None:
        return rows
    result: list[dict[str, Any]] = []
    for row in rows:
        row_scope_path = str(row.get("scope_path") or "").strip()
        row_scope = str(row.get("scope") or "").strip()
        row_org = str(row.get("org_id") or "").strip()
        row_entity = str(row.get("entity_id") or "").strip()
        row_reporter = str(row.get("reporter_name") or "").strip()
        if not row_scope_path and not row_scope and not row_org and not row_entity and not row_reporter:
            result.append(row)
            continue
        if row_scope_path and row_scope_path != scope_context.scope_path:
            continue
        if row_scope and row_scope != scope_context.scope:
            continue
        if row_org and row_org != scope_context.org_id:
            continue
        if row_entity and row_entity != scope_context.entity_id:
            continue
        if row_reporter and row_reporter != scope_context.reporter_name:
            continue
        result.append(row)
    return result


def _latest_issue_analysis_version_from_logs(rows: list[dict[str, Any]]) -> int:
    """从日志中解析最新举报事项分析版本。"""
    versions: list[int] = []
    for row in rows:
        raw_version = _row_issue_analysis_version(row)
        if not raw_version.isdigit():
            continue
        versions.append(int(raw_version))
    return max(versions) if versions else 0


def _search_trace_logs(base_id: str, extra_terms: list[str] | None = None) -> list[dict[str, Any]]:
    log_dir = _default_log_dir()
    if not base_id or not os.path.isdir(log_dir):
        return []
    rg_bin = str(os.getenv("TRACE_RG_BIN") or "rg").strip() or "rg"
    terms = [base_id]
    pattern = "|".join(re.escape(term) for term in terms)
    log_target = _current_log_file_path()
    if not os.path.isfile(log_target):
        log_target = log_dir
    cmd = [rg_bin, "--no-heading", "--line-number", "--with-filename", pattern, log_target]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=10, check=False)
    except Exception as exc:
        return [{"raw": f"rg 日志检索失败: {exc}", "error": str(exc)}]
    rows: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        file_path = ""
        line_no = ""
        raw_line = line
        parts = line.split(":", 2)
        if len(parts) == 3:
            file_path, line_no, raw_line = parts
        item = _parse_log_line(raw_line)
        item["file"] = file_path
        item["line"] = line_no
        rows.append(item)
    return rows


def _build_wordfit_records_from_logs(rows: list[dict[str, Any]], base_id: str) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("app") != "word_fit_app" or row.get("route") != "POST":
            continue
        raw = str(row.get("raw") or "")
        if "/api/word-fit/extract" not in raw:
            continue
        if str(row.get("base_id") or "") != str(base_id or ""):
            continue
        group = str(row.get("request_group") or "request1_wordfit_extract")
        record = grouped.setdefault(
            group,
            {
                "base_id": base_id,
                "conversation_id": str(row.get("conversation_id") or ""),
                "file_ids": [],
                "node_timings": [],
                "result": {},
                "success": False,
                "duration_ms": 0,
                "error": "",
            },
        )
        extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
        if row.get("conversation_id"):
            record["conversation_id"] = str(row.get("conversation_id") or "")
        if row.get("file_id"):
            record["file_ids"] = [item for item in str(row.get("file_id") or "").split(",") if item]
        for key in ("scope", "scope_path", "org_id", "entity_id", "reporter_name"):
            value = str(row.get(key) or extra.get(key) or "").strip()
            if value:
                record[key] = value
        if extra.get("extract_model") is not None:
            record["extract_model"] = str(extra.get("extract_model") or "")
        if extra.get("ocr_model") is not None:
            record["ocr_model"] = str(extra.get("ocr_model") or "")
        if row.get("kind") == "phase":
            record["node_timings"].append(
                {
                    "node": str(row.get("phase") or ""),
                    "duration_ms": row.get("duration_ms") or "",
                    "success": str(row.get("success") or "").lower() == "true",
                }
            )
        elif row.get("kind") == "route":
            record["duration_ms"] = row.get("duration_ms") or 0
            record["success"] = str(row.get("success") or "").lower() == "true"
    return [item for item in grouped.values() if item.get("node_timings")]


def _extract_source_request_ids(text_versions: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for item in text_versions:
        meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
        source_request_id = str(meta.get("source_request_id") or "").strip()
        if source_request_id and source_request_id not in seen:
            seen.add(source_request_id)
            ids.append(source_request_id)
    return ids


def _version_dirs(user_id: str, base_id: str, stage: str, scope_context: ComplaintScopeContext | None = None) -> list[tuple[int, str]]:
    root = stage_dir(user_id, base_id, stage, scope_context=scope_context)
    if not os.path.isdir(root):
        return []
    versions: list[tuple[int, str]] = []
    for name in os.listdir(root):
        if not name.startswith("v"):
            continue
        try:
            version = int(name[1:])
        except Exception:
            continue
        path = os.path.join(root, name)
        if os.path.isdir(path):
            versions.append((version, path))
    return sorted(versions)


def _load_stage_versions(user_id: str, base_id: str, stage: str, scope_context: ComplaintScopeContext | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for version, path in _version_dirs(user_id, base_id, stage, scope_context=scope_context):
        meta = _read_json(os.path.join(path, "meta.json"))
        if stage == "text":
            payload: Any = {"text_preview": _read_text(os.path.join(path, "final.txt")), "mapping": _read_json(os.path.join(path, "final_mapping.json"))}
        else:
            payload = _read_json(os.path.join(path, "final.json"))
        rows.append({"stage": stage, "version": version, "path": path, "meta": meta, "payload": payload})
    return rows


def _status_text(success: Any) -> str:
    if success is True:
        return "成功"
    if success is False:
        return "失败"
    return "未知"


def _json_block(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    return f"<pre>{html.escape(text)}</pre>"


def _metric_items_by_type(records: list[dict[str, Any]], metric_type: str) -> list[dict[str, Any]]:
    return [item for item in records if str(item.get("type") or "") == metric_type]


def _metric_by_page(records: list[dict[str, Any]], metric_type: str) -> dict[int, dict[str, Any]]:
    rows: dict[int, dict[str, Any]] = {}
    for item in records:
        if str(item.get("type") or "") != metric_type:
            continue
        try:
            page = int(item.get("page") or 0)
        except Exception:
            page = 0
        if page > 0:
            rows[page] = item
    return rows


def _info_grid(items: list[tuple[str, Any]]) -> str:
    chunks = ["<dl class='kv-list'>"]
    for label, value in items:
        chunks.append(f"<div class='kv-row'><dt>{html.escape(label)}</dt><dd>{html.escape(str(value if value is not None else ''))}</dd></div>")
    chunks.append("</dl>")
    return "".join(chunks)


def _info_grid_html(items: list[tuple[str, str]]) -> str:
    chunks = ["<dl class='kv-list'>"]
    for label, value in items:
        chunks.append(f"<div class='kv-row'><dt>{html.escape(label)}</dt><dd>{value}</dd></div>")
    chunks.append("</dl>")
    return "".join(chunks)


def _detail_list_html(items: list[tuple[str, str]]) -> str:
    chunks = ["<div class='detail-list'>"]
    for label, value in items:
        chunks.append(f"<div><b>{html.escape(label)}</b>：{value}</div>")
    chunks.append("</div>")
    return "".join(chunks)


def _ocr_page_detail_rows(records: list[dict[str, Any]]) -> list[tuple[str, str]]:
    if not records:
        return [("逐页明细", "<p>未找到 OCR/VL 明细。</p>")]
    page_metrics = _metric_by_page(records, "ocr_page")
    llm_metrics = _metric_by_page(records, "ocr_llm")
    pages = sorted(set(page_metrics) | set(llm_metrics))
    if not pages:
        return [("逐页明细", "<p>未找到逐页 OCR/VL 明细。</p>")]
    rows: list[tuple[str, str]] = []
    for page in pages:
        page_item = page_metrics.get(page) or {}
        llm_item = llm_metrics.get(page) or {}
        saved_path = str(page_item.get("saved_path") or "").strip()
        text_preview = _read_text(saved_path, limit=4000) if saved_path else ""
        page_summary = (
            f"总耗时={html.escape(str(page_item.get('duration_ms') or ''))}ms，"
            f"VL耗时={html.escape(str(llm_item.get('llm_duration_ms') or page_item.get('ocr_ms') or ''))}ms，"
            f"文本长度={html.escape(str(page_item.get('text_len') or llm_item.get('output_chars') or ''))}"
        )
        page_detail = (
            f"<details><summary>{page_summary}</summary>"
            + _detail_list_html([
                ("开始时间", html.escape(str(page_item.get("started_at") or ""))),
                ("结束时间", html.escape(str(page_item.get("finished_at") or ""))),
                ("模型", html.escape(str(llm_item.get("model") or ""))),
                ("渲染耗时ms", html.escape(str(page_item.get("render_ms") or ""))),
                ("VL耗时ms", html.escape(str(llm_item.get("llm_duration_ms") or page_item.get("ocr_ms") or ""))),
                ("总耗时ms", html.escape(str(page_item.get("duration_ms") or ""))),
                ("输入字符", html.escape(str(llm_item.get("prompt_chars") or ""))),
                ("图片base64字符", html.escape(str(llm_item.get("image_b64_chars") or ""))),
                ("输出字符", html.escape(str(llm_item.get("output_chars") or ""))),
                ("首token ms", html.escape(str(llm_item.get("first_token_ms") or ""))),
                ("识别文本文件", html.escape(os.path.basename(saved_path) if saved_path else "")),
                ("识别文本内容", "<details><summary>展开</summary>" + (f"<pre>{html.escape(text_preview)}</pre>" if text_preview else "<p>未找到识别文本。</p>") + "</details>"),
                ("原始 metrics JSON", "<details><summary>展开</summary>" + _json_block({"ocr_page": page_item, "ocr_llm": llm_item}) + "</details>"),
            ])
            + "</details>"
        )
        rows.append((f"第{page}页", page_detail))
    return rows


def _render_extract_llm_details(records: list[dict[str, Any]]) -> str:
    rows = _metric_items_by_type(records, "extract_llm")
    if not rows:
        return "<p>未找到字段提取 LLM 明细。</p>"
    chunks: list[str] = []
    for item in rows:
        chunks.append(
            "<div class='metric-panel'>"
            + _info_grid([
                ("模型", item.get("model") or ""),
                ("输入字符", item.get("prompt_chars") or ""),
                ("输出字符", item.get("output_chars") or ""),
                ("首token ms", item.get("first_token_ms") or ""),
                ("LLM耗时ms", item.get("llm_duration_ms") or ""),
            ])
            + "<details><summary>原始 metrics JSON</summary>"
            + _json_block(item)
            + "</details>"
            + "</div>"
        )
    return "\n".join(chunks)


def _render_node_execution(record: dict[str, Any]) -> str:
    node_timings = record.get("node_timings") if isinstance(record.get("node_timings"), list) else []
    metrics = record.get("ocr_metrics") if isinstance(record.get("ocr_metrics"), list) else []
    if not node_timings:
        return "<p>未找到节点耗时。</p>"
    chunks: list[str] = []
    for index, item in enumerate(node_timings, start=1):
        if not isinstance(item, dict):
            continue
        node = str(item.get("node") or "")
        duration_ms = str(item.get("duration_ms") or "")
        success_text = _status_text(item.get("success"))
        detail = "<p>暂无更多明细。</p>"
        if node == "ocr_node":
            file_rows = _metric_items_by_type(metrics, "ocr_file")
            file_summary_rows = [
                ("PDF页数", html.escape(str(file_rows[-1].get("pages") if file_rows else ""))),
                ("并发数", html.escape(str(file_rows[-1].get("workers") if file_rows else ""))),
                ("OCR总耗时ms", html.escape(str(file_rows[-1].get("duration_ms") if file_rows else ""))),
                ("模型", html.escape(str(file_rows[-1].get("model") if file_rows else ""))),
                ("缓存", html.escape(str(file_rows[-1].get("cache_hit") if file_rows else ""))),
                ("文件类型", html.escape(str(file_rows[-1].get("mime_type") if file_rows else ""))),
            ]
            detail = (
                "<details class='metric-panel'><summary>OCR/VL 明细</summary>"
                + _info_grid_html(file_summary_rows + _ocr_page_detail_rows(metrics))
                + "</details>"
            )
        elif node == "extract_info":
            detail = "<details class='metric-panel'><summary>字段提取 LLM 明细</summary>" + _render_extract_llm_details(metrics) + "</details>"
        chunks.append(
            "<details open>"
            f"<summary>node{index}：{html.escape(node)}，耗时：{html.escape(duration_ms)}ms，状态：{html.escape(success_text)}</summary>"
            + detail
            + "</details>"
        )
    return "\n".join(chunks)


def _section(title: str, body: str) -> str:
    return f"<section><h2>{html.escape(title)}</h2>{body}</section>"


def _render_wordfit_timeline(records: list[dict[str, Any]]) -> str:
    if not records:
        return "<p>未找到 word-fit extract 接口请求记录。</p>"
    rows = []
    for index, item in enumerate(sorted(records, key=lambda row: str(row.get("started_at") or "")), start=1):
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        extract = result.get("extract") if isinstance(result.get("extract"), dict) else result
        request_label = f"请求{index}：/api/word-fit/extract"
        call_type = "触发举报分析" if str(item.get("analysis_scene") or "").strip() else "未触发举报分析"
        base_id = str(item.get("base_id") or "").strip()
        log_command = _build_log_search_command(base_id)
        log_command_block = (
            "<div class='copy-line'>"
            f"<code>{html.escape(log_command)}</code>"
            f"<button type='button' data-command='{html.escape(log_command, quote=True)}' onclick='copyTraceCommand(this)'>复制</button>"
            "</div>"
            if log_command else ""
        )
        rows.append(
            "<details open>"
            f"<summary>{html.escape(request_label)}，{html.escape(call_type)}；"
            f"开始时间：{html.escape(str(item.get('started_at') or ''))}，"
            f"结束时间：{html.escape(str(item.get('ended_at') or ''))}，"
            f"状态：{html.escape(_status_text(item.get('success')))}，"
            f"耗时：{html.escape(str(item.get('duration_ms') or 0))}ms；"
            f"可在日志中搜索 base_id：{html.escape(base_id)}</summary>"
            "<div class='grid'>"
            f"<div><b>analysis_scene</b><br>{html.escape(str(item.get('analysis_scene') or ''))}</div>"
            f"<div><b>scope</b><br>{html.escape(str(item.get('scope_path') or item.get('scope') or ''))}<br>org={html.escape(str(item.get('org_id') or ''))}<br>entity={html.escape(str(item.get('entity_id') or ''))}<br>reporter={html.escape(str(item.get('reporter_name') or ''))}</div>"
            f"<div><b>模型</b><br>extract={html.escape(str(item.get('extract_model') or ''))}<br>ocr={html.escape(str(item.get('ocr_model') or ''))}</div>"
            f"<div><b>错误</b><br>{html.escape(str(item.get('error') or ''))}</div>"
            "</div>"
            + log_command_block
            + "<h3>节点执行明细</h3>"
            + _render_node_execution(item)
            + "<h3>提取结果</h3>"
            + _json_block(extract)
            + "</details>"
        )
    return "\n".join(rows)


def _render_stage_versions(title: str, records: list[dict[str, Any]]) -> str:
    if not records:
        return f"<p>未找到 {html.escape(title)} 产物。</p>"
    chunks = []
    for item in sorted(records, key=lambda row: int(row.get("version") or 0), reverse=True):
        meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
        chunks.append(
            "<details>"
            f"<summary>v{item.get('version')} | {html.escape(str(meta.get('ts') or ''))} | {html.escape(str(meta.get('source') or ''))}</summary>"
            f"<p class='path'>{html.escape(str(item.get('path') or ''))}</p>"
            "<h3>meta</h3>"
            + _json_block(meta)
            + "<h3>result</h3>"
            + _json_block(item.get("payload"))
            + "</details>"
        )
    return "\n".join(chunks)


def _stage_by_version(records: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """按版本号索引本地产物。"""
    result: dict[int, dict[str, Any]] = {}
    for item in records:
        try:
            version = int(item.get("version") or 0)
        except Exception:
            version = 0
        if version > 0:
            result[version] = item
    return result


def _meta_int(meta: dict[str, Any], key: str) -> int:
    try:
        return int(meta.get(key) or 0)
    except Exception:
        return 0


def _payload_source_issue_version(payload: dict[str, Any]) -> int:
    source_versions = payload.get("source_versions") if isinstance(payload.get("source_versions"), dict) else {}
    try:
        return int(source_versions.get("issue_analysis_version") or source_versions.get("issue_version") or 0)
    except Exception:
        return 0


def _payload_law_rationality_version(payload: dict[str, Any]) -> int:
    """读取 issue_analysis 引用的合理性分析版本。"""
    versions = payload.get("versions") if isinstance(payload.get("versions"), dict) else {}
    try:
        return int(versions.get("law_rationality_version") or 0)
    except Exception:
        return 0


def _issue_versions_from_logs(rows: list[dict[str, Any]]) -> list[int]:
    versions: set[int] = set()
    for row in rows:
        raw_version = _row_issue_analysis_version(row)
        if raw_version.isdigit():
            versions.add(int(raw_version))
    return sorted(versions)


def _build_trace_version_rows(user_id: str, base_id: str, rows: list[dict[str, Any]], issue_analysis_version: int | None = None, scope_context: ComplaintScopeContext | None = None) -> list[dict[str, Any]]:
    """构建按举报事项分析版本展示的 trace 数据。"""
    read_chain = complaint_scope_read_chain(scope_context)
    default_scope = read_chain[-1]
    issue_scope = read_chain[0]
    for candidate_scope in read_chain:
        if _load_stage_versions(user_id, base_id, "issue_analysis", scope_context=candidate_scope):
            issue_scope = candidate_scope
            break
    text_by_version = _stage_by_version(_load_stage_versions(user_id, base_id, "text", scope_context=default_scope))
    summary_by_version = _stage_by_version(_load_stage_versions(user_id, base_id, "summary", scope_context=default_scope))
    law_rationality_by_version = _stage_by_version(_load_stage_versions(user_id, base_id, "law_rationality", scope_context=issue_scope))
    issue_by_version = _stage_by_version(_load_stage_versions(user_id, base_id, "issue_analysis", scope_context=issue_scope))
    reply_by_version = _stage_by_version(_load_stage_versions(user_id, base_id, "reply", scope_context=issue_scope))
    versions = set(issue_by_version) | set(_issue_versions_from_logs(rows))
    if issue_analysis_version and issue_analysis_version > 0:
        versions = {int(issue_analysis_version)}
    result: list[dict[str, Any]] = []
    for version in sorted(versions, reverse=True):
        issue = issue_by_version.get(version) or {}
        issue_meta = issue.get("meta") if isinstance(issue.get("meta"), dict) else {}
        issue_payload = issue.get("payload") if isinstance(issue.get("payload"), dict) else {}
        base_text_version = _meta_int(issue_meta, "base_text_version") or _meta_int(issue_meta, "base_document_version")
        base_summary_version = _meta_int(issue_meta, "base_summary_version")
        law_rationality_version = _payload_law_rationality_version(issue_payload)
        law_rationality = law_rationality_by_version.get(law_rationality_version) if law_rationality_version else {}
        if not law_rationality:
            for item in law_rationality_by_version.values():
                meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
                if _meta_int(meta, "issue_analysis_version") == version:
                    law_rationality = item
                    break
        replies = []
        for reply in reply_by_version.values():
            payload = reply.get("payload") if isinstance(reply.get("payload"), dict) else {}
            meta = reply.get("meta") if isinstance(reply.get("meta"), dict) else {}
            if _payload_source_issue_version(payload) == version or _meta_int(meta, "issue_analysis_version") == version:
                replies.append(reply)
        result.append({
            "version": version,
            "scope_context": issue_scope.to_meta(),
            "text": text_by_version.get(base_text_version) if base_text_version else {},
            "summary": summary_by_version.get(base_summary_version) if base_summary_version else {},
            "law_rationality": law_rationality,
            "issue_analysis": issue,
            "replies": sorted(replies, key=lambda item: int(item.get("version") or 0), reverse=True),
            "logs": _filter_logs_by_issue_analysis_version(rows, version),
            "extract_logs": _filter_extract_logs(rows, base_text_version),
            "summary_logs": _filter_summary_logs(rows, base_summary_version),
            "reply_logs": _filter_reply_logs(rows, version),
        })
    return result


def _extra_int(row: dict[str, Any], key: str) -> int:
    extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
    try:
        return int(extra.get(key) or row.get(key) or 0)
    except Exception:
        return 0


def _filter_extract_logs(rows: list[dict[str, Any]], text_version: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        raw = str(row.get("raw") or "")
        group = str(row.get("request_group") or "")
        phase = str(row.get("phase") or "")
        if group in {"request1_wordfit_extract", "request2_wordfit_extract"} or "/api/word-fit/extract" in raw:
            result.append(row)
            continue
        if phase in {"prepare_original", "extract_text"}:
            row_text_version = _extra_int(row, "text_version")
            if not text_version or not row_text_version or row_text_version == text_version:
                result.append(row)
    return result


def _filter_summary_logs(rows: list[dict[str, Any]], summary_version: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        raw = str(row.get("raw") or "")
        phase = str(row.get("phase") or "")
        if phase == "generate_summary" or "summary_generation" in raw or "generate_summary" in raw:
            row_summary_version = _extra_int(row, "summary_version")
            if not summary_version or not row_summary_version or row_summary_version == summary_version:
                result.append(row)
    return result


def _filter_reply_logs(rows: list[dict[str, Any]], issue_version: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        raw = str(row.get("raw") or "")
        group = str(row.get("request_group") or "")
        phase = str(row.get("phase") or "")
        if group == "request4_reply_draft" or "/api/complaint/reply-draft" in raw or phase in {"load_issue_analysis", "generate_reply", "save_reply"}:
            row_issue_version = _extra_int(row, "issue_version") or _extra_int(row, "issue_analysis_version")
            if not issue_version or not row_issue_version or row_issue_version == issue_version:
                result.append(row)
    return result


def _dedupe_logs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        key = (str(row.get("file") or ""), str(row.get("line") or ""), str(row.get("raw") or ""))
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return sorted(result, key=lambda item: (str(item.get("file") or ""), int(str(item.get("line") or "0")) if str(item.get("line") or "").isdigit() else 0))


def _render_version_artifact(title: str, item: dict[str, Any] | None) -> str:
    if not item:
        return f"<p>未找到{html.escape(title)}产物。</p>"
    meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
    return (
        "<details open>"
        f"<summary>{html.escape(title)} v{html.escape(str(item.get('version') or ''))} | {html.escape(str(meta.get('ts') or ''))}</summary>"
        f"<p class='path'>{html.escape(str(item.get('path') or ''))}</p>"
        "<h4>meta</h4>"
        + _json_block(meta)
        + "<h4>result</h4>"
        + _json_block(item.get("payload"))
        + "</details>"
    )


def _render_logs_detail(title: str, rows: list[dict[str, Any]], open_by_default: bool = False) -> str:
    open_attr = " open" if open_by_default else ""
    return f"<details{open_attr}><summary>{html.escape(title)}（{len(rows)} 条）</summary>" + _render_log_table(rows) + "</details>"


def _render_versioned_trace(payload: dict[str, Any]) -> str:
    versions = payload.get("versions") if isinstance(payload.get("versions"), list) else []
    if not versions:
        return "<p>未找到可按版本展示的数据。</p>" + _render_logs_detail("原始日志", payload.get("trace_logs") or [])
    chunks: list[str] = []
    for item in versions:
        version = int(item.get("version") or 0)
        chunks.append(f"<details open class='version-block'><summary>v{version}</summary>")
        chunks.append("<div class='stage-block'><h3>extract接口 / 原文提取</h3>" + _render_version_artifact("原文提取", item.get("text") if isinstance(item.get("text"), dict) else {}) + _render_logs_detail("extract接口日志（含VL/OCR）", item.get("extract_logs") if isinstance(item.get("extract_logs"), list) else [], open_by_default=True) + "</div>")
        chunks.append("<div class='stage-block'><h3>摘要</h3>" + _render_version_artifact("摘要", item.get("summary") if isinstance(item.get("summary"), dict) else {}) + _render_logs_detail("摘要日志", item.get("summary_logs") if isinstance(item.get("summary_logs"), list) else []) + "</div>")
        chunks.append("<div class='stage-block'><h3>合理性分析</h3>" + _render_version_artifact("合理性分析", item.get("law_rationality") if isinstance(item.get("law_rationality"), dict) else {}) + "</div>")
        issue_logs = item.get("logs") if isinstance(item.get("logs"), list) else []
        chunks.append("<div class='stage-block'><h3>举报事项分析</h3>" + _render_version_artifact("举报事项分析", item.get("issue_analysis") if isinstance(item.get("issue_analysis"), dict) else {}) + (_render_complaint_grouped_log_table(issue_logs) if issue_logs else "<p>未找到本版本举报事项分析日志。</p>") + "</div>")
        reply_chunks = [_render_version_artifact("答复建议书", reply) for reply in item.get("replies") or [] if isinstance(reply, dict)]
        reply_body = "".join(reply_chunks) if reply_chunks else "<p>未找到答复建议书产物。</p>"
        chunks.append("<div class='stage-block'><h3>答复建议书</h3>" + reply_body + _render_logs_detail("答复建议书日志", item.get("reply_logs") if isinstance(item.get("reply_logs"), list) else []) + "</div>")
        version_logs = _dedupe_logs(
            (item.get("extract_logs") if isinstance(item.get("extract_logs"), list) else [])
            + (item.get("summary_logs") if isinstance(item.get("summary_logs"), list) else [])
            + issue_logs
            + (item.get("reply_logs") if isinstance(item.get("reply_logs"), list) else [])
        )
        chunks.append(_render_logs_detail("本版本原始日志", version_logs))
        chunks.append("</details>")
    chunks.append(_render_logs_detail("全部原始日志", payload.get("trace_logs") or []))
    return "\n".join(chunks)


def _render_log_table(records: list[dict[str, Any]]) -> str:
    if not records:
        return "<p>未找到相关日志。</p>"
    rows = [
        "<table><thead><tr><th>文件</th><th>行号</th><th>日志</th></tr></thead><tbody>"
    ]
    for item in records:
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(item.get('file') or ''))}</td>"
            f"<td>{html.escape(str(item.get('line') or ''))}</td>"
            f"<td><pre>{html.escape(str(item.get('raw') or ''))}</pre></td>"
            "</tr>"
        )
    rows.append("</tbody></table>")
    return "\n".join(rows)


def _session_prompt_event_label(raw: str) -> str:
    if "llm_request_start" in raw:
        return "LLM请求开始"
    if "llm_request_done" in raw:
        return "LLM请求完成"
    if "llm_request_timeout" in raw:
        return "LLM请求超时"
    if "response metrics" in raw:
        return "LLM响应指标"
    if "parallel_tool_execution" in raw:
        return "并行工具执行"
    if "tool_call_start" in raw:
        return "工具调用开始"
    if "tool_call_done" in raw:
        return "工具调用完成"
    if "tool_result" in raw:
        return "工具结果"
    if "tool_call " in raw:
        return "工具调用参数"
    if "tool_round_limit_reached" in raw:
        return "工具轮次上限"
    if "tool_round=" in raw:
        return "工具轮次"
    return "其他"


def _extract_log_suffix(raw: str, marker: str) -> str:
    idx = raw.find(marker)
    return raw[idx + len(marker):].strip() if idx >= 0 else ""


def _extract_tool_command(raw: str) -> str:
    return _extract_log_suffix(raw, " command=")


def _extract_between_markers(raw: str, marker: str, next_markers: list[str] | None = None) -> str:
    idx = raw.find(marker)
    if idx < 0:
        return ""
    start = idx + len(marker)
    end = len(raw)
    for next_marker in next_markers or []:
        next_idx = raw.find(next_marker, start)
        if next_idx >= 0:
            end = min(end, next_idx)
    return raw[start:end].strip()


def _parse_json_fragment(value: str) -> Any:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return text


def _format_pre(value: Any, empty: str = "未记录") -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    else:
        text = str(value or "").strip()
    return f"<pre>{html.escape(text or empty)}</pre>"


def _extract_law_summary(raw: str) -> dict[str, Any]:
    text = _extract_log_suffix(raw, " law_summary=")
    if not text:
        return {}
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _round_key(item: dict[str, Any]) -> str:
    value = str(item.get("round") or item.get("tool_round") or "").strip()
    return value if value else "未标记"


def _build_round_summary(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rounds: dict[str, dict[str, Any]] = {}
    for item in records:
        raw = str(item.get("raw") or "")
        if "[session_prompt]" not in raw:
            continue
        round_key = _round_key(item)
        summary = rounds.setdefault(round_key, {"round": round_key, "events": [], "tool_calls": {}, "llm_elapsed": "", "duration_ms": "", "tool_call_count": ""})
        event_label = _session_prompt_event_label(raw)
        summary["events"].append({"label": event_label, "line": str(item.get("line") or ""), "raw": raw})
        if "llm_request_done" in raw:
            summary["llm_elapsed"] = str(item.get("elapsed") or "")
        if "response metrics" in raw:
            summary["duration_ms"] = str(item.get("duration_ms") or "")
            summary["tool_call_count"] = str(item.get("tool_call_count") or "")
        if "tool_call_start" in raw or "tool_call_done" in raw or "tool_result" in raw or "tool_call " in raw:
            call_id = str(item.get("call_id") or "").strip()
            name = str(item.get("name") or "").strip()
            key = call_id or f"{name}:{len(summary['tool_calls']) + 1}"
            tool_call = summary["tool_calls"].setdefault(key, {"name": name, "call_id": call_id, "elapsed": "", "command": "", "events": []})
            if name:
                tool_call["name"] = name
            if call_id:
                tool_call["call_id"] = call_id
            if "tool_call " in raw:
                tool_call["command"] = _extract_tool_command(raw)
            if "tool_call_done" in raw:
                tool_call["elapsed"] = str(item.get("elapsed") or "")
            tool_call["events"].append(event_label)
    return sorted(rounds.values(), key=lambda item: (int(str(item.get("round"))) if str(item.get("round")).isdigit() else 999999, str(item.get("round") or "")))


def _render_round_summary(records: list[dict[str, Any]]) -> str:
    round_items = _build_round_summary(records)
    if not round_items:
        return "<p>未找到 round 级 session_prompt 日志。</p>"
    rows = ["<table><thead><tr><th>Round</th><th>LLM耗时</th><th>响应耗时</th><th>工具调用</th></tr></thead><tbody>"]
    for item in round_items:
        tool_calls = list((item.get("tool_calls") or {}).values())
        tool_text = "<br>".join(
            html.escape(" ".join(part for part in [
                str(tool.get("name") or ""),
                f"call_id={tool.get('call_id')}" if tool.get("call_id") else "",
                f"耗时={tool.get('elapsed')}" if tool.get("elapsed") else "",
                f"命令={tool.get('command')}" if tool.get("command") else "",
            ] if part))
            for tool in tool_calls
        )
        if not tool_text:
            count = str(item.get("tool_call_count") or "").strip()
            tool_text = html.escape(f"{count} 次" if count else "-")
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(item.get('round') or ''))}</td>"
            f"<td>{html.escape(str(item.get('llm_elapsed') or ''))}</td>"
            f"<td>{html.escape(str(item.get('duration_ms') or ''))}ms</td>"
            f"<td>{tool_text}</td>"
            "</tr>"
        )
    rows.append("</tbody></table>")
    return "\n".join(rows)


def _build_dialog_rounds(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 round 组装工具调用与模型输出。"""
    rounds: dict[str, dict[str, Any]] = {}
    for item in records:
        raw = str(item.get("raw") or "")
        if "[session_prompt]" not in raw:
            continue
        round_key = _round_key(item)
        entry = rounds.setdefault(round_key, {"round": round_key, "request_messages": None, "assistant_outputs": [], "tool_calls": {}, "tool_results": {}})
        if " request_messages=" in raw:
            entry["request_messages"] = _parse_json_fragment(_extract_log_suffix(raw, " request_messages="))
            continue
        if "assistant_message " in raw:
            content = _extract_between_markers(raw, " content=", [" tool_calls="])
            tool_calls = _parse_json_fragment(_extract_log_suffix(raw, " tool_calls="))
            entry["assistant_outputs"].append({"label": "模型输出", "content": content, "tool_calls": tool_calls})
            continue
        if "final_assistant_message content=" in raw or "follow_assistant_message content=" in raw:
            content = _extract_log_suffix(raw, " content=")
            entry["assistant_outputs"].append({"label": "最终模型输出", "content": content, "tool_calls": None})
            continue
        if "tool_call " in raw:
            call_id = str(item.get("call_id") or "").strip()
            name = str(item.get("name") or "").strip()
            index = str(item.get("index") or "").strip()
            key = call_id or f"{round_key}:{index or len(entry['tool_calls']) + 1}:{name}"
            args_text = _extract_between_markers(raw, " args=", [" command="])
            command = _extract_log_suffix(raw, " command=")
            entry["tool_calls"][key] = {"call_id": call_id, "name": name, "index": index, "args": _parse_json_fragment(args_text), "command": command}
            continue
        if "tool_result " in raw:
            call_id = str(item.get("call_id") or "").strip()
            name = str(item.get("name") or "").strip()
            key = call_id or f"{round_key}:result:{len(entry['tool_results']) + 1}:{name}"
            output = _extract_log_suffix(raw, " output=")
            entry["tool_results"][key] = {"call_id": call_id, "name": name, "output": _parse_json_fragment(output)}
    return sorted(rounds.values(), key=lambda item: (int(str(item.get("round"))) if str(item.get("round")).isdigit() else 999999, str(item.get("round") or "")))


def _tool_result_for_call(tool: dict[str, Any], results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    call_id = str(tool.get("call_id") or "").strip()
    if call_id and call_id in results:
        return results[call_id]
    name = str(tool.get("name") or "").strip()
    for result in results.values():
        if name and str(result.get("name") or "").strip() == name:
            return result
    return {}


def _render_tool_dialog_rounds(records: list[dict[str, Any]]) -> str:
    rounds = _build_dialog_rounds(records)
    if not rounds:
        return "<p>未找到可展示的 session_prompt 消息链。</p>"
    chunks: list[str] = []
    for item in rounds:
        tools = list((item.get("tool_calls") or {}).values())
        results = item.get("tool_results") if isinstance(item.get("tool_results"), dict) else {}
        assistant_outputs = item.get("assistant_outputs") if isinstance(item.get("assistant_outputs"), list) else []
        left_parts: list[str] = []
        if tools:
            for tool in tools:
                result = _tool_result_for_call(tool, results)
                header = " ".join(part for part in [
                    str(tool.get("name") or "工具调用"),
                    f"call_id={tool.get('call_id')}" if tool.get("call_id") else "",
                    f"index={tool.get('index')}" if tool.get("index") else "",
                ] if part)
                command = str(tool.get("command") or "").strip()
                left_parts.append(
                    "<div class='dialog-card'>"
                    f"<h5>{html.escape(header)}</h5>"
                    + (f"<div class='dialog-label'>命令</div>{_format_pre(command)}" if command else "")
                    + f"<div class='dialog-label'>参数</div>{_format_pre(tool.get('args'))}"
                    + f"<div class='dialog-label'>结果</div>{_format_pre(result.get('output') if result else '')}"
                    + "</div>"
                )
        else:
            left_parts.append("<p>本轮未调用工具。</p>")
        right_parts: list[str] = []
        if assistant_outputs:
            for output in assistant_outputs:
                right_parts.append(
                    "<div class='dialog-card'>"
                    f"<h5>{html.escape(str(output.get('label') or '模型输出'))}</h5>"
                    + _format_pre(output.get("content"))
                    + (f"<div class='dialog-label'>tool_calls</div>{_format_pre(output.get('tool_calls'))}" if output.get("tool_calls") else "")
                    + "</div>"
                )
        else:
            right_parts.append("<p>本轮未记录模型输出；需使用包含 assistant_message trace 的新日志。</p>")
        request_messages = item.get("request_messages")
        messages_block = ""
        if request_messages is not None:
            messages_block = "<details><summary>本轮发送给模型的完整 messages</summary>" + _format_pre(request_messages) + "</details>"
        chunks.append(
            "<details open class='dialog-round'>"
            f"<summary>Round {html.escape(str(item.get('round') or ''))}</summary>"
            "<div class='dialog-grid'>"
            "<div><h4>工具调用执行</h4>" + "".join(left_parts) + "</div>"
            "<div><h4>模型输出</h4>" + "".join(right_parts) + "</div>"
            "</div>"
            + messages_block
            + "</details>"
        )
    return "\n".join(chunks)


def _build_skill_analysis_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    commands: list[str] = []
    seen_commands: set[str] = set()
    law_summary: dict[str, Any] = {}
    for item in records:
        raw = str(item.get("raw") or "")
        if "[session_prompt]" in raw and "tool_call " in raw:
            command = _extract_tool_command(raw)
            if command and command not in seen_commands:
                seen_commands.add(command)
                commands.append(command)
        if "[complaint_law_skill]" in raw and " law_summary=" in raw:
            parsed = _extract_law_summary(raw)
            if parsed:
                law_summary = parsed
    return {"commands": commands, "law_summary": law_summary}


def _render_clause_list(clauses: Any) -> str:
    if not isinstance(clauses, list) or not clauses:
        return ""
    rows = []
    for clause in clauses:
        if not isinstance(clause, dict):
            continue
        law = str(clause.get("law") or "")
        article = str(clause.get("article") or "")
        text_summary = str(clause.get("text_summary") or "")
        rows.append(f"<li>{html.escape(' '.join(part for part in [law, article, text_summary] if part))}</li>")
    return "<ul>" + "".join(rows) + "</ul>" if rows else ""


def _render_law_summary(law_summary: dict[str, Any]) -> str:
    basic_fact = law_summary.get("basic_fact_analysis") if isinstance(law_summary.get("basic_fact_analysis"), dict) else {}
    applicability = law_summary.get("law_applicability_analysis") if isinstance(law_summary.get("law_applicability_analysis"), list) else []
    reasonableness = str(law_summary.get("reasonableness_conclusion") or "").strip()
    user_laws = law_summary.get("user_cited_laws") if isinstance(law_summary.get("user_cited_laws"), list) else []
    possible_laws = law_summary.get("possible_laws") if isinstance(law_summary.get("possible_laws"), list) else []
    if not user_laws and not possible_laws and not basic_fact and not applicability and not reasonableness:
        return "<p>未找到 law_summary 日志；需重新触发包含该字段的新请求。</p>"
    chunks: list[str] = []
    if basic_fact:
        chunks.append("<h4>基础事实分析</h4>" + _json_block(basic_fact))
    if applicability:
        chunks.append("<h4>法律适用性分析</h4>" + _json_block(applicability))
    if reasonableness:
        chunks.append(f"<h4>合理性结论</h4><p>{html.escape(reasonableness)}</p>")
    if user_laws:
        chunks.append("<h4>用户提及法律分析</h4><ul>")
        for item in user_laws:
            if not isinstance(item, dict):
                continue
            title = " | ".join(part for part in [str(item.get("raw_text") or ""), str(item.get("normalized_law") or ""), str(item.get("evidence_match_level") or "")] if part)
            chunks.append(f"<li>{html.escape(title)}{_render_clause_list(item.get('matched_clauses'))}</li>")
        chunks.append("</ul>")
    if possible_laws:
        chunks.append("<h4>系统补充可能涉及法律</h4><ul>")
        for item in possible_laws:
            if not isinstance(item, dict):
                continue
            title = " | ".join(part for part in [str(item.get("law") or ""), str(item.get("reason") or "")] if part)
            chunks.append(f"<li>{html.escape(title)}{_render_clause_list(item.get('matched_clauses'))}</li>")
        chunks.append("</ul>")
    return "".join(chunks)


def _render_skill_analysis_summary(records: list[dict[str, Any]]) -> str:
    summary = _build_skill_analysis_summary(records)
    return "<h4>Round 与工具执行</h4>" + _render_round_summary(records) + "<h4>法律分析结果</h4>" + _render_law_summary(summary.get("law_summary") if isinstance(summary.get("law_summary"), dict) else {})


def _complaint_log_group_key(item: dict[str, Any]) -> str:
    complaint_id = str(item.get("complaint_id") or "").strip()
    complaint_hash = str(item.get("complaint_hash") or "").strip()
    complaint_index = str(item.get("complaint_index") or "").strip()
    if complaint_id:
        return f"id:{complaint_id}"
    if complaint_index and complaint_hash:
        return f"idx:{complaint_index}:hash:{complaint_hash}"
    if complaint_index:
        return f"idx:{complaint_index}"
    if complaint_hash:
        return f"hash:{complaint_hash}"
    return ""


def _complaint_log_group_label(items: list[dict[str, Any]]) -> str:
    item = items[0] if items else {}
    complaint_index = str(item.get("complaint_index") or "").strip()
    complaint_id = str(item.get("complaint_id") or "").strip()
    complaint_hash = str(item.get("complaint_hash") or "").strip()
    parts: list[str] = []
    try:
        index_label = f"举报事项{int(complaint_index) + 1}"
    except Exception:
        index_label = "举报事项"
    parts.append(index_label)
    if complaint_id:
        parts.append(f"complaint_id={complaint_id}")
    if complaint_hash:
        parts.append(f"hash={complaint_hash}")
    return " | ".join(parts)


def _render_complaint_grouped_log_table(records: list[dict[str, Any]]) -> str:
    complaint_groups: dict[str, list[dict[str, Any]]] = {}
    other_items: list[dict[str, Any]] = []
    for item in records:
        key = _complaint_log_group_key(item)
        if key:
            complaint_groups.setdefault(key, []).append(item)
        else:
            other_items.append(item)
    if not complaint_groups:
        return _render_log_table(records)
    chunks: list[str] = []
    if other_items:
        chunks.append("<details><summary>接口级日志（%s 条）</summary>%s</details>" % (len(other_items), _render_log_table(other_items)))
    sorted_groups = sorted(complaint_groups.values(), key=lambda items: (int(str(items[0].get("complaint_index") or "999999")) if str(items[0].get("complaint_index") or "").isdigit() else 999999, str(items[0].get("complaint_id") or ""), str(items[0].get("complaint_hash") or "")))
    for items in sorted_groups:
        chunks.append(
            "<details>"
            f"<summary>{html.escape(_complaint_log_group_label(items))}（{len(items)} 条）</summary>"
            "<details open><summary>对话式工具调用</summary>"
            + _render_tool_dialog_rounds(items)
            + "</details>"
            "<details open><summary>Skill 执行摘要</summary>"
            + _render_skill_analysis_summary(items)
            + "</details>"
            + _render_log_table(items)
            + "</details>"
        )
    return "\n".join(chunks)


def _render_trace_logs(records: list[dict[str, Any]]) -> str:
    if not records:
        return "<p>未找到包含该 base_id/source_request_id 的日志。</p>"
    grouped: dict[str, list[dict[str, Any]]] = {}
    group_meta: dict[str, dict[str, Any]] = {}
    for item in records:
        request_key = str(item.get("request_group") or "other:outside_api_access")
        grouped.setdefault(request_key, []).append(item)
        if request_key not in group_meta and isinstance(item.get("request_meta"), dict):
            group_meta[request_key] = item.get("request_meta") or {}
    ordered_keys = ["request1_wordfit_extract", "request2_wordfit_extract", "request3_complaint_analysis", "request4_reply_draft"]
    ordered_keys.extend(key for key in grouped if key not in ordered_keys)
    chunks: list[str] = []
    for key in ordered_keys:
        items = grouped.get(key) or []
        if not items:
            continue
        chunks.append(
            "<details open>"
            f"<summary>{html.escape(_request_label(key, group_meta.get(key) or {}))}（{len(items)} 条）</summary>"
            + (_render_complaint_grouped_log_table(items) if any(_complaint_log_group_key(item) for item in items) else _render_log_table(items))
            + "</details>"
        )
    chunks.append("<details><summary>原始日志 JSON</summary>" + _json_block(records) + "</details>")
    return "\n".join(chunks)


def _resolve_trace_issue_analysis_version(user_id: str, base_id: str, issue_analysis_version: int | None, trace_logs: list[dict[str, Any]] | None = None, scope_context: ComplaintScopeContext | None = None) -> int:
    """解析 trace 查询使用的举报事项分析版本。"""
    if issue_analysis_version and issue_analysis_version > 0:
        return int(issue_analysis_version)
    log_version = _latest_issue_analysis_version_from_logs(trace_logs or [])
    if log_version > 0:
        return log_version
    for read_scope in complaint_scope_read_chain(scope_context):
        manifest = load_manifest(user_id, base_id, scope_context=read_scope)
        current = int(manifest.get("issue_analysis", {}).get("current") or 0)
        if current > 0:
            return current
    return 0


def build_complaint_trace_payload(user_id: str, file_ids: list[str] | None = None, base_id: str | None = None, issue_analysis_version: int | None = None, scope_context: ComplaintScopeContext | None = None) -> dict[str, Any]:
    """按 base_id 查询日志，不再组装本地 trace/产物。"""
    normalized_file_ids = normalize_trace_file_ids(file_ids or [])
    resolved_base_id = validate_base_id(str(base_id or "").strip()) if str(base_id or "").strip() else build_trace_base_id(normalized_file_ids)
    matched_logs = _filter_logs_by_scope(_filter_logs_by_user_id(_search_trace_logs(resolved_base_id), user_id), scope_context)
    all_trace_logs = _filter_logs_by_scope(_filter_logs_by_user_id(_expand_logs_by_request(matched_logs), user_id), scope_context) or matched_logs
    resolved_issue_version = _resolve_trace_issue_analysis_version(user_id, resolved_base_id, issue_analysis_version, all_trace_logs, scope_context=scope_context)
    trace_logs = _filter_logs_by_issue_analysis_version(all_trace_logs, resolved_issue_version) if resolved_issue_version else all_trace_logs
    version_rows = _build_trace_version_rows(user_id, resolved_base_id, all_trace_logs, issue_analysis_version, scope_context=scope_context)
    return {
        "user_id": user_id,
        "file_ids": normalized_file_ids,
        "base_id": resolved_base_id,
        "scope_context": scope_context.to_meta() if scope_context is not None else {},
        "issue_analysis_version": int(issue_analysis_version or 0) or resolved_issue_version,
        "log_command": _build_log_search_command(resolved_base_id),
        "trace_logs": trace_logs,
        "versions": version_rows,
    }


def render_complaint_trace_html(user_id: str, file_ids: list[str] | None = None, base_id: str | None = None, issue_analysis_version: int | None = None, scope_context: ComplaintScopeContext | None = None) -> str:
    """渲染 base_id 相关日志 HTML。"""
    payload = build_complaint_trace_payload(user_id, file_ids, base_id, issue_analysis_version, scope_context=scope_context)
    head = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>举报件排查</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #172033; background: #f7f8fa; }
    .log-wrap { background: #fff; border: 1px solid #d9dee7; border-radius: 6px; padding: 12px; }
    .version-block { border: 1px solid #cfd6e3; border-radius: 6px; margin: 0 0 14px; padding: 10px; background: #fbfcfe; }
    .stage-block { border-top: 1px solid #edf0f5; padding: 10px 0; }
    .dialog-round { border: 1px solid #e2e8f0; border-radius: 6px; padding: 8px; background: #fff; }
    .dialog-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 12px; align-items: start; }
    .dialog-card { border: 1px solid #e5e7eb; border-radius: 6px; padding: 8px; margin: 8px 0; background: #fcfcfd; }
    .dialog-card h5 { margin: 0 0 6px; font-size: 13px; }
    .dialog-label { margin: 8px 0 4px; color: #475569; font-weight: 600; font-size: 12px; }
    @media (max-width: 900px) { .dialog-grid { grid-template-columns: 1fr; } }
    h3 { margin: 6px 0 8px; font-size: 16px; }
    h4 { margin: 8px 0 6px; font-size: 14px; }
    details { margin: 8px 0; }
    summary { cursor: pointer; font-weight: 600; }
    .path { font-size: 12px; }
    p { margin: 0; color: #586174; }
    pre { margin: 0; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; white-space: pre-wrap; overflow-wrap: anywhere; }
    table { border-collapse: collapse; width: 100%; font-size: 13px; }
    th, td { border: 1px solid #e5e7eb; padding: 6px; text-align: left; vertical-align: top; }
    th { background: #f3f4f6; }
  </style>
</head>
<body>
"""
    version = int(payload.get("issue_analysis_version") or 0)
    version_text = f" issue_analysis_version={version}" if issue_analysis_version else ""
    scope_meta = payload.get("scope_context") if isinstance(payload.get("scope_context"), dict) else {}
    scope_text = f" scope={scope_meta.get('scope_path')}" if scope_meta.get("scope_path") else ""
    summary = f"<p>base_id={html.escape(str(payload.get('base_id') or ''))}{html.escape(scope_text)}{html.escape(version_text)}</p>"
    body = summary + "<div class='log-wrap'>" + _render_versioned_trace(payload) + "</div>"
    return head + body + "</body></html>"
