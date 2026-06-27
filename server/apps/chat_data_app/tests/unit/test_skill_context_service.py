import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.services import skill_context_service
from apps.chat_data_app.agent.tools.readonly_skill_load import ReadonlyLoadSkillInput, create_readonly_skill_load_tool

TEST_QUERY_SKILL_NAME = "custom-query-skill"
TEST_QUERY_REFERENCE_NAME = "domain/business-context"


def _create_query_skill(tmp_path):
    skill_dir = tmp_path / TEST_QUERY_SKILL_NAME
    refs_dir = skill_dir / "references" / "domain"
    refs_dir.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "---\n"
        f"name: {TEST_QUERY_SKILL_NAME}\n"
        "title: 自定义问数技能\n"
        "description: 测试只读问数技能\n"
        "metadata:\n"
        "  category: business_query\n"
        "  skill_scope: query\n"
        "  output_type: text\n"
        "---\n\n"
        "# 自定义问数技能\n",
        encoding="utf-8",
    )
    (refs_dir / "business-context.md").write_text("自定义业务背景", encoding="utf-8")
    return skill_md


def _patch_query_skill(monkeypatch, skill_md):
    async def fake_resolve_skill_path_async(_name, user_id=""):
        return {
            "name": TEST_QUERY_SKILL_NAME,
            "title": "自定义问数技能",
            "description": "测试只读问数技能",
            "category": "business_query",
            "skill_scope": "query",
            "output_type": "text",
            "path": str(skill_md),
        }

    monkeypatch.setattr(skill_context_service.skill_service, "resolve_skill_path_async", fake_resolve_skill_path_async)


def test_readonly_skill_input_accepts_json_string_reference_names():
    payload = ReadonlyLoadSkillInput(
        skill_name=TEST_QUERY_SKILL_NAME,
        reference_names=f'["{TEST_QUERY_REFERENCE_NAME}"]',
    )

    assert payload.reference_names == [TEST_QUERY_REFERENCE_NAME]


def test_readonly_skill_input_normalizes_reference_name_variants():
    assert ReadonlyLoadSkillInput(skill_name=TEST_QUERY_SKILL_NAME, reference_names=None).reference_names is None
    assert ReadonlyLoadSkillInput(skill_name=TEST_QUERY_SKILL_NAME, reference_names="  ").reference_names is None
    assert ReadonlyLoadSkillInput(skill_name=TEST_QUERY_SKILL_NAME, reference_names="plain/ref").reference_names == ["plain/ref"]
    assert ReadonlyLoadSkillInput(skill_name=TEST_QUERY_SKILL_NAME, reference_names='"plain/ref"').reference_names == ["plain/ref"]
    assert ReadonlyLoadSkillInput(skill_name=TEST_QUERY_SKILL_NAME, reference_names=("a", "b")).reference_names == ["a", "b"]


def test_readonly_skill_tool_accepts_json_string_reference_names(monkeypatch, tmp_path):
    _patch_query_skill(monkeypatch, _create_query_skill(tmp_path))
    tool = create_readonly_skill_load_tool()
    result = asyncio.run(tool.ainvoke({
        "skill_name": TEST_QUERY_SKILL_NAME,
        "reference_names": f'["{TEST_QUERY_REFERENCE_NAME}"]',
    }))

    assert result.startswith("[skill_load]")
    assert f"loaded_references={TEST_QUERY_REFERENCE_NAME}" in result
    assert f"# references/{TEST_QUERY_REFERENCE_NAME}.md" in result


def test_readonly_skill_tool_returns_error_text(monkeypatch):
    async def fake_load_readonly_skill_context(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "apps.chat_data_app.agent.tools.readonly_skill_load.load_readonly_skill_context",
        fake_load_readonly_skill_context,
    )

    tool = create_readonly_skill_load_tool()
    result = asyncio.run(tool.ainvoke({"skill_name": TEST_QUERY_SKILL_NAME}))

    assert result == "skill_load 执行失败: boom"


