"""历史案例学习提示词。"""

import json
from typing import Any


def build_history_learning_prompt(
    *,
    semantic_config_payload: dict[str, Any],
    table_context: list[dict[str, Any]],
    compact_samples: list[dict[str, Any]],
) -> str:
    """构造历史学习维护建议生成提示词。"""
    return f"""
你是智能问数语义层维护助手。请只基于给定的当前语义模型、表结构和查询历史，分析哪些指标、维度或配置问题值得维护。

要求：
- 只输出 JSON 对象，不要 Markdown，不要解释。
- 查询历史已经由服务端限定为当前语义模型相关历史。
- 分来源使用证据：
  1. source=agent（智能问数）：主要学习 conversation_context 中的用户问题、追问、纠错反馈、工具错误；sql 只是辅助证据，不能把 SQL 结果当成唯一学习来源。
  2. source=builder（可视化查询）：主要学习用户手动组装的 builder_config，包括维度、指标表达式、过滤和排序；sql 只是编译后的辅助证据。
  3. source=sql（SQL查询）：主要学习用户自己写的 sql；服务端已确保 SQL 解析出的物理表全部属于当前语义模型表范围。
- SQL 查询只作为辅助学习样例，不能因为单条 SQL 查询就新增配置；只有和智能问数/可视化查询互相印证，或 SQL 查询多次重复同类口径时才输出建议。
- 建议必须来自历史样例，不要凭空创造。
- 成功的智能问数历史只作为弱信号：如果 SQL 已由当前语义模型成功生成并执行成功，通常说明当前配置已能表达该问题，不要仅因为成功历史就新增指标或维度。
- 成功历史只能用于辅助判断高频使用、用户问法和查询习惯；只有当多个历史样例反复出现“当前语义模型未维护、但 SQL 中稳定存在的派生指标/维度表达式”时，才可以输出候选指标/维度。
- 失败历史和用户纠错反馈优先用于学习配置缺口，包括缺失指标、缺失维度、缺失表关系、字段口径不清、表范围不匹配。
- 可视化查询是用户在页面上手动选择维度、指标、过滤和排序的强信号：如果 builder_config 中字段或表达式能对应当前语义模型相关表结构，且当前语义模型尚未维护，可以输出候选指标/维度；不要求同类样例重复出现。
- SQL 查询中，如果用户手动构造了当前语义模型缺失但反复使用的字段/表达式，可以作为候选配置来源。
- 保持保守：只有必要时才输出建议。没有明确证据时对应数组保持为空。
- 不要因为 SQL 别名、字段英文名、单次查询样例就输出业务补充；这些只作为参考，不能当作用户真实表达。可视化查询 builder_config 里的 semanticName、alias、displayName 是用户页面选择或语义模型字段名，可以作为候选名称参考。
- issues 只在以下情况输出：历史失败原因明确；或多个历史样例显示同一配置风险。不要输出泛泛的“可能导致无法识别”。
- metrics/dimensions 只有在失败历史明确指向缺失项、可视化查询 builder_config 明确选择了当前语义模型缺失的字段/表达式，或 SQL 查询多次稳定使用当前语义模型缺失的字段/表达式时才输出；成功智能问数不能单独作为新增依据。不确定表字段时宁可不输出候选项，改放 issues 或不输出。
- 不要建议当前语义模型已经存在的同名指标或维度，除非是明确的配置风险，此时放到 issues。
- 如果当前语义模型已将某个物理字段维护为维度，不要再基于同一字段建议月份、日期、时间等派生维度；时间粒度由查询时表达，不作为新维度维护。
- 如果当前语义模型已维护相同名称、相同表字段或相同表达式的指标，不要再输出为候选指标。
- 指标或维度如果无法确定表名/字段名，可以留空，但 reason 必须说明需要人工确认。
- 维度必须输出 logical_type，取值只能是 text / number / date / datetime / enum；不要输出 type 字段。无法判断时使用 text。
- 不要输出业务规则、业务补充、枚举定义、同义词沉淀类建议；历史学习不能把查询片段直接沉淀为业务事实。
- issues.content 用于说明风险，可以保留问题判断。
- sample_execution_ids 必须引用输入历史中的 execution_id。
- confidence 只能是 high / medium / low；source 只能是 agent / builder / sql。

输出格式：
{{
  "suggestions": {{
    "metrics": [
      {{"name":"候选指标名","table":"表名","field_name":"字段名或逻辑字段名","expression":"SQL表达式","reason":"为什么建议维护","source":"agent","confidence":"medium","support_count":1,"sample_execution_ids":["..."]}}
    ],
    "dimensions": [
      {{"name":"候选维度名","table":"表名","field_name":"逻辑字段名","column":"物理字段名","expression":"SQL表达式","logical_type":"text","reason":"为什么建议维护","source":"builder","confidence":"high","support_count":1,"sample_execution_ids":["..."]}}
    ],
    "issues": [
      {{"title":"问题标题","content":"缺失配置或风险说明","reason":"为什么这是问题","source":"agent","confidence":"medium","support_count":1,"sample_execution_ids":["..."]}}
    ]
  }}
}}

当前语义模型：
{json.dumps(semantic_config_payload, ensure_ascii=False)}

当前语义模型相关表结构：
{json.dumps(table_context, ensure_ascii=False)}

历史样例：
{json.dumps(compact_samples, ensure_ascii=False)}
""".strip()
