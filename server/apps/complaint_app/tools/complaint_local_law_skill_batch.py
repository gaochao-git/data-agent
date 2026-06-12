from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
import sys
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional runtime dependency
    load_dotenv = None

SERVER_DIR = Path(__file__).resolve().parents[3]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from apps.complaint_app.core.llm.llm_client import AsyncLLMClient, ModelConfig
from apps.complaint_app.core.model_config import get_complaint_law_analysis_model
from apps.complaint_app.opencode_runtime.main_agent import MainAgent
from apps.complaint_app.opencode_runtime.planner import PlanResult, PlanStep
from apps.complaint_app.opencode_runtime.session import Session
from utils.llm_thinking import build_thinking_extra_body
from utils.logger import get_logger


logger = get_logger(__name__)
DEFAULT_ITEM_COL = "举报事项"
DEFAULT_STORY_COL = "摘要"
DEFAULT_SKILL_NAME = "complaint-law-analysis"
DEFAULT_SKILL_PROMPT = "基于提供材料使用本地laws分析举报事项并给出法律依据。"
DEFAULT_ANALYSIS_COL = "本地法律_分析原文"
DEFAULT_LAWS_COL = "本地法律_法律列表"
DEFAULT_LAW_ITEMS_COL = "本地法律_法规条款数组"
DEFAULT_COMMANDS_COL = "本地法律_shell命令"


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


# 读取环境变量文件（.env 与 server/.env）。
def _load_env_files() -> None:
    if load_dotenv is None:
        return
    load_dotenv()
    server_env = SERVER_DIR / ".env"
    if server_env.is_file():
        try:
            content = server_env.read_text(encoding="utf-8")
            has_openai_key = any(line.strip().startswith("OPENAI_API_KEY=") for line in content.splitlines() if line.strip() and not line.strip().startswith("#"))
            has_openai_base = any(line.strip().startswith("OPENAI_BASE_URL=") for line in content.splitlines() if line.strip() and not line.strip().startswith("#"))
            if has_openai_key and "OPENAI_API_KEY" in os.environ:
                os.environ.pop("OPENAI_API_KEY", None)
            if has_openai_base and "OPENAI_BASE_URL" in os.environ:
                os.environ.pop("OPENAI_BASE_URL", None)
        except Exception:
            pass
        load_dotenv(server_env, override=True)


# 统一读取响应消息字段值。
def _message_field(message: Any, field: str) -> Any:
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
        except Exception:
            return None
    return None


# 兼容 content 为字符串或数组的场景。
def _normalize_message_content(value: Any) -> str:
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


# 从模型响应中提取最终文本。
def _response_text(resp: Any) -> str:
    choices = getattr(resp, "choices", None) or []
    if not choices:
        return ""
    choice = choices[0]
    message = getattr(choice, "message", None)
    if not message:
        return ""
    content = _normalize_message_content(_message_field(message, "content")).strip()
    if content:
        return content
    for field in ("reasoning_content", "reasoning", "analysis", "thoughts", "think"):
        fallback = _normalize_message_content(_message_field(message, field)).strip()
        if fallback:
            return fallback
    return ""


# 从字符串中定位JSON片段。
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


# 尝试将任意文本解析为JSON对象。
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


# 选择本次运行行号（2-based）。
def _select_run_rows(ws: Any, start: int, limit: int) -> list[int]:
    all_rows = list(range(2, ws.max_row + 1))
    s = max(0, int(start))
    e = len(all_rows)
    if int(limit) > 0:
        e = min(e, s + int(limit))
    return all_rows[s:e]


# 解析输入输出文件路径。
def _resolve_io_paths(input_excel: str, output_excel: str) -> tuple[Path, Path]:
    input_path = Path(input_excel).expanduser().resolve()
    output_path = Path(output_excel).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    return input_path, output_path


# 读取CSV全部行（优先 utf-8-sig，失败回退 gb18030）。
def _read_csv_rows(input_path: Path) -> list[list[str]]:
    try:
        with input_path.open("r", encoding="utf-8-sig", newline="") as f:
            return [list(row) for row in csv.reader(f)]
    except UnicodeDecodeError:
        with input_path.open("r", encoding="gb18030", newline="") as f:
            return [list(row) for row in csv.reader(f)]