def test_skill_context_small_helpers_cover_truncation_and_path_edges(tmp_path):
    assert skill_context_service._truncate_text("abc", 3) == ("abc", False)
    truncated, was_truncated = skill_context_service._truncate_text("abcd", 3)
    assert truncated == "abc\n\n[内容已截断]"
    assert was_truncated is True
    assert skill_context_service._normalize_reference_name("domain/business-context.md") == "domain/business-context"
    with pytest.raises(ValueError, match="路径越界"):
        skill_context_service._reference_path(tmp_path, "/tmp/outside")
    assert asyncio.run(skill_context_service._list_references_async(tmp_path)) == []


def test_readonly_skill_context_loads_skill_and_selected_references(monkeypatch, tmp_path):
    _patch_query_skill(monkeypatch, _create_query_skill(tmp_path))
    payload = asyncio.run(skill_context_service.load_readonly_skill_context(
        TEST_QUERY_SKILL_NAME,
        reference_names=[TEST_QUERY_REFERENCE_NAME],
    ))
    formatted = skill_context_service.format_readonly_skill_context(payload)

    assert payload["name"] == TEST_QUERY_SKILL_NAME
    assert payload["capabilities"] == {
        "read_references": True,
        "use_scripts": False,
        "use_assets": False,
        "sandbox": False,
    }
    assert TEST_QUERY_REFERENCE_NAME in payload["available_references"]
    assert [item["name"] for item in payload["loaded_references"]] == [TEST_QUERY_REFERENCE_NAME]
    assert "runtime-skills" not in formatted
    assert "capabilities=" not in formatted
    assert f"# references/{TEST_QUERY_REFERENCE_NAME}.md" in formatted
    assert "自定义业务背景" in formatted


def test_readonly_skill_context_rejects_invalid_reference_name(monkeypatch, tmp_path):
    _patch_query_skill(monkeypatch, _create_query_skill(tmp_path))

    with pytest.raises(ValueError, match="reference 名称无效"):
        asyncio.run(skill_context_service.load_readonly_skill_context(
            TEST_QUERY_SKILL_NAME,
            reference_names=["../secret"],
        ))

    with pytest.raises(ValueError, match="reference 名称无效"):
        asyncio.run(skill_context_service.load_readonly_skill_context(
            TEST_QUERY_SKILL_NAME,
            reference_names=["/tmp/secret"],
        ))


def test_readonly_skill_context_rejects_empty_missing_and_missing_file(monkeypatch, tmp_path):
    with pytest.raises(ValueError, match="skill_name 不能为空"):
        asyncio.run(skill_context_service.load_readonly_skill_context("  "))

    async def fake_missing_metadata(_name, user_id=""):
        return None

    monkeypatch.setattr(skill_context_service.skill_service, "resolve_skill_path_async", fake_missing_metadata)
    with pytest.raises(ValueError, match="技能不存在或无权限访问"):
        asyncio.run(skill_context_service.load_readonly_skill_context("missing-query-skill"))

    missing_md = tmp_path / "missing" / "SKILL.md"

    async def fake_missing_file(_name, user_id=""):
        return {
            "name": "missing-file-skill",
            "title": "缺文件技能",
            "category": "query_routing",
            "skill_scope": "query",
            "output_type": "text",
            "path": str(missing_md),
        }

    monkeypatch.setattr(skill_context_service.skill_service, "resolve_skill_path_async", fake_missing_file)
    with pytest.raises(ValueError, match="技能文件不存在"):
        asyncio.run(skill_context_service.load_readonly_skill_context("missing-file-skill"))


def test_readonly_skill_context_rejects_non_text_skill(monkeypatch, tmp_path):
    skill_dir = tmp_path / "non-text-skill"
    skill_dir.mkdir()
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text("# 非文本技能\n", encoding="utf-8")

    async def fake_resolve_skill_path_async(_name, user_id=""):
        return {
            "name": "non-text-skill",
            "title": "非文本技能",
            "description": "测试输出类型限制",
            "category": "query_routing",
            "skill_scope": "query",
            "output_type": "artifact",
            "path": str(skill_md),
        }

    monkeypatch.setattr(skill_context_service.skill_service, "resolve_skill_path_async", fake_resolve_skill_path_async)

    with pytest.raises(ValueError, match="仅支持文本技能"):
        asyncio.run(skill_context_service.load_readonly_skill_context("non-text-skill"))


