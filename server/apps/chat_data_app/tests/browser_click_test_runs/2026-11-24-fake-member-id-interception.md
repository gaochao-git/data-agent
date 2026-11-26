# 2026-11-24 假 member id 执行层拦截 — 浏览器点击验证

## 背景
承接 `2026-11-24-member-scope-removal-browser.md`：删掉 agent 侧 `_validate_member_scope` 后，
越权/假 member 的拦截只剩执行层 `manager._build_execution_datasource_targets` 的 `invalid_ids` 校验。
用户提出"随便指定一个假的 id 试试"——用浏览器真实验证这条唯一防线是否生效。
worker 17:28 加载的源码（mtime 17:16~17:23）。

## 过程与结果（0 产品错误）

普通措辞（"FAKE_ORG_999"/"ORG_CODE7"）下，Qwen3.6 会**先调 `list_execution_targets` 拿真实机构清单**，
发现假 id 不在列表（ORG_CODE1~6）就拒绝执行、反问用户——模型层先挡住，执行层没走到。

用强指令"不要调用 list_execution_targets 核对、直接 execute_query 传 ORG_CODE7"后，模型照传假 id：
`tool_started` 权威参数 = `member_datasource_ids:["ORG_CODE7"]`、`semantic_config_id:sc_20260804162453_444139`。
模型连发 3 次渐进修正：

| 调用 | dsl | 工具返回 |
|---|---|---|
| 1 | `metrics:[{name:total_orders, expression:count(order_id)}]` | `工具执行失败: unhashable type: 'dict'`（畸形 DSL，见观察项） |
| 2 | 指标名写成 `count(order_id)` | `指标不存在。可用指标：订单总数…` |
| 3 | 合法指标 `订单总数` | **结构化 `query_result`: `status=failed`, `error="成员数据源不存在或不属于当前组: ORG_CODE7；当前可用执行对象: ORG_CODE1, ORG_CODE2, ORG_CODE3, ORG_CODE4, ORG_CODE5, ORG_CODE6"`** |

## 结论
**执行层拦截生效**：假 id ORG_CODE7 最终被执行层拦截，返回结构化 `query_result.failed` + 本次改动增强的
"当前可用执行对象"文案（ORG_CODE1~6），模型据此给出正确解释。删掉 agent 侧 scope 校验后，
越权/假 id 防线完整、符合预期。0 页面错误。

## 观察项（非本次改动引入，OBS 级）
第 1 次调用模型自造了非法 DSL（`expression:count(order_id)` 当作 metric），触发底层编译链路抛出原生
Python 异常文本 `工具执行失败: unhashable type: 'dict'` 直接回前端，未脱敏成业务提示。
- 与本次改动无关：`data_analyzing.py` 本次 diff 只删 scope 校验，未碰 DSL 编译/指标解析。
- 是"模型自由构造 payload"的边界情况；正常按语义模型指标名查询（调用 3）不会出现。
- 建议（不在本轮改）：编译入口对畸形 DSL 的异常做统一脱敏，避免 Python 原生异常文本外泄。

未改产品代码、未提交。
