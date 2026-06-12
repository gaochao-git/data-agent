from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency
    load_dotenv = None
try:
    from rdflib import Graph, URIRef
except Exception:  # pragma: no cover - optional dependency
    Graph = None
    URIRef = None

SERVER_ROOT = Path(__file__).resolve().parents[4]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from apps.complaint_app.core.llm.llm_client import AsyncLLMClient, ModelConfig
from apps.complaint_app.core.model_config import get_analysis_model
from utils.llm_thinking import build_thinking_extra_body
from utils.logger import get_logger

from apps.complaint_app.tools.zhidu_clause_extract.validate_clause_semantics_ttl import DEFAULT_SHAPES_FILE, BASE_URI, build_report_json, load_turtle_graph, run_shacl_validation
from apps.complaint_app.tools.zhidu_clause_extract.progress_utils import gather_with_doc_progress


logger = get_logger(__name__)
MAX_LOG_PREVIEW = 2000
SCHEMA_FILE = Path(__file__).resolve().with_name("clause_semantics_ontology_schema.ttl")
EX_NS = "http://example.com/zhidu-clause-semantics/"

ONTOLOGY_TTL_SYSTEM_PROMPT = """你正在做制度条款语义的 OWL/Turtle 映射。

你会收到：
1. 固定的 ontology schema
2. 单个制度的一条条款相关信息
3. 这条条款拆出的规制表达
4. 这条条款涉及的概念与别名
5. 已经为你预生成好的 IRI

你的任务是：严格按照 schema，为该条款生成一段 Turtle 片段。

要求：
1. 只能使用输入中提供的 IRI，不要自造新命名空间。
2. 输出中不要包含 `@prefix`，不要重复输出 schema，只输出实例层 Turtle 片段。
3. 必须覆盖：
   - `PolicyDocument`
   - `Clause`
   - 该条款下的所有 `RuleExpression`
   - 该条款实际用到的 `Concept`
4. 必须建立这些关系：
   - `PolicyDocument -> hasClause -> Clause`
   - `Clause -> belongsToDocument -> PolicyDocument`
   - `Clause -> expressesRule -> RuleExpression`
   - `RuleExpression -> supportedByClause -> Clause`
   - `RuleExpression -> hasSubject/hasAction/hasObject/hasCondition/hasConstraint/hasBusinessContext -> Concept`
5. `Concept` 必须带：
   - `canonicalName`
   - `aliasText`（如有）
   - `mentionCategory`
   - `conceptStatus`
   - `ontologyRole`
   - `definitionText`（如有）
   - `screenReason`（如有）
   - `normalizationReason`（如有）
   - `hasBroaderConcept/hasNarrowerConcept/hasRelatedConcept`（如有）
6. `RuleExpression` 必须带：
   - `effectText`
   - `evidenceText`
7. 如果某个字段为空，就不要生成该属性或关系。
8. 输出必须是合法 Turtle 片段，不要输出解释，不要输出 Markdown 代码块。
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="把条款语义结果映射成 OWL/Turtle。")
    parser.add_argument("--documents-jsonl", required=True, help="documents_mapped.jsonl 路径")
    parser.add_argument("--normalization-jsonl", required=True, help="clause_semantics_normalization.jsonl 路径")
    parser.add_argument("--concepts-jsonl", required=True, help="clause_semantics_concepts.jsonl 路径")
    parser.add_argument("--terms-jsonl", default="", help="clause_semantics_terms.jsonl 路径，未传则不接术语增强")
    parser.add_argument("--rule-expressions-jsonl", required=True, help="clause_rule_expressions.jsonl 路径")
    parser.add_argument("--output-dir", required=True, help="输出目录")
    parser.add_argument("--doc-id", default="", help="只处理指定 doc_id")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 条条款，0 表示全部")
    parser.add_argument("--model", default="", help="模型名，默认读取 OPENAI_ANALYSIS_MODEL")
    parser.add_argument("--concurrency", type=int, default=3, help="并发数，默认 3")
    parser.add_argument("--business-retries", type=int, default=3, help="模型返回非 Turtle 时的业务重试次数，默认 3")
    parser.add_argument("--enable-thinking", action="store_true", help="显式开启模型思考")
    parser.add_argument("--skip-shacl-validation", action="store_true", help="跳过 TTL 生成后的 SHACL 结构校验")
    parser.add_argument("--fail-on-shacl-violation", action="store_true", help="SHACL 校验发现违规时返回失败")
    return parser


# 加载环境变量，便于直接读取 server/.env。
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
        source = str(line or "").strip()
        if not source:
            continue
        rows.append(json.loads(source))
    return rows


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def serialize_turtle_with_base_uri(ttl_text: str, base_uri: str) -> str:
    source = str(ttl_text or "").strip()
    if not source:
        raise ValueError("TTL 内容为空，无法生成 GraphDB 导入文件")
    if Graph is None:
        raise ModuleNotFoundError("缺少 rdflib 依赖，请先安装：pip install rdflib")
    graph = Graph()
    graph.parse(data=source, format="turtle", publicID=base_uri)
    serialized = graph.serialize(format="turtle")
    if isinstance(serialized, bytes):
        return serialized.decode("utf-8")
    return str(serialized)


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
            logger.warning("条款 OWL 映射 content 为空，回退字段=%s", field)
            return fallback
    return ""


def preview_text(text: str, limit: int = MAX_LOG_PREVIEW) -> str:
    source = str(text or "").strip()
    if len(source) <= limit:
        return source
    return f"{source[:limit]}... [truncated {len(source) - limit} chars]"


def strip_code_fence(text: str) -> str:
    source = str(text or "").strip()
    lower_source = source.lower()
    if lower_source.startswith("```ttl"):
        source = source[6:].lstrip()
    elif lower_source.startswith("```turtle"):
        source = source[9:].lstrip()
    elif lower_source.startswith("```"):
        source = source[3:].lstrip()
    if source.endswith("```"):
        source = source[:-3].rstrip()
    return source


def iri_token(value: str, prefix: str) -> str:
    source = str(value or "").strip()
    if not source:
        return prefix
    normalized = re.sub(r"[^0-9A-Za-z\u4e00-\u9fa5]+", "_", source).strip("_")
    if not normalized:
        normalized = hashlib.md5(source.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{normalized}"


def stable_hash_token(*parts: str) -> str:
    source = "::".join(str(part or "").strip() for part in parts)
    return hashlib.md5(source.encode("utf-8")).hexdigest()[:12]


def build_clause_key(doc_id: str, article_no: str) -> str:
    return f"{doc_id}::{article_no}"


def unique_str_list(items: list[Any]) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        rows.append(value)
    return rows


def index_documents(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row.get("doc_id") or "").strip(): row for row in rows if str(row.get("doc_id") or "").strip()}


def index_concepts_by_clause(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        for member in list(row.get("member_mentions") or []):
            if not isinstance(member, dict):
                continue
            doc_id = str(member.get("doc_id") or "").strip()
            article_no = str(member.get("article_no") or "").strip()
            if not doc_id or not article_no:
                continue
            grouped[build_clause_key(doc_id, article_no)].append(row)
            break
    return grouped


def index_normalization_by_group(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row.get("normalization_group_id") or "").strip(): row for row in rows if str(row.get("normalization_group_id") or "").strip()}


def index_terms_by_group(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row.get("normalization_group_id") or "").strip(): row for row in rows if str(row.get("normalization_group_id") or "").strip()}


def index_terms_by_canonical_name(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row.get("canonical_name") or "").strip(): row for row in rows if str(row.get("canonical_name") or "").strip()}


def build_noise_canonical_names(rows: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for row in rows:
        if str(row.get("concept_status") or "").strip() != "noise_concept":
            continue
        canonical_name = str(row.get("canonical_name") or "").strip()
        if canonical_name:
            names.add(canonical_name)
    return names


def index_rule_expressions_by_clause(rows: list[dict[str, Any]], doc_id: str, limit: int) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    clause_order: list[str] = []
    for row in rows:
        current_doc_id = str(row.get("doc_id") or "").strip()
        if doc_id and current_doc_id != doc_id:
            continue
        article_no = str(row.get("article_no") or "").strip()
        if not current_doc_id or not article_no:
            continue
        clause_key = build_clause_key(current_doc_id, article_no)
        if clause_key not in grouped:
            clause_order.append(clause_key)
        grouped[clause_key].append(row)
    if limit <= 0:
        return grouped
    limited_keys = set(clause_order[:limit])
    return {key: value for key, value in grouped.items() if key in limited_keys}


def determine_concept_class(role: str) -> str:
    role_map = {
        "subject": "SubjectConcept",
        "action": "ActionConcept",
        "object": "ObjectConcept",
        "context": "ContextConcept",
    }
    return role_map.get(role, "Concept")


def build_term_reference_candidate(term_name: str, terms_by_name: dict[str, dict[str, Any]]) -> dict[str, Any]:
    term_row = terms_by_name.get(term_name)
    if not term_row:
        return {
            "concept_iri": iri_token(f"term_{term_name}", "concept"),
            "concept_class": "Concept",
            "canonical_name": term_name,
            "aliases": [],
            "mention_category": "term_reference",
            "concept_status": "term_reference",
            "ontology_role": "",
            "screen_reason": "",
            "normalization_reason": "",
            "definition": "",
            "broader_terms": [],
            "narrower_terms": [],
            "related_terms": [],
            "source": "term_reference_literal",
        }
    return {
        "concept_iri": iri_token(str(term_row.get("normalization_group_id") or "").strip(), "concept"),
        "concept_class": determine_concept_class(str(term_row.get("ontology_role") or "").strip()),
        "canonical_name": str(term_row.get("canonical_name") or "").strip(),
        "aliases": unique_str_list(list(term_row.get("aliases") or [])),
        "mention_category": str(term_row.get("mention_category") or "").strip(),
        "concept_status": str(term_row.get("concept_status") or "").strip(),
        "ontology_role": str(term_row.get("ontology_role") or "").strip(),
        "screen_reason": str(term_row.get("screen_reason") or "").strip(),
        "normalization_reason": str(term_row.get("normalization_reason") or "").strip(),
        "definition": str(term_row.get("definition") or "").strip(),
        "broader_terms": unique_str_list(list(term_row.get("broader_terms") or [])),
        "narrower_terms": unique_str_list(list(term_row.get("narrower_terms") or [])),
        "related_terms": unique_str_list(list(term_row.get("related_terms") or [])),
        "source": "term_reference_concept",
    }


def attach_term_relationship_candidates(candidates: list[dict[str, Any]], terms_by_name: dict[str, dict[str, Any]], noise_canonical_names: set[str]) -> list[dict[str, Any]]:
    candidate_map = {str(row.get("canonical_name") or "").strip(): row for row in candidates if str(row.get("canonical_name") or "").strip()}
    for row in list(candidates):
        for field_name, relation_key in (("broader_terms", "broader_concepts"), ("narrower_terms", "narrower_concepts"), ("related_terms", "related_concepts")):
            references: list[dict[str, str]] = []
            for term_name in unique_str_list(list(row.get(field_name) or [])):
                if not term_name or term_name == str(row.get("canonical_name") or "").strip() or term_name in noise_canonical_names:
                    continue
                reference_row = candidate_map.get(term_name)
                if not reference_row:
                    reference_row = build_term_reference_candidate(term_name, terms_by_name)
                    candidates.append(reference_row)
                    candidate_map[term_name] = reference_row
                references.append({"concept_iri": str(reference_row.get("concept_iri") or "").strip(), "canonical_name": str(reference_row.get("canonical_name") or "").strip()})
            row[relation_key] = references
    return candidates


def build_clause_concept_candidates(concept_rows: list[dict[str, Any]], normalization_map: dict[str, dict[str, Any]], terms_by_group: dict[str, dict[str, Any]], terms_by_name: dict[str, dict[str, Any]], rule_rows: list[dict[str, Any]], doc_id: str, article_no: str, noise_canonical_names: set[str]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for row in concept_rows:
        normalization_group_id = str(row.get("normalization_group_id") or "").strip()
        canonical_name = str(row.get("canonical_name") or "").strip()
        concept_status = str(row.get("concept_status") or "").strip()
        ontology_role = str(row.get("ontology_role") or "").strip()
        if not normalization_group_id or not canonical_name or concept_status == "noise_concept" or ontology_role == "discard" or canonical_name in noise_canonical_names:
            continue
        term_row = terms_by_group.get(normalization_group_id) or {}
        aliases = unique_str_list(list(term_row.get("aliases") or []))
        if not aliases:
            for member in list(row.get("member_mentions") or []):
                if not isinstance(member, dict):
                    continue
                if str(member.get("doc_id") or "").strip() != doc_id or str(member.get("article_no") or "").strip() != article_no:
                    continue
                alias = str(member.get("mention_text") or "").strip()
                if alias and alias != canonical_name:
                    aliases.append(alias)
        aliases = unique_str_list(aliases)
        candidates.append(
            {
                "concept_iri": iri_token(normalization_group_id, "concept"),
                "concept_class": determine_concept_class(ontology_role),
                "canonical_name": canonical_name,
                "aliases": aliases,
                "mention_category": str(row.get("mention_category") or "").strip(),
                "concept_status": concept_status,
                "ontology_role": ontology_role,
                "screen_reason": str(row.get("screen_reason") or "").strip(),
                "normalization_reason": str((normalization_map.get(normalization_group_id) or {}).get("reason") or "").strip(),
                "definition": str(term_row.get("definition") or "").strip(),
                "broader_terms": unique_str_list(list(term_row.get("broader_terms") or [])),
                "narrower_terms": unique_str_list(list(term_row.get("narrower_terms") or [])),
                "related_terms": unique_str_list(list(term_row.get("related_terms") or [])),
                "source": "screened_concept",
            }
        )
        seen_names.add(canonical_name)
    for rule_index, rule_row in enumerate(rule_rows, start=1):
        field_map = {
            "subject": ("SubjectConcept", list(rule_row.get("subject") or [])),
            "actions": ("ActionConcept", list(rule_row.get("actions") or [])),
            "object": ("ObjectConcept", list(rule_row.get("object") or [])),
            "condition": ("ConditionConcept", list(rule_row.get("condition") or [])),
            "constraint": ("ConstraintConcept", list(rule_row.get("constraint") or [])),
            "business_context": ("ContextConcept", [str(rule_row.get("business_context") or "").strip()]),
        }
        for field_name, (concept_class, items) in field_map.items():
            for value in unique_str_list(items):
                if value in seen_names or value in noise_canonical_names:
                    continue
                candidates.append(
                    {
                        "concept_iri": iri_token(f"{doc_id}_{article_no}_{field_name}_{rule_index}_{stable_hash_token(value)}", "concept"),
                        "concept_class": concept_class,
                        "canonical_name": value,
                        "aliases": [],
                        "mention_category": field_name,
                        "concept_status": "derived_from_rule_expression",
                        "ontology_role": field_name.rstrip("s"),
                        "screen_reason": "",
                        "normalization_reason": "",
                        "definition": "",
                        "broader_terms": [],
                        "narrower_terms": [],
                        "related_terms": [],
                        "source": "rule_expression_literal",
                    }
                )
                seen_names.add(value)
    return attach_term_relationship_candidates(candidates, terms_by_name, noise_canonical_names)


def build_clause_payload(document_row: dict[str, Any], clause_key: str, concept_rows: list[dict[str, Any]], normalization_map: dict[str, dict[str, Any]], terms_by_group: dict[str, dict[str, Any]], terms_by_name: dict[str, dict[str, Any]], rule_rows: list[dict[str, Any]], schema_text: str, noise_canonical_names: set[str]) -> dict[str, Any]:
    doc_id, article_no = clause_key.split("::", 1)
    document_iri = iri_token(doc_id, "document")
    clause_iri = iri_token(f"{doc_id}_{article_no}", "clause")
    clause_text = "\n".join(unique_str_list([str(row.get("evidence_text") or "").strip() for row in rule_rows]))
    rule_payloads: list[dict[str, Any]] = []
    for index, row in enumerate(rule_rows, start=1):
        rule_payloads.append(
            {
                "rule_iri": iri_token(f"{doc_id}_{article_no}_{index}", "rule"),
                "subject": unique_str_list(list(row.get("subject") or [])),
                "actions": unique_str_list(list(row.get("actions") or [])),
                "object": unique_str_list(list(row.get("object") or [])),
                "business_context": str(row.get("business_context") or "").strip(),
                "condition": unique_str_list(list(row.get("condition") or [])),
                "constraint": unique_str_list(list(row.get("constraint") or [])),
                "effect": str(row.get("effect") or "").strip(),
                "evidence_text": str(row.get("evidence_text") or "").strip(),
            }
        )
    concept_candidates = build_clause_concept_candidates(concept_rows, normalization_map, terms_by_group, terms_by_name, rule_rows, doc_id, article_no, noise_canonical_names)
    return {
        "schema": schema_text,
        "document": {
            "document_iri": document_iri,
            "document_id": doc_id,
            "title": str(document_row.get("title") or "").strip(),
            "board": str(document_row.get("版块") or "").strip(),
            "domain": str(document_row.get("领域") or "").strip(),
            "doc_no": str(document_row.get("制度文号") or "").strip(),
            "doc_level": str(document_row.get("制度级别") or "").strip(),
            "publish_date": str(document_row.get("发布时间") or "").strip(),
            "owner_department": str(document_row.get("主责部门") or "").strip(),
        },
        "clause": {
            "clause_iri": clause_iri,
            "clause_key": clause_key,
            "article_no": article_no,
            "article_text": clause_text,
        },
        "concept_candidates": concept_candidates,
        "rule_expressions": rule_payloads,
    }


def build_user_prompt(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def ttl_literal(value: str) -> str:
    return json.dumps(str(value or ""), ensure_ascii=False)


def build_concept_term_fragment(payload: dict[str, Any]) -> str:
    rows: list[str] = []
    seen_iris: set[str] = set()
    for concept in list(payload.get("concept_candidates") or []):
        concept_iri = str(concept.get("concept_iri") or "").strip()
        concept_class = str(concept.get("concept_class") or "").strip() or "Concept"
        canonical_name = str(concept.get("canonical_name") or "").strip()
        if not concept_iri or not canonical_name or concept_iri in seen_iris:
            continue
        seen_iris.add(concept_iri)
        predicates = [f"a ex:{concept_class}", f"ex:canonicalName {ttl_literal(canonical_name)}"]
        for alias in unique_str_list(list(concept.get("aliases") or [])):
            predicates.append(f"ex:aliasText {ttl_literal(alias)}")
        mention_category = str(concept.get("mention_category") or "").strip()
        concept_status = str(concept.get("concept_status") or "").strip()
        ontology_role = str(concept.get("ontology_role") or "").strip()
        screen_reason = str(concept.get("screen_reason") or "").strip()
        normalization_reason = str(concept.get("normalization_reason") or "").strip()
        definition = str(concept.get("definition") or "").strip()
        if mention_category:
            predicates.append(f"ex:mentionCategory {ttl_literal(mention_category)}")
        if concept_status:
            predicates.append(f"ex:conceptStatus {ttl_literal(concept_status)}")
        if ontology_role:
            predicates.append(f"ex:ontologyRole {ttl_literal(ontology_role)}")
        if definition:
            predicates.append(f"ex:definitionText {ttl_literal(definition)}")
        if screen_reason:
            predicates.append(f"ex:screenReason {ttl_literal(screen_reason)}")
        if normalization_reason:
            predicates.append(f"ex:normalizationReason {ttl_literal(normalization_reason)}")
        for reference in list(concept.get("broader_concepts") or []):
            ref_iri = str(reference.get("concept_iri") or "").strip()
            if ref_iri:
                predicates.append(f"ex:hasBroaderConcept <{ref_iri}>")
        for reference in list(concept.get("narrower_concepts") or []):
            ref_iri = str(reference.get("concept_iri") or "").strip()
            if ref_iri:
                predicates.append(f"ex:hasNarrowerConcept <{ref_iri}>")
        for reference in list(concept.get("related_concepts") or []):
            ref_iri = str(reference.get("concept_iri") or "").strip()
            if ref_iri:
                predicates.append(f"ex:hasRelatedConcept <{ref_iri}>")
        rows.append(f"<{concept_iri}> " + " ;\n    ".join(predicates) + " .")
    return "\n\n".join(rows)


def validate_ttl_fragment(ttl_text: str, payload: dict[str, Any]) -> None:
    source = strip_code_fence(ttl_text)
    graph = parse_ttl_fragment(source)
    document_iri = str(((payload.get("document") or {}).get("document_iri")) or "").strip()
    clause_iri = str(((payload.get("clause") or {}).get("clause_iri")) or "").strip()
    if not has_ttl_node(graph, document_iri):
        raise ValueError("TTL 片段缺少 document 实例")
    if not has_ttl_node(graph, clause_iri):
        raise ValueError("TTL 片段缺少 clause 实例")
    for rule in list(payload.get("rule_expressions") or []):
        rule_iri = str(rule.get("rule_iri") or "").strip()
        if rule_iri and not has_ttl_node(graph, rule_iri):
            raise ValueError(f"TTL 片段缺少 rule 实例 {rule_iri}")


def parse_ttl_fragment(ttl_text: str) -> Any:
    source = str(ttl_text or "").strip()
    if not source:
        raise ValueError("TTL 片段为空")
    if Graph is None:
        raise ModuleNotFoundError("缺少 rdflib 依赖，请先安装：pip install rdflib")
    prefix_lines = [
        f"@prefix ex: <{EX_NS}> .",
        f"@prefix : <{EX_NS}> .",
    ]
    ttl_source = "\n".join(prefix_lines) + "\n" + source
    graph = Graph()
    try:
        graph.parse(data=ttl_source, format="turtle", publicID=EX_NS)
    except Exception as err:
        raise ValueError(f"TTL 语法错误: {err}") from err
    return graph


def has_ttl_subject(graph: Any, local_iri: str) -> bool:
    token = str(local_iri or "").strip()
    if not token:
        return False
    if URIRef is None:
        return False
    subject = URIRef(f"{EX_NS}{token}")
    return (subject, None, None) in graph


def has_ttl_node(graph: Any, local_iri: str) -> bool:
    token = str(local_iri or "").strip()
    if not token:
        return False
    if URIRef is None:
        return False
    node = URIRef(f"{EX_NS}{token}")
    if (node, None, None) in graph:
        return True
    return (None, None, node) in graph


async def build_single_clause_ttl(llm: AsyncLLMClient, payload: dict[str, Any], model: str, thinking_extra_body: dict[str, Any] | None, business_retries: int, sem: asyncio.Semaphore) -> str:
    async with sem:
        clause = payload.get("clause") or {}
        doc = payload.get("document") or {}
        logger.info("开始条款本体映射 doc_id=%s article_no=%s rule_count=%s concept_count=%s", doc.get("document_id"), clause.get("article_no"), len(list(payload.get("rule_expressions") or [])), len(list(payload.get("concept_candidates") or [])))
        messages = [{"role": "system", "content": ONTOLOGY_TTL_SYSTEM_PROMPT}, {"role": "user", "content": build_user_prompt(payload)}]
        last_error = ""
        last_content = ""
        attempts = max(1, int(business_retries))
        for attempt in range(1, attempts + 1):
            resp = await llm.chat(messages=messages, model=model, temperature=0.0, extra_body=thinking_extra_body)
            content = extract_message_text(resp)
            last_content = content
            logger.info("条款本体映射模型返回 doc_id=%s article_no=%s attempt=%s/%s content=%s", doc.get("document_id"), clause.get("article_no"), attempt, attempts, preview_text(content))
            try:
                ttl_fragment = strip_code_fence(content)
                validate_ttl_fragment(ttl_fragment, payload)
                logger.info("完成条款本体映射 doc_id=%s article_no=%s", doc.get("document_id"), clause.get("article_no"))
                term_fragment = build_concept_term_fragment(payload)
                if not term_fragment:
                    return ttl_fragment
                merged_fragment = ttl_fragment.rstrip() + "\n\n" + term_fragment
                validate_ttl_fragment(merged_fragment, payload)
                return merged_fragment
            except Exception as err:  # pylint: disable=broad-except
                last_error = f"{err}; 模型输出预览: {preview_text(content)}"
                logger.warning("条款本体映射解析失败 doc_id=%s article_no=%s attempt=%s/%s err=%s", doc.get("document_id"), clause.get("article_no"), attempt, attempts, last_error)
        raise ValueError(f"条款本体映射失败 doc_id={doc.get('document_id')} article_no={clause.get('article_no')}: {last_error}; 原始输出: {last_content}")


def build_sparql_examples() -> str:
    return "\n".join(
        [
            "# 哪些制度规定了董事会可以决定什么",
            "PREFIX ex: <http://example.com/zhidu-clause-semantics/>",
            "SELECT ?title ?articleNo ?objectName WHERE {",
            "  ?doc a ex:PolicyDocument ;",
            "       ex:title ?title ;",
            "       ex:hasClause ?clause .",
            "  ?clause ex:articleNo ?articleNo ;",
            "          ex:expressesRule ?rule .",
            "  ?rule ex:hasSubject ?subject ;",
            "        ex:hasAction ?action ;",
            "        ex:hasObject ?object .",
            "  ?subject ex:canonicalName ?subjectName .",
            "  ?action ex:canonicalName ?actionName .",
            "  ?object ex:canonicalName ?objectName .",
            '  FILTER (?subjectName = "董事会" || ?subjectAlias = "董事会")',
            '  FILTER (?actionName = "决定" || ?actionAlias = "决定")',
            "  OPTIONAL { ?subject ex:aliasText ?subjectAlias . }",
            "  OPTIONAL { ?action ex:aliasText ?actionAlias . }",
            "}",
            "",
            "# 查询与“平台部”相关的条款（支持别名）",
            "PREFIX ex: <http://example.com/zhidu-clause-semantics/>",
            "SELECT ?title ?articleNo ?canonicalName WHERE {",
            "  ?doc a ex:PolicyDocument ;",
            "       ex:title ?title ;",
            "       ex:hasClause ?clause .",
            "  ?clause ex:articleNo ?articleNo ;",
            "          ex:expressesRule ?rule .",
            "  ?rule ?p ?concept .",
            "  ?concept a ex:Concept ;",
            "           ex:canonicalName ?canonicalName .",
            '  OPTIONAL { ?concept ex:aliasText ?alias . }',
            '  FILTER (?canonicalName = "基础平台部" || ?alias = "平台部")',
            "}",
            "",
        ]
    )


def build_summary(documents: dict[str, dict[str, Any]], clause_payloads: list[dict[str, Any]]) -> dict[str, Any]:
    concept_count = sum(len(list(payload.get("concept_candidates") or [])) for payload in clause_payloads)
    rule_count = sum(len(list(payload.get("rule_expressions") or [])) for payload in clause_payloads)
    return {
        "doc_count": len({str((payload.get("document") or {}).get("document_id") or "").strip() for payload in clause_payloads}),
        "clause_count": len(clause_payloads),
        "rule_expression_count": rule_count,
        "concept_candidate_count": concept_count,
        "document_indexed_count": len(documents),
    }


# 对生成后的 TTL 执行 SHACL 结构校验。
def validate_generated_ttl(ttl_path: Path, output_dir: Path) -> dict[str, Any]:
    data_graph = load_turtle_graph(ttl_path, BASE_URI)
    shapes_graph = load_turtle_graph(DEFAULT_SHAPES_FILE, BASE_URI)
    conforms, report_graph, report_text = run_shacl_validation(data_graph, shapes_graph)
    report_json = build_report_json(conforms, report_graph, report_text)
    write_json(output_dir / "shacl_validation_report.json", report_json)
    write_text(output_dir / "shacl_validation_report.txt", report_text)
    return report_json


async def async_main() -> int:
    args = build_parser().parse_args()
    load_env_files()
    documents_jsonl = Path(args.documents_jsonl).expanduser().resolve()
    normalization_jsonl = Path(args.normalization_jsonl).expanduser().resolve()
    concepts_jsonl = Path(args.concepts_jsonl).expanduser().resolve()
    terms_jsonl = Path(args.terms_jsonl).expanduser().resolve() if str(args.terms_jsonl or "").strip() else None
    rule_expressions_jsonl = Path(args.rule_expressions_jsonl).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    schema_text = SCHEMA_FILE.read_text(encoding="utf-8")
    document_rows = index_documents(load_jsonl_rows(documents_jsonl))
    normalization_map = index_normalization_by_group(load_jsonl_rows(normalization_jsonl))
    concept_rows = load_jsonl_rows(concepts_jsonl)
    term_rows = load_jsonl_rows(terms_jsonl) if terms_jsonl else []
    concepts_by_clause = index_concepts_by_clause(concept_rows)
    terms_by_group = index_terms_by_group(term_rows)
    terms_by_name = index_terms_by_canonical_name(term_rows)
    noise_canonical_names = build_noise_canonical_names(concept_rows)
    rule_rows_by_clause = index_rule_expressions_by_clause(load_jsonl_rows(rule_expressions_jsonl), str(args.doc_id or "").strip(), int(args.limit))
    if not rule_rows_by_clause:
        raise ValueError("未找到符合条件的规制表达记录")

    clause_payloads: list[dict[str, Any]] = []
    for clause_key, rule_rows in rule_rows_by_clause.items():
        doc_id, _article_no = clause_key.split("::", 1)
        document_row = document_rows.get(doc_id)
        if not document_row:
            raise ValueError(f"documents_mapped.jsonl 中缺少 doc_id={doc_id} 的记录")
        clause_payloads.append(build_clause_payload(document_row, clause_key, concepts_by_clause.get(clause_key, []), normalization_map, terms_by_group, terms_by_name, rule_rows, schema_text, noise_canonical_names))

    model = str(args.model or "").strip() or get_analysis_model()
    thinking_extra_body = build_thinking_extra_body(model, bool(args.enable_thinking))
    llm = AsyncLLMClient(config=ModelConfig(default_model=model), name="zhidu_clause_semantics_ontology_ttl")
    sem = asyncio.Semaphore(max(1, int(args.concurrency)))

    logger.info("开始条款语义 OWL 映射 clause_count=%s model=%s thinking_extra_body=%s", len(clause_payloads), model, thinking_extra_body)
    tasks = []
    total_doc_ids = {str((payload.get("document") or {}).get("document_id") or "").strip() for payload in clause_payloads if str((payload.get("document") or {}).get("document_id") or "").strip()}
    for payload in clause_payloads:
        doc = payload.get("document") or {}
        clause = payload.get("clause") or {}
        doc_id = str(doc.get("document_id") or "").strip()
        article_no = str(clause.get("article_no") or "").strip()
        tasks.append(
            (
                asyncio.create_task(build_single_clause_ttl(llm, payload, model, thinking_extra_body, int(args.business_retries), sem)),
                {doc_id} if doc_id else set(),
                f"{doc_id}:{article_no}",
            )
        )
    clause_fragments = await gather_with_doc_progress("08", tasks, total_doc_ids)

    fragments_dir = output_dir / "fragments"
    for payload, fragment in zip(clause_payloads, clause_fragments):
        clause = payload.get("clause") or {}
        doc = payload.get("document") or {}
        fragment_name = f"{str(doc.get('document_id') or '').strip()}_{str(clause.get('article_no') or '').strip()}"
        write_text(fragments_dir / f"{iri_token(fragment_name, 'clause')}.ttl", fragment)

    merged_ttl = schema_text.rstrip() + "\n\n" + "\n\n".join(clause_fragments).strip() + "\n"
    output_ttl = output_dir / "clause_semantics_ontology.ttl"
    output_graphdb_ttl = output_dir / "clause_semantics_ontology.graphdb_ready.ttl"
    output_schema_copy = output_dir / "clause_semantics_ontology_schema.ttl"
    output_sparql = output_dir / "ontology_query_examples.sparql"
    output_summary = output_dir / "clause_semantics_ontology_summary.json"
    write_text(output_ttl, merged_ttl)
    write_text(output_graphdb_ttl, serialize_turtle_with_base_uri(merged_ttl, BASE_URI))
    write_text(output_schema_copy, schema_text)
    write_text(output_sparql, build_sparql_examples())
    write_json(output_summary, build_summary(document_rows, clause_payloads))
    shacl_report: dict[str, Any] | None = None
    if not bool(args.skip_shacl_validation):
        logger.info("开始 SHACL 结构校验 ttl=%s", output_ttl)
        shacl_report = validate_generated_ttl(output_ttl, output_dir)
        logger.info("完成 SHACL 结构校验 conforms=%s violation_count=%s", shacl_report.get("conforms"), shacl_report.get("violation_count"))
    print(f"ontology ttl: {output_ttl}")
    print(f"graphdb ttl: {output_graphdb_ttl}")
    print(f"schema ttl: {output_schema_copy}")
    print(f"sparql examples: {output_sparql}")
    print(f"summary: {output_summary}")
    if shacl_report is not None:
        print(f"shacl report json: {output_dir / 'shacl_validation_report.json'}")
        print(f"shacl report text: {output_dir / 'shacl_validation_report.txt'}")
        print(f"shacl_conforms={str(bool(shacl_report.get('conforms'))).lower()} violation_count={shacl_report.get('violation_count')}")
    print(f"clause_count={len(clause_payloads)}")
    if shacl_report is not None and bool(args.fail_on_shacl_violation) and not bool(shacl_report.get("conforms")):
        return 1
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
