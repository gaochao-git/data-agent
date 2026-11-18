"""子图中间件槽位装配。

与主图共用「规范顺序表 + 槽位」机制，但子图有自己的中间件集合与相对顺序：

- 不挂 Todo / MCP 发现 / 图片注入 / steering / subagent 等主图专属中间件；
- 控制与审批只对可中断的后台任务启用；
- 租约围栏必须最外层：若被 ``ToolErrorMiddleware`` 包住，租约丢失会被转成
  普通工具失败，模型会继续调用工具而不是终止子图。
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import ToolErrorMiddleware

from ..agent_runtime import on_tool_error
from ..tools.registry import TOOL_REQUIRED_SKILLS
from .assembly import assemble_middleware
from .skill_prerequisite import SkillPrerequisiteMiddleware
from .tool_call_normalizer import create_tool_call_name_normalizer_middleware
from .tool_result import create_tool_result_middleware
from .tool_timing import create_tool_timing_middleware

# 外层 -> 内层。新增中间件必须先在此表登记位置并写明原因；组合顺序由
# tests/unit/test_subagent_middleware_ordering.py 枚举校验。
SUBAGENT_MIDDLEWARE_CANONICAL_ORDER: tuple[str, ...] = (
    "ToolExecutionFenceMiddleware",  # 租约围栏必须最外层，错误不能被内层转成普通工具失败
    "SubagentControlMiddleware",  # 暂停/插话先于压缩，插话不会被摘要吞掉
    "ToolApprovalMiddleware",  # 审批在围栏内、任何工具副作用之前
    "ContextCompressionMiddleware",  # 压缩是 before_model 节点，先于模型调用改写 state
    "ContextUsageMiddleware",  # 用量口径与压缩共用同一请求预算
    "SkillPrerequisiteMiddleware",  # 工具声明 required_skills 时先拦未加载技能
    "ToolTimingMiddleware",  # 计时覆盖卸载与错误转换在内的完整工具链路
    "ToolCallNameNormalizerMiddleware",  # 内层模型同样可能返回重复工具名/字符串 args
    "ToolResultMiddleware",  # 查询工具返回内部协议 JSON，需在出口统一处理
    "ToolErrorMiddleware",  # 恒为最内层工具兜底
    "SandboxRenewalMiddleware",  # 仅 wrap_model_call，最内层续期覆盖模型思考与随后的工具执行
)

# 注：围栏只挂 wrap_tool_call，不参与 before_model / after_model；控制中间件
# 仍是第一个 before_model 钩子，暂停与插话注入依旧先于上下文压缩。


def assemble_subagent_middleware(
    tools: list[Any],
    *,
    context_compression_middleware: Any,
    context_usage_middleware: Any,
    control_middleware: Any | None = None,
    approval_middleware: Any | None = None,
    sandbox_renewal_middleware: Any | None = None,
    execution_fence_middleware: Any | None = None,
) -> list[Any]:
    """按子图规范顺序表装配 middleware；位置性逻辑只允许出现在这张表里。"""
    slots: dict[str, Any | None] = {
        "ToolExecutionFenceMiddleware": execution_fence_middleware,
        "SubagentControlMiddleware": control_middleware,
        "ToolApprovalMiddleware": approval_middleware,
        "ContextCompressionMiddleware": context_compression_middleware,
        "ContextUsageMiddleware": context_usage_middleware,
        "SkillPrerequisiteMiddleware": SkillPrerequisiteMiddleware(TOOL_REQUIRED_SKILLS),
        "ToolTimingMiddleware": create_tool_timing_middleware(),
        "ToolCallNameNormalizerMiddleware": create_tool_call_name_normalizer_middleware(tools),
        "ToolResultMiddleware": create_tool_result_middleware(),
        "ToolErrorMiddleware": ToolErrorMiddleware(on_tool_error),
        "SandboxRenewalMiddleware": sandbox_renewal_middleware,
    }
    return assemble_middleware(SUBAGENT_MIDDLEWARE_CANONICAL_ORDER, slots)
