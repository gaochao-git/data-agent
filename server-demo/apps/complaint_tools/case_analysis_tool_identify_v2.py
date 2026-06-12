from __future__ import annotations

import json
import urllib.request
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List, Tuple

from fastapi.concurrency import run_in_threadpool
from langchain.tools import tool
from langgraph.config import get_stream_writer

from apps.complaint_app.services.pipeline_base import ComplaintSSEEvent
from utils.logger import get_logger

from .case_analysis_llm import get_llm_for_case_analysis, safe_parse_json_from_llm_output


logger = get_logger(__name__)


def _build_case_item_prompt(content: str) -> str:
    return f"""
你是一名金融投诉与案例分析助手，请从下面的文本中识别“举报事项”列表。
【任务要求】1. 将整段文本按“一个独立的举报事项/争议点”进行拆分；
2. 每个举报事项需要给出：
   - "complaint_desc": 对该举报事项的详细描述，保留原文关键信息；
   - "complaint_law_list": 举报人引用或提及的法律、法规、规章或规范性文件名称列表（只要名称，不需要条款内容），如 ["中华人民共和国消费者权益保护法", "商业银行法"]；
3. 如果无法识别出任何事项，返回空数组 []。
【输出格式】请严格返回 JSON 数组（不要包含其他多余文字），形如：
[
  {{"complaint_desc": "举报事项1的描述", "complaint_law_list": ["法律名称1", "法律名称2"]}},
  {{"complaint_desc": "举报事项2的描述", "complaint_law_list": ["法律名称3"]}}
]

【待分析文本】{content}
    """.strip()


_POLICIES_LOADED = False
_POLICIES_TITLE_TO_AGENCY: Dict[str, str] = {}
_POLICIES_TITLE_SET: set = set()


def _load_policies() -> None:
    """
    调用政策库接口，构建 {title: agency} 字典和 title 集合。
    接口：http://39.97.229.91:8082/api/policies
    返回示例：
        {"policies": [{"title": "...", "agency": "..."}, ...]}
    """
    global _POLICIES_LOADED, _POLICIES_TITLE_TO_AGENCY, _POLICIES_TITLE_SET
    if _POLICIES_LOADED:
        return

    url = "http://39.97.229.91:8082/api/policies"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        data = json.loads(raw)
    except Exception:
        _POLICIES_LOADED = True
        return

    policies = data.get("policies") or []
    for p in policies:
        if not isinstance(p, dict):
            continue
        title = str(p.get("title") or "").strip()
        agency = str(p.get("agency") or "中国人民银行").strip()
        if not title:
            continue
        _POLICIES_TITLE_TO_AGENCY[title] = agency or "库中未找到"
        _POLICIES_TITLE_SET.add(title)

    _POLICIES_LOADED = True


def _get_agency_for_complaint_law(complaint_law: str) -> str:
    """
    验证单个法律名称是否存在。
    输入：complaint_law（举报人引用的法律名称）
    输出：对应的 agency，如在集合中找不到则为“库中未找到”。
    """
    complaint_law = (complaint_law or "").strip()
    if not complaint_law:
        return "库中未找到"

    _load_policies()
    if complaint_law in _POLICIES_TITLE_SET:
        return _POLICIES_TITLE_TO_AGENCY.get(complaint_law, "库中未找到")

    return "库中未找到"


def _normalize_law_list(raw: Any) -> List[str]:
    """
    规范化法律名称列表，支持字符串/列表输入。
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        trimmed = raw.strip()
        if not trimmed:
            return []
        return [trimmed]
    if isinstance(raw, list):
        result: List[str] = []
        for item in raw:
            if not isinstance(item, str):
                continue
            trimmed = item.strip()
            if trimmed:
                result.append(trimmed)
        return result
    return []


def identify_case_items_core(content: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    业务核心逻辑：从一段长文本（如投诉材料、案例描述等）中识别举报事项。

    返回值：
    - (items, artifact)
      - items: [
            {
                "complaint_desc": str,
                "complaint_law_list": [{complaint_law: agency}],
            }
        ]
      - artifact: {"law_info": [{complaint_law: agency}]}
    """
    text = (content or "").strip()
    if not text:
        return [], {"law_info": []}

    prompt = _build_case_item_prompt(text)
    llm = get_llm_for_case_analysis(streaming=False)

    try:
        result = llm.invoke(prompt)
        parsed = safe_parse_json_from_llm_output(result, default=[])
    except Exception:
        parsed = []

    items: List[Dict[str, Any]] = []
    all_law_info: List[Dict[str, str]] = []

    if isinstance(parsed, list):
        for idx, item in enumerate(parsed, start=1):
            if isinstance(item, dict):
                complaint_desc = str(
                    item.get("complaint_desc")
                    or item.get("summary")
                    or item.get("title")
                    or f"举报事项 {idx}"
                )
                law_names = _normalize_law_list(item.get("complaint_law_list"))
            else:
                complaint_desc = str(item)
                law_names = []

            complaint_law_list: List[Dict[str, str]] = []
            for law_name in law_names:
                agency = _get_agency_for_complaint_law(law_name)
                entry = {law_name: agency}
                complaint_law_list.append(entry)
                all_law_info.append(entry)

            items.append(
                {
                    "complaint_desc": complaint_desc,
                    "complaint_law_list": complaint_law_list,
                }
            )
    elif isinstance(parsed, dict):
        complaint_desc = str(
            parsed.get("complaint_desc")
            or parsed.get("summary")
            or parsed.get("title")
            or "举报事项"
        )
        law_names = _normalize_law_list(parsed.get("complaint_law_list"))
        complaint_law_list: List[Dict[str, str]] = []
        for law_name in law_names:
            agency = _get_agency_for_complaint_law(law_name)
            entry = {law_name: agency}
            complaint_law_list.append(entry)
            all_law_info.append(entry)

        items.append(
            {
                "complaint_desc": complaint_desc,
                "complaint_law_list": complaint_law_list,
            }
        )
    else:
        items.append({"complaint_desc": text, "complaint_law_list": []})

    artifact = {"law_info": all_law_info}
    return items, artifact


