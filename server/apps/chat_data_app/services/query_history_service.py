"""查询历史业务服务。"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, time as datetime_time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from utils.lang_util.models import ChatDataQueryExecution, ChatDataQueryTask
from utils.logger import get_logger

from . import query_runtime
from ..daos.query_history_dao import QueryHistoryDao

logger = get_logger(__name__)

QUERY_TASK_FINAL_STATUSES = {"succeeded", "failed", "cancelled", "partial_success"}
RUNNING_SQL_STATUSES = {"pending", "running"}


def _safe_json_loads(text: Any, default: Any = None) -> Any:
    if not text:
        return default
    if isinstance(text, (dict, list)):
        return text
    try:
        return json.loads(str(text))
    except Exception:
        return default


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _first_text(*values: Any) -> str:
    for value in values:
        text = _clean_text(value)
        if text:
            return text
    return ""


def _task_runtime_data(task: Any) -> Dict[str, Any]:
    if task is None:
        return {}
    try:
        return query_runtime.build_query_task_runtime_data(task)
    except AttributeError:
        return {
            "request_payload": getattr(task, "request_payload", None),
            "remote_task_payload": getattr(task, "remote_task_payload", None),
            "create_by": getattr(task, "create_by", ""),
            "update_by": getattr(task, "update_by", ""),
            "create_by_name": getattr(task, "create_by_name", ""),
            "update_by_name": getattr(task, "update_by_name", ""),
        }


class QueryHistoryService:
    """查询历史列表、详情和批量停止编排。"""

    def __init__(self, dao: Optional[QueryHistoryDao] = None):
        self.dao = dao or QueryHistoryDao()

    @staticmethod
    def normalize_source_filter(source: Optional[str]) -> Optional[List[str]]:
        normalized = str(source or "").strip().lower()
        if not normalized or normalized == "all":
            return None
        if normalized == "sql":
            return ["sql", ""]
        if normalized == "builder":
            return ["builder"]
        if normalized in {"agent", "conversation"}:
            return ["agent", "conversation"]
        if normalized == "dashboard":
            return ["dashboard"]
        return None

    @staticmethod
    def normalize_status_filter(status: Optional[str]) -> Optional[str]:
        normalized = str(status or "").strip().lower()
        if normalized in {"pending", "running", "succeeded", "failed", "cancelled", "timeout", "kill_failed"}:
            return normalized
        return None

    @staticmethod
    def clean_optional_text(value: Optional[str]) -> str:
        return str(value or "").strip()

    @classmethod
    def parse_history_time(cls, value: Optional[str], *, end_of_day: bool = False) -> Optional[datetime]:
        raw_value = cls.clean_optional_text(value)
        if not raw_value:
            return None
        normalized = raw_value.replace("T", " ")
        formats = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")
        for fmt in formats:
            try:
                parsed = datetime.strptime(normalized, fmt)
                if fmt == "%Y-%m-%d" and end_of_day:
                    return datetime.combine(parsed.date(), datetime_time.max)
                return parsed
            except ValueError:
                continue
        raise ValueError("时间格式错误，请使用 YYYY-MM-DD HH:mm:ss")

    @staticmethod
    def get_history_source(query_type: Optional[str]) -> str:
        normalized_type = str(query_type or "").strip().lower()
        if normalized_type == "builder":
            return "可视化查询"
        if normalized_type in {"agent", "conversation"}:
            return "智能问数"
        if normalized_type == "dashboard":
            return "看板"
        return "SQL编辑器"

    @staticmethod
    def task_operator_name(task_data: Dict[str, Any]) -> str:
        payload = _safe_json_loads(task_data.get("request_payload"), {}) or {}
        return str((payload or {}).get("operator_name") or "").strip()

    @staticmethod
    def task_user_id(task_data: Dict[str, Any]) -> str:
        payload = _safe_json_loads(task_data.get("request_payload"), {}) or {}
        return str((payload or {}).get("user_id") or task_data.get("create_by") or task_data.get("update_by") or "").strip()

    @staticmethod
    def _history_operator_name(
        item: ChatDataQueryExecution,
        task: Optional[ChatDataQueryTask],
        task_data: Dict[str, Any],
    ) -> str:
        return _first_text(
            getattr(item, "create_by_name", ""),
            getattr(item, "update_by_name", ""),
            getattr(task, "create_by_name", "") if task else "",
            getattr(task, "update_by_name", "") if task else "",
            QueryHistoryService.task_operator_name(task_data) if task else "",
        )

    @staticmethod
    def _history_display_time(payload: Dict[str, Any]) -> Any:
        return payload.get("finished_time") or payload.get("started_time") or payload.get("create_time")

    @classmethod
    def build_history_payload(cls, item: ChatDataQueryExecution, task: Optional[ChatDataQueryTask]) -> Dict[str, Any]:
        query_type = task.query_type if task else "sql"
        task_status = str(task.task_status or "").strip().lower() if task else ""
        task_data = _task_runtime_data(task)
        operator_name = cls._history_operator_name(item, task, task_data)
        display_sql_status = "cancelled" if task_status == "cancelled" else item.sql_status
        payload = item.to_dict()
        payload.pop("result_payload", None)
        display_time = cls._history_display_time(payload)
        payload.update({
            "query_type": query_type,
            "task_status": task_status,
            "sql_status": display_sql_status,
            "title": "SQL查询",
            "last_used": display_time,
            "executed_at": display_time,
            "source": cls.get_history_source(query_type),
            "success_flag": 1 if display_sql_status == "succeeded" else 0,
            "operator_name": operator_name,
            "has_result": bool(item.result_payload or item.remote_task_id),
        })
        return payload

    async def list_user_history(
        self,
        *,
        datasource_id: str,
        user_id: str,
        page: int = 1,
        page_size: int = 10,
        source: Optional[str] = None,
        status: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_page = max(1, page)
        normalized_page_size = max(1, min(page_size, 100))
        rows, total = await self.dao.list_user_history(
            datasource_id=datasource_id,
            user_id=user_id,
            page=normalized_page,
            page_size=normalized_page_size,
            source_filter=self.normalize_source_filter(source),
            status_filter=self.normalize_status_filter(status),
            start_time=self.parse_history_time(start_time),
            end_time=self.parse_history_time(end_time, end_of_day=True),
        )
        return {
            "queries": [self.build_history_payload(item, task) for item, task in rows],
            "total": total,
            "page": normalized_page,
            "page_size": normalized_page_size,
        }

    async def list_admin_history(
        self,
        *,
        datasource_id: Optional[str] = None,
        semantic_config_id: Optional[str] = None,
        user_id: Optional[str] = None,
        username: Optional[str] = None,
        keyword: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
        source: Optional[str] = None,
        status: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_page = max(1, page)
        normalized_page_size = max(1, min(page_size, 100))
        rows, total = await self.dao.list_admin_history(
            datasource_id=self.clean_optional_text(datasource_id),
            semantic_config_id=self.clean_optional_text(semantic_config_id),
            user_id=self.clean_optional_text(user_id),
            username=self.clean_optional_text(username),
            keyword=self.clean_optional_text(keyword),
            page=normalized_page,
            page_size=normalized_page_size,
            source_filter=self.normalize_source_filter(source),
            status_filter=self.normalize_status_filter(status),
            start_time=self.parse_history_time(start_time),
            end_time=self.parse_history_time(end_time, end_of_day=True),
        )
        return {
            "queries": [self.build_history_payload(item, task) for item, task in rows],
            "total": total,
            "page": normalized_page,
            "page_size": normalized_page_size,
        }

    @staticmethod
    def is_http_query_execution(execution: ChatDataQueryExecution) -> bool:
        return bool(str(getattr(execution, "remote_task_id", "") or "").strip())

    @staticmethod
    def resolve_remote_history_source_type(execution: ChatDataQueryExecution, task: Optional[ChatDataQueryTask]) -> str:
        member_datasource_id = str(execution.member_datasource_id or "").strip()
        for remote_task in query_runtime.load_remote_tasks(task.remote_task_payload if task else None):
            if str(remote_task.get("member_datasource_id") or "").strip() == member_datasource_id:
                return str(remote_task.get("source_type") or "hive").strip().lower() or "hive"
        return "hive"

    @staticmethod
    def _remote_operator_name(execution: ChatDataQueryExecution, task: Optional[ChatDataQueryTask]) -> str:
        return _first_text(
            getattr(execution, "create_by_name", ""),
            getattr(execution, "update_by_name", ""),
            QueryHistoryService.task_operator_name(_task_runtime_data(task)) if task else "",
        )

    @staticmethod
    def _remote_execution_time_ms(execution: ChatDataQueryExecution, remote_result: Optional[Dict[str, Any]] = None) -> Optional[int]:
        remote_time = (remote_result or {}).get("execution_time_ms")
        return query_runtime.resolve_execution_time_ms(
            remote_time if remote_time is not None else getattr(execution, "execution_time_ms", None),
            getattr(execution, "started_time", None),
            getattr(execution, "finished_time", None),
        )

    @classmethod
    def _remote_unavailable_payload(cls, execution: ChatDataQueryExecution) -> Dict[str, Any]:
        return {
            "success": False,
            "columns": [],
            "data": [],
            "total_count": 0,
            "execution_time_ms": cls._remote_execution_time_ms(execution),
            "error": "历史结果已不可用或获取失败",
            "remote_result_status": "unavailable",
        }

    @classmethod
    def _remote_not_succeeded_payload(
        cls,
        execution: ChatDataQueryExecution,
        remote_result: Dict[str, Any],
        status: str,
    ) -> Dict[str, Any]:
        error_message = str((remote_result or {}).get("error") or "").strip() or "历史结果已不可用或仍在处理中"
        return {
            "success": False,
            "columns": [],
            "data": [],
            "total_count": 0,
            "execution_time_ms": cls._remote_execution_time_ms(execution, remote_result),
            "error": error_message,
            "remote_result_status": status or "unknown",
        }

    @classmethod
    def _remote_success_payload(
        cls,
        execution: ChatDataQueryExecution,
        remote_result: Dict[str, Any],
        status: str,
    ) -> Dict[str, Any]:
        return {
            "success": True,
            "columns": (remote_result or {}).get("columns") or [],
            "data": (remote_result or {}).get("data") or [],
            "total_count": int((remote_result or {}).get("total_count") or 0),
            "execution_time_ms": cls._remote_execution_time_ms(execution, remote_result),
            "error": None,
            "remote_result_status": status,
        }

    async def build_remote_history_result_payload(
        self,
        execution: ChatDataQueryExecution,
        task: Optional[ChatDataQueryTask] = None,
        *,
        fetch_remote_result: Callable[..., Awaitable[Dict[str, Any]]],
    ) -> Optional[Dict[str, Any]]:
        remote_task_id = str(execution.remote_task_id or "").strip()
        if not remote_task_id:
            return None
        operator_name = self._remote_operator_name(execution, task)
        try:
            remote_result = await fetch_remote_result(
                remote_task_id,
                self.resolve_remote_history_source_type(execution, task),
                user_id=str(execution.user_id or "").strip() or None,
                username=operator_name or None,
            )
        except Exception as exc:
            logger.warning("获取远端历史查询结果失败: execution_id=%s remote_task_id=%s error=%s", execution.execution_id, remote_task_id, exc)
            return self._remote_unavailable_payload(execution)
        status = str((remote_result or {}).get("status") or "").strip().lower()
        if status != "succeeded":
            return self._remote_not_succeeded_payload(execution, remote_result, status)
        return self._remote_success_payload(execution, remote_result, status)

    async def get_history_result(
        self,
        *,
        execution_id: str,
        user_id: str,
        fetch_remote_result: Callable[..., Awaitable[Dict[str, Any]]],
    ) -> Optional[Dict[str, Any]]:
        execution, task = await self.dao.get_user_history_execution(execution_id=execution_id, user_id=user_id)
        if not execution:
            return None
        payload = execution.to_dict()
        result_payload = payload.get("result_payload")
        if self.is_http_query_execution(execution):
            result_payload = await self.build_remote_history_result_payload(execution, task, fetch_remote_result=fetch_remote_result)
        execution_time_ms = query_runtime.resolve_execution_time_ms(
            execution.execution_time_ms,
            execution.started_time,
            execution.finished_time,
        )
        return {
            "execution_id": execution.execution_id,
            "query": execution.sql_text,
            "member_datasource_id": execution.member_datasource_id,
            "member_name": execution.member_name,
            "execution_time_ms": execution_time_ms,
            "error_message": execution.error_message,
            "result_payload": result_payload,
        }

    async def get_execution_models(self, task_id: str) -> List[ChatDataQueryExecution]:
        return await self.dao.get_execution_models_by_task(task_id)

    async def get_execution_model(self, execution_id: str) -> Optional[ChatDataQueryExecution]:
        return await self.dao.get_execution_model(execution_id)

    @staticmethod
    def copy_execution_identity(source: ChatDataQueryExecution, target: ChatDataQueryExecution) -> None:
        source.remote_task_id = str(getattr(target, "remote_task_id", "") or "").strip()
        source.db_connection_id = str(getattr(target, "db_connection_id", "") or "").strip()
        source.sql_status = str(getattr(target, "sql_status", "") or source.sql_status or "").strip()

    @staticmethod
    def build_cancel_task_status(execution_results: List[Dict[str, Any]]) -> str:
        if not execution_results:
            return "cancelled"
        statuses = {str(item.get("status") or "").strip().lower() for item in execution_results}
        if statuses <= {"cancelled"}:
            return "cancelled"
        if statuses <= {"kill_failed"}:
            return "failed"
        if "kill_failed" in statuses:
            return "partial_success"
        return "cancelled"

    @staticmethod
    def group_cancel_results_by_task(
        rows: List[Tuple[ChatDataQueryExecution, Optional[ChatDataQueryTask]]],
        execution_results: List[Dict[str, Any]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for (execution, _task), cancel_result in zip(rows, execution_results):
            task_id = str(execution.task_id or "").strip()
            if task_id:
                grouped.setdefault(task_id, []).append(cancel_result)
        return grouped

    @classmethod
    def execution_cancel_operator_name(
        cls,
        execution: ChatDataQueryExecution,
        task: Optional[ChatDataQueryTask],
        fallback: Optional[str],
    ) -> str:
        execution_operator = str(execution.create_by_name or execution.update_by_name or "").strip()
        task_name = str(task.create_by_name or task.update_by_name or "").strip() if task else ""
        task_operator = cls.task_operator_name(_task_runtime_data(task)) if task else ""
        return execution_operator or task_name or task_operator or str(fallback or "").strip()

    async def cancel_query_execution_for_batch(
        self,
        execution: ChatDataQueryExecution,
        *,
        cancel_execution: Callable[..., Awaitable[Dict[str, Any]]],
        update_execution: Callable[..., Awaitable[None]],
        operator_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            return await cancel_execution(execution, operator_name=operator_name)
        except Exception as err:
            logger.error("批量停止单条 SQL 失败: execution_id=%s error=%s", execution.execution_id, err, exc_info=True)
            await update_execution(
                execution.execution_id,
                sql_status="kill_failed",
                error_message="停止执行失败",
                finished_time=datetime.now(),
            )
            return {
                "execution_id": execution.execution_id,
                "status": "kill_failed",
                "success": False,
                "message": "停止执行失败",
            }

    async def cancel_running_query_history(
        self,
        *,
        datasource_id: Optional[str] = None,
        user_id: Optional[str] = None,
        operator_name: Optional[str] = None,
        cancel_execution: Callable[..., Awaitable[Dict[str, Any]]],
        update_execution: Callable[..., Awaitable[None]],
        update_task: Callable[..., Awaitable[Optional[Dict[str, Any]]]],
    ) -> Dict[str, Any]:
        rows = await self.dao.list_running_executions(
            running_statuses=RUNNING_SQL_STATUSES,
            datasource_id=self.clean_optional_text(datasource_id),
            user_id=self.clean_optional_text(user_id),
        )
        if not rows:
            return {"total": 0, "cancelled": 0, "failed": 0, "executions": []}

        execution_results = await asyncio.gather(*[
            self.cancel_query_execution_for_batch(
                execution,
                cancel_execution=cancel_execution,
                update_execution=update_execution,
                operator_name=self.execution_cancel_operator_name(execution, task, operator_name),
            )
            for execution, task in rows
        ])
        task_by_id = {
            str(task.task_id or "").strip(): task
            for _execution, task in rows
            if task and str(task.task_id or "").strip()
        }
        for task_id, task_results in self.group_cancel_results_by_task(rows, execution_results).items():
            task = task_by_id.get(task_id)
            task_status = str(task.task_status or "").strip().lower() if task else ""
            if task_status in QUERY_TASK_FINAL_STATUSES:
                continue
            cancel_status = self.build_cancel_task_status(task_results)
            await update_task(
                task_id,
                status=cancel_status,
                progress_text=("已停止" if cancel_status == "cancelled" else "部分停止失败"),
                error_message=(None if cancel_status == "cancelled" else "部分 SQL 停止失败"),
                finished_time=datetime.now(),
            )

        failed_count = sum(1 for item in execution_results if not item.get("success"))
        return {
            "total": len(execution_results),
            "cancelled": len(execution_results) - failed_count,
            "failed": failed_count,
            "executions": execution_results,
        }
