"""
SQL 执行器模块

支持 MySQL、Hive 的异步查询执行
通过环境变量配置 SQL 安全限制和结果行数限制
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Tuple, Optional, Callable
from decimal import Decimal
from datetime import date, datetime, timedelta
import asyncio
import inspect
import re
import os

from utils.logger import get_logger
from utils.crypto import decrypt_password
from utils.alarm import emit_dangerous_sql_alarm, emit_external_api_alarm, emit_database_connection_alarm
from utils.db_failover import is_mysql_connection_error
from .datasource_credentials import build_hive_connect_params, resolve_hive_credentials, resolve_mysql_credentials
from .hive_sync import _apply_socket_timeout, SOCKET_TIMEOUT_SECONDS
from .runtime_config import DEFAULT_ROW_LIMIT, normalize_row_limit
from .sql_parser import extract_top_level_limit_count, validate_raw_single_sql_statement

logger = get_logger(__name__)

# ============ 可配置参数（从环境变量读取） ============

DEFAULT_MYSQL_QUERY_TIMEOUT_SECONDS = int(os.getenv("CHAT_DATA_MYSQL_QUERY_TIMEOUT_SECONDS", "60"))
DEFAULT_HIVE_QUERY_TIMEOUT_SECONDS = int(os.getenv("CHAT_DATA_HIVE_QUERY_TIMEOUT_SECONDS", "600"))
DEFAULT_HTTP_POLL_INTERVAL_MS = int(os.getenv("CHAT_DATA_HTTP_POLL_INTERVAL_MS", "1500"))

# 允许的 SQL 操作类型（从环境变量读取，默认仅 SELECT）
#
# SHOW / EXPLAIN / DESCRIBE 属于元数据或执行计划类命令；Hive 方言下 sqlglot
# 对部分语法会降级为 COMMAND，暂不复用通用 SQL 执行口放行。后续如需支持，
# 应单独提供受控接口并做权限、审计和结果脱敏。
_ALLOWED_SQL_TYPES = os.getenv("CHATDATA_ALLOWED_SQL", "SELECT")
ALLOWED_SQL_TYPES = [s.strip().upper() for s in _ALLOWED_SQL_TYPES.split(",") if s.strip()]
CONNECT_TIMEOUT_SECONDS = 3
HTTP_SUBMIT_URL = str(os.getenv("CHAT_DATA_HTTP_SUBMIT_URL") or "").strip()
HTTP_STATUS_URL = str(os.getenv("CHAT_DATA_HTTP_STATUS_URL") or "").strip()
HTTP_STOP_URL = str(os.getenv("CHAT_DATA_HTTP_STOP_URL") or "").strip()
HTTP_VERIFY_TLS = str(os.getenv("CHAT_DATA_HTTP_VERIFY_TLS", "false")).strip().lower() in {"1", "true", "yes", "on"}
JS_SAFE_INTEGER_MAX = 2**53 - 1


def _get_http_auth_token() -> str:
    """读取 ChatData HTTP 认证 token，环境变量中存储 SM4 密文。"""
    encrypted_token = str(os.getenv("CHAT_DATA_HTTP_AUTH_TOKEN") or "").strip()
    if not encrypted_token:
        return ""
    return decrypt_password(encrypted_token)


HTTP_AUTH_TOKEN = _get_http_auth_token()


def _http_payload_summary(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
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


def _http_response_summary(payload: Any) -> Dict[str, Any]:
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
        "task_id": _first_not_none(result.get("task_id"), result.get("taskId"), payload.get("task_id"), payload.get("taskId")),
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


def resolve_query_timeout(source_type: str, timeout: Optional[int] = None) -> int:
    """解析查询超时时间，优先使用显式传参，其次按数据源类型读取默认值。"""
    if timeout is not None:
        normalized_timeout = int(timeout)
        if normalized_timeout > 0:
            return normalized_timeout

    normalized_source_type = str(source_type or "").strip().lower()
    if normalized_source_type == "hive":
        return DEFAULT_HIVE_QUERY_TIMEOUT_SECONDS
    if normalized_source_type == "mysql":
        return DEFAULT_MYSQL_QUERY_TIMEOUT_SECONDS
    return DEFAULT_MYSQL_QUERY_TIMEOUT_SECONDS


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
        return value.decode('utf-8', errors='replace')
    return value


def _serialize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """序列化整行数据"""
    return {k: _serialize_value(v) for k, v in row.items()}


def _extract_response_payload(payload: Any) -> Dict[str, Any]:
    """提取接口响应主体。"""
    return payload if isinstance(payload, dict) else {}


def _extract_remote_data(payload: Dict[str, Any]) -> Dict[str, Any]:
    """提取远端 data 节点。"""
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def _extract_task_id(payload: Dict[str, Any]) -> str:
    """从提交响应中提取任务 ID。"""
    for source in (payload, _extract_remote_data(payload)):
        if not isinstance(source, dict):
            continue
        for key in ("task_id", "job_id", "id"):
            value = str(source.get(key) or "").strip()
            if value:
                return value
    return ""


def _extract_remote_code(payload: Dict[str, Any]) -> Optional[int]:
    """提取远端业务码。"""
    if not isinstance(payload, dict) or "code" not in payload:
        return None
    try:
        return int(payload.get("code"))
    except (TypeError, ValueError):
        return None


def _extract_remote_message(payload: Dict[str, Any], default: str = "") -> str:
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


def _extract_remote_status(payload: Dict[str, Any]) -> str:
    """提取远端任务状态。"""
    data_payload = _extract_remote_data(payload)
    return _normalize_remote_task_status(data_payload.get("status") or payload.get("status"))


def _extract_result_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
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


def _normalize_http_execution_time_ms(payload: Dict[str, Any], result_payload: Dict[str, Any]) -> Optional[int]:
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


def _parse_http_start_time(payload: Dict[str, Any], result_payload: Dict[str, Any]) -> Optional[datetime]:
    """解析远端开始时间，业务接口格式为 yyyy-MM-dd HH:mm:ss。"""
    for source in (result_payload, _extract_remote_data(payload), payload):
        if not isinstance(source, dict):
            continue
        raw_value = source.get("startTime") or source.get("started_time") or source.get("start_time")
        if isinstance(raw_value, datetime):
            return raw_value
        text = str(raw_value or "").strip()
        if not text:
            continue
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(text[:19], fmt)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            continue
    return None


def _build_http_execution_times(payload: Dict[str, Any], result_payload: Dict[str, Any]) -> Dict[str, Any]:
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


def _normalize_http_columns(raw_columns: Any, rows: list[dict[str, Any]]) -> list[str]:
    """标准化远端返回的列结构。"""
    if isinstance(raw_columns, list):
        result: list[str] = []
        for item in raw_columns:
            if isinstance(item, str):
                normalized = item.strip()
                if normalized:
                    result.append(normalized)
            elif isinstance(item, dict):
                normalized = str(item.get("name") or item.get("column_name") or "").strip()
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
            result.append(_serialize_row(dict(zip(columns, row))))
    return result


def _get_sql_operation_type(sql: str, source_type: Optional[str] = None) -> str:
    """解析 SQL 类型，用于白名单校验，不改写 SQL。"""
    try:
        from sqlglot import exp
        from .sql_parser import _parse_sql
    except Exception:
        return ""

    try:
        expressions = _parse_sql(sql, source_type)
    except Exception:
        return ""
    if len(expressions) != 1:
        return ""
    expression = expressions[0]
    if isinstance(expression, exp.Query):
        return "SELECT"
    if isinstance(expression, exp.Show):
        return "SHOW"
    if isinstance(expression, exp.Describe):
        text = str(sql or "").lstrip().upper()
        return "EXPLAIN" if text.startswith("EXPLAIN") else "DESCRIBE"
    key = str(getattr(expression, "key", "") or "").strip().upper()
    return key


def validate_sql(sql: str, source_type: Optional[str] = None) -> Tuple[bool, str]:
    """
    验证 SQL 是否安全

    Args:
        sql: SQL 语句

    Returns:
        (is_valid, error_message) - 如果验证通过返回 (True, "")，否则返回 (False, 错误信息)
    """
    statement_result = validate_raw_single_sql_statement(sql, source_type=source_type)
    if not statement_result.success:
        return False, statement_result.error or "SQL 解析失败"

    sql_operation_type = _get_sql_operation_type(statement_result.sql or sql, source_type=source_type)
    if sql_operation_type and sql_operation_type in ALLOWED_SQL_TYPES:
        return True, ""

    allowed_str = ", ".join(ALLOWED_SQL_TYPES)
    return False, f"只允许执行以下类型的 SQL: {allowed_str}"


def _should_auto_add_limit(sql: str, source_type: Optional[str] = None) -> bool:
    """判断 SQL 是否需要自动追加 LIMIT。"""
    normalized_sql = str(sql or "").strip().lstrip("(").strip()
    if not normalized_sql:
        return False
    if extract_top_level_limit_count(normalized_sql, source_type=source_type) is not None:
        return False
    return _get_sql_operation_type(normalized_sql, source_type=source_type) == "SELECT"


def _line_has_comment_marker(line: str) -> bool:
    """判断单行非字符串位置是否存在 -- 注释。"""
    in_quote: Optional[str] = None
    i = 0
    while i < len(line):
        char = line[i]
        next_char = line[i + 1] if i + 1 < len(line) else ""
        if in_quote:
            if char == "\\":
                i += 2
                continue
            if char == in_quote:
                if in_quote == "'" and next_char == "'":
                    i += 2
                    continue
                in_quote = None
            i += 1
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


def _add_limit_if_needed(sql: str, limit: int, source_type: Optional[str] = None) -> str:
    """仅对查询型 SQL 自动追加 LIMIT。"""
    normalized_limit = normalize_row_limit(limit)
    top_level_limit_count = extract_top_level_limit_count(str(sql or ""), source_type=source_type)
    if top_level_limit_count is not None and top_level_limit_count > DEFAULT_ROW_LIMIT:
        raise ValueError(f"查询结果最多返回 {DEFAULT_ROW_LIMIT} 条，请调整 SQL LIMIT")
    return _append_limit(sql, normalized_limit) if _should_auto_add_limit(sql, source_type=source_type) else sql


def normalize_sql_aliases(sql: str) -> str:
    """
    统一给 SQL 中的中文标识符加反引号，兼容 MySQL 和 Hive

    处理场景：
    1. AS 后的别名：as 中文 → as `中文`
    2. ORDER BY 中的列名：ORDER BY 中文 → ORDER BY `中文`
    3. GROUP BY 中的列名：GROUP BY 中文 → GROUP BY `中文`

    Args:
        sql: 原始 SQL 语句

    Returns:
        处理后的 SQL 语句
    """
    # 1. 处理 AS 后面的别名（所有别名都加反引号）
    # 不匹配：as `alias`（已有反引号）, as 'alias'（字符串）, as "alias"（双引号）
    as_pattern = r'\bAS\s+(?!`|\'|")([a-zA-Z_\u4e00-\u9fa5][a-zA-Z0-9_\u4e00-\u9fa5]*)\b'
    sql = re.sub(as_pattern, lambda m: f'AS `{m.group(1)}`', sql, flags=re.IGNORECASE)

    # 2. 处理 ORDER BY 中的中文列名（仅处理包含中文的）
    # 匹配：ORDER BY 中文列名 或 ORDER BY 中文列名 DESC/ASC
    order_pattern = r'\bORDER\s+BY\s+(?!`)([\u4e00-\u9fa5][a-zA-Z0-9_\u4e00-\u9fa5]*)\b'
    sql = re.sub(order_pattern, lambda m: f'ORDER BY `{m.group(1)}`', sql, flags=re.IGNORECASE)

    # 3. 处理 GROUP BY 中的中文列名
    group_pattern = r'\bGROUP\s+BY\s+(?!`)([\u4e00-\u9fa5][a-zA-Z0-9_\u4e00-\u9fa5]*)\b'
    sql = re.sub(group_pattern, lambda m: f'GROUP BY `{m.group(1)}`', sql, flags=re.IGNORECASE)

    return sql


def build_effective_sql(sql: str, limit: int = DEFAULT_ROW_LIMIT, source_type: Optional[str] = None) -> str:
    """生成真实执行 SQL：仅按需追加 LIMIT。"""
    return _add_limit_if_needed(sql, limit, source_type=source_type)


def _log_sql_execution_summary(source_type: str, database: str, execution_time_ms: int, row_count: int) -> None:
    """记录不含 SQL 明文的执行摘要。"""
    logger.info("[%s] SQL 执行完成: database=%s elapsed_ms=%s rows=%s", source_type, database, execution_time_ms, row_count)


def _emit_sql_execution_validation_alarm(sql: str, error_msg: str, *, source_type: str, execution_scope: str) -> None:
    """记录执行边界拦截的高危 SQL。"""
    emit_dangerous_sql_alarm(
        sql,
        error_msg,
        source_type=source_type,
        execution_scope=execution_scope,
    )


def _emit_query_database_connection_alarm(
    source_type: str,
    database: str,
    err: Exception,
    *,
    host: str | None = None,
    port: int | None = None,
) -> None:
    """记录查询数据源连接类异常。"""
    normalized_source_type = str(source_type or "").strip().lower()
    if normalized_source_type == "mysql" and not is_mysql_connection_error(err):
        return
    if normalized_source_type == "hive" and not _is_hive_connection_exception(err):
        return
    emit_database_connection_alarm(
        "查询数据源连接异常",
        ext1={
            "source_type": normalized_source_type,
            "host": host or "",
            "port": port or "",
            "database": database or "",
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


def _add_hive_http_user_params(payload: Dict[str, Any], user_id: Optional[str] = None, username: Optional[str] = None) -> Dict[str, Any]:
    """补充 Hive 外部 HTTP 请求的用户参数。"""
    normalized_user_id = str(user_id or "").strip()
    normalized_username = str(username or "").strip()
    if normalized_user_id:
        payload["userId"] = normalized_user_id
    if normalized_username:
        payload["username"] = normalized_username
    return payload


class SQLExecutor(ABC):
    """SQL 执行器抽象基类"""

    @abstractmethod
    async def execute(
        self,
        sql: str,
        limit: int = DEFAULT_ROW_LIMIT,
        timeout: Optional[int] = None,
        user_id: Optional[str] = None,
        remote_task_callback: Optional[Callable[[Dict[str, Any]], Any]] = None,
        db_connection_callback: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> Dict[str, Any]:
        """
        执行 SQL 并返回结果

        Args:
            sql: SQL 查询语句
            limit: 结果行数限制
            timeout: 查询超时时间（秒）

        Returns:
            {
                "success": bool,
                "columns": List[str],  # 列名列表
                "data": List[Dict],    # 数据行列表
                "total_count": int,    # 返回的行数
                "error": str           # 错误信息（仅当 success=False）
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
                charset='utf8mb4',
                autocommit=True,
                minsize=1,
                maxsize=5,
                connect_timeout=CONNECT_TIMEOUT_SECONDS
            )
        return self._pool

    async def execute(
        self,
        sql: str,
        limit: int = DEFAULT_ROW_LIMIT,
        timeout: Optional[int] = None,
        user_id: Optional[str] = None,
        remote_task_callback: Optional[Callable[[Dict[str, Any]], Any]] = None,
        db_connection_callback: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> Dict[str, Any]:
        import time
        resolved_timeout = resolve_query_timeout("mysql", timeout)
        # SQL 安全验证
        is_valid, error_msg = validate_sql(sql, source_type="mysql")
        if not is_valid:
            _emit_sql_execution_validation_alarm(sql, error_msg, source_type="mysql", execution_scope="mysql_executor")
            return {"success": False, "error": error_msg, "columns": [], "data": [], "total_count": 0}

        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    connection_id = ""
                    await cursor.execute("SELECT CONNECTION_ID()")
                    connection_row = await cursor.fetchone()
                    if connection_row:
                        connection_id = str(connection_row[0] if isinstance(connection_row, (list, tuple)) else connection_row)
                    if connection_id and db_connection_callback is not None:
                        callback_result = db_connection_callback({"db_connection_id": connection_id})
                        if inspect.isawaitable(callback_result):
                            await callback_result

                    sql_with_limit = build_effective_sql(sql, limit, source_type="mysql")
                    logger.debug(f"[MySQL] 执行 SQL: {sql_with_limit[:200]}...")
                    # 添加超时控制
                    start_time = time.time()
                    await asyncio.wait_for(
                        cursor.execute(sql_with_limit),
                        timeout=resolved_timeout
                    )
                    rows = await cursor.fetchall()
                    execution_time_ms = int((time.time() - start_time) * 1000)
                    columns = [desc[0] for desc in cursor.description] if cursor.description else []
                    data = [_serialize_row(dict(zip(columns, row))) for row in rows]
                    _log_sql_execution_summary("MySQL", self.database, execution_time_ms, len(data))
                    return {
                        "success": True,
                        "columns": columns,
                        "data": data,
                        "total_count": len(data),
                        "execution_time_ms": execution_time_ms,
                        "db_connection_id": connection_id,
                    }
        except asyncio.TimeoutError:
            logger.error(f"[MySQL] 执行 SQL 超时（{resolved_timeout}s）")
            return {"success": False, "error": f"查询超时（{resolved_timeout}秒）", "columns": [], "data": [], "total_count": 0}
        except ValueError as e:
            logger.warning("[MySQL] SQL 执行参数非法: %s", e)
            return {"success": False, "error": str(e), "columns": [], "data": [], "total_count": 0}
        except Exception as e:
            _emit_query_database_connection_alarm("mysql", self.database, e, host=self.host, port=self.port)
            logger.error(f"[MySQL] 执行 SQL 失败: {e}")
            return {"success": False, "error": _friendly_sql_error("MySQL", e), "columns": [], "data": [], "total_count": 0}

    async def test_connection(self) -> bool:
        """测试 MySQL 连接"""
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute("SELECT 1")
                    return True
        except Exception as e:
            _emit_query_database_connection_alarm("mysql", self.database, e, host=self.host, port=self.port)
            logger.error(f"[MySQL] 连接测试失败: {e}")
            return False

    async def kill_query(self, connection_id: str) -> Dict[str, Any]:
        """使用独立连接停止指定 MySQL 会话上的当前查询。"""
        normalized_connection_id = str(connection_id or "").strip()
        if not normalized_connection_id.isdigit():
            return {"success": False, "error": "无效的 MySQL 连接ID"}
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(f"KILL QUERY {normalized_connection_id}")
            return {"success": True, "error": None}
        except Exception as e:
            logger.error("[MySQL] 停止查询失败: connection_id=%s error=%s", normalized_connection_id, e)
            return {"success": False, "error": _friendly_sql_error("MySQL", e)}

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
        timeout: Optional[int] = None,
        user_id: Optional[str] = None,
        remote_task_callback: Optional[Callable[[Dict[str, Any]], Any]] = None,
        db_connection_callback: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> Dict[str, Any]:
        resolved_timeout = resolve_query_timeout("hive", timeout)
        # SQL 安全验证
        is_valid, error_msg = validate_sql(sql, source_type="hive")
        if not is_valid:
            _emit_sql_execution_validation_alarm(sql, error_msg, source_type="hive", execution_scope="hive_executor")
            return {"success": False, "error": error_msg, "columns": [], "data": [], "total_count": 0}

        # 使用线程池执行同步操作（Hive 查询通常较慢，默认60秒超时）
        loop = asyncio.get_event_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, self._execute_sync, sql, limit),
                timeout=resolved_timeout
            )
        except asyncio.TimeoutError:
            logger.error(f"[Hive] 执行 SQL 超时（{resolved_timeout}s）")
            return {"success": False, "error": f"查询超时（{resolved_timeout}秒）", "columns": [], "data": [], "total_count": 0}

    def _execute_sync(self, sql: str, limit: int) -> Dict[str, Any]:
        """同步执行 SQL（在线程池中运行）"""
        import time
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            sql_with_limit = build_effective_sql(sql, limit, source_type="hive")
            logger.debug(f"[Hive] 执行 SQL: {sql_with_limit[:200]}...")
            start_time = time.time()
            cursor.execute(sql_with_limit)
            rows = cursor.fetchall()
            execution_time_ms = int((time.time() - start_time) * 1000)
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            data = [_serialize_row(dict(zip(columns, row))) for row in rows]
            _log_sql_execution_summary("Hive", self.database, execution_time_ms, len(data))
            return {
                "success": True,
                "columns": columns,
                "data": data,
                "total_count": len(data),
                "execution_time_ms": execution_time_ms
            }
        except Exception as e:
            if isinstance(e, ValueError):
                logger.warning("[Hive] SQL 执行参数非法: %s", e)
                return {"success": False, "error": str(e), "columns": [], "data": [], "total_count": 0}
            _emit_query_database_connection_alarm("hive", self.database, e, host=self.host, port=self.port)
            logger.error(f"[Hive] 执行 SQL 失败: {e}")
            return {"success": False, "error": _friendly_sql_error("Hive", e), "columns": [], "data": [], "total_count": 0}
        finally:
            if conn:
                conn.close()

    async def test_connection(self) -> bool:
        """测试 Hive 连接"""
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(None, self._test_sync)
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

    def __init__(self, namespace: str, source_type: str = "hive", database: str = "", org_code: str = "", org_name: str = ""):
        self.namespace = str(namespace or "").strip()
        self.source_type = str(source_type or "hive").strip().lower() or "hive"
        self.database = str(database or "").strip()
        self.org_code = str(org_code or "").strip()
        self.org_name = str(org_name or "").strip()

    def _build_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if HTTP_AUTH_TOKEN:
            headers["Authorization"] = f"Bearer {HTTP_AUTH_TOKEN}"
        return headers

    def _render_url(self, template: str, task_id: str) -> str:
        if "{task_id}" in template:
            return template.format(task_id=task_id)
        return template

    async def _request_json(self, method: str, url: str, *, json_payload: Optional[Dict[str, Any]] = None, task_id: str = "", params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        import httpx

        request_kwargs: Dict[str, Any] = {
            "headers": self._build_headers(),
            "timeout": max(CONNECT_TIMEOUT_SECONDS, resolve_query_timeout(self.source_type)),
        }
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
            async with httpx.AsyncClient(verify=HTTP_VERIFY_TLS) as client:
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

    def _build_submit_payload(self, sql: str, limit: int, user_id: Optional[str] = None, username: Optional[str] = None) -> Dict[str, Any]:
        payload = {
            "sql": sql,
            "database": self.database,
            "orgCode": self.org_code,
            "orgName": self.org_name,
            "limit": limit,
        }
        _add_hive_http_user_params(payload, user_id, username)
        return payload

    def _normalize_result(self, payload: Dict[str, Any]) -> Dict[str, Any]:
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
            "success": True,
            "columns": columns,
            "data": data,
            "total_count": int(total_count or 0),
            **execution_times,
        }

    async def _notify_remote_task_created(
        self,
        remote_task_callback: Optional[Callable[[Dict[str, Any]], Any]],
        remote_task_id: str,
    ) -> None:
        """在 HTTP 任务提交成功后通知上层记录远端任务映射。"""
        if remote_task_callback is None:
            return
        callback_result = remote_task_callback({
            "remote_task_id": remote_task_id,
            "namespace": self.namespace,
            "source_type": self.source_type,
        })
        if inspect.isawaitable(callback_result):
            await callback_result

    async def execute(
        self,
        sql: str,
        limit: int = DEFAULT_ROW_LIMIT,
        timeout: Optional[int] = None,
        user_id: Optional[str] = None,
        username: Optional[str] = None,
        remote_task_callback: Optional[Callable[[Dict[str, Any]], Any]] = None,
        db_connection_callback: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> Dict[str, Any]:
        resolved_timeout = resolve_query_timeout(self.source_type, timeout)
        is_valid, error_msg = validate_sql(sql, source_type=self.source_type)
        if not is_valid:
            _emit_sql_execution_validation_alarm(
                sql,
                error_msg,
                source_type=self.source_type,
                execution_scope="http_executor",
            )
            return {"success": False, "error": error_msg, "columns": [], "data": [], "total_count": 0}
        if not self.namespace:
            return {"success": False, "error": "缺少 HTTP 执行标识，请配置机构编码", "columns": [], "data": [], "total_count": 0}
        if not HTTP_SUBMIT_URL or not HTTP_STATUS_URL:
            return {
                "success": False,
                "error": "未配置 HTTP 查询接口，请检查 CHAT_DATA_HTTP_SUBMIT_URL/CHAT_DATA_HTTP_STATUS_URL",
                "columns": [],
                "data": [],
                "total_count": 0,
            }

        try:
            sql_with_limit = build_effective_sql(sql, limit, source_type=self.source_type)
            submit_payload = await self._request_json(
                "POST",
                HTTP_SUBMIT_URL,
                json_payload=self._build_submit_payload(sql_with_limit, limit, user_id, username),
            )
            submit_code = _extract_remote_code(submit_payload)
            if submit_code is not None and submit_code != 20000:
                return {
                    "success": False,
                    "error": _extract_remote_message(submit_payload, "HTTP 查询提交失败"),
                    "columns": [],
                    "data": [],
                    "total_count": 0,
                }
            task_id = _extract_task_id(submit_payload)
            if not task_id:
                return {
                    "success": False,
                    "error": _extract_remote_message(submit_payload, "Hive HTTP 提交成功但未返回任务 ID"),
                    "columns": [],
                    "data": [],
                    "total_count": 0,
                }
            await self._notify_remote_task_created(remote_task_callback, task_id)

            loop = asyncio.get_running_loop()
            deadline = loop.time() + resolved_timeout
            while True:
                if loop.time() >= deadline:
                    return {"success": False, "error": f"查询超时（{resolved_timeout}秒）", "columns": [], "data": [], "total_count": 0}
                status_params = _add_hive_http_user_params({}, user_id, username)
                if status_params:
                    status_payload = await self._request_json("GET", HTTP_STATUS_URL, task_id=task_id, params=status_params)
                else:
                    status_payload = await self._request_json("GET", HTTP_STATUS_URL, task_id=task_id)
                status_code = _extract_remote_code(status_payload)
                if status_code is not None and status_code != 20000:
                    return {
                        "success": False,
                        "error": _extract_remote_message(status_payload, "HTTP 查询失败"),
                        "columns": [],
                        "data": [],
                        "total_count": 0,
                        **_build_http_execution_times(status_payload, {}),
                    }
                normalized_status = _extract_remote_status(status_payload)
                if normalized_status == "succeeded":
                    result = self._normalize_result(status_payload)
                    result["remote_task_id"] = task_id
                    result["source_type"] = self.source_type
                    return result
                if normalized_status == "cancelled":
                    return {
                        "success": False,
                        "error": _extract_remote_message(status_payload, "查询已取消"),
                        "columns": [],
                        "data": [],
                        "total_count": 0,
                        **_build_http_execution_times(status_payload, {}),
                    }
                if normalized_status == "failed":
                    error_message = _extract_remote_message(status_payload, "HTTP 查询失败")
                    return {
                        "success": False,
                        "error": str(error_message),
                        "columns": [],
                        "data": [],
                        "total_count": 0,
                        **_build_http_execution_times(status_payload, {}),
                    }
                await asyncio.sleep(max(0.2, DEFAULT_HTTP_POLL_INTERVAL_MS / 1000))
        except asyncio.TimeoutError:
            logger.error("[HTTPExecutor] 执行 SQL 超时（%ss）", resolved_timeout)
            return {"success": False, "error": f"查询超时（{resolved_timeout}秒）", "columns": [], "data": [], "total_count": 0}
        except ValueError as e:
            logger.warning("[HTTPExecutor] SQL 执行参数非法: %s", e)
            return {"success": False, "error": str(e), "columns": [], "data": [], "total_count": 0}
        except Exception as e:
            logger.error("[HTTPExecutor] 执行 SQL 失败: %s", e)
            engine_name = "MySQL" if self.source_type == "mysql" else "Hive"
            return {"success": False, "error": _friendly_sql_error(engine_name, e), "columns": [], "data": [], "total_count": 0}

    async def test_connection(self) -> bool:
        """Hive HTTP 模式不再校验直连，仅校验必要配置。"""
        return bool(HTTP_SUBMIT_URL and HTTP_STATUS_URL)

    async def close(self):
        """HTTP 客户端按请求创建，无需显式关闭。"""
        pass


async def fetch_http_query_task_result(remote_task_id: str, source_type: str = "hive", user_id: Optional[str] = None, username: Optional[str] = None) -> Dict[str, Any]:
    """按远端任务 ID 查询 HTTP 任务状态与结果。"""
    executor = HiveHttpExecutor(namespace="_remote_task_lookup_", source_type=source_type)
    status_params = _add_hive_http_user_params({}, user_id, username)
    if status_params:
        payload = await executor._request_json("GET", HTTP_STATUS_URL, task_id=remote_task_id, params=status_params)
    else:
        payload = await executor._request_json("GET", HTTP_STATUS_URL, task_id=remote_task_id)
    status_code = _extract_remote_code(payload)
    if status_code is not None and status_code != 20000:
        normalized_status = _extract_remote_status(payload)
        if normalized_status == "failed":
            return {
                "status": "failed",
                "success": False,
                "error": _extract_remote_message(payload, "HTTP 查询失败"),
                "columns": [],
                "data": [],
                "total_count": 0,
                **_build_http_execution_times(payload, {}),
            }
        return {
            "status": normalized_status or "failed",
            "success": False,
            "error": _extract_remote_message(payload, "HTTP 查询失败"),
            "columns": [],
            "data": [],
            "total_count": 0,
            **_build_http_execution_times(payload, {}),
        }

    normalized_status = _extract_remote_status(payload)
    if normalized_status == "succeeded":
        result = executor._normalize_result(payload)
        return {
            "status": "succeeded",
            "success": True,
            "columns": result.get("columns") or [],
            "data": result.get("data") or [],
            "total_count": int(result.get("total_count") or 0),
            "execution_time_ms": result.get("execution_time_ms"),
            "started_time": result.get("started_time"),
            "finished_time": result.get("finished_time"),
            "error": None,
        }
    if normalized_status in {"failed", "cancelled"}:
        return {
            "status": normalized_status,
            "success": False,
            "error": _extract_remote_message(payload, "查询已取消" if normalized_status == "cancelled" else "HTTP 查询失败"),
            "columns": [],
            "data": [],
            "total_count": 0,
            **_build_http_execution_times(payload, {}),
        }
    return {
        "status": normalized_status,
        "success": False,
        "error": None,
        "columns": [],
        "data": [],
        "total_count": 0,
    }


async def stop_http_query_task(remote_task_id: str, source_type: str = "hive", user_id: Optional[str] = None, username: Optional[str] = None) -> Dict[str, Any]:
    """按远端任务 ID 调用 HTTP 停止接口。"""
    normalized_task_id = str(remote_task_id or "").strip()
    if not normalized_task_id:
        return {"success": False, "error": "缺少远端任务ID"}
    if not HTTP_STOP_URL:
        return {"success": False, "error": "未配置 HTTP 停止接口，请检查 CHAT_DATA_HTTP_STOP_URL"}
    executor = HiveHttpExecutor(namespace="_remote_task_cancel_", source_type=source_type)
    try:
        stop_payload = _add_hive_http_user_params({"task_id": normalized_task_id}, user_id, username)
        payload = await executor._request_json("POST", HTTP_STOP_URL, json_payload=stop_payload)
        status_code = _extract_remote_code(payload)
        if status_code is not None and status_code != 20000:
            return {"success": False, "error": _extract_remote_message(payload, "HTTP 停止任务失败")}
        return {"success": True, "error": None, "payload": payload}
    except Exception as e:
        logger.error("[HTTPExecutor] 停止任务失败: task_id=%s error=%s", normalized_task_id, e)
        return {"success": False, "error": _friendly_sql_error("HTTP", e)}


async def kill_mysql_query(datasource: dict, db_connection_id: str) -> Dict[str, Any]:
    """按数据源配置停止 MySQL 连接上的当前查询。"""
    executor = None
    try:
        executor = get_executor(datasource)
        if not isinstance(executor, MySQLExecutor):
            return {"success": False, "error": "当前执行器不支持直连停止"}
        return await executor.kill_query(db_connection_id)
    except Exception as e:
        logger.error("[MySQL] 停止查询初始化失败: error=%s", e)
        return {"success": False, "error": str(e) or "停止查询失败"}
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
    config = dict(datasource.get("source_config", {}) or {})

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
        if not config.get("user") or "password" not in config:
            credentials = resolve_mysql_credentials(config)
            config["user"] = credentials["user"]
            config["password"] = credentials["password"]
        return MySQLExecutor(
            host=config.get("host"),
            port=config.get("port", 3306),
            database=config.get("database"),
            user=config.get("user"),
            password=config.get("password")
        )
    elif source_type == "hive":
        if not config.get("username") or "password" not in config:
            credentials = resolve_hive_credentials()
            if credentials.get("username"):
                config["username"] = credentials["username"]
            if credentials.get("password"):
                config["password"] = credentials["password"]
        return HiveExecutor(
            host=config.get("host"),
            port=config.get("port", 10000),
            database=config.get("database", "default"),
            username=config.get("username"),
            password=config.get("password"),
        )
    else:
        raise ValueError(f"不支持的数据源类型: {source_type}，只支持 mysql/hive")
