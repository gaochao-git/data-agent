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
    """一次查询的完整、可序列化描述（用于回放/审计/迁移）。"""

    source: QuerySource | str
    datasource_group_id: str = ""
    member_datasource_ids: tuple[str, ...] = ()
    sql: str = ""
    limit: int = 100
    timeout: int = 30
    user_id: str = ""
    username: str = ""
    query_mode: str = ""
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
