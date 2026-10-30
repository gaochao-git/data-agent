"""Harness 内部跨层契约（Port）。

这一层只放 Protocol/纯类型，不依赖任何 harness 实现模块；上层通过它消费
下层能力，由装配层注入具体实现，避免子包之间形成反向依赖或环。
"""

from .inbox import ClaimedInboxMessages, InboxPort

__all__ = ["ClaimedInboxMessages", "InboxPort"]
