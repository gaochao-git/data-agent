"""数据源路由共享工具。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set

from ..services.semantic_core.common import build_table_columns_map

SOURCE_DEFAULT_PORTS = {"mysql": 3306, "hive": 10000}


def _normalize_source_item(
    source_type: str,
    host: str,
    port: Optional[int],
    database: str,
    org_name: Optional[str] = None,
    org_code: Optional[str] = None,
    query_executor_type: Optional[str] = None,
) -> Dict[str, Any]:
    """标准化数据源连接项。"""
    normalized_host = str(host or "").strip()
    normalized_database = str(database or "").strip()
    normalized_org_name = str(org_name or "").strip()
    normalized_org_code = str(org_code or "").strip()
    normalized_executor_type = str(query_executor_type or "direct").strip().lower() or "direct"
    if not normalized_org_name:
        raise ValueError("机构名称不能为空")
    if not normalized_org_code:
        raise ValueError("机构编码不能为空")
    if normalized_executor_type not in {"direct", "http"}:
        raise ValueError(f"不支持的查询执行方式: {normalized_executor_type}")
    if not normalized_database:
        raise ValueError("数据库名不能为空")
    if normalized_executor_type == "direct" and not normalized_host:
        raise ValueError("数据源连接信息不完整")
    if normalized_executor_type == "http" and (normalized_host or port is not None):
        raise ValueError("HTTP 模式只填写数据库名，不填写 IP 和端口")
    if normalized_executor_type == "direct":
        result = {
            "host": normalized_host,
            "port": int(port or SOURCE_DEFAULT_PORTS[source_type]),
            "database": normalized_database,
            "query_executor_type": normalized_executor_type,
        }
    else:
        result = {
            "database": normalized_database,
            "query_executor_type": normalized_executor_type,
        }
    result["org_name"] = normalized_org_name
    result["org_code"] = normalized_org_code
    return result


def _column_type_label(column: Dict[str, Any]) -> str:
    """提取字段类型用于表结构差异比较。"""
    return str(column.get("mysql_type") or column.get("hive_type") or column.get("type") or "").strip().lower()


def _build_table_schema_map(tables: List[Dict[str, Any]]) -> Dict[str, Dict[str, str]]:
    """构建表字段类型索引。"""
    schema: Dict[str, Dict[str, str]] = {}
    for table in tables or []:
        table_name = str(table.get("table_name") or "").strip()
        if not table_name:
            continue
        schema[table_name] = {
            str(column.get("name") or "").strip(): _column_type_label(column)
            for column in table.get("columns", []) or []
            if str(column.get("name") or "").strip()
        }
    return schema


def _schema_health_issue(level: str, issue_type: str, message: str, target: str) -> Dict[str, Any]:
    """构造表结构校验问题项。"""
    return {"level": level, "type": issue_type, "message": message, "target": target}


def _compare_datasource_schema(cached_tables: List[Dict[str, Any]], physical_tables: List[Dict[str, Any]]) -> Dict[str, Any]:
    """校验缓存表结构是否仍存在于真实物理表结构中。"""
    cached_schema = _build_table_schema_map(cached_tables)
    physical_schema = _build_table_schema_map(physical_tables)
    issues: List[Dict[str, Any]] = []

    for table_name in sorted(set(cached_schema) - set(physical_schema)):
        issues.append(_schema_health_issue("error", "physical_table_removed", f"物理库已不存在表: {table_name}", f"table:{table_name}"))

    for table_name in sorted(set(cached_schema) & set(physical_schema)):
        cached_columns = cached_schema[table_name]
        physical_columns = physical_schema[table_name]
        for column_name in sorted(set(cached_columns) - set(physical_columns)):
            issues.append(_schema_health_issue("error", "physical_column_removed", f"物理库已不存在字段: {table_name}.{column_name}", f"column:{table_name}.{column_name}"))
        for column_name in sorted(set(cached_columns) & set(physical_columns)):
            cached_type = cached_columns[column_name]
            physical_type = physical_columns[column_name]
            if cached_type and physical_type and cached_type != physical_type:
                issues.append(_schema_health_issue("warning", "physical_column_type_changed", f"字段类型变化: {table_name}.{column_name}，缓存为 {cached_type}，物理库为 {physical_type}", f"column:{table_name}.{column_name}"))

    status = "invalid" if any(item["level"] == "error" for item in issues) else "warning" if issues else "ok"
    return {
        "status": status,
        "summary": {
            "cached_table_count": len(cached_schema),
            "physical_table_count": len(physical_schema),
            "issue_count": len(issues),
            "error_count": sum(1 for issue in issues if issue.get("level") == "error"),
            "warning_count": sum(1 for issue in issues if issue.get("level") == "warning"),
        },
        "issues": issues,
    }


def _build_datasource_schema_health(datasource: Dict[str, Any], physical_tables: Optional[List[Dict[str, Any]]] = None, error: Optional[Exception] = None) -> Dict[str, Any]:
    """根据实时物理表结构校验当前缓存表字段是否仍存在。"""
    source_type = datasource.get("source_type")
    if source_type not in ("mysql", "hive"):
        return {
            "status": "warning",
            "summary": {"cached_table_count": len(datasource.get("ds_tables") or []), "physical_table_count": 0, "issue_count": 1, "error_count": 0, "warning_count": 1},
            "issues": [_schema_health_issue("warning", "physical_schema_unsupported", f"暂不支持校验该数据源类型的物理表结构: {source_type or '未知'}", "datasource")],
        }
    if error is not None:
        return {
            "status": "invalid",
            "summary": {"cached_table_count": len(datasource.get("ds_tables") or []), "physical_table_count": 0, "issue_count": 1, "error_count": 1, "warning_count": 0},
            "issues": [_schema_health_issue("error", "physical_schema_check_failed", f"连接物理库读取表结构失败: {str(error)}", "datasource")],
        }
    return _compare_datasource_schema(datasource.get("ds_tables") or [], physical_tables or [])


def _extract_expression_identifiers(expression: str) -> Set[str]:
    """从简单 SQL 表达式中提取可能的字段标识符。"""
    text = str(expression or "")
    if not text:
        return set()
    without_strings = re.sub(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"", " ", text).replace("`", "")
    identifiers = set()
    sql_keywords = {
        "and", "as", "asc", "between", "case", "cast", "count", "date", "date_format",
        "desc", "distinct", "else", "end", "from", "if", "in", "is", "max", "min", "not",
        "null", "or", "sum", "avg", "then", "trim", "upper", "lower", "when", "year",
    }
    pattern = r"[A-Za-z_][A-Za-z0-9_]*(?:\s*\.\s*[A-Za-z_][A-Za-z0-9_]*)*"
    for match in re.finditer(pattern, without_strings):
        raw = match.group(0)
        candidate = re.split(r"\s*\.\s*", raw)[-1]
        next_char_index = match.end()
        while next_char_index < len(without_strings) and without_strings[next_char_index].isspace():
            next_char_index += 1
        if next_char_index < len(without_strings) and without_strings[next_char_index] == "(":
            continue
        if candidate and candidate.lower() not in sql_keywords:
            identifiers.add(candidate)
    return identifiers


def _is_count_all_expression(expression: str) -> bool:
    """判断是否为不依赖具体字段的总数计数表达式。"""
    normalized = re.sub(r"\s+", "", str(expression or "")).upper()
    return normalized in {"COUNT(*)", "COUNT(1)"}


def _semantic_health_issue(level: str, issue_type: str, message: str, target: str, config: Optional[Dict] = None) -> Dict[str, Any]:
    """构造语义健康检查问题项。"""
    issue = {
        "level": level,
        "type": issue_type,
        "message": message,
        "target": target,
    }
    if config:
        issue["config_id"] = config.get("id") or config.get("semantic_config_id")
        issue["config_name"] = config.get("name") or ""
    return issue


def _build_semantic_health_result(config: Dict, issues: List[Dict[str, Any]]) -> Dict[str, Any]:
    """构造单个指标集健康检查结果。"""
    status = "invalid" if any(item["level"] == "error" for item in issues) else "warning" if issues else "ok"
    return {
        "config_id": config.get("id") or config.get("semantic_config_id"),
        "config_name": config.get("name") or "",
        "status": status,
        "issues": issues,
    }


def _validate_semantic_dimension_health(
    dim: Dict[str, Any],
    table_columns_map: Dict[str, Set[str]],
    config: Dict,
) -> List[Dict[str, Any]]:
    """校验单个语义维度是否匹配物理表。"""
    dim_name = dim.get("name") or dim.get("field_name") or ""
    table_name = dim.get("table")
    column_name = dim.get("column")
    target = f"dimension:{dim_name}"
    if not table_name or table_name not in table_columns_map:
        return [_semantic_health_issue("error", "missing_table", f"维度 {dim_name} 所属表不存在: {table_name}", target, config)]

    issues: List[Dict[str, Any]] = []
    if not column_name or column_name not in table_columns_map[table_name]:
        issues.append(_semantic_health_issue("error", "missing_column", f"维度 {dim_name} 字段不存在: {table_name}.{column_name}", target, config))
    expression = str(dim.get("expression") or "").strip()
    for identifier in _extract_expression_identifiers(expression):
        if identifier not in table_columns_map[table_name]:
            issues.append(_semantic_health_issue("warning", "expression_column_changed", f"维度 {dim_name} 表达式疑似引用已变化字段: {table_name}.{identifier}", target, config))
    return issues


def _validate_semantic_metric_health(
    metric: Dict[str, Any],
    table_columns_map: Dict[str, Set[str]],
    config: Dict,
) -> List[Dict[str, Any]]:
    """校验单个语义指标是否匹配物理表。"""
    metric_name = metric.get("name") or metric.get("field_name") or ""
    table_name = metric.get("table")
    expression = str(metric.get("expression") or "").strip()
    target = f"metric:{metric_name}"
    if not table_name or table_name not in table_columns_map:
        return [_semantic_health_issue("error", "missing_table", f"指标 {metric_name} 所属表不存在: {table_name}", target, config)]
    if _is_count_all_expression(expression):
        return []

    identifiers = _extract_expression_identifiers(expression)
    if not identifiers:
        return [_semantic_health_issue("warning", "expression_unparsed", f"指标 {metric_name} 表达式未识别到物理字段: {expression or '-'}", target, config)]

    return [
        _semantic_health_issue("error", "expression_column_missing", f"指标 {metric_name} 表达式引用不存在字段: {table_name}.{identifier}", target, config)
        for identifier in sorted(identifiers - table_columns_map[table_name])
    ]


def _validate_semantic_relationship_health(
    rel: Dict[str, Any],
    index: int,
    table_columns_map: Dict[str, Set[str]],
    config: Dict,
) -> List[Dict[str, Any]]:
    """校验单个语义关系是否匹配物理表。"""
    left_table = rel.get("left_table")
    right_table = rel.get("right_table")
    target = f"relationship:{left_table}->{right_table}"
    if not left_table or left_table not in table_columns_map:
        return [_semantic_health_issue("error", "missing_table", f"关系 {index} 左表不存在: {left_table}", target, config)]
    if not right_table or right_table not in table_columns_map:
        return [_semantic_health_issue("error", "missing_table", f"关系 {index} 右表不存在: {right_table}", target, config)]

    issues: List[Dict[str, Any]] = []
    conditions = rel.get("join_conditions") or [{"left_column": rel.get("left_column"), "right_column": rel.get("right_column")}]
    for cond_index, cond in enumerate(conditions, start=1):
        left_column = (cond or {}).get("left_column")
        right_column = (cond or {}).get("right_column")
        if not left_column or left_column not in table_columns_map[left_table]:
            issues.append(_semantic_health_issue("error", "missing_join_column", f"关系 {index}.{cond_index} 左字段不存在: {left_table}.{left_column}", target, config))
        if not right_column or right_column not in table_columns_map[right_table]:
            issues.append(_semantic_health_issue("error", "missing_join_column", f"关系 {index}.{cond_index} 右字段不存在: {right_table}.{right_column}", target, config))
    return issues


def _validate_semantic_config_health(datasource: Dict, config: Dict) -> Dict[str, Any]:
    """校验单个指标集是否仍匹配当前物理表结构。"""
    table_columns_map = build_table_columns_map(datasource)
    issues: List[Dict[str, Any]] = []
    if not table_columns_map:
        issues.append(_semantic_health_issue("error", "missing_schema", "当前数据源缺少表结构信息，请在数据源组编辑页拉取表信息并保存", "datasource", config))
        return _build_semantic_health_result(config, issues)

    for dim in config.get("dimensions") or []:
        issues.extend(_validate_semantic_dimension_health(dim, table_columns_map, config))

    for metric in config.get("metrics") or []:
        issues.extend(_validate_semantic_metric_health(metric, table_columns_map, config))

    for index, rel in enumerate(config.get("relationships") or [], start=1):
        issues.extend(_validate_semantic_relationship_health(rel, index, table_columns_map, config))

    return _build_semantic_health_result(config, issues)


def _build_datasource_semantic_health(datasource: Dict, configs: List[Dict]) -> Dict[str, Any]:
    """构造数据源下所有指标集的健康检查结果。"""
    config_results = [_validate_semantic_config_health(datasource, config) for config in configs or []]
    all_issues = [issue for result in config_results for issue in result.get("issues") or []]
    status = "invalid" if any(issue["level"] == "error" for issue in all_issues) else "warning" if all_issues else "ok"
    return {
        "datasource_id": datasource.get("id"),
        "group_name": datasource.get("group_name") or "",
        "status": status,
        "summary": {
            "config_count": len(config_results),
            "issue_count": len(all_issues),
            "error_count": sum(1 for issue in all_issues if issue.get("level") == "error"),
            "warning_count": sum(1 for issue in all_issues if issue.get("level") == "warning"),
        },
        "configs": config_results,
        "issues": all_issues,
    }


def _merge_health_status(*statuses: str) -> str:
    """合并多层健康状态。"""
    if any(status == "invalid" for status in statuses):
        return "invalid"
    if any(status == "warning" for status in statuses):
        return "warning"
    return "ok"
