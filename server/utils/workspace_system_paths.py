"""系统管理的会话工作区目录常量。

``.system`` 是 workspace 下唯一的系统数据区：用户可见、可下载，但不可删改；
智能体只读。所有模块必须从这里获取路径，禁止自行拼字符串。
"""

from __future__ import annotations

from pathlib import PurePosixPath

SYSTEM_DIR_NAME = ".system"
WORKSPACE_VIRTUAL_ROOT = "/mnt/user-data/workspace"
SYSTEM_VIRTUAL_ROOT = f"{WORKSPACE_VIRTUAL_ROOT}/{SYSTEM_DIR_NAME}"
SEMANTIC_DIR_NAME = "semantic"
QUERY_RESULT_DIR_NAME = "query_result"
TOOL_OFFLOAD_DIR_NAME = "tool-offload"
SEMANTIC_VIRTUAL_ROOT = f"{SYSTEM_VIRTUAL_ROOT}/{SEMANTIC_DIR_NAME}"
QUERY_RESULT_VIRTUAL_ROOT = f"{SYSTEM_VIRTUAL_ROOT}/{QUERY_RESULT_DIR_NAME}"
TOOL_OFFLOAD_VIRTUAL_ROOT = f"{SYSTEM_VIRTUAL_ROOT}/{TOOL_OFFLOAD_DIR_NAME}"


def system_virtual_path(*parts: str) -> str:
    return str(PurePosixPath(SYSTEM_VIRTUAL_ROOT).joinpath(*parts))


__all__ = [
    "QUERY_RESULT_DIR_NAME",
    "QUERY_RESULT_VIRTUAL_ROOT",
    "SEMANTIC_DIR_NAME",
    "SEMANTIC_VIRTUAL_ROOT",
    "SYSTEM_DIR_NAME",
    "SYSTEM_VIRTUAL_ROOT",
    "TOOL_OFFLOAD_DIR_NAME",
    "TOOL_OFFLOAD_VIRTUAL_ROOT",
    "WORKSPACE_VIRTUAL_ROOT",
    "system_virtual_path",
]
