# zhidu_clause_extract

用于批量扫描制度 PDF 目录，抽取制度正文并按 `章/条/款` 切分，供后续制度冲突分析直接使用。`document_catlog.json` 可以作为补充分类信息和对账依据，但不再是唯一数据源。

## 当前脚本清单

当前目录下与“制度抽取 -> 主数据映射 -> 有效规则 -> 冲突分析”直接相关的脚本如下：

1. `extract_pdf_clauses.py`
作用：
从 PDF 提取正文全文，并进一步切成条款。
主要输出：
`documents.jsonl`、`clauses.jsonl`、`raw_texts/*.txt`、`failed_documents.jsonl`

2. `generate_inter_category_pairs.py`
作用：
从 `outline.xlsx` 提取制度目录元数据，生成：
`sheet1_rows.json`、`sheet2_rows.json`、`merged_outline.json`
并进一步生成：
`inter_group_pairs.json`

3. `outline_data_store.py`
作用：
统一读取 `merged_outline.json`，并缓存到内存中，供其他脚本复用。

4. `load_merged_outline.py`
作用：
检查 `merged_outline.json` 是否已正确加载到内存，并输出加载摘要。

5. `map_documents_to_outline.py`
作用：
将 `documents.jsonl` 中每一条制度，与整个 `merged_outline.json` 一起发给大模型判断映射关系。
主要输出：
`document_outline_mapping.jsonl`、`document_outline_mapping.xlsx`、`documents_mapped.jsonl`

6. `extract_effective_rules.py`
作用：
从制度正文中筛选真正有约束意义的“有效规则”。
输入：
`documents_mapped.jsonl` + `raw_texts/*.txt`
主要输出：
`effective_rules.jsonl`、`effective_rules.xlsx`

7. `extract_ontology_jsonl.py`
作用：
从制度正文中抽取本体实例，并输出三层 JSONL：
- `ontology_instances.jsonl`
- `ontology_entities.jsonl`
- `ontology_relations.jsonl`
同时输出：
- `ontology_normalization.jsonl`

8. `extract_clause_semantics_mentions.py`
作用：
从 `clauses.jsonl` 中按条款抽取“原始提及”，包括：
- `subject_mentions`
- `action_mentions`
- `object_mentions`
- `constraint_mentions`

9. `normalize_clause_semantics_mentions.py`
作用：
对 `clause_semantics_mentions.jsonl` 先做制度内归一化，再做跨制度归一化，输出：
- `clause_semantics_normalization.jsonl`

10. `screen_clause_semantics_concepts.py`
作用：
对 `clause_semantics_normalization.jsonl` 做概念筛选，输出：
- `clause_semantics_concepts.jsonl`
- `clause_semantics_concepts_review_noise.jsonl`

11. `enrich_clause_semantics_terms.py`
作用：
对 `clause_semantics_normalization.jsonl` + `clause_semantics_concepts.jsonl` 做术语增强，输出：
- `clause_semantics_terms.jsonl`
- `clause_semantics_terms_summary.json`

12. `extract_clause_rule_expressions.py`
作用：
基于原始提及、归并结果和概念筛选结果，按条款生成规制表达，输出：
- `clause_rule_expressions.jsonl`

13. `build_clause_semantics_ontology_ttl.py`
作用：
基于制度元数据、概念归并、概念筛选、术语增强和规制表达，按条款调用大模型生成可落库的 `OWL/Turtle`，输出：
- `clause_semantics_ontology.ttl`
- `clause_semantics_ontology.graphdb_ready.ttl`
- `clause_semantics_ontology_schema.ttl`
- `ontology_query_examples.sparql`
- `clause_semantics_ontology_summary.json`
- `shacl_validation_report.json`
- `shacl_validation_report.txt`
- `fragments/*.ttl`

14. `validate_clause_semantics_ttl.py`
作用：
基于 `clause_semantics_shapes.ttl` 校验 `OWL/Turtle` 的必填关系和类型约束，输出：
- `shacl_validation_report.json`
- `shacl_validation_report.txt`

15. `export_clause_semantics_mentions_review.py`
作用：
把 `clause_semantics_mentions.jsonl` 导出成便于人工 review 的 Excel。

16. `detect_clause_conflicts.py`
作用：
基于 `effective_rules.jsonl` 做制度冲突分析，包括：
- 组内制度对初筛
- 组间制度对初筛
- 高风险制度对的条款级冲突定位

17. `judge_ontology_conflicts.py`
作用：
基于图谱候选查询（SPARQL）调用大模型做冲突裁决，输出：
- `ontology_conflict_candidates.jsonl`
- `ontology_conflict_judgments.jsonl`
- `ontology_conflict_judgments_summary.json`

## 推荐执行顺序

如果按当前这套链路执行，推荐顺序如下：

1. `extract_pdf_clauses.py`
得到：
`documents.jsonl`、`clauses.jsonl`、`raw_texts/*.txt`

2. `generate_inter_category_pairs.py`
得到：
`merged_outline.json`、`inter_group_pairs.json`

3. `map_documents_to_outline.py`
得到：
`document_outline_mapping.jsonl`、`documents_mapped.jsonl`

4. `extract_effective_rules.py`
得到：
`effective_rules.jsonl`

