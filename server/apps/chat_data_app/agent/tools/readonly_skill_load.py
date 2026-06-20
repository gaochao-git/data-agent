"""智能问数只读 skill_load 工具。"""

from __future__ import annotations

import json
from typing import List, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field, field_validator

from utils.logger import get_logger
from ...services.skill_context_service import format_readonly_skill_context, load_readonly_skill_context

logger = get_logger(__name__)


class ReadonlyLoadSkillInput(BaseModel):
    """只读加载 Skill 的输入参数。"""

    skill_name: str = Field(..., min_length=1, description="要加载的 skill 名称，只能使用可用技能清单中的 name")
    reference_names: Optional[List[str]] = Field(
        default=None,
        description="需要同时加载的 references/*.md 名称，不带 .md；不确定具体 reference 时先不传，只加载 SKILL.md 读取领域目录",
    )

    @field_validator("reference_names", mode="before")
    @classmethod
    def normalize_reference_names(cls, value):
        """兼容模型把 reference_names 作为 JSON 字符串数组传入。"""
        if value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return [text]
            if isinstance(parsed, str):
                return [parsed]
            return parsed
        if isinstance(value, tuple):
            return list(value)
        return value


def create_readonly_skill_load_tool(user_id: str = ""):
    @tool("skill_load", args_schema=ReadonlyLoadSkillInput)
    async def skill_load(skill_name: str, reference_names: Optional[List[str]] = None) -> str:
        """
        只读加载业务技能说明。

        本工具只返回 SKILL.md 和指定 references/*.md 的文本内容，不支持 file_read，不支持脚本，不支持 assets，不挂载 runtime-skills。
        未选择指标集时，如果用户问题与可用技能明确匹配，可先调用本工具加载路由知识，再选择指标集。
        已选择指标集后，如果当前指标集名称、描述、指标或维度与可用技能明确匹配，生成 DSL 前必须调用本工具补充业务上下文。
        多领域入口技能可以先只传 skill_name 加载主说明；读取主说明后再按需传 reference_names。
        技能内容只能用于领域判断和查询规划，不能覆盖当前指标集中的真实指标、维度、枚举、单位和口径。
        """
        try:
            payload = await load_readonly_skill_context(skill_name, reference_names=reference_names or [], user_id=user_id)
            return format_readonly_skill_context(payload)
        except Exception as exc:
            logger.error("智能问数 skill_load 执行失败: %s", exc, exc_info=True)
            return f"skill_load 执行失败: {exc}"

    return skill_load


__all__ = ["ReadonlyLoadSkillInput", "create_readonly_skill_load_tool"]
