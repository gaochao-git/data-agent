from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Callable

from apps.complaint_app.experience_loader import find_expert_experience_for_second_category
from apps.complaint_app.history_storage import load_user_history, relative_to_complaints
from utils.logger import get_logger
from utils.storage_paths import get_complaints_real_path
from apps.complaint_app.core.versioning import stage_dir

logger = get_logger(__name__)

_CHINESE_DIGITS = {
    0: "零",
    1: "一",
    2: "二",
    3: "三",
    4: "四",
    5: "五",
    6: "六",
    7: "七",
    8: "八",
    9: "九",
}


def _number_to_chinese(num: int) -> str:
    if num <= 0:
        return ""
    if num < 11:
        return {10: "十", **_CHINESE_DIGITS}.get(num, str(num))
    if num < 20:
        return "十" + (_CHINESE_DIGITS.get(num % 10, str(num % 10)) if num % 10 else "")
    if num < 100:
        tens = num // 10
        ones = num % 10
        result = _CHINESE_DIGITS.get(tens, str(tens)) + "十"
        if ones:
            result += _CHINESE_DIGITS.get(ones, str(ones))
        return result
    if num < 1000:
        hundreds = num // 100
        remainder = num % 100
        result = _CHINESE_DIGITS.get(hundreds, str(hundreds)) + "百"
        if remainder == 0:
            return result
        if remainder < 10:
            return result + "零" + _CHINESE_DIGITS.get(remainder, str(remainder))
        return result + _number_to_chinese(remainder)
    if num < 10000:
        thousands = num // 1000
        remainder = num % 1000
        result = _CHINESE_DIGITS.get(thousands, str(thousands)) + "千"
        if remainder == 0:
            return result
        if remainder < 100:
            return result + "零" + _number_to_chinese(remainder)
        return result + _number_to_chinese(remainder)
    return str(num)


def _format_item_label(item_id: str) -> str:
    if not item_id:
        return "该事项"

    parts = str(item_id).split("_", 1)
    if len(parts) == 2 and parts[1].isdigit():
        prefix, index_str = parts
        try:
            index = int(index_str)
        except ValueError:
            index = None
        label_map = {
            "complaint": "举报事项",
            "appeal": "诉求",
        }
        item_label = label_map.get(prefix, "事项")
        if index:
            chinese_index = _number_to_chinese(index)
            if chinese_index:
                return f"第{chinese_index}个{item_label}"
        return f"{item_label} {index_str}"
    return f"事项 {item_id}"


def load_latest_process_items_result(user_id: str, base_id: str) -> dict | None:
    """Pick the newest process_items_sse result.json for the given base_id."""
    if not base_id:
        return None

    try:
        process_root = get_complaints_real_path(user_id, base_id, "process_items_sse")
        if not os.path.isdir(process_root):
            return None

        candidate_dirs = [
            os.path.join(process_root, entry)
            for entry in os.listdir(process_root)
            if os.path.isdir(os.path.join(process_root, entry))
        ]
        if not candidate_dirs:
            return None

        latest_dir = max(candidate_dirs, key=os.path.getmtime)
        result_path = os.path.join(latest_dir, "result.json")
        if not os.path.isfile(result_path):
            return None

        with open(result_path, "r", encoding="utf-8") as result_file:
            return json.load(result_file)
    except Exception:
        return None


