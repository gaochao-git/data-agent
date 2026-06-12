from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook

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
DEFAULT_FIRST_LAWS_COL = "专家经验一级法律汇总"
DEFAULT_SECOND_LAWS_COL = "专家经验二级分类法律汇总"
DEFAULT_SECOND_VERIFY_COL = "专家经验二级分类_核实要点"
DEFAULT_SECOND_CHECKLIST_COL = "专家经验二级分类_调阅清单"


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


# 加载环境变量文件。
def _load_env_files() -> None:
    if load_dotenv is None:
        return
    load_dotenv()
    server_env = SERVER_DIR / ".env"
    if server_env.is_file():
        load_dotenv(server_env, override=False)


# 解析数据根目录。
def _resolve_data_root() -> Path:
    env_value = _to_text(os.getenv("DATA_ROOT"))
    if not env_value:
        raise ValueError("缺少 DATA_ROOT，请显式传 --expert-experience-base-dir 或 --expert-experience-json")
    path = Path(env_value).expanduser()
    return path if path.is_absolute() else (SERVER_DIR / path)


# 获取默认专家经验目录。
def _default_expert_base_dir() -> Path:
    return _resolve_data_root() / "complaints" / "expert_experience" / "complaint-analysis-uploaded"


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


# 计算专家经验目录排序键。
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


# 定位专家经验 parsed.json 路径。
def _resolve_expert_json_path(base_dir: Path, file_path: str) -> Path | None:
    if file_path:
        p = Path(file_path).expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(f"expert experience file not found: {p}")
        return p
    latest = _latest_subdir(base_dir)
    if latest is None:
        return None
    parsed = latest / "parsed.json"
    return parsed if parsed.is_file() else None


# 加载并解析专家经验JSON。
def _load_expert_experience_json(args: argparse.Namespace) -> tuple[dict[str, Any], Path | None]:
    base_dir = Path(args.expert_experience_base_dir).expanduser().resolve() if args.expert_experience_base_dir else _default_expert_base_dir()
    parsed_path = _resolve_expert_json_path(base_dir, _to_text(args.expert_experience_json))
    if parsed_path is None:
        logger.warning("专家经验为空，目录=%s", base_dir)
        return {}, None
    raw = parsed_path.read_text(encoding="utf-8").strip()
    if not raw:
        logger.warning("专家经验文件为空：%s", parsed_path)
        return {}, parsed_path
    parsed = _parse_json_any(raw)
    if not isinstance(parsed, dict):
        logger.warning("专家经验JSON不是对象结构：%s", parsed_path)
        return {}, parsed_path
    logger.info("专家经验加载成功，一级分类数=%s 文件=%s", len(parsed), parsed_path)
    return parsed, parsed_path


# 构造表头列名到列号映射。
def _header_map(ws: Any) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for col in range(1, ws.max_column + 1):
        name = _to_text(ws.cell(row=1, column=col).value)
        if name and name not in mapping:
            mapping[name] = col
    return mapping


# 确保指定列存在。
def _ensure_columns(ws: Any, mapping: dict[str, int], names: list[str]) -> None:
    for name in names:
        if name in mapping:
            continue
        col = ws.max_column + 1
        ws.cell(row=1, column=col, value=name)
        mapping[name] = col


# 计算本次要处理的行号集合。
def _select_run_rows(ws: Any, start: int, limit: int) -> list[int]:
    all_rows = list(range(2, ws.max_row + 1))
    s = max(0, int(start))
    e = len(all_rows)
    if int(limit) > 0:
        e = min(e, s + int(limit))
    return all_rows[s:e]


# 解析输入输出路径并创建输出目录。
def _resolve_io_paths(input_excel: str, output_excel: str) -> tuple[Path, Path]:
    input_path = Path(input_excel).expanduser().resolve()
    output_path = Path(output_excel).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    return input_path, output_path


# 读取CSV全部行。
def _read_csv_rows(input_path: Path) -> list[list[str]]:
    try:
        with input_path.open("r", encoding="utf-8-sig", newline="") as f:
            return [list(row) for row in csv.reader(f)]
    except UnicodeDecodeError:
        with input_path.open("r", encoding="gb18030", newline="") as f:
            return [list(row) for row in csv.reader(f)]


# 按输入文件类型加载工作簿与工作表。
def _load_input_workbook(input_path: Path, sheet_name: str) -> tuple[Any, Any]:
    suffix = input_path.suffix.lower()
    if suffix == ".csv":
        wb = Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        for row in _read_csv_rows(input_path):
            ws.append(row)
        logger.info("已加载CSV输入：%s 总行数=%s", input_path, ws.max_row)
        return wb, ws
    if suffix in {".xlsx", ".xlsm", ".xltx", ".xltm"}:
        wb = load_workbook(input_path)
        ws = wb[_to_text(sheet_name)] if _to_text(sheet_name) else wb[wb.sheetnames[0]]
        return wb, ws
    raise ValueError(f"不支持的输入文件类型：{input_path.suffix}")


