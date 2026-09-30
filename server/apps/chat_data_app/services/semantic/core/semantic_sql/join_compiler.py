"""语义 SQL 的 FROM/JOIN 片段编译。"""

from typing import Any


def _build_from_ref(table: str, alias: str | None = None) -> str:
    table_name = str(table or "").strip()
    alias_name = str(alias or "").strip()
    if not table_name:
        return ""
    if not alias_name or alias_name == table_name:
        return table_name
    return f"{table_name} {alias_name}"


def _condition_left_table(
    condition: dict[str, Any],
    join: dict[str, Any],
    effective_main_alias: str,
) -> str | None:
    return (
        condition.get("leftAlias")
        or condition.get("leftTable")
        or join.get("leftAlias")
        or join.get("leftTable")
        or effective_main_alias
    )


def _build_condition_pairs(
    join: dict[str, Any],
    join_conditions: list[dict[str, Any]],
    right_alias: str,
    effective_main_alias: str,
) -> str:
    """多条 joinConditions 逐条解析为 ON 片段。"""
    pairs = []
    for condition in join_conditions:
        left_table = _condition_left_table(condition, join, effective_main_alias)
        left_column = condition.get("leftColumn")
        right_column = condition.get("rightColumn")
        if left_table and left_column and right_column:
            pairs.append(f"{left_table}.{left_column} = {right_alias}.{right_column}")
    return " AND ".join(pairs)


def _build_single_condition_expr(
    join: dict[str, Any],
    right_alias: str,
    effective_main_alias: str,
) -> str:
    """无 joinConditions 时回退到 join 级单条件。"""
    left_table = join.get("leftAlias") or join.get("leftTable") or effective_main_alias
    left_column = join.get("leftColumn")
    right_column = join.get("rightColumn")
    return (
        f"{left_table}.{left_column} = {right_alias}.{right_column}"
        if left_table and left_column and right_column
        else ""
    )


def _build_join_on_expr(
    join: dict[str, Any],
    right_alias: str,
    effective_main_alias: str,
) -> str:
    join_conditions = join.get("joinConditions") or []
    if join_conditions:
        return _build_condition_pairs(join, join_conditions, right_alias, effective_main_alias)
    return _build_single_condition_expr(join, right_alias, effective_main_alias)


def build_join_clauses(
    main_table: str,
    joins: list[dict[str, Any]],
    main_alias: str | None = None,
) -> list[str]:
    """生成 FROM 和 JOIN 片段，保持 Builder DSL 的原有字段兼容。"""
    effective_main_alias = main_alias or main_table
    parts: list[str] = [f"FROM {_build_from_ref(main_table, effective_main_alias)}"]
    for join in joins:
        right_table = join.get("rightTable")
        if not right_table:
            raise ValueError("查询计划中的 JOIN 缺少 rightTable")
        right_alias = str(join.get("rightAlias") or right_table).strip()
        join_type = (join.get("joinType") or "LEFT").upper()
        clause = f"{join_type} JOIN {_build_from_ref(right_table, right_alias)}"
        on_expr = _build_join_on_expr(join, right_alias, effective_main_alias)
        if not on_expr:
            raise ValueError(f"查询计划中的 JOIN {right_alias} 缺少有效关联条件")
        parts.append(f"{clause} ON {on_expr}")
    return parts
