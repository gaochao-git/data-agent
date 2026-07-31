"""ChatData runtime configuration helpers."""

from __future__ import annotations

import os

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


def read_bool_env(name: str, default: bool = False) -> bool:
    """读取布尔环境变量，非法或未配置时回退默认值。"""
    raw_value = str(os.getenv(name) or "").strip().lower()
    if not raw_value:
        return default
    return raw_value in {"1", "true", "yes", "y", "on"}


DEFAULT_ROW_LIMIT = read_positive_int_env("CHATDATA_ROW_LIMIT", 100)
TOOL_RESULT_OFFLOAD_SIZE = read_positive_int_env("CHAT_DATA_TOOL_RESULT_OFFLOAD_SIZE", 32000)
FILE_READ_MAX_CHARS = max(4096, read_positive_int_env("CHAT_DATA_FILE_READ_MAX_CHARS", 25000))
QUERY_CACHE_ENABLED = read_bool_env("CHAT_DATA_QUERY_CACHE_ENABLED", False)
QUERY_CACHE_TTL_SECONDS = read_positive_int_env("CHAT_DATA_QUERY_CACHE_TTL_SECONDS", 3600)
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
CHAT_DATA_DEFAULT_WORKSPACE_BACKEND = str(
    os.getenv("CHAT_DATA_DEFAULT_WORKSPACE_BACKEND") or "local"
).strip().lower() or "local"
if CHAT_DATA_DEFAULT_WORKSPACE_BACKEND not in ("local", "sandbox"):
    logger.warning(
        "CHAT_DATA_DEFAULT_WORKSPACE_BACKEND=%r 非法，回退 local",
        CHAT_DATA_DEFAULT_WORKSPACE_BACKEND,
    )
    CHAT_DATA_DEFAULT_WORKSPACE_BACKEND = "local"
CHAT_DATA_DEFAULT_BASH_ENABLED = read_bool_env("CHAT_DATA_DEFAULT_BASH_ENABLED", False)
# 是否允许用户在前端手动选择运行选项以覆盖系统默认配置；关闭则完全依赖系统默认。
CHAT_DATA_CAPABILITY_USER_OVERRIDABLE = read_bool_env("CHAT_DATA_CAPABILITY_USER_OVERRIDABLE", False)
CONTEXT_COMPRESSION_ENABLED = read_bool_env("CHAT_DATA_CONTEXT_COMPRESSION_ENABLED", True)
CONTEXT_COMPRESSION_DEFAULT_TRIGGER_RATIO = 0.70
CONTEXT_COMPRESSION_MAX_TRIGGER_RATIO = 0.80


def read_bounded_float_env(name: str, default: float, *, minimum: float, maximum: float) -> float:
    """读取并限制浮点环境变量，避免压缩阈值侵占输出预留。"""
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


CONTEXT_COMPRESSION_TRIGGER_RATIO = read_bounded_float_env(
    "CHAT_DATA_CONTEXT_COMPRESSION_TRIGGER_RATIO",
    CONTEXT_COMPRESSION_DEFAULT_TRIGGER_RATIO,
    minimum=0.01,
    maximum=CONTEXT_COMPRESSION_MAX_TRIGGER_RATIO,
)
CONTEXT_USAGE_SHOW_SYSTEM_PROMPT = read_bool_env("CHAT_DATA_CONTEXT_USAGE_SHOW_SYSTEM_PROMPT", False)
LLM_FIRST_TOKEN_TIMEOUT_SECONDS = read_positive_int_env("CHAT_DATA_LLM_FIRST_TOKEN_TIMEOUT_SECONDS", 60)


def get_context_window(model_id: str | None) -> int:
    """按模型名返回上下文窗口大小。"""
    from .llm_config import get_chat_data_model_config

    return int(get_chat_data_model_config(model_id).get("context_window") or DEFAULT_CONTEXT_WINDOW)


def get_context_compression_config() -> dict:
    """读取智能问数上下文压缩配置。"""
    return {
        "enabled": CONTEXT_COMPRESSION_ENABLED,
        "trigger_ratio": CONTEXT_COMPRESSION_TRIGGER_RATIO,
    }


def is_context_usage_system_prompt_visible() -> bool:
    """是否在上下文预算明细中展示 system prompt 原文。"""
    return CONTEXT_USAGE_SHOW_SYSTEM_PROMPT


def get_chat_data_runtime_config() -> dict:
    """读取智能问数前端运行时配置。"""
    from .llm_config import get_chat_data_model_options

    model_options = get_chat_data_model_options()
    default_model = model_options.get("default_model")
    return {
        "models": model_options["models"],
        "runtime_options": {
            "workspace_backend": CHAT_DATA_DEFAULT_WORKSPACE_BACKEND,
            "bash_enabled": CHAT_DATA_DEFAULT_BASH_ENABLED,
        },
        "user_overridable": CHAT_DATA_CAPABILITY_USER_OVERRIDABLE,
        "default_row_limit": DEFAULT_ROW_LIMIT,
        "query_cache_enabled": QUERY_CACHE_ENABLED,
        "context_window": get_context_window(default_model),
    }


def is_chat_data_runtime_option_user_overridable() -> bool:
    """是否允许用户在前端手动覆盖系统默认运行选项。"""
    return CHAT_DATA_CAPABILITY_USER_OVERRIDABLE


def normalize_row_limit(limit: int | None) -> int:
    """读取请求行数限制，未传时使用全局配置。"""
    if limit is None:
        return DEFAULT_ROW_LIMIT
    if not isinstance(limit, int) or limit < 1:
        raise ValueError(f"查询结果行数限制必须为正整数，当前值：{limit}")
    if limit > DEFAULT_ROW_LIMIT:
        raise ValueError(f"查询结果最多返回 {DEFAULT_ROW_LIMIT} 条，请调整查询条件")
    return limit
