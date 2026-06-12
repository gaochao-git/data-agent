"""智能问数指标集选择器。"""

from __future__ import annotations

import json
import asyncio
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage

from utils.logger import get_logger
from ..agent.middleware.token_usage_middleware import estimate_text_tokens
from ..semantic.utils import parse_llm_json
from .datasource_manager import get_datasource_manager
from .llm_config import get_chat_data_llm

logger = get_logger(__name__)

SELECTOR_CHUNK_TOKEN_BUDGET = 90_000
SELECTOR_SINGLE_REQUEST_TOKEN_LIMIT = 100_000
SELECTOR_MAX_CONCURRENCY = 4
SELECTOR_MAX_REDUCER_CANDIDATES = 20


def _truncate(value: Any, max_length: int) -> str:
    """裁剪指标集摘要文本，避免选择器上下文过大。"""
    text = str(value or "").strip()
    if len(text) <= max_length:
        return text
    return text[:max_length] + "..."


def _config_id(config: Dict[str, Any]) -> str:
    """读取指标集 ID。"""
    return str(config.get("id") or config.get("semantic_config_id") or "").strip()


def _config_name(config: Dict[str, Any]) -> str:
    """读取指标集名称。"""
    return str(config.get("name") or config.get("semantic_config_name") or "").strip()


def _compact_metric_summary(item: Dict[str, Any]) -> Dict[str, Any]:
    """构造指标选择摘要，只保留业务语义字段。"""
    summary = {
        "name": item.get("name") or "",
        "description": _truncate(item.get("description") or "", 160),
        "supplement": _truncate(item.get("metadata_supplement") or "", 240),
    }
    return {key: value for key, value in summary.items() if value}


def _compact_dimension_summary(item: Dict[str, Any]) -> Dict[str, Any]:
    """构造维度选择摘要，只保留业务语义字段。"""
    allowed_values = item.get("allowed_values") if isinstance(item.get("allowed_values"), list) else []
    mapping = item.get("mapping") if isinstance(item.get("mapping"), dict) else {}
    summary = {
        "name": item.get("name") or "",
        "description": _truncate(item.get("description") or "", 160),
        "supplement": _truncate(item.get("metadata_supplement") or "", 240),
        "type": item.get("type") or "",
        "allowed_values": [str(value)[:80] for value in allowed_values if str(value or "").strip()],
        "mapping": {str(key)[:80]: str(value)[:80] for key, value in mapping.items() if str(key or "").strip() and str(value or "").strip()},
    }
    return {key: value for key, value in summary.items() if value not in ("", [], {})}


def _summarize_semantic_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """构造给大模型判断的指标集摘要。"""
    metrics = config.get("metrics") or []
    dimensions = config.get("dimensions") or []
    config_id = _config_id(config)
    return {
        "id": config_id,
        "name": _config_name(config),
        "description": _truncate(config.get("description") or config.get("semantic_config_desc") or "", 180),
        "business_description": _truncate(config.get("ontology_rules") or "", 500),
        "metrics_total": len(metrics),
        "dimensions_total": len(dimensions),
        "metrics": [_compact_metric_summary(item) for item in metrics],
        "dimensions": [_compact_dimension_summary(item) for item in dimensions],
    }


