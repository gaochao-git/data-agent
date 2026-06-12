"""
DSL 定义

定义语义查询的结构化表示。
"""

from typing import List, Dict, Any, Optional, Union
from dataclasses import dataclass, field
from enum import Enum


class QueryMode(str, Enum):
    """查询模式"""
    AGGREGATE = "aggregate"  # 聚合查询
    DETAIL = "detail"        # 明细查询


@dataclass
class DimensionSpec:
    """维度规格（支持时间粒度）"""
    name: str
    grain: Optional[str] = None  # 时间粒度：year/quarter/month/week/day/hour/minute

    @classmethod
    def from_dict(cls, data: Union[str, Dict]) -> "DimensionSpec":
        """从字典或字符串创建"""
        if isinstance(data, str):
            return cls(name=data)
        return cls(
            name=data.get('name', data.get('dimension', '')),
            grain=data.get('grain', data.get('time_grain'))
        )

    def to_dict(self) -> Dict:
        """转换为字典"""
        result = {'name': self.name}
        if self.grain:
            result['grain'] = self.grain
        return result


@dataclass
class FilterCondition:
    """过滤条件"""
    dimension: Optional[str] = None
    metric: Optional[str] = None
    operator: str = "="
    value: Any = None

    @classmethod
    def from_dict(cls, data: Dict) -> "FilterCondition":
        """从字典创建"""
        return cls(
            dimension=data.get('dimension'),
            metric=data.get('metric'),
            operator=data.get('operator', '='),
            value=data.get('value')
        )


@dataclass
class OrderBySpec:
    """排序规格"""
    field: str
    direction: str = "ASC"  # ASC or DESC

    @classmethod
    def from_dict(cls, data: Union[str, Dict]) -> "OrderBySpec":
        """从字典或字符串创建"""
        if isinstance(data, str):
            return cls(field=data)
        return cls(
            field=data.get('field', data.get('metric', data.get('dimension', ''))),
            direction=data.get('direction', data.get('order', 'ASC')).upper()
        )

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {'field': self.field, 'direction': self.direction}


@dataclass
class SemanticQuery:
    """
    语义查询 DSL

    表示一个结构化的语义查询请求。

    Examples:
        # 简单聚合查询
        query = SemanticQuery(
            metrics=["销售额", "订单数"],
            dimensions=["产品类别"]
        )

        # 带时间粒度的查询
        query = SemanticQuery(
            metrics=["销售额"],
            dimensions=[DimensionSpec("日期", grain="month")]
        )

        # 带过滤条件的查询
        query = SemanticQuery(
            metrics=["销售额"],
            filters=[{"dimension": "地区", "operator": "=", "value": "北京"}]
        )
    """
    # 必填：指标列表
    metrics: List[str] = field(default_factory=list)

    # 可选：维度列表（支持时间粒度）
    dimensions: List[Union[str, DimensionSpec, Dict]] = field(default_factory=list)

    # 可选：WHERE 条件（支持树形 AND/OR）
    filters: List[Dict[str, Any]] = field(default_factory=list)

    # 可选：HAVING 条件
    having: List[Dict[str, Any]] = field(default_factory=list)

    # 可选：排序
    order_by: List[Union[str, OrderBySpec, Dict]] = field(default_factory=list)

    # 可选：分页
    limit: Optional[int] = None
    offset: Optional[int] = None

    # 查询模式
    mode: QueryMode = QueryMode.AGGREGATE

    def __post_init__(self):
        """初始化后处理"""
        # 规范化维度
        self.dimensions = [
            DimensionSpec.from_dict(d) if not isinstance(d, DimensionSpec) else d
            for d in self.dimensions
        ]

        # 规范化排序
        self.order_by = [
            OrderBySpec.from_dict(o) if not isinstance(o, OrderBySpec) else o
            for o in self.order_by
        ]

    def get_dimension_names(self) -> List[str]:
        """获取所有维度名称"""
        return [d.name if isinstance(d, DimensionSpec) else d for d in self.dimensions]

    def get_time_dimensions(self) -> List[DimensionSpec]:
        """获取带时间粒度的维度"""
        return [d for d in self.dimensions if isinstance(d, DimensionSpec) and d.grain]

    def has_filters(self) -> bool:
        """是否有过滤条件"""
        return bool(self.filters)

    def has_having(self) -> bool:
        """是否有 HAVING 条件"""
        return bool(self.having)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        result = {
            'metrics': self.metrics,
            'dimensions': [
                d.to_dict() if isinstance(d, DimensionSpec) else d
                for d in self.dimensions
            ],
            'mode': self.mode.value
        }

        if self.filters:
            result['filters'] = self.filters
        if self.having:
            result['having'] = self.having
        if self.order_by:
            result['order_by'] = [
                o.to_dict() if isinstance(o, OrderBySpec) else o
                for o in self.order_by
            ]
        if self.limit is not None:
            result['limit'] = self.limit
        if self.offset is not None:
            result['offset'] = self.offset

        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SemanticQuery":
        """从字典创建"""
        mode = data.get('mode', 'aggregate')
        if isinstance(mode, str):
            mode = QueryMode(mode)

        return cls(
            metrics=data.get('metrics', []),
            dimensions=data.get('dimensions', []),
            filters=data.get('filters', []),
            having=data.get('having', []),
            order_by=data.get('order_by', []),
            limit=data.get('limit'),
            offset=data.get('offset'),
            mode=mode
        )


@dataclass
class MatchResult:
    """匹配结果"""
    success: bool
    dsl: Optional[SemanticQuery] = None
    error: Optional[str] = None
    confidence: float = 0.0
    raw_response: Optional[Dict] = None

    def to_dict(self) -> Dict:
        """转换为字典"""
        result = {
            'success': self.success,
            'confidence': self.confidence
        }
        if self.dsl:
            result['dsl'] = self.dsl.to_dict()
        if self.error:
            result['error'] = self.error
        return result


@dataclass
class TranslateResult:
    """翻译结果"""
    success: bool
    sql: Optional[str] = None
    error: Optional[str] = None
    explanation: Optional[str] = None
    tables_used: List[str] = field(default_factory=list)
    summary: Optional[Dict[str, Any]] = None  # 结构化摘要（用于前端显示）

    def to_dict(self) -> Dict:
        """转换为字典"""
        result = {'success': self.success}
        if self.sql:
            result['sql'] = self.sql
        if self.error:
            result['error'] = self.error
        if self.explanation:
            result['explanation'] = self.explanation
        if self.tables_used:
            result['tables_used'] = self.tables_used
        if self.summary:
            result['summary'] = self.summary
        return result


@dataclass
class QueryResult:
    """查询结果"""
    success: bool
    sql: Optional[str] = None
    dsl: Optional[SemanticQuery] = None
    error: Optional[str] = None
    explanation: Optional[str] = None
    confidence: float = 0.0
    tables_used: List[str] = field(default_factory=list)
    summary: Optional[Dict[str, Any]] = None  # 结构化摘要（用于前端显示）

    def to_dict(self) -> Dict:
        """转换为字典"""
        result = {
            'success': self.success,
            'confidence': self.confidence
        }
        if self.sql:
            result['sql'] = self.sql
        if self.dsl:
            result['dsl'] = self.dsl.to_dict()
        if self.error:
            result['error'] = self.error
        if self.explanation:
            result['explanation'] = self.explanation
        if self.tables_used:
            result['tables_used'] = self.tables_used
        if self.summary:
            result['summary'] = self.summary
        return result
