# complaint-analysis 接口时序

本文描述 `POST /api/complaint/complaint-analysis` 的完整处理时序，重点说明主流程分为哪些阶段，以及每个阶段对前端 SSE 和后端版本产物的影响。

配套时序图：

- Mermaid 源文件：`complaint-analysis接口时序图.mmd`
- SVG 渲染图：`complaint-analysis接口时序图.svg`

## 1. 请求接入与参数校验

接口先判断请求类型：如果是 `multipart/form-data`，从表单中解析 `user_id`、`file_id`、`force`、`use_uploaded_pdf`、`match_strategy`、`scope/org_id/entity_id`、`reported_entities` 等字段，并读取上传文件；如果是 JSON 请求，则直接解析 JSON 请求体。

随后服务端会做统一校验：`user_id` 必填，`scope/org_id/entity_id` 必须能解析成合法存储范围，`match_strategy` 必须是支持的策略；上传文件模式下必须提供 PDF/PNG/JPG/JPEG 文件，非上传模式下必须提供 `file_id` 或 `file_ids`，并校验文件扩展名。

如果文件类型不支持，接口不会进入真实分析流程，而是返回一个空的 `complaint_analysis` 结果，`issue_analysis_version=0`，并带上 `unsupported_file_type=true`。

本阶段一般还不会输出真实分析 SSE；只有文件类型不支持时，会走短路 SSE：

```text
status=started
message=开始处理举报件
progress=0

status=completed
message=仅支持 PDF/PNG/JPG/JPEG 文件
progress=100
result=空 complaint_analysis 结果
issue_analysis_version=0
unsupported_file_type=true
```

## 2. 基础上下文初始化

参数校验通过后，服务端会生成或解析本次举报件的 `base_id`：上传文件模式按 `file_id + 文件内容` 计算，普通模式按 `file_id/file_ids` 计算。然后解析当前写入 scope，规范化 `match_strategy`，记录请求日志，并发出第一条 SSE：

```text
task=complaint_analysis
status=started
message=开始处理举报件
progress=0
```

从这个阶段开始，后续所有 SSE 都会围绕同一个 `base_id` 返回。

## 3. 原文准备与文本抽取

服务端先检查 default scope 下是否已经有可复用的文本版本。如果已有 `texts/current`，直接复用已有文本，不重新准备原文件，也不重新 OCR。

如果没有可用文本，服务端进入原文准备：上传文件模式会先保存上传的 PDF/图片；普通模式会根据 `file_id/file_ids` 找到原始文件。原始文件准备好后，调用文本抽取流程生成文本内容，并保存为新的 `texts/vN` 版本。

这一阶段主要产物是 `text_content` 和 `text_version`，后续摘要和举报事项分析都会依赖它。

本阶段当前没有单独对前端输出 `processing` SSE；文本复用、原文准备和 OCR/文本抽取只记录后端日志与阶段耗时。该阶段的结果会体现在最终 `completed` SSE 的 `versions.text` 和 `result.versions.text_version` 中。

## 4. 摘要生成或复用

文本准备完成后，服务端检查当前 scope 下是否已有可复用摘要。如果已有 `summary/current`，直接读取 `summary_text` 和 `summary_version`。

如果没有可用摘要，接口会发出摘要阶段 SSE，然后基于抽取文本生成摘要，并保存为新的 `summary/vN`。如果请求中传了 `reported_entities`，摘要生成时也会带上这些被举报主体信息。

本阶段 SSE 输出：

```text
status=processing
message=正在生成摘要
progress=10
```

如果摘要命中缓存，则不会发这条 `processing` SSE；摘要版本会体现在最终 `completed` SSE 的 `versions.summary` 和 `result.versions.summary_version` 中。

## 5. 举报事项分析准备与缓存判断

摘要准备好后，接口进入 `issue_analysis` 阶段。服务端先构造 trace 上下文，包含 `base_id`、`user_id`、`file_id`、`match_strategy`、scope 信息和被举报主体信息。

如果 `force=false`，服务端会优先读取当前 scope 下最新的 `issue_analysis`。只要已有完成版结果，就直接返回 `cache_hit`，后续不再重新执行事项识别、合理性分析、经验匹配和法律分析。

如果缓存未命中，服务端会计算新的 `issue_analysis_version`，并尝试从最新结构化摘要中预取举报事项、诉求、举报人、举报日期、被举报机构、事件摘要等字段，作为后续分析节点的输入。

