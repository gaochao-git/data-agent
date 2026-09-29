"""Sandbox text search via rg and Files API glob."""

from __future__ import annotations

import fnmatch
import json
import shlex
from collections import deque
from typing import Any

from utils.logger import get_logger

from .workspace_sandbox_api import (
    SandboxExecutionSessionRef,
    _resolve_sandbox_real_path,
    require_sandbox_session,
    run_command_in_sandbox,
)
from .workspace_sandbox_paths import sandbox_path_to_visible
from .workspace_sync import _recover_pending_persistence
from .workspace_text_budget import MAX_FILE_READ_LINE_CHARS

logger = get_logger(__name__)

_TEXT_GREP_MAX_FILE_BYTES = 2 * 1024 * 1024


def _matches_sandbox_glob(relative_path: str, pattern: str) -> bool:
    """Match root files for **/ patterns like pathlib's recursive glob does."""
    normalized = str(pattern or "**/*")
    return fnmatch.fnmatchcase(relative_path, normalized) or (
        normalized.startswith("**/") and fnmatch.fnmatchcase(relative_path, normalized[3:])
    )


def build_rg_args(
    target: str,
    patterns: list[str],
    *,
    glob: str = "**/*",
    use_regex: bool = False,
    ignore_case: bool = False,
    context_lines: int = 0,
    output_mode: str = "content",
) -> list[str]:
    """构造 rg 参数列表（本地后端 subprocess 直用；沙箱经 build_rg_command_str 转壳）。

    默认 -F：fixed strings（与既有字面量语义兼容）；use_regex=True 时去掉 -F
    交给 ripgrep 正则引擎（Rust regex 线性时间，无经典 ReDoS 风险）。
    --json: structured JSON Lines output for robust parsing.
    --hidden: include hidden dirs such as ``.tool-offload`` in the search.
    """
    parts = ["rg", "--json", "--hidden"]
    if not use_regex:
        parts.append("-F")
    if ignore_case:
        parts.append("-i")
    if output_mode == "content" and context_lines > 0:
        parts.extend(["--context", str(int(context_lines))])
    if glob and glob != "**/*":
        parts.extend(["--glob", glob])
    parts.append(target)
    for p in patterns:
        parts.extend(["-e", p])
    return parts


def build_rg_command_str(
    target: str,
    patterns: list[str],
    *,
    glob: str = "**/*",
    use_regex: bool = False,
    ignore_case: bool = False,
    context_lines: int = 0,
    output_mode: str = "content",
) -> str:
    """Build an rg command string for the sandbox backend (shell-executed)."""
    return " ".join(
        shlex.quote(part)
        for part in build_rg_args(
            target,
            patterns,
            glob=glob,
            use_regex=use_regex,
            ignore_case=ignore_case,
            context_lines=context_lines,
            output_mode=output_mode,
        )
    )


def _parse_rg_entry(
    line: str,
    normalized_target: str,
    prefix: str,
    prefix_len: int,
    expected_type: str = "match",
) -> tuple[str, Any, str] | None:
    """解析单行 ``rg --json`` 输出；类型不符 / 无效路径 / 缺行号时返回 None。"""
    try:
        data = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    if data.get("type") != expected_type:
        return None
    pdata = data.get("data") or {}
    file_path = (pdata.get("path") or {}).get("text") or ""
    if file_path == normalized_target:
        file_rel = ""
    elif file_path.startswith(prefix):
        file_rel = file_path[prefix_len:]
    else:
        return None
    line_no = pdata.get("line_number")
    if line_no is None:
        return None
    content = ((pdata.get("lines") or {}).get("text") or "").rstrip("\n")[:MAX_FILE_READ_LINE_CHARS]
    return file_rel, line_no, content


def _parse_rg_match(
    line: str,
    normalized_target: str,
    prefix: str,
    prefix_len: int,
) -> tuple[str, Any, str] | None:
    """解析单行 ``rg --json`` 的 match 条目；非 match / 无效路径 / 缺行号时返回 None。"""
    return _parse_rg_entry(line, normalized_target, prefix, prefix_len, expected_type="match")


