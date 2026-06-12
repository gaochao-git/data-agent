#!/usr/bin/env python3
"""Benchmark /api/word-fit/extract concurrency and field quality.

The script intentionally uses only Python standard library modules so it can run
from a developer machine against an internal UAT endpoint without installing
project dependencies.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import os
import re
import statistics
import time
import traceback
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_MATCH_TYPE = "auto"
JUDGE_RESULTS = {"correct", "partial", "wrong", "missing", "extra", "judge_error", "skipped"}
DEFAULT_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"
OCR_PAGE_PATTERN = re.compile(
    r"\[OCR\]\s+第\s+(?P<page_no>\d+)\s+页处理完成 file=(?P<file>\S+) page=(?P<page>\d+) "
    r"started_at=(?P<started_at>[\d\-:\.\s]+) finished_at=(?P<finished_at>[\d\-:\.\s]+) "
    r"render_ms=(?P<render_ms>\d+) ocr_ms=(?P<ocr_ms>\d+) duration_ms=(?P<duration_ms>\d+) "
    r"text_len=(?P<text_len>\d+) saved_path=(?P<saved_path>\S+)"
)
OCR_IMAGE_PATTERN = re.compile(
    r"\[OCR\]\s+图片处理完成 file=(?P<file>\S+) page=(?P<page>\d+) "
    r"started_at=(?P<started_at>[\d\-:\.\s]+) finished_at=(?P<finished_at>[\d\-:\.\s]+) "
    r"ocr_ms=(?P<ocr_ms>\d+) duration_ms=(?P<duration_ms>\d+) text_len=(?P<text_len>\d+) saved_path=(?P<saved_path>\S+)"
)
OCR_FILE_TIMING_PATTERN = re.compile(
    r"\[timing\]\s+app=word_fit_app kind=ocr_file file=(?P<file>\S+) duration_ms=(?P<duration_ms>\d+) "
    r"model=(?P<model>\S+) thinking=(?P<thinking>\S+) cache_hit=(?P<cache_hit>\S+) mime_type=(?P<mime_type>\S+)"
    r"(?: pages=(?P<pages>\d+) workers=(?P<workers>\d+))?"
)


@dataclass
class RequestResult:
    request_id: str
    model_label: str
    endpoint: str
    file_id: str
    concurrency: int
    round_index: int
    request_index: int
    force: bool
    extract_model: str
    ocr_model: str
    success: bool
    latency_ms: int
    http_status: int
    json_valid: bool
    empty_result: bool
    extract: Any = None
    raw_events: list[dict[str, Any]] = field(default_factory=list)
    error_type: str = ""
    error_message: str = ""


@dataclass
class FieldCompare:
    request_id: str
    model_label: str
    concurrency: int
    round_index: int
    field_key: str
    match_type: str
    gold_value: Any
    actual_value: Any
    judge_result: str
    judge_reason: str


@dataclass
class OcrDetail:
    request_id: str
    file_path: str
    page: int
    started_at: str
    finished_at: str
    render_ms: int
    ocr_ms: int
    duration_ms: int
    text_len: int
    saved_path: str
    record_type: str


@dataclass
class OcrFileTiming:
    request_id: str
    file_path: str
    duration_ms: int
    model: str
    cache_hit: bool
    mime_type: str
    pages: int
    workers: int


@dataclass
class LlmCallMetric:
    request_id: str
    stage: str
    model: str
    page: int
    prompt_chars: int
    output_chars: int
    first_token_ms: int
    llm_duration_ms: int
    concurrency: int = 0
    round_index: int = 0
    request_index: int = 0


def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def strip_env_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_env_file(path: str, override: bool = False) -> bool:
    if not path:
        return False
    env_path = Path(path).expanduser()
    if not env_path.exists():
        return False
    with open(env_path, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            key, value = line.split("=", 1)
            key = key.strip()
            if not key:
                continue
            if override or key not in os.environ:
                os.environ[key] = strip_env_value(value)
    return True


def ensure_dir(path: str | Path) -> Path:
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    return target


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def extract_request_id_from_path(file_path: str) -> str:
    normalized = str(file_path or "").replace("\\", "/")
    marker = "/runs/"
    if marker not in normalized:
        return ""
    tail = normalized.split(marker, 1)[1]
    return tail.split("/", 1)[0].strip()


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.split()).strip()
    return " ".join(json.dumps(value, ensure_ascii=False, sort_keys=True).split()).strip()


def parse_ocr_log_file(path: str) -> tuple[list[OcrDetail], list[OcrFileTiming]]:
    details: list[OcrDetail] = []
    file_timings: list[OcrFileTiming] = []
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            page_match = OCR_PAGE_PATTERN.search(line)
            if page_match:
                groups = page_match.groupdict()
                file_path = groups["file"]
                details.append(
                    OcrDetail(
                        request_id=extract_request_id_from_path(file_path),
                        file_path=file_path,
                        page=int(groups["page"]),
                        started_at=groups["started_at"],
                        finished_at=groups["finished_at"],
                        render_ms=int(groups["render_ms"]),
                        ocr_ms=int(groups["ocr_ms"]),
                        duration_ms=int(groups["duration_ms"]),
                        text_len=int(groups["text_len"]),
                        saved_path=groups["saved_path"],
                        record_type="pdf_page",
                    )
                )
                continue
            image_match = OCR_IMAGE_PATTERN.search(line)
            if image_match:
                groups = image_match.groupdict()
                file_path = groups["file"]
                details.append(
                    OcrDetail(
                        request_id=extract_request_id_from_path(file_path),
                        file_path=file_path,
                        page=int(groups["page"]),
                        started_at=groups["started_at"],
                        finished_at=groups["finished_at"],
                        render_ms=0,
                        ocr_ms=int(groups["ocr_ms"]),
                        duration_ms=int(groups["duration_ms"]),
                        text_len=int(groups["text_len"]),
                        saved_path=groups["saved_path"],
                        record_type="image",
                    )
                )
                continue
            timing_match = OCR_FILE_TIMING_PATTERN.search(line)
            if timing_match:
                groups = timing_match.groupdict()
                file_path = groups["file"]
                file_timings.append(
                    OcrFileTiming(
                        request_id=extract_request_id_from_path(file_path),
                        file_path=file_path,
                        duration_ms=int(groups["duration_ms"]),
                        model=groups["model"],
                        cache_hit=str(groups["cache_hit"]).strip().lower() == "true",
                        mime_type=groups["mime_type"],
                        pages=int(groups["pages"] or 0),
                        workers=int(groups["workers"] or 0),
                    )
                )
    return details, file_timings


def parse_ocr_metrics_dir(metrics_dir: str, batch_id: str) -> tuple[list[OcrDetail], list[OcrFileTiming]]:
    details: list[OcrDetail] = []
    file_timings: list[OcrFileTiming] = []
    if not metrics_dir or not batch_id:
        return details, file_timings
    batch_dir = Path(metrics_dir).expanduser() / sanitize_filename(batch_id)
    if not batch_dir.exists():
        return details, file_timings
    for path in sorted(batch_dir.glob("*.jsonl")):
        with open(path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                request_id = str(row.get("request_id") or path.stem).strip()
                record_type = str(row.get("type") or "").strip()
                if record_type == "ocr_page":
                    details.append(
                        OcrDetail(
                            request_id=request_id,
                            file_path=str(row.get("file_path") or ""),
                            page=int_cell(row.get("page", 0)),
                            started_at=str(row.get("started_at") or ""),
                            finished_at=str(row.get("finished_at") or ""),
                            render_ms=int_cell(row.get("render_ms", 0)),
                            ocr_ms=int_cell(row.get("ocr_ms", 0)),
                            duration_ms=int_cell(row.get("duration_ms", 0)),
                            text_len=int_cell(row.get("text_len", 0)),
                            saved_path=str(row.get("saved_path") or ""),
                            record_type=str(row.get("record_type") or "ocr_page"),
                        )
                    )
                    continue
                if record_type == "ocr_file":
                    file_timings.append(
                        OcrFileTiming(
                            request_id=request_id,
                            file_path=str(row.get("file_path") or ""),
                            duration_ms=int_cell(row.get("duration_ms", 0)),
                            model=str(row.get("model") or ""),
                            cache_hit=bool_cell(row.get("cache_hit", "")),
                            mime_type=str(row.get("mime_type") or ""),
                            pages=int_cell(row.get("pages", 0)),
                            workers=int_cell(row.get("workers", 0)),
                        )
                    )
    return details, file_timings


def parse_llm_metrics_dir(metrics_dir: str, batch_id: str) -> list[LlmCallMetric]:
    calls: list[LlmCallMetric] = []
    if not metrics_dir or not batch_id:
        return calls
    batch_dir = Path(metrics_dir).expanduser() / sanitize_filename(batch_id)
    if not batch_dir.exists():
        return calls
    for path in sorted(batch_dir.glob("*.jsonl")):
        with open(path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                record_type = str(row.get("type") or "").strip()
                if record_type not in {"ocr_llm", "extract_llm"}:
                    continue
                calls.append(
                    LlmCallMetric(
                        request_id=str(row.get("request_id") or path.stem).strip(),
                        stage="ocr" if record_type == "ocr_llm" else "extract",
                        model=str(row.get("model") or ""),
                        page=int_cell(row.get("page", 0)),
                        prompt_chars=int_cell(row.get("prompt_chars", 0)),
                        output_chars=int_cell(row.get("output_chars", 0)),
                        first_token_ms=int_cell(row.get("first_token_ms", 0)),
                        llm_duration_ms=int_cell(row.get("llm_duration_ms", 0)),
                    )
                )
    return calls


def is_empty_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def flatten_json(value: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten dict/list to comparable path keys."""
    flattened: dict[str, Any] = {}
    if isinstance(value, dict):
        if not value and prefix:
            flattened[prefix] = value
        for key, item in value.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            flattened.update(flatten_json(item, child))
        return flattened
    if isinstance(value, list):
        if not value and prefix:
            flattened[prefix] = value
        for idx, item in enumerate(value):
            child = f"{prefix}[{idx}]"
            flattened.update(flatten_json(item, child))
        return flattened
    if prefix:
        flattened[prefix] = value
    return flattened


