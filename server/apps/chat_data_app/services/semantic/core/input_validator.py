"""
输入参数验证器

验证用户输入参数的合法性（在 DSL 生成之前）。
"""

from typing import List, Dict, Tuple, Optional, Any
import logging
import re
from datetime import datetime

from .filter_normalization import split_text_between_filter, time_equal_value_to_range

logger = logging.getLogger(__name__)


FILTER_OPERATORS = {
    '=', '!=', '<>', '>', '<', '>=', '<=', 'IN', 'NOT IN', 'LIKE',
    'NOT LIKE', 'STARTS WITH', 'ENDS WITH', 'IS NULL', 'IS NOT NULL',
    'BETWEEN', 'EMPTY STRING',
}
#: 无需筛选值的过滤操作符（IS NULL / IS NOT NULL / EMPTY STRING）。
_NO_VALUE_FILTER_OPERATORS = {'IS NULL', 'IS NOT NULL', 'EMPTY STRING'}
EXPRESSION_OPERATORS = {'=', '!=', '<>', '>', '<', '>=', '<='}
FREE_EXPRESSION_OPERATORS = EXPRESSION_OPERATORS | {'IN', 'NOT IN'}
BINARY_OPERATORS = {'+', '-', '*', '/'}
LIKE_OPERATORS = {'LIKE', 'NOT LIKE', 'STARTS WITH', 'ENDS WITH'}
BUILDER_ONLY_FILTER_FIELDS = {'rightTable', 'rightTableAlias', 'rightColumn'}
FILTER_VALUE_TYPES = {'literal', 'sql_function'}
FREE_EXPRESSION_VALUE_TYPE = 'expression'
UNSAFE_EXPRESSION_PATTERN = re.compile(
    r";|--|/\*|\*/|\b(?:INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|MERGE|CALL|EXECUTE)\b",
    re.IGNORECASE,
)
LOGICAL_TYPES = {'text', 'number', 'date', 'datetime', 'enum'}
DATE_PATTERN = re.compile(r'^\d{4}-\d{2}-\d{2}$')
DATETIME_PATTERN = re.compile(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$')


class InputValidator:
    """
    输入参数验证器

    验证原始输入参数，在 DSL 生成之前使用。
    与 SemanticValidator（DSL 验证）不同，本类验证的是原始输入格式。
    """

    def __init__(
        self,
        metrics_map: Dict[str, Dict],
        dimensions_map: Dict[str, Dict],
        max_limit: int = 100,
    ):
        """
        初始化验证器

        Args:
            metrics_map: 指标名称到指标配置的映射
            dimensions_map: 维度名称到维度配置的映射
        """
        self.metrics_map = metrics_map
        self.dimensions_map = dimensions_map
        self.max_limit = max_limit

    def validate_metrics(self, metrics: List[str]) -> Tuple[List[Dict], Optional[str]]:
        """
        验证指标参数

        Args:
            metrics: 指标名称列表

        Returns:
            (matched_metrics, error_msg): 匹配的指标列表和错误消息
        """
        matched_metrics = []
        for name in metrics:
            if name in self.metrics_map:
                matched_metrics.append(self.metrics_map[name])
            else:
                return [], f"错误：指标 '{name}' 不存在。可用指标：{', '.join(self.metrics_map.keys())}"

        if not matched_metrics:
            return [], f"错误：请至少选择一个指标。可用指标：{', '.join(self.metrics_map.keys())}"

        return matched_metrics, None

    def validate_dimensions(self, dimensions: List[Any]) -> Tuple[List[Any], Optional[str]]:
        """
        验证维度参数

        Args:
            dimensions: 维度列表，支持两种格式：
                - 字符串: "维度名"
                - 字典: {"name": "维度名", "time_grain": "month"}

        Returns:
            (validated_dimensions, error_msg): 验证后的维度列表和错误消息
        """
        validated_dimensions = []
        for dim_item in dimensions:
            # 支持字符串和 dict 两种格式
            if isinstance(dim_item, dict):
                name = dim_item.get('name')
                time_grain = dim_item.get('time_grain', dim_item.get('grain'))
                # 验证 time_grain 值
                valid_grains = ['year', 'quarter', 'month', 'week', 'day', 'hour']
                if time_grain and time_grain not in valid_grains:
                    return [], f"错误：time_grain '{time_grain}' 无效。可选值：{', '.join(valid_grains)}"
            else:
                name = dim_item

            if name not in self.dimensions_map:
                return [], f"错误：维度 '{name}' 不存在。可用维度：{', '.join(self.dimensions_map.keys())}"

            # 保持原格式返回（str 或 dict）
            validated_dimensions.append(dim_item)

        return validated_dimensions, None

    def validate_filters(self, filters: List[Dict]) -> Tuple[List[Dict], Optional[str]]:
        """
        验证 WHERE 过滤条件（支持树形和平铺两种格式）

        Args:
            filters: 过滤条件列表

        Returns:
            (validated_filters, error_msg): 验证后的过滤条件和错误消息
        """
        if not filters:
            return [], None

        # 检查是否是树形格式
        if len(filters) == 1 and isinstance(filters[0], dict):
            first_filter = filters[0]
            if 'and' in first_filter or 'or' in first_filter:
                # 树形格式：递归验证
                err = self._validate_tree_filter(first_filter)
                if err:
                    return [], err
                return [self._normalize_tree_filter_allowed_values(first_filter)], None

        # 平铺格式：逐个验证叶子节点
        validated_filters = []
        for f in filters:
            err = self._validate_filter_leaf(f)
            if err:
                return [], err
            validated_filters.append(self._normalize_filter_leaf(f))

        return validated_filters, None

    def _validate_tree_filter(self, filter_node: Dict) -> Optional[str]:
        """
        递归验证树形过滤条件

        Args:
            filter_node: 过滤节点（可以是 AND/OR 节点或叶子节点）

        Returns:
            错误消息，验证通过返回 None
        """
        if not isinstance(filter_node, dict):
            return "错误：无效的过滤条件格式，必须是对象"

        # OR 节点
        if 'or' in filter_node:
            children = filter_node['or']
            if not isinstance(children, list):
                return "错误：'or' 节点的值必须是数组"
            for child in children:
                err = self._validate_tree_filter(child)
                if err:
                    return err
            return None

        # AND 节点
        if 'and' in filter_node:
            children = filter_node['and']
            if not isinstance(children, list):
                return "错误：'and' 节点的值必须是数组"
            for child in children:
                err = self._validate_tree_filter(child)
                if err:
                    return err
            return None

        err = self._validate_filter_leaf(filter_node)
        if err:
            return err
        return None

    def _validate_filter_leaf(self, filter_node: Dict) -> Optional[str]:
        """验证普通过滤叶子或结构化表达式过滤叶子。"""
        if not isinstance(filter_node, dict):
            return "错误：无效的过滤条件格式，必须是对象"
        builder_only_fields = BUILDER_ONLY_FILTER_FIELDS & set(filter_node.keys())
        if builder_only_fields:
            return f"错误：语义 DSL 过滤条件不支持字段 {', '.join(sorted(builder_only_fields))}；字段间比较请使用结构化 left/right 表达"
        if self._is_expression_filter(filter_node):
            if {'valueType', 'rightExpression'} & set(filter_node.keys()):
                return "错误：结构化 left/right 与自由 rightExpression 不能混用"
            return self._validate_expression_filter(filter_node)
        if 'dimension' in filter_node:
            dim_name = filter_node.get('dimension')
            if dim_name not in self.dimensions_map:
                return f"错误：过滤条件中的维度 '{dim_name}' 不存在。可用维度：{', '.join(self.dimensions_map.keys())}"
            operator = str(filter_node.get('operator', '=')).upper()
            if operator not in FILTER_OPERATORS:
                return f"错误：过滤条件操作符 '{operator}' 不支持。可选值：{', '.join(sorted(FILTER_OPERATORS))}"
            err = self.validate_dimension_operator(dim_name, operator)
            if err:
                return err
            err = self._validate_free_right_expression(filter_node)
            if err:
                return err
            err = self._validate_filter_value_type(filter_node)
            if err:
                return err
            err = self._validate_filter_logical_value(dim_name, filter_node)
            if err:
                return err
            err = self._validate_filter_allowed_value(filter_node)
            if err:
                return err
            return None
        return "错误：无效的过滤条件格式，必须是叶子节点或 and/or 组合节点"

    @staticmethod
    def _validate_filter_value_type(filter_node: Dict) -> Optional[str]:
        if 'valueType' in filter_node:
            if filter_node.get('valueType') != FREE_EXPRESSION_VALUE_TYPE:
                return "错误：valueType 仅支持 expression"
            return None
        if 'value_type' not in filter_node:
            return None
        value_type = filter_node.get('value_type')
        values = value_type if isinstance(value_type, list) else [value_type]
        if not values:
            return "错误：value_type 仅支持 literal 或 sql_function"
        for item in values:
            if not isinstance(item, str) or item.strip().lower() not in FILTER_VALUE_TYPES:
                return "错误：value_type 仅支持 literal 或 sql_function"
        return None

    @staticmethod
    def _validate_free_right_expression(node: Dict) -> Optional[str]:
        has_value_type = 'valueType' in node
        has_expression = 'rightExpression' in node
        if not has_value_type and not has_expression:
            return None
        if node.get('valueType') != FREE_EXPRESSION_VALUE_TYPE or not has_expression:
            return "错误：自由右侧表达式必须同时使用 valueType='expression' 和 rightExpression"
        operator = str(node.get('operator', '=')).upper()
        if operator not in FREE_EXPRESSION_OPERATORS:
            return f"错误：自由右侧表达式不支持操作符 '{operator}'。可选值：{', '.join(sorted(FREE_EXPRESSION_OPERATORS))}"
        expression = node.get('rightExpression')
        if not isinstance(expression, str) or not expression.strip():
            return "错误：rightExpression 必须是非空字符串"
        if UNSAFE_EXPRESSION_PATTERN.search(expression):
            return "错误：rightExpression 只允许单条只读 SQL 表达式，不允许注释、分号或写操作"
        return None

    @staticmethod
    def _is_expression_filter(filter_node: Dict) -> bool:
        return isinstance(filter_node, dict) and isinstance(filter_node.get('left'), dict) and 'right' in filter_node

    def _validate_expression_filter(self, filter_node: Dict) -> Optional[str]:
        operator = str(filter_node.get('operator', '=')).upper()
        if operator not in EXPRESSION_OPERATORS:
            return f"错误：表达式过滤暂不支持操作符 '{operator}'。可选值：{', '.join(sorted(EXPRESSION_OPERATORS))}"

        left = filter_node.get('left')
        right = filter_node.get('right')
        if not isinstance(left, dict) or left.get('type') != 'dimension':
            return "错误：表达式过滤 left 只支持 dimension"
        left_name = str(left.get('name') or '').strip()
        if left_name not in self.dimensions_map:
            return f"错误：过滤条件中的维度 '{left_name}' 不存在。可用维度：{', '.join(self.dimensions_map.keys())}"
        err = self.validate_dimension_operator(left_name, operator)
        if err:
            return err

        err = self._validate_expression_operand(right, path='right')
        if err:
            return err
        err = self._validate_expression_logical_value(left_name, right)
        if err:
            return err
        return self._validate_expression_allowed_value(left_name, operator, right)

    def validate_dimension_operator(self, dim_name: str, operator: Any) -> Optional[str]:
        """Enforce the optional per-dimension operator contract."""
        normalized_operator = str(operator or '=').strip().upper()
        if normalized_operator not in FILTER_OPERATORS:
            return f"错误：过滤条件操作符 '{normalized_operator}' 不支持。可选值：{', '.join(sorted(FILTER_OPERATORS))}"
        dim_config = self.dimensions_map.get(dim_name) or {}
        configured = dim_config.get('allowed_operators')
        if not configured:
            return None
        if not isinstance(configured, list):
            return f"错误：维度 '{dim_name}' 的 allowed_operators 配置必须是数组"
        allowed = {
            str(item or '').strip().upper()
            for item in configured
            if str(item or '').strip()
        }
        if normalized_operator not in allowed:
            return f"错误：维度 '{dim_name}' 不允许操作符 '{normalized_operator}'。可选值：{', '.join(sorted(allowed))}"
        return None

    def _validate_expression_operand(self, operand: Any, path: str) -> Optional[str]:
        if not isinstance(operand, dict):
            return f"错误：表达式过滤 {path} 必须是对象"
        operand_type = str(operand.get('type') or '').strip().lower()
        if operand_type == 'dimension':
            return self._validate_dimension_operand(operand)
        if operand_type == 'literal':
            value = operand.get('value')
            if isinstance(value, (dict, list)):
                return f"错误：表达式过滤 {path}.value 只支持字符串、数字、布尔值或空值"
            return None
        if operand_type == 'binary':
            return self._validate_binary_operand(operand, path)
        return f"错误：表达式过滤 {path}.type 只支持 dimension、literal、binary"

    def _validate_dimension_operand(self, operand: Any) -> Optional[str]:
        """校验表达式过滤中的维度引用。"""
        dim_name = str(operand.get('name') or '').strip()
        if dim_name not in self.dimensions_map:
            return f"错误：表达式过滤引用的维度 '{dim_name}' 不存在。可用维度：{', '.join(self.dimensions_map.keys())}"
        return None

    def _validate_binary_operand(self, operand: Any, path: str) -> Optional[str]:
        """校验二元表达式：运算符合法性、两侧递归校验、禁止除以 0。"""
        operator = str(operand.get('operator') or '').strip()
        if operator not in BINARY_OPERATORS:
            return f"错误：表达式过滤二元运算符 '{operator}' 不支持。可选值：{', '.join(sorted(BINARY_OPERATORS))}"
        left = operand.get('left')
        right = operand.get('right')
        err = self._validate_expression_operand(left, path=f'{path}.left')
        if err:
            return err
        err = self._validate_expression_operand(right, path=f'{path}.right')
        if err:
            return err
        if operator == '/' and isinstance(right, dict) and right.get('type') == 'literal' and self._is_zero_literal(right.get('value')):
            return "错误：表达式过滤不允许除以 0"
        return None

    @staticmethod
    def _is_zero_literal(value: Any) -> bool:
        try:
            return float(value) == 0
        except Exception:
            return False

    def _validate_expression_allowed_value(self, left_dim_name: str, operator: str, right: Dict) -> Optional[str]:
        """结构化过滤右侧为 literal 时，沿用枚举维度值校验。"""
        dim_config = self.dimensions_map.get(left_dim_name) or {}
        allowed_values = dim_config.get('allowed_values') or []
        if not allowed_values or not self._should_validate_allowed_values(dim_config) or not isinstance(right, dict) or right.get('type') != 'literal':
            return None
        value = right.get('value')
        if value is None:
            return None
        allowed_set = {str(item) for item in allowed_values}
        checked_value = value if self._logical_type(dim_config) == 'enum' else self._normalize_allowed_value(dim_config, value)
        if str(checked_value) not in allowed_set:
            return f"错误：维度 '{left_dim_name}' 的值 '{value}' 不在允许值范围内。可用值：{', '.join(allowed_set)}"
        return None

    def _validate_filter_logical_value(self, dim_name: str, filter_node: Dict) -> Optional[str]:
        dim_config = self.dimensions_map.get(dim_name) or {}
        operator = str(filter_node.get('operator', '=')).upper()
        if operator in ('IS NULL', 'IS NOT NULL'):
            return None
        filter_node = split_text_between_filter(filter_node)
        values = self._filter_literal_values(filter_node)
        return self._validate_logical_values(dim_name, dim_config, values, operator)

    def _validate_expression_logical_value(self, left_dim_name: str, right: Dict) -> Optional[str]:
        if not isinstance(right, dict) or right.get('type') != 'literal':
            return None
        dim_config = self.dimensions_map.get(left_dim_name) or {}
        return self._validate_logical_values(left_dim_name, dim_config, [right.get('value')], '=')

    @staticmethod
    def _filter_literal_values(filter_node: Dict) -> List[Any]:
        value = filter_node.get('value')
        values = value if isinstance(value, list) else [value]
        value_type = filter_node.get('value_type')
        value_types = value_type if isinstance(value_type, list) else [value_type] * len(values)
        literal_values = []
        for idx, item in enumerate(values):
            item_type = value_types[idx] if idx < len(value_types) else None
            if str(item_type or '').strip().lower() == 'sql_function':
                continue
            literal_values.append(item)
        return literal_values

    def _validate_logical_values(self, dim_name: str, dim_config: Dict, values: List[Any], operator: str) -> Optional[str]:
        logical_type = self._logical_type(dim_config)
        if not logical_type or logical_type == 'text':
            return None
        if logical_type not in LOGICAL_TYPES:
            return f"错误：维度 '{dim_name}' 的 logical_type '{logical_type}' 不支持。可选值：{', '.join(sorted(LOGICAL_TYPES))}"
        if logical_type == 'enum':
            return self._validate_enum_config(dim_name, dim_config)
        for item in values:
            if item is None:
                continue
            if logical_type in {'date', 'datetime'} and self._is_sql_function_literal(item):
                continue
            if self._is_supported_time_filter_shorthand(dim_config, logical_type, operator, item):
                continue
            err = self._validate_single_logical_value(dim_name, logical_type, item)
            if err:
                return err
        return None

    @staticmethod
    def _enum_allowed_set(allowed_values: Any) -> set:
        """归一化枚举允许值集合；配置非法返回空集。"""
        return {str(item).strip() for item in allowed_values or [] if str(item or '').strip()} if isinstance(allowed_values, list) else set()

    @staticmethod
    def _enum_mapping_keys(mapping: Any) -> set:
        """归一化枚举映射键集合（键值均非空才计入）；配置非法返回空集。"""
        return {str(key).strip() for key, value in (mapping or {}).items() if str(key or '').strip() and str(value or '').strip()} if isinstance(mapping, dict) else set()

    @staticmethod
    def _validate_enum_config(dim_name: str, dim_config: Dict) -> Optional[str]:
        allowed_values = dim_config.get('allowed_values')
        mapping = dim_config.get('mapping')
        allowed_set = InputValidator._enum_allowed_set(allowed_values)
        mapping_keys = InputValidator._enum_mapping_keys(mapping)
        if not allowed_set or not mapping_keys:
            return f"错误：维度 '{dim_name}' 的 logical_type 为 enum 时必须同时配置 allowed_values 和 mapping"
        missing_values = sorted(allowed_set - mapping_keys)
        if missing_values:
            return f"错误：维度 '{dim_name}' 的枚举值 '{missing_values[0]}' 缺少物理值映射"
        return None

    @staticmethod
    def _logical_type(dim_config: Dict) -> str:
        return str((dim_config or {}).get('logical_type') or '').strip().lower()

    @staticmethod
    def _validate_single_logical_value(dim_name: str, logical_type: str, value: Any) -> Optional[str]:
        if logical_type == 'text':
            return None
        if logical_type == 'number':
            return None if InputValidator._is_number_value(value) else f"错误：维度 '{dim_name}' 的值必须是数字"
        if logical_type == 'date':
            return None if InputValidator._is_date_text(value) else f"错误：维度 '{dim_name}' 的值必须是 yyyy-MM-dd 格式"
        if logical_type == 'datetime':
            return None if InputValidator._is_datetime_text(value) else f"错误：维度 '{dim_name}' 的值必须是 yyyy-MM-dd HH:mm:ss 格式"
        return None

    @staticmethod
    def _is_sql_function_literal(value: Any) -> bool:
        if not isinstance(value, str):
            return False
        return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\([^;]*\)", value.strip()))

    @staticmethod
    def _is_supported_time_filter_shorthand(dim_config: Dict, logical_type: str, operator: str, value: Any) -> bool:
        if logical_type not in {'date', 'datetime'}:
            return False
        if not InputValidator._has_physical_field(dim_config):
            return False
        if operator == '=':
            return time_equal_value_to_range(value) is not None
        return False

    @staticmethod
    def _has_physical_field(dim_config: Dict) -> bool:
        return any((dim_config or {}).get(key) for key in ('column', 'expression', 'field_name'))

    @staticmethod
    def _is_number_value(value: Any) -> bool:
        if isinstance(value, bool) or value is None:
            return False
        if isinstance(value, (int, float)):
            return True
        return False

    @staticmethod
    def _is_date_text(value: Any) -> bool:
        if not isinstance(value, str) or not DATE_PATTERN.fullmatch(value.strip()):
            return False
        try:
            datetime.strptime(value.strip(), "%Y-%m-%d")
        except ValueError:
            return False
        return True

    @staticmethod
    def _is_datetime_text(value: Any) -> bool:
        if not isinstance(value, str) or not DATETIME_PATTERN.fullmatch(value.strip()):
            return False
        try:
            datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return False
        return True

    def _validate_filter_allowed_value(self, filter_node: Dict) -> Optional[str]:
        """校验枚举维度过滤值是否在 allowed_values 范围内。"""
        dim_name = filter_node.get('dimension')
        dim_config = self.dimensions_map.get(dim_name) or {}
        allowed_values = dim_config.get('allowed_values') or []
        if not allowed_values or not self._should_validate_allowed_values(dim_config):
            return None

        operator = str(filter_node.get('operator', '=')).upper()
        value = filter_node.get('value')
        if value is None or operator in ('IS NULL', 'IS NOT NULL') or operator in LIKE_OPERATORS:
            return None

        values = self._filter_literal_values(filter_node)
        allowed_set = {str(item) for item in allowed_values}
        invalid_values = []
        for item in values:
            checked_value = item if self._logical_type(dim_config) == 'enum' else self._normalize_allowed_value(dim_config, item)
            if str(checked_value) not in allowed_set:
                invalid_values.append(str(item))
        if invalid_values:
            return f"错误：维度 '{dim_name}' 的值 '{invalid_values[0]}' 不在允许值范围内。可用值：{', '.join(allowed_set)}"
        return None

    def _normalize_filter_leaf(self, filter_node: Dict) -> Dict:
        if self._is_expression_filter(filter_node):
            return self._normalize_expression_filter_allowed_value(filter_node)
        return self._normalize_filter_allowed_value(filter_node)

    def _normalize_expression_filter_allowed_value(self, filter_node: Dict) -> Dict:
        left = filter_node.get('left') or {}
        right = filter_node.get('right')
        left_dim_name = left.get('name')
        dim_config = self.dimensions_map.get(left_dim_name) or {}
        if not self._should_normalize_allowed_values(dim_config) or not isinstance(right, dict) or right.get('type') != 'literal':
            return filter_node
        normalized = dict(filter_node)
        normalized_right = dict(right)
        if normalized_right.get('value') is not None:
            normalized_right['value'] = self._normalize_allowed_value(dim_config, normalized_right.get('value'))
        normalized['right'] = normalized_right
        return normalized

    @staticmethod
    def _normalize_allowed_value(dim_config: Dict, value: Any) -> Any:
        """按维度 mapping 将同义词值规范到枚举值。"""
        mapping = dim_config.get('mapping') or {}
        if not isinstance(mapping, dict) or value is None:
            return value
        value_text = str(value)
        for key, mapped_value in mapping.items():
            if str(key).lower() == value_text.lower():
                return mapped_value
        return value

    def _normalize_filter_allowed_value(self, filter_node: Dict) -> Dict:
        """返回应用 mapping 后的过滤条件副本。"""
        dim_name = filter_node.get('dimension')
        dim_config = self.dimensions_map.get(dim_name) or {}
        if not self._should_normalize_allowed_values(dim_config):
            return filter_node
        normalized = dict(filter_node)
        value = normalized.get('value')
        if isinstance(value, list):
            normalized['value'] = [self._normalize_allowed_value(dim_config, item) for item in value]
        elif value is not None:
            normalized['value'] = self._normalize_allowed_value(dim_config, value)
        return normalized

    def _normalize_tree_filter_allowed_values(self, filter_node: Dict) -> Dict:
        """递归规范化树形过滤中的枚举同义词值。"""
        if 'and' in filter_node:
            return {'and': [self._normalize_tree_filter_allowed_values(child) for child in filter_node.get('and', [])]}
        if 'or' in filter_node:
            return {'or': [self._normalize_tree_filter_allowed_values(child) for child in filter_node.get('or', [])]}
        return self._normalize_filter_leaf(filter_node)

    @staticmethod
    def _should_validate_allowed_values(dim_config: Dict) -> bool:
        logical_type = InputValidator._logical_type(dim_config)
        return logical_type == 'enum'

    @staticmethod
    def _should_normalize_allowed_values(dim_config: Dict) -> bool:
        mapping = (dim_config or {}).get('mapping')
        return bool(isinstance(mapping, dict) and mapping and InputValidator._should_validate_allowed_values(dim_config))

    def validate_having(self, having: List[Dict]) -> Tuple[List[Dict], Optional[str]]:
        """
        验证 HAVING 过滤条件

        Args:
            having: HAVING 条件列表

        Returns:
            (validated_having, error_msg): 验证后的 HAVING 条件和错误消息
        """
        validated_having = []
        for h in having:
            if not isinstance(h, dict):
                return [], "错误：HAVING 条件格式错误，必须是对象"
            if self._is_expression_having(h):
                if {'valueType', 'rightExpression'} & set(h.keys()):
                    return [], "错误：结构化 left/right 与自由 rightExpression 不能混用"
                err = self._validate_expression_having(h)
                if err:
                    return [], err
                validated_having.append(h)
                continue
            metric_name = h.get('metric')
            if metric_name and metric_name in self.metrics_map:
                err = self._validate_free_right_expression(h)
                if err:
                    return [], err
                validated_having.append(h)
            elif metric_name:
                return [], f"错误：HAVING 条件中的指标 '{metric_name}' 不存在。可用指标：{', '.join(self.metrics_map.keys())}"

        return validated_having, None

    @staticmethod
    def _is_expression_having(having_node: Dict) -> bool:
        return isinstance(having_node, dict) and isinstance(having_node.get('left'), dict) and 'right' in having_node

    def _validate_expression_having(self, having_node: Dict) -> Optional[str]:
        operator = str(having_node.get('operator', '=')).upper()
        if operator not in EXPRESSION_OPERATORS:
            return f"错误：HAVING 表达式暂不支持操作符 '{operator}'。可选值：{', '.join(sorted(EXPRESSION_OPERATORS))}"

        left = having_node.get('left')
        right = having_node.get('right')
        if not isinstance(left, dict) or left.get('type') != 'metric':
            return "错误：HAVING 表达式 left 只支持 metric"
        left_name = str(left.get('name') or '').strip()
        if left_name not in self.metrics_map:
            return f"错误：HAVING 条件中的指标 '{left_name}' 不存在。可用指标：{', '.join(self.metrics_map.keys())}"

        return self._validate_metric_expression_operand(right, path='right')

    def _validate_metric_expression_operand(self, operand: Any, path: str) -> Optional[str]:
        if not isinstance(operand, dict):
            return f"错误：HAVING 表达式 {path} 必须是对象"
        operand_type = str(operand.get('type') or '').strip().lower()
        if operand_type == 'metric':
            return self._validate_metric_name_operand(operand)
        if operand_type == 'literal':
            value = operand.get('value')
            if isinstance(value, (dict, list)):
                return f"错误：HAVING 表达式 {path}.value 只支持字符串、数字、布尔值或空值"
            return None
        if operand_type == 'binary':
            return self._validate_metric_binary_operand(operand, path)
        return f"错误：HAVING 表达式 {path}.type 只支持 metric、literal、binary"

    def _validate_metric_name_operand(self, operand: Any) -> Optional[str]:
        """校验 HAVING 表达式中的指标引用。"""
        metric_name = str(operand.get('name') or '').strip()
        if metric_name not in self.metrics_map:
            return f"错误：HAVING 表达式引用的指标 '{metric_name}' 不存在。可用指标：{', '.join(self.metrics_map.keys())}"
        return None

    def _validate_metric_binary_operand(self, operand: Any, path: str) -> Optional[str]:
        """校验 HAVING 二元表达式：运算符合法性、两侧递归校验、禁止除以 0。"""
        operator = str(operand.get('operator') or '').strip()
        if operator not in BINARY_OPERATORS:
            return f"错误：HAVING 表达式二元运算符 '{operator}' 不支持。可选值：{', '.join(sorted(BINARY_OPERATORS))}"
        left = operand.get('left')
        right = operand.get('right')
        err = self._validate_metric_expression_operand(left, path=f'{path}.left')
        if err:
            return err
        err = self._validate_metric_expression_operand(right, path=f'{path}.right')
        if err:
            return err
        if operator == '/' and isinstance(right, dict) and right.get('type') == 'literal' and self._is_zero_literal(right.get('value')):
            return "错误：HAVING 表达式不允许除以 0"
        return None

    def validate_order_by(
        self,
        order_by: List[Dict],
        metrics: List[str],
        dimensions: List[str]
    ) -> Tuple[List[Dict], Optional[str]]:
        """
        验证 ORDER BY 排序条件，只允许按当前查询输出字段排序

        Args:
            order_by: 排序列表
            metrics: 查询中的指标列表
            dimensions: 查询中的维度列表

        Returns:
            (validated_order_by, error_msg): 验证后的排序条件和错误消息
        """
        if not order_by:
            return [], None

        validated_order_by = []
        all_fields = set(metrics) | set(dimensions)
        available_fields = ', '.join(all_fields)

        for i, o in enumerate(order_by):
            # 检查元素类型
            if not isinstance(o, dict):
                logger.error(f"[排序验证] order_by[{i}] 不是字典类型: {type(o)}, 值: {o}")
                return [], f"错误：排序条件格式错误，第 {i+1} 项不是字典: {o}"

            field = o.get('field')
            direction = o.get('direction', 'DESC').upper()

            # 验证字段存在
            if not field:
                logger.error(f"[排序验证] order_by[{i}] 缺少 'field' 字段: {o}")
                return [], f"错误：排序条件缺少 'field' 字段，第 {i+1} 项: {o}"

            if field not in all_fields:
                return [], f"错误：排序字段 '{field}' 不在当前查询输出字段中。ORDER BY 只能使用已选择的指标或维度；可用字段：{available_fields}"

            # 验证排序方向
            if direction not in ['ASC', 'DESC']:
                return [], f"错误：排序方向 '{direction}' 无效，只能是 'ASC' 或 'DESC'"

            validated_order_by.append({
                'field': field,
                'direction': direction
            })

        return validated_order_by, None

    def validate_limit(self, limit: Optional[int], max_limit: Optional[int] = None) -> Tuple[Optional[int], Optional[str]]:
        """
        验证 LIMIT 限制条数

        Args:
            limit: 限制条数
            max_limit: 最大限制（默认使用 CHATDATA_ROW_LIMIT）

        Returns:
            (validated_limit, error_msg): 验证后的限制条数和错误消息
        """
        effective_max_limit = self.max_limit if max_limit is None else max_limit
        if limit is None:
            return None, None

        # 验证是否为正整数
        if not isinstance(limit, int) or limit <= 0:
            return None, f"错误：LIMIT 必须为正整数，当前值：{limit}"

        # 防止过大的 LIMIT
        if limit > effective_max_limit:
            return None, f"错误：LIMIT 不能超过 {effective_max_limit}，当前值：{limit}"

        return limit, None

    def validate_offset(self, offset: Optional[int]) -> Tuple[Optional[int], Optional[str]]:
        """
        验证 OFFSET 跳过条数。

        Args:
            offset: 跳过条数

        Returns:
            (validated_offset, error_msg): 验证后的跳过条数和错误消息
        """
        if offset is None:
            return None, None

        if not isinstance(offset, int) or offset < 0:
            return None, f"错误：OFFSET 必须为非负整数，当前值：{offset}"

        return offset, None


# 兼容别名
SemanticValidator = InputValidator