如果命中 `issue_analysis` 缓存，接口不会再输出后续阶段 SSE，而是直接进入最终完成事件：

```text
status=completed
message=生成完成
progress=100
cache_hit=true
result=已保存的 issue_analysis 结果
issue_analysis_version=已命中的版本号
```

如果未命中缓存，本阶段本身不输出独立 SSE；后续从识别举报事项阶段开始输出分析进度。

## 6. 分析节点执行

真实分析由 `run_complaint_analysis` 流式推进，当前主线可以理解为五个子阶段。

### 6.1 识别举报事项

节点为 `complaint_extract_items`。服务端优先使用摘要阶段已经结构化出的举报事项；如果当前 scope 的结构化摘要没有举报事项，则流程会停止，并返回无法继续分析的提示。

识别成功后，前端会收到约 `progress=30` 的 SSE，`phase_result` 中包含：

- `complaint_profile`：举报人、举报日期、被举报机构、标题基础信息；
- `items_and_appeals`：举报事项列表和诉求列表。

如果请求中传了 `reported_entities`，服务端还会补充按主体归属的阶段结果。

本阶段 SSE 输出示意：

```text
status=processing
message=系统正在阅读举报材料，提取举报事项、诉求和被举报主体信息。
progress=30
phase_node=complaint_extract_items

status=processing
message=系统已提取 N 个举报事项和 M 个诉求，下一步将逐项核对事实和法律适用性。
progress=30
phase_node=complaint_extract_items
phase_result.complaint_profile=举报人、举报日期、被举报机构等
phase_result.items_and_appeals=举报事项和诉求
issue_analysis_version=当前新版本号
```

如果存在 `reported_entities`，还可能额外输出：

```text
status=processing
message=系统已识别 N 个被举报机构或人员，并将举报事项和诉求按主体进行归属。
progress=35
phase_node=complaint_extract_items
phase_result.reported_entities=被举报主体列表
phase_result.reported_entity_groups=按主体归属的事项和诉求
```

### 6.2 合理性分析

节点为 `complaint_law_rationality`。服务端会逐项核对举报人提及的法律是否存在，以及该法律与举报事实是否相关。

每完成一个举报事项，会发出一条 `law_rationality_item` 类型的阶段事件，通常表现为 `progress=45`，用于让前端展示单项合理性分析的中间结果。

这一阶段结束后，结果会暂存在内存中，等整个 `issue_analysis` 完成时再保存为 `law_rationality/vN/final.json`。

本阶段 SSE 输出示意：

```text
status=processing
message=系统正在逐项核对举报事项的法律存在性和事实相关性。
progress=40
phase_node=complaint_law_rationality

status=processing
message=第 N 个举报事项合理性分析完成
progress=45
phase_node=complaint_law_rationality
phase_result=该举报事项的合理性分析结果
issue_analysis_version=当前新版本号
```

单项合理性结果不会立即出现在最终 `result` 的顶层字段中，但会先保存为 `law_rationality` 版本，并通过最终 `result.versions.law_rationality_version` 关联。

### 6.3 经验库或本地法规匹配

节点为 `complaint_match_experience`。具体行为由 `match_strategy` 决定：

- `two_stage`：先判断每个举报事项属于哪类业务场景，例如商户、扣款、账户或支付结算问题，对应 `progress=50`；再细化具体违规情形，并核对可引用的法律依据，对应 `progress=60`。
- `legacy`：直接按旧逻辑匹配专家经验并补强法律依据，通常对应 `progress=60`。
- `law_skill`：逐项调用本地法规库 skill，基于举报事项、用户提及法律和证据材料判断法律适用性，也对应本地法规分析阶段。

这个阶段完成后，`phase_result` 会返回精简后的 `matched_items`，用于展示每个举报事项匹配到了哪些分类、核查点和调阅材料。

`two_stage` 策略下，本阶段 SSE 输出示意：

```text
status=processing
message=系统正在判断每个举报事项属于哪类业务场景，例如商户、扣款、账户或支付结算问题。
progress=50
phase_node=complaint_match_experience_stage1

status=processing
message=系统正在细化具体违规情形，并核对可引用的法律依据。
progress=60
phase_node=complaint_match_experience_stage2

status=processing
message=系统已完成经验匹配，并补充可引用的法律依据。
progress=60
phase_node=complaint_match_experience
phase_result.matched_items=精简后的匹配结果
issue_analysis_version=当前新版本号
```

