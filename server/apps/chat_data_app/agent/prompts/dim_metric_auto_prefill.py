"""维度指标自动预填提示词。"""

import json
from typing import Any, Dict, List


def build_prefill_model_guidance(table_model_type: str) -> str:
    """构造自动预填时的表类型口径说明。"""
    if table_model_type == "aggregated":
        return (
            "当前表是预聚合表。请识别分组粒度字段为 dimension，例如日期、分类、地区、状态、ID、名称等；"
            "识别已经聚合好的度量列为 metric，例如 count、amount、sum、avg、total、rate、score、qty、num 等。"
        )
    return (
        "当前表是明细表。请保守识别可聚合的度量字段为 metric，例如金额、数量、时长、分数、成本、价格等；"
        "ID、名称、日期、分类、状态、层级、枚举、描述类字段通常作为 dimension。"
    )


def _build_prefill_input_payload(
    *,
    data_key: str,
    data_payload: List[Dict[str, Any]],
    generate_business_flow: bool = False,
) -> Dict[str, Any]:
    """构造自动预填模型输入 JSON。"""
    payload: Dict[str, Any] = {data_key: data_payload}
    generation_request: Dict[str, bool] = {}
    if generate_business_flow:
        generation_request["business_flow"] = True
    if generation_request:
        payload["generation_request"] = generation_request
    return payload


def _build_knowledge_output_guidance(generate_business_flow: bool = False) -> str:
    """构造业务知识生成输出说明。"""
    field_item_format = "{\"table\":\"...\",\"column\":\"...\",\"role\":\"metric|dimension\"}"
    if not generate_business_flow:
        return (
            "4) 只判断字段角色，不要输出业务描述、元数据说明或 SQL 表达式。\n"
            "5) 必须覆盖输入中的所有字段，每个字段都返回一条。\n"
        )
    knowledge_fields = []
    if generate_business_flow:
        knowledge_fields.append("\"business_flow\":\"业务运转说明\"")
    knowledge_format = f",\"knowledge\":{{{','.join(knowledge_fields)}}}" if knowledge_fields else ""
    guidance_lines = [
        "4) 字段角色必须覆盖所有字段；同时按 generation_request 生成缺失的业务信息。",
        f"5) 输出格式：{{\"fields\":[{field_item_format}]{knowledge_format}}}",
        "6) 只生成 generation_request 中为 true 的内容，不要生成未请求字段；不要改写用户已维护内容。",
        "7) 补充信息只写非常必要且能降低歧义的确定信息：必须是仅靠表名、字段名、字段说明不容易理解，且表结构能支撑的信息。",
        "8) 不要逐字段解释、不要复述字段名/字段说明、不要输出常识性内容。",
    ]
    if generate_business_flow:
        guidance_lines.append("9) business_flow 面向业务整体：只写业务对象、业务流程、记录如何产生/流转、跨表业务关系、纳入/排除统计口径；不要写字段级含义、枚举值、ID/层级/单位/同义词。")
    guidance_lines.extend([
        "12) 如果没有必要补充，对应字符串值返回空字符串。",
        "13) 每个字符串值最多 3 条，按条目换行书写，格式为：1）...\\n2）...\\n3）...；每条只表达一个必要要点。",
        "14) 必须覆盖输入中的所有字段，每个字段都返回一条。",
    ])
    return (
        "\n".join(guidance_lines) + "\n"
    )


def build_auto_prefill_prompt(
    *,
    model_guidance: str,
    table_payload: List[Dict[str, Any]],
    generate_business_flow: bool = False,
) -> str:
    """构造维度指标自动预填主提示词。"""
    input_payload = _build_prefill_input_payload(
        data_key="tables",
        data_payload=table_payload,
        generate_business_flow=generate_business_flow,
    )
    knowledge_guidance = _build_knowledge_output_guidance(generate_business_flow)
    return (
        "你是数据建模助手。请根据字段语义判断每个字段更适合作为维度还是指标。\n"
        f"表类型口径：{model_guidance}\n"
        "要求：\n"
        "1) 每个字段只能二选一：metric 或 dimension；不要双选。\n"
        "2) 输出必须是 JSON，不要解释文本。\n"
        "3) 输出必须包含 fields 数组，每项至少包含 table、column、role。\n"
        f"{knowledge_guidance}"
        "输入数据：\n"
        f"{json.dumps(input_payload, ensure_ascii=False)}"
    )


