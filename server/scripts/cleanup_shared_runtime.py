#!/usr/bin/env python3
"""清理共享 CHAT_DATA_HOME/threads/*/runtime 派生数据。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

from utils.shared_runtime_cleanup import cleanup_shared_runtime  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="清理过期共享 runtime 派生数据")
    parser.add_argument("--max-age-days", type=float, default=7.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = cleanup_shared_runtime(
        max_age_seconds=args.max_age_days * 86400,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
