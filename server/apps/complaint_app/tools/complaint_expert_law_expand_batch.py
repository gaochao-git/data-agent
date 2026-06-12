from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

SERVER_DIR = Path(__file__).resolve().parents[3]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional runtime dependency
    load_dotenv = None

from utils.logger import get_logger


logger = get_logger(__name__)
DEFAULT_FIRST_REAL_COL = "一级分类_real"
DEFAULT_SECOND_REAL_COL = "二级分类_real"
DEFAULT_LAWS_COL = "专家经验_法律列表"
DEFAULT_LAW_ITEMS_COL = "专家经验_法律条款数组"


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _resolve_data_root() -> Path:
    env_value = _to_text(os.getenv("DATA_ROOT"))
    if not env_value:
        raise ValueError("缺少 DATA_ROOT，请在环境变量中配置数据根目录")
    path = Path(env_value)
    return path if path.is_absolute() else SERVER_DIR / path


# 加载环境变量文件（.env 与 server/.env）。
def _load_env_files() -> None:
    if load_dotenv is None:
        return
    load_dotenv()
    server_env = SERVER_DIR / ".env"
    if server_env.is_file():
        load_dotenv(server_env, override=False)


# 解析字符串中的JSON片段。
def _extract_json_fragment(text: str) -> str:
    s = _to_text(text)
    if not s:
        return ""
    start = -1
    for i, ch in enumerate(s):
        if ch in "{[":
            start = i
            break
    if start < 0:
        return ""
    for end in range(len(s), start, -1):
        candidate = s[start:end].strip()
        if not candidate:
            continue
        if candidate[-1] not in "}]":
            continue
        try:
            json.loads(candidate)
            return candidate
        except Exception:
            continue
    return ""


