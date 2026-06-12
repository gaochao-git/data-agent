from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
import time
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
from utils.llm_thinking import build_thinking_extra_body
from utils.logger import get_logger


logger = get_logger(__name__)
DEFAULT_ITEM_COL = "举报事项"
DEFAULT_STORY_COL = "摘要"
DEFAULT_SECOND_LAW_COL = "专家经验二级分类法律汇总"
DEFAULT_SECOND_VERIFY_COL = "专家经验二级分类_核实要点"
DEFAULT_SECOND_CHECKLIST_COL = "专家经验二级分类_调阅清单"
DEFAULT_ANALYSIS_COL = "本地法律_分析原文"
DEFAULT_LAW_SCORE_COL = "法律准确性分"
DEFAULT_CHECKLIST_SCORE_COL = "调阅清单准确性分"
DEFAULT_VERIFY_SCORE_COL = "核实思路准确性分"
DEFAULT_LAW_REASON_COL = "法律准确性评分理由"
DEFAULT_CHECKLIST_REASON_COL = "调阅清单准确性评分理由"
DEFAULT_VERIFY_REASON_COL = "核实思路准确性评分理由"
DEFAULT_CONCLUSION_COL = "总体评估结论"
DEFAULT_RESULT_KEYS = [
    DEFAULT_LAW_SCORE_COL,
    DEFAULT_CHECKLIST_SCORE_COL,
    DEFAULT_VERIFY_SCORE_COL,
    DEFAULT_LAW_REASON_COL,
    DEFAULT_CHECKLIST_REASON_COL,
    DEFAULT_VERIFY_REASON_COL,
    DEFAULT_CONCLUSION_COL,
]
DEFAULT_FEWSHOT_EXAMPLE = """示例输入：
举报事项：吴祖与2023年举报支付宝和江苏峰云供应链管理有限公司，反映其在2022年3月13日于广东省中山市通过支付宝花呗分期办理广东移动套餐后，号码于2023年8月注销且未被使用，但支付宝仍继续每月扣款81元，交涉多次推脱责任未处理，导致扣款提示和征信影响。
摘要：2022年3月13日，吴祖在广东中山办理移动电话卡并签约花呗分期支付81元/月，期限3年，获赠电动车。同年5月单位停业后该号码未再使用。2023年8月中旬起，吴祖通过支付宝客服、12345热线等渠道多次反映持续扣款问题，支付宝答复需与商家协商，但江苏峰云供应链管理公司未予回应。期间支付宝曾表示若商家48小时内未回复可解除协议，但未实际处理。2023年8月下旬，移动公司确认号码欠费且未使用，已注销。吴祖主张该号码已停用，扣款未用于话费，要求撤销后续扣款1539元并消除征信影响。
专家经验二级分类_法律：中国人民银行关于规范代收业务的通知（银发【2020】248号）
专家经验二级分类_核实要点：1.代收机构与收款人代收业务协议签订情况。2.收款人与付款人授权协议签订情况。3.代收机构在代收业务处理过程中逐笔核实、确认收款人支付指令的证明材料。
专家经验二级分类_调阅清单：1.代收机构就举报事项核实情况的报告；2.代收机构与收款人签订的代收业务协议；3.收款人与付款人授权协议；4.收款人发起的支付指令；5.代收机构逐笔核实、确认代收业务协议约定事项以及收付款人授权状态的证明材料；6.举报涉及的代收业务交易流水与资金结算流水；7.其他与举报事项相关的证明材料。
基于法律技能完整输出：本案涉及移动套餐签约、花呗分期付款、号码停用、持续扣款及投诉争议等情形，属于“金融消费服务合同纠纷”，适用以下本地法规库中的规范文件：中国人民银行金融消费者权益保护实施办法、中国人民银行关于规范代收业务的通知、非银行支付机构网络支付业务管理办法、非银行支付机构监督管理条例。分析认为：若扣款授权范围与实际服务状态不符，支付机构有核验并拒收义务；支付机构应履行投诉处理主体责任；消费者对无服务对价的持续扣款提出退款主张具有合理性。调查取证清单包括授权协议或线上授权流程截图、银行/支付机构交易明细截图或对账单、移动公司注销证明+扣款流水、支付宝客服对话记录及12345回复、商家沟通记录、支付宝与商家的代收协议中关于终止授权的约定、征信查询结果或相关催收通知。待核实问题包括授权条款范围、授权终止机制、争议处理流程、商家信息披露、征信影响闭环。
示例输出（严格单个JSON对象，7个key各出现一次，不要```json代码块）：
{"法律准确性分":2,"调阅清单准确性分":1,"核实思路准确性分":1,"法律准确性评分理由":"命中核心法律《中国人民银行关于规范代收业务的通知》，扩展法律整体可接受，无明显无关法。","调阅清单准确性评分理由":"覆盖了授权协议、交易流水等关键材料，但未直接覆盖收款人发起的支付指令、逐笔核实授权状态证明材料、正式核实情况报告。","核实思路准确性评分理由":"覆盖了授权条款范围、授权终止机制、争议处理流程等关键点，但未直接覆盖代收机构与收款人协议、逐笔核实支付指令两个核心核查点。","总体评估结论":"命中核心法律，扩展法律整体可接受；调阅清单和核实思路覆盖了部分关键点，但对代收业务链路相关核心证据与关键核查点覆盖不完整。"}"""


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


