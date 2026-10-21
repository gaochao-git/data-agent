"""Ensure bundled system skills only describe tools registered by the unified agent.

2026-09-18 起 ``semantic-query`` / ``dashboard-generation`` / ``semantic-quality-check``
合并为单个 ``data-analyzing`` 技能（router ``SKILL.md`` + ``references`` 按能力分流）。
因此本文件按**能力文档**分别断言：若仍旧对技能目录做整目录文本匹配，合并后
「任一份文档里有该字符串」就会让断言通过，等于悄悄放松了契约。
"""

import re
from pathlib import Path

from utils.builtin_skills import DATA_ANALYZING_SKILL, OFFICE_SKILL, SKILL_CREATOR_SKILL

SYSTEM_SKILLS_ROOT = Path(__file__).resolve().parents[2] / "agent" / "skills"

# 合并前的三个技能名：不得再出现在任何技能文档里（残留即指向不存在的技能）
LEGACY_SKILL_NAMES = ("semantic-query", "dashboard-generation", "semantic-quality-check")

# 合并后最初使用的技能名，同日又改名为 data-analyzing；一并按零残留锁定，
# 否则「旧名写回去」这种改错没有任何一层会报错，只会静默表现为技能不存在。
RENAMED_SKILL_NAMES = ("data-warehouse",)

DEAD_SKILL_NAMES = LEGACY_SKILL_NAMES + RENAMED_SKILL_NAMES

CURRENT_SKILLS = (DATA_ANALYZING_SKILL, OFFICE_SKILL, SKILL_CREATOR_SKILL)


def _skill_text(name: str) -> str:
    """技能目录下全部 md 文本（skill 入口 + references）。"""
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted((SYSTEM_SKILLS_ROOT / name).rglob("*.md")))


def _skill_doc(name: str, *relative_paths: str) -> str:
    """技能目录下指定文档的文本。"""
    return "\n".join(
        (SYSTEM_SKILLS_ROOT / name / relative_path).read_text(encoding="utf-8") for relative_path in relative_paths
    )


# ---------------------------------------------------------------------------
# 合并结构本身
# ---------------------------------------------------------------------------


def test_builtin_skill_names_are_pinned():
    """技能名是对外契约（提示词 / 工具描述 / 用户手册都引用它），改名必须显式改这里。"""
    assert DATA_ANALYZING_SKILL == "data-analyzing"
    assert SKILL_CREATOR_SKILL == "skill-creator"
    assert OFFICE_SKILL == "officecli"


def test_legacy_skills_are_merged_into_data_analyzing():
    """三个旧技能目录已删除，只剩合并后的 data-analyzing。"""
    assert (SYSTEM_SKILLS_ROOT / DATA_ANALYZING_SKILL / "SKILL.md").is_file()
    for dead in DEAD_SKILL_NAMES:
        assert not (SYSTEM_SKILLS_ROOT / dead).exists(), f"旧技能目录未删除: {dead}"


def test_data_analyzing_router_routes_to_every_capability_doc():
    """路由层必须显式指向三类能力文档，模型读完 SKILL.md 才知道下一步读什么。"""
    router = _skill_doc(DATA_ANALYZING_SKILL, "SKILL.md")
    for relative_path in (
        "references/analysis/query.md",
        "references/analysis/dialect.md",
        "references/dashboard/workflow.md",
        "references/dashboard/guide.md",
        "references/governance/quality-check.md",
        "references/governance/quality-rules.md",
        "references/governance/model-template.md",
    ):
        assert relative_path in router, f"路由层未指向 {relative_path}"


def test_data_analyzing_references_are_layered_by_capability():
    """references 按能力分目录，根下不再散落文档：能力规则互不污染是合并的前提。"""
    references_root = SYSTEM_SKILLS_ROOT / DATA_ANALYZING_SKILL / "references"

    assert {item.name for item in references_root.iterdir() if item.is_dir()} == {
        "analysis",
        "dashboard",
        "governance",
    }
    assert not list(references_root.glob("*.md")), "references 根下不应再有散落文档"
    assert not list(references_root.glob("*.json")), "示例应放在 assets/ 下，不放 references"


def test_data_analyzing_assets_are_layered_by_capability():
    assets_root = SYSTEM_SKILLS_ROOT / DATA_ANALYZING_SKILL / "assets"

    assert {item.name for item in assets_root.iterdir() if item.is_dir()} == {"dashboard"}
    assert len(list((assets_root / "dashboard" / "examples").glob("*.json"))) == 3


