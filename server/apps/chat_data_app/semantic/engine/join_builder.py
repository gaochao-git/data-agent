"""
JOIN 构建器

负责构建 SQL 的 FROM 和 JOIN 子句。
"""

from typing import List, Dict, Set, Optional, Any
from collections import defaultdict
import logging

from ..models import SemanticConfig, SemanticRelationship

logger = logging.getLogger(__name__)


class JoinBuilder:
    """
    JOIN 构建器

    根据语义配置和查询需求，自动构建 JOIN 子句。
    """

    def __init__(self, config: SemanticConfig):
        """
        初始化构建器

        Args:
            config: 语义配置
        """
        self.config = config
        # 预构建关系图（邻接表）
        self._relationship_graph = self._build_relationship_graph()
        # 表别名映射
        self._table_aliases: Dict[str, str] = {}

    def _build_relationship_graph(self) -> Dict[str, Set[str]]:
        """
        构建关系图（邻接表）

        Returns:
            表名到相邻表名集合的映射
        """
        graph = defaultdict(set)
        for rel in self.config.relationships:
            graph[rel.left_table].add(rel.right_table)
            graph[rel.right_table].add(rel.left_table)
        return dict(graph)

    def can_join(self, table1: str, table2: str) -> bool:
        """
        检查两个表是否可以直接 JOIN

        Args:
            table1: 表1
            table2: 表2

        Returns:
            是否可以 JOIN
        """
        if table1 == table2:
            return True
        return table2 in self._relationship_graph.get(table1, set())

    def find_join_path(self, from_table: str, to_table: str) -> Optional[List[str]]:
        """
        查找两个表之间的 JOIN 路径（BFS）

        Args:
            from_table: 起始表
            to_table: 目标表

        Returns:
            路径列表，如果不存在返回 None
        """
        if from_table == to_table:
            return [from_table]

        visited = {from_table}
        queue = [[from_table]]

        while queue:
            path = queue.pop(0)
            current = path[-1]

            neighbors = self._relationship_graph.get(current, set())
            for neighbor in neighbors:
                if neighbor in visited:
                    continue

                new_path = path + [neighbor]
                if neighbor == to_table:
                    return new_path

                visited.add(neighbor)
                queue.append(new_path)

        return None

    def build(
        self,
        main_table: str,
        required_tables: Set[str],
        table_aliases: Optional[Dict[str, str]] = None
    ) -> str:
        """
        构建 FROM + JOIN 子句

        Args:
            main_table: 主表名
            required_tables: 需要的表集合
            table_aliases: 表别名映射

        Returns:
            FROM + JOIN 子句
        """
        if table_aliases:
            self._table_aliases = table_aliases.copy()

        # 如果只有主表，直接返回
        if len(required_tables) <= 1:
            return f"FROM {main_table}"

        # 构建 JOIN 子句
        joined_tables = {main_table}
        join_clauses = []

        # 对于每个需要的表，找到 JOIN 路径
        tables_to_join = required_tables - {main_table}

        for table in tables_to_join:
            if table in joined_tables:
                continue

            # 找到从已加入的表到目标表的路径
            path = None
            for joined in joined_tables:
                path = self.find_join_path(joined, table)
                if path:
                    break

            if not path:
                logger.warning(f"无法找到表 {table} 的 JOIN 路径")
                continue

            # 沿路径添加 JOIN
            for i in range(len(path) - 1):
                left = path[i]
                right = path[i + 1]

                if right in joined_tables:
                    continue

                rel = self.config.find_relationship(left, right)
                if rel:
                    join_clause = self._build_join_clause(rel, left, right)
                    join_clauses.append(join_clause)
                    joined_tables.add(right)

        # 组装结果
        result = f"FROM {main_table}"

        if join_clauses:
            result += "\n" + "\n".join(join_clauses)

        return result

    def _build_join_clause(
        self,
        rel: SemanticRelationship,
        left_table: str,
        right_table: str
    ) -> str:
        """
        构建单个 JOIN 子句

        Args:
            rel: 关系定义
            left_table: 左表
            right_table: 右表

        Returns:
            JOIN 子句
        """
        # 确定 JOIN 方向
        reverse = rel.left_table != left_table
        if rel.left_table == left_table:
            join_table = rel.right_table
        else:
            join_table = rel.left_table

        # 构建条件（使用完整表名）
        if rel.condition:
            condition = rel.condition
        else:
            cond_pairs = []
            for cond in rel.join_conditions or []:
                left_col = (cond or {}).get("left_column")
                right_col = (cond or {}).get("right_column")
                if left_col and right_col:
                    cond_pairs.append((left_col, right_col))
            if not cond_pairs:
                raise ValueError(f"关系 {rel.left_table} -> {rel.right_table} 缺少 join_conditions")
            if reverse:
                cond_pairs = [(right_col, left_col) for left_col, right_col in cond_pairs]
            condition = " AND ".join([f"{left_table}.{left_col} = {join_table}.{right_col}" for left_col, right_col in cond_pairs])

        # 获取 JOIN 类型
        join_keyword = rel.get_join_keyword()

        return f"{join_keyword} {join_table} ON {condition}"

    def _get_alias(self, table_name: str) -> str:
        """
        获取表别名

        Args:
            table_name: 表名

        Returns:
            别名
        """
        if table_name in self._table_aliases:
            return self._table_aliases[table_name]

        # 生成默认别名（使用表名首字母或前几个字符）
        alias = table_name[0].lower()

        # 确保别名唯一
        used_aliases = set(self._table_aliases.values())
        if alias in used_aliases:
            for i in range(1, 100):
                new_alias = f"{alias}{i}"
                if new_alias not in used_aliases:
                    alias = new_alias
                    break

        self._table_aliases[table_name] = alias
        return alias

    def get_table_alias(self, table_name: str) -> str:
        """
        获取表别名（公开方法）

        Args:
            table_name: 表名

        Returns:
            别名
        """
        return self._get_alias(table_name)

    def get_all_aliases(self) -> Dict[str, str]:
        """获取所有表别名映射"""
        return self._table_aliases.copy()
