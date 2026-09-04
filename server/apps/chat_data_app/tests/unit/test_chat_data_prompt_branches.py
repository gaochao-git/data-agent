"""chat_data 系统提示词分支补测。

覆盖目标：`agent/prompts/chat_data.py` 中未被触达的分支（原 10 条缺失分支弧 + 7 行）。

这些函数此前只在"构造 Agent"时被间接调用，单一入参形态覆盖了不到 2/3 的分支。
本文件直接针对格式化函数做单元级验证，无需 mock、无需构造 Agent。

不可达分支说明：`build_unified_system_prompt` 第 261 行 `if dialect_context_text`
的假分支无法触达 —— `build_dialect_prompt_context` 的返回值是 f-string，MySQL/Hive
两种取值都非空。下方 `test_dialect_block_is_always_present` 固化该结论。
"""

from __future__ import annotations

from apps.chat_data_app.agent.prompts import chat_data
from apps.chat_data_app.agent.runtime_options import AgentRuntimeOptions


# ---------------------------------------------------------------------------
# 数据上下文格式化（14->16 / 22->24）
# ---------------------------------------------------------------------------


def test_format_datasource_group_context_for_llm():
    assert chat_data.format_datasource_group_context_for_llm("说明文本") == (
        "\n## 数据源组智能问数说明\n说明文本\n"
    )
    # 入参先 strip 再拼装
    assert chat_data.format_datasource_group_context_for_llm("  说明文本  ") == (
        "\n## 数据源组智能问数说明\n说明文本\n"
    )
    # 空白 / 空值统一返回空串，不产生空标题
    assert chat_data.format_datasource_group_context_for_llm("   ") == ""
    assert chat_data.format_datasource_group_context_for_llm(None) == ""


# ---------------------------------------------------------------------------
# build_semantic_layer_context（38->40 / 40->42）
# ---------------------------------------------------------------------------


def test_build_semantic_layer_context_without_selection():
    unselected = chat_data.build_semantic_layer_context(None)

    assert unselected.startswith("## 当前语义模型")
    assert "本轮未手动选择语义模型" in unselected
    # 空 dict 与 None 走同一分支
    assert chat_data.build_semantic_layer_context({}) == unselected


def test_build_semantic_layer_context_delegates_to_semantic_query_skill():
    """检索用法单一维护在 semantic-query 技能：提示词只声明目录存在 + 指向技能，不重复步骤。"""
    unselected = chat_data.build_semantic_layer_context(None)

    assert "/mnt/user-data/workspace/semantic/" in unselected
    assert "semantic-query" in unselected
    assert "SKILL.md" in unselected
    assert "semantic_search" not in unselected
    # 不再重复定义检索步骤
    assert "dir_list" not in unselected
    assert "file_grep" not in unselected
    # 已选模型分支同样只指向技能
    selected = chat_data.build_semantic_layer_context({"id": "cfg_1", "name": "交易域"})
    assert "semantic-query" in selected
    assert "file_grep" not in selected
    assert "semantic_search" not in selected


def test_build_semantic_layer_context_with_name_and_id():
    text = chat_data.build_semantic_layer_context({"id": "cfg_1", "name": "交易域"})

    assert "当前已选择语义模型：交易域（semantic_config_id: cfg_1）" in text
    assert "这是本轮优先参考，不代表锁定。" in text


def test_build_semantic_layer_context_name_fallbacks():
    """name 缺失时依次回落 config_name、id。"""
    assert "：交易域配置" in chat_data.build_semantic_layer_context(
        {"id": "cfg_1", "config_name": "交易域配置"}
    )
    # 连 config_name 都没有时用 id 顶上
    assert "：cfg_1" in chat_data.build_semantic_layer_context({"id": "cfg_1"})
    # 有 name 但没 id 时不输出 semantic_config_id 片段
    text = chat_data.build_semantic_layer_context({"name": "交易域"})
    assert "：交易域" in text
    assert "semantic_config_id" not in text


