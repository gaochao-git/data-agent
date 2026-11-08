"""
Skills 模块

符合 agentskills.io 规范的 skill 实现。

系统技能直接以本代码包内的技能目录为唯一真值（随发版更新、只读），不再
复制到 `CHAT_DATA_HOME/skills/system/`；应用不自动复制，也不将系统技能写入数据库。
用户技能仍落在 `CHAT_DATA_HOME/skills/user/`，由数据库元数据与版本目录管理。

API：
- parse_metadata(skill_path): 解析单个技能元数据
- discover_skills(directories): 扫描目录发现技能
- load_metadata(): 启动时加载所有元数据
- format_skills_prompt(): 格式化注入 system prompt 的技能描述
- activate_skill(name): 激活技能，按需加载完整内容
"""

from .loader import (
    # 类型
    Skill,  # noqa: F401  # 包级重导出（__init__ 对外 API）
    SkillMetadata,  # noqa: F401  # 包级重导出（__init__ 对外 API）
    activate_skill,  # noqa: F401  # 包级重导出（__init__ 对外 API）
    clear_metadata_cache,  # noqa: F401  # 包级重导出（__init__ 对外 API）
    discover_skills,  # noqa: F401  # 包级重导出（__init__ 对外 API）
    format_skills_prompt,  # noqa: F401  # 包级重导出（__init__ 对外 API）
    load_metadata,  # noqa: F401  # 包级重导出（__init__ 对外 API）
    parse_metadata,  # noqa: F401  # 包级重导出（__init__ 对外 API）
    # API
    parse_metadata_text,  # noqa: F401  # 包级重导出（__init__ 对外 API）
)