def lookup_path(value: Any, path: str) -> Any:
    """Lookup a flattened path like a.b[0].c; return None when missing."""
    current = value
    token = ""
    i = 0
    parts: list[Any] = []
    while i < len(path):
        char = path[i]
        if char == ".":
            if token:
                parts.append(token)
                token = ""
            i += 1
            continue
        if char == "[":
            if token:
                parts.append(token)
                token = ""
            end = path.find("]", i)
            if end < 0:
                return None
            try:
                parts.append(int(path[i + 1 : end]))
            except ValueError:
                return None
            i = end + 1
            continue
        token += char
        i += 1
    if token:
        parts.append(token)
    for part in parts:
        if isinstance(part, int):
            if not isinstance(current, list) or part >= len(current):
                return None
            current = current[part]
        else:
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
    return current


def load_match_rules(path: str | None) -> dict[str, str]:
    if not path:
        return {}
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise ValueError("match rules must be a JSON object")
    rules: dict[str, str] = {}
    for key, value in payload.items():
        if isinstance(value, dict):
            match_type = str(value.get("match_type") or value.get("type") or DEFAULT_MATCH_TYPE).strip()
        else:
            match_type = str(value or DEFAULT_MATCH_TYPE).strip()
        rules[str(key)] = match_type
    return rules


def is_child_path(path: str, parent: str) -> bool:
    return path.startswith(f"{parent}.") or path.startswith(f"{parent}[")


def build_compare_targets(gold_answer: Any, match_rules: dict[str, str]) -> dict[str, Any]:
    targets = flatten_json(gold_answer)
    for rule_key, match_type in match_rules.items():
        if rule_key == "*":
            continue
        gold_value = lookup_path(gold_answer, rule_key)
        if gold_value is None:
            continue
        targets = {key: value for key, value in targets.items() if key != rule_key and not is_child_path(key, rule_key)}
        if match_type != "ignore":
            targets[rule_key] = gold_value
    return targets


def load_extract_fields(args: argparse.Namespace, payload_template: dict[str, Any] | None) -> list[dict[str, Any]]:
    if args.extract_fields:
        extract_fields = load_json(args.extract_fields)
    elif payload_template is not None:
        extract_fields = payload_template.get("extract_fields", [])
    else:
        raise ValueError("either --payload-json or --extract-fields must be provided")
    if not isinstance(extract_fields, list):
        raise ValueError("extract_fields must be a JSON array")
    return extract_fields


def load_payload_template(path: str) -> dict[str, Any] | None:
    if not path:
        return None
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise ValueError("payload json must be a JSON object")
    return payload


def resolve_file_id(args: argparse.Namespace, payload_template: dict[str, Any] | None) -> str:
    file_id = args.file_id or ""
    if not file_id and payload_template is not None:
        file_id = str(payload_template.get("file_id") or "")
    if not file_id:
        raise ValueError("either --payload-json with file_id or --file-id must be provided")
    return file_id


def build_request_payload(args: argparse.Namespace, payload_template: dict[str, Any] | None, extract_fields: list[dict[str, Any]], request_id: str) -> dict[str, Any]:
    payload = dict(payload_template or {})
    payload["file_id"] = resolve_file_id(args, payload_template)
    payload["extract_fields"] = extract_fields
    payload["conversation_id"] = request_id
    if args.disable_analysis_preheat:
        payload["analysis_scene"] = None
        payload["analysis_user_id"] = None
        payload["analysis_match_strategy"] = None
    if args.locate_enabled:
        payload["locate_enabled"] = True
    if args.force:
        payload["force"] = True
    if args.extract_model:
        payload["extract_model"] = args.extract_model
    if args.ocr_model:
        payload["ocr_model"] = args.ocr_model
    ocr_metrics_batch_id = getattr(args, "ocr_metrics_batch_id", "")
    if ocr_metrics_batch_id:
        payload["ocr_metrics_batch_id"] = ocr_metrics_batch_id
    return payload


def infer_match_type(gold_value: Any) -> str:
    if isinstance(gold_value, (dict, list)):
        return "contains"
    text = normalize_text(gold_value)
    if len(text) > 60:
        return "llm_judge"
    return "exact"


def compare_exact(gold_value: Any, actual_value: Any) -> tuple[str, str]:
    if is_empty_value(actual_value):
        return "missing", "actual value is empty"
    if normalize_text(gold_value) == normalize_text(actual_value):
        return "correct", "exact match"
    return "wrong", "exact value differs"


def compare_contains(gold_value: Any, actual_value: Any) -> tuple[str, str]:
    if is_empty_value(actual_value):
        return "missing", "actual value is empty"
    gold_text = normalize_text(gold_value)
    actual_text = normalize_text(actual_value)
    if not gold_text:
        return "correct", "empty gold value"
    if gold_text in actual_text:
        return "correct", "actual contains gold"
    if actual_text in gold_text:
        return "partial", "actual is contained by gold"
    if isinstance(gold_value, list):
        gold_items = [normalize_text(item) for item in gold_value if normalize_text(item)]
        hit_count = sum(1 for item in gold_items if item in actual_text)
        if hit_count == len(gold_items) and gold_items:
            return "correct", "all gold list items found"
        if hit_count > 0:
            return "partial", f"{hit_count}/{len(gold_items)} gold list items found"
    return "wrong", "gold not found in actual"


def compare_without_llm(match_type: str, gold_value: Any, actual_value: Any) -> tuple[str, str]:
    if match_type == "exact":
        return compare_exact(gold_value, actual_value)
    if match_type in {"contains", "auto"}:
        return compare_contains(gold_value, actual_value)
    if match_type == "llm_judge":
        if is_empty_value(actual_value):
            return "missing", "actual value is empty"
        return "skipped", "llm judge disabled"
    return compare_contains(gold_value, actual_value)


def sanitize_filename(value: str) -> str:
    allowed = []
    for char in value:
        if char.isalnum() or char in {"-", "_", "."}:
            allowed.append(char)
        else:
            allowed.append("_")
    return "".join(allowed)[:180]


def parse_json_from_text(text: str) -> Any:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = [line for line in stripped.splitlines() if not line.strip().startswith("```")]
        stripped = "\n".join(lines).strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end < start:
            raise
        parsed = json.loads(stripped[start : end + 1])
    return parsed


def extract_json_object(text: str) -> dict[str, Any]:
    parsed = parse_json_from_text(text)
    if not isinstance(parsed, dict):
        raise ValueError("judge response must be a JSON object")
    return parsed


def judge_chat_endpoint(base_url: str) -> str:
    clean = base_url.rstrip("/")
    if clean.endswith("/chat/completions"):
        return clean
    return f"{clean}/chat/completions"


