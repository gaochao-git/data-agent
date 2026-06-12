# ChatData 测试框架

本测试框架用于评估 ChatData 的自然语言问数能力。

当前产品主链路为语义层 NL2DSL2SQL，因此 `nl2dsl_tests/` 是现行语义问数能力的核心测试集；`nl2sql_tests/` 主要用于评估 SQL 编辑器 AI 辅助生成等非主链路能力。

## 目录结构

```
tests/
├── README.md                   # 本文档
├── evaluator.py                # 评估器（评估逻辑）
├── unit/                       # 单元测试：函数、编译器、服务逻辑，不依赖真实 HTTP 服务
├── api/                        # API 契约测试：走 FastAPI 路由，外部依赖 mock
├── integration/                # 集成测试：可调用本地后端服务、LLM、测试数据源
│   └── test_runner.py          # NL2SQL/NL2DSL 测试运行器
├── nl2sql_tests/               # NL2SQL 测试用例
│   ├── simple/                 # 简单查询（10个）
│   │   └── test_cases.json
│   ├── joins/                  # 连接查询（10个）
│   │   └── test_cases.json
│   └── aggregation/            # 聚合查询（10个）
│       └── test_cases.json
├── nl2dsl_tests/               # NL2DSL 测试用例
│   ├── simple/                 # 简单语义查询（15个）
│   │   └── test_cases.json
│   └── complex/                # 复杂语义查询（15个）
│       └── test_cases.json
└── reports/                    # 测试报告输出目录（运行后生成）
```

## 测试用例格式

### NL2SQL 测试用例

```json
{
  "id": "nl2sql_simple_001",
  "category": "simple",
  "difficulty": "easy",
  "question": "查询所有数据库集群",
  "ground_truth": {
    "sql": "SELECT * FROM db_cluster",
    "components": ["SELECT", "FROM"]
  }
}
```

### NL2DSL 测试用例

```json
{
  "id": "nl2dsl_simple_001",
  "category": "simple",
  "difficulty": "easy",
  "question": "查询集群数量",
  "ground_truth": {
    "dsl": {
      "metrics": ["cluster_count"],
      "dimensions": [],
      "filters": [],
      "having": []
    },
    "components": ["METRIC"]
  }
}
```

## 评估指标

### NL2SQL 评估指标

适用场景：SQL 编辑器中的 AI 辅助生成 SQL。

1. **Exact Match (EM)**: 精确匹配率 - 生成的 SQL 与标准答案完全一致
2. **Component Match (CM)**: 组件匹配率 - SQL 组件（SELECT, FROM, WHERE 等）的匹配程度
3. **Similarity**: 相似度 - 基于字符串相似度算法计算

### NL2DSL 评估指标

适用场景：智能问数主链路（语义问数）。

1. **Exact Match (EM)**: 精确匹配率 - 生成的 DSL 与标准答案完全一致
2. **Component Match (CM)**: 组件匹配率 - DSL 组件（METRIC, DIMENSION, FILTER, HAVING）的匹配程度
3. **Overall Accuracy**: 综合准确率 - 各字段准确率的平均值
4. **Field Accuracy**: 字段准确率 - metrics, dimensions, filters, having 各字段的准确率

## 使用方法

### 1. 配置环境

确保后端服务正在运行：

```bash
cd /Users/gaochao/work/git_repo/fxcjg/server
python main.py
```

### 2. 配置测试参数

编辑 `integration/test_runner.py` 中的配置：

```python
# API 地址
API_BASE_URL = "http://127.0.0.1:5001"

# 数据源 ID（可选，None 表示使用默认数据源）
DATASOURCE_ID = None

# 语义配置（用于 NL2DSL 测试）
SEMANTIC_CONFIG = {
    "metrics": [
        {"name": "cluster_count", "expression": "COUNT(DISTINCT cluster_id)"},
        # ... 更多指标
    ],
    "dimensions": [
        {"name": "db_type", "column": "db_type"},
        # ... 更多维度
    ],
}
```

### 3. 运行测试

推荐从 `server/` 目录执行，便于 coverage 的 `--source` 路径和 CI 保持一致：

```bash
cd /Users/gaochao/work/git_repo/fxcjg/server
conda activate fxcjg

# 运行 ChatData 全部测试
pytest apps/chat_data_app/tests -q

# 只运行单元测试
pytest apps/chat_data_app/tests/unit -q

# 只运行 API 契约测试
pytest apps/chat_data_app/tests/api -q

# 只运行某个测试文件
pytest apps/chat_data_app/tests/unit/test_sql_executor.py -q

# 只运行某个测试用例
pytest apps/chat_data_app/tests/unit/test_sql_executor.py::test_prepare_sql_for_execution_removes_comments_without_touching_string_literals -q
```

