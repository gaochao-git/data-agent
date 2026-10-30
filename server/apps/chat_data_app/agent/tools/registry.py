"""工具注册单一事实源（Single Source of Truth）。

工具清单、注册顺序与单工具策略此前散落在 ``data_agent.py`` 的装配函数与
多个硬编码集合：新增工具要同步改装配列表与策略，漏一处就是静默失效
（漏装配 = 模型看不到工具；漏白名单 = 子智能体工具面漂移）。现集中到本模块：

- ``TOOL_BUILD_GROUPS``：有哪些工具、按什么顺序注册（顺序即模型看到的工具顺序）、
  各自提供哪些工具名（``provides``，与 ``@tool`` 注册名一致，供权限排除匹配）；
- ``ToolBuildContext``：一次装配的上下文（运行选项、工作区运行时、身份与数据源）；
- 子智能体工具白名单：由 ``data_agent.SUBAGENT_TOOL_ALLOWLISTS`` 显式声明，
  配套 ``provided_tool_names()`` 与 ``tests/unit/test_tools_registry.py`` 护栏。

与 ``utils/builtin_skills.py`` 的分工：那里登记技能名，这里登记工具名；
技能文档单向引用工具名，本模块不反向依赖技能。

2026-09-18：``semantic_search`` 工具与 ``semantic_model_search`` LLM 检索器整体下线
（不再通过大模型选模型，语义模型定位走物化 YAML + ``file_grep``/``file_read`` 自导航）。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from apps.chat_data_app.services.ontology.object_projection import ontology_object_read_enabled
from apps.chat_data_app.services.ontology.object_type_locator import object_type_locator_enabled
from apps.chat_data_app.services.ontology.query_mode import hybrid_query_mode_enabled, ontology_query_mode_active
from utils.builtin_skills import DATA_ANALYZING_SKILL, OFFICE_SKILL

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
from .skill import create_skill_tool

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
class ToolBuildGroup:
    """构建组：哪些工具共享一次构建、如何构建、一句话职责。

    只负责“怎么构建”；审批/风险/展示名/技能前置/交互需求等单工具策略
    统一由 ``ToolDefinition`` 声明，避免组级配置混入不同工具语义。
    """

    provides: tuple[str, ...]
    build: Callable[[ToolBuildContext], Sequence[Any]]
    note: str = ""


TOOL_BUILD_GROUPS: tuple[ToolBuildGroup, ...] = (
    ToolBuildGroup(
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
    ),
    ToolBuildGroup(
        provides=("parse_document",),
        build=lambda ctx: create_parse_document_tool(ctx.workspace_runtime),
        note="解析 Office/PDF 等文档到工作区",
    ),
    ToolBuildGroup(
        provides=("read_image",),
        build=lambda ctx: create_read_image_tool(ctx.workspace_runtime),
        note="读取工作区图片供多模态模型查看",
    ),
    ToolBuildGroup(
        provides=("present_artifacts",),
        build=lambda ctx: [create_present_artifacts_tool(ctx.workspace_runtime)],
        note="向用户呈现最终产物（仅主智能体）",
    ),
    ToolBuildGroup(
        provides=("get_current_time",),
        build=lambda _ctx: [get_current_time_tool],
        note="当前时间",
    ),
    ToolBuildGroup(
        provides=("load_skill",),
        build=lambda ctx: [create_skill_tool(ctx.workspace_runtime)],
        note="加载会话技能正文（SKILL.md）到上下文；技能相关工具的前置入口",
    ),
    ToolBuildGroup(
        provides=("data_analyzing_tool",),
        # 语义/本体二选一；hybrid 开关打开时本体域也装配语义工具，由技能路由。
        build=lambda ctx: (
            []
            if ontology_query_mode_active() and not hybrid_query_mode_enabled()
            else [
                create_data_analyzing_tool(
                    ctx.datasource.datasource_id,
                    user_id=ctx.identity.user_id or "system",
                    operator_name=ctx.identity.operator_name,
                    workspace_runtime=ctx.workspace_runtime,
                )
            ]
        ),
        note="数据分析合并工具：DSL/dashboard_spec 校验、语义查询执行、语义模型 YAML 自检（按 action 分派；语义模式专用）",
    ),
    ToolBuildGroup(
        provides=("ontology_tool",),
        # 本体工具仅在「判定为有本体」的业务域装配：语义模式下即使功能开关打开也
        # 不挂载（目录与工具必须二选一）。工具实现内保留开关 raise 作为纵深防御。
        build=lambda ctx: (
            [create_ontology_tool(ctx.datasource.datasource_id, user_id=ctx.identity.user_id or "system")]
            if ontology_query_mode_active() and (object_type_locator_enabled() or ontology_object_read_enabled())
            else []
        ),
        note="本体对象/关系/Action 查询与导航：列对象类型/定位数据源绑定/关系探索/对象查询/Action（本体模式专用）",
    ),
    ToolBuildGroup(
        provides=("calculator_tool",),
        build=lambda _ctx: [calculator_tool],
        note="精确数值计算",
    ),
    ToolBuildGroup(
        provides=("ask_user_question",),
        build=lambda _ctx: [ask_user_question_tool],
        note="向用户提问（仅主智能体）",
    ),
    ToolBuildGroup(
        provides=("officecli_run",),
        build=lambda ctx: [create_officecli_run_tool(ctx.workspace_runtime)],
        note="OfficeCLI 编辑/查询 Office 文件",
    ),
    ToolBuildGroup(
        provides=("bash_run",),
        build=lambda ctx: [create_bash_run_tool(ctx.workspace_runtime)],
        note="受 ToolDefinition.enabled 控制的执行工具；bash_run.BASH_RUN_DISABLED 代码级总闸生效时即使注册也拒绝执行",
    ),
)


MIDDLEWARE_INJECTED_APPROVAL_TOOLS = frozenset({"write_todos", "task", "mcp_tool_search", "mcp_tool_call"})


@dataclass(frozen=True)
class ToolDefinition:
    """单个工具的管理声明：审批/风险/展示名/技能前置/交互需求/启停。"""

    approval: str = "allow"
    risk: str = "medium"
    display_name: str = ""
    required_skills: tuple[str, ...] = ()
    requires_user_interaction: bool = False
    # 唯一启停开关：False 时装配阶段不构建该工具，不再依赖环境变量。
    enabled: bool = True


# 每个工具一条声明：构建可以按 ToolBuildGroup 分组，策略必须逐工具独立。
TOOL_DEFINITIONS: dict[str, ToolDefinition] = {
    "file_read": ToolDefinition(approval="allow", risk="low", display_name="读取文件"),
    "file_edit": ToolDefinition(approval="allow", risk="medium", display_name="修改文件"),
    "file_write": ToolDefinition(approval="allow", risk="medium", display_name="写入文件"),
    "file_move": ToolDefinition(approval="allow", risk="medium", display_name="移动文件"),
    "file_copy": ToolDefinition(approval="allow", risk="medium", display_name="复制文件"),
    "file_delete": ToolDefinition(approval="allow", risk="high", display_name="删除文件"),
    "file_glob": ToolDefinition(approval="allow", risk="low", display_name="查找文件"),
    "file_grep": ToolDefinition(approval="allow", risk="low", display_name="搜索文本"),
    "file_diff": ToolDefinition(approval="allow", risk="low", display_name="对比文件"),
    "dir_create": ToolDefinition(approval="allow", risk="medium", display_name="创建目录"),
    "dir_list": ToolDefinition(approval="allow", risk="low", display_name="列出目录"),
    "dir_delete": ToolDefinition(approval="allow", risk="high", display_name="删除目录"),
    "parse_document": ToolDefinition(approval="allow", risk="low", display_name="解析文档"),
    "read_image": ToolDefinition(approval="allow", risk="low", display_name="读取图片"),
    "present_artifacts": ToolDefinition(
        approval="allow",
        risk="low",
        display_name="展示文件",
    ),
    "get_current_time": ToolDefinition(approval="allow", risk="low", display_name="获取当前时间"),
    "load_skill": ToolDefinition(approval="allow", risk="low", display_name="加载技能"),
    "data_analyzing_tool": ToolDefinition(
        approval="allow",
        risk="medium",
        display_name="数据分析",
        required_skills=(DATA_ANALYZING_SKILL,),
    ),
    # 本体查询流程、对象契约、图/一跳边界都在 data-analyzing 技能正文里；
    # 未加载就调用容易凭记忆拼关系或误用图查询。
    "ontology_tool": ToolDefinition(
        approval="allow",
        risk="medium",
        display_name="本体查询",
        required_skills=(DATA_ANALYZING_SKILL,),
    ),
    "calculator_tool": ToolDefinition(approval="allow", risk="low", display_name="精确计算"),
    "ask_user_question": ToolDefinition(
        approval="allow",
        risk="low",
        display_name="询问用户",
        requires_user_interaction=True,
    ),
    "officecli_run": ToolDefinition(
        approval="allow",
        risk="medium",
        display_name="运行 OfficeCLI",
        required_skills=(OFFICE_SKILL,),
    ),
    "bash_run": ToolDefinition(
        approval="allow",
        risk="high",
        display_name="运行脚本",
        enabled=False,
    ),
    "write_todos": ToolDefinition(approval="allow", risk="low", display_name="更新任务计划"),
    "task": ToolDefinition(approval="allow", risk="low", display_name="派发子智能体"),
    "mcp_tool_search": ToolDefinition(approval="allow", risk="medium", display_name="搜索 MCP 工具"),
    "mcp_tool_call": ToolDefinition(approval="allow", risk="high", display_name="调用 MCP 工具"),
}

# 由逐工具声明生成的兼容视图；消费方仍可读取/测试这些 map。
SYSTEM_TOOL_APPROVALS: dict[str, str] = {}
TOOL_RISK: dict[str, str] = {}
for _spec in TOOL_BUILD_GROUPS:
    for _tool_name in _spec.provides:
        _declaration = TOOL_DEFINITIONS.get(_tool_name)
        if _declaration is None:
            raise RuntimeError(f"工具缺少 ToolDefinition: {_tool_name}")
        SYSTEM_TOOL_APPROVALS[_tool_name] = _declaration.approval
        TOOL_RISK[_tool_name] = _declaration.risk
for _tool_name in MIDDLEWARE_INJECTED_APPROVAL_TOOLS:
    _declaration = TOOL_DEFINITIONS.get(_tool_name)
    if _declaration is None:
        raise RuntimeError(f"中间件工具缺少 ToolDefinition: {_tool_name}")
    SYSTEM_TOOL_APPROVALS[_tool_name] = _declaration.approval
    TOOL_RISK[_tool_name] = _declaration.risk

# 未显式配置的系统工具默认 ask；MCP 动态工具仍由插件 tools.<name>.approval 默认 ask。
SYSTEM_TOOL_APPROVAL_DEFAULT = "ask"


def tool_display_names() -> dict[str, str]:
    """返回所有系统/中间件工具的展示名；前端 /config 消费，未知工具由前端兜底。"""
    return {
        name: declaration.display_name for name, declaration in TOOL_DEFINITIONS.items() if declaration.display_name
    }


def tool_enabled(tool_name: str) -> bool:
    """工具是否启用；未知工具按 False 处理（不构建）。"""
    declaration = TOOL_DEFINITIONS.get(str(tool_name or "").strip())
    return bool(declaration.enabled) if declaration else False


def tool_requires_user_interaction(tool_name: str) -> bool:
    """工具是否需要用户实时应答；未知工具按 False 处理。"""
    declaration = TOOL_DEFINITIONS.get(str(tool_name or "").strip())
    return bool(declaration.requires_user_interaction) if declaration else False


def system_tool_approval(tool_name: str) -> str:
    """返回系统工具审批档位；未配置时 fail-closed 为 ask。"""
    return SYSTEM_TOOL_APPROVALS.get(str(tool_name or "").strip(), SYSTEM_TOOL_APPROVAL_DEFAULT)


def tool_risk(tool_name: str) -> str:
    """返回系统工具风险分级；未配置时按 medium 保守处理。"""
    return TOOL_RISK.get(str(tool_name or "").strip(), "medium")


def provided_tool_names() -> frozenset[str]:
    """注册表声明的全部工具名（含受开关控制、当前未挂载的工具）。"""
    return frozenset(name for spec in TOOL_BUILD_GROUPS for name in spec.provides)


def system_approval_required_tools() -> frozenset[str]:
    """返回当前需要 ask 的系统工具名（含中间件动态工具与未显式配置的新工具）。"""
    names = (*provided_tool_names(), *MIDDLEWARE_INJECTED_APPROVAL_TOOLS)
    return frozenset(name for name in names if system_tool_approval(name) == "ask")


# 工具 → 必需技能（调用前必须已加载）；从逐工具声明派生，不全局强制。
TOOL_REQUIRED_SKILLS: dict[str, tuple[str, ...]] = {
    name: declaration.required_skills for name, declaration in TOOL_DEFINITIONS.items() if declaration.required_skills
}


__all__ = [
    "ToolBuildContext",
    "ToolDefinition",
    "ToolBuildGroup",
    "TOOL_DEFINITIONS",
    "TOOL_BUILD_GROUPS",
    "SYSTEM_TOOL_APPROVALS",
    "TOOL_RISK",
    "tool_display_names",
    "tool_enabled",
    "tool_requires_user_interaction",
    "tool_risk",
    "SYSTEM_TOOL_APPROVAL_DEFAULT",
    "system_tool_approval",
    "system_approval_required_tools",
    "TOOL_REQUIRED_SKILLS",
    "provided_tool_names",
]
