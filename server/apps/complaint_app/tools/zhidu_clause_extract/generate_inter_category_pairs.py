from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency
    load_dotenv = None

SERVER_ROOT = Path(__file__).resolve().parents[4]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from apps.complaint_app.core.llm.llm_client import AsyncLLMClient, ModelConfig
from apps.complaint_app.core.model_config import get_analysis_model
from utils.llm_thinking import build_thinking_extra_body
from utils.logger import get_logger


logger = get_logger(__name__)
MAX_LOG_PREVIEW = 2000
SHEET1_HEADER_ROW_INDEX = 2
SHEET2_HEADER_ROW_INDEX = 2
REQUIRED_FIELDS = ["版块", "领域", "制度名称", "制度文号", "制度级别", "发布时间", "主责部门"]
SYSTEM_PROMPT = """你正在参与公司内部制度冲突分析项目的前置分组设计。

输入是一份已经结构化整理好的制度目录 JSON。每条记录至少包含：
版块、领域、制度名称、制度文号、制度级别、发布时间、主责部门。

你的任务不是分析具体条文，而是先梳理：
1. 哪些版块需要做组内比较
2. 哪些版块值得做组间比较

要求：
1. 版块优先，使用版块作为比较键；只有某条制度没有版块时，才回退使用其领域。
2. 组内关系：凡是同一比较键下存在 2 份及以上制度，都应输出为一个组内比较单元。
3. 组间关系：只输出值得比较的重点组间对，不要穷举所有组合。
4. 只有在两个版块之间存在明显的业务交叉、流程衔接、职责边界关联、审批衔接、数据流转、资料流转、资产流转、风险传导关系时，才纳入。
5. 如果两个版块业务上明显独立、约束对象完全不同，则不要纳入。
6. 每个配对都要给出简短理由，理由必须从制度管理关系出发，而不是空泛表述。
7. 输出必须是严格 JSON，不要输出解释，不要输出 Markdown 代码块。

输出格式：
{
  "intra_groups": [
    {
      "key": "组内比较键",
      "group": "",
      "domain": "",
      "doc_count": 0,
      "reason": ""
    }
  ],
  "inter_group_pairs": [
    {
      "left": "左侧比较键",
      "right": "右侧比较键",
      "left_group": "",
      "left_domain": "",
      "right_group": "",
      "right_domain": "",
      "reason": ""
    }
  ]
}"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="基于 outline.xlsx 生成结构化目录 JSON 与组内/组间比较关系文件。")
    parser.add_argument("--outline-xlsx", required=True, help="outline.xlsx 路径")
    parser.add_argument("--output-json", required=True, help="输出 inter_group_pairs.json 路径")
    parser.add_argument("--model", default="", help="模型名，默认读取 OPENAI_ANALYSIS_MODEL")
    parser.add_argument("--enable-thinking", action="store_true", help="显式开启模型思考")
    parser.add_argument("--business-retries", type=int, default=3, help="模型返回非JSON时的业务重试次数，默认 3")
    return parser


def load_env_files() -> None:
    if load_dotenv is None:
        return
    load_dotenv()
    server_env = SERVER_ROOT / ".env"
    if server_env.is_file():
        load_dotenv(server_env, override=False)


def write_json(path: Path, payload: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_cell(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return text if text and text.lower() != "nan" else ""


def load_outline_sheets(path: Path) -> dict[str, pd.DataFrame]:
    workbook = pd.read_excel(path, sheet_name=None, header=None)
    return {str(sheet_name): dataframe.fillna("") for sheet_name, dataframe in workbook.items()}


def normalize_headers(raw_headers: list[Any]) -> list[str]:
    return [normalize_cell(header).replace("\n", "").replace(" ", "") for header in raw_headers]


def normalize_record_value(value: Any) -> str:
    return normalize_cell(value)


def is_meaningful_text(value: str) -> bool:
    normalized = str(value or "").strip()
    if not normalized:
        return False
    return bool(re.search(r"[\u4e00-\u9fffA-Za-z0-9]", normalized))


def normalize_group_field_value(value: Any) -> str:
    normalized = normalize_record_value(value)
    return normalized if is_meaningful_text(normalized) else ""


def is_valid_title(value: Any) -> bool:
    normalized = normalize_record_value(value)
    if not normalized:
        return False
    if normalized in {"无", "涉密"}:
        return False
    if not is_meaningful_text(normalized):
        return False
    return True


def dataframe_preview_rows(dataframe: pd.DataFrame, max_rows: int = 10) -> list[list[str]]:
    preview: list[list[str]] = []
    max_cols = int(dataframe.shape[1]) if int(dataframe.shape[1]) > 0 else 0
    for _, row in dataframe.head(max_rows).iterrows():
        preview.append([normalize_record_value(row.iloc[index]) for index in range(max_cols)])
    return preview


def build_rows_from_sheet(dataframe: pd.DataFrame, header_row_index: int, sheet_name: str, debug_dir: Path) -> list[dict[str, str]]:
    if len(dataframe.index) <= header_row_index:
        raise ValueError(f"sheet 行数不足，无法读取第 {header_row_index + 1} 行表头")
    headers = normalize_headers(dataframe.iloc[header_row_index].tolist())
    body = dataframe.iloc[header_row_index + 1 :].copy()
    body.columns = headers
    body = body.fillna("")
    # 处理合并单元格导致的空值，尤其是版块/领域列。
    for field in ("版块", "领域"):
        if field in body.columns:
            body[field] = body[field].map(normalize_group_field_value)
            body[field] = body[field].replace("", pd.NA).ffill().fillna("")
    missing_required_fields = [field for field in REQUIRED_FIELDS if field not in body.columns]
    row_debug: list[dict[str, Any]] = []
    rows: list[dict[str, str]] = []
    for row_index, row in body.iterrows():
        item = {field: normalize_record_value(row[field]) if field in body.columns else "" for field in REQUIRED_FIELDS}
        skip_reason = ""
        if not is_valid_title(item["制度名称"]):
            skip_reason = "制度名称无效或为占位值，已跳过"
        row_debug.append(
            {
                "excel_row_index": int(row_index) + 1,
                "normalized_item": item,
                "skip_reason": skip_reason,
            }
        )
        if skip_reason:
            continue
        rows.append(item)
    debug_payload = {
        "sheet_name": sheet_name,
        "header_row_index_1_based": header_row_index + 1,
        "raw_preview_rows": dataframe_preview_rows(dataframe),
        "recognized_headers": headers,
        "missing_required_fields": missing_required_fields,
        "required_fields": REQUIRED_FIELDS,
        "body_column_count": len(list(body.columns)),
        "body_row_count": len(body.index),
        "extracted_row_count": len(rows),
        "row_debug_preview": row_debug[:30],
    }
    debug_path = debug_dir / f"{sheet_name}_debug.json"
    write_json(debug_path, debug_payload)
    logger.info("sheet 解析调试信息已写入 sheet=%s debug=%s missing_fields=%s extracted_rows=%s", sheet_name, debug_path, missing_required_fields, len(rows))
    return rows


def build_sheet_json_paths(output_json: Path) -> tuple[Path, Path, Path]:
    output_dir = output_json.parent
    return (
        output_dir / "sheet1_rows.json",
        output_dir / "sheet2_rows.json",
        output_dir / "merged_outline.json",
    )


def build_debug_dir(output_json: Path) -> Path:
    debug_dir = output_json.parent / "outline_debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    return debug_dir


def merge_two_records(sheet1_row: dict[str, str] | None, sheet2_row: dict[str, str] | None) -> dict[str, str]:
    merged: dict[str, str] = {}
    left = sheet1_row or {}
    right = sheet2_row or {}
    all_fields = sorted(set(left.keys()) | set(right.keys()))
    for field in all_fields:
        left_value = str(left.get(field, "")).strip()
        right_value = str(right.get(field, "")).strip()
        if left_value and right_value:
            if left_value == right_value:
                merged[field] = left_value
            else:
                merged[f"sheet1_{field}"] = left_value
                merged[f"sheet2_{field}"] = right_value
        elif left_value:
            merged[field] = left_value
        elif right_value:
            merged[field] = right_value
    source_sheets: list[str] = []
    if left:
        source_sheets.append("sheet1")
    if right:
        source_sheets.append("sheet2")
    merged["source_sheets"] = source_sheets
    return merged


def merge_sheet_rows(sheet1_rows: list[dict[str, str]], sheet2_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    sheet1_map = {str(row.get("制度名称") or "").strip(): row for row in sheet1_rows if str(row.get("制度名称") or "").strip()}
    sheet2_map = {str(row.get("制度名称") or "").strip(): row for row in sheet2_rows if str(row.get("制度名称") or "").strip()}
    names = sorted(set(sheet1_map) | set(sheet2_map))
    merged_rows: list[dict[str, str]] = []
    for name in names:
        merged = merge_two_records(sheet1_map.get(name), sheet2_map.get(name))
        merged["制度名称"] = name
        merged_rows.append(merged)
    return merged_rows


def build_outline_prompt_text(merged_rows: list[dict[str, str]]) -> str:
    return json.dumps(merged_rows, ensure_ascii=False, indent=2)


def build_user_prompt(merged_rows: list[dict[str, str]]) -> str:
    return "请基于以下制度目录结构化 JSON，生成制度组内和组间比较关系文件。字段中 `版块` 表示最高一级分类，也是本次制度比较的优先比较键；只有 `版块` 为空时才回退使用 `领域`。如有 `sheet1_字段`/`sheet2_字段`，表示两个 sheet 的值不一致，请结合上下文判断。\n\n目录数据如下：\n" + build_outline_prompt_text(merged_rows)


def message_field(message: Any, field: str) -> Any:
    if isinstance(message, dict):
        return message.get(field)
    value = getattr(message, field, None)
    if value is not None:
        return value
    if hasattr(message, "model_dump"):
        try:
            dumped = message.model_dump()
            if isinstance(dumped, dict):
                return dumped.get(field)
        except Exception as err:
            logger.warning("message.model_dump 读取字段失败 field=%s err=%s", field, err)
            return None
    return None


def normalize_message_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("text") or item.get("value")
                if text:
                    parts.append(str(text))
            elif item is not None:
                parts.append(str(item))
        return "".join(parts)
    return "" if value is None else str(value)


def extract_message_text(resp: Any) -> str:
    choices = getattr(resp, "choices", None) or []
    if not choices:
        return ""
    message = getattr(choices[0], "message", None)
    if not message:
        return ""
    content = normalize_message_content(message_field(message, "content")).strip()
    if content:
        return content
    for field in ("reasoning_content", "reasoning", "analysis", "thoughts", "think"):
        fallback = normalize_message_content(message_field(message, field)).strip()
        if fallback:
            logger.warning("组间关系生成 content 为空，回退字段=%s", field)
            return fallback
    return ""


def extract_json_fragment(text: str) -> str:
    source = str(text or "").strip()
    if not source:
        return ""
    start = source.find("{")
    if start < 0:
        return ""
    for end in range(len(source), start, -1):
        candidate = source[start:end].strip()
        if not candidate or candidate[-1] != "}":
            continue
        try:
            json.loads(candidate)
            return candidate
        except Exception:
            continue
    return ""


def normalize_compare_key(value: Any) -> str:
    return "".join(str(value or "").split())


def parse_inter_group_pairs_payload(text: str) -> dict[str, list[dict[str, str]]]:
    fragment = extract_json_fragment(text)
    if not fragment:
        raise ValueError("模型返回中未找到 JSON 对象")
    payload = json.loads(fragment)
    if not isinstance(payload, dict):
        raise ValueError("模型返回不是 JSON 对象")
    intra_groups = payload.get("intra_groups")
    pairs = payload.get("inter_group_pairs")
    if not isinstance(intra_groups, list):
        raise ValueError("模型返回缺少 intra_groups 数组")
    if not isinstance(pairs, list):
        raise ValueError("模型返回缺少 inter_group_pairs 数组")
    normalized_intra_groups: list[dict[str, str | int]] = []
    seen_intra_keys: set[str] = set()
    for item in intra_groups:
        if not isinstance(item, dict):
            continue
        key = normalize_compare_key(item.get("key") or "")
        if not key or key in seen_intra_keys:
            continue
        seen_intra_keys.add(key)
        normalized_intra_groups.append(
            {
                "key": key,
                "group": normalize_compare_key(item.get("group") or item.get("board") or item.get("版块") or ""),
                "domain": normalize_compare_key(item.get("domain") or ""),
                "doc_count": int(item.get("doc_count", 0) or 0),
                "reason": str(item.get("reason") or "").strip(),
            }
        )
    normalized_pairs: list[dict[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for item in pairs:
        if not isinstance(item, dict):
            continue
        left = normalize_compare_key(item.get("left") or "")
        right = normalize_compare_key(item.get("right") or "")
        if not left or not right or left == right:
            continue
        pair_key = tuple(sorted([left, right]))
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)
        normalized_pairs.append(
            {
                "left": left,
                "right": right,
                "left_group": normalize_compare_key(item.get("left_group") or item.get("left_board") or item.get("left_版块") or ""),
                "left_domain": normalize_compare_key(item.get("left_domain") or ""),
                "right_group": normalize_compare_key(item.get("right_group") or item.get("right_board") or item.get("right_版块") or ""),
                "right_domain": normalize_compare_key(item.get("right_domain") or ""),
                "reason": str(item.get("reason") or "").strip(),
            }
        )
    return {"intra_groups": normalized_intra_groups, "inter_group_pairs": normalized_pairs}


def preview_text(text: str, limit: int = MAX_LOG_PREVIEW) -> str:
    source = str(text or "").strip()
    if len(source) <= limit:
        return source
    return f"{source[:limit]}... [truncated {len(source) - limit} chars]"


async def generate_inter_group_pairs(llm: AsyncLLMClient, model: str, thinking_extra_body: dict[str, Any] | None, merged_rows: list[dict[str, str]], business_retries: int) -> dict[str, list[dict[str, str]]]:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(merged_rows)},
    ]
    last_error = ""
    last_content = ""
    attempts = max(1, int(business_retries))
    for attempt in range(1, attempts + 1):
        resp = await llm.chat(messages=messages, model=model, temperature=0.0, extra_body=thinking_extra_body)
        content = extract_message_text(resp)
        last_content = content
        logger.info("组内/组间关系生成模型返回 attempt=%s/%s content=%s", attempt, attempts, preview_text(content))
        try:
            return parse_inter_group_pairs_payload(content)
        except Exception as err:  # pylint: disable=broad-except
            last_error = f"{err}; 模型输出预览: {preview_text(content)}"
            logger.warning("组内/组间关系生成解析失败 attempt=%s/%s err=%s", attempt, attempts, last_error)
    raise ValueError(f"组内/组间关系生成失败: {last_error}; 原始输出: {last_content}")


async def async_main() -> int:
    args = build_parser().parse_args()
    load_env_files()

    outline_xlsx = Path(args.outline_xlsx).expanduser().resolve()
    output_json = Path(args.output_json).expanduser().resolve()

    sheets = load_outline_sheets(outline_xlsx)
    if len(sheets) < 2:
        raise ValueError("outline.xlsx 至少需要 2 个 sheet")
    sheet_names = list(sheets.keys())
    debug_dir = build_debug_dir(output_json)
    sheet1_rows = build_rows_from_sheet(sheets[sheet_names[0]], SHEET1_HEADER_ROW_INDEX, sheet_names[0], debug_dir)
    sheet2_rows = build_rows_from_sheet(sheets[sheet_names[1]], SHEET2_HEADER_ROW_INDEX, sheet_names[1], debug_dir)
    merged_rows = merge_sheet_rows(sheet1_rows, sheet2_rows)

    sheet1_json_path, sheet2_json_path, merged_json_path = build_sheet_json_paths(output_json)
    write_json(sheet1_json_path, sheet1_rows)
    write_json(sheet2_json_path, sheet2_rows)
    write_json(merged_json_path, merged_rows)
    if not sheet2_rows:
        logger.warning("sheet2_rows 为空，请检查 debug 文件：%s", debug_dir / f"{sheet_names[1]}_debug.json")

    model = str(args.model or "").strip() or get_analysis_model()
    thinking_extra_body = build_thinking_extra_body(model, bool(args.enable_thinking))
    llm = AsyncLLMClient(config=ModelConfig(default_model=model), name="zhidu_inter_group_pairs")

    payload = await generate_inter_group_pairs(llm, model, thinking_extra_body, merged_rows, int(args.business_retries))
    write_json(output_json, payload)
    print(f"输出文件: {output_json}")
    print(f"sheet1 json: {sheet1_json_path}")
    print(f"sheet2 json: {sheet2_json_path}")
    print(f"merged json: {merged_json_path}")
    print(f"debug dir: {debug_dir}")
    print(f"组内关系数量: {len(payload['intra_groups'])}")
    print(f"组间关系数量: {len(payload['inter_group_pairs'])}")
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