5. `extract_ontology_jsonl.py`
得到：
`ontology_instances.jsonl`、`ontology_entities.jsonl`、`ontology_relations.jsonl`、`ontology_normalization.jsonl`

6. `extract_clause_semantics_mentions.py`
得到：
`clause_semantics_mentions.jsonl`

7. `normalize_clause_semantics_mentions.py`
得到：
`clause_semantics_normalization.jsonl`

8. `screen_clause_semantics_concepts.py`
得到：
`clause_semantics_concepts.jsonl`、`clause_semantics_concepts_review_noise.jsonl`

9. `enrich_clause_semantics_terms.py`
得到：
`clause_semantics_terms.jsonl`

10. `extract_clause_rule_expressions.py`
得到：
`clause_rule_expressions.jsonl`

11. `build_clause_semantics_ontology_ttl.py`
得到：
`clause_semantics_ontology.ttl`、`clause_semantics_ontology.graphdb_ready.ttl`、`clause_semantics_ontology_schema.ttl`、`ontology_query_examples.sparql`、`clause_semantics_ontology_summary.json`、`shacl_validation_report.json`、`shacl_validation_report.txt`、`fragments/*.ttl`

12. `validate_clause_semantics_ttl.py`
得到：
`shacl_validation_report.json`、`shacl_validation_report.txt`

13. `export_clause_semantics_mentions_review.py`
得到：
`clause_semantics_mentions_review.xlsx`

14. `detect_clause_conflicts.py`
得到：
制度对冲突分析结果与条款级冲突定位结果

15. `judge_ontology_conflicts.py`
得到：
`ontology_conflict_candidates.jsonl`、`ontology_conflict_judgments.jsonl`、`ontology_conflict_judgments_summary.json`

## 01-08 输出说明（可直接复述）

- `01_extract`：从 PDF 抽出基础数据，产出 `documents.jsonl`、`clauses.jsonl`、`raw_texts/*.txt`，这是后续所有步骤的原料层。
- `02_outline_mapping`：把制度目录元数据（版块、领域、文号、主责部门等）映射回制度文档，核心产出 `documents_mapped.jsonl`。
- `03_ontology`：旧方案的本体 JSON 抽取实验层（`ontology_instances/entities/relations`），当前主线流程可以不依赖它。
- `04_clause_semantics_mentions`：按条款抽取原始语义提及（主体、动作、对象、约束），不做归并。
- `05_clause_semantics_normalization`：对 `04` 的主体、动作、对象、约束提及做同义/近义归并（先制度内，再跨制度），得到规范名称分组。
- `06_clause_semantics_concepts`：对归并结果做概念筛选，区分 `core/context/noise`，为后续规制表达和本体映射去噪。
- `06b_clause_semantics_terms`：对已归并概念补充术语定义、上位词、下位词、相关词，作为术语语义增强产物，不改变主线七层。
- `07_clause_rule_expressions`：把条款转换成稳定规制表达（`subject/actions/object/condition/constraint/effect/evidence_text`），其中 `object` 优先参考 `04` 的 `object_mentions`。
- `08_clause_semantics_ontology`：把 `07` 映射成 OWL/TTL 和 SPARQL 示例，导入图数据库后可检索、可比对、可做冲突候选召回。
- `08_clause_semantics_ontology/shacl_validation_report.*`：对 TTL 做 SHACL 结构校验，检查缺失关系和脏连边。

当前主线建议：
`01 -> 02 -> 04 -> 05 -> 06 -> 07 -> 08`
（`03` 作为历史实验或对照层，`06b` 作为术语增强补充产物）

## 最新全量批次公共变量（zhidu_0430）

执行前先修改 `PDF_DIR` 和 `OUTLINE_XLSX` 为真实路径，下面“直接大模型条款对比链路”和“本体/GraphDB 全量链路”都复用这组变量。

```bash
cd /Users/fengyingsheng/workspaces/personal/fxcjg/fxcjg/server

export BATCH=zhidu_0430
export OUT=/data/aigc/${BATCH}/output
export PDF_DIR=/data/aigc/${BATCH}/pdfs
export OUTLINE_XLSX=/data/aigc/${BATCH}/outline.xlsx
export MODEL=GLM-4.7-W8A8
export GRAPHDB_REPOSITORY=http://172.27.60.34:7200/repositories/zhidu
export GRAPHDB_STATEMENTS=${GRAPHDB_REPOSITORY}/statements

mkdir -p ${OUT}
```

## 直接大模型条款对比链路（zhidu_original）

已拆到独立文档：[README_DIRECT_LLM_CONFLICT.md](README_DIRECT_LLM_CONFLICT.md)。

## 本体/GraphDB 全量链路（zhidu_0430）

适用场景：
业务侧提供最新最全制度 PDF 与 `outline.xlsx` 后，按一个新批次完整重跑 `01 -> 02 -> 04 -> 05 -> 06 -> 06b -> 07 -> 08 -> GraphDB -> 09`。

1. PDF 抽取与条款切分：

```bash
python apps/complaint_app/tools/zhidu_clause_extract/extract_pdf_clauses.py \
  --pdf-dir ${PDF_DIR} \
  --input-mode scan \
  --output-dir ${OUT}/01_extract \
  --llm-model ${MODEL}
```

2. 生成目录元数据与组间关系初稿：

