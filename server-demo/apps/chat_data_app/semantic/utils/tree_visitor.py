"""
树形结构访问器

用于处理树形过滤条件（AND/OR 嵌套结构）。
"""

from typing import Callable, Any, Dict, List, Optional, Set
import logging

logger = logging.getLogger(__name__)


class TreeDepthExceededError(Exception):
    """树形结构深度超限错误"""
    pass


class TreeVisitor:
    """
    通用的树形结构访问器

    支持处理 AND/OR 嵌套的树形结构，如：
    {
        "or": [
            {"dimension": "type", "operator": "=", "value": "A"},
            {"and": [
                {"dimension": "status", "operator": "=", "value": "active"},
                {"dimension": "region", "operator": "=", "value": "CN"}
            ]}
        ]
    }
    """

    def __init__(self, max_depth: int = 10):
        """
        初始化访问器

        Args:
            max_depth: 最大允许深度
        """
        self.max_depth = max_depth

    def visit(
        self,
        node: Dict[str, Any],
        leaf_fn: Callable[[Dict[str, Any]], str],
        depth: int = 0
    ) -> str:
        """
        访问树形节点，返回 SQL 条件字符串

        Args:
            node: 当前节点
            leaf_fn: 叶子节点处理函数
            depth: 当前深度

        Returns:
            SQL 条件字符串

        Raises:
            TreeDepthExceededError: 深度超限
        """
        if depth > self.max_depth:
            raise TreeDepthExceededError(
                f"树形结构嵌套层级过深: {depth}，最大允许: {self.max_depth}"
            )

        # AND 节点
        if 'and' in node:
            children = node['and']
            if not isinstance(children, list):
                raise ValueError(f"'and' 节点的值必须是列表，得到: {type(children)}")
            if not children:
                return ""
            child_results = [
                self.visit(child, leaf_fn, depth + 1)
                for child in children
            ]
            # 过滤空结果
            child_results = [r for r in child_results if r]
            if not child_results:
                return ""
            if len(child_results) == 1:
                return child_results[0]
            return f"({' AND '.join(child_results)})"

        # OR 节点
        if 'or' in node:
            children = node['or']
            if not isinstance(children, list):
                raise ValueError(f"'or' 节点的值必须是列表，得到: {type(children)}")
            if not children:
                return ""
            child_results = [
                self.visit(child, leaf_fn, depth + 1)
                for child in children
            ]
            # 过滤空结果
            child_results = [r for r in child_results if r]
            if not child_results:
                return ""
            if len(child_results) == 1:
                return child_results[0]
            return f"({' OR '.join(child_results)})"

        # 叶子节点
        return leaf_fn(node)

    def visit_with_context(
        self,
        node: Dict[str, Any],
        leaf_fn: Callable[[Dict[str, Any], Set[str]], str],
        context: Optional[Set[str]] = None,
        depth: int = 0
    ) -> str:
        """
        带上下文的访问（用于收集使用到的表等信息）

        Args:
            node: 当前节点
            leaf_fn: 叶子节点处理函数，接收节点和上下文
            context: 上下文集合（如已使用的表）
            depth: 当前深度

        Returns:
            SQL 条件字符串
        """
        if context is None:
            context = set()

        if depth > self.max_depth:
            raise TreeDepthExceededError(
                f"树形结构嵌套层级过深: {depth}，最大允许: {self.max_depth}"
            )

        # AND 节点
        if 'and' in node:
            children = node['and']
            if not isinstance(children, list) or not children:
                return ""
            child_results = [
                self.visit_with_context(child, leaf_fn, context, depth + 1)
                for child in children
            ]
            child_results = [r for r in child_results if r]
            if not child_results:
                return ""
            if len(child_results) == 1:
                return child_results[0]
            return f"({' AND '.join(child_results)})"

        # OR 节点
        if 'or' in node:
            children = node['or']
            if not isinstance(children, list) or not children:
                return ""
            child_results = [
                self.visit_with_context(child, leaf_fn, context, depth + 1)
                for child in children
            ]
            child_results = [r for r in child_results if r]
            if not child_results:
                return ""
            if len(child_results) == 1:
                return child_results[0]
            return f"({' OR '.join(child_results)})"

        # 叶子节点
        return leaf_fn(node, context)

    def collect_leaves(
        self,
        node: Dict[str, Any],
        depth: int = 0
    ) -> List[Dict[str, Any]]:
        """
        收集所有叶子节点

        Args:
            node: 当前节点
            depth: 当前深度

        Returns:
            叶子节点列表
        """
        if depth > self.max_depth:
            raise TreeDepthExceededError(
                f"树形结构嵌套层级过深: {depth}，最大允许: {self.max_depth}"
            )

        leaves = []

        # AND 节点
        if 'and' in node:
            for child in node['and']:
                leaves.extend(self.collect_leaves(child, depth + 1))
            return leaves

        # OR 节点
        if 'or' in node:
            for child in node['or']:
                leaves.extend(self.collect_leaves(child, depth + 1))
            return leaves

        # 叶子节点
        leaves.append(node)
        return leaves


def is_tree_node(node: Any) -> bool:
    """
    判断是否是树形节点

    Args:
        node: 要判断的值

    Returns:
        是否是树形节点（包含 and/or 键）
    """
    if not isinstance(node, dict):
        return False
    return 'and' in node or 'or' in node


def is_leaf_node(node: Any) -> bool:
    """
    判断是否是叶子节点

    Args:
        node: 要判断的值

    Returns:
        是否是叶子节点（不包含 and/or 键的字典）
    """
    if not isinstance(node, dict):
        return False
    return 'and' not in node and 'or' not in node


def flatten_filters(filters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    扁平化过滤条件列表（提取所有叶子节点）

    Args:
        filters: 过滤条件列表（可能包含树形结构）

    Returns:
        扁平化的叶子节点列表
    """
    visitor = TreeVisitor()
    leaves = []
    for f in filters:
        if is_tree_node(f):
            leaves.extend(visitor.collect_leaves(f))
        else:
            leaves.append(f)
    return leaves
