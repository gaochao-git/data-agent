#!/usr/bin/env python3
"""把旧会话目录迁移到 workspace/.system 布局。

迁移范围（只处理会话 user-data，不碰用户技能/插件/运行缓存）：

    outputs/query_result/       -> workspace/.system/query_result/
    outputs/.tool-offload/      -> workspace/.system/tool-offload/

旧的 workspace/semantic/ 不迁移：应用会按当前配置重新物化，旧文件不再读取。
默认只做 dry-run；确认后加 --apply。脚本幂等，目标已存在的文件不覆盖。
旧消息里的 result_path / semantic 路径不会在数据库里自动改写，升级后历史卡片
可能需要重新发起查询或由前端按 result_id 重新定位。

用法：
    cd server
    CHAT_DATA_HOME=/path/to/chat_data python scripts/migrate_workspace_system_layout.py
    CHAT_DATA_HOME=/path/to/chat_data python scripts/migrate_workspace_system_layout.py --apply
"""

from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path

_SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(_SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVER_ROOT))

from utils.workspace_system_paths import (  # noqa: E402
    QUERY_RESULT_DIR_NAME,
    SYSTEM_DIR_NAME,
    TOOL_OFFLOAD_DIR_NAME,
)

LEGACY_TOOL_OFFLOAD_DIR = ".tool-offload"


def _iter_conversation_user_data(threads_root: Path) -> Iterator[Path]:
    """threads/<uid>/<date>/<cid>/user-data。"""
    if not threads_root.is_dir():
        return
    for path in sorted(threads_root.glob("*/*/*/user-data")):
        if path.is_dir() and not path.is_symlink():
            yield path


def _move_contents(src: Path, dst: Path, *, apply: bool) -> tuple[int, int]:
    """把 src 下的文件和子目录移入 dst；返回 (moved, conflicts)。"""
    if not src.is_dir() or src.is_symlink():
        return 0, 0
    moved = 0
    conflicts = 0
    if apply:
        dst.mkdir(parents=True, exist_ok=True)
    for child in sorted(src.iterdir()):
        target = dst / child.name
        if child.is_symlink():
            conflicts += 1
            continue
        if target.exists():
            conflicts += 1
            continue
        if apply:
            shutil.move(str(child), str(target))
        moved += 1
    if apply:
        with contextlib.suppress(OSError):
            # 冲突/隐藏文件导致未清空，保留现场。
            src.rmdir()
    return moved, conflicts


def _migrate_one(user_data: Path, *, apply: bool) -> dict[str, tuple[int, int]]:
    system_root = user_data / "workspace" / SYSTEM_DIR_NAME
    jobs: list[tuple[str, Path, Path]] = []
    legacy_query = user_data / "outputs" / "query_result"
    legacy_offload = user_data / "outputs" / LEGACY_TOOL_OFFLOAD_DIR
    if legacy_query.is_dir():
        jobs.append(("query_result", legacy_query, system_root / QUERY_RESULT_DIR_NAME))
    if legacy_offload.is_dir():
        jobs.append(("tool_offload", legacy_offload, system_root / TOOL_OFFLOAD_DIR_NAME))
    result: dict[str, tuple[int, int]] = {
        "query_result": (0, 0),
        "tool_offload": (0, 0),
    }
    for label, src, dst in jobs:
        result[label] = _move_contents(src, dst, apply=apply)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="迁移旧 ChatData 会话目录到 workspace/.system")
    parser.add_argument("--apply", action="store_true", help="真正执行移动；默认只 dry-run")
    parser.add_argument("--chat-data-home", default=os.getenv("CHAT_DATA_HOME", ""), help="默认读取 CHAT_DATA_HOME")
    args = parser.parse_args()

    if not args.chat_data_home:
        print("错误：必须设置 CHAT_DATA_HOME 或传 --chat-data-home", file=sys.stderr)
        return 2
    home = Path(args.chat_data_home).expanduser().resolve()
    threads_root = home / "threads"
    if not threads_root.is_dir():
        print(f"错误：threads 目录不存在: {threads_root}", file=sys.stderr)
        return 2

    conversations = 0
    query_moved = query_conflicts = 0
    offload_moved = offload_conflicts = 0
    for user_data in _iter_conversation_user_data(threads_root):
        conversations += 1
        moved = _migrate_one(user_data, apply=args.apply)
        query_moved += moved["query_result"][0]
        query_conflicts += moved["query_result"][1]
        offload_moved += moved["tool_offload"][0]
        offload_conflicts += moved["tool_offload"][1]

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] CHAT_DATA_HOME={home}")
    print(f"会话数: {conversations}")
    print(f"query_result: moved={query_moved} conflicts={query_conflicts}")
    print(f"tool_offload: moved={offload_moved} conflicts={offload_conflicts}")
    print("semantic: 不迁移，应用会按当前数据源重新物化")
    if not args.apply:
        print("未修改任何文件；确认后加 --apply。")
    print("注意：历史消息里的旧 result_path 不会自动改写，旧卡片可能需要重新查询/定位。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
