"""
输入参数验证器

验证用户输入参数的合法性（在 DSL 生成之前）。
"""

from typing import List, Dict, Tuple, Optional, Any
import logging

logger = logging.getLogger(__name__)


class InputValidator:
    """
    输入参数验证器

    验证原始输入参数，在 DSL 生成之前使用。
    与 SemanticValidator（DSL 验证）不同，本类验证的是原始输入格式。
    """

    def __init__(self, metrics_map: Dict[str, Dict], dimensions_map: Dict[str, Dict]):
        """
        初始化验证器

        Args:
            metrics_map: 指标名称到指标配置的映射
            dimensions_map: 维度名称到维度配置的映射
        """
        self.metrics_map = metrics_map
        self.dimensions_map = dimensions_map

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
                return filters, None

        # 平铺格式：逐个验证叶子节点
        validated_filters = []
        for f in filters:
            dim_name = f.get('dimension')
            if dim_name and dim_name in self.dimensions_map:
                validated_filters.append(f)
            elif dim_name:
                return [], f"错误：过滤条件中的维度 '{dim_name}' 不存在。可用维度：{', '.join(self.dimensions_map.keys())}"

        return validated_filters, None

    def _validate_tree_filter(self, filter_node: Dict) -> Optional[str]:
        """
        递归验证树形过滤条件

        Args:
            filter_node: 过滤节点（可以是 AND/OR 节点或叶子节点）

        Returns:
            错误消息，验证通过返回 None
        """
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

        # 叶子节点
        if 'dimension' in filter_node:
            dim_name = filter_node.get('dimension')
            if dim_name not in self.dimensions_map:
                return f"错误：过滤条件中的维度 '{dim_name}' 不存在。可用维度：{', '.join(self.dimensions_map.keys())}"
            return None

        return "错误：无效的过滤条件格式，必须是叶子节点或 and/or 组合节点"

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
            metric_name = h.get('metric')
            if metric_name and metric_name in self.metrics_map:
                validated_having.append(h)
            elif metric_name:
                return [], f"错误：HAVING 条件中的指标 '{metric_name}' 不存在。可用指标：{', '.join(self.metrics_map.keys())}"

        return validated_having, None

    def validate_order_by(
        self,
        order_by: List[Dict],
        metrics: List[str],
        dimensions: List[str]
    ) -> Tuple[List[Dict], Optional[str]]:
        """
        验证 ORDER BY 排序条件

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
                return [], f"错误：排序字段 '{field}' 不在查询的指标或维度中。可用字段：{', '.join(all_fields)}"

            # 验证排序方向
            if direction not in ['ASC', 'DESC']:
                return [], f"错误：排序方向 '{direction}' 无效，只能是 'ASC' 或 'DESC'"

            validated_order_by.append({
                'field': field,
                'direction': direction
            })

        return validated_order_by, None

    def validate_limit(self, limit: Optional[int], max_limit: int = 10000) -> Tuple[Optional[int], Optional[str]]:
        """
        验证 LIMIT 限制条数

        Args:
            limit: 限制条数
            max_limit: 最大限制（默认 10000）

        Returns:
            (validated_limit, error_msg): 验证后的限制条数和错误消息
        """
        if limit is None:
            return None, None

        # 验证是否为正整数
        if not isinstance(limit, int) or limit <= 0:
            return None, f"错误：LIMIT 必须为正整数，当前值：{limit}"

        # 防止过大的 LIMIT
        if limit > max_limit:
            return None, f"错误：LIMIT 不能超过 {max_limit}，当前值：{limit}"

        return limit, None


# 兼容别名
SemanticValidator = InputValidator
