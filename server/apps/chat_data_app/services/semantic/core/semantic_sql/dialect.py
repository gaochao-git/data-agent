"""
SQL 方言适配

支持不同数据库的 SQL 语法差异。
"""

from abc import ABC, abstractmethod
from typing import List, Dict
import logging

logger = logging.getLogger(__name__)


class SQLDialect(ABC):
    """
    SQL 方言基类

    定义所有需要适配的 SQL 操作。
    """

    name: str = "base"

    # ========== 标识符处理 ==========

    @abstractmethod
    def quote_identifier(self, name: str) -> str:
        """
        标识符引用（表名、列名）

        Args:
            name: 标识符名称

        Returns:
            引用后的标识符
        """
        pass

    # ========== 日期/时间函数 ==========

    @abstractmethod
    def format_date(self, column: str, grain: str) -> str:
        """
        按粒度格式化日期

        Args:
            column: 列引用
            grain: 时间粒度 (year/quarter/month/week/day/hour)

        Returns:
            格式化后的 SQL 表达式
        """
        pass

    @abstractmethod
    def current_date(self) -> str:
        """当前日期函数"""
        pass

    @abstractmethod
    def current_timestamp(self) -> str:
        """当前时间戳函数"""
        pass

    @abstractmethod
    def date_add(self, column: str, interval: int, unit: str) -> str:
        """
        日期加法

        Args:
            column: 列引用
            interval: 间隔数
            unit: 单位 (day/month/year)

        Returns:
            SQL 表达式
        """
        pass

    @abstractmethod
    def date_sub(self, column: str, interval: int, unit: str) -> str:
        """日期减法"""
        pass

    @abstractmethod
    def date_diff(self, start: str, end: str) -> str:
        """日期差值（天数）"""
        pass

    # ========== 字符串函数 ==========

    @abstractmethod
    def concat(self, *args: str) -> str:
        """字符串连接"""
        pass

    @abstractmethod
    def substring(self, column: str, start: int, length: int) -> str:
        """字符串截取"""
        pass

    # ========== 聚合函数 ==========

    @abstractmethod
    def count_distinct(self, column: str) -> str:
        """去重计数"""
        pass

    # ========== 分页 ==========

    @abstractmethod
    def limit_offset(self, limit: int, offset: int = 0) -> str:
        """分页语法"""
        pass

    # ========== NULL 处理 ==========

    @abstractmethod
    def coalesce(self, *args: str) -> str:
        """空值处理（返回第一个非空值）"""
        pass

    @abstractmethod
    def ifnull(self, column: str, default: str) -> str:
        """空值替换"""
        pass


class MySQLDialect(SQLDialect):
    """MySQL 方言实现"""

    name = "mysql"

    def quote_identifier(self, name: str) -> str:
        return f"`{name}`"

    def format_date(self, column: str, grain: str) -> str:
        formats = {
            "year": f"DATE_FORMAT({column}, '%Y')",
            "quarter": f"CONCAT(YEAR({column}), '-Q', QUARTER({column}))",
            "month": f"DATE_FORMAT({column}, '%Y-%m')",
            "week": f"YEARWEEK({column})",
            "day": f"DATE({column})",
            "hour": f"DATE_FORMAT({column}, '%Y-%m-%d %H:00')",
            "minute": f"DATE_FORMAT({column}, '%Y-%m-%d %H:%i')",
        }
        return formats.get(grain, column)

    def current_date(self) -> str:
        return "CURRENT_DATE()"

    def current_timestamp(self) -> str:
        return "NOW()"

    def date_add(self, column: str, interval: int, unit: str) -> str:
        return f"DATE_ADD({column}, INTERVAL {interval} {unit.upper()})"

    def date_sub(self, column: str, interval: int, unit: str) -> str:
        return f"DATE_SUB({column}, INTERVAL {interval} {unit.upper()})"

    def date_diff(self, start: str, end: str) -> str:
        return f"DATEDIFF({end}, {start})"

    def concat(self, *args: str) -> str:
        return f"CONCAT({', '.join(args)})"

    def substring(self, column: str, start: int, length: int) -> str:
        return f"SUBSTRING({column}, {start}, {length})"

    def count_distinct(self, column: str) -> str:
        return f"COUNT(DISTINCT {column})"

    def limit_offset(self, limit: int, offset: int = 0) -> str:
        if offset > 0:
            return f"LIMIT {offset}, {limit}"
        return f"LIMIT {limit}"

    def coalesce(self, *args: str) -> str:
        return f"COALESCE({', '.join(args)})"

    def ifnull(self, column: str, default: str) -> str:
        return f"IFNULL({column}, {default})"


