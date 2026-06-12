from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from apps.complaint_app.core.llm.llm_client import AsyncLLMClient
from apps.complaint_app.core.model_config import get_skill_model
from apps.complaint_app.opencode_runtime.session import Session
from apps.complaint_app.opencode_runtime.session_prompt import SessionPrompt
from apps.complaint_app.opencode_runtime.skills.parser import parse_frontmatter
from apps.complaint_app.opencode_runtime.tools.base_tool import ToolExecutor
from apps.complaint_app.opencode_runtime.tools.builtin.shell_tool import registry as shell_registry
from utils.logger import get_logger

logger = get_logger(__name__)

SKILL_PATH = Path(__file__).resolve().parents[2] / "opencode_runtime" / "skills" / ".complaint_skills" / "complaint-law-rationality" / "SKILL.md"
ALLOWED_CASE_TYPES = {"law_exists_related", "law_exists_unrelated", "law_not_found", "no_law_mentioned"}
CASE_TYPE_REASONABLE_STATUS = {
    "law_exists_related": "合理",
    "law_exists_unrelated": "不合理",
    "law_not_found": "不合理",
    "no_law_mentioned": "合理",
}


def _law_rationality_max_attempts() -> int:
    """读取合理性分析 skill 最大尝试次数。"""
    raw = os.getenv("COMPLAINT_LAW_RATIONALITY_MAX_ATTEMPTS", "2").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 2
    return max(value, 1)


def _extract_json_object(text: str) -> dict[str, Any]:
    """从 skill 输出中提取 JSON 对象。"""
    value = str(text or "").strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if lines:
            lines = lines[1:]
        while lines and lines[-1].strip() == "```":
            lines.pop()
        value = "\n".join(lines).strip()
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        pass
    start = value.find("{")
    end = value.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        parsed = json.loads(value[start : end + 1])
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _render_skill_prompt() -> str:
    """读取并渲染 complaint-law-rationality skill。"""
    content = SKILL_PATH.read_text(encoding="utf-8")
    metadata: dict[str, Any] = {}
    body = content
    try:
        metadata, body = parse_frontmatter(content)
    except Exception as exc:
        logger.warning("[law_rationality_skill] parse_frontmatter_failed err=%s", exc)
    rendered = str(body or "")
    skill_metadata = metadata.get("metadata") if isinstance(metadata.get("metadata"), dict) else {}
    for key, value in skill_metadata.items():
        rendered = rendered.replace(f"<metadata.{key}>", str(value))
    return f"## Skill: complaint-law-rationality\n\n{rendered.strip()}"


def _normalize_str_list(value: Any) -> list[str]:
    """规范化字符串列表。"""
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _normalize_article_list(value: Any) -> list[dict[str, str]]:
    """规范化条款摘要列表。"""
    rows: list[dict[str, str]] = []
    if not isinstance(value, list):
        return rows
    for item in value:
        if not isinstance(item, dict):
            continue
        law = str(item.get("law") or "").strip()
        article = str(item.get("article") or item.get("item") or "").strip()
        text_summary = str(item.get("text_summary") or "").strip()
        if law or article or text_summary:
            rows.append({"law": law, "article": article, "text_summary": text_summary})
    return rows


def _normalize_candidate_laws(value: Any) -> list[str]:
    """规范化候选法律列表。"""
    rows: list[str] = []
    if not isinstance(value, list):
        return rows
    for item in value:
        if isinstance(item, dict):
            law = str(item.get("law") or item.get("name") or "").strip()
        else:
            law = str(item or "").strip()
        if law and law not in rows:
            rows.append(law)
    return rows[:5]


def _validate_result(payload: dict[str, Any], parsed: dict[str, Any]) -> dict[str, Any]:
    """校验并规范化合理性分析结果。"""
    index = int(payload.get("index") or 0)
    complaint_id = str(payload.get("complaint_id") or f"complaint_{index + 1}").strip()
    case_type = str(parsed.get("case_type") or "").strip()
    if case_type not in ALLOWED_CASE_TYPES:
        raise ValueError(f"invalid case_type: {case_type}")
    reasonable_status = CASE_TYPE_REASONABLE_STATUS.get(case_type) or "不合理"
    conclusion = str(parsed.get("conclusion") or "").strip()
    if not conclusion:
        raise ValueError("conclusion is empty")
    return {
        "index": index,
        "complaint_id": str(parsed.get("complaint_id") or complaint_id).strip() or complaint_id,
        "case_type": case_type,
        "reasonable_status": reasonable_status,
        "scene_keywords": _normalize_str_list(parsed.get("scene_keywords")),
        "related_articles": _normalize_article_list(parsed.get("related_articles")),
        "candidate_laws": _normalize_candidate_laws(parsed.get("candidate_laws")),
        "conclusion": conclusion,
    }


