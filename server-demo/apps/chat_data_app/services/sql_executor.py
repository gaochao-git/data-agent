"""
SQL 执行器模块

支持 MySQL、Hive、SQLite 的异步查询执行
通过环境变量配置 SQL 安全限制和结果行数限制
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Tuple, Optional, Callable
from decimal import Decimal
from datetime import date, datetime
import asyncio
import inspect
import re
import os

from utils.logger import get_logger
from .datasource_credentials import resolve_hive_credentials, resolve_mysql_credentials
from .hive_sync import _apply_socket_timeout, SOCKET_TIMEOUT_SECONDS

logger = get_logger(__name__)

# ============ 可配置参数（从环境变量读取） ============

# 结果行数限制（从环境变量读取，默认100）
DEFAULT_ROW_LIMIT = int(os.getenv("CHATDATA_ROW_LIMIT", "100"))
DEFAULT_MYSQL_QUERY_TIMEOUT_SECONDS = int(os.getenv("CHAT_DATA_MYSQL_QUERY_TIMEOUT_SECONDS", "60"))
DEFAULT_HIVE_QUERY_TIMEOUT_SECONDS = int(os.getenv("CHAT_DATA_HIVE_QUERY_TIMEOUT_SECONDS", "600"))
DEFAULT_HTTP_POLL_INTERVAL_MS = int(os.getenv("CHAT_DATA_HTTP_POLL_INTERVAL_MS", "1500"))

# 允许的 SQL 操作类型（从环境变量读取，默认 SELECT,SHOW,EXPLAIN,DESCRIBE）
_ALLOWED_SQL_TYPES = os.getenv("CHATDATA_ALLOWED_SQL", "SELECT,SHOW,EXPLAIN,DESCRIBE")
ALLOWED_SQL_TYPES = [s.strip().upper() for s in _ALLOWED_SQL_TYPES.split(",") if s.strip()]
CONNECT_TIMEOUT_SECONDS = 3
HTTP_SUBMIT_URL = str(os.getenv("CHAT_DATA_HTTP_SUBMIT_URL") or "").strip()
HTTP_STATUS_URL = str(os.getenv("CHAT_DATA_HTTP_STATUS_URL") or "").strip()
HTTP_AUTH_TOKEN = str(os.getenv("CHAT_DATA_HTTP_AUTH_TOKEN") or "").strip()
HTTP_VERIFY_TLS = str(os.getenv("CHAT_DATA_HTTP_VERIFY_TLS", "false")).strip().lower() in {"1", "true", "yes", "on"}


def _build_sql_patterns() -> list:
    """根据配置的 SQL 类型生成正则匹配模式"""
    patterns = []
    for sql_type in ALLOWED_SQL_TYPES:
        if sql_type == "DESCRIBE":
            # DESCRIBE 可以简写为 DESC
            patterns.append(r'^\s*DESC(RIBE)?\s+')
        else:
            patterns.append(rf'^\s*{sql_type}\s+')
    return patterns


ALLOWED_SQL_PATTERNS = _build_sql_patterns()


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
    """将底层数据库异常转换为前端可读错误信息。"""
    raw = str(err).strip()
    lower = raw.lower()
    normalized_raw = " ".join(raw.split())

    if "tsocket read 0 bytes" in lower or "connection reset" in lower or "broken pipe" in lower:
        return f"{engine}连接中断，请检查服务状态或网络连通性"
    if "timed out" in lower or "timeout" in lower:
        return "连接或查询超时，请稍后重试"
    if "table not found" in lower or "unknown table" in lower or "does not exist" in lower:
        return "表不存在或未授权访问，请检查库表配置"
    if "unknown column" in lower or "invalid column" in lower:
        return "字段不存在或未授权访问，请检查字段配置"
    if "access denied" in lower or "permission denied" in lower:
        return "无权限访问该库表，请联系管理员开通权限"
    if "syntax" in lower or "parseexception" in lower:
        return f"SQL语法错误: {normalized_raw or raw}"
    return "SQL执行错误，请检查语句或稍后重试"


def _serialize_value(value: Any) -> Any:
    """将不可 JSON 序列化的值转换为可序列化类型"""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
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
    if normalized in {"failed", "error", "cancelled", "canceled"}:
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


def validate_sql(sql: str) -> Tuple[bool, str]:
    """
    验证 SQL 是否安全

    Args:
        sql: SQL 语句

    Returns:
        (is_valid, error_message) - 如果验证通过返回 (True, "")，否则返回 (False, 错误信息)
    """
    sql_stripped = sql.strip()
    for pattern in ALLOWED_SQL_PATTERNS:
        if re.match(pattern, sql_stripped, re.IGNORECASE):
            return True, ""

    allowed_str = ", ".join(ALLOWED_SQL_TYPES)
    return False, f"只允许执行以下类型的 SQL: {allowed_str}"


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
    ) -> Dict[str, Any]:
        import time
        resolved_timeout = resolve_query_timeout("mysql", timeout)
        # SQL 安全验证
        is_valid, error_msg = validate_sql(sql)
        if not is_valid:
            return {"success": False, "error": error_msg, "columns": [], "data": [], "total_count": 0}

        # 标准化别名（统一加反引号）
        sql = normalize_sql_aliases(sql)

        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    # 添加 LIMIT（如果没有）
                    sql_with_limit = self._add_limit(sql, limit)
                    logger.info(f"[MySQL] 执行 SQL: {sql_with_limit[:200]}...")
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
                    return {
                        "success": True,
                        "columns": columns,
                        "data": data,
                        "total_count": len(data),
                        "execution_time_ms": execution_time_ms
                    }
        except asyncio.TimeoutError:
            logger.error(f"[MySQL] 执行 SQL 超时（{resolved_timeout}s）")
            return {"success": False, "error": f"查询超时（{resolved_timeout}秒）", "columns": [], "data": [], "total_count": 0}
        except Exception as e:
            logger.error(f"[MySQL] 执行 SQL 失败: {e}")
            return {"success": False, "error": _friendly_sql_error("MySQL", e), "columns": [], "data": [], "total_count": 0}

    def _add_limit(self, sql: str, limit: int) -> str:
        """如果 SQL 没有 LIMIT，自动添加"""
        if not re.search(r'\bLIMIT\s+\d+', sql, re.IGNORECASE):
            return f"{sql.rstrip(';')} LIMIT {limit}"
        return sql

    async def test_connection(self) -> bool:
        """测试 MySQL 连接"""
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute("SELECT 1")
                    return True
        except Exception as e:
            logger.error(f"[MySQL] 连接测试失败: {e}")
            return False

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
        conn_params = {
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "username": self.username,
            "password": self.password,
        }
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
    ) -> Dict[str, Any]:
        resolved_timeout = resolve_query_timeout("hive", timeout)
        # SQL 安全验证
        is_valid, error_msg = validate_sql(sql)
        if not is_valid:
            return {"success": False, "error": error_msg, "columns": [], "data": [], "total_count": 0}

        # 标准化别名（统一加反引号，兼容 Hive 中文别名）
        sql = normalize_sql_aliases(sql)

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
            sql_with_limit = self._add_limit(sql, limit)
            logger.info(f"[Hive] 执行 SQL: {sql_with_limit[:200]}...")
            start_time = time.time()
            cursor.execute(sql_with_limit)
            rows = cursor.fetchall()
            execution_time_ms = int((time.time() - start_time) * 1000)
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            data = [_serialize_row(dict(zip(columns, row))) for row in rows]
            return {
                "success": True,
                "columns": columns,
                "data": data,
                "total_count": len(data),
                "execution_time_ms": execution_time_ms
            }
        except Exception as e:
            logger.error(f"[Hive] 执行 SQL 失败: {e}")
            return {"success": False, "error": _friendly_sql_error("Hive", e), "columns": [], "data": [], "total_count": 0}
        finally:
            if conn:
                conn.close()

    def _add_limit(self, sql: str, limit: int) -> str:
        """如果 SQL 没有 LIMIT，自动添加"""
        if not re.search(r'\bLIMIT\s+\d+', sql, re.IGNORECASE):
            return f"{sql.rstrip(';')} LIMIT {limit}"
        return sql

    async def test_connection(self) -> bool:
        """测试 Hive 连接"""
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(None, self._test_sync)
        except Exception as e:
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

    def __init__(self, namespace: str, source_type: str = "hive"):
        self.namespace = str(namespace or "").strip()
        self.source_type = str(source_type or "hive").strip().lower() or "hive"

    def _build_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if HTTP_AUTH_TOKEN:
            headers["Authorization"] = f"Bearer {HTTP_AUTH_TOKEN}"
        return headers

    def _render_url(self, template: str, task_id: str) -> str:
        if "{task_id}" in template:
            return template.format(task_id=task_id)
        return template

    async def _request_json(self, method: str, url: str, *, json_payload: Optional[Dict[str, Any]] = None, task_id: str = "") -> Dict[str, Any]:
        import httpx

        request_kwargs: Dict[str, Any] = {
            "headers": self._build_headers(),
            "timeout": max(CONNECT_TIMEOUT_SECONDS, resolve_query_timeout(self.source_type)),
        }
        if method.upper() == "GET":
            if task_id and "{task_id}" not in url:
                request_kwargs["params"] = {"task_id": task_id}
        else:
            request_kwargs["json"] = json_payload or {}
        async with httpx.AsyncClient(verify=HTTP_VERIFY_TLS) as client:
            response = await client.request(method.upper(), self._render_url(url, task_id), **request_kwargs)
            response.raise_for_status()
            payload = response.json()
            return _extract_response_payload(payload)

    def _build_submit_payload(self, sql: str, limit: int, user_id: Optional[str] = None) -> Dict[str, Any]:
        payload = {
            "sql": sql,
            "namespace": self.namespace,
            "limit": limit,
        }
        normalized_user_id = str(user_id or "").strip()
        if normalized_user_id:
            payload["user_id"] = normalized_user_id
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
        execution_time_ms = result_payload.get("execution_time_ms")
        if execution_time_ms is None:
            execution_time_ms = result_payload.get("duration_ms")
        return {
            "success": True,
            "columns": columns,
            "data": data,
            "total_count": int(total_count or 0),
            "execution_time_ms": execution_time_ms,
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
        remote_task_callback: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> Dict[str, Any]:
        resolved_timeout = resolve_query_timeout(self.source_type, timeout)
        is_valid, error_msg = validate_sql(sql)
        if not is_valid:
            return {"success": False, "error": error_msg, "columns": [], "data": [], "total_count": 0}
        if not self.namespace:
            return {"success": False, "error": "缺少 HTTP 执行标识，请在名称中配置 namespace", "columns": [], "data": [], "total_count": 0}
        if not HTTP_SUBMIT_URL or not HTTP_STATUS_URL:
            return {
                "success": False,
                "error": "未配置 HTTP 查询接口，请检查 CHAT_DATA_HTTP_SUBMIT_URL/CHAT_DATA_HTTP_STATUS_URL",
                "columns": [],
                "data": [],
                "total_count": 0,
            }

        sql_with_limit = sql if re.search(r'\bLIMIT\s+\d+', sql, re.IGNORECASE) else f"{sql.rstrip(';')} LIMIT {limit}"
        sql_with_limit = normalize_sql_aliases(sql_with_limit)
        try:
            submit_payload = await self._request_json(
                "POST",
                HTTP_SUBMIT_URL,
                json_payload=self._build_submit_payload(sql_with_limit, limit, user_id),
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
                status_payload = await self._request_json("GET", HTTP_STATUS_URL, task_id=task_id)
                status_code = _extract_remote_code(status_payload)
                if status_code is not None and status_code != 20000:
                    return {
                        "success": False,
                        "error": _extract_remote_message(status_payload, "HTTP 查询失败"),
                        "columns": [],
                        "data": [],
                        "total_count": 0,
                    }
                normalized_status = _extract_remote_status(status_payload)
                if normalized_status == "succeeded":
                    return self._normalize_result(status_payload)
                if normalized_status == "failed":
                    error_message = _extract_remote_message(status_payload, "HTTP 查询失败")
                    return {"success": False, "error": str(error_message), "columns": [], "data": [], "total_count": 0}
                await asyncio.sleep(max(0.2, DEFAULT_HTTP_POLL_INTERVAL_MS / 1000))
        except asyncio.TimeoutError:
            logger.error("[HTTPExecutor] 执行 SQL 超时（%ss）", resolved_timeout)
            return {"success": False, "error": f"查询超时（{resolved_timeout}秒）", "columns": [], "data": [], "total_count": 0}
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


async def fetch_http_query_task_result(remote_task_id: str, source_type: str = "hive") -> Dict[str, Any]:
    """按远端任务 ID 查询 HTTP 任务状态与结果。"""
    executor = HiveHttpExecutor(namespace="_remote_task_lookup_", source_type=source_type)
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
            }
        return {
            "status": normalized_status or "failed",
            "success": False,
            "error": _extract_remote_message(payload, "HTTP 查询失败"),
            "columns": [],
            "data": [],
            "total_count": 0,
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
            "error": None,
        }
    if normalized_status == "failed":
        return {
            "status": "failed",
            "success": False,
            "error": _extract_remote_message(payload, "HTTP 查询失败"),
            "columns": [],
            "data": [],
            "total_count": 0,
        }
    return {
        "status": normalized_status,
        "success": False,
        "error": None,
        "columns": [],
        "data": [],
        "total_count": 0,
    }


class SQLiteExecutor(SQLExecutor):
    """SQLite 执行器 - 使用 aiosqlite 异步驱动"""

    def __init__(self, db_path: str):
        self.db_path = db_path

    async def execute(
        self,
        sql: str,
        limit: int = DEFAULT_ROW_LIMIT,
        timeout: int = 30,
        user_id: Optional[str] = None,
        remote_task_callback: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> Dict[str, Any]:
        import time
        # SQL 安全验证
        is_valid, error_msg = validate_sql(sql)
        if not is_valid:
            return {"success": False, "error": error_msg, "columns": [], "data": [], "total_count": 0}

        # 标准化别名（统一加反引号）
        sql = normalize_sql_aliases(sql)

        try:
            import aiosqlite
            async with aiosqlite.connect(self.db_path) as db:
                sql_with_limit = self._add_limit(sql, limit)
                logger.info(f"[SQLite] 执行 SQL: {sql_with_limit[:200]}...")
                # 添加超时控制
                start_time = time.time()
                cursor = await asyncio.wait_for(
                    db.execute(sql_with_limit),
                    timeout=timeout
                )
                rows = await cursor.fetchall()
                execution_time_ms = int((time.time() - start_time) * 1000)
                columns = [desc[0] for desc in cursor.description] if cursor.description else []
                data = [_serialize_row(dict(zip(columns, row))) for row in rows]
                return {
                    "success": True,
                    "columns": columns,
                    "data": data,
                    "total_count": len(data),
                    "execution_time_ms": execution_time_ms
                }
        except asyncio.TimeoutError:
            logger.error(f"[SQLite] 执行 SQL 超时（{timeout}s）")
            return {"success": False, "error": f"查询超时（{timeout}秒）", "columns": [], "data": [], "total_count": 0}
        except Exception as e:
            logger.error(f"[SQLite] 执行 SQL 失败: {e}")
            return {"success": False, "error": _friendly_sql_error("SQLite", e), "columns": [], "data": [], "total_count": 0}

    def _add_limit(self, sql: str, limit: int) -> str:
        """如果 SQL 没有 LIMIT，自动添加"""
        if not re.search(r'\bLIMIT\s+\d+', sql, re.IGNORECASE):
            return f"{sql.rstrip(';')} LIMIT {limit}"
        return sql

    async def test_connection(self) -> bool:
        """测试 SQLite 连接"""
        try:
            import aiosqlite
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("SELECT 1")
                return True
        except Exception as e:
            logger.error(f"[SQLite] 连接测试失败: {e}")
            return False

    async def close(self):
        """SQLite 使用 context manager，无需手动关闭"""
        pass


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
        return HiveHttpExecutor(
            namespace=str(config.get("namespace") or datasource.get("member_name") or "").strip(),
            source_type=source_type,
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
            config["username"] = credentials["username"]
            config["password"] = credentials["password"]
        return HiveExecutor(
            host=config.get("host"),
            port=config.get("port", 10000),
            database=config.get("database", "default"),
            username=config.get("username"),
            password=config.get("password"),
        )
    elif source_type == "sqlite":
        return SQLiteExecutor(db_path=config.get("db_path"))
    else:
        raise ValueError(f"不支持的数据源类型: {source_type}，只支持 mysql/hive/sqlite")
