"""本体模块业务异常（与统一错误码一一对应，router 层负责映射）。"""

from __future__ import annotations


class OntologyNameInvalidError(ValueError):
    """API 名格式非法（对象/属性/关系）→ 40066。"""


class OntologyInvalidError(ValueError):
    """内容非法（校验器 error 阻断）→ 40067。"""


class OntologyAlreadyExistsError(RuntimeError):
    """同 API 名活跃记录已存在且本次不允许覆盖 → 40068。"""


class OntologyStateInvalidError(RuntimeError):
    """状态机非法迁移、certified 冻结、破坏性变更未确认 → 40069。"""


class OntologyNotFoundError(LookupError):
    """对象/关系不存在 → 40487。"""
