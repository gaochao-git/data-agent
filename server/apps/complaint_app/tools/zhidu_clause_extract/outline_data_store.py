from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency
    load_dotenv = None

SERVER_ROOT = Path(__file__).resolve().parents[4]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from utils.logger import get_logger


logger = get_logger(__name__)
_MERGED_OUTLINE_CACHE: list[dict[str, Any]] | None = None
_MERGED_OUTLINE_INDEX_BY_TITLE: dict[str, dict[str, Any]] | None = None
_MERGED_OUTLINE_PATH_CACHE: Path | None = None
_DEFAULT_MOCK_PATH = Path(__file__).resolve().parent / "examples" / "merged_outline.mock.json"


def load_env_files() -> None:
    if load_dotenv is None:
        return
    load_dotenv()
    server_env = SERVER_ROOT / ".env"
    if server_env.is_file():
        load_dotenv(server_env, override=False)


def get_merged_outline_json_path() -> Path:
    load_env_files()
    configured = str(os.getenv("ZHIDU_MERGED_OUTLINE_JSON") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return _DEFAULT_MOCK_PATH.resolve()


def read_merged_outline_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"merged_outline.json 不存在: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"merged_outline.json 顶层必须是数组: {path}")
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            logger.warning("merged_outline 第 %s 项不是对象，已跳过 path=%s", index, path)
            continue
        title = str(item.get("制度名称") or "").strip()
        if not title:
            logger.warning("merged_outline 第 %s 项缺少 制度名称，已跳过 path=%s", index, path)
            continue
        rows.append(item)
    return rows


def build_outline_index_by_title(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        title = str(row.get("制度名称") or "").strip()
        if title in index:
            logger.warning("merged_outline 存在重复 制度名称，后者覆盖前者 title=%s", title)
        index[title] = row
    return index


def load_merged_outline_rows(force_reload: bool = False) -> list[dict[str, Any]]:
    global _MERGED_OUTLINE_CACHE, _MERGED_OUTLINE_INDEX_BY_TITLE, _MERGED_OUTLINE_PATH_CACHE  # pylint: disable=global-statement
    if _MERGED_OUTLINE_CACHE is not None and not force_reload:
        return _MERGED_OUTLINE_CACHE
    path = get_merged_outline_json_path()
    rows = read_merged_outline_rows(path)
    _MERGED_OUTLINE_CACHE = rows
    _MERGED_OUTLINE_INDEX_BY_TITLE = build_outline_index_by_title(rows)
    _MERGED_OUTLINE_PATH_CACHE = path
    logger.info("merged_outline 已加载到内存 path=%s count=%s", path, len(rows))
    return rows


def load_merged_outline_index_by_title(force_reload: bool = False) -> dict[str, dict[str, Any]]:
    global _MERGED_OUTLINE_INDEX_BY_TITLE  # pylint: disable=global-statement
    if _MERGED_OUTLINE_INDEX_BY_TITLE is not None and not force_reload:
        return _MERGED_OUTLINE_INDEX_BY_TITLE
    load_merged_outline_rows(force_reload=force_reload)
    return _MERGED_OUTLINE_INDEX_BY_TITLE or {}


def get_loaded_merged_outline_path() -> Path:
    if _MERGED_OUTLINE_PATH_CACHE is not None:
        return _MERGED_OUTLINE_PATH_CACHE
    return get_merged_outline_json_path()
