"""``tools/registry.py`` 护栏：装配面必须与声明面一致。

新增/下线工具只改 ``registry.py``；本文件防止漂移——
漏装配、``provides`` 工具名写错、排除集引用了不存在的工具名，都在这里变红。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")

from apps.chat_data_app.agent.data_agent import (  # noqa: E402
    DatasourceContext,
    IdentityContext,
    assemble_tools,
)
from apps.chat_data_app.agent.runtime_options import AgentRuntimeOptions  # noqa: E402
from apps.chat_data_app.agent.tools.registry import (  # noqa: E402
    DATA_EXPLORE_EXTRA_EXCLUDED_TOOL_NAMES,
    GENERAL_PURPOSE_EXTRA_EXCLUDED_TOOL_NAMES,
    PLAN_EXTRA_EXCLUDED_TOOL_NAMES,
    SUBAGENT_BASE_EXCLUDED_TOOL_NAMES,
    TOOL_SPECS,
    provided_tool_names,
)
from apps.chat_data_app.services.agent_run.conversation_workspace import (  # noqa: E402
    ConversationWorkspaceRuntime,
)

# 与 TOOL_SPECS 声明一一对应（bash_run 受运行选项控制，单独断言）
EXPECTED_WITHOUT_BASH = [
    "file_read",
    "file_edit",
    "file_write",
    "file_move",
    "file_copy",
    "file_delete",
    "file_glob",
    "file_grep",
    "file_diff",
    "dir_create",
    "dir_list",
    "dir_delete",
    "parse_document",
    "read_image",
    "present_artifacts",
    "get_current_time",
    "data_analyzing_tool",
    "calculator_tool",
    "ask_user_question",
    "officecli_run",
]


def _runtime(tmp_path, *, bash_enabled=False):
    return ConversationWorkspaceRuntime(
        workspace_root=str(tmp_path),
        user_id="u1",
        backend="local",
    ), AgentRuntimeOptions(workspace_backend="local", bash_enabled=bash_enabled)


def _tool_names(tmp_path, *, bash_enabled=False):
    runtime, options = _runtime(tmp_path, bash_enabled=bash_enabled)
    return [
        tool.name
        for tool in assemble_tools(
            options,
            runtime,
            identity_context=IdentityContext(user_id="u1", operator_name="测试用户"),
            datasource_context=DatasourceContext(datasource_id="ds_a"),
        )
    ]


def test_assembled_tools_match_registry_order(tmp_path):
    assert _tool_names(tmp_path) == EXPECTED_WITHOUT_BASH


def test_bash_run_gated_by_runtime_options(tmp_path):
    names = _tool_names(tmp_path, bash_enabled=True)
    assert names[-1] == "bash_run"
    assert names[:-1] == EXPECTED_WITHOUT_BASH


def test_exclusion_sets_reference_registered_tools_only():
    """排除集出现注册表之外的工具名 = 拼写错误或忘了登记 provides。"""
    registered = provided_tool_names()
    for label, excluded in (
        ("SUBAGENT_BASE", SUBAGENT_BASE_EXCLUDED_TOOL_NAMES),
        ("DATA_EXPLORE", DATA_EXPLORE_EXTRA_EXCLUDED_TOOL_NAMES),
        ("PLAN", PLAN_EXTRA_EXCLUDED_TOOL_NAMES),
        ("GENERAL_PURPOSE", GENERAL_PURPOSE_EXTRA_EXCLUDED_TOOL_NAMES),
    ):
        unknown = excluded - registered
        assert not unknown, f"{label} 排除集引用了未注册工具: {sorted(unknown)}"


def test_built_tools_are_declared_in_provides(tmp_path):
    """实际装配出的工具必须全部登记在 provides：漏登记时排除集匹配会静默失效。"""
    missing = set(_tool_names(tmp_path)) - provided_tool_names()
    assert not missing, f"工具已装配但未登记 provides: {sorted(missing)}"


def test_registry_names_are_unique_and_non_empty():
    names = [name for spec in TOOL_SPECS for name in spec.provides]
    assert all(spec.provides for spec in TOOL_SPECS)
    assert len(names) == len(set(names)), "同一工具名被多个条目重复声明"
