"""DataAnalyze file_edit tool."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from utils.logger import get_logger
from ...services.data_analyze_workspace import resolve_workspace_path, to_visible_path
from ...services.workspace_execution import (
    SandboxExecutionSessionRef,
    sandbox_apply_patch_ops,
    sandbox_artifact_payload,
    sandbox_write_text_file,
)
from .artifact_payloads import load_chart_spec_artifact_payload, load_dashboard_spec_artifact_payload
from .common import json_dumps

logger = get_logger(__name__)

BEGIN_PATCH = "*** Begin Patch"
END_PATCH = "*** End Patch"
ADD_FILE = "*** Add File: "
UPDATE_FILE = "*** Update File: "
DELETE_FILE = "*** Delete File: "
END_OF_FILE = "*** End of File"


class PatchError(Exception):
    """Raised when a patch cannot be parsed or applied."""


@dataclass
class Hunk:
    lines: list[str] = field(default_factory=list)


@dataclass
class AddOp:
    path: str
    lines: list[str]


@dataclass
class DeleteOp:
    path: str


@dataclass
class UpdateOp:
    path: str
    hunks: list[Hunk] = field(default_factory=list)


@dataclass
class FileState:
    exists: bool
    text: str = ""


def _is_header(line: str) -> bool:
    return line.startswith((ADD_FILE, UPDATE_FILE, DELETE_FILE))


def parse_patch(text: str) -> list[AddOp | DeleteOp | UpdateOp]:
    lines = text.splitlines()
    if not lines or lines[0] != BEGIN_PATCH:
        raise PatchError("patch must start with '*** Begin Patch'")
    ops: list[AddOp | DeleteOp | UpdateOp] = []
    i = 1
    while i < len(lines):
        line = lines[i]
        if line == END_PATCH:
            if i != len(lines) - 1:
                raise PatchError("unexpected content after '*** End Patch'")
            return ops
        if line.startswith(ADD_FILE):
            path = line[len(ADD_FILE):]
            i += 1
            added: list[str] = []
            while i < len(lines) and not _is_header(lines[i]) and lines[i] != END_PATCH:
                current = lines[i]
                if not current.startswith("+"):
                    raise PatchError("add file lines must start with '+': " + current)
                added.append(current[1:])
                i += 1
            ops.append(AddOp(path=path, lines=added))
            continue
        if line.startswith(DELETE_FILE):
            ops.append(DeleteOp(path=line[len(DELETE_FILE):]))
            i += 1
            continue
        if line.startswith(UPDATE_FILE):
            path = line[len(UPDATE_FILE):]
            i += 1
            hunks: list[Hunk] = []
            current_hunk: Hunk | None = None
            while i < len(lines) and not _is_header(lines[i]) and lines[i] != END_PATCH:
                current = lines[i]
                if current.startswith("@@"):
                    if current_hunk is not None:
                        hunks.append(current_hunk)
                    current_hunk = Hunk()
                    i += 1
                    continue
                if current == END_OF_FILE:
                    i += 1
                    continue
                if current_hunk is None:
                    raise PatchError(f"update block missing '@@' before line: {current}")
                if not current or current[0] not in (" ", "+", "-"):
                    raise PatchError(f"invalid update line: {current}")
                current_hunk.lines.append(current)
                i += 1
            if current_hunk is not None:
                hunks.append(current_hunk)
            if not hunks:
                raise PatchError(f"update file has no hunks: {path}")
            ops.append(UpdateOp(path=path, hunks=hunks))
            continue
        raise PatchError(f"unexpected patch header: {line}")
    raise PatchError("patch missing '*** End Patch'")


def _load_state(path: Path, state: dict[Path, FileState]) -> FileState:
    if path not in state:
        if path.exists():
            state[path] = FileState(exists=True, text=path.read_text(encoding="utf-8"))
        else:
            state[path] = FileState(exists=False, text="")
    return state[path]


def _find_hunk_start(content: list[str], pattern: list[str], start_index: int) -> int:
    if not pattern:
        return start_index
    max_start = len(content) - len(pattern)
    for index in range(max(start_index, 0), max_start + 1):
        if content[index:index + len(pattern)] == pattern:
            return index
    raise PatchError(f"failed to locate hunk context: {pattern[:3]}")


def _apply_add(op: AddOp, *, root: str, state: dict[Path, FileState]) -> str:
    target = resolve_workspace_path(root, op.path, writable=True)
    current = _load_state(target, state)
    if current.exists:
        raise PatchError(f"cannot add existing file: {op.path}")
    current.exists = True
    current.text = "\n".join(op.lines) + ("\n" if op.lines else "")
    return f"add {op.path}"


def _apply_delete(op: DeleteOp, *, root: str, state: dict[Path, FileState]) -> str:
    target = resolve_workspace_path(root, op.path, writable=True)
    current = _load_state(target, state)
    if not current.exists:
        raise PatchError(f"cannot delete missing file: {op.path}")
    current.exists = False
    current.text = ""
    return f"delete {op.path}"


def _apply_update(op: UpdateOp, *, root: str, state: dict[Path, FileState]) -> list[str]:
    source = resolve_workspace_path(root, op.path, writable=True)
    current = _load_state(source, state)
    if not current.exists:
        raise PatchError(f"cannot update missing file: {op.path}")
    updated = current.text.splitlines()
    search_start = 0
    for hunk in op.hunks:
        old_lines = [line[1:] for line in hunk.lines if line[0] in (" ", "-")]
        new_lines = [line[1:] for line in hunk.lines if line[0] in (" ", "+")]
        start = _find_hunk_start(updated, old_lines, search_start)
        updated[start:start + len(old_lines)] = new_lines
        search_start = start + len(new_lines)
    current.text = "\n".join(updated) + ("\n" if current.text.endswith("\n") or updated else "")
    return [f"update {op.path}"]


def apply_patch_ops(ops: list[AddOp | DeleteOp | UpdateOp], *, root: str, dry_run: bool = False) -> list[str]:
    state: dict[Path, FileState] = {}
    actions: list[str] = []
    for op in ops:
        if isinstance(op, AddOp):
            actions.append(_apply_add(op, root=root, state=state))
        elif isinstance(op, DeleteOp):
            actions.append(_apply_delete(op, root=root, state=state))
        else:
            actions.extend(_apply_update(op, root=root, state=state))
    if dry_run:
        return actions
    for path, file_state in state.items():
        if file_state.exists:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(file_state.text, encoding="utf-8")
        elif path.exists():
            path.unlink()
    return actions


def _visible_paths_for_ops(ops: list[AddOp | DeleteOp | UpdateOp]) -> list[str]:
    paths: list[str] = []
    for op in ops:
        paths.append(op.path)
    return paths


def _ops_to_sandbox_payload(ops: list[AddOp | DeleteOp | UpdateOp]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for op in ops:
        if isinstance(op, AddOp):
            payload.append({"kind": "add", "path": op.path, "visible_path": op.path, "lines": op.lines})
        elif isinstance(op, DeleteOp):
            payload.append({"kind": "delete", "path": op.path, "visible_path": op.path})
        else:
            payload.append({
                "kind": "update",
                "path": op.path,
                "visible_path": op.path,
                "hunks": [{"lines": hunk.lines} for hunk in op.hunks],
            })
    return payload


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


def create_file_edit_tool(workspace_root: str, execution_session_ref: SandboxExecutionSessionRef | None = None):
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
                touched_paths = _visible_paths_for_ops(ops)
                actions = await sandbox_apply_patch_ops(session_ref=execution_session_ref, ops=_ops_to_sandbox_payload(ops), dry_run=dry_run)
                payload = {
                    "operation": "patch",
                    "dry_run": dry_run,
                    "actions": actions,
                    "action_count": len(actions),
                }
                if not dry_run:
                    for path in reversed(touched_paths):
                        payload.update(await sandbox_artifact_payload(session_ref=execution_session_ref, file_path=path))
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
            payload = await sandbox_write_text_file(session_ref=execution_session_ref, file_path=file_path, content=text, mode=normalized_mode)
            visible_path = str(payload.get("file_path") or file_path)
            payload.update(await sandbox_artifact_payload(session_ref=execution_session_ref, file_path=visible_path))
            _attach_dashboard_next_action(payload, operation="write", paths=[visible_path])
            return _tool_result(payload)
        except Exception as exc:
            logger.error("file_edit 执行失败: %s", exc, exc_info=True)
            return f"file_edit 执行失败: {exc}"

    return file_edit
