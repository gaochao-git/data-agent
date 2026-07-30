"""ChatData 会话工作区及本地 / OpenSandbox 后端适配。"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Literal

from utils.database import get_session
from utils.logger import get_logger
from utils.skill_paths import SKILLS_VIRTUAL_ROOT, runtime_skill_visible_root
from utils.storage_paths import CHAT_DATA_HOME, normalize_conversation_id, normalize_user_id
from utils.tool_result_store import TOOL_RESULTS_DIR_NAME, ToolResultStore

from ..daos.conversation_dao import ConversationDao
from .artifact_registry import parse_output_artifact
from .runtime_skill_snapshot import (
    build_runtime_skill_snapshot_async,
    runtime_skills_snapshot_dir_from_workspace,
)
from .semantic_common import AGENT_ID
from .skill_service import skill_service
from .workspace_execution import (
    SandboxExecutionSessionRef,
    get_workspace_execution_session,
    sandbox_apply_patch_ops,
    sandbox_artifact_payload,
    sandbox_write_text_file,
)

logger = get_logger(__name__)

THREADS_DIR = Path(CHAT_DATA_HOME) / "threads"
WORKSPACE_DIR_NAMES = ("workspace", "outputs", "uploads")
conversation_dao = ConversationDao(session_context=lambda: get_session())


@dataclass
class ConversationWorkspaceRuntime:
    """当前会话工作区的后端绑定。

    文件读取始终直接访问受控的共享 ``workspace_root``，确保 local 与 sandbox
    使用同一套路径校验和分页语义。OpenSandbox session 仅在执行、写入或产物操作时创建。

    运行期技能统一来自本会话的 ``runtime-skills`` 快照（只读），模型只看到扁平的
    ``/mnt/skills/<name>``；不再按名远程解析、不再逐技能登记挂载。
    """

    workspace_root: str
    user_id: str
    backend: Literal["local", "sandbox"]
    sandbox_session_ref: SandboxExecutionSessionRef = field(default_factory=SandboxExecutionSessionRef)
    # 当前用户有权访问的全部技能名（由请求入口按 user_id 解析得到）。
    # 仅用于说明/调试，不作为本地读取的前置条件。
    available_skill_names: list[str] = field(default_factory=list)

    @property
    def runtime_skills_dir(self) -> Path:
        """本会话 runtime-skills 快照目录（运行域只读技能根）。"""
        return runtime_skills_snapshot_dir_from_workspace(self.workspace_root)

    async def _ensure_snapshot_async(self) -> None:
        """确保本会话技能快照存在且可用；为空则按当前用户可见集合惰性构建。

        ``ensure_conversation_workspace`` 会先建出空的 ``runtime-skills`` 目录，因此不能仅以
        目录是否存在判断快照就绪——空目录必须在此填充。已含技能内容（有任意技能子目录）
        的目录视为会话内稳定快照，直接复用、不重复复制。单个技能路径异常不会中断构建
        （由 ``list_runtime_skill_sources_async`` 跳过），构建失败也不影响技能本地读取。
        """
        snapshot_dir = self.runtime_skills_dir
        # 目录非空（已有技能子目录）则视为已就绪，直接复用。
        if snapshot_dir.is_dir() and any(snapshot_dir.iterdir()):
            return
        base = Path(self.workspace_root).resolve().parent  # .../<cid>
        uid, cid = base.parent.name, base.name
        try:
            await build_runtime_skill_snapshot_async(uid, cid)
        except Exception as err:
            logger.warning("惰性构建技能快照失败，技能读取可能不可用: %s", err)

    async def resolve_readonly_skill_path_async(self, path: str) -> tuple[Path, str]:
        """将 /mnt/skills 路径解析到本会话快照真实文件系统。

        运行域技能统一为扁平路径 ``/mnt/skills/<name>/...``，直接映射到
        ``threads/<uid>/<cid>/runtime-skills/<name>/...``；不做按名远程解析，
        快照内不存在即视为无权限。快照缺失时惰性构建。
        """
        raw = str(path or "").strip().replace("\\", "/")
        prefix = f"{SKILLS_VIRTUAL_ROOT}/"
        if not raw.startswith(prefix):
            raise ValueError(f"只允许访问技能路径: {path}")
        parts = PurePosixPath(raw).parts
        if len(parts) < 4 or parts[1:3] != ("mnt", "skills"):
            raise ValueError(f"技能路径无效: {path}")
        name = parts[3]
        if not name or name.startswith("."):
            raise ValueError(f"技能路径无效: {path}")
        await self._ensure_snapshot_async()
        source_dir = (self.runtime_skills_dir / name).resolve()
        if not source_dir.is_dir():
            raise ValueError(f"技能不存在或无权限访问: {name}")
        relative_parts = parts[4:]
        if ".." in relative_parts:
            raise ValueError(f"技能路径越界: {path}")
        target = (source_dir.joinpath(*relative_parts)).resolve()
        if target != source_dir and source_dir not in target.parents:
            raise ValueError(f"技能路径越界: {path}")
        visible = runtime_skill_visible_root(name)
        if relative_parts:
            visible += "/" + "/".join(relative_parts)
        return target, visible

    async def get_sandbox_session_ref(self) -> SandboxExecutionSessionRef:
        if self.backend != "sandbox":
            raise ValueError("当前工作区后端不是 OpenSandbox")
        if self.sandbox_session_ref.current is None:
            # 沙箱只挂载两个固定卷：/mnt/user-data（可写）+ /mnt/skills（只读快照）。
            # 不再逐技能登记挂载——可见技能由快照在会话首次运行时一次性冻结。
            await self._ensure_snapshot_async()
            self.sandbox_session_ref.current = await get_workspace_execution_session(
                self.workspace_root,
                self.user_id,
                runtime_skills_dir=self.runtime_skills_dir,
            )
        return self.sandbox_session_ref


def _safe_segment(value: str) -> str:
    """规整路径段，避免会话 ID 造成目录越界。"""
    try:
        return normalize_conversation_id(value)
    except ValueError as exc:
        raise ValueError("非法会话 ID") from exc


def ensure_conversation_workspace(conversation_id: str, user_id: str = "system") -> dict[str, str]:
    """创建并返回 ChatData 会话目录结构。

    布局：``threads/<uid>/<cid>/{user-data/{workspace,outputs,uploads}, runtime-skills/}``。
    ``user-data`` 为模型可写运行域；``runtime-skills`` 为会话级技能快照（由
    ``runtime_skill_snapshot.build_runtime_skill_snapshot_async`` 惰性填充）。
    """
    thread_id = _safe_segment(conversation_id)
    safe_user_id = normalize_user_id(user_id or "system")
    base = (THREADS_DIR / safe_user_id / thread_id).resolve()
    root = (base / "user-data").resolve()
    root.mkdir(parents=True, exist_ok=True)
    (base / "runtime-skills").mkdir(parents=True, exist_ok=True)
    paths = {"root": str(root)}
    for name in WORKSPACE_DIR_NAMES:
        path = root / name
        path.mkdir(parents=True, exist_ok=True)
        paths[name.replace("-", "_")] = str(path)
    (root / "outputs" / TOOL_RESULTS_DIR_NAME).mkdir(parents=True, exist_ok=True)
    return paths


def resolve_workspace_path(workspace_root: str, path: str, *, writable: bool = False) -> Path:
    """解析模型可见路径并按解析后的真实路径实施目录边界。"""
    root = Path(workspace_root).resolve()
    raw = str(path or "").strip()
    while raw.startswith("./"):
        raw = raw[2:]
    if not raw:
        raise ValueError("必须指定 workspace、outputs 或 uploads 下的路径")

    alias_map = {
        "/mnt/user-data/workspace": "workspace",
        "/mnt/user-data/outputs": "outputs",
        "/mnt/user-data/uploads": "uploads",
        "/workspace": "workspace",
        "/outputs": "outputs",
        "/uploads": "uploads",
    }
    for prefix, alias in alias_map.items():
        if raw == prefix or raw.startswith(f"{prefix}/"):
            raw = f"{alias}/{raw[len(prefix):].lstrip('/')}"
            break

    if os.path.isabs(raw):
        raise ValueError(f"只允许访问当前会话目录内的相对路径: {path}")

    target = (root / raw).resolve()
    if root not in target.parents:
        raise ValueError(f"路径越界，禁止访问当前会话目录之外的路径: {path}")

    visible_roots = [(root / name).resolve() for name in WORKSPACE_DIR_NAMES]
    if not any(target == item or item in target.parents for item in visible_roots):
        raise ValueError("只允许访问 workspace/、outputs/ 或 uploads/")

    if writable:
        writable_roots = [(root / "workspace").resolve(), (root / "outputs").resolve()]
        if not any(target == item or item in target.parents for item in writable_roots):
            raise ValueError("只允许写入 workspace/ 或 outputs/")
        if is_tool_result_workspace_path(str(root), target):
            raise ValueError("不允许通过文件编辑工具修改 outputs/.tool-results/")
    return target


def is_tool_result_workspace_path(workspace_root: str, path: Path) -> bool:
    """Return whether a resolved path belongs to hidden tool-result storage."""
    results_root = (Path(workspace_root).resolve() / "outputs" / TOOL_RESULTS_DIR_NAME).resolve()
    target = path.resolve()
    return target == results_root or results_root in target.parents


def to_visible_path(workspace_root: str, path: Path) -> str:
    """把物理路径转为模型/前端可见路径。"""
    root = Path(workspace_root).resolve()
    target = path.resolve()
    try:
        relative = target.relative_to(root).as_posix()
    except ValueError:
        return str(target)
    return f"/{relative}"


def resolve_output_skill_directory(workspace_root: str, path: str) -> Path:
    """Return a generated skill directory that is eligible for user import."""
    skill_dir = resolve_workspace_path(workspace_root, path)
    if is_tool_result_workspace_path(workspace_root, skill_dir):
        raise ValueError("不允许导入 outputs/.tool-results/ 下的内容")
    outputs_root = resolve_workspace_path(workspace_root, "outputs")
    try:
        relative = skill_dir.relative_to(outputs_root)
    except ValueError as exc:
        raise ValueError("只允许导入 outputs/<skill-name>/ 下的技能目录") from exc
    if len(relative.parts) != 1 or relative.name.startswith("."):
        raise ValueError("只允许导入 outputs/<skill-name>/ 下的技能目录")

    skill_md_path = skill_dir / "SKILL.md"
    if not skill_dir.is_dir() or skill_md_path.is_symlink() or not skill_md_path.is_file():
        raise ValueError("技能目录必须包含有效的 SKILL.md")
    artifact = parse_output_artifact(
        visible_path=f"/outputs/{relative.name}/SKILL.md",
        data=skill_md_path.read_bytes(),
    )
    if artifact.get("artifact_type") != "skill":
        raise ValueError("SKILL.md 元数据无效或与技能目录名不一致")
    return skill_dir


@dataclass
class WorkspacePathMapping:
    """虚拟路径 ↔ 物理路径映射，本地模式命令翻译与输出遮蔽的单一入口。

    虚拟前缀（白名单）：
      - 工作区：``/mnt/user-data/{workspace,outputs,uploads}`` 及其裸别名
        ``/workspace``、``/outputs``、``/uploads``
      - 已登记技能：``/mnt/skills/{system,user}/<name>/...``

    - ``translate``：正向，把文本里的虚拟路径翻成物理路径，交给本地 subprocess。
    - ``mask``：反向，把输出里的物理路径还原为虚拟路径，避免向模型泄漏宿主机路径。
      边界感知，单次替换，物理根内含 ``/uploads`` 等段也不会自引用膨胀。
    """

    workspace_root: str
    runtime_skills_dir: Path | None = None

    # 裸别名仅在路径边界处匹配：前置负向断言挡住 server/uploads 之类内部段，
    # 后置负向断言挡住 /workspaceX 误吞。与工作区虚拟前缀共用同一套边界规则。
    _ALIAS_RE = re.compile(r"(?<![\w/])/(workspace|outputs|uploads)(?![\w])")
    _VIRTUAL_RE = re.compile(r"/mnt/user-data/(workspace|outputs|uploads)(?![\w])")

    def __post_init__(self) -> None:
        root = self.workspace_root.rstrip("/")
        # 物理根绝不能包含虚拟前缀 /mnt/user-data/；若包含说明 CHAT_DATA_HOME
        # 被误配成 sandbox 挂载点路径，继续替换会导致路径指数膨胀。
        if "/mnt/user-data/" in root:
            raise ValueError(
                "workspace_root 配置异常：物理根不应包含 /mnt/user-data/，"
                "请检查 CHAT_DATA_HOME 环境变量是否误用了 sandbox 虚拟挂载点路径"
            )
        self._root = root
        self._physical_workspace = {
            "workspace": f"{root}/workspace",
            "outputs": f"{root}/outputs",
            "uploads": f"{root}/uploads",
        }
        # /mnt/skills/<name> -> 本会话快照目录/<name>；从快照目录直接枚举，无需逐技能登记。
        # runtime_skills_dir 为 None 表示本映射不绑定技能快照（仅工作区路径翻译）。
        self._skill_physical: dict[str, str] = {}
        skills_dir = self.runtime_skills_dir
        if skills_dir is not None and skills_dir.is_dir():
            for item in skills_dir.iterdir():
                if item.is_dir() and not item.is_symlink() and not item.name.startswith("."):
                    self._skill_physical[runtime_skill_visible_root(item.name)] = str(item)

    def translate(self, text: str) -> str:
        """正向：命令/文本中的虚拟路径 → 物理路径。"""
        if not text:
            return text
        out = text
        # 1) 裸别名规范化成 /mnt/user-data/ 形式（边界安全，不误伤物理根内部段）。
        out = self._ALIAS_RE.sub(r"/mnt/user-data/\1", out)
        # 2) 技能虚拟 → 物理（最长优先）。
        for virtual_root, physical in sorted(
            self._skill_physical.items(), key=lambda kv: len(kv[0]), reverse=True
        ):
            out = out.replace(virtual_root, physical)
        # 3) 工作区虚拟 → 物理（单次替换，root 不含 /mnt/user-data/，不自引用）。
        out = self._VIRTUAL_RE.sub(lambda m: f"{self._root}/{m.group(1)}", out)
        return out

    def mask(self, text: str) -> str:
        """反向：输出中的物理路径 → 虚拟路径，遮蔽宿主机绝对路径。"""
        if not text:
            return text
        out = text
        # 1) 技能物理 → 虚拟（最长优先）。
        for virtual_root, physical in sorted(
            self._skill_physical.items(), key=lambda kv: len(kv[0]), reverse=True
        ):
            if physical:
                out = out.replace(physical, virtual_root)
        # 2) 工作区物理 → 虚拟。
        for name in ("workspace", "outputs", "uploads"):
            out = out.replace(self._physical_workspace[name], f"/mnt/user-data/{name}")
        return out


def list_workspace_files(conversation_id: str, user_id: str = "system") -> list[dict[str, Any]]:
    """列出会话工作区中的可见文件，不跟随逃逸符号链接。"""
    paths = ensure_conversation_workspace(conversation_id, user_id)
    root = Path(paths["root"]).resolve()
    files: list[dict[str, Any]] = []
    for directory_name in WORKSPACE_DIR_NAMES:
        directory = root / directory_name
        if not directory.exists():
            continue
        for item in sorted(directory.rglob("*")):
            if not item.is_file():
                continue
            try:
                relative_path = item.relative_to(root).as_posix()
                resolved_item = resolve_workspace_path(str(root), relative_path)
                if is_tool_result_workspace_path(str(root), resolved_item):
                    continue
                stat = item.stat()
            except (OSError, ValueError):
                continue
            files.append({
                "path": to_visible_path(str(root), resolved_item),
                "relative_path": relative_path,
                "directory": directory_name,
                "name": item.name,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "source": directory_name,
            })
    return files


def read_workspace_text_file(
    conversation_id: str,
    path: str,
    max_bytes: int = 1024 * 1024,
    user_id: str = "system",
) -> dict[str, Any]:
    """读取会话工作区中的文本文件。"""
    paths = ensure_conversation_workspace(conversation_id, user_id)
    root = paths["root"]
    target = resolve_workspace_path(root, path)
    if is_tool_result_workspace_path(root, target):
        raise ValueError("工具结果缓存不在工作区文件预览范围内")
    if not target.exists() or not target.is_file():
        raise FileNotFoundError(f"文件不存在: {path}")
    size = target.stat().st_size
    if size > max_bytes:
        raise ValueError(f"文件过大，暂不支持预览超过 {max_bytes} 字节的文件")
    content = target.read_text(encoding="utf-8", errors="replace")
    return {
        "path": to_visible_path(root, target),
        "relative_path": target.relative_to(Path(root).resolve()).as_posix(),
        "content": content,
        "size": size,
    }


async def can_access_conversation_workspace(conversation_id: str, user_id: str) -> bool:
    """校验当前用户是否可访问 ChatData 会话工作区。"""
    if not str(conversation_id or "").strip() or not str(user_id or "").strip():
        return False
    return await conversation_dao.exists_user_agent_thread(
        conversation_id=conversation_id,
        user_id=user_id,
        agent_id=AGENT_ID,
    )


async def list_conversation_workspace_files(
    conversation_id: str,
    *,
    user_id: str,
) -> list[dict[str, Any]]:
    """读取当前用户会话的持久化工作区文件。"""
    return await asyncio.to_thread(list_workspace_files, conversation_id, user_id)


async def read_conversation_workspace_file(
    conversation_id: str,
    *,
    user_id: str,
    path: str,
    max_bytes: int = 1024 * 1024,
) -> dict[str, Any]:
    """读取当前用户会话持久化工作区中的文本文件。"""
    return await asyncio.to_thread(read_workspace_text_file, conversation_id, path, max_bytes, user_id)


async def read_conversation_query_result(
    conversation_id: str,
    *,
    user_id: str,
    result_id: str,
) -> dict[str, Any] | None:
    """读取当前用户当前会话内被卸载的大查询结果。"""
    return await asyncio.to_thread(
        ToolResultStore().load_query_result_for_conversation,
        result_id=result_id,
        conversation_id=conversation_id,
        user_id=user_id,
    )


_TEXT_READ_CHUNK_CHARS = 64 * 1024
_TEXT_LINE_SEPARATOR_RE = re.compile(r"[\n\v\f\x1c-\x1e\x85\u2028\u2029]")


def _iter_text_line_fragments(file_obj: Any):
    """逐块产出文本行片段，保持 ``str.splitlines()`` 的行分隔语义。

    ``newline=None`` 已将 CR / CRLF 标准化为 LF；其余 Python 识别的行分隔符
    仍需显式处理，避免大文件流式读取改变 ``offset`` 的按行语义。
    """
    while chunk := file_obj.read(_TEXT_READ_CHUNK_CHARS):
        position = 0
        for match in _TEXT_LINE_SEPARATOR_RE.finditer(chunk):
            yield chunk[position:match.start()], True
            position = match.end()
        if position < len(chunk):
            yield chunk[position:], False


def _ensure_workspace_root_in_chat_data_home(workspace_root: str) -> None:
    """确保服务端直接读取的会话根仍属于 ChatData 共享数据目录。"""
    root = Path(CHAT_DATA_HOME).expanduser().resolve()
    target = Path(workspace_root).expanduser().resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"会话工作区不在 CHAT_DATA_HOME 内: {workspace_root}")


def _allocate_longest_line_budgets(line_lengths: list[int], max_chars: int) -> list[int]:
    """在正文预算内保留同页短行，优先截断最长源行。"""
    budgets = [max(0, int(length)) for length in line_lengths]
    excess = max(0, sum(budgets) - max(0, int(max_chars)))
    for index in sorted(range(len(budgets)), key=lambda item: (-budgets[item], item)):
        removed = min(budgets[index], excess)
        budgets[index] -= removed
        excess -= removed
        if not excess:
            break
    return budgets


def _scan_text_page_line_lengths(path: Path, *, offset: int, limit: int, char_offset: int) -> dict[str, Any]:
    selected_lines: list[dict[str, int]] = []
    line_index = 0
    pending_chars = 0

    def finish_line() -> bool:
        nonlocal line_index, pending_chars
        if offset <= line_index < offset + limit:
            line_char_offset = char_offset if line_index == offset else 0
            selected_lines.append({
                "line_number": line_index + 1,
                "original_chars": pending_chars,
                "char_offset": line_char_offset,
                "available_chars": max(0, pending_chars - line_char_offset),
            })
        elif line_index >= offset + limit:
            return True
        line_index += 1
        pending_chars = 0
        return False

    with path.open("r", encoding="utf-8", errors="replace", newline=None) as file_obj:
        for part, ends_line in _iter_text_line_fragments(file_obj):
            pending_chars += len(part)
            if ends_line and finish_line():
                return {"selected_lines": selected_lines, "has_more_lines": True}
            if len(selected_lines) == limit and pending_chars:
                return {"selected_lines": selected_lines, "has_more_lines": True}
    if pending_chars:
        if finish_line():
            return {"selected_lines": selected_lines, "has_more_lines": True}

    return {"selected_lines": selected_lines, "has_more_lines": False, "observed_lines": line_index}


def _read_text_page_prefixes(path: Path, selected_lines: list[dict[str, int]], budgets: list[int]) -> list[str]:
    selected_by_index = {
        item["line_number"] - 1: (position, item)
        for position, item in enumerate(selected_lines)
    }
    captured_parts = [[] for _ in selected_lines]
    captured_chars = [0 for _ in selected_lines]
    current_line_index = 0
    current_line_char = 0
    last_line_index = selected_lines[-1]["line_number"] - 1

    def consume(part: str) -> None:
        nonlocal current_line_char
        selected = selected_by_index.get(current_line_index)
        if selected is not None:
            position, metadata = selected
            available_start = metadata["char_offset"]
            start = max(0, available_start - current_line_char)
            remaining = budgets[position] - captured_chars[position]
            if start < len(part) and remaining > 0:
                piece = part[start:start + remaining]
                captured_parts[position].append(piece)
                captured_chars[position] += len(piece)
        current_line_char += len(part)

    def finish_line() -> bool:
        nonlocal current_line_index, current_line_char
        current_line_index += 1
        current_line_char = 0
        return current_line_index > last_line_index

    with path.open("r", encoding="utf-8", errors="replace", newline=None) as file_obj:
        for part, ends_line in _iter_text_line_fragments(file_obj):
            consume(part)
            if ends_line and finish_line():
                return ["".join(parts) for parts in captured_parts]
    return ["".join(parts) for parts in captured_parts]


def _read_text_page_from_path(
    target: Path,
    visible_path: str,
    *,
    offset: int,
    limit: int,
    char_offset: int,
    max_chars: int,
) -> dict[str, Any]:
    if not target.exists():
        return {"exists": False, "path": visible_path}
    if target.is_dir():
        return {"exists": True, "is_dir": True, "path": visible_path}
    with target.open("rb") as file_obj:
        sample = file_obj.read(4096)
    if b"\x00" in sample:
        return {"exists": True, "is_binary": True, "path": visible_path}

    page = _scan_text_page_line_lengths(target, offset=offset, limit=limit, char_offset=char_offset)
    selected_lines = list(page["selected_lines"])
    if not selected_lines:
        observed_lines = int(page.get("observed_lines") or 0)
        return {
            "exists": True,
            "is_dir": False,
            "is_binary": False,
            "path": visible_path,
            "total_lines": observed_lines,
            "empty": observed_lines == 0,
            "offset_out_of_range": observed_lines > 0,
        }
    first_line = selected_lines[0]
    if char_offset and char_offset >= first_line["original_chars"]:
        return {
            "exists": True,
            "is_dir": False,
            "is_binary": False,
            "path": visible_path,
            "char_offset_out_of_range": True,
            "line_number": first_line["line_number"],
            "line_chars": first_line["original_chars"],
        }

    budgets = _allocate_longest_line_budgets(
        [item["available_chars"] for item in selected_lines],
        max(0, max_chars - max(0, len(selected_lines) - 1)),
    )
    lines = _read_text_page_prefixes(target, selected_lines, budgets)
    truncated_lines = [
        {
            "line_number": item["line_number"],
            "original_chars": item["original_chars"],
            "returned_chars": budget,
            "next_char_offset": item["char_offset"] + budget,
        }
        for item, budget in zip(selected_lines, budgets)
        if budget < item["available_chars"]
    ]
    has_more_lines = bool(page["has_more_lines"])
    return {
        "exists": True,
        "is_dir": False,
        "is_binary": False,
        "path": visible_path,
        "lines": lines,
        "total_lines": None,
        "start_line": selected_lines[0]["line_number"],
        "end_line": selected_lines[-1]["line_number"],
        # 长行续读只补齐同一行，不能基于该请求推导后续页，避免回退到已读行。
        "next_offset": offset + len(selected_lines) if has_more_lines and not char_offset else None,
        "has_more_lines": has_more_lines,
        "has_more": has_more_lines or bool(truncated_lines),
        "truncated_lines": truncated_lines,
    }


def _read_local_text_file(
    workspace_root: str,
    file_path: str,
    offset: int,
    limit: int,
    char_offset: int,
    max_chars: int,
) -> dict[str, Any]:
    target = resolve_workspace_path(workspace_root, file_path)
    return _read_text_page_from_path(
        target,
        to_visible_path(workspace_root, target),
        offset=offset,
        limit=limit,
        char_offset=char_offset,
        max_chars=max_chars,
    )


def _read_local_runtime_skill_file(
    target: Path,
    visible_path: str,
    offset: int,
    limit: int,
    char_offset: int,
    max_chars: int,
) -> dict[str, Any]:
    return _read_text_page_from_path(
        target,
        visible_path,
        offset=offset,
        limit=limit,
        char_offset=char_offset,
        max_chars=max_chars,
    )


def _write_local_text_file(workspace_root: str, file_path: str, content: str, mode: str) -> dict[str, Any]:
    target = resolve_workspace_path(workspace_root, file_path, writable=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w" if mode == "overwrite" else "a", encoding="utf-8") as file_obj:
        file_obj.write(content)
    return {
        "operation": "write",
        "file_path": to_visible_path(workspace_root, target),
        "mode": mode,
        "bytes": len(content.encode("utf-8")),
    }


async def workspace_read_text_file(
    runtime: ConversationWorkspaceRuntime,
    *,
    file_path: str,
    offset: int,
    limit: int,
    char_offset: int,
    max_chars: int,
) -> dict[str, Any]:
    raw_path = str(file_path or "").strip()
    if raw_path.startswith(f"{SKILLS_VIRTUAL_ROOT}/"):
        target, visible_path = await runtime.resolve_readonly_skill_path_async(file_path)
        return await asyncio.to_thread(
            _read_local_runtime_skill_file,
            target,
            visible_path,
            offset,
            limit,
            char_offset,
            max_chars,
        )
    _ensure_workspace_root_in_chat_data_home(runtime.workspace_root)
    return await asyncio.to_thread(
        _read_local_text_file,
        runtime.workspace_root,
        file_path,
        offset,
        limit,
        char_offset,
        max_chars,
    )


async def workspace_write_text_file(
    runtime: ConversationWorkspaceRuntime,
    *,
    file_path: str,
    content: str,
    mode: str,
) -> dict[str, Any]:
    if runtime.backend == "local":
        return await asyncio.to_thread(
            _write_local_text_file,
            runtime.workspace_root,
            file_path,
            content,
            mode,
        )
    return await sandbox_write_text_file(
        session_ref=await runtime.get_sandbox_session_ref(),
        file_path=file_path,
        content=content,
        mode=mode,
    )


async def workspace_apply_patch_ops(
    runtime: ConversationWorkspaceRuntime,
    *,
    ops: list[dict[str, Any]],
    dry_run: bool,
    local_apply: Any,
) -> list[str]:
    if runtime.backend == "local":
        return await asyncio.to_thread(
            local_apply,
            ops,
            root=runtime.workspace_root,
            dry_run=dry_run,
        )
    return await sandbox_apply_patch_ops(
        session_ref=await runtime.get_sandbox_session_ref(),
        ops=ops,
        dry_run=dry_run,
    )


async def workspace_artifact_payload(
    runtime: ConversationWorkspaceRuntime,
    *,
    file_path: str,
    local_loader: Any,
) -> dict[str, Any]:
    try:
        target = resolve_workspace_path(runtime.workspace_root, file_path)
    except ValueError:
        return {}
    if is_tool_result_workspace_path(runtime.workspace_root, target):
        return {}
    if runtime.backend == "local":
        return await asyncio.to_thread(local_loader, runtime.workspace_root, [file_path])
    return await sandbox_artifact_payload(
        session_ref=await runtime.get_sandbox_session_ref(),
        file_path=file_path,
    )
