from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

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


logger = get_logger(__name__)
MAX_LOG_PREVIEW = 2000

SYSTEM_PROMPT = """你正在参与公司内部规章制度冲突分析项目。

你当前的任务是阅读一份制度全文，生成用于后续制度冲突分析的文档级摘要。

摘要的作用是提供制度背景、适用范围和管理语境，但不能替代具体条款作为冲突证据。

请输出严格 JSON，不要输出解释文字，不要输出 Markdown 代码块。

输出格式如下：
{
  "purpose": "制度目的，尽量简洁",
  "scope": "适用范围",
  "main_subjects": ["主要责任主体"],
  "managed_objects": ["主要管理对象"],
  "key_processes": ["关键流程或管理环节"],
  "risk_points": ["风险点或控制重点"],
  "summary": "100到300字的整体摘要"
}

要求：
1. 不要编造原文没有的信息。
2. 如果某项无法从原文判断，填空字符串或空数组。
3. 保持客观，不判断制度之间是否冲突。"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="基于 effective_rules.jsonl 补充每份制度的文档级摘要。")
    parser.add_argument("--effective-rules-jsonl", required=True, help="effective_rules.jsonl 路径")
    parser.add_argument("--output-jsonl", required=True, help="输出 effective_rules_with_summary.jsonl 路径")
    parser.add_argument("--raw-text-dir", default="", help="可选：raw_texts 目录；当行内 raw_text_path 不存在时按 doc_id 回退读取")
    parser.add_argument("--model", default="", help="模型名，默认读取 OPENAI_ANALYSIS_MODEL")
    parser.add_argument("--concurrency", type=int, default=3, help="并发数，默认 3")
    parser.add_argument("--enable-thinking", action="store_true", help="显式开启模型思考")
    parser.add_argument("--business-retries", type=int, default=3, help="模型返回非JSON时的业务重试次数，默认 3")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 行，0 表示全部")
    return parser


# 加载 server/.env，便于脚本直接运行。
def load_env_files() -> None:
    if load_dotenv is None:
        return
    load_dotenv()
    server_env = SERVER_ROOT / ".env"
    if server_env.is_file():
        load_dotenv(server_env, override=False)


def load_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if text:
            rows.append(json.loads(text))
    return rows


def write_jsonl_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file_obj:
        for row in rows:
            file_obj.write(json.dumps(row, ensure_ascii=False) + "\n")


def preview_text(text: str, limit: int = MAX_LOG_PREVIEW) -> str:
    source = str(text or "").strip()
    if len(source) <= limit:
        return source
    return f"{source[:limit]}... [truncated {len(source) - limit} chars]"


def resolve_raw_text_path(row: dict[str, Any], raw_text_dir: Path | None) -> Path | None:
    raw_text_path = str(row.get("raw_text_path") or "").strip()
    if raw_text_path:
        path = Path(raw_text_path).expanduser().resolve()
        if path.is_file():
            return path
    if raw_text_dir is None:
        return None
    doc_id = str(row.get("doc_id") or "").strip()
    if not doc_id:
        return None
    path = raw_text_dir / f"{doc_id}.txt"
    return path if path.is_file() else None


def build_user_prompt(row: dict[str, Any], raw_text: str) -> str:
    title = str(row.get("title") or "").strip()
    category = str(row.get("category") or "").strip()
    file_name = str(row.get("file_name") or "").strip()
    return f"制度标题：{title}\n制度分类：{category}\n原始文件名：{file_name}\n\n制度全文如下：\n{raw_text}"


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
        except Exception:
            return None
    return None


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


# 读取模型返回正文，content 为空时兼容 reasoning 字段。
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
            logger.warning("制度摘要 content 为空，回退字段=%s", field)
            return fallback
    return ""


def extract_json_fragment(text: str) -> str:
    source = str(text or "").strip()
    if not source:
        return ""
    start = source.find("{")
    if start < 0:
        return ""
    for end in range(len(source), start, -1):
        candidate = source[start:end].strip()
        if not candidate or candidate[-1] != "}":
            continue
        try:
            json.loads(candidate)
            return candidate
        except Exception:
            continue
    return ""


def normalize_text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item or "").strip()]


def parse_summary_payload(text: str) -> dict[str, Any]:
    fragment = extract_json_fragment(text)
    if not fragment:
        raise ValueError("模型返回中未找到 JSON 对象")
    payload = json.loads(fragment)
    if not isinstance(payload, dict):
        raise ValueError("模型返回不是 JSON 对象")
    return {
        "purpose": str(payload.get("purpose") or "").strip(),
        "scope": str(payload.get("scope") or "").strip(),
        "main_subjects": normalize_text_list(payload.get("main_subjects")),
        "managed_objects": normalize_text_list(payload.get("managed_objects")),
        "key_processes": normalize_text_list(payload.get("key_processes")),
        "risk_points": normalize_text_list(payload.get("risk_points")),
        "summary": str(payload.get("summary") or "").strip(),
    }


def build_error_summary(error: str) -> dict[str, Any]:
    return {"purpose": "", "scope": "", "main_subjects": [], "managed_objects": [], "key_processes": [], "risk_points": [], "summary": "", "error": error}


# 为单份制度补充文档级摘要。
async def summarize_single_row(llm: AsyncLLMClient, row: dict[str, Any], raw_text_dir: Path | None, model: str, thinking_extra_body: dict[str, Any] | None, business_retries: int, sem: asyncio.Semaphore) -> dict[str, Any]:
    async with sem:
        result = dict(row)
        doc_id = str(row.get("doc_id") or "").strip()
        raw_text_path = resolve_raw_text_path(row, raw_text_dir)
        if raw_text_path is None:
            result["document_summary"] = build_error_summary("raw_text_missing")
            result["summary_status"] = "raw_text_missing"
            return result
        raw_text = raw_text_path.read_text(encoding="utf-8")
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": build_user_prompt(row, raw_text)}]
        last_error = ""
        last_content = ""
        for attempt in range(1, max(1, int(business_retries)) + 1):
            try:
                resp = await llm.chat(messages=messages, model=model, temperature=0.0, extra_body=thinking_extra_body)
                content = extract_message_text(resp)
                last_content = content
                logger.info("制度摘要模型返回 doc_id=%s attempt=%s content=%s", doc_id, attempt, preview_text(content))
                result["document_summary"] = parse_summary_payload(content)
                result["summary_status"] = "ok"
                result["summary_model"] = model
                result["summary_attempt_count"] = attempt
                return result
            except Exception as err:  # pylint: disable=broad-except
                last_error = f"{err}; 模型输出预览: {preview_text(last_content)}"
                logger.warning("制度摘要解析失败 doc_id=%s file=%s attempt=%s err=%s", doc_id, row.get("file_name", ""), attempt, last_error)
        result["document_summary"] = build_error_summary(last_error or "unknown_error")
        result["summary_status"] = "error"
        result["summary_model"] = model
        result["summary_error"] = last_error
        return result


def build_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("summary_status") or "unknown").strip()
        status_counts[status] = status_counts.get(status, 0) + 1
    return {"document_count": len(rows), "summary_status_counts": status_counts}


async def async_main() -> int:
    args = build_parser().parse_args()
    load_env_files()
    input_path = Path(args.effective_rules_jsonl).expanduser().resolve()
    output_path = Path(args.output_jsonl).expanduser().resolve()
    raw_text_dir = Path(args.raw_text_dir).expanduser().resolve() if str(args.raw_text_dir or "").strip() else None
    rows = load_jsonl_rows(input_path)
    if int(args.limit) > 0:
        rows = rows[: int(args.limit)]
    model = str(args.model or "").strip() or get_analysis_model()
    thinking_extra_body = build_thinking_extra_body(model, bool(args.enable_thinking))
    llm = AsyncLLMClient(config=ModelConfig(default_model=model), name="zhidu_document_summary")
    sem = asyncio.Semaphore(max(1, int(args.concurrency)))
    logger.info("开始补充制度摘要 document_count=%s model=%s output=%s", len(rows), model, output_path)
    tasks = [asyncio.create_task(summarize_single_row(llm, row, raw_text_dir, model, thinking_extra_body, int(args.business_retries), sem)) for row in rows]
    result_rows = await asyncio.gather(*tasks)
    write_jsonl_rows(output_path, result_rows)
    summary_path = output_path.with_name(f"{output_path.stem}_summary.json")
    summary_path.write_text(json.dumps(build_summary(result_rows), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"jsonl 输出: {output_path}")
    print(f"summary 输出: {summary_path}")
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
