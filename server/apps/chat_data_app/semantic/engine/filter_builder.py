"""
过滤条件构建器

负责构建 WHERE 和 HAVING 子句。
"""

from typing import List, Dict, Set, Any, Optional, Callable
import re
import logging

from ..models import SemanticConfig, SemanticDimension, SemanticMetric
from ..utils import TreeVisitor, TreeDepthExceededError
from .dialect import SQLDialect

logger = logging.getLogger(__name__)

FILTER_VALUE_TYPES = {'literal', 'sql_function'}


# SQL 安全函数白名单
SQL_SAFE_FUNCTIONS = {
    # 日期函数
    'current_date', 'current_timestamp', 'now', 'curdate', 'curtime',
    'date_sub', 'date_add', 'datediff', 'date_format', 'date',
    'year', 'month', 'day', 'hour', 'minute', 'second',
    'quarter', 'week', 'dayofweek', 'dayofyear', 'dayofmonth',
    'to_date', 'from_unixtime', 'unix_timestamp',
    # 字符串函数
    'concat', 'substring', 'substr', 'length', 'char_length',
    'upper', 'lower', 'trim', 'ltrim', 'rtrim',
    'replace', 'reverse', 'left', 'right',
    # 数学函数
    'abs', 'round', 'ceil', 'ceiling', 'floor', 'mod',
    'pow', 'power', 'sqrt', 'rand',
    # 聚合函数
    'count', 'sum', 'avg', 'max', 'min',
    # 条件函数
    'if', 'ifnull', 'nullif', 'coalesce', 'nvl',
    'case', 'when', 'then', 'else', 'end',
}


class FilterValidationError(Exception):
    """过滤条件验证错误"""
    pass


