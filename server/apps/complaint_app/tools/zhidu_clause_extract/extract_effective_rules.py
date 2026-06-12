from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd

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
SELF_MANAGEMENT_RE = re.compile(
    r"(本(?:办法|制度|细则|规定|规则).{0,40}(?:负责解释|解释)|"
    r"(?:解释权|修订权).{0,40}(?:归|由|属于|负责)|"
    r"自.{0,30}(?:起|之日起)(?:施行|实施|执行)|"
    r"(?:同时|同步)?(?:废止|失效|停止执行).{0,60}(?:办法|制度|细则|规定|规则|文件)|"
    r"原.{0,60}(?:办法|制度|细则|规定|规则|文件).{0,20}(?:废止|失效|停止执行))"
)

SYSTEM_PROMPT = """你正在参与一个“公司内部规章制度冲突分析”项目。

项目目标是：
后续需要比对不同规章制度之间是否存在冲突。
但在正式比对之前，先要从单份制度中筛选出“真正有约束意义、值得参与冲突分析的条款”，并排除大量无关内容，降低后续分析噪音。

你当前的任务不是判断冲突，而是对下面这份制度文本做预处理：
把条款分成“有效规则条款”和“无效条款”。

分类标准：

一、valid_rules：可用于后续冲突分析的条款
这类条款通常包含明确的约束、要求、权限、流程、条件、时限、金额、责任主体、审批要求、例外条件等。
例如：
- 应当、必须、不得、禁止、严禁、可以、可
- 负责、审批、备案、报送、执行、移交、归档、保存
- 在多少时间内完成
- 超过多少金额需要什么审批
- 哪个部门/岗位负责什么事项
- 哪种条件下允许例外

二、invalid_rules：不适合用于后续冲突分析的条款
这类条款通常是背景或说明性内容，例如：
- 制定目的、指导思想、总体原则、意义说明
- 会议规则中的组织说明、解释权说明
- “本规则自发布之日起施行”
- “同时废止某文件”
- 一般性通知、背景介绍、红头文件说明

请注意：
1. 你的任务只是筛选哪些条款有分析价值，不需要判断是否冲突。
2. 只要条款中有明确约束或执行要求，就应优先归入 valid_rules。
3. 如果条款主要是说明性内容，即使看起来正式，也归入 invalid_rules。

输出要求：
1. 只输出 JSON
2. 不要输出任何解释性文字
3. 格式如下：
{
  "valid_rules": [
    {
      "article_no": "",
      "text": ""
    }
  ],
  "invalid_rules": [
    {
      "article_no": "",
      "text": ""
    }
  ]
}"""

