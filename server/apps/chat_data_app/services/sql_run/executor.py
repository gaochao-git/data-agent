"""
SQL 执行器模块

支持 MySQL、Hive 的异步查询执行
通过环境变量配置 SQL 安全限制和结果行数限制
"""

import asyncio
import inspect
import os
from abc import ABC, abstractmethod
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from utils.alarm import (
    DB_FAIL_DATASOURCE,
    emit_dangerous_sql_alarm,
    emit_database_connection_alarm,
    emit_external_api_alarm,
)
from utils.db_failover import is_mysql_connection_error
from utils.logger import get_logger

from ..datasource.credentials import (
    HIVE_SOCKET_TIMEOUT_SECONDS,
    add_http_user_params,
    apply_hive_socket_timeout,
    build_hive_connect_params,
    build_http_auth_headers,
    get_http_auth_token,
    open_http_client,
    resolve_execution_source_config,
)

# Keep the executor seam patchable without duplicating Hive transport logic.
_apply_socket_timeout = apply_hive_socket_timeout
SOCKET_TIMEOUT_SECONDS = HIVE_SOCKET_TIMEOUT_SECONDS
from ..runtime_config import DEFAULT_ROW_LIMIT, MAX_QUERY_TIMEOUT_SECONDS, normalize_row_limit
from ..setting_service import get_runtime_setting
from .parser import extract_top_level_limit_count
from .sql_guard import (  # noqa: F401  ALLOWED_SQL_TYPES 为历史兼容 re-export，测试与外部仍从本模块引用
    ALLOWED_SQL_TYPES,
    check_effective_limit_gate,
    has_line_comment_marker,
    run_sql_guard,
    sql_operation_type,
)

logger = get_logger(__name__)

# ============ 可配置参数 ============

# MySQL/Hive 查询默认超时已迁移到后台配置变量表（CHAT_DATA_MYSQL/HIVE_QUERY_TIMEOUT_SECONDS，
# 运行期热值经 resolve_query_timeout 读取）；HTTP 轮询间隔同（CHAT_DATA_HTTP_POLL_INTERVAL_MS）。

# 允许的 SQL 操作类型（从环境变量读取，默认仅 SELECT；定义与规则实现在 sql_guard）
#
# SHOW / EXPLAIN / DESCRIBE 属于元数据或执行计划类命令；Hive 方言下 sqlglot
# 对部分语法会降级为 COMMAND，暂不复用通用 SQL 执行口放行。后续如需支持，
# 应单独提供受控接口并做权限、审计和结果脱敏。
CONNECT_TIMEOUT_SECONDS = 3
MYSQL_KILL_TIMEOUT_SECONDS = 5
HTTP_SUBMIT_URL = str(os.getenv("CHAT_DATA_HTTP_SUBMIT_URL") or "").strip()
HTTP_STATUS_URL = str(os.getenv("CHAT_DATA_HTTP_STATUS_URL") or "").strip()
HTTP_STOP_URL = str(os.getenv("CHAT_DATA_HTTP_STOP_URL") or "").strip()
JS_SAFE_INTEGER_MAX = 2**53 - 1


HTTP_AUTH_TOKEN = get_http_auth_token()

# Hive 同步驱动专用线程池。pyhive 无法被 asyncio 取消：wait_for 超时后线程
# 会继续跑到 socket 超时才收口。用有界专用池隔离，避免这些滞留线程拖垮
# 进程内共享的默认线程池（asyncio.to_thread 的技能文件 IO 等共用默认池）。
_HIVE_EXECUTOR_THREAD_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="chatdata-hive-query")


def _http_payload_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    """生成 HTTP 日志用的请求摘要。"""
    summary = dict(payload or {})
    if "sql" in summary:
        summary["sql_len"] = len(str(summary.get("sql") or ""))
    return summary


def _first_not_none(*values: Any) -> Any:
    """返回第一个非 None 值，保留 0、空字符串等合法原值。"""
    for value in values:
        if value is not None:
            return value
    return None


def _http_response_summary(payload: Any) -> dict[str, Any]:
    """生成 HTTP 日志用的响应摘要，避免打印查询结果明细。"""
    if not isinstance(payload, dict):
        return {"payload_type": type(payload).__name__}
    data = payload.get("data")
    result = data if isinstance(data, dict) else payload
    rows = result.get("data")
    if not isinstance(rows, list):
        rows = result.get("rows")
    if not isinstance(rows, list):
        rows = result.get("records")
    columns = result.get("columns")
    tables = result.get("tables")
    summary = {
        "code": payload.get("code"),
        "msg": _first_not_none(payload.get("msg"), payload.get("message")),
        "status": _first_not_none(result.get("status"), payload.get("status")),
        "task_id": _first_not_none(
            result.get("task_id"), result.get("taskId"), payload.get("task_id"), payload.get("taskId")
        ),
        "cost": _first_not_none(result.get("cost"), payload.get("cost")),
        "startTime": _first_not_none(result.get("startTime"), payload.get("startTime")),
        "execution_time_ms": _first_not_none(result.get("execution_time_ms"), payload.get("execution_time_ms")),
        "duration_ms": _first_not_none(result.get("duration_ms"), payload.get("duration_ms")),
    }
    if isinstance(rows, list):
        summary["row_count"] = len(rows)
    if isinstance(columns, list):
        summary["column_count"] = len(columns)
    if isinstance(tables, list):
        summary["table_count"] = len(tables)
    return {key: value for key, value in summary.items() if value is not None}


async def resolve_query_timeout(source_type: str, timeout: int | None = None) -> int:
    """解析查询超时时间：显式传参优先（封顶 MAX_QUERY_TIMEOUT_SECONDS），
    否则按数据源类型读配置变量表热值（表 > env > 注册表默认 60/600 秒）。"""
    if timeout is not None:
        normalized_timeout = int(timeout)
        if normalized_timeout > 0:
            return min(normalized_timeout, MAX_QUERY_TIMEOUT_SECONDS)

    normalized_source_type = str(source_type or "").strip().lower()
    if normalized_source_type == "hive":
        return await get_runtime_setting("CHAT_DATA_HIVE_QUERY_TIMEOUT_SECONDS")
    return await get_runtime_setting("CHAT_DATA_MYSQL_QUERY_TIMEOUT_SECONDS")