# 按输入文件类型加载工作簿与工作表（支持 xlsx/csv）。
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


# 保存CSV（仅导出本次处理行）。
def _save_sheet_to_csv(ws: Any, output_path: Path, run_rows: list[int]) -> None:
    with output_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        headers = [_to_text(ws.cell(row=1, column=col).value) for col in range(1, ws.max_column + 1)]
        writer.writerow(headers)
        for row_num in run_rows:
            row = [ws.cell(row=row_num, column=col).value for col in range(1, ws.max_column + 1)]
            writer.writerow(["" if v is None else v for v in row])


# 落盘快照（支持xlsx/csv）。
def _save_output_snapshot(wb: Any, ws: Any, output_path: Path, run_rows: list[int]) -> None:
    if output_path.suffix.lower() == ".csv":
        _save_sheet_to_csv(ws, output_path, run_rows)
    else:
        wb.save(output_path)


# 将摘要与举报事项拼为技能 source_text。
def _build_source_text(item: str, story: str) -> str:
    item_text = _to_text(item)
    story_text = _to_text(story)
    if story_text:
        return f"摘要：{story_text}\n举报事项：{item_text}"
    return f"举报事项：{item_text}" if item_text else ""


# 构造只执行 skill 的单步计划。
def _build_skill_plan(skill_name: str, source_text: str) -> PlanResult:
    inputs: dict[str, Any] = {"name": skill_name}
    if source_text:
        inputs["source_text"] = source_text
    step = PlanStep(step_id="run_skill", tool="skill", kind="skill", inputs=inputs, description=f"run skill {skill_name}")
    return PlanResult(mode="sequential", steps=[step], reason="manual_skill_batch", max_parallel=1)


# 执行单行 skill 分析（复用 runtime skill + shell 工具链）。
def _extract_shell_commands_text(skill_result: dict[str, Any]) -> str:
    metadata = skill_result.get("metadata")
    if not isinstance(metadata, dict):
        return ""
    commands = metadata.get("tool_commands")
    if isinstance(commands, list):
        return "\n".join([_to_text(item) for item in commands if _to_text(item)])
    if isinstance(commands, str):
        return _to_text(commands)
    return ""


def _extract_skill_runtime_stats(skill_result: dict[str, Any]) -> dict[str, int]:
    metadata = skill_result.get("metadata")
    if not isinstance(metadata, dict):
        return {"tool_rounds": 0, "parallel_rounds": 0, "shell_call_count": 0}
    return {
        "tool_rounds": int(metadata.get("tool_rounds") or 0),
        "parallel_rounds": int(metadata.get("parallel_rounds") or 0),
        "shell_call_count": int(metadata.get("shell_call_count") or 0),
    }


async def _run_skill_analysis(agent: MainAgent, skill_name: str, prompt: str, source_text: str, row_num: int, max_retries: int) -> tuple[str, str, dict[str, int]]:
    retry_n = max(1, int(max_retries))
    for attempt in range(1, retry_n + 1):
        try:
            plan = _build_skill_plan(skill_name, source_text)
            session = Session(session_id=str(uuid.uuid4()))
            outputs = await agent._execute_plan(plan, session, agent_name=None, user_input=prompt)
            result = outputs.get("run_skill") or {}
            analysis_text = _to_text(result.get("output"))
            commands_text = _extract_shell_commands_text(result)
            skill_stats = _extract_skill_runtime_stats(result)
            command_count = len([x for x in commands_text.split("\n") if _to_text(x)])
            logger.info("第%s行 本地法律skill完成 尝试=%s 输出长度=%s 命令数=%s 轮次=%s 并行轮次=%s shell调用=%s", row_num, attempt, len(analysis_text), command_count, skill_stats.get("tool_rounds"), skill_stats.get("parallel_rounds"), skill_stats.get("shell_call_count"))
            return analysis_text, commands_text, skill_stats
        except Exception as exc:
            logger.warning("第%s行 本地法律skill失败 尝试=%s 错误=%s", row_num, attempt, exc)
            if attempt >= retry_n:
                logger.error("第%s行 本地法律skill失败 达到最大重试次数=%s", row_num, retry_n)
                return "", "", {"tool_rounds": 0, "parallel_rounds": 0, "shell_call_count": 0}
            await asyncio.sleep(1.0 * attempt)
    return "", "", {"tool_rounds": 0, "parallel_rounds": 0, "shell_call_count": 0}


