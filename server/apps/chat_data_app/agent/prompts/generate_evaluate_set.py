"""智能问数评测集生成提示词。"""

import json
from typing import Any


def build_evaluation_draft_case_prompt(
    *,
    user_input: str,
    is_sql_input: bool,
    nl_example: str,
    business_domain: str,
    existing_questions: list[str],
    semantic_config_payload: dict[str, Any],
) -> str:
    """构造单条评测用例草稿生成提示词。"""
    return f"""
你是智能问数 NL2DSL2SQL 准确率评测数据构造助手。
请基于用户输入和当前语义配置，生成用于评测的 gold_dsl 草稿。用户输入可能是自然语言问题，也可能是 SQL。

要求：
- 只输出 JSON 对象，不要 Markdown，不要解释。
- 如果用户输入是自然语言问题，nl 必须原样使用用户输入。
- 如果用户输入是 SQL，请基于 SQL 生成一句自然语言问题，并基于 SQL 反推 gold_dsl；不要把 SQL 原文放进 nl。
- SQL 只用于反推标准语义结构；gold_sql 仍由服务端根据 gold_dsl 统一编译。
- source 固定为 "llm"。
- business_domain 使用 "{business_domain}"。
- query_type 只能是 filter / join / aggregation / subquery。
- difficulty 只能是 easy / medium / hard。
- gold_dsl 必须只使用语义配置中存在的指标和维度名称。
- 只有用户输入明确表达排序、排名、Top、前N、升序或降序时，gold_dsl.order_by 才允许非空；普通“查询各X的Y”“按时间统计”不要补排序。
- 只有用户输入明确表达 Top、前N、分页、明细行数限制时，gold_dsl.limit 才允许非空；普通汇总、分组统计、筛选统计不要补默认 limit。
- 只有用户输入明确表达分页、跳过前N条、从第N条开始时，gold_dsl.offset 才允许非空；普通查询不要补 offset。
- gold_sql 先输出空字符串，服务端会基于 gold_dsl 编译。

输出格式：
{{
  "nl": {nl_example},
  "gold_dsl": {{"metrics": [], "dimensions": [], "filters": [], "having": [], "order_by": []}},
  "gold_sql": "",
  "query_type": "aggregation",
  "difficulty": "easy",
  "source": "llm",
  "business_domain": "{business_domain}"
}}

用户输入类型：{"SQL" if is_sql_input else "自然语言"}
用户输入：{json.dumps(user_input, ensure_ascii=False)}
已有问题：{json.dumps(existing_questions[:80], ensure_ascii=False)}
语义配置：{json.dumps(semantic_config_payload, ensure_ascii=False)}
""".strip()