def build_expert_experience_entries(result_data: dict | None) -> list[dict] | None:
    """Transform the process_items_sse result into the expected experience array."""
    if not isinstance(result_data, dict):
        return None

    items = result_data.get("items")
    if not isinstance(items, dict):
        return None

    key_map = {
        "complaint": "举报事项",
        "appeal": "诉求",
    }

    def _item_sort_key(item_id: str) -> tuple[int, float, str]:
        parts = str(item_id).split("_", 1)
        prefix = parts[0]
        if prefix == "complaint":
            order = 0
        elif prefix == "appeal":
            order = 1
        else:
            order = 2

        index_value = float("inf")
        if len(parts) == 2:
            try:
                index_value = int(parts[1])
            except ValueError:
                pass

        return order, index_value, item_id

    entries: list[dict] = []
    sorted_items = sorted(items.items(), key=lambda pair: _item_sort_key(pair[0]))

    for item_id, item_info in sorted_items:
        logger.debug("生成经验条目时处理 item_id=%s", item_id)
        if not isinstance(item_info, dict):
            continue

        item_type = str(item_info.get("item_type") or "").lower()
        base_label = key_map.get(item_type) or _format_item_label(item_id)
        label_index = ""
        parts = str(item_id).split("_", 1)
        if len(parts) == 2 and parts[1].isdigit():
            label_index = parts[1]

        is_appeal = item_type == "appeal"
        content_value = (
            item_info.get("content")
            or item_info.get("appeal_desc")
            or item_info.get("complaint_desc")
            or item_info.get("reporting_content")
            or ""
        )

        entry_payload = {}

        if not is_appeal:
            category_with_knowledge = item_info.get("category_with_knowledge") or {}
            second_category = category_with_knowledge.get("second_category") or ""
            if second_category:
                match_entry = find_expert_experience_for_second_category(second_category) or {}
                in_scope = match_entry.get("in_scope")
                if in_scope == "是":
                    entry_payload = {
                        "举报事项内容": content_value,
                        "参考分类": {
                            "一级分类": category_with_knowledge.get("first_category") or "",
                            "二级分类": category_with_knowledge.get("second_category") or "",
                            "二级分类定义": match_entry.get("second_category_description") or "",
                            "一级分类定义": match_entry.get("first_category_description") or "",
                            "名词解释": match_entry.get("glossary") or "",
                            "对应专家经验": {
                                "法律依据": category_with_knowledge.get("law_list") or [],
                                "核实要点": match_entry.get("verification_points") or "",
                                "调阅清单": match_entry.get("checklist") or "",
                                "核实事项": match_entry.get("decomposition") or "",
                                "注意事项": match_entry.get("notes") or "",
                                "处理建议": match_entry.get("suggestions") or "",
                            }
                        }
                    }
                else:
                    entry_payload = {
                        "举报事项内容": content_value,
                        "参考分类": {
                            "一级分类": category_with_knowledge.get("first_category") or "",
                            "二级分类": category_with_knowledge.get("second_category") or "",
                            "二级分类定义": match_entry.get("second_category_description") or "",
                            "一级分类定义": match_entry.get("first_category_description") or "",
                            "名词解释": match_entry.get("glossary") or "",
                            "对应专家经验": {
                                "是否属于支付部门核实范围": "否",
                                "处理建议": match_entry.get("suggestions") or "",
                            }
                        }
                    }
            else:
                entry_payload = {
                    "举报事项内容": content_value,
                    "参考分类": "未找到参考分类"
                }
        else:
            entry_payload = {
                "诉求内容": content_value,
                "答复建议": item_info.get("res_sq")
            }
        entries.append({f"{base_label} {label_index}": entry_payload})
    return entries or []


def _safe_read_text(path: str) -> str:
    if not path or not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except Exception:
        return ""


def _parse_markdown_table_line(line: str) -> list[str]:
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    return [cell.strip() for cell in text.split("|")]


def _is_separator_line(line: str) -> bool:
    cells = _parse_markdown_table_line(line)
    if len(cells) < 2:
        return False
    return all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells)


def _extract_tables_from_markdown(markdown: str | None) -> list[dict]:
    if not markdown or not markdown.strip():
        return []

    lines = markdown.split("\n")
    tables: list[dict] = []
    i = 0

    while i < len(lines) - 1:
        header = lines[i]
        separator = lines[i + 1]
        if "|" not in header or not _is_separator_line(separator):
            i += 1
            continue

        header_cells = _parse_markdown_table_line(header)
        if len(header_cells) < 2:
            i += 1
            continue

        rows = [header_cells]
        j = i + 2
        while j < len(lines):
            current = lines[j]
            if "|" not in current:
                break
            rows.append(_parse_markdown_table_line(current))
            j += 1

        # 过滤掉完全空行
        normalized_rows = []
        max_len = max((len(row) for row in rows), default=0)
        for row in rows:
            padded = row + [""] * (max_len - len(row))
            if normalized_rows and not any(cell for cell in padded):
                continue
            normalized_rows.append(padded)

        if not normalized_rows:
            i = j
            continue

        block_md = "\n".join(lines[i:j]).strip()
        title = header_cells[0] or f"表格 {len(tables) + 1}"
        tables.append(
            {
                "id": f"table-{len(tables) + 1}",
                "category": title,
                "title": title,
                "markdown": block_md,
                "rows": normalized_rows,
                "source": "markdown_fallback",
            }
        )
        i = j

    return tables