# 正则提取《法规名》列表。
def _regex_extract_laws(text: str) -> list[str]:
    laws = re.findall(r"《[^》\n]{1,120}》", _to_text(text))
    dedup: list[str] = []
    seen: set[str] = set()
    for law in laws:
        if law not in seen:
            dedup.append(law)
            seen.add(law)
    return dedup


# 规范化模型提取的法律列表。
def _normalize_laws(value: Any) -> list[str]:
    if isinstance(value, str):
        laws = [x for x in _regex_extract_laws(value) if _to_text(x)]
    elif isinstance(value, list):
        laws = [_to_text(x) for x in value if _to_text(x)]
    else:
        laws = []
    dedup: list[str] = []
    seen: set[str] = set()
    for law in laws:
        if law not in seen:
            dedup.append(law)
            seen.add(law)
    return dedup


# 规范化模型提取的法规条款数组。
def _normalize_law_items(value: Any) -> list[dict[str, Any]]:
    items = value if isinstance(value, list) else []
    grouped: OrderedDict[str, list[str]] = OrderedDict()
    for item in items:
        if not isinstance(item, dict):
            continue
        law = _to_text(item.get("law"))
        raw_items = item.get("items")
        clauses = []
        if isinstance(raw_items, list):
            clauses = [_to_text(x) for x in raw_items if _to_text(x)]
        elif _to_text(raw_items):
            clauses = [_to_text(raw_items)]
        if not law:
            continue
        if law not in grouped:
            grouped[law] = []
        for clause in clauses:
            if clause not in grouped[law]:
                grouped[law].append(clause)
    return [{"law": law, "items": clauses} for law, clauses in grouped.items()]


# 构造提取模型消息。
def _build_extract_messages(analysis_text: str) -> list[dict[str, str]]:
    system_text = (
        "你是法规信息结构化抽取器。"
        "任务：从输入分析原文中抽取法律名称与条款。"
        "只输出JSON对象，不要输出其他文字。"
        'JSON格式固定为：{"laws":["《法规名》"],"law_items":[{"law":"《法规名》","items":["第X条"]}]}.'
        "若没有条款，law_items可为空数组；若没有法律，laws可为空数组。"
    )
    user_text = "请仅基于以下分析原文抽取法律与条款：\n" + f"分析原文：\n{analysis_text}"
    return [{"role": "system", "content": system_text}, {"role": "user", "content": user_text}]


# 对单行分析原文做模型抽取（每条都执行）。
async def _extract_with_model(llm: AsyncLLMClient, model: str, analysis_text: str, row_num: int, max_retries: int) -> tuple[list[str], list[dict[str, Any]], bool]:
    if not _to_text(analysis_text):
        return [], [], False
    retry_n = max(1, int(max_retries))
    messages = _build_extract_messages(analysis_text)
    thinking_extra_body = build_thinking_extra_body(model, False)
    for attempt in range(1, retry_n + 1):
        try:
            logger.info("第%s行 提取模型开始 尝试=%s", row_num, attempt)
            logger.info("第%s行 提取模型请求 model=%s extra_body=%s messages=%s", row_num, model, json.dumps(thinking_extra_body, ensure_ascii=False), json.dumps(messages, ensure_ascii=False))
            resp = await llm.chat(messages=messages, model=model, temperature=0.0, extra_body=thinking_extra_body)
            text = _response_text(resp)
            logger.info("第%s行 提取模型原始输出=%s", row_num, json.dumps(text, ensure_ascii=False))
            parsed = _parse_json_any(text)
            if not isinstance(parsed, dict):
                parsed = _parse_json_any(_extract_json_fragment(text))
            if not isinstance(parsed, dict):
                raise ValueError("extractor output is not json object")
            laws = _normalize_laws(parsed.get("laws"))
            law_items = _normalize_law_items(parsed.get("law_items"))
            logger.info("第%s行 提取模型完成 尝试=%s 法律数=%s 条款法数=%s laws=%s law_items=%s", row_num, attempt, len(laws), len(law_items), json.dumps(laws, ensure_ascii=False), json.dumps(law_items, ensure_ascii=False))
            return laws, law_items, True
        except Exception as exc:
            logger.warning("第%s行 提取模型失败 尝试=%s 错误=%s", row_num, attempt, exc)
            if attempt >= retry_n:
                logger.warning("第%s行 提取模型失败，返回空结果", row_num)
                return [], [], False
            await asyncio.sleep(1.0 * attempt)
    return [], [], False


