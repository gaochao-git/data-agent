"""业务域上下文的通用载体。

内核只负责原样传递，不解释 key 的业务含义；具体业务（例如问数的数据源）
由业务装配层写入 ``values``，工具构建器按 key 读取。新增业务域字段不需要
修改内核类型，也不需要让内核认识业务概念。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class BusinessContext:
    """一次装配的业务域上下文。"""

    values: Mapping[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        """按 key 读取业务字段；缺省返回 ``default``。"""
        return self.values.get(key, default)


__all__ = ["BusinessContext"]
