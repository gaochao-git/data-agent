"""语义 SQL 的 FROM/JOIN 片段编译。"""

from typing import Any, Dict, List, Optional


def _build_from_ref(table: str, alias: Optional[str] = None) -> str:
    table_name = str(table or "").strip()
    alias_name = str(alias or "").strip()
    if not table_name:
        return ""
    if not alias_name or alias_name == table_name:
        return table_name
    return f"{table_name} {alias_name}"


def build_join_clauses(
    main_table: str,
    joins: List[Dict[str, Any]],
    main_alias: Optional[str] = None,
) -> List[str]:
    """生成 FROM 和 JOIN 片段，保持 Builder DSL 的原有字段兼容。"""
    effective_main_alias = main_alias or main_table
    parts: List[str] = [f"FROM {_build_from_ref(main_table, effective_main_alias)}"]
    for join in joins:
        right_table = join.get("rightTable")
        if not right_table:
            continue
        right_alias = str(join.get("rightAlias") or right_table).strip()
        join_type = (join.get("joinType") or "LEFT").upper()
        clause = f"{join_type} JOIN {_build_from_ref(right_table, right_alias)}"

        join_conditions = join.get("joinConditions") or []
        if join_conditions:
            pairs = []
            for condition in join_conditions:
                left_table = (
                    condition.get("leftAlias")
                    or condition.get("leftTable")
                    or join.get("leftAlias")
                    or join.get("leftTable")
                    or effective_main_alias
                )
                left_column = condition.get("leftColumn")
                right_column = condition.get("rightColumn")
                if left_table and left_column and right_column:
                    pairs.append(f"{left_table}.{left_column} = {right_alias}.{right_column}")
            on_expr = " AND ".join(pairs)
        else:
            left_table = join.get("leftAlias") or join.get("leftTable") or effective_main_alias
            left_column = join.get("leftColumn")
            right_column = join.get("rightColumn")
            on_expr = (
                f"{left_table}.{left_column} = {right_alias}.{right_column}"
                if left_table and left_column and right_column
                else ""
            )
        parts.append(f"{clause} ON {on_expr}" if on_expr else clause)
    return parts