# 法律名列表按换行拼接。
def _laws_to_text(laws: list[str]) -> str:
    return "\n".join([_to_text(x) for x in laws if _to_text(x)])


# 单行执行：skill分析 + 正则 + 模型提取。
async def _run_single_row(agent: MainAgent, llm: AsyncLLMClient, model: str, skill_name: str, skill_prompt: str, row_num: int, item: str, story: str, max_retries: int) -> dict[str, Any]:
    started_at = time.time()
    logger.info("第%s行 行任务开始", row_num)
    source_text = _build_source_text(item, story)
    analysis_text, commands_text, skill_stats = await _run_skill_analysis(agent, skill_name, skill_prompt, source_text, row_num, max_retries)
    laws, law_items, extract_ok = await _extract_with_model(llm, model, analysis_text, row_num, max_retries)
    result = {
        "analysis_text": analysis_text,
        "commands_text": commands_text,
        "laws_text": _laws_to_text(laws),
        "law_items_json": json.dumps(law_items, ensure_ascii=False),
        "skill_ok": bool(_to_text(analysis_text)),
        "extract_ok": bool(extract_ok),
        "tool_rounds": int(skill_stats.get("tool_rounds") or 0),
        "parallel_rounds": int(skill_stats.get("parallel_rounds") or 0),
        "shell_call_count": int(skill_stats.get("shell_call_count") or 0),
    }
    logger.info("第%s行 行任务完成 耗时=%.3fs skill成功=%s 提取成功=%s 轮次=%s 并行轮次=%s shell调用=%s", row_num, time.time() - started_at, bool(result.get("skill_ok")), bool(result.get("extract_ok")), int(result.get("tool_rounds") or 0), int(result.get("parallel_rounds") or 0), int(result.get("shell_call_count") or 0))
    return result


# 执行单行任务并返回行号结果。
async def _run_row_task(agent: MainAgent, llm: AsyncLLMClient, sem: asyncio.Semaphore, row_num: int, item: str, story: str, model: str, skill_name: str, skill_prompt: str, max_retries: int) -> tuple[int, dict[str, Any]]:
    async with sem:
        result = await _run_single_row(agent, llm, model, skill_name, skill_prompt, row_num, item, story, max_retries)
        return row_num, result


# 写回单行结果。
def _write_row_result(ws: Any, columns: dict[str, int], row_num: int, result: dict[str, Any]) -> tuple[bool, bool]:
    analysis_text = _to_text(result.get("analysis_text"))
    commands_text = _to_text(result.get("commands_text"))
    laws_text = _to_text(result.get("laws_text"))
    law_items_json = _to_text(result.get("law_items_json"))
    ws.cell(row=row_num, column=columns[DEFAULT_ANALYSIS_COL], value=analysis_text)
    ws.cell(row=row_num, column=columns[DEFAULT_COMMANDS_COL], value=commands_text)
    ws.cell(row=row_num, column=columns[DEFAULT_LAWS_COL], value=laws_text)
    ws.cell(row=row_num, column=columns[DEFAULT_LAW_ITEMS_COL], value=law_items_json)
    skill_ok = bool(result.get("skill_ok"))
    extract_ok = bool(result.get("extract_ok"))
    logger.info("第%s行 写回完成 skill成功=%s 提取成功=%s 法律数=%s", row_num, skill_ok, extract_ok, len([x for x in laws_text.split('\n') if _to_text(x)]))
    return skill_ok, extract_ok


