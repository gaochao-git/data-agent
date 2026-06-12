"""
同比/环比 SQL 构造器。

仅处理公共的 compare 查询模板，供 Builder 和语义问数共用。
"""

from typing import Any, Dict, List, Tuple, Callable


def build_compare_sql(
    builder_config: Dict[str, Any],
    *,
    dialect: str = "mysql",
    quote_identifier: Callable[[str], str],
    to_sql_alias: Callable[[str, str], str],
    normalize_raw_expression: Callable[[str | None, str, str, bool], str],
    is_aggregate_expression: Callable[[str | None], bool],
    normalize_aggregate_expression: Callable[[str | None, str, str, bool], str],
    get_column_expr: Callable[[str, str, bool], str],
    is_filter_tree_node: Callable[[Dict[str, Any]], bool],
    get_filter_tree_scope: Callable[[Dict[str, Any]], str | None],
    build_filter_tree_clause: Callable[..., str],
    build_where_leaf_clause: Callable[[Dict[str, Any], bool, Dict[Tuple[str, str], str], str], str],
) -> str:
    compare = builder_config.get("compare") or {}
    compare_type = str(compare.get("type") or "").lower()
    compare_mode = str(compare.get("mode") or "trend").lower()
    compare_strategy = str(compare.get("strategy") or "calc").lower()

    main_table = (builder_config.get("mainTable") or "").strip()
    dimensions: List[Dict[str, Any]] = builder_config.get("dimensions") or []
    aggregations: List[Dict[str, Any]] = builder_config.get("aggregations") or []
    joins: List[Dict[str, Any]] = builder_config.get("joins") or []
    filters: List[Dict[str, Any]] = builder_config.get("filters") or []
    limit = builder_config.get("limit") or 100

    if compare_type not in {"mom", "yoy"}:
        raise ValueError("同环比类型仅支持 mom 或 yoy")
    if compare_strategy != "calc":
        raise ValueError(f"同比/环比当前仅支持 strategy=calc，暂不支持 {compare_strategy}")
    if compare_mode != "trend":
        raise ValueError("当前仅支持趋势同比/环比")
    if len(dimensions) != 1:
        raise ValueError("同比/环比当前仅支持一个时间维度")
    if len(aggregations) != 1:
        raise ValueError("同比/环比当前仅支持一个指标")

    time_dim = dimensions[0]
    agg = aggregations[0]
    transform = str(time_dim.get("transform") or "raw").lower()
    dialect_name = (dialect or "mysql").strip().lower()
    if dialect_name not in {"mysql", "hive"}:
        raise ValueError(f"同比/环比当前暂不支持 {dialect_name} 方言")
    if transform not in {"day", "month", "quarter", "year"}:
        raise ValueError("同比/环比当前仅支持 day/month/quarter/year 时间粒度，暂不支持 week")

    agg_func = str(agg.get("func") or "RAW").upper()
    if agg_func == "RAW":
        raise ValueError("同比/环比当前仅支持聚合指标，不支持原始值指标")

    selected_tables = [main_table] + [j.get("rightTable") for j in joins if j.get("rightTable")]
    is_multi_table = len(selected_tables) > 1

    def build_join_clauses() -> List[str]:
        parts: List[str] = [f"FROM {main_table}"]
        for join in joins:
            right_table = join.get("rightTable")
            if not right_table:
                continue
            join_type = (join.get("joinType") or "LEFT").upper()
            clause = f"{join_type} JOIN {right_table}"

            on_expr = ""
            join_conditions = join.get("joinConditions") or []
            if join_conditions:
                pairs = []
                for cond in join_conditions:
                    left_table = cond.get("leftTable") or join.get("leftTable") or main_table
                    left_col = cond.get("leftColumn")
                    right_col = cond.get("rightColumn")
                    if left_table and left_col and right_col:
                        pairs.append(f"{left_table}.{left_col} = {right_table}.{right_col}")
                on_expr = " AND ".join(pairs)
            else:
                left_table = join.get("leftTable") or main_table
                left_column = join.get("leftColumn")
                right_column = join.get("rightColumn")
                if left_table and left_column and right_column:
                    on_expr = f"{left_table}.{left_column} = {right_table}.{right_column}"
            if on_expr:
                clause += f" ON {on_expr}"
            parts.append(clause)
        return parts

    time_dim_expr = normalize_raw_expression(time_dim.get("expression"), time_dim.get("table", main_table), time_dim.get("column", ""), is_multi_table)
    if dialect_name == "hive":
        hive_week_start_expr = f"DATE_SUB(TO_DATE({time_dim_expr}), PMOD(DATEDIFF(TO_DATE({time_dim_expr}), '1900-01-01'), 7))"
        period_expr_map = {
            "day": f"TO_DATE({time_dim_expr})",
            "week": hive_week_start_expr,
            "month": f"TRUNC({time_dim_expr}, 'MM')",
            "quarter": f"ADD_MONTHS(TRUNC({time_dim_expr}, 'YEAR'), (QUARTER({time_dim_expr}) - 1) * 3)",
            "year": f"TRUNC({time_dim_expr}, 'YEAR')",
        }
        period_label_expr_map = {
            "day": "DATE_FORMAT(cur.stat_time, 'yyyy-MM-dd')",
            "week": "CONCAT(CAST(YEAR(cur.stat_time) AS STRING), '-W', LPAD(CAST(WEEKOFYEAR(cur.stat_time) AS STRING), 2, '0'))",
            "month": "DATE_FORMAT(cur.stat_time, 'yyyy-MM')",
            "quarter": "CONCAT(YEAR(cur.stat_time), '-Q', QUARTER(cur.stat_time))",
            "year": "DATE_FORMAT(cur.stat_time, 'yyyy')",
        }
        previous_period_expr_map = {
            ("mom", "day"): "DATE_SUB(cur.stat_time, 1)",
            ("mom", "week"): "DATE_SUB(cur.stat_time, 7)",
            ("mom", "month"): "ADD_MONTHS(cur.stat_time, -1)",
            ("mom", "quarter"): "ADD_MONTHS(cur.stat_time, -3)",
            ("mom", "year"): "ADD_MONTHS(cur.stat_time, -12)",
            ("yoy", "day"): "ADD_MONTHS(cur.stat_time, -12)",
            ("yoy", "week"): "DATE_SUB(TO_DATE(ADD_MONTHS(cur.stat_time, -12)), PMOD(DATEDIFF(TO_DATE(ADD_MONTHS(cur.stat_time, -12)), '1900-01-01'), 7))",
            ("yoy", "month"): "ADD_MONTHS(cur.stat_time, -12)",
            ("yoy", "quarter"): "ADD_MONTHS(cur.stat_time, -12)",
            ("yoy", "year"): "ADD_MONTHS(cur.stat_time, -12)",
        }
    else:
        mysql_week_start_expr = f"DATE_SUB(DATE({time_dim_expr}), INTERVAL WEEKDAY({time_dim_expr}) DAY)"
        period_expr_map = {
            "day": f"DATE({time_dim_expr})",
            "week": mysql_week_start_expr,
            "month": f"STR_TO_DATE(DATE_FORMAT({time_dim_expr}, '%Y-%m-01'), '%Y-%m-%d')",
            "quarter": f"DATE_ADD(MAKEDATE(YEAR({time_dim_expr}), 1), INTERVAL QUARTER({time_dim_expr}) - 1 QUARTER)",
            "year": f"STR_TO_DATE(CONCAT(YEAR({time_dim_expr}), '-01-01'), '%Y-%m-%d')",
        }
        period_label_expr_map = {
            "day": "DATE_FORMAT(cur.stat_time, '%Y-%m-%d')",
            "week": "CONCAT(DATE_FORMAT(cur.stat_time, '%x'), '-W', DATE_FORMAT(cur.stat_time, '%v'))",
            "month": "DATE_FORMAT(cur.stat_time, '%Y-%m')",
            "quarter": "CONCAT(YEAR(cur.stat_time), '-Q', QUARTER(cur.stat_time))",
            "year": "DATE_FORMAT(cur.stat_time, '%Y')",
        }
        previous_period_expr_map = {
            ("mom", "day"): "DATE_SUB(cur.stat_time, INTERVAL 1 DAY)",
            ("mom", "week"): "DATE_SUB(cur.stat_time, INTERVAL 1 WEEK)",
            ("mom", "month"): "DATE_SUB(cur.stat_time, INTERVAL 1 MONTH)",
            ("mom", "quarter"): "DATE_SUB(cur.stat_time, INTERVAL 1 QUARTER)",
            ("mom", "year"): "DATE_SUB(cur.stat_time, INTERVAL 1 YEAR)",
            ("yoy", "day"): "DATE_SUB(cur.stat_time, INTERVAL 1 YEAR)",
            ("yoy", "week"): "DATE_SUB(DATE_SUB(cur.stat_time, INTERVAL 1 YEAR), INTERVAL WEEKDAY(DATE_SUB(cur.stat_time, INTERVAL 1 YEAR)) DAY)",
            ("yoy", "month"): "DATE_SUB(cur.stat_time, INTERVAL 1 YEAR)",
            ("yoy", "quarter"): "DATE_SUB(cur.stat_time, INTERVAL 1 YEAR)",
            ("yoy", "year"): "DATE_SUB(cur.stat_time, INTERVAL 1 YEAR)",
        }
    period_expr = period_expr_map[transform]

    matched_expr = agg.get("expression")
    table = agg.get("table", main_table)
    column = agg.get("column", "")
    if (matched_expr or "").strip() and is_aggregate_expression(matched_expr):
        agg_expr = normalize_aggregate_expression(matched_expr, table, column, is_multi_table)
    else:
        col_expr = "*" if column == "*" else get_column_expr(table, column, is_multi_table)
        if agg_func == "COUNT_DISTINCT":
            agg_expr = f"{'COUNT(*)' if column == '*' else f'COUNT(DISTINCT {col_expr})'}"
        elif agg_func == "COUNT":
            agg_expr = f"COUNT({'*' if column == '*' else col_expr})"
        else:
            agg_expr = f"{agg_func}({col_expr})"

    dimension_expr_map: Dict[Tuple[str, str], str] = {}
    for dim in dimensions:
        dim_table = dim.get("table", main_table)
        dim_column = dim.get("column", "")
        dimension_expr_map[(dim_table, dim_column)] = normalize_raw_expression(dim.get("expression"), dim_table, dim_column, is_multi_table)

    where_filters: List[Dict[str, Any]] = []
    for fil in filters:
        if is_filter_tree_node(fil):
            tree_scope = get_filter_tree_scope(fil)
            if tree_scope == "mixed":
                raise ValueError("过滤条件不合法：同一组合筛选树不能混用明细过滤和结果过滤")
            if tree_scope == "aggregate":
                raise ValueError("同比/环比当前不支持结果过滤（HAVING）")
            where_filters.append(fil)
            continue
        if fil.get("aggFunc"):
            raise ValueError("同比/环比当前不支持结果过滤（HAVING）")
        where_filters.append(fil)

    parts: List[str] = [
        f"SELECT {period_expr} AS stat_time, {agg_expr} AS current_value",
        *build_join_clauses(),
    ]
    if where_filters:
        if len(where_filters) == 1 and is_filter_tree_node(where_filters[0]):
            where_clause = build_filter_tree_clause(
                where_filters[0],
                for_having=False,
                is_multi_table=is_multi_table,
                main_table=main_table,
                dimension_expr_map=dimension_expr_map,
                aggregations=aggregations,
                agg_by_id={},
            )
            if where_clause:
                parts.append(f"WHERE {where_clause}")
        else:
            where_clauses = []
            for idx, fil in enumerate(where_filters):
                logic = str(fil.get("logic", "AND")).upper()
                clause = build_where_leaf_clause(fil, is_multi_table, dimension_expr_map, main_table)
                where_clauses.append(clause if idx == 0 else f"{logic} {clause}")
            parts.append(f"WHERE {' '.join(where_clauses)}")

    parts.append(f"GROUP BY {period_expr}")
    base_sql = "\n".join(parts)
    stat_label = time_dim.get("displayName") or time_dim.get("alias") or time_dim.get("column") or "stat_time"
    stat_alias = quote_identifier(to_sql_alias(stat_label, "stat_time"))

    return "\n".join([
        "SELECT",
        f"  {period_label_expr_map[transform]} AS {stat_alias},",
        "  cur.current_value AS `current_value`,",
        "  prev.current_value AS `previous_value`,",
        "  cur.current_value - prev.current_value AS `diff_value`,",
        "  CASE",
        "    WHEN prev.current_value IS NULL OR prev.current_value = 0 THEN NULL",
        "    ELSE (cur.current_value - prev.current_value) / prev.current_value",
        "  END AS `ratio_value`",
        f"FROM ({base_sql}) cur",
        f"LEFT JOIN ({base_sql}) prev ON prev.stat_time = {previous_period_expr_map[(compare_type, transform)]}",
        "ORDER BY cur.stat_time ASC",
        f"LIMIT {int(limit)}",
    ])
