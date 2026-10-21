"""工具注册单一事实源（Single Source of Truth）。

工具清单、注册顺序与子智能体可见性此前散落在 ``data_agent.py`` 的装配函数与
三个硬编码排除集：新增工具要同步改装配列表与排除集，漏一处就是静默失效
（漏装配 = 模型看不到工具；漏排除 = 子智能体拿到不该有的写权限）。现集中到本模块：

- ``TOOL_SPECS``：有哪些工具、按什么顺序注册（顺序即模型看到的工具顺序）、
  各自提供哪些工具名（``provides``，与 ``@tool`` 注册名一致，供权限排除匹配）；
- ``ToolBuildContext``：一次装配的上下文（运行选项、工作区运行时、身份与数据源）；
- 子智能体排除集：作为策略声明与注册表同处一地，配套 ``provided_tool_names()`` 与
  ``tests/unit/test_tools_registry.py`` 护栏——排除集出现未注册的工具名直接变红。

与 ``utils/builtin_skills.py`` 的分工：那里登记技能名，这里登记工具名；
技能文档单向引用工具名，本模块不反向依赖技能。

2026-09-18：``semantic_search`` 工具与 ``semantic_model_search`` LLM 检索器整体下线
（不再通过大模型选模型，语义模型定位走物化 YAML + ``file_grep``/``file_read`` 自导航）。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from apps.chat_data_app.services.ontology.object_type_locator import object_type_locator_enabled

from .ask_user_question import ask_user_question_tool
from .bash_run import create_bash_run_tool
from .calculator import calculator_tool
from .current_time import get_current_time_tool
from .data_analyzing import create_data_analyzing_tool
from .filesystem import create_filesystem_tools
from .officecli_run import create_officecli_run_tool
from .ontology_tool import create_ontology_tool
from .parse_document import create_parse_document_tool
from .present_artifacts import create_present_artifacts_tool
from .read_image import create_read_image_tool

if TYPE_CHECKING:
    from ..data_agent import DatasourceContext, IdentityContext, ModelInvocationConfig
    from ..runtime_options import AgentRuntimeOptions
    from ..workspace.runtime import ConversationWorkspaceRuntime


@dataclass(frozen=True)
class ToolBuildContext:
    """一次工具装配的上下文，由 ``data_agent.assemble_tools`` 解析后传入。

    身份/数据源/模型调用三个上下文类型定义在 ``data_agent``（调用方也从此导入），
    这里仅作类型标注引用，避免模块级循环导入。
    """

    runtime_options: AgentRuntimeOptions
    workspace_runtime: ConversationWorkspaceRuntime
    identity: IdentityContext
    datasource: DatasourceContext
    model_invocation: ModelInvocationConfig


@dataclass(frozen=True)
class ToolSpec:
    """单个可装配条目：提供哪些工具名、如何构建、一句话职责（供审计与迁移）。

    ``requires_user_interaction``：工具需要用户实时应答（当前仅 ask_user_question）。
    无人值守运行（定时轮）装配时跳过此类条目——用户不在场，提问必然悬挂在
    waiting_user 等待应答，从装配层剔除即从根上关闭提问入口。

    ``approval_required``：需要人工审批才能执行的工具名（⊆ provides，按工具粒度
    声明——同一条目可提供多个工具，如文件工具组只有删除/移动需要审批）。它是
    **默认策略**：审批模式下装配 ``ToolApprovalMiddleware`` 在执行前中断询问；
    自动模式直接执行；无人值守不可能弹窗，因此由装配/运行侧强制自动模式。
    """

    provides: tuple[str, ...]
    build: Callable[[ToolBuildContext], Sequence[Any]]
    note: str = ""
    requires_user_interaction: bool = False
    approval_required: tuple[str, ...] = ()


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        provides=(
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
        ),
        build=lambda ctx: create_filesystem_tools(ctx.workspace_runtime),
        note="会话工作区文件读写、检索与目录操作",
        # 破坏性/移动类操作默认按需审批；读写与检索不打断
        approval_required=("file_delete", "dir_delete", "file_move"),
    ),
    ToolSpec(
        provides=("parse_document",),
        build=lambda ctx: create_parse_document_tool(ctx.workspace_runtime),
        note="解析 Office/PDF 等文档到工作区",
    ),
    ToolSpec(
        provides=("read_image",),
        build=lambda ctx: create_read_image_tool(ctx.workspace_runtime),
        note="读取工作区图片供多模态模型查看",
    ),
    ToolSpec(
        provides=("present_artifacts",),
        build=lambda ctx: [create_present_artifacts_tool(ctx.workspace_runtime)],
        note="向用户呈现最终产物（仅主智能体）",
    ),
    ToolSpec(
        provides=("get_current_time",),
        build=lambda _ctx: [get_current_time_tool],
        note="当前时间",
    ),
    ToolSpec(
        provides=("data_analyzing_tool",),
        build=lambda ctx: [
            create_data_analyzing_tool(
                ctx.datasource.datasource_id,
                user_id=ctx.identity.user_id or "system",
                operator_name=ctx.identity.operator_name,
                workspace_runtime=ctx.workspace_runtime,
            )
        ],
        note="数据分析合并工具：DSL/dashboard_spec 校验、语义查询执行、语义模型 YAML 自检（按 action 分派）",
    ),
    ToolSpec(
        provides=("ontology_tool",),
        # 本体灰度开关在注册层收口（可见性≠权限，对齐业内 tool-gating 惯例）：
        # 未启用时工具不装配，模型看不到导航工具，自然回落语义清单检索；
        # 工具实现内仍保留开关 raise 作为纵深防御（防运行中翻转/直调）。
        build=lambda ctx: (
            [create_ontology_tool(ctx.datasource.datasource_id, user_id=ctx.identity.user_id or "system")]
            if object_type_locator_enabled()
            else []
        ),
        note="本体对象类型导航：列对象类型/按对象类型定位语义模型（纯导航不执行查询；受 CHAT_DATA_ONTOLOGY_LOCATE_ENABLED 门控）",
    ),
    ToolSpec(
        provides=("calculator_tool",),
        build=lambda _ctx: [calculator_tool],
        note="精确数值计算",
    ),
    ToolSpec(
        provides=("ask_user_question",),
        build=lambda _ctx: [ask_user_question_tool],
        note="向用户提问（仅主智能体）",
        # 需要用户实时应答：无人值守轮（定时任务）装配时剔除
        requires_user_interaction=True,
    ),
    ToolSpec(
        provides=("officecli_run",),
        build=lambda ctx: [create_officecli_run_tool(ctx.workspace_runtime)],
        note="OfficeCLI 编辑/查询 Office 文件",
    ),
    ToolSpec(
        provides=("bash_run",),
        build=lambda ctx: ([create_bash_run_tool(ctx.workspace_runtime)] if ctx.runtime_options.bash_enabled else []),
        note="受 CHAT_DATA_DEFAULT_BASH_ENABLED 运行选项控制的可选执行工具；bash_run.BASH_RUN_DISABLED 代码级总闸生效时即使注册也拒绝执行",
    ),
)


def provided_tool_names() -> frozenset[str]:
    """注册表声明的全部工具名（含受开关控制、当前未挂载的工具）。"""
    return frozenset(name for spec in TOOL_SPECS for name in spec.provides)


# 需人工审批的工具名派生集合（审批中间件按名匹配）；空 = 当前无工具需要审批。
APPROVAL_REQUIRED_TOOL_NAMES: frozenset[str] = frozenset(name for spec in TOOL_SPECS for name in spec.approval_required)


# 子智能体不继承的基础工具：ask_user_question 需要人工应答（子智能体不注册人工中断），
# present_artifacts 是用户可见的产物呈现（最终交付由主智能体负责）。
SUBAGENT_BASE_EXCLUDED_TOOL_NAMES = frozenset({"ask_user_question", "present_artifacts"})

# data-explore 在基础排除集之上额外排除的写操作工具：只读事实收集不写文件、
# 不执行命令、不做文档编辑，避免子智能体在探查过程中产生副作用。
DATA_EXPLORE_EXTRA_EXCLUDED_TOOL_NAMES = frozenset(
    {
        "file_edit",
        "file_write",
        "file_move",
        "file_copy",
        "file_delete",
        "dir_create",
        "dir_delete",
        "bash_run",
        "officecli_run",
    }
)

# plan 在基础排除集之上额外排除执行类工具：规划子智能体只盘点资料与产出计划，
# 不运行查询、不做计算、不编辑文档，保证规划与执行分离（执行由主智能体完成）。
# data_analyzing_tool 为合并工具（含执行查询的 execute_query action），整工具排除；
# ontology_tool 为纯导航工具，随拆分前「plan 无本体工具」的现状一并排除。
PLAN_EXTRA_EXCLUDED_TOOL_NAMES = frozenset(
    {
        "file_edit",
        "file_move",
        "file_copy",
        "file_delete",
        "dir_create",
        "dir_delete",
        "bash_run",
        "officecli_run",
        "data_analyzing_tool",
        "ontology_tool",
        "calculator_tool",
        "read_image",
        "parse_document",
    }
)

# general-purpose 在基础排除集之上额外排除 bash_run：业内通行的通用执行角色
# （对齐 deepagents/Claude Code 的 general-purpose），保留文件写工具与查询工具
# （产出交付物、执行汇总需要），bash_run 沿用 data-explore/plan 的保守口径不开放
# （任意执行面只在主智能体按 bash_enabled 开关注册）。
GENERAL_PURPOSE_EXTRA_EXCLUDED_TOOL_NAMES = frozenset({"bash_run"})

__all__ = [
    "ToolBuildContext",
    "ToolSpec",
    "TOOL_SPECS",
    "APPROVAL_REQUIRED_TOOL_NAMES",
    "provided_tool_names",
    "SUBAGENT_BASE_EXCLUDED_TOOL_NAMES",
    "DATA_EXPLORE_EXTRA_EXCLUDED_TOOL_NAMES",
    "PLAN_EXTRA_EXCLUDED_TOOL_NAMES",
    "GENERAL_PURPOSE_EXTRA_EXCLUDED_TOOL_NAMES",
]
