# 投诉分析接口与持久化（新版）

本文定义各阶段接口的入参/出参、模型生成 vs 用户保存的逻辑，以及版本化落盘规则。

## 阶段、任务号与版本
- 阶段：原件预览（task=0）→ 原文/文本（task=1）→ 摘要 + 事项/诉求抽取（task=2，前后端统一归并）→ 归类分析（task=3）。
- 版本：每次模型生成或用户保存都创建新的 `v{max+1}`，旧版本保留。SSE 事件携带 `task`、`version`、`base_*_version` 方便前端展示“vN 基于 vM”。
- 目录结构（每个举报件 `base_id`）：
  ```
  complaints/<base_id>/
    original/
    images/
    texts/              # 原文版本 vN/
    summary/            # 摘要版本 vN/
    analysis/           # 事项/诉求版本 vN/
    classification/     # 归类版本 vN/
    manifest.json       # 当前指针
  ```
  `manifest.json` 示例：
  ```json
  { "text": { "current": 2 }, "summary": { "current": 3 }, "analysis": { "current": 1 }, "classification": { "current": 1 } }
  ```
  每个 `vN` 目录包含 `final.*` 与 `meta.json`，`meta` 记录 `stage`、`version`、`base_*_version`、`ts`、`source`（`auto`|`user`）。

## 接口定义

### 1) POST /api/complaint/text
- 作用：上传原件或提交用户编辑后的全文，生成新的“原文版本”，并触发 OCR（task0/task1）。
- 入参：
  - `file` (form-data, optional)：原件文件，触发上传+OCR。
  - `base_id` (string, optional)：复跑/增量。
  - `document_text` (string, optional, UTF-8)：用户直接提交的全文（新增/删/改）。
  - `force` (bool, optional)：跳过 OCR 缓存。
- 行为与持久化：
  - 有 `document_text`：不走模型，直接写 `texts/vN/final.txt` + `final_mapping.json`；`meta.source=user`。
  - 有 `file`：执行上传+OCR，写入同目录；`meta.source=auto`，SSE 推 task0/1。
  - 更新 `manifest.text.current`。
- 出参（JSON）：
  ```json
  { "base_id": "abc", "text_version": 2, "source": "user|auto", "task": 1 }
  ```

### 2) POST /api/complaint/{base_id}/summary
- 作用：基于原文生成摘要，或保存用户编辑的摘要，生成新的“摘要版本”。
- 入参：
  - `summary_text` (string, optional, UTF-8)：用户提供的摘要（新增/删/改）。
  - `base_document_version` (int, optional)：摘要基于的原文版本，缺省取最新。
  - 其余业务字段：如需强制重跑可加 `force` (bool)。
- 行为与持久化：
  - 有 `summary_text`：跳过模型，直接落盘 `summary/vN/final.(json|md|txt)`，`meta.source=user`，`meta.base_document_version` 记录来源。
  - 无 `summary_text`：用指定或最新原文调用模型生成摘要，落盘同上，`meta.source=auto`。
  - 更新 `manifest.summary.current`，SSE 推 task2，附 `version`、`base_document_version`、`source`。
- 出参（JSON）：
  ```json
  { "base_id": "abc", "summary_version": 3, "base_document_version": 2, "source": "user|auto", "task": 2, "content": "<摘要文本或结构>" }
  ```

### 3) POST /api/complaint/{base_id}/extraction
- 作用：生成或保存“事项/诉求”结构化结果，形成新的“抽取版本”（analysis）。
- 入参：
  - `items` (array<object>, optional)：用户编辑的事项列表。单项字段约定：
    - `complaint_desc` (string)：事项描述，必填。
    - `complaint_reference_list` (array<string>, optional)：证据/参考线索。
    - `ifViolation` / `ifNotViolation` (string|bool, optional)：模型/用户的合规性判断。
    - `checklist` (array<any>, optional)：补充检查点；保留透传。
    - 其余字段透传保存，便于回显。
  - `appeals` (array<object>, optional)：用户编辑的诉求列表。单项字段约定：
    - `appeal_desc` (string)：诉求描述，必填。
    - `appeal_reference_list` (array<string>, optional)：证据/参考线索。
    - `response` (string, optional)：模型/用户回复或处置建议。
    - 其余字段透传保存。
  - `input_text` (string, optional)：用指定文本作为模型输入（覆盖默认原文/摘要）。
  - `base_document_version` (int, optional) / `base_summary_version` (int, optional)：指明使用的原文或摘要版本。
  - 若无 `items`/`appeals`：模型生成，输入优先顺序 `input_text` → 摘要（若传版本）→ 最新原文。
- 行为与持久化：
  - 有 `items`/`appeals`：视为用户稿，保存时做缺省补全——如果只传 `items`（或只传 `appeals`），未提供的部分从上一版 `analysis/current` 取值后合并，再写 `analysis/vN/final.json`；`meta.source=user`。
  - 无结构体：调用模型生成结构化结果，写入同目录，`meta.source=auto`。
  - `meta.base_document_version`/`meta.base_summary_version` 记录依赖，更新 `manifest.analysis.current`，SSE 推 task2（与摘要同一 task，前端按版本区分）。
- 出参（JSON）：
  ```json
  { "base_id": "abc", "analysis_version": 4, "base_document_version": 2, "base_summary_version": 3, "source": "user|auto", "task": 2, "items": [...], "appeals": [...] }
  ```


### 4) POST /api/complaint/{base_id}/classification
- 作用：对事项/诉求做归类/分析，或保存用户编辑后的归类结果，生成新的“归类版本”（classification）。
- 入参：
  - `items` (array<object>, optional)：用于归类的事项列表。
  - `appeals` (array<object>, optional)：用于归类的诉求列表。
  - `story` (string, optional)：上下文/摘要文本。
  - `classification` (object, optional)：用户编辑后的归类结果（新增/删/改）。
  - `base_analysis_version` (int, optional)：归类基于的抽取版本，缺省最新。
- 行为与持久化：
  - 有 `classification`：视为用户稿，直接写 `classification/vN/final.json`，`meta.source=user`。
  - 否则调用模型基于传入的 `items`/`appeals`/`story` 或最新抽取版本生成归类结果，`meta.source=auto`。
  - `meta.base_analysis_version` 记录依赖，更新 `manifest.classification.current`，SSE 推 task3。
- 出参（JSON）：
  ```json
  { "base_id": "abc", "classification_version": 2, "base_analysis_version": 4, "source": "user|auto", "task": 3, "classification": { ... } }
  ```

## 统一说明
- “新增/删除/更新”均作为新版本写入 `v{max+1}`，不覆盖旧数据。
- 所有落盘的 `meta.json` 需写入：`stage`、`version`、`base_*_version`、`ts`、`source`（`auto`/`user`）、`by`（可选用户标识）。
- SSE 只消费对应阶段的 `task`，不跨阶段混用；如前端需要预览文件路径，task0 携带 `source_file_url`。