def build_auto_prefill_retry_prompt(*, model_guidance: str, missing_payload: List[Dict[str, Any]]) -> str:
    """构造维度指标自动预填补偿提示词。"""
    input_payload = _build_prefill_input_payload(data_key="missing_fields", data_payload=missing_payload)
    return (
        "你是数据建模助手。请只判断以下缺失字段的角色。\n"
        f"表类型口径：{model_guidance}\n"
        "要求：\n"
        "1) 每个字段只能二选一：metric 或 dimension。\n"
        "2) 只输出 JSON，不要解释。\n"
        "3) 输出格式：{\"fields\":[{\"table\":\"...\",\"column\":\"...\",\"role\":\"metric|dimension\"}]}\n"
        "4) 只判断字段角色，不要输出业务描述、元数据说明或 SQL 表达式。\n"
        "5) 必须覆盖输入中的全部字段。\n"
        "输入数据：\n"
        f"{json.dumps(input_payload, ensure_ascii=False)}"
    )


def build_metric_recommendation_prompt(*, table_payload: List[Dict[str, Any]], business_context: str = "", max_metrics: int = 10) -> str:
    """构造自动预填的原子指标推荐提示词。"""
    payload = _build_prefill_input_payload(data_key="tables", data_payload=table_payload)
    context = str(business_context or "").strip()
    context_section = f"业务补充：\n{context}\n" if context else "业务补充：无\n"
    return (
        "你是数据建模助手。当前系统会把所有物理字段自动作为维度；你需要为所有物理字段生成显示名称，并推荐少量最可能被分析使用的基础原子指标。\n"
        f"{context_section}"
        "要求：\n"
        "1) fields 必须覆盖输入中的所有字段，每个物理字段返回一条；role 固定为 dimension。\n"
        "2) fields.name 是给用户看的字段显示名称，必须根据字段名、字段说明和业务补充生成，最多 15 个字。\n"
        f"3) metrics 最多推荐 {max_metrics} 个指标；如果没有高置信度指标，可以返回空数组。\n"
        "4) metrics 只选择明显可作为度量分析的字段，例如金额、数量、时长、费用、成本、收入、价格、分数、重量、面积等。\n"
        "5) 不要为 ID、编码、状态、类型、日期、时间、分区、flag、等级、排序号生成 SUM/AVG/MAX/MIN 指标。\n"
        "6) COUNT 只在字段明显表示可计数业务对象时推荐，例如订单号、用户ID、实例ID；不要为状态、类型、日期、分区、普通编码推荐 COUNT。\n"
        "7) 每个字段最多推荐 1 个最常用聚合函数：金额/数量优先 SUM，价格/单价/分数/率/比例优先 AVG，确有必要才用 MAX/MIN。\n"
        "8) 指标必须来自输入字段，expression 只能是 SUM(`字段`)、AVG(`字段`)、MAX(`字段`)、MIN(`字段`) 或 COUNT(`字段`) 之一。\n"
        "9) metrics.name 是给用户看的指标显示名称，必须是短名称，最多 15 个字。\n"
        "10) 所有 name 都不要包含逗号、句号、括号、关联关系、字段解释或完整列备注。\n"
        "11) 输出必须是 JSON，不要解释文本。\n"
        "12) 输出格式：{\"fields\":[{\"table\":\"...\",\"column\":\"...\",\"role\":\"dimension\",\"name\":\"订单编号\"}],\"metrics\":[{\"table\":\"...\",\"column\":\"...\",\"name\":\"销售额\",\"agg\":\"SUM|AVG|MAX|MIN|COUNT\",\"expression\":\"SUM(`字段`)\"}]}\n"
        "输入数据：\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )


def build_auto_prefill_knowledge_prompt(
    *,
    table_payload: List[Dict[str, Any]],
    generate_business_flow: bool = False,
) -> str:
    """构造业务知识补偿生成提示词。"""
    input_payload = _build_prefill_input_payload(
        data_key="tables",
        data_payload=table_payload,
        generate_business_flow=generate_business_flow,
    )
    knowledge_fields = []
    if generate_business_flow:
        knowledge_fields.append("\"business_flow\":\"业务运转说明\"")
    return (
        "你是数据建模助手。请只根据输入表结构和字段说明生成缺失的业务知识。\n"
        "要求：\n"
        "1) 只输出 JSON，不要解释文本。\n"
        f"2) 输出格式：{{\"knowledge\":{{{','.join(knowledge_fields)}}}}}\n"
        "3) knowledge 只生成 generation_request 中为 true 的字段，不要生成未请求字段。\n"
        "4) 只补充非常必要且能降低歧义的信息：必须是仅靠表名、字段名、字段说明不容易理解，且表结构能支撑的确定信息。\n"
        "5) 不要逐字段解释、不要复述字段名/字段说明、不要输出常识性内容；如果没有必要补充，对应 knowledge 字段返回空字符串。\n"
        "6) business_flow 面向业务整体：只写业务对象、业务流程、记录如何产生/流转、跨表业务关系、纳入/排除统计口径；不要写字段级含义、枚举值、ID/层级/单位/同义词。\n"
        "7) 每个字符串值最多 3 条，按条目换行书写，格式为：1）...\\n2）...\\n3）...；每条只表达一个必要要点。\n"
        "输入数据：\n"
        f"{json.dumps(input_payload, ensure_ascii=False)}"
    )
