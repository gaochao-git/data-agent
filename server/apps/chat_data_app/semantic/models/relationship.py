"""
语义层关系定义

关系（Relationship）定义表之间的关联方式。
"""

from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, ConfigDict, Field


class RelationshipType(str, Enum):
    """关系类型枚举"""
    ONE_TO_ONE = "one_to_one"      # 1:1
    ONE_TO_MANY = "one_to_many"    # 1:N
    MANY_TO_ONE = "many_to_one"    # N:1


class JoinType(str, Enum):
    """JOIN 类型枚举"""
    INNER = "inner"
    LEFT = "left"
    RIGHT = "right"


class SemanticRelationship(BaseModel):
    """
    语义层关系定义

    关系定义了两个表之间如何进行 JOIN 操作。

    Attributes:
        name: 关系名称，便于引用
        left_table: 左表（主表）名
        right_table: 右表（关联表）名
        left_column: 左表关联字段
        right_column: 右表关联字段
        relationship_type: 关系类型（1:1, 1:N, N:1）
        join_type: JOIN 类型（INNER, LEFT, RIGHT）
        alias: 关联表别名
        condition: 自定义 JOIN 条件（支持复合键）
    """

    # 可选标识
    name: Optional[str] = Field(None, description="关系名称")

    # 必填字段
    left_table: str = Field(..., description="左表名")
    right_table: str = Field(..., description="右表名")
    left_column: str = Field(..., description="左表关联字段")
    right_column: str = Field(..., description="右表关联字段")
    join_conditions: List[dict] = Field(
        default_factory=list,
        description="多字段关联条件列表，如[{left_column:'id', right_column:'id'}]"
    )

    # 关系配置
    relationship_type: RelationshipType = Field(
        RelationshipType.MANY_TO_ONE,
        description="关系类型"
    )
    join_type: JoinType = Field(JoinType.LEFT, description="JOIN类型")

    # 扩展
    alias: Optional[str] = Field(None, description="关联表别名")
    condition: Optional[str] = Field(None, description="自定义JOIN条件")

    model_config = ConfigDict(use_enum_values=True)

    def get_join_condition(self, left_alias: Optional[str] = None,
                          right_alias: Optional[str] = None) -> str:
        """
        获取 JOIN 条件

        Args:
            left_alias: 左表别名
            right_alias: 右表别名

        Returns:
            JOIN 条件字符串
        """
        if self.condition:
            return self.condition

        left_ref_table = left_alias or self.left_table
        right_ref_table = right_alias or self.alias or self.right_table
        conds = self.join_conditions
        pairs = []
        for cond in conds:
            left_col = (cond or {}).get("left_column")
            right_col = (cond or {}).get("right_column")
            if left_col and right_col:
                pairs.append((left_col, right_col))
        if not pairs:
            raise ValueError("join_conditions 不能为空")
        return " AND ".join([f"{left_ref_table}.{left_col} = {right_ref_table}.{right_col}" for left_col, right_col in pairs])

    def get_join_keyword(self) -> str:
        """获取 JOIN 关键字"""
        keywords = {
            JoinType.INNER: "INNER JOIN",
            JoinType.LEFT: "LEFT JOIN",
            JoinType.RIGHT: "RIGHT JOIN",
        }
        return keywords.get(self.join_type, "LEFT JOIN")

    def involves_table(self, table_name: str) -> bool:
        """检查关系是否涉及指定表"""
        return table_name in (self.left_table, self.right_table)

    def get_other_table(self, table_name: str) -> Optional[str]:
        """获取关系中的另一个表"""
        if table_name == self.left_table:
            return self.right_table
        if table_name == self.right_table:
            return self.left_table
        return None
