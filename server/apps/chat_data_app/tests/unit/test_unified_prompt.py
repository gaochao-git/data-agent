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
from utils.builtin_skills import DATA_ANALYZING_SKILL


def test_system_prompt_routes_queries_to_skill_without_gate_blocks():
    """门禁/场景路由/优先级块已删除：技能路由单一来源是技能使用块 + available_skills 描述。"""
    prompt = build_unified_system_prompt(
        AgentRuntimeOptions(),
        available_skills_xml=format_skills_prompt(
            [
                {
                    "name": DATA_ANALYZING_SKILL,
                    "title": "数据分析技能",
                    "description": "基于语义模型完成数据查询",
                }
            ]
        ),
    )

    # 已删除的块不再出现
    assert "## 语义查询门禁" not in prompt
    assert "## 场景路由" not in prompt
    assert "## 优先级" not in prompt
    assert "第一步必须调用 `file_read`" not in prompt
    assert "get_current_time" not in prompt
    assert "calculator_tool" not in prompt

    # 技能使用规则仍在：意图命中读取技能 + 显式指定技能直接读取
    assert "## 技能使用" in prompt
    assert f"<name>{DATA_ANALYZING_SKILL}</name>" in prompt

    # DSL 细节、查询专属回答规则不进系统提示词
    assert "**DSL 模式**和**SQL 模式**" not in prompt
    assert "DSL 模式传顶层 `dsl`" not in prompt
    assert "SQL 模式传顶层 `sql`" not in prompt
    assert "不是工具参数，不要传 `mode`" not in prompt
    assert "不能通过 SQL 绕过" not in prompt
    assert "每次查询只能使用一个 `semantic_config_id`" not in prompt
    assert "单指标单值查询" not in prompt
    assert "洞察只能基于" not in prompt
    assert "get_field" not in prompt
    assert "source_tool=semantic_search" not in prompt
    # 工具结果卸载规则段落已收敛进 ENVIRONMENT_BLOCK 目录树（目录名保留，段落标题不出现）
    assert "## 工具结果卸载处理" not in prompt
    # 提示词精简后这些独立段落已删除，环境说明统一在 ## 环境 目录树中
    assert "## 执行连续性" not in prompt
    assert "## 工作区与交付" not in prompt


def test_removed_workflow_blocks_are_absent_and_environment_keeps_tree():
    """执行连续性/工作区交付/工具结果卸载三个段落已删除，环境目录树保留卸载目录声明。"""
    prompt = build_unified_system_prompt(AgentRuntimeOptions())

    assert "## 执行连续性" not in prompt
    assert "## 工作区与交付" not in prompt
    assert "## 工具结果卸载处理" not in prompt
    assert "上下文足够时直接执行" not in prompt
    assert "工具调用前后各用一句话" not in prompt
    assert "修改已有文件前先读取最新内容" not in prompt

    assert "## 环境" in prompt
    assert "├── workspace/" in prompt
    assert "└── semantic/" in prompt
    assert "query_result/  查询结果卸载文件" in prompt
    assert ".tool-offload/ 其它工具大结果卸载文件" in prompt
    assert "└── uploads/     上传区（只读，不可写）" in prompt


def test_system_prompt_keeps_detailed_semantic_rules_in_skill():
    prompt = build_unified_system_prompt(
        AgentRuntimeOptions(),
        available_skills_xml=format_skills_prompt(
            [
                {
                    "name": DATA_ANALYZING_SKILL,
                    "title": "数据分析技能",
                    "description": "基于语义模型完成数据查询",
                }
            ]
        ),
    )

    assert "## DSL 参数" not in prompt
    assert "## DSL 示例" not in prompt
    assert "## NL2SQL 回退规则" not in prompt
    assert "# 多步数据分析示例" not in prompt
    assert '"dimensions":[{"name":"时间维度"' not in prompt
    # 方言不再进系统提示词：随 list_execution_targets 返回（prompts/chat_data.py 开关）
    assert "当前数据库方言" not in prompt
    assert "## 方言上下文" not in prompt
    assert "/mnt/skills/data-analyzing/references/dialect.md" not in prompt
    assert prompt.count("## 输出与最终回答") == 1
    assert "## 输出规范" not in prompt
    assert "## 最终回答" not in prompt


def test_system_prompt_omits_dialect_for_tool_return_channel():
    prompt = build_unified_system_prompt(AgentRuntimeOptions())

    assert "当前数据库方言" not in prompt
    assert "## 方言上下文" not in prompt
    assert "/mnt/skills/data-analyzing/references/dialect.md" not in prompt
