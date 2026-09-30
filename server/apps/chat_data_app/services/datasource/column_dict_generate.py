"""字段数据字典候选值生成（自 routers/datasource_admin.py 下沉的 LLM 编排）。

职责：构造生成提示词 → 调用大模型 → 解析校验候选字典。router 只保留
鉴权、404 判断与响应组装。
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage

from utils.alarm import emit_llm_connection_alarm_if_needed
from utils.llm_json import parse_llm_json
from utils.logger import get_logger

from ..llm_config import get_chat_data_llm

logger = get_logger(__name__)


def build_column_dict_generate_prompt(table_name: str, column_name: str, column: dict[str, Any], text: str) -> str:
    """构造字段数据字典生成提示词。"""
    payload = {
        "table_name": table_name,
        "column_name": column_name,
        "column_type": column.get("mysql_type") or column.get("hive_type") or column.get("type") or "",
        "column_description": column.get("description") or "",
        "user_text": str(text or "").strip()[:10000],
    }
    return (
        "你是数据字典维护助手。请从用户文本中抽取当前字段的数据字典。\n"
        "要求：\n"
        "1) 只抽取文本中明确出现或能直接对应的信息，不要猜测、不要扩展。\n"
        "2) enum_values 表示字段真实入库值与业务含义，value 必须是真实入库值，例如 0、1、RUNNING；label 是业务含义，例如 下线、在线、运行中。\n"
        "3) unit 表示数值单位，例如 元、万元、秒；如果文本没有明确单位，返回空字符串。\n"
        "4) 不要生成 storage_format，column_type 只是数据库字段类型，不代表字段真实数据存储格式。\n"
        "5) 不要生成同义词。\n"
        '6) 只输出 JSON，不要解释文本。格式：{"dict":{"enum_values":[{"value":"...","label":"..."}],"unit":"..."}}\n'
        "输入数据：\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )


def parse_column_enum_values(raw_enums: Any) -> list[dict[str, str]]:
    """解析字段枚举候选值列表，去重并截断长度。"""
    enum_values: list[dict[str, str]] = []
    seen: set[str] = set()
    if not isinstance(raw_enums, list):
        return enum_values
    for item in raw_enums:
        if not isinstance(item, dict):
            continue
        enum_value = str(item.get("value") or item.get("enum_value") or "").strip()[:128]
        enum_label = str(item.get("label") or item.get("enum_label") or "").strip()[:255]
        if not enum_value or enum_value in seen:
            continue
        seen.add(enum_value)
        enum_values.append({"value": enum_value, "label": enum_label})
    return enum_values


def parse_column_dict_generate_response(content: str) -> dict[str, Any]:
    """解析字段数据字典生成模型返回。"""
    parsed = parse_llm_json(str(content or ""), strict=False)
    raw_dict = parsed.get("dict") if isinstance(parsed, dict) else {}
    if not isinstance(raw_dict, dict):
        raw_dict = {}
    raw_enums = raw_dict.get("enum_values") or raw_dict.get("enums") or []
    return {
        "enum_values": parse_column_enum_values(raw_enums)[:100],
        "synonyms": [],
        "storage_format": "",
        "unit": str(raw_dict.get("unit") or "").strip()[:100],
    }


def find_datasource_column(
    datasource: dict[str, Any] | None, table_name: str, column_name: str
) -> dict[str, Any] | None:
    """在数据源表结构中定位指定字段。"""
    target_column = None
    for table in (datasource or {}).get("ds_tables") or []:
        if str(table.get("table_name") or "").strip() != table_name:
            continue
        for column in table.get("columns") or []:
            if str(column.get("name") or "").strip() != column_name:
                continue
            target_column = column
            break
    return target_column


async def invoke_column_dict_llm(prompt: str, datasource_id: str, table_name: str, column_name: str):
    """调用字段数据字典生成模型，失败时上报连接报警。"""
    llm = get_chat_data_llm(streaming=False, max_tokens=1200)
    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)])
    except Exception as exc:
        emit_llm_connection_alarm_if_needed(
            exc,
            ext1={
                "purpose": "datasource_column_dict_generate",
                "datasource_id": datasource_id,
                "table_name": table_name,
                "column_name": column_name,
            },
        )
        raise
    return response.content if hasattr(response, "content") else str(response)


async def generate_column_dict_candidates(
    target_column: dict[str, Any],
    table_name: str,
    column_name: str,
    text: str,
    datasource_id: str,
) -> dict[str, Any]:
    """生成字段数据字典候选值（提示词 → LLM → 解析），不直接保存。"""
    prompt = build_column_dict_generate_prompt(table_name, column_name, target_column, text)
    content = await invoke_column_dict_llm(prompt, datasource_id, table_name, column_name)
    return parse_column_dict_generate_response(str(content))
