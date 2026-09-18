
# 看板生成

## 适用目标

把用户的“生成看板 / 创建看板草案 / 预览看板 / 沉淀分析面板”需求转成可预览的 `dashboard_spec`。

`dashboard_spec` 是看板规格，不是 SQL 查询结果报表。它只描述卡片、布局、图表类型和语义 DSL；不保存 SQL 执行后的数值、明细行或图表数据。

系统看板写入只能由用户在前端预览区点击“写入看板”触发。模型只生成已校验的预览文件，不自动写入业务系统。

## 产物契约

看板预览文件必须写入 outputs，文件名使用 `.dashboard.json` 后缀。每次新建看板必须使用独立文件名，避免覆盖同一会话里的历史看板预览；推荐格式：

```text
outputs/<业务主题>-<短时间戳或短随机后缀>.dashboard.json
```

工作草稿：

```text
workspace/dashboard_draft.json
```

写入 outputs 后必须调用 `present_artifacts(files=["/mnt/user-data/outputs/<实际文件名>.dashboard.json"])` 显式呈现给用户；`present_artifacts` 只返回轻量文件元信息，前端会按文件路径加载对应看板规格并渲染预览。

## 必读资料

进入看板生成路径后执行：

1. 读取主指南：
   - `/mnt/skills/data-analyzing/references/dashboard/guide.md`
2. 读取查询与分析规范：语义模型检索与字段口径确认以 `/mnt/skills/data-analyzing/references/analysis/query.md` 为唯一依据，本文档不重复定义这些规则
3. 用户需求宽泛或需要模板时，再读取一个示例：
   - 综合 / 默认分析：`/mnt/skills/data-analyzing/assets/dashboard/examples/comprehensive-dashboard.json`
   - 风险 / 异常 / 预警：`/mnt/skills/data-analyzing/assets/dashboard/examples/risk-monitoring-dashboard.json`
   - 机构 / 区域 / 产品 / 客户画像：`/mnt/skills/data-analyzing/assets/dashboard/examples/profile-dashboard.json`

## 工具流程

按下面顺序完成，不把工具参数、JSON、DSL、SQL 或工具返回原文写进用户正文。
每次调用工具前先输出一句短句说明当前要做什么；工具返回后用一句短句概括发现，再继续下一步。

1. `file_read("/mnt/skills/data-analyzing/references/dashboard/guide.md")`
2. `file_read("/mnt/skills/data-analyzing/references/analysis/query.md")`：语义模型检索与字段口径确认以该文档为唯一依据
3. 按需 `file_read` 一个示例 JSON
4. 按 `references/analysis/query.md` 检索语义模型并精读候选 YAML，为每张卡片确定一个语义模型、确认准备使用的指标和维度定义；无法唯一判断时先澄清该卡片。系统提示词中的手动选择仅是优先参考，不包含完整字段口径，也不锁定语义模型
5. 如枚举、状态、类型、时间或金额等字段存储口径不明确，先构造明细抽样并查询 3 条原始数据确认结构
6. 内部构建完整 `dashboard_spec`，每张卡片必须包含 `semantic_config_id`；旧规格才使用顶层默认字段
7. **一次批量校验整份规格**：调用 `data_analyzing_tool(action="validate_dashboard", action_payload={"dsl": dashboard_spec})`，其中 `dsl` 必须是包含 `dashboard_name` 和完整 `cards` 数组的整个 `dashboard_spec`。
8. 工具返回 `mode=batch`、`valid_count`、逐卡 `panels` 和 `warnings`；不要为每张卡片单独调用校验。如果有卡片失败，修正完整 `dashboard_spec` 后再次整体提交，不能拆卡片重试。
9. 校验通过后用 `file_write(file_path="workspace/dashboard_draft.json", content=dashboard_spec)` 写入草稿
10. 校验完成后用 `file_move(source_path="/mnt/user-data/workspace/dashboard_draft.json", destination_path="/mnt/user-data/outputs/<业务主题>-<唯一后缀>.dashboard.json")` 将草稿移入最终产物区；不要再次用 `file_write` 重写相同内容
11. 只有用户明确要求修改某个已有看板预览文件时，才先 `file_read` 该 outputs 文件并用 `file_edit(file_path, old_content, new_content)` 精确替换一段完整且唯一的原文；需要整体覆盖时改用 `file_write`
12. 调用 `present_artifacts(files=["/mnt/user-data/outputs/<实际文件名>.dashboard.json"])` 展示最终看板预览
13. 简短总结预览结果