def test_build_semantic_layer_context_with_id_only_skips_name_part():
    """config_id 为空时连括号片段一并跳过（38->40 假分支 + 40->42 假分支）。"""
    text = chat_data.build_semantic_layer_context({"name": ""})

    assert text.startswith("## 当前语义模型\n当前已选择语义模型。")


# ---------------------------------------------------------------------------
# _format_conversation_context（51->52 非 dict）
# ---------------------------------------------------------------------------


def test_format_conversation_context_rejects_non_dict():
    assert chat_data._format_conversation_context(None) == "{}"
    assert chat_data._format_conversation_context("not a dict") == "{}"
    assert chat_data._format_conversation_context([]) == "{}"


def test_format_conversation_context_picks_selected_config():
    data_context = {
        "datasource_group_context": "数据源组说明",
        "semantic_configs": [
            {"id": "cfg_other", "name": "未选中"},
            "junk-entry",
            {"id": "cfg_1", "name": "交易域", "selected": True},
        ],
    }

    text = chat_data._format_conversation_context(data_context)

    assert "数据源组说明" in text
    assert "cfg_1" in text
    assert "未选中" not in text


def test_build_data_context_text_prefers_explicit_data_context():
    """传了 data_context 时忽略独立入参（82 真分支）。"""
    text = chat_data.build_data_context_text(
        data_context={"datasource_group_context": "来自会话上下文"},
        datasource_group_context="被忽略",
    )

    assert "来自会话上下文" in text
    assert "被忽略" not in text


def test_build_data_context_text_assembles_parts_when_no_data_context():
    """未传 data_context 时按两段拼装，空段自动跳过（82 假分支 + 89 过滤）。"""
    text = chat_data.build_data_context_text(
        datasource_group_context="数据源组说明",
    )

    assert text.startswith("## 数据源组智能问数说明")
    assert "本轮未手动选择语义模型" in text


# ---------------------------------------------------------------------------
# _extract_source_type（100->101 / 105->106 / 108->109）
# ---------------------------------------------------------------------------


def test_extract_source_type_prefers_datasource():
    """数据源上的 source_type 优先，且大小写/空格归一化（100->101 真分支）。"""
    assert chat_data._extract_source_type({"datasource": {"source_type": " MySQL "}}) == "mysql"
    # 数据源上的空值不短路，继续回落到语义模型
    assert chat_data._extract_source_type(
        {"datasource": {"source_type": "  "}, "semantic_configs": [{"source_type": "Hive"}]}
    ) == "hive"


def test_extract_source_type_falls_back_to_semantic_configs():
    """数据源没给方言时从语义模型里取（108->109 真分支）。"""
    assert chat_data._extract_source_type({"semantic_configs": [{"source_type": "hive"}]}) == "hive"
    # 多个语义模型取第一个非空的
    assert chat_data._extract_source_type(
        {"semantic_configs": [{"source_type": ""}, {"source_type": "mysql"}]}
    ) == "mysql"


def test_extract_source_type_skips_non_dict_configs():
    """语义模型列表里的脏数据跳过而不是抛错（105->106 真分支）。"""
    assert chat_data._extract_source_type(
        {"semantic_configs": ["junk", None, 42, {"source_type": "hive"}]}
    ) == "hive"


def test_extract_source_type_returns_empty_when_nothing_available():
    assert chat_data._extract_source_type({}) == ""
    assert chat_data._extract_source_type({"datasource": None}) == ""
    assert chat_data._extract_source_type({"semantic_configs": "not-a-list"}) == ""
    assert chat_data._extract_source_type("not-a-dict") == ""
    assert chat_data._extract_source_type({"semantic_configs": [{"source_type": ""}]}) == ""


# ---------------------------------------------------------------------------
# build_dialect_prompt_context / 方言段落恒存在
# ---------------------------------------------------------------------------


