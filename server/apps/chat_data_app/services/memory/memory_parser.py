"""会话记忆条目的解析、规整与渲染（纯函数层，无 IO、无配置依赖）。

条目行格式统一为 ``- [类别] 结论（适用范围）``，范围后缀可省略：
``- [口径] 销售额含税（仅订单分析）``。
文档首行 ``#`` 开头为标题行，解析时忽略；条目在文档中的顺序即新旧顺序
（越靠后越新），淘汰与预算裁剪均以「保留最新」为准。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

MEMORY_ITEM_LINE_RE = re.compile(r"^- \[(?P<category>[^\[\]]+)\]\s?(?P<body>.+)$")

# 单会话记忆条目上限：与注入预算共同约束 system prompt 体积
MAX_MEMORY_ITEMS = 20
MAX_ITEM_CONTENT_CHARS = 160
MAX_CATEGORY_CHARS = 12
MAX_SCOPE_CHARS = 40


@dataclass(frozen=True)
class MemoryItem:
    """一条会话记忆。"""

    category: str
    content: str
    scope: str = ""
    # 本次抽取相对已有记忆的覆盖关系；不落盘，只在合并时消费。
    supersedes: tuple[str, ...] = ()

    def to_line(self) -> str:
        scope_suffix = f"（{self.scope}）" if self.scope else ""
        return f"- [{self.category}] {self.content}{scope_suffix}"


def normalize_memory_text(text: str, max_chars: int) -> str:
    """单行化（折叠全部空白）并截断到上限。"""
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    return normalized[: max(int(max_chars), 0)]


def _memory_item_key(item: MemoryItem) -> str:
    """合并去重键：折叠空白差异，同义空白变体视为同一条。"""
    return " ".join(f"{item.category} {item.content} {item.scope}".split())


def normalize_memory_items(
    items: Iterable[MemoryItem],
    *,
    max_items: int = MAX_MEMORY_ITEMS,
) -> list[MemoryItem]:
    """批量规整：逐字段限长、跳过缺类别或缺结论的条目、批内去重并封顶（保留最新）。"""
    normalized: list[MemoryItem] = []
    for item in items:
        category = normalize_memory_text(item.category, MAX_CATEGORY_CHARS)
        content = normalize_memory_text(item.content, MAX_ITEM_CONTENT_CHARS)
        scope = normalize_memory_text(item.scope, MAX_SCOPE_CHARS)
        if not category or not content:
            continue
        supersedes = tuple(
            text for raw in item.supersedes if (text := normalize_memory_text(raw, MAX_ITEM_CONTENT_CHARS))
        )
        candidate = MemoryItem(
            category=category,
            content=content,
            scope=scope,
            supersedes=supersedes,
        )
        if any(_memory_item_key(existing) == _memory_item_key(candidate) for existing in normalized):
            continue
        normalized.append(candidate)
    limit = max(int(max_items), 0)
    return normalized[-limit:] if limit else []


def _split_scope(body: str) -> tuple[str, str]:
    """把 ``结论（适用范围）`` 拆成 (结论, 范围)；无合法范围后缀时整体视为结论。"""
    open_index = body.rfind("（")
    if open_index <= 0 or not body.endswith("）"):
        return body, ""
    content = body[:open_index].strip()
    scope = body[open_index + 1 : -1].strip()
    if not content:
        return body, ""
    return content, scope


def parse_memory_item_line(line: str) -> MemoryItem | None:
    """解析单行条目；不符合格式的行返回 None。"""
    match = MEMORY_ITEM_LINE_RE.match(line.strip())
    if not match:
        return None
    category = match.group("category").strip()
    content, scope = _split_scope(match.group("body").strip())
    if not category or not content:
        return None
    return MemoryItem(category=category, content=content, scope=scope)


def parse_memory_document(text: str) -> list[MemoryItem]:
    """解析记忆文档为条目列表；标题行与空行忽略，重复条目去重。"""
    items: list[MemoryItem] = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        item = parse_memory_item_line(stripped)
        if item is not None and item not in items:
            items.append(item)
    return items


def enforce_memory_item_limit(items: Sequence[MemoryItem], *, max_items: int) -> list[MemoryItem]:
    """条目封顶：超出时淘汰最早的条目，至少保留 1 条。"""
    limit = max(int(max_items), 1)
    return list(items[-limit:])


def _normalize_update_target(text: str) -> str:
    """删除/覆盖目标的比较键：折叠空白，保留用户可见文本。"""
    return normalize_memory_text(text, MAX_ITEM_CONTENT_CHARS)


def _matches_update_target(item: MemoryItem, targets: set[str]) -> bool:
    """删除/覆盖目标匹配 content 或完整条目标识，兼容用户只给结论。"""
    return _normalize_update_target(item.content) in targets or _normalize_update_target(item.to_line()) in targets


def apply_memory_updates(
    existing: Sequence[MemoryItem],
    new_items: Sequence[MemoryItem],
    removals: Iterable[str] = (),
) -> list[MemoryItem]:
    """应用删除与覆盖：先删旧条目，再追加新条目，最后按 content/scope 去重。

    ``new_items[*].supersedes`` 与顶层 ``removals`` 都按 content 或完整条目行匹配；
    写入文件时不保留 ``supersedes``，它只是本次合并的指令。
    """
    removal_targets = {target for raw in removals if (target := _normalize_update_target(raw))}
    superseded_targets = {
        target for item in new_items for raw in item.supersedes if (target := _normalize_update_target(raw))
    }
    targets = removal_targets | superseded_targets
    kept = [item for item in existing if not _matches_update_target(item, targets)]
    merged: list[MemoryItem] = []
    seen: set[str] = set()
    for item in [*kept, *new_items]:
        stored = MemoryItem(category=item.category, content=item.content, scope=item.scope)
        key = " ".join(f"{stored.category} {stored.content} {stored.scope}".split())
        if key in seen:
            continue
        seen.add(key)
        merged.append(stored)
    return merged


def render_memory_document(items: Sequence[MemoryItem]) -> str:
    """渲染记忆文档：逐条条目（每条一行）。

    不写标题行：解析方本就忽略 ``#`` 行，标题对解析与注入零作用，对在
    面板预览该文件的用户只是噪音；历史文件中可能存在的旧标题行由解析端
    的 ``#`` 行跳过逻辑兜底。
    """
    return "\n".join(item.to_line() for item in items) + "\n"


def escape_memory_for_prompt(text: str) -> str:
    """转义注入提示词的条目文本，防止伪造提示词结构。"""
    return str(text or "").replace("<", "＜").replace(">", "＞").replace("`", "ˋ")


def select_items_within_budget(
    items: Sequence[MemoryItem],
    *,
    max_tokens: int,
) -> list[MemoryItem]:
    """按注入预算从最新往回保留条目（1 token≈1 字符，含换行成本）。

    最新一条即使超预算也保留（floor=1），保证有记忆时不出现空注入。
    """
    budget = max(int(max_tokens), 0)
    selected: list[MemoryItem] = []
    used = 0
    for item in reversed(items):
        cost = len(item.to_line()) + 1
        if selected and used + cost > budget:
            break
        selected.append(item)
        used += cost
    selected.reverse()
    return selected
