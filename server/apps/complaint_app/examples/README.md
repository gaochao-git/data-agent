# complaint_app examples

## 1. 示例文件
- `complaint_items_example.xlsx`：举报事项样例数据（含 10 条）

列名与脚本默认参数一致：
- `举报事项`
- `举报信`
- `举报时间`
- `摘要`
- `举报人引用法律`
- `一级分类_real`
- `二级分类_real`

## 2. 办法A批量脚本

脚本路径：
- `server/apps/complaint_app/tools/complaint_expert_classification_batch.py`

说明：
- 脚本按“两阶段提示词”执行：先一级分类，再按每个一级候选分别执行一次二级分类（N个一级候选=二级调用N次）。
- 复用 `server/apps/complaint_app/core/llm/llm_client.py` 直连模型接口。
- 默认提示词文件：
  - `server/apps/complaint_app/tools/prompts/complaint_expert_classification_first_system.txt`
  - `server/apps/complaint_app/tools/prompts/complaint_expert_classification_second_system.txt`

### 2.1 必要环境变量
- `OPENAI_API_KEY`（或命令行 `--api-key`）
- `OPENAI_BASE_URL`（或命令行 `--base-url`）

可选：
- `DATA_ROOT`（用于自动读取最新专家经验 `parsed.json`）
- `COMPLAINT_MATCH_EXPERIENCE_THINKING`（默认思考开关；可被命令行参数覆盖）

脚本会自动尝试加载当前目录 `.env` 和 `server/.env`，所以如果你已写入 `.env`，可不传 `--api-key/--base-url`。

### 2.2 小批次试跑（推荐先跑）
```bash
python3 server/apps/complaint_app/tools/complaint_expert_classification_batch.py \
  --input-excel server/apps/complaint_app/examples/complaint_items_example.xlsx \
  --output-excel server/apps/complaint_app/examples/complaint_items_example.out.csv \
  --item-col 举报事项 \
  --story-col 摘要 \
  --first-real-col 一级分类_real \
  --second-real-col 二级分类_real \
  --start 0 \
  --limit 10 \
  --max-concurrent 4 \
  --disable-thinking
```

如果本机开了代理，建议追加：
```bash
  --no-proxy
```

### 2.3 全量跑（你的1038条）
```bash
python3 server/apps/complaint_app/tools/complaint_expert_classification_batch.py \
  --input-excel /path/to/your_1038.xlsx \
  --output-excel /path/to/your_1038.complaint_expert_classification.glm.csv \
  --item-col 举报事项 \
  --story-col 摘要 \
  --first-real-col 一级分类_real \
  --second-real-col 二级分类_real \
  --max-concurrent 8
```

### 2.4 可选参数
- `--api-key`：模型 API Key（覆盖环境变量）
- `--base-url`：模型 Base URL（覆盖环境变量）
- `--model`：覆盖环境中的模型名（默认读取 `COMPLAINT_MATCH_EXPERIENCE_MODEL`）
- `--skip-real-credit-card`：跳过 `一级分类_real=信用卡类` 的样本
- `--skip-real-nfra-duty`：跳过 `一级分类_real=国家金融监督管理总局职责类` 的样本
- `--sheet-name`：指定 sheet
- `--progress-every`：每完成 N 条打印一次进度（默认 `1`）
- `--max-retries`：单条重试次数（默认 `3`）
- `--story-max-chars`：摘要截断长度（默认 `4096`）
- `--no-proxy`：强制直连，不读取系统代理环境变量
- `--enable-thinking`：强制开启思考（覆盖环境变量）
- `--disable-thinking`：强制关闭思考（覆盖环境变量）
- `--expert-experience-json`：显式指定专家经验 `parsed.json`
- `--expert-experience-base-dir`：显式指定专家经验目录
- `--metrics-json`：分类准确率指标输出路径

说明：`--output-excel` 参数支持 `.xlsx` 和 `.csv`。  
当输出路径后缀为 `.csv` 时，脚本会直接导出 CSV（`utf-8-sig`）。
脚本会额外生成同名的 `*.preview.md`，便于在 Linux 终端直接查看核心结果字段。

## 4. 本地法律Skill批量脚本（后半程）

脚本路径：
- `server/apps/complaint_app/tools/complaint_local_law_skill_batch.py`