```bash
python apps/complaint_app/tools/zhidu_clause_extract/generate_inter_category_pairs.py \
  --outline-xlsx ${OUTLINE_XLSX} \
  --output-json ${OUT}/02_outline_mapping/inter_group_pairs.json \
  --model ${MODEL}
```

3. documents 映射 outline：

```bash
python apps/complaint_app/tools/zhidu_clause_extract/map_documents_to_outline.py \
  --documents-jsonl ${OUT}/01_extract/documents.jsonl \
  --merged-outline-json ${OUT}/02_outline_mapping/merged_outline.json \
  --output-dir ${OUT}/02_outline_mapping \
  --model ${MODEL}
```

4. 原始提及抽取：

```bash
python apps/complaint_app/tools/zhidu_clause_extract/extract_clause_semantics_mentions.py \
  --clauses-jsonl ${OUT}/01_extract/clauses.jsonl \
  --output-dir ${OUT}/04_clause_semantics_mentions \
  --model ${MODEL}
```

5. 归并：

```bash
python apps/complaint_app/tools/zhidu_clause_extract/normalize_clause_semantics_mentions.py \
  --mentions-jsonl ${OUT}/04_clause_semantics_mentions/clause_semantics_mentions.jsonl \
  --output-dir ${OUT}/05_clause_semantics_normalization \
  --max-doc-mentions-per-batch 120 \
  --model ${MODEL}
```

6. 概念筛选：

```bash
python apps/complaint_app/tools/zhidu_clause_extract/screen_clause_semantics_concepts.py \
  --normalization-jsonl ${OUT}/05_clause_semantics_normalization/clause_semantics_normalization.jsonl \
  --output-dir ${OUT}/06_clause_semantics_concepts \
  --model ${MODEL}
```

7. 术语增强：

```bash
python apps/complaint_app/tools/zhidu_clause_extract/enrich_clause_semantics_terms.py \
  --normalization-jsonl ${OUT}/05_clause_semantics_normalization/clause_semantics_normalization.jsonl \
  --concepts-jsonl ${OUT}/06_clause_semantics_concepts/clause_semantics_concepts.jsonl \
  --output-dir ${OUT}/06b_clause_semantics_terms \
  --model ${MODEL}
```

8. 规制表达：

```bash
python apps/complaint_app/tools/zhidu_clause_extract/extract_clause_rule_expressions.py \
  --mentions-jsonl ${OUT}/04_clause_semantics_mentions/clause_semantics_mentions.jsonl \
  --normalization-jsonl ${OUT}/05_clause_semantics_normalization/clause_semantics_normalization.jsonl \
  --concepts-jsonl ${OUT}/06_clause_semantics_concepts/clause_semantics_concepts.jsonl \
  --output-dir ${OUT}/07_clause_rule_expressions \
  --model ${MODEL}
```

9. 生成 TTL / GraphDB 专用 TTL：

```bash
python apps/complaint_app/tools/zhidu_clause_extract/build_clause_semantics_ontology_ttl.py \
  --documents-jsonl ${OUT}/02_outline_mapping/documents_mapped.jsonl \
  --normalization-jsonl ${OUT}/05_clause_semantics_normalization/clause_semantics_normalization.jsonl \
  --concepts-jsonl ${OUT}/06_clause_semantics_concepts/clause_semantics_concepts.jsonl \
  --terms-jsonl ${OUT}/06b_clause_semantics_terms/clause_semantics_terms.jsonl \
  --rule-expressions-jsonl ${OUT}/07_clause_rule_expressions/clause_rule_expressions.jsonl \
  --output-dir ${OUT}/08_clause_semantics_ontology \
  --model ${MODEL}
```

10. 导入 GraphDB：

```bash
curl -X POST \
  -H "Content-Type: text/turtle" \
  --data-binary @${OUT}/08_clause_semantics_ontology/clause_semantics_ontology.graphdb_ready.ttl \
  "${GRAPHDB_STATEMENTS}"
```

说明：
- GraphDB 导入使用 `clause_semantics_ontology.graphdb_ready.ttl`，不要直接导原始 `clause_semantics_ontology.ttl`。
- 如需避免旧数据混在同一 repository 中，先在 GraphDB UI 清空或新建 repository 后再导入。

11. 冲突候选裁决：

```bash
python apps/complaint_app/tools/zhidu_clause_extract/judge_ontology_conflicts.py \
  --sparql-endpoint ${GRAPHDB_REPOSITORY} \
  --sparql-file apps/complaint_app/tools/zhidu_clause_extract/examples/conflict_candidates.sparql \
  --output-dir ${OUT}/09_ontology_conflict_judgments \
  --model ${MODEL}
```

如果 `01` 和 `02` 已经跑完并检查通过，可以用下面一条命令合并执行 `04 -> 09`：

```bash
python apps/complaint_app/tools/zhidu_clause_extract/run_clause_semantics_pipeline.py \
  --output-root ${OUT} \
  --model ${MODEL} \
  --graphdb-repository ${GRAPHDB_REPOSITORY} \
  --start-step 04 \
  --end-step 09
```

常用变体：