def _friendly_sql_error(engine: str, err: Exception) -> str:
    """保留底层执行错误，避免把具体库表/字段/语法原因覆盖成泛化文案。"""
    raw = str(err).strip()
    lower = raw.lower()
    normalized_raw = " ".join(raw.split())

    if (
        "query execution was interrupted" in lower
        or "query interrupted" in lower
        or "statement cancelled" in lower
        or "statement canceled" in lower
        or "operation cancelled" in lower
        or "operation canceled" in lower
        or "killed" in lower
    ):
        return "已人工取消"
    return normalized_raw or raw or f"{engine}执行失败: {err.__class__.__name__}"


def _serialize_value(value: Any) -> Any:
    """将查询值转换为 JSON 可表示且不会损失 JavaScript 整数精度的类型。"""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, int) and not isinstance(value, bool) and abs(value) > JS_SAFE_INTEGER_MAX:
        return str(value)
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    """序列化整行数据"""
    return {k: _serialize_value(v) for k, v in row.items()}


def _extract_response_payload(payload: Any) -> dict[str, Any]:
    """提取接口响应主体。"""
    return payload if isinstance(payload, dict) else {}


def _extract_remote_data(payload: dict[str, Any]) -> dict[str, Any]:
    """提取远端 data 节点。"""
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def _extract_task_id(payload: dict[str, Any]) -> str:
    """从提交响应中提取任务 ID。"""
    for source in (payload, _extract_remote_data(payload)):
        if not isinstance(source, dict):
            continue
        for key in ("task_id", "job_id", "id"):
            value = str(source.get(key) or "").strip()
            if value:
                return value
    return ""


def _extract_remote_code(payload: dict[str, Any]) -> int | None:
    """提取远端业务码。"""
    if not isinstance(payload, dict) or "code" not in payload:
        return None
    try:
        return int(payload.get("code"))
    except (TypeError, ValueError):
        return None


