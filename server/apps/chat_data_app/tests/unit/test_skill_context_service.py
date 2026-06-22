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


def test_readonly_skill_input_accepts_json_string_reference_names():
    payload = ReadonlyLoadSkillInput(
        skill_name="business-query",
        reference_names='["common/query-routing"]',
    )

    assert payload.reference_names == ["common/query-routing"]


def test_readonly_skill_input_normalizes_reference_name_variants():
    assert ReadonlyLoadSkillInput(skill_name="business-query", reference_names=None).reference_names is None
    assert ReadonlyLoadSkillInput(skill_name="business-query", reference_names="  ").reference_names is None
    assert ReadonlyLoadSkillInput(skill_name="business-query", reference_names="plain/ref").reference_names == ["plain/ref"]
    assert ReadonlyLoadSkillInput(skill_name="business-query", reference_names='"plain/ref"').reference_names == ["plain/ref"]
    assert ReadonlyLoadSkillInput(skill_name="business-query", reference_names=("a", "b")).reference_names == ["a", "b"]


def test_readonly_skill_tool_accepts_json_string_reference_names():
    tool = create_readonly_skill_load_tool()
    result = asyncio.run(tool.ainvoke({
        "skill_name": "business-query",
        "reference_names": '["common/query-routing"]',
    }))

    assert result.startswith("[skill_load]")
    assert "loaded_references=common/query-routing" in result
    assert "# references/common/query-routing.md" in result


def test_readonly_skill_tool_returns_error_text(monkeypatch):
    async def fake_load_readonly_skill_context(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "apps.chat_data_app.agent.tools.readonly_skill_load.load_readonly_skill_context",
        fake_load_readonly_skill_context,
    )

    tool = create_readonly_skill_load_tool()
    result = asyncio.run(tool.ainvoke({"skill_name": "business-query"}))

    assert result == "skill_load 执行失败: boom"


def test_skill_context_small_helpers_cover_truncation_and_path_edges(tmp_path):
    assert skill_context_service._truncate_text("abc", 3) == ("abc", False)
    truncated, was_truncated = skill_context_service._truncate_text("abcd", 3)
    assert truncated == "abc\n\n[内容已截断]"
    assert was_truncated is True
    assert skill_context_service._normalize_reference_name("common/query-routing.md") == "common/query-routing"
    with pytest.raises(ValueError, match="路径越界"):
        skill_context_service._reference_path(tmp_path, "/tmp/outside")
    assert asyncio.run(skill_context_service._list_references_async(tmp_path)) == []


def test_readonly_skill_context_loads_skill_and_selected_references():
    payload = asyncio.run(skill_context_service.load_readonly_skill_context(
        "business-query",
        reference_names=["prepaid-fund-supervision/business-context"],
    ))
    formatted = skill_context_service.format_readonly_skill_context(payload)

    assert payload["name"] == "business-query"
    assert payload["capabilities"] == {
        "read_references": True,
        "use_scripts": False,
        "use_assets": False,
        "sandbox": False,
    }
    assert "common/query-routing" in payload["available_references"]
    assert "common/query-policy" not in payload["available_references"]
    assert "prepaid-fund-supervision/data-sources" not in payload["available_references"]
    assert "prepaid-fund-supervision/examples" not in payload["available_references"]
    assert "order-analysis/business-context" in payload["available_references"]
    assert [item["name"] for item in payload["loaded_references"]] == ["prepaid-fund-supervision/business-context"]
    assert "runtime-skills" not in formatted
    assert "capabilities=" not in formatted
    assert "# references/prepaid-fund-supervision/business-context.md" in formatted


def test_readonly_skill_context_loads_order_analysis_references():
    payload = asyncio.run(skill_context_service.load_readonly_skill_context(
        "business-query",
        reference_names=["order-analysis/business-context"],
    ))
    formatted = skill_context_service.format_readonly_skill_context(payload)

    assert [item["name"] for item in payload["loaded_references"]] == [
        "order-analysis/business-context",
    ]
    assert "订单销售分析业务背景" in formatted


