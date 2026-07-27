"""
Skills 模块

符合 agentskills.io 规范的 skill 实现。

部署时将本包中的技能目录复制到
`CHAT_DATA_HOME/skills/system/`。运行时只读取该部署目录，应用不会自动
复制，也不会将系统技能写入数据库。

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
    build_system_skills_prompt,
    activate_skill,
)