# 保存CSV结果。
def _save_sheet_to_csv(ws: Any, output_path: Path, run_rows: list[int]) -> None:
    with output_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        headers = [_to_text(ws.cell(row=1, column=col).value) for col in range(1, ws.max_column + 1)]
        writer.writerow(headers)
        for row_num in run_rows:
            row = [ws.cell(row=row_num, column=col).value for col in range(1, ws.max_column + 1)]
            writer.writerow(["" if v is None else v for v in row])


# 落盘结果。
def _save_output_snapshot(wb: Any, ws: Any, output_path: Path, run_rows: list[int]) -> None:
    if output_path.suffix.lower() == ".csv":
        _save_sheet_to_csv(ws, output_path, run_rows)
    else:
        wb.save(output_path)


# 获取某一级下的专家经验节点列表。
def _first_level_items(expert_json: dict[str, Any], first_real: str) -> list[dict[str, Any]]:
    first_node = expert_json.get(first_real)
    if isinstance(first_node, dict):
        return [first_node]
    if isinstance(first_node, list):
        return [x for x in first_node if isinstance(x, dict)]
    return []


# 从专家经验里按一级/二级真值严格匹配记录。
def _find_expert_entry(expert_json: dict[str, Any], first_real: str, second_real: str) -> dict[str, Any] | None:
    if not first_real or not second_real:
        return None
    for item in _first_level_items(expert_json, first_real):
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


# 归一化专家经验文本或列表字段。
def _normalize_text_list(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join([_to_text(item) for item in value if _to_text(item)]).strip()
    return _to_text(value).replace("\r\n", "\n").replace("\r", "\n").strip()


# 解析 law_list 为去重后的法律名列表。
def _parse_law_names(law_list_text: str, row_num: int) -> list[str]:
    text = _to_text(law_list_text)
    if not text:
        return []
    names: list[str] = []
    seen: set[str] = set()
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    for line in lines:
        sep_idx = line.find("：")
        if sep_idx < 0:
            sep_idx = line.find(":")
        if sep_idx < 0:
            logger.warning("第%s行 法律行未包含冒号，已忽略：%s", row_num, line)
            continue
        law_name = _to_text(line[:sep_idx])
        if not law_name:
            logger.warning("第%s行 法律名称为空，已忽略：%s", row_num, line)
            continue
        if law_name in seen:
            continue
        names.append(law_name)
        seen.add(law_name)
    return names


# 把法律名列表拼成换行文本。
def _join_law_names(names: list[str]) -> str:
    return "\n".join([_to_text(name) for name in names if _to_text(name)])


# 汇总一级分类下全部法律。
def _collect_first_level_laws(expert_json: dict[str, Any], first_real: str, row_num: int) -> tuple[str, bool]:
    names: list[str] = []
    seen: set[str] = set()
    items = _first_level_items(expert_json, first_real)
    if not items:
        return "", False
    for item in items:
        parsed_names = _parse_law_names(_normalize_law_list_text(item.get("law_list")), row_num)
        for law_name in parsed_names:
            if law_name in seen:
                continue
            names.append(law_name)
            seen.add(law_name)
    return _join_law_names(names), True


# 汇总二级分类命中的法律。
def _collect_second_level_laws(expert_json: dict[str, Any], first_real: str, second_real: str, row_num: int) -> tuple[str, bool]:
    entry = _find_expert_entry(expert_json, first_real, second_real)
    if not entry:
        return "", False
    names = _parse_law_names(_normalize_law_list_text(entry.get("law_list")), row_num)
    return _join_law_names(names), True


# 汇总二级分类命中的核实要点与调阅清单。
def _collect_second_level_details(expert_json: dict[str, Any], first_real: str, second_real: str) -> tuple[str, str, bool]:
    entry = _find_expert_entry(expert_json, first_real, second_real)
    if not entry:
        return "", "", False
    verify_text = _normalize_text_list(entry.get("verification_points"))
    checklist_text = _normalize_text_list(entry.get("checklist"))
    return verify_text, checklist_text, True


# 写入单行扩展结果。
def _write_row(ws: Any, columns: dict[str, int], row_num: int, first_laws_text: str, second_laws_text: str, second_verify_text: str, second_checklist_text: str) -> None:
    ws.cell(row=row_num, column=columns[DEFAULT_FIRST_LAWS_COL], value=first_laws_text)
    ws.cell(row=row_num, column=columns[DEFAULT_SECOND_LAWS_COL], value=second_laws_text)
    ws.cell(row=row_num, column=columns[DEFAULT_SECOND_VERIFY_COL], value=second_verify_text)
    ws.cell(row=row_num, column=columns[DEFAULT_SECOND_CHECKLIST_COL], value=second_checklist_text)


# 构造命令行参数。
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="基于专家经验 parsed.json 为结果表补充一级/二级法律汇总及二级核实要点、调阅清单。")
    parser.add_argument("--input-excel", required=True, help="输入文件路径（支持 .xlsx/.csv）")
    parser.add_argument("--output-excel", required=True, help="输出文件路径（支持 .xlsx/.csv）")
    parser.add_argument("--sheet-name", default="", help="可选：sheet名称，默认首个")
    parser.add_argument("--first-real-col", default=DEFAULT_FIRST_REAL_COL, help="一级真值列名")
    parser.add_argument("--second-real-col", default=DEFAULT_SECOND_REAL_COL, help="二级真值列名")
    parser.add_argument("--expert-experience-json", default="", help="可选：指定专家经验 parsed.json 文件路径")
    parser.add_argument("--expert-experience-base-dir", default="", help="可选：专家经验目录；未传时使用 DATA_ROOT/complaints/expert_experience/complaint-analysis-uploaded")
    parser.add_argument("--start", type=int, default=0, help="起始数据行（0-based，含）")
    parser.add_argument("--limit", type=int, default=0, help="处理条数，0=到结尾")
    parser.add_argument("--progress-every", type=int, default=20, help="每完成N条打印一次进度")
    return parser


