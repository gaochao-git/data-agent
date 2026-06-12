from __future__ import annotations

import argparse
import asyncio
import csv
from functools import partial
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional runtime dependency
    load_dotenv = None

SERVER_DIR = Path(__file__).resolve().parents[3]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from utils.logger import get_logger
from utils.llm_thinking import build_thinking_extra_body


logger = get_logger(__name__)
DEFAULT_FIRST_SYSTEM_PROMPT = SERVER_DIR / "apps" / "complaint_app" / "tools" / "prompts" / "complaint_expert_classification_first_system.txt"
DEFAULT_SECOND_SYSTEM_PROMPT = SERVER_DIR / "apps" / "complaint_app" / "tools" / "prompts" / "complaint_expert_classification_second_system.txt"
DEFAULT_FIRST_USER_PROMPT = "举报信背景：{{#sys.story#}}\n举报事项：{{#sys.jb_item#}}"
DEFAULT_SECOND_USER_PROMPT = "举报信背景：{{#sys.story#}}\n举报事项：{{#sys.jb_item#}}"


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _load_env_files() -> None:
    if load_dotenv is None:
        return
    load_dotenv()
    server_env = SERVER_DIR / ".env"
    if server_env.is_file():
        load_dotenv(server_env, override=False)


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
            logger.info("响应content为空，回退字段=%s", field)
            return fallback
    return ""


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


def _split_think_and_content(text: str) -> tuple[str, str]:
    raw = _to_text(text)
    if not raw:
        return "", ""
    if "</think>" not in raw:
        return "", raw
    parts = raw.split("</think>")
    think = parts[0].replace("<think>", "").strip()
    content = "</think>".join(parts[1:]).strip()
    return think, content


def _extract_json_list_from_llm_text(text: str) -> tuple[list[dict[str, Any]], str]:
    think, content = _split_think_and_content(text)
    cleaned = content.replace("```json", "").replace("```", "").strip()
    parsed = _parse_json_any(cleaned)
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)], think
    if isinstance(parsed, dict):
        obj = parsed.get("obj")
        if isinstance(obj, list):
            return [item for item in obj if isinstance(item, dict)], think
    return [], think


def _resolve_data_root() -> Path:
    env_value = _to_text(os.getenv("DATA_ROOT"))
    if not env_value:
        raise ValueError("缺少 DATA_ROOT，请在环境变量中配置数据根目录")
    path = Path(env_value)
    return path if path.is_absolute() else SERVER_DIR / path


def _latest_subdir(base_dir: Path) -> Path | None:
    if not base_dir.is_dir():
        return None
    dirs = [p for p in base_dir.iterdir() if p.is_dir()]
    if not dirs:
        return None
    return sorted(dirs, key=_latest_subdir_sort_key, reverse=True)[0]


# 计算专家经验目录的排序键。
def _latest_subdir_sort_key(path: Path) -> tuple[str, float]:
    pattern = re.compile(r"^(\d{8})_(\d{6})_.+")
    m = pattern.match(path.name)
    if m:
        return (f"{m.group(1)}{m.group(2)}", path.stat().st_mtime)
    return ("", path.stat().st_mtime)


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


def _header_map(ws) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for col in range(1, ws.max_column + 1):
        name = _to_text(ws.cell(row=1, column=col).value)
        if name and name not in mapping:
            mapping[name] = col
    return mapping


def _ensure_columns(ws, mapping: dict[str, int], names: list[str]) -> None:
    for name in names:
        if name in mapping:
            continue
        col = ws.max_column + 1
        ws.cell(row=1, column=col, value=name)
        mapping[name] = col


def _load_text_file(path: Path, label: str) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"{label} file not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def _candidate_first_second(item: dict[str, Any]) -> tuple[str, str]:
    first = _to_text(item.get("一级分类") or item.get("first_category") or item.get("分类"))
    second = _to_text(item.get("二级分类") or item.get("second_category") or item.get("overview"))
    return first, second


