import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "dummy")

from apps.chat_data_app.agent.prompts.chat_data import build_unified_system_prompt
from apps.chat_data_app.agent.runtime_options import AgentRuntimeOptions
from apps.chat_data_app.agent.skills.loader import format_skills_prompt


def test_system_prompt_requires_semantic_discovery_and_detail_read_before_query():
    prompt = build_unified_system_prompt(
        AgentRuntimeOptions(),
        available_skills_xml=format_skills_prompt([{
            "name": "semantic-query",
            "title": "语义查询技能",
            "description": "基于语义模型完成数据查询",
        }]),
    )

    assert "第一步必须调用 `file_read`" in prompt
    assert "不得跳过技能直接调用语义或查询工具" in prompt
    assert "<name>semantic-query</name>" in prompt
    assert "必须先读取该技能" in prompt
    assert "DSL 与 SQL 只能二选一" in prompt
    assert "SQL 路径不调用 `dsl2sql_tool`" in prompt
    assert "不能通过 SQL 绕过" not in prompt
    assert "每次查询只能使用一个 `semantic_config_id`" in prompt
    assert "get_field" not in prompt
    assert "source_tool=semantic_search" not in prompt
    assert ".tool-offload" not in prompt
    assert "## 执行连续性" in prompt
    assert "## 工作区与交付" in prompt


def test_execution_continuity_is_separate_from_workspace_rules():
    prompt = build_unified_system_prompt(AgentRuntimeOptions())

    continuity_start = prompt.index("## 执行连续性")
    workspace_start = prompt.index("## 工作区与交付")
    continuity = prompt[continuity_start:workspace_start]
    workspace = prompt[workspace_start:prompt.index("## 工具结果卸载处理")]

    assert "上下文足够时直接执行" in continuity
    assert "工具调用前后各用一句话" in continuity
    assert "上下文足够时直接执行" not in workspace
    assert "工具调用前后各用一句话" not in workspace
    assert "修改已有文件前先读取最新内容" in workspace
    assert "outputs" in workspace


def test_system_prompt_keeps_detailed_semantic_rules_in_skill():
    prompt = build_unified_system_prompt(
        AgentRuntimeOptions(),
        available_skills_xml=format_skills_prompt([{
            "name": "semantic-query",
            "title": "语义查询技能",
            "description": "基于语义模型完成数据查询",
        }]),
    )

    assert "## DSL 参数" not in prompt
    assert "## DSL 示例" not in prompt
    assert "## NL2SQL 回退规则" not in prompt
    assert "# 多步数据分析示例" not in prompt
    assert '"dimensions":[{"name":"时间维度"' not in prompt
    assert "当前数据库方言：Hive" in prompt
    assert "DSL 中只写 `time_grain`" in prompt
    assert "/mnt/skills/semantic-query/references/dialect.md" not in prompt
    assert prompt.count("## 输出与最终回答") == 1
    assert "## 输出规范" not in prompt
    assert "## 最终回答" not in prompt


def test_system_prompt_keeps_dialect_fallback_for_legacy_skill_snapshot():
    prompt = build_unified_system_prompt(AgentRuntimeOptions())

    assert "当前数据库方言：Hive" in prompt
    assert "DSL 中只写 `time_grain`" in prompt
    assert "/mnt/skills/semantic-query/references/dialect.md" not in prompt
