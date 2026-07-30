"""Structured patch parsing and application for conversation workspaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping


BEGIN_PATCH = "*** Begin Patch"
END_PATCH = "*** End Patch"
ADD_FILE = "*** Add File: "
UPDATE_FILE = "*** Update File: "
DELETE_FILE = "*** Delete File: "
END_OF_FILE = "*** End of File"


class PatchError(ValueError):
    """The patch is syntactically invalid or cannot be applied."""


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


PatchOp = AddOp | DeleteOp | UpdateOp


@dataclass
class FileState:
    exists: bool
    text: str = ""


@dataclass(frozen=True)
class PatchFileChange:
    """One validated final file state produced by a patch plan."""

    path: str
    exists: bool
    text: str = ""


def parse_patch(text: str) -> list[PatchOp]:
    """Parse the constrained patch format accepted by ``file_edit``."""
    lines = text.splitlines()
    if not lines or lines[0] != BEGIN_PATCH:
        raise PatchError("patch must start with '*** Begin Patch'")
    ops: list[PatchOp] = []
    index = 1
    while index < len(lines):
        line = lines[index]
        if line == END_PATCH:
            if index != len(lines) - 1:
                raise PatchError("unexpected content after '*** End Patch'")
            return ops
        if line.startswith(ADD_FILE):
            path = line[len(ADD_FILE):]
            index += 1
            added: list[str] = []
            while index < len(lines) and not _is_header(lines[index]) and lines[index] != END_PATCH:
                current = lines[index]
                if not current.startswith("+"):
                    raise PatchError("add file lines must start with '+': " + current)
                added.append(current[1:])
                index += 1
            ops.append(AddOp(path=path, lines=added))
            continue
        if line.startswith(DELETE_FILE):
            ops.append(DeleteOp(path=line[len(DELETE_FILE):]))
            index += 1
            continue
        if line.startswith(UPDATE_FILE):
            path = line[len(UPDATE_FILE):]
            index += 1
            hunks: list[Hunk] = []
            current_hunk: Hunk | None = None
            while index < len(lines) and not _is_header(lines[index]) and lines[index] != END_PATCH:
                current = lines[index]
                if current.startswith("@@"):
                    if current_hunk is not None:
                        hunks.append(current_hunk)
                    current_hunk = Hunk()
                    index += 1
                    continue
                if current == END_OF_FILE:
                    index += 1
                    continue
                if current_hunk is None:
                    raise PatchError(f"update block missing '@@' before line: {current}")
                if not current or current[0] not in (" ", "+", "-"):
                    raise PatchError(f"invalid update line: {current}")
                current_hunk.lines.append(current)
                index += 1
            if current_hunk is not None:
                hunks.append(current_hunk)
            if not hunks:
                raise PatchError(f"update file has no hunks: {path}")
            ops.append(UpdateOp(path=path, hunks=hunks))
            continue
        raise PatchError(f"unexpected patch header: {line}")
    raise PatchError("patch missing '*** End Patch'")


def visible_paths_for_ops(ops: list[PatchOp]) -> list[str]:
    return [op.path for op in ops]


def apply_patch_ops(
    ops: list[PatchOp],
    *,
    resolve_writable_path: Callable[[str], Path],
    dry_run: bool = False,
) -> list[str]:
    """Plan then apply patch changes to local paths."""
    resolved_paths = {op.path: resolve_writable_path(op.path) for op in ops}
    source = {
        path: FileState(
            exists=target.exists(),
            text=target.read_text(encoding="utf-8") if target.exists() else "",
        )
        for path, target in resolved_paths.items()
    }
    actions, changes = plan_patch_ops(ops, source)
    if dry_run:
        return actions
    for change in changes:
        target = resolved_paths[change.path]
        if change.exists:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(change.text, encoding="utf-8")
        elif target.exists():
            target.unlink()
    return actions


def _is_header(line: str) -> bool:
    return line.startswith((ADD_FILE, UPDATE_FILE, DELETE_FILE))


def plan_patch_ops(
    ops: list[PatchOp],
    source: Mapping[str, FileState],
) -> tuple[list[str], list[PatchFileChange]]:
    """Apply a patch in memory and return its actions and final file states.

    Backends provide the initial file state and persist the returned changes. Keeping
    this logic here makes local and sandbox patch behavior identical.
    """
    state = {
        path: FileState(exists=item.exists, text=item.text)
        for path, item in source.items()
    }
    actions: list[str] = []
    changed_paths: list[str] = []
    for op in ops:
        if op.path not in changed_paths:
            changed_paths.append(op.path)
        current = state.setdefault(op.path, FileState(exists=False))
        if isinstance(op, AddOp):
            actions.append(_apply_add(op, current))
        elif isinstance(op, DeleteOp):
            actions.append(_apply_delete(op, current))
        else:
            actions.extend(_apply_update(op, current))
    return actions, [
        PatchFileChange(path=path, exists=state[path].exists, text=state[path].text)
        for path in changed_paths
    ]


def _apply_add(op: AddOp, current: FileState) -> str:
    if current.exists:
        raise PatchError(f"cannot add existing file: {op.path}")
    current.exists = True
    current.text = "\n".join(op.lines) + ("\n" if op.lines else "")
    return f"add {op.path}"


def _apply_delete(op: DeleteOp, current: FileState) -> str:
    if not current.exists:
        raise PatchError(f"cannot delete missing file: {op.path}")
    current.exists = False
    current.text = ""
    return f"delete {op.path}"


def _apply_update(op: UpdateOp, current: FileState) -> list[str]:
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


def _find_hunk_start(content: list[str], pattern: list[str], start_index: int) -> int:
    if not pattern:
        return start_index
    max_start = len(content) - len(pattern)
    for index in range(max(start_index, 0), max_start + 1):
        if content[index:index + len(pattern)] == pattern:
            return index
    raise PatchError(f"failed to locate hunk context: {pattern[:3]}")