# 计算“任一候选命中”与“同一候选双命中”的三列判定值。
def _compute_any_hit_columns(candidates_text: str, first_real: str, second_real: str) -> tuple[str, str, str]:
    parsed = _parse_json_any(candidates_text)
    if _to_text(candidates_text) and not isinstance(parsed, list):
        logger.warning("二阶段候选JSON解析失败，已按空候选处理")
    candidates = [item for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []
    first_any_hit = ""
    second_any_hit = ""
    final_correct = ""
    if first_real:
        first_any_hit = "是" if any(_candidate_first_second(item)[0] == first_real for item in candidates) else "否"
    if second_real:
        second_any_hit = "是" if any(_candidate_first_second(item)[1] == second_real for item in candidates) else "否"
    if first_real and second_real:
        final_correct = "是" if any(_candidate_first_second(item)[0] == first_real and _candidate_first_second(item)[1] == second_real for item in candidates) else "否"
    return first_any_hit, second_any_hit, final_correct


def _save_sheet_to_csv(ws: Any, output_path: Path, analyzed_rows: set[int] | None = None, first_real_col: str = "一级分类_real", second_real_col: str = "二级分类_real") -> None:
    with output_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        headers = [_to_text(ws.cell(row=1, column=col).value) for col in range(1, ws.max_column + 1)]
        writer.writerow(headers)
        if not analyzed_rows:
            return
        for row_num in sorted([r for r in analyzed_rows if 2 <= r <= ws.max_row]):
            row = [ws.cell(row=row_num, column=col).value for col in range(1, ws.max_column + 1)]
            writer.writerow(["" if v is None else v for v in row])


def _save_output_snapshot(wb: Any, ws: Any, output_path: Path, analyzed_rows: set[int] | None = None, first_real_col: str = "一级分类_real", second_real_col: str = "二级分类_real") -> None:
    if output_path.suffix.lower() == ".csv":
        _save_sheet_to_csv(ws, output_path, analyzed_rows, first_real_col, second_real_col)
    else:
        wb.save(output_path)


def _md_escape(value: str) -> str:
    return _to_text(value).replace("|", "\\|").replace("\n", "<br>")


def _save_preview_markdown(ws: Any, output_path: Path, analyzed_rows: set[int] | None = None) -> None:
    headers = _header_map(ws)
    preferred = ["举报事项", "摘要", "一级分类_real", "二级分类_real", "一级分类_pred", "二级分类_pred", "一级分类_pred_raw", "二级分类_pred_raw", "一级命中状态", "二级命中状态", "一级命中_任一候选", "二级命中_任一候选", "分析是否正确", "匹配度", "一级是否正确", "二级是否正确", "分类理由", "二阶段候选_json"]
    cols = [name for name in preferred if name in headers]
    if not cols:
        return
    md_path = output_path.with_suffix(".preview.md")
    with md_path.open("w", encoding="utf-8") as f:
        f.write("# 结果预览\n\n")
        if analyzed_rows:
            row_numbers = sorted([r for r in analyzed_rows if 2 <= r <= ws.max_row])
        else:
            row_numbers = list(range(2, ws.max_row + 1))
        f.write(f"- 总行数（不含表头）：{len(row_numbers)}\n\n")
        f.write("| " + " | ".join(cols) + " |\n")
        f.write("| " + " | ".join(["---"] * len(cols)) + " |\n")
        for row in row_numbers:
            row_vals: list[str] = []
            for col_name in cols:
                raw = _to_text(ws.cell(row=row, column=headers[col_name]).value)
                value = (raw[:180] + "...") if len(raw) > 180 else raw
                row_vals.append(_md_escape(value))
            f.write("| " + " | ".join(row_vals) + " |\n")
    logger.info("已保存预览文档：%s", md_path)


def _render_template(text: str, variables: dict[str, str]) -> str:
    rendered = re.sub(r"\{\{#([^#]+)#\}\}", partial(_render_template_match, variables=variables), text or "")
    return re.sub(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}", partial(_render_template_match, variables=variables), rendered)


# 渲染模板占位符为变量值。
def _render_template_match(match: re.Match, variables: dict[str, str]) -> str:
    expr = _to_text(match.group(1))
    key = expr.split(".")[-1] if expr else ""
    return variables.get(key, "")


# 从单个一级分类的专家经验中提取二级分类及其描述。
def _extract_second_level_items(value: Any) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    items = value if isinstance(value, list) else ([value] if isinstance(value, dict) else [])
    for item in items:
        if not isinstance(item, dict):
            continue
        second_label = _to_text(item.get("overview") or item.get("二级分类") or item.get("second_category"))
        second_desc = _to_text(item.get("second_category_description") or item.get("二级分类说明") or item.get("二级分类的描述") or item.get("description"))
        if not second_label:
            continue
        dedup_key = (second_label, second_desc)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        result.append({"二级分类": second_label, "二级分类的描述": second_desc})
    return result


# 从单个一级分类的专家经验中提取一级描述与名词解释（各取首个非空）。
def _extract_first_level_meta(value: Any) -> tuple[str, str]:
    items = value if isinstance(value, list) else ([value] if isinstance(value, dict) else [])
    first_desc = ""
    glossary = ""
    for item in items:
        if not isinstance(item, dict):
            continue
        if not first_desc:
            first_desc = _to_text(item.get("first_category_description") or item.get("一级分类说明"))
        if not glossary:
            glossary = _to_text(item.get("glossary") or item.get("名词辨析"))
        if first_desc and glossary:
            break
    return first_desc, glossary


# 按一级分类聚合专家经验，并输出二级标签候选集合。
def _build_cate_know_grouped(first_keys: list[str], expert_experience_json: dict[str, Any], row_num: int) -> tuple[list[dict[str, Any]], list[str]]:
    grouped: list[dict[str, Any]] = []
    second_labels: list[str] = []
    seen_second: set[str] = set()
    total_n = len(first_keys)
    for idx, first_key in enumerate(first_keys, start=1):
        value = expert_experience_json.get(first_key)
        if value is None:
            logger.warning("第%s行 第二阶段准备 专家经验缺失 序号=%s/%s 一级=%s", row_num, idx, total_n, first_key)
        second_list = _extract_second_level_items(value)
        first_desc, glossary = _extract_first_level_meta(value)
        if not second_list:
            logger.warning("第%s行 第二阶段准备 一级=%s 未提取到二级分类列表", row_num, first_key)
        grouped.append({"一级分类": first_key, "一级分类描述": first_desc, "相关名词解释": glossary, "二级分类列表": second_list})
        for item in second_list:
            second_label = _to_text(item.get("二级分类"))
            if second_label and second_label not in seen_second:
                second_labels.append(second_label)
                seen_second.add(second_label)
    return grouped, second_labels


# 将按一级分类聚合的候选经验格式化为二级提示词文本。
def _format_cate_know_text(grouped: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in grouped:
        first_label = _to_text(item.get("一级分类"))
        first_desc = _to_text(item.get("一级分类描述"))
        glossary = _to_text(item.get("相关名词解释"))
        second_list = item.get("二级分类列表")
        second_data = second_list if isinstance(second_list, list) else []
        lines.append(f"一级分类是{first_label}，它的描述是：{first_desc}，相关名词解释：{glossary}，它的二级分类及描述列表如下：{json.dumps(second_data, ensure_ascii=False)}")
    return "\n".join(lines)


def _category_values_from_final_obj(final_obj: list[dict[str, Any]], first_stage_list: list[dict[str, Any]]) -> tuple[str, str, str, str]:
    first = ""
    second = ""
    score = ""
    reason = ""
    if final_obj:
        top = final_obj[0]
        first = _to_text(top.get("一级分类") or top.get("first_category") or top.get("分类"))
        second = _to_text(top.get("二级分类") or top.get("second_category") or top.get("overview"))
        score = _to_text(top.get("匹配度") or top.get("score"))
        reason = _to_text(top.get("分类匹配逻辑说明") or top.get("分类理由") or top.get("classification_criteria"))
    if not first and first_stage_list:
        top = first_stage_list[0]
        first = _to_text(top.get("分类") or top.get("一级分类") or top.get("first_category"))
        if not score:
            score = _to_text(top.get("匹配度") or top.get("score"))
        if not reason:
            reason = _to_text(top.get("分类理由") or top.get("分类匹配逻辑说明") or top.get("classification_criteria"))
    return first, second, score, reason


def _pred_matches_real(pred: str, real: str) -> bool:
    p = _to_text(pred)
    r = _to_text(real)
    if not p or not r:
        return False
    return p == r


def _pred_raw_hit_status(pred_raw: str, real: str) -> str:
    r = _to_text(real)
    p = _to_text(pred_raw)
    if not r:
        return ""
    if not p:
        return "未命中"
    return "命中" if p == r else "未命中"


async def _run_single_row(llm: Any, prompts: dict[str, Any], *, row_num: int, item: str, story: str, first_level_categories: str, first_level_labels: list[str], expert_experience_json: dict[str, Any], model: str, thinking_extra_body: dict[str, Any] | None, max_retries: int) -> dict[str, str]:
    # 阶段1：基础入参校验。
    if not _to_text(item):
        logger.warning("第%s行 行处理终止：举报事项为空", row_num)
        return {"status": "error", "error": "empty 举报事项", "first_pred": "", "second_pred": "", "first_pred_raw": "", "second_pred_raw": "", "match_score": "", "match_reason": ""}

    # 阶段2：准备第一阶段提示词与消息体。
    variables = {"jb_item": item, "story": story, "first_level_categories": first_level_categories}
    first_system_tpl = prompts["first_system"]
    first_system_text = _render_template(first_system_tpl, variables)
    first_messages = [
        {"role": "system", "content": first_system_text},
        {"role": "user", "content": _render_template(prompts["first_user"], variables)},
    ]
    logger.info("第%s行 行处理开始 最大重试=%s 举报事项长度=%s 摘要长度=%s", row_num, max(1, max_retries), len(_to_text(item)), len(_to_text(story)))
    logger.info("第%s行 阶段准备完成 一级候选总数=%s", row_num, len(first_level_labels))

    # 阶段3：按重试策略执行两阶段推理。
    for attempt in range(1, max(1, max_retries) + 1):
        try:
            t0 = time.time()
            # 阶段3.1：第一阶段分类请求与解析。
            logger.info("第%s行 第一阶段开始 尝试=%s", row_num, attempt)
            logger.info("第%s行 第一阶段请求 模型=%s 采样温度=0.0 思考参数=%s 系统提示长度=%s 用户提示长度=%s", row_num, model, thinking_extra_body, len(_to_text(first_messages[0].get("content"))), len(_to_text(first_messages[1].get("content"))))
            logger.info("第%s行 第一阶段输入消息=%s", row_num, json.dumps(first_messages, ensure_ascii=False))
            first_resp = await llm.chat(
                messages=first_messages,
                model=model,
                temperature=0.0,
                extra_body=thinking_extra_body,
            )
            first_text = _response_text(first_resp)
            logger.info("第%s行 第一阶段原始输出=%s", row_num, json.dumps(first_text, ensure_ascii=False))
            first_list, first_think = _extract_json_list_from_llm_text(first_text)
            logger.info("第%s行 第一阶段完成 候选数=%s 思考长度=%s 一级候选列表=%s", row_num, len(first_list), len(first_think), json.dumps(first_list, ensure_ascii=False))

            # 阶段3.2：提取并去重一级候选，作为第二阶段路由。
            first_candidates: list[str] = []
            first_candidates_raw: list[str] = []
            for item_obj in first_list:
                first_key_raw = _to_text(item_obj.get("分类") or item_obj.get("一级分类") or item_obj.get("first_category"))
                if first_key_raw:
                    first_candidates_raw.append(first_key_raw)
                    first_candidates.append(first_key_raw)
            dedup_first_candidates: list[str] = []
            seen_first: set[str] = set()
            for label in first_candidates:
                if label not in seen_first:
                    dedup_first_candidates.append(label)
                    seen_first.add(label)
            logger.info("第%s行 第二阶段一级候选数=%s 一级候选=%s", row_num, len(dedup_first_candidates), json.dumps(dedup_first_candidates, ensure_ascii=False))
            if not dedup_first_candidates:
                logger.error("第%s行 第一阶段后无可用一级候选，终止本行", row_num)
                return {"status": "error", "error": "no valid first-level candidates", "first_pred": "", "second_pred": "", "first_pred_raw": "；".join(first_candidates_raw), "second_pred_raw": "", "match_score": "", "match_reason": ""}

            # 阶段3.3：聚合一级候选对应经验后，仅执行一次第二阶段分类。
            cate_know_grouped, second_level_labels = _build_cate_know_grouped(dedup_first_candidates, expert_experience_json, row_num)
            cate_know_text = _format_cate_know_text(cate_know_grouped)
            second_level_categories = json.dumps(second_level_labels, ensure_ascii=False)
            variables2 = {"jb_item": item, "story": story, "cate_know": cate_know_text, "second_level_categories": second_level_categories}
            second_system_tpl = prompts["second_system"]
            second_system_text = _render_template(second_system_tpl, variables2)
            second_messages = [
                {"role": "system", "content": second_system_text},
                {"role": "user", "content": _render_template(prompts["second_user"], variables2)},
            ]
            logger.info("第%s行 第二阶段开始 单次调用 一级候选数=%s 一级分组数=%s 二级候选总数=%s", row_num, len(dedup_first_candidates), len(cate_know_grouped), len(second_level_labels))
            logger.info("第%s行 第二阶段请求 模型=%s 采样温度=0.0 思考参数=%s 系统提示长度=%s 用户提示长度=%s", row_num, model, thinking_extra_body, len(_to_text(second_messages[0].get("content"))), len(_to_text(second_messages[1].get("content"))))
            logger.info("第%s行 第二阶段输入消息=%s", row_num, json.dumps(second_messages, ensure_ascii=False))
            second_resp = await llm.chat(messages=second_messages, model=model, temperature=0.0, extra_body=thinking_extra_body)
            second_text = _response_text(second_resp)
            logger.info("第%s行 第二阶段原始输出=%s", row_num, json.dumps(second_text, ensure_ascii=False))
            final_obj, second_think = _extract_json_list_from_llm_text(second_text)
            logger.info("第%s行 第二阶段完成 候选数=%s 思考长度=%s 二级候选列表=%s", row_num, len(final_obj), len(second_think), json.dumps(final_obj, ensure_ascii=False))

            # 阶段3.4：汇总两阶段结果并返回单行输出。
            if not final_obj:
                logger.error("第%s行 第%s次尝试失败：第二阶段无候选结果 一级原始候选=%s 一级路由候选=%s", row_num, attempt, json.dumps(first_candidates_raw, ensure_ascii=False), json.dumps(dedup_first_candidates, ensure_ascii=False))
                return {"status": "error", "error": "no candidate results after second stage", "first_pred": "", "second_pred": "", "first_pred_raw": "", "second_pred_raw": "", "match_score": "", "match_reason": ""}
            else:
                _first_from_second, second_pred_raw, score, reason = _category_values_from_final_obj(final_obj, first_list)
                second_pred = _to_text(second_pred_raw)
                first_pred = _to_text(first_list[0].get("分类") or first_list[0].get("一级分类") or first_list[0].get("first_category")) if first_list else (_to_text(dedup_first_candidates[0]) if dedup_first_candidates else "")
                first_pred_raw = first_pred
                match_score = _to_text(score)
                match_reason = json.dumps(final_obj, ensure_ascii=False)
                logger.info("第%s行 第二阶段汇总完成 一级原始候选数=%s 二级候选数=%s", row_num, len(first_candidates_raw), len(final_obj))
                logger.info("第%s行 完成 结果数=%s 一级=%s 二级=%s 匹配度=%s 用时=%.3fs", row_num, 1, first_pred, second_pred, match_score, time.time() - t0)
                return {"status": "completed", "error": "", "first_pred": first_pred, "second_pred": second_pred, "first_pred_raw": first_pred_raw, "second_pred_raw": second_pred, "match_score": match_score, "match_reason": match_reason}
        except Exception as exc:
            # 阶段3.5：异常重试与最终失败返回。
            logger.warning("第%s行 尝试=%s 失败：%s", row_num, attempt, exc)
            if attempt >= max(1, max_retries):
                logger.error("第%s行 行处理失败：已达到最大重试次数=%s", row_num, max(1, max_retries))
                return {"status": "error", "error": str(exc), "first_pred": "", "second_pred": "", "first_pred_raw": "", "second_pred_raw": "", "match_score": "", "match_reason": ""}
            logger.info("第%s行 准备重试 下次尝试=%s 等待秒数=%.1f", row_num, attempt + 1, 1.5 * attempt)
            await asyncio.sleep(1.5 * attempt)

    # 阶段4：兜底返回（理论上不会进入）。
    logger.error("第%s行 行处理失败：未知错误", row_num)
    return {"status": "error", "error": "unknown error", "first_pred": "", "second_pred": "", "first_pred_raw": "", "second_pred_raw": "", "match_score": "", "match_reason": ""}


def _compute_metrics(records: list[dict[str, str]]) -> dict[str, Any]:
    completed = [r for r in records if _to_text(r.get("status")) == "completed"]
    if not completed:
        return {}

    l1_total = 0
    l1_correct = 0
    l2_total = 0
    l2_correct = 0
    l2_cond_total = 0
    l2_cond_correct = 0
    for row in completed:
        first_real = _to_text(row.get("first_real"))
        second_real = _to_text(row.get("second_real"))
        first_pred = _to_text(row.get("first_pred"))
        second_pred = _to_text(row.get("second_pred"))
        l1_ok = False
        if first_real:
            l1_total += 1
            if _pred_matches_real(first_pred, first_real):
                l1_correct += 1
                l1_ok = True
        if second_real:
            l2_total += 1
            if _pred_matches_real(second_pred, second_real):
                l2_correct += 1
            if l1_ok:
                l2_cond_total += 1
                if _pred_matches_real(second_pred, second_real):
                    l2_cond_correct += 1

    l1_acc = _safe_ratio(l1_correct, l1_total)
    l2_acc = _safe_ratio(l2_correct, l2_total)
    l2_cond_acc = _safe_ratio(l2_cond_correct, l2_cond_total)

    return {"completed_rows": len(completed), "completed_rows_desc": "成功完成并拿到工作流有效输出的条数", "l1_total": l1_total, "l1_correct": l1_correct, "l1_acc": l1_acc, "l1_formula": "l1_acc = l1_correct / l1_total", "l1_acc_percent": f"{l1_acc * 100:.2f}%", "l1_desc": "一级分类准确率（只在一级真值非空的样本上统计）", "l2_total": l2_total, "l2_correct": l2_correct, "l2_acc": l2_acc, "l2_formula": "l2_acc = l2_correct / l2_total", "l2_acc_percent": f"{l2_acc * 100:.2f}%", "l2_desc": "二级分类准确率（只在二级真值非空的样本上统计）", "l2_cond_total": l2_cond_total, "l2_cond_correct": l2_cond_correct, "l2_cond_acc": l2_cond_acc, "l2_cond_formula": "l2_cond_acc = l2_cond_correct / l2_cond_total", "l2_cond_acc_percent": f"{l2_cond_acc * 100:.2f}%", "l2_cond_desc": "条件二级准确率（仅在一级预测正确的样本上统计二级准确率）", "note": "若 l2_cond_total=0，说明当前样本中没有任何一级预测正确的记录，故条件二级准确率记为0。"}


# 计算安全比例，避免分母为0。
def _safe_ratio(num: int, den: int) -> float:
    return 0.0 if den <= 0 else float(num) / float(den)


# 加载模型与思考配置函数。
def _load_runtime_model_hooks() -> tuple[Any, Any]:
    try:
        from apps.complaint_app.core.model_config import get_complaint_match_experience_model, get_complaint_match_experience_thinking
        return get_complaint_match_experience_model, get_complaint_match_experience_thinking
    except Exception:
        return None, None


# 加载异步LLM客户端类型。
def _load_llm_client_types() -> tuple[Any, Any] | None:
    try:
        from apps.complaint_app.core.llm.llm_client import AsyncLLMClient, ModelConfig
        return AsyncLLMClient, ModelConfig
    except Exception as exc:
        logger.error("导入模型客户端失败：%s", exc)
        logger.error("请确认依赖已安装（例如 openai）且 Python 运行时兼容")
        return None


# 加载两阶段提示词文本。
def _load_prompt_texts() -> tuple[Path, Path, dict[str, str]]:
    first_system_prompt_path = DEFAULT_FIRST_SYSTEM_PROMPT
    second_system_prompt_path = DEFAULT_SECOND_SYSTEM_PROMPT
    prompts = {
        "first_system": _load_text_file(first_system_prompt_path, "first system prompt"),
        "first_user": DEFAULT_FIRST_USER_PROMPT,
        "second_system": _load_text_file(second_system_prompt_path, "second system prompt"),
        "second_user": DEFAULT_SECOND_USER_PROMPT,
    }
    return first_system_prompt_path, second_system_prompt_path, prompts


# 解析并校验模型运行参数。
def _resolve_runtime_settings(args: argparse.Namespace) -> dict[str, Any] | None:
    get_model_fn, get_thinking_fn = _load_runtime_model_hooks()
    api_key = _to_text(args.api_key) or _to_text(os.getenv("OPENAI_API_KEY"))
    base_url = _to_text(args.base_url) or _to_text(os.getenv("OPENAI_BASE_URL"))
    model = _to_text(args.model) or (get_model_fn() if callable(get_model_fn) else "")
    if not api_key:
        logger.error("缺少 --api-key 或 OPENAI_API_KEY")
        return None
    if not base_url:
        logger.error("缺少 --base-url 或 OPENAI_BASE_URL")
        return None
    if not model:
        logger.error("缺少 --model 且未配置 COMPLAINT_MATCH_EXPERIENCE_MODEL")
        return None
    if args.enable_thinking and args.disable_thinking:
        logger.error("--enable-thinking 和 --disable-thinking 不能同时设置")
        return None
    if args.enable_thinking:
        enable_thinking = True
    elif args.disable_thinking:
        enable_thinking = False
    elif callable(get_thinking_fn):
        enable_thinking = bool(get_thinking_fn(True))
    else:
        enable_thinking = True
    return {
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        "enable_thinking": enable_thinking,
        "thinking_extra_body": build_thinking_extra_body(model, enable_thinking),
    }


# 写入运行时连接环境变量。
def _configure_runtime_env(api_key: str, base_url: str, no_proxy: bool) -> None:
    os.environ["OPENAI_API_KEY"] = api_key
    os.environ["OPENAI_BASE_URL"] = base_url
    if no_proxy:
        os.environ["OPENAI_HTTP_TRUST_ENV"] = "false"


# 解析输入输出文件路径并创建输出目录。
def _resolve_io_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    input_path = Path(args.input_excel).expanduser().resolve()
    output_path = Path(args.output_excel).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    return input_path, output_path


# 加载专家经验并返回JSON字典。
def _load_expert_experience_json(args: argparse.Namespace) -> dict[str, Any]:
    data_root = _resolve_data_root()
    expert_base_dir = Path(args.expert_experience_base_dir).expanduser().resolve() if args.expert_experience_base_dir else data_root / "complaints" / "expert_experience" / "complaint-analysis-uploaded"
    expert_experience = _load_expert_experience(expert_base_dir, args.expert_experience_json)
    if not expert_experience:
        logger.warning("专家经验为空，基础目录=%s", expert_base_dir)
    else:
        logger.info("专家经验已加载 长度=%s 基础目录=%s", len(expert_experience), expert_base_dir)
    parsed = _parse_json_any(expert_experience)
    return parsed if isinstance(parsed, dict) else {}


# 打开输入工作簿并校验关键列。
def _load_workbook_sheet(args: argparse.Namespace, input_path: Path) -> tuple[Any, Any, dict[str, int]] | None:
    wb = load_workbook(input_path)
    ws = wb[args.sheet_name] if args.sheet_name else wb[wb.sheetnames[0]]
    columns = _header_map(ws)
    if args.item_col not in columns:
        logger.error("未找到举报事项列：%s", args.item_col)
        return None
    if args.story_col not in columns:
        logger.error("未找到摘要列：%s", args.story_col)
        return None
    return wb, ws, columns


# 返回结果列定义。
def _get_result_columns() -> list[str]:
    return ["一级分类_pred", "二级分类_pred", "匹配度", "分类理由", "一级是否正确", "二级是否正确", "一级分类_pred_raw", "二级分类_pred_raw", "一级命中状态", "二级命中状态", "二阶段候选_json", "一级命中_任一候选", "二级命中_任一候选", "分析是否正确"]


# 根据过滤条件计算可运行行。
def _select_run_rows(ws: Any, columns: dict[str, int], args: argparse.Namespace, first_real_exists: bool) -> tuple[list[int], list[int], list[tuple[int, str, str]], list[int], int, int]:
    all_rows = list(range(2, ws.max_row + 1))
    eligible_rows: list[int] = []
    skipped_rows: list[tuple[int, str, str]] = []
    for row_num in all_rows:
        first_real = _to_text(ws.cell(row=row_num, column=columns[args.first_real_col]).value) if first_real_exists else ""
        if args.skip_real_credit_card and first_real == "信用卡类":
            skipped_rows.append((row_num, first_real, "skip-real-credit-card"))
            continue
        if args.skip_real_nfra_duty and first_real == "国家金融监督管理总局职责":
            skipped_rows.append((row_num, first_real, "skip-real-nfra-duty"))
            continue
        eligible_rows.append(row_num)
    start = max(0, int(args.start))
    end = len(eligible_rows)
    if int(args.limit) > 0:
        end = min(end, start + int(args.limit))
    run_rows = eligible_rows[start:end]
    return all_rows, eligible_rows, skipped_rows, run_rows, start, end


# 执行单行异步推理任务。
async def _run_row_task(llm: Any, sem: asyncio.Semaphore, ws: Any, columns: dict[str, int], row_num: int, args: argparse.Namespace, prompts: dict[str, Any], first_level_categories: str, first_level_labels: list[str], expert_experience_json: dict[str, Any], model: str, thinking_extra_body: dict[str, Any] | None) -> tuple[int, dict[str, str]]:
    item = _to_text(ws.cell(row=row_num, column=columns[args.item_col]).value)
    story = _to_text(ws.cell(row=row_num, column=columns[args.story_col]).value)
    if int(args.story_max_chars) > 0:
        story = story[: int(args.story_max_chars)]
    async with sem:
        result = await _run_single_row(llm, prompts, row_num=row_num, item=item, story=story, first_level_categories=first_level_categories, first_level_labels=first_level_labels, expert_experience_json=expert_experience_json, model=model, thinking_extra_body=thinking_extra_body, max_retries=max(1, int(args.max_retries)))
        return row_num, result


# 写入单行结果并打印实时日志。
def _write_row_result(ws: Any, columns: dict[str, int], row_num: int, result: dict[str, str], args: argparse.Namespace, first_real_exists: bool, second_real_exists: bool) -> tuple[str, bool]:
    status = _to_text(result.get("status"))
    first_pred = _to_text(result.get("first_pred"))
    second_pred = _to_text(result.get("second_pred"))
    first_pred_raw = _to_text(result.get("first_pred_raw"))
    second_pred_raw = _to_text(result.get("second_pred_raw"))
    match_score = _to_text(result.get("match_score"))
    match_reason = _to_text(result.get("match_reason"))
    ws.cell(row=row_num, column=columns["一级分类_pred"], value=first_pred)
    ws.cell(row=row_num, column=columns["二级分类_pred"], value=second_pred)
    ws.cell(row=row_num, column=columns["一级分类_pred_raw"], value=first_pred_raw)
    ws.cell(row=row_num, column=columns["二级分类_pred_raw"], value=second_pred_raw)
    ws.cell(row=row_num, column=columns["匹配度"], value=match_score)
    ws.cell(row=row_num, column=columns["分类理由"], value=match_reason)
    ws.cell(row=row_num, column=columns["二阶段候选_json"], value=match_reason)
    first_real = _to_text(ws.cell(row=row_num, column=columns[args.first_real_col]).value) if first_real_exists else ""
    second_real = _to_text(ws.cell(row=row_num, column=columns[args.second_real_col]).value) if second_real_exists else ""
    l1_ok = "" if not first_real else ("是" if _pred_matches_real(first_pred, first_real) else "否")
    l2_ok = "" if not second_real else ("是" if _pred_matches_real(second_pred, second_real) else "否")
    l1_hit_status = _pred_raw_hit_status(first_pred_raw, first_real)
    l2_hit_status = _pred_raw_hit_status(second_pred_raw, second_real)
    l1_any_hit, l2_any_hit, final_correct = _compute_any_hit_columns(match_reason, first_real, second_real)
    ws.cell(row=row_num, column=columns["一级是否正确"], value=l1_ok)
    ws.cell(row=row_num, column=columns["二级是否正确"], value=l2_ok)
    ws.cell(row=row_num, column=columns["一级命中状态"], value=l1_hit_status)
    ws.cell(row=row_num, column=columns["二级命中状态"], value=l2_hit_status)
    ws.cell(row=row_num, column=columns["一级命中_任一候选"], value=l1_any_hit)
    ws.cell(row=row_num, column=columns["二级命中_任一候选"], value=l2_any_hit)
    ws.cell(row=row_num, column=columns["分析是否正确"], value=final_correct)
    logger.info("第%s行 实时结果 状态=%s 一级=%s 二级=%s 一级原始=%s 二级原始=%s 一级真实=%s 一级是否正确=%s 一级命中状态=%s 一级命中_任一候选=%s 二级真实=%s 二级是否正确=%s 二级命中状态=%s 二级命中_任一候选=%s 分析是否正确=%s 匹配度=%s", row_num, status, first_pred, second_pred, first_pred_raw, second_pred_raw, first_real, l1_ok, l1_hit_status, l1_any_hit, second_real, l2_ok, l2_hit_status, l2_any_hit, final_correct, match_score)
    return status, final_correct == "是"


# 并发执行所有样本并实时落盘。
async def _run_batch_rows(wb: Any, ws: Any, columns: dict[str, int], output_path: Path, run_rows: list[int], args: argparse.Namespace, llm: Any, prompts: dict[str, Any], first_level_categories: str, first_level_labels: list[str], expert_experience_json: dict[str, Any], model: str, thinking_extra_body: dict[str, Any] | None, first_real_exists: bool, second_real_exists: bool) -> tuple[dict[int, dict[str, str]], int, int, int]:
    sem = asyncio.Semaphore(max(1, int(args.max_concurrent)))
    progress_every = max(1, int(args.progress_every))
    started_at = time.time()
    completed = 0
    success = 0
    failed = 0
    analysis_correct = 0
    by_row: dict[int, dict[str, str]] = {}
    tasks = [asyncio.create_task(_run_row_task(llm, sem, ws, columns, row_num, args, prompts, first_level_categories, first_level_labels, expert_experience_json, model, thinking_extra_body)) for row_num in run_rows]
    for coro in asyncio.as_completed(tasks):
        row_num, result = await coro
        by_row[row_num] = result
        status, is_analysis_correct = _write_row_result(ws, columns, row_num, result, args, first_real_exists, second_real_exists)
        _save_output_snapshot(wb, ws, output_path, set(by_row.keys()), args.first_real_col, args.second_real_col)
        completed += 1
        if status == "completed":
            success += 1
        else:
            failed += 1
        if is_analysis_correct:
            analysis_correct += 1
        if completed % progress_every == 0 or completed == len(run_rows):
            elapsed = time.time() - started_at
            logger.info("进度 %s/%s 成功=%s 失败=%s 分析是否正确(是)=%s 用时=%.1fs", completed, len(run_rows), success, failed, analysis_correct, elapsed)
    return by_row, completed, success, failed


# 构建指标统计所需记录。
def _build_metrics_rows(selected_rows: list[int], by_row: dict[int, dict[str, str]], ws: Any, columns: dict[str, int], args: argparse.Namespace, first_real_exists: bool, second_real_exists: bool) -> list[dict[str, str]]:
    metrics_rows: list[dict[str, str]] = []
    for row_num in selected_rows:
        result = by_row.get(row_num, {"status": "error", "error": "missing result"})
        first_pred = _to_text(result.get("first_pred"))
        second_pred = _to_text(result.get("second_pred"))
        first_real = _to_text(ws.cell(row=row_num, column=columns[args.first_real_col]).value) if first_real_exists else ""
        second_real = _to_text(ws.cell(row=row_num, column=columns[args.second_real_col]).value) if second_real_exists else ""
        status = _to_text(result.get("status"))
        if status == "error":
            logger.warning("第%s行 失败 错误=%s", row_num, _to_text(result.get("error")))
        metrics_rows.append({"status": status, "first_pred": first_pred, "second_pred": second_pred, "first_real": first_real, "second_real": second_real})
    return metrics_rows


# 保存指标文件到目标路径。
def _save_metrics(metrics: dict[str, Any], args: argparse.Namespace, output_path: Path) -> None:
    metrics_path = Path(args.metrics_json).expanduser().resolve() if args.metrics_json else output_path.with_suffix(".metrics.json")
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("已保存指标：%s", metrics_path)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="基于两阶段提示词批量处理 Excel（复用 complaint_app llm_client）。")
    parser.add_argument("--api-key", default="", help="模型 API Key，优先级高于 OPENAI_API_KEY")
    parser.add_argument("--base-url", default="", help="模型 Base URL，优先级高于 OPENAI_BASE_URL")
    parser.add_argument("--model", default="", help="模型名，默认从 COMPLAINT_MATCH_EXPERIENCE_MODEL 读取")
    parser.add_argument("--input-excel", required=True, help="输入Excel路径")
    parser.add_argument("--output-excel", required=True, help="输出Excel路径")
    parser.add_argument("--sheet-name", default=None, help="sheet 名称，默认首个sheet")
    parser.add_argument("--item-col", default="举报事项", help="举报事项列名")
    parser.add_argument("--story-col", default="摘要", help="摘要列名（作为 story）")
    parser.add_argument("--first-real-col", default="一级分类_real", help="一级分类真值列名")
    parser.add_argument("--second-real-col", default="二级分类_real", help="二级分类真值列名")
    parser.add_argument("--skip-real-credit-card", action="store_true", help="跳过一级分类_real=信用卡类")
    parser.add_argument("--skip-real-nfra-duty", action="store_true", help="跳过一级分类_real=国家金融监督管理总局职责")
    parser.add_argument("--start", type=int, default=0, help="起始数据行(0-based, 含)")
    parser.add_argument("--limit", type=int, default=0, help="处理条数，0=到结尾")
    parser.add_argument("--max-concurrent", type=int, default=6, help="最大并发数")
    parser.add_argument("--progress-every", type=int, default=1, help="每完成N条打印一次进度，默认1")
    parser.add_argument("--max-retries", type=int, default=3, help="单条最大重试次数")
    parser.add_argument("--story-max-chars", type=int, default=4096, help="story最大长度，0=不截断")
    parser.add_argument("--no-proxy", action="store_true", help="强制直连，不读取系统代理环境变量")
    parser.add_argument("--enable-thinking", action="store_true", help="强制开启模型思考（覆盖环境配置）")
    parser.add_argument("--disable-thinking", action="store_true", help="强制关闭模型思考（覆盖环境配置）")
    parser.add_argument("--expert-experience-json", default="", help="可选：指定 expert_experience JSON 文件路径")
    parser.add_argument("--expert-experience-base-dir", default="", help="可选：专家经验目录，默认 DATA_ROOT/complaints/expert_experience/complaint-analysis-uploaded")
    parser.add_argument("--metrics-json", default="", help="可选：分类指标json输出路径")
    return parser


async def _run_async(args: argparse.Namespace) -> int:
    # 1) 初始化环境与运行时依赖。
    _load_env_files()
    llm_types = _load_llm_client_types()
    if llm_types is None:
        return 2
    AsyncLLMClient, ModelConfig = llm_types
    first_system_prompt_path, second_system_prompt_path, prompts = _load_prompt_texts()
    runtime = _resolve_runtime_settings(args)
    if runtime is None:
        return 2

    # 2) 解析模型参数并配置连接环境变量。
    api_key = _to_text(runtime.get("api_key"))
    base_url = _to_text(runtime.get("base_url"))
    model = _to_text(runtime.get("model"))
    enable_thinking = bool(runtime.get("enable_thinking"))
    thinking_extra_body = runtime.get("thinking_extra_body")
    _configure_runtime_env(api_key, base_url, bool(args.no_proxy))

    # 3) 加载输入文件与专家经验数据。
    input_path, output_path = _resolve_io_paths(args)
    expert_experience_json = _load_expert_experience_json(args)
    first_level_labels = list(expert_experience_json.keys())
    first_level_categories = json.dumps(first_level_labels, ensure_ascii=False)
    workbook_data = _load_workbook_sheet(args, input_path)
    if workbook_data is None:
        return 2
    wb, ws, columns = workbook_data
    _ensure_columns(ws, columns, _get_result_columns())
    first_real_exists = args.first_real_col in columns
    second_real_exists = args.second_real_col in columns
    all_rows, eligible_rows, skipped_rows, run_rows, start, end = _select_run_rows(ws, columns, args, first_real_exists)
    selected_rows = run_rows

    # 4) 无可运行样本时直接落盘并结束。
    if not run_rows:
        _save_output_snapshot(wb, ws, output_path, set(), args.first_real_col, args.second_real_col)
        logger.info("过滤后无可运行样本：可运行总数=%s 跳过总数=%s 起始=%s 限制=%s", len(eligible_rows), len(skipped_rows), start, int(args.limit))
        return 0

    # 5) 执行批量推理并实时写回结果。
    logger.info("开始执行投诉分类：总行数=%s 可运行总数=%s 本次运行数=%s 跳过总数=%s 范围=[%s,%s) 模型=%s 最大并发=%s 禁用代理=%s 采样温度=0.0 开启思考=%s 思考参数=%s 一级提示词=%s 二级提示词=%s", len(all_rows), len(eligible_rows), len(run_rows), len(skipped_rows), start, end, model, args.max_concurrent, bool(args.no_proxy), enable_thinking, thinking_extra_body, first_system_prompt_path, second_system_prompt_path)

    llm = AsyncLLMClient(config=ModelConfig(default_model=model), name="complaint_expert_classification_direct")
    if skipped_rows:
        logger.info("已启用跳过过滤，跳过总数=%s", len(skipped_rows))
    by_row, _completed, _success, _failed = await _run_batch_rows(wb, ws, columns, output_path, run_rows, args, llm, prompts, first_level_categories, first_level_labels, expert_experience_json, model, thinking_extra_body, first_real_exists, second_real_exists)
    metrics_rows = _build_metrics_rows(selected_rows, by_row, ws, columns, args, first_real_exists, second_real_exists)

    # 6) 保存结果文件与预览文件。
    _save_output_snapshot(wb, ws, output_path, set(by_row.keys()), args.first_real_col, args.second_real_col)
    logger.info("已保存输出：%s", output_path)
    _save_preview_markdown(ws, output_path, set(by_row.keys()))

    # 7) 在有真值列时计算并落盘指标。
    if first_real_exists and second_real_exists:
        metrics = _compute_metrics(metrics_rows)
        if metrics:
            logger.info("分类指标：%s", json.dumps(metrics, ensure_ascii=False))
            _save_metrics(metrics, args, output_path)
    return 0


def main() -> int:
    args = _build_parser().parse_args()
    return asyncio.run(_run_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