`legacy` 或 `law_skill` 策略下，通常不会拆成 50/60 两段，而是直接围绕 `progress=60` 输出匹配或本地法规分析进度。

### 6.4 法律条文分析

节点为 `complaint_law_analysis`。服务端根据上一步的匹配结果补充法律条文、法律依据原文、调阅清单、核实要点等信息。

如果某个事项没有匹配到专家经验或法律依据，该事项仍会保留在最终结果中，只是对应的法律条文、调阅清单或核实要点可能为空。

本阶段对前端的主要 SSE 通常合并到上一阶段完成事件中展示：

```text
status=processing
message=系统已完成经验匹配，并补充可引用的法律依据。
progress=60
phase_node=complaint_match_experience
phase_result.matched_items=匹配结果、核查点、调阅材料摘要
```

法律条文分析的完整产物不会以完整 `laws` 明细直接作为独立阶段 SSE 输出给前端，而是在最终结果中汇总为 `law_basis`、`document_checklist` 和 `verification_points`。

### 6.5 综合结论与结构化组装

节点为 `complaint_final_conclusion`。当前代码中这一阶段不再额外调用大模型生成长结论，而是基于前面各节点结果组装结构化输出。

接口会发出约 `progress=80` 的 SSE，表示已经完成基础事实、法律适用性和合理性结论的综合整理。

本阶段 SSE 输出：

```text
status=processing
message=系统已完成综合分析，形成基础事实、法律适用性和合理性结论。
progress=80
phase_node=complaint_final_conclusion
phase_result.conclusion_text=综合结论文本
issue_analysis_version=当前新版本号
```

当前实现中 `conclusion_text` 可能为空，因为最终结论主要通过结构化字段承载。

## 7. 结果保存与完成返回

分析节点全部结束后，服务端先保存合理性分析结果到当前 scope 下的 `law_rationality/vN/final.json`，并更新 `law_rationality.current`。

随后服务端组装最终 `complaint_analysis` 结果，主要包括：

- `api_kind`、`base_id`、`scope_context`；
- `incident` 和 `complaint_profile`；
- `items_and_appeals`；
- 可选的 `reported_entities` 和 `reported_entity_groups`；
- `law_basis`；
- `document_checklist`；
- `verification_points`；
- `conclusion_text`；
- `versions`，包含 `text_version`、`summary_version`、`classification_version`、`law_rationality_version` 等。

最终结果会保存到当前 scope 下的 `issue_analysis/vN/final.json`，并更新 `issue_analysis.current`。

保存完成后，接口发出最终 SSE：

```text
status=completed
message=生成完成
progress=100
result=最终 complaint_analysis 结果
issue_analysis_version=最终保存的 issue_analysis 版本号
cache_hit=false 或 true
match_strategy=本次匹配策略
scope_context=本次命中的 scope
versions.text=文本版本
versions.summary=摘要版本
versions.law_rationality=合理性分析版本
versions.issue_analysis=举报事项分析版本
```

最终事件会携带 `result`、`issue_analysis_version`、`cache_hit`、`match_strategy`、`scope_context` 和当前版本号。

最终 `result` 中各字段可以对应回前面的阶段：

- `complaint_profile`、`items_and_appeals`：来自识别举报事项阶段；
- `reported_entities`、`reported_entity_groups`：来自被举报主体归属阶段；
- `law_basis`、`document_checklist`、`verification_points`：来自经验匹配和法律条文分析阶段；
- `conclusion_text`：来自综合结论阶段；
- `versions.text_version`：来自文本抽取或文本复用阶段；
- `versions.summary_version`：来自摘要生成或摘要复用阶段；
- `versions.law_rationality_version`：来自合理性分析保存阶段；
- `issue_analysis_version` 和 `versions.issue_analysis`：来自最终 `issue_analysis` 保存阶段。

## 8. 异常返回

如果某个阶段抛出 `HTTPException`，接口会返回 `status=error`，`message` 为异常详情，并记录当前 `active_phase` 的失败耗时。

如果是未预期异常，接口同样返回 `status=error`，`message` 形如 `处理失败: ...`，并记录错误日志。

异常 SSE 输出示意：

```text
status=error
message=异常详情或处理失败: ...
progress=0
error=异常详情
match_strategy=本次匹配策略
```

无论成功还是失败，接口最后都会记录整条 `POST /api/complaint/complaint-analysis` 路由的总耗时。