说明：
- 不走 planner 前半程（不做文本提取/摘要生成），直接复用 runtime 的 skill 执行链。
- 每条样本执行 `complaint-law-analysis` skill（允许 shell 工具检索本地 laws）。
- 输出新增三列：
  - `本地法律_分析原文`
  - `本地法律_法律列表`
  - `本地法律_法规条款数组`

执行示例：
```bash
python3 server/apps/complaint_app/tools/complaint_local_law_skill_batch.py \
  --input-excel /path/to/your_input.xlsx \
  --output-excel /path/to/your_output.xlsx \
  --item-col 举报事项 \
  --story-col 摘要 \
  --max-concurrent 4 \
  --max-retries 3
```

可选参数：
- `--api-key`：覆盖 `OPENAI_API_KEY`
- `--base-url`：覆盖 `OPENAI_BASE_URL`
- `--skill-name`：默认 `complaint-law-analysis`
- `--skill-prompt`：默认 `基于提供材料使用本地laws分析举报事项并给出法律依据。`
- `--extract-model`：提取模型名，默认读取 `COMPLAINT_LAW_ANALYSIS_MODEL`
- `--start` / `--limit`：分批运行
- `--progress-every`：进度打印频率
- `--no-proxy`：直连模式

## 5. 专家经验法律对比补列脚本

脚本路径：
- `server/apps/complaint_app/tools/complaint_expert_law_compare_batch.py`

说明：
- 不重跑本地法律 skill，仅对已有结果表追加专家经验法律对比列。
- 数据来源为专家经验 `parsed.json`。
- 按 `一级分类_real` 和 `二级分类_real` 生成四列：
  - `专家经验一级法律汇总`
  - `专家经验二级分类法律汇总`
  - `专家经验二级分类_核实要点`
  - `专家经验二级分类_调阅清单`

执行示例：
```bash
python3 server/apps/complaint_app/tools/complaint_expert_law_compare_batch.py \
  --input-excel /path/to/local_law_output.csv \
  --output-excel /path/to/local_law_output.with_expert.csv \
  --first-real-col 一级分类_real \
  --second-real-col 二级分类_real
```

可选参数：
- `--sheet-name`：可选：sheet名称，默认首个
- `--expert-experience-json`：显式指定专家经验 `parsed.json`
- `--expert-experience-base-dir`：显式指定专家经验目录
- `--start` / `--limit`：分批运行
- `--progress-every`：进度打印频率

## 6. 本地法律结果评估脚本

脚本路径：
- `server/apps/complaint_app/tools/complaint_local_law_eval_batch.py`

说明：
- 基于专家经验二级分类三列与本地法律完整输出，调用大模型批量评估 801 条样本。
- 输出新增七列：
  - `法律准确性分`
  - `调阅清单准确性分`
  - `核实思路准确性分`
  - `法律准确性评分理由`
  - `调阅清单准确性评分理由`
  - `核实思路准确性评分理由`
  - `总体评估结论`

执行示例：
```bash
python3 server/apps/complaint_app/tools/complaint_local_law_eval_batch.py \
  --input-excel /path/to/local_law_output.with_expert.csv \
  --output-excel /path/to/local_law_output.with_eval.csv \
  --analysis-col 本地法律_分析原文 \
  --max-concurrent 4 \
  --progress-every 10
```

可选参数：
- `--api-key`：覆盖 `OPENAI_API_KEY`
- `--base-url`：覆盖 `OPENAI_BASE_URL`
- `--model`：覆盖评估模型名，默认读取 `COMPLAINT_LAW_ANALYSIS_MODEL`
- `--second-law-col`：专家经验二级分类法律列名
- `--second-verify-col`：专家经验二级分类核实要点列名
- `--second-checklist-col`：专家经验二级分类调阅清单列名
- `--analysis-col`：本地法律完整输出列名
- `--start` / `--limit`：分批运行
- `--max-retries`：单条最大重试次数
- `--no-proxy`：直连模式

## 3. 输出说明

输出 Excel 会新增这些结果列：
- `一级分类_pred`
- `二级分类_pred`
- `匹配度`
- `分类理由`
- `一级是否正确`
- `二级是否正确`

分类指标会单独写到 `*.metrics.json`，不写入主结果表。
