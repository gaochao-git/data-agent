"""
语义层维度定义

维度（Dimension）是用于分组和过滤的字段，如"地区"、"日期"等。
"""

from enum import Enum
from typing import Optional, List, Dict
from pydantic import BaseModel, ConfigDict, Field


class DimensionType(str, Enum):
    """维度类型枚举"""
    CATEGORICAL = "categorical"  # 分类维度，如"地区"、"产品类型"
    TIME = "time"                # 时间维度，如"日期"、"创建时间"
    NUMBER = "number"            # 数值维度，如"年龄段"、"价格区间"


class TimeGrain(str, Enum):
    """时间粒度枚举"""
    YEAR = "year"
    QUARTER = "quarter"
    MONTH = "month"
    WEEK = "week"
    DAY = "day"
    HOUR = "hour"
    MINUTE = "minute"


class SemanticDimension(BaseModel):
    """
    语义层维度定义

    维度用于对数据进行分组或过滤，是分析的"切面"。

    Attributes:
        name: 维度名称，如"数据库类型"
        column: 对应的数据库字段名
        table: 所属表名
        type: 维度类型（分类/时间/数值）
        description: 维度描述
        expression: SQL表达式，支持 CASE WHEN 等复杂计算
        mapping: 同义词映射，如 {"mysql": "MySQL", "pg": "PostgreSQL"}
        allowed_operators: 允许的过滤操作符
        allowed_values: 允许的值列表（用于枚举校验）
        default_time_grain: 时间维度的默认粒度
        hierarchy: 维度层级，如 ["国家", "省份", "城市"]
    """

    # 必填字段
    name: str = Field(..., description="维度名称")
    column: str = Field(..., description="对应的字段名")
    table: str = Field(..., description="所属表名")
    type: DimensionType = Field(..., description="维度类型")

    # 元数据（可选）
    description: Optional[str] = Field(None, description="维度描述")
    expression: Optional[str] = Field(None, description="SQL表达式，支持CASE WHEN等")

    # 过滤增强
    mapping: Optional[Dict[str, str]] = Field(None, description="同义词映射")
    allowed_operators: Optional[List[str]] = Field(None, description="允许的操作符")
    allowed_values: Optional[List[str]] = Field(None, description="允许的值列表")

    # 时间维度特有
    default_time_grain: Optional[TimeGrain] = Field(None, description="默认时间粒度")

    # 层级支持
    hierarchy: Optional[List[str]] = Field(None, description="维度层级")

    model_config = ConfigDict(use_enum_values=True)

    def is_time_dimension(self) -> bool:
        """是否是时间维度"""
        return self.type == DimensionType.TIME

    def get_column_ref(self, alias: Optional[str] = None) -> str:
        """
        获取列引用

        Args:
            alias: 表别名

        Returns:
            列引用字符串，如 "t.column" 或 "column"
        """
        if self.expression:
            return self.expression
        prefix = f"{alias}." if alias else f"{self.table}."
        return f"{prefix}{self.column}"

    def normalize_value(self, value: str) -> str:
        """
        规范化值（应用同义词映射）

        Args:
            value: 原始值

        Returns:
            规范化后的值
        """
        if self.mapping and value.lower() in {k.lower(): k for k in self.mapping}:
            # 不区分大小写查找
            for k, v in self.mapping.items():
                if k.lower() == value.lower():
                    return v
        return value

    def validate_value(self, value: str) -> bool:
        """
        验证值是否在允许列表中

        Args:
            value: 要验证的值

        Returns:
            是否有效
        """
        if not self.allowed_values:
            return True
        normalized = self.normalize_value(value)
        return normalized in self.allowed_values