class _RgContextAccumulator:
    """rg -C 上下文流的流式归属状态机。

    match 前紧邻的 context 行用滑动窗口保留「最近的 N 行」（窗口满后新行挤掉
    最旧行——语义要求紧邻命中，而不是命中间最早出现的那批）；match 后的
    context 行追加给最近一条命中（重叠行 rg 会为相邻 match 各输出一次，两侧
    各自吸收即可）。文件边界（rg --json 的 ``begin`` 事件）处必须整体重置，
    否则上一文件的尾部 context 行会污染下一文件的首个命中。
    """

    def __init__(self, context_lines: int) -> None:
        self.context_lines = context_lines
        # maxlen=0（context_lines=0）时 append 即丢弃，天然退化为无上下文
        self.pending_before: deque[str] = deque(maxlen=context_lines)
        self.after_count = 0
        # begin 事件后、本文件首个 match 前，context 行不得挂到上一文件末条
        # 命中的 after——matches 列表跨文件共享，靠此标志隔离（与 before 的
        # 跨文件污染同源：单纯清零 after_count 会重新打开 after 吸收）
        self.after_blocked = True

    def reset(self) -> None:
        """文件边界处清空跨文件残留（begin 事件时调用）。"""
        self.pending_before.clear()
        self.after_count = 0
        self.after_blocked = True

    def absorb(self, content: str, matches: list[dict[str, Any]], *, budget_left: int) -> int:
        """归属一条 context 行，返回其计入 max_chars 预算的字符开销。

        预算按「实际进入结果的内容」计费：只有行被挂到某条命中的 after 时才
        计费；仅进 before 缓冲的行不计——它们可能随窗口滑动被丢弃、可能到流
        结束都等不到命中。若该行随后成为下一命中的 before，会在命中处按
        :func:`_rg_match_cost` 计费（相邻命中的重叠行 rg 发射两次、结果出现
        两次、计费两次，三者一致）。剩余预算装不下该行（cost > budget_left）
        时行不进入结果、原样返回 cost，由调用方截断——max_chars 对尾随 after
        行同样是硬上限。
        """
        self.pending_before.append(content)
        if matches and not self.after_blocked and self.after_count < self.context_lines:
            cost = len(content) + 1
            if cost > budget_left:
                return cost
            matches[-1].setdefault("after", []).append(content)
            self.after_count += 1
            return cost
        return 0

    def observe(
        self,
        line: str,
        normalized_target: str,
        prefix: str,
        prefix_len: int,
        matches: list[dict[str, Any]],
        *,
        budget_left: int,
    ) -> int:
        """处理一条非 match 的 JSON 行：begin 事件重置 / context 行归属。

        返回计入 max_chars 预算的字符开销（仅 context 进入结果时非零）；
        剩余预算装不下该行时返回其开销但行不进入结果（调用方据此截断）。
        """
        if _is_rg_file_begin(line):
            self.reset()
            return 0
        context = _parse_rg_entry(line, normalized_target, prefix, prefix_len, expected_type="context")
        if context is None:
            return 0
        return self.absorb(context[2], matches, budget_left=budget_left)

    def take_before(self) -> list[str]:
        """快照当前 before 缓冲并重置 after 计数（新 match 到来时调用）。"""
        before = list(self.pending_before)
        self.pending_before.clear()
        self.after_count = 0
        self.after_blocked = False
        return before


def _rg_match_cost(content: str, before: list[str], *, has_prior_matches: bool) -> int:
    """单条命中计入 max_chars 预算的字符开销（含分隔符与 before 行）。"""
    return len(content) + (1 if has_prior_matches else 0) + sum(len(item) + 1 for item in before)


def _build_rg_match_entry(parsed: tuple[str, Any, str], before: list[str]) -> dict[str, Any]:
    """由 match 解析结果与 before 缓冲构造命中条目。"""
    file_rel, line_no, content = parsed
    entry: dict[str, Any] = {"file": file_rel, "line_number": int(line_no), "content": content}
    if before:
        entry["before"] = before
    return entry


def _is_rg_file_begin(line: str) -> bool:
    """判断一行是否为 rg --json 的 ``begin`` 事件（文件边界信号）。"""
    try:
        data = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return False
    return data.get("type") == "begin"


def parse_rg_output(
    stdout: str,
    target: str,
    *,
    max_results: int,
    max_chars: int,
    context_lines: int = 0,
) -> tuple[list[dict[str, Any]], bool]:
    """Parse ``rg --json`` output into (matches, has_more).

    Each line is a JSON object. ``type == "match"`` entries contain
    ``data.path.text``, ``data.line_number`` and ``data.lines.text``.
    The ``target`` prefix is stripped from the file path so that ``file``
    in each match is relative to the search root.

    context_lines > 0 时（rg -C），相邻 ``type == "context"`` 条目按流式顺序
    归属（见 :class:`_RgContextAccumulator`），并在文件边界（begin 事件）重置；
    预算按实际进入结果的内容计费（before/after/正文各计一次，重叠行按出现次数
    计），``max_chars`` 是硬上限——装不下的尾随 after 行不进入结果并置
    ``has_more``。
    """
    normalized_target = target.rstrip("/")
    prefix = normalized_target + "/"
    prefix_len = len(prefix)
    matches: list[dict[str, Any]] = []
    body_chars = 0
    has_more = False
    accumulator = _RgContextAccumulator(context_lines)
    for line in stdout.splitlines():
        if len(matches) >= max_results:
            has_more = True
            break
        parsed = _parse_rg_entry(line, normalized_target, prefix, prefix_len)
        if parsed is not None:
            before = accumulator.take_before()
            cost = _rg_match_cost(parsed[2], before, has_prior_matches=bool(matches))
            if body_chars + cost > max_chars:
                has_more = True
                break
            matches.append(_build_rg_match_entry(parsed, before))
            body_chars += cost
            continue
        if context_lines > 0:
            remaining = max_chars - body_chars
            cost = accumulator.observe(
                line,
                normalized_target,
                prefix,
                prefix_len,
                matches,
                budget_left=remaining,
            )
            if cost > remaining:
                has_more = True
                break
            body_chars += cost
    return matches, has_more


