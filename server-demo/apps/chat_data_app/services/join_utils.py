"""
JOIN 工具函数

提供表关联路径查找和 JOIN 子句构建的公共函数，
供 SQLTranslator 和 SemanticSQLGenerator 共用。
"""

from collections import deque
from typing import List, Set, Dict, Any, Optional

from utils.logger import get_logger

logger = get_logger(__name__)


def _extract_join_conditions(rel: Dict[str, Any], reverse: bool = False) -> List[tuple[str, str]]:
    """提取关联条件列表（统一使用 join_conditions）。"""
    conds = rel.get('join_conditions') or []
    pairs: List[tuple[str, str]] = []
    for cond in conds:
        left_col = (cond or {}).get('left_column')
        right_col = (cond or {}).get('right_column')
        if not left_col or not right_col:
            continue
        pairs.append((left_col, right_col))

    if reverse:
        return [(right, left) for left, right in pairs]
    return pairs


def find_join_path(
    from_table: str,
    to_table: str,
    relationships: List[Dict[str, Any]]
) -> List[str]:
    """
    查找从 from_table 到 to_table 的连接路径（使用 BFS 查找最短路径）

    Args:
        from_table: 起始表名
        to_table: 目标表名
        relationships: 表关系配置列表，每个元素包含 left_table, right_table 等

    Returns:
        中间表列表（不包括起点和终点），如果无法到达返回空列表
    """
    if from_table == to_table:
        return []

    # 构建邻接表
    adjacency: Dict[str, List[str]] = {}
    for rel in relationships:
        left = rel.get('left_table')
        right = rel.get('right_table')
        if not left or not right:
            continue
        if left not in adjacency:
            adjacency[left] = []
        if right not in adjacency:
            adjacency[right] = []
        adjacency[left].append(right)
        adjacency[right].append(left)

    # BFS 查找路径
    queue = deque([(from_table, [from_table])])
    seen = {from_table}

    while queue:
        current, path = queue.popleft()
        if current == to_table:
            # 返回中间表（不包括起点和终点）
            return path[1:-1] if len(path) > 2 else []

        for neighbor in adjacency.get(current, []):
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append((neighbor, path + [neighbor]))

    return []  # 无法到达


def expand_tables_with_intermediates(
    main_table: str,
    tables_needed: Set[str],
    relationships: List[Dict[str, Any]]
) -> Set[str]:
    """
    扩展表集合，添加必要的中间表

    当需要 JOIN 多个表时，可能需要经过一些中间表才能建立连接。
    此函数会找出所有必要的中间表并添加到集合中。

    Args:
        main_table: 主表名（FROM 子句中的表）
        tables_needed: 需要连接的表集合
        relationships: 表关系配置列表

    Returns:
        扩展后的表集合（包含原始表和必要的中间表）
    """
    expanded = set(tables_needed)

    for target_table in list(tables_needed):
        if target_table == main_table:
            continue
        # 查找从主表到目标表的路径
        intermediates = find_join_path(main_table, target_table, relationships)
        expanded.update(intermediates)

    return expanded


def build_join_clauses(
    main_table: str,
    tables_needed: Set[str],
    relationships: List[Dict[str, Any]],
    table_aliases: Optional[Dict[str, str]] = None
) -> List[str]:
    """
    构建 JOIN 子句列表

    Args:
        main_table: 主表名
        tables_needed: 需要连接的表集合
        relationships: 表关系配置列表
        table_aliases: 表别名映射（可选）

    Returns:
        JOIN 子句列表，如 ["LEFT JOIN table2 ON table1.id = table2.fk_id", ...]
    """
    if not table_aliases:
        table_aliases = {}

    join_clauses = []
    joined_tables = {main_table}

    # 扩展表集合，添加必要的中间表
    expanded_tables = expand_tables_with_intermediates(main_table, tables_needed, relationships)

    # 需要连接但还未连接的表
    pending_tables = expanded_tables - joined_tables

    # 多轮尝试，直到所有表都连接上或无法继续
    max_iterations = len(relationships) + 2
    for _ in range(max_iterations):
        if not pending_tables:
            break

        made_progress = False
        for rel in relationships:
            left_table = rel.get('left_table')
            right_table = rel.get('right_table')
            join_type = rel.get('join_type', 'left').upper()
            alias = rel.get('alias') or right_table

            # 获取实际使用的表名/别名
            left_ref = table_aliases.get(left_table, left_table)
            right_ref = table_aliases.get(right_table, right_table)

            # 情况1：左表已连接，右表待连接
            if left_table in joined_tables and right_table in pending_tables:
                cond_pairs = _extract_join_conditions(rel, reverse=False)
                if not cond_pairs:
                    continue
                on_expr = " AND ".join(
                    [f"{left_ref}.{left_col} = {(alias if alias != right_table else right_ref)}.{right_col}" for left_col, right_col in cond_pairs]
                )
                if alias != right_table:
                    join_clause = f"{join_type} JOIN {right_table} AS {alias} ON {on_expr}"
                else:
                    join_clause = f"{join_type} JOIN {right_table} ON {on_expr}"
                join_clauses.append(join_clause)
                joined_tables.add(right_table)
                pending_tables.discard(right_table)
                made_progress = True

            # 情况2：右表已连接，左表待连接（反向 JOIN）
            elif right_table in joined_tables and left_table in pending_tables:
                right_alias = alias if alias != right_table else right_ref
                cond_pairs = _extract_join_conditions(rel, reverse=True)
                if not cond_pairs:
                    continue
                on_expr = " AND ".join(
                    [f"{left_table}.{left_col} = {right_alias}.{right_col}" for left_col, right_col in cond_pairs]
                )
                join_clause = f"{join_type} JOIN {left_table} ON {on_expr}"
                join_clauses.append(join_clause)
                joined_tables.add(left_table)
                pending_tables.discard(left_table)
                made_progress = True

        if not made_progress:
            break

    # 检查是否所有表都已连接
    if pending_tables:
        logger.warning(f"[build_join_clauses] 无法找到表 {pending_tables} 的关联路径")

    return join_clauses


def get_table_alias(table_name: str, table_aliases: Dict[str, str]) -> str:
    """获取表的别名，如果没有别名则返回表名本身"""
    return table_aliases.get(table_name, table_name)


def get_column_ref(table_name: str, column: str, table_aliases: Dict[str, str]) -> str:
    """获取带表别名的列引用，如 table_alias.column"""
    alias = get_table_alias(table_name, table_aliases)
    return f"{alias}.{column}"
