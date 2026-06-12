"""
参数验证器

验证 DSL 参数的合法性。
"""

from typing import List, Dict, Set, Optional, Any
from dataclasses import dataclass, field
import logging

from ..models import SemanticConfig, SemanticDimension, SemanticMetric
from .dsl import SemanticQuery, DimensionSpec, QueryMode

logger = logging.getLogger(__name__)


@dataclass
class ValidationError:
    """验证错误"""
    field: str
    message: str
    code: str = "VALIDATION_ERROR"

    def to_dict(self) -> Dict:
        return {
            'field': self.field,
            'message': self.message,
            'code': self.code
        }


@dataclass
class ValidationResult:
    """验证结果"""
    is_valid: bool
    errors: List[ValidationError] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def add_error(self, field: str, message: str, code: str = "VALIDATION_ERROR"):
        """添加错误"""
        self.errors.append(ValidationError(field, message, code))
        self.is_valid = False

    def add_warning(self, message: str):
        """添加警告"""
        self.warnings.append(message)

    def to_dict(self) -> Dict:
        return {
            'is_valid': self.is_valid,
            'errors': [e.to_dict() for e in self.errors],
            'warnings': self.warnings
        }


class SemanticValidator:
    """
    语义验证器

    验证 DSL 查询的合法性，包括：
    - 指标和维度是否存在
    - 操作符是否有效
    - 值是否在允许范围内
    - 时间粒度是否有效
    """

    VALID_OPERATORS = {
        '=', '!=', '<>', '>', '>=', '<', '<=',
        'IN', 'NOT IN', 'LIKE', 'NOT LIKE',
        'IS NULL', 'IS NOT NULL', 'BETWEEN'
    }

    VALID_TIME_GRAINS = {
        'year', 'quarter', 'month', 'week', 'day', 'hour', 'minute'
    }

    def __init__(self, config: SemanticConfig):
        """
        初始化验证器

        Args:
            config: 语义配置
        """
        self.config = config
        # 构建索引
        self._metrics_index = {m.name: m for m in config.metrics}
        self._dimensions_index = {d.name: d for d in config.dimensions}

    def validate(self, query: SemanticQuery) -> ValidationResult:
        """
        验证 DSL 查询

        Args:
            query: 语义查询

        Returns:
            验证结果
        """
        result = ValidationResult(is_valid=True)

        # 验证指标
        self._validate_metrics(query.metrics, query.mode, result)

        # 验证维度
        self._validate_dimensions(query.dimensions, result)

        # 验证过滤条件
        self._validate_filters(query.filters, result)

        # 验证 HAVING 条件
        self._validate_having(query.having, result)

        # 验证排序
        self._validate_order_by(query.order_by, result)

        # 验证分页
        self._validate_pagination(query.limit, query.offset, result)

        return result

    def _validate_metrics(
        self,
        metrics: List[str],
        mode: QueryMode,
        result: ValidationResult
    ) -> None:
        """验证指标"""
        if not metrics:
            result.add_error('metrics', '必须指定至少一个指标', 'MISSING_METRICS')
            return

        for metric_name in metrics:
            if metric_name in self._metrics_index:
                continue
            if metric_name in self._dimensions_index:
                if mode == QueryMode.DETAIL:
                    continue
                result.add_error(
                    'metrics',
                    f"字段 '{metric_name}' 只能用于明细查询，请改用 detail 模式或放入 dimensions",
                    'RAW_FIELD_REQUIRES_DETAIL_MODE'
                )
                continue
            result.add_error(
                'metrics',
                f"未知的指标: {metric_name}",
                'UNKNOWN_METRIC'
            )

    def _validate_dimensions(
        self,
        dimensions: List[DimensionSpec],
        result: ValidationResult
    ) -> None:
        """验证维度"""
        for dim_spec in dimensions:
            dim_name = dim_spec.name if isinstance(dim_spec, DimensionSpec) else dim_spec

            if dim_name not in self._dimensions_index:
                result.add_error(
                    'dimensions',
                    f"未知的维度: {dim_name}",
                    'UNKNOWN_DIMENSION'
                )
                continue

            # 验证时间粒度
            if isinstance(dim_spec, DimensionSpec) and dim_spec.grain:
                dim = self._dimensions_index[dim_name]
                if dim.type.value != 'time':
                    result.add_warning(
                        f"维度 '{dim_name}' 不是时间类型，时间粒度可能不适用"
                    )
                elif dim_spec.grain not in self.VALID_TIME_GRAINS:
                    result.add_error(
                        'dimensions',
                        f"无效的时间粒度: {dim_spec.grain}，"
                        f"允许值: {', '.join(self.VALID_TIME_GRAINS)}",
                        'INVALID_TIME_GRAIN'
                    )

    def _validate_filters(
        self,
        filters: List[Dict[str, Any]],
        result: ValidationResult
    ) -> None:
        """验证过滤条件（递归处理树形结构）"""
        for f in filters:
            self._validate_filter_node(f, result, 'filters', depth=0)

    def _validate_filter_node(
        self,
        node: Dict[str, Any],
        result: ValidationResult,
        field_name: str,
        depth: int
    ) -> None:
        """验证过滤条件节点"""
        if depth > 10:
            result.add_error(
                field_name,
                '过滤条件嵌套层级过深（最大 10 层）',
                'MAX_DEPTH_EXCEEDED'
            )
            return

        # 处理 AND 节点
        if 'and' in node:
            for child in node['and']:
                self._validate_filter_node(child, result, field_name, depth + 1)
            return

        # 处理 OR 节点
        if 'or' in node:
            for child in node['or']:
                self._validate_filter_node(child, result, field_name, depth + 1)
            return

        # 叶子节点：维度过滤
        dim_name = node.get('dimension')
        if dim_name:
            self._validate_dimension_filter(node, result, field_name)

    def _validate_dimension_filter(
        self,
        node: Dict[str, Any],
        result: ValidationResult,
        field_name: str
    ) -> None:
        """验证维度过滤条件"""
        dim_name = node.get('dimension')
        operator = node.get('operator', '=').upper()
        value = node.get('value')

        # 检查维度是否存在
        if dim_name not in self._dimensions_index:
            result.add_error(
                field_name,
                f"过滤条件中使用了未知的维度: {dim_name}",
                'UNKNOWN_DIMENSION_IN_FILTER'
            )
            return

        dim = self._dimensions_index[dim_name]

        # 检查操作符
        if operator not in self.VALID_OPERATORS:
            result.add_error(
                field_name,
                f"无效的操作符: {operator}",
                'INVALID_OPERATOR'
            )

        # 检查操作符是否被允许
        if dim.allowed_operators and operator not in dim.allowed_operators:
            result.add_error(
                field_name,
                f"维度 '{dim_name}' 不允许使用操作符: {operator}",
                'OPERATOR_NOT_ALLOWED'
            )

        # 检查值是否在允许范围内
        if dim.allowed_values and value is not None:
            # 处理 IN 操作符的值列表
            values_to_check = value if isinstance(value, list) else [value]
            for v in values_to_check:
                if str(v) not in [str(av) for av in dim.allowed_values]:
                    result.add_warning(
                        f"维度 '{dim_name}' 的值 '{v}' 不在允许列表中"
                    )

    def _validate_having(
        self,
        having: List[Dict[str, Any]],
        result: ValidationResult
    ) -> None:
        """验证 HAVING 条件"""
        for h in having:
            self._validate_having_node(h, result, 'having', depth=0)

    def _validate_having_node(
        self,
        node: Dict[str, Any],
        result: ValidationResult,
        field_name: str,
        depth: int
    ) -> None:
        """验证 HAVING 条件节点"""
        if depth > 10:
            result.add_error(
                field_name,
                'HAVING 条件嵌套层级过深（最大 10 层）',
                'MAX_DEPTH_EXCEEDED'
            )
            return

        # 处理 AND 节点
        if 'and' in node:
            for child in node['and']:
                self._validate_having_node(child, result, field_name, depth + 1)
            return

        # 处理 OR 节点
        if 'or' in node:
            for child in node['or']:
                self._validate_having_node(child, result, field_name, depth + 1)
            return

        # 叶子节点：指标过滤
        metric_name = node.get('metric')
        if metric_name:
            self._validate_metric_filter(node, result, field_name)

    def _validate_metric_filter(
        self,
        node: Dict[str, Any],
        result: ValidationResult,
        field_name: str
    ) -> None:
        """验证指标过滤条件"""
        metric_name = node.get('metric')
        operator = node.get('operator', '=').upper()

        # 检查指标是否存在
        if metric_name not in self._metrics_index:
            result.add_error(
                field_name,
                f"HAVING 条件中使用了未知的指标: {metric_name}",
                'UNKNOWN_METRIC_IN_HAVING'
            )
            return

        # 检查操作符
        if operator not in self.VALID_OPERATORS:
            result.add_error(
                field_name,
                f"无效的操作符: {operator}",
                'INVALID_OPERATOR'
            )

    def _validate_order_by(
        self,
        order_by: List,
        result: ValidationResult
    ) -> None:
        """验证排序"""
        for o in order_by:
            field_name = o.field if hasattr(o, 'field') else o.get('field', '')

            # 检查是否是有效的指标或维度
            if field_name not in self._metrics_index and field_name not in self._dimensions_index:
                result.add_error(
                    'order_by',
                    f"排序字段 '{field_name}' 不是有效的指标或维度",
                    'INVALID_ORDER_FIELD'
                )

    def _validate_pagination(
        self,
        limit: Optional[int],
        offset: Optional[int],
        result: ValidationResult
    ) -> None:
        """验证分页"""
        if limit is not None:
            if not isinstance(limit, int) or limit < 0:
                result.add_error(
                    'limit',
                    'limit 必须是非负整数',
                    'INVALID_LIMIT'
                )
            elif limit > 10000:
                result.add_warning('limit 值过大，可能影响性能')

        if offset is not None:
            if not isinstance(offset, int) or offset < 0:
                result.add_error(
                    'offset',
                    'offset 必须是非负整数',
                    'INVALID_OFFSET'
                )

    def get_valid_metrics(self) -> List[str]:
        """获取所有有效的指标名称"""
        return list(self._metrics_index.keys())

    def get_valid_dimensions(self) -> List[str]:
        """获取所有有效的维度名称"""
        return list(self._dimensions_index.keys())

    def get_dimension_info(self, name: str) -> Optional[SemanticDimension]:
        """获取维度信息"""
        return self._dimensions_index.get(name)

    def get_metric_info(self, name: str) -> Optional[SemanticMetric]:
        """获取指标信息"""
        return self._metrics_index.get(name)