# 读取环境变量文件。
def _load_env_files() -> None:
    if load_dotenv is None:
        return
    load_dotenv()
    server_env = SERVER_DIR / ".env"
    if server_env.is_file():
        load_dotenv(server_env, override=False)


# 解析输入输出文件路径。
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


# 选择本次运行行号。
def _select_run_rows(ws: Any, start: int, limit: int) -> list[int]:
    all_rows = list(range(2, ws.max_row + 1))
    s = max(0, int(start))
    e = len(all_rows)
    if int(limit) > 0:
        e = min(e, s + int(limit))
    return all_rows[s:e]


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


# 组装单条评估消息。
def _build_eval_messages(row: dict[str, str]) -> list[dict[str, str]]:
    system_text = (
        "你是举报事项本地法律分析评估员。"
        "请只基于输入中的举报事项、摘要、专家经验二级分类三列与基于法律技能完整输出进行评估。"
        "评分维度固定为：法律准确性分、调阅清单准确性分、核实思路准确性分。"
        "评分规则固定为0/1/2。"
        "法律准确性分：2分=核心法律命中且扩展法律基本合理且无明显无关法；1分=核心法律部分命中或扩展法律有一定合理性但轻微跑偏；0分=核心法律未命中或存在明显无关法/明显跑偏。"
        "调阅清单准确性分：2分=覆盖本案关键材料且无明显关键证据缺失且无明显无关材料；1分=覆盖部分关键材料但缺少部分关键证据或夹杂少量无关材料；0分=关键材料覆盖不足或缺失关键证据较多或无关材料明显。"
        "核实思路准确性分：2分=抓住本案关键核查点且路径清晰且无明显跑偏；1分=抓住部分关键核查点但路径不完整或轻微跑偏；0分=未抓住关键核查点或路径混乱或明显跑偏。"
        "必须输出单个JSON对象，不要输出```json代码块，不要输出解释文字，不要重复任何key。"
        "且只能包含以下7个键，顺序也必须保持一致：法律准确性分、法律准确性评分理由、调阅清单准确性分、调阅清单准确性评分理由、核实思路准确性分、核实思路准确性评分理由、总体评估结论。"
        "三个分值必须是整数0、1或2。"
        "总体评估结论必须是简体中文一句话或两句话。"
        '输出模板固定为：{"法律准确性分":0, "法律准确性评分理由":"", "调阅清单准确性分":0,"调阅清单准确性评分理由":"", "核实思路准确性分":0,"核实思路准确性评分理由":"","总体评估结论":""}'
        f"{DEFAULT_FEWSHOT_EXAMPLE}"
    )
    user_text = (
        f"举报事项：{row['item']}\n"
        f"摘要：{row['story']}\n"
        f"专家经验二级分类_法律：{row['expert_law']}\n"
        f"专家经验二级分类_核实要点：{row['expert_verify']}\n"
        f"专家经验二级分类_调阅清单：{row['expert_checklist']}\n"
        f"基于法律技能完整输出：{row['analysis']}"
    )
    return [{"role": "system", "content": system_text}, {"role": "user", "content": user_text}]


