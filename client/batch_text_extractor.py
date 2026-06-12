#!/usr/bin/env python3
"""
批量读取文本文件，通过大模型抽取字段，并将结果写入 data/extractions.xlsx。

示例：
python client/batch_text_extractor.py --input-dir /path/to/texts --model gpt-4o-mini
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Dict, Iterable, List

import httpx
import openai
from openpyxl import Workbook

DEFAULT_MODEL = os.getenv("OPENAI_MODEL") or os.getenv("OCR_LLM_MODEL") or "gpt-4o-mini"
DEFAULT_BASE_URL = os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"
DEFAULT_API_KEY = os.getenv("OPENAI_API_KEY")

OUTPUT_HEADERS: List[str] = [
    "序号",
    "原文内容",
    "文件字号",
    "收件日期",
    "业务类型",
    "举报类型",
    "来件渠道",
    "是否匿名投诉",
    "举报人",
    "被举报人/机构",
    "违规行为",
    "文件标题",
    "来件单位处理意见",
    "管辖地",
    "管辖人民银行",
    "是否群体事件",
]

EXTRACTION_KEYS: List[str] = [
    "file_number",
    "received_date",
    "business_type",
    "report_type",
    "channel",
    "is_anonymous",
    "reporters",
    "accused",
    "violation",
    "title",
    "handling_comment",
    "jurisdiction",
    "pbc_branch",
    "is_group_event",
]


def iter_text_files(input_dir: Path) -> Iterable[Path]:
    for path in sorted(input_dir.glob("*.txt")):
        if path.is_file():
            yield path


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(errors="ignore")


def build_messages(document: str) -> List[Dict[str, str]]:
    system = (
        "你是信访举报材料的结构化助手。"
        "阅读用户提供的原文，只从文本中提取信息，不要臆测。"
        "输出 JSON 对象，字段缺失时填空字符串。"
        "收件日期使用 YYYY-MM-DD 格式（示例 2025-11-01）。"
        "枚举字段若无法匹配请填“其他”。"
        "业务类型枚举：银行帐户、支付帐户、银行卡、票据、电子支付业务、支付机构经营管理、信用卡类、其他、涉电信诈骗类、非法平台类、特约商户类、银行账户类(非涉诈)、扣划资金类、支付帐户类。"
        "举报类型枚举：信访；举报；投诉；投诉、举报；投诉、信访；举报、信访；投诉、举报、信访。"
        "来件渠道枚举：办公厅(室)、上级行转办、外单位转办、纪检转办、消保转办、来访、信件、电话、传真、电子邮件、线上举报、其他。"
        "是否匿名投诉枚举：是、否。"
        "管辖地请提取省/市/县三级中能确定的地理信息，缺失则留空。"
        "管辖人民银行使用文中提到的应负责的人民银行机构全称（例如 xxx人民银行支行），缺失留空。"
        "是否群体事件枚举：是、否。"
        "文件标题优先使用原文标题，如缺失则按“举报人关于被举报人违规行为的举报类型”格式拼出最贴近的短句。"
        "举报人和被举报人/机构可多选，使用英文逗号分隔。"
    )
    user = (
        "请从下文抽取字段，返回 JSON 对象，键名固定如下："
        '{"file_number","received_date","business_type","report_type","channel","is_anonymous",'
        '"reporters","accused","violation","title","handling_comment","jurisdiction","pbc_branch","is_group_event"}.'
        f"\n原文：\n{document}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def clean_json_text(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            text = text[start : end + 1]
    return text


def call_model(
    client: openai.OpenAI,
    model: str,
    document: str,
    temperature: float,
    max_retries: int,
) -> Dict[str, str]:
    messages = build_messages(document)
    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                response_format={"type": "json_object"},
            )
            content = resp.choices[0].message.content or "{}"
            payload = clean_json_text(content)
            data = json.loads(payload)
            return {key: str(data.get(key, "") or "") for key in EXTRACTION_KEYS}
        except Exception as exc:  # pragma: no cover - network errors are runtime only
            last_err = exc
            if attempt == max_retries:
                break
    raise RuntimeError(f"model extraction failed after {max_retries} attempts: {last_err}")


def write_excel(rows: List[List[str]], output_path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "extractions"
    ws.append(OUTPUT_HEADERS)
    for row in rows:
        ws.append(row)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="批量抽取文本信息并写入 Excel。")
    parser.add_argument("--input-dir", required=True, help="包含文本文件的目录（仅处理 .txt）。")
    parser.add_argument("--output", help="输出 Excel 路径，默认写入脚本同级的 data/extractions.xlsx。")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"模型名称（默认 {DEFAULT_MODEL}）。")
    parser.add_argument("--api-key", default=DEFAULT_API_KEY, help="OpenAI 风格 API key（默认读取 OPENAI_API_KEY）。")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"OpenAI 风格 base_url（默认 {DEFAULT_BASE_URL}）。")
    parser.add_argument("--temperature", type=float, default=0.0, help="生成温度，默认 0。")
    parser.add_argument("--max-retries", type=int, default=3, help="调用失败时的重试次数。")
    parser.add_argument("--timeout", type=int, default=120, help="HTTP 超时时间（秒）。")
    parser.add_argument("--insecure", action="store_true", help="跳过 HTTPS 证书校验（仅在可信环境使用）。")
    args = parser.parse_args()

    input_dir = Path(args.input_dir).expanduser().resolve()
    if not input_dir.exists() or not input_dir.is_dir():
        raise SystemExit(f"输入目录不存在: {input_dir}")

    output_path = (
        Path(__file__).resolve().parent / "data" / "extractions.xlsx"
        if not args.output
        else Path(args.output).expanduser().resolve()
    )

    if not args.api_key:
        raise SystemExit("缺少 API key，请通过 --api-key 或环境变量 OPENAI_API_KEY 提供。")

    http_client = httpx.Client(
        timeout=args.timeout,
        follow_redirects=True,
        verify=not args.insecure,
    )
    client = openai.OpenAI(api_key=args.api_key, base_url=args.base_url, http_client=http_client)

    text_files = list(iter_text_files(input_dir))
    if not text_files:
        raise SystemExit(f"目录下未找到 txt 文件: {input_dir}")

    rows: List[List[str]] = []
    for idx, path in enumerate(text_files, start=1):
        document = read_text(path)
        result = call_model(client, args.model, document, args.temperature, args.max_retries)
        row = [str(idx), document]
        row.extend(result.get(key, "") for key in EXTRACTION_KEYS)
        rows.append(row)
        print(f"[ok] {path.name} -> 行 {idx}")

    write_excel(rows, output_path)
    print(f"写入完成: {output_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
