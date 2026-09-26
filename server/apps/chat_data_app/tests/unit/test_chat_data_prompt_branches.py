"""chat_data 系统提示词数据上下文区的固化测试。

2026-09-24 起静态注入链路整体下线（可用执行对象清单/方言/智能问数说明改为
``list_execution_targets`` 按需实时获取，语义模型选中信息并入当轮 Human 消息），
本文件固化下线后的提示词形态：数据上下文区只承载 semantic_materialize_note，
历史注入内容一律不出现。
"""

from __future__ import annotations

from apps.chat_data_app.agent.prompts import chat_data
from apps.chat_data_app.agent.runtime_options import AgentRuntimeOptions


# ---------------------------------------------------------------------------
# _format_conversation_context
# ---------------------------------------------------------------------------


def test_format_conversation_context_rejects_non_dict():
    assert chat_data._format_conversation_context(None) == "{}"
    assert chat_data._format_conversation_context("not a dict") == "{}"
    assert chat_data._format_conversation_context([]) == "{}"


def test_format_conversation_context_keeps_semantic_note_only():
    """数据上下文区只保留物化说明；历史注入字段（数据源组段/选中模型）被忽略。

    静态注入链路已下线：执行对象清单/方言/智能问数说明走 list_execution_targets，
    语义模型指定由输入框 @ 引用挂在当轮 Human 消息上。
    """
    data_context = {
        "datasource_group_context": "数据源组说明",
        "semantic_materialize_note": "已预物化 3 个语义模型",
        "semantic_configs": [{"id": "cfg_1", "name": "交易域", "selected": True}],
    }

    text = chat_data._format_conversation_context(data_context)

    assert text == "已预物化 3 个语义模型"


def test_format_conversation_context_empty_without_note():
    """没有物化说明时返回空串，不产生空标题或历史注入内容。"""
    data_context = {
        "datasource_group_context": "数据源组说明",
        "semantic_configs": [{"id": "cfg_1", "name": "交易域", "selected": True}],
    }

    text = chat_data._format_conversation_context(data_context)

    assert text == ""
    assert "数据源组说明" not in text
    assert "## 数据源组智能问数说明" not in text
    assert "## 当前语义模型" not in text


def test_build_data_context_text_returns_empty_without_data_context():
    assert chat_data.build_data_context_text() == ""
    assert chat_data.build_data_context_text(data_context=None) == ""


# ---------------------------------------------------------------------------
# build_unified_system_prompt
# ---------------------------------------------------------------------------


def test_system_prompt_invariant_to_bash_flag():
    without_bash = chat_data.build_unified_system_prompt(AgentRuntimeOptions(bash_enabled=False))
    with_bash = chat_data.build_unified_system_prompt(AgentRuntimeOptions(bash_enabled=True))

    # bash_enabled 只控制 bash_run 工具注册；系统提示词不再有 bash 专属段落
    assert without_bash == with_bash

    # 其余段落始终存在
    for block in (
        chat_data.ROLE_BLOCK,
        chat_data.PRIORITY_BLOCK,
        chat_data.ENVIRONMENT_BLOCK,
        chat_data.OUTPUT_AND_FINAL_ANSWER_BLOCK,
        chat_data.ERROR_HANDLING_BLOCK,
    ):
        assert block in without_bash
        assert block in with_bash


def test_memory_block_only_in_main_agent_prompt():
    """会话记忆约定仅主智能体可见：子智能体是一次性执行者，不维护会话记忆。"""
    prompt = chat_data.build_unified_system_prompt(AgentRuntimeOptions())

    assert chat_data.MEMORY_BLOCK in prompt
    # 记忆文件路径必须是路径策略接受的绝对形式：相对形式(workspace/...)会被
    # WorkspacePathPolicy 拒绝，模型首次读写必失败
    assert "/mnt/user-data/memory/memory.md" in chat_data.MEMORY_BLOCK
    assert chat_data.MEMORY_BLOCK not in chat_data.DATA_EXPLORE_SUBAGENT_PROMPT
    assert chat_data.MEMORY_BLOCK not in chat_data.PLAN_SUBAGENT_PROMPT
    # 防回归：MEMORY_BLOCK 独立成块，若并入 ENVIRONMENT_BLOCK 会被子智能体 prompt 连带引用
    assert chat_data.MEMORY_BLOCK not in chat_data.ENVIRONMENT_BLOCK


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
    assert "├── workspace/   中间文件写入该工作区" in with_root
    # semantic/ 与 /mnt/skills 在环境块集中声明一次
    assert "└── semantic/  本轮启用语义模型的只读 YAML 快照" in with_root
    assert "{模型名}__{id后缀}.yaml" in with_root
    assert "query_result/  查询结果卸载文件" in with_root
    assert ".tool-offload/ 其它工具大结果卸载文件" in with_root
    assert "└── skills/" in with_root
    assert "工作目录：\n/mnt/user-data/workspace" not in with_root
    assert "未初始化" not in with_root
    # workspace_root 不参与拼装，两个入参产出完全相同
    assert chat_data.build_unified_system_prompt(AgentRuntimeOptions()) == with_root


def test_system_prompt_omits_static_injection_content():
    """静态注入链路下线固化：数据源组段、方言块、语义模型选中段都不进系统提示词。"""
    prompt = chat_data.build_unified_system_prompt(
        AgentRuntimeOptions(),
        data_context={
            "datasource_group_context": "数据源组说明",
            "semantic_configs": [{"id": "cfg_1", "name": "交易域", "selected": True}],
        },
    )

    assert "## 数据源组智能问数说明" not in prompt
    assert "数据源组说明" not in prompt
    assert "## 方言上下文" not in prompt
    assert "当前数据库方言" not in prompt
    assert "## 当前语义模型" not in prompt
    assert "cfg_1" not in prompt


def test_system_prompt_inlines_semantic_materialize_note():
    prompt = chat_data.build_unified_system_prompt(
        AgentRuntimeOptions(),
        data_context={"semantic_materialize_note": "已预物化 3 个语义模型"},
    )

    assert "# 数据上下文" in prompt
    assert "已预物化 3 个语义模型" in prompt
