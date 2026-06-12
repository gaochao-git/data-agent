from __future__ import annotations

import json
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List, Tuple

from fastapi.concurrency import run_in_threadpool
from langchain.tools import tool
from langgraph.config import get_stream_writer

from apps.complaint_app.experience_loader import (
    find_expert_experience_for_second_category,
)
from apps.complaint_app.services.pipeline_base import ComplaintSSEEvent
from utils.logger import get_logger

from .case_analysis_config import get_case_env
from .case_analysis_llm import (
    get_llm_for_case_analysis,
    safe_parse_json_from_llm_output,
)
from .case_analysis_tool_identify_v2 import identify_case_items_core


logger = get_logger(__name__)


def _to_str_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        parts = [p.strip() for p in value.replace("；", ";").split(";")]
        return [p for p in parts if p]
    return []


def get_experience(primary_category: str, secondary_category: str) -> Dict[str, Any]:
    match_entry = find_expert_experience_for_second_category(secondary_category or "")

    if not match_entry: 
        return {
            "是否属于支付结算": "否",
            "调阅清单": [],
            "核查材料": [],
            "法律依据列表": [],
        }
        # 本地测试可直接mock专家数据
        # return {
        #     "是否属于支付结算": "是",
        #     "调阅清单": ["特约商户入网材料"],
        #     "核查材料": ["特约商户入网许可"],
        #     "法律依据列表": ["中国人民银行关于加强支付受理终端及相关业务管理的通知（银发〔2021〕259号）第五条、第三条、第四条", "中国人民银行公告〔2015〕第43号（非银行支付机构网络支付业务管理办法）第一条、第四条、第八条",
        #     "中国人民银行关于加强支付结算管理 防范电信网络新型违法犯罪有关事项的通知(银发〔2016〕261号)第一条、第二条、第三条"],
        # }

    in_scope = match_entry.get("is_in_scope") or "是"
    review_list = _to_str_list(match_entry.get("checklist") or [])
    check_materials = _to_str_list(match_entry.get("verification_points") or [])
    law_list = _to_str_list(match_entry.get("law_list") or [])

    return {
        "是否属于支付结算": in_scope,
        "调阅清单": review_list,
        "核查材料": check_materials,
        "法律依据列表": law_list,
    }


def get_law_item(key: str) -> List[Dict[str, str]]:
    import urllib.parse
    import urllib.request

    base_url = get_case_env(
        "CASE_ANALYSIS_LAW_BASE_URL", "http://39.97.229.91:8082/api/clause"
    )
    query = urllib.parse.urlencode({"key": key})
    url = f"{base_url}?{query}"

    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        data = json.loads(raw)
    except Exception as e:
        # logger.error(f"get_law_item error: {e}")
        print(f"get_law_item error: {e}")
        return []

    matches = data.get("matches") or []
    # print(f"matches: {matches}")
    results: List[Dict[str, str]] = []
    for m in matches:
        query_info = m.get("query") or {}
        # print(f"query_info: {query_info}")
        law_name = str(query_info.get("title") or "")
        item_name = str(query_info.get("clause") or "")

        result_info = m.get("result") or {}
        item_text = str(result_info.get("article_text") or "")

        if not law_name or not item_name or not item_text:
            continue
        results.append(
            {
                "law": law_name,
                "item": item_name,
                "item_text": item_text,
            }
        )

    return results


def _build_classification_prompt(complaint_desc: str) -> str:
    return f"""
你是一名熟悉银行投诉与监管口径的业务专家，请根据下面的“举报事项描述”做结构化分类。

【举报事项描述】
{complaint_desc}

【任务要求】
1. 请判断该举报事项属于哪一类问题，给出“一级分类”和“二级分类”（中文短句，尽量贴近监管口径）。
2. 说明你的分类理由，写入“分类理由”字段，要求简洁、专业，可直接给监管或合规同事阅读。
3. 请给出一个 0~1 之间的“匹配度”字符串，表示你对该分类结果的信心（如 "0.85"）。


【输出格式】
请严格返回 JSON 数组，不要附加额外说明，例如：
[
  {{"一级分类": "...", "二级分类": "...", "匹配度": "0.85", "分类理由": "..."}}
]
    """.strip()


