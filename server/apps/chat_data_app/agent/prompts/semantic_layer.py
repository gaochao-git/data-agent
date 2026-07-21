"""Semantic layer prompt formatting."""

from __future__ import annotations


def build_semantic_prompt(semantic_config: dict, mode: str = "query") -> str:
    """构建语义配置提示词片段，供智能问数 system prompt 注入。"""

    def get_metric_type(field: dict) -> str:
        return str(
            field.get("data_type")
            or field.get("mysql_type")
            or field.get("hive_type")
            or field.get("columnType")
            or ""
        ).strip()

    def get_dimension_type(field: dict) -> str:
        return str(
            field.get("logical_type")
            or field.get("mysql_type")
            or field.get("hive_type")
            or field.get("columnType")
            or field.get("data_type")
            or ""
        ).strip()

    def format_prompt_line(name: str, desc: str, field_type: str, expression: str, metadata_supplement: str = "") -> str:
        parts = [f"**{name}**"]
        if desc and desc != name:
            parts.append(desc)
        if field_type:
            parts.append(f"类型: {field_type}")
        parts.append(f"表达式: {expression}")
        supplement = str(metadata_supplement or "").strip()
        if supplement:
            parts.append(f"元数据补充: {supplement}")
        return "，".join(parts)

    def semantic_table_ref(table: str, table_alias: str = "") -> str:
        table_name = str(table or "").strip()
        alias = str(table_alias or table_name).strip()
        if not table_name:
            return alias
        if not alias or alias == table_name:
            return table_name
        return f"{alias}({table_name})"

    def collect_semantic_table_names() -> set[str]:
        table_names: set[str] = set()
        for item in [*(semantic_config.get("metrics") or []), *(semantic_config.get("dimensions") or [])]:
            table_name = str((item or {}).get("table") or "").strip()
            if table_name:
                table_names.add(table_name)
        for relationship in semantic_config.get("relationships") or []:
            left_table = str((relationship or {}).get("left_table") or "").strip()
            right_table = str((relationship or {}).get("right_table") or "").strip()
            if left_table:
                table_names.add(left_table)
            if right_table:
                table_names.add(right_table)
        return table_names

    def column_name(column: dict) -> str:
        return str(column.get("name") or column.get("column_name") or "").strip()

    def column_type(column: dict) -> str:
        return str(
            column.get("type")
            or column.get("mysql_type")
            or column.get("hive_type")
            or column.get("data_type")
            or column.get("columnType")
            or ""
        ).strip()

    def column_description(column: dict) -> str:
        return str(column.get("description") or column.get("comment") or "").strip()

    description_section = ""
    if semantic_config.get("description"):
        description_section = f"**配置说明**: {semantic_config['description']}\n\n"

    business_description_section = ""
    if semantic_config.get("ontology_rules"):
        business_description_section = f"**业务描述**: {semantic_config['ontology_rules']}\n\n"

    source_type = str(semantic_config.get("source_type") or "").strip()
    dialect_section = f"**SQL方言**: {source_type.upper()}\n\n" if source_type else ""

    dimensions = semantic_config.get("dimensions", [])
    dimensions_section = ""
    if dimensions:
        dimension_lines = []
        for dimension in dimensions:
            name = dimension.get("name", "")
            desc = dimension.get("description") or dimension.get("label") or name
            expression = dimension.get("expression")
            if not expression:
                field = dimension.get("field")
                if field:
                    expression = field
                else:
                    table = dimension.get("table", "")
                    table_alias = dimension.get("table_alias", "")
                    column = dimension.get("column", "")
                    table_ref = table_alias or table
                    expression = f"{table_ref}.{column}" if table_ref and column else column or "-"
            field_type = get_dimension_type(dimension)
            table_ref = semantic_table_ref(dimension.get("table", ""), dimension.get("table_alias", ""))
            prompt_line = format_prompt_line(name, desc, field_type, expression, dimension.get("metadata_supplement"))
            if table_ref:
                prompt_line = f"{prompt_line}，表: {table_ref}"

            allowed_values = dimension.get("allowed_values")
            mapping = dimension.get("mapping") if isinstance(dimension.get("mapping"), dict) else {}
            value_hints = []
            if allowed_values:
                values_text = "、".join(str(value) for value in allowed_values)
                value_hints.append(f"业务可选值: [{values_text}]")
            if mapping:
                mapping_text = "，".join(f"{key}={value}" for key, value in mapping.items() if str(key).strip() and str(value).strip())
                if mapping_text:
                    value_hints.append(f"SQL取值映射: {mapping_text}")
                    value_hints.append("生成 DSL 时使用业务可选值；直接生成 SQL 时使用映射后的物理值")
            if value_hints:
                dimension_lines.append(f"- {prompt_line} - {'；'.join(value_hints)}")
            else:
                dimension_lines.append(f"- {prompt_line}")

        dimensions_section = "### 可用维度/字段（可用于返回、筛选、分组）\n" + "\n".join(dimension_lines) + "\n\n"

    metrics = semantic_config.get("metrics", [])
    metrics_section = ""
    if metrics:
        metric_lines = []
        for metric in metrics:
            name = metric.get("name", "")
            desc = metric.get("description") or metric.get("label") or name
            expression = metric.get("expression")
            if not expression:
                aggregation = metric.get("aggregation", "COUNT")
                field = metric.get("field", "")
                expression = f"{aggregation}({field})"
            table_ref = semantic_table_ref(metric.get("table", ""), metric.get("table_alias", ""))
            prompt_line = format_prompt_line(name, desc, get_metric_type(metric), expression, metric.get("metadata_supplement"))
            if table_ref:
                prompt_line = f"{prompt_line}，表: {table_ref}"
            metric_lines.append(f"- {prompt_line}")
        metrics_section = "### 可用指标（仅用于明确聚合口径）\n" + "\n".join(metric_lines) + "\n\n"

    tables = semantic_config.get("tables") or []
    table_names = collect_semantic_table_names()
    tables_section = ""
    if tables and table_names:
        table_lines = []
        for table in tables:
            table_name = str((table or {}).get("table_name") or "").strip()
            if not table_name or table_name not in table_names:
                continue
            table_desc = str((table or {}).get("description") or "").strip()
            header = f"- **{table_name}**"
            if table_desc:
                header = f"{header}：{table_desc}"
            table_lines.append(header)
            for column in table.get("columns") or []:
                name = column_name(column)
                if not name:
                    continue
                parts = [name]
                col_type = column_type(column)
                col_desc = column_description(column)
                if col_type:
                    parts.append(f"类型: {col_type}")
                if col_desc:
                    parts.append(f"备注: {col_desc}")
                table_lines.append(f"  - {'，'.join(parts)}")
        if table_lines:
            tables_section = "### 相关表和原始字段（用于补充字段理解和 SQL 回退）\n" + "\n".join(table_lines) + "\n\n"

    relationships = semantic_config.get("relationships", [])
    relationships_section = ""
    if relationships:
        relationship_lines = []
        for relationship in relationships:
            left_table = relationship.get("left_table", "")
            right_table = relationship.get("right_table", "")
            left_ref = semantic_table_ref(left_table, relationship.get("left_alias", ""))
            right_ref = semantic_table_ref(right_table, relationship.get("right_alias", ""))
            join_type = relationship.get("join_type", "left").upper()
            rel_type = relationship.get("relationship_type", "one_to_many")
            join_conditions = relationship.get("join_conditions") or []
            condition_parts = []
            for condition in join_conditions:
                left_col = (condition or {}).get("left_column")
                right_col = (condition or {}).get("right_column")
                if left_col and right_col:
                    condition_parts.append(f"{left_ref}.{left_col} = {right_ref}.{right_col}")
            if not condition_parts:
                continue
            relationship_lines.append(
                f"- **{left_ref}** → **{right_ref}**: {' AND '.join(condition_parts)} ({join_type} JOIN, {rel_type})"
            )
        relationships_section = "### 表关系（用于跨表查询）\n" + "\n".join(relationship_lines) + "\n\n"

    return f"""{description_section}{business_description_section}{dialect_section}{dimensions_section}{tables_section}{relationships_section}{metrics_section}"""
