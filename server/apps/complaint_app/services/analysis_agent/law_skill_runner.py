from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from apps.complaint_app.core.model_config import get_skill_model
from apps.complaint_app.core.llm.llm_client import AsyncLLMClient
from apps.complaint_app.opencode_runtime.session import Session
from apps.complaint_app.opencode_runtime.session_prompt import SessionPrompt
from apps.complaint_app.opencode_runtime.tools.base_tool import ToolExecutor
from apps.complaint_app.opencode_runtime.skills.parser import parse_frontmatter
from utils.logger import get_logger

logger = get_logger(__name__)

SKILL_PATH = Path(__file__).resolve().parents[2] / "opencode_runtime" / "skills" / ".complaint_skills" / "complaint-law-analysis" / "SKILL.md"


def _law_skill_max_attempts() -> int:
    """读取法规 skill 最大尝试次数。"""
    raw = os.getenv("COMPLAINT_LAW_SKILL_MAX_ATTEMPTS", "2").strip()
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
    """读取并渲染 complaint-law-analysis skill。"""
    content = SKILL_PATH.read_text(encoding="utf-8")
    metadata: dict[str, Any] = {}
    body = content
    try:
        metadata, body = parse_frontmatter(content)
    except Exception as exc:
        logger.warning("[law_skill] parse_frontmatter_failed err=%s", exc)
    rendered = str(body or "")
    skill_metadata = metadata.get("metadata") if isinstance(metadata.get("metadata"), dict) else {}
    for key, value in skill_metadata.items():
        rendered = rendered.replace(f"<metadata.{key}>", str(value))
    return f"## Skill: complaint-law-analysis\n\n{rendered.strip()}"


async def analyze_complaint_items_with_law_skill(payload: dict[str, Any]) -> dict[str, Any]:
    """调用本地法规分析 skill 批量分析合理举报事项。"""
    user_input = json.dumps(payload, ensure_ascii=False, indent=2)
    trace_context = payload.get("trace_context") if isinstance(payload.get("trace_context"), dict) else {}
    issue_version = str(trace_context.get("issue_analysis_version") or "")
    complaint_count = len(payload.get("complaint_items") or []) if isinstance(payload.get("complaint_items"), list) else 0
    last_reason = "核查材料和核实思路生成失败，需人工复核。"
    for attempt in range(1, _law_skill_max_attempts() + 1):
        executor = ToolExecutor()
        session = Session(session_id=f"law_skill_{uuid.uuid4().hex}")
        prompt = SessionPrompt(
            llm_client=AsyncLLMClient(
                name="complaint_issue_analysis_skill",
                metric_stage="complaint_issue_analysis_skill",
                metric_context={
                    "session_id": session.session_id,
                    "business_stage": "complaint_issue_analysis",
                    "skill_name": "complaint-law-analysis",
                    "complaint_count": complaint_count,
                    "attempt": attempt,
                    **trace_context,
                },
            ),
            tool_executor=executor,
        )
        try:
            logger.info("[law_skill] batch_attempt_start user_id=%s base_id=%s issue_analysis_version=%s session_id=%s complaint_count=%s attempt=%s", trace_context.get("user_id") or "", trace_context.get("base_id") or "", issue_version, session.session_id, complaint_count, attempt)
            result = await prompt.prompt(
                session=session,
                user_input=user_input,
                system_prompt=_render_skill_prompt(),
                model=get_skill_model(),
                allowed_tools=[],
                max_tool_rounds=0,
                record_messages=False,
                trace_context=trace_context,
            )
        except Exception as exc:
            last_reason = f"核查材料和核实思路生成执行失败：{exc}"
            logger.exception("[law_skill] batch_session_prompt_failed user_id=%s base_id=%s issue_analysis_version=%s session_id=%s complaint_count=%s attempt=%s err=%s", trace_context.get("user_id") or "", trace_context.get("base_id") or "", issue_version, session.session_id, complaint_count, attempt, exc)
            continue
        output_text = ""
        if result.message and result.message.parts:
            output_text = result.message.parts[-1].content or ""
        parsed = _extract_json_object(output_text)
        if isinstance(parsed.get("items"), list):
            if attempt > 1:
                logger.info("[law_skill] batch_retry_succeeded user_id=%s base_id=%s issue_analysis_version=%s session_id=%s complaint_count=%s attempt=%s", trace_context.get("user_id") or "", trace_context.get("base_id") or "", issue_version, session.session_id, complaint_count, attempt)
            return parsed
        last_reason = "核查材料和核实思路生成输出解析失败，需人工复核。"
        logger.error("[law_skill] batch_parse_output_failed user_id=%s base_id=%s issue_analysis_version=%s session_id=%s complaint_count=%s attempt=%s output=%s", trace_context.get("user_id") or "", trace_context.get("base_id") or "", issue_version, session.session_id, complaint_count, attempt, output_text)
    return _build_law_skill_batch_fallback(payload, last_reason)


def _build_law_skill_fallback(payload: dict[str, Any], reason: str) -> dict[str, Any]:
    """构建核查材料生成失败时的兜底结果。"""
    return {
        "complaint_id": str((payload.get("complaint_item") or {}).get("id") or ""),
        "neutral_claim": str((payload.get("complaint_item") or {}).get("claim_text") or ""),
        "evidence_summary": [],
        "user_cited_laws_analysis": [],
        "possible_laws": [],
        "verification_points": [reason],
        "document_checklist": [],
        "preliminary_conclusion": reason,
    }


def _build_law_skill_batch_fallback(payload: dict[str, Any], reason: str) -> dict[str, Any]:
    """构建核查材料批量生成失败时的兜底结果。"""
    items: list[dict[str, Any]] = []
    for item in payload.get("complaint_items") or []:
        if not isinstance(item, dict):
            continue
        single_payload = {"complaint_item": item}
        items.append(_build_law_skill_fallback(single_payload, reason))
    return {"items": items}