def test_data_analyzing_router_stays_thin():
    """路由层不带实现细节：DSL/SQL 与看板规格只存在于能力文档，避免两处漂移。"""
    router = _skill_doc(DATA_ANALYZING_SKILL, "SKILL.md")

    for inline_detail in ("time_grain", "rightExpression", "dashboard_spec", "chart_type"):
        assert inline_detail not in router, f"路由层内联了能力细节: {inline_detail}"


def test_data_analyzing_referenced_paths_exist():
    """技能内引用的 /mnt/skills/data-analyzing/... 路径必须真实存在。"""
    skill_root = SYSTEM_SKILLS_ROOT / DATA_ANALYZING_SKILL
    referenced = set(re.findall(r"/mnt/skills/data-analyzing/([A-Za-z0-9_./-]+)", _skill_text(DATA_ANALYZING_SKILL)))

    assert referenced, "未在技能内找到任何自引用路径"
    missing = sorted(item for item in referenced if not (skill_root / item).exists())
    assert not missing, f"技能内引用了不存在的路径: {missing}"


def test_system_skills_do_not_reference_removed_patch_tool_contract():
    legacy_markers = (
        'file_edit(operation="write"',
        'file_edit(operation="patch"',
        "*** Begin Patch",
        "*** Update File:",
        "*** End Patch",
    )

    for name in CURRENT_SKILLS:
        text = _skill_text(name)
        assert all(marker not in text for marker in legacy_markers)


def test_system_skills_do_not_reference_legacy_skill_names():
    """技能文档里不得残留旧技能名：既可能是漏改路径，也说明路由已失效。"""
    for name in CURRENT_SKILLS:
        text = _skill_text(name)
        for dead in DEAD_SKILL_NAMES:
            assert dead not in text, f"{name} 仍引用旧技能名: {dead}"


def test_system_skills_describe_current_filesystem_tools():
    dashboard = _skill_doc(
        DATA_ANALYZING_SKILL,
        "references/dashboard/workflow.md",
        "references/dashboard/guide.md",
    )
    creator = _skill_doc(SKILL_CREATOR_SKILL, "SKILL.md")

    assert "file_write" in dashboard
    assert "file_move(source_path=" in dashboard
    assert "不要再次用 `file_write` 重写相同内容" in dashboard
    assert "file_edit(file_path, old_content, new_content)" in dashboard
    assert "dir_create" in creator
    assert "present_artifacts" in creator


def test_system_skills_use_load_skill_as_the_skill_entrypoint():
    for name in CURRENT_SKILLS:
        text = _skill_text(name)
        assert "skill_load" not in text

    assert "load_skill" in _skill_text(OFFICE_SKILL)


# ---------------------------------------------------------------------------
# 各能力文档的内容契约
# ---------------------------------------------------------------------------


def test_quality_check_capability_requires_tool_self_check():
    """质量治理能力必须以 data_analyzing_tool 的 check_yaml 为执行臂，禁止纯口算质量报告。"""
    quality_check = _skill_doc(DATA_ANALYZING_SKILL, "references/governance/quality-check.md")

    assert "data_analyzing_tool" in quality_check
    assert "check_yaml" in quality_check
    assert "all_importable" in quality_check
    assert "禁止跳过自检直接交付" in quality_check


def test_query_capability_contains_complete_query_contract():
    query = _skill_doc(DATA_ANALYZING_SKILL, "references/analysis/query.md", "references/analysis/dialect.md")

    # AI coding 式目录路线：file_read(semantic_catalog) -> file_grep -> file_read，不引导 semantic_search
    assert "当前用户可见（当前数据源组）的全部启用语义模型" in query
    assert "`/mnt/user-data/workspace/semantic/`" in query
    assert "_semantic_catalog.yaml" in query
    assert "禁止为找模型逐个读取目录里的 YAML" in query
    # grep 无命中即判定缺失：不得靠逐个读文件碰运气
    assert "无命中" in query
    assert "file_read" in query
    assert "file_grep" in query
    assert "value_type" in query
    assert "literal" in query
    assert "sql_function" in query
    assert 'valueType: "expression"' in query
    assert "rightExpression" in query
    assert "结构化 `left` / `right`" in query
    assert "禁止分号、SQL 注释和写操作" in query
    assert "IN` / `NOT IN`" in query
    assert "与 `value` 等长的类型列表" in query
    assert '"type":"dimension"' in query
    assert '"type":"metric"' in query
    assert "顶层 `and`" in query
    assert "范围优先使用半开区间" in query
    assert "不能同时传 `dsl`" in query
    assert "**DSL 模式**和**SQL 模式**" in query
    assert "DSL 模式在 `action_payload` 里传 `dsl`" in query
    assert "SQL 模式传 `sql`" in query
    assert "SQL 模式不做 validate_dsl 校验" in query
    assert "不是 `mode` 参数" in query
    assert "CTE" in query
    assert "年度总量对比" in query
    assert "读取候选后确认当前配置不存在" in query
    assert "用户必须补充公式和统计周期" in query
    assert "查询成功后系统始终将完整结果保存" in query
    assert "result_path" in query
    assert "用户只要求查询、查看或列出结果时，不读取结果文件" in query
    assert "回答具体数值、形成业务结论" in query
    assert "必须先读取 `result_path` 中的实际数据" in query
    assert "优先重新执行更小的 DSL/SQL" in query
    assert "禁止为了分析把大型结果逐页全部加载进模型上下文" in query
    assert "/mnt/skills/data-analyzing/references/analysis/dialect.md" in query
    assert "## 必读资料" in query
    assert "DSL 查询只使用 `time_grain`" in query
    assert "DATE_FORMAT(time_col, 'yyyy-MM')" in query
    assert "DATE_FORMAT(time_col, '%Y-%m')" in query
    # 查询能力文档不承担场景路由与跨域内容（那是路由层和各自文档的职责）
    assert "### 1. 判断场景" not in query
    assert "看板、图表、Office 文档" not in query