# 校验评估结果结构。
def _normalize_eval_result(parsed: dict[str, Any]) -> dict[str, Any]:
    if set(parsed.keys()) != set(DEFAULT_RESULT_KEYS):
        raise ValueError(f"evaluation keys invalid: {list(parsed.keys())}")
    result = {
        DEFAULT_LAW_SCORE_COL: int(parsed.get(DEFAULT_LAW_SCORE_COL)),
        DEFAULT_CHECKLIST_SCORE_COL: int(parsed.get(DEFAULT_CHECKLIST_SCORE_COL)),
        DEFAULT_VERIFY_SCORE_COL: int(parsed.get(DEFAULT_VERIFY_SCORE_COL)),
        DEFAULT_LAW_REASON_COL: _to_text(parsed.get(DEFAULT_LAW_REASON_COL)),
        DEFAULT_CHECKLIST_REASON_COL: _to_text(parsed.get(DEFAULT_CHECKLIST_REASON_COL)),
        DEFAULT_VERIFY_REASON_COL: _to_text(parsed.get(DEFAULT_VERIFY_REASON_COL)),
        DEFAULT_CONCLUSION_COL: _to_text(parsed.get(DEFAULT_CONCLUSION_COL)),
    }
    for key in (DEFAULT_LAW_SCORE_COL, DEFAULT_CHECKLIST_SCORE_COL, DEFAULT_VERIFY_SCORE_COL):
        if result[key] not in {0, 1, 2}:
            raise ValueError(f"{key} must be 0/1/2")
    if not result[DEFAULT_CONCLUSION_COL]:
        raise ValueError("总体评估结论不能为空")
    return result


# 单条调用模型完成评估。
async def _evaluate_row(llm: AsyncLLMClient, model: str, row: dict[str, str], row_num: int, max_retries: int) -> dict[str, Any]:
    retry_n = max(1, int(max_retries))
    messages = _build_eval_messages(row)
    thinking_extra_body = build_thinking_extra_body(model, True)
    for attempt in range(1, retry_n + 1):
        try:
            logger.info("第%s行 评估模型开始 尝试=%s", row_num, attempt)
            resp = await llm.chat(messages=messages, model=model, temperature=0.0, extra_body=thinking_extra_body)
            text = _response_text(resp)
            logger.info("第%s行 评估模型原始输出=%s", row_num, json.dumps(text, ensure_ascii=False))
            parsed = _parse_json_any(text)
            if not isinstance(parsed, dict):
                parsed = _parse_json_any(_extract_json_fragment(text))
            if not isinstance(parsed, dict):
                raise ValueError("evaluation output is not json object")
            result = _normalize_eval_result(parsed)
            logger.info("第%s行 评估模型完成 尝试=%s 法律分=%s 调阅分=%s 核实分=%s", row_num, attempt, result[DEFAULT_LAW_SCORE_COL], result[DEFAULT_CHECKLIST_SCORE_COL], result[DEFAULT_VERIFY_SCORE_COL])
            return result
        except Exception as exc:
            logger.warning("第%s行 评估模型失败 尝试=%s 错误=%s", row_num, attempt, exc)
            if attempt >= retry_n:
                logger.error("第%s行 评估模型失败 达到最大重试次数=%s", row_num, retry_n)
                return {
                    DEFAULT_LAW_SCORE_COL: 0,
                    DEFAULT_CHECKLIST_SCORE_COL: 0,
                    DEFAULT_VERIFY_SCORE_COL: 0,
                    DEFAULT_LAW_REASON_COL: f"评估失败：{exc}",
                    DEFAULT_CHECKLIST_REASON_COL: f"评估失败：{exc}",
                    DEFAULT_VERIFY_REASON_COL: f"评估失败：{exc}",
                    DEFAULT_CONCLUSION_COL: "评估失败，未生成有效结论。",
                }
            await asyncio.sleep(1.0 * attempt)
    return {
        DEFAULT_LAW_SCORE_COL: 0,
        DEFAULT_CHECKLIST_SCORE_COL: 0,
        DEFAULT_VERIFY_SCORE_COL: 0,
        DEFAULT_LAW_REASON_COL: "评估失败",
        DEFAULT_CHECKLIST_REASON_COL: "评估失败",
        DEFAULT_VERIFY_REASON_COL: "评估失败",
        DEFAULT_CONCLUSION_COL: "评估失败，未生成有效结论。",
    }


# 组装单条样本输入。
def _build_row_input(ws: Any, columns: dict[str, int], row_num: int, args: argparse.Namespace) -> dict[str, str]:
    return {
        "item": _to_text(ws.cell(row=row_num, column=columns[args.item_col]).value),
        "story": _to_text(ws.cell(row=row_num, column=columns[args.story_col]).value),
        "expert_law": _to_text(ws.cell(row=row_num, column=columns[args.second_law_col]).value),
        "expert_verify": _to_text(ws.cell(row=row_num, column=columns[args.second_verify_col]).value),
        "expert_checklist": _to_text(ws.cell(row=row_num, column=columns[args.second_checklist_col]).value),
        "analysis": _to_text(ws.cell(row=row_num, column=columns[args.analysis_col]).value),
    }