def build_law_rationality_fallback(payload: dict[str, Any], reason: str) -> dict[str, Any]:
    """构建合理性分析失败时的兜底结果。"""
    index = int(payload.get("index") or 0)
    complaint_id = str(payload.get("complaint_id") or f"complaint_{index + 1}").strip()
    return {
        "index": index,
        "complaint_id": complaint_id,
        "case_type": "no_law_mentioned",
        "reasonable_status": "不合理",
        "scene_keywords": [],
        "related_articles": [],
        "candidate_laws": [],
        "conclusion": "该举报事项暂无法完成合理性分析，需人工复核。",
    }


async def analyze_complaint_item_with_law_rationality(payload: dict[str, Any]) -> dict[str, Any]:
    """调用合理性分析 skill 分析单个举报事项。"""
    user_input = json.dumps(payload, ensure_ascii=False, indent=2)
    complaint_id = str(payload.get("complaint_id") or "")
    trace_context = payload.get("trace_context") if isinstance(payload.get("trace_context"), dict) else {}
    complaint_index = str(trace_context.get("complaint_index") or "")
    complaint_hash = str(trace_context.get("complaint_hash") or "")
    issue_version = str(trace_context.get("issue_analysis_version") or "")
    last_reason = "合理性分析失败，需人工复核。"
    for attempt in range(1, _law_rationality_max_attempts() + 1):
        executor = ToolExecutor()
        executor.register(shell_registry())
        session = Session(session_id=f"law_rationality_{uuid.uuid4().hex}")
        prompt = SessionPrompt(
            llm_client=AsyncLLMClient(
                name="complaint_law_rationality_skill",
                metric_stage="complaint_law_rationality_skill",
                metric_context={
                    "session_id": session.session_id,
                    "business_stage": "complaint_issue_analysis",
                    "skill_name": "complaint-law-rationality",
                    "complaint_id": complaint_id,
                    "attempt": attempt,
                    **trace_context,
                },
            ),
            tool_executor=executor,
        )
        try:
            logger.info("[law_rationality_skill] attempt_start user_id=%s base_id=%s issue_analysis_version=%s session_id=%s complaint_index=%s complaint_id=%s complaint_hash=%s attempt=%s", trace_context.get("user_id") or "", trace_context.get("base_id") or "", issue_version, session.session_id, complaint_index, complaint_id, complaint_hash, attempt)
            result = await prompt.prompt(
                session=session,
                user_input=user_input,
                system_prompt=_render_skill_prompt(),
                model=get_skill_model(),
                allowed_tools=["shell"],
                max_tool_rounds=15,
                record_messages=False,
                trace_context=trace_context,
            )
        except Exception as exc:
            last_reason = f"合理性分析执行失败：{exc}"
            logger.exception("[law_rationality_skill] session_prompt_failed user_id=%s base_id=%s issue_analysis_version=%s session_id=%s complaint_index=%s complaint_id=%s complaint_hash=%s attempt=%s err=%s", trace_context.get("user_id") or "", trace_context.get("base_id") or "", issue_version, session.session_id, complaint_index, complaint_id, complaint_hash, attempt, exc)
            continue
        output_text = ""
        if result.message and result.message.parts:
            output_text = result.message.parts[-1].content or ""
        parsed = _extract_json_object(output_text)
        try:
            normalized = _validate_result(payload, parsed)
            if attempt > 1:
                logger.info("[law_rationality_skill] retry_succeeded user_id=%s base_id=%s issue_analysis_version=%s session_id=%s complaint_index=%s complaint_id=%s complaint_hash=%s attempt=%s", trace_context.get("user_id") or "", trace_context.get("base_id") or "", issue_version, session.session_id, complaint_index, complaint_id, complaint_hash, attempt)
            return normalized
        except Exception as exc:
            last_reason = f"合理性分析输出解析失败：{exc}"
            logger.error("[law_rationality_skill] parse_output_failed user_id=%s base_id=%s issue_analysis_version=%s session_id=%s complaint_index=%s complaint_id=%s complaint_hash=%s attempt=%s err=%s output=%s", trace_context.get("user_id") or "", trace_context.get("base_id") or "", issue_version, session.session_id, complaint_index, complaint_id, complaint_hash, attempt, exc, output_text)
    return build_law_rationality_fallback(payload, last_reason)
