"""会话级 runtime-skills 快照：把当前用户在本会话「有权访问」的技能冻结为只读副本。

管理域（``CHAT_DATA_HOME/skills/...``）与运行域（``threads/<uid>/<cid>/runtime-skills/``）
解耦：模型在运行期只看到 ``/mnt/skills/<name>/...``，读取的是本会话快照，不直接访问管理域，
也不跨用户 / 跨历史会话越权。快照在会话首次运行时原子构建，此后会话内保持不变：
技能的编辑、发布、回滚不影响已运行会话，新会话获取新快照。

可见技能集合 = 系统技能 + 当前用户自己的当前版本 + 他人 ``visibility_type=global`` 的当前版本。
``chat_data_skill.uniq_skill_name`` 已保证名称全局唯一，因此扁平快照 ``/mnt/skills/<name>`` 无冲突。
"""

from __future__ import annotations

import asyncio
import shutil
import stat
import sys
from pathlib import Path
from uuid import uuid4

from utils.logger import get_logger
from utils.storage_paths import CHAT_DATA_HOME, normalize_conversation_id, normalize_user_id

logger = get_logger(__name__)

THREADS_DIR = Path(CHAT_DATA_HOME) / "threads"


def runtime_skills_snapshot_dir(user_id: str, conversation_id: str) -> Path:
    """返回某会话的 runtime-skills 快照目录（``threads/<uid>/<cid>/runtime-skills``）。"""
    safe_user = normalize_user_id(user_id or "system")
    thread_id = normalize_conversation_id(conversation_id)
    return THREADS_DIR / safe_user / thread_id / "runtime-skills"


def runtime_skills_snapshot_dir_from_workspace(workspace_root: str) -> Path:
    """从 user-data 工作区根推导同级 runtime-skills 快照目录。"""
    return Path(workspace_root).resolve().parent / "runtime-skills"


def list_snapshot_skill_names(snapshot_dir: Path) -> list[str]:
    """列出快照中已存在的技能名（按目录名，排除隐藏文件）。"""
    root = Path(snapshot_dir)
    if not root.is_dir():
        return []
    return sorted(
        item.name
        for item in root.iterdir()
        if item.is_dir() and not item.is_symlink() and not item.name.startswith(".")
    )


def _ignore_symlinks(directory: str, names: list[str]) -> set[str]:
    """copytree ignore 回调：显式跳过符号链接，避免把管理域外的内容带进快照。"""
    return {name for name in names if (Path(directory) / name).is_symlink()}


def _rmtree(path: Path) -> None:
    """兼容各 Python 版本的 rmtree：3.12+ 用 onexc，更早版本用 onerror。"""
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_on_rm_error)
    else:
        shutil.rmtree(path, onerror=_on_rm_error)


def _copy_skill_dir(src: Path, dst: Path) -> None:
    """复制技能版本目录（跳过符号链接），目标存在则先清空。"""
    if dst.exists():
        _rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, symlinks=False, ignore=_ignore_symlinks)


def _on_rm_error(func, path, exc_info):
    try:
        Path(path).chmod(stat.S_IWRITE | stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
        if Path(path).is_dir():
            _rmtree(path)
        else:
            Path(path).unlink()
    except OSError:
        logger.debug("清理快照目录失败（忽略）: %s", path)


def _remove_tree(path: Path) -> None:
    """尽力删除目录树，失败仅记录日志（残留由下次构建时清扫）。"""
    try:
        _rmtree(path)
    except OSError:
        logger.debug("清理快照目录失败（忽略）: %s", path)


def _sweep_stale_siblings(target: Path) -> None:
    """清扫上次构建残留的 .building-* / .old-* 临时目录。"""
    parent = target.parent
    if not parent.is_dir():
        return
    for item in parent.iterdir():
        if item.name.startswith(f"{target.name}.building-") or item.name.startswith(f"{target.name}.old-"):
            _remove_tree(item)


async def build_runtime_skill_snapshot_async(user_id: str, conversation_id: str) -> Path:
    """原子构建（或复用）本会话的 runtime-skills 快照，返回快照目录。

    快照在会话创建/首次运行时构建，此后会话内保持不变：技能的编辑、发布、回滚
    不影响已运行会话，新会话才会获取新快照。
    注意：``ensure_conversation_workspace`` 会预先建出空的 runtime-skills 目录，
    空目录视为未构建，必须填充；只有已含技能内容的目录才视为冻结快照。
    """
    target = runtime_skills_snapshot_dir(user_id, conversation_id)
    if target.is_dir() and any(target.iterdir()):
        return target

    # 延迟 import 打破循环：skill_service 链路经 conversation_workspace 回引本模块。
    from .skill_service import skill_service

    sources = await skill_service.list_runtime_skill_sources_async(user_id)
    if not sources:
        # 空快照不落盘：避免系统技能尚未部署等暂时状态下构建出空快照并被会话内冻结，
        # 下次调用会重试；沙箱仅在快照目录存在时挂载 /mnt/skills 卷，行为一致。
        logger.warning("可见技能集合为空，跳过快照构建: user_id=%s conversation_id=%s", user_id, conversation_id)
        return target

    _sweep_stale_siblings(target)

    tmp = target.with_name(f"{target.name}.building-{uuid4().hex}")
    if tmp.exists():
        _remove_tree(tmp)
    tmp.mkdir(parents=True, exist_ok=True)

    try:
        def _populate() -> None:
            for name, source_dir in sources:
                _copy_skill_dir(source_dir, tmp / name)

        await asyncio.to_thread(_populate)

        if target.is_dir() and any(target.iterdir()):
            # 并发请求已先完成构建；快照会话内不变，丢弃本次临时目录并复用
            _remove_tree(tmp)
            return target
        if target.exists():
            # ensure_conversation_workspace 预先创建的空目录，移除后就位
            _remove_tree(target)
        tmp.rename(target)
    except BaseException:
        _remove_tree(tmp)
        raise
    return target


__all__ = [
    "THREADS_DIR",
    "runtime_skills_snapshot_dir",
    "runtime_skills_snapshot_dir_from_workspace",
    "list_snapshot_skill_names",
    "build_runtime_skill_snapshot_async",
]
