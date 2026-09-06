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

from pathlib import Path, PurePosixPath

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


def resolve_skill_snapshot_path(runtime_skills_dir: Path, path: str) -> tuple[Path, str]:
    """把 ``/mnt/skills/<name>/...`` 解析到 runtime-skills 快照的真实文件系统。

    这是本地后端与沙箱只读访问共用的唯一路径安全实现（此前是两份手写副本，
    语义已漂移：一份缺少隐藏名防护）。规则：
    - 技能名不允许为空或以 ``.`` 开头（隐藏目录不是合法技能）；
    - 技能源目录必须是真实目录（非 symlink），且解析后必须位于快照根之内；
    - 相对部分不允许 ``..``，最终路径 resolve 后必须仍在技能目录之内。

    Returns:
        (真实物理路径, Agent 可见的扁平虚拟路径)。

    Raises:
        ValueError: 前缀/格式/越界/技能不存在。
    """
    raw = str(path or "").strip().replace("\\", "/")
    prefix = f"{SKILLS_VIRTUAL_ROOT}/"
    if not raw.startswith(prefix):
        raise ValueError(f"只允许访问技能路径: {path}")
    parts = PurePosixPath(raw).parts
    if len(parts) < 4 or parts[1:3] != ("mnt", "skills"):
        raise ValueError(f"技能路径无效: {path}")
    name = parts[3]
    if not name or name.startswith("."):
        raise ValueError(f"技能路径无效: {path}")
    source_entry = Path(runtime_skills_dir) / name
    source_dir = source_entry.resolve()
    snapshot_root = Path(runtime_skills_dir).resolve()
    if (
        source_entry.is_symlink()
        or not source_dir.is_dir()
        or snapshot_root not in source_dir.parents
    ):
        raise ValueError(f"技能不存在或无权限访问: {name}")
    relative_parts = parts[4:]
    if ".." in relative_parts:
        raise ValueError(f"技能路径越界: {path}")
    target = source_dir.joinpath(*relative_parts).resolve()
    if target != source_dir and source_dir not in target.parents:
        raise ValueError(f"技能路径越界: {path}")
    visible = runtime_skill_visible_root(name)
    if relative_parts:
        visible += "/" + "/".join(relative_parts)
    return target, visible


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
    "resolve_skill_snapshot_path",
    "physical_skill_category_root",
    "physical_skill_root",
]