# 判断是否具备专家经验评估输入。
def _has_expert_inputs(row: dict[str, str]) -> bool:
    return bool(_to_text(row.get("expert_law")) and _to_text(row.get("expert_verify")) and _to_text(row.get("expert_checklist")))


# 返回跳过评估时的空结果。
def _empty_eval_result() -> dict[str, Any]:
    return {
        DEFAULT_LAW_SCORE_COL: "",
        DEFAULT_CHECKLIST_SCORE_COL: "",
        DEFAULT_VERIFY_SCORE_COL: "",
        DEFAULT_LAW_REASON_COL: "",
        DEFAULT_CHECKLIST_REASON_COL: "",
        DEFAULT_VERIFY_REASON_COL: "",
        DEFAULT_CONCLUSION_COL: "",
    }


# 写回单行评估结果。
def _write_row_result(ws: Any, columns: dict[str, int], row_num: int, result: dict[str, Any]) -> None:
    ws.cell(row=row_num, column=columns[DEFAULT_LAW_SCORE_COL], value=result[DEFAULT_LAW_SCORE_COL])
    ws.cell(row=row_num, column=columns[DEFAULT_CHECKLIST_SCORE_COL], value=result[DEFAULT_CHECKLIST_SCORE_COL])
    ws.cell(row=row_num, column=columns[DEFAULT_VERIFY_SCORE_COL], value=result[DEFAULT_VERIFY_SCORE_COL])
    ws.cell(row=row_num, column=columns[DEFAULT_LAW_REASON_COL], value=result[DEFAULT_LAW_REASON_COL])
    ws.cell(row=row_num, column=columns[DEFAULT_CHECKLIST_REASON_COL], value=result[DEFAULT_CHECKLIST_REASON_COL])
    ws.cell(row=row_num, column=columns[DEFAULT_VERIFY_REASON_COL], value=result[DEFAULT_VERIFY_REASON_COL])
    ws.cell(row=row_num, column=columns[DEFAULT_CONCLUSION_COL], value=result[DEFAULT_CONCLUSION_COL])


# 执行单行任务并返回结果。
async def _run_row_task(llm: AsyncLLMClient, sem: asyncio.Semaphore, ws: Any, columns: dict[str, int], row_num: int, args: argparse.Namespace, model: str) -> tuple[int, dict[str, Any]]:
    async with sem:
        row = _build_row_input(ws, columns, row_num, args)
        if not _has_expert_inputs(row):
            logger.info("第%s行 跳过评估：专家经验二级分类法律/核实要点/调阅清单存在空值", row_num)
            return row_num, _empty_eval_result()
        return row_num, await _evaluate_row(llm, model, row, row_num, max(1, int(args.max_retries)))


# 并发处理全部行并实时落盘。
async def _run_batch_rows(llm: AsyncLLMClient, wb: Any, ws: Any, columns: dict[str, int], run_rows: list[int], output_path: Path, args: argparse.Namespace, model: str) -> tuple[int, float]:
    sem = asyncio.Semaphore(max(1, int(args.max_concurrent)))
    progress_every = max(1, int(args.progress_every))
    completed = 0
    started_at = time.time()
    tasks = [asyncio.create_task(_run_row_task(llm, sem, ws, columns, row_num, args, model)) for row_num in run_rows]
    for coro in asyncio.as_completed(tasks):
        row_num, result = await coro
        _write_row_result(ws, columns, row_num, result)
        completed += 1
        _save_output_snapshot(wb, ws, output_path, run_rows)
        if completed % progress_every == 0 or completed == len(run_rows):
            logger.info("进度 %s/%s 用时=%.1fs", completed, len(run_rows), time.time() - started_at)
    return completed, time.time() - started_at


# 运行前配置环境变量。
def _configure_runtime_env(args: argparse.Namespace) -> tuple[str, str, str]:
    api_key = _to_text(args.api_key) or _to_text(os.getenv("OPENAI_API_KEY"))
    base_url = _to_text(args.base_url) or _to_text(os.getenv("OPENAI_BASE_URL"))
    model = _to_text(args.model) or get_complaint_law_analysis_model()
    if not api_key:
        raise ValueError("缺少 OPENAI_API_KEY（可通过 --api-key 或环境变量设置）")
    if not base_url:
        raise ValueError("缺少 OPENAI_BASE_URL（可通过 --base-url 或环境变量设置）")
    os.environ["OPENAI_API_KEY"] = api_key
    os.environ["OPENAI_BASE_URL"] = base_url
    if args.no_proxy:
        os.environ["OPENAI_HTTP_TRUST_ENV"] = "false"
    return api_key, base_url, model