def test_readonly_skill_context_rejects_invalid_reference_name():
    with pytest.raises(ValueError, match="reference 名称无效"):
        asyncio.run(skill_context_service.load_readonly_skill_context(
            "business-query",
            reference_names=["../secret"],
        ))

    with pytest.raises(ValueError, match="reference 名称无效"):
        asyncio.run(skill_context_service.load_readonly_skill_context(
            "business-query",
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
            "output_type": "text",
            "path": str(skill_md),
        }

    monkeypatch.setattr(skill_context_service.skill_service, "resolve_skill_path_async", fake_resolve_skill_path_async)

    with pytest.raises(ValueError, match="不支持包含 assets 的技能"):
        asyncio.run(skill_context_service.load_readonly_skill_context("asset-query-skill"))


def test_readonly_skill_context_rejects_non_query_skills():
    with pytest.raises(ValueError, match="仅支持问数类技能"):
        asyncio.run(skill_context_service.load_readonly_skill_context("dashboard-generation"))


def test_build_available_skills_prompt_filters_non_query_skills():
    prompt = asyncio.run(skill_context_service.build_available_skills_prompt())

    assert "business-query" in prompt
    assert "## 可用业务知识技能" in prompt
    assert "不是 workflow 技能" in prompt
    assert "先调用 `skill_load` 加载主说明和路由 reference" in prompt
    assert "common/query-routing" in prompt
    assert "必须在生成 DSL 前调用 `skill_load`" in prompt
    assert "没有明显匹配的技能时不要硬加载" in prompt
    assert "dashboard-generation" not in prompt
    assert "chart-processing" not in prompt


def test_build_available_skills_prompt_empty_and_plain_skill(monkeypatch):
    async def fake_empty_list(*_args, **_kwargs):
        return []

    monkeypatch.setattr(skill_context_service, "list_query_skill_routing_metadata", fake_empty_list)
    assert asyncio.run(skill_context_service.build_available_skills_prompt()) == ""

    async def fake_plain_list(*_args, **_kwargs):
        return [{"name": "plain", "title": "普通技能", "description": "只含描述", "domain": "", "category": ""}]

    monkeypatch.setattr(skill_context_service, "list_query_skill_routing_metadata", fake_plain_list)
    prompt = asyncio.run(skill_context_service.build_available_skills_prompt())

    assert "- plain: 普通技能。只含描述" in prompt
    assert "领域:" not in prompt
    assert "分类:" not in prompt


def test_list_query_skills_fallback_and_filters(monkeypatch):
    calls = []

    async def fake_list_skills_async(user_id="", scope="runtime"):
        calls.append(scope)
        if scope == "runtime":
            raise RuntimeError("runtime failed")
        return [
            {"name": "", "title": "无名技能", "category": "query_routing", "output_type": "text"},
            {"name": "workflow", "title": "工作流", "category": "workflow", "output_type": "text"},
            {"name": "scripted", "title": "脚本", "category": "query_routing", "output_type": "text", "scripts": ["main.py"]},
            {"name": "asseted", "title": "素材", "category": "query_routing", "output_type": "text", "assets": ["a.png"]},
            {"name": "plain", "title": "普通", "category": "", "output_type": ""},
        ]

    monkeypatch.setattr(skill_context_service.skill_service, "list_skills_async", fake_list_skills_async)

    result = asyncio.run(skill_context_service._list_query_skills(scope="runtime"))

    assert calls == ["runtime", "system"]
    assert [item["name"] for item in result] == ["plain"]


def test_list_query_skills_double_failure_and_routing_truncation(monkeypatch):
    async def fake_list_skills_async(*_args, **_kwargs):
        raise RuntimeError("all failed")

    monkeypatch.setattr(skill_context_service.skill_service, "list_skills_async", fake_list_skills_async)

    assert asyncio.run(skill_context_service._list_query_skills(scope="runtime")) == []
    assert skill_context_service._truncate_routing_text("abc", 5) == "abc"
    assert skill_context_service._truncate_routing_text("abcdef", 3) == "abc..."


def test_list_query_skill_routing_metadata_skips_empty_name_and_truncates(monkeypatch):
    async def fake_list_query_skills(*_args, **_kwargs):
        return [
            {"name": "", "title": "跳过"},
            {"name": "query", "title": "t" * 200, "domain": "d" * 240, "description": "x" * 400, "category": "business_query"},
        ]

    monkeypatch.setattr(skill_context_service, "_list_query_skills", fake_list_query_skills)

    result = asyncio.run(skill_context_service.list_query_skill_routing_metadata())

    assert [item["name"] for item in result] == ["query"]
    assert result[0]["title"].endswith("...")
    assert result[0]["domain"].endswith("...")
    assert result[0]["description"].endswith("...")
