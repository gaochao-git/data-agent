"""用户级共享 runtime-skills 快照：把当前用户「有权访问」的技能冻结为只读副本。

管理域（代码包 / ``CHAT_DATA_HOME/skills/...``）与运行域
（``CHAT_DATA_CACHE_HOME/runtime-skills/<uid>/``）解耦：模型在运行期只看到
``/mnt/skills/<name>/...``，读取的是该用户的共享快照，不直接访问管理域，也不跨用户越权。

刷新语义：每次运行前对管理域做指纹校验（各技能目录的文件路径/大小/mtime 摘要），
指纹一致直接复用；技能新增/移除/内容变更则在轮次开始前原子重建。同一用户的多个
会话共享一份目录，重建在进程内按目录加锁串行执行，提交阶段再由跨进程文件锁串行化
（生产默认 4 个 Gunicorn worker），并以「临时目录填充 → 原子替换」收口，避免并发
构建互相清扫或同时 rename 同一目录；等锁有超时，卡住的持锁者不会让其它 worker
永久阻塞。技能变更从下一轮开始生效。
早期无指纹的存量快照视为待升级，下一次运行自动重建对齐。

可见技能集合 = 系统技能 + 当前用户自己的当前版本 + 他人 ``visibility_type=global`` 的当前版本。
``chat_data_skill.uniq_skill_name`` 已保证名称全局唯一，因此扁平快照 ``/mnt/skills/<name>`` 无冲突。
"""

from __future__ import annotations

import asyncio
import errno
import fcntl
import hashlib
import json
import os
import shutil
import stat
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from utils.logger import get_logger
from utils.storage_paths import conversation_cache_dir, conversation_cache_dir_from_workspace, runtime_skills_cache_dir

logger = get_logger(__name__)

SNAPSHOT_READY_MARKER = ".snapshot-ready"


def runtime_skills_snapshot_dir(
    user_id: str,
    conversation_id: str,
    *,
    created_date: str | None = None,
) -> Path:
    """返回某会话的 runtime-skills 快照目录（``threads/<uid>/<yyyy-MM-DD>/<cid>/runtime-skills``）。

    路径经 ``storage_paths.conversation_cache_dir`` 拼装，落到本地派生缓存根。
    """
    return conversation_cache_dir(user_id, conversation_id, created_date=created_date) / "runtime-skills"


def runtime_skills_snapshot_dir_from_workspace(workspace_root: str) -> Path:
    """从 user-data 工作区根推导同会话的本地 runtime-skills 快照目录。"""
    return conversation_cache_dir_from_workspace(workspace_root) / "runtime-skills"


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


def is_runtime_skill_snapshot_ready(snapshot_dir: Path) -> bool:
    """判断会话快照是否已冻结，兼容早期无完成标记的非空快照。"""
    root = Path(snapshot_dir)
    marker = root / SNAPSHOT_READY_MARKER
    return root.is_dir() and ((marker.is_file() and not marker.is_symlink()) or bool(list_snapshot_skill_names(root)))


def list_snapshot_skill_metadata(snapshot_dir: Path) -> list[dict[str, Any]]:
    """读取运行时快照元数据，作为 Agent 技能清单的唯一来源。

    快照目录名就是模型实际可访问的 ``/mnt/skills/<name>`` 路径，因此即使
    SKILL.md frontmatter 中的 ``name`` 异常，也必须以目录名为准，避免提示词
    声明的名称与运行时路径不一致。缺失或损坏的 frontmatter 降级为空描述，
    但仍保留可读取的快照技能。
    """
    # 延迟导入复用 loader 的唯一 frontmatter 解析入口，避免顶层相互 import。
    from ..contracts.skill_metadata import parse_metadata

    entries: list[dict[str, Any]] = []
    for name in list_snapshot_skill_names(snapshot_dir):
        skill_md = Path(snapshot_dir) / name / "SKILL.md"
        if not skill_md.is_file() or skill_md.is_symlink():
            continue
        metadata: Mapping[str, Any] = parse_metadata(skill_md) or {}
        entries.append(
            {
                "name": name,
                "title": str(metadata.get("title") or name),
                "description": str(metadata.get("description") or ""),
                "domain": str(metadata.get("domain") or ""),
                "category": str(metadata.get("category") or ""),
            }
        )
    return entries


def _ignore_symlinks(directory: str, names: list[str]) -> set[str]:
    """copytree ignore 回调：显式跳过符号链接，避免把管理域外的内容带进快照。"""
    return {name for name in names if (Path(directory) / name).is_symlink()}


