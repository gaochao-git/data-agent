from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import requests

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency
    load_dotenv = None

SERVER_ROOT = Path(__file__).resolve().parents[4]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from apps.complaint_app.core.llm.llm_client import AsyncLLMClient, ModelConfig
from apps.complaint_app.core.model_config import get_analysis_model
from utils.llm_thinking import build_thinking_extra_body
from utils.logger import get_logger
from apps.complaint_app.tools.zhidu_clause_extract.progress_utils import gather_with_item_progress


logger = get_logger(__name__)
MAX_LOG_PREVIEW = 2000
FOCUS_CSV_FIELDS = [
    "candidate_id",
    "query_label",
    "query_index",
    "verdict",
    "confidence",
    "conflict_dimension",
    "conflict_type",
    "reason",
    "key_differences",
    "doc_id_1",
    "title_1",
    "article_no_1",
    "evidence_1",
    "doc_id_2",
    "title_2",
    "article_no_2",
    "evidence_2",
    "subject",
    "action",
    "object",
    "condition_1",
    "condition_2",
    "constraint_1",
    "constraint_2",
    "error",
]

SYSTEM_PROMPT = """你是制度冲突判定助手。

你会收到一条来自图谱查询的“冲突候选记录”。
请只基于给定字段判断该候选是否构成“冲突/不冲突/不确定”。

输出 JSON，格式必须如下：
{
  "verdict": "conflict",
  "confidence": "high",
  "conflict_dimension": "threshold_conflict",
  "conflict_type": "审批层级不一致",
  "reason": "两条规则约束同一事项，但审批层级要求不一致。",
  "key_differences": ["约束口径不同", "同一主体同一动作"]
}

要求：
1. verdict 只能取：conflict、non_conflict、uncertain
2. confidence 只能取：high、medium、low
3. conflict_dimension 只能取：
   - constraint_conflict（约束口径冲突）
   - condition_conflict（条件冲突）
   - threshold_conflict（时间/金额阈值冲突）
   - scope_conflict（适用范围冲突）
   - general_special_conflict（一般 vs 特别条款冲突）
   - hierarchy_exemption_conflict（上位/下位规则豁免冲突）
   - mixed（混合型）
   - none（无明确冲突维度）
4. 只输出 JSON
5. 不要输出代码块标记"""


# 解析命令行参数。
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="基于 SPARQL 候选结果做条款冲突 LLM 裁决。")
    parser.add_argument("--sparql-endpoint", required=True, help="Fuseki 查询端点，如 http://ip:3030/zhidu/query")
    parser.add_argument("--sparql-file", required=True, help="SPARQL 文件路径（支持 # Query N: 多段）")
    parser.add_argument("--output-dir", required=True, help="输出目录")
    parser.add_argument("--query-labels", default="", help="只跑指定查询标签，逗号分隔（如 Query 1,Query 3）")
    parser.add_argument("--limit-per-query", type=int, default=200, help="每条查询最多取多少候选，默认 200")
    parser.add_argument("--limit-candidates", type=int, default=0, help="最多处理多少候选，0 表示全部")
    parser.add_argument("--page-size", type=int, default=20, help="SPARQL 每页取多少候选，默认 20")
    parser.add_argument("--timeout-seconds", type=int, default=30, help="SPARQL 请求超时秒数，默认 30")
    parser.add_argument("--model", default="", help="模型名，默认读取 OPENAI_ANALYSIS_MODEL")
    parser.add_argument("--concurrency", type=int, default=3, help="并发数，默认 3")
    parser.add_argument("--business-retries", type=int, default=3, help="模型返回非 JSON 时的业务重试次数，默认 3")
    parser.add_argument("--enable-thinking", action="store_true", help="显式开启模型思考")
    return parser


# 加载环境变量，便于直接读取 server/.env。
def load_env_files() -> None:
    if load_dotenv is None:
        return
    load_dotenv()
    server_env = SERVER_ROOT / ".env"
    if server_env.is_file():
        load_dotenv(server_env, override=False)