def test_readonly_skill_context_handles_duplicate_limited_missing_and_truncated_references(monkeypatch, tmp_path):
    skill_dir = tmp_path / "limited-query-skill"
    refs_dir = skill_dir / "references"
    refs_dir.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text("---\nname: limited-query-skill\n---\n\n# 主说明\n", encoding="utf-8")
    for index in range(1, 7):
        (refs_dir / f"ref{index}.md").write_text(f"reference-{index}-" + "x" * 200, encoding="utf-8")

    async def fake_resolve_skill_path_async(_name, user_id=""):
        return {
            "name": "limited-query-skill",
            "title": "有限技能",
            "description": "测试引用限制",
            "category": "query_routing",
            "skill_scope": "query",
            "output_type": "text",
            "path": str(skill_md),
        }

    monkeypatch.setattr(skill_context_service.skill_service, "resolve_skill_path_async", fake_resolve_skill_path_async)

    payload = asyncio.run(skill_context_service.load_readonly_skill_context(
        "limited-query-skill",
        reference_names=["ref1", "ref1", "missing", "ref2", "ref3", "ref4", "ref5", "ref6"],
        max_chars=80,
    ))
    formatted = skill_context_service.format_readonly_skill_context(payload)

    assert [item["name"] for item in payload["loaded_references"]] == ["ref1"]
    assert payload["loaded_references"][0]["truncated"] is True
    assert payload["missing_references"] == ["missing"]
    assert "missing_references=missing" in formatted

    exhausted = asyncio.run(skill_context_service.load_readonly_skill_context(
        "limited-query-skill",
        reference_names=["ref1"],
        max_chars=4,
    ))
    assert exhausted["loaded_references"] == []
    assert exhausted["skill_md_truncated"] is True


def test_readonly_skill_context_rejects_script_skills(monkeypatch, tmp_path):
    skill_dir = tmp_path / "script-query-skill"
    (skill_dir / "scripts").mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "---\n"
        "name: script-query-skill\n"
        "title: 脚本问数技能\n"
        "description: 测试脚本限制\n"
        "metadata:\n"
        "  category: query_routing\n"
        "  output_type: text\n"
        "---\n\n"
        "# 脚本问数技能\n",
        encoding="utf-8",
    )
    (skill_dir / "scripts" / "main.py").write_text("print('no')\n", encoding="utf-8")

    async def fake_resolve_skill_path_async(_name, user_id=""):
        return {
            "name": "script-query-skill",
            "title": "脚本问数技能",
            "description": "测试脚本限制",
            "category": "query_routing",
            "skill_scope": "query",
            "output_type": "text",
            "path": str(skill_md),
        }

    monkeypatch.setattr(skill_context_service.skill_service, "resolve_skill_path_async", fake_resolve_skill_path_async)

    with pytest.raises(ValueError, match="不支持包含 scripts 的技能"):
        asyncio.run(skill_context_service.load_readonly_skill_context("script-query-skill"))


def test_readonly_skill_context_rejects_asset_skills(monkeypatch, tmp_path):
    skill_dir = tmp_path / "asset-query-skill"
    (skill_dir / "assets").mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "---\n"
        "name: asset-query-skill\n"
        "title: 素材问数技能\n"
        "description: 测试素材限制\n"
        "metadata:\n"
        "  category: query_routing\n"
        "  output_type: text\n"
        "---\n\n"
        "# 素材问数技能\n",
        encoding="utf-8",
    )
    (skill_dir / "assets" / "sample.txt").write_text("no\n", encoding="utf-8")

    async def fake_resolve_skill_path_async(_name, user_id=""):
        return {
            "name": "asset-query-skill",
            "title": "素材问数技能",
            "description": "测试素材限制",
            "category": "query_routing",
            "skill_scope": "query",
            "output_type": "text",
            "path": str(skill_md),
        }

    monkeypatch.setattr(skill_context_service.skill_service, "resolve_skill_path_async", fake_resolve_skill_path_async)

    with pytest.raises(ValueError, match="不支持包含 assets 的技能"):
        asyncio.run(skill_context_service.load_readonly_skill_context("asset-query-skill"))


def test_readonly_skill_context_rejects_non_query_skills():
    with pytest.raises(ValueError, match="仅支持问数可用技能"):
        asyncio.run(skill_context_service.load_readonly_skill_context("dashboard-generation"))