class FilterBuilder:
    """
    过滤条件构建器

    构建 WHERE 和 HAVING 子句，支持树形 AND/OR 结构。
    """

    def __init__(
        self,
        config: SemanticConfig,
        dialect: SQLDialect,
        table_aliases: Optional[Dict[str, str]] = None
    ):
        """
        初始化构建器

        Args:
            config: 语义配置
            dialect: SQL 方言
            table_aliases: 表别名映射
        """
        self.config = config
        self.dialect = dialect
        self.table_aliases = table_aliases or {}
        self.tree_visitor = TreeVisitor(max_depth=10)

        # 构建索引
        self._dimensions_index = {d.name: d for d in config.dimensions}
        self._metrics_index = {m.name: m for m in config.metrics}

    def set_table_aliases(self, aliases: Dict[str, str]) -> None:
        """设置表别名映射"""
        self.table_aliases = aliases

    def build_where(
        self,
        filters: List[Dict[str, Any]],
        tables_used: Optional[Set[str]] = None
    ) -> str:
        """
        构建 WHERE 子句

        Args:
            filters: 过滤条件列表
            tables_used: 收集使用到的表（可选）

        Returns:
            WHERE 子句（不含 WHERE 关键字）
        """
        if not filters:
            return ""

        if tables_used is None:
            tables_used = set()

        conditions = []
        for f in filters:
            try:
                cond = self.tree_visitor.visit_with_context(
                    f,
                    lambda node, ctx: self._build_dimension_condition(node, ctx),
                    tables_used
                )
                if cond:
                    conditions.append(cond)
            except TreeDepthExceededError as e:
                logger.error(f"过滤条件嵌套过深: {e}")
                raise FilterValidationError(str(e))

        if not conditions:
            return ""

        return " AND ".join(conditions)

    def build_having(
        self,
        having: List[Dict[str, Any]]
    ) -> str:
        """
        构建 HAVING 子句

        Args:
            having: HAVING 条件列表

        Returns:
            HAVING 子句（不含 HAVING 关键字）
        """
        if not having:
            return ""

        conditions = []
        for h in having:
            try:
                cond = self.tree_visitor.visit(
                    h,
                    self._build_metric_condition
                )
                if cond:
                    conditions.append(cond)
            except TreeDepthExceededError as e:
                logger.error(f"HAVING 条件嵌套过深: {e}")
                raise FilterValidationError(str(e))

        if not conditions:
            return ""

        return " AND ".join(conditions)

    def _build_dimension_condition(
        self,
        node: Dict[str, Any],
        tables_used: Set[str]
    ) -> str:
        """
        构建维度过滤条件（WHERE 叶子节点）

        Args:
            node: 条件节点
            tables_used: 收集使用到的表

        Returns:
            SQL 条件字符串
        """
        dim_name = node.get('dimension')
        operator = node.get('operator', '=').upper()
        value = node.get('value')
        value_type = self._normalize_value_type(node.get('value_type'))

        if not dim_name:
            raise FilterValidationError(f"缺少 dimension 字段: {node}")

        # 获取维度定义
        dim = self._dimensions_index.get(dim_name)
        if not dim:
            raise FilterValidationError(f"未知的维度: {dim_name}")

        # 记录使用的表
        tables_used.add(dim.table)

        # 获取列引用（使用完整表名）
        column_ref = dim.get_column_ref()

        # 规范化值（应用同义词映射）
        if isinstance(value, str) and not self._contains_sql_function_type(value_type):
            value = dim.normalize_value(value)

        # 验证值
        if not self._validate_filter_value(dim, value, operator, value_type):
            raise FilterValidationError(
                f"维度 '{dim_name}' 的值 '{value}' 不在允许列表中"
            )

        # 构建条件
        return self._format_condition(column_ref, operator, value, value_type)

    def _build_metric_condition(self, node: Dict[str, Any]) -> str:
        """
        构建指标过滤条件（HAVING 叶子节点）

        Args:
            node: 条件节点

        Returns:
            SQL 条件字符串
        """
        metric_name = node.get('metric')
        operator = node.get('operator', '=').upper()
        value = node.get('value')
        value_type = self._normalize_value_type(node.get('value_type'))

        if not metric_name:
            raise FilterValidationError(f"缺少 metric 字段: {node}")

        # 获取指标定义
        metric = self._metrics_index.get(metric_name)
        if not metric:
            raise FilterValidationError(f"未知的指标: {metric_name}")

        # 使用指标表达式作为列引用
        column_ref = metric.expression

        # 构建条件
        return self._format_condition(column_ref, operator, value, value_type)

    def _format_condition(
        self,
        column_ref: str,
        operator: str,
        value: Any,
        value_type: Optional[Any] = None
    ) -> str:
        """
        格式化单个条件

        Args:
            column_ref: 列引用
            operator: 操作符
            value: 值

        Returns:
            格式化的条件字符串
        """
        operator = operator.upper()

        # 处理 IN / NOT IN
        if operator in ('IN', 'NOT IN'):
            if isinstance(value, list):
                formatted_values = [
                    self._format_value(v, self._get_list_item_value_type(value_type, idx))
                    for idx, v in enumerate(value)
                ]
                return f"{column_ref} {operator} ({', '.join(formatted_values)})"
            else:
                formatted = self._format_value(value, value_type)
                return f"{column_ref} {operator} ({formatted})"

        # 处理 LIKE / NOT LIKE
        if operator in ('LIKE', 'NOT LIKE'):
            formatted = self._format_value(value, value_type)
            return f"{column_ref} {operator} {formatted}"

        # 处理 IS NULL / IS NOT NULL
        if operator in ('IS NULL', 'IS NOT NULL'):
            return f"{column_ref} {operator}"

        # 处理 BETWEEN
        if operator == 'BETWEEN':
            if isinstance(value, list) and len(value) == 2:
                v1 = self._format_value(value[0], self._get_list_item_value_type(value_type, 0))
                v2 = self._format_value(value[1], self._get_list_item_value_type(value_type, 1))
                return f"{column_ref} BETWEEN {v1} AND {v2}"
            raise FilterValidationError(f"BETWEEN 需要两个值: {value}")

        # 普通比较操作符
        formatted = self._format_value(value, value_type)
        return f"{column_ref} {operator} {formatted}"

    def _format_value(self, value: Any, value_type: Optional[Any] = None) -> str:
        """
        格式化值

        Args:
            value: 原始值

        Returns:
            格式化后的值
        """
        if value is None:
            return "NULL"

        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"

        if isinstance(value, (int, float)):
            return str(value)

        if isinstance(value, str):
            raw = value.strip()
            unwrapped = self._unwrap_quoted_function(raw)
            normalized_value_type = self._normalize_scalar_value_type(value_type)

            # 显式 literal：始终按普通字符串处理
            if normalized_value_type == 'literal':
                return self.dialect.quote_string(value)

            # 显式 sql_function：仅白名单函数按函数处理，否则回退字面量
            if normalized_value_type == 'sql_function':
                if self._is_sql_function(raw):
                    return raw
                if unwrapped and self._is_sql_function(unwrapped):
                    return unwrapped
                logger.warning(f"无效的 sql_function，按字面量处理: {value}")
                return self.dialect.quote_string(value)

            # 检查是否是 SQL 函数
            if self._is_sql_function(raw):
                return raw
            # 兼容 LLM 返回带引号的函数值，如 "'current_date()'"
            if unwrapped and self._is_sql_function(unwrapped):
                return unwrapped
            # 普通字符串
            return self.dialect.quote_string(value)

        return str(value)

    def _is_sql_function(self, value: str) -> bool:
        """
        判断值是否是 SQL 函数

        Args:
            value: 值

        Returns:
            是否是 SQL 函数
        """
        if not isinstance(value, str):
            return False

        value_lower = value.lower().strip()

        # 检查是否匹配 function(...) 模式
        match = re.match(r'^([a-z_][a-z0-9_]*)\s*\(', value_lower)
        if match:
            func_name = match.group(1)
            return func_name in SQL_SAFE_FUNCTIONS

        return False

    def _unwrap_quoted_function(self, value: str) -> Optional[str]:
        """
        去除最外层引号，尝试提取函数表达式

        示例:
            "'current_date()'" -> "current_date()"
            "\"date_sub(current_date(), interval 1 day)\"" -> "date_sub(current_date(), interval 1 day)"
        """
        if not isinstance(value, str):
            return None
        text = value.strip()
        if len(text) < 3:
            return None
        if (text[0] == "'" and text[-1] == "'") or (text[0] == '"' and text[-1] == '"'):
            inner = text[1:-1].strip()
            return inner or None
        return None

    def _normalize_value_type(self, value_type: Any) -> Optional[Any]:
        """规范化 value_type，仅允许 literal/sql_function（字符串或数组）。"""
        if isinstance(value_type, str):
            normalized = value_type.strip().lower()
            if normalized in FILTER_VALUE_TYPES:
                return normalized
            return None

        if isinstance(value_type, list):
            normalized_list: List[str] = []
            for item in value_type:
                if not isinstance(item, str):
                    return None
                normalized_item = item.strip().lower()
                if normalized_item not in FILTER_VALUE_TYPES:
                    return None
                normalized_list.append(normalized_item)
            return normalized_list or None

        return None

    def _normalize_scalar_value_type(self, value_type: Any) -> Optional[str]:
        """将 value_type 归一到标量类型。"""
        normalized = self._normalize_value_type(value_type)
        if isinstance(normalized, list):
            return normalized[0] if normalized else None
        return normalized

    def _get_list_item_value_type(self, value_type: Any, idx: int) -> Optional[str]:
        """获取 IN/BETWEEN 列表值对应的 value_type。"""
        normalized = self._normalize_value_type(value_type)
        if isinstance(normalized, list):
            if idx < len(normalized):
                return normalized[idx]
            return None
        return normalized

    def _contains_sql_function_type(self, value_type: Any) -> bool:
        """判断 value_type 是否包含 sql_function。"""
        normalized = self._normalize_value_type(value_type)
        if normalized == 'sql_function':
            return True
        if isinstance(normalized, list):
            return 'sql_function' in normalized
        return False

    def _validate_filter_value(
        self,
        dim: SemanticDimension,
        value: Any,
        operator: str,
        value_type: Optional[Any] = None
    ) -> bool:
        """
        验证过滤值是否有效

        Args:
            dim: 维度定义
            value: 值
            operator: 操作符

        Returns:
            是否有效
        """
        # 如果没有 allowed_values，不做验证
        if not dim.allowed_values:
            return True

        # IN 操作符需要验证列表中的每个值
        if operator.upper() in ('IN', 'NOT IN'):
            if isinstance(value, list):
                allowed_values = [str(av) for av in dim.allowed_values]
                for idx, v in enumerate(value):
                    item_type = self._get_list_item_value_type(value_type, idx)
                    if item_type == 'sql_function':
                        if isinstance(v, str):
                            unwrapped = self._unwrap_quoted_function(v.strip())
                            if self._is_sql_function(v) or (unwrapped and self._is_sql_function(unwrapped)):
                                continue
                        return False
                    if str(v) not in allowed_values:
                        return False
                return True

        normalized_value_type = self._normalize_scalar_value_type(value_type)

        # 普通值验证
        if isinstance(value, str):
            if normalized_value_type == 'sql_function':
                if self._is_sql_function(value):
                    return True
                unwrapped = self._unwrap_quoted_function(value.strip())
                return bool(unwrapped and self._is_sql_function(unwrapped))
            if normalized_value_type == 'literal':
                return value in dim.allowed_values or str(value) in [str(v) for v in dim.allowed_values]

            # 如果是 SQL 函数，跳过验证
            if self._is_sql_function(value):
                return True
            unwrapped = self._unwrap_quoted_function(value.strip())
            if unwrapped and self._is_sql_function(unwrapped):
                return True
            return value in dim.allowed_values or str(value) in [str(v) for v in dim.allowed_values]

        return True


def build_metric_filter(metric: SemanticMetric) -> Optional[str]:
    """
    构建指标的默认过滤条件

    Args:
        metric: 指标定义

    Returns:
        过滤条件字符串，如果没有返回 None
    """
    if not metric.filter:
        return None
    return metric.filter
