"""Utilities for complaint summaries stored under per-user directories."""
from __future__ import annotations

import json
from pathlib import Path

from utils.storage_paths import get_complaints_real_path

def load_summary_json_from_storage(user_id: str, base_id: str) -> dict | None:
    if not base_id:
        return None

    summary_dir = Path(get_complaints_real_path(user_id, base_id, "summary"))
    if not summary_dir.exists() or not summary_dir.is_dir():
        return None

    candidates: list[Path] = []
    preferred = summary_dir / f"{base_id}_summary.json"
    if preferred.exists():
        candidates.append(preferred)

    json_files = sorted(summary_dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in json_files:
        if path not in candidates:
            candidates.append(path)

    for path in candidates:
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                return data
        except Exception:
            continue

    return None
