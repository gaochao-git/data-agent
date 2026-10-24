"""对象投影/本体查询错误类型（供 SQL 构造层与投影服务层共用）。"""

from __future__ import annotations


class ObjectProjectionError(ValueError):
    """对象投影入参/配置不可用。"""
