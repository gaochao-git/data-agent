"""ChatData skill_load 工具。"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from utils.logger import get_logger
from utils.skill_paths import runtime_skill_visible_root
from ...services.conversation_workspace import ConversationWorkspaceRuntime
from ...services.runtime_config import FILE_READ_MAX_CHARS

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# 参数 schema
# ---------------------------------------------------------------------------

class SkillLoadInput(BaseModel):
    """加载 Skill 的输入参数。"""

    skill_name: str = Field(..., min_length=1, description="要加载的 skill 名称，只能使用可用技能清单中的 name")


# ---------------------------------------------------------------------------
# 运行时只读技能映射
# ---------------------------------------------------------------------------

def format_skill_load_result(payload: dict[str, Any], max_chars: int = FILE_READ_MAX_CHARS) -> str:
    """格式化 skill_load 工具结果，供模型工具和编排预加载复用。

    references/scripts/assets 一律输出可直接 file_read 的完整虚拟路径，避免模型自行
    拼接前缀时写出 /mnt/user-data/runtime-skills/ 等不存在的路径。
    """
    visible_root = payload["visible_path"]
    references_full = [
        f"{visible_root}/references/{ref}.md" for ref in payload.get("references") or []
    ]
    scripts_full = [
        f"{visible_root}/scripts/{script}" for script in payload.get("scripts") or []
    ]
    assets_full = [
        f"{visible_root}/assets/{asset}" for asset in payload.get("assets") or []
    ]
    result = (
        "[skill_load]\n"
        f"skill_name={payload['name']}\n"
        f"title={payload['title']}\n"
        f"runtime_path={visible_root}\n"
        "⚠️ 读取技能参考文件必须使用上面的 runtime_path 拼接，禁止自造路径前缀；"
        "错误示例 /mnt/user-data/runtime-skills/、缺少 /mnt/skills/ 前缀的写法都会让 file_read 直接报错。\n"
        f"references={json.dumps(references_full, ensure_ascii=False)}\n"
        f"scripts={json.dumps(scripts_full, ensure_ascii=False)}\n"
        f"assets={json.dumps(assets_full, ensure_ascii=False)}\n\n"
        f"{payload['skill_md']}"
    )
    if len(result) <= max_chars:
        return result
    marker = (
        f"\n\n[skill_load 内容已截断。需要继续读取时，使用 "
        f"file_read(file_path={visible_root}/SKILL.md, offset=..., limit=...) 按行读取。]"
    )
    return result[:max(0, max_chars - len(marker))].rstrip() + marker


# ---------------------------------------------------------------------------
# 统一工具工厂
# ---------------------------------------------------------------------------

def create_skill_load_tool(workspace_runtime: ConversationWorkspaceRuntime, user_id: str = ""):
    """创建统一 skill_load 工具。

    Args:
        workspace_runtime: 当前会话的本地或 OpenSandbox 工作区。
        user_id: 用于按权限解析技能。
    """

    @tool("skill_load", args_schema=SkillLoadInput)
    async def skill_load(skill_name: str) -> str:
        """
        加载指定技能说明，返回受 CHAT_DATA_FILE_READ_MAX_CHARS 限制的 SKILL.md 正文、references/scripts/assets 清单与只读 `/mnt/skills/...` 路径。

        技能目录保留在既有技能仓库，不复制到会话 workspace。本工具是「获取 SKILL.md 内联文本」的便捷入口；
        读取技能的 references/scripts/assets 子文件时，也可直接用返回的 runtime_path 拼接后交给 file_read/list_dir，
        无需先调用本工具（任何当前用户有权访问的技能路径均可直接读取，按权限校验，对齐 deer-flow 的
        discover + read_file 模型）。

        读取参考文件时，严格使用本工具返回的 runtime_path 拼接子路径，不要自行猜测或省略路径前缀。

        前端选中技能不等于已自动加载。仅当用户本轮显式指定技能时调用本工具，不要因领域词相近或系统技能存在而自动调用。
        返回内容被截断时，使用 file_read 从 `/mnt/skills/<技能名>/SKILL.md` 按行读取；单行超过 500 个字符时会直接截断。
        """
        try:
            _ = user_id
            payload = await workspace_runtime.load_skill_payload(
                skill_name=skill_name,
                max_chars=FILE_READ_MAX_CHARS,
            )
            payload["visible_path"] = str(payload.get("visible_path") or runtime_skill_visible_root(payload["name"]))
            payload["runtime_path"] = payload["visible_path"]
            return format_skill_load_result(payload, max_chars=FILE_READ_MAX_CHARS)
        except Exception as exc:
            logger.error("skill_load 执行失败: %s", exc, exc_info=True)
            return f"skill_load 执行失败: {exc}"

    return skill_load


__all__ = [
    "SkillLoadInput",
    "create_skill_load_tool",
]