def _extract_remote_message(payload: dict[str, Any], default: str = "") -> str:
    """提取远端错误或提示信息。"""
    if not isinstance(payload, dict):
        return default
    for key in ("msg", "message", "error_message", "error", "detail"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return default


def _normalize_remote_task_status(status: Any) -> str:
    """标准化远端任务状态。"""
    normalized = str(status or "").strip().lower()
    if normalized in {"queued", "created", "pending", "submitted"}:
        return "pending"
    if normalized in {"running", "processing", "executing"}:
        return "running"
    if normalized in {"success", "succeeded", "finished", "completed", "done"}:
        return "succeeded"
    if normalized in {"cancelled", "canceled", "cancel", "killed"}:
        return "cancelled"
    if normalized in {"failed", "error"}:
        return "failed"
    return normalized or "pending"


def _extract_remote_status(payload: dict[str, Any]) -> str:
    """提取远端任务状态。"""
    data_payload = _extract_remote_data(payload)
    return _normalize_remote_task_status(data_payload.get("status") or payload.get("status"))


def _extract_result_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """从远端结果中提取真正的查询结果。"""
    if not isinstance(payload, dict):
        return {}
    data_payload = _extract_remote_data(payload)
    for container in (data_payload, payload):
        if not isinstance(container, dict):
            continue
        for key in ("result", "query_result"):
            value = container.get(key)
            if isinstance(value, dict):
                return value
        if any(key in container for key in ("columns", "data", "rows", "records", "total_count", "row_count")):
            return container
    return {}


def _normalize_http_execution_time_ms(payload: dict[str, Any], result_payload: dict[str, Any]) -> int | None:
    """提取远端执行耗时，优先使用业务 status 接口 cost 字段。"""
    sources = (_extract_remote_data(payload), payload, result_payload)
    for key in ("cost", "execution_time_ms", "duration_ms"):
        for source in sources:
            if not isinstance(source, dict):
                continue
            value = source.get(key)
            if value is None or isinstance(value, bool):
                continue
            try:
                return max(0, int(float(value)))
            except (TypeError, ValueError):
                continue
    return None


def _parse_start_time_from_source(source: Any) -> datetime | None:
    """从单个数据源字典解析开始时间；无法解析返回 None。"""
    if not isinstance(source, dict):
        return None
    raw_value = source.get("startTime") or source.get("started_time") or source.get("start_time")
    if isinstance(raw_value, datetime):
        return raw_value
    text = str(raw_value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _parse_http_start_time(payload: dict[str, Any], result_payload: dict[str, Any]) -> datetime | None:
    """解析远端开始时间，业务接口格式为 yyyy-MM-dd HH:mm:ss。"""
    for source in (result_payload, _extract_remote_data(payload), payload):
        parsed = _parse_start_time_from_source(source)
        if parsed is not None:
            return parsed
    return None


def _build_http_execution_times(payload: dict[str, Any], result_payload: dict[str, Any]) -> dict[str, Any]:
    """按远端 cost/startTime 生成内部统一执行时间字段。"""
    execution_time_ms = _normalize_http_execution_time_ms(payload, result_payload)
    started_time = _parse_http_start_time(payload, result_payload)
    finished_time = None
    if started_time is None or execution_time_ms is None:
        started_time = None
    else:
        finished_time = started_time + timedelta(milliseconds=execution_time_ms)
    return {
        "execution_time_ms": execution_time_ms,
        "started_time": started_time.strftime("%Y-%m-%d %H:%M:%S") if started_time else None,
        "finished_time": finished_time.strftime("%Y-%m-%d %H:%M:%S") if finished_time else None,
    }


def _normalize_http_column_item(item: Any) -> str | None:
    """标准化单个远端列项；无法归一化为有效列名返回 None。"""
    if isinstance(item, str):
        normalized = item.strip()
        if normalized:
            return normalized
    elif isinstance(item, dict):
        normalized = str(item.get("name") or item.get("column_name") or "").strip()
        if normalized:
            return normalized
    return None


def _normalize_http_columns(raw_columns: Any, rows: list[dict[str, Any]]) -> list[str]:
    """标准化远端返回的列结构。"""
    if isinstance(raw_columns, list):
        result: list[str] = []
        for item in raw_columns:
            normalized = _normalize_http_column_item(item)
            if normalized:
                result.append(normalized)
        if result:
            return result
    if rows:
        return list(rows[0].keys())
    return []


def _normalize_http_rows(raw_rows: Any, columns: list[str]) -> list[dict[str, Any]]:
    """标准化远端返回的数据行。"""
    if not isinstance(raw_rows, list):
        return []
    result: list[dict[str, Any]] = []
    for row in raw_rows:
        if isinstance(row, dict):
            result.append(_serialize_row(row))
            continue
        if isinstance(row, (list, tuple)) and columns:
            result.append(_serialize_row(dict(zip(columns, row, strict=False))))
    return result


def _get_sql_operation_type(sql: str, source_type: str | None = None) -> str:
    """解析 SQL 类型，用于白名单校验，不改写 SQL（实现收敛在 sql_guard）。"""
    return sql_operation_type(sql, source_type)


def validate_sql(sql: str, source_type: str | None = None, max_rows: int | None = None) -> tuple[bool, str]:
    """
    验证 SQL 是否安全（历史兼容门面，规则实现在 sql_guard 管线）

    Args:
        sql: SQL 语句
        source_type: 数据源方言
        max_rows: 行数上限热值（未传回退导入期兜底值）

    Returns:
        (is_valid, error_message) - 如果验证通过返回 (True, "")，否则返回 (False, 错误信息)
    """
    violation = run_sql_guard(sql, source_type=source_type, max_rows=max_rows)
    if violation is not None:
        return False, violation.message
    return True, ""


def _should_auto_add_limit(sql: str, source_type: str | None = None) -> bool:
    """判断 SQL 是否需要自动追加 LIMIT。"""
    normalized_sql = str(sql or "").strip().lstrip("(").strip()
    if not normalized_sql:
        return False
    if extract_top_level_limit_count(normalized_sql, source_type=source_type) is not None:
        return False
    return _get_sql_operation_type(normalized_sql, source_type=source_type) == "SELECT"


def _consume_comment_scan_quote(line: str, i: int, in_quote: str | None) -> tuple[int, str | None]:
    """处理 _line_has_comment_marker 引号内字符，返回 (新索引, 引号状态)。"""
    char = line[i]
    next_char = line[i + 1] if i + 1 < len(line) else ""
    if char == "\\":
        return i + 2, in_quote
    if char == in_quote:
        if in_quote == "'" and next_char == "'":
            return i + 2, in_quote
        return i + 1, None
    return i + 1, in_quote


def _line_has_comment_marker(line: str) -> bool:
    """判断单行非字符串位置是否存在 -- 注释。"""
    in_quote: str | None = None
    i = 0
    n = len(line)
    while i < n:
        char = line[i]
        next_char = line[i + 1] if i + 1 < n else ""
        if in_quote:
            i, in_quote = _consume_comment_scan_quote(line, i, in_quote)
            continue
        if char in {"'", '"', "`"}:
            in_quote = char
            i += 1
            continue
        if char == "-" and next_char == "-":
            return True
        i += 1
    return False


def _append_limit(sql: str, limit: int) -> str:
    """追加 LIMIT，避免被末尾 -- 注释吞掉。"""
    base_sql = str(sql or "").rstrip().rstrip(";").rstrip()
    separator = "\n" if _line_has_comment_marker(base_sql.splitlines()[-1] if base_sql.splitlines() else "") else " "
    return f"{base_sql}{separator}LIMIT {limit}"


def _add_limit_if_needed(sql: str, limit: int, source_type: str | None = None, max_rows: int | None = None) -> str:
    """仅对查询型 SQL 自动追加 LIMIT；max_rows 为行数上限热值（未传回退兜底值）。"""
    effective_max_rows = DEFAULT_ROW_LIMIT if max_rows is None else max_rows
    normalized_limit = normalize_row_limit(limit, max_rows=effective_max_rows)
    top_level_limit_count = extract_top_level_limit_count(str(sql or ""), source_type=source_type)
    if top_level_limit_count is not None and top_level_limit_count > effective_max_rows:
        raise ValueError(f"查询结果最多返回 {effective_max_rows} 条，请调整 SQL LIMIT")
    return _append_limit(sql, normalized_limit) if _should_auto_add_limit(sql, source_type=source_type) else sql


def build_effective_sql(
    sql: str,
    limit: int = DEFAULT_ROW_LIMIT,
    source_type: str | None = None,
    *,
    datasource_id: str | None = None,
    user_id: str | None = None,
    max_rows: int | None = None,
) -> str:
    """生成真实执行 SQL：仅按需追加 LIMIT，出口终检强制行数上限。

    max_rows 为行数上限热值（调用方从配置变量表解析后传入；未传回退兜底值）。
    终检门是"进入数据库的 SELECT 必须带合法 LIMIT"的硬保证：补全阶段的任何
    缺陷（如注释吞掉追加文本）在这里从静默无界查询变成响亮拒绝。拒绝时按
    原因分叉——末行注释导致补全失效给可行动文案；其余属系统内部缺陷，
    记 error 日志并走高危 SQL 报警。
    """
    effective_sql = _add_limit_if_needed(sql, limit, source_type=source_type, max_rows=max_rows)
    gate = check_effective_limit_gate(effective_sql, source_type=source_type, max_rows=max_rows)
    if gate is None:
        return effective_sql
    base_sql = str(sql or "").rstrip().rstrip(";").rstrip()
    last_line = base_sql.splitlines()[-1] if base_sql.splitlines() else ""
    if has_line_comment_marker(last_line):
        raise ValueError("SQL 末尾的注释导致系统无法安全追加行数上限，请将注释移至 LIMIT 之前或删除注释后重试")
    logger.error("LIMIT 终检失败（系统内部缺陷信号）: sql_tail=%r", effective_sql[-120:])
    _emit_sql_execution_validation_alarm(
        sql,
        gate.message,
        source_type=source_type or "",
        execution_scope="limit_gate",
        datasource_id=datasource_id or "",
        user_id=user_id or "",
    )
    raise ValueError(gate.message)


def _log_sql_execution_summary(source_type: str, database: str, execution_time_ms: int, row_count: int) -> None:
    """记录不含 SQL 明文的执行摘要。"""
    logger.info(
        "[%s] SQL 执行完成: database=%s elapsed_ms=%s rows=%s", source_type, database, execution_time_ms, row_count
    )


def _emit_sql_execution_validation_alarm(
    sql: str,
    error_msg: str,
    *,
    source_type: str,
    execution_scope: str,
    datasource_id: str = "",
    user_id: str = "",
) -> None:
    """记录执行边界拦截的高危 SQL。"""
    emit_dangerous_sql_alarm(
        sql,
        error_msg,
        source_type=source_type,
        execution_scope=execution_scope,
        datasource_id=datasource_id or None,
        user_id=user_id or None,
    )


def _emit_query_database_connection_alarm(
    source_type: str,
    database: str,
    err: Exception,
    *,
    host: str | None = None,
    port: int | None = None,
    datasource_id: str = "",
    user_id: str = "",
) -> None:
    """记录查询数据源连接类异常。"""
    normalized_source_type = str(source_type or "").strip().lower()
    if normalized_source_type == "mysql" and not is_mysql_connection_error(err):
        return
    if normalized_source_type == "hive" and not _is_hive_connection_exception(err):
        return
    emit_database_connection_alarm(
        "查询数据源连接异常",
        reason_code=DB_FAIL_DATASOURCE,
        ext1={
            "source_type": normalized_source_type,
            "host": host or "",
            "port": port or "",
            "database": database or "",
            "datasource_id": datasource_id or "",
            "user_id": user_id or "",
        },
        ext2={"error_type": err.__class__.__name__, "error": str(err)},
    )


def _is_hive_connection_exception(err: Exception) -> bool:
    text = f"{err.__class__.__module__}.{err.__class__.__name__} {err}".lower()
    markers = (
        "connect",
        "connection",
        "socket",
        "transport",
        "thrift",
        "timeout",
        "timed out",
        "refused",
        "unreachable",
        "network",
    )
    return any(marker in text for marker in markers)


def _http_error_result(error: str | None, **extra: Any) -> dict[str, Any]:
    """HTTP 链路统一错误结果结构；extra 携带 status、耗时等补充字段。"""
    result: dict[str, Any] = {"status": "failed", "error": error, "columns": [], "data": [], "total_count": 0}
    result.update(extra)
    return result


class SQLExecutor(ABC):
    """SQL 执行器抽象基类"""

    @abstractmethod
    async def execute(
        self,
        sql: str,
        limit: int = DEFAULT_ROW_LIMIT,
        timeout: int | None = None,
        user_id: str | None = None,
        datasource_id: str | None = None,
        remote_task_callback: Callable[[dict[str, Any]], Any] | None = None,
        db_connection_callback: Callable[[dict[str, Any]], Any] | None = None,
    ) -> dict[str, Any]:
        """
        执行 SQL 并返回结果

        Args:
            sql: SQL 查询语句
            limit: 结果行数限制
            timeout: 查询超时时间（秒）
            user_id: 发起用户 ID（告警定位用）
            datasource_id: 所属数据源组 ID（告警定位用）

        Returns:
            {
                "status": str,         # succeeded / failed（HTTP 链路可为 running/cancelled 等过程态）
                "columns": List[str],  # 列名列表
                "data": List[Dict],    # 数据行列表
                "total_count": int,    # 返回的行数
                "error": str           # 错误信息（仅当 status != succeeded）
            }
        """
        pass

    @abstractmethod
    async def test_connection(self) -> bool:
        """测试数据库连接"""
        pass

    @abstractmethod
    async def close(self):
        """关闭连接/连接池"""
        pass


class MySQLExecutor(SQLExecutor):
    """MySQL 执行器 - 使用 aiomysql 异步驱动"""

    def __init__(self, host: str, port: int, database: str, user: str, password: str):
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        # 密码已由 datasource_manager 解密，直接使用
        self.password = password or None
        self._pool = None

    async def _get_pool(self):
        """获取连接池（懒加载）"""
        if self._pool is None:
            import aiomysql

            self._pool = await aiomysql.create_pool(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                db=self.database,
                charset="utf8mb4",
                autocommit=True,
                minsize=1,
                maxsize=5,
                connect_timeout=CONNECT_TIMEOUT_SECONDS,
            )
        return self._pool

    @staticmethod
    async def _fetch_connection_id(cursor) -> str:
        """取当前 MySQL 连接 ID（用于超时 KILL 与回调透传），空结果返回空串。"""
        await cursor.execute("SELECT CONNECTION_ID()")
        connection_row = await cursor.fetchone()
        if not connection_row:
            return ""
        if isinstance(connection_row, (list, tuple)):
            return str(connection_row[0])
        return str(connection_row)

    @staticmethod
    async def _maybe_invoke_db_connection_callback(
        connection_id: str,
        db_connection_callback: Callable[[dict[str, Any]], Any] | None,
    ) -> None:
        """连接 ID 就绪且回调存在时调用回调；若为 awaitable 协程则 await。"""
        if not (connection_id and db_connection_callback is not None):
            return
        result = db_connection_callback({"db_connection_id": connection_id})
        if inspect.isawaitable(result):
            await result

    async def execute(
        self,
        sql: str,
        limit: int = DEFAULT_ROW_LIMIT,
        timeout: int | None = None,
        user_id: str | None = None,
        datasource_id: str | None = None,
        remote_task_callback: Callable[[dict[str, Any]], Any] | None = None,
        db_connection_callback: Callable[[dict[str, Any]], Any] | None = None,
    ) -> dict[str, Any]:
        import time

        resolved_timeout = await resolve_query_timeout("mysql", timeout)
        # 行数上限热值：校验与 LIMIT 补全/终检共用同一运行时配置
        max_rows = await get_runtime_setting("CHATDATA_ROW_LIMIT")
        # SQL 安全验证
        is_valid, error_msg = validate_sql(sql, source_type="mysql", max_rows=max_rows)
        if not is_valid:
            _emit_sql_execution_validation_alarm(
                sql,
                error_msg,
                source_type="mysql",
                execution_scope="mysql_executor",
                datasource_id=datasource_id or "",
                user_id=user_id or "",
            )
            return {"status": "failed", "error": error_msg, "columns": [], "data": [], "total_count": 0}

        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn, conn.cursor() as cursor:
                connection_id = await self._fetch_connection_id(cursor)
                await self._maybe_invoke_db_connection_callback(connection_id, db_connection_callback)

                sql_with_limit = build_effective_sql(
                    sql, limit, source_type="mysql", datasource_id=datasource_id, user_id=user_id, max_rows=max_rows
                )
                logger.debug(f"[MySQL] 执行 SQL: {sql_with_limit[:200]}...")
                # 添加超时控制：execute 与 fetchall 都必须在保护区内，
                # 否则查询在超时边界完成后 fetchall 仍可能无限挂起
                start_time = time.time()

                async def _execute_and_fetch() -> list:
                    await cursor.execute(sql_with_limit)
                    return await cursor.fetchall()

                try:
                    rows = await asyncio.wait_for(_execute_and_fetch(), timeout=resolved_timeout)
                except TimeoutError:
                    # 超时 = 服务端终止 + 废弃连接：客户端取消不会让服务端停跑，
                    # 半读状态的连接也绝不能释放回连接池（否则污染后续复用者）
                    await self._kill_timed_out_query(connection_id)
                    conn.close()
                    logger.error(f"[MySQL] 执行 SQL 超时（{resolved_timeout}s），已发送 KILL 并废弃连接")
                    return {
                        "status": "failed",
                        "error": f"查询超时（{resolved_timeout}秒）",
                        "columns": [],
                        "data": [],
                        "total_count": 0,
                    }
                execution_time_ms = int((time.time() - start_time) * 1000)
                columns = [desc[0] for desc in cursor.description] if cursor.description else []
                data = [_serialize_row(dict(zip(columns, row, strict=False))) for row in rows]
                _log_sql_execution_summary("MySQL", self.database, execution_time_ms, len(data))
                return {
                    "status": "succeeded",
                    "columns": columns,
                    "data": data,
                    "total_count": len(data),
                    "execution_time_ms": execution_time_ms,
                    "db_connection_id": connection_id,
                }
        except TimeoutError:
            logger.error(f"[MySQL] 执行 SQL 超时（{resolved_timeout}s）")
            return {
                "status": "failed",
                "error": f"查询超时（{resolved_timeout}秒）",
                "columns": [],
                "data": [],
                "total_count": 0,
            }
        except ValueError as e:
            logger.warning("[MySQL] SQL 执行参数非法: %s", e)
            return {"status": "failed", "error": str(e), "columns": [], "data": [], "total_count": 0}
        except Exception as e:
            _emit_query_database_connection_alarm(
                "mysql",
                self.database,
                e,
                host=self.host,
                port=self.port,
                datasource_id=datasource_id or "",
                user_id=user_id or "",
            )
            logger.error(f"[MySQL] 执行 SQL 失败: {e}")
            return {
                "status": "failed",
                "error": _friendly_sql_error("MySQL", e),
                "columns": [],
                "data": [],
                "total_count": 0,
            }

    async def test_connection(self) -> bool:
        """测试 MySQL 连接"""
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn, conn.cursor() as cursor:
                await cursor.execute("SELECT 1")
                return True
        except Exception as e:
            _emit_query_database_connection_alarm("mysql", self.database, e, host=self.host, port=self.port)
            logger.error(f"[MySQL] 连接测试失败: {e}")
            return False

    async def kill_query(self, connection_id: str) -> dict[str, Any]:
        """使用独立连接停止指定 MySQL 会话上的当前查询。"""
        normalized_connection_id = str(connection_id or "").strip()
        if not normalized_connection_id.isdigit():
            return {"status": "failed", "error": "无效的 MySQL 连接ID"}
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn, conn.cursor() as cursor:
                await cursor.execute(f"KILL QUERY {normalized_connection_id}")
            return {"status": "succeeded", "error": None}
        except Exception as e:
            logger.error("[MySQL] 停止查询失败: connection_id=%s error=%s", normalized_connection_id, e)
            return {"status": "failed", "error": _friendly_sql_error("MySQL", e)}

    async def _kill_timed_out_query(self, connection_id: str) -> None:
        """超时后服务端终止查询；KILL 失败只告警不阻塞超时返回（连接随后会被废弃）。"""
        normalized_connection_id = str(connection_id or "").strip()
        if not normalized_connection_id:
            return
        try:
            kill_result = await asyncio.wait_for(
                self.kill_query(normalized_connection_id), timeout=MYSQL_KILL_TIMEOUT_SECONDS
            )
            if kill_result.get("status") != "succeeded":
                logger.warning(
                    "[MySQL] 超时查询 KILL 失败: connection_id=%s error=%s",
                    normalized_connection_id,
                    kill_result.get("error"),
                )
        except Exception as exc:
            logger.warning("[MySQL] 超时查询 KILL 异常: connection_id=%s error=%s", normalized_connection_id, exc)

    async def close(self):
        """关闭连接池"""
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None


class HiveExecutor(SQLExecutor):
    """Hive 执行器 - 使用 pyhive (同步库，通过 run_in_executor 包装为异步)"""

    def __init__(self, host: str, port: int, database: str, username: str = None, password: str = None):
        self.host = host
        self.port = port
        self.database = database
        self.username = username
        # 密码已由 datasource_manager 解密，直接使用
        self.password = password or None

    def _get_connection(self):
        """获取 Hive 连接"""
        from pyhive import hive

        conn_params = build_hive_connect_params(
            host=self.host,
            port=self.port,
            database=self.database,
            username=self.username,
            password=self.password,
        )
        conn = hive.connect(**conn_params)
        _apply_socket_timeout(getattr(conn, "_transport", None), SOCKET_TIMEOUT_SECONDS)
        return conn

    async def execute(
        self,
        sql: str,
        limit: int = DEFAULT_ROW_LIMIT,
        timeout: int | None = None,
        user_id: str | None = None,
        datasource_id: str | None = None,
        remote_task_callback: Callable[[dict[str, Any]], Any] | None = None,
        db_connection_callback: Callable[[dict[str, Any]], Any] | None = None,
    ) -> dict[str, Any]:
        resolved_timeout = await resolve_query_timeout("hive", timeout)
        # 行数上限热值：校验与 LIMIT 补全/终检共用同一运行时配置（同步线程路径经参数下传）
        max_rows = await get_runtime_setting("CHATDATA_ROW_LIMIT")
        # SQL 安全验证
        is_valid, error_msg = validate_sql(sql, source_type="hive", max_rows=max_rows)
        if not is_valid:
            _emit_sql_execution_validation_alarm(
                sql,
                error_msg,
                source_type="hive",
                execution_scope="hive_executor",
                datasource_id=datasource_id or "",
                user_id=user_id or "",
            )
            return {"status": "failed", "error": error_msg, "columns": [], "data": [], "total_count": 0}

        # 使用专用线程池执行同步操作（Hive 查询通常较慢，默认60秒超时）
        loop = asyncio.get_event_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(
                    _HIVE_EXECUTOR_THREAD_POOL,
                    self._execute_sync,
                    sql,
                    limit,
                    datasource_id or "",
                    user_id or "",
                    max_rows,
                ),
                timeout=resolved_timeout,
            )
        except TimeoutError:
            logger.error(f"[Hive] 执行 SQL 超时（{resolved_timeout}s）")
            return {
                "status": "failed",
                "error": f"查询超时（{resolved_timeout}秒）",
                "columns": [],
                "data": [],
                "total_count": 0,
            }

    def _execute_sync(
        self, sql: str, limit: int, datasource_id: str = "", user_id: str = "", max_rows: int | None = None
    ) -> dict[str, Any]:
        """同步执行 SQL（在线程池中运行；max_rows 为异步侧解析的热值）"""
        import time

        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            sql_with_limit = build_effective_sql(
                sql, limit, source_type="hive", datasource_id=datasource_id, user_id=user_id, max_rows=max_rows
            )
            logger.debug(f"[Hive] 执行 SQL: {sql_with_limit[:200]}...")
            start_time = time.time()
            cursor.execute(sql_with_limit)
            rows = cursor.fetchall()
            execution_time_ms = int((time.time() - start_time) * 1000)
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            data = [_serialize_row(dict(zip(columns, row, strict=False))) for row in rows]
            _log_sql_execution_summary("Hive", self.database, execution_time_ms, len(data))
            return {
                "status": "succeeded",
                "columns": columns,
                "data": data,
                "total_count": len(data),
                "execution_time_ms": execution_time_ms,
            }
        except Exception as e:
            if isinstance(e, ValueError):
                logger.warning("[Hive] SQL 执行参数非法: %s", e)
                return {"status": "failed", "error": str(e), "columns": [], "data": [], "total_count": 0}
            _emit_query_database_connection_alarm(
                "hive",
                self.database,
                e,
                host=self.host,
                port=self.port,
                datasource_id=datasource_id,
                user_id=user_id,
            )
            logger.error(f"[Hive] 执行 SQL 失败: {e}")
            return {
                "status": "failed",
                "error": _friendly_sql_error("Hive", e),
                "columns": [],
                "data": [],
                "total_count": 0,
            }
        finally:
            if conn:
                conn.close()

    async def test_connection(self) -> bool:
        """测试 Hive 连接"""
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(_HIVE_EXECUTOR_THREAD_POOL, self._test_sync)
        except Exception as e:
            _emit_query_database_connection_alarm("hive", self.database, e, host=self.host, port=self.port)
            logger.error(f"[Hive] 连接测试失败: {e}")
            return False

    def _test_sync(self) -> bool:
        """同步测试连接"""
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            return True
        finally:
            if conn:
                conn.close()

    async def close(self):
        """Hive 每次创建新连接，无需关闭池"""
        pass


class HiveHttpExecutor(SQLExecutor):
    """HTTP 执行器，提交远端异步任务并轮询结果。"""

    def __init__(
        self, namespace: str, source_type: str = "hive", database: str = "", org_code: str = "", org_name: str = ""
    ):
        self.namespace = str(namespace or "").strip()
        self.source_type = str(source_type or "hive").strip().lower() or "hive"
        self.database = str(database or "").strip()
        self.org_code = str(org_code or "").strip()
        self.org_name = str(org_name or "").strip()

    def _build_headers(self) -> dict[str, str]:
        return build_http_auth_headers(HTTP_AUTH_TOKEN)

    def _render_url(self, template: str, task_id: str) -> str:
        if "{task_id}" in template:
            return template.format(task_id=task_id)
        return template

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        json_payload: dict[str, Any] | None = None,
        task_id: str = "",
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request_kwargs: dict[str, Any] = {}
        if method.upper() == "GET":
            query_params = dict(params or {})
            if task_id and "{task_id}" not in url:
                query_params["task_id"] = task_id
            if query_params:
                request_kwargs["params"] = query_params
        else:
            request_kwargs["json"] = json_payload or {}
        request_url = self._render_url(url, task_id)
        logger.info(
            "[HTTPExecutor] 请求对方接口: method=%s url=%s task_id=%s params=%s payload=%s",
            method.upper(),
            request_url,
            task_id,
            request_kwargs.get("params") or {},
            _http_payload_summary(request_kwargs.get("json")),
        )
        try:
            async with open_http_client(
                timeout_seconds=max(CONNECT_TIMEOUT_SECONDS, await resolve_query_timeout(self.source_type)),
                headers=self._build_headers(),
            ) as client:
                response = await client.request(method.upper(), request_url, **request_kwargs)
                response.raise_for_status()
                payload = response.json()
                logger.debug(
                    "[HTTPExecutor] 对方接口返回: method=%s url=%s status_code=%s result=%s",
                    method.upper(),
                    request_url,
                    getattr(response, "status_code", None),
                    _http_response_summary(payload),
                )
                return _extract_response_payload(payload)
        except Exception as exc:
            emit_external_api_alarm(
                "chat_data_http_query",
                exc,
                method=method.upper(),
                url=request_url,
                ext1={
                    "task_id": task_id,
                    "params": request_kwargs.get("params") or {},
                    "payload": _http_payload_summary(request_kwargs.get("json")),
                },
            )
            raise

    def _build_submit_payload(
        self, sql: str, limit: int, user_id: str | None = None, username: str | None = None
    ) -> dict[str, Any]:
        payload = {
            "sql": sql,
            "database": self.database,
            "orgCode": self.org_code,
            "orgName": self.org_name,
            "limit": limit,
        }
        add_http_user_params(payload, user_id, username)
        return payload

    def _normalize_result(self, payload: dict[str, Any]) -> dict[str, Any]:
        result_payload = _extract_result_payload(payload)
        raw_rows = result_payload.get("data")
        if not isinstance(raw_rows, list):
            raw_rows = result_payload.get("rows")
        if not isinstance(raw_rows, list):
            raw_rows = result_payload.get("records")
        raw_columns = result_payload.get("columns")
        columns = _normalize_http_columns(raw_columns, raw_rows if isinstance(raw_rows, list) else [])
        data = _normalize_http_rows(raw_rows, columns)
        total_count = result_payload.get("total_count")
        if total_count is None:
            total_count = result_payload.get("row_count")
        if total_count is None:
            total_count = len(data)
        execution_times = _build_http_execution_times(payload, result_payload)
        return {
            "status": "succeeded",
            "columns": columns,
            "data": data,
            "total_count": int(total_count or 0),
            **execution_times,
        }

    async def _notify_remote_task_created(
        self,
        remote_task_callback: Callable[[dict[str, Any]], Any] | None,
        remote_task_id: str,
    ) -> None:
        """在 HTTP 任务提交成功后通知上层记录远端任务映射。"""
        if remote_task_callback is None:
            return
        callback_result = remote_task_callback(
            {
                "remote_task_id": remote_task_id,
                "namespace": self.namespace,
                "source_type": self.source_type,
            }
        )
        if inspect.isawaitable(callback_result):
            await callback_result

    async def execute(
        self,
        sql: str,
        limit: int = DEFAULT_ROW_LIMIT,
        timeout: int | None = None,
        user_id: str | None = None,
        username: str | None = None,
        datasource_id: str | None = None,
        remote_task_callback: Callable[[dict[str, Any]], Any] | None = None,
        db_connection_callback: Callable[[dict[str, Any]], Any] | None = None,
    ) -> dict[str, Any]:
        resolved_timeout = await resolve_query_timeout(self.source_type, timeout)
        # 行数上限热值：校验与 LIMIT 补全/终检共用同一运行时配置
        max_rows = await get_runtime_setting("CHATDATA_ROW_LIMIT")
        # 前置检查保持在 try 之外：异常直接向上传播，不落入兜底 except 转成错误结果（与拆分前行为一致）
        validation_error = self._http_validation_error(
            sql, datasource_id=datasource_id, user_id=user_id, max_rows=max_rows
        )
        if validation_error:
            return validation_error
        if not self.namespace:
            return _http_error_result("缺少 HTTP 执行标识，请配置机构编码")
        config_error = self._http_config_error()
        if config_error:
            return config_error
        try:
            submit_error, task_id = await self._submit_http_query(
                sql, limit, user_id, username, remote_task_callback, datasource_id=datasource_id, max_rows=max_rows
            )
            if submit_error:
                return submit_error
            return await self._poll_http_result(task_id, resolved_timeout, user_id, username)
        except TimeoutError:
            logger.error("[HTTPExecutor] 执行 SQL 超时（%ss）", resolved_timeout)
            return _http_error_result(f"查询超时（{resolved_timeout}秒）")
        except ValueError as e:
            logger.warning("[HTTPExecutor] SQL 执行参数非法: %s", e)
            return _http_error_result(str(e))
        except Exception as e:
            logger.error("[HTTPExecutor] 执行 SQL 失败: %s", e)
            engine_name = "MySQL" if self.source_type == "mysql" else "Hive"
            return _http_error_result(_friendly_sql_error(engine_name, e))

    def _http_validation_error(
        self,
        sql: str,
        *,
        datasource_id: str | None = None,
        user_id: str | None = None,
        max_rows: int | None = None,
    ) -> dict[str, Any] | None:
        """SQL 校验失败时告警并返回错误结果；合法返回 None。"""
        is_valid, error_msg = validate_sql(sql, source_type=self.source_type, max_rows=max_rows)
        if is_valid:
            return None
        _emit_sql_execution_validation_alarm(
            sql,
            error_msg,
            source_type=self.source_type,
            execution_scope="http_executor",
            datasource_id=datasource_id or "",
            user_id=user_id or "",
        )
        return _http_error_result(error_msg)

    @staticmethod
    def _http_config_error() -> dict[str, Any] | None:
        """执行前置配置检查；缺配置返回错误结果，否则 None。"""
        if not HTTP_SUBMIT_URL or not HTTP_STATUS_URL:
            return _http_error_result(
                "未配置 HTTP 查询接口，请检查 CHAT_DATA_HTTP_SUBMIT_URL/CHAT_DATA_HTTP_STATUS_URL"
            )
        return None

    async def _submit_http_query(
        self,
        sql: str,
        limit: int,
        user_id: str | None,
        username: str | None,
        remote_task_callback: Callable[[dict[str, Any]], Any] | None,
        datasource_id: str | None = None,
        max_rows: int | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        """提交查询并认领远端任务；返回 (错误结果, None) 或 (None, task_id)。"""
        sql_with_limit = build_effective_sql(
            sql, limit, source_type=self.source_type, datasource_id=datasource_id, user_id=user_id, max_rows=max_rows
        )
        submit_payload = await self._request_json(
            "POST",
            HTTP_SUBMIT_URL,
            json_payload=self._build_submit_payload(sql_with_limit, limit, user_id, username),
        )
        submit_code = _extract_remote_code(submit_payload)
        if submit_code is not None and submit_code != 20000:
            return _http_error_result(_extract_remote_message(submit_payload, "HTTP 查询提交失败")), None
        task_id = _extract_task_id(submit_payload)
        if not task_id:
            return _http_error_result(
                _extract_remote_message(submit_payload, "Hive HTTP 提交成功但未返回任务 ID")
            ), None
        await self._notify_remote_task_created(remote_task_callback, task_id)
        return None, task_id

    async def _poll_http_result(
        self,
        task_id: str,
        resolved_timeout: int,
        user_id: str | None,
        username: str | None,
    ) -> dict[str, Any]:
        """轮询远端任务直到成功、失败或超时，返回结构化结果。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + resolved_timeout
        # 轮询间隔运行期经后台配置变量表读取（CHAT_DATA_HTTP_POLL_INTERVAL_MS），每轮循环取最新值
        poll_interval_ms = await get_runtime_setting("CHAT_DATA_HTTP_POLL_INTERVAL_MS")
        while True:
            if loop.time() >= deadline:
                return _http_error_result(f"查询超时（{resolved_timeout}秒）")
            status_params = add_http_user_params({}, user_id, username)
            if status_params:
                status_payload = await self._request_json("GET", HTTP_STATUS_URL, task_id=task_id, params=status_params)
            else:
                status_payload = await self._request_json("GET", HTTP_STATUS_URL, task_id=task_id)
            status_code = _extract_remote_code(status_payload)
            if status_code is not None and status_code != 20000:
                return _http_error_result(
                    _extract_remote_message(status_payload, "HTTP 查询失败"),
                    **_build_http_execution_times(status_payload, {}),
                )
            normalized_status = _extract_remote_status(status_payload)
            if normalized_status == "succeeded":
                result = self._normalize_result(status_payload)
                result["remote_task_id"] = task_id
                result["source_type"] = self.source_type
                return result
            if normalized_status in ("cancelled", "failed"):
                return self._cancelled_or_failed_result(status_payload, normalized_status)
            await asyncio.sleep(max(0.2, poll_interval_ms / 1000))

    def _cancelled_or_failed_result(self, status_payload: dict[str, Any], normalized_status: str) -> dict[str, Any]:
        default_message = "查询已取消" if normalized_status == "cancelled" else "HTTP 查询失败"
        return _http_error_result(
            _extract_remote_message(status_payload, default_message),
            status=normalized_status,
            **_build_http_execution_times(status_payload, {}),
        )

    async def test_connection(self) -> bool:
        """Hive HTTP 模式不再校验直连，仅校验必要配置。"""
        return bool(HTTP_SUBMIT_URL and HTTP_STATUS_URL)

    async def close(self):
        """HTTP 客户端按请求创建，无需显式关闭。"""
        pass


async def fetch_http_query_task_result(
    remote_task_id: str, source_type: str = "hive", user_id: str | None = None, username: str | None = None
) -> dict[str, Any]:
    """按远端任务 ID 查询 HTTP 任务状态与结果。"""
    executor = HiveHttpExecutor(namespace="_remote_task_lookup_", source_type=source_type)
    status_params = add_http_user_params({}, user_id, username)
    if status_params:
        payload = await executor._request_json("GET", HTTP_STATUS_URL, task_id=remote_task_id, params=status_params)
    else:
        payload = await executor._request_json("GET", HTTP_STATUS_URL, task_id=remote_task_id)
    status_code = _extract_remote_code(payload)
    if status_code is not None and status_code != 20000:
        normalized_status = _extract_remote_status(payload)
        return _http_error_result(
            _extract_remote_message(payload, "HTTP 查询失败"),
            status=normalized_status or "failed",
            **_build_http_execution_times(payload, {}),
        )

    normalized_status = _extract_remote_status(payload)
    if normalized_status == "succeeded":
        result = executor._normalize_result(payload)
        return {
            "status": "succeeded",
            "columns": result.get("columns") or [],
            "data": result.get("data") or [],
            "total_count": int(result.get("total_count") or 0),
            "execution_time_ms": result.get("execution_time_ms"),
            "started_time": result.get("started_time"),
            "finished_time": result.get("finished_time"),
            "error": None,
        }
    if normalized_status in {"failed", "cancelled"}:
        return _http_error_result(
            _extract_remote_message(payload, "查询已取消" if normalized_status == "cancelled" else "HTTP 查询失败"),
            status=normalized_status,
            **_build_http_execution_times(payload, {}),
        )
    return _http_error_result(None, status=normalized_status)


async def stop_http_query_task(
    remote_task_id: str, source_type: str = "hive", user_id: str | None = None, username: str | None = None
) -> dict[str, Any]:
    """按远端任务 ID 调用 HTTP 停止接口。"""
    normalized_task_id = str(remote_task_id or "").strip()
    if not normalized_task_id:
        return {"status": "failed", "error": "缺少远端任务ID"}
    if not HTTP_STOP_URL:
        return {"status": "failed", "error": "未配置 HTTP 停止接口，请检查 CHAT_DATA_HTTP_STOP_URL"}
    executor = HiveHttpExecutor(namespace="_remote_task_cancel_", source_type=source_type)
    try:
        stop_payload = add_http_user_params({"task_id": normalized_task_id}, user_id, username)
        payload = await executor._request_json("POST", HTTP_STOP_URL, json_payload=stop_payload)
        status_code = _extract_remote_code(payload)
        if status_code is not None and status_code != 20000:
            return {"status": "failed", "error": _extract_remote_message(payload, "HTTP 停止任务失败")}
        return {"status": "succeeded", "error": None, "payload": payload}
    except Exception as e:
        logger.error("[HTTPExecutor] 停止任务失败: task_id=%s error=%s", normalized_task_id, e)
        return {"status": "failed", "error": _friendly_sql_error("HTTP", e)}


async def kill_mysql_query(datasource: dict, db_connection_id: str) -> dict[str, Any]:
    """按数据源配置停止 MySQL 连接上的当前查询。"""
    executor = None
    try:
        executor = get_executor(datasource)
        if not isinstance(executor, MySQLExecutor):
            return {"status": "failed", "error": "当前执行器不支持直连停止"}
        return await executor.kill_query(db_connection_id)
    except Exception as e:
        logger.error("[MySQL] 停止查询初始化失败: error=%s", e)
        return {"status": "failed", "error": str(e) or "停止查询失败"}
    finally:
        if executor is not None:
            await executor.close()


def get_executor(datasource: dict) -> SQLExecutor:
    """
    根据数据源配置返回对应的执行器

    Args:
        datasource: 数据源配置字典，包含 source_type 和 source_config

    Returns:
        SQLExecutor 实例

    Raises:
        ValueError: 不支持的数据源类型
    """
    source_type = datasource.get("source_type")
    query_executor_type = str(datasource.get("query_executor_type") or "direct").strip().lower() or "direct"
    config = resolve_execution_source_config(
        source_type,
        query_executor_type,
        datasource.get("source_config", {}),
        org_code=datasource.get("org_code") or "",
        org_name="",
    )

    if query_executor_type == "http":
        org_code = str(config.get("org_code") or "").strip()
        return HiveHttpExecutor(
            namespace=org_code,
            source_type=source_type,
            database=str(config.get("database") or "").strip(),
            org_code=org_code,
            org_name=str(config.get("org_name") or "").strip(),
        )

    if source_type == "mysql":
        return MySQLExecutor(
            host=config.get("host"),
            port=config.get("port", 3306),
            database=config.get("database"),
            user=config.get("user"),
            password=config.get("password"),
        )
    elif source_type == "hive":
        return HiveExecutor(
            host=config.get("host"),
            port=config.get("port", 10000),
            database=config.get("database", "default"),
            username=config.get("username"),
            password=config.get("password"),
        )
    else:
        raise ValueError(f"不支持的数据源类型: {source_type}，只支持 mysql/hive")
