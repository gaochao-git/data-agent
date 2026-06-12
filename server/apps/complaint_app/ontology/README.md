# ontology

`ontology/` 当前先按 `对外投资管理` 样例专题做本地验证，不直接泛化到全量制度库。

## 当前状态

- `sources/` 下有 4 份样例 PDF，可用于验证专题本体和条款归一化。
- 现有脚本可完成 `PDF 正文抽取 -> 章条切分`，但已暴露两个兼容点：
  - `任子行...2025年7月.PDF` 因大写 `.PDF` 后缀未被现有 `rglob("*.pdf")` 扫到。
  - `深圳王子...` 使用 `第2.3条` 这类十进制编号，未被现有 `第X条` 规则切分。

这两个问题说明，后续正式实现时需要把：

- 文件后缀大小写兼容
- 条款编号体系兼容

一起纳入抽取层。

## 本目录新增内容

- `schema/ontology_schema.md`
  - 只针对 `对外投资管理` 样例制度定义的专题本体。
- `schema/norm_unit_schema.json`
  - 条款归一化后的 `NormUnit` 结构定义。
- `examples/sample_norm_units.jsonl`
  - 基于 4 份样例制度整理的首批 `NormUnit` 映射样本。
- `examples/sample_norm_units.md`
  - 样本的人类可读版摘要。

## 当前建议的使用方式

1. 先用 `examples/sample_norm_units.jsonl` 验证 `NormUnit` 字段是否足以覆盖真实条款。
2. 确认 `schema/norm_unit_schema.json` 稳定后，再实现自动归一化脚本。
3. 归一化结果再进入图数据库，图库只负责查询，不承担推理。
4. 冲突判断由大模型基于 `NormUnit + 文档关系上下文` 完成。

## 本地原型流水线

当前已经补齐一条可直接运行的样例流水线：

```bash
cd server
python apps/complaint_app/ontology/scripts/run_sample_pipeline.py
```

默认输出到：

- `apps/complaint_app/ontology/output/sample_pipeline/01_extract`
- `apps/complaint_app/ontology/output/sample_pipeline/02_norm_units`
- `apps/complaint_app/ontology/output/sample_pipeline/03_conflicts`

也可以分步运行：

```bash
cd server
python apps/complaint_app/tools/zhidu_clause_extract/extract_pdf_clauses.py \
  --pdf-dir apps/complaint_app/ontology/sources \
  --output-dir apps/complaint_app/ontology/output/sample_pipeline/01_extract

python apps/complaint_app/ontology/scripts/normalize_investment_norm_units.py \
  --clauses-json apps/complaint_app/ontology/output/sample_pipeline/01_extract/clauses.jsonl \
  --output-dir apps/complaint_app/ontology/output/sample_pipeline/02_norm_units

python apps/complaint_app/ontology/scripts/generate_conflict_candidates.py \
  --norm-units-json apps/complaint_app/ontology/output/sample_pipeline/02_norm_units/normalized_norm_units.jsonl \
  --output-dir apps/complaint_app/ontology/output/sample_pipeline/03_conflicts
```

## 当前输出

- 抽取层已兼容：
  - 大写 `.PDF`
  - `第2.3条` 这类十进制条款编号
- 样例流水线当前可得到：
  - `161` 条原始条款
  - `146` 条归一化 `NormUnit`
  - `412` 对候选冲突

## 当前边界

- `normalize_investment_norm_units.py` 是规则驱动原型，不是最终版本。
- `generate_conflict_candidates.py` 当前输出的是候选冲突，不是最终冲突结论。
- 真正的最终结论仍应由：
  - 图数据库查询到的上下文
  - 大模型推理
  - 人工复核

共同完成。
