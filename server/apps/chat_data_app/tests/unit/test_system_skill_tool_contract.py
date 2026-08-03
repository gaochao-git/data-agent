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
        "chart-processing",
        "dashboard-generation",
        "officecli",
        "skill-creator",
    ):
        text = _skill_text(name)
        assert all(marker not in text for marker in legacy_markers)


def test_system_skills_describe_current_filesystem_tools():
    chart = _skill_text("chart-processing")
    dashboard = _skill_text("dashboard-generation")
    creator = _skill_text("skill-creator")

    assert "semantic_query_tool(dsl=同一份DSL, save_as=" in chart
    assert "file_write" in chart
    assert "file_edit(file_path, old_content, new_content)" in chart
    assert "file_write" in dashboard
    assert "file_move(source_path=" in dashboard
    assert "不要再次用 `file_write` 重写相同内容" in dashboard
    assert "file_edit(file_path, old_content, new_content)" in dashboard
    assert "dir_create" in creator
    assert "present_artifacts" in creator


def test_system_skills_use_file_read_as_the_only_skill_entrypoint():
    for name in (
        "chart-processing",
        "dashboard-generation",
        "officecli",
        "skill-creator",
    ):
        text = _skill_text(name)
        assert "skill_load" not in text

    assert "file_read" in _skill_text("chart-processing")
    assert "file_read" in _skill_text("dashboard-generation")
    assert "file_read" in _skill_text("officecli")
    assert "file_read" in _skill_text("skill-creator")


def test_officecli_system_skill_uses_chatdata_runtime_contract():
    office = _skill_text("officecli")

    assert "present_artifacts" in office
    assert "references/word.md" in office
    assert "officecli load_skill" not in office
    assert "officecli plugins" not in office
    assert "officecli watch" not in office
    assert "curl -fsSL" not in office