def main() -> int:
    _load_env_files()
    args = _build_parser().parse_args()
    input_path, output_path = _resolve_io_paths(args.input_excel, args.output_excel)
    try:
        expert_json, expert_path = _load_expert_experience_json(args)
    except Exception as exc:
        logger.error("加载专家经验失败：%s", exc)
        return 2
    if not input_path.is_file():
        logger.error("输入文件不存在：%s", input_path)
        return 2
    try:
        wb, ws = _load_input_workbook(input_path, _to_text(args.sheet_name))
    except Exception as exc:
        logger.error("加载输入文件失败：%s 错误=%s", input_path, exc)
        return 2
    columns = _header_map(ws)
    if args.first_real_col not in columns:
        logger.error("未找到一级真值列：%s", args.first_real_col)
        return 2
    if args.second_real_col not in columns:
        logger.error("未找到二级真值列：%s", args.second_real_col)
        return 2
    _ensure_columns(ws, columns, [DEFAULT_FIRST_LAWS_COL, DEFAULT_SECOND_LAWS_COL, DEFAULT_SECOND_VERIFY_COL, DEFAULT_SECOND_CHECKLIST_COL])
    run_rows = _select_run_rows(ws, int(args.start), int(args.limit))
    if not run_rows:
        _save_output_snapshot(wb, ws, output_path, [])
        logger.info("无可处理样本，已输出：%s", output_path)
        return 0
    progress_every = max(1, int(args.progress_every))
    first_hits = 0
    second_hits = 0
    total = len(run_rows)
    logger.info("开始补充专家经验法律：总样本=%s 范围起点=%s 限制=%s 来源=%s", total, int(args.start), int(args.limit), expert_path)
    for idx, row_num in enumerate(run_rows, start=1):
        first_real = _to_text(ws.cell(row=row_num, column=columns[args.first_real_col]).value)
        second_real = _to_text(ws.cell(row=row_num, column=columns[args.second_real_col]).value)
        first_laws_text, first_hit = _collect_first_level_laws(expert_json, first_real, row_num)
        second_laws_text, second_hit = _collect_second_level_laws(expert_json, first_real, second_real, row_num)
        second_verify_text, second_checklist_text, second_detail_hit = _collect_second_level_details(expert_json, first_real, second_real)
        if not first_hit:
            logger.warning("第%s行 未命中一级专家经验 一级=%s", row_num, first_real)
        if first_hit:
            first_hits += 1
        if second_hit:
            second_hits += 1
        else:
            logger.warning("第%s行 未命中二级专家经验 一级=%s 二级=%s", row_num, first_real, second_real)
        if not second_detail_hit:
            logger.warning("第%s行 未命中二级专家经验详情 一级=%s 二级=%s", row_num, first_real, second_real)
        _write_row(ws, columns, row_num, first_laws_text, second_laws_text, second_verify_text, second_checklist_text)
        logger.info("第%s行 补充完成 一级=%s 二级=%s 一级法律数=%s 二级法律数=%s 核实要点数=%s 调阅清单数=%s", row_num, first_real, second_real, len([x for x in first_laws_text.split('\n') if _to_text(x)]), len([x for x in second_laws_text.split('\n') if _to_text(x)]), len([x for x in second_verify_text.split('\n') if _to_text(x)]), len([x for x in second_checklist_text.split('\n') if _to_text(x)]))
        if idx % progress_every == 0 or idx == total:
            logger.info("进度 %s/%s 一级命中=%s 二级命中=%s", idx, total, first_hits, second_hits)
    _save_output_snapshot(wb, ws, output_path, run_rows)
    logger.info("补充完成：输出=%s 总样本=%s 一级命中=%s 二级命中=%s", output_path, total, first_hits, second_hits)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
