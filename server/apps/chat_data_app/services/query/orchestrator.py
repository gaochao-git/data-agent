"""查询编排层：统一查询执行与结果归一化。

当前先做兼容薄门面：不改变现有调用方行为，只提供统一 QuerySpec/QueryResult。
迁移顺序：object_projection → semantic → SQL/看板 → Pipeline。
"""

from __future__ import annotations

from typing import Any

from .contract import QueryResult, QuerySource, QuerySpec, normalize_member_result


def _source_value(spec: QuerySpec) -> str:
    return spec.source.value if isinstance(spec.source, QuerySource) else str(spec.source)


def _total_count(members: list[dict[str, Any]]) -> int:
    return sum(int(member.get("total_count") or 0) for member in members)


def _execution_time(members: list[dict[str, Any]]) -> int:
    return max((int(member.get("execution_time_ms") or 0) for member in members), default=0)


class QueryOrchestrator:
    """查询编排唯一入口；调用方不得直接连数据源。"""

    async def execute_member_async(self, spec: QuerySpec, execution_target: dict[str, Any]) -> QueryResult:
        """单 member 查询（对象投影、Pipeline 读取等）。"""
        from ..sql_run.runtime import execute_sql_for_member

        result = await execute_sql_for_member(
            execution_target,
            spec.sql,
            limit=spec.limit,
            timeout=spec.timeout,
            user_id=spec.user_id or None,
            username=spec.username or None,
            datasource_id=spec.datasource_group_id or None,
        )
        member = normalize_member_result(result)
        return QueryResult(
            source=_source_value(spec),
            query_mode=spec.query_mode,
            sql=spec.sql,
            members=[member],
            total_count=member["total_count"],
            execution_time_ms=member["execution_time_ms"],
            lineage=self._lineage(spec),
            warnings=[] if member["status"] == "succeeded" else [str(member.get("error") or "查询失败")],
        )

    async def execute_agent_async(self, spec: QuerySpec) -> QueryResult:
        """多 member / Agent 查询：复用统一 runtime 的 execute_agent_sql。"""
        from ..sql_run.runtime import execute_agent_sql

        payload = await execute_agent_sql(
            spec.sql,
            datasource_id=spec.datasource_group_id or None,
            member_datasource_ids=list(spec.member_datasource_ids),
            user_id=spec.user_id or None,
            semantic_config_id=spec.semantic_config_id,
            conversation_id=None,
            operator_name=spec.username or None,
        )
        members = [normalize_member_result(item) for item in payload.get("results") or [] if isinstance(item, dict)]
        return QueryResult(
            source=_source_value(spec),
            query_mode=spec.query_mode or str(payload.get("query_mode") or ""),
            sql=str(payload.get("sql") or spec.sql),
            members=members,
            total_count=_total_count(members),
            execution_time_ms=_execution_time(members),
            lineage=self._lineage(spec),
            warnings=[str(payload.get("error"))] if payload.get("error") else [],
        )

    @staticmethod
    def _lineage(spec: QuerySpec) -> dict[str, Any]:
        return {
            "source": spec.source.value if isinstance(spec.source, QuerySource) else str(spec.source),
            "object_type": spec.object_type or "",
            "dataset_id": spec.dataset_id,
            "dataset_version_id": spec.dataset_version_id or "",
            "semantic_config_id": spec.semantic_config_id or "",
            "trace_id": spec.trace_id,
        }
