# 举报项分析思考块模板

以下模板用于“举报项分析”功能的思考块（steps）结构。该结构包含“思考完成”与“工具调用”两部分，不包含“结果”步骤。

## Steps 模板

### 1) 思考完成
- id: `<message_id>-thinking`
- label: `思考完成`
- status: `done`
- detail：

```
我会先从摘要中提取明确的举报事项，再结合专家经验进行分类，
补全核查要点和所需材料，并调阅对应法律条文原文。
最后综合上述信息给出结构化分析结论。
```

### 2) 工具调用
- id: `<message_id>-tool`
- label: `工具调用`
- status: `done`

#### children（5 个子步骤）

A. 梳理举报事项
- id: `complaint_items_extract`
- label: `梳理举报事项`
- status: `done`
- detailLines：
  - lineId: `items-count`
    - content: `已从摘要中梳理出 {count} 条举报事项。`
  - lineId: `item-1`
    - content: `事项1：{item_text}`
  - lineId: `item-2`
    - content: `事项2：{item_text}`
  - 以此类推

B. 经验匹配分类
- id: `complaint_expert_classify`
- label: `我将在专家经验库里找出最佳的一级分类和二级分类`
- status: `done`
- detailLines：
  - lineId: `classify-1`
    - content: `事项1：{一级分类}-{二级分类}（匹配度 {score}）`
  - lineId: `classify-2`
    - content: `事项2：{一级分类}-{二级分类}（匹配度 {score}）`

C. 核查要点补齐
- id: `complaint_expert_fields`
- label: `我会按一级、二级分类补齐调阅清单、核查材料、法律依据和是否涉及支付结算`
- status: `done`
- detailLines：
  - lineId: `scope-1`
    - content: `事项1：是否涉及支付结算：{是/否}`
  - lineId: `review-1`
    - content: `事项1：调阅清单：{list}`
  - lineId: `verify-1`
    - content: `事项1：核查材料：{list}`
  - lineId: `lawlist-1`
    - content: `事项1：法律依据列表：{list}`
  - 事项2、事项3按编号追加

D. 调阅法律条文
- id: `complaint_law_fetch`
- label: `调阅法律条文`
- status: `done`
- detailLines：
  - lineId: `law-1`
    - content: `事项1：已调阅《{law_name}》{clause}`
  - lineId: `law-2`
    - content: `事项1：已调阅《{law_name}》{clause}`
  - 同事项多条法条可用 `law-1-1`、`law-1-2`

E. 整理分析结论
- id: `complaint_analysis_summary`
- label: `整理分析结论`
- status: `done`
- detailLines：
  - lineId: `summary-done`
    - content: `已完成结构化分析结果整理。`

## lineId 规则
- `items-count` / `item-1` / `item-2`
- `classify-1` / `classify-2`
- `scope-1` / `review-1` / `verify-1` / `lawlist-1`
- `law-1` / `law-1-1` / `law-1-2`
- `summary-done`