def _compute_single_skill_fingerprint(name: str, source_dir: Path) -> str:
    """计算单个技能源目录的内容指纹（相对路径 + 大小 + mtime 的摘要）。"""
    entries: list[str] = []
    root = Path(source_dir)
    if not root.is_dir():
        entries.append("<missing>")
    else:
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                continue
            rel = path.relative_to(root).as_posix()
            if path.is_dir():
                entries.append(f"d:{rel}")
            else:
                try:
                    file_stat = path.stat()
                except OSError:
                    # rglob 枚举与 stat 之间文件可能被并发删除/替换；
                    # 跳过该文件让指纹自然失配触发快照重建，
                    # 而不是让异常冒泡违背"指纹计算永不抛"的约定
                    continue
                entries.append(f"f:{rel}:{file_stat.st_size}:{file_stat.st_mtime_ns}")
    return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()


def compute_skill_fingerprints(sources: Iterable[tuple[str, Path]]) -> dict[str, str]:
    """对每个技能源目录计算内容指纹（相对路径 + 大小 + mtime 的摘要）。

    返回 ``{技能名: 指纹}``；目录缺失的技能记为占位指纹（构建阶段会自然报错），
    不在指纹计算阶段抛异常，保证校验路径永不比构建路径先失败。
    """
    fingerprints: dict[str, str] = {}
    for name, source_dir in sources:
        fingerprints[name] = _compute_single_skill_fingerprint(name, source_dir)
    return fingerprints


def _write_ready_marker(target_dir: Path, fingerprints: dict[str, str]) -> None:
    """把指纹写入完成标记文件，供后续运行做变更检测。"""
    (target_dir / SNAPSHOT_READY_MARKER).write_text(
        json.dumps({"skills": fingerprints}, ensure_ascii=False), encoding="utf-8"
    )


def _read_snapshot_fingerprints(snapshot_dir: Path) -> dict[str, str] | None:
    """读取快照标记中的技能指纹；缺失、损坏或早期空标记返回 None（视为待升级）。"""
    marker = Path(snapshot_dir) / SNAPSHOT_READY_MARKER
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    skills = data.get("skills") if isinstance(data, dict) else None
    if not isinstance(skills, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in skills.items()
    ):
        return None
    return skills


def _describe_fingerprint_changes(stored: dict[str, str] | None, current: dict[str, str]) -> str:
    """生成人类可读的技能变更摘要（用于重建日志）。"""
    if stored is None:
        return "升级无指纹的存量快照"
    stored_names, current_names = set(stored), set(current)
    parts = [f"{name}(新增)" for name in sorted(current_names - stored_names)]
    parts += [f"{name}(移除)" for name in sorted(stored_names - current_names)]
    parts += [f"{name}(内容变更)" for name in sorted(stored_names & current_names) if stored[name] != current[name]]
    return "、".join(parts) if parts else "指纹不一致（无明细）"


def _rmtree(path: Path) -> None:
    """rmtree 兼容层：3.12+ 用 onexc 参数（基线已是 3.12，旧版 onerror 分支移除）。"""
    shutil.rmtree(path, onexc=_on_rm_error)


def _copy_skill_dir(src: Path, dst: Path) -> None:
    """复制技能版本目录（跳过符号链接），目标存在则先清空。"""
    if dst.exists():
        _rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, symlinks=False, ignore=_ignore_symlinks)


