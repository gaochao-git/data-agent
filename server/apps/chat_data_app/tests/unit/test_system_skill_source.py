"""系统技能来源回归：代码包即唯一真值，数据根不再持有系统技能副本。"""

from __future__ import annotations

from pathlib import Path

from apps.chat_data_app.agent.skills import loader
from utils.skill_paths import (
    SYSTEM_SKILL_CATEGORY,
    USER_SKILL_CATEGORY,
    physical_skill_category_root,
    physical_skill_root,
    system_skills_source_root,
)


def test_system_skill_root_points_to_code_package():
    """loader 与路径常量必须指向同一代码包目录，且能扫出内置技能。"""
    root = system_skills_source_root()
    assert root.is_dir()
    assert root.name == "skills"
    assert (root / "loader.py").is_file()
    assert loader.system_skills_root() == root
    assert physical_skill_category_root(SYSTEM_SKILL_CATEGORY) == root
    assert (root / "data-analyzing" / "SKILL.md").is_file()


def test_load_metadata_reads_code_package_without_data_home_copy():
    """不建 CHAT_DATA_HOME/skills/system 也能加载系统技能元数据。"""
    loader.clear_metadata_cache()
    try:
        metadata = loader.load_metadata()
    finally:
        loader.clear_metadata_cache()
    assert "data-analyzing" in metadata
    assert "officecli" in metadata
    assert "skill-creator" in metadata
    assert Path(metadata["data-analyzing"]["path"]).is_relative_to(system_skills_source_root())


def test_system_category_ignores_chat_data_home_but_user_does_not(tmp_path: Path):
    """切换数据根只影响用户技能，系统技能永远来自代码包。"""
    assert physical_skill_category_root(SYSTEM_SKILL_CATEGORY, chat_data_home=str(tmp_path)) == (
        system_skills_source_root()
    )
    assert physical_skill_root(SYSTEM_SKILL_CATEGORY, "data-analyzing", chat_data_home=str(tmp_path)) == (
        system_skills_source_root() / "data-analyzing"
    )
    assert physical_skill_category_root(USER_SKILL_CATEGORY, chat_data_home=str(tmp_path)) == (
        tmp_path / "skills" / "user"
    )
