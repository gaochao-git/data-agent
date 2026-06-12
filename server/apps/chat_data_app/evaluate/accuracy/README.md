# ChatData Accuracy Evaluate

智能问数评测目录。当前只保留平台内数据库测试集评测能力，评估链路：

```text
NL -> DSL -> SQL -> 执行结果 -> 图表/答案
```

## 第一阶段指标

- `dsl_exact_match`：DSL 是否完全匹配。
- `sql_exact_match`：生成 SQL 与标准 SQL 归一化空白后是否完全匹配。
- `dsl_slot_accuracy`：metrics / dimensions / filters / having / order_by / limit / offset 分项准确率；展示方式不参与评测。
- `compile_success`：DSL 是否能编译为 SQL。
- `execution_success`：SQL 是否能执行成功。
- `result_match`：执行结果是否和 golden result 一致。

## 构建流程

1. 在语义配置页打开「智能问数评测」抽屉。
2. **测试集**：对当前指标集点击「生成测试集草稿」（按指标、维度、表关系生成 7 元组并编译 `gold_sql`），校对后「保存测试集」。
3. **评测**：测试集保存完成后，使用「接口链路」模式跑评测（每个指标集的测试集保存在 `chat_data_eval_case_set` / `chat_data_eval_case`）。

无测试集时不应直接评测；后端会拒绝缺少场景测试集的请求。

## 7 元组格式

```json
{
  "id": "order_operating_001",
  "nl": "查询订单总销量",
  "gold_dsl": {
    "metrics": ["销量"],
    "dimensions": [],
    "filters": [],
    "having": [],
    "order_by": []
  },
  "gold_sql": "SELECT SUM(quantity) AS sales_volume FROM order_items",
  "query_type": "aggregation",
  "difficulty": "easy",
  "source": "human",
  "business_domain": "order_operating"
}
```

字段说明：

| 字段 | 类型 | 说明 |
|------|------|------|
| `nl` | string | 自然语言问题，模拟真实用户提问 |
| `gold_dsl` | JSON | 标准 DSL，结构化中间语言 |
| `gold_sql` | string | 标准 SQL |
| `query_type` | enum | `filter` / `join` / `aggregation` / `subquery` |
| `difficulty` | enum | `easy` / `medium` / `hard` |
| `source` | enum | `human` / `llm` |
| `business_domain` | enum | 由业务领域梳理结果决定，例如 `order_operating` |

`id` 不是 7 元组业务字段，但建议保留，便于页面/接口按用例精确筛选和报告追踪。
