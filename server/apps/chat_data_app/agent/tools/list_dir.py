"""ChatData list_dir tool."""

from __future__ import annotations

import datetime
import os
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from utils.logger import get_logger
from utils.skill_paths import SKILLS_VIRTUAL_ROOT
from ...services.conversation_workspace import (
    ConversationWorkspaceRuntime,
    is_tool_result_workspace_path,
    resolve_workspace_path,
)

logger = get_logger(__name__)

_MAX_ENTRIES = 500


def _format_mtime(st_mtime: float) -> str:
    return datetime.datetime.fromtimestamp(st_mtime).strftime("%Y-%m-%d %H:%M")


def _iter_dir(
    physical: Path,
    *,
    mode: str,
    workspace_root: str,
    visible_top: str,
    depth: int,
    max_depth: int,
    out: list[dict[str, Any]],
) -> None:
    """递归列出 physical 目录内容，结果写入 out。

    - mode="workspace": 可见路径基于会话根反推为 ``/mnt/user-data/<name>/<sub>``。
    - mode="skill": 可见路径基于已登记技能的可见根 ``visible_top`` 拼接子名。
    不跟随符号链接（``is_dir(follow_symlinks=False)``），避免越界访问；只在目标
    子树内下钻，物理上不会逃出会话根或技能源目录。条目数超过 ``_MAX_ENTRIES`` 即截断。
    """
    if len(out) >= _MAX_ENTRIES:
        return
    root = Path(workspace_root).resolve()
    try:
        with os.scandir(physical) as it:
            items = sorted(it, key=lambda e: (e.is_file(), e.name))
    except (PermissionError, OSError):
        return
    for entry in items:
        if len(out) >= _MAX_ENTRIES:
            break
        child = Path(entry.path).resolve()
        is_dir = entry.is_dir(follow_symlinks=False)
        if mode == "workspace":
            if is_tool_result_workspace_path(workspace_root, child):
                continue
            try:
                rel = child.relative_to(root).as_posix()
                vis_child = "/mnt/user-data/" + rel
            except ValueError:
                # 符号链接指向会话根之外：给一个安全的可见名，不泄漏外部物理路径。
                vis_child = f"/mnt/user-data/workspace/{entry.name}"
        else:
            vis_child = f"{visible_top}/{entry.name}"
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        out.append({
            "type": "dir" if is_dir else "file",
            "path": vis_child + ("/" if is_dir else ""),
            "size": "-" if is_dir else st.st_size,
            "mtime": _format_mtime(st.st_mtime),
        })
        if is_dir and (depth + 1) < max_depth:
            _iter_dir(
                child,
                mode=mode,
                workspace_root=workspace_root,
                visible_top=vis_child,
                depth=depth + 1,
                max_depth=max_depth,
                out=out,
            )


def create_list_dir_tool(workspace_runtime: ConversationWorkspaceRuntime):
    @tool("list_dir")
    async def list_dir(path: str = "workspace", max_depth: int = 2) -> str:
        """
        列出当前会话 workspace / outputs / uploads 目录，或有权访问的技能只读 /mnt/skills 目录内容。

        参数：
        - path: 目标目录。支持 workspace / outputs / uploads 及其别名 /mnt/user-data/*、/workspace 等；
          或技能路径 /mnt/skills/<技能名>/...。默认 "workspace"。
        - max_depth: 递归深度，默认 2；1 表示只列当前目录，最大 5。

        约束：
        - 不能列出会话目录之外、或无权限访问的 /mnt/skills 目录（会被直接拒绝）。
        - 技能路径无需先经 skill_load 加载即可列出，按当前用户权限校验。
        - 浏览目录优先用本工具；仅当需要 find / grep / 递归脚本等高级能力时才使用 bash_run。
        """
        try:
            raw = str(path or "workspace").strip().replace("\\", "/")
            if max_depth < 1 or max_depth > 5:
                return "max_depth 必须在 1-5 之间"

            if raw.startswith(f"{SKILLS_VIRTUAL_ROOT}/"):
                try:
                    target, visible_top = await workspace_runtime.resolve_readonly_skill_path_async(raw)
                except ValueError as exc:
                    return str(exc)
                mode = "skill"
            else:
                try:
                    target = resolve_workspace_path(
                        workspace_runtime.workspace_root, raw, writable=False
                    )
                except ValueError as exc:
                    return str(exc)
                if is_tool_result_workspace_path(workspace_runtime.workspace_root, target):
                    return "outputs/.tool-results/ 是隐藏的工具结果缓存，不能列出"
                visible_top = ""
                mode = "workspace"

            if not target.exists():
                return f"目录不存在: {raw}"
            if not target.is_dir():
                return f"目标不是目录: {raw}"

            entries: list[dict[str, Any]] = []
            _iter_dir(
                target,
                mode=mode,
                workspace_root=workspace_runtime.workspace_root,
                visible_top=visible_top,
                depth=0,
                max_depth=max_depth,
                out=entries,
            )
            if not entries:
                return f"[list_dir]\npath={raw}\nis_dir=true\ncount=0\n(空目录)"
            lines = [f"[list_dir]", f"path={raw}", "is_dir=true", f"count={len(entries)}", ""]
            for item in entries:
                lines.append(
                    f"type={item['type']}  path={item['path']}  "
                    f"size={item['size']}  mtime={item['mtime']}"
                )
            return "\n".join(lines)
        except Exception as exc:
            logger.error("list_dir 执行失败: %s", exc, exc_info=True)
            return f"list_dir 执行失败: {exc}"

    return list_dir
