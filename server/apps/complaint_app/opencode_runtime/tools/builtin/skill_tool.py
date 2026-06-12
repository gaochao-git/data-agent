from __future__ import annotations

from typing import Any, Dict, List
from pathlib import Path

from apps.complaint_app.opencode_runtime.skills.service import SkillService
from apps.complaint_app.opencode_runtime.skills.parser import parse_frontmatter
from utils.logger import get_logger

from ..base_tool import Tool, ToolContext

logger = get_logger(__name__)


# 用 skill metadata 渲染正文中的占位符。
def _render_skill_body(body: str, skill_metadata: dict[str, Any] | None) -> str:
    rendered = str(body or "")
    if not rendered or not isinstance(skill_metadata, dict):
        return rendered
    for key, value in skill_metadata.items():
        token = f"<metadata.{key}>"
        rendered = rendered.replace(token, str(value))
    return rendered


def registry() -> Tool:
    """返回内置 skill 工具实例。"""
    service = SkillService()

    description = service.tool_prompt()

    def _handler(payload: Dict[str, object], context: ToolContext):
        name = str((payload or {}).get("name") or "").strip()
        if not name:
            raise ValueError("Skill name is required")
        skill = service.get(name)
        if not skill:
            available = ", ".join([item.name for item in service.list()]) or "none"
            raise ValueError(f'Skill "{name}" not found. Available skills: {available}')

        content = (service.load_content(name) or "").strip()
        location = None
        registry = getattr(service, "_registry", None)
        if registry and hasattr(registry, "get_location"):
            location = registry.get_location(name)

        body = content
        if content:
            try:
                _, body = parse_frontmatter(content)
            except Exception as exc:
                logger.warning("[skill_tool] parse frontmatter failed skill=%s err=%s", skill.name, exc)
                body = content
        body = _render_skill_body(body, skill.metadata)

        output_lines: List[str] = [f"## Skill: {skill.name}"]
        if location:
            base_dir = str(Path(location).parent)
            output_lines.extend(["", f"**Base directory**: {base_dir}"])
        if body:
            output_lines.extend(["", body.strip()])

        metadata = {"name": skill.name}
        if location:
            metadata["dir"] = str(Path(location).parent)
            metadata["location"] = location
        if skill.metadata:
            metadata["metadata"] = dict(skill.metadata)
        if skill.allowed_tools:
            metadata["allowed_tools"] = list(skill.allowed_tools)

        return {
            "title": f"Loaded skill: {skill.name}",
            "output": "\n".join(output_lines).strip(),
            "metadata": metadata,
        }

    return Tool(
        name="skill",
        description=description,
        _executor=_handler,
        metadata={
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "skill identifier from available_skills",
                    }
                },
                "required": ["name"],
            }
        },
    )
