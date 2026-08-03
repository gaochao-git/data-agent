"""技能路径的单一事实源（Single Source of Truth）。

Agent 运行期看到的技能路径统一为扁平的 ``/mnt/skills/<name>/...``（会话级
runtime-skills 快照，不区分系统 / 用户类别）；磁盘真实管理域位置为
``CHAT_DATA_HOME/skills/system/<name>/...``（系统技能）、
``CHAT_DATA_HOME/skills/user/<user_id>/<name>/...``（用户技能）。

此前该虚拟根、类别集合、物理根在 ``conversation_workspace.py``、
``workspace_execution.py``、``skill_service.py``、``loader.py``
等多处各自硬编码副本，新增类别（如 team）时极易漏改导致后端行为漂移。
现集中至此模块，全项目统一引用。
"""
from __future__ import annotations

from pathlib import Path

from utils.storage_paths import CHAT_DATA_HOME

# 技能虚拟根（Agent 视角），所有类别共用同一前缀。
SKILLS_VIRTUAL_ROOT = "/mnt/skills"

# 物理技能根在 CHAT_DATA_HOME 下的子目录名（即 ``CHAT_DATA_HOME/skills``）。
_SKILL_PHYSICAL_ROOT_NAME = "skills"

# 受支持的技能类别。新增类别只需在此追加 + 确保 ``CHAT_DATA_HOME/skills/<类别>`` 存在。
SYSTEM_SKILL_CATEGORY = "system"
USER_SKILL_CATEGORY = "user"
SKILL_CATEGORIES = frozenset({SYSTEM_SKILL_CATEGORY, USER_SKILL_CATEGORY})


def is_valid_skill_category(category: str) -> bool:
    """校验是否为受支持的技能类别。"""
    return category in SKILL_CATEGORIES


def runtime_skill_visible_root(name: str) -> str:
    """会话运行期技能扁平虚拟根，如 ``/mnt/skills/demo``。

    运行域不区分系统 / 用户类别，所有可见技能统一挂在 ``/mnt/skills/<name>``
    之下（快照来自 ``threads/<uid>/<cid>/runtime-skills/<name>``）。
    """
    return f"{SKILLS_VIRTUAL_ROOT}/{name}"


def physical_skill_category_root(category: str, chat_data_home: str | None = None) -> Path:
    """类别级物理根，如 ``CHAT_DATA_HOME/skills/system``。

    ``chat_data_home`` 允许调用方显式传入数据根（如测试时切换），默认取
    ``storage_paths.CHAT_DATA_HOME``。
    """
    home = chat_data_home or CHAT_DATA_HOME
    return Path(home) / _SKILL_PHYSICAL_ROOT_NAME / category


def physical_skill_root(
    category: str,
    name: str,
    chat_data_home: str | None = None,
) -> Path:
    """技能级物理根，如 ``CHAT_DATA_HOME/skills/system/demo``。"""
    return physical_skill_category_root(category, chat_data_home=chat_data_home) / name


__all__ = [
    "SKILLS_VIRTUAL_ROOT",
    "SYSTEM_SKILL_CATEGORY",
    "USER_SKILL_CATEGORY",
    "SKILL_CATEGORIES",
    "is_valid_skill_category",
    "runtime_skill_visible_root",
    "physical_skill_category_root",
    "physical_skill_root",
]