def parse_rg_files_or_counts(
    stdout: str,
    target: str,
    *,
    max_results: int,
    mode: str,
) -> tuple[list[dict[str, Any]], bool]:
    """Parse ``rg --json`` output into per-file aggregates (files_with_matches / count).

    从同一份 --json 流按文件聚合 match 条目：files 模式返回去重保序的文件清单，
    count 模式返回每文件匹配条数。max_results 限制返回的文件条数，超出置 has_more。
    """
    normalized_target = target.rstrip("/")
    prefix = normalized_target + "/"
    prefix_len = len(prefix)
    order: list[str] = []
    counts: dict[str, int] = {}
    has_more = False
    for line in stdout.splitlines():
        parsed = _parse_rg_entry(line, normalized_target, prefix, prefix_len)
        if parsed is None:
            continue
        file_rel, _line_no, _content = parsed
        if file_rel not in counts:
            order.append(file_rel)
            counts[file_rel] = 0
        counts[file_rel] += 1
    truncated = order[max_results:]
    has_more = bool(truncated)
    result: list[dict[str, Any]] = []
    for file_rel in order[:max_results]:
        if mode == "count":
            result.append({"file": file_rel, "match_count": counts[file_rel]})
        else:
            result.append({"file": file_rel})
    return result, has_more


async def sandbox_grep_text(
    *,
    session_ref: SandboxExecutionSessionRef,
    path: str,
    patterns: list[str],
    glob: str,
    max_results: int,
    max_chars: int,
    use_regex: bool = False,
    ignore_case: bool = False,
    context_lines: int = 0,
    output_mode: str = "content",
) -> dict[str, Any]:
    """Search text in sandbox files via rg (literal by default, optional regex/context/modes)."""
    session = require_sandbox_session(session_ref)
    async with session.operation_lock:
        await _recover_pending_persistence(session)
        target = await _resolve_sandbox_real_path(session=session, file_path=path)

        command = build_rg_command_str(
            target,
            patterns,
            glob=glob,
            use_regex=use_regex,
            ignore_case=ignore_case,
            context_lines=context_lines,
            output_mode=output_mode,
        )
        exit_code, stdout, stderr = await run_command_in_sandbox(
            session=session,
            command=f"bash -lc {shlex.quote(command)}",
            timeout_seconds=30,
        )
        if exit_code == 127 or "command not found" in (stderr or "").lower():
            raise RuntimeError(
                "file_grep 需要 ripgrep (rg)，沙箱环境未安装。请在沙箱镜像中安装 ripgrep: apt-get install ripgrep"
            )
        if exit_code > 1:
            raise RuntimeError(f"rg 执行失败 (exit={exit_code}): {stderr}")

        stripped_target = target.rstrip("/")
        if output_mode in ("files_with_matches", "count"):
            entries, has_more = parse_rg_files_or_counts(
                stdout,
                target,
                max_results=max_results,
                mode=output_mode,
            )
            entries = [
                {
                    "path": sandbox_path_to_visible(f"{stripped_target}/{e['file']}" if e["file"] else stripped_target),
                    **({"match_count": e["match_count"]} if "match_count" in e else {}),
                }
                for e in entries
            ]
            payload = {"entries": entries, "has_more": has_more, "skipped_binary": 0, "skipped_large": 0}
            logger.info(
                "OpenSandbox file_grep(%s) 完成: sandbox_id=%s path=%s entries=%s has_more=%s",
                output_mode,
                session.sandbox_id,
                target,
                len(entries),
                payload["has_more"],
            )
            return payload

        raw_matches, has_more = parse_rg_output(
            stdout,
            target,
            max_results=max_results,
            max_chars=max_chars,
            context_lines=context_lines,
        )
        matches = [
            {
                "path": sandbox_path_to_visible(f"{stripped_target}/{m['file']}" if m["file"] else stripped_target),
                "line_number": m["line_number"],
                "line": m["content"],
                **({"before": m["before"]} if m.get("before") else {}),
                **({"after": m["after"]} if m.get("after") else {}),
            }
            for m in raw_matches
        ]
        payload = {"matches": matches, "has_more": has_more, "skipped_binary": 0, "skipped_large": 0}
        logger.info(
            "OpenSandbox file_grep 完成: sandbox_id=%s path=%s matches=%s has_more=%s",
            session.sandbox_id,
            target,
            len(matches),
            payload["has_more"],
        )
        return payload
