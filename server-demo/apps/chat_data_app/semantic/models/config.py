"""
语义层配置定义

SemanticConfig 是完整的语义配置，包含指标、维度、关系的定义。
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .metric import SemanticMetric
from .dimension import SemanticDimension
from .relationship import SemanticRelationship


class SemanticConfig(BaseModel):
    """
    完整的语义配置

    语义配置定义了一个分析场景的完整语义模型。

    Attributes:
        id: 配置唯一标识
        name: 配置名称
        description: 配置描述
        enabled: 是否启用
        version: 版本号
        metrics: 指标列表
        dimensions: 维度列表
        relationships: 关系列表
        create_time: 创建时间
        update_time: 更新时间
    """

    # 标识
    id: str = Field(..., description="配置ID")
    name: str = Field(..., description="配置名称")
    description: Optional[str] = Field(None, description="配置描述")
    enabled: bool = Field(True, description="是否启用")
    version: str = Field("1.0", description="版本号")

    # 核心定义
    metrics: List[SemanticMetric] = Field(default_factory=list, description="指标列表")
    dimensions: List[SemanticDimension] = Field(default_factory=list, description="维度列表")
    relationships: List[SemanticRelationship] = Field(default_factory=list, description="关系列表")

    # 元数据
    create_time: datetime = Field(default_factory=datetime.now, description="创建时间")
    update_time: datetime = Field(default_factory=datetime.now, description="更新时间")

    # 内部索引（运行时构建）
    _metrics_index: Dict[str, SemanticMetric] = {}
    _dimensions_index: Dict[str, SemanticDimension] = {}
    _tables: set = set()

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @model_validator(mode='after')
    def build_indexes(self):
        """构建内部索引"""
        # 指标索引
        self._metrics_index = {m.name: m for m in self.metrics}
        # 维度索引
        self._dimensions_index = {d.name: d for d in self.dimensions}
        # 表集合
        tables = set()
        for m in self.metrics:
            tables.add(m.table)
        for d in self.dimensions:
            tables.add(d.table)
        self._tables = tables
        return self

    def get_metric(self, name: str) -> Optional[SemanticMetric]:
        """根据名称获取指标"""
        return self._metrics_index.get(name)

    def get_dimension(self, name: str) -> Optional[SemanticDimension]:
        """根据名称获取维度"""
        return self._dimensions_index.get(name)

    def get_tables(self) -> set:
        """获取所有涉及的表"""
        return self._tables

    def get_relationships_for_table(self, table_name: str) -> List[SemanticRelationship]:
        """获取与指定表相关的所有关系"""
        return [r for r in self.relationships if r.involves_table(table_name)]

    def find_relationship(self, table1: str, table2: str) -> Optional[SemanticRelationship]:
        """
        查找两个表之间的关系

        Args:
            table1: 表1
            table2: 表2

        Returns:
            关系对象，如果不存在返回 None
        """
        for rel in self.relationships:
            if rel.involves_table(table1) and rel.involves_table(table2):
                return rel
        return None

    def can_join(self, table1: str, table2: str) -> bool:
        """检查两个表是否可以 JOIN"""
        return self.find_relationship(table1, table2) is not None

    def get_dimension_tables(self, dimension_names: List[str]) -> set:
        """获取指定维度所属的表"""
        tables = set()
        for name in dimension_names:
            dim = self.get_dimension(name)
            if dim:
                tables.add(dim.table)
        return tables

    def get_metric_tables(self, metric_names: List[str]) -> set:
        """获取指定指标所属的表"""
        tables = set()
        for name in metric_names:
            metric = self.get_metric(name)
            if metric:
                tables.add(metric.table)
        return tables

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SemanticConfig":
        """
        从字典创建配置

        Args:
            data: 配置字典

        Returns:
            SemanticConfig 实例
        """
        return cls.model_validate(data)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return self.model_dump(exclude={'_metrics_index', '_dimensions_index', '_tables'})
