"""查询层契约：QuerySpec / QueryResult。

目标：查询构造、执行、结果装配各只有一处权威；任何调用方只描述“查什么”，
不关心数据源连接、执行器、缓存、超时和 member 权限实现。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class QuerySource(str, Enum):
    """查询来源；用于 query_mode/审计/迁移统计，不参与执行分支。"""

    ONTOLOGY = "ontology"
    SEMANTIC = "semantic"
    SQL = "sql"
    DASHBOARD = "dashboard"
    PIPELINE = "pipeline"


@dataclass(frozen=True)
class QuerySpec:
    """一次查询的完整、可序列化描述（用于回放/审计/迁移）。

    ``limit/timeout/max_rows/query_task_id/execution_id/statement_index/skip_query_cache``
    由 ``execute_member_*`` 路径消费；``execute_agent_async`` 走 runtime 的
    agent 任务配置（limit/timeout 由运行时设置决定），当前不读取这些字段。
    """

    source: QuerySource | str
    datasource_group_id: str = ""
    member_datasource_ids: tuple[str, ...] = ()
    sql: str = ""
    limit: int = 100
    timeout: int | None = 30
    max_rows: int | None = None
    query_task_id: str | None = None
    execution_id: str | None = None
    statement_index: int | None = None
    skip_query_cache: bool = False
    user_id: str = ""
    username: str = ""
    query_mode: str = ""
    query_text: str = ""
    conversation_id: str | None = None
    semantic_config_id: str | None = None
    object_type: str | None = None
    dataset_id: int | None = None
    dataset_version_id: str | None = None
    trace_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        source = self.source.value if isinstance(self.source, QuerySource) else str(self.source)
        payload["source"] = source
        payload["member_datasource_ids"] = list(self.member_datasource_ids)
        return payload


@dataclass
class QueryResult:
    """所有查询入口统一的返回契约；members 复用现有 results[] 字段。"""

    source: str
    query_mode: str = ""
    sql: str = ""
    members: list[dict[str, Any]] = field(default_factory=list)
    total_count: int = 0
    execution_time_ms: int = 0
    lineage: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_legacy_payload(self) -> dict[str, Any]:
        """兼容旧查询结果协议；迁移期调用方仍消费 results[]/status/error。"""
        from ..sql_run.result import build_query_result_payload

        payload = build_query_result_payload(sql=self.sql, member_results=self.members, fallback_error=self.error)
        if self.query_mode:
            payload["query_mode"] = self.query_mode
        if self.lineage:
            payload["lineage"] = self.lineage
        if self.warnings:
            payload["warnings"] = self.warnings
        return payload


def normalize_member_result(result: dict[str, Any]) -> dict[str, Any]:
    """把执行层返回收敛成统一 member 结构；保留未知字段，避免丢审计信息。"""
    member = dict(result or {})
    member["status"] = str(member.get("status") or "").strip() or "failed"
    member["columns"] = list(member.get("columns") or [])
    member["data"] = list(member.get("data") or [])
    member["total_count"] = int(member.get("total_count") or 0)
    member["execution_time_ms"] = int(member.get("execution_time_ms") or 0)
    member["error"] = member.get("error")
    return member
