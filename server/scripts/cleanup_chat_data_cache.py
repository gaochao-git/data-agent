#!/usr/bin/env python3
"""清理过期的 ChatData 本地派生缓存。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

from utils.cache_cleanup import cleanup_derived_caches  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="清理过期的 ChatData 本地派生缓存")
    parser.add_argument("--max-age-days", type=float, default=7.0, help="超过该天数的缓存可清理，默认 7")
    parser.add_argument("--dry-run", action="store_true", help="只输出将清理的目录，不删除")
    args = parser.parse_args()
    result = cleanup_derived_caches(max_age_seconds=args.max_age_days * 86400, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
