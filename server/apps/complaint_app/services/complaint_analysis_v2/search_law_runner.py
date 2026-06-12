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
from apps.complaint_app.opencode_runtime.skills.parser import render_skill_body
from apps.complaint_app.opencode_runtime.trace_logger import trace_detail_logger, trace_enabled, trace_tag
from apps.complaint_app.opencode_runtime.tools.base_tool import ToolExecutor
from apps.complaint_app.opencode_runtime.tools.builtin.document_read_tool import registry as document_read_registry
from utils.logger import get_logger

logger = get_logger(__name__)

SKILL_PATH = Path(__file__).resolve().parents[2] / "opencode_runtime" / "skills" / ".complaint_skills" / "complaint-search-law" / "SKILL.md"


def _trace_info(trace_context: dict[str, Any] | None, message: str, *args: Any) -> None:
    if trace_enabled():
        trace_detail_logger(trace_context).info(message, *args)


def _extract_json_object(text: str) -> dict[str, Any]:
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


def _output_preview(value: Any, limit: int = 500) -> dict[str, Any]:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value or "")
    return {"preview": text[:limit], "chars": len(text), "truncated": len(text) > limit}


def _render_skill_prompt() -> str:
    laws_dir = (os.getenv("COMPLAINT_LAWS_ROOT") or "").strip()
    overrides: dict[str, Any] = {"laws_dir": laws_dir} if laws_dir else None
    return render_skill_body(SKILL_PATH, name="complaint-search-law", metadata_overrides=overrides)


def _normalize_article_rows(value: Any) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if not isinstance(value, list):
        return rows
    for item in value:
        if not isinstance(item, dict):
            continue
        article = str(item.get("article") or item.get("item") or "").strip()
        content = str(item.get("content") or item.get("text") or "").strip()
        if article or content:
            rows.append({"article": article, "content": content})
    return rows[:3]


