from __future__ import annotations

import argparse
import asyncio
import ast
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

try:
    from dotenv import dotenv_values
except Exception:  # pragma: no cover
    dotenv_values = None

SERVER_ROOT = Path(__file__).resolve().parents[4]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from apps.complaint_app.core.llm.llm_client import AsyncLLMClient, ModelConfig
from utils.llm_thinking import build_thinking_extra_body
from utils.logger import get_logger


logger = get_logger(__name__)
MAX_LOG_PREVIEW = 2000
PAGE_NOISE_RE = re.compile(r"^第\s*\d+\s*页（共\s*\d+\s*页）$")
CHAPTER_TITLE_RE = re.compile(r"^第[一二三四五六七八九十百零0-9]+章")
ALLOWED_MODALITIES = {"应当", "可以", "不得", "必须", "须", "有权"}
ALLOWED_STAGES = {"总则", "分类定义", "立项", "可研评估", "审核", "审批", "实施", "披露备案", "监督管理", "职责分工", "附则"}

LAYER1_SYSTEM_PROMPT = """你正在执行“制度冲突分析试点本体”的第一层任务：制度结构解析。

现在输入给你的是一整份制度的原始 txt 文本。

你的任务是把整份制度拆成“条”列表，并输出一个严格 JSON。

要求：
1. 顶层输出 document 对象
2. 只抽取制度中的“条(article)”作为 articles 数组元素
3. 每个 article 本身只能是一个节点
4. article 下若存在 “（一）”“（二）” 等分项，把它们放入 children 数组
5. 不要发明 body/tail 字段
6. 保留原始文本
7. 如果 parent_id/page_span 无法从文本中确定，分别输出 null 和 []
8. node_id 尽量依据条号生成，例如 article_10
9. 如果无法可靠识别 document_title，可返回空字符串

输出格式固定为：
{
  "document_id": "",
  "document_title": "",
  "source_text": "",
  "articles": [
    {
      "node_id": "",
      "node_type": "article",
      "label": "",
      "title": "",
      "parent_id": null,
      "page_span": [],
      "raw_text": "",
      "children": [
        {
          "node_id": "",
          "node_type": "item",
          "label": "",
          "title": null,
          "parent_id": "",
          "page_span": [],
          "raw_text": ""
        }
      ]
    }
  ]
}

输出要求：
1. 只输出 JSON
2. 不要输出代码块
3. 不要解释"""

