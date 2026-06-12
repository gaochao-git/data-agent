from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from apps.complaint_app.opencode_runtime.skills.parser import render_skill_body
from apps.complaint_app.services.complaint_api_service import normalize_reported_entities

_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts" / "complaint_analysis_v2"
_SKILLS_DIR = Path(__file__).resolve().parents[3] / "opencode_runtime" / "skills" / ".complaint_skills"
_PROMPT_CACHE: dict[str, str] = {}

SYSTEM_PROMPT_FILE = "common/system.md"
SUMMARY_USER_FILE = "summary/user.md"
SUMMARY_GUIDANCE_FILE = "summary/guidance.md"
SCENARIO_LOCATOR_USER_FILE = "issue_analysis/scenario_locator_user.md"
ANALYZER_USER_FILE = "issue_analysis/analyzer_user.md"
FINAL_USER_FILE = "final/user.md"

SCENARIO_LOCATOR_SKILL = "complaint-scenario-locator/SKILL.md"
ANALYZER_SKILL = "complaint-issue-analyzer/SKILL.md"
SCENARIO_LOCATOR_SKILL_KEY = f"skill:{SCENARIO_LOCATOR_SKILL}"
ANALYZER_SKILL_KEY = f"skill:{ANALYZER_SKILL}"


def prompt_path(name: str) -> Path:
    return _PROMPTS_DIR / name


def skill_path(name: str) -> Path:
    return _SKILLS_DIR / name


def _resolve_stage_file(name: str) -> Path:
    if name.startswith("skill:"):
        return skill_path(name[len("skill:"):])
    return prompt_path(name)


def load_prompt_text(name: str) -> str:
    cached = _PROMPT_CACHE.get(name)
    if cached is not None:
        return cached
    text = _resolve_stage_file(name).read_text(encoding="utf-8")
    _PROMPT_CACHE[name] = text
    return text


def render_template(template: str, variables: dict[str, Any]) -> str:
    rendered = str(template or "")
    for key, value in (variables or {}).items():
        rendered = rendered.replace(f"{{{{{key}}}}}", str(value or ""))
    return rendered


def _reported_institution_text(reported_entities: list[str] | None) -> str:
    entities = normalize_reported_entities(reported_entities)
    return "、".join(entities)


def build_summary_messages(*, complaint_text: str, reporter_name: str, reported_entities: list[str] | None) -> list[dict[str, str]]:
    system_prompt = load_prompt_text(SYSTEM_PROMPT_FILE)
    template_variables = {
        "complaint_text": str(complaint_text or "").strip(),
        "reporter_name": str(reporter_name or "").strip(),
        "reported_institution": _reported_institution_text(reported_entities),
    }
    summary_guidance = render_template(
        load_prompt_text(SUMMARY_GUIDANCE_FILE),
        template_variables,
    )
    user_prompt = render_template(
        load_prompt_text(SUMMARY_USER_FILE),
        {
            **template_variables,
            "complaint_summary_guidance": summary_guidance,
        },
    )
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]


def build_scenario_locator_messages(
    *,
    complaint_text: str,
    summary_markdown: str,
    reporter_name: str,
    reported_entities: list[str] | None,
) -> list[dict[str, str]]:
    system_prompt = render_skill_body(skill_path(SCENARIO_LOCATOR_SKILL), name="complaint-scenario-locator")
    template_variables = {
        "complaint_text": str(complaint_text or "").strip(),
        "summary_markdown": str(summary_markdown or "").strip(),
        "reporter_name": str(reporter_name or "").strip(),
        "reported_institution": _reported_institution_text(reported_entities),
    }
    user_prompt = render_template(load_prompt_text(SCENARIO_LOCATOR_USER_FILE), template_variables)
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]


def build_analyzer_messages(
    *,
    complaint_text: str,
    summary_markdown: str,
    law_results_json: str,
    reporter_name: str,
    reported_entities: list[str] | None,
) -> list[dict[str, str]]:
    system_prompt = render_skill_body(skill_path(ANALYZER_SKILL), name="complaint-issue-analyzer")
    template_variables = {
        "complaint_text": str(complaint_text or "").strip(),
        "summary_markdown": str(summary_markdown or "").strip(),
        "law_results_json": str(law_results_json or "[]").strip(),
        "reporter_name": str(reporter_name or "").strip(),
        "reported_institution": _reported_institution_text(reported_entities),
    }
    user_prompt = render_template(load_prompt_text(ANALYZER_USER_FILE), template_variables)
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]


def build_final_messages(*, issue_analysis_markdown: str) -> list[dict[str, str]]:
    system_prompt = load_prompt_text(SYSTEM_PROMPT_FILE)
    user_prompt = render_template(
        load_prompt_text(FINAL_USER_FILE),
        {
            "issue_analysis_markdown": str(issue_analysis_markdown or "").strip(),
        },
    )
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]


def build_stage_input_hash(stage: str, payload: dict[str, Any]) -> str:
    body = {"stage": stage, **(payload or {})}
    return hashlib.sha1(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def prompt_files_for_stage(stage: str) -> list[str]:
    if stage == "summary_v2":
        return [SYSTEM_PROMPT_FILE, SUMMARY_USER_FILE, SUMMARY_GUIDANCE_FILE]
    if stage == "analysis_v2":
        return [
            SYSTEM_PROMPT_FILE,
            SCENARIO_LOCATOR_USER_FILE,
            ANALYZER_USER_FILE,
            SCENARIO_LOCATOR_SKILL_KEY,
            ANALYZER_SKILL_KEY,
        ]
    if stage == "complaint_analysis_v2":
        return [SYSTEM_PROMPT_FILE, FINAL_USER_FILE]
    return [SYSTEM_PROMPT_FILE]


def prompt_versions_for_stage(stage: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in prompt_files_for_stage(stage):
        result[name] = hashlib.sha1(load_prompt_text(name).encode("utf-8")).hexdigest()
    return result