```bash
# 只打印将要执行的命令，不真正执行
python apps/complaint_app/tools/zhidu_clause_extract/run_clause_semantics_pipeline.py \
  --output-root ${OUT} \
  --model ${MODEL} \
  --graphdb-repository ${GRAPHDB_REPOSITORY} \
  --start-step 04 \
  --end-step 09 \
  --dry-run

# 只从规制表达开始跑到 TTL
python apps/complaint_app/tools/zhidu_clause_extract/run_clause_semantics_pipeline.py \
  --output-root ${OUT} \
  --model ${MODEL} \
  --start-step 07 \
  --end-step 08

# TTL 已生成时，只导入 GraphDB 并做冲突裁决
python apps/complaint_app/tools/zhidu_clause_extract/run_clause_semantics_pipeline.py \
  --output-root ${OUT} \
  --model ${MODEL} \
  --graphdb-repository ${GRAPHDB_REPOSITORY} \
  --start-step graphdb \
  --end-step 09
```

## 整体总结（可对外说明）

这条链路做了三件事：

1. 把制度 PDF 先变成结构化条款数据，并补齐制度目录元数据。
2. 把条款语义逐层加工成可计算规则表达（原始提及 -> 归并 -> 概念筛选 -> 规制表达）。
3. 把规则表达映射成 OWL/TTL 并落到图数据库，实现 SPARQL 查询和后续冲突分析。

例子（便于解释）：
条款“董事会就解聘会计师事务所进行表决时，应当允许会计师事务所陈述意见”会被结构化为：
`subject=董事会`、`action=允许陈述意见`、`object=会计师事务所`、`condition=解聘表决时`、`constraint=应当`，并保留 `evidence_text` 回链原文证据。

当前还没有正式接入总流程的“制度摘要”脚本；如后续新增，推荐放在：
`documents_mapped.jsonl` 之后、`extract_effective_rules.py` 之前。

## 条款语义三层方案

条款语义理解的固定方案已独立整理到：
[CLAUSE_SEMANTICS_PLAN.md](/Users/fengyingsheng/workspaces/personal/fxcjg/fxcjg/server/apps/complaint_app/tools/zhidu_clause_extract/CLAUSE_SEMANTICS_PLAN.md)

条款语义实验中的阶段性观察、人工复核口径与经验记录整理到：
[CLAUSE_SEMANTICS_EXPERIENCE.md](/Users/fengyingsheng/workspaces/personal/fxcjg/fxcjg/server/apps/complaint_app/tools/zhidu_clause_extract/CLAUSE_SEMANTICS_EXPERIENCE.md)

冲突候选查询样例（SPARQL）：
[examples/conflict_candidates.sparql](/Users/fengyingsheng/workspaces/personal/fxcjg/fxcjg/server/apps/complaint_app/tools/zhidu_clause_extract/examples/conflict_candidates.sparql)

说明：
- 当前样例包含 `Query1-Query9`，覆盖约束差异、条件差异、义务/禁止对冲、组内/组间候选、时间金额阈值、适用范围、一般/特别条款、上位/下位豁免候选。
- `judge_ontology_conflicts.py` 二次裁决输出新增 `conflict_dimension` 字段，用于标记冲突维度。

当前状态：

- 原始提及层：已实现
- 归并层：已实现
- 概念筛选层：已实现
- 规制表达层：已实现
- 本体映射与查询层：已实现到 `TTL/SPARQL` 产出，导入外部图数据库需单独执行

原始提及归并命令：

```bash
cd /Users/fengyingsheng/workspaces/personal/fxcjg/fxcjg/server
python apps/complaint_app/tools/zhidu_clause_extract/normalize_clause_semantics_mentions.py \
  --mentions-jsonl /data/aigc/zhidu_0422/output/04_clause_semantics_mentions/clause_semantics_mentions.jsonl \
  --output-dir /data/aigc/zhidu_0422/output/05_clause_semantics_normalization \
  --concurrency 2 \
  --max-doc-mentions-per-batch 120 \
  --max-global-groups-per-batch 80 \
  --model glm-5
```

说明：
- `05` 默认支持续跑，中间结果保存在 `output-dir/checkpoints/`。
- 制度内归一化按 `doc_id + mention_category` 保存，跨制度归一化按 `mention_category` 保存。
- 失败后直接重跑同一命令即可复用已完成块；如需完全重跑，可加 `--no-resume` 或删除 `checkpoints/`。

概念筛选命令：

```bash
cd /Users/fengyingsheng/workspaces/personal/fxcjg/fxcjg/server
python apps/complaint_app/tools/zhidu_clause_extract/screen_clause_semantics_concepts.py \
  --normalization-jsonl /data/aigc/zhidu_0407/output/05_clause_semantics_normalization/clause_semantics_normalization.jsonl \
  --output-dir /data/aigc/zhidu_0407/output/06_clause_semantics_concepts \
  --model glm-5
```

术语增强命令：

```bash
cd /Users/fengyingsheng/workspaces/personal/fxcjg/fxcjg/server
python apps/complaint_app/tools/zhidu_clause_extract/enrich_clause_semantics_terms.py \
  --normalization-jsonl /data/aigc/zhidu_0407/output/05_clause_semantics_normalization/clause_semantics_normalization.jsonl \
  --concepts-jsonl /data/aigc/zhidu_0407/output/06_clause_semantics_concepts/clause_semantics_concepts.jsonl \
  --output-dir /data/aigc/zhidu_0407/output/06b_clause_semantics_terms \
  --model glm-5
```

