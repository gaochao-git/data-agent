"""Agent 执行节点准入的唯一判断入口。

用户对话和定时任务必须使用同一套节点身份、白名单解析和失败策略：
- ``CHAT_DATA_NODE_ID``：当前节点身份，必须显式配置，不回退 hostname；
- ``CHAT_DATA_AGENT_EXECUTOR_NODES``：允许执行的节点名，逗号分隔；空表示不限制；
- 白名单读取失败时 fail-close，避免数据库异常时 Standby 误执行。
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable

from apps.chat_data_app.services.setting_service import get_runtime_setting
from utils.logger import get_logger

logger = get_logger(__name__)

EXECUTOR_NODES_SETTING_KEY = "CHAT_DATA_AGENT_EXECUTOR_NODES"
_SETTING_READER = Callable[[str, object], Awaitable[object]]


def configured_node_id() -> str:
    """当前执行节点名：只认显式配置，不回退 hostname。"""
    node_id = str(os.getenv("CHAT_DATA_NODE_ID") or "").strip()
    if not node_id:
        logger.warning("CHAT_DATA_NODE_ID 未配置：执行节点白名单非空时本节点不会执行 Agent Run")
    return node_id


def parse_executor_nodes(raw: object) -> frozenset[str]:
    """解析逗号分隔的执行节点白名单；非字符串/空配置视为不限制。"""
    if not isinstance(raw, str):
        return frozenset()
    return frozenset(item.strip() for item in raw.split(",") if item.strip())


async def is_executor_allowed(
    node_id: str,
    *,
    setting_reader: _SETTING_READER = get_runtime_setting,
) -> bool:
    """判断当前节点是否允许执行；白名单读取失败按 fail-close 处理。"""
    try:
        raw = await setting_reader(EXECUTOR_NODES_SETTING_KEY, "")
    except Exception:
        logger.error("读取 Agent 执行节点白名单失败，按禁止执行处理", exc_info=True)
        return False
    allowed = parse_executor_nodes(raw)
    return not allowed or (bool(node_id) and node_id in allowed)
