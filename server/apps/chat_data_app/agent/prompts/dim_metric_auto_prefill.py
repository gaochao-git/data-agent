"""语义字段显示名称和原子指标推荐提示词。"""

import json
from typing import Any, Dict, List


def build_metric_recommendation_prompt(
    *,
    table_payload: List[Dict[str, Any]],
    business_context: str = "",
    max_metrics: int = 10,
) -> str:
    """构造字段显示名称和原子指标推荐提示词。"""
    context = str(business_context or "").strip()
    context_section = f"业务补充：\n{context}\n" if context else "业务补充：无\n"
    return (
        "你是数据建模助手。请为物理字段生成显示名称，并推荐少量高置信度的基础原子指标。\n"
        f"{context_section}"
        "要求：\n"
        "1) fields 必须覆盖所有输入字段；每项包含 table、column、name，name 最多 15 个字。\n"
        "2) 显示名称只能根据表名、字段名、字段类型、字段说明和业务补充生成。\n"
        f"3) metrics 最多推荐 {max_metrics} 个；没有高置信度指标时返回空数组。\n"
        "4) 只为明显的金额、数量、时长、费用、成本、收入、价格、分数、重量、面积等度量字段推荐指标。\n"
        "5) 不要为 ID、编码、状态、类型、日期、时间、分区、flag、等级或排序号推荐 SUM、AVG、MAX、MIN。\n"
        "6) COUNT 只用于明确可计数的业务对象字段，例如订单号、用户 ID、实例 ID。\n"
        "7) 每个字段最多推荐一个聚合函数；金额或数量优先 SUM，价格、单价、分数、率或比例优先 AVG。\n"
        "8) metrics 每项只包含 table、column、name、agg；agg 只能是 SUM、AVG、MAX、MIN、COUNT。\n"
        "9) 所有 name 必须是短名称，不要包含标点、关联关系、字段解释或完整列备注。\n"
        "10) 只输出 JSON，格式为："
        "{\"fields\":[{\"table\":\"...\",\"column\":\"...\",\"name\":\"订单编号\"}],"
        "\"metrics\":[{\"table\":\"...\",\"column\":\"...\",\"name\":\"销售额\",\"agg\":\"SUM\"}]}\n"
        "输入数据：\n"
        f"{json.dumps({'tables': table_payload}, ensure_ascii=False)}"
    )
