"""Export the current FastAPI OpenAPI contract for ChatData API regression."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SERVER_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_PATH = Path(__file__).resolve().parent / "api" / "openapi.json"


def build_openapi_document() -> dict:
    """Build the schema from the real application router, not a test-only app."""
    if str(SERVER_ROOT) not in sys.path:
        sys.path.insert(0, str(SERVER_ROOT))

    from main import app

    return app.openapi()


def export_openapi(output_path: Path) -> Path:
    """Write a deterministic UTF-8 OpenAPI document and return its path."""
    document = build_openapi_document()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="导出 ChatData OpenAPI 接口契约")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=f"输出文件路径，默认: {DEFAULT_OUTPUT_PATH}",
    )
    args = parser.parse_args()
    output_path = export_openapi(args.output.resolve())
    print(f"OpenAPI contract exported: {output_path}")


if __name__ == "__main__":
    main()