def _normalize_law_rows(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not isinstance(value, list):
        return rows
    for item in value:
        if not isinstance(item, dict):
            continue
        law_name = str(item.get("law_name") or item.get("law") or item.get("name") or "").strip()
        articles = _normalize_article_rows(item.get("articles"))
        if law_name or articles:
            rows.append({"law_name": law_name, "articles": articles})
    return rows[:5]


def _normalize_result(sentence: str, parsed: dict[str, Any]) -> dict[str, Any]:
    task_understanding = parsed.get("task_understanding") if isinstance(parsed.get("task_understanding"), dict) else {}
    related_laws = parsed.get("related_laws") if isinstance(parsed.get("related_laws"), dict) else {}

    def _normalize_named_rows(value: Any) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        if not isinstance(value, list):
            return rows
        for item in value:
            if not isinstance(item, dict):
                continue
            law_name = str(item.get("law_name") or item.get("law") or item.get("name") or "").strip()
            reason = str(item.get("reason") or "").strip()
            if law_name or reason:
                rows.append({"law_name": law_name, "reason": reason})
        return rows

    articles: list[dict[str, str]] = []
    if isinstance(parsed.get("articles"), list):
        for item in parsed.get("articles") or []:
            if not isinstance(item, dict):
                continue
            law_name = str(item.get("law_name") or item.get("law") or "").strip()
            article = str(item.get("article") or item.get("item") or "").strip()
            content = str(item.get("content") or item.get("text") or "").strip()
            relevance = str(item.get("relevance") or item.get("reason") or "").strip()
            if law_name or article or content or relevance:
                articles.append({"law_name": law_name, "article": article, "content": content, "relevance": relevance})

    return {
        "query": str(parsed.get("query") or sentence or "").strip(),
        "task_understanding": {
            "reported_issue": str(task_understanding.get("reported_issue") or "").strip(),
            "scene": str(task_understanding.get("scene") or "").strip(),
            "law_scope": [str(item).strip() for item in task_understanding.get("law_scope") or [] if str(item).strip()],
            "search_target": str(task_understanding.get("search_target") or "").strip(),
        },
        "related_laws": {
            "high_relevance": _normalize_named_rows(related_laws.get("high_relevance")),
            "medium_relevance": _normalize_named_rows(related_laws.get("medium_relevance")),
            "not_read_but_possible": _normalize_named_rows(related_laws.get("not_read_but_possible")),
        },
        "articles": articles,
        "summary": str(parsed.get("summary") or "").strip(),
    }


async def search_law(sentence: str, trace_context: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {"sentence": str(sentence or "").strip()}
    logger.info("[complaint_api] search_law_start user_id=%s base_id=%s file_id=%s stage=%s query=%s", (trace_context or {}).get("user_id") or "", (trace_context or {}).get("base_id") or "", (trace_context or {}).get("file_id") or "", (trace_context or {}).get("stage") or "", payload["sentence"])
    _trace_info(trace_context, "[search_law] %s search_law_start query=%s", trace_tag(trace_context), payload["sentence"])
    user_input = json.dumps(payload, ensure_ascii=False, indent=2)
    executor = ToolExecutor()
    executor.register(document_read_registry())
    skill_trace_context = {**(trace_context or {}), "skill_name": "complaint-search-law", "laws_root": os.getenv("COMPLAINT_LAWS_ROOT") or ""}
    _trace_info(skill_trace_context, "[search_law] %s skill_input user_input=%s skill_path=%s", trace_tag(skill_trace_context), user_input, str(SKILL_PATH))
    session = Session(session_id=f"search_law_{uuid.uuid4().hex}", metadata=skill_trace_context)
    prompt = SessionPrompt(
        llm_client=AsyncLLMClient(
            name="complaint_search_law_skill",
            metric_stage="complaint_search_law_skill",
            metric_context={
                "business_stage": "complaint_analysis_v2",
                "skill_name": "complaint-search-law",
                **(trace_context or {}),
            },
        ),
        tool_executor=executor,
    )
    result = await prompt.prompt(
        session=session,
        user_input=user_input,
        system_prompt=_render_skill_prompt(),
        model=get_skill_model(),
        allowed_tools=["document_read"],
        max_tool_rounds=8,
        record_messages=False,
        trace_context=skill_trace_context,
    )
    output_text = ""
    if result.message and result.message.parts:
        output_text = result.message.parts[-1].content or ""
    _trace_info(skill_trace_context, "[search_law] %s skill_output output_chars=%s output=%s", trace_tag(skill_trace_context), len(output_text), output_text)
    parsed = _extract_json_object(output_text)
    normalized = _normalize_result(payload["sentence"], parsed)
    tool_calls = []
    for item in result.tool_results or []:
        output = item.output if isinstance(item.output, dict) else {"raw": str(item.output or "")}
        tool_name = item.name
        logger.info(
            "[complaint_api] search_law_tool_result user_id=%s base_id=%s file_id=%s stage=%s tool=%s args=%s output_preview=%s",
            (trace_context or {}).get("user_id") or "",
            (trace_context or {}).get("base_id") or "",
            (trace_context or {}).get("file_id") or "",
            (trace_context or {}).get("stage") or "",
            tool_name,
            json.dumps(item.arguments if isinstance(item.arguments, dict) else {}, ensure_ascii=False),
            _output_preview(output),
        )
        _trace_info(skill_trace_context, "[search_law] %s tool_result tool=%s args=%s output_preview=%s", trace_tag(skill_trace_context), tool_name, json.dumps(item.arguments if isinstance(item.arguments, dict) else {}, ensure_ascii=False), json.dumps(_output_preview(output), ensure_ascii=False))
        tool_calls.append({
            "name": tool_name,
            "arguments": item.arguments if isinstance(item.arguments, dict) else {},
            "output_preview": _output_preview(output),
        })
    audit = {
        "session_id": session.session_id,
        "query": payload["sentence"],
        "skill_name": "complaint-search-law",
        "tool_rounds": int((result.stats or {}).get("tool_rounds") or 0),
        "parallel_rounds": int((result.stats or {}).get("parallel_rounds") or 0),
        "tool_calls": tool_calls,
        "trace_context": dict(trace_context or {}),
    }
    normalized["audit"] = audit
    logger.info("[complaint_api] search_law_done user_id=%s base_id=%s file_id=%s stage=%s query=%s articles=%s tool_rounds=%s", (trace_context or {}).get("user_id") or "", (trace_context or {}).get("base_id") or "", (trace_context or {}).get("file_id") or "", (trace_context or {}).get("stage") or "", payload["sentence"], len(normalized.get("articles") or []), audit["tool_rounds"])
    _trace_info(skill_trace_context, "[search_law] %s search_law_done articles=%s tool_rounds=%s return=%s", trace_tag(skill_trace_context), len(normalized.get("articles") or []), audit["tool_rounds"], json.dumps(normalized, ensure_ascii=False))
    return normalized