def _build_selector_prompt(user_query: str, configs: List[Dict[str, Any]]) -> str:
    """构造指标集选择提示词。"""
    payload = {
        "user_query": str(user_query or "").strip(),
        "semantic_configs": configs,
    }
    return (
        "你是智能问数指标集选择器。请只从给定 semantic_configs 中判断哪个指标集最适合回答用户问题。\n"
        "要求：\n"
        "1) 只能选择给定列表中的 id，不能编造指标集、指标或维度。\n"
        "2) 逐个检查所有 semantic_configs，判断每个指标集是否能完整覆盖用户问题所需的指标、维度、过滤条件和业务口径。coverage 只能是 full、partial、none。\n"
        "3) 先判断用户问题是明细查询还是聚合统计：明细查询包括查询明细、查看列表、查看详情、列出记录、查询具体数据；聚合统计包括统计、数量、金额、趋势、排行、分组、TopN。\n"
        "4) 明细查询不要求 metrics，必须按 dimensions 判断展示字段和过滤字段是否可覆盖；只要该指标集的 dimensions 能表达用户要看的字段/条件，就可以 coverage=full。不要因为 metrics_total=0 就判定不支持明细查询。\n"
        "5) 聚合统计必须有可表达用户统计口径的 metrics；如果只存在 dimensions 而无对应指标，聚合统计不能 coverage=full。\n"
        "6) candidates 必须包含所有 coverage=full 的指标集；如果没有 full，可返回最相关的 partial 候选。\n"
        "7) 如果多个指标集都能完整覆盖，ambiguous 返回 true，selected_config_id 返回空字符串，candidates 返回这些指标集，交给用户选择。\n"
        "8) 如果用户问题必须同时使用多个指标集才能回答，cross_config_required 返回 true，selected_config_id 返回空字符串。\n"
        "9) 如果所有指标集都无法完整表达用户问题，selected_config_id 返回空字符串，confidence 返回 none。\n"
        "10) confidence 只能是 high、medium、low、none，只用于候选排序和展示，不能用来排除 coverage=full 的候选。\n"
        "11) 输出 JSON，不要解释文本。格式：\n"
        "{\"selected_config_id\":\"...\",\"coverage\":\"full|partial|none\",\"confidence\":\"high|medium|low|none\",\"reason\":\"...\",\"matched_metrics\":[\"...\"],\"matched_dimensions\":[\"...\"],\"ambiguous\":false,\"ambiguity_reason\":\"...\",\"cross_config_required\":false,\"cross_config_reason\":\"...\",\"candidates\":[{\"id\":\"...\",\"coverage\":\"full|partial|none\",\"confidence\":\"high|medium|low\",\"reason\":\"...\"}]}\n"
        "输入：\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )


def _estimate_selector_prompt_tokens(user_query: str, configs: List[Dict[str, Any]]) -> int:
    """估算选择器提示词 token 数。"""
    return estimate_text_tokens(_build_selector_prompt(user_query, configs))


def _chunk_semantic_config_summaries(
    summaries: List[Dict[str, Any]],
    user_query: str,
    token_budget: int = SELECTOR_CHUNK_TOKEN_BUDGET,
) -> List[List[Dict[str, Any]]]:
    """按完整指标集分片，禁止把单个指标集拆到两个分片。"""
    chunks: List[List[Dict[str, Any]]] = []
    current_chunk: List[Dict[str, Any]] = []
    safe_budget = max(int(token_budget or 0), 1)
    for summary in summaries:
        single_tokens = _estimate_selector_prompt_tokens(user_query, [summary])
        if single_tokens > SELECTOR_SINGLE_REQUEST_TOKEN_LIMIT:
            logger.warning(
                "单个指标集选择上下文超过限制但不会拆分: config_id=%s tokens=%s limit=%s",
                summary.get("id") or "",
                single_tokens,
                SELECTOR_SINGLE_REQUEST_TOKEN_LIMIT,
            )
        if not current_chunk:
            current_chunk = [summary]
            continue
        next_tokens = _estimate_selector_prompt_tokens(user_query, [*current_chunk, summary])
        if next_tokens > safe_budget:
            chunks.append(current_chunk)
            current_chunk = [summary]
        else:
            current_chunk.append(summary)
    if current_chunk:
        chunks.append(current_chunk)
    return chunks


def _candidate_score(candidate: Dict[str, Any]) -> int:
    """给候选指标集排序，优先完整覆盖和高置信度。"""
    coverage_score = {"full": 30, "partial": 10, "none": 0}.get(str(candidate.get("coverage") or ""), 0)
    confidence_score = {"high": 30, "medium": 20, "low": 10, "none": 0}.get(str(candidate.get("confidence") or ""), 0)
    return coverage_score + confidence_score


def _append_selection_candidate(
    result: List[Dict[str, Any]],
    seen: set[str],
    config_id: str,
    config_names: Dict[str, str],
    selection: Dict[str, Any],
    source: str,
) -> None:
    """追加一个去重后的分片候选。"""
    normalized_id = str(config_id or "").strip()
    if not normalized_id or normalized_id in seen or normalized_id not in config_names:
        return
    seen.add(normalized_id)
    result.append({
        "id": normalized_id,
        "name": config_names.get(normalized_id, normalized_id),
        "coverage": selection.get("coverage") or "",
        "confidence": selection.get("confidence") or "",
        "reason": selection.get("reason") or selection.get("ambiguity_reason") or selection.get("cross_config_reason") or "",
        "matched_metrics": selection.get("matched_metrics") or [],
        "matched_dimensions": selection.get("matched_dimensions") or [],
        "source": source,
    })


def _collect_selection_candidates(selections: List[Dict[str, Any]], configs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """收集所有分片返回的候选指标集。"""
    config_names = {_config_id(config): _config_name(config) or _config_id(config) for config in configs if _config_id(config)}
    candidates: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for index, selection in enumerate(selections):
        selected_id = str(selection.get("selected_config_id") or "").strip()
        if selected_id:
            _append_selection_candidate(candidates, seen, selected_id, config_names, selection, f"chunk_{index + 1}")
        for item in selection.get("candidates") or []:
            if not isinstance(item, dict):
                continue
            config_id = str(item.get("id") or "").strip()
            item_selection = {
                "coverage": item.get("coverage") or selection.get("coverage") or "",
                "confidence": item.get("confidence") or selection.get("confidence") or "",
                "reason": item.get("reason") or selection.get("reason") or "",
                "matched_metrics": selection.get("matched_metrics") or [],
                "matched_dimensions": selection.get("matched_dimensions") or [],
            }
            _append_selection_candidate(candidates, seen, config_id, config_names, item_selection, f"chunk_{index + 1}")
    return sorted(candidates, key=_candidate_score, reverse=True)[:SELECTOR_MAX_REDUCER_CANDIDATES]


def _build_reducer_prompt(user_query: str, chunk_results: List[Dict[str, Any]], candidates: List[Dict[str, Any]]) -> str:
    """构造分片结果汇总提示词。"""
    payload = {
        "user_query": str(user_query or "").strip(),
        "chunk_results": chunk_results,
        "candidates": candidates,
    }
    return (
        "你是智能问数指标集选择汇总器。输入来自多个并行分片，每个分片都包含完整指标集，候选项也都代表完整指标集。\n"
        "要求：\n"
        "1) 只能从 candidates 中选择 id，不能编造指标集。\n"
        "2) 明细查询按 dimensions 判断覆盖度，不要求 metrics；聚合统计才要求 metrics。不要因为候选 metrics_total=0 就排除可覆盖明细查询的指标集。\n"
        "3) candidates 必须包含所有 coverage=full 的指标集；如果没有 full，可返回最相关的 partial 候选。\n"
        "4) 如果只有一个指标集能完整覆盖，selected_config_id 返回该 id。\n"
        "5) 如果多个指标集都能完整覆盖，ambiguous 返回 true，selected_config_id 返回空字符串，candidates 返回这些指标集，交给用户选择。\n"
        "6) 如果问题必须同时使用多个指标集才能回答，cross_config_required 返回 true，selected_config_id 返回空字符串。\n"
        "7) 如果没有指标集能完整表达问题，selected_config_id 返回空字符串，confidence 返回 none。\n"
        "8) 输出 JSON，不要解释文本。格式与选择器一致。\n"
        "输入：\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )


def _build_empty_selection(reason: str = "") -> Dict[str, Any]:
    """构造空选择结果。"""
    return {
        "selected_config_id": "",
        "selected_config_name": "",
        "coverage": "none",
        "confidence": "none",
        "reason": reason,
        "matched_metrics": [],
        "matched_dimensions": [],
        "ambiguous": False,
        "ambiguity_reason": "",
        "cross_config_required": False,
        "cross_config_reason": "",
        "candidates": [],
    }


def _normalize_confidence(value: Any) -> str:
    """标准化置信度。"""
    confidence = str(value or "").strip().lower()
    return confidence if confidence in {"high", "medium", "low", "none"} else "none"


def _normalize_coverage(value: Any) -> str:
    """标准化覆盖度。"""
    coverage = str(value or "").strip().lower()
    return coverage if coverage in {"full", "partial", "none"} else ""


def _normalize_bool(value: Any) -> Optional[bool]:
    """标准化模型返回的布尔值。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in {"true", "1", "yes", "y", "是", "支持"}:
        return True
    if text in {"false", "0", "no", "n", "否", "不支持"}:
        return False
    return None


def _normalize_text_list(value: Any, max_items: int = 10) -> List[str]:
    """标准化模型返回的文本数组。"""
    if not isinstance(value, list):
        return []
    result: List[str] = []
    seen = set()
    for item in value:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text[:100])
        if len(result) >= max_items:
            break
    return result


def _normalize_candidates(value: Any, config_names: Dict[str, str]) -> List[Dict[str, str]]:
    """标准化候选指标集。"""
    if not isinstance(value, list):
        return []
    valid_ids = set(config_names)
    result: List[Dict[str, str]] = []
    seen = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        config_id = str(item.get("id") or item.get("config_id") or "").strip()
        if not config_id or config_id not in valid_ids or config_id in seen:
            continue
        seen.add(config_id)
        confidence = _normalize_confidence(item.get("confidence"))
        coverage = _normalize_coverage(item.get("coverage"))
        if not coverage:
            coverage = "partial"
        result.append({
            "id": config_id,
            "name": config_names.get(config_id, config_id),
            "coverage": coverage,
            "confidence": confidence,
            "reason": str(item.get("reason") or "").strip()[:300],
        })
        if len(result) >= SELECTOR_MAX_REDUCER_CANDIDATES:
            break
    return result


def _full_match_candidates(candidates: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """提取完整覆盖的候选指标集。"""
    return [item for item in candidates if item.get("coverage") == "full"]


def _normalize_ambiguous_selection(selection: Dict[str, Any]) -> Dict[str, Any]:
    """多个完整候选时强制转为待用户选择，避免模型自行猜一个。"""
    candidates = selection.get("candidates") if isinstance(selection.get("candidates"), list) else []
    full_candidates = _full_match_candidates(candidates)
    selected_config_id = str(selection.get("selected_config_id") or "").strip()
    if selected_config_id and len(full_candidates) <= 1:
        return selection
    if len(full_candidates) <= 1:
        return selection
    selection["selected_config_id"] = ""
    selection["selected_config_name"] = ""
    selection["confidence"] = "none"
    selection["ambiguous"] = True
    if not selection.get("ambiguity_reason"):
        names = "、".join(str(item.get("name") or item.get("id") or "").strip() for item in full_candidates[:5])
        selection["ambiguity_reason"] = f"多个指标集都能完整覆盖该问题：{names}"
    selection["candidates"] = full_candidates
    return selection


def _build_ambiguous_selection_from_full_candidates(candidates: List[Dict[str, str]]) -> Optional[Dict[str, Any]]:
    """根据多个完整覆盖候选构造待用户选择结果。"""
    full_candidates = _full_match_candidates(candidates)
    if len(full_candidates) <= 1:
        return None
    selection = _build_empty_selection()
    selection["coverage"] = "full"
    selection["ambiguous"] = True
    names = "、".join(str(item.get("name") or item.get("id") or "").strip() for item in full_candidates[:5])
    selection["ambiguity_reason"] = f"多个指标集都能完整覆盖该问题：{names}"
    selection["candidates"] = full_candidates
    return selection


def _parse_selection_response(content: str, configs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """解析指标集选择模型返回。"""
    parsed = parse_llm_json(str(content or ""), strict=False)
    if not isinstance(parsed, dict):
        parsed = {}
    config_names = {_config_id(config): _config_name(config) or _config_id(config) for config in configs if _config_id(config)}
    valid_ids = set(config_names)
    selected_config_id = str(parsed.get("selected_config_id") or parsed.get("config_id") or "").strip()
    if selected_config_id not in valid_ids:
        selected_config_id = ""
    confidence = _normalize_confidence(parsed.get("confidence"))
    if not selected_config_id:
        confidence = "none"
    coverage = _normalize_coverage(parsed.get("coverage"))
    if not coverage:
        coverage = "none"
    selection = {
        "selected_config_id": selected_config_id,
        "selected_config_name": config_names.get(selected_config_id, ""),
        "coverage": coverage,
        "confidence": confidence,
        "reason": str(parsed.get("reason") or "").strip()[:500],
        "matched_metrics": _normalize_text_list(parsed.get("matched_metrics")),
        "matched_dimensions": _normalize_text_list(parsed.get("matched_dimensions")),
        "ambiguous": bool(_normalize_bool(parsed.get("ambiguous"))),
        "ambiguity_reason": str(parsed.get("ambiguity_reason") or "").strip()[:500],
        "cross_config_required": bool(_normalize_bool(parsed.get("cross_config_required"))),
        "cross_config_reason": str(parsed.get("cross_config_reason") or "").strip()[:500],
        "candidates": _normalize_candidates(parsed.get("candidates"), config_names),
    }
    return _normalize_ambiguous_selection(selection)


def _build_chunk_result_payload(selection: Dict[str, Any]) -> Dict[str, Any]:
    """压缩分片结果，供 reducer 判断。"""
    return {
        "selected_config_id": selection.get("selected_config_id") or "",
        "selected_config_name": selection.get("selected_config_name") or "",
        "coverage": selection.get("coverage") or "",
        "confidence": selection.get("confidence") or "",
        "reason": selection.get("reason") or "",
        "matched_metrics": selection.get("matched_metrics") or [],
        "matched_dimensions": selection.get("matched_dimensions") or [],
        "ambiguous": bool(selection.get("ambiguous")),
        "ambiguity_reason": selection.get("ambiguity_reason") or "",
        "cross_config_required": bool(selection.get("cross_config_required")),
        "cross_config_reason": selection.get("cross_config_reason") or "",
        "candidates": selection.get("candidates") or [],
    }


async def _invoke_selector_prompt(llm: Any, prompt: str) -> str:
    """执行一次选择器模型调用。"""
    response = await llm.ainvoke([HumanMessage(content=prompt)])
    return str(getattr(response, "content", response) or "")


async def _select_semantic_config_chunk(
    llm: Any,
    user_query: str,
    chunk: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """选择单个分片内最匹配的指标集。"""
    prompt = _build_selector_prompt(user_query, chunk)
    content = await _invoke_selector_prompt(llm, prompt)
    return _parse_selection_response(content, chunk)


async def _select_semantic_config_chunks(
    llm: Any,
    user_query: str,
    chunks: List[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """并发执行多个指标集分片选择。"""
    semaphore = asyncio.Semaphore(SELECTOR_MAX_CONCURRENCY)

    async def run_chunk(chunk: List[Dict[str, Any]]) -> Dict[str, Any]:
        async with semaphore:
            return await _select_semantic_config_chunk(llm, user_query, chunk)

    results = await asyncio.gather(*(run_chunk(chunk) for chunk in chunks), return_exceptions=True)
    selections: List[Dict[str, Any]] = []
    for index, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error("指标集分片选择失败: chunk_index=%s error=%s", index + 1, result, exc_info=True)
            continue
        selections.append(result)
    return selections


async def _reduce_semantic_config_selections(
    llm: Any,
    user_query: str,
    selections: List[Dict[str, Any]],
    enabled_configs: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """汇总多个分片选择结果。"""
    if not selections:
        return _build_empty_selection("指标集自动选择失败，请稍后重试或手动选择指标集")
    candidates = _collect_selection_candidates(selections, enabled_configs)
    if not candidates:
        reasons = [str(item.get("reason") or "").strip() for item in selections if str(item.get("reason") or "").strip()]
        return _build_empty_selection(reasons[0] if reasons else "当前可用指标集暂不支持该问题")
    ambiguous_selection = _build_ambiguous_selection_from_full_candidates(candidates)
    if ambiguous_selection:
        return ambiguous_selection
    candidate_configs = [config for config in enabled_configs if _config_id(config) in {item["id"] for item in candidates}]
    prompt = _build_reducer_prompt(
        user_query=user_query,
        chunk_results=[_build_chunk_result_payload(selection) for selection in selections],
        candidates=candidates,
    )
    content = await _invoke_selector_prompt(llm, prompt)
    return _parse_selection_response(content, candidate_configs)


async def select_semantic_config_for_query(
    datasource_id: str | None,
    user_query: str,
    model_id: str | None = None,
) -> Optional[Dict[str, Any]]:
    """用大模型从当前数据源组可用指标集中选择最匹配项。"""
    normalized_datasource_id = str(datasource_id or "").strip()
    normalized_query = str(user_query or "").strip()
    if not normalized_datasource_id or not normalized_query:
        return None

    ds_manager = get_datasource_manager()
    configs = await ds_manager.get_semantic_configs_async(normalized_datasource_id)
    enabled_configs = [
        config for config in configs or []
        if _config_id(config) and bool(config.get("enabled", True))
    ]
    if not enabled_configs:
        return None

    summaries = [_summarize_semantic_config(config) for config in enabled_configs]
    chunks = _chunk_semantic_config_summaries(summaries, normalized_query)
    chunk_tokens = [
        _estimate_selector_prompt_tokens(normalized_query, chunk)
        for chunk in chunks
    ]
    logger.info(
        "智能问数指标集选择上下文: datasource_id=%s configs=%s chunks=%s chunk_tokens=%s max_chunk_tokens=%s",
        normalized_datasource_id,
        len(enabled_configs),
        len(chunks),
        chunk_tokens,
        max(chunk_tokens) if chunk_tokens else 0,
    )
    llm = get_chat_data_llm(model=model_id, streaming=False, temperature=0, max_tokens=1200)
    if len(chunks) == 1:
        selection = await _select_semantic_config_chunk(llm, normalized_query, chunks[0])
    else:
        chunk_selections = await _select_semantic_config_chunks(llm, normalized_query, chunks)
        selection = await _reduce_semantic_config_selections(llm, normalized_query, chunk_selections, enabled_configs)
    selection["auto_selected"] = bool(selection.get("selected_config_id"))
    logger.info(
        "智能问数指标集选择: datasource_id=%s configs=%s chunks=%s selected=%s confidence=%s",
        normalized_datasource_id,
        len(enabled_configs),
        len(chunks),
        selection.get("selected_config_id") or "",
        selection.get("confidence") or "",
    )
    return selection


__all__ = [
    "select_semantic_config_for_query",
    "_parse_selection_response",
    "_summarize_semantic_config",
]