新建看板预览时不要复用或覆盖已有 outputs 看板文件；同一会话中多次生成看板，应保留多个独立的 `.dashboard.json` 产物。修改已有预览文件前必须 `file_read` 当前文件内容，修改后必要时重新调用 `validate_dashboard` 校验。

`execute_query` 只用于接收抽样 DSL，并在内部生成 SQL 后确认是否有数据，或确认枚举、状态、类型、时间、金额等字段存储口径；执行结果不能写入 `dashboard_spec`。

## 设计规则

具体规格、布局、图表选择、DSL 规则和失败处理以 `references/dashboard/guide.md` 为准。这里保留必须遵守的底线：

- 每张卡片的指标、维度、过滤、排序字段必须来自该卡片 `semantic_config_id` 对应的语义模型。
- 语义模型检索、候选定位与字段口径确认全部按 `references/analysis/query.md` 执行，本文档不重复定义；不能凭模型名称、文件名或字段相似性猜测，检索无命中即停止并澄清。主模型需为每张卡片选择一个真实 ID 并确认使用的字段，无法唯一判断时先向用户澄清，不得生成该卡片 DSL。
- 每张卡片的 `semantic_config_id` 必填，取值必须是按 `references/analysis/query.md` 检索到的、当前目录中实际存在的语义模型 ID；顶层同名字段仅兼容旧规格默认值。
- 同一张卡片不得混用多个语义模型；不同卡片可以使用不同语义模型，但仍使用当前会话绑定的同一个 `datasource_id`。
- 默认生成最小可用看板，优先覆盖总览、趋势、结构 / 对比、排行 / 诊断等核心视角。
- 每张卡片必须包含 `title`、`chart_type`、`layout`、`dsl`。
- 卡片中不包含 `sql`、`data`、`rows`、`result`、`x_field`、`y_field`，不使用 `type` 代替 `chart_type`。
- 生成或整体覆盖 `dashboard_spec` 时必须使用多行缩进 JSON 和 `file_write`，不要压缩成一行。
- 修改已有 `dashboard_spec` 的局部内容前必须先用 `file_read` 读取目标文件，再用 `file_edit(file_path, old_content, new_content)` 精确替换；找不到或匹配多处时重新读取，不能凭记忆重试。
- 最终文件必须是看板规格文件，不是查询结果文件。
- 写入或更新 outputs 看板文件后，必须调用 `present_artifacts(files=["/mnt/user-data/outputs/<实际文件名>.dashboard.json"])`，否则聊天框不会展示看板预览。

## 完成判定

同时满足以下条件，才算看板预览已生成：

1. 已读取主指南。
2. 已构建包含 `dashboard_name` 和非空 `cards` 的 `dashboard_spec`。
3. 已将完整 `dashboard_spec` 一次传给 `validate_dashboard` 批量校验，并根据逐卡 `semantic_config_id`、`panels` / `warnings` 修正失败卡片。
4. 已生成或更新一个 `outputs/*.dashboard.json` 文件，且可被识别为 dashboard_spec。
5. 已调用 `present_artifacts` 显式展示最终 outputs 看板。
6. 最终文件符合“设计规则”的字段底线。

只写入 `workspace/dashboard_draft.json` 不算完成；只执行 SQL 或总结数据也不算完成。

## 用户输出

过程说明保持一句短句，只说明当前动作或发现，不展开指标、维度、SQL、卡片清单或工具结果明细。最终回答只包含：

- 看板预览已生成或失败原因
- 实际生成或更新的 `outputs/<实际文件名>.dashboard.json`
- 卡片数量和默认口径
- 提醒用户在右侧预览区点击“写入看板”

不要在正文展示完整看板 JSON、DSL、SQL、工具参数或原始查询结果。
