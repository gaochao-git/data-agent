"""ChatData list_dir tool."""

from __future__ import annotations

from langchain_core.tools import tool

from utils.logger import get_logger
from ...services.conversation_workspace import ConversationWorkspaceRuntime

logger = get_logger(__name__)

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
            if max_depth < 1 or max_depth > 5:
                return "max_depth 必须在 1-5 之间"
            raw = str(path or "workspace").strip().replace("\\", "/")
            entries = await workspace_runtime.list_dir(path=raw, max_depth=max_depth)
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
