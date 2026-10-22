"""ChatData runtime configuration helpers."""

from __future__ import annotations

import math
import os
from typing import Literal, cast

from utils.builtin_skills import DATA_ANALYZING_SKILL
from utils.logger import get_logger

logger = get_logger(__name__)


def read_positive_int_env(name: str, default: int) -> int:
    """读取正整数环境变量，非法时回退默认值。"""
    raw_value = str(os.getenv(name) or "").strip()
    if not raw_value:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        logger.warning("环境变量 %s=%r 不是合法整数，回退默认值 %s", name, raw_value, default)
        return default
    if value < 1:
        logger.warning("环境变量 %s=%s 小于 1，回退默认值 %s", name, value, default)
        return default
    return value


def read_optional_positive_int_env(name: str) -> int | None:
    """读取可选的正整数环境变量；未设置返回 None，非法时告警并按未设置处理。

    与 read_positive_int_env 的差异：允许「不配置」是合法语义（调用方自行决定
    回退口径），因此默认值由调用方持有，这里只负责解析与非法值告警。
    """
    raw_value = str(os.getenv(name) or "").strip()
    if not raw_value:
        return None
    try:
        value = int(raw_value)
    except ValueError:
        logger.warning("环境变量 %s=%r 不是合法整数，忽略该覆盖配置", name, raw_value)
        return None
    if value < 1:
        logger.warning("环境变量 %s=%s 小于 1，忽略该覆盖配置", name, value)
        return None
    return value


def read_bool_env(name: str, default: bool = False) -> bool:
    """读取布尔环境变量，仅接受 true/false（大小写不敏感），非法值直接报错。

    不接受 1/0、yes/on 等变体：配置口径与 .env.example 保持严格一致。
    对 TLS 校验开关这类安全配置，非法值静默回退默认值等于静默改变安全语义
    （如 CHAT_DATA_HTTP_VERIFY_TLS=1 回退成 False），必须 fail-fast 让运维
    在启动时就看到配置错误，而不是运行期踩坑。
    """
    raw_value = str(os.getenv(name) or "").strip().lower()
    if not raw_value:
        return default
    if raw_value == "true":
        return True
    if raw_value == "false":
        return False
    raise ValueError(
        f"环境变量 {name}={raw_value!r} 不是合法布尔值（仅接受 true/false，"
        "大小写不敏感）。安全相关开关不允许静默回退默认值，请修正配置后重启。"
    )


