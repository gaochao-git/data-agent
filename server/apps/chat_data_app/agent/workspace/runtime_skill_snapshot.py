"""会话级 runtime-skills 快照：把当前用户在本会话「有权访问」的技能冻结为只读副本。

管理域（``CHAT_DATA_HOME/skills/...``）与运行域（``threads/<uid>/<yyyy-MM-DD>/<cid>/runtime-skills/``）
解耦：模型在运行期只看到 ``/mnt/skills/<name>/...``，读取的是本会话快照，不直接访问管理域，
也不跨用户 / 跨历史会话越权。

刷新语义：每次运行前对管理域做指纹校验（各技能目录的文件路径/大小/mtime 摘要），
指纹一致直接复用快照；管理域发生变化（技能新增/移除/内容变更）则在轮次开始前
原子重建快照——单轮执行期间快照保持稳定，技能变更从用户的下一条消息起生效。
早期无指纹的存量快照视为待升级，下一次运行自动重建对齐。

可见技能集合 = 系统技能 + 当前用户自己的当前版本 + 他人 ``visibility_type=global`` 的当前版本。
``chat_data_skill.uniq_skill_name`` 已保证名称全局唯一，因此扁平快照 ``/mnt/skills/<name>`` 无冲突。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import stat
import sys
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from utils.logger import get_logger
from utils.storage_paths import conversation_thread_dir

logger = get_logger(__name__)

SNAPSHOT_READY_MARKER = ".snapshot-ready"


def runtime_skills_snapshot_dir(
    user_id: str,
    conversation_id: str,
    *,
    created_date: str | None = None,
) -> Path:
    """返回某会话的 runtime-skills 快照目录（``threads/<uid>/<yyyy-MM-DD>/<cid>/runtime-skills``）。

    路径经由 ``storage_paths.conversation_thread_dir`` 唯一拼装点，与工作区目录保持同一日期分层。
    """
    return conversation_thread_dir(user_id, conversation_id, created_date=created_date) / "runtime-skills"


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
    from ..skills.loader import parse_metadata

    entries: list[dict[str, Any]] = []
    for name in list_snapshot_skill_names(snapshot_dir):
        skill_md = Path(snapshot_dir) / name / "SKILL.md"
        if not skill_md.is_file() or skill_md.is_symlink():
            continue
        metadata = parse_metadata(skill_md) or {}
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


async def build_runtime_skill_snapshot_async(
    user_id: str,
    conversation_id: str,
    *,
    target_dir: Path | None = None,
) -> Path:
    """按管理域当前状态构建（指纹一致时复用）本会话的 runtime-skills 快照，返回快照目录。

    每次调用先取技能源并计算指纹：与快照标记中的指纹一致则直接复用；不一致
    （技能新增/移除/内容变更）或早期无指纹的存量快照则原子重建。重建发生在
    轮次开始前，单轮执行期间快照保持稳定。
    ``target_dir`` 显式指定快照目录（从工作区根推导，见
    ``ConversationWorkspaceRuntime.runtime_skills_dir``），保证与实际工作区同层；
    缺省按 ``threads/<uid>/<yyyy-MM-DD>/<cid>`` 当前日期拼装，仅适用于会话刚创建的预热场景。
    注意：``ensure_conversation_workspace`` 会预先建出空的 runtime-skills 目录，
    空目录视为未构建，必须填充；无可见技能时通过带指纹的隐藏完成标记冻结空快照。
    """
    target = Path(target_dir) if target_dir is not None else runtime_skills_snapshot_dir(user_id, conversation_id)
    was_ready = is_runtime_skill_snapshot_ready(target)

    from apps.chat_data_app.agent.skills.skill_service import skill_service

    sources = await skill_service.list_runtime_skill_sources_async(user_id)
    current_fingerprints = compute_skill_fingerprints(sources)

    if was_ready:
        stored_fingerprints = _read_snapshot_fingerprints(target)
        if stored_fingerprints == current_fingerprints:
            return target
        logger.info(
            "会话技能快照重建: user=%s conversation=%s 原因=%s",
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
            # 即使技能集合为空，也必须明确冻结本会话的运行时视图（标记带指纹）。
            _write_ready_marker(tmp, current_fingerprints)

        await asyncio.to_thread(_populate)

        if not was_ready and is_runtime_skill_snapshot_ready(target):
            # 填充期间并发请求已先完成构建；快照会话内不变，丢弃本次临时目录并复用
            _remove_tree(tmp)
            return target
        if target.exists():
            # 重建路径：移除旧快照（空目录或指纹过期的旧版本）后就位
            _remove_tree(target)
        tmp.rename(target)
    except BaseException:
        _remove_tree(tmp)
        raise
    return target


__all__ = [
    "SNAPSHOT_READY_MARKER",
    "runtime_skills_snapshot_dir",
    "runtime_skills_snapshot_dir_from_workspace",
    "list_snapshot_skill_names",
    "list_snapshot_skill_metadata",
    "is_runtime_skill_snapshot_ready",
    "compute_skill_fingerprints",
    "build_runtime_skill_snapshot_async",
]