# 构造命令行参数。
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="基于专家经验二级分类信息与本地法律技能输出，批量评估法律、调阅清单、核实思路三项分值。")
    parser.add_argument("--api-key", default="", help="可选：覆盖 OPENAI_API_KEY")
    parser.add_argument("--base-url", default="", help="可选：覆盖 OPENAI_BASE_URL")
    parser.add_argument("--model", default="", help="评估模型名，默认 COMPLAINT_LAW_ANALYSIS_MODEL")
    parser.add_argument("--input-excel", required=True, help="输入文件路径（支持 .xlsx/.csv）")
    parser.add_argument("--output-excel", required=True, help="输出文件路径（支持 .xlsx/.csv）")
    parser.add_argument("--sheet-name", default="", help="可选：sheet名称，默认首个")
    parser.add_argument("--item-col", default=DEFAULT_ITEM_COL, help="举报事项列名")
    parser.add_argument("--story-col", default=DEFAULT_STORY_COL, help="摘要列名")
    parser.add_argument("--second-law-col", default=DEFAULT_SECOND_LAW_COL, help="专家经验二级分类法律列名")
    parser.add_argument("--second-verify-col", default=DEFAULT_SECOND_VERIFY_COL, help="专家经验二级分类核实要点列名")
    parser.add_argument("--second-checklist-col", default=DEFAULT_SECOND_CHECKLIST_COL, help="专家经验二级分类调阅清单列名")
    parser.add_argument("--analysis-col", default=DEFAULT_ANALYSIS_COL, help="基于法律技能完整输出列名")
    parser.add_argument("--start", type=int, default=0, help="起始数据行（0-based，含）")
    parser.add_argument("--limit", type=int, default=0, help="处理条数，0=到结尾")
    parser.add_argument("--max-concurrent", type=int, default=4, help="最大并发数")
    parser.add_argument("--max-retries", type=int, default=3, help="单条最大重试次数")
    parser.add_argument("--progress-every", type=int, default=10, help="每完成N条打印一次进度")
    parser.add_argument("--no-proxy", action="store_true", help="强制直连，不读取系统代理环境变量")
    return parser


async def _run_async(args: argparse.Namespace) -> int:
    _load_env_files()
    try:
        _api_key, _base_url, model = _configure_runtime_env(args)
    except Exception as exc:
        logger.error("初始化评估环境失败：%s", exc)
        return 2
    input_path, output_path = _resolve_io_paths(args.input_excel, args.output_excel)
    if not input_path.is_file():
        logger.error("输入文件不存在：%s", input_path)
        return 2
    try:
        wb, ws = _load_input_workbook(input_path, _to_text(args.sheet_name))
    except Exception as exc:
        logger.error("加载输入文件失败：%s 错误=%s", input_path, exc)
        return 2
    columns = _header_map(ws)
    for required_col in [args.item_col, args.story_col, args.second_law_col, args.second_verify_col, args.second_checklist_col, args.analysis_col]:
        if required_col not in columns:
            logger.error("未找到输入列：%s", required_col)
            return 2
    _ensure_columns(ws, columns, [DEFAULT_LAW_SCORE_COL, DEFAULT_CHECKLIST_SCORE_COL, DEFAULT_VERIFY_SCORE_COL, DEFAULT_LAW_REASON_COL, DEFAULT_CHECKLIST_REASON_COL, DEFAULT_VERIFY_REASON_COL, DEFAULT_CONCLUSION_COL])
    run_rows = _select_run_rows(ws, int(args.start), int(args.limit))
    if not run_rows:
        _save_output_snapshot(wb, ws, output_path, [])
        logger.info("无可处理样本，已输出：%s", output_path)
        return 0
    logger.info("开始执行本地法律评估：总样本=%s 模型=%s 最大并发=%s", len(run_rows), model, int(args.max_concurrent))
    llm = AsyncLLMClient(config=ModelConfig(default_model=model), name="local_law_eval")
    completed, elapsed = await _run_batch_rows(llm, wb, ws, columns, run_rows, output_path, args, model)
    _save_output_snapshot(wb, ws, output_path, run_rows)
    logger.info("评估完成：输出=%s 完成数=%s 用时=%.1fs", output_path, completed, elapsed)
    return 0


def main() -> int:
    args = _build_parser().parse_args()
    return asyncio.run(_run_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