# 并发处理全部行并实时落盘。
async def _run_batch_rows(agent: MainAgent, llm: AsyncLLMClient, wb: Any, ws: Any, columns: dict[str, int], run_rows: list[int], output_path: Path, args: argparse.Namespace, model: str) -> tuple[int, int, int, int, int]:
    sem = asyncio.Semaphore(max(1, int(args.max_concurrent)))
    progress_every = max(1, int(args.progress_every))
    skill_success = 0
    extract_success = 0
    total_tool_rounds = 0
    total_parallel_rounds = 0
    total_shell_calls = 0
    completed = 0
    started_at = time.time()
    tasks = []
    for row_num in run_rows:
        item = _to_text(ws.cell(row=row_num, column=columns[args.item_col]).value)
        story = _to_text(ws.cell(row=row_num, column=columns[args.story_col]).value)
        tasks.append(asyncio.create_task(_run_row_task(agent, llm, sem, row_num, item, story, model, _to_text(args.skill_name), _to_text(args.skill_prompt), max(1, int(args.max_retries)))))
    for coro in asyncio.as_completed(tasks):
        row_num, result = await coro
        s_ok, e_ok = _write_row_result(ws, columns, row_num, result)
        if s_ok:
            skill_success += 1
        if e_ok:
            extract_success += 1
        total_tool_rounds += int(result.get("tool_rounds") or 0)
        total_parallel_rounds += int(result.get("parallel_rounds") or 0)
        total_shell_calls += int(result.get("shell_call_count") or 0)
        completed += 1
        _save_output_snapshot(wb, ws, output_path, run_rows)
        if completed % progress_every == 0 or completed == len(run_rows):
            elapsed = time.time() - started_at
            logger.info("进度 %s/%s skill成功=%s 提取成功=%s 总轮次=%s 并行轮次=%s shell调用=%s 用时=%.1fs", completed, len(run_rows), skill_success, extract_success, total_tool_rounds, total_parallel_rounds, total_shell_calls, elapsed)
    return skill_success, extract_success, total_tool_rounds, total_parallel_rounds, total_shell_calls


# 保存指标JSON。
def _save_metrics(output_path: Path, args: argparse.Namespace, total: int, skill_success: int, extract_success: int, total_tool_rounds: int, total_parallel_rounds: int, total_shell_calls: int) -> None:
    metrics = {
        "rows_total": total,
        "rows_skill_success": skill_success,
        "rows_skill_failed": max(0, total - skill_success),
        "rows_extract_success": extract_success,
        "rows_extract_fallback": max(0, total - extract_success),
        "tool_rounds_total": total_tool_rounds,
        "parallel_tool_rounds_total": total_parallel_rounds,
        "shell_calls_total": total_shell_calls,
        "avg_tool_rounds_per_row": round(float(total_tool_rounds) / float(total), 4) if total > 0 else 0.0,
        "avg_shell_calls_per_row": round(float(total_shell_calls) / float(total), 4) if total > 0 else 0.0,
    }
    metrics_path = Path(args.metrics_json).expanduser().resolve() if _to_text(args.metrics_json) else output_path.with_suffix(".metrics.json")
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("已保存指标：%s", metrics_path)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="批量执行本地法律skill（后半程），并抽取法律列表与法规条款数组。")
    parser.add_argument("--api-key", default="", help="可选：覆盖 OPENAI_API_KEY")
    parser.add_argument("--base-url", default="", help="可选：覆盖 OPENAI_BASE_URL")
    parser.add_argument("--input-excel", required=True, help="输入文件路径（支持 .xlsx/.csv）")
    parser.add_argument("--output-excel", required=True, help="输出文件路径（支持 .xlsx/.csv）")
    parser.add_argument("--sheet-name", default="", help="可选：sheet名称，默认首个")
    parser.add_argument("--item-col", default=DEFAULT_ITEM_COL, help="举报事项列名")
    parser.add_argument("--story-col", default=DEFAULT_STORY_COL, help="摘要列名")
    parser.add_argument("--skill-name", default=DEFAULT_SKILL_NAME, help="skill名称")
    parser.add_argument("--skill-prompt", default=DEFAULT_SKILL_PROMPT, help="skill执行提示词")
    parser.add_argument("--extract-model", default="", help="提取模型名，默认 COMPLAINT_LAW_ANALYSIS_MODEL")
    parser.add_argument("--start", type=int, default=0, help="起始数据行(0-based, 含)")
    parser.add_argument("--limit", type=int, default=0, help="处理条数，0=到结尾")
    parser.add_argument("--max-concurrent", type=int, default=4, help="最大并发数")
    parser.add_argument("--max-retries", type=int, default=3, help="单条最大重试次数")
    parser.add_argument("--progress-every", type=int, default=1, help="每完成N条打印一次进度")
    parser.add_argument("--no-proxy", action="store_true", help="强制直连，不读取系统代理环境变量")
    parser.add_argument("--metrics-json", default="", help="可选：指标json输出路径")
    return parser


