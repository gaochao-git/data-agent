"""Ensure bundled system skills only describe tools registered by the unified agent."""

from pathlib import Path


SYSTEM_SKILLS_ROOT = Path(__file__).resolve().parents[2] / "agent" / "skills"


def _skill_text(name: str) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((SYSTEM_SKILLS_ROOT / name).rglob("*.md"))
    )


def test_system_skills_do_not_reference_removed_patch_tool_contract():
    legacy_markers = (
        'file_edit(operation="write"',
        'file_edit(operation="patch"',
        "*** Begin Patch",
        "*** Update File:",
        "*** End Patch",
    )

    for name in (
        "dashboard-generation",
        "officecli",
        "semantic-query",
        "skill-creator",
    ):
        text = _skill_text(name)
        assert all(marker not in text for marker in legacy_markers)


def test_system_skills_describe_current_filesystem_tools():
    dashboard = _skill_text("dashboard-generation")
    creator = _skill_text("skill-creator")

    assert "file_write" in dashboard
    assert "file_move(source_path=" in dashboard
    assert "不要再次用 `file_write` 重写相同内容" in dashboard
    assert "file_edit(file_path, old_content, new_content)" in dashboard
    assert "dir_create" in creator
    assert "present_artifacts" in creator


def test_system_skills_use_file_read_as_the_only_skill_entrypoint():
    for name in (
        "dashboard-generation",
        "officecli",
        "semantic-query",
        "skill-creator",
    ):
        text = _skill_text(name)
        assert "skill_load" not in text

    assert "file_read" in _skill_text("dashboard-generation")
    assert "file_read" in _skill_text("officecli")
    assert "file_read" in _skill_text("semantic-query")
    assert "file_read" in _skill_text("skill-creator")


def test_semantic_query_skill_contains_complete_query_contract():
    semantic_query = _skill_text("semantic-query")

    # AI coding 式目录路线：dir_list -> file_grep -> file_read，不引导 semantic_search
    assert "当前用户可见（当前数据源组）的全部启用语义模型" in semantic_query
    assert "`/mnt/user-data/workspace/semantic/`" in semantic_query
    assert "禁止为找模型逐个读取目录里的 YAML" in semantic_query
    # grep 无命中即判定缺失：不得靠逐个读文件碰运气
    assert "无命中" in semantic_query
    assert "dir_list" in semantic_query
    assert "file_grep" in semantic_query
    assert "file_read" in semantic_query
    assert "value_type" in semantic_query
    assert "literal" in semantic_query
    assert "sql_function" in semantic_query
    assert 'valueType: "expression"' in semantic_query
    assert "rightExpression" in semantic_query
    assert "结构化 `left` / `right`" in semantic_query
    assert "禁止分号、SQL 注释和写操作" in semantic_query
    assert "IN` / `NOT IN`" in semantic_query
    assert "与 `value` 等长的类型列表" in semantic_query
    assert '"type":"dimension"' in semantic_query
    assert '"type":"metric"' in semantic_query
    assert "顶层 `and`" in semantic_query
    assert "范围优先使用半开区间" in semantic_query
    assert "不能同时传 `dsl`" in semantic_query
    assert "**DSL 模式**和**SQL 模式**" in semantic_query
    assert "DSL 模式传顶层 `dsl`" in semantic_query
    assert "SQL 模式传顶层 `sql`" in semantic_query
    assert "SQL 模式不调用 `dsl2sql_tool`" in semantic_query
    assert "不是 `mode` 参数" in semantic_query
    assert "CTE" in semantic_query
    assert "年度总量对比" in semantic_query
    assert "读取候选后确认当前配置不存在" in semantic_query
    assert "用户必须补充公式和统计周期" in semantic_query
    assert "查询成功后系统始终将完整结果保存" in semantic_query
    assert "result_path" in semantic_query
    assert "用户只要求查询、查看或列出结果时，不读取结果文件" in semantic_query
    assert "回答具体数值、形成业务结论" in semantic_query
    assert "必须先读取 `result_path` 中的实际数据" in semantic_query
    assert "优先重新执行更小的 DSL/SQL" in semantic_query
    assert "禁止为了分析把大型结果逐页全部加载进模型上下文" in semantic_query
    assert "/mnt/skills/semantic-query/references/dialect.md" in semantic_query
    assert "## 必读资料" in semantic_query
    assert "DSL 查询只使用 `time_grain`" in semantic_query
    assert "DATE_FORMAT(time_col, 'yyyy-MM')" in semantic_query
    assert "DATE_FORMAT(time_col, '%Y-%m')" in semantic_query
    assert "### 1. 判断场景" not in semantic_query
    assert "看板、图表、Office 文档" not in semantic_query


def test_officecli_system_skill_uses_chatdata_runtime_contract():
    office = _skill_text("officecli")

    assert "present_artifacts" in office
    assert "references/word.md" in office
    assert "officecli load_skill" not in office
    assert "officecli plugins" not in office
    assert "officecli watch" not in office
    assert "curl -fsSL" not in office
