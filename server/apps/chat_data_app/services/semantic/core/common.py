"""语义核心共享的纯函数和常量。"""

from typing import Any, Dict, Set

LOGICAL_TYPE_VALUES = {"text", "number", "date", "datetime", "enum"}
SUPPORTED_RELATIONSHIP_TYPES = frozenset({"one_to_one", "one_to_many", "many_to_one"})
DEFAULT_RELATIONSHIP_TYPE = "one_to_one"


def normalize_relationship_type(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return DEFAULT_RELATIONSHIP_TYPE
    if normalized not in SUPPORTED_RELATIONSHIP_TYPES:
        supported = ", ".join(sorted(SUPPORTED_RELATIONSHIP_TYPES))
        raise ValueError(f"不支持的语义关系基数: {normalized}，仅支持: {supported}")
    return normalized


def build_table_columns_map(datasource: Dict) -> Dict[str, Set[str]]:
    table_columns: Dict[str, Set[str]] = {}
    for table in datasource.get("ds_tables", []) or []:
        table_name = table.get("table_name")
        if table_name:
            table_columns[table_name] = {
                column.get("name")
                for column in table.get("columns", []) or []
                if column.get("name")
            }
    return table_columns


def normalize_logical_type(value: Any) -> str:
    logical_type = str(value or "").strip().lower()
    return logical_type if logical_type in LOGICAL_TYPE_VALUES else ""


def ensure_dimension_logical_type(dimension: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(dimension or {})
    normalized["logical_type"] = normalize_logical_type(normalized.get("logical_type")) or "text"
    normalized.pop("type", None)
    return normalized


def is_time_logical_dimension(config: Dict[str, Any]) -> bool:
    return normalize_logical_type((config or {}).get("logical_type")) in {"date", "datetime"}


def is_number_logical_dimension(config: Dict[str, Any]) -> bool:
    return normalize_logical_type((config or {}).get("logical_type")) == "number"