规制表达生成命令：

```bash
cd /Users/fengyingsheng/workspaces/personal/fxcjg/fxcjg/server
python apps/complaint_app/tools/zhidu_clause_extract/extract_clause_rule_expressions.py \
  --mentions-jsonl /data/aigc/zhidu_0407/output/04_clause_semantics_mentions/clause_semantics_mentions.jsonl \
  --normalization-jsonl /data/aigc/zhidu_0407/output/05_clause_semantics_normalization/clause_semantics_normalization.jsonl \
  --concepts-jsonl /data/aigc/zhidu_0407/output/06_clause_semantics_concepts/clause_semantics_concepts.jsonl \
  --output-dir /data/aigc/zhidu_0407/output/07_clause_rule_expressions \
  --model glm-5
```

如需检查 `07_clause_rule_expressions` 的“空规则”和“缺主语/动作”占比，可执行：

```bash
cd /Users/fengyingsheng/workspaces/personal/fxcjg/fxcjg/server
python apps/complaint_app/tools/zhidu_clause_extract/check_clause_rule_expressions_quality.py \
  --input-jsonl /data/aigc/zhidu_0407/output/07_clause_rule_expressions/clause_rule_expressions.jsonl
```

该脚本会输出：

- 总规则数
- 空规则数与占比
- 缺 `subject` 规则数与占比
- 缺 `actions` 规则数与占比
- `subject/actions` 同时缺失规则数与占比
- 整条款都无 `subject/actions` 的条款数

OWL/Turtle 生成命令：

```bash
cd /Users/fengyingsheng/workspaces/personal/fxcjg/fxcjg/server
python apps/complaint_app/tools/zhidu_clause_extract/build_clause_semantics_ontology_ttl.py \
  --documents-jsonl /data/aigc/zhidu_0407/output/02_outline_mapping/documents_mapped.jsonl \
  --normalization-jsonl /data/aigc/zhidu_0407/output/05_clause_semantics_normalization/clause_semantics_normalization.jsonl \
  --concepts-jsonl /data/aigc/zhidu_0407/output/06_clause_semantics_concepts/clause_semantics_concepts.jsonl \
  --terms-jsonl /data/aigc/zhidu_0407/output/06b_clause_semantics_terms/clause_semantics_terms.jsonl \
  --rule-expressions-jsonl /data/aigc/zhidu_0407/output/07_clause_rule_expressions/clause_rule_expressions.jsonl \
  --output-dir /data/aigc/zhidu_0407/output/08_clause_semantics_ontology \
  --model glm-5
```

说明：
- 默认会在 TTL 生成后自动执行 SHACL 结构校验，并输出 `shacl_validation_report.json`、`shacl_validation_report.txt`
- 如需跳过校验，可加 `--skip-shacl-validation`
- 如需校验失败时让命令返回失败，可加 `--fail-on-shacl-violation`
- SHACL 规则定义在 `apps/complaint_app/tools/zhidu_clause_extract/clause_semantics_shapes.ttl`

单独重跑 SHACL 结构校验命令：

```bash
cd /Users/fengyingsheng/workspaces/personal/fxcjg/fxcjg/server
python apps/complaint_app/tools/zhidu_clause_extract/validate_clause_semantics_ttl.py \
  --ttl /data/aigc/zhidu_0407/output/08_clause_semantics_ontology/clause_semantics_ontology.ttl \
  --output-dir /data/aigc/zhidu_0407/output/08_clause_semantics_ontology
```

单独校验用于排查既有 TTL；发现违规时默认返回非 0，如只想生成报告不中断命令，可加 `--no-fail-on-violation`。

GraphDB 导入（HTTP）：

```bash
curl -X POST \
  -H "Content-Type: text/turtle" \
  --data-binary @/data/aigc/zhidu_0407/output/08_clause_semantics_ontology/clause_semantics_ontology.graphdb_ready.ttl \
  "http://<graphdb-ip>:7200/repositories/zhidu/statements"
```

GraphDB 常见报错处理：

- 报错：`MALFORMED DATA: Unable to resolve URIs, no base URI has been set [line ...]`
- 原因：TTL 中存在相对 IRI（如 `<document_xxx>`），GraphDB 导入时需要明确 base URI。
- 现在 `build_clause_semantics_ontology_ttl.py` 会自动生成 `clause_semantics_ontology.graphdb_ready.ttl`，GraphDB 直接导这个文件即可。
- 注意：不要把“正则批量替换 `<...>` 为 `ex:...`”作为长期方案，这只适合临时救火，存在误替换风险。
- 长期方案：在 TTL 生成阶段强制模型只输出 `ex:...`/绝对 IRI；若检测到相对 IRI（如 `<document_xxx>`）则判失败并重试。

```bash
grep -nE '<(document|clause|rule)_' /data/aigc/zhidu_0407/output/08_clause_semantics_ontology/clause_semantics_ontology.graphdb_ready.ttl | head
```

图谱候选冲突裁决命令（SPARQL 召回 + LLM 裁决）：

