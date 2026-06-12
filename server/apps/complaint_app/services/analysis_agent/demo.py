from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List

repo_root = Path(__file__).resolve().parents[5]
sys.path.extend([str(repo_root), str(repo_root / "server"), str(Path(__file__).resolve().parent)])

from llm_fc import function_calling_blocking, function_calling_streaming
from fc_tools import TOOLS, FUNCTION_MAPPER
from utils.logger import get_logger

logger = get_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["blocking", "streaming"], default="blocking")
    parser.add_argument("--model", default="qwen-plus")
    parser.add_argument("--question", required=True)
    parser.add_argument("--api-key", default="sk-3662667687614e4ba32f232e37a85fb3")
    parser.add_argument("--base-url", default="https://dashscope.aliyuncs.com/compatible-mode/v1")
    args = parser.parse_args()
    if args.mode == "blocking":
        asyncio.run(function_calling_blocking(args.model, args.question, tools=TOOLS, function_mapper=FUNCTION_MAPPER, api_key=args.api_key, base_url=args.base_url))
    else:
        async def _run_stream():
            final_result = {}
            async for ev in function_calling_streaming(
                args.model,
                args.question,
                tools=TOOLS,
                function_mapper=FUNCTION_MAPPER,
                api_key=args.api_key,
                base_url=args.base_url,
            ):
                if ev.get("type") == "content_delta":
                    logger.info("content_delta: %s", ev.get("delta"))
                elif ev.get("type") == "final":
                    final_result = ev
            return final_result

        result = asyncio.run(_run_stream())
        logger.info("tool_call result: %s", result)


if __name__ == "__main__":
    main()
