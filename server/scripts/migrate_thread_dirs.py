#!/usr/bin/env python3
"""把旧布局会话目录一次性迁移到按创建日期分层的布局。

旧1：``threads/<uid>/<cid>/``　旧2：``threads/<uid>/<YYYY-MM>/<cid>/``
新：``threads/<uid>/<YYYY-MM-DD>/<cid>/``

日期取自 ``user_threads.create_time``（唯一权威），目标路径统一由
``utils.storage_paths.conversation_thread_dir`` 拼装，与线上代码落盘口径一致。
支持对已有 ``YYYY-MM`` 分层结果直接二次迁移到 ``YYYY-MM-DD``。

用法:
  python scripts/migrate_thread_dirs.py           # 预览（dry-run，不移动任何目录）
  python scripts/migrate_thread_dirs.py --apply   # 实际执行迁移
可通过 ``CHAT_DATA_ENV=dev`` 指定加载 conf/.env-dev。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

from dotenv import dotenv_values

SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

from utils.common_util import (  # noqa: E402
    ACTIVE_ENV_KEY,
    ENV_KEY,
    get_local_ip,
    load_deploy_env_hosts,
    resolve_deploy_env,
)

# 历史月份分层目录名（迁移源之一）；日期分层目录名与 storage_paths.normalize_created_date 同口径
_LEGACY_MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_DATE_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")


def load_env() -> str:
    """按脚本约定加载 conf/.env-{env}；已存在的真实环境变量不覆盖。"""
    protected_keys = set(os.environ)
    active_env = os.getenv(ENV_KEY, "").strip()
    if not active_env:
        hosts = load_deploy_env_hosts(SERVER_DIR / "conf")
        active_env = resolve_deploy_env(hosts, local_ips=(get_local_ip(),))
    env_file = SERVER_DIR / "conf" / f".env-{active_env}"
    if not env_file.exists():
        raise ValueError(f"未找到环境配置文件: {env_file}")
    for key, value in dotenv_values(env_file).items():
        if key and value is not None and key not in protected_keys:
            os.environ[key] = value
    os.environ[ACTIVE_ENV_KEY] = active_env
    return active_env


async def fetch_thread_rows() -> dict[str, tuple[str, datetime | None, int]]:
    """读取全量会话：thread_id -> (user_id, create_time, delete_flag)。只读查询。"""
    from sqlalchemy import select

    from utils.database import close_database, get_session
    from utils.models import UserThread

    rows: dict[str, tuple[str, datetime | None, int]] = {}
    try:
        async with get_session() as session:
            result = await session.execute(
                select(UserThread.thread_id, UserThread.user_id, UserThread.create_time, UserThread.delete_flag)
            )
            for thread_id, user_id, create_time, delete_flag in result:
                rows[str(thread_id)] = (str(user_id or ""), create_time, int(delete_flag or 0))
    finally:
        # 在事件循环内释放连接池，避免退出时 aiomysql 连接回收报 Event loop is closed
        await close_database()
    return rows


def collect_old_dirs(threads_dir: Path) -> list[tuple[str, Path]]:
    """收集旧布局目录：(用户目录名, 旧会话目录路径)。

    覆盖两种来源：用户目录下直接挂的会话目录（旧1），以及历史 ``YYYY-MM``
    分层目录下的会话目录（旧2）；已是 ``YYYY-MM-DD`` 的目录跳过。
    """
    old_dirs: list[tuple[str, Path]] = []
    for user_dir in sorted(threads_dir.iterdir()):
        if not user_dir.is_dir() or _DATE_PATTERN.fullmatch(user_dir.name):
            continue
        for entry in sorted(user_dir.iterdir()):
            if not entry.is_dir() or _DATE_PATTERN.fullmatch(entry.name):
                continue
            if _LEGACY_MONTH_PATTERN.fullmatch(entry.name):
                for conv_dir in sorted(entry.iterdir()):
                    if conv_dir.is_dir() and not _DATE_PATTERN.fullmatch(conv_dir.name):
                        old_dirs.append((user_dir.name, conv_dir))
            else:
                old_dirs.append((user_dir.name, entry))
    return old_dirs


def build_plan(
    old_dirs: list[tuple[str, Path]],
    thread_rows: dict[str, tuple[str, datetime | None, int]],
) -> tuple[list[tuple[Path, Path, str]], list[tuple[Path, str]], list[str]]:
    """把旧目录分类为待迁移 / 跳过保留 / 告警三类。"""
    moves: list[tuple[Path, Path, str]] = []
    keeps: list[tuple[Path, str]] = []
    warnings: list[str] = []

    from utils import storage_paths

    for uid, src in old_dirs:
        row = thread_rows.get(src.name)
        if row is None:
            keeps.append((src, "user_threads 无此会话记录"))
            continue
        db_user, create_time, delete_flag = row
        if delete_flag:
            keeps.append((src, "会话已删除（delete_flag=1），保留原位"))
            continue
        if create_time is None:
            keeps.append((src, "create_time 为空，无法确定创建日期"))
            continue
        if storage_paths.normalize_user_id(db_user) != uid:
            warnings.append(f"目录用户 {uid} 与库中 user_id {db_user} 不一致: {src.name}")
        date_str = create_time.strftime("%Y-%m-%d")
        target = storage_paths.conversation_thread_dir(uid, src.name, created_date=date_str)
        if target.exists():
            keeps.append((src, f"目标已存在（新布局已落盘）: {target}"))
            continue
        moves.append((src, target, date_str))
    return moves, keeps, warnings


def apply_moves(moves: list[tuple[Path, Path, str]]) -> None:
    """同盘原子移动；逐条落盘，失败即停，保留已完成部分。"""
    for src, target, _date in moves:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(target))
        print(f"已迁移 {src.name} -> {target.parent.name}/{target.name}")


def remove_empty_legacy_month_dirs(threads_dir: Path) -> int:
    """清理迁移后遗留的空 ``YYYY-MM`` 分层目录，返回删除数。"""
    removed = 0
    for user_dir in sorted(threads_dir.iterdir()):
        if not user_dir.is_dir():
            continue
        for entry in sorted(user_dir.iterdir()):
            if entry.is_dir() and _LEGACY_MONTH_PATTERN.fullmatch(entry.name):
                try:
                    entry.rmdir()
                except OSError:
                    continue  # 非空（跳过保留项仍在其中），保留不动
                removed += 1
                print(f"已清理空目录 {entry}")
    return removed


def _print_keeps(keeps: list[tuple[Path, str]], limit: int = 15) -> None:
    print(f"\n跳过保留 {len(keeps)} 项（不迁移、不删除）:")
    for src, reason in keeps[:limit]:
        print(f"  - {src.parent.name}/{src.name}: {reason}")
    if len(keeps) > limit:
        print(f"  …… 其余 {len(keeps) - limit} 项略")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="会话目录按创建日期分层迁移（默认 dry-run）")
    parser.add_argument("--apply", action="store_true", help="实际执行迁移；缺省仅预览")
    args = parser.parse_args(argv)

    active_env = load_env()
    if not os.getenv("ENCRYPT_KEY"):
        print(f"环境 {active_env} 未配置 ENCRYPT_KEY，无法解密业务库密码")
        return 1
    # storage_paths 在 import 时解析 CHAT_DATA_HOME，必须晚于 load_env() 导入
    from utils import storage_paths

    threads_dir = storage_paths.THREADS_DIR
    if not threads_dir.is_dir():
        print(f"会话根目录不存在: {threads_dir}")
        return 1

    print(f"环境: {active_env}  数据根: {threads_dir.parent}")
    print("读取 user_threads ...")
    thread_rows = asyncio.run(fetch_thread_rows())
    old_dirs = collect_old_dirs(threads_dir)
    moves, keeps, warnings = build_plan(old_dirs, thread_rows)

    for warning in warnings:
        print(f"[警告] {warning}")
    for src, target, date_str in moves:
        print(f"计划迁移 {src.parent.name}/{src.name} -> {date_str}/{src.name}")
    _print_keeps(keeps)

    if not args.apply:
        print(f"\ndry-run 结束：共 {len(moves)} 项目标迁移、{len(keeps)} 项跳过，未移动任何目录。加 --apply 执行。")
        return 0

    apply_moves(moves)
    remove_empty_legacy_month_dirs(threads_dir)
    print(f"\n迁移完成：移动 {len(moves)} 项，跳过 {len(keeps)} 项。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
