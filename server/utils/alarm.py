"""Alarm log helpers for production-facing failure categories."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Iterable

from langchain_core.exceptions import ModelConnectionError, ModelTimeoutError

from utils.logger import get_alarm_logger, get_logger

logger = get_logger(__name__)

MAX_ALARM_MESSAGE_LENGTH = 500
MAX_ALARM_EXT_LENGTH = 1000


def _safe_text(value: Any, max_length: int = MAX_ALARM_EXT_LENGTH) -> str:
    if value is None:
        return "-"
    text = _format_value(value)
    text = " ".join(text.split())
    if not text:
        return "-"
    if len(text) <= max_length:
        return text
    return f"{text[:max_length]}...(truncated,total={len(text)})"


def _format_value(value: Any) -> str:
    if isinstance(value, Mapping):
        return " ".join(_format_mapping_items(value))
    if isinstance(value, set):
        return ",".join(_format_value(item) for item in sorted(value, key=lambda item: str(item)))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return ",".join(_format_value(item) for item in value)
    return _format_scalar(value)


def _format_mapping_items(value: Mapping[Any, Any], prefix: str = "") -> list[str]:
    items: list[str] = []
    for key, item in value.items():
        normalized_key = str(key or "").strip()
        if not normalized_key:
            continue
        item_key = f"{prefix}.{normalized_key}" if prefix else normalized_key
        if isinstance(item, Mapping):
            items.extend(_format_mapping_items(item, item_key))
        else:
            items.append(f"{item_key}={_format_value(item)}")
    return items


def _format_scalar(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def emit_alarm(
    message: str,
    *,
    alarm_id: str,
    request_type: str = "ALARM",
    error_code: str | None = None,
    ext1: Any = None,
    ext2: Any = None,
) -> None:
    """Write one normalized alarm log record."""
    normalized_alarm_id = str(alarm_id or "UNKNOWN_ALARM").strip() or "UNKNOWN_ALARM"
    normalized_request_type = str(request_type or "ALARM").strip() or "ALARM"
    normalized_error_code = str(error_code or normalized_alarm_id).strip() or normalized_alarm_id
    safe_message = _safe_text(message or normalized_alarm_id, MAX_ALARM_MESSAGE_LENGTH)
    safe_ext1 = _safe_text(ext1)
    safe_ext2 = _safe_text(ext2)
    get_alarm_logger().error(
        safe_message,
        extra={
            "alarm_id": normalized_alarm_id,
            "request_type": normalized_request_type,
            "error_code": normalized_error_code,
            "ext1": safe_ext1,
            "ext2": safe_ext2,
        },
    )


# 数据库连接失败的细分原因码（写入报警日志 error_code 字段，供检索与统计）。
# alarm_id="DATABASE_CONNECTION_FAILED" 对应 OMS 告警配置中的告警名称，
# 属于平台契约，不可随意改名；业务库 / checkpoint 库 / 查询数据源的区分只落在 error_code。
DB_FAIL_CHATDATA = "CHATDATA_DB_CONNECTION_FAILED"
DB_FAIL_CHECKPOINT = "CHECKPOINT_DB_CONNECTION_FAILED"
DB_FAIL_DATASOURCE = "QUERY_DATASOURCE_CONNECTION_FAILED"


def emit_database_connection_alarm(message: str, *, reason_code: str, ext1: Any = None, ext2: Any = None) -> None:
    """数据库连接类失败统一入口；reason_code 必传，强制调用方声明细分场景。"""
    emit_alarm(
        message,
        alarm_id="DATABASE_CONNECTION_FAILED",
        request_type="DATABASE",
        error_code=reason_code,
        ext1=ext1,
        ext2=ext2,
    )


# 高危 SQL 拦截的细分原因码（写入报警日志 error_code 字段，供检索与统计）。
# 注意：alarm_id="DANGEROUS_SQL_BLOCKED" 对应 OMS 告警配置中的告警名称，
# 属于平台契约，不可随意改名；细分原因只允许落在 error_code。
# 语法写错等普通校验失败（如 SQL 解析失败）不算高危，照常拒绝执行但不发本告警
# （见 _infer_sql_block_reason 返回 None 的语义）。
SQL_BLOCK_MULTI_STATEMENT = "MULTI_STATEMENT_BLOCKED"
SQL_BLOCK_TYPE_NOT_ALLOWED = "SQL_TYPE_NOT_ALLOWED"
SQL_BLOCK_SELECT_INTO = "SELECT_INTO_BLOCKED"
SQL_BLOCK_DANGEROUS_FUNCTION = "DANGEROUS_FUNCTION_BLOCKED"
SQL_BLOCK_LOCKING_READ = "LOCKING_READ_BLOCKED"
SQL_BLOCK_ROW_LIMIT = "ROW_LIMIT_EXCEEDED"
SQL_BLOCK_LIMIT_GATE = "LIMIT_GATE_FAILURE"


# sql_run 固定拦截文案（子串）→ 细分原因码；新增拦截分支在此登记一行即可。
_SQL_BLOCK_REASON_MAP = {
    "一次只能执行一条 SQL": SQL_BLOCK_MULTI_STATEMENT,
    "只允许执行以下类型的 SQL": SQL_BLOCK_TYPE_NOT_ALLOWED,
    "不允许使用 INTO": SQL_BLOCK_SELECT_INTO,
    "不允许使用高危函数": SQL_BLOCK_DANGEROUS_FUNCTION,
    "锁定读": SQL_BLOCK_LOCKING_READ,
    "请调整 SQL LIMIT": SQL_BLOCK_ROW_LIMIT,
    "行数上限校验失败": SQL_BLOCK_LIMIT_GATE,
}


def _infer_sql_block_reason(reason: str) -> str | None:
    """根据拦截文案推断细分原因码（文案来自 sql_run 固定拦截分支）。

    解析失败、SQL 为空等普通校验失败不在映射内，返回 None，调用方不应为此
    发 DANGEROUS_SQL_BLOCKED 告警。
    """
    text = str(reason or "")
    for marker, reason_code in _SQL_BLOCK_REASON_MAP.items():
        if marker in text:
            return reason_code
    return None


def emit_dangerous_sql_alarm(
    sql: str,
    reason: str,
    *,
    source_type: str | None = None,
    datasource_id: str | None = None,
    user_id: str | None = None,
    execution_scope: str | None = None,
    reason_code: str | None = None,
) -> None:
    resolved_code = reason_code or _infer_sql_block_reason(reason)
    if not resolved_code:
        # 非安全拦截（解析失败等普通校验失败）：拒绝执行由业务错误路径负责，
        # 不占用高危告警。warning 便于发现新增拦截分支漏传 reason_code。
        logger.warning(
            "[alarm] 跳过 DANGEROUS_SQL_BLOCKED 告警（非安全拦截）: reason=%s",
            str(reason or "")[:200],
        )
        return
    emit_alarm(
        "高危 SQL 已拦截",
        # OMS 契约字段：与告警配置中的告警名称一致，勿改。
        alarm_id="DANGEROUS_SQL_BLOCKED",
        request_type="SQL",
        error_code=resolved_code,
        ext1={
            "source_type": source_type or "",
            "datasource_id": datasource_id or "",
            "user_id": user_id or "",
            "execution_scope": execution_scope or "",
            "reason": reason,
        },
        ext2={"sql": sql},
    )


# 外部接口/平台自身接口异常的细分原因码。alarm_id 统一为 API_CALL_EXCEPTION，
# 是否外部依赖只落在 error_code。
API_FAIL_EXTERNAL = "EXTERNAL_API_CALL_FAILED"
API_FAIL_INTERNAL = "INTERNAL_API_CALL_FAILED"

# 大模型异常的细分原因码。alarm_id 统一为 LLM_EXCEPTION。
LLM_FAIL_CONNECTION = "LLM_CONNECTION_FAILED"
LLM_FAIL_FIRST_TOKEN_TIMEOUT = "LLM_FIRST_TOKEN_TIMEOUT"


def emit_external_api_alarm(
    api_name: str,
    exc: Exception,
    *,
    method: str | None = None,
    url: str | None = None,
    ext1: Any = None,
) -> None:
    emit_alarm(
        f"调用外部接口异常: {api_name}",
        alarm_id="API_CALL_EXCEPTION",
        request_type="HTTP_CLIENT",
        error_code=API_FAIL_EXTERNAL,
        ext1=ext1 or {"api": api_name, "method": method or "", "url": url or ""},
        ext2={"error_type": exc.__class__.__name__, "error": str(exc)},
    )


def emit_api_call_alarm(
    path: str,
    exc: Exception,
    *,
    method: str | None = None,
    ext1: Any = None,
    ext2: Any = None,
) -> None:
    emit_alarm(
        "接口调用异常",
        alarm_id="API_CALL_EXCEPTION",
        request_type=method or "API",
        error_code=API_FAIL_INTERNAL,
        ext1=ext1,
        ext2=ext2 or {"path": path, "error_type": exc.__class__.__name__, "error": str(exc)},
    )


def _iter_exception_chain(exc: BaseException) -> Iterable[BaseException]:
    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        nested = getattr(current, "exceptions", None)
        if isinstance(nested, (list, tuple)):
            stack.extend(item for item in nested if isinstance(item, BaseException))
        cause = getattr(current, "__cause__", None)
        context = getattr(current, "__context__", None)
        if isinstance(cause, BaseException):
            stack.append(cause)
        if isinstance(context, BaseException):
            stack.append(context)


def is_llm_connection_exception(exc: BaseException) -> bool:
    """Best-effort classifier for model provider connectivity failures."""
    # langchain-core 1.6+ 标准模型异常族:类型即事实,优先按类型判定,
    # 避免依赖厂商报错文案(内网 vLLM 的报错措辞不受我们控制)。
    for item in _iter_exception_chain(exc):
        if isinstance(item, (ModelConnectionError, ModelTimeoutError)):
            return True
    markers = (
        "api connection",
        "api timeout",
        "connection error",
        "connection refused",
        "connection reset",
        "connect timeout",
        "connecterror",
        "readtimeout",
        "timeout",
        "timed out",
        "连接失败",
        "连接超时",
        "请求超时",
    )
    connection_class_markers = (
        "apiconnectionerror",
        "apitimeouterror",
        "connecterror",
        "connecttimeout",
        "connectionerror",
        "networkerror",
        "readtimeout",
        "timeout",
        "timeouterror",
    )
    for item in _iter_exception_chain(exc):
        class_path = f"{item.__class__.__module__}.{item.__class__.__name__}".lower()
        message = str(item).lower()
        if any(marker in class_path for marker in connection_class_markers):
            return True
        if any(marker in class_path or marker in message for marker in markers):
            return True
    return False


def _mark_exception_once(exc: BaseException, marker: str) -> bool:
    attr = f"_chatdata_alarm_{marker}_emitted"
    if bool(getattr(exc, attr, False)):
        return False
    try:
        setattr(exc, attr, True)
    except Exception as exc:
        logger.warning("告警异常对象打去重标记失败,按首次告警继续上报: marker=%s error=%s", marker, exc)
        return True
    return True


def emit_llm_connection_alarm_if_needed(exc: BaseException, *, ext1: Any = None, ext2: Any = None) -> bool:
    if not is_llm_connection_exception(exc) or not _mark_exception_once(exc, "llm_connection"):
        return False
    emit_alarm(
        "大模型连接异常",
        alarm_id="LLM_EXCEPTION",
        request_type="LLM",
        error_code=LLM_FAIL_CONNECTION,
        ext1=ext1,
        ext2=ext2 or {"error_type": exc.__class__.__name__, "error": str(exc)},
    )
    return True


def emit_llm_first_token_timeout_alarm(
    *, elapsed_ms: int, threshold_seconds: int, ext1: Any = None, ext2: Any = None
) -> None:
    emit_alarm(
        "大模型首 token 超时",
        alarm_id="LLM_EXCEPTION",
        request_type="LLM",
        error_code=LLM_FAIL_FIRST_TOKEN_TIMEOUT,
        ext1=ext1,
        ext2=ext2 or {"elapsed_ms": elapsed_ms, "threshold_seconds": threshold_seconds},
    )


# 单会话工具调用频率超限的细分原因码（写入报警日志 error_code 字段）。
# 注意：alarm_id="TOOL_CALL_RATE_EXCEEDED" 对应 OMS 告警配置中的告警名称，
# 属于平台契约，不可随意改名；窗口/阈值等参数只落在 ext1，便于按会话检索。
TOOL_RATE_CONVERSATION_EXCEEDED = "CONVERSATION_TOOL_CALL_RATE_EXCEEDED"


def emit_tool_call_rate_alarm(
    *,
    conversation_id: str,
    count: int,
    threshold: int,
    window_seconds: int,
    tool_name: str | None = None,
    user_id: str | None = None,
    ext1: Any = None,
) -> None:
    """单会话工具调用频率超限告警（合规要求：按会话在时间窗口内聚合计数）。

    ``count`` 为触发时的窗口内调用数（含当次），``threshold`` 为配置上限；
    两者一并入 ext1，便于告警侧判断超限倍数。
    """
    emit_alarm(
        "单会话工具调用频率超限",
        # OMS 契约字段：与告警配置中的告警名称一致，勿改。
        alarm_id="TOOL_CALL_RATE_EXCEEDED",
        request_type="TOOL",
        error_code=TOOL_RATE_CONVERSATION_EXCEEDED,
        ext1=ext1
        or {
            "conversation_id": conversation_id,
            "count": count,
            "threshold": threshold,
            "window_seconds": window_seconds,
        },
        ext2={"last_tool_name": tool_name or "", "user_id": user_id or ""},
    )