CLAUSE_CLASSIFY_PROMPT = """你正在参与一个“公司内部规章制度冲突分析”项目。

项目目标是：
后续需要比对不同规章制度之间是否存在冲突。
但在正式比对之前，先要从已经切分好的条款中筛选出“真正有约束意义、值得参与冲突分析的条款”，并排除无关内容，降低后续分析噪音。

你当前的任务不是重新切分条款，也不是改写条款，而是对输入的条款列表做分类。

分类标准：

一、valid_clause_indexes：可用于后续冲突分析的条款序号
这类条款通常包含明确的约束、要求、权限、流程、条件、时限、金额、责任主体、审批要求、例外条件等。
例如：
- 应当、必须、不得、禁止、严禁、可以、可
- 负责、审批、备案、报送、执行、移交、归档、保存
- 在多少时间内完成
- 超过多少金额需要什么审批
- 哪个部门/岗位负责什么事项
- 哪种条件下允许例外

二、invalid_clause_indexes：不适合用于后续冲突分析的条款序号
这类条款通常是背景或说明性内容，例如：
- 制定目的、指导思想、总体原则、意义说明
- 纯粹的解释权、修订权、生效日期、废止说明、附件更新、发布实施、版本管理
- 一般性通知、背景介绍、红头文件说明

请注意：
1. 输入条款已经人工确认过边界，你不能重新拆分、合并、改写条款。
2. 你的输出只能引用输入中的 clause_index。
3. 只要条款中有明确业务约束或执行要求，就应优先归入 valid_clause_indexes。
4. 文件自身管理内容优先归入 invalid_clause_indexes，即使出现“负责”“实施”等词也不能因为这些词归入 valid_clause_indexes。
5. 文件自身管理内容包括：解释权、修订权、生效日期、施行日期、实施日期、废止原制度、版本管理、附件更新等。

输出要求：
1. 只输出 JSON
2. 不要输出任何解释性文字
3. 格式如下：
{
  "valid_clause_indexes": [1, 2],
  "invalid_clause_indexes": [3, 4]
}"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从制度原文中提取有效规则条款与无效条款。")
    parser.add_argument("--documents-json", required=True, help="documents.jsonl 或 documents_mapped.jsonl 路径")
    parser.add_argument("--raw-text-dir", default="", help="raw_texts 目录路径；未传 --clauses-jsonl 时使用")
    parser.add_argument("--clauses-jsonl", default="", help="可选：clauses.jsonl 路径；传入后以人工确认条款为唯一来源，只做 valid/invalid 分类")
    parser.add_argument("--output-dir", required=True, help="输出目录")
    parser.add_argument("--category", default="", help="只处理指定分类")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 份文档，0 表示全部")
    parser.add_argument("--model", default="", help="模型名，默认读取 OPENAI_ANALYSIS_MODEL")
    parser.add_argument("--concurrency", type=int, default=3, help="并发数，默认 3")
    parser.add_argument("--enable-thinking", action="store_true", help="显式开启模型思考")
    parser.add_argument("--business-retries", type=int, default=3, help="模型返回非JSON时的业务重试次数，默认 3")
    return parser


# 加载环境变量，便于直接读取 server/.env。
def load_env_files() -> None:
    if load_dotenv is None:
        return
    load_dotenv()
    server_env = SERVER_ROOT / ".env"
    if server_env.is_file():
        load_dotenv(server_env, override=False)


def load_jsonl_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def write_jsonl_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file_obj:
        for row in rows:
            file_obj.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def filter_documents(document_rows: list[dict], category: str, limit: int) -> list[dict]:
    rows: list[dict] = []
    for row in document_rows:
        if category and str(row.get("category", "")).strip() != category:
            continue
        rows.append(row)
        if limit > 0 and len(rows) >= limit:
            break
    return rows


def group_clauses_by_doc_id(clause_rows: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for row in clause_rows:
        doc_id = str(row.get("doc_id") or "").strip()
        if not doc_id:
            continue
        grouped.setdefault(doc_id, []).append(row)
    return grouped


def resolve_raw_text_path(raw_text_dir: Path, doc_id: str) -> Path:
    return raw_text_dir / f"{doc_id}.txt"


def read_raw_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def build_user_prompt(document: dict, raw_text: str) -> str:
    title = str(document.get("title", "")).strip()
    category = str(document.get("category", "")).strip()
    return f"制度标题：{title}\n制度分类：{category}\n\n制度文本如下：\n{raw_text}"


def build_clause_classify_user_prompt(document: dict, clauses: list[dict]) -> str:
    title = str(document.get("title", "")).strip()
    category = str(document.get("category", "")).strip()
    lines = [f"制度标题：{title}", f"制度分类：{category}", "", "条款列表如下："]
    for index, clause in enumerate(clauses, start=1):
        article_no = str(clause.get("article_no") or "").strip()
        article_text = str(clause.get("article_text") or clause.get("text") or "").strip()
        lines.append(f"clause_index: {index}")
        lines.append(f"article_no: {article_no}")
        lines.append(f"article_text: {article_text}")
        lines.append("")
    return "\n".join(lines).strip()


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
        except Exception:
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
            logger.warning("有效规则提取 content 为空，回退字段=%s", field)
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


def parse_rules_payload(text: str) -> dict:
    fragment = extract_json_fragment(text)
    if not fragment:
        raise ValueError("模型返回中未找到 JSON 对象")
    payload = json.loads(fragment)
    if not isinstance(payload, dict):
        raise ValueError("模型返回不是 JSON 对象")
    valid_rules = payload.get("valid_rules")
    invalid_rules = payload.get("invalid_rules")
    if not isinstance(valid_rules, list) or not isinstance(invalid_rules, list):
        raise ValueError("模型返回缺少 valid_rules/invalid_rules 数组")
    return payload


def parse_clause_index_payload(text: str) -> dict:
    fragment = extract_json_fragment(text)
    if not fragment:
        raise ValueError("模型返回中未找到 JSON 对象")
    payload = json.loads(fragment)
    if not isinstance(payload, dict):
        raise ValueError("模型返回不是 JSON 对象")
    valid_indexes = payload.get("valid_clause_indexes")
    invalid_indexes = payload.get("invalid_clause_indexes")
    if not isinstance(valid_indexes, list) or not isinstance(invalid_indexes, list):
        raise ValueError("模型返回缺少 valid_clause_indexes/invalid_clause_indexes 数组")
    return payload


def normalize_index_items(items: list[Any], max_index: int) -> list[int]:
    indexes: list[int] = []
    seen: set[int] = set()
    for item in items:
        try:
            index = int(item)
        except Exception:
            continue
        if index < 1 or index > max_index or index in seen:
            continue
        seen.add(index)
        indexes.append(index)
    return indexes


def build_rules_from_clause_indexes(clauses: list[dict], indexes: list[int]) -> list[dict]:
    rules = []
    for index in indexes:
        clause = clauses[index - 1]
        article_no = str(clause.get("article_no") or "").strip()
        text = str(clause.get("article_text") or clause.get("text") or "").strip()
        if article_no or text:
            rules.append({"article_no": article_no, "text": text})
    return rules


def is_self_management_rule(rule: dict) -> bool:
    article_no = str(rule.get("article_no") or "").strip()
    text = str(rule.get("text") or "").strip()
    normalized = re.sub(r"\s+", "", f"{article_no}{text}")
    return bool(SELF_MANAGEMENT_RE.search(normalized))


def move_self_management_rules_to_invalid(valid_rules: list[dict], invalid_rules: list[dict]) -> tuple[list[dict], list[dict]]:
    filtered_valid: list[dict] = []
    merged_invalid = list(invalid_rules)
    invalid_keys = {(str(rule.get("article_no") or ""), str(rule.get("text") or "")) for rule in merged_invalid}
    for rule in valid_rules:
        if is_self_management_rule(rule):
            key = (str(rule.get("article_no") or ""), str(rule.get("text") or ""))
            if key not in invalid_keys:
                merged_invalid.append(rule)
                invalid_keys.add(key)
            logger.info("文件自身管理条款强制归为无效 article_no=%s text=%s", rule.get("article_no", ""), preview_text(str(rule.get("text", "")), 300))
            continue
        filtered_valid.append(rule)
    return filtered_valid, merged_invalid


def normalize_rule_items(items: list[Any]) -> list[dict]:
    normalized: list[dict] = []
    for item in items:
        if isinstance(item, dict):
            article_no = str(item.get("article_no", "")).strip()
            text = str(item.get("text", "")).strip()
        else:
            article_no = ""
            text = str(item or "").strip()
        if not article_no and not text:
            continue
        normalized.append({"article_no": article_no, "text": text})
    return normalized


def flatten_rules_for_excel(items: list[dict]) -> str:
    return json.dumps(items, ensure_ascii=False, indent=2)


def preview_text(text: str, limit: int = MAX_LOG_PREVIEW) -> str:
    source = str(text or "").strip()
    if len(source) <= limit:
        return source
    return f"{source[:limit]}... [truncated {len(source) - limit} chars]"


def build_parse_error_message(err: Exception, content: str) -> str:
    return f"{err}; 模型输出预览: {preview_text(content)}"


# 调用模型对单份制度做有效/无效条款分类。
async def analyze_single_document(llm: AsyncLLMClient, document: dict, raw_text_dir: Path, model: str, thinking_extra_body: dict[str, Any] | None, business_retries: int, sem: asyncio.Semaphore) -> dict:
    async with sem:
        doc_id = str(document.get("doc_id", "")).strip()
        raw_text_path = resolve_raw_text_path(raw_text_dir, doc_id)
        result = {
            "doc_id": doc_id,
            "category": str(document.get("category", "")).strip(),
            "title": str(document.get("title", "")).strip(),
            "file_name": str(document.get("file_name", "")).strip(),
            "file_path": str(document.get("file_path", "")).strip(),
            "primary_department": str(document.get("主责部门") or document.get("primary_department") or "").strip(),
            "policy_level": str(document.get("制度级别") or document.get("policy_level") or "").strip(),
            "raw_text_path": str(raw_text_path),
            "valid_rules": [],
            "invalid_rules": [],
            "valid_rule_count": 0,
            "invalid_rule_count": 0,
            "model": model,
            "status": "ok",
            "error": "",
            "exception_output": "",
            "original_text": "",
        }
        if not raw_text_path.is_file():
            result["status"] = "raw_text_missing"
            result["error"] = "raw_text_missing"
            return result
        raw_text = read_raw_text(raw_text_path)
        result["original_text"] = raw_text
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": build_user_prompt(document, raw_text)}]
        try:
            last_error = ""
            last_content = ""
            attempts = max(1, int(business_retries))
            for attempt in range(1, attempts + 1):
                resp = await llm.chat(messages=messages, model=model, temperature=0.0, extra_body=thinking_extra_body)
                content = extract_message_text(resp)
                last_content = content
                logger.info("有效规则提取模型返回 doc_id=%s attempt=%s/%s content=%s", doc_id, attempt, attempts, preview_text(content))
                try:
                    payload = parse_rules_payload(content)
                    valid_rules = normalize_rule_items(payload.get("valid_rules", []))
                    invalid_rules = normalize_rule_items(payload.get("invalid_rules", []))
                    valid_rules, invalid_rules = move_self_management_rules_to_invalid(valid_rules, invalid_rules)
                    result["valid_rules"] = valid_rules
                    result["invalid_rules"] = invalid_rules
                    result["valid_rule_count"] = len(valid_rules)
                    result["invalid_rule_count"] = len(invalid_rules)
                    result["attempt_count"] = attempt
                    return result
                except Exception as parse_err:  # pylint: disable=broad-except
                    last_error = build_parse_error_message(parse_err, content)
                    logger.warning("有效规则提取解析失败 doc_id=%s file=%s attempt=%s/%s err=%s", doc_id, result["file_name"], attempt, attempts, last_error)
            result["status"] = "error"
            result["error"] = last_error or "unknown_parse_error"
            result["exception_output"] = last_content
        except Exception as err:  # pylint: disable=broad-except
            logger.error("有效规则提取失败 doc_id=%s file=%s err=%s", doc_id, result["file_name"], err)
            result["status"] = "error"
            result["error"] = str(err)
        return result


async def analyze_single_document_from_clauses(llm: AsyncLLMClient, document: dict, clauses: list[dict], model: str, thinking_extra_body: dict[str, Any] | None, business_retries: int, sem: asyncio.Semaphore) -> dict:
    async with sem:
        doc_id = str(document.get("doc_id", "")).strip()
        result = {
            "doc_id": doc_id,
            "category": str(document.get("category", "")).strip(),
            "title": str(document.get("title", "")).strip(),
            "file_name": str(document.get("file_name", "")).strip(),
            "file_path": str(document.get("file_path", "")).strip(),
            "primary_department": str(document.get("主责部门") or document.get("primary_department") or "").strip(),
            "policy_level": str(document.get("制度级别") or document.get("policy_level") or "").strip(),
            "raw_text_path": "",
            "valid_rules": [],
            "invalid_rules": [],
            "valid_rule_count": 0,
            "invalid_rule_count": 0,
            "model": model,
            "status": "ok",
            "error": "",
            "exception_output": "",
            "original_text": "",
            "rule_source": "clauses_jsonl",
        }
        if not clauses:
            result["status"] = "clause_missing"
            result["error"] = "clause_missing"
            return result
        messages = [{"role": "system", "content": CLAUSE_CLASSIFY_PROMPT}, {"role": "user", "content": build_clause_classify_user_prompt(document, clauses)}]
        try:
            last_error = ""
            last_content = ""
            attempts = max(1, int(business_retries))
            for attempt in range(1, attempts + 1):
                resp = await llm.chat(messages=messages, model=model, temperature=0.0, extra_body=thinking_extra_body)
                content = extract_message_text(resp)
                last_content = content
                logger.info("有效规则条款分类模型返回 doc_id=%s attempt=%s/%s content=%s", doc_id, attempt, attempts, preview_text(content))
                try:
                    payload = parse_clause_index_payload(content)
                    valid_indexes = normalize_index_items(payload.get("valid_clause_indexes", []), len(clauses))
                    invalid_indexes = normalize_index_items(payload.get("invalid_clause_indexes", []), len(clauses))
                    valid_set = set(valid_indexes)
                    invalid_indexes = [index for index in invalid_indexes if index not in valid_set]
                    classified_set = valid_set | set(invalid_indexes)
                    missing_indexes = [index for index in range(1, len(clauses) + 1) if index not in classified_set]
                    if missing_indexes:
                        logger.warning("有效规则条款分类存在遗漏 doc_id=%s missing_indexes=%s", doc_id, missing_indexes)
                        invalid_indexes.extend(missing_indexes)
                    valid_rules = build_rules_from_clause_indexes(clauses, valid_indexes)
                    invalid_rules = build_rules_from_clause_indexes(clauses, invalid_indexes)
                    valid_rules, invalid_rules = move_self_management_rules_to_invalid(valid_rules, invalid_rules)
                    result["valid_rules"] = valid_rules
                    result["invalid_rules"] = invalid_rules
                    result["valid_rule_count"] = len(valid_rules)
                    result["invalid_rule_count"] = len(invalid_rules)
                    result["attempt_count"] = attempt
                    return result
                except Exception as parse_err:  # pylint: disable=broad-except
                    last_error = build_parse_error_message(parse_err, content)
                    logger.warning("有效规则条款分类解析失败 doc_id=%s file=%s attempt=%s/%s err=%s", doc_id, result["file_name"], attempt, attempts, last_error)
            result["status"] = "error"
            result["error"] = last_error or "unknown_parse_error"
            result["exception_output"] = last_content
        except Exception as err:  # pylint: disable=broad-except
            logger.error("有效规则条款分类失败 doc_id=%s file=%s err=%s", doc_id, result["file_name"], err)
            result["status"] = "error"
            result["error"] = str(err)
        return result


def build_excel_rows(result_rows: list[dict]) -> list[dict]:
    excel_rows: list[dict] = []
    for row in result_rows:
        excel_rows.append(
            {
                "doc_id": row.get("doc_id", ""),
                "category": row.get("category", ""),
                "title": row.get("title", ""),
                "file_name": row.get("file_name", ""),
                "file_path": row.get("file_path", ""),
                "primary_department": row.get("primary_department", ""),
                "policy_level": row.get("policy_level", ""),
                "raw_text_path": row.get("raw_text_path", ""),
                "valid_rule_count": row.get("valid_rule_count", 0),
                "invalid_rule_count": row.get("invalid_rule_count", 0),
                "status": row.get("status", ""),
                "error": row.get("error", ""),
                "exception_output": row.get("exception_output", ""),
                "rule_source": row.get("rule_source", ""),
                "valid_rules_json": flatten_rules_for_excel(list(row.get("valid_rules", []))),
                "invalid_rules_json": flatten_rules_for_excel(list(row.get("invalid_rules", []))),
                "original_text": row.get("original_text", ""),
            }
        )
    return excel_rows


def write_excel(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    df.to_excel(path, index=False)


def build_summary(rows: list[dict]) -> dict:
    status_counts: dict[str, int] = {}
    total_valid_rules = 0
    total_invalid_rules = 0
    for row in rows:
        status = str(row.get("status", "")).strip() or "unknown"
        status_counts[status] = status_counts.get(status, 0) + 1
        total_valid_rules += int(row.get("valid_rule_count", 0) or 0)
        total_invalid_rules += int(row.get("invalid_rule_count", 0) or 0)
    return {
        "document_count": len(rows),
        "status_counts": status_counts,
        "total_valid_rules": total_valid_rules,
        "total_invalid_rules": total_invalid_rules,
    }


async def async_main() -> int:
    args = build_parser().parse_args()
    load_env_files()
    documents_path = Path(args.documents_json).expanduser().resolve()
    clauses_path = Path(args.clauses_jsonl).expanduser().resolve() if str(args.clauses_jsonl or "").strip() else None
    raw_text_dir = Path(args.raw_text_dir).expanduser().resolve() if str(args.raw_text_dir or "").strip() else None
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    document_rows = load_jsonl_rows(documents_path)
    selected_documents = filter_documents(document_rows, args.category.strip(), args.limit)
    clauses_by_doc_id = group_clauses_by_doc_id(load_jsonl_rows(clauses_path)) if clauses_path else {}
    model = str(args.model or "").strip() or get_analysis_model()
    thinking_extra_body = build_thinking_extra_body(model, bool(args.enable_thinking))
    llm = AsyncLLMClient(config=ModelConfig(default_model=model), name="zhidu_effective_rules")
    sem = asyncio.Semaphore(max(1, int(args.concurrency)))

    if clauses_path is None and raw_text_dir is None:
        raise ValueError("必须传入 --clauses-jsonl 或 --raw-text-dir")
    logger.info("开始提取有效规则 文档数=%s model=%s clauses_jsonl=%s thinking_extra_body=%s business_retries=%s", len(selected_documents), model, clauses_path or "", thinking_extra_body, args.business_retries)
    if clauses_path is not None:
        tasks = [asyncio.create_task(analyze_single_document_from_clauses(llm, document, clauses_by_doc_id.get(str(document.get("doc_id", "")).strip(), []), model, thinking_extra_body, int(args.business_retries), sem)) for document in selected_documents]
    else:
        tasks = [asyncio.create_task(analyze_single_document(llm, document, raw_text_dir, model, thinking_extra_body, int(args.business_retries), sem)) for document in selected_documents]
    result_rows = await asyncio.gather(*tasks)

    jsonl_rows = []
    for row in result_rows:
        jsonl_row = dict(row)
        jsonl_row.pop("original_text", None)
        jsonl_rows.append(jsonl_row)

    output_jsonl = output_dir / "effective_rules.jsonl"
    output_xlsx = output_dir / "effective_rules.xlsx"
    output_summary = output_dir / "effective_rules_summary.json"
    write_jsonl_rows(output_jsonl, jsonl_rows)
    write_excel(output_xlsx, build_excel_rows(result_rows))
    write_json(output_summary, build_summary(result_rows))

    print(f"jsonl 输出: {output_jsonl}")
    print(f"excel 输出: {output_xlsx}")
    print(f"summary 输出: {output_summary}")
    print(f"文档数: {len(result_rows)}")
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
