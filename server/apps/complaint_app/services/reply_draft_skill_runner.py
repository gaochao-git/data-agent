from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from apps.complaint_app.core.llm.llm_client import AsyncLLMClient
from apps.complaint_app.opencode_runtime.session import Session
from apps.complaint_app.opencode_runtime.session_prompt import SessionPrompt
from apps.complaint_app.opencode_runtime.skills.parser import parse_frontmatter
from apps.complaint_app.opencode_runtime.tools.base_tool import ToolExecutor
from utils.logger import get_logger

logger = get_logger(__name__)

SKILL_PATH = Path(__file__).resolve().parents[1] / "opencode_runtime" / "skills" / ".complaint_skills" / "complaint-reply-draft" / "SKILL.md"


def _extract_json_object(text: str) -> dict[str, Any]:
    """从模型输出中提取 JSON 对象。"""
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
    """读取并渲染答复意见书 skill。"""
    content = SKILL_PATH.read_text(encoding="utf-8")
    body = content
    try:
        _, body = parse_frontmatter(content)
    except Exception as exc:
        logger.warning("[reply_draft_skill] parse_frontmatter_failed err=%s", exc)
    return f"## Skill: complaint-reply-draft\n\n{str(body or '').strip()}"


async def generate_reply_draft_with_skill(payload: dict[str, Any], model: str) -> dict[str, Any]:
    """调用答复意见书 skill 生成文书 JSON。"""
    session = Session(session_id=f"reply_draft_skill_{uuid.uuid4().hex}")
    prompt = SessionPrompt(
        llm_client=AsyncLLMClient(
            name="complaint_reply_draft_skill",
            metric_stage="complaint_reply_draft_skill",
            metric_context={
                "session_id": session.session_id,
                "business_stage": "complaint_reply_draft",
                "skill_name": "complaint-reply-draft",
            },
        ),
        tool_executor=ToolExecutor(),
    )
    user_input = json.dumps(payload, ensure_ascii=False, indent=2)
    result = await prompt.prompt(
        session=session,
        user_input=user_input,
        system_prompt=_render_skill_prompt(),
        model=model,
        allowed_tools=[],
        max_tool_rounds=1,
        record_messages=False,
    )
    output_text = ""
    if result.message and result.message.parts:
        output_text = result.message.parts[-1].content or ""
    parsed = _extract_json_object(output_text)
    if parsed:
        return parsed
    logger.error("[reply_draft_skill] parse_output_failed session_id=%s output=%s", session.session_id, output_text)
    return {}