```bash
cd /Users/fengyingsheng/workspaces/personal/fxcjg/fxcjg/server
python apps/complaint_app/tools/zhidu_clause_extract/judge_ontology_conflicts.py \
  --sparql-endpoint http://172.27.60.34:7200/repositories/zhidu \
  --sparql-file apps/complaint_app/tools/zhidu_clause_extract/examples/conflict_candidates.sparql \
  --output-dir /data/aigc/zhidu_0407/output/09_ontology_conflict_judgments \
  --model glm-5
```

说明：
- `--sparql-endpoint` 同时支持 GraphDB 与 Fuseki。
- GraphDB 示例：`http://<graphdb-ip>:7200/repositories/<repo-id>`
- Fuseki 示例：`http://<fuseki-ip>:3030/<dataset>/query`
- 输出文件：
- `ontology_conflict_candidates.jsonl`：SPARQL 命中的候选全集
- `ontology_conflict_judgments.jsonl`：LLM 对候选全集的裁决结果
- `ontology_conflict_judgments_focus.jsonl`：仅 `conflict/uncertain` 的快速复核清单
- `ontology_conflict_judgments_focus.csv`：快速复核表（含双侧制度、条款号、证据文本）
- `ontology_conflict_judgments_summary.json`：汇总统计

## 用法

```bash
cd server
python apps/complaint_app/tools/zhidu_clause_extract/extract_pdf_clauses.py \
  --pdf-dir /data/aigc/zhidu/pdf \
  --output-dir /data/aigc/zhidu/output/zhidu_extract
```

可选参数：

- `--input-mode`：`scan` 或 `catalog`，默认 `scan`
- `--catalog-json`：可选，提供后会输出目录对账结果，并在 `scan` 模式下补充分类信息
- `--category`：只处理指定版块比较键
- `--limit`：只处理前 N 份文件
- `--debug`：输出调试日志

只做目录对账时，可使用：

```bash
cd server
python apps/complaint_app/tools/zhidu_clause_extract/audit_catalog_vs_pdf.py \
  --catalog-json /data/aigc/zhidu/document_catlog.json \
  --pdf-dir /data/aigc/zhidu/pdf \
  --output-dir /data/aigc/zhidu/output/zhidu_audit
```

已完成 `scan` 抽取、但 `documents.jsonl` 里分类仍为 `未分类` 时，可使用：

```bash
cd server
python apps/complaint_app/tools/zhidu_clause_extract/remap_document_categories.py \
  --catalog-json /data/aigc/zhidu/document_catlog.json \
  --documents-json /data/aigc/zhidu/output/zhidu_extract/documents.jsonl \
  --clauses-json /data/aigc/zhidu/output/zhidu_extract/clauses.jsonl \
  --output-dir /data/aigc/zhidu/output/zhidu_extract_remapped
```

如需直接覆盖原文件，可加：

```bash
  --in-place
```

把 `documents.jsonl` 或 `documents_mapped.jsonl` 导出成 Excel：

```bash
cd server
python apps/complaint_app/tools/zhidu_clause_extract/export_jsonl_to_excel.py \
  --input-jsonl /data/aigc/zhidu/output/zhidu_extract/documents.jsonl \
  --output-xlsx /data/aigc/zhidu/output/zhidu_extract/documents.xlsx
```

有效规则筛选、制度对初筛、条款级冲突定位的当前执行命令已迁移到独立文档：
[README_DIRECT_LLM_CONFLICT.md](README_DIRECT_LLM_CONFLICT.md)。

`extract_effective_rules.py` 说明：

- `--limit 2` 可用于先做少量试点验证
- 对 `GLM4.7`，脚本默认按“不思考”调用，并会显式传关闭思考参数
- 只有传入 `--enable-thinking` 时，才会尝试开启思考模式
- 当模型返回内容无法解析为 JSON 时，脚本会在业务层额外重试，默认 `3` 次，可通过 `--business-retries` 调整
- 每次模型返回的原始内容会按截断预览写入日志，便于排查格式问题

## 输入约定

默认模式为 `scan`：

- 直接按 `--pdf-dir` 递归扫描全部 PDF
- 处理顺序按相对路径稳定排序
- 更适合 `json` 不完整或不准确的场景

可选提供 `document_catlog.json`，示例：

```json
{
  "公司治理": [
    "制度A.pdf",
    "制度B.pdf"
  ],
  "采购管理": [
    "采购管理办法.pdf"
  ]
}
```

`--pdf-dir` 支持平铺目录或多级子目录。脚本会递归查找 PDF；当存在同名文件时，优先选择路径中包含分类名的文件。

## 输出

输出目录包含：

- `documents.jsonl`：文档级结果
- `clauses.jsonl`：条款级结果
- `errors.jsonl`：处理失败或未找到文件
- `failed_documents.jsonl`：失败或需重点复核的文件清单
- `raw_texts/*.txt`：每份制度抽出的正文文本
- `raw_text_manifest.txt`：本次成功生成的正文 txt 文件清单（启用 `--raw-text-only` 时尤其有用）
- `catalog_audit.json`：当提供 `--catalog-json` 时输出 JSON 与 PDF 目录的差异对账结果

如需先只提取正文全文、不做条款切分，可加：

```bash
python apps/complaint_app/tools/zhidu_clause_extract/extract_pdf_clauses.py \
  --pdf-dir /data/aigc/zhidu_0407/pdfs \
  --output-dir /data/aigc/zhidu_0407/output/01_extract \
  --raw-text-only
```

