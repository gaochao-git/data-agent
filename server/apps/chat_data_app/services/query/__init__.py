"""查询层：契约与编排入口。"""

from .contract import QueryResult, QuerySource, QuerySpec, normalize_member_result
from .orchestrator import QueryOrchestrator

__all__ = [
    "QueryOrchestrator",
    "QueryResult",
    "QuerySource",
    "QuerySpec",
    "normalize_member_result",
]