# 读取文本预览，避免日志过长。
def preview_text(text: str, limit: int = MAX_LOG_PREVIEW) -> str:
    source = str(text or "").strip()
    if len(source) <= limit:
        return source
    return f"{source[:limit]}... [truncated {len(source) - limit} chars]"


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
            logger.warning("冲突裁决 content 为空，回退字段=%s", field)
            return fallback
    return ""


# 去掉 JSON 代码块标记。
def strip_json_fence(text: str) -> str:
    source = str(text or "").strip()
    lower_source = source.lower()
    if lower_source.startswith("```json"):
        source = source[7:].lstrip()
    elif lower_source.startswith("```"):
        source = source[3:].lstrip()
    if source.endswith("```"):
        source = source[:-3].rstrip()
    return source


# 解析并校验模型 JSON 输出。
def parse_judgment_payload(text: str) -> dict[str, Any]:
    source = strip_json_fence(text)
    payload = json.loads(source)
    if not isinstance(payload, dict):
        raise ValueError("模型返回不是 JSON 对象")
    verdict = str(payload.get("verdict") or "").strip()
    confidence = str(payload.get("confidence") or "").strip()
    if verdict not in {"conflict", "non_conflict", "uncertain"}:
        raise ValueError("模型返回缺少合法 verdict")
    if confidence not in {"high", "medium", "low"}:
        raise ValueError("模型返回缺少合法 confidence")
    conflict_dimension = str(payload.get("conflict_dimension") or "").strip()
    valid_dimensions = {
        "constraint_conflict",
        "condition_conflict",
        "threshold_conflict",
        "scope_conflict",
        "general_special_conflict",
        "hierarchy_exemption_conflict",
        "mixed",
        "none",
    }
    if conflict_dimension not in valid_dimensions:
        raise ValueError("模型返回缺少合法 conflict_dimension")
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise ValueError("模型返回缺少 reason")
    differences = payload.get("key_differences")
    if differences is None:
        payload["key_differences"] = []
    elif not isinstance(differences, list):
        raise ValueError("模型返回 key_differences 不是数组")
    else:
        payload["key_differences"] = [str(item).strip() for item in differences if str(item).strip()]
    payload["conflict_type"] = str(payload.get("conflict_type") or "").strip()
    payload["conflict_dimension"] = conflict_dimension
    payload["verdict"] = verdict
    payload["confidence"] = confidence
    payload["reason"] = reason
    return payload


# 写 JSONL 文件。
def write_jsonl_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file_obj:
        for row in rows:
            file_obj.write(json.dumps(row, ensure_ascii=False) + "\n")


# 追加写 JSONL 文件。
def append_jsonl_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file_obj:
        for row in rows:
            file_obj.write(json.dumps(row, ensure_ascii=False) + "\n")


# 读取 JSONL 文件，跳过空行。
def read_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file_obj:
        for line_no, line in enumerate(file_obj, start=1):
            source = line.strip()
            if not source:
                continue
            try:
                payload = json.loads(source)
            except Exception as err:
                raise ValueError(f"读取 JSONL 失败 path={path} line={line_no} err={err}") from err
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


# 写 JSON 文件。
def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# 写 CSV 文件。
def write_csv_rows(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: str(row.get(field) or "") for field in fieldnames})