LAYER2_SYSTEM_PROMPT = """你正在执行“制度冲突分析试点本体”的第二层任务：本体语义映射。

现在输入给你的是第一层产出的单条 article 结构 JSON。

你的任务是把这条 article 映射为 semantic_json。

输出必须包含 4 个顶层 key：
- source_article
- children
- entities
- norm_units

要求：
1. source_article 直接复用输入中的 article 基本信息
2. children 直接复用输入中的 item 节点
3. entities 只保留后续图谱需要复用的概念实体
4. entities 每项必须包含：entity_id、entity_type、name
5. norm_units 只抽有约束意义、可用于制度冲突分析的规则
6. 一条 article 可以映射出多个 norm_unit
7. threshold 要分成 threshold_raw 和 threshold_normalized
8. 如果某条是原则性条款、定义性条款、说明性条款，没有适合的 norm_unit，可以返回空数组
9. scope 单独承接“单项投资”“一年内累计”“子公司”“煤化工领域以外”等范围信息
10. authority_source 单独承接“董事会授权”“依据章程授权”等权限来源
11. modality 只允许：应当、可以、不得、必须、须、有权
12. stage 只允许：总则、分类定义、立项、可研评估、审核、审批、实施、披露备案、监督管理、职责分工、附则
13. 不要把“授权”放进 modality；不要把“单项投资”“一年内累计投资”放进 stage

entity_type 仅允许：
- Topic
- SubjectRole
- Action
- ObjectEntity
- Modality
- Stage

norm_unit 的最小字段为：
- norm_unit_id
- derived_from
- topic
- subjects
- action
- object
- scope
- modality
- stage
- authority_source
- threshold
- precondition
- exception

threshold 若为空，返回 null。

输出要求：
1. 只输出 JSON
2. 不要输出代码块
3. 不要解释"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="用一个脚本完成制度 txt 的第一层结构解析与第二层本体语义映射。")
    parser.add_argument("--input-path", required=True, help="制度 txt 文件或目录")
    parser.add_argument("--output-dir", required=True, help="输出目录")
    parser.add_argument("--model", default="", help="模型名，默认读取 OPENAI_ANALYSIS_MODEL")
    parser.add_argument("--enable-thinking", action="store_true", help="显式开启模型思考")
    parser.add_argument("--business-retries", type=int, default=3, help="JSON 解析失败时业务重试次数")
    return parser


# 只从 server/.env 加载本脚本所需模型配置，并覆盖当前进程同名变量。
def load_script_env() -> dict[str, str]:
    server_env = SERVER_ROOT / ".env"
    if dotenv_values is None:
        raise RuntimeError("缺少 python-dotenv，无法读取 server/.env")
    values = dotenv_values(server_env)
    env_map = {str(key): str(value).strip() for key, value in values.items() if key and value is not None}
    for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ANALYSIS_MODEL"):
        if key in env_map:
            os.environ[key] = env_map[key]
        else:
            os.environ.pop(key, None)
    return env_map


# 本脚本默认模型仅从 server/.env 读取。
def get_script_analysis_model(env_map: dict[str, str], default: str = "Qwen3-30B-A3B-Instruct-2507") -> str:
    value = str(env_map.get("OPENAI_ANALYSIS_MODEL") or "").strip()
    return value or default


# 枚举输入 txt 文件。
def collect_input_files(path: Path) -> list[Path]:
    resolved = path.expanduser().resolve()
    if resolved.is_file():
        return [resolved]
    if resolved.is_dir():
        files = sorted(item for item in resolved.iterdir() if item.is_file() and item.suffix.lower() == ".txt")
        if not files:
            raise ValueError("input-path 目录下没有 txt 文件")
        return files
    raise ValueError("input-path 必须是 txt 文件或目录")


# 读取输入 txt。
def load_input_text(path: Path) -> str:
    text = clean_document_text(path.read_text(encoding="utf-8")).strip()
    if not text:
        raise ValueError(f"输入 txt 为空: {path}")
    return text


# 清理整份制度文本里的页眉页脚等噪音。
def clean_document_text(text: str) -> str:
    kept_lines: list[str] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if PAGE_NOISE_RE.match(line):
            continue
        kept_lines.append(raw_line)
    cleaned = "\n".join(kept_lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


# 写出 JSON。
def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# 写出文本。
def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


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
            logger.warning("层解析 content 为空，回退字段=%s", field)
            return fallback
    return ""


# 截断日志预览。
def preview_text(text: str, limit: int = MAX_LOG_PREVIEW) -> str:
    source = str(text or "").strip()
    if len(source) <= limit:
        return source
    return f"{source[:limit]}... [truncated {len(source) - limit} chars]"


# 从文本中提取最外层 JSON。
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


# 解析第一层 document JSON。
def parse_layer1_payload(text: str) -> dict:
    fragment = extract_json_fragment(text)
    if not fragment:
        raise ValueError("第一层模型返回中未找到 JSON 对象")
    payload = json.loads(fragment)
    if not isinstance(payload, dict):
        raise ValueError("第一层结果不是 JSON 对象")
    required_keys = {"document_id", "document_title", "source_text", "articles"}
    if not required_keys.issubset(payload.keys()):
        raise ValueError("第一层结果缺少必要字段")
    articles = payload.get("articles")
    if not isinstance(articles, list):
        raise ValueError("第一层 articles 必须是数组")
    for article in articles:
        validate_article_payload(article)
    return payload


# 校验单条 article 结构。
def validate_article_payload(payload: dict) -> None:
    required_keys = {"node_id", "node_type", "label", "title", "parent_id", "page_span", "raw_text", "children"}
    if not isinstance(payload, dict):
        raise ValueError("article 不是对象")
    if not required_keys.issubset(payload.keys()):
        raise ValueError("article 缺少必要字段")
    if str(payload.get("node_type", "")).strip() != "article":
        raise ValueError("article 的 node_type 必须是 article")
    if not isinstance(payload.get("page_span"), list):
        raise ValueError("article 的 page_span 必须是数组")
    if not isinstance(payload.get("children"), list):
        raise ValueError("article 的 children 必须是数组")


# 解析第二层 article semantic JSON。
def parse_layer2_payload(text: str) -> dict:
    fragment = extract_json_fragment(text)
    if not fragment:
        raise ValueError("第二层模型返回中未找到 JSON 对象")
    payload = json.loads(fragment)
    if not isinstance(payload, dict):
        raise ValueError("第二层结果不是 JSON 对象")
    required_keys = {"source_article", "children", "entities", "norm_units"}
    if not required_keys.issubset(payload.keys()):
        raise ValueError("第二层结果缺少必要字段")
    if not isinstance(payload.get("children"), list):
        raise ValueError("第二层 children 必须是数组")
    if not isinstance(payload.get("entities"), list):
        raise ValueError("第二层 entities 必须是数组")
    if not isinstance(payload.get("norm_units"), list):
        raise ValueError("第二层 norm_units 必须是数组")
    for entity in payload.get("entities") or []:
        normalize_entity_payload(entity)
    normalize_layer2_payload(payload)
    return payload


# 统一实体 name/text 字段。
def normalize_entity_payload(entity: dict) -> None:
    if not isinstance(entity, dict):
        return
    name = str(entity.get("name") or entity.get("text") or "").strip()
    entity["name"] = name


# 统一规整第二层 payload，收敛 modality/action 口径。
def normalize_layer2_payload(payload: dict) -> None:
    source_article = dict(payload.get("source_article") or {})
    source_article["raw_text"] = clean_document_text(str(source_article.get("raw_text") or ""))
    payload["source_article"] = source_article
    children = []
    for child in list(payload.get("children") or []):
        child_payload = dict(child or {})
        child_payload["raw_text"] = clean_document_text(str(child_payload.get("raw_text") or ""))
        children.append(child_payload)
    payload["children"] = children
    entity_lookup = build_entity_lookup(payload)
    node_text_lookup = build_node_text_lookup(source_article, children)
    for norm_unit in payload.get("norm_units") or []:
        resolve_norm_unit_entities(norm_unit, entity_lookup)
        normalize_norm_unit_payload(norm_unit, source_article, node_text_lookup)


# 调整单条规范单元里的 modality/action 口径。
def normalize_norm_unit_payload(norm_unit: dict, source_article: dict | None, node_text_lookup: dict[str, str]) -> None:
    if not isinstance(norm_unit, dict):
        return
    norm_unit["topic"] = scalar_text(norm_unit.get("topic"))
    norm_unit["action"] = scalar_text(norm_unit.get("action"))
    norm_unit["scope"] = scalar_text(norm_unit.get("scope"))
    norm_unit["modality"] = scalar_text(norm_unit.get("modality"))
    norm_unit["stage"] = scalar_text(norm_unit.get("stage"))
    norm_unit["authority_source"] = scalar_text(norm_unit.get("authority_source"))
    norm_unit["precondition"] = scalar_text(norm_unit.get("precondition"))
    norm_unit["exception"] = scalar_text(norm_unit.get("exception"))
    action = str(norm_unit.get("action") or "").strip()
    modality = str(norm_unit.get("modality") or "").strip()
    scope = str(norm_unit.get("scope") or "").strip()
    authority_source = str(norm_unit.get("authority_source") or "").strip()
    stage = str(norm_unit.get("stage") or "").strip()
    source_context = build_norm_unit_source_context(norm_unit, source_article or {}, node_text_lookup)
    prefix_pairs = [
        ("不得", "不得"),
        ("禁止", "不得"),
        ("严禁", "不得"),
        ("应当", "应当"),
        ("必须", "必须"),
        ("须", "须"),
        ("可以", "可以"),
        ("有权", "有权"),
    ]
    for prefix, normalized_modality in prefix_pairs:
        if action.startswith(prefix):
            remaining = action[len(prefix):].strip()
            norm_unit["modality"] = normalized_modality
            norm_unit["action"] = remaining or action
            action = str(norm_unit.get("action") or "").strip()
            modality = str(norm_unit.get("modality") or "").strip()
            break
    if action == "适用":
        norm_unit["modality"] = None
    elif modality in ALLOWED_MODALITIES:
        norm_unit["modality"] = modality
    else:
        if modality and ("授权" in modality or "章程" in modality or "董事会" in modality):
            norm_unit["authority_source"] = authority_source or modality
            norm_unit["modality"] = "有权" if "有权" in source_context or action in {"决定", "审批", "审议批准"} else None
        else:
            norm_unit["modality"] = None if not modality else modality

    if stage and stage not in ALLOWED_STAGES:
        if should_treat_as_scope(stage):
            norm_unit["scope"] = scope or normalize_scope_text(stage)
        norm_unit["stage"] = infer_stage_from_norm_unit(norm_unit, source_context)
    elif stage in ALLOWED_STAGES:
        norm_unit["stage"] = stage
    else:
        norm_unit["stage"] = infer_stage_from_norm_unit(norm_unit, source_context)

    if not norm_unit.get("scope"):
        inferred_scope = infer_scope_from_norm_unit(norm_unit, source_context)
        if inferred_scope:
            norm_unit["scope"] = inferred_scope

    normalized_scope = normalize_scope_text(str(norm_unit.get("scope") or "").strip())
    norm_unit["scope"] = normalized_scope or None
    if not norm_unit.get("object") and norm_unit.get("scope") and str(norm_unit.get("action") or "").strip() == "适用":
        norm_unit["object"] = norm_unit.get("scope")
        norm_unit["scope"] = None

    if not norm_unit.get("authority_source") or str(norm_unit.get("authority_source")).strip() == "授权":
        inferred_authority = infer_authority_source_from_norm_unit(norm_unit, source_context)
        if inferred_authority:
            norm_unit["authority_source"] = inferred_authority
    elif norm_unit.get("authority_source"):
        norm_unit["authority_source"] = normalize_authority_source_text(str(norm_unit.get("authority_source")).strip(), source_context)

    if norm_unit.get("threshold") and isinstance(norm_unit["threshold"], dict):
        normalized = norm_unit["threshold"].get("threshold_normalized")
        if isinstance(normalized, dict) and normalized.get("scope") and not norm_unit.get("scope"):
            norm_unit["scope"] = normalize_scope_text(str(normalized.get("scope")).strip())


# 把 norm_unit 中引用的 entity_id 先替换为真实名称。
def resolve_norm_unit_entities(norm_unit: dict, entity_lookup: dict[str, str]) -> None:
    if not isinstance(norm_unit, dict):
        return
    for key in ("topic", "action", "object", "scope", "modality", "stage", "authority_source"):
        norm_unit[key] = resolve_entity_name(entity_lookup, norm_unit.get(key))
    norm_unit["subjects"] = [resolve_entity_name(entity_lookup, item) for item in as_list(norm_unit.get("subjects"))]


# 为第二层归一化准备条文与分项原文映射。
def build_node_text_lookup(source_article: dict, children: list[dict]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    article_id = str(source_article.get("node_id") or "").strip()
    if article_id:
        lookup[article_id] = str(source_article.get("raw_text") or "")
    for child in children:
        node_id = str(child.get("node_id") or "").strip()
        if node_id:
            lookup[node_id] = str(child.get("raw_text") or "")
    return lookup


# 汇总 norm_unit 可用的原文上下文。
def build_norm_unit_source_context(norm_unit: dict, source_article: dict, node_text_lookup: dict[str, str]) -> str:
    derived_from = str(norm_unit.get("derived_from") or "").strip()
    return " ".join(
        [
            str(source_article.get("title") or ""),
            str(source_article.get("raw_text") or ""),
            node_text_lookup.get(derived_from, ""),
            str(norm_unit.get("precondition") or ""),
            str(norm_unit.get("exception") or ""),
        ]
    ).strip()


# 基于当前 norm_unit 反推流程阶段。
def infer_stage_from_norm_unit(norm_unit: dict, source_context: str = "") -> str | None:
    raw = " ".join(
        [
            str(norm_unit.get("topic") or ""),
            str(norm_unit.get("action") or ""),
            str(norm_unit.get("object") or ""),
            str(norm_unit.get("scope") or ""),
            str(norm_unit.get("precondition") or ""),
            str(norm_unit.get("exception") or ""),
            str(source_context or ""),
        ]
    )
    if any(keyword in raw for keyword in ("适用范围", "总则", "基本原则", "宗旨和根据")) or str(norm_unit.get("action") or "").strip() == "适用":
        return "总则"
    if any(keyword in raw for keyword in ("施行日期", "施行", "附则", "解释权")):
        return "附则"
    if any(keyword in raw for keyword in ("立项", "提出项目", "提出建议")):
        return "立项"
    if any(keyword in raw for keyword in ("可行性研究", "风险评估", "可研")):
        return "可研评估"
    if any(keyword in raw for keyword in ("技术评审", "经济评审", "法律评审", "审核", "前置研究")):
        return "审核"
    if any(keyword in raw for keyword in ("审批", "审议批准", "决定", "股东会", "董事会", "董事长", "总经理")):
        return "审批"
    if any(keyword in raw for keyword in ("支付投资资金", "协议", "起草", "合法性审核", "实施")):
        return "实施"
    if any(keyword in raw for keyword in ("披露", "备案", "公告")):
        return "披露备案"
    if any(keyword in raw for keyword in ("督导", "后评价", "减值", "回收", "转让", "收益", "监督")):
        return "监督管理"
    if any(keyword in raw for keyword in ("职责", "负责", "参与")):
        return "职责分工"
    if any(keyword in raw for keyword in ("施行", "解释", "附则")):
        return "附则"
    return None


# 基于当前 norm_unit 反推范围。
def infer_scope_from_norm_unit(norm_unit: dict, source_context: str = "") -> str | None:
    raw_parts = [str(norm_unit.get("object") or ""), str(norm_unit.get("precondition") or ""), str(norm_unit.get("exception") or "")]
    threshold = norm_unit.get("threshold") or {}
    if isinstance(threshold, dict):
        raw_parts.append(str(threshold.get("threshold_raw") or ""))
        normalized = threshold.get("threshold_normalized")
        if isinstance(normalized, dict):
            raw_parts.append(str(normalized.get("scope") or ""))
    raw = " ".join(raw_parts)
    if "一年内累计" in raw or "累计" in raw:
        return "一年内累计投资"
    if "单项投资" in raw:
        return "单项投资"
    if "子公司" in raw:
        return "子公司"
    if "分公司" in raw:
        return "分公司"
    if "煤化工领域以外" in raw:
        return "煤化工领域以外"
    return None


# 基于当前 norm_unit 反推权限来源。
def infer_authority_source_from_norm_unit(norm_unit: dict, source_context: str = "") -> str | None:
    action = str(norm_unit.get("action") or "").strip()
    modality = str(norm_unit.get("modality") or "").strip()
    if "授权" not in source_context and "授权" not in action and modality != "有权":
        return None
    raw = " ".join(
        [
            str(norm_unit.get("precondition") or ""),
            str(norm_unit.get("exception") or ""),
            str((norm_unit.get("threshold") or {}).get("threshold_raw") or "") if isinstance(norm_unit.get("threshold"), dict) else "",
            str(source_context or ""),
        ]
    )
    if "董事会授权" in raw:
        return "董事会授权"
    if "股东会授权" in raw:
        return "股东会授权"
    if "章程授权" in raw or re.search(r"(依据|按照|根据).{0,8}章程.{0,4}授权", raw):
        return "章程授权"
    return None


# 只把真正的范围信息塞到 scope。
def should_treat_as_scope(value: str) -> bool:
    text = str(value or "").strip()
    return any(keyword in text for keyword in ("单项", "累计", "子公司", "分公司", "领域", "项目", "投资"))


# 统一范围文本。
def normalize_scope_text(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    mapping = {
        "single": "单项投资",
        "cumulative_annual": "一年内累计投资",
        "single_investment": "单项投资",
        "annual_cumulative_investment": "一年内累计投资",
    }
    if text in mapping:
        return mapping[text]
    return text


# 统一权限来源文本。
def normalize_authority_source_text(value: str, source_context: str = "") -> str:
    text = str(value or "").strip()
    raw = f"{text} {source_context}".strip()
    if "董事会授权" in raw:
        return "董事会授权"
    if "股东会授权" in raw:
        return "股东会授权"
    if "章程" in raw and "授权" in raw:
        return "章程授权"
    return text


# 规范化条文本体节点，清理噪音字段。
def sanitize_article_payload(article: dict) -> dict:
    payload = dict(article or {})
    payload["raw_text"] = clean_document_text(str(payload.get("raw_text") or ""))
    children = []
    for child in list(payload.get("children") or []):
        child_payload = dict(child or {})
        child_payload["raw_text"] = clean_document_text(str(child_payload.get("raw_text") or ""))
        children.append(child_payload)
    payload["children"] = children
    return payload


# 提取文档标题，提取不到时回退文件名。
def infer_document_title(payload: dict, file_path: Path) -> str:
    current = str(payload.get("document_title") or "").strip()
    if current and not CHAPTER_TITLE_RE.match(current):
        return current
    source_text = str(payload.get("source_text") or "")
    for raw_line in source_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if CHAPTER_TITLE_RE.match(line):
            continue
        if len(line) <= 80 and re.search(r"(办法|制度|规定|细则|规则|管理)$", line):
            return line
    return file_path.stem


# 第一层结果后处理，补齐文档主键并清洗 article 文本。
def enrich_layer1_payload(payload: dict, file_path: Path) -> dict:
    enriched = dict(payload or {})
    enriched["source_file"] = file_path.name
    enriched["source_text"] = clean_document_text(str(enriched.get("source_text") or ""))
    document_title = infer_document_title(enriched, file_path)
    enriched["document_title"] = document_title
    document_id = str(enriched.get("document_id") or "").strip()
    enriched["document_id"] = document_id or f"document_{iri_name(file_path.stem)}"
    enriched["articles"] = [sanitize_article_payload(article) for article in list(enriched.get("articles") or [])]
    return enriched


# 调用模型并带业务重试。
async def call_llm_with_retries(llm: AsyncLLMClient, system_prompt: str, user_prompt: str, model: str, thinking_extra_body: dict[str, Any] | None, business_retries: int, parse_func) -> dict:
    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]
    last_error = ""
    last_content = ""
    attempts = max(1, int(business_retries))
    for attempt in range(1, attempts + 1):
        resp = await llm.chat(messages=messages, model=model, temperature=0.0, extra_body=thinking_extra_body)
        content = extract_message_text(resp)
        last_content = content
        logger.info("层解析模型返回 attempt=%s/%s content=%s", attempt, attempts, preview_text(content))
        try:
            return parse_func(content)
        except Exception as parse_err:  # pylint: disable=broad-except
            last_error = f"{parse_err}; 模型输出预览: {preview_text(content)}"
            logger.warning("层解析结果解析失败 attempt=%s/%s err=%s", attempt, attempts, last_error)
    raise ValueError(last_error or f"模型返回无法解析。最后输出：{preview_text(last_content)}")


# 构建第一层输入提示。
def build_layer1_user_prompt(document_text: str, source_name: str) -> str:
    return f"来源文件名：{source_name}\n\n制度原文如下：\n{document_text}"


# 构建第二层输入提示。
def build_layer2_user_prompt(article_tree: dict) -> str:
    return json.dumps(article_tree, ensure_ascii=False, indent=2)


# 归一化为可用 IRI 尾缀。
def iri_name(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff]", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "unknown"


# 统一把字符串或数组变成数组。
def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if item not in (None, "", [])]
    return [value]


# 按实体类型映射到类名。
def entity_class_name(entity_type: str) -> str:
    mapping = {
        "Topic": "Topic",
        "SubjectRole": "SubjectRole",
        "Action": "Action",
        "ObjectEntity": "ObjectEntity",
        "Modality": "Modality",
        "Stage": "Stage",
    }
    return mapping.get(str(entity_type).strip(), "Entity")


# 生成稳定的实体 IRI。
def entity_iri(entity_type: str, name: str) -> str:
    prefix_map = {
        "Topic": "topic",
        "SubjectRole": "subject",
        "Action": "action",
        "ObjectEntity": "object",
        "Modality": "modality",
        "Stage": "stage",
    }
    prefix = prefix_map.get(str(entity_type).strip(), "entity")
    return iri_name(f"{prefix}_{name}")


# 阈值是否真实存在。
def has_threshold(threshold: Any) -> bool:
    if not isinstance(threshold, dict):
        return False
    raw = str(threshold.get("threshold_raw") or "").strip()
    normalized = threshold.get("threshold_normalized")
    if raw:
        return True
    if isinstance(normalized, dict):
        return any(normalized.get(key) not in (None, "", []) for key in ("metric", "operator", "value", "unit", "time_window"))
    return False


# 统一 threshold_normalized 的形态，兼容 dict / string / null。
def normalize_threshold_normalized(value: Any) -> tuple[dict[str, Any], str]:
    if isinstance(value, dict):
        return value, ""
    if value is None:
        return {}, ""
    text = str(value).strip()
    return {}, text


# 汇总整份制度的实体，并生成稳定 IRI。
def collect_entity_registry(article_semantics: list[dict]) -> dict[tuple[str, str], str]:
    registry: dict[tuple[str, str], str] = {}

    def register(entity_type: str, name_value: Any) -> None:
        name = str(name_value or "").strip()
        if not name:
            return
        key = (str(entity_type).strip(), name)
        if key not in registry:
            registry[key] = entity_iri(key[0], key[1])

    for payload in article_semantics:
        entity_lookup = build_entity_lookup(payload)
        for entity in list(payload.get("entities") or []):
            register(str(entity.get("entity_type") or "").strip(), entity.get("name") or entity.get("text"))
        for norm_unit in list(payload.get("norm_units") or []):
            register("Topic", resolve_entity_name(entity_lookup, norm_unit.get("topic")))
            for subject in [resolve_entity_name(entity_lookup, item) for item in as_list(norm_unit.get("subjects"))]:
                register("SubjectRole", subject)
            register("Action", resolve_entity_name(entity_lookup, norm_unit.get("action")))
            for object_value in [resolve_entity_name(entity_lookup, item) for item in as_list(norm_unit.get("object"))]:
                register("ObjectEntity", object_value)
            register("Modality", resolve_entity_name(entity_lookup, norm_unit.get("modality")))
            register("Stage", resolve_entity_name(entity_lookup, norm_unit.get("stage")))
    return registry


# 建立单条 article 的 entity_id -> name 映射。
def build_entity_lookup(payload: dict) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for entity in list(payload.get("entities") or []):
        entity_id = str(entity.get("entity_id") or "").strip()
        entity_name = str(entity.get("name") or entity.get("text") or "").strip()
        if entity_id and entity_name:
            lookup[entity_id] = entity_name
    return lookup


# 解析 norm_unit 中引用到的实体 id。
def resolve_entity_name(entity_lookup: dict[str, str], value: Any) -> Any:
    if isinstance(value, str):
        return entity_lookup.get(value, value)
    if isinstance(value, list):
        return [entity_lookup.get(str(item), item) if isinstance(item, str) else item for item in value]
    return value


# 把模型返回的标量字段压成稳定文本，兼容数组和字符串化数组。
def scalar_text(value: Any) -> str | None:
    if value in (None, "", []):
        return None
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
        return "、".join(dict.fromkeys(items)) if items else None
    text = str(value).strip()
    if not text:
        return None
    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = ast.literal_eval(text)
            if isinstance(parsed, list):
                items = [str(item).strip() for item in parsed if str(item).strip()]
                return "、".join(dict.fromkeys(items)) if items else None
        except Exception:
            return text
    return text


# 生成单条 article 的 TTL 片段。
def convert_article_semantic_to_ttl(payload: dict, entity_registry: dict[tuple[str, str], str]) -> str:
    lines = []
    source_article = dict(payload.get("source_article") or {})
    children = list(payload.get("children") or [])
    norm_units = list(payload.get("norm_units") or [])
    entity_lookup = build_entity_lookup(payload)

    article_id = iri_name(source_article.get("node_id"))
    article_lines = [
        f"ex:{article_id} a ex:Article ;",
        f'    ex:labelText "{str(source_article.get("label", "")).strip()}" ;',
        f'    ex:titleText "{str(source_article.get("title", "")).strip()}" ;',
        f'    ex:rawText """{str(source_article.get("raw_text", "")).strip()}""" ;',
    ]
    article_pages = list(source_article.get("page_span") or [])
    if article_pages:
        article_lines.append(f"    ex:pageStart {int(article_pages[0] or 0)} ;")
        article_lines.append(f"    ex:pageEnd {int(article_pages[-1] or 0)} ;")
    last_line = article_lines.pop()
    article_lines.append(last_line.rstrip(" ;") + " .")
    lines.extend(article_lines + [""])

    for child in children:
        child_id = iri_name(child.get("node_id"))
        child_lines = [
            f"ex:{child_id} a ex:Item ;",
            f'    ex:labelText "{str(child.get("label", "")).strip()}" ;',
            f'    ex:rawText """{str(child.get("raw_text", "")).strip()}""" ;',
        ]
        child_pages = list(child.get("page_span") or [])
        if child_pages:
            child_lines.append(f"    ex:pageStart {int(child_pages[0] or 0)} ;")
            child_lines.append(f"    ex:pageEnd {int(child_pages[-1] or 0)} ;")
        last_line = child_lines.pop()
        child_lines.append(last_line.rstrip(" ;") + " .")
        lines.extend(child_lines + ["", f"ex:{article_id} ex:hasChild ex:{child_id} .", ""])

    for norm_unit in norm_units:
        norm_unit_id = iri_name(norm_unit.get("norm_unit_id"))
        derived_from = iri_name(norm_unit.get("derived_from"))
        topic_value = resolve_entity_name(entity_lookup, norm_unit.get("topic"))
        subject_values = [resolve_entity_name(entity_lookup, item) for item in as_list(norm_unit.get("subjects"))]
        action_value = resolve_entity_name(entity_lookup, norm_unit.get("action"))
        object_values = [resolve_entity_name(entity_lookup, item) for item in as_list(norm_unit.get("object"))]
        scope_value = resolve_entity_name(entity_lookup, norm_unit.get("scope"))
        modality_value = resolve_entity_name(entity_lookup, norm_unit.get("modality"))
        stage_value = resolve_entity_name(entity_lookup, norm_unit.get("stage"))
        authority_source_value = resolve_entity_name(entity_lookup, norm_unit.get("authority_source"))
        norm_lines = [
            f"ex:{norm_unit_id} a ex:NormUnit ;",
            f"    ex:derivedFrom ex:{derived_from} ;",
        ]
        if topic_value:
            topic_key = ("Topic", str(topic_value).strip())
            topic_iri = entity_registry.get(topic_key) or entity_iri(topic_key[0], topic_key[1])
            norm_lines.append(f"    ex:hasTopic ex:{topic_iri} ;")
        for subject in subject_values:
            subject_key = ("SubjectRole", str(subject).strip())
            subject_iri = entity_registry.get(subject_key) or entity_iri(subject_key[0], subject_key[1])
            norm_lines.append(f"    ex:hasSubject ex:{subject_iri} ;")
        if action_value:
            action_key = ("Action", str(action_value).strip())
            action_iri = entity_registry.get(action_key) or entity_iri(action_key[0], action_key[1])
            norm_lines.append(f"    ex:hasAction ex:{action_iri} ;")
        for object_value in object_values:
            object_key = ("ObjectEntity", str(object_value).strip())
            object_iri = entity_registry.get(object_key) or entity_iri(object_key[0], object_key[1])
            norm_lines.append(f"    ex:hasObject ex:{object_iri} ;")
        if scope_value:
            norm_lines.append(f'    ex:scopeText """{str(scope_value).strip()}""" ;')
        if modality_value:
            modality_key = ("Modality", str(modality_value).strip())
            modality_iri = entity_registry.get(modality_key) or entity_iri(modality_key[0], modality_key[1])
            norm_lines.append(f"    ex:hasModality ex:{modality_iri} ;")
        if stage_value:
            stage_key = ("Stage", str(stage_value).strip())
            stage_iri = entity_registry.get(stage_key) or entity_iri(stage_key[0], stage_key[1])
            norm_lines.append(f"    ex:hasStage ex:{stage_iri} ;")
        if authority_source_value:
            norm_lines.append(f'    ex:authoritySourceText """{str(authority_source_value).strip()}""" ;')
        if norm_unit.get("precondition"):
            norm_lines.append(f'    ex:precondition """{str(norm_unit.get("precondition")).strip()}""" ;')
        if norm_unit.get("exception"):
            norm_lines.append(f'    ex:exceptionText """{str(norm_unit.get("exception")).strip()}""" ;')

        threshold = norm_unit.get("threshold")
        threshold_id = ""
        if has_threshold(threshold):
            threshold_id = iri_name(threshold.get("threshold_id") or f"{norm_unit_id}_threshold")
            norm_lines.append(f"    ex:hasThreshold ex:{threshold_id} ;")

        last_line = norm_lines.pop()
        norm_lines.append(last_line.rstrip(" ;") + " .")
        lines.extend(norm_lines + ["", f"ex:{article_id} ex:hasNormUnit ex:{norm_unit_id} .", ""])

        if threshold_id:
            normalized, normalized_text = normalize_threshold_normalized((threshold or {}).get("threshold_normalized"))
            threshold_lines = [
                f"ex:{threshold_id} a ex:Threshold ;",
                f'    ex:thresholdRaw """{str((threshold or {}).get("threshold_raw") or "").strip()}""" ;',
            ]
            if normalized.get("metric"):
                threshold_lines.append(f'    ex:metric "{str(normalized.get("metric")).strip()}" ;')
            if normalized.get("operator"):
                threshold_lines.append(f'    ex:operator "{str(normalized.get("operator")).strip()}" ;')
            if normalized.get("value") is not None:
                threshold_lines.append(f'    ex:value "{normalized.get("value")}"^^xsd:decimal ;')
            if normalized.get("unit"):
                threshold_lines.append(f'    ex:unit "{str(normalized.get("unit")).strip()}" ;')
            if normalized.get("time_window"):
                threshold_lines.append(f'    ex:timeWindow "{str(normalized.get("time_window")).strip()}" ;')
            if normalized.get("amount") is not None:
                threshold_lines.append(f'    ex:amount "{normalized.get("amount")}"^^xsd:decimal ;')
            if normalized.get("percentage") is not None:
                threshold_lines.append(f'    ex:percentage "{normalized.get("percentage")}"^^xsd:decimal ;')
            if normalized.get("upper_bound") is not None:
                threshold_lines.append(f'    ex:upperBound "{normalized.get("upper_bound")}"^^xsd:decimal ;')
            if normalized.get("lower_bound") is not None:
                threshold_lines.append(f'    ex:lowerBound "{normalized.get("lower_bound")}"^^xsd:decimal ;')
            if normalized.get("upper_bound_ratio") is not None:
                threshold_lines.append(f'    ex:upperBoundRatio "{normalized.get("upper_bound_ratio")}"^^xsd:decimal ;')
            if normalized.get("lower_bound_ratio") is not None:
                threshold_lines.append(f'    ex:lowerBoundRatio "{normalized.get("lower_bound_ratio")}"^^xsd:decimal ;')
            if normalized.get("inclusive") is not None:
                threshold_lines.append(f'    ex:inclusive "{str(bool(normalized.get("inclusive"))).lower()}" ;')
            if normalized.get("scope"):
                threshold_lines.append(f'    ex:scopeText "{normalize_scope_text(str(normalized.get("scope")).strip())}" ;')
            if normalized.get("base"):
                threshold_lines.append(f'    ex:baseText "{str(normalized.get("base")).strip()}" ;')
            if normalized.get("reference"):
                threshold_lines.append(f'    ex:referenceText "{str(normalized.get("reference")).strip()}" ;')
            if normalized_text:
                threshold_lines.append(f'    ex:normalizedText """{normalized_text}""" ;')
            last_line = threshold_lines.pop()
            threshold_lines.append(last_line.rstrip(" ;") + " .")
            lines.extend(threshold_lines + [""])

    return "\n".join(lines).strip()


# 合并整份制度的 TTL。
def convert_document_semantics_to_ttl(document_payload: dict, article_semantics: list[dict]) -> str:
    lines = [
        "@prefix ex: <http://example.com/complaint-ontology/> .",
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        "",
    ]
    raw_document_id = str(document_payload.get("document_id") or "").strip()
    source_file = str(document_payload.get("source_file") or "").strip()
    raw_document_title = str(document_payload.get("document_title") or "").strip()
    document_id = iri_name(raw_document_id or f"document_{source_file or 'unknown'}")
    document_title = raw_document_title or source_file
    lines.extend(
        [
            f"ex:{document_id} a ex:Document ;",
            f'    ex:titleText "{document_title}" ;',
            f'    ex:sourceFile "{source_file}" .',
            "",
        ]
    )
    entity_registry = collect_entity_registry(article_semantics)
    for (entity_type, entity_name), iri in sorted(entity_registry.items(), key=lambda item: item[1]):
        entity_class = entity_class_name(entity_type)
        lines.extend(
            [
                f"ex:{iri} a ex:{entity_class} ;",
                f'    ex:name "{entity_name}" .',
                "",
            ]
        )
    for article in document_payload.get("articles") or []:
        article_id = iri_name(article.get("node_id"))
        lines.append(f"ex:{document_id} ex:hasArticle ex:{article_id} .")
    if document_payload.get("articles"):
        lines.append("")
    for payload in article_semantics:
        block = convert_article_semantic_to_ttl(payload, entity_registry)
        if block:
            lines.append(block)
            lines.append("")
    return "\n".join(line for line in lines if line is not None).strip() + "\n"


# 处理一份制度文件。
async def process_document_file(llm: AsyncLLMClient, file_path: Path, output_dir: Path, model: str, thinking_extra_body: dict[str, Any] | None, business_retries: int) -> None:
    document_text = load_input_text(file_path)
    document_output_dir = output_dir / file_path.stem
    document_output_dir.mkdir(parents=True, exist_ok=True)

    layer1_payload = await call_llm_with_retries(
        llm,
        LAYER1_SYSTEM_PROMPT,
        build_layer1_user_prompt(document_text, file_path.name),
        model,
        thinking_extra_body,
        int(business_retries),
        parse_layer1_payload,
    )
    layer1_payload = enrich_layer1_payload(layer1_payload, file_path)
    write_json(document_output_dir / "layer1_articles.json", layer1_payload)

    article_semantics: list[dict] = []
    semantic_dir = document_output_dir / "layer2_semantic"
    semantic_dir.mkdir(parents=True, exist_ok=True)

    for article in layer1_payload.get("articles") or []:
        layer2_payload = await call_llm_with_retries(
            llm,
            LAYER2_SYSTEM_PROMPT,
            build_layer2_user_prompt(article),
            model,
            thinking_extra_body,
            int(business_retries),
            parse_layer2_payload,
        )
        article_semantics.append(layer2_payload)
        write_json(semantic_dir / f"{article.get('node_id', 'unknown')}.json", layer2_payload)

    document_semantic_payload = {
        "document_id": layer1_payload.get("document_id"),
        "document_title": layer1_payload.get("document_title"),
        "source_file": file_path.name,
        "article_semantics": article_semantics,
    }
    write_json(document_output_dir / "layer2_semantic.json", document_semantic_payload)

    ttl_text = convert_document_semantics_to_ttl(layer1_payload, article_semantics)
    write_text(document_output_dir / "layer2_instances.ttl", ttl_text)

    print(f"制度文件: {file_path}")
    print(f"输出目录: {document_output_dir}")
    print(f"第一层输出: {document_output_dir / 'layer1_articles.json'}")
    print(f"第二层输出: {document_output_dir / 'layer2_semantic.json'}")
    print(f"TTL 输出: {document_output_dir / 'layer2_instances.ttl'}")


async def async_main() -> int:
    args = build_parser().parse_args()
    env_map = load_script_env()
    input_files = collect_input_files(Path(args.input_path))
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    model = str(args.model or "").strip() or get_script_analysis_model(env_map)
    thinking_extra_body = build_thinking_extra_body(model, bool(args.enable_thinking))
    llm = AsyncLLMClient(config=ModelConfig(default_model=model), name="ontology_article_builder")

    for file_path in input_files:
        await process_document_file(llm, file_path, output_dir, model, thinking_extra_body, int(args.business_retries))
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