# 尝试把任意值解析成JSON对象。
def _parse_json_any(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    text = _to_text(value)
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        fragment = _extract_json_fragment(text)
        if not fragment:
            return None
        try:
            return json.loads(fragment)
        except Exception:
            return None


# 计算专家经验目录排序键（优先目录名时间戳）。
def _latest_subdir_sort_key(path: Path) -> tuple[str, float]:
    m = re.match(r"^(\d{8})_(\d{6})_.+", path.name)
    if m:
        return (f"{m.group(1)}{m.group(2)}", path.stat().st_mtime)
    return ("", path.stat().st_mtime)


# 获取专家经验目录下最新子目录。
def _latest_subdir(base_dir: Path) -> Path | None:
    if not base_dir.is_dir():
        return None
    dirs = [p for p in base_dir.iterdir() if p.is_dir()]
    if not dirs:
        return None
    return sorted(dirs, key=_latest_subdir_sort_key, reverse=True)[0]


# 加载专家经验原始JSON字符串（指定文件或最新目录 parsed.json）。
def _load_expert_experience(base_dir: Path, file_path: str) -> str:
    if file_path:
        p = Path(file_path).expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(f"expert experience file not found: {p}")
        raw = p.read_text(encoding="utf-8").strip()
        if not raw:
            return ""
        return json.dumps(json.loads(raw), ensure_ascii=False)
    latest = _latest_subdir(base_dir)
    if latest is None:
        return ""
    parsed = latest / "parsed.json"
    if not parsed.is_file():
        return ""
    raw = parsed.read_text(encoding="utf-8").strip()
    if not raw:
        return ""
    return json.dumps(json.loads(raw), ensure_ascii=False)


# 构造表头列名到列号映射。
def _header_map(ws: Any) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for col in range(1, ws.max_column + 1):
        name = _to_text(ws.cell(row=1, column=col).value)
        if name and name not in mapping:
            mapping[name] = col
    return mapping


# 确保指定列存在，不存在则追加新列。
def _ensure_columns(ws: Any, mapping: dict[str, int], names: list[str]) -> None:
    for name in names:
        if name in mapping:
            continue
        col = ws.max_column + 1
        ws.cell(row=1, column=col, value=name)
        mapping[name] = col


# 从专家经验里按一级/二级真值严格匹配记录。
def _find_expert_entry(expert_json: dict[str, Any], first_real: str, second_real: str) -> dict[str, Any] | None:
    if not first_real or not second_real:
        return None
    first_node = expert_json.get(first_real)
    if isinstance(first_node, dict):
        items = [first_node]
    elif isinstance(first_node, list):
        items = [x for x in first_node if isinstance(x, dict)]
    else:
        return None
    for item in items:
        if _to_text(item.get("overview")) == second_real:
            return item
    return None


# 把 law_list 归一成多行文本。
def _normalize_law_list_text(law_list_value: Any) -> str:
    if isinstance(law_list_value, str):
        return law_list_value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if isinstance(law_list_value, list):
        lines = [_to_text(x) for x in law_list_value if _to_text(x)]
        return "\n".join(lines).strip()
    return _to_text(law_list_value).replace("\r\n", "\n").replace("\r", "\n").strip()


# 解析 law_list 为法律名列表（去重后换行）和法规条款数组。
def _parse_law_list(law_list_text: str, row_num: int) -> tuple[str, str]:
    text = _to_text(law_list_text)
    if not text:
        return "", ""
    law_items: OrderedDict[str, list[str]] = OrderedDict()
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    for line in lines:
        sep_idx = line.find("：")
        if sep_idx < 0:
            sep_idx = line.find(":")
        if sep_idx < 0:
            logger.warning("第%s行 法律行未包含冒号，已忽略：%s", row_num, line)
            continue
        law_name = _to_text(line[:sep_idx])
        clause_text = _to_text(line[sep_idx + 1 :])
        if not law_name:
            logger.warning("第%s行 法律名称为空，已忽略：%s", row_num, line)
            continue
        clauses = [_to_text(x) for x in re.split(r"[、,，;；]+", clause_text) if _to_text(x)]
        if law_name not in law_items:
            law_items[law_name] = []
        for clause in clauses:
            if clause not in law_items[law_name]:
                law_items[law_name].append(clause)
    if not law_items:
        return "", ""
    law_names = "\n".join(law_items.keys())
    arr = [{"law": law, "items": items} for law, items in law_items.items()]
    return law_names, json.dumps(arr, ensure_ascii=False)


# 保存CSV（仅导出本次处理的行和表头）。
def _save_sheet_to_csv(ws: Any, output_path: Path, run_rows: list[int]) -> None:
    with output_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        headers = [_to_text(ws.cell(row=1, column=col).value) for col in range(1, ws.max_column + 1)]
        writer.writerow(headers)
        for row_num in run_rows:
            row = [ws.cell(row=row_num, column=col).value for col in range(1, ws.max_column + 1)]
            writer.writerow(["" if v is None else v for v in row])


# 解析输入输出路径并创建输出目录。
def _resolve_io_paths(input_excel: str, output_excel: str) -> tuple[Path, Path]:
    input_path = Path(input_excel).expanduser().resolve()
    output_path = Path(output_excel).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    return input_path, output_path


# 计算本次要处理的行号集合。
def _select_run_rows(ws: Any, start: int, limit: int) -> list[int]:
    all_rows = list(range(2, ws.max_row + 1))
    s = max(0, int(start))
    e = len(all_rows)
    if int(limit) > 0:
        e = min(e, s + int(limit))
    return all_rows[s:e]


# 加载并解析专家经验JSON。
def _load_expert_experience_json(args: argparse.Namespace) -> dict[str, Any]:
    default_base_dir = _resolve_data_root() / "complaints" / "expert_experience" / "complaint-analysis-uploaded"
    base_dir = Path(args.expert_experience_base_dir).expanduser().resolve() if args.expert_experience_base_dir else default_base_dir
    raw = _load_expert_experience(base_dir, _to_text(args.expert_experience_json))
    if not raw:
        logger.warning("专家经验为空，目录=%s", base_dir)
        return {}
    parsed = _parse_json_any(raw)
    if not isinstance(parsed, dict):
        logger.warning("专家经验JSON不是对象结构，目录=%s", base_dir)
        return {}
    logger.info("专家经验加载成功，一级分类数=%s 目录=%s", len(parsed), base_dir)
    return parsed


# 写入单行扩展结果。
def _write_row(ws: Any, columns: dict[str, int], row_num: int, first_real: str, second_real: str, law_names: str, law_items_json: str) -> None:
    ws.cell(row=row_num, column=columns[DEFAULT_LAWS_COL], value=law_names)
    ws.cell(row=row_num, column=columns[DEFAULT_LAW_ITEMS_COL], value=law_items_json)
    logger.info("第%s行 扩展完成 一级=%s 二级=%s 法律数=%s", row_num, first_real, second_real, len([x for x in law_names.split('\n') if _to_text(x)]))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="基于专家经验扩展法律列（按一级/二级真值严格匹配）。")
    parser.add_argument("--input-excel", required=True, help="输入Excel路径")
    parser.add_argument("--output-excel", required=True, help="输出Excel路径（支持 .xlsx/.csv）")
    parser.add_argument("--sheet-name", default="", help="可选：sheet名称，默认首个")
    parser.add_argument("--first-real-col", default=DEFAULT_FIRST_REAL_COL, help="一级真值列名")
    parser.add_argument("--second-real-col", default=DEFAULT_SECOND_REAL_COL, help="二级真值列名")
    parser.add_argument("--expert-experience-json", default="", help="可选：指定专家经验 parsed.json 文件路径")
    parser.add_argument("--expert-experience-base-dir", default="", help="可选：专家经验目录，默认 DATA_ROOT/complaints/expert_experience/complaint-analysis-uploaded")
    parser.add_argument("--start", type=int, default=0, help="起始数据行（0-based，含）")
    parser.add_argument("--limit", type=int, default=0, help="处理条数，0=到结尾")
    parser.add_argument("--progress-every", type=int, default=20, help="每完成N条打印一次进度")
    return parser


