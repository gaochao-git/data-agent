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


def test_readonly_skill_tool_accepts_json_string_reference_names():
    tool = create_readonly_skill_load_tool()
    result = asyncio.run(tool.ainvoke({
        "skill_name": "business-query",
        "reference_names": '["common/query-routing"]',
    }))

    assert result.startswith("[skill_load]")
    assert "loaded_references=common/query-routing" in result
    assert "# references/common/query-routing.md" in result


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
    assert "no_scripts" in formatted
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