# 取候选字段值，按候选 key 顺序回退。
def pick_candidate_value(row: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


# 过滤需要人工快速复核的裁决结果。
def filter_focus_judgments(judgments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in judgments if str(row.get("verdict") or "").strip() in {"conflict", "uncertain"}]


# 转换为人工筛选友好的 CSV 行。
def build_focus_csv_rows(judgments: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for judgment in judgments:
        candidate = judgment.get("row")
        candidate_row = candidate if isinstance(candidate, dict) else {}
        key_differences = judgment.get("key_differences")
        key_differences_text = " | ".join([str(item).strip() for item in key_differences if str(item).strip()]) if isinstance(key_differences, list) else ""
        rows.append(
            {
                "candidate_id": str(judgment.get("candidate_id") or "").strip(),
                "query_label": str(judgment.get("query_label") or "").strip(),
                "query_index": str(judgment.get("query_index") or "").strip(),
                "verdict": str(judgment.get("verdict") or "").strip(),
                "confidence": str(judgment.get("confidence") or "").strip(),
                "conflict_dimension": str(judgment.get("conflict_dimension") or "").strip(),
                "conflict_type": str(judgment.get("conflict_type") or "").strip(),
                "reason": str(judgment.get("reason") or "").strip(),
                "key_differences": key_differences_text,
                "doc_id_1": pick_candidate_value(candidate_row, ["docId1", "docId"]),
                "title_1": pick_candidate_value(candidate_row, ["title1", "title"]),
                "article_no_1": pick_candidate_value(candidate_row, ["articleNo1"]),
                "evidence_1": pick_candidate_value(candidate_row, ["evidence1", "articleText1"]),
                "doc_id_2": pick_candidate_value(candidate_row, ["docId2", "docId"]),
                "title_2": pick_candidate_value(candidate_row, ["title2", "title"]),
                "article_no_2": pick_candidate_value(candidate_row, ["articleNo2"]),
                "evidence_2": pick_candidate_value(candidate_row, ["evidence2", "articleText2"]),
                "subject": pick_candidate_value(candidate_row, ["subject"]),
                "action": pick_candidate_value(candidate_row, ["action"]),
                "object": pick_candidate_value(candidate_row, ["object"]),
                "condition_1": pick_candidate_value(candidate_row, ["cond1", "condition1"]),
                "condition_2": pick_candidate_value(candidate_row, ["cond2", "condition2"]),
                "constraint_1": pick_candidate_value(candidate_row, ["constraint1", "c1"]),
                "constraint_2": pick_candidate_value(candidate_row, ["constraint2", "c2"]),
                "error": str(judgment.get("error") or "").strip(),
            }
        )
    return rows


# 读取并拆分 SPARQL 查询块。
def load_sparql_queries(path: Path) -> list[dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    header_prefix: list[str] = []
    blocks: list[dict[str, str]] = []
    current_label = ""
    current_lines: list[str] = []
    query_started = False
    for line in lines:
        stripped = line.strip()
        if not query_started and (stripped.upper().startswith("PREFIX ") or stripped.upper().startswith("BASE ")):
            header_prefix.append(line)
            continue
        if stripped.startswith("# Query "):
            query_started = True
            if current_lines:
                body = "\n".join(current_lines).strip()
                if body:
                    blocks.append({"label": current_label or f"Query {len(blocks) + 1}", "body": body})
                current_lines = []
            current_label = stripped[1:].strip()
            continue
        if not query_started and not stripped:
            continue
        query_started = True
        current_lines.append(line)
    if current_lines:
        body = "\n".join(current_lines).strip()
        if body:
            blocks.append({"label": current_label or f"Query {len(blocks) + 1}", "body": body})
    queries: list[dict[str, str]] = []
    for idx, block in enumerate(blocks, start=1):
        body = str(block.get("body") or "").strip()
        if not body:
            continue
        if "SELECT" not in body.upper() and "ASK" not in body.upper():
            continue
        query_text = body if "PREFIX " in body.upper() else ("\n".join(header_prefix + [body]).strip())
        queries.append({"index": str(idx), "label": str(block.get("label") or f"Query {idx}"), "query": query_text})
    if queries:
        return queries
    source = path.read_text(encoding="utf-8").strip()
    if not source:
        return []
    return [{"index": "1", "label": "Query 1", "query": source}]


# 调用 Fuseki 执行单条 SPARQL 查询。
def run_sparql_query(endpoint: str, query: str, timeout_seconds: int) -> list[dict[str, str]]:
    response = requests.get(endpoint, params={"query": query}, headers={"Accept": "application/sparql-results+json"}, timeout=max(1, int(timeout_seconds)))
    if response.status_code != 200:
        raise ValueError(f"SPARQL 请求失败 status={response.status_code} body={preview_text(response.text)}")
    try:
        payload = response.json()
    except Exception as err:
        raise ValueError(f"SPARQL 返回非 JSON: {err}; body={preview_text(response.text)}") from err
    bindings = (((payload or {}).get("results") or {}).get("bindings") or [])
    if not isinstance(bindings, list):
        raise ValueError("SPARQL 返回 results.bindings 不是数组")
    rows: list[dict[str, str]] = []
    for item in bindings:
        if not isinstance(item, dict):
            continue
        row: dict[str, str] = {}
        for key, value in item.items():
            if isinstance(value, dict):
                row[str(key)] = str(value.get("value") or "").strip()
            else:
                row[str(key)] = str(value or "").strip()
        if row:
            rows.append(row)
    return rows


# 生成稳定候选ID，便于追踪与去重。
def build_candidate_id(label: str, row: dict[str, str]) -> str:
    source = f"{label}::{json.dumps(row, ensure_ascii=False, sort_keys=True)}"
    return hashlib.md5(source.encode("utf-8")).hexdigest()[:16]


# 生成单条查询候选快照文件名。
def build_query_candidates_path(output_dir: Path, query: dict[str, str]) -> Path:
    index = str(query.get("index") or "0").strip() or "0"
    label = str(query.get("label") or "").strip()
    suffix = hashlib.md5(label.encode("utf-8")).hexdigest()[:8]
    return output_dir / "candidates_by_query" / f"query_{index}_{suffix}.jsonl"


# 构造分页查询，优先替换查询块内部 LIMIT 作为分页点。
def build_paged_query(query_text: str, page_size: int, offset: int) -> str:
    lines = str(query_text or "").splitlines()
    limit_line_index = -1
    for idx, line in enumerate(lines):
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("LIMIT "):
            limit_line_index = idx
            break
    if limit_line_index >= 0:
        indent = lines[limit_line_index][: len(lines[limit_line_index]) - len(lines[limit_line_index].lstrip())]
        lines[limit_line_index] = f"{indent}LIMIT {page_size}"
        lines.insert(limit_line_index + 1, f"{indent}OFFSET {offset}")
        return "\n".join(lines)
    return f"{str(query_text or '').strip()}\nLIMIT {page_size}\nOFFSET {offset}"


# 写出当前阶段的汇总、重点结果和 CSV，便于中断后查看。
def refresh_judgment_outputs(output_dir: Path, candidates: list[dict[str, Any]], judgments: list[dict[str, Any]]) -> None:
    focus_jsonl_path = output_dir / "ontology_conflict_judgments_focus.jsonl"
    focus_csv_path = output_dir / "ontology_conflict_judgments_focus.csv"
    summary_path = output_dir / "ontology_conflict_judgments_summary.json"
    focus_judgments = filter_focus_judgments(judgments)
    write_jsonl_rows(focus_jsonl_path, focus_judgments)
    write_csv_rows(focus_csv_path, build_focus_csv_rows(focus_judgments), FOCUS_CSV_FIELDS)
    write_json(summary_path, build_summary(candidates, judgments))


# 调用 LLM 裁决单条候选是否冲突。
async def judge_single_candidate(llm: AsyncLLMClient, candidate: dict[str, Any], model: str, thinking_extra_body: dict[str, Any] | None, business_retries: int, sem: asyncio.Semaphore) -> dict[str, Any]:
    async with sem:
        candidate_id = str(candidate.get("candidate_id") or "").strip()
        label = str(candidate.get("query_label") or "").strip()
        row = candidate.get("row") or {}
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps({"query_label": label, "candidate": row}, ensure_ascii=False, indent=2)},
        ]
        last_error = ""
        attempts = max(1, int(business_retries))
        for attempt in range(1, attempts + 1):
            response = await llm.chat(messages=messages, model=model, temperature=0.0, extra_body=thinking_extra_body)
            content = extract_message_text(response)
            logger.info("冲突裁决模型返回 candidate_id=%s attempt=%s/%s content=%s", candidate_id, attempt, attempts, preview_text(content))
            try:
                payload = parse_judgment_payload(content)
                return {
                    "candidate_id": candidate_id,
                    "query_label": label,
                    "query_index": str(candidate.get("query_index") or "").strip(),
                    "row": row,
                    "verdict": payload.get("verdict"),
                    "confidence": payload.get("confidence"),
                    "conflict_dimension": payload.get("conflict_dimension"),
                    "conflict_type": payload.get("conflict_type"),
                    "reason": payload.get("reason"),
                    "key_differences": payload.get("key_differences", []),
                    "error": "",
                }
            except Exception as err:
                last_error = f"{err}; content={preview_text(content)}"
                logger.warning("冲突裁决解析失败 candidate_id=%s attempt=%s/%s err=%s", candidate_id, attempt, attempts, last_error)
        return {
            "candidate_id": candidate_id,
            "query_label": label,
            "query_index": str(candidate.get("query_index") or "").strip(),
            "row": row,
            "verdict": "uncertain",
            "confidence": "low",
            "conflict_dimension": "none",
            "conflict_type": "",
            "reason": "模型裁决失败，需人工复核",
            "key_differences": [],
            "error": last_error or "模型裁决失败",
        }


# 裁决一页候选并保持原始顺序。
async def judge_candidate_page(llm: AsyncLLMClient, candidates: list[dict[str, Any]], model: str, thinking_extra_body: dict[str, Any] | None, business_retries: int, concurrency: int) -> list[dict[str, Any]]:
    if not candidates:
        return []
    sem = asyncio.Semaphore(max(1, int(concurrency)))
    tasks = [
        (
            asyncio.create_task(judge_single_candidate(llm, candidate, model, thinking_extra_body, int(business_retries), sem)),
            str(candidate.get("candidate_id") or "").strip(),
        )
        for candidate in candidates
    ]
    return await gather_with_item_progress("09", tasks, len(candidates))


# 汇总裁决结果统计。
def build_summary(candidates: list[dict[str, Any]], judgments: list[dict[str, Any]]) -> dict[str, Any]:
    verdict_counter = Counter(str(row.get("verdict") or "").strip() for row in judgments)
    confidence_counter = Counter(str(row.get("confidence") or "").strip() for row in judgments)
    dimension_counter = Counter(str(row.get("conflict_dimension") or "").strip() for row in judgments)
    error_count = sum(1 for row in judgments if str(row.get("error") or "").strip())
    by_query: dict[str, int] = {}
    for row in judgments:
        label = str(row.get("query_label") or "").strip()
        by_query[label] = by_query.get(label, 0) + 1
    return {
        "candidate_count": len(candidates),
        "judgment_count": len(judgments),
        "error_count": error_count,
        "verdict_counts": dict(verdict_counter),
        "confidence_counts": dict(confidence_counter),
        "conflict_dimension_counts": dict(dimension_counter),
        "query_candidate_counts": by_query,
    }


async def async_main() -> int:
    args = build_parser().parse_args()
    load_env_files()
    sparql_file = Path(args.sparql_file).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    queries = load_sparql_queries(sparql_file)
    if not queries:
        raise ValueError("未从 SPARQL 文件解析出可执行查询")
    selected_labels = {item.strip() for item in str(args.query_labels or "").split(",") if item.strip()}
    if selected_labels:
        queries = [query for query in queries if str(query.get("label") or "").strip() in selected_labels]
        if not queries:
            raise ValueError("query_labels 过滤后无可执行查询")

    candidates_path = output_dir / "ontology_conflict_candidates.jsonl"
    judgments_path = output_dir / "ontology_conflict_judgments.jsonl"
    if not candidates_path.exists():
        candidates_path.parent.mkdir(parents=True, exist_ok=True)
        candidates_path.touch()
    if not judgments_path.exists():
        judgments_path.parent.mkdir(parents=True, exist_ok=True)
        judgments_path.touch()
    candidates = read_jsonl_rows(candidates_path)
    judgments = read_jsonl_rows(judgments_path)
    candidate_by_id = {str(row.get("candidate_id") or "").strip(): row for row in candidates if str(row.get("candidate_id") or "").strip()}
    judged_candidate_ids = {str(row.get("candidate_id") or "").strip() for row in judgments if str(row.get("candidate_id") or "").strip()}
    model = str(args.model or "").strip() or get_analysis_model()
    thinking_extra_body = build_thinking_extra_body(model, bool(args.enable_thinking))
    llm = AsyncLLMClient(config=ModelConfig(default_model=model), name="zhidu_ontology_conflict_judge")
    page_size = max(1, int(args.page_size))
    limit_per_query = max(0, int(args.limit_per_query))
    limit_candidates = max(0, int(args.limit_candidates))
    logger.info("开始分页冲突裁决 existing_candidate_count=%s existing_judgment_count=%s model=%s thinking_extra_body=%s page_size=%s", len(candidates), len(judgments), model, thinking_extra_body, page_size)
    for query in queries:
        label = str(query.get("label") or "").strip()
        query_candidates_path = build_query_candidates_path(output_dir, query)
        query_total = 0
        offset = 0
        while True:
            if limit_per_query > 0 and query_total >= limit_per_query:
                break
            if limit_candidates > 0 and len(candidates) >= limit_candidates:
                break
            current_page_size = page_size
            if limit_per_query > 0:
                current_page_size = min(current_page_size, limit_per_query - query_total)
            if limit_candidates > 0:
                current_page_size = min(current_page_size, limit_candidates - len(candidates))
            if current_page_size <= 0:
                break
            paged_query = build_paged_query(str(query.get("query") or ""), current_page_size, offset)
            logger.info("执行 SPARQL 分页查询 label=%s limit=%s offset=%s", label, current_page_size, offset)
            rows = run_sparql_query(str(args.sparql_endpoint or "").strip(), paged_query, int(args.timeout_seconds))
            logger.info("SPARQL 分页查询完成 label=%s offset=%s row_count=%s", label, offset, len(rows))
            if not rows:
                break
            page_candidates: list[dict[str, Any]] = []
            new_page_candidates: list[dict[str, Any]] = []
            label_for_id = str(query.get("label") or "").strip()
            query_index = str(query.get("index") or "").strip()
            for row in rows:
                candidate_id = build_candidate_id(label_for_id, row)
                if candidate_id in candidate_by_id:
                    page_candidates.append(candidate_by_id[candidate_id])
                    continue
                candidate = {"candidate_id": candidate_id, "query_label": label_for_id, "query_index": query_index, "row": row}
                candidate_by_id[candidate_id] = candidate
                candidates.append(candidate)
                page_candidates.append(candidate)
                new_page_candidates.append(candidate)
            page_candidates_to_judge = [candidate for candidate in page_candidates if str(candidate.get("candidate_id") or "").strip() not in judged_candidate_ids]
            if new_page_candidates:
                append_jsonl_rows(candidates_path, new_page_candidates)
                append_jsonl_rows(query_candidates_path, new_page_candidates)
            if page_candidates_to_judge:
                page_judgments = await judge_candidate_page(llm, page_candidates_to_judge, model, thinking_extra_body, int(args.business_retries), int(args.concurrency))
                judgments.extend(page_judgments)
                append_jsonl_rows(judgments_path, page_judgments)
                judged_candidate_ids.update(str(row.get("candidate_id") or "").strip() for row in page_judgments if str(row.get("candidate_id") or "").strip())
            refresh_judgment_outputs(output_dir, candidates, judgments)
            query_total += len(rows)
            offset += len(rows)
            if len(rows) < current_page_size:
                break
    refresh_judgment_outputs(output_dir, candidates, judgments)
    print(f"candidates: {candidates_path}")
    print(f"judgments: {judgments_path}")
    print(f"focus_judgments: {output_dir / 'ontology_conflict_judgments_focus.jsonl'}")
    print(f"focus_judgments_csv: {output_dir / 'ontology_conflict_judgments_focus.csv'}")
    print(f"summary: {output_dir / 'ontology_conflict_judgments_summary.json'}")
    print(f"candidate_count={len(candidates)}")
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