def main() -> int:
    _load_env_files()
    args = _build_parser().parse_args()
    input_path, output_path = _resolve_io_paths(args.input_excel, args.output_excel)
    expert_json = _load_expert_experience_json(args)
    if not input_path.is_file():
        logger.error("输入文件不存在：%s", input_path)
        return 2
    wb = load_workbook(input_path)
    ws = wb[_to_text(args.sheet_name)] if _to_text(args.sheet_name) else wb[wb.sheetnames[0]]
    columns = _header_map(ws)
    if args.first_real_col not in columns:
        logger.error("未找到一级真值列：%s", args.first_real_col)
        return 2
    if args.second_real_col not in columns:
        logger.error("未找到二级真值列：%s", args.second_real_col)
        return 2
    _ensure_columns(ws, columns, [DEFAULT_LAWS_COL, DEFAULT_LAW_ITEMS_COL])
    run_rows = _select_run_rows(ws, int(args.start), int(args.limit))
    if not run_rows:
        if output_path.suffix.lower() == ".csv":
            _save_sheet_to_csv(ws, output_path, [])
        else:
            wb.save(output_path)
        logger.info("无可处理样本，已输出空结果：%s", output_path)
        return 0
    progress_every = max(1, int(args.progress_every))
    matched = 0
    law_expanded = 0
    parse_warning_like = 0
    total = len(run_rows)
    logger.info("开始扩展法律列：总样本=%s 范围起点=%s 限制=%s", total, int(args.start), int(args.limit))
    for idx, row_num in enumerate(run_rows, start=1):
        first_real = _to_text(ws.cell(row=row_num, column=columns[args.first_real_col]).value)
        second_real = _to_text(ws.cell(row=row_num, column=columns[args.second_real_col]).value)
        law_names = ""
        law_items_json = ""
        entry = _find_expert_entry(expert_json, first_real, second_real)
        if entry:
            matched += 1
            law_text = _normalize_law_list_text(entry.get("law_list"))
            law_names, law_items_json = _parse_law_list(law_text, row_num)
            if law_names:
                law_expanded += 1
            elif law_text:
                parse_warning_like += 1
        else:
            logger.warning("第%s行 未命中专家经验 一级=%s 二级=%s", row_num, first_real, second_real)
        _write_row(ws, columns, row_num, first_real, second_real, law_names, law_items_json)
        if idx % progress_every == 0 or idx == total:
            logger.info("进度 %s/%s 命中=%s 已扩展法律=%s 解析异常近似计数=%s", idx, total, matched, law_expanded, parse_warning_like)
    if output_path.suffix.lower() == ".csv":
        _save_sheet_to_csv(ws, output_path, run_rows)
    else:
        wb.save(output_path)
    logger.info("扩展完成：输出=%s 总样本=%s 命中=%s 已扩展法律=%s 解析异常近似计数=%s", output_path, total, matched, law_expanded, parse_warning_like)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
