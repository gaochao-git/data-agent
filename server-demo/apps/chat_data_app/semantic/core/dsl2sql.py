"""
SQL 翻译器

将 DSL 转换为 SQL。
"""

from typing import List, Dict, Set, Optional, Any, Tuple
import logging

from ..models import SemanticConfig, SemanticDimension, SemanticMetric
from ..engine import SQLDialect, get_dialect, JoinBuilder, FilterBuilder
from .dsl import SemanticQuery, DimensionSpec, TranslateResult, QueryMode, OrderBySpec

logger = logging.getLogger(__name__)


class SQLTranslator:
    """
    SQL 翻译器

    将语义查询 DSL 转换为 SQL 语句。

    Example:
        translator = SQLTranslator(config, dialect='mysql')
        result = translator.translate(dsl)
        print(result.sql)
    """

    def __init__(
        self,
        config: SemanticConfig,
        dialect: str = "mysql"
    ):
        """
        初始化翻译器

        Args:
            config: 语义配置
            dialect: SQL 方言名称
        """
        self.config = config
        self.dialect = get_dialect(dialect)

        # 构建索引
        self._metrics_index = {m.name: m for m in config.metrics}
        self._dimensions_index = {d.name: d for d in config.dimensions}

        # 构建器
        self.join_builder = JoinBuilder(config)
        self.filter_builder = FilterBuilder(config, self.dialect)

    def translate(self, dsl: SemanticQuery) -> TranslateResult:
        """
        翻译 DSL 为 SQL

        Args:
            dsl: 语义查询

        Returns:
            翻译结果
        """
        try:
            # 收集使用的表
            tables_used: Set[str] = set()

            # 1. 构建 SELECT 子句
            select_clause, select_tables = self._build_select(dsl)
            tables_used.update(select_tables)

            # 2. 确定主表
            main_table = self._determine_main_table(dsl, tables_used)

            # 3. 构建 WHERE 子句（同时收集表）
            filter_tables: Set[str] = set()
            self.filter_builder.set_table_aliases({})  # 重置别名

            where_clause = ""
            if dsl.filters:
                where_clause = self.filter_builder.build_where(
                    dsl.filters, filter_tables
                )
                tables_used.update(filter_tables)

            # 4. 构建 FROM + JOIN 子句
            from_clause = self.join_builder.build(
                main_table,
                tables_used
            )

            # 更新 filter_builder 的表别名
            self.filter_builder.set_table_aliases(
                self.join_builder.get_all_aliases()
            )

            # 重新构建 WHERE（使用正确的别名）
            if dsl.filters:
                filter_tables = set()
                where_clause = self.filter_builder.build_where(
                    dsl.filters, filter_tables
                )

            # 5. 构建 GROUP BY 子句
            group_clause = self._build_group_by(dsl)

            # 6. 构建 HAVING 子句
            having_clause = ""
            if dsl.having:
                having_clause = self.filter_builder.build_having(dsl.having)

            # 7. 构建 ORDER BY 子句
            order_clause = self._build_order_by(dsl)

            # 8. 构建 LIMIT 子句
            limit_clause = self._build_limit(dsl)

            # 9. 组装 SQL
            sql = self._assemble_sql(
                select_clause,
                from_clause,
                where_clause,
                group_clause,
                having_clause,
                order_clause,
                limit_clause
            )

            # 10. 生成解释
            explanation = self._build_explanation(dsl)

            # 11. 生成结构化摘要
            summary = self._generate_summary(dsl)

            return TranslateResult(
                success=True,
                sql=sql,
                explanation=explanation,
                tables_used=list(tables_used),
                summary=summary
            )

        except Exception as e:
            logger.error(f"SQL 翻译失败: {e}", exc_info=True)
            return TranslateResult(
                success=False,
                error=str(e)
            )

    def _build_select(
        self,
        dsl: SemanticQuery
    ) -> Tuple[str, Set[str]]:
        """
        构建 SELECT 子句

        Returns:
            (SELECT 子句, 使用的表集合)
        """
        tables_used: Set[str] = set()
        select_items: List[str] = []

        # 添加维度字段
        for dim_spec in dsl.dimensions:
            dim_name = dim_spec.name if isinstance(dim_spec, DimensionSpec) else dim_spec
            dim = self._dimensions_index.get(dim_name)

            if not dim:
                raise ValueError(f"未知的维度: {dim_name}")

            tables_used.add(dim.table)

            # 获取列引用（暂时不使用别名，后面会更新）
            if isinstance(dim_spec, DimensionSpec) and dim_spec.grain:
                # 时间维度，应用时间粒度
                column_ref = dim.get_column_ref()
                formatted = self.dialect.format_date(column_ref, dim_spec.grain)
                alias = f"{dim_name}_{dim_spec.grain}"
                select_items.append(f"{formatted} AS {self.dialect.quote_identifier(alias)}")
            else:
                # 普通维度
                if dim.expression:
                    select_items.append(f"{dim.expression} AS {self.dialect.quote_identifier(dim_name)}")
                else:
                    column_ref = dim.get_column_ref()
                    select_items.append(f"{column_ref} AS {self.dialect.quote_identifier(dim_name)}")

        # 添加指标字段
        for metric_name in dsl.metrics:
            metric = self._metrics_index.get(metric_name)
            if metric:
                tables_used.add(metric.table)
                select_items.append(f"{metric.expression} AS {self.dialect.quote_identifier(metric_name)}")
                continue

            if dsl.mode == QueryMode.DETAIL:
                dim = self._dimensions_index.get(metric_name)
                if dim:
                    tables_used.add(dim.table)
                    if dim.expression:
                        select_items.append(f"{dim.expression} AS {self.dialect.quote_identifier(metric_name)}")
                    else:
                        column_ref = dim.get_column_ref()
                        select_items.append(f"{column_ref} AS {self.dialect.quote_identifier(metric_name)}")
                    continue

            raise ValueError(f"未知的指标: {metric_name}")

        return f"SELECT\n    {',\n    '.join(select_items)}", tables_used

    def _determine_main_table(
        self,
        dsl: SemanticQuery,
        tables_used: Set[str]
    ) -> str:
        """确定主表"""
        # 如果配置中定义了主表，使用它
        if hasattr(self.config, 'main_table') and self.config.main_table:
            return self.config.main_table

        # 否则，使用第一个指标的表
        if dsl.metrics:
            first_metric = self._metrics_index.get(dsl.metrics[0])
            if first_metric:
                return first_metric.table
            if dsl.mode == QueryMode.DETAIL:
                first_dimension_metric = self._dimensions_index.get(dsl.metrics[0])
                if first_dimension_metric:
                    return first_dimension_metric.table

        # 最后，使用第一个使用的表
        if tables_used:
            return next(iter(tables_used))

        raise ValueError("无法确定主表")

    def _build_group_by(self, dsl: SemanticQuery) -> str:
        """构建 GROUP BY 子句"""
        if not dsl.dimensions or dsl.mode == QueryMode.DETAIL:
            return ""

        group_items: List[str] = []

        for dim_spec in dsl.dimensions:
            dim_name = dim_spec.name if isinstance(dim_spec, DimensionSpec) else dim_spec
            dim = self._dimensions_index.get(dim_name)

            if not dim:
                continue

            if isinstance(dim_spec, DimensionSpec) and dim_spec.grain:
                # 时间维度，使用相同的格式化表达式
                column_ref = dim.get_column_ref()  # 不传别名，使用表名
                formatted = self.dialect.format_date(column_ref, dim_spec.grain)
                group_items.append(formatted)
            else:
                # 普通维度
                if dim.expression:
                    group_items.append(dim.expression)
                else:
                    column_ref = dim.get_column_ref()  # 不传别名，使用表名
                    group_items.append(column_ref)

        if not group_items:
            return ""

        return f"GROUP BY\n    {',\n    '.join(group_items)}"

    def _build_order_by(self, dsl: SemanticQuery) -> str:
        """构建 ORDER BY 子句"""
        if not dsl.order_by:
            return ""

        order_items: List[str] = []

        for order_spec in dsl.order_by:
            field_name = order_spec.field
            direction = order_spec.direction

            # 检查是否是指标
            if field_name in self._metrics_index:
                # 指标使用别名
                order_items.append(f"{self.dialect.quote_identifier(field_name)} {direction}")
            # 检查是否是维度
            elif field_name in self._dimensions_index:
                dim = self._dimensions_index[field_name]
                # 检查是否有时间粒度
                grain = None
                for dim_spec in dsl.dimensions:
                    if isinstance(dim_spec, DimensionSpec) and dim_spec.name == field_name:
                        grain = dim_spec.grain
                        break

                if grain:
                    alias = f"{field_name}_{grain}"
                    order_items.append(f"{self.dialect.quote_identifier(alias)} {direction}")
                else:
                    order_items.append(f"{self.dialect.quote_identifier(field_name)} {direction}")
            else:
                # 未知字段，直接使用
                order_items.append(f"{field_name} {direction}")

        if not order_items:
            return ""

        return f"ORDER BY\n    {', '.join(order_items)}"

    def _build_limit(self, dsl: SemanticQuery) -> str:
        """构建 LIMIT 子句"""
        if dsl.limit is None:
            return ""

        return self.dialect.limit_offset(dsl.limit, dsl.offset or 0)

    def _assemble_sql(
        self,
        select_clause: str,
        from_clause: str,
        where_clause: str,
        group_clause: str,
        having_clause: str,
        order_clause: str,
        limit_clause: str
    ) -> str:
        """组装完整 SQL"""
        parts = [select_clause, from_clause]

        if where_clause:
            parts.append(f"WHERE\n    {where_clause}")

        if group_clause:
            parts.append(group_clause)

        if having_clause:
            parts.append(f"HAVING\n    {having_clause}")

        if order_clause:
            parts.append(order_clause)

        if limit_clause:
            parts.append(limit_clause)

        return "\n".join(parts)

    def _build_explanation(self, dsl: SemanticQuery) -> str:
        """构建查询解释"""
        parts = []

        # 指标说明
        metrics_desc = "、".join(dsl.metrics)
        parts.append(f"查询指标: {metrics_desc}")

        # 维度说明
        if dsl.dimensions:
            dim_descs = []
            for dim_spec in dsl.dimensions:
                if isinstance(dim_spec, DimensionSpec):
                    if dim_spec.grain:
                        dim_descs.append(f"{dim_spec.name}（按{dim_spec.grain}）")
                    else:
                        dim_descs.append(dim_spec.name)
                else:
                    dim_descs.append(str(dim_spec))
            parts.append(f"按维度分组: {', '.join(dim_descs)}")

        # 过滤说明
        if dsl.filters:
            parts.append(f"应用了 {len(dsl.filters)} 个过滤条件")

        # HAVING 说明
        if dsl.having:
            parts.append(f"应用了 {len(dsl.having)} 个聚合过滤条件")

        return "；".join(parts)

    def _generate_summary(self, dsl: SemanticQuery) -> Dict[str, Any]:
        """
        生成结构化查询摘要（用于前端显示）

        Args:
            dsl: 语义查询

        Returns:
            结构化摘要字典
        """
        summary: Dict[str, Any] = {}

        # 指标
        if dsl.metrics:
            summary['metrics'] = dsl.metrics

        # 维度
        if dsl.dimensions:
            dim_items = []
            for dim_spec in dsl.dimensions:
                if isinstance(dim_spec, DimensionSpec):
                    if dim_spec.grain:
                        dim_items.append({
                            'name': dim_spec.name,
                            'grain': dim_spec.grain
                        })
                    else:
                        dim_items.append(dim_spec.name)
                else:
                    dim_items.append(str(dim_spec))
            summary['dimensions'] = dim_items

        # 筛选条件
        if dsl.filters:
            filter_desc = self._describe_filters(dsl.filters)
            if filter_desc:
                summary['filters'] = [filter_desc]

        # HAVING 条件
        if dsl.having:
            having_desc = []
            for h in dsl.having:
                if isinstance(h, dict) and 'metric' in h:
                    having_desc.append(
                        f"{h['metric']} {h.get('operator', '=')} {h.get('value')}"
                    )
            if having_desc:
                summary['having'] = having_desc

        # 排序
        if dsl.order_by:
            order_desc = []
            for order_spec in dsl.order_by:
                if isinstance(order_spec, OrderBySpec):
                    direction_text = '降序' if order_spec.direction.upper() == 'DESC' else '升序'
                    order_desc.append(f"{order_spec.field} {direction_text}")
                elif isinstance(order_spec, dict):
                    field = order_spec.get('field', '')
                    direction = order_spec.get('direction', 'DESC').upper()
                    direction_text = '降序' if direction == 'DESC' else '升序'
                    order_desc.append(f"{field} {direction_text}")
            if order_desc:
                summary['order_by'] = order_desc

        # 分页
        if dsl.limit:
            summary['limit'] = dsl.limit
            if dsl.offset:
                summary['offset'] = dsl.offset

        return summary

    def _describe_filters(self, filters: List[Dict]) -> str:
        """
        生成过滤条件的文本描述

        Args:
            filters: 过滤条件列表

        Returns:
            过滤条件的文本描述
        """
        if not filters:
            return ""

        # 检查是否是树形格式（第一个元素包含 'and' 或 'or'）
        if len(filters) == 1 and isinstance(filters[0], dict):
            first = filters[0]
            if 'and' in first or 'or' in first:
                return self._describe_tree_filter(first)

        # 平铺格式
        descs = []
        for f in filters:
            if isinstance(f, dict) and 'dimension' in f:
                descs.append(
                    f"{f['dimension']}{f.get('operator', '=')}{f.get('value')}"
                )
        return ', '.join(descs)

    def _describe_tree_filter(self, node: Dict) -> str:
        """
        递归描述树形过滤条件

        Args:
            node: 过滤节点

        Returns:
            文本描述
        """
        if 'or' in node:
            children = node['or']
            if not children:
                return ""
            child_descs = [self._describe_tree_filter(c) for c in children]
            child_descs = [d for d in child_descs if d]
            if len(child_descs) == 1:
                return child_descs[0]
            return f"({' OR '.join(child_descs)})"

        if 'and' in node:
            children = node['and']
            if not children:
                return ""
            child_descs = [self._describe_tree_filter(c) for c in children]
            child_descs = [d for d in child_descs if d]
            if len(child_descs) == 1:
                return child_descs[0]
            return f"({' AND '.join(child_descs)})"

        if 'dimension' in node:
            return f"{node['dimension']}{node.get('operator', '=')}{node.get('value')}"

        return ""

    def translate_with_aliases(
        self,
        dsl: SemanticQuery,
        table_aliases: Dict[str, str]
    ) -> TranslateResult:
        """
        使用指定的表别名翻译 DSL

        Args:
            dsl: 语义查询
            table_aliases: 表别名映射

        Returns:
            翻译结果
        """
        # 临时设置别名
        original_aliases = self.join_builder._table_aliases.copy()
        self.join_builder._table_aliases = table_aliases.copy()
        self.filter_builder.set_table_aliases(table_aliases)

        try:
            result = self.translate(dsl)
        finally:
            # 恢复原始别名
            self.join_builder._table_aliases = original_aliases

        return result
