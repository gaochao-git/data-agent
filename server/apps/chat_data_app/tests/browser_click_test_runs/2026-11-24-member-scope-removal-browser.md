# 2026-11-24 移除 agent 侧执行对象 scope 校验 — 浏览器点击验证

## 背景
工作区未提交改动：删除 `ExecutionContext`/`_ResolvedToolContext`/`SemanticConversationContext` 的
`available_member_datasource_ids`/`matched_member_datasource_ids` 字段，删 data_analyzing 的
`_validate_member_scope` 与 semantic 的 `match_execution_targets_from_query`，把权限裁决收敛到执行器边界
（`manager.authorize_execution_datasources_async` 按 user_id 过滤机构权限 + member 归属校验）。
用户重启后验证：删掉 agent 侧校验后，机构点名→模型选 member→执行层裁决→查询 的完整链路是否符合预期。

## 环境
- 源码 mtime 最晚 17:23（`semantic_conversation_context.py`），worker 17:28:15+ 启动 → 新代码已加载。
- 前后端 5001/8089 均 200；模型 `local/Qwen3.6-35B-A3B-4bit`；URL 带 `?parentComponentName=执法检查`。
- SSE 用 fetch+ReadableStream.tee hook；错误用 error/unhandledrejection/console.error hook。

## 场景与结果（0 产品错误）

| # | 场景 | 判据 | 结果 |
|---|---|---|---|
| 1 | 机构点名问数（核心） | "查询财付通机构 orders 订单表总记录数" → 工具链：`list_execution_targets`（拿机构清单）→ `execute_query` 参数含 `member_datasource_ids=["ORG_CODE1"]`（财付通）、`semantic_config_id=sc_20260804162453_444139`；返回 `success:true`/`source_type:hive`；最终答复"5000 条"，工作过程工具卡"财付通/TOTAL_COUNT/5000/成功/3.05秒/共1条" | ✅ 符合预期 |
| 2 | 历史会话回放 | 刷新后打开该会话 → 问答完整（5000 保留）、思考7·工具8、工作过程工具卡与结果从持久化数据完整重建，0 错误 | ✅ 符合预期 |

## 结论
删掉 agent 侧 matched/available scope 校验后，机构点名场景全链路正常：
模型先取可用执行对象清单→正确选定 member→执行层按权限裁决→返回正确数据。
未发现缺陷。与静态核对（权限裁决在 `authorize_execution_datasources_async` 按 user_id 过滤 + member 归属校验）
及定向单测（224+164=388 passed）结论一致。

## 边界（诚实）
- 越权 member 的执行层拦截（本次删校验后唯一防线）浏览器层造不出——需让模型故意传不在权限内的 member，
  由 `test_datasource_manager.py` / manager 的 invalid_ids 校验覆盖（报错本次还增强带出"当前可用执行对象"列表）。
- 机构点名一致性（用户点 A、模型传 B）的 agent 侧拦截随 matched 字段一并删除；本次模型正确选了 ORG_CODE1，
  但浏览器无法强制制造"模型选错机构"，该语义兜底的移除属有意设计（机构选择交给模型显式传参）。
- 定向验证非发布裁决，基线 144 条未逐条重跑。

未改产品代码、未提交。
