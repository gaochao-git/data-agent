from __future__ import annotations

import os
import re
import json
import time
import threading
from typing import Optional
from utils.storage_paths import COMPLAINTS_BASE_DIR
from utils.logger import get_logger
COMPLAINT_EXPERT_EXPERIENCE = os.path.join(COMPLAINTS_BASE_DIR, "expert_experience", "complaint-analysis-uploaded")
logger = get_logger(__name__)


def load_latest_expert_experience_json(base_dir: Optional[str] = COMPLAINT_EXPERT_EXPERIENCE) -> str:
    """
    从本地 COMPLAINT_EXPERT_EXPERIENCE 目录（默认 DATA_ROOT/complaints/expert_experience/complaint-analysis-uploaded）中选择最新的一个子文件夹，
    读取其中的 parsed.json 文件并返回其内容的 JSON 字符串。

    - 子文件夹命名格式: YYYYMMDD_HHMMSS_<random>
    - 若路径或文件不存在，返回空字符串
    - 若文件内容不是合法 JSON，返回原始文本
    """
    try:
        # 使用传入或默认目录 COMPLAINT_EXPERT_EXPERIENCE
        if not os.path.isdir(base_dir):
            logger.info("expert_experience base_dir not found: %s", base_dir)
            return ""

        # 找到所有子目录
        subdirs = [d for d in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, d))]
        logger.info("expert_experience subdirs: %s", subdirs)
        if not subdirs:
            return ""

        # 使用命名中的时间戳排序：YYYYMMDD_HHMMSS_random
        pattern = re.compile(r"^(\d{8})_(\d{6})_.+")

        def sort_key(name: str):
            m = pattern.match(name)
            if m:
                ymd = m.group(1)
                hms = m.group(2)
                # 直接拼接为可比较的字符串
                return f"{ymd}{hms}"
            # 回退到修改时间
            return str(os.path.getmtime(os.path.join(base_dir, name)))
        # logger.info("expert_experience pattern: %s", pattern.pattern)
        latest_subdir = sorted(subdirs, key=sort_key, reverse=True)[0]
        parsed_path = os.path.join(base_dir, latest_subdir, "parsed.json")
        # logger.info("expert_experience parsed_path: %s", parsed_path)
        if not os.path.isfile(parsed_path):
            return ""

        with open(parsed_path, "r", encoding="utf-8") as f:
            raw = f.read().strip()
            # 尝试解析为 JSON，保证返回是 JSON 字符串
            try:
                obj = json.loads(raw)
                return json.dumps(obj, ensure_ascii=False)
            except Exception:
                # 解析失败则视为无数据
                return ""
    except Exception:
        # 不抛出异常，避免影响主流程
        return ""


_cache_lock = threading.Lock()
_cache_state = {"value": "", "ts": 0.0}

def get_expert_experience_cached(base_dir: Optional[str] = COMPLAINT_EXPERT_EXPERIENCE, ttl_seconds: int = 60, force: bool = False) -> str:
    """
    读取专家经验数据，带简单TTL缓存（默认60秒）。
    - 多线程安全；异常时返回空字符串。
    """
    try:
        now = time.time()
        with _cache_lock:
            expired = (now - _cache_state["ts"]) >= ttl_seconds
            if force or expired or not _cache_state["value"]:
                logger.info("expert_experience base_dir: %s", base_dir)
                value = load_latest_expert_experience_json(base_dir) or ""
                _cache_state["value"] = value
                _cache_state["ts"] = now
            return _cache_state["value"]
    except Exception:
        return ""

def find_expert_experience_for_second_category(second_category: str) -> dict | None:
    """Return an expert experience record whose second_category matches the provided value."""
    if not second_category:
        logger.info("second_category is empty: %s", second_category)
        return None

    target_value = second_category.strip()
    if not target_value:
        logger.info("second_category is blank after stripping: %s", second_category)
        return None

    experience_raw = get_expert_experience_cached(ttl_seconds=60)
    if not experience_raw:
        logger.info("experience_raw is empty for second_category=%s", second_category)
        return None
    try:
        experience_data = json.loads(experience_raw)
    except json.JSONDecodeError as exc:
        logger.error("Failed to decode experience data for second_category=%s: %s", second_category, exc)
        return None

    match_entry = None
    first_category = None

    def _search(obj, parent_category=None):
        nonlocal match_entry, first_category

        if match_entry is not None:
            return True

        if isinstance(obj, dict):
            second_value = str(obj.get("second_category", "")).strip()
            overview_value = str(obj.get("overview", "")).strip()
            if second_value == target_value or overview_value == target_value:
                match_entry = dict(obj)
                first_category = parent_category
                return True
            for value in obj.values():
                if _search(value, parent_category):
                    return True

        elif isinstance(obj, list):
            for item in obj:
                if _search(item, parent_category):
                    return True

        return False

    if isinstance(experience_data, dict):
        for key, value in experience_data.items():
            parent = str(key).strip() if key else None
            if _search(value, parent):
                break
    else:
        _search(experience_data, None)

    if not match_entry:
        return None

    if first_category and "first_category" not in match_entry:
        match_entry["first_category"] = first_category
    return match_entry


if __name__ == "__main__":
    logger.info("expert experience data: %s", get_expert_experience_cached())