覆盖率统计命令：

```bash
cd /Users/gaochao/work/git_repo/fxcjg/server
conda activate fxcjg

python -m coverage erase
python -m coverage run --source=apps/chat_data_app --branch -m pytest apps/chat_data_app/tests -q
python -m coverage report -m --sort=miss
```

集成评测需要先启动后端服务，再执行评测脚本：

```bash
cd /Users/gaochao/work/git_repo/fxcjg/server/apps/chat_data_app/tests

# 激活虚拟环境
conda activate fxcjg

# 运行集成评测
python integration/test_runner.py
```

### 4. 查看报告

测试完成后，报告将保存在 `reports/` 目录下：

- `nl2sql_report_YYYYMMDD_HHMMSS.json` - SQL 编辑器 AI 辅助生成测试报告
- `nl2dsl_report_YYYYMMDD_HHMMSS.json` - 语义问数主链路测试报告

报告格式：

```json
{
  "timestamp": "2025-12-28T10:00:00",
  "summary": {
    "test_type": "nl2sql",
    "total_cases": 30,
    "successful": 28,
    "failed": 2,
    "exact_match_count": 20,
    "exact_match_rate": 0.67,
    "avg_component_match_rate": 0.85,
    "avg_similarity": 0.78
  },
  "category_stats": {
    "simple": {
      "total": 10,
      "exact_match": 8,
      "accuracy": 0.8
    },
    "joins": {
      "total": 10,
      "exact_match": 7,
      "accuracy": 0.7
    },
    "aggregation": {
      "total": 10,
      "exact_match": 5,
      "accuracy": 0.5
    }
  },
  "difficulty_stats": {
    "easy": {"total": 10, "exact_match": 9, "accuracy": 0.9},
    "medium": {"total": 12, "exact_match": 8, "accuracy": 0.67},
    "hard": {"total": 8, "exact_match": 3, "accuracy": 0.38}
  },
  "detailed_results": [
    {
      "test_case_id": "nl2sql_simple_001",
      "category": "simple",
      "difficulty": "easy",
      "question": "查询所有数据库集群",
      "evaluation": {
        "exact_match": true,
        "component_match_rate": 1.0,
        "similarity": 1.0,
        "component_details": {...},
        "predicted_sql": "SELECT * FROM db_cluster",
        "ground_truth_sql": "SELECT * FROM db_cluster"
      },
      "success": true
    }
    // ... 更多测试结果
  ]
}
```

## 业界基准

根据业界研究（截至 2025 年初）：

### NL2SQL 基准

- **Spider 数据集**: SOTA 准确率约 88.7%
- **BIRD 数据集**: SOTA 准确率约 67.28%
- **一般应用**: 60-80% 的执行准确率被认为是可接受的

### 目标准确率

- **简单查询 (Simple)**: 目标 > 85%
- **连接查询 (Joins)**: 目标 > 70%
- **聚合查询 (Aggregation)**: 目标 > 60%
- **语义层查询 (NL2DSL，主链路)**: 目标 > 80%

## 添加新测试用例

### 1. 创建测试用例文件

在相应目录下的 `test_cases.json` 中添加新用例。

### 2. 遵循格式规范

确保包含所有必需字段：
- `id`: 唯一标识符
- `category`: 分类（simple, joins, aggregation, complex）
- `difficulty`: 难度（easy, medium, hard）
- `question`: 自然语言问题
- `ground_truth`: 标准答案

### 3. 组件标记

NL2SQL 组件：
- SELECT, FROM, WHERE, JOIN, GROUP BY, HAVING, ORDER BY, LIMIT
- DISTINCT, COUNT, SUM, AVG, MAX, MIN
- IS NULL, LIKE, IN, SUBQUERY

NL2DSL 组件：
- METRIC, DIMENSION, FILTER, HAVING

## 常见问题

### Q: 测试失败怎么办？

A: 检查：
1. 后端服务是否正常运行
2. 数据源是否正确配置
3. API 地址是否正确
4. 查看 `detailed_results` 中的错误信息

### Q: 如何调整语义配置？

A: 修改 `test_runner.py` 中的 `SEMANTIC_CONFIG`，确保包含测试用例中使用的所有指标和维度。

### Q: 如何单独测试某个类别？

A: 修改 `test_runner.py`，在 `load_test_cases` 中添加过滤逻辑：

```python
test_cases = self.load_test_cases(test_dir)
# 只测试 simple 类别
test_cases = [tc for tc in test_cases if tc['category'] == 'simple']
```

## 贡献指南

欢迎添加新的测试用例！请确保：

1. 测试用例覆盖不同的 SQL 模式和业务场景
2. 标准答案经过人工验证
3. 组件标记准确完整
4. 遵循 JSON 格式规范