# 查询结果行数上限的同步签名兜底值；运行期热值以 get_runtime_setting("CHATDATA_ROW_LIMIT")
# 为准（表 > env > 注册表默认），async 调用方解析后经 max_rows 参数下传，本常量仅兜底
# 同步深层调用（语义编译 sync 链等未接热值的路径）。
DEFAULT_ROW_LIMIT = read_positive_int_env("CHATDATA_ROW_LIMIT", 100)
# 单条查询超时的硬上限（秒）：请求显式传参超过该值一律封顶，防止实质关闭超时
MAX_QUERY_TIMEOUT_SECONDS = 3600
TOOL_RESULT_OFFLOAD_SIZE = read_positive_int_env("CHAT_DATA_TOOL_RESULT_OFFLOAD_SIZE", 32000)
# 子智能体过程外发上限：子图同样挂载了 ToolResultMiddleware，工具结果超过
# TOOL_RESULT_OFFLOAD_SIZE 后已被替换成几百字符的信封（含 result_id / result_path），
# 因此 SSE 侧拿到的工具结果天然有上界——把预览上限对齐到同一常量后，工具结果
# 要么完整原文、要么信封，不会在 SSE 层被二次砍断。这里的截断仅作防御性兜底
# （例如外置落盘失败时的长内容回退）。
SUBAGENT_RESULT_PREVIEW_MAX_CHARS = TOOL_RESULT_OFFLOAD_SIZE
# 子智能体的中间正文与思考没有卸载机制保护，模型可直接吐长文，故单独配置上限。
# 默认值取大：过程区的设计意图是「用户能看到子智能体做了什么」，截断太狠会让
# 过程失去可读性；同时保留上限作为安全兜底，避免单个 SSE 帧无限膨胀。
SUBAGENT_MESSAGE_PREVIEW_MAX_CHARS = read_positive_int_env("CHAT_DATA_SUBAGENT_MESSAGE_PREVIEW_MAX_CHARS", 32000)
SUBAGENT_THINKING_PREVIEW_MAX_CHARS = read_positive_int_env("CHAT_DATA_SUBAGENT_THINKING_PREVIEW_MAX_CHARS", 64000)
# 主智能体与子智能体共用的 super-step 上限，两处必须同源：
# 子图的一次调用整体只占主图 1 个 super-step，但子图内部有自己的循环，若两边
# 口径不一致（子图更宽松）会出现「主图还有余量、子图已先耗尽」的错位。
# langgraph 未显式设置时默认仅 25 个 super-step（约 12 轮工具循环），复杂分析
# 任务会抛 GraphRecursionError，故显式抬到 40。
AGENT_RECURSION_LIMIT = read_positive_int_env("CHAT_DATA_AGENT_RECURSION_LIMIT", 40)
# 子图调用配置的兜底口径（agent/middleware/subagents.py::build_subagent_invocation_config）；
# 主图装配已改为运行期经后台配置变量表读取（CHAT_DATA_AGENT_RECURSION_LIMIT），见 agent_stream。
FILE_READ_MAX_CHARS = max(4096, read_positive_int_env("CHAT_DATA_FILE_READ_MAX_CHARS", 25000))
# SQL 查询缓存开关/过期时间已迁移到后台配置变量表（CHAT_DATA_QUERY_CACHE_ENABLED / _TTL_SECONDS），
# 运行期读取见 sql_run/cache.py；前端 runtime_config 展示值同样走配置服务。
# 会话工作区文件预览源文件上限（MB）：文本预览与 Office 预览各一档，消费者为
# conversation_workspace（字节值在彼处由 MB 派生）。注意与 OFFICE_PREVIEW_MAX_CHARS
# （Office 转出 HTML 的字符上限，12MB，独立配置）是两回事。
TEXT_PREVIEW_MAX_MB = read_positive_int_env("CHAT_DATA_TEXT_PREVIEW_MAX_MB", 5)
OFFICE_PREVIEW_SOURCE_MAX_MB = read_positive_int_env("CHAT_DATA_OFFICE_PREVIEW_SOURCE_MAX_MB", 50)
# 会话工作区目录打包下载的压缩包大小上限（MB）：限的是 zip 产物字节而非源文件总大小
# （压缩率不可控，限源大小无法预测产物；用户拍板 2026-09-19 限压缩后大小）。默认 50。
# 消费者为 conversation_workspace 的目录打包下载，打包过程中边写边计量、超限即中止。
WORKSPACE_ARCHIVE_MAX_MB = read_positive_int_env("CHAT_DATA_WORKSPACE_ARCHIVE_MAX_MB", 50)
DEFAULT_CONTEXT_WINDOW = 128000

# ---- 单一 ChatData Agent 运行配置 ----
# ``workspace_backend``（隔离模式：本地/沙箱）与 ``bash_enabled``（运行模式：受控/灵活）是两个正交维度：
# - workspace_backend=sandbox（OpenSandbox）的职责是「标准化运行环境」：把代码执行场所钉死为
#   统一的 Python 版本、预装依赖、可复现环境，且不污染宿主生产服务。它的容器隔离（FS/网络/进程
#   边界）只是顺带能力，本职不是安全隔离设备。
# - bash_enabled 才是真正的安全控制：决定 Bash 工具是否注册；注册后对命令做黑名单与路径词法校验。
#   但这些校验是「策略轨道」而非「执行之墙」——agent 可写脚本落盘再执行，内容不被审计，
#   因此 bash_enabled=true（灵活模式）推荐配合 sandbox 后端由容器隔离兜底，但本地亦可
#   （local+true 即子进程以宿主权限执行代码，仅在可信 / 纯本地开发场景使用）。两个维度正交，组合交给部署者。
# - bash_enabled=false（受控模式）时文件工具仍严格路径管控，用 local 即可，无需 sandbox 开销。
# 完整选型矩阵见 chat_data_app/README.md「运行环境后端与 Bash 安全模型」。
_raw_workspace_backend = str(os.getenv("CHAT_DATA_DEFAULT_WORKSPACE_BACKEND") or "local").strip().lower() or "local"
if _raw_workspace_backend not in ("local", "sandbox"):
    logger.warning(
        "CHAT_DATA_DEFAULT_WORKSPACE_BACKEND=%r 非法，回退 local",
        _raw_workspace_backend,
    )
    _raw_workspace_backend = "local"
