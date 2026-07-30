"""ChatData file_edit tool."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from utils.logger import get_logger
from ...services.conversation_workspace import ConversationWorkspaceRuntime
from ...services.workspace_patch import parse_patch, visible_paths_for_ops
from .common import json_dumps

logger = get_logger(__name__)

def _tool_result(payload: dict[str, Any]) -> str:
    return "[file_edit]\n" + json_dumps(payload)


def _dashboard_next_action_for_path(path: str, operation: str) -> str | None:
    normalized = str(path or "").strip().replace("\\", "/")
    if normalized.startswith("/mnt/user-data/workspace/dashboard_draft") or normalized.startswith("/workspace/dashboard_draft") or normalized.startswith("workspace/dashboard_draft"):
        return (
            "草稿已更新，但看板生成任务尚未完成。继续调用 dsl2sql_tool 校验完整 dashboard_spec；"
            "校验通过后写入 outputs/<业务名>-<唯一后缀>.dashboard.json；只有用户明确要求修改已有看板预览时才 patch outputs 文件。"
        )
    visible_path = normalized
    for prefix in ("/mnt/user-data/", "/"):
        if visible_path.startswith(prefix):
            visible_path = visible_path[len(prefix):]
            break
    if visible_path.startswith("outputs/") and visible_path.endswith(".dashboard.json"):
        return (
            "看板预览文件已更新。确认 cards 非空且已通过 dsl2sql_tool 校验后，"
            "输出简短最终说明；不要继续写入系统看板。"
        )
    if operation == "patch":
        return "文件已 patch 更新。根据当前任务继续执行下一步；不要把 file_edit 成功等同于任务完成。"
    return None


def _attach_dashboard_next_action(payload: dict[str, Any], *, operation: str, paths: list[str]) -> None:
    for path in reversed(paths):
        next_action = _dashboard_next_action_for_path(path, operation)
        if next_action:
            payload["next_required_action"] = next_action
            return


def create_file_edit_tool(workspace_runtime: ConversationWorkspaceRuntime):
    @tool("file_edit")
    async def file_edit(
        operation: str = "write",
        file_path: str = "",
        content: Any = "",
        mode: str = "overwrite",
        patch: str = "",
        dry_run: bool = False,
    ) -> str:
        """
        写入或 patch 修改当前会话 workspace/ 与 outputs/ 下的文本文件。

        常规写入：
        - operation: write
        - file_path: 目标路径，只能写 workspace/ 或 outputs/
        - content: 文本或结构化对象
        - mode: overwrite 覆盖写入，append 追加文本

        局部修改：
        - operation: patch
        - 使用 patch 参数，patch 必须是 raw patch 文本，不要传 JSON 包装。
        - patch 必须以 `*** Begin Patch` 单独一行开始。
        - patch 必须以 `*** End Patch` 单独一行结束。
        - 更新文件必须使用 `*** Update File: <path>`，并在修改内容前写 `@@`。
        - hunk 内行首必须是空格、`-` 或 `+`：空格表示上下文，`-` 表示删除，`+` 表示新增。
        - 新增文件使用 `*** Add File: <path>`，新增内容每行都以 `+` 开头。
        - 删除文件使用 `*** Delete File: <path>`。

        更新示例：
        *** Begin Patch
        *** Update File: workspace/demo.json
        @@
        -  "title": "旧标题"
        +  "title": "新标题"
        *** End Patch
        """
        try:
            normalized_operation = str(operation or "write").strip().lower()
            if str(patch or "").strip():
                normalized_operation = "patch"
            if normalized_operation == "patch":
                ops = parse_patch(str(patch))
                touched_paths = visible_paths_for_ops(ops)
                actions = await workspace_runtime.apply_patch_ops(ops=ops, dry_run=dry_run)
                payload = {
                    "operation": "patch",
                    "dry_run": dry_run,
                    "actions": actions,
                    "action_count": len(actions),
                }
                if not dry_run:
                    for path in reversed(touched_paths):
                        payload.update(await workspace_runtime.artifact_payload(file_path=path))
                        if payload.get("artifact_type"):
                            break
                _attach_dashboard_next_action(payload, operation="patch", paths=touched_paths)
                return _tool_result(payload)
            if normalized_operation != "write":
                return "operation 只支持 write / patch；发布最终产物请写入 outputs 并使用 .chart.json / .dashboard.json 等后缀"
            normalized_mode = str(mode or "overwrite").strip()
            if normalized_mode not in {"overwrite", "append"}:
                return "mode 只支持 overwrite / append；局部修改请使用 patch 参数"
            text = content if isinstance(content, str) else json_dumps(content)
            payload = await workspace_runtime.write_text_file(
                file_path=file_path, content=text, mode=normalized_mode
            )
            visible_path = str(payload.get("file_path") or file_path)
            payload.update(await workspace_runtime.artifact_payload(file_path=visible_path))
            _attach_dashboard_next_action(payload, operation="write", paths=[visible_path])
            return _tool_result(payload)
        except Exception as exc:
            logger.error("file_edit 执行失败: %s", exc, exc_info=True)
            return f"file_edit 执行失败: {exc}"

    return file_edit
