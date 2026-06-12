"""
Skills 模块

符合 agentskills.io 规范的 skill 实现。

目录结构：
- loader.py                  # Skills API（发现、加载、激活）
- dashboard-generation/      # 看板生成技能目录
  - SKILL.md                 # 技能定义
- chart-processing/          # 图表生成技能目录
  - SKILL.md                 # 技能定义

API：
- parse_metadata(skill_path): 解析单个技能元数据
- discover_skills(directories): 扫描目录发现技能
- load_metadata(): 启动时加载所有元数据
- format_skills_prompt(): 格式化注入 system prompt 的技能描述
- activate_skill(name): 激活技能，按需加载完整内容
"""

from .loader import (
    # 类型
    Skill,
    SkillMetadata,
    # API
    parse_metadata,
    discover_skills,
    load_metadata,
    clear_metadata_cache,
    format_skills_prompt,
    build_builtin_skills_prompt,
    activate_skill,
)