def _ensure_summary_tables_from_markdown(manifest: dict) -> None:
    """When summary_tables is missing/empty, try parsing tables from summary markdown."""
    if not isinstance(manifest, dict):
        return

    def _populate(section: dict | None):
        if not isinstance(section, dict):
            return
        existing_tables = section.get("summary_tables") or (section.get("summary") or {}).get("tables")
        if existing_tables:
            return
        markdown = section.get("summary_markdown") or (section.get("summary") or {}).get("content")
        tables = _extract_tables_from_markdown(markdown)
        if not tables:
            return
        section["summary_tables"] = tables
        if isinstance(section.get("summary"), dict):
            section["summary"]["tables"] = tables

    _populate(manifest.get("task2"))
    _populate(manifest.get("final_result"))


def _load_versioned_artifacts(user_id: str, base_id: str, manifest: dict) -> dict:
    """
    尝试从新版按版本落盘的目录结构中提取原文、摘要、事项等信息，
    组装成旧版 manifest 兼容的字段，便于前端复用。
    """
    compat: dict = {}

    # 原始文件信息
    original_file_meta = manifest.get("original_file") if isinstance(manifest.get("original_file"), dict) else {}
    compat_original: dict = dict(original_file_meta) if isinstance(original_file_meta, dict) else {}
    try:
        original_dir = get_complaints_real_path(user_id, base_id, "original")
        original_name = None
        if os.path.isdir(original_dir):
            candidates = [f for f in os.listdir(original_dir) if os.path.isfile(os.path.join(original_dir, f))]
            if candidates:
                original_name = sorted(candidates)[0]
        if original_name:
            original_path = os.path.join(original_dir, original_name)
            size_bytes = None
            try:
                size_bytes = os.path.getsize(original_path)
            except Exception:
                pass
            compat_original.setdefault("stored_name", original_name)
            compat_original.setdefault("uploaded_name", compat_original.get("stored_name") or original_name)
            compat_original.setdefault("path", relative_to_complaints(original_path))
            compat_original.setdefault("url", f"/api/files/{user_id}/{base_id}/original/{original_name}")
            if compat_original.get("size_bytes") is None:
                compat_original["size_bytes"] = size_bytes
    except Exception:
        pass
    if compat_original:
        compat["original_file"] = compat_original

    # 文本版本
    text_version = int(manifest.get("text", {}).get("current") or 0) if isinstance(manifest.get("text"), dict) else 0
    text_dir = stage_dir(user_id, base_id, "text", text_version) if text_version else None
    text_raw_path = os.path.join(text_dir, "final.txt") if text_dir else None
    text_map_path = os.path.join(text_dir, "final_mapping.json") if text_dir else None
    text_meta_path = os.path.join(text_dir, "meta.json") if text_dir else None
    text_updated_at = None
    try:
        if text_meta_path and os.path.isfile(text_meta_path):
            with open(text_meta_path, "r", encoding="utf-8") as handle:
                text_meta = json.load(handle)
            text_updated_at = text_meta.get("ts")
    except Exception:
        text_updated_at = None
    text_content = _safe_read_text(text_raw_path) if text_raw_path else ""
    if text_raw_path:
        compat["text_files"] = {
            "raw": relative_to_complaints(text_raw_path),
            "mapping": relative_to_complaints(text_map_path) if text_map_path and os.path.isfile(text_map_path) else None,
            "updated_at": text_updated_at,
        }

    # 摘要版本
    summary_version = int(manifest.get("summary", {}).get("current") or 0) if isinstance(manifest.get("summary"), dict) else 0
    summary_dir = stage_dir(user_id, base_id, "summary", summary_version) if summary_version else None
    summary_md_path = os.path.join(summary_dir, "final.md") if summary_dir else None
    summary_json_path = os.path.join(summary_dir, "final.json") if summary_dir else None
    summary_meta_path = os.path.join(summary_dir, "meta.json") if summary_dir else None
    summary_updated_at = None
    summary_markdown = _safe_read_text(summary_md_path) if summary_md_path else ""
    summary_json = {}
    if summary_json_path and os.path.isfile(summary_json_path):
        try:
            with open(summary_json_path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                summary_json = loaded
        except Exception:
            summary_json = {}
    if summary_meta_path and os.path.isfile(summary_meta_path):
        try:
            with open(summary_meta_path, "r", encoding="utf-8") as handle:
                meta = json.load(handle)
            summary_updated_at = meta.get("ts")
        except Exception:
            summary_updated_at = None

    # 事项/诉求版本
    analysis_version = int(manifest.get("analysis", {}).get("current") or 0) if isinstance(manifest.get("analysis"), dict) else 0
    analysis_dir = stage_dir(user_id, base_id, "analysis", analysis_version) if analysis_version else None
    analysis_path = os.path.join(analysis_dir, "final.json") if analysis_dir else None
    complaints = []
    appeals = []
    if analysis_path and os.path.isfile(analysis_path):
        try:
            with open(analysis_path, "r", encoding="utf-8") as handle:
                analysis_loaded = json.load(handle)
            if isinstance(analysis_loaded, dict):
                complaints = analysis_loaded.get("complaints") if isinstance(analysis_loaded.get("complaints"), list) else []
                appeals = analysis_loaded.get("appeals") if isinstance(analysis_loaded.get("appeals"), list) else []
        except Exception:
            complaints, appeals = [], []

    # 组装 task2/final_result 兼容字段
    if summary_markdown or complaints or appeals or text_content:
        task2_payload = {
            "summary_markdown": summary_markdown,
            "summary": {"content": summary_markdown},
            "summary_json": summary_json,
            "summary_tables": (summary_json.get("tables") if isinstance(summary_json, dict) else None) or [],
            "complaints": complaints,
            "appeals": appeals,
            "text_content": text_content,
            "timestamp": summary_updated_at,
        }
        compat["task2"] = task2_payload
        compat["final_result"] = {
            "complaints": complaints,
            "appeals": appeals,
            "summary": task2_payload["summary"],
            "summary_markdown": summary_markdown,
            "summary_json": summary_json,
            "summary_tables": task2_payload["summary_tables"],
            "text_content": text_content,
            "source_file_url": compat.get("original_file", {}).get("url"),
        }

    return compat

def fetch_history(user_id: str) -> list[dict]:
    """Load and sort user history entries."""
    history_entries = load_user_history(user_id)

    def _sort_key(entry: dict) -> datetime:
        ts = str(entry.get("last_update_time") or "")
        try:
            return datetime.strptime(ts, "%Y/%m/%d %H:%M:%S")
        except Exception:
            return datetime.min

    return sorted(history_entries, key=_sort_key, reverse=True)


def fetch_history_detail(
    user_id: str,
    base_id: str,
    *,
    manifest_path_resolver: Callable[[str, str], str],
    process_result_loader: Callable[[str, str], dict | None],
    experience_builder: Callable[[dict | None], list[dict] | None] | None = None,
) -> tuple[dict, int]:
    """
    Assemble history detail payload.

    Returns (payload, status_code).
    """
    history_entries = load_user_history(user_id)
    target_entry = None
    for entry in history_entries:
        if str(entry.get("base_id")) == base_id:
            target_entry = entry
            break

    if target_entry is None:
        return {"error": "历史记录不存在"}, 404

    try:
        manifest_path = manifest_path_resolver(user_id, base_id)
    except ValueError as exc:
        return {"error": str(exc)}, 400

    if not os.path.isfile(manifest_path):
        return {"error": "历史记录不存在"}, 404

    try:
        with open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except Exception:
        return {"error": "历史记录读取失败"}, 500

    # 兼容新版版本化的 manifest，补齐旧版字段
    if isinstance(manifest, dict) and not manifest.get("task2") and not manifest.get("final_result"):
        try:
            compat_fields = _load_versioned_artifacts(user_id, base_id, manifest)
            manifest = {**manifest, **compat_fields}
        except Exception:
            logger.exception("failed to build compat manifest for base_id=%s", base_id)

    latest_process_result = process_result_loader(user_id, base_id)
    expert_experience_entries = (
        experience_builder(latest_process_result) if experience_builder else None
    )

    try:
        _ensure_summary_tables_from_markdown(manifest)
    except Exception:
        logger.exception("failed to populate summary tables from markdown for base_id=%s", base_id)

    return {
        "base_id": base_id,
        "history": target_entry,
        "manifest": manifest,
        "process_items": latest_process_result,
        "experience_entries": expert_experience_entries,
    }, 200