class HiveDialect(SQLDialect):
    """Hive/Spark SQL 方言实现"""

    name = "hive"

    def quote_identifier(self, name: str) -> str:
        return f"`{name}`"

    def format_date(self, column: str, grain: str) -> str:
        formats = {
            "year": f"DATE_FORMAT({column}, 'yyyy')",
            "quarter": f"CONCAT(YEAR({column}), '-Q', QUARTER({column}))",
            "month": f"DATE_FORMAT({column}, 'yyyy-MM')",
            "week": f"CONCAT(CAST(YEAR({column}) AS STRING), '-W', LPAD(CAST(WEEKOFYEAR({column}) AS STRING), 2, '0'))",
            "day": f"TO_DATE({column})",
            "hour": f"DATE_FORMAT({column}, 'yyyy-MM-dd HH:00')",
            "minute": f"DATE_FORMAT({column}, 'yyyy-MM-dd HH:mm')",
        }
        return formats.get(grain, column)

    def current_date(self) -> str:
        return "CURRENT_DATE()"

    def current_timestamp(self) -> str:
        return "CURRENT_TIMESTAMP()"

    def date_add(self, column: str, interval: int, unit: str) -> str:
        if unit.lower() == "day":
            return f"DATE_ADD({column}, {interval})"
        return f"({column} + INTERVAL {interval} {unit.upper()})"

    def date_sub(self, column: str, interval: int, unit: str) -> str:
        if unit.lower() == "day":
            return f"DATE_SUB({column}, {interval})"
        return f"({column} - INTERVAL {interval} {unit.upper()})"

    def date_diff(self, start: str, end: str) -> str:
        return f"DATEDIFF({end}, {start})"

    def concat(self, *args: str) -> str:
        return f"CONCAT({', '.join(args)})"

    def substring(self, column: str, start: int, length: int) -> str:
        return f"SUBSTR({column}, {start}, {length})"

    def count_distinct(self, column: str) -> str:
        return f"COUNT(DISTINCT {column})"

    def limit_offset(self, limit: int, offset: int = 0) -> str:
        if offset > 0:
            return f"LIMIT {offset}, {limit}"
        return f"LIMIT {limit}"

    def coalesce(self, *args: str) -> str:
        return f"COALESCE({', '.join(args)})"

    def ifnull(self, column: str, default: str) -> str:
        return f"NVL({column}, {default})"


# ========== 方言注册表 ==========

class DialectRegistry:
    """
    方言注册表

    支持动态注册和获取方言。
    """

    _dialects: Dict[str, SQLDialect] = {}

    @classmethod
    def register(cls, dialect: SQLDialect) -> None:
        """
        注册方言

        Args:
            dialect: 方言实例
        """
        cls._dialects[dialect.name] = dialect
        logger.debug(f"注册 SQL 方言: {dialect.name}")

    @classmethod
    def get(cls, name: str) -> SQLDialect:
        """
        获取方言

        Args:
            name: 方言名称

        Returns:
            方言实例

        Raises:
            ValueError: 方言不存在
        """
        if name not in cls._dialects:
            available = list(cls._dialects.keys())
            raise ValueError(f"未知的 SQL 方言: {name}，可用: {available}")
        return cls._dialects[name]

    @classmethod
    def list(cls) -> List[str]:
        """列出所有可用方言"""
        return list(cls._dialects.keys())

    @classmethod
    def has(cls, name: str) -> bool:
        """检查方言是否存在"""
        return name in cls._dialects


# 注册内置方言
DialectRegistry.register(MySQLDialect())
DialectRegistry.register(HiveDialect())


def get_dialect(name: str) -> SQLDialect:
    """
    获取 SQL 方言（便捷函数）

    Args:
        name: 方言名称

    Returns:
        方言实例
    """
    return DialectRegistry.get(name)
