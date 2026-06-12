"""
语义层指标定义

指标（Metric）是对数据的聚合计算，如 COUNT、SUM、AVG 等。
"""

from enum import Enum
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class AggregationType(str, Enum):
    """聚合类型枚举"""
    COUNT = "COUNT"
    COUNT_DISTINCT = "COUNT_DISTINCT"
    SUM = "SUM"
    AVG = "AVG"
    MAX = "MAX"
    MIN = "MIN"


class DataType(str, Enum):
    """数据类型枚举"""
    INT = "INT"
    BIGINT = "BIGINT"
    DECIMAL = "DECIMAL"
    DOUBLE = "DOUBLE"
    STRING = "STRING"
    DATE = "DATE"
    TIMESTAMP = "TIMESTAMP"


class SemanticMetric(BaseModel):
    """
    语义层指标定义

    指标代表一个可度量的业务概念，如"销售额"、"订单数"等。

    Attributes:
        name: 指标名称，如"销售额"
        expression: SQL聚合表达式，如"SUM(amount)"
        table: 所属表名
        description: 指标描述
        data_type: 返回值数据类型
        unit: 单位，如"个"、"万元"
        precision: 小数位数（用于 DECIMAL 类型）
        aggregation_type: 聚合类型
        filter: 默认过滤条件，如"status='active'"
    """

    # 必填字段
    name: str = Field(..., description="指标名称，如'销售额'")
    expression: str = Field(..., description="SQL聚合表达式，如'SUM(amount)'")
    table: str = Field(..., description="所属表名")

    # 元数据（可选）
    description: Optional[str] = Field(None, description="指标描述")
    data_type: DataType = Field(DataType.DECIMAL, description="返回值类型")
    unit: Optional[str] = Field(None, description="单位，如'个'、'万元'、'GB'")
    precision: Optional[int] = Field(None, description="小数位数")
    aggregation_type: Optional[AggregationType] = Field(None, description="聚合类型")

    # 过滤配置
    filter: Optional[str] = Field(None, description="默认过滤条件")

    # 对比指标元数据
    compare_type: Optional[str] = Field(None, description="预计算同比/环比指标类型：mom 或 yoy")
    compare_mode: Optional[str] = Field(None, description="预计算同比/环比指标形态：trend 或 value")
    base_metric: Optional[str] = Field(None, description="预计算同比/环比指标对应的基础指标名称")

    model_config = ConfigDict(use_enum_values=True)

    def get_alias(self) -> str:
        """获取指标别名（用于 SQL AS 子句）"""
        return self.name

    def has_filter(self) -> bool:
        """是否有默认过滤条件"""
        return bool(self.filter)
