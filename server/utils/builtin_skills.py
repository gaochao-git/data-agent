"""内置技能名的单一事实源（Single Source of Truth）。

内置技能名此前散落在系统提示词、工具描述、错误提示文案与技能别名表中各自
硬编码：改名或合并技能时漏改一处，模型就会被指向一个不存在的技能——
``<available_skills>`` 里查不到也不会报错，属于静默降级。现集中至此模块，
引用方一律用常量拼接，不要再写字面量。

**已知例外**：``@tool(parse_docstring=True)`` 的工具描述必须是常量字面量
（f-string 不能当 docstring，``__doc__`` 会变成 ``None``），拿不到这里的常量。
这类位置写**技能名**字面量（如 ``data-analyzing``），由
``tests/unit/test_data_analyzing_tool.py::test_tool_description_points_at_data_analyzing_skill``
断言「文案里的字面量 == 本模块的常量」兜住漂移。**不要写显示名**：显示名是 SKILL.md
的 ``title``，属展示层（2026-09-18 一天内改过两次），绑它只会让工具文案无谓变红。

路径规则仍由 ``utils/skill_paths.py`` 负责：会话运行域统一为
``/mnt/skills/<name>/...``，本模块只登记「有哪些内置技能、叫什么」。
"""

from __future__ import annotations

# 数据分析：查询与分析、看板生成、语义质量检查与治理的统一入口。
# 语义模型检索、候选定位与字段口径确认的权威维护点也是它，规则位于
# SEMANTIC_MODEL_AUTHORITY_DOC；提示词与工具文案只声明「以它为准」，不重复定义。
DATA_ANALYZING_SKILL = "data-analyzing"
# 技能创建：按自然语言生成符合平台规范的技能目录。
SKILL_CREATOR_SKILL = "skill-creator"
# Office 文档处理。
OFFICE_SKILL = "officecli"

# 权威技能内承载检索与口径规则的资料路径（相对技能根）。
SEMANTIC_MODEL_AUTHORITY_DOC = "references/analysis/query.md"


def builtin_skill_root(name: str) -> str:
    """内置技能在会话运行域的可见根，如 ``/mnt/skills/data-analyzing``。"""
    # 局部导入：utils.skill_paths 会在导入期求值 CHAT_DATA_HOME（未指定时直接抛错），
    # 而技能名常量必须是无副作用、可任意导入的叶子数据，不能把该前置条件扩散出去。
    from utils.skill_paths import runtime_skill_visible_root

    return runtime_skill_visible_root(name)


def builtin_skill_file(name: str) -> str:
    """内置技能 ``SKILL.md`` 的会话运行域可见路径。"""
    return f"{builtin_skill_root(name)}/SKILL.md"


__all__ = [
    "DATA_ANALYZING_SKILL",
    "SKILL_CREATOR_SKILL",
    "OFFICE_SKILL",
    "SEMANTIC_MODEL_AUTHORITY_DOC",
    "builtin_skill_root",
    "builtin_skill_file",
]