def _build_law_refine_prompt(
    complaint_desc: str,
    category_infos: List[Dict[str, Any]],
    law_text_map: Dict[str, str],
) -> str:
    law_text_parts: List[str] = []
    for key, text in law_text_map.items():
        law_text_parts.append(f"- {key}：{text}")
    law_text_block = "\n".join(law_text_parts) if law_text_parts else "（暂无可用条文原文）"

    category_json = json.dumps(category_infos, ensure_ascii=False, indent=2)

    return f"""
你是一名金融法规与银行业务合规专家，将结合举报事项描述、初步分类结果以及相关法律条文原文，对分类和适用条款进行精炼与补充。

【举报事项描述】
{complaint_desc}

【初步分类结果】(JSON)
{category_json}

【可用法律条文原文】
{law_text_block}

【任务】
1. 在每个分类结果中，核实“调阅材料”“核查思路”是否合理，如有遗漏或不当请进行调整。
2. 根据条文原文，核查“法律法规及其原文”是否匹配举报事实，可适当增删或修正。
3. 保持“一级分类”“二级分类”“匹配度”“分类理由”“是否属于支付结算”字段不变或做轻微优化，不要引入新的字段。

【输出格式要求】
请直接返回 JSON 数组，每个元素对应一个精炼后的分类结果对象，字段包括：
- "一级分类": string
- "二级分类": string
- "匹配度": string
- "分类理由": string
- "是否属于支付结算": string
- "调阅材料": list[string]
- "核查思路": list[string]
- "法律法规及其原文": list[{{"law": string, "item": string, "item_text": string}}]

请直接返回 JSON 数组，不要附加额外说明。
    """.strip()