第一步执行完成后，如需直接从同一输出目录下的 `raw_text_manifest.jsonl` 继续做条款提取，可执行：

```bash
python apps/complaint_app/tools/zhidu_clause_extract/extract_pdf_clauses.py \
  --output-dir /data/aigc/zhidu_0407/output/01_extract \
  --clauses-from-raw-text
```

这一步会自动读取：

- `output-dir/raw_text_manifest.jsonl`
- `output-dir/raw_texts/*.txt`

不需要再手动指定 txt 路径，也不会重新读取 PDF 做 OCR，除非你走的是其他模式。

对账脚本输出：

- `catalog_diff.json`：对账汇总
- `json_only.txt`：JSON 里有但 PDF 目录里没有
- `pdf_only.txt`：PDF 目录里有但 JSON 里没有
- `duplicate_json_names.txt`：JSON 内部重名
- `duplicate_pdf_names.txt`：PDF 目录内部重名

分类回填脚本输出：

- `documents_mapped.jsonl`：已回填分类的文档级结果
- `clauses_mapped.jsonl`：已同步回填分类的条款级结果
- `category_remap_summary.json`：回填结果汇总

JSONL 导出脚本输出：

- `documents.xlsx`：Excel 结果

有效规则筛选脚本输出：

- `effective_rules.jsonl`：每份制度一行，包含 `valid_rules` / `invalid_rules`
- `effective_rules.xlsx`：便于人工复核的 Excel，带原文列
- `effective_rules_summary.json`：汇总统计

如需先准备制度目录主数据 mock，并通过 `.env` 配置给后续工具使用，可直接复用仓库内示例文件：

- mock 文件：`apps/complaint_app/tools/zhidu_clause_extract/examples/merged_outline.mock.json`
- `.env` 配置项：`ZHIDU_MERGED_OUTLINE_JSON=/absolute/path/to/merged_outline.json`

如需把 `merged_outline.json` 加载到内存并确认实际生效路径，可执行：

```bash
cd server
python apps/complaint_app/tools/zhidu_clause_extract/load_merged_outline.py
```

查看某一条制度主数据时，可执行：

```bash
cd server
python apps/complaint_app/tools/zhidu_clause_extract/load_merged_outline.py \
  --title "固定资产管理办法"
```

该工具会输出：

- `merged_outline_path`：当前实际加载的 JSON 路径
- `loaded_row_count`：已加载到内存的制度条数
- `title_preview`：前几条制度名称预览

如果未配置 `ZHIDU_MERGED_OUTLINE_JSON`，会自动回退到仓库内的 mock 文件。

`documents.jsonl` 与 `merged_outline.json` 映射、`effective_rules.jsonl` 生成、制度冲突分析的执行命令见：
[README_DIRECT_LLM_CONFLICT.md](README_DIRECT_LLM_CONFLICT.md)。

如需先基于 `documents_mapped.jsonl + raw_texts` 抽取制度本体三层 JSONL，可执行：

```bash
cd server
python apps/complaint_app/tools/zhidu_clause_extract/extract_ontology_jsonl.py \
  --documents-json /data/aigc/zhidu_0407/output/02_outline_mapping/documents_mapped.jsonl \
  --raw-text-dir /data/aigc/zhidu_0407/output/01_extract/raw_texts \
  --output-dir /data/aigc/zhidu_0407/output/03_ontology \
  --model glm-5
```

该脚本会输出：

- `ontology_instances.jsonl`：逐制度的本体实例层
- `ontology_entities.jsonl`：全局实体层
- `ontology_relations.jsonl`：全局关系层
- `ontology_normalization.jsonl`：概念归一化层
- `ontology_summary.json`：汇总统计

如需先按条款抽取某一份制度的“原始提及”，可执行：

```bash
cd server
python apps/complaint_app/tools/zhidu_clause_extract/extract_clause_semantics_mentions.py \
  --clauses-jsonl /data/aigc/zhidu_0407/output/01_extract/clauses.jsonl \
  --doc-id 你的doc_id \
  --output-dir /data/aigc/zhidu_0407/output/04_clause_semantics_mentions \
  --model glm-5
```

该脚本会输出：

- `clause_semantics_mentions.jsonl`：逐条款原始提及结果
- `clause_semantics_mentions_summary.json`：汇总统计

如需把 `clause_semantics_mentions.jsonl` 转成便于人工 review 的 Excel，可执行：

```bash
cd server
python apps/complaint_app/tools/zhidu_clause_extract/export_clause_semantics_mentions_review.py \
  --input-jsonl /data/aigc/zhidu_0407/output/04_clause_semantics_mentions/clause_semantics_mentions.jsonl \
  --output-xlsx /data/aigc/zhidu_0407/output/04_clause_semantics_mentions/clause_semantics_mentions_review.xlsx
```

`detect_clause_conflicts.py` 使用说明：

- 默认跳过已经写入 `doc_pair_review.jsonl` 的制度对，避免重复分析
- 如需强制重新跑全部制度对，可加 `--force-rerun`
- 默认只对 `confidence=high` 的制度对继续做二筛
- 如果要放宽到 `medium` 及以上，可加 `--stage2-min-confidence medium`
- 如需自定义重点版块对，可通过 `--inter-category-pairs-json /data/aigc/zhidu/output/inter_category_pairs.json` 传入 JSON 配置