def test_build_dialect_prompt_context_maps_source_type_to_dialect():
    assert "MySQL" in chat_data.build_dialect_prompt_context({"datasource": {"source_type": "mysql"}})
    # 非 mysql 一律按 Hive
    assert "Hive" in chat_data.build_dialect_prompt_context({"datasource": {"source_type": "hive"}})
    assert "Hive" in chat_data.build_dialect_prompt_context({})


def test_dialect_block_is_always_present():
    """`build_dialect_prompt_context` 恒返回非空串。

    因此 `build_unified_system_prompt` 第 261 行 `if dialect_context_text` 的假分支
    无法触达（方言段落必然被追加），这里固化该结论，避免误判为漏测。
    """
    for data_context in ({}, {"datasource": None}, {"datasource": {"source_type": "mysql"}}, None):
        assert chat_data.build_dialect_prompt_context(data_context or {})

    prompt = chat_data.build_unified_system_prompt(AgentRuntimeOptions())
    assert chat_data.DIALECT_BLOCK.split("{")[0] in prompt


# ---------------------------------------------------------------------------
# build_unified_system_prompt（267->268 bash_enabled）
# ---------------------------------------------------------------------------


def test_system_prompt_appends_bash_block_only_when_enabled():
    without_bash = chat_data.build_unified_system_prompt(AgentRuntimeOptions(bash_enabled=False))
    with_bash = chat_data.build_unified_system_prompt(AgentRuntimeOptions(bash_enabled=True))

    assert chat_data.BASH_WORKFLOW_BLOCK not in without_bash
    assert chat_data.BASH_WORKFLOW_BLOCK in with_bash
    # 其余段落与 bash 开关无关
    for block in (
        chat_data.ROLE_BLOCK,
        chat_data.PRIORITY_BLOCK,
        chat_data.WORKSPACE_WORKFLOW_BLOCK,
        chat_data.OUTPUT_AND_FINAL_ANSWER_BLOCK,
        chat_data.ERROR_HANDLING_BLOCK,
    ):
        assert block in without_bash
        assert block in with_bash


def test_system_prompt_uses_provided_skills_xml_or_empty_fallback():
    custom_xml = '<available_skills>\n  <skill name="demo" />\n</available_skills>'

    assert custom_xml in chat_data.build_unified_system_prompt(
        AgentRuntimeOptions(), available_skills_xml=custom_xml
    )
    assert chat_data.EMPTY_AVAILABLE_SKILLS_XML in chat_data.build_unified_system_prompt(
        AgentRuntimeOptions()
    )


def test_system_prompt_environment_block_ignores_workspace_root():
    """环境块是纯静态目录树（含 semantic 快照与 /mnt/skills），workspace_root 入参不参与拼装。"""
    with_root = chat_data.build_unified_system_prompt(
        AgentRuntimeOptions(), workspace_root="/mnt/user-data/workspace"
    )

    assert "## 环境" in with_root
    assert "├── workspace/   主工作区" in with_root
    # semantic/ 与 /mnt/skills 在环境块集中声明一次
    assert "└── semantic/  本轮启用语义模型的只读 YAML 快照" in with_root
    assert "{模型名}__{id后缀}.yaml" in with_root
    assert "查询结果由系统存于 outputs/query_result/" in with_root
    assert "/mnt/skills/<技能名>/" in with_root
    assert "工作目录：\n/mnt/user-data/workspace" not in with_root
    assert "未初始化" not in with_root
    # workspace_root 不参与拼装，两个入参产出完全相同
    assert chat_data.build_unified_system_prompt(AgentRuntimeOptions()) == with_root


def test_system_prompt_inlines_data_context_parts():
    prompt = chat_data.build_unified_system_prompt(
        AgentRuntimeOptions(),
        datasource_group_context="数据源组说明",
    )

    assert "# 数据上下文" in prompt
    assert "## 数据源组智能问数说明\n数据源组说明" in prompt
    # 系统提示词不再枚举模型目录，只引导 dir_list 浏览工作区目录
    assert "当前可见语义模型目录" not in prompt
    assert "dir_list" in prompt