def _identify_case_items_langchain_impl(
    content: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    识别一段文本中的举报事项列表，并检验举报人引用的法律是否存在（tool1）。

    输入：
    - content: 一段长文本（如投诉材料、案例描述等）。

    返回值：
    - (items, artifact)
      - items: [
            {
                "complaint_desc": str,
                "complaint_law_list": [{{complaint_law: agency}}],
            }
        ]
      - artifact: {"law_infot": [{{complaint_law: agency}}]}
    """
    writer = get_stream_writer()

    try:
        writer(
            {
                "event": "chat_message",
                "chat_message": {
                    "id": "identify-case-items",
                    "role": "assistant",
                    "type": "tool",
                    "title": "工具调用",
                    "status": "processing",
                    "render_mode": "list",
                    "line_id": "tool-line-identify-case-items",
                    "order": 1,
                    "content": "识别举报事项中…",
                },
            }
        )
    except Exception:
        pass

    items, artifact = identify_case_items_core(content)

    try:
        writer(
            {
                "event": "chat_message",
                "chat_message": {
                    "id": "identify-case-items",
                    "role": "assistant",
                    "type": "tool",
                    "title": "工具调用",
                    "status": "end",
                    "render_mode": "list",
                    "content": f"识别完成，共 {len(items)} 条举报事项。",
                },
            }
        )
    except Exception:
        pass

    return items, artifact


async def stream_identify_case_items(
    user_id: str,
    base_id: str,
    content: str,
    *,
    task: int = 3,
) -> AsyncGenerator[str, None]:
    """
    供 FastAPI SSE 接口使用的版本，参考 extraction_pipeline 的流式输出形式。
    """
    start_dt = datetime.utcnow()
    start_ts_str = start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        yield ComplaintSSEEvent(
            base_id=base_id,
            task=task,
            status="started",
            message="开始识别举报事项...",
            agent_say="我会根据原文识别举报事项。",
            ts=start_ts_str,
            extra={},
        ).to_sse(user_id=user_id)

        items, artifact = await run_in_threadpool(identify_case_items_core, content)

        end_dt = datetime.utcnow()
        end_ts_str = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        duration_ms = int((end_dt - start_dt).total_seconds() * 1000)

        yield ComplaintSSEEvent(
            base_id=base_id,
            task=task,
            status="completed",
            progress=100,
            message=f"识别完成，共 {len(items)} 条举报事项。",
            agent_say="举报事项识别已完成。",
            ts=end_ts_str,
            extra={
                "items": items,
                "artifact": artifact,
                "duration_ms": duration_ms,
                "counts": {"complaints": len(items)},
            },
        ).to_sse(user_id=user_id)
    except Exception as exc:
        logger.exception("identify_case_items stream failed, base_id=%s user_id=%s", base_id, user_id)
        yield ComplaintSSEEvent(
            base_id=base_id,
            task=task,
            status="error",
            message=f"举报事项识别失败：{exc}",
            agent_say="举报事项识别失败，请稍后重试。",
            ts=datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            extra={},
        ).to_sse(user_id=user_id)


def identify_case_items(content: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    普通函数调用入口，保持向后兼容：复用 LangChain 实现（包含 get_stream_writer SSE 输出），
    在没有 LangGraph 上下文时，SSE 写入会被安全忽略。
    """
    return _identify_case_items_langchain_impl(content)


identify_case_items_tool = tool(
    "identify_case_items",
    response_format="content_and_artifact",
)(_identify_case_items_langchain_impl)


__all__ = [
    "identify_case_items_core",
    "identify_case_items",
    "identify_case_items_tool",
    "stream_identify_case_items",
]