# 上面已做合法性校验回退，此处收紧为 Literal 供 AgentRuntimeOptions 使用
CHAT_DATA_DEFAULT_WORKSPACE_BACKEND = cast('Literal["local", "sandbox"]', _raw_workspace_backend)
# 注意：bash_run 另有代码级总闸（agent/tools/bash_run.py 的 BASH_RUN_DISABLED=True）：
# env 或请求覆盖误开时，工具执行只会返回「功能已禁用」，命令不会真正触达运行时。
CHAT_DATA_DEFAULT_BASH_ENABLED = read_bool_env("CHAT_DATA_DEFAULT_BASH_ENABLED", False)
# 是否注册 data-explore 子智能体（只读事实收集）；任一子智能体启用即注册 task 工具。
CHAT_DATA_DEFAULT_EXPLORE_SUBAGENT_ENABLED = read_bool_env("CHAT_DATA_DEFAULT_EXPLORE_SUBAGENT_ENABLED", False)
# 是否注册 plan 子智能体（统一规划：产出计划文件，只规划不执行）。
CHAT_DATA_DEFAULT_PLAN_SUBAGENT_ENABLED = read_bool_env("CHAT_DATA_DEFAULT_PLAN_SUBAGENT_ENABLED", False)
# 是否注册 general-purpose 子智能体（业内通行的通用执行角色：产出交付物/批量加工/查询汇总）。
CHAT_DATA_DEFAULT_GENERAL_PURPOSE_SUBAGENT_ENABLED = read_bool_env(
    "CHAT_DATA_DEFAULT_GENERAL_PURPOSE_SUBAGENT_ENABLED", False
)
# 语义模型发现引导语（工具缺 semantic_config_id 的错误文案共用）：
# 检索用法的单一维护点是「数据分析」技能（SKILL.md → references/analysis/query.md），错误提示统一
# 指向该技能，不随工具开关切换文案，也不在此重复检索步骤。
SEMANTIC_MODEL_DISCOVERY_HINT = f"按 {DATA_ANALYZING_SKILL} 技能检索并确认语义模型"

if CHAT_DATA_DEFAULT_BASH_ENABLED and CHAT_DATA_DEFAULT_WORKSPACE_BACKEND == "local":
    logger.warning(
        "当前运行组合为 bash_enabled=true + workspace_backend=local：Agent 可以宿主进程权限执行任意代码，"
        "命令黑名单与路径校验可被「写脚本再执行」绕过。仅可用于可信 / 纯本地开发场景，"
        "多租户或对外暴露环境请改用 workspace_backend=sandbox 由容器隔离兜底。"
    )
CONTEXT_COMPRESSION_ENABLED = read_bool_env("CHAT_DATA_CONTEXT_COMPRESSION_ENABLED", True)
CONTEXT_COMPRESSION_DEFAULT_TRIGGER_RATIO = 0.70
CONTEXT_COMPRESSION_MAX_TRIGGER_RATIO = 0.95


def read_optional_positive_ratio_env(name: str, *, maximum: float) -> float | None:
    """读取可选的正比例 env（0 < value <= maximum）：无效配置直接拒启，不静默回退。"""
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} 必须是 0 到 {maximum} 之间的数字") from exc
    if not math.isfinite(value) or value <= 0 or value > maximum:
        raise RuntimeError(f"{name} 必须大于 0 且不超过 {maximum}，当前值: {value}")
    return value


# 子智能体预算告警线的可选覆盖（上下文窗口比例）：不设置时与主图压缩触发阈值
# 同源（同一 ContextBudget.compression_trigger_tokens = 窗口 × TRIGGER_RATIO）；
# 设置后仅覆盖子图告警线 = 窗口 × 该比例（不超过硬限），主图与子图压缩均不受
# 影响。与压缩线同用比例口径，换模型/调窗口后配置不失真。主要用于压测/调试——
# 设 0.05 之类的小值让告警线先于压缩线触发，肉眼验证「预算告警 → 提前收尾」
# 链路，测完删除该 env 即恢复同源口径。
SUBAGENT_BUDGET_ALERT_TRIGGER_RATIO = read_optional_positive_ratio_env(
    "CHAT_DATA_SUBAGENT_BUDGET_ALERT_TRIGGER_RATIO",
    maximum=CONTEXT_COMPRESSION_MAX_TRIGGER_RATIO,
)