def call_llm_judge(args: argparse.Namespace, field_key: str, gold_value: Any, actual_value: Any, log_path: Path) -> tuple[str, str]:
    base_url = args.judge_base_url or os.getenv("OPENAI_BASE_URL", "")
    api_key = args.judge_api_key or os.getenv("OPENAI_API_KEY", "")
    if not base_url or not args.judge_model:
        return "judge_error", "judge_mode=llm requires --judge-base-url and --judge-model"
    prompt = {
        "task": "Judge whether the extracted field matches the gold answer semantically. Return JSON only.",
        "allowed_judge_result": ["correct", "partial", "wrong", "missing", "extra"],
        "field_key": field_key,
        "gold_value": gold_value,
        "actual_value": actual_value,
        "rules": [
            "correct: actual value has the same meaning as gold value",
            "partial: actual value covers only part of the gold value",
            "wrong: actual value conflicts with gold value",
            "missing: actual value is empty or absent",
            "extra: actual value contains materially unsupported extra information",
        ],
        "response_schema": {"judge_result": "correct|partial|wrong|missing|extra", "judge_reason": "short reason"},
    }
    payload = {
        "model": args.judge_model,
        "messages": [
            {"role": "system", "content": "You are a strict field extraction evaluator. Return valid JSON only."},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        "temperature": 0,
        "stream": False,
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    log_payload: dict[str, Any] = {"request": payload}
    try:
        request = urllib.request.Request(judge_chat_endpoint(base_url), data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=int(args.judge_timeout_seconds)) as response:
            body = response.read().decode("utf-8", errors="replace")
        log_payload["response_body"] = body
        response_payload = json.loads(body)
        content = response_payload["choices"][0]["message"]["content"]
        parsed = extract_json_object(content)
        judge_result = str(parsed.get("judge_result") or "judge_error").strip()
        judge_reason = str(parsed.get("judge_reason") or "").strip()
        if judge_result not in {"correct", "partial", "wrong", "missing", "extra"}:
            return "judge_error", f"invalid judge_result: {judge_result}"
        return judge_result, judge_reason
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        log_payload["http_status"] = int(exc.code)
        log_payload["response_body"] = body
        log_payload["error_type"] = exc.__class__.__name__
        log_payload["error_message"] = str(exc)
        return "judge_error", f"HTTP {exc.code}: {body[:500] or str(exc)}"
    except Exception as exc:  # pylint: disable=broad-except
        log_payload["error_type"] = exc.__class__.__name__
        log_payload["error_message"] = str(exc)
        return "judge_error", str(exc)
    finally:
        log_path.write_text(json.dumps(log_payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_sse_lines(raw_text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("data:"):
            line = line[5:].strip()
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            events.append({"type": "raw", "content": line})
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events


def extract_result_from_events(events: list[dict[str, Any]]) -> tuple[Any, bool]:
    for event in reversed(events):
        if event.get("type") != "result":
            continue
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        extract = data.get("extract")
        if isinstance(extract, str):
            try:
                parsed_extract = parse_json_from_text(extract)
            except Exception:
                return extract, False
            return parsed_extract, isinstance(parsed_extract, (dict, list))
        return extract, isinstance(extract, (dict, list))
    return None, False


def http_post_json(endpoint: str, payload: dict[str, Any], timeout_seconds: int) -> tuple[int, str]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8", errors="replace")
            return int(response.status), body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return int(exc.code), body


def build_request_id(model_label: str, concurrency: int, round_index: int, request_index: int) -> str:
    return f"{model_label}_c{concurrency}_r{round_index:03d}_req{request_index:03d}"


async def run_single_request(args: argparse.Namespace, payload_template: dict[str, Any] | None, extract_fields: list[dict[str, Any]], concurrency: int, round_index: int, request_index: int) -> RequestResult:
    request_id = build_request_id(args.model_label, concurrency, round_index, request_index)
    file_id = resolve_file_id(args, payload_template)
    payload = build_request_payload(args, payload_template, extract_fields, request_id)
    started = time.perf_counter()
    print(f"[{now_text()}] request start request_id={request_id} concurrency={concurrency} round={round_index} index={request_index}")
    try:
        status, body = await asyncio.to_thread(http_post_json, args.endpoint, payload, int(args.timeout_seconds))
        latency_ms = int((time.perf_counter() - started) * 1000)
        events = parse_sse_lines(body)
        extract, json_valid = extract_result_from_events(events)
        success = 200 <= status < 300 and json_valid and not is_empty_value(extract)
        result = RequestResult(
            request_id=request_id,
            model_label=args.model_label,
            endpoint=args.endpoint,
            file_id=file_id,
            concurrency=concurrency,
            round_index=round_index,
            request_index=request_index,
            force=bool(args.force),
            extract_model=args.extract_model or "",
            ocr_model=args.ocr_model or "",
            success=success,
            latency_ms=latency_ms,
            http_status=status,
            json_valid=json_valid,
            empty_result=is_empty_value(extract),
            extract=extract,
            raw_events=events,
            error_type="" if success else "invalid_result",
            error_message="" if success else body[:500],
        )
        print(f"[{now_text()}] request done request_id={request_id} success={success} latency_ms={latency_ms} status={status}")
        return result
    except Exception as exc:  # pylint: disable=broad-except
        latency_ms = int((time.perf_counter() - started) * 1000)
        result = RequestResult(
            request_id=request_id,
            model_label=args.model_label,
            endpoint=args.endpoint,
            file_id=file_id,
            concurrency=concurrency,
            round_index=round_index,
            request_index=request_index,
            force=bool(args.force),
            extract_model=args.extract_model or "",
            ocr_model=args.ocr_model or "",
            success=False,
            latency_ms=latency_ms,
            http_status=0,
            json_valid=False,
            empty_result=True,
            error_type=exc.__class__.__name__,
            error_message=str(exc),
        )
        print(f"[{now_text()}] request failed request_id={request_id} error={exc.__class__.__name__} latency_ms={latency_ms}")
        return result


async def run_benchmark(args: argparse.Namespace, output_dir: Path, payload_template: dict[str, Any] | None, extract_fields: list[dict[str, Any]], completed_request_ids: set[str]) -> list[RequestResult]:
    results: list[RequestResult] = []
    for concurrency in args.concurrency:
        for round_index in range(1, args.rounds + 1):
            pending_indexes = [
                request_index
                for request_index in range(1, concurrency + 1)
                if build_request_id(args.model_label, concurrency, round_index, request_index) not in completed_request_ids
            ]
            if not pending_indexes:
                print(f"[{now_text()}] model={args.model_label} concurrency={concurrency} round={round_index}/{args.rounds} skipped")
                continue
            tasks = [
                run_single_request(args, payload_template, extract_fields, concurrency, round_index, request_index)
                for request_index in pending_indexes
            ]
            round_saved = 0
            for done_task in asyncio.as_completed(tasks):
                result = await done_task
                results.append(result)
                append_request_results(output_dir, [result])
                completed_request_ids.add(result.request_id)
                round_saved += 1
            print(f"[{now_text()}] model={args.model_label} concurrency={concurrency} round={round_index}/{args.rounds} done saved={round_saved}")
    return results


def build_field_compares(args: argparse.Namespace, output_dir: Path, request_results: list[RequestResult], gold_answer: Any, match_rules: dict[str, str], completed_compare_keys: set[tuple[str, str]]) -> list[FieldCompare]:
    compare_targets = build_compare_targets(gold_answer, match_rules)
    judge_logs_dir = ensure_dir(Path(args.output_dir) / "judge_logs") if args.judge_mode == "llm" else None
    compares: list[FieldCompare] = []
    total_compares = len(request_results) * len(compare_targets)
    llm_judge_total = 0
    for _result in request_results:
        for field_key, gold_value in compare_targets.items():
            match_type = match_rules.get(field_key) or match_rules.get("*") or infer_match_type(gold_value)
            if match_type == "llm_judge" and args.judge_mode == "llm":
                llm_judge_total += 1
    print(f"[{now_text()}] field compare start total={total_compares} llm_judge_total={llm_judge_total}")
    compare_index = 0
    llm_judge_index = 0
    compare_started = time.perf_counter()
    for result in request_results:
        actual = result.extract if isinstance(result.extract, (dict, list)) else {}
        for field_key, gold_value in compare_targets.items():
            compare_index += 1
            actual_value = lookup_path(actual, field_key) if isinstance(actual, (dict, list)) else None
            match_type = match_rules.get(field_key) or match_rules.get("*") or infer_match_type(gold_value)
            compare_key = (result.request_id, field_key)
            if compare_key in completed_compare_keys:
                continue
            if match_type == "llm_judge" and args.judge_mode == "llm" and judge_logs_dir is not None:
                llm_judge_index += 1
                started = time.perf_counter()
                print(f"[{now_text()}] llm judge {llm_judge_index}/{llm_judge_total} compare={compare_index}/{total_compares} request_id={result.request_id} field={field_key}")
                log_name = sanitize_filename(f"{result.request_id}_{field_key}.json")
                judge_result, judge_reason = call_llm_judge(args, field_key, gold_value, actual_value, judge_logs_dir / log_name)
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                print(f"[{now_text()}] llm judge done {llm_judge_index}/{llm_judge_total} result={judge_result} duration_ms={elapsed_ms} field={field_key}")
            else:
                judge_result, judge_reason = compare_without_llm(match_type, gold_value, actual_value)
            compare = FieldCompare(
                request_id=result.request_id,
                model_label=result.model_label,
                concurrency=result.concurrency,
                round_index=result.round_index,
                field_key=field_key,
                match_type=match_type,
                gold_value=gold_value,
                actual_value=actual_value,
                judge_result=judge_result,
                judge_reason=judge_reason,
            )
            compares.append(compare)
            append_field_compares(output_dir, [compare])
            completed_compare_keys.add(compare_key)
    elapsed = time.perf_counter() - compare_started
    print(f"[{now_text()}] field compare done total={total_compares} llm_judge_total={llm_judge_total} elapsed={elapsed:.2f}s")
    return compares


def percentile(values: list[int], p: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    idx = math.ceil((p / 100) * len(ordered)) - 1
    idx = min(max(idx, 0), len(ordered) - 1)
    return ordered[idx]


def group_by(items: list[Any], key_func) -> dict[Any, list[Any]]:
    grouped: dict[Any, list[Any]] = {}
    for item in items:
        grouped.setdefault(key_func(item), []).append(item)
    return grouped


def summarize_performance(results: list[RequestResult]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for (model_label, concurrency), items in sorted(group_by(results, lambda item: (item.model_label, item.concurrency)).items()):
        latencies = [item.latency_ms for item in items]
        success_count = sum(1 for item in items if item.success)
        total_elapsed_ms = max(sum(latencies), 1)
        wall_clock_ms = max(max(latencies), 1)
        rows.append(
            {
                "model": model_label,
                "concurrency": concurrency,
                "request_count": len(items),
                "success_rate": success_count / len(items) if items else 0,
                "avg_latency_ms": int(statistics.mean(latencies)) if latencies else 0,
                "p50_ms": percentile(latencies, 50),
                "p90_ms": percentile(latencies, 90),
                "p95_ms": percentile(latencies, 95),
                "p99_ms": percentile(latencies, 99),
                "qps_estimated": round(len(items) / (wall_clock_ms / 1000), 4),
                "timeout_count": sum(1 for item in items if item.error_type in {"TimeoutError", "socket.timeout"}),
                "failure_count": len(items) - success_count,
                "json_valid_rate": sum(1 for item in items if item.json_valid) / len(items) if items else 0,
                "empty_result_rate": sum(1 for item in items if item.empty_result) / len(items) if items else 0,
                "total_latency_ms": total_elapsed_ms,
            }
        )
    return rows


def summarize_quality(compares: list[FieldCompare]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for (model_label, concurrency), items in sorted(group_by(compares, lambda item: (item.model_label, item.concurrency)).items()):
        total = len(items)
        correct = sum(1 for item in items if item.judge_result == "correct")
        partial = sum(1 for item in items if item.judge_result == "partial")
        missing = sum(1 for item in items if item.judge_result == "missing")
        wrong = sum(1 for item in items if item.judge_result == "wrong")
        judge_error = sum(1 for item in items if item.judge_result == "judge_error")
        rows.append(
            {
                "model": model_label,
                "concurrency": concurrency,
                "field_count": total,
                "field_accuracy": (correct + partial * 0.5) / total if total else 0,
                "correct_rate": correct / total if total else 0,
                "partial_rate": partial / total if total else 0,
                "missing_rate": missing / total if total else 0,
                "wrong_rate": wrong / total if total else 0,
                "judge_error_rate": judge_error / total if total else 0,
            }
        )
    return rows


def summarize_ocr_requests(results: list[RequestResult], ocr_details: list[OcrDetail], ocr_file_timings: list[OcrFileTiming]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    detail_grouped = group_by([item for item in ocr_details if item.request_id], lambda item: sanitize_filename(item.request_id))
    timing_grouped = group_by([item for item in ocr_file_timings if item.request_id], lambda item: sanitize_filename(item.request_id))
    for result in results:
        normalized_request_id = sanitize_filename(result.request_id)
        detail_items = detail_grouped.get(normalized_request_id, [])
        timing_items = timing_grouped.get(normalized_request_id, [])
        e2e_values = [item.duration_ms for item in timing_items]
        page_values = [item.duration_ms for item in detail_items]
        rows.append(
            {
                "request_id": result.request_id,
                "model": result.model_label,
                "concurrency": result.concurrency,
                "round": result.round_index,
                "request_latency_ms": result.latency_ms,
                "ocr_file_count": len(timing_items),
                "ocr_page_count": len(detail_items),
                "ocr_cache_hit_count": sum(1 for item in timing_items if item.cache_hit),
                "ocr_e2e_avg_ms": int(statistics.mean(e2e_values)) if e2e_values else 0,
                "ocr_e2e_p95_ms": percentile(e2e_values, 95) if e2e_values else 0,
                "ocr_page_avg_ms": int(statistics.mean(page_values)) if page_values else 0,
                "ocr_page_p95_ms": percentile(page_values, 95) if page_values else 0,
                "ocr_total_e2e_ms": sum(e2e_values),
                "ocr_total_page_ms": sum(page_values),
            }
        )
    return rows


def summarize_ocr_overview(ocr_request_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for (model_label, concurrency), items in sorted(group_by(ocr_request_rows, lambda item: (item["model"], item["concurrency"])).items()):
        e2e_values = [int(item["ocr_e2e_avg_ms"]) for item in items if int(item["ocr_e2e_avg_ms"]) > 0]
        page_values = [int(item["ocr_page_avg_ms"]) for item in items if int(item["ocr_page_avg_ms"]) > 0]
        rows.append(
            {
                "model": model_label,
                "concurrency": concurrency,
                "request_count": len(items),
                "ocr_file_count": sum(int(item["ocr_file_count"]) for item in items),
                "ocr_page_count": sum(int(item["ocr_page_count"]) for item in items),
                "ocr_cache_hit_count": sum(int(item["ocr_cache_hit_count"]) for item in items),
                "ocr_e2e_avg_ms": int(statistics.mean(e2e_values)) if e2e_values else 0,
                "ocr_e2e_p95_ms": percentile(e2e_values, 95) if e2e_values else 0,
                "ocr_page_avg_ms": int(statistics.mean(page_values)) if page_values else 0,
                "ocr_page_p95_ms": percentile(page_values, 95) if page_values else 0,
            }
        )
    return rows


def enrich_llm_calls(calls: list[LlmCallMetric], results: list[RequestResult]) -> list[LlmCallMetric]:
    by_request = {sanitize_filename(item.request_id): item for item in results}
    for call in calls:
        result = by_request.get(sanitize_filename(call.request_id))
        if result is None:
            continue
        call.concurrency = result.concurrency
        call.round_index = result.round_index
        call.request_index = result.request_index
    return calls


def summarize_llm_metrics(calls: list[LlmCallMetric]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    grouped = group_by(calls, lambda item: (item.stage, item.concurrency))
    for (stage, concurrency), items in sorted(grouped.items()):
        first_values = [item.first_token_ms for item in items if item.first_token_ms > 0]
        duration_values = [item.llm_duration_ms for item in items if item.llm_duration_ms > 0]
        prompt_chars = [item.prompt_chars for item in items if item.prompt_chars > 0]
        output_chars = [item.output_chars for item in items if item.output_chars > 0]
        rows.append(
            {
                "stage": stage,
                "concurrency": concurrency,
                "call_count": len(items),
                "prompt_chars_total": sum(item.prompt_chars for item in items),
                "prompt_chars_avg": int(statistics.mean(prompt_chars)) if prompt_chars else 0,
                "output_chars_total": sum(item.output_chars for item in items),
                "output_chars_avg": int(statistics.mean(output_chars)) if output_chars else 0,
                "first_token_avg_ms": int(statistics.mean(first_values)) if first_values else 0,
                "first_token_max_ms": max(first_values) if first_values else 0,
                "first_token_p95_ms": percentile(first_values, 95) if first_values else 0,
                "duration_avg_ms": int(statistics.mean(duration_values)) if duration_values else 0,
                "duration_max_ms": max(duration_values) if duration_values else 0,
                "duration_p95_ms": percentile(duration_values, 95) if duration_values else 0,
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def json_cell(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


RAW_RESULT_FIELDNAMES = [
    "request_id",
    "model",
    "endpoint",
    "file_id",
    "concurrency",
    "round",
    "request_index",
    "force",
    "extract_model",
    "ocr_model",
    "success",
    "latency_ms",
    "http_status",
    "json_valid",
    "empty_result",
    "error_type",
    "error_message",
]

FIELD_COMPARE_FIELDNAMES = ["request_id", "model", "concurrency", "round", "field_key", "match_type", "gold_value", "actual_value", "judge_result", "judge_reason"]
OCR_DETAIL_FIELDNAMES = ["request_id", "file_path", "page", "started_at", "finished_at", "render_ms", "ocr_ms", "duration_ms", "text_len", "saved_path", "record_type"]
OCR_REQUEST_FIELDNAMES = ["request_id", "model", "concurrency", "round", "request_latency_ms", "ocr_file_count", "ocr_page_count", "ocr_cache_hit_count", "ocr_e2e_avg_ms", "ocr_e2e_p95_ms", "ocr_page_avg_ms", "ocr_page_p95_ms", "ocr_total_e2e_ms", "ocr_total_page_ms"]
OCR_OVERVIEW_FIELDNAMES = ["model", "concurrency", "request_count", "ocr_file_count", "ocr_page_count", "ocr_cache_hit_count", "ocr_e2e_avg_ms", "ocr_e2e_p95_ms", "ocr_page_avg_ms", "ocr_page_p95_ms"]
LLM_CALL_FIELDNAMES = ["request_id", "stage", "model", "concurrency", "round", "request_index", "page", "prompt_chars", "output_chars", "first_token_ms", "llm_duration_ms"]
LLM_SUMMARY_FIELDNAMES = ["stage", "concurrency", "call_count", "prompt_chars_total", "prompt_chars_avg", "output_chars_total", "output_chars_avg", "first_token_avg_ms", "first_token_max_ms", "first_token_p95_ms", "duration_avg_ms", "duration_max_ms", "duration_p95_ms"]


def request_result_row(item: RequestResult) -> dict[str, Any]:
    return {
        "request_id": item.request_id,
        "model": item.model_label,
        "endpoint": item.endpoint,
        "file_id": item.file_id,
        "concurrency": item.concurrency,
        "round": item.round_index,
        "request_index": item.request_index,
        "force": item.force,
        "extract_model": item.extract_model,
        "ocr_model": item.ocr_model,
        "success": item.success,
        "latency_ms": item.latency_ms,
        "http_status": item.http_status,
        "json_valid": item.json_valid,
        "empty_result": item.empty_result,
        "error_type": item.error_type,
        "error_message": item.error_message,
    }


def field_compare_row(item: FieldCompare) -> dict[str, Any]:
    return {
        "request_id": item.request_id,
        "model": item.model_label,
        "concurrency": item.concurrency,
        "round": item.round_index,
        "field_key": item.field_key,
        "match_type": item.match_type,
        "gold_value": json_cell(item.gold_value),
        "actual_value": json_cell(item.actual_value),
        "judge_result": item.judge_result,
        "judge_reason": item.judge_reason,
    }


def append_csv_rows(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    if not rows:
        return
    has_header = path.exists() and path.stat().st_size > 0
    with open(path, "a", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if not has_header:
            writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def write_matrix_csv(path: Path, row_title: str, column_labels: list[str], rows: list[tuple[str, list[int]]]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([row_title, *column_labels])
        for label, values in rows:
            writer.writerow([label, *values])


def color_for_value(value: int, max_value: int) -> str:
    if max_value <= 0 or value <= 0:
        return "#f8fafc"
    ratio = min(max(value / max_value, 0), 1)
    red = int(230 - 45 * ratio)
    green = int(244 - 140 * ratio)
    blue = int(255 - 180 * ratio)
    return f"#{red:02x}{green:02x}{blue:02x}"


def write_heatmap_svg(path: Path, title: str, x_labels: list[str], y_labels: list[str], values: list[list[int]], unit: str) -> None:
    cell_w = 64
    cell_h = 34
    left = 88
    top = 58
    right = 24
    bottom = 28
    width = left + right + cell_w * max(len(x_labels), 1)
    height = top + bottom + cell_h * max(len(y_labels), 1)
    max_value = max([max(row) for row in values if row] or [0])
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<style>text{font-family:Arial,"Microsoft YaHei",sans-serif;font-size:12px;fill:#1f2937}.title{font-size:18px;font-weight:700}.axis{fill:#4b5563}.cell-text{font-size:11px}</style>',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text class="title" x="20" y="28">{title}</text>',
    ]
    for col, label in enumerate(x_labels):
        x = left + col * cell_w + cell_w / 2
        parts.append(f'<text class="axis" x="{x}" y="{top - 14}" text-anchor="middle">{label}</text>')
    for row_index, label in enumerate(y_labels):
        y = top + row_index * cell_h + cell_h / 2 + 4
        parts.append(f'<text class="axis" x="{left - 12}" y="{y}" text-anchor="end">{label}</text>')
    for row_index, row in enumerate(values):
        for col, value in enumerate(row):
            x = left + col * cell_w
            y = top + row_index * cell_h
            fill = color_for_value(value, max_value)
            text = f"{value / 1000:.1f}s" if unit == "s" and value else (str(value) if value else "")
            parts.append(f'<rect x="{x}" y="{y}" width="{cell_w}" height="{cell_h}" fill="{fill}" stroke="#d1d5db"/>')
            parts.append(f'<text class="cell-text" x="{x + cell_w / 2}" y="{y + cell_h / 2 + 4}" text-anchor="middle">{text}</text>')
    parts.append("</svg>")
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def write_line_svg(path: Path, title: str, x_labels: list[str], series: list[tuple[str, list[int]]], unit: str) -> None:
    width = 920
    height = 360
    left = 64
    top = 48
    right = 180
    bottom = 54
    plot_w = width - left - right
    plot_h = height - top - bottom
    max_value = max([max(values) for _name, values in series if values] or [0])
    max_value = max(max_value, 1)
    colors = ["#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c", "#0891b2", "#4f46e5", "#be123c"]

    def point(col: int, value: int) -> tuple[float, float]:
        x = left + (plot_w * col / max(len(x_labels) - 1, 1))
        y = top + plot_h - (plot_h * value / max_value)
        return x, y

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<style>text{font-family:Arial,"Microsoft YaHei",sans-serif;font-size:12px;fill:#1f2937}.title{font-size:18px;font-weight:700}.axis{fill:#4b5563}.legend{font-size:12px}</style>',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text class="title" x="20" y="28">{title}</text>',
        f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" stroke="#9ca3af"/>',
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" stroke="#9ca3af"/>',
    ]
    for idx, label in enumerate(x_labels):
        x, _ = point(idx, 0)
        parts.append(f'<text class="axis" x="{x}" y="{height - 22}" text-anchor="middle">{label}</text>')
    for tick in range(5):
        value = int(max_value * tick / 4)
        y = top + plot_h - (plot_h * tick / 4)
        label = f"{value / 1000:.1f}s" if unit == "s" else str(value)
        parts.append(f'<line x1="{left}" y1="{y}" x2="{left + plot_w}" y2="{y}" stroke="#e5e7eb"/>')
        parts.append(f'<text class="axis" x="{left - 8}" y="{y + 4}" text-anchor="end">{label}</text>')
    for series_index, (name, values) in enumerate(series):
        if not values:
            continue
        color = colors[series_index % len(colors)]
        points = [point(idx, value) for idx, value in enumerate(values)]
        path_d = " ".join(("M" if idx == 0 else "L") + f"{x:.1f},{y:.1f}" for idx, (x, y) in enumerate(points))
        parts.append(f'<path d="{path_d}" fill="none" stroke="{color}" stroke-width="2"/>')
        for x, y in points:
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{color}"/>')
        legend_y = top + 18 + series_index * 20
        parts.append(f'<line x1="{left + plot_w + 24}" y1="{legend_y - 4}" x2="{left + plot_w + 44}" y2="{legend_y - 4}" stroke="{color}" stroke-width="2"/>')
        parts.append(f'<text class="legend" x="{left + plot_w + 50}" y="{legend_y}">{name}</text>')
    parts.append("</svg>")
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def build_matrix(items: list[LlmCallMetric], row_getter, col_getter, value_getter) -> tuple[list[str], list[str], list[list[int]]]:
    row_labels = sorted({str(row_getter(item)) for item in items})
    col_labels = sorted({str(col_getter(item)) for item in items})
    grouped: dict[tuple[str, str], list[int]] = {}
    for item in items:
        grouped.setdefault((str(row_getter(item)), str(col_getter(item))), []).append(int(value_getter(item)))
    values: list[list[int]] = []
    for row in row_labels:
        values.append([int(statistics.mean(grouped.get((row, col), [0]))) for col in col_labels])
    return row_labels, col_labels, values


def generate_metric_artifacts(output_dir: Path, calls: list[LlmCallMetric]) -> list[str]:
    chart_dir = ensure_dir(output_dir / "charts")
    matrix_dir = ensure_dir(output_dir / "matrix")
    chart_paths_by_type: dict[str, list[str]] = {
        "ocr_page_latency_heatmap": [],
        "ocr_page_latency_lines": [],
        "ocr_round_latency_lines": [],
        "extract_first_token_lines": [],
        "extract_total_latency_lines": [],
        "extract_output_chars_lines": [],
    }
    for concurrency in sorted({item.concurrency for item in calls if item.concurrency}):
        ocr_items = [item for item in calls if item.stage == "ocr" and item.concurrency == concurrency]
        if ocr_items:
            rows, cols, values = build_matrix(ocr_items, lambda item: f"r{item.round_index:03d}", lambda item: f"p{item.page:02d}", lambda item: item.llm_duration_ms)
            write_matrix_csv(matrix_dir / f"ocr_page_latency_matrix_c{concurrency}.csv", "round", cols, list(zip(rows, values)))
            heatmap = chart_dir / f"ocr_page_latency_heatmap_c{concurrency}.svg"
            write_heatmap_svg(heatmap, f"OCR page latency heatmap c{concurrency}", cols, rows, values, "s")
            chart_paths_by_type["ocr_page_latency_heatmap"].append(heatmap.relative_to(output_dir).as_posix())
            line_series = [(row, values[idx]) for idx, row in enumerate(rows)]
            line = chart_dir / f"ocr_page_latency_lines_c{concurrency}.svg"
            write_line_svg(line, f"OCR page latency by round c{concurrency}", cols, line_series, "s")
            chart_paths_by_type["ocr_page_latency_lines"].append(line.relative_to(output_dir).as_posix())
            round_groups = group_by(ocr_items, lambda item: item.round_index)
            round_labels = [f"r{round_index:03d}" for round_index in sorted(round_groups)]
            avg_values = [int(statistics.mean([item.llm_duration_ms for item in round_groups[round_index]])) for round_index in sorted(round_groups)]
            max_values = [max(item.llm_duration_ms for item in round_groups[round_index]) for round_index in sorted(round_groups)]
            round_line = chart_dir / f"ocr_round_latency_lines_c{concurrency}.svg"
            write_line_svg(round_line, f"OCR round latency c{concurrency}", round_labels, [("avg", avg_values), ("max", max_values)], "s")
            chart_paths_by_type["ocr_round_latency_lines"].append(round_line.relative_to(output_dir).as_posix())

        extract_items = [item for item in calls if item.stage == "extract" and item.concurrency == concurrency]
        if extract_items:
            for metric_name, title, getter, unit in [
                ("first_token", "Extract first token latency", lambda item: item.first_token_ms, "s"),
                ("total_latency", "Extract total latency", lambda item: item.llm_duration_ms, "s"),
                ("output_chars", "Extract output chars", lambda item: item.output_chars, "chars"),
            ]:
                rows, cols, values = build_matrix(extract_items, lambda item: f"req{item.request_index:03d}", lambda item: f"r{item.round_index:03d}", getter)
                write_matrix_csv(matrix_dir / f"extract_{metric_name}_matrix_c{concurrency}.csv", "request", cols, list(zip(rows, values)))
                series = [(row, values[idx]) for idx, row in enumerate(rows)]
                line = chart_dir / f"extract_{metric_name}_lines_c{concurrency}.svg"
                write_line_svg(line, f"{title} c{concurrency}", cols, series, unit)
                chart_paths_by_type[f"extract_{metric_name}_lines"].append(line.relative_to(output_dir).as_posix())
    chart_paths: list[str] = []
    for paths in chart_paths_by_type.values():
        chart_paths.extend(paths)
    return chart_paths


def chart_group_title(chart_path: str) -> str:
    name = Path(chart_path).stem
    if name.startswith("ocr_page_latency_heatmap"):
        return "OCR 页耗时热力图"
    if name.startswith("ocr_page_latency_lines"):
        return "OCR 页耗时折线图"
    if name.startswith("ocr_round_latency_lines"):
        return "OCR 轮次耗时趋势"
    if name.startswith("extract_first_token_lines"):
        return "字段提取首 token 趋势"
    if name.startswith("extract_total_latency_lines"):
        return "字段提取总耗时趋势"
    if name.startswith("extract_output_chars_lines"):
        return "字段提取输出字数趋势"
    return "其他趋势图"


def append_request_results(output_dir: Path, results: list[RequestResult]) -> None:
    append_csv_rows(output_dir / "raw_results.csv", [request_result_row(item) for item in results], RAW_RESULT_FIELDNAMES)
    with open(output_dir / "responses.jsonl", "a", encoding="utf-8") as handle:
        for item in results:
            payload = {
                "request_id": item.request_id,
                "success": item.success,
                "extract": item.extract,
                "raw_events": item.raw_events,
                "error_type": item.error_type,
                "error_message": item.error_message,
            }
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def append_field_compares(output_dir: Path, compares: list[FieldCompare]) -> None:
    append_csv_rows(output_dir / "field_compare.csv", [field_compare_row(item) for item in compares], FIELD_COMPARE_FIELDNAMES)


def bool_cell(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes"}


def int_cell(value: Any) -> int:
    try:
        return int(float(str(value).strip()))
    except Exception:
        return 0


def load_existing_request_results(output_dir: Path) -> list[RequestResult]:
    path = output_dir / "raw_results.csv"
    if not path.exists():
        return []
    response_payloads = load_existing_response_payloads(output_dir)
    results: list[RequestResult] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            request_id = row.get("request_id", "")
            response_payload = response_payloads.get(request_id, {})
            results.append(
                RequestResult(
                    request_id=request_id,
                    model_label=row.get("model", ""),
                    endpoint=row.get("endpoint", ""),
                    file_id=row.get("file_id", ""),
                    concurrency=int_cell(row.get("concurrency", 0)),
                    round_index=int_cell(row.get("round", 0)),
                    request_index=int_cell(row.get("request_index", 0)),
                    force=bool_cell(row.get("force", "")),
                    extract_model=row.get("extract_model", ""),
                    ocr_model=row.get("ocr_model", ""),
                    success=bool_cell(row.get("success", "")),
                    latency_ms=int_cell(row.get("latency_ms", 0)),
                    http_status=int_cell(row.get("http_status", 0)),
                    json_valid=bool_cell(row.get("json_valid", "")),
                    empty_result=bool_cell(row.get("empty_result", "")),
                    extract=response_payload.get("extract"),
                    raw_events=response_payload.get("raw_events") if isinstance(response_payload.get("raw_events"), list) else [],
                    error_type=row.get("error_type", ""),
                    error_message=row.get("error_message", ""),
                )
            )
    return results


def load_existing_response_payloads(output_dir: Path) -> dict[str, dict[str, Any]]:
    path = output_dir / "responses.jsonl"
    if not path.exists():
        return {}
    payloads: dict[str, dict[str, Any]] = {}
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and payload.get("request_id"):
                payloads[str(payload["request_id"])] = payload
    return payloads


def load_existing_field_compares(output_dir: Path) -> list[FieldCompare]:
    path = output_dir / "field_compare.csv"
    if not path.exists():
        return []
    compares: list[FieldCompare] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            compares.append(
                FieldCompare(
                    request_id=row.get("request_id", ""),
                    model_label=row.get("model", ""),
                    concurrency=int_cell(row.get("concurrency", 0)),
                    round_index=int_cell(row.get("round", 0)),
                    field_key=row.get("field_key", ""),
                    match_type=row.get("match_type", ""),
                    gold_value=row.get("gold_value", ""),
                    actual_value=row.get("actual_value", ""),
                    judge_result=row.get("judge_result", ""),
                    judge_reason=row.get("judge_reason", ""),
                )
            )
    return compares


def save_outputs(args: argparse.Namespace, output_dir: Path, results: list[RequestResult], compares: list[FieldCompare], ocr_details: list[OcrDetail], ocr_file_timings: list[OcrFileTiming], llm_calls: list[LlmCallMetric]) -> None:
    performance_rows = summarize_performance(results)
    write_csv(
        output_dir / "performance_summary.csv",
        performance_rows,
        [
            "model",
            "concurrency",
            "request_count",
            "success_rate",
            "avg_latency_ms",
            "p50_ms",
            "p90_ms",
            "p95_ms",
            "p99_ms",
            "qps_estimated",
            "timeout_count",
            "failure_count",
            "json_valid_rate",
            "empty_result_rate",
            "total_latency_ms",
        ],
    )

    quality_rows = summarize_quality(compares)
    write_csv(
        output_dir / "quality_summary.csv",
        quality_rows,
        ["model", "concurrency", "field_count", "field_accuracy", "correct_rate", "partial_rate", "missing_rate", "wrong_rate", "judge_error_rate"],
    )

    ocr_request_rows = summarize_ocr_requests(results, ocr_details, ocr_file_timings)
    ocr_overview_rows = summarize_ocr_overview(ocr_request_rows)
    llm_calls = enrich_llm_calls(llm_calls, results)
    llm_summary_rows = summarize_llm_metrics(llm_calls)
    chart_paths = generate_metric_artifacts(output_dir, llm_calls)
    write_csv(output_dir / "ocr_detail.csv", [item.__dict__ for item in ocr_details], OCR_DETAIL_FIELDNAMES)
    write_csv(output_dir / "ocr_request_summary.csv", ocr_request_rows, OCR_REQUEST_FIELDNAMES)
    write_csv(output_dir / "ocr_overview.csv", ocr_overview_rows, OCR_OVERVIEW_FIELDNAMES)
    write_csv(output_dir / "llm_call_detail.csv", [item.__dict__ for item in llm_calls], LLM_CALL_FIELDNAMES)
    write_csv(output_dir / "llm_metrics_summary.csv", llm_summary_rows, LLM_SUMMARY_FIELDNAMES)
    write_report(output_dir / "report.md", args, performance_rows, quality_rows, ocr_overview_rows, llm_summary_rows, chart_paths, results, compares)


def percent_text(value: float) -> str:
    return f"{value * 100:.2f}%"


def markdown_table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
    if not rows:
        return "_无数据_"
    header = "| " + " | ".join(title for title, _key in columns) + " |"
    separator = "| " + " | ".join("---" for _title, _key in columns) + " |"
    body_lines = []
    for row in rows:
        values = []
        for _title, key in columns:
            value = row.get(key, "")
            if isinstance(value, float) and ("rate" in key or "accuracy" in key):
                values.append(percent_text(value))
            else:
                values.append(str(value))
        body_lines.append("| " + " | ".join(values) + " |")
    return "\n".join([header, separator, *body_lines])


def write_report(path: Path, args: argparse.Namespace, performance_rows: list[dict[str, Any]], quality_rows: list[dict[str, Any]], ocr_overview_rows: list[dict[str, Any]], llm_summary_rows: list[dict[str, Any]], chart_paths: list[str], results: list[RequestResult], compares: list[FieldCompare]) -> None:
    failed_examples = [item for item in results if not item.success][:5]
    field_errors = [item for item in compares if item.judge_result not in {"correct"}][:20]
    content = [
        "# WordFit 字段提取并发验证报告",
        "",
        f"- 生成时间：{now_text()}",
        f"- Endpoint：`{args.endpoint}`",
        f"- file_id：`{args.file_id or 'from payload'}`",
        f"- payload_json：`{args.payload_json or ''}`",
        f"- model_label：`{args.model_label}`",
        f"- extract_model：`{args.extract_model or ''}`",
        f"- ocr_model：`{args.ocr_model or ''}`",
        f"- ocr_log_file：`{args.ocr_log_file or ''}`",
        f"- ocr_metrics_dir：`{args.ocr_metrics_dir or ''}`",
        f"- ocr_metrics_batch_id：`{args.ocr_metrics_batch_id or ''}`",
        f"- force：`{bool(args.force)}`",
        f"- disable_analysis_preheat：`{bool(args.disable_analysis_preheat)}`",
        f"- 并发数：`{','.join(str(x) for x in args.concurrency)}`",
        f"- 轮次：`{args.rounds}`",
        f"- judge_mode：`{args.judge_mode}`",
        "",
        "## 测试配置",
        "",
        "| 项目 | 值 |",
        "| --- | --- |",
        f"| 模型标签 | {args.model_label} |",
        f"| 字段提取模型 | {args.extract_model or ''} |",
        f"| OCR模型 | {args.ocr_model or ''} |",
        f"| 并发配置 | {','.join(str(x) for x in args.concurrency)} |",
        f"| 轮次 | {args.rounds} |",
        f"| OCR缓存强制刷新 | {bool(args.force)} |",
        f"| Judge模式 | {args.judge_mode} |",
        "",
        "## 性能对比",
        "",
        markdown_table(
            performance_rows,
            [
                ("模型", "model"),
                ("并发数", "concurrency"),
                ("请求数", "request_count"),
                ("成功率", "success_rate"),
                ("平均耗时ms", "avg_latency_ms"),
                ("P95", "p95_ms"),
                ("P99", "p99_ms"),
                ("QPS估算", "qps_estimated"),
                ("失败数", "failure_count"),
                ("JSON合法率", "json_valid_rate"),
                ("空结果率", "empty_result_rate"),
            ],
        ),
        "",
        "## OCR 对比",
        "",
        markdown_table(
            ocr_overview_rows,
            [
                ("模型", "model"),
                ("并发数", "concurrency"),
                ("请求数", "request_count"),
                ("OCR文件数", "ocr_file_count"),
                ("OCR页数", "ocr_page_count"),
                ("OCR缓存命中数", "ocr_cache_hit_count"),
                ("OCR E2E均值ms", "ocr_e2e_avg_ms"),
                ("OCR E2E P95", "ocr_e2e_p95_ms"),
                ("OCR页均值ms", "ocr_page_avg_ms"),
                ("OCR页P95", "ocr_page_p95_ms"),
            ],
        ),
        "",
        "## LLM 字数与首 token",
        "",
        markdown_table(
            llm_summary_rows,
            [
                ("阶段", "stage"),
                ("并发数", "concurrency"),
                ("调用数", "call_count"),
                ("Prompt总字数", "prompt_chars_total"),
                ("Prompt均值", "prompt_chars_avg"),
                ("输出总字数", "output_chars_total"),
                ("输出均值", "output_chars_avg"),
                ("首token均值ms", "first_token_avg_ms"),
                ("首token最大ms", "first_token_max_ms"),
                ("首token P95", "first_token_p95_ms"),
                ("总耗时均值ms", "duration_avg_ms"),
                ("总耗时最大ms", "duration_max_ms"),
                ("总耗时 P95", "duration_p95_ms"),
            ],
        ),
        "",
        "## 趋势图",
        "",
    ]
    if chart_paths:
        current_group = ""
        for chart_path in chart_paths:
            group = chart_group_title(chart_path)
            if group != current_group:
                content.extend([f"### {group}", ""])
                current_group = group
            content.extend([f"![{Path(chart_path).stem}]({chart_path})", ""])
    else:
        content.extend(["_无趋势图数据_", ""])
    content.extend(
        [
        "## 质量对比",
        "",
        markdown_table(
            quality_rows,
            [
                ("模型", "model"),
                ("并发数", "concurrency"),
                ("字段数", "field_count"),
                ("字段准确率", "field_accuracy"),
                ("正确率", "correct_rate"),
                ("部分正确率", "partial_rate"),
                ("缺失率", "missing_rate"),
                ("错误率", "wrong_rate"),
                ("Judge错误率", "judge_error_rate"),
            ],
        ),
        "",
        "## 失败请求样例",
        "",
        ]
    )
    if failed_examples:
        for item in failed_examples:
            content.append(f"- `{item.request_id}` status={item.http_status} error={item.error_type} message={item.error_message[:200]}")
    else:
        content.append("_无失败请求_")
    content.extend(["", "## 字段错误样例", ""])
    if field_errors:
        for item in field_errors:
            content.append(f"- `{item.request_id}` `{item.field_key}` result={item.judge_result} reason={item.judge_reason}")
    else:
        content.append("_无字段错误_")
    content.extend(
        [
            "",
            "## 输出文件",
            "",
            "- `raw_results.csv`：请求级明细",
            "- `performance_summary.csv`：性能汇总",
            "- `quality_summary.csv`：质量汇总",
            "- `field_compare.csv`：字段级对比",
            "- `ocr_detail.csv`：OCR 每页/每图明细",
            "- `ocr_request_summary.csv`：OCR 按接口请求汇总",
            "- `ocr_overview.csv`：OCR 按并发维度汇总",
            "- `llm_call_detail.csv`：OCR 与字段提取模型调用明细",
            "- `llm_metrics_summary.csv`：OCR 与字段提取字数、首 token、耗时汇总",
            "- `charts/`：趋势图与热力图 SVG",
            "- `matrix/`：图表对应的矩阵 CSV",
            "- `responses.jsonl`：原始 SSE 解析结果与 extract 结果",
            "- `judge_logs/`：LLM judge 输入输出日志，仅 `--judge-mode llm` 时生成",
        ]
    )
    path.write_text("\n".join(content) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark /api/word-fit/extract concurrency and quality.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE), help="env file loaded before running; defaults to server/.env")
    parser.add_argument("--env-override", action="store_true", help="override existing environment variables with --env-file values")
    parser.add_argument("--endpoint", required=True, help="UAT /api/word-fit/extract endpoint")
    parser.add_argument("--payload-json", default="", help="full request payload JSON file path; file_id and extract_fields can be read from it")
    parser.add_argument("--file-id", default="", help="override file_id sent to word-fit/extract")
    parser.add_argument("--extract-fields", default="", help="override extract_fields JSON file path")
    parser.add_argument("--gold-answer", required=True, help="gold answer JSON file path")
    parser.add_argument("--match-rules", default="", help="optional field match rules JSON path")
    parser.add_argument("--output-dir", required=True, help="output directory")
    parser.add_argument("--model-label", default="default", help="model label shown in reports")
    parser.add_argument("--extract-model", default="", help="request-level extract_model")
    parser.add_argument("--ocr-model", default="", help="request-level ocr_model")
    parser.add_argument("--ocr-log-file", default="", help="optional UAT-exported log file for OCR per-page/per-image timing statistics")
    parser.add_argument("--ocr-metrics-dir", default="", help="directory containing OCR metrics batches written by the UAT service")
    parser.add_argument("--ocr-metrics-batch-id", default="", help="OCR metrics batch id sent to the service and read from --ocr-metrics-dir")
    parser.add_argument("--force", action="store_true", help="send force=true to bypass OCR cache")
    parser.add_argument("--disable-analysis-preheat", action="store_true", help="clear analysis_scene/user/strategy to disable async complaint-analysis preheat")
    parser.add_argument("--locate-enabled", action="store_true", help="send locate_enabled=true")
    parser.add_argument("--concurrency", nargs="+", type=int, default=[1], help="concurrency list")
    parser.add_argument("--rounds", type=int, default=1, help="rounds per concurrency")
    parser.add_argument("--timeout-seconds", type=int, default=600, help="single request timeout")
    parser.add_argument("--judge-mode", choices=["none", "llm"], default="none", help="llm enables judge for match_type=llm_judge")
    parser.add_argument("--judge-base-url", default="", help="OpenAI-compatible base URL for llm judge; defaults to OPENAI_BASE_URL")
    parser.add_argument("--judge-api-key", default="", help="OpenAI-compatible API key for llm judge; defaults to OPENAI_API_KEY")
    parser.add_argument("--judge-model", default="", help="model name used by llm judge")
    parser.add_argument("--judge-timeout-seconds", type=int, default=120, help="single llm judge timeout")
    parser.add_argument("--resume", action="store_true", help="resume from existing raw_results.csv and field_compare.csv in output-dir")
    parser.add_argument("--judge-only", action="store_true", help="only rerun field compare/judge from existing raw_results.csv and responses.jsonl; do not call extract endpoint")
    parser.add_argument("--rerun-judge-errors", action="store_true", help="with --resume or --judge-only, rerun existing llm_judge rows whose result is judge_error")
    parser.add_argument("--rerun-judge", action="store_true", help="with --resume or --judge-only, rerun all existing llm_judge rows")
    return parser.parse_args()


def ensure_resume_policy(args: argparse.Namespace, output_dir: Path) -> None:
    if args.resume or args.judge_only:
        return
    existing = [name for name in ["raw_results.csv", "field_compare.csv", "responses.jsonl"] if (output_dir / name).exists()]
    if existing:
        raise ValueError(f"output-dir already has benchmark files {existing}; use --resume or choose a new --output-dir")


def validate_judge_config(args: argparse.Namespace) -> None:
    if args.judge_mode != "llm":
        return
    base_url = args.judge_base_url or os.getenv("OPENAI_BASE_URL", "")
    api_key = args.judge_api_key or os.getenv("OPENAI_API_KEY", "")
    missing = []
    if not base_url:
        missing.append("OPENAI_BASE_URL or --judge-base-url")
    if not args.judge_model:
        missing.append("--judge-model")
    if missing:
        raise ValueError("judge-mode=llm missing required config: " + ", ".join(missing))
    if not api_key:
        print(f"[{now_text()}] warning: OPENAI_API_KEY/--judge-api-key is empty; judge request will be sent without Authorization header")
    print(f"[{now_text()}] judge config base_url={base_url} model={args.judge_model} api_key_present={bool(api_key)}")


def filter_existing_compares(args: argparse.Namespace, compares: list[FieldCompare]) -> list[FieldCompare]:
    if args.rerun_judge:
        return [item for item in compares if item.match_type != "llm_judge"]
    if args.rerun_judge_errors:
        return [item for item in compares if not (item.match_type == "llm_judge" and item.judge_result == "judge_error")]
    return compares


def main() -> None:
    args = parse_args()
    loaded_env = load_env_file(args.env_file, args.env_override)
    print(f"[{now_text()}] env file {args.env_file} loaded={loaded_env}")
    validate_judge_config(args)
    if args.ocr_metrics_dir and not args.ocr_metrics_batch_id:
        if args.judge_only:
            raise ValueError("--judge-only with --ocr-metrics-dir requires --ocr-metrics-batch-id")
        args.ocr_metrics_batch_id = sanitize_filename(f"{args.model_label}_{datetime.now().strftime('%Y%m%d%H%M%S')}")
    if args.ocr_metrics_batch_id:
        print(f"[{now_text()}] ocr metrics batch id={args.ocr_metrics_batch_id}")
    output_dir = ensure_dir(args.output_dir)
    ensure_resume_policy(args, output_dir)
    payload_template = load_payload_template(args.payload_json)
    extract_fields = load_extract_fields(args, payload_template)
    resolve_file_id(args, payload_template)
    gold_answer = load_json(args.gold_answer)
    match_rules = load_match_rules(args.match_rules or None)
    ocr_details, ocr_file_timings = (parse_ocr_log_file(args.ocr_log_file) if args.ocr_log_file else ([], []))
    llm_calls: list[LlmCallMetric] = []
    if args.ocr_log_file:
        print(f"[{now_text()}] ocr log loaded details={len(ocr_details)} file_timings={len(ocr_file_timings)} from={args.ocr_log_file}")
    should_load_existing = args.resume or args.judge_only
    existing_results = load_existing_request_results(output_dir) if should_load_existing else []
    existing_compares = filter_existing_compares(args, load_existing_field_compares(output_dir)) if should_load_existing else []
    if should_load_existing and (args.rerun_judge or args.rerun_judge_errors):
        write_csv(output_dir / "field_compare.csv", [field_compare_row(item) for item in existing_compares], FIELD_COMPARE_FIELDNAMES)
    completed_request_ids = {item.request_id for item in existing_results}
    completed_compare_keys = {(item.request_id, item.field_key) for item in existing_compares}
    if should_load_existing:
        print(f"[{now_text()}] resume loaded requests={len(existing_results)} field_compares={len(existing_compares)}")
    if args.judge_only and not existing_results:
        raise ValueError("--judge-only requires existing raw_results.csv and responses.jsonl in output-dir")
    started = time.perf_counter()
    try:
        new_results = [] if args.judge_only else asyncio.run(run_benchmark(args, output_dir, payload_template, extract_fields, completed_request_ids))
        all_results = existing_results + new_results
        new_compares = build_field_compares(args, output_dir, all_results, gold_answer, match_rules, completed_compare_keys)
        all_compares = existing_compares + new_compares
        if args.ocr_metrics_dir and args.ocr_metrics_batch_id:
            metric_details, metric_file_timings = parse_ocr_metrics_dir(args.ocr_metrics_dir, args.ocr_metrics_batch_id)
            metric_llm_calls = parse_llm_metrics_dir(args.ocr_metrics_dir, args.ocr_metrics_batch_id)
            print(f"[{now_text()}] ocr metrics loaded details={len(metric_details)} file_timings={len(metric_file_timings)} llm_calls={len(metric_llm_calls)} from={Path(args.ocr_metrics_dir).expanduser() / sanitize_filename(args.ocr_metrics_batch_id)}")
            ocr_details = ocr_details + metric_details
            ocr_file_timings = ocr_file_timings + metric_file_timings
            llm_calls = llm_calls + metric_llm_calls
        save_outputs(args, output_dir, all_results, all_compares, ocr_details, ocr_file_timings, llm_calls)
    except Exception as exc:  # pylint: disable=broad-except
        error_path = output_dir / "benchmark_error.log"
        error_path.write_text(f"{exc}\n\n{traceback.format_exc()}", encoding="utf-8")
        raise
    elapsed = time.perf_counter() - started
    print(f"[{now_text()}] benchmark done output_dir={output_dir} elapsed={elapsed:.2f}s")


if __name__ == "__main__":
    main()