# 运行前配置环境变量。
def _configure_runtime_env(args: argparse.Namespace) -> None:
    if _to_text(args.api_key):
        os.environ["OPENAI_API_KEY"] = _to_text(args.api_key)
    if _to_text(args.base_url):
        os.environ["OPENAI_BASE_URL"] = _to_text(args.base_url)
    if args.no_proxy:
        os.environ["OPENAI_HTTP_TRUST_ENV"] = "false"


async def _run_async(args: argparse.Namespace) -> int:
    # 1) 初始化环境与参数。
    _load_env_files()
    _configure_runtime_env(args)
    model = _to_text(args.extract_model) or get_complaint_law_analysis_model()
    input_path, output_path = _resolve_io_paths(args.input_excel, args.output_excel)
    if not input_path.is_file():
        logger.error("输入文件不存在：%s", input_path)
        return 2
    if not _to_text(os.getenv("OPENAI_API_KEY")):
        logger.error("缺少 OPENAI_API_KEY（可通过 --api-key 或环境变量设置）")
        return 2

    # 2) 加载工作簿并校验输入列。
    try:
        wb, ws = _load_input_workbook(input_path, _to_text(args.sheet_name))
    except Exception as exc:
        logger.error("加载输入文件失败：%s 错误=%s", input_path, exc)
        return 2
    columns = _header_map(ws)
    if args.item_col not in columns:
        logger.error("未找到举报事项列：%s", args.item_col)
        return 2
    if args.story_col not in columns:
        logger.error("未找到摘要列：%s", args.story_col)
        return 2
    _ensure_columns(ws, columns, [DEFAULT_ANALYSIS_COL, DEFAULT_COMMANDS_COL, DEFAULT_LAWS_COL, DEFAULT_LAW_ITEMS_COL])
    run_rows = _select_run_rows(ws, int(args.start), int(args.limit))
    if not run_rows:
        _save_output_snapshot(wb, ws, output_path, [])
        logger.info("无可处理样本，已输出：%s", output_path)
        return 0

    # 3) 执行并发处理（skill分析 + 模型提取）。
    logger.info("开始执行本地法律skill批处理：总样本=%s 模型=%s skill=%s 最大并发=%s", len(run_rows), model, _to_text(args.skill_name), int(args.max_concurrent))
    agent = MainAgent()
    llm = AsyncLLMClient(config=ModelConfig(default_model=model), name="local_law_extractor")
    skill_success, extract_success, total_tool_rounds, total_parallel_rounds, total_shell_calls = await _run_batch_rows(agent, llm, wb, ws, columns, run_rows, output_path, args, model)

    # 4) 收尾输出结果与指标。
    _save_output_snapshot(wb, ws, output_path, run_rows)
    logger.info("已保存输出：%s", output_path)
    _save_metrics(output_path, args, len(run_rows), skill_success, extract_success, total_tool_rounds, total_parallel_rounds, total_shell_calls)
    return 0


def main() -> int:
    args = _build_parser().parse_args()
    return asyncio.run(_run_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
