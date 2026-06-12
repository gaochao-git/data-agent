"""
DSL 生成器

将匹配结果转换为结构化查询。
"""

from typing import Dict, List, Optional
from dataclasses import dataclass, field
import logging

from .dsl import SemanticQuery

logger = logging.getLogger(__name__)


@dataclass
class LegacyMatchResult:
    """
    语义匹配结果（兼容旧版本格式）

    用于 SemanticSQLGenerator 等需要旧格式的场景。
    """
    metrics: List[Dict] = field(default_factory=list)
    dimensions: List[Dict] = field(default_factory=list)
    filters: List[Dict] = field(default_factory=list)
    confidence: float = 0.0
    llm_result: Dict = field(default_factory=dict)


class DSLGenerator:
    """
    DSL 生成器

    将匹配结果转换为结构化查询（SemanticQuery）。
    """

    def generate(
        self,
        match_result: LegacyMatchResult,
        llm_result: Dict
    ) -> Optional[SemanticQuery]:
        """
        生成语义查询 DSL

        Args:
            match_result: 语义匹配结果
            llm_result: LLM 返回的完整结果（包含 order_by, limit 等）

        Returns:
            SemanticQuery: 语义查询对象
        """
        if not match_result.metrics:
            return None

        # 提取指标名称
        metric_names = [m['name'] for m in match_result.metrics]

        # 提取维度信息
        dimension_items = []
        for d in match_result.dimensions:
            if isinstance(d, str):
                dimension_items.append(d)
            elif isinstance(d, dict):
                time_grain = d.get('time_grain', d.get('grain'))
                if time_grain:
                    dimension_items.append({
                        "name": d['name'],
                        "time_grain": time_grain
                    })
                else:
                    dimension_items.append(d.get('name', d))

        # 过滤条件
        filters = match_result.filters

        # 从 LLM 结果中提取 HAVING 条件
        having = llm_result.get('having', [])

        # 从 LLM 结果中提取排序
        order_by = llm_result.get('order_by', [])
        if isinstance(order_by, dict):
            order_by = [order_by] if order_by else []

        # 从 LLM 结果中提取 limit 和 offset
        limit = llm_result.get('limit')
        offset = llm_result.get('offset')

        return SemanticQuery(
            metrics=metric_names,
            dimensions=dimension_items,
            filters=filters,
            having=having,
            order_by=order_by,
            limit=limit,
            offset=offset
        )

    def generate_from_dict(self, dsl_dict: Dict) -> Optional[SemanticQuery]:
        """
        从字典直接生成 DSL

        Args:
            dsl_dict: DSL 字典

        Returns:
            SemanticQuery 对象
        """
        try:
            return SemanticQuery.from_dict(dsl_dict)
        except Exception as e:
            logger.error(f"DSL 解析失败: {e}")
            return None