def _on_rm_error(
    func: Callable[..., Any],
    path: str,
    exc: BaseException,
) -> None:
    try:
        Path(path).chmod(stat.S_IWRITE | stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
        if Path(path).is_dir():
            _rmtree(Path(path))
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


# 同一用户共享一份快照目录后，多个会话可能同时构建。按 (事件循环, 目标目录)
# 串行化重建，避免互相清扫临时目录、互相覆盖 target；跨进程由 _commit_snapshot
# 的文件锁把提交阶段串行化，进程内锁保留作快速路径。
_SNAPSHOT_LOCK_GUARD = threading.Lock()
_SNAPSHOT_LOCKS: dict[tuple[int, str], asyncio.Lock] = {}
# 只有超过此时长的临时目录才可能是残留；刚创建的属于别的会话正在构建。
SNAPSHOT_TMP_TTL_SECONDS = 3600.0
# 提交锁的最长等待：持锁进程若卡在共享盘 rename，其它 worker 不能无限阻塞。
SNAPSHOT_LOCK_TIMEOUT_SECONDS = 30.0
SNAPSHOT_LOCK_POLL_SECONDS = 0.05


class SnapshotLockTimeoutError(RuntimeError):
    """等跨进程提交锁超时，且没有可复用的现有快照。"""


def _snapshot_lock_for(target: Path) -> asyncio.Lock:
    key = (id(asyncio.get_running_loop()), str(target))
    with _SNAPSHOT_LOCK_GUARD:
        lock = _SNAPSHOT_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _SNAPSHOT_LOCKS[key] = lock
        return lock


def _snapshot_matches(target: Path, fingerprints: dict[str, str]) -> bool:
    """目标目录已就绪且指纹一致（含其它进程刚刚构建完成的情形）。"""
    return is_runtime_skill_snapshot_ready(target) and _read_snapshot_fingerprints(target) == fingerprints


def _snapshot_lock_path(target: Path) -> Path:
    """跨进程锁文件：普通隐藏文件，放在共享快照根的父目录。

    必须是普通文件且永不删除：GC 只递归删目录，不会碰它；删掉会换 inode，
    使新旧进程持有不同的锁对象，互斥静默失效。用路径摘要命名，避免用户 ID
    里的特殊字符进入文件名。
    """
    digest = hashlib.sha256(str(target).encode("utf-8")).hexdigest()[:32]
    return target.parent / f".{digest}.lock"


def _acquire_snapshot_lock(fd: int, *, timeout: float | None = None) -> bool:
    """非阻塞轮询获取提交锁，超时返回 False（交给调用方决定复用还是失败）。

    裸 ``LOCK_EX`` 会让持锁进程卡住时其它 worker 在 ``asyncio.to_thread`` 里永久
    阻塞并长期占用线程池，因此改为带 deadline 的 ``LOCK_NB`` 轮询。只重试「锁被
    别人持有」这类 errno，其它 errno 直接抛出，避免把磁盘/权限故障误判成锁竞争。
    """
    limit = SNAPSHOT_LOCK_TIMEOUT_SECONDS if timeout is None else timeout
    deadline = time.monotonic() + limit
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError as exc:
            if exc.errno not in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES):
                raise
        if time.monotonic() >= deadline:
            return False
        time.sleep(SNAPSHOT_LOCK_POLL_SECONDS)


def _commit_snapshot(
    target: Path,
    tmp: Path,
    fingerprints: dict[str, str],
    was_ready: bool,
) -> tuple[bool, Path | None]:
    """持跨进程文件锁完成「指纹复核 → 原子替换」，返回 (是否复用, 待清理旧目录)。

    锁的获取与释放都在这个同步函数内完成，由调用方用一次 ``asyncio.to_thread``
    执行：等锁有上界，持锁者也不必再申请线程，避免死锁。临界区只有 stat / rename；
    递归删除旧目录留给调用方在锁外做，缩短持锁时间。
    """
    lock_path = _snapshot_lock_path(target)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    try:
        acquired = _acquire_snapshot_lock(fd)
        if not acquired:
            # 不把本次可能已过期的 tmp 覆盖上去：有可用快照就降级复用，不阻断本轮 run。
            if is_runtime_skill_snapshot_ready(target):
                logger.warning("技能快照提交等待跨进程锁超时，本轮复用现有快照: %s", target)
                return True, None
            raise SnapshotLockTimeoutError(f"技能快照提交等待跨进程锁超时且无可用快照: {lock_path}")
        if _snapshot_matches(target, fingerprints):
            return True, None
        if not was_ready and is_runtime_skill_snapshot_ready(target):
            # 填充期间别人已建好快照：绝不覆盖正在被读的目录，本轮复用对方视图。
            # 下一轮 was_ready=True 且指纹不匹配会自动重建收敛（见 _build_snapshot_locked_async）。
            if _read_snapshot_fingerprints(target) is None:
                logger.info("填充期间出现无指纹存量快照，本轮兼容复用: %s", target)
            else:
                logger.warning("填充期间并发请求已提交不同指纹，本轮复用其视图、下轮重建: %s", target)
            return True, None
        old_target: Path | None = None
        if target.exists():
            old_target = target.with_name(f"{target.name}.old-{uuid4().hex}")
            target.rename(old_target)
        tmp.rename(target)
        return False, old_target
    finally:
        try:
            if acquired:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _sweep_stale_siblings(target: Path, *, now: float | None = None) -> None:
    """清扫超过 TTL 的 .building-* / .old-* 临时目录。

    快照按用户共享后，同一目录下可能有多个会话在构建；新创建的临时目录
    属于别的会话，绝不能按前缀一律删除（会让对方复制中途 FileNotFoundError）。
    """
    parent = target.parent
    if not parent.is_dir():
        return
    cutoff = (now if now is not None else time.time()) - SNAPSHOT_TMP_TTL_SECONDS
    prefixes = (f"{target.name}.building-", f"{target.name}.old-")
    for item in parent.iterdir():
        if not item.name.startswith(prefixes):
            continue
        try:
            if item.stat().st_mtime >= cutoff:
                continue
        except OSError:
            continue
        _remove_tree(item)


