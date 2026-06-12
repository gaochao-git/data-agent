from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from itertools import combinations
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
DEFAULT_INTER_CATEGORY_PAIRS = [
    ("公司治理", "法务管理"),
    ("内部信息传递", "法务管理"),
    ("内部信息传递", "公司治理"),
    ("内部审计", "法务管理"),
    ("人力资源管理", "外包管理"),
    ("外包管理", "信息科技风险管理"),
    ("财务管理", "预算管理"),
    ("财务管理", "资金管理"),
    ("财务管理", "资产管理"),
    ("财务管理", "采购管理"),
    ("预算管理", "采购管理"),
    ("资产管理", "采购管理"),
    ("采购管理", "信息科技运行管理"),
    ("法务管理", "采购管理"),
    ("运营管理", "财务管理"),
    ("风险管理", "产品管理"),
    ("风险管理", "运营管理"),
    ("风险管理", "合作机构管理"),
    ("风险管理", "法务管理"),
    ("风险管理", "业务连续性管理"),
    ("工程项目", "采购管理"),
    ("工程项目", "财务管理"),
    ("工程项目", "预算管理"),
    ("工程项目", "资金管理"),
    ("工程项目", "资产管理"),
]

SYSTEM_PROMPT = """你正在参与公司内部规章制度冲突分析项目。

现在给你两份同一分类或重点相关分类下的制度，它们已经提前过滤掉了无关噪音，只保留了“有约束意义的规则条款（valid_rules）”。

你当前的任务不是直接输出最终冲突结论，也不是定位具体条款，
而是做“制度对初筛”：
判断这两份制度是否存在“值得继续下钻分析的冲突可能”。

请重点判断以下情况是否存在。只有两份制度针对同一事项、同一对象或同一管理动作，且要求不一致或难以同时满足时，才认为存在冲突可能：
1. 权责维度冲突：职责归属、审批层级、决策权限等关于“谁来做、谁负责、谁决策”的要求不一致。
2. 流程维度冲突：业务流程顺序、流程路径、关键节点、前置条件等要求相悖；如输入中包含流程图文字说明，也需判断制度文字与流程图内容是否不一致。
3. 标准与指标维度冲突：时限、数量、金额、限额、阈值等标准不一致。
4. 定义与范围维度冲突：术语定义不一致，或适用范围在重叠部分存在不同要求；单纯范围重叠或空白不直接视为冲突。
5. 规范要求维度冲突：同一事项下禁止/允许、必须/可选、应当/不得等规则口径相反或互斥。
6. 其他明显的制度要求冲突。

请输出 JSON，格式如下：
{
  "possible_conflict": true,
  "confidence": "high",
  "conflict_types": [
    "审批层级冲突",
    "职责归属冲突"
  ],
  "topics": [
    "采购审批",
    "职责分工"
  ],
  "reason": "两份制度均涉及同类事项，且在审批层级和职责归属上存在不一致可能，建议继续做条款级分析。"
}

如果判断基本不存在冲突可能，则输出：
{
  "possible_conflict": false,
  "confidence": "low",
  "conflict_types": [],
  "topics": [],
  "reason": "未发现明显冲突可能。"
}

输出要求：
1. 只输出 JSON
2. 不要输出任何解释性文字
3. 不要输出代码块标记
4. confidence 只能取值：high、medium、low"""