def _analyze_single_case(
    complaint_desc: str,
    analysis_llm: Any,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    complaint_desc = (complaint_desc or "").strip()
    if not complaint_desc:
        return [], []

    classification_prompt = _build_classification_prompt(complaint_desc)
    classification_raw = analysis_llm.invoke(classification_prompt)
    classification_default = {
        "一级分类": "其他",
        "二级分类": "其他",
        "匹配度": "0.5",
        "分类理由": "模型暂无法给出明确分类，仅根据举报描述做了初步判断。"
    }
    default_list = [classification_default]
    parsed = safe_parse_json_from_llm_output(classification_raw, default_list)

    if isinstance(parsed, list):
        classification_list = [c for c in parsed if isinstance(c, dict)] or default_list
    elif isinstance(parsed, dict):
        classification_list = [parsed]
    else:
        classification_list = default_list

    category_infos: List[Dict[str, Any]] = []
    artifact_case: List[Dict[str, str]] = []

    for cls in classification_list:
        p = str(cls.get("一级分类") or "其他").strip()
        s = str(cls.get("二级分类") or "其他").strip()
        m = str(cls.get("匹配度") or "0").strip()
        r = str(cls.get("分类理由") or "").strip()

        exp = get_experience(p, s)
        flag = exp.get("是否属于支付结算") or "否"
        review_list = exp.get("调阅清单") or []
        check_list = exp.get("核查材料") or []
        law_basis_list = exp.get("法律依据列表") or []

        print(f"law_basis_list: {law_basis_list}")

        laws_for_cat: List[Dict[str, str]] = []
        for law_entry in law_basis_list:
            key = str(law_entry).strip()
            if not key:
                continue
            items = get_law_item(key)
            for law_item in items:
                law_name = str(law_item.get("law") or "")
                item_name = str(law_item.get("item") or "")
                item_text = str(law_item.get("item_text") or "")
                if not law_name or not item_name or not item_text:
                    continue
                law_obj = {
                    "law": law_name,
                    "item": item_name,
                    "item_text": item_text,
                }
                laws_for_cat.append(law_obj)
                artifact_case.append(law_obj)

        print(f"laws_for_cat: {len(laws_for_cat)}")

        category_infos.append(
            {
                "一级分类": p,
                "二级分类": s,
                "匹配度": m,
                "分类理由": r,
                "是否属于支付结算": flag,
                "调阅材料": review_list,
                "核查思路": check_list,
                "法律法规及其原文": laws_for_cat,
            }
        )

    law_text_map: Dict[str, str] = {}
    for cat in category_infos:
        for law in cat.get("法律法规及其原文") or []:
            key = f"{law.get('law')}-{law.get('item')}"
            text = str(law.get("item_text") or "").strip()
            if key and text and key not in law_text_map:
                law_text_map[key] = text

    # print(f"law_text_map: {len(law_text_map)}")

    refined_infos = category_infos
    if law_text_map:
        refine_prompt = _build_law_refine_prompt(
            complaint_desc=complaint_desc,
            category_infos=category_infos,
            law_text_map=law_text_map,
        )
        refine_raw = analysis_llm.invoke(refine_prompt)
        parsed_refine = safe_parse_json_from_llm_output(refine_raw, category_infos)
        # print(f"parsed_refine: {parsed_refine}")

        if isinstance(parsed_refine, list):
            refined_infos = [c for c in parsed_refine if isinstance(c, dict)] or category_infos

    return refined_infos, artifact_case


def analyze_case_items_core(content: str) -> Tuple[List[Dict[str, Any]], List[Any]]:
    text = (content or "").strip()
    if not text:
        return [], []

    try:
        items, _artifact = identify_case_items_core(text)
    except Exception:
        logger.exception("identify_case_items_core failed in analyze_case_items_core")
        items, _artifact = [], {}

    if not isinstance(items, list):
        items = []

    analysis_llm = get_llm_for_case_analysis(streaming=False)

    all_refined_infos: List[Dict[str, Any]] = []
    artifact_results: List[Any] = []

    for item in items:
        if isinstance(item, dict):
            complaint_desc = str(item.get("complaint_desc") or "").strip()
        else:
            complaint_desc = str(item).strip()

        refined_infos, artifact_case = _analyze_single_case(complaint_desc, analysis_llm)
        all_refined_infos.extend(refined_infos)
        artifact_results.append(artifact_case)

    return all_refined_infos, artifact_results


def _analyze_case_items_langchain_impl(content: str) -> Tuple[str, str]:
    """
    基于专家经验和法律条文，对举报事项进行分类与分析（tool2）。

    工作流：
    - 节点 1：调用 tool1 抽取举报事项列表，仅使用 complaint_desc；
    - 节点 2：逐条举报事项分析（结构上可扩展为并行）：
        - 子节点 1：分类提示，得到一级分类、二级分类、匹配度、分类理由、是否属于支付结算；
        - 子节点 2：调用 get_experience() 获取调阅清单、核查材料、法律依据列表；
        - 子节点 3：对每个法律依据字符串调用 get_law_item() 获取条文原文，构建 law_text_map；
        - 子节点 4：将举报事项、分类结果、专家经验和条文原文交给 LLM，精细化适用条款与清单；
    - 节点 3：汇总为“分类结果列表”，作为工具返回。
    """
    writer = get_stream_writer()

    try:
        writer(
            {
                "event": "chat_message",
                "chat_message": {
                    "id": "analyze-case-items",
                    "role": "assistant",
                    "type": "tool",
                    "title": "工具调用",
                    "status": "processing",
                    "render_mode": "list",
                    "line_id": "tool-line-analyze-case-items-v2",
                    "order": 1,
                    "content": "分析举报事项中……",
                },
            }
        )
    except Exception:
        pass

    text = (content or "").strip()
    if not text:
        empty_obj = {"分类结果列表": []}
        empty = json.dumps(empty_obj, ensure_ascii=False)
        try:
            writer(
                {
                    "event": "chat_message",
                    "chat_message": {
                        "id": "analyze-case-items",
                        "role": "assistant",
                        "type": "tool",
                        "title": "工具调用",
                        "status": "end",
                        "render_mode": "list",
                        "content": "未检测到有效文本，已结束分析。",
                    },
                }
            )
        except Exception:
            pass
        return empty, empty

    try:
        items, _artifact = identify_case_items_core(text)
    except Exception:
        logger.exception("identify_case_items_core failed in _analyze_case_items_langchain_impl")
        items, _artifact = [], {}

    if not isinstance(items, list):
        items = []

    try:
        writer(
            {
                "event": "chat_message",
                "chat_message": {
                    "id": "analyze-case-items",
                    "role": "assistant",
                    "type": "tool",
                    "title": "工具调用",
                    "status": "processing",
                    "render_mode": "list",
                    "line_id": "tool-line-analyze-case-items-identified-v2",
                    "order": 2,
                    "content": f"已识别到 {len(items)} 条举报事项。",
                },
            }
        )
    except Exception:
        pass

    analysis_llm = get_llm_for_case_analysis(streaming=False)

    all_refined_infos: List[Dict[str, Any]] = []
    artifact_results: List[Any] = []

    for index, item in enumerate(items, start=1):
        if isinstance(item, dict):
            complaint_desc = str(item.get("complaint_desc") or "").strip()
        else:
            complaint_desc = str(item).strip()

        if not complaint_desc:
            complaint_desc = f"举报事项{index}"

        case_title = f"举报事项{index}"

        refined_infos, artifact_case = _analyze_single_case(complaint_desc, analysis_llm)
        all_refined_infos.extend(refined_infos)
        artifact_results.append(artifact_case)

        summary_parts: List[str] = []
        for info in refined_infos:
            p = str(info.get("一级分类") or "").strip()
            s = str(info.get("二级分类") or "").strip()
            if p and s:
                summary_parts.append(f"{p}-{s}")
            elif p:
                summary_parts.append(p)
        classify_summary = "、".join(dict.fromkeys(summary_parts)) if summary_parts else "其他"

        try:
            writer(
                {
                    "event": "chat_message",
                    "chat_message": {
                        "id": "analyze-case-items",
                        "role": "assistant",
                        "type": "tool",
                        "title": "工具调用",
                        "status": "processing",
                        "render_mode": "list",
                        "line_id": f"tool-line-analyze-case-items-v2-{index}",
                        "order": index + 2,
                        "content": f"举报事项《{case_title}》综合专家经验判断为《{classify_summary}》类举报。",
                    },
                }
            )
        except Exception:
            pass

    try:
        writer(
            {
                "event": "chat_message",
                "chat_message": {
                    "id": "analyze-case-items",
                    "role": "assistant",
                    "type": "tool",
                    "title": "工具调用",
                    "status": "end",
                    "render_mode": "list",
                    "content": "举报事项分析完成。",
                },
            }
        )
    except Exception:
        pass

    content_obj = {"分类结果列表": all_refined_infos}
    content_str = json.dumps(content_obj, ensure_ascii=False)
    artifact_str = json.dumps(artifact_results, ensure_ascii=False)

    return content_str, artifact_str


async def stream_analyze_case_items(
    user_id: str,
    base_id: str,
    content: str,
    *,
    task: int = 3,
) -> AsyncGenerator[str, None]:
    start_dt = datetime.utcnow()
    start_ts_str = start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    text = (content or "").strip()

    try:
        yield ComplaintSSEEvent(
            base_id=base_id,
            task=task,
            status="started",
            message="开始分析举报事项…",
            agent_say="我会结合专家经验和法规条文，对举报事项进行结构化分析。",
            ts=start_ts_str,
            extra={},
        ).to_sse(user_id=user_id)

        if not text:
            empty_obj = {"分类结果列表": []}
            end_dt = datetime.utcnow()
            end_ts_str = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            duration_ms = int((end_dt - start_dt).total_seconds() * 1000)
            yield ComplaintSSEEvent(
                base_id=base_id,
                task=task,
                status="completed",
                progress=100,
                message="未检测到有效文本，已结束分析。",
                agent_say="未检测到可分析的举报文本，已结束本次分析。",
                ts=end_ts_str,
                extra={
                    "result": empty_obj,
                    "artifact": [],
                    "duration_ms": duration_ms,
                    "counts": {"cases": 0},
                },
            ).to_sse(user_id=user_id)
            return

        refined_infos, artifact_results = await run_in_threadpool(
            analyze_case_items_core,
            text,
        )

        end_dt = datetime.utcnow()
        end_ts_str = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        duration_ms = int((end_dt - start_dt).total_seconds() * 1000)
        total_cases = len(refined_infos)

        yield ComplaintSSEEvent(
            base_id=base_id,
            task=task,
            status="completed",
            progress=100,
            message=f"举报事项分析完成，共生成 {total_cases} 条分类结果。",
            agent_say="举报事项分析已完成，可以查看分类结果与适用条款。",
            ts=end_ts_str,
            extra={
                "result": {"分类结果列表": refined_infos},
                "artifact": artifact_results,
                "duration_ms": duration_ms,
                "counts": {"cases": total_cases},
            },
        ).to_sse(user_id=user_id)
    except Exception as exc:
        logger.exception(
            "analyze_case_items stream failed, base_id=%s user_id=%s",
            base_id,
            user_id,
        )
        yield ComplaintSSEEvent(
            base_id=base_id,
            task=task,
            status="error",
            message=f"举报事项分析失败：{exc}",
            agent_say="举报事项分析失败，请稍后重试。",
            ts=datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            extra={},
        ).to_sse(user_id=user_id)


def analyze_case_items(content: str) -> Tuple[str, str]:
    return _analyze_case_items_langchain_impl(content)


analyze_case_items_tool = tool(
    "analyze_case_items",
    response_format="content_and_artifact",
)(_analyze_case_items_langchain_impl)


__all__ = [
    "analyze_case_items_core",
    "analyze_case_items",
    "analyze_case_items_tool",
    "stream_analyze_case_items",
]