async def _build_snapshot_locked_async(
    user_id: str,
    conversation_id: str,
    *,
    target: Path,
    sources: list[tuple[str, Path]],
    current_fingerprints: dict[str, str],
    was_ready: bool,
) -> Path:
    """持进程内锁执行：填充临时目录 → 持跨进程锁复核指纹并原子替换共享目录。

    ``was_ready`` 必须在取技能源之前采样：并发请求可能在本请求取源阶段就建好
    快照，用锁内重新采样的值会把它误判成「本就存在的旧快照」而覆盖掉。
    """
    stored_fingerprints = _read_snapshot_fingerprints(target) if was_ready else None
    if was_ready and stored_fingerprints == current_fingerprints:
        return target
    if was_ready:
        logger.info(
            "技能快照重建: user=%s conversation=%s 原因=%s",
            user_id,
            conversation_id,
            _describe_fingerprint_changes(stored_fingerprints, current_fingerprints),
        )

    await asyncio.to_thread(_sweep_stale_siblings, target)
    tmp = target.with_name(f"{target.name}.building-{uuid4().hex}")
    if tmp.exists():
        await asyncio.to_thread(_remove_tree, tmp)
    tmp.mkdir(parents=True, exist_ok=True)

    try:

        def _populate() -> None:
            for name, source_dir in sources:
                _copy_skill_dir(source_dir, tmp / name)
            # 即使技能集合为空，也必须明确冻结本轮运行时视图（标记带指纹）。
            _write_ready_marker(tmp, current_fingerprints)

        await asyncio.to_thread(_populate)

        # 提交阶段（指纹复核 + 两次 rename）整体交给一个同步线程并持跨进程锁：
        # 其它 worker 同时重建时，第二个会在锁内复用已有快照（同指纹或并发
        # 不同指纹），不会出现「旧目录已被移走 / 目标已非空」的 rename 冲突；
        # 等锁超时且已有可用快照时也走同一条复用降级路径，本轮不中断。
        reused, old_target = await asyncio.to_thread(_commit_snapshot, target, tmp, current_fingerprints, was_ready)
        if reused:
            await asyncio.to_thread(_remove_tree, tmp)
        if old_target is not None:
            # 旧目录仍可被已打开 fd 的读侧继续访问，锁外再递归清理。
            await asyncio.to_thread(_remove_tree, old_target)
    except BaseException:
        _remove_tree(tmp)
        raise
    return target


async def build_runtime_skill_snapshot_async(
    user_id: str,
    conversation_id: str,
    *,
    target_dir: Path | None = None,
) -> Path:
    """按管理域当前状态构建（指纹一致时复用）该用户的 runtime-skills 快照。

    每次调用先取技能源并计算指纹：与快照标记一致则直接复用；不一致（技能
    新增/移除/内容变更）或早期无指纹的存量快照则原子重建。快照按用户共享
    （``runtime-skills/<uid>``），重建在锁内串行执行，单轮执行期间保持稳定。
    ``target_dir`` 显式指定目录仅保留给兼容/惰性构建路径。
    无可见技能时通过带指纹的隐藏完成标记冻结空快照。
    """
    from ..contracts.skill_source import get_skill_snapshot_source

    target = Path(target_dir) if target_dir is not None else runtime_skills_cache_dir(user_id)
    was_ready = is_runtime_skill_snapshot_ready(target)
    source = get_skill_snapshot_source()
    sources = await source.list_runtime_skill_sources_async(user_id) if source is not None else []
    current_fingerprints = compute_skill_fingerprints(sources)

    async with _snapshot_lock_for(target):
        return await _build_snapshot_locked_async(
            user_id,
            conversation_id,
            target=target,
            sources=list(sources),
            current_fingerprints=current_fingerprints,
            was_ready=was_ready,
        )


__all__ = [
    "SNAPSHOT_READY_MARKER",
    "SnapshotLockTimeoutError",
    "runtime_skills_snapshot_dir",
    "runtime_skills_snapshot_dir_from_workspace",
    "list_snapshot_skill_names",
    "list_snapshot_skill_metadata",
    "is_runtime_skill_snapshot_ready",
    "compute_skill_fingerprints",
    "build_runtime_skill_snapshot_async",
]