def read_bounded_float_env(name: str, default: float, *, minimum: float, maximum: float) -> float:
    """读取并限制浮点环境变量。"""
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


def read_compression_trigger_ratio_env() -> float:
    """Read the compression ratio strictly so invalid config cannot start the service."""
    name = "CHAT_DATA_CONTEXT_COMPRESSION_TRIGGER_RATIO"
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return CONTEXT_COMPRESSION_DEFAULT_TRIGGER_RATIO
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} 必须是 0 到 {CONTEXT_COMPRESSION_MAX_TRIGGER_RATIO} 之间的数字") from exc
    if not math.isfinite(value) or value <= 0 or value > CONTEXT_COMPRESSION_MAX_TRIGGER_RATIO:
        raise RuntimeError(f"{name} 必须大于 0 且不超过 {CONTEXT_COMPRESSION_MAX_TRIGGER_RATIO}，当前值: {value}")
    return value


CONTEXT_USAGE_SHOW_SYSTEM_PROMPT = read_bool_env("CHAT_DATA_CONTEXT_USAGE_SHOW_SYSTEM_PROMPT", False)
LLM_FIRST_TOKEN_TIMEOUT_SECONDS = read_positive_int_env("CHAT_DATA_LLM_FIRST_TOKEN_TIMEOUT_SECONDS", 60)
# 单会话工具调用频率告警（合规要求）：同一个会话在统计窗口内的工具调用数超过阈值
# 即写一条 alarm 日志，不阻断工具执行。已迁移到后台配置变量表（运行期读取），见
# agent/middleware/tool_call_rate_alert.py。计数在**单个 worker 进程内**按会话聚合
# （GUNICORN_WORKERS 默认 4）：同一会话单次运行（一次 SSE 长连接由同一 worker 处理）
# 计数精确；若同一会话在多进程间并发运行，计数会被拆分而只可能低估（漏报，不会误报）。


def get_context_window(model_id: str | None) -> int:
    """按模型名返回上下文窗口大小。

    无可用模型（注册表为空或全部停用）时回退缺省窗口：/config 是页面初始化
    请求，空清单必须正常返回（前端入口显示「请配置模型」），不得 500。
    """
    from .llm_config import get_chat_data_model_config, get_chat_data_model_configs

    if not get_chat_data_model_configs():
        return DEFAULT_CONTEXT_WINDOW
    return int(get_chat_data_model_config(model_id).get("context_window") or DEFAULT_CONTEXT_WINDOW)


def get_context_compression_config() -> dict:
    """读取智能问数上下文压缩配置。"""
    return {
        "enabled": CONTEXT_COMPRESSION_ENABLED,
        "trigger_ratio": read_compression_trigger_ratio_env(),
    }


def is_context_usage_system_prompt_visible() -> bool:
    """是否在上下文预算明细中展示 system prompt 原文。"""
    return CONTEXT_USAGE_SHOW_SYSTEM_PROMPT


async def get_chat_data_runtime_config() -> dict:
    """读取智能问数前端运行时配置。"""
    from .llm_config import get_chat_data_model_options
    from .setting_service import get_runtime_setting

    model_options = get_chat_data_model_options()
    default_model = model_options.get("default_model")
    return {
        "models": model_options["models"],
        # 权限选择（前端唯一常驻项）：默认值来自配置表（管理页「运行配置」可改）
        "default_row_limit": await get_runtime_setting("CHATDATA_ROW_LIMIT"),
        "query_cache_enabled": await get_runtime_setting("CHAT_DATA_QUERY_CACHE_ENABLED"),
        # 功能开关：终端用户自助创建语义模型的入口（默认关闭，管理页「运行配置」可临时打开）
        "user_semantic_model_enabled": await get_runtime_setting("CHAT_DATA_USER_SEMANTIC_MODEL_ENABLED"),
        "context_window": get_context_window(default_model),
    }


def normalize_row_limit(limit: int | None, max_rows: int | None = None) -> int:
    """读取请求行数限制，未传时使用全局配置；校验上限默认同全局配置，可由调用方传入热值覆盖。"""
    effective_max = DEFAULT_ROW_LIMIT if max_rows is None else max_rows
    if limit is None:
        return effective_max
    if not isinstance(limit, int) or limit < 1:
        raise ValueError(f"查询结果行数限制必须为正整数，当前值：{limit}")
    if limit > effective_max:
        raise ValueError(f"查询结果最多返回 {effective_max} 条，请调整查询条件")
    return limit
