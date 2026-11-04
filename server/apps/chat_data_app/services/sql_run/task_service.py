"""SQL 异步查询任务服务。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from utils.logger import get_logger
from utils.models import ChatDataQueryExecution

from ...schema import QueryTaskCreateRequest
from ..dashboard import runtime as dashboard_runtime
from ..query import QueryOrchestrator, QuerySource, QuerySpec
from . import runtime as query_runtime
from .history_service import QUERY_TASK_FINAL_STATUSES, QueryHistoryService
from .result import (
    QUERY_RUNNING_STATUS,
    build_query_result_payload,
    query_result_status,
    query_result_succeeded,
)

logger = get_logger(__name__)

QUERY_TASK_POLL_INTERVAL_MS = 1500
# 心跳间隔：须显著小于启动对账的 stale 阈值（默认 300s），供多 worker 区分活任务
QUERY_TASK_HEARTBEAT_SECONDS = 30
CANCEL_TARGET_WAIT_SECONDS = 3
CANCEL_TARGET_POLL_INTERVAL_SECONDS = 0.1


class QueryTaskNotFoundError(Exception):
    """查询任务或相关资源不存在。"""


class QueryTaskPermissionDeniedError(Exception):
    """无权操作查询任务。"""


@dataclass
class QueryTaskServiceResponse:
    """服务层返回给路由的响应数据。"""

    data: dict[str, Any]
    message: str


class QueryTaskService:
    """编排 SQL 异步任务、取消任务和看板运行时依赖。"""

    def __init__(
        self,
        *,
        query_history_service: QueryHistoryService,
        datasource_manager_factory: Callable[[], Any],
        fetch_http_query_task_result: Callable[..., Awaitable[dict[str, Any]]],
        stop_http_query_task: Callable[..., Awaitable[dict[str, Any]]],
        kill_mysql_query: Callable[..., Awaitable[dict[str, Any]]],
        query_task_poll_interval_ms: int = QUERY_TASK_POLL_INTERVAL_MS,
        cancel_target_wait_seconds: float = CANCEL_TARGET_WAIT_SECONDS,
        cancel_target_poll_interval_seconds: float = CANCEL_TARGET_POLL_INTERVAL_SECONDS,
    ) -> None:
        self.query_history_service = query_history_service
        self.datasource_manager_factory = datasource_manager_factory
        self.fetch_http_query_task_result = fetch_http_query_task_result
        self.stop_http_query_task = stop_http_query_task
        self.kill_mysql_query = kill_mysql_query
        self.query_task_poll_interval_ms = query_task_poll_interval_ms
        self.cancel_target_wait_seconds = cancel_target_wait_seconds
        self.cancel_target_poll_interval_seconds = cancel_target_poll_interval_seconds
        self.background_tasks: set[asyncio.Task] = set()

    async def execute_sql_for_member(
        self,
        execution_target: dict[str, Any],
        sql: str,
        limit: int,
        timeout: int | None,
        user_id: str | None = None,
        username: str | None = None,
        query_task_id: str | None = None,
        execution_id: str | None = None,
        skip_query_cache: bool = False,
        datasource_id: str | None = None,
        max_rows: int | None = None,
        source: QuerySource = QuerySource.SQL,
    ) -> dict[str, Any]:
        """执行单个成员数据源 SQL（经查询层编排门面）。

        ``source`` 由调用方按业务入口传入（看板=``DASHBOARD``），默认 SQL；
        只影响审计/统计口径，不参与执行分支。
        """
        spec = QuerySpec(
            source=source,
            datasource_group_id=datasource_id or "",
            sql=sql,
            limit=limit,
            timeout=timeout,
            max_rows=max_rows,
            query_task_id=query_task_id,
            execution_id=execution_id,
            skip_query_cache=skip_query_cache,
            user_id=user_id or "",
            username=username or "",
        )
        return await QueryOrchestrator().execute_member_raw_async(spec, execution_target)

    async def update_query_execution(
        self,
        execution_id: str,
        *,
        sql_status: str | None = None,
        remote_task_id: str | None = None,
        db_connection_id: str | None = None,
        execution_time_ms: int | None = None,
        result_row_count: int | None = None,
        result_payload: Any = None,
        error_message: str | None = None,
        started_time: datetime | None = None,
        finished_time: datetime | None = None,
    ) -> None:
        """更新 SQL 执行明细。"""
        await query_runtime.update_query_execution(
            execution_id,
            sql_status=sql_status,
            remote_task_id=remote_task_id,
            db_connection_id=db_connection_id,
            execution_time_ms=execution_time_ms,
            result_row_count=result_row_count,
            result_payload=result_payload,
            error_message=error_message,
            started_time=started_time,
            finished_time=finished_time,
        )

    async def create_task_record(
        self,
        *,
        task_type: str,
        datasource_id: str,
        query_type: str,
        request_payload: dict[str, Any],
        progress_text: str = "任务已创建，等待执行",
    ) -> dict[str, Any]:
        """创建异步任务记录。"""
        return await query_runtime.create_task_record(
            task_type=task_type,
            datasource_id=datasource_id,
            query_type=query_type,
            request_payload=request_payload,
            progress_text=progress_text,
        )

    async def update_query_task(
        self,
        task_id: str,
        *,
        status: str | None = None,
        progress_text: str | None = None,
        result_payload: Any = None,
        error_message: str | None = None,
        started_time: datetime | None = None,
        finished_time: datetime | None = None,
    ) -> dict[str, Any] | None:
        """更新异步查询任务。"""
        return await query_runtime.update_query_task(
            task_id,
            status=status,
            progress_text=progress_text,
            result_payload=result_payload,
            error_message=error_message,
            started_time=started_time,
            finished_time=finished_time,
        )

    async def create_query_task_for_user(
        self,
        body: QueryTaskCreateRequest,
        *,
        user_id: str,
        operator_name: str,
    ) -> dict[str, Any]:
        """创建用户发起的 SQL 异步查询任务。"""
        if body.query_type not in {"sql", "builder"}:
            raise ValueError(f"不支持的查询类型: {body.query_type}")
        body = body.model_copy(update={"user_id": user_id, "operator_name": operator_name})
        logger.info("query-tasks create: datasource_id=%s", body.datasource_id)
        datasource = await self.datasource_manager_factory().get_datasource_async(body.datasource_id)
        if not datasource:
            raise QueryTaskNotFoundError(f"数据源不存在: {body.datasource_id}")
        prepared_body = await query_runtime.prepare_sql_batch_request_for_execution(body, datasource)
        task_info = await query_runtime.create_query_task(prepared_body)
        self.schedule_query_task(task_info["task_id"])
        return {
            "task_id": task_info["task_id"],
            "task_type": task_info["task_type"],
            "status": task_info["status"],
            "poll_interval_ms": self.query_task_poll_interval_ms,
        }

    async def get_query_task_for_user(self, task_id: str, *, user_id: str) -> dict[str, Any]:
        """查询当前用户可见的异步任务状态与结果。"""
        task_data = await query_runtime.get_query_task(task_id)
        if not task_data:
            raise QueryTaskNotFoundError(f"任务不存在: {task_id}")
        response_data = query_runtime.build_query_task_response_data(task_data)
        executions = await query_runtime.get_query_executions(response_data["task_id"])
        if not self.is_query_task_owned_by_user(task_data, executions, user_id):
            raise QueryTaskNotFoundError(f"任务不存在: {task_id}")
        result_payload = response_data["result_payload"]
        task_type = response_data["task_type"]
        task_response = {
            "task_id": response_data["task_id"],
            "task_type": task_type,
            "datasource_id": response_data["datasource_id"],
            "query_type": response_data["query_type"],
            "status": response_data["status"],
            "progress_text": response_data["progress_text"],
            "remote_tasks": response_data["remote_tasks"],
            "error_message": response_data["error_message"],
            "started_time": response_data["started_time"],
            "finished_time": response_data["finished_time"],
            "create_time": response_data["create_time"],
            "update_time": response_data["update_time"],
        }
        if task_type == "sql":
            response_data = await self.merge_live_remote_sql_result(response_data, executions)
            task_response.update(
                {
                    "status": response_data["status"],
                    "progress_text": response_data["progress_text"],
                    "error_message": response_data["error_message"],
                }
            )
            result_payload = response_data["result_payload"]
            task_response["results"] = self.build_sql_task_results(result_payload, executions)
        elif task_type == "sql_batch":
            statement_results = await self.build_sql_batch_statement_results(response_data, executions)
            batch_status, progress_text, error_message = self.resolve_sql_batch_status(
                statement_results,
                fallback_status=response_data["status"],
                fallback_progress_text=response_data["progress_text"],
                fallback_error_message=response_data["error_message"],
            )
            task_response.update(
                {
                    "status": batch_status,
                    "progress_text": progress_text,
                    "error_message": error_message,
                    "statement_results": statement_results,
                }
            )
        else:
            task_response["executions"] = self.build_query_task_execution_views(executions)
            task_response["result"] = result_payload
        return task_response

    @staticmethod
    def build_query_task_execution_views(
        executions: Sequence[dict[str, Any] | ChatDataQueryExecution],
    ) -> list[dict[str, Any]]:
        """构造任务状态接口使用的轻量执行视图，避免和 results[] 重复返回业务结果。

        入参同时兼容 ORM 行（.to_dict()）与已转 dict 的执行数据（历史调用两种形态都有）。
        """
        view_fields = (
            "execution_id",
            "task_id",
            "datasource_id",
            "member_datasource_id",
            "member_name",
            "executor_type",
            "sql_status",
            "remote_task_id",
            "db_connection_id",
            "started_time",
            "finished_time",
            "operator_name",
        )
        views: list[dict[str, Any]] = []
        for execution in executions or []:
            source = execution if isinstance(execution, dict) else execution.to_dict()
            views.append({field: source.get(field) for field in view_fields if field in source})
        return views

    @classmethod
    def build_sql_task_results(
        cls,
        result_payload: Any,
        executions: list[dict[str, Any]],
    ) -> list[dict[str, Any]] | None:
        """构造 SQL/可视化任务统一成员结果，执行明细并入 results[]。"""
        if not isinstance(result_payload, dict):
            return cls.build_sql_task_results_from_executions(executions)

        raw_result_items = result_payload.get("results")
        result_items = list(raw_result_items) if isinstance(raw_result_items, list) else []
        execution_views = cls.build_sql_task_execution_result_views(executions)
        if not result_items and execution_views:
            return cls.normalize_sql_task_results(execution_views, result_payload)
        if not result_items:
            return []

        task_sql = (result_payload or {}).get("sql")
        execution_map = {
            str(item.get("member_datasource_id") or "").strip(): item
            for item in execution_views
            if str(item.get("member_datasource_id") or "").strip()
        }
        merged_results = [
            cls.fill_sql_task_result_defaults(
                cls.merge_sql_task_result_execution(
                    result,
                    cls.resolve_sql_task_execution_view(result, index, execution_views, execution_map),
                ),
                task_sql,
            )
            for index, result in enumerate(result_items)
        ]
        return cls.normalize_sql_task_results(merged_results, result_payload)

    @classmethod
    def build_sql_task_results_from_executions(cls, executions: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        """任务尚未写入结果时，用执行记录返回成员运行状态。"""
        execution_views = cls.build_sql_task_execution_result_views(executions)
        if not execution_views:
            return None
        return cls.normalize_sql_task_results(execution_views, {})

    @staticmethod
    def _build_execution_result_view(execution: Any) -> dict[str, Any]:
        """把单条执行记录转换为可并入成员结果的轻量字段。"""
        result_fields = (
            "execution_id",
            "member_datasource_id",
            "member_name",
            "executor_type",
            "remote_task_id",
            "db_connection_id",
            "started_time",
            "finished_time",
        )
        source = execution if isinstance(execution, dict) else execution.to_dict()
        view: dict[str, Any] = {field: source.get(field) for field in result_fields if field in source}
        if source.get("sql_status"):
            view["status"] = source.get("sql_status")
        execution_sql = source.get("query") or source.get("sql_text")
        if execution_sql:
            view["sql"] = execution_sql
        if source.get("execution_time_ms") is not None:
            view["execution_time_ms"] = source.get("execution_time_ms")
        if source.get("result_row_count") is not None:
            view["total_count"] = source.get("result_row_count")
        if source.get("error_message"):
            view["error"] = source.get("error_message")
        execution_payload = source.get("result_payload")
        if isinstance(execution_payload, dict):
            QueryTaskService.fill_sql_task_execution_payload_defaults(view, execution_payload)
        view.setdefault("columns", [])
        view.setdefault("data", [])
        view.setdefault("total_count", 0)
        view.setdefault("error", None)
        return view

    @classmethod
    def build_sql_task_execution_result_views(cls, executions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """将执行记录转换为可并入成员结果的轻量字段。"""
        return [QueryTaskService._build_execution_result_view(execution) for execution in (executions or [])]

    @staticmethod
    def fill_sql_task_execution_payload_defaults(view: dict[str, Any], execution_payload: dict[str, Any]) -> None:
        """执行明细已落库但任务结果未落库时，保留成员查询结果。"""
        for field in (
            "status",
            "columns",
            "data",
            "total_count",
            "execution_time_ms",
            "error",
            "cache_hit",
            "cache_age_seconds",
            "cache_expires_in_seconds",
        ):
            value = execution_payload.get(field)
            if value is not None and (field not in view or view.get(field) in (None, [], "")):
                view[field] = value

    @staticmethod
    def resolve_sql_task_execution_view(
        result: dict[str, Any],
        index: int,
        execution_views: list[dict[str, Any]],
        execution_map: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        """按成员 ID 匹配执行记录；单成员无 ID 时按位置兜底。"""
        member_datasource_id = str((result or {}).get("member_datasource_id") or "").strip()
        if member_datasource_id:
            return execution_map.get(member_datasource_id)
        if len(execution_views) == 1:
            return execution_views[0]
        if index < len(execution_views):
            return execution_views[index]
        return None

    @staticmethod
    def _merge_execution_view_field(merged: dict[str, Any], field: str, value: Any) -> None:
        """把单条执行视图字段合并到成员结果，遵循「已有值优先」原则。"""
        if field == "status":
            if not merged.get("status"):
                merged["status"] = value
            return
        if field in {"member_datasource_id", "member_name", "sql"}:
            if not merged.get(field):
                merged[field] = value
            return
        if field not in merged or merged.get(field) is None:
            merged[field] = value

    @staticmethod
    def merge_sql_task_result_execution(
        result: dict[str, Any],
        execution_view: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """把执行标识、运行时间等元数据合并到成员结果。"""
        merged = dict(result or {})
        if execution_view:
            for field, value in execution_view.items():
                QueryTaskService._merge_execution_view_field(merged, field, value)
        merged.setdefault("columns", [])
        merged.setdefault("data", [])
        merged["total_count"] = int(merged.get("total_count") or 0)
        merged.setdefault("error", None)
        return merged

    @staticmethod
    def fill_sql_task_result_defaults(
        merged: dict[str, Any],
        task_sql: Any,
    ) -> dict[str, Any]:
        """补齐成员结果的任务级默认字段。"""
        if not merged.get("sql") and task_sql:
            merged["sql"] = task_sql
        return merged

    @staticmethod
    def normalize_sql_task_results(
        results: list[dict[str, Any]],
        result_payload: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """复用统一结果规范，清理 success 等旧字段。"""
        normalized_payload = build_query_result_payload(
            sql=str((result_payload or {}).get("sql") or ""),
            member_results=results,
            fallback_error=(result_payload or {}).get("error"),
            fallback_execution_time_ms=(result_payload or {}).get("execution_time_ms"),
        )
        return normalized_payload["results"]

    async def cancel_query_task_for_user(
        self,
        task_id: str,
        *,
        user_id: str,
        operator_name: str,
    ) -> QueryTaskServiceResponse:
        """停止当前用户可操作的异步查询任务。"""
        task_data = await query_runtime.get_query_task(task_id)
        if not task_data:
            raise QueryTaskNotFoundError(f"任务不存在: {task_id}")
        executions = await self.query_history_service.get_execution_models(task_id)
        if not self.can_cancel_query_task(task_data, executions, user_id):
            raise QueryTaskPermissionDeniedError("无权停止该查询任务")
        resolved_operator = QueryHistoryService.task_operator_name(task_data) or operator_name
        cancellable_executions = [
            item for item in executions if str(item.sql_status or "").strip().lower() in {"pending", "running"}
        ]
        task_status = str(task_data.get("status") or "").strip().lower()
        if task_status in QUERY_TASK_FINAL_STATUSES and not cancellable_executions:
            return QueryTaskServiceResponse(
                data={
                    "task_id": task_id,
                    "status": task_data.get("status"),
                    "executions": self.build_query_task_execution_views(executions),
                },
                message="任务已结束",
            )
        execution_results = await self.cancel_query_executions(
            cancellable_executions or executions,
            cleanup_without_target=task_status in QUERY_TASK_FINAL_STATUSES,
            operator_name=resolved_operator,
        )
        cancel_status = QueryHistoryService.build_cancel_task_status(execution_results)
        if task_status not in QUERY_TASK_FINAL_STATUSES:
            await self.update_query_task(
                task_id,
                status=cancel_status,
                progress_text="已停止" if cancel_status == "cancelled" else "部分停止失败",
                error_message=None if cancel_status == "cancelled" else "部分 SQL 停止失败",
                finished_time=datetime.now(),
            )
        return QueryTaskServiceResponse(
            data={
                "task_id": task_id,
                "status": cancel_status,
                "executions": execution_results,
            },
            message="停止请求已提交",
        )

    async def cancel_query_execution_for_user(
        self,
        execution_id: str,
        *,
        user_id: str,
        operator_name: str,
    ) -> QueryTaskServiceResponse:
        """停止当前用户的一条 SQL 执行明细，不影响同批次其他 SQL。"""
        execution = await self.query_history_service.get_execution_model(execution_id)
        if not execution or str(execution.user_id or "").strip() != str(user_id or "").strip():
            raise QueryTaskNotFoundError(f"执行记录不存在: {execution_id}")
        resolved_operator = (
            str(execution.create_by_name or execution.update_by_name or "").strip() or str(operator_name or "").strip()
        )
        result = await self.cancel_query_execution(execution, operator_name=resolved_operator)
        return QueryTaskServiceResponse(
            data=result,
            message="停止请求已提交" if result.get("status") in {"cancelled", "kill_failed"} else "执行已结束",
        )

    async def cancel_running_query_history(
        self,
        *,
        datasource_id: str | None = None,
        user_id: str | None = None,
        operator_name: str | None = None,
    ) -> dict[str, Any]:
        """批量停止执行中的 SQL，并同步更新关联任务状态。"""
        return await self.query_history_service.cancel_running_query_history(
            datasource_id=datasource_id,
            user_id=user_id,
            operator_name=operator_name,
            cancel_execution=self.cancel_query_execution,
            update_execution=self.update_query_execution,
            update_task=self.update_query_task,
        )

    async def merge_live_remote_sql_result(
        self,
        response_data: dict[str, Any],
        executions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """在任务结果中合并 HTTP 远端实时结果。"""
        if response_data["task_type"] != "sql" or not response_data["remote_tasks"]:
            return response_data
        live_remote_payload = await self.build_live_remote_sql_result(response_data, executions)
        if not live_remote_payload:
            return response_data
        return {
            **response_data,
            "status": live_remote_payload["status"],
            "progress_text": live_remote_payload["progress_text"],
            "error_message": live_remote_payload["error_message"],
            "result_payload": live_remote_payload["result_payload"],
        }

    @staticmethod
    def _cancelled_remote_result(execution: dict[str, Any], execution_status: str) -> dict[str, Any]:
        return {
            "status": execution_status,
            "error": execution.get("error_message")
            or ("已人工取消" if execution_status == "cancelled" else "停止执行失败"),
            "columns": [],
            "data": [],
            "total_count": 0,
            "execution_time_ms": execution.get("execution_time_ms"),
        }

    def _build_remote_member_result(
        self,
        remote_task: dict[str, Any],
        statement: dict[str, Any],
        remote_result: dict[str, Any],
        execution: dict[str, Any] | None,
    ) -> dict[str, Any]:
        local_status = str((execution or {}).get("sql_status") or "").strip().lower()
        if local_status == "failed":
            # 本地已终态 failed 的成员以本地为准，防止远端实时状态把失败“复活”
            status, error = "failed", ((execution or {}).get("error_message") or remote_result.get("error"))
        else:
            status, error = query_result_status(remote_result), remote_result.get("error")
        return {
            "member_datasource_id": remote_task.get("member_datasource_id"),
            "member_name": remote_task.get("member_name"),
            "sql": statement.get("sql") or "",
            "status": status,
            "columns": remote_result.get("columns") or [],
            "data": remote_result.get("data") or [],
            "total_count": int(remote_result.get("total_count") or 0),
            "execution_time_ms": self.resolve_member_execution_time_ms(remote_result, execution),
            "error": error,
        }

    @staticmethod
    def _merge_member_result_into_statement(
        statement: dict[str, Any],
        member_result: dict[str, Any],
    ) -> None:
        existing = list(statement.get("results") or [])
        member_id = str(member_result["member_datasource_id"] or "").strip()
        statement["results"] = [
            item for item in existing if str((item or {}).get("member_datasource_id") or "").strip() != member_id
        ] + [member_result]
        aggregated = build_query_result_payload(
            sql=str(statement.get("sql") or ""),
            member_results=statement["results"],
            fallback_error=statement.get("error"),
        )
        statement.update(
            {
                "status": aggregated["status"],
                "error": aggregated["error"],
                "results": aggregated["results"],
            }
        )

    async def _apply_remote_task_result(
        self,
        statements: list[dict[str, Any]],
        remote_task: dict[str, Any],
        *,
        user_id: str,
        username: str,
        execution_by_remote_task_id: dict[str, dict[str, Any]],
    ) -> None:
        try:
            # 缺省/非法值经 TypeError/ValueError 统一走早退（int(None) 即 TypeError）
            statement_index = int(cast(int, remote_task.get("statement_index")))
        except (TypeError, ValueError):
            return
        if statement_index < 0 or statement_index >= len(statements):
            return
        statement = statements[statement_index]
        execution = execution_by_remote_task_id.get(str(remote_task.get("remote_task_id") or "").strip())
        try:
            remote_result = await self.fetch_http_query_task_result(
                str(remote_task.get("remote_task_id") or ""),
                str(remote_task.get("source_type") or "hive"),
                user_id=user_id,
                username=username,
            )
        except Exception as exc:
            # 单个远端状态接口抖动只降级该成员，不影响批次内其它语句
            logger.warning(
                "拉取 HTTP 远端结果失败，降级本地执行状态: remote_task_id=%s error=%s",
                remote_task.get("remote_task_id"),
                exc,
            )
            remote_result = self._degraded_remote_result(exc, execution)
        execution_status = str((execution or {}).get("sql_status") or "").strip().lower()
        if execution_status in {"cancelled", "kill_failed"}:
            remote_result = self._cancelled_remote_result(execution or {}, execution_status)
        member_result = self._build_remote_member_result(remote_task, statement, remote_result, execution)
        self._merge_member_result_into_statement(statement, member_result)

    async def _merge_http_member_results(
        self,
        response_data: dict[str, Any],
        statements: list[dict[str, Any]],
        remote_tasks: list[dict[str, Any]],
        executions: list[dict[str, Any]],
    ) -> None:
        request_payload = response_data.get("request_payload") or {}
        user_id = str(request_payload.get("user_id") or "").strip()
        username = str(request_payload.get("operator_name") or "").strip()
        execution_by_remote_task_id = {
            str((execution or {}).get("remote_task_id") or "").strip(): execution
            for execution in executions
            if str((execution or {}).get("remote_task_id") or "").strip()
        }
        for remote_task in remote_tasks:
            await self._apply_remote_task_result(
                statements,
                remote_task,
                user_id=user_id,
                username=username,
                execution_by_remote_task_id=execution_by_remote_task_id,
            )

    async def build_sql_batch_statement_results(
        self,
        response_data: dict[str, Any],
        executions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """补齐批次任务中不落库的 HTTP 成员结果。"""
        result_payload = response_data.get("result_payload") or {}
        statements = [dict(item) for item in result_payload.get("statement_results") or []]
        remote_tasks = list(response_data.get("remote_tasks") or [])
        if statements and remote_tasks:
            await self._merge_http_member_results(response_data, statements, remote_tasks, executions)
        if str(response_data.get("status") or "").strip().lower() == "cancelled":
            return self.mark_pending_sql_batch_statements_cancelled(statements)
        return statements

    @staticmethod
    def _cancel_pending_member_result(member_result: dict[str, Any]) -> dict[str, Any]:
        """把在途成员结果标记为已停止，已完成结果原样保留。"""
        next_member_result = dict(member_result or {})
        if query_result_status(next_member_result) in {"pending", "running"}:
            next_member_result["status"] = "cancelled"
            next_member_result["error"] = next_member_result.get("error") or "已人工取消"
        return next_member_result

    @staticmethod
    def mark_pending_sql_batch_statements_cancelled(statements: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """父任务收到停止信号后，查询接口即时把未完成 SQL 显示为已停止。"""
        for statement in statements:
            member_results = list(statement.get("results") or [])
            if not member_results:
                if query_result_status(statement) in {"pending", "running"}:
                    statement["status"] = "cancelled"
                    statement["error"] = statement.get("error") or "已人工取消"
                continue
            next_member_results = [
                QueryTaskService._cancel_pending_member_result(member_result) for member_result in member_results
            ]
            aggregated = build_query_result_payload(
                sql=str(statement.get("sql") or ""),
                member_results=next_member_results,
                fallback_error=statement.get("error"),
            )
            statement.update(
                {
                    "status": aggregated["status"],
                    "error": aggregated["error"],
                    "results": aggregated["results"],
                }
            )
        return statements

    @staticmethod
    def _sql_batch_pending_status(
        statuses: list[str],
        normalized_fallback_status: str,
    ) -> tuple[str, str, str | None] | None:
        """仍有语句在途时的状态判定；返回 None 表示无在途语句。"""
        if not any(status in {"pending", "running"} for status in statuses):
            return None
        if normalized_fallback_status == "cancelled":
            has_succeeded = any(status == "succeeded" for status in statuses)
            if has_succeeded:
                return "partial_success", "执行完成，部分 SQL 已停止", "部分 SQL 已停止"
            return "cancelled", "已停止", "已人工取消"
        return "running", "SQL 执行中", None

    @staticmethod
    def _sql_batch_error_summary(statement_results: list[dict[str, Any]]) -> str:
        errors = [str(item.get("error") or "").strip() for item in statement_results]
        return "；".join(error for error in errors if error) or "SQL 执行失败"

    @staticmethod
    def _sql_batch_terminal_status(
        statuses: list[str],
        statement_results: list[dict[str, Any]],
    ) -> tuple[str, str, str | None]:
        """全部语句到达终态后聚合批次结果。"""
        if statuses and all(status == "cancelled" for status in statuses):
            return "cancelled", "已停止", "已人工取消"
        has_succeeded = any(status == "succeeded" for status in statuses)
        has_failures = any(status != "succeeded" for status in statuses)
        if has_succeeded and has_failures:
            if all(status in {"succeeded", "cancelled"} for status in statuses):
                return "partial_success", "执行完成，部分 SQL 已停止", "部分 SQL 已停止"
            return "partial_success", "执行完成，部分 SQL 失败", "部分 SQL 执行失败"
        if has_succeeded:
            return "succeeded", "执行完成", None
        return "failed", "执行失败", QueryTaskService._sql_batch_error_summary(statement_results)

    @staticmethod
    def resolve_sql_batch_status(
        statement_results: list[dict[str, Any]],
        *,
        fallback_status: str = "running",
        fallback_progress_text: str | None = None,
        fallback_error_message: str | None = None,
    ) -> tuple[str, str, str | None]:
        """按批次内每条 SQL 的实时状态决定前端是否继续轮询。"""
        normalized_fallback_status = str(fallback_status or "").strip().lower()
        if not statement_results:
            # 后台任务尚未初始化结果时，必须保留任务表状态，不能把空数组误判为失败。
            return (
                normalized_fallback_status or "pending",
                str(fallback_progress_text or "任务已创建，等待执行"),
                fallback_error_message,
            )
        statuses = [query_result_status(item) for item in statement_results]
        if normalized_fallback_status == "failed" and not any(status in {"pending", "running"} for status in statuses):
            return (
                normalized_fallback_status,
                str(fallback_progress_text or "执行失败"),
                fallback_error_message,
            )
        pending_status = QueryTaskService._sql_batch_pending_status(statuses, normalized_fallback_status)
        if pending_status is not None:
            return pending_status
        return QueryTaskService._sql_batch_terminal_status(statuses, statement_results)

    async def build_live_remote_sql_result(
        self,
        response_data: dict[str, Any],
        executions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        """基于远端 HTTP 任务实时拉取 SQL 结果。"""
        remote_tasks = list(response_data.get("remote_tasks") or [])
        if not remote_tasks:
            return None
        remote_results = await self.fetch_remote_member_results(response_data, remote_tasks, executions or [])
        merged_results = self.merge_direct_and_remote_results(response_data, remote_tasks, remote_results)
        task_status, progress_text, error_message = self.resolve_live_result_status(remote_results, merged_results)
        return {
            "status": task_status,
            "progress_text": progress_text,
            "error_message": error_message,
            "result_payload": build_query_result_payload(
                sql=((response_data.get("request_payload") or {}).get("sql") or ""),
                member_results=merged_results,
                fallback_error=error_message,
            ),
        }

    async def fetch_remote_member_results(
        self,
        response_data: dict[str, Any],
        remote_tasks: list[dict[str, Any]],
        executions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """拉取每个 HTTP 远端任务的实时结果。"""
        request_payload = response_data.get("request_payload") or {}
        remote_user_id = str((request_payload or {}).get("user_id") or "").strip()
        remote_username = str((request_payload or {}).get("operator_name") or "").strip()
        # return_exceptions：单个远端状态接口抖动不能炸掉整个任务查询，
        # 异常成员降级为本地执行状态（见 _degraded_remote_result）
        fetched_results = await asyncio.gather(
            *[
                self.fetch_http_query_task_result(
                    str(remote_task.get("remote_task_id") or ""),
                    str(remote_task.get("source_type") or "hive"),
                    user_id=remote_user_id,
                    username=remote_username,
                )
                for remote_task in remote_tasks
            ],
            return_exceptions=True,
        )
        local_execution_map = self.build_execution_map_by_member(executions)
        cancelled_execution_map = self.build_cancelled_execution_map(executions)
        remote_results: list[dict[str, Any]] = []
        for remote_task, fetched in zip(remote_tasks, fetched_results, strict=True):
            # gather(return_exceptions=True) 会返回 BaseException（含 CancelledError），一并降级
            if isinstance(fetched, BaseException):
                logger.warning(
                    "拉取 HTTP 远端结果失败，降级本地执行状态: remote_task_id=%s error=%s",
                    remote_task.get("remote_task_id"),
                    fetched,
                )
                member_datasource_id = str(remote_task.get("member_datasource_id") or "").strip()
                remote_results.append(
                    self._degraded_remote_result(fetched, local_execution_map.get(member_datasource_id))
                )
            else:
                remote_results.append(fetched)
        return [
            self.build_remote_member_result(
                response_data,
                remote_task,
                remote_result,
                local_execution_map.get(str(remote_task.get("member_datasource_id") or "").strip()),
                cancelled_execution_map.get(str(remote_task.get("member_datasource_id") or "").strip()),
            )
            for remote_task, remote_result in zip(remote_tasks, remote_results, strict=True)
        ]

    @staticmethod
    def _degraded_remote_result(error: BaseException, local_execution: dict[str, Any] | None) -> dict[str, Any]:
        """远端状态接口异常时的降级结果：沿用本地执行状态，不武断终态化。

        status 收敛到远端结果契约枚举内（见 result.query_result_status）：本地专属状态
        （如 kill_failed）归一为 failed，避免未识别值透传给下游状态汇总。
        """
        local_status = query_result_status(
            {
                "status": str((local_execution or {}).get("sql_status") or "").strip().lower() or QUERY_RUNNING_STATUS,
            }
        )
        return {
            "status": local_status,
            "error": (local_execution or {}).get("error_message") or f"远端状态查询失败: {error}",
            "columns": [],
            "data": [],
            "total_count": 0,
        }

    def build_remote_member_result(
        self,
        response_data: dict[str, Any],
        remote_task: dict[str, Any],
        remote_result: dict[str, Any],
        local_execution: dict[str, Any] | None,
        local_cancelled_execution: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """构造单个成员远端实时结果。"""
        base: dict[str, Any] = {
            "member_datasource_id": remote_task.get("member_datasource_id"),
            "member_name": remote_task.get("member_name"),
            "sql": ((response_data.get("request_payload") or {}).get("sql") or ""),
            "columns": [],
            "data": [],
            "total_count": 0,
        }
        if local_cancelled_execution:
            base.update(self._cancelled_member_fields(remote_result, local_cancelled_execution))
        else:
            base.update(self._executed_member_fields(remote_result, local_execution))
        return base

    def _cancelled_member_fields(
        self,
        remote_result: dict[str, Any],
        local_cancelled_execution: dict[str, Any],
    ) -> dict[str, Any]:
        """本地已取消/停止失败的成员结果字段。"""
        local_status = str(local_cancelled_execution.get("sql_status") or "cancelled").strip().lower()
        return {
            "status": local_status,
            "execution_time_ms": self.resolve_member_execution_time_ms(remote_result, local_cancelled_execution),
            "started_time": remote_result.get("started_time") or local_cancelled_execution.get("started_time"),
            "finished_time": remote_result.get("finished_time") or local_cancelled_execution.get("finished_time"),
            "error": local_cancelled_execution.get("error_message")
            or ("已人工取消" if local_status == "cancelled" else "停止执行失败"),
        }

    def _executed_member_fields(
        self,
        remote_result: dict[str, Any],
        local_execution: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """正常执行的成员结果字段。"""
        local_status = str((local_execution or {}).get("sql_status") or "").strip().lower()
        if local_status == "failed":
            # 本地已终态 failed 的成员以本地为准，防止远端实时状态把失败“复活”
            return self._member_result_fields(
                status="failed",
                error=(local_execution or {}).get("error_message") or remote_result.get("error"),
                remote_result=remote_result,
                local_execution=local_execution,
            )
        return self._member_result_fields(
            status=query_result_status(remote_result),
            error=remote_result.get("error"),
            remote_result=remote_result,
            local_execution=local_execution,
        )

    def _member_result_fields(
        self,
        *,
        status: str,
        error: str | None,
        remote_result: dict[str, Any],
        local_execution: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """构造成员结果公共字段。"""
        return {
            "status": status,
            "columns": remote_result.get("columns") or [],
            "data": remote_result.get("data") or [],
            "total_count": int(remote_result.get("total_count") or 0),
            "execution_time_ms": self.resolve_member_execution_time_ms(remote_result, local_execution),
            "started_time": remote_result.get("started_time") or (local_execution or {}).get("started_time"),
            "finished_time": remote_result.get("finished_time") or (local_execution or {}).get("finished_time"),
            "error": error,
            "error_source": remote_result.get("error_source")
            or ("service" if (local_execution or {}).get("sql_status") == "failed" else "remote_http")
            if error
            else None,
        }

    def merge_direct_and_remote_results(
        self,
        response_data: dict[str, Any],
        remote_tasks: list[dict[str, Any]],
        remote_results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """合并本地直连结果和 HTTP 远端结果。"""
        local_result_payload = response_data.get("result_payload")
        local_results = (
            list(local_result_payload.get("results") or []) if isinstance(local_result_payload, dict) else []
        )
        remote_member_ids = {
            str(item.get("member_datasource_id") or "").strip()
            for item in remote_tasks
            if str(item.get("member_datasource_id") or "").strip()
        }
        direct_results = [
            item
            for item in local_results
            if str((item or {}).get("member_datasource_id") or "").strip() not in remote_member_ids
        ]
        for item in direct_results:
            if str(item.get("status") or "").strip().lower() in {"failed", "cancelled"}:
                item.setdefault("error_source", "datasource")
        for item in remote_results:
            if str(item.get("status") or "").strip().lower() in {"failed", "cancelled"}:
                item.setdefault("error_source", "remote_http")
        return direct_results + remote_results

    def resolve_live_result_status(
        self,
        remote_results: list[dict[str, Any]],
        merged_results: list[dict[str, Any]],
    ) -> tuple[str, str, str | None]:
        """按成员结果汇总任务状态、进度文案和错误信息。"""
        statuses = {str(item.get("status") or "").strip().lower() for item in remote_results}
        if "pending" in statuses or "running" in statuses:
            return "running", "SQL 执行中", None
        if statuses and statuses <= {"cancelled"}:
            return "cancelled", "已停止", self.resolve_live_error_message("cancelled", merged_results)
        if any(query_result_succeeded(item) for item in merged_results):
            return "succeeded", "执行完成", None
        return "failed", "执行失败", self.resolve_live_error_message("failed", merged_results)

    @staticmethod
    def resolve_live_error_message(task_status: str, merged_results: list[dict[str, Any]]) -> str | None:
        """汇总远端实时结果错误信息。"""
        if task_status == "running" or any(query_result_succeeded(item) for item in merged_results):
            return None
        error_messages = [
            str(item.get("error") or "").strip() for item in merged_results if str(item.get("error") or "").strip()
        ]
        return "；".join(error_messages) if error_messages else "所有执行对象均执行失败"

    @staticmethod
    def build_cancelled_execution_map(executions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """按成员数据源整理本地已取消的执行记录。"""
        cancelled_map: dict[str, dict[str, Any]] = {}
        for execution in executions:
            status = str((execution or {}).get("sql_status") or "").strip().lower()
            member_datasource_id = str((execution or {}).get("member_datasource_id") or "").strip()
            if member_datasource_id and status in {"cancelled", "kill_failed"}:
                cancelled_map[member_datasource_id] = execution
        return cancelled_map

    @staticmethod
    def build_execution_map_by_member(executions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """按成员数据源整理本地执行记录。"""
        execution_map: dict[str, dict[str, Any]] = {}
        for execution in executions:
            member_datasource_id = str((execution or {}).get("member_datasource_id") or "").strip()
            if member_datasource_id:
                execution_map[member_datasource_id] = execution
        return execution_map

    @staticmethod
    def resolve_member_execution_time_ms(
        remote_result: dict[str, Any],
        local_execution: dict[str, Any] | None,
    ) -> int | None:
        """解析成员远端结果耗时，远端缺失时使用本地执行记录兜底。"""
        if not local_execution:
            return query_runtime.resolve_execution_time_ms((remote_result or {}).get("execution_time_ms"))
        remote_time_ms = (remote_result or {}).get("execution_time_ms")
        return query_runtime.resolve_execution_time_ms(
            remote_time_ms if remote_time_ms is not None else local_execution.get("execution_time_ms"),
            local_execution.get("started_time"),
            local_execution.get("finished_time"),
        )

    @staticmethod
    def is_query_task_owned_by_user(task_data: dict[str, Any], executions: list[dict[str, Any]], user_id: str) -> bool:
        """判断查询任务是否归属于当前用户；owner 信息缺失时 fail-close 拒绝。"""
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id:
            return False
        owner_ids = {
            str(item.get("user_id") or item.get("create_by") or item.get("update_by") or "").strip()
            for item in executions or []
            if str(item.get("user_id") or item.get("create_by") or item.get("update_by") or "").strip()
        }
        task_user_id = QueryHistoryService.task_user_id(task_data)
        if task_user_id:
            owner_ids.add(task_user_id)
        # 任务创建时必填 user_id；owner 集合为空说明数据异常，按无权处理
        return bool(owner_ids) and normalized_user_id in owner_ids

    @staticmethod
    def can_cancel_query_task(
        task_data: dict[str, Any], executions: list[ChatDataQueryExecution], user_id: str
    ) -> bool:
        """判断当前用户是否可停止查询任务；owner 信息缺失时 fail-close 拒绝。"""
        owner_ids = {str(item.user_id or "").strip() for item in executions if str(item.user_id or "").strip()}
        task_user_id = QueryHistoryService.task_user_id(task_data)
        if not owner_ids and task_user_id:
            owner_ids.add(task_user_id)
        # 同 is_query_task_owned_by_user：owner 集合为空视为数据异常，拒绝停止
        return bool(owner_ids) and str(user_id or "").strip() in owner_ids

    async def resolve_execution_target(self, execution: ChatDataQueryExecution) -> dict[str, Any] | None:
        """根据执行记录解析实际成员数据源配置。"""
        member_ids = [execution.member_datasource_id] if execution.member_datasource_id else None
        targets = await self.datasource_manager_factory().get_execution_datasources_async(
            execution.datasource_id,
            member_ids,
        )
        return targets[0] if targets else None

    async def wait_for_cancel_target(self, execution: ChatDataQueryExecution) -> ChatDataQueryExecution:
        """短暂等待执行记录写入远端任务ID或数据库连接ID。"""
        if self.has_cancel_target_or_finished(execution):
            return execution
        deadline = time.monotonic() + self.cancel_target_wait_seconds
        while time.monotonic() < deadline:
            await asyncio.sleep(self.cancel_target_poll_interval_seconds)
            latest = await self.query_history_service.get_execution_model(execution.execution_id)
            if latest is None:
                return execution
            QueryHistoryService.copy_execution_identity(execution, latest)
            if self.has_cancel_target_or_finished(execution):
                return execution
        return execution

    @staticmethod
    def has_cancel_target_or_finished(execution: ChatDataQueryExecution) -> bool:
        """判断执行记录是否已经可停止或不再运行。"""
        return bool(
            execution.remote_task_id
            or execution.db_connection_id
            or str(execution.sql_status or "").strip().lower() != "running"
        )

    async def cancel_query_executions(
        self,
        executions: list[ChatDataQueryExecution],
        *,
        cleanup_without_target: bool = False,
        operator_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """并发停止多条 SQL 执行记录。"""

        async def cancel_one(execution: ChatDataQueryExecution, *, operator_name: str | None = None) -> dict[str, Any]:
            return await self.cancel_query_execution(
                execution,
                cleanup_without_target=cleanup_without_target,
                operator_name=operator_name,
            )

        return await asyncio.gather(
            *[
                self.query_history_service.cancel_query_execution_for_batch(
                    item,
                    cancel_execution=cancel_one,
                    update_execution=self.update_query_execution,
                    operator_name=operator_name,
                )
                for item in executions
            ]
        )

    async def cancel_query_execution(
        self,
        execution: ChatDataQueryExecution,
        *,
        cleanup_without_target: bool = False,
        operator_name: str | None = None,
    ) -> dict[str, Any]:
        """停止一条 SQL 执行记录对应的远端任务或直连查询。"""
        current_status = str(execution.sql_status or "").strip().lower()
        if current_status not in {"pending", "running"}:
            return self.noop_cancel_result(execution, current_status)
        execution = await self.wait_for_cancel_target(execution)
        current_status = str(execution.sql_status or "").strip().lower()
        if current_status not in {"pending", "running"}:
            return self.noop_cancel_result(execution, current_status)
        stop_result = await self.stop_query_execution(execution, current_status, cleanup_without_target, operator_name)
        success = str(stop_result.get("status") or "").strip().lower() == "succeeded"
        await self.update_query_execution(
            execution.execution_id,
            sql_status="cancelled" if success else "kill_failed",
            error_message="已人工取消" if success else str(stop_result.get("error") or "停止执行失败"),
            finished_time=datetime.now(),
        )
        return {
            "execution_id": execution.execution_id,
            "status": "cancelled" if success else "kill_failed",
            "message": None if success else str(stop_result.get("error") or "停止执行失败"),
        }

    @staticmethod
    def noop_cancel_result(execution: ChatDataQueryExecution, status: str) -> dict[str, Any]:
        """构造无需停止的执行结果。"""
        return {
            "execution_id": execution.execution_id,
            "status": status,
            "message": "无需停止",
        }

    async def stop_query_execution(
        self,
        execution: ChatDataQueryExecution,
        current_status: str,
        cleanup_without_target: bool,
        operator_name: str | None,
    ) -> dict[str, Any]:
        """根据执行记录类型发送停止请求。"""
        if execution.remote_task_id:
            return await self.stop_remote_query_execution(execution, operator_name)
        if execution.db_connection_id:
            return await self.stop_mysql_query_execution(execution)
        if current_status == "pending" or cleanup_without_target:
            return {"status": "succeeded", "error": None}
        return {"status": "failed", "error": "缺少可停止的远端任务ID或数据库连接ID"}

    async def stop_remote_query_execution(
        self,
        execution: ChatDataQueryExecution,
        operator_name: str | None,
    ) -> dict[str, Any]:
        """停止 HTTP 远端查询任务。"""
        target = None
        try:
            target = await self.resolve_execution_target(execution)
        except Exception as err:
            logger.warning("解析 HTTP 停止数据源失败: execution_id=%s error=%s", execution.execution_id, err)
        source_type = str((target or {}).get("source_type") or "hive").strip().lower() or "hive"
        return await self.stop_http_query_task(
            execution.remote_task_id,
            source_type,
            user_id=str(execution.user_id or "").strip(),
            username=str(operator_name or "").strip(),
        )

    async def stop_mysql_query_execution(self, execution: ChatDataQueryExecution) -> dict[str, Any]:
        """停止 MySQL 直连查询。"""
        try:
            target = await self.resolve_execution_target(execution)
        except Exception as err:
            logger.error(
                "解析 MySQL 停止数据源失败: execution_id=%s error=%s", execution.execution_id, err, exc_info=True
            )
            return {"status": "failed", "error": "解析执行数据源失败"}
        return await self.kill_mysql_query(target or {}, execution.db_connection_id)

    async def run_sql_query_task(self, task_id: str, task: dict[str, Any]) -> None:
        """后台执行 SQL 查询任务。"""
        await QueryOrchestrator().execute_task_async(task_id, task)

    async def run_sql_batch_query_task(self, task_id: str, task: dict[str, Any]) -> None:
        """后台执行顺序 SQL 批次任务。"""
        await query_runtime.run_sql_batch_query_task(task_id, task)

    async def run_query_task(self, task_id: str) -> None:
        """后台执行查询任务。"""
        task = await query_runtime.get_query_task(task_id)
        if not task:
            logger.warning("异步查询任务不存在: %s", task_id)
            return
        heartbeat = asyncio.create_task(self._query_task_heartbeat(task_id))
        try:
            await self.dispatch_query_task(task_id, task)
        except ValueError as err:
            logger.error("异步查询任务失败: %s", err)
            await self.mark_query_task_failed(task_id, str(err))
        except Exception as err:
            logger.error("异步查询任务异常: %s", err, exc_info=True)
            await self.mark_query_task_failed(task_id, "执行失败，请稍后重试或联系管理员")
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def _query_task_heartbeat(self, task_id: str) -> None:
        """运行期刷新任务 update_time；进程退出后停止，供启动对账识别僵尸任务。"""
        while True:
            await asyncio.sleep(QUERY_TASK_HEARTBEAT_SECONDS)
            try:
                await query_runtime.touch_query_task(task_id)
            except Exception as err:  # 心跳失败不影响查询本身
                logger.warning("查询任务心跳刷新失败: task_id=%s error=%s", task_id, err)

    async def dispatch_query_task(self, task_id: str, task: dict[str, Any]) -> None:
        """按任务类型分发后台查询任务。"""
        task_type = task.get("task_type")
        if task_type == "sql":
            await self.run_sql_query_task(task_id, task)
            return
        if task_type == "sql_batch":
            await self.run_sql_batch_query_task(task_id, task)
            return
        if task_type == "dashboard_panel":
            await dashboard_runtime.run_dashboard_panel_task(task_id, task)
            return
        if task_type == "dashboard_batch":
            await dashboard_runtime.run_dashboard_batch_task(task_id, task)
            return
        raise ValueError(f"不支持的任务类型: {task_type}")

    async def mark_query_task_failed(self, task_id: str, error_message: str) -> None:
        """将后台任务标记为失败。"""
        await self.update_query_task(
            task_id,
            status="failed",
            progress_text="执行失败",
            error_message=error_message,
            finished_time=datetime.now(),
        )

    def schedule_query_task(self, task_id: str) -> None:
        """提交后台异步查询任务。"""
        background_task = asyncio.create_task(self.run_query_task(task_id))
        self.background_tasks.add(background_task)
        background_task.add_done_callback(self.background_tasks.discard)

    def configure_dashboard_runtime(self) -> None:
        """向看板运行时注入查询任务依赖。"""
        dashboard_runtime.configure_dashboard_runtime(
            dashboard_runtime.DashboardRuntime(
                get_datasource_manager=self.datasource_manager_factory,
                execute_sql_for_member=lambda **kwargs: self.execute_sql_for_member(**kwargs),
                create_task_record=lambda **kwargs: self.create_task_record(**kwargs),
                update_query_task=lambda *args, **kwargs: self.update_query_task(*args, **kwargs),
                is_query_task_cancelled=lambda task_id: query_runtime.is_query_task_cancelled(task_id),
                schedule_query_task=lambda task_id: self.schedule_query_task(task_id),
                query_task_poll_interval_ms=self.query_task_poll_interval_ms,
            )
        )
