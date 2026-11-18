"""中间件槽位装配的公共原语。

主图与子图各自维护规范顺序表，但都只允许「按名取槽、按表出列」；调用方
不再用 ``insert`` / ``append`` 拼位置，避免顺序约束散落在各编译入口。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def assemble_middleware(
    order: Sequence[str],
    slots: Mapping[str, Any | None],
) -> list[Any]:
    """按规范顺序表把槽位实例出列；``None`` 表示本次不启用。

    顺序表与槽位必须一一对应：漏登记会直接报错，而不是静默漏挂中间件。
    """
    ordered = set(order)
    provided = set(slots)
    unknown = provided - ordered
    missing = ordered - provided
    if unknown or missing:
        raise ValueError(f"middleware 槽位与规范顺序表不一致：未登记={sorted(unknown)}, 缺槽位={sorted(missing)}")
    return [slots[name] for name in order if slots.get(name) is not None]