常用执行命令见：[README_DIRECT_LLM_CONFLICT.md](README_DIRECT_LLM_CONFLICT.md)。

制度对初筛脚本输出：

- `doc_pair_review.jsonl`：制度对初筛结果
- `doc_pair_review.xlsx`：便于人工复核的 Excel
- `summary.json`：汇总统计
- `high_conflict_clauses.jsonl`：high 制度对的条款级定位结果（启用 `--run-high-clause-locate` 时输出）
- `high_conflict_clauses.xlsx`：high 制度对条款级定位的 Excel 结果
- `doc_pair_review_merged.jsonl`：初筛与二筛合并结果（启用 `--run-high-clause-locate` 时输出）
- `doc_pair_review_merged.xlsx`：初筛与二筛合并后的单表结果

`summary.json` 会额外给出：

- `pair_scope_counts`：组内/组间制度对数量
- `new_pair_count`：本次新增分析的制度对数量
- `skipped_existing_pair_count`：因历史结果已存在而跳过的制度对数量
- `run_inter_category`：本次是否开启组间比较
- `force_rerun`：本次是否强制重跑

`export_high_conflict_report.py` 会从 `doc_pair_review_merged.xlsx` 导出 high/high 冲突报告，筛选满足以下条件的记录：

- `possible_conflict = true`
- `confidence = high`
- `stage2_conflict_candidates_json` 中至少包含一条 `confidence = high` 的候选

输出文件：

- `high_conflict_report_pairs.xlsx`：制度对级结果
- `high_conflict_report_candidates.xlsx`：候选级结果，一行一条冲突候选
- `high_conflict_report.docx`：格式化 Word 报告
- `high_conflict_report.pdf`：由 Word 转换得到的 PDF 报告

说明：

- 如果当前环境找不到 `soffice`，脚本会自动跳过 PDF 导出，不影响 `xlsx + docx`
- 如需手动指定 LibreOffice 路径，可加 `--soffice-bin /usr/bin/soffice`
- 如只想生成 `xlsx + docx`，可加 `--skip-pdf`

从制度清单 `txt` 结构化并和本地文档目录对账：

```bash
cd /Users/fengyingsheng/workspaces/personal/fxcjg/fxcjg/server
python apps/complaint_app/tools/zhidu_clause_extract/reconcile_document_catalog.py \
  --catalog-txt /data/aigc/zhidu/catalog_list.txt \
  --local-dir /data/aigc/zhidu/pdf \
  --output-dir /data/aigc/zhidu/output/zhidu_catalog_reconcile
```

脚本假设：

- `txt` 中一行一条记录
- 字段顺序为：`序号、名称、起草部门、生效日期、时效性、附件数、上传者`
- 本地文档在一个目录及其子目录下，脚本会递归扫描

输出文件：

- `catalog_structured.jsonl`：结构化后的清单
- `catalog_structured.xlsx`：结构化后的 Excel
- `matched.xlsx`：匹配成功结果
- `missing_local.xlsx`：清单里有但本地没有
- `local_only.xlsx`：本地有但清单里没有
- `catalog_invalid_lines.xlsx`：未成功解析的原始行
- `summary.json`：汇总统计

从“现行有效制度目录”表格 PDF 提取结构化目录：

```bash
cd /Users/fengyingsheng/workspaces/personal/fxcjg/fxcjg/server
python apps/complaint_app/tools/zhidu_clause_extract/extract_effective_catalog_table.py \
  --pdf-path /data/aigc/zhidu/effective_catalog.pdf \
  --output-dir /data/aigc/zhidu/output/zhidu_effective_catalog
```

输出文件：

- `effective_catalog.jsonl`
- `effective_catalog.xlsx`
- `effective_catalog_summary.json`

从 XMind 导出的制度体系图 PDF 提取层级 JSON：

```bash
cd /Users/fengyingsheng/workspaces/personal/fxcjg/fxcjg/server
python apps/complaint_app/tools/zhidu_clause_extract/extract_system_tree_from_pdf.py \
  --pdf-path /data/aigc/zhidu/system_tree.pdf \
  --output-dir /data/aigc/zhidu/output/zhidu_system_tree
```

输出文件：

- `hierarchy_catalog.json`
- `hierarchy_catalog_summary.json`
- `hierarchy_catalog_unresolved.json`

## 处理规则

- 正文优先从 `附件 / 第一章 / 第一条` 开始
- 在识别到若干条款后，命中 `附表 / 清单 / 审批表 / 流程图 / 附录` 等尾部特征时尝试截断
- 按 `第X章 / 第X条 / （一）（二）` 切分，第一版以 `第X条` 为主单元

## 依赖

优先使用 `PyMuPDF`（`fitz`）读取 PDF；若运行环境未安装，则自动回退到 `pypdf`。

## 建议

如果实际 PDF 数量和 `document_catlog.json` 不一致，优先使用默认的 `scan` 模式全量跑一遍，再根据 `catalog_audit.json` 看：

- 哪些 PDF 在目录里但不在 JSON 里
- 哪些 JSON 里有但目录里不存在
- 是否存在同名 PDF