CLAUSE_LOCATE_PROMPT = """你正在参与公司内部规章制度冲突分析项目。

现在给你两份制度，以及它们的有效规则条款（valid_rules）。
这两份制度只是因为“需要进一步复核”才进入当前步骤，这并不代表它们一定存在条款级冲突。

你的当前任务是独立判断：
这两份制度里，哪些条款对存在明确或较明显的冲突可能。

请重点寻找以下类型的条款冲突：
1. 权责维度冲突：主要涉及“谁来做、谁负责、谁决策”。包括职责归属界定不清或重叠、审批层级不一致、决策权限冲突等。注意：职责重叠本身不必然构成冲突，只有同一事项下主责/协办/最终负责方或决策方式存在不一致、互斥或难以同时满足时，才输出候选。
2. 流程维度冲突：主要涉及操作的顺序、路径和节点。包括业务流程顺序相悖、制度内文字与流程图内容不一致、前置条件冲突等。注意：只有输入条款中包含流程图或流程节点的文字信息时，才判断图文不一致；普通流程差异或补充说明不要当作冲突。
3. 标准与指标维度冲突：包括时限标准不同、数量或限额不同、金额或阈值冲突等。
4. 定义与范围维度冲突：包括术语定义不一致、适用范围在重叠部分存在不同要求。注意：单纯适用范围重叠或空白通常只是管理覆盖问题，不要直接当作条款冲突。
5. 规范要求维度冲突：包括禁止/允许口径不一致、必须/可选口径不一致、应当/不得等同一事项处理要求相反或互斥。
6. 其他明显的制度要求冲突。

请注意：
1. 必须先判断两条条款是否针对同一事项、同一对象或同一管理动作；如果不是在约束同一事项，就不要当作冲突候选
2. 只有在两条条款针对同一事项、且要求明显不一致时，才输出为冲突候选
3. 如果两条条款只是主题相关、互为补充、适用范围不同、一般规定与特殊规定并存，或上下位规则关系明确，不要当作冲突候选
4. 如果没有明确或较明显的条款级冲突，应输出空列表，不要为了输出结果而勉强给出候选
5. 不要默认给出 high，必须根据证据强弱独立判断

confidence 判定标准：
- high：两条条款针对同一事项，要求直接矛盾或明显不一致，冲突证据充分
- medium：两条条款较可能冲突，但仍依赖进一步人工复核
- low：只有较弱疑似，证据不足；除非非常必要，否则不建议输出 low 候选

请输出 JSON，格式如下：
{
  "conflict_candidates": [
    {
      "left_article_no": "第四条",
      "left_text": "......",
      "right_article_no": "第六条",
      "right_text": "......",
      "conflict_type": "审批层级不一致",
      "reason": "两条都针对同一事项审批，但审批层级分别不同。",
      "confidence": "medium"
    }
  ]
}

如果未发现明确的条款级冲突候选，则输出：
{
  "conflict_candidates": []
}

输出要求：
1. 只输出 JSON
2. 不要输出任何解释性文字
3. 不要输出代码块标记
4. confidence 只能取值：high、medium、low"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="基于有效规则条款做版块内/重点版块间制度对冲突初筛。")
    parser.add_argument("--effective-rules-jsonl", required=True, help="effective_rules.jsonl 路径")
    parser.add_argument("--output-dir", required=True, help="输出目录")
    parser.add_argument("--category", default="", help="只处理指定版块比较键")
    parser.add_argument("--limit-categories", type=int, default=0, help="只处理前 N 个版块比较键，0 表示全部")
    parser.add_argument("--limit-pairs", type=int, default=0, help="只处理前 N 对制度，0 表示全部")
    parser.add_argument("--model", default="", help="模型名，默认读取 OPENAI_ANALYSIS_MODEL")
    parser.add_argument("--concurrency", type=int, default=3, help="并发数，默认 3")
    parser.add_argument("--enable-thinking", action="store_true", help="显式开启模型思考")
    parser.add_argument("--business-retries", type=int, default=3, help="模型返回非JSON时的业务重试次数，默认 3")
    parser.add_argument("--include-uncategorized", action="store_true", help="是否纳入 未分类 文档，默认不纳入")
    parser.add_argument("--max-chars-per-doc", type=int, default=12000, help="每份制度发送给模型的最大字符数，默认 12000")
    parser.add_argument("--run-inter-category", action="store_true", help="是否开启重点版块对的组间比较，默认关闭")
    parser.add_argument("--skip-intra-category", action="store_true", help="跳过组内比较；通常与 --run-inter-category 一起用于只跑组间")
    parser.add_argument("--inter-category-pairs-json", default="", help="重点版块对配置 JSON 文件路径；不传则使用内置默认版块对")
    parser.add_argument("--force-rerun", action="store_true", help="强制重新分析已跑过的制度对，默认跳过已分析结果")
    parser.add_argument("--run-high-clause-locate", action="store_true", help="是否对 high 结果继续做条款级定位")
    parser.add_argument("--stage2-min-confidence", choices=["high", "medium", "low"], default="high", help="二筛最低置信度门槛，默认 high")
    parser.add_argument("--max-clause-candidates", type=int, default=10, help="每对 high 制度最多输出多少条条款冲突候选，默认 10")
    parser.add_argument("--export-clause-candidates-only", action="store_true", help="只从 output-dir/high_conflict_clauses.jsonl 导出候选条款级 Excel，不重新调用模型")
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


def load_jsonl_rows_if_exists(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return load_jsonl_rows(path)


def write_jsonl_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file_obj:
        for row in rows:
            file_obj.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_excel(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_excel(path, index=False)


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
            logger.warning("制度对初筛 content 为空，回退字段=%s", field)
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


def parse_pair_payload(text: str) -> dict:
    fragment = extract_json_fragment(text)
    if not fragment:
        raise ValueError("模型返回中未找到 JSON 对象")
    payload = json.loads(fragment)
    if not isinstance(payload, dict):
        raise ValueError("模型返回不是 JSON 对象")
    if not isinstance(payload.get("possible_conflict"), bool):
        raise ValueError("模型返回缺少 possible_conflict 布尔值")
    confidence = str(payload.get("confidence", "")).strip().lower()
    if confidence not in {"high", "medium", "low"}:
        raise ValueError("模型返回 confidence 非 high/medium/low")
    conflict_types = payload.get("conflict_types")
    topics = payload.get("topics")
    if not isinstance(conflict_types, list) or not isinstance(topics, list):
        raise ValueError("模型返回缺少 conflict_types/topics 数组")
    return payload


def parse_clause_locate_payload(text: str) -> dict:
    fragment = extract_json_fragment(text)
    if not fragment:
        raise ValueError("模型返回中未找到 JSON 对象")
    payload = json.loads(fragment)
    if not isinstance(payload, dict):
        raise ValueError("模型返回不是 JSON 对象")
    candidates = payload.get("conflict_candidates")
    if not isinstance(candidates, list):
        raise ValueError("模型返回缺少 conflict_candidates 数组")
    return payload


def normalize_compare_key(value: Any) -> str:
    return "".join(str(value or "").split())


def filter_effective_documents(rows: list[dict], category: str, include_uncategorized: bool) -> list[dict]:
    filtered: list[dict] = []
    category_key = normalize_compare_key(category)
    for row in rows:
        status = str(row.get("status", "")).strip()
        valid_rule_count = int(row.get("valid_rule_count", 0) or 0)
        row_category = normalize_compare_key(row.get("category", ""))
        if status != "ok" or valid_rule_count <= 0:
            continue
        if category_key and row_category != category_key:
            continue
        if not include_uncategorized and row_category == "未分类":
            continue
        row["category"] = row_category
        filtered.append(row)
    return filtered


def group_by_category(rows: list[dict], limit_categories: int) -> list[tuple[str, list[dict]]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[normalize_compare_key(row.get("category", ""))].append(row)
    items = sorted(grouped.items(), key=lambda item: item[0])
    if limit_categories > 0:
        items = items[:limit_categories]
    return items


def load_inter_category_pairs(config_path: str) -> list[tuple[str, str]]:
    if not str(config_path or "").strip():
        return [(normalize_compare_key(left), normalize_compare_key(right)) for left, right in DEFAULT_INTER_CATEGORY_PAIRS]
    path = Path(config_path).expanduser().resolve()
    raw = json.loads(path.read_text(encoding="utf-8"))
    pairs: list[tuple[str, str]] = []
    if not isinstance(raw, list):
        raise ValueError("inter-category-pairs-json 顶层必须是数组")
    for item in raw:
        if isinstance(item, list) and len(item) == 2:
            left = normalize_compare_key(item[0])
            right = normalize_compare_key(item[1])
        elif isinstance(item, dict):
            left = normalize_compare_key(item.get("left") or item.get("category_a") or "")
            right = normalize_compare_key(item.get("right") or item.get("category_b") or "")
        else:
            continue
        if left and right:
            pairs.append((left, right))
    return pairs


def build_pair_key_from_ids(left_doc_id: str, right_doc_id: str) -> tuple[str, str]:
    left = str(left_doc_id or "").strip()
    right = str(right_doc_id or "").strip()
    return tuple(sorted([left, right]))


def build_pair_key(row: dict) -> tuple[str, str]:
    return build_pair_key_from_ids(str(row.get("left_doc_id", "")).strip(), str(row.get("right_doc_id", "")).strip())


def collect_processed_pair_keys(rows: list[dict]) -> set[tuple[str, str]]:
    return {build_pair_key(row) for row in rows if str(row.get("left_doc_id", "")).strip() and str(row.get("right_doc_id", "")).strip()}


def is_primary_secondary_pair(left: dict, right: dict) -> bool:
    left_level = str(left.get("policy_level") or left.get("制度级别") or "").strip()
    right_level = str(right.get("policy_level") or right.get("制度级别") or "").strip()
    return ("一级" in left_level and "二级" in right_level) or ("二级" in left_level and "一级" in right_level)


def build_intra_pair_tasks(grouped_rows: list[tuple[str, list[dict]]], limit_pairs: int, processed_keys: set[tuple[str, str]]) -> list[tuple[str, str, str, dict, dict]]:
    pairs: list[tuple[str, str, str, dict, dict]] = []
    for category, docs in grouped_rows:
        docs_sorted = sorted(docs, key=lambda row: (str(row.get("title", "")), str(row.get("doc_id", ""))))
        for left, right in combinations(docs_sorted, 2):
            if is_primary_secondary_pair(left, right):
                continue
            pair_key = build_pair_key_from_ids(str(left.get("doc_id", "")), str(right.get("doc_id", "")))
            if pair_key in processed_keys:
                continue
            pairs.append(("intra", category, category, left, right))
            if limit_pairs > 0 and len(pairs) >= limit_pairs:
                return pairs
    return pairs


def build_inter_pair_tasks(grouped_rows: list[tuple[str, list[dict]]], category_pairs: list[tuple[str, str]], limit_pairs: int, processed_keys: set[tuple[str, str]]) -> list[tuple[str, str, str, dict, dict]]:
    pairs: list[tuple[str, str, str, dict, dict]] = []
    docs_by_category = {category: docs for category, docs in grouped_rows}
    for left_category, right_category in category_pairs:
        left_docs = sorted(docs_by_category.get(left_category, []), key=lambda row: (str(row.get("title", "")), str(row.get("doc_id", ""))))
        right_docs = sorted(docs_by_category.get(right_category, []), key=lambda row: (str(row.get("title", "")), str(row.get("doc_id", ""))))
        if not left_docs or not right_docs:
            continue
        for left in left_docs:
            for right in right_docs:
                pair_key = build_pair_key_from_ids(str(left.get("doc_id", "")), str(right.get("doc_id", "")))
                if pair_key in processed_keys:
                    continue
                pairs.append(("inter", left_category, right_category, left, right))
                if limit_pairs > 0 and len(pairs) >= limit_pairs:
                    return pairs
    return pairs


def build_rules_text(document: dict, max_chars_per_doc: int) -> str:
    valid_rules = list(document.get("valid_rules", []))
    lines = []
    for item in valid_rules:
        article_no = str(item.get("article_no", "")).strip()
        text = str(item.get("text", "")).strip()
        line = f"{article_no} {text}".strip()
        if line:
            lines.append(line)
    merged = "\n".join(lines).strip()
    if max_chars_per_doc > 0 and len(merged) > max_chars_per_doc:
        return f"{merged[:max_chars_per_doc]}\n[TRUNCATED]"
    return merged


def build_document_summary_text(document: dict) -> str:
    summary = document.get("document_summary")
    if not isinstance(summary, dict):
        return ""
    parts = []
    for label, key in (("制度目的", "purpose"), ("适用范围", "scope"), ("整体摘要", "summary")):
        value = str(summary.get(key) or "").strip()
        if value:
            parts.append(f"{label}：{value}")
    for label, key in (("主要责任主体", "main_subjects"), ("主要管理对象", "managed_objects"), ("关键流程", "key_processes"), ("风险/控制重点", "risk_points")):
        value = summary.get(key)
        if isinstance(value, list):
            items = [str(item).strip() for item in value if str(item or "").strip()]
            if items:
                parts.append(f"{label}：{'、'.join(items)}")
    return "\n".join(parts).strip()


def build_user_prompt(left: dict, right: dict, max_chars_per_doc: int) -> str:
    left_title = str(left.get("title", "")).strip()
    right_title = str(right.get("title", "")).strip()
    left_summary = build_document_summary_text(left)
    right_summary = build_document_summary_text(right)
    rules_a = build_rules_text(left, max_chars_per_doc)
    rules_b = build_rules_text(right, max_chars_per_doc)
    return f"制度A标题：{left_title}\n制度A摘要（仅作背景语境，不能替代条款证据）：\n{left_summary or '无'}\n制度A的有效规则条款：\n{rules_a}\n\n制度B标题：{right_title}\n制度B摘要（仅作背景语境，不能替代条款证据）：\n{right_summary or '无'}\n制度B的有效规则条款：\n{rules_b}"


def build_clause_locate_prompt(left: dict, right: dict, max_chars_per_doc: int) -> str:
    left_title = str(left.get("title", "")).strip()
    right_title = str(right.get("title", "")).strip()
    left_summary = build_document_summary_text(left)
    right_summary = build_document_summary_text(right)
    rules_a = build_rules_text(left, max_chars_per_doc)
    rules_b = build_rules_text(right, max_chars_per_doc)
    return f"制度A标题：{left_title}\n制度A摘要（仅作背景语境，冲突证据必须落到具体条款）：\n{left_summary or '无'}\n制度A的有效规则条款：\n{rules_a}\n\n制度B标题：{right_title}\n制度B摘要（仅作背景语境，冲突证据必须落到具体条款）：\n{right_summary or '无'}\n制度B的有效规则条款：\n{rules_b}"


def build_parse_error_message(err: Exception, content: str) -> str:
    return f"{err}; 模型输出预览: {preview_text(content)}"


# 对单个制度对做冲突可能性初筛。
async def analyze_single_pair(llm: AsyncLLMClient, pair_scope: str, left_category: str, right_category: str, left: dict, right: dict, model: str, thinking_extra_body: dict[str, Any] | None, business_retries: int, max_chars_per_doc: int, sem: asyncio.Semaphore) -> dict:
    async with sem:
        pair_label = left_category if pair_scope == "intra" else f"{left_category} <-> {right_category}"
        result = {
            "pair_scope": pair_scope,
            "category": pair_label,
            "left_category": left_category,
            "right_category": right_category,
            "left_doc_id": str(left.get("doc_id", "")).strip(),
            "right_doc_id": str(right.get("doc_id", "")).strip(),
            "left_title": str(left.get("title", "")).strip(),
            "right_title": str(right.get("title", "")).strip(),
            "left_primary_department": str(left.get("primary_department") or left.get("主责部门") or "").strip(),
            "right_primary_department": str(right.get("primary_department") or right.get("主责部门") or "").strip(),
            "left_policy_level": str(left.get("policy_level") or left.get("制度级别") or "").strip(),
            "right_policy_level": str(right.get("policy_level") or right.get("制度级别") or "").strip(),
            "left_file_name": str(left.get("file_name", "")).strip(),
            "right_file_name": str(right.get("file_name", "")).strip(),
            "left_valid_rule_count": int(left.get("valid_rule_count", 0) or 0),
            "right_valid_rule_count": int(right.get("valid_rule_count", 0) or 0),
            "possible_conflict": False,
            "confidence": "low",
            "conflict_types": [],
            "topics": [],
            "reason": "",
            "model": model,
            "status": "ok",
            "error": "",
            "exception_output": "",
            "attempt_count": 0,
            "left_rules_preview": preview_text(build_rules_text(left, max_chars_per_doc), 1000),
            "right_rules_preview": preview_text(build_rules_text(right, max_chars_per_doc), 1000),
        }
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": build_user_prompt(left, right, max_chars_per_doc)}]
        try:
            last_error = ""
            last_content = ""
            attempts = max(1, int(business_retries))
            for attempt in range(1, attempts + 1):
                resp = await llm.chat(messages=messages, model=model, temperature=0.0, extra_body=thinking_extra_body)
                content = extract_message_text(resp)
                last_content = content
                logger.info("制度对初筛模型返回 pair_scope=%s category=%s left=%s right=%s attempt=%s/%s content=%s", pair_scope, pair_label, result["left_doc_id"], result["right_doc_id"], attempt, attempts, preview_text(content))
                try:
                    payload = parse_pair_payload(content)
                    result["possible_conflict"] = bool(payload.get("possible_conflict"))
                    result["confidence"] = str(payload.get("confidence", "")).strip().lower()
                    result["conflict_types"] = [str(item).strip() for item in payload.get("conflict_types", []) if str(item).strip()]
                    result["topics"] = [str(item).strip() for item in payload.get("topics", []) if str(item).strip()]
                    result["reason"] = str(payload.get("reason", "")).strip()
                    result["attempt_count"] = attempt
                    return result
                except Exception as parse_err:  # pylint: disable=broad-except
                    last_error = build_parse_error_message(parse_err, content)
                    logger.warning("制度对初筛解析失败 pair_scope=%s category=%s left=%s right=%s attempt=%s/%s err=%s", pair_scope, pair_label, result["left_doc_id"], result["right_doc_id"], attempt, attempts, last_error)
            result["status"] = "error"
            result["error"] = last_error or "unknown_parse_error"
            result["exception_output"] = last_content
        except Exception as err:  # pylint: disable=broad-except
            logger.error("制度对初筛失败 pair_scope=%s category=%s left=%s right=%s err=%s", pair_scope, pair_label, result["left_doc_id"], result["right_doc_id"], err)
            result["status"] = "error"
            result["error"] = str(err)
        return result


# 对 high 制度对继续做条款级冲突定位。
async def locate_clause_conflicts_for_pair(llm: AsyncLLMClient, pair_row: dict, documents_by_id: dict[str, dict], model: str, thinking_extra_body: dict[str, Any] | None, business_retries: int, max_chars_per_doc: int, max_clause_candidates: int, sem: asyncio.Semaphore) -> dict:
    async with sem:
        left_doc_id = str(pair_row.get("left_doc_id", "")).strip()
        right_doc_id = str(pair_row.get("right_doc_id", "")).strip()
        left = documents_by_id.get(left_doc_id, {})
        right = documents_by_id.get(right_doc_id, {})
        result = {
            "pair_scope": str(pair_row.get("pair_scope", "")).strip(),
            "category": str(pair_row.get("category", "")).strip(),
            "left_category": str(pair_row.get("left_category", "")).strip(),
            "right_category": str(pair_row.get("right_category", "")).strip(),
            "left_doc_id": left_doc_id,
            "right_doc_id": right_doc_id,
            "left_title": str(pair_row.get("left_title", "")).strip(),
            "right_title": str(pair_row.get("right_title", "")).strip(),
            "left_primary_department": str(pair_row.get("left_primary_department") or left.get("primary_department") or left.get("主责部门") or "").strip(),
            "right_primary_department": str(pair_row.get("right_primary_department") or right.get("primary_department") or right.get("主责部门") or "").strip(),
            "left_policy_level": str(pair_row.get("left_policy_level") or left.get("policy_level") or left.get("制度级别") or "").strip(),
            "right_policy_level": str(pair_row.get("right_policy_level") or right.get("policy_level") or right.get("制度级别") or "").strip(),
            "pair_confidence": str(pair_row.get("confidence", "")).strip(),
            "pair_reason": str(pair_row.get("reason", "")).strip(),
            "conflict_candidates": [],
            "candidate_count": 0,
            "status": "ok",
            "error": "",
            "exception_output": "",
            "attempt_count": 0,
        }
        messages = [{"role": "system", "content": CLAUSE_LOCATE_PROMPT}, {"role": "user", "content": build_clause_locate_prompt(left, right, max_chars_per_doc)}]
        try:
            last_error = ""
            last_content = ""
            attempts = max(1, int(business_retries))
            for attempt in range(1, attempts + 1):
                resp = await llm.chat(messages=messages, model=model, temperature=0.0, extra_body=thinking_extra_body)
                content = extract_message_text(resp)
                last_content = content
                logger.info("条款级定位模型返回 left=%s right=%s attempt=%s/%s content=%s", left_doc_id, right_doc_id, attempt, attempts, preview_text(content))
                try:
                    payload = parse_clause_locate_payload(content)
                    candidates = payload.get("conflict_candidates", [])
                    normalized_candidates = []
                    for item in candidates[: max(1, int(max_clause_candidates))]:
                        if not isinstance(item, dict):
                            continue
                        normalized_candidates.append(
                            {
                                "left_article_no": str(item.get("left_article_no", "")).strip(),
                                "left_text": str(item.get("left_text", "")).strip(),
                                "right_article_no": str(item.get("right_article_no", "")).strip(),
                                "right_text": str(item.get("right_text", "")).strip(),
                                "conflict_type": str(item.get("conflict_type", "")).strip(),
                                "reason": str(item.get("reason", "")).strip(),
                                "confidence": str(item.get("confidence", "")).strip().lower(),
                            }
                        )
                    result["conflict_candidates"] = normalized_candidates
                    result["candidate_count"] = len(normalized_candidates)
                    result["attempt_count"] = attempt
                    return result
                except Exception as parse_err:  # pylint: disable=broad-except
                    last_error = build_parse_error_message(parse_err, content)
                    logger.warning("条款级定位解析失败 left=%s right=%s attempt=%s/%s err=%s", left_doc_id, right_doc_id, attempt, attempts, last_error)
            result["status"] = "error"
            result["error"] = last_error or "unknown_parse_error"
            result["exception_output"] = last_content
        except Exception as err:  # pylint: disable=broad-except
            logger.error("条款级定位失败 left=%s right=%s err=%s", left_doc_id, right_doc_id, err)
            result["status"] = "error"
            result["error"] = str(err)
        return result


def build_summary(rows: list[dict]) -> dict:
    by_category: dict[str, dict[str, int]] = {}
    by_scope: dict[str, int] = {}
    total_possible = 0
    total_error = 0
    for row in rows:
        category = str(row.get("category", "")).strip()
        pair_scope = str(row.get("pair_scope", "")).strip() or "unknown"
        bucket = by_category.setdefault(category, {"pair_count": 0, "possible_conflict_count": 0, "error_count": 0})
        bucket["pair_count"] += 1
        by_scope[pair_scope] = by_scope.get(pair_scope, 0) + 1
        if bool(row.get("possible_conflict")):
            bucket["possible_conflict_count"] += 1
            total_possible += 1
        if str(row.get("status", "")).strip() != "ok":
            bucket["error_count"] += 1
            total_error += 1
    return {
        "pair_count": len(rows),
        "possible_conflict_count": total_possible,
        "error_count": total_error,
        "categories": by_category,
        "pair_scope_counts": by_scope,
    }


def confidence_rank(value: str) -> int:
    normalized = str(value or "").strip().lower()
    if normalized == "high":
        return 3
    if normalized == "medium":
        return 2
    if normalized == "low":
        return 1
    return 0


def flatten_clause_candidates(candidates: list[dict]) -> str:
    return json.dumps(candidates, ensure_ascii=False, indent=2)


def build_clause_candidate_excel_rows(clause_rows: list[dict]) -> list[dict]:
    excel_rows: list[dict] = []
    for row in clause_rows:
        candidates = row.get("conflict_candidates", [])
        if not isinstance(candidates, list):
            continue
        for index, candidate in enumerate(candidates, start=1):
            if not isinstance(candidate, dict):
                continue
            excel_rows.append(
                {
                    "pair_scope": row.get("pair_scope", ""),
                    "category": row.get("category", ""),
                    "left_category": row.get("left_category", ""),
                    "right_category": row.get("right_category", ""),
                    "left_doc_id": row.get("left_doc_id", ""),
                    "right_doc_id": row.get("right_doc_id", ""),
                    "left_title": row.get("left_title", ""),
                    "right_title": row.get("right_title", ""),
                    "left_primary_department": row.get("left_primary_department", ""),
                    "right_primary_department": row.get("right_primary_department", ""),
                    "left_policy_level": row.get("left_policy_level", ""),
                    "right_policy_level": row.get("right_policy_level", ""),
                    "pair_confidence": row.get("pair_confidence", ""),
                    "pair_reason": row.get("pair_reason", ""),
                    "candidate_index": index,
                    "left_article_no": candidate.get("left_article_no", ""),
                    "left_text": candidate.get("left_text", ""),
                    "right_article_no": candidate.get("right_article_no", ""),
                    "right_text": candidate.get("right_text", ""),
                    "conflict_type": candidate.get("conflict_type", ""),
                    "reason": candidate.get("reason", ""),
                    "confidence": candidate.get("confidence", ""),
                }
            )
    return excel_rows


def merge_pair_and_clause_rows(pair_rows: list[dict], clause_rows: list[dict]) -> list[dict]:
    clause_map = {build_pair_key(row): row for row in clause_rows}
    merged_rows: list[dict] = []
    for pair_row in pair_rows:
        merged_row = dict(pair_row)
        clause_row = clause_map.get(build_pair_key(pair_row), {})
        merged_row["stage2_status"] = str(clause_row.get("status", "")).strip()
        merged_row["stage2_error"] = str(clause_row.get("error", "")).strip()
        merged_row["stage2_attempt_count"] = int(clause_row.get("attempt_count", 0) or 0)
        merged_row["stage2_candidate_count"] = int(clause_row.get("candidate_count", 0) or 0)
        merged_row["stage2_exception_output"] = str(clause_row.get("exception_output", "") or "")
        merged_row["stage2_conflict_candidates_json"] = flatten_clause_candidates(list(clause_row.get("conflict_candidates", [])))
        merged_rows.append(merged_row)
    return merged_rows


async def async_main() -> int:
    args = build_parser().parse_args()
    load_env_files()
    input_path = Path(args.effective_rules_jsonl).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.export_clause_candidates_only:
        clause_rows = load_jsonl_rows(output_dir / "high_conflict_clauses.jsonl")
        output_path = output_dir / "high_conflict_clause_candidates.xlsx"
        write_excel(output_path, build_clause_candidate_excel_rows(clause_rows))
        print(f"candidate excel 输出: {output_path}")
        return 0

    rows = load_jsonl_rows(input_path)
    filtered_rows = filter_effective_documents(rows, args.category.strip(), bool(args.include_uncategorized))
    grouped_rows = group_by_category(filtered_rows, int(args.limit_categories))
    output_jsonl = output_dir / "doc_pair_review.jsonl"
    output_xlsx = output_dir / "doc_pair_review.xlsx"
    output_summary = output_dir / "summary.json"
    existing_pair_rows = [] if args.force_rerun else load_jsonl_rows_if_exists(output_jsonl)
    processed_pair_keys = collect_processed_pair_keys(existing_pair_rows)

    intra_pair_tasks = [] if bool(args.skip_intra_category) else build_intra_pair_tasks(grouped_rows, int(args.limit_pairs), processed_pair_keys)
    remaining_limit = 0
    if int(args.limit_pairs) > 0:
        remaining_limit = max(0, int(args.limit_pairs) - len(intra_pair_tasks))
    inter_pair_tasks: list[tuple[str, str, str, dict, dict]] = []
    if args.run_inter_category:
        inter_pairs = load_inter_category_pairs(args.inter_category_pairs_json)
        inter_pair_tasks = build_inter_pair_tasks(grouped_rows, inter_pairs, remaining_limit if int(args.limit_pairs) > 0 else 0, processed_pair_keys)
    pair_tasks = intra_pair_tasks + inter_pair_tasks

    model = str(args.model or "").strip() or get_analysis_model()
    thinking_extra_body = build_thinking_extra_body(model, bool(args.enable_thinking))
    llm = AsyncLLMClient(config=ModelConfig(default_model=model), name="zhidu_doc_pair_conflict")
    sem = asyncio.Semaphore(max(1, int(args.concurrency)))

    logger.info("开始制度对初筛 版块比较键数=%s 组内制度对数=%s 组间制度对数=%s 跳过已分析制度对数=%s model=%s thinking_extra_body=%s business_retries=%s force_rerun=%s", len(grouped_rows), len(intra_pair_tasks), len(inter_pair_tasks), len(processed_pair_keys), model, thinking_extra_body, args.business_retries, bool(args.force_rerun))
    tasks = [
        asyncio.create_task(
            analyze_single_pair(
                llm,
                pair_scope,
                left_category,
                right_category,
                left,
                right,
                model,
                thinking_extra_body,
                int(args.business_retries),
                int(args.max_chars_per_doc),
                sem,
            )
        )
        for pair_scope, left_category, right_category, left, right in pair_tasks
    ]
    new_rows = await asyncio.gather(*tasks)
    result_rows = existing_pair_rows + new_rows
    write_jsonl_rows(output_jsonl, result_rows)
    write_excel(output_xlsx, result_rows)
    summary = build_summary(result_rows)
    summary["new_pair_count"] = len(new_rows)
    summary["skipped_existing_pair_count"] = len(processed_pair_keys)
    summary["run_inter_category"] = bool(args.run_inter_category)
    summary["force_rerun"] = bool(args.force_rerun)

    if args.run_high_clause_locate:
        documents_by_id = {str(row.get("doc_id", "")).strip(): row for row in filtered_rows}
        existing_clause_rows = [] if args.force_rerun else load_jsonl_rows_if_exists(output_dir / "high_conflict_clauses.jsonl")
        processed_clause_keys = collect_processed_pair_keys(existing_clause_rows)
        stage2_rows = [
            row
            for row in result_rows
            if bool(row.get("possible_conflict")) and confidence_rank(str(row.get("confidence", "")).strip()) >= confidence_rank(str(args.stage2_min_confidence).strip())
            and (args.force_rerun or build_pair_key(row) not in processed_clause_keys)
        ]
        logger.info("开始制度对条款级定位 min_confidence=%s 新增pair_count=%s 跳过已定位pair_count=%s", args.stage2_min_confidence, len(stage2_rows), len(processed_clause_keys))
        clause_tasks = [
            asyncio.create_task(
                locate_clause_conflicts_for_pair(
                    llm,
                    row,
                    documents_by_id,
                    model,
                    thinking_extra_body,
                    int(args.business_retries),
                    int(args.max_chars_per_doc),
                    int(args.max_clause_candidates),
                    sem,
                )
            )
            for row in stage2_rows
        ]
        new_clause_rows = await asyncio.gather(*clause_tasks)
        clause_rows = existing_clause_rows + new_clause_rows
        write_jsonl_rows(output_dir / "high_conflict_clauses.jsonl", clause_rows)
        write_excel(output_dir / "high_conflict_clauses.xlsx", clause_rows)
        write_excel(output_dir / "high_conflict_clause_candidates.xlsx", build_clause_candidate_excel_rows(clause_rows))
        merged_rows = merge_pair_and_clause_rows(result_rows, clause_rows)
        write_jsonl_rows(output_dir / "doc_pair_review_merged.jsonl", merged_rows)
        write_excel(output_dir / "doc_pair_review_merged.xlsx", merged_rows)
        summary["stage2_min_confidence"] = str(args.stage2_min_confidence).strip()
        summary["high_clause_locate_pair_count"] = len(clause_rows)
        summary["new_high_clause_locate_pair_count"] = len(new_clause_rows)
        summary["skipped_existing_stage2_pair_count"] = len(processed_clause_keys)
        summary["high_clause_locate_candidate_count"] = sum(int(row.get("candidate_count", 0) or 0) for row in clause_rows)

    write_json(output_summary, summary)

    print(f"jsonl 输出: {output_jsonl}")
    print(f"excel 输出: {output_xlsx}")
    print(f"summary 输出: {output_summary}")
    print(f"制度对数量: {len(result_rows)}")
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
