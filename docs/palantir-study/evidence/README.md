# 浏览器 / 接口验证证据（2026-10-25）

对应改动：Phase A 对象集合（static + temporary）+ QA-010 复查。页面
`http://localhost:8089/chatdata?parentComponentName=执法检查`，模型 `deepseek/deepseek-v4-flash`。

| 文件 | 场景 | 关键结论 |
|---|---|---|
| `objset-done.png` | 同一轮：查财付通前 3 客户 → `save_object_set` → `load_object_set` | 集合 `ftpay_customer_top3`、`object_key_count=3`、`total=3/returned=3/missing_count=0`，有效期至次日 19:17（24h） |
| `objset-cross-turn.png` | 下一轮：只给集合名「加载刚才保存的对象集合」 | 命中 `ftpay_customer_top3`，返回 3 个客户（杜佑/田静博等）用户名与邮箱，证明跨轮持久化 |
| `qa010-rerun.png` | 按原评测 QA-010 复跑：两笔订单明细/发货单统计 | ORG1-ORD001906=3 条明细、ORG1-ORD004041=2 条明细，各 1 张发货单；与原评测「1906 少返 1 行」相反，DEF-01 当前 HEAD 不可复现 |
| `obs01-model-persist.png` | 选中 deepseek 后连续新建任务并发送消息 | 模型稳定保持 `deepseek/deepseek-v4-flash`，不再回落默认 Qwen；本轮会话标题生成成功（「助手功能介绍咨询」），控制台无「生成标题失败」 |

接口级复核（同轮执行）：

- `PUT /api/chatdata/admin/meta/ontology/object-types/customer/object-sets/{name}` → 保存成功，
  `object_key_count=3`；
- `GET .../object-sets/{name}/objects?limit=5` → `total=3/returned=3/missing=0`；
- `GET .../object-sets` → 列表包含 `ftpay_customer_top3`；
- `DELETE .../object-sets/{name}` → 软删成功；
- `order_item` 直接查询 `order_id=ORG1-ORD001906` → `total=3`，3 行（ORG1-ITM003735/003736/003737）。

说明：本轮没有新增对象集合管理 UI（仅 API + 智能体工具 + DDL），UI 入口留在后续迭代。