def test_officecli_system_skill_uses_chatdata_runtime_contract():
    office = _skill_text(OFFICE_SKILL)

    assert "present_artifacts" in office
    assert "references/word.md" in office
    assert "officecli load_skill" not in office
    assert "officecli plugins" not in office
    assert "officecli watch" not in office
    assert "curl -fsSL" not in office


# ---------------------------------------------------------------------------
# 代码侧引用的技能内相对路径
# ---------------------------------------------------------------------------

# 后端源码根（只扫源码，不含 tests：测试里的 references/*.md 多为合成夹具名）。
SERVER_ROOT = Path(__file__).resolve().parents[4]

SKILL_DOC_REFERENCE = re.compile(r"(?:references|assets)/[A-Za-z0-9_./-]+\.(?:md|json)")

# 旧技能名与旧显示名：改名/合并后源码侧不得再出现。
# 技能目录侧由 ``test_system_skills_do_not_reference_legacy_skill_names`` 覆盖。
LEGACY_SKILL_MARKERS = DEAD_SKILL_NAMES + (
    "语义查询技能",
    "看板生成技能",
    "语义质量检查技能",
    # 2026-09-18 合并后的显示名，当日又随技能名 data-warehouse -> data-analyzing 一并换掉
    "数仓技能",
    "风险分析技能",
    "机构画像分析",
)


def _builtin_skill_files() -> set[str]:
    """全部内置技能内的相对文件路径，如 ``references/analysis/query.md``。"""
    files: set[str] = set()
    for name in CURRENT_SKILLS:
        root = SYSTEM_SKILLS_ROOT / name
        files.update(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())
    return files


def test_source_code_references_existing_skill_docs():
    """代码注释与 docstring 里提到的技能内资料路径必须真实存在。

    注释和 docstring 拿不到 ``utils.builtin_skills`` 的常量（f-string 也不能当
    docstring），路径只能写字面量；而这类写法往往紧挨着「单一维护点在这里」的声明，
    路径错了比不写更误导——技能内资料按能力分层后正是靠本用例兜住这类漂移。
    技能目录内的自引用由 ``test_data_analyzing_referenced_paths_exist`` 覆盖。
    """
    known = _builtin_skill_files()
    missing = []
    for path in sorted(SERVER_ROOT.rglob("*.py")):
        if "tests" in path.parts or "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            for reference in SKILL_DOC_REFERENCE.findall(line):
                if reference not in known:
                    missing.append(f"{path.relative_to(SERVER_ROOT)}:{lineno} -> {reference}")

    assert not missing, "代码里引用了不存在的技能内资料路径:\n" + "\n".join(missing)


def test_source_code_does_not_reference_legacy_skill_names():
    """源码侧的旧技能名/旧显示名必须清零。

    技能名只有 ``utils.builtin_skills`` 一个来源，解析不到旧名不会报错、只会静默
    表现为「没有该技能」；别名机制已在 2026-09-18 删除，旧名不再被任何一层兜住。
    注释与 docstring 拿不到常量（f-string 不能当 docstring），只能靠本用例兜住。
    """
    hits = []
    for path in sorted(SERVER_ROOT.rglob("*.py")):
        if "tests" in path.parts or "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            for marker in LEGACY_SKILL_MARKERS:
                if marker in line:
                    hits.append(f"{path.relative_to(SERVER_ROOT)}:{lineno} -> {marker}")

    assert not hits, "源码里残留旧技能名:\n" + "\n".join(hits)
