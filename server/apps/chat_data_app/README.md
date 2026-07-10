# ChatData App - 智能问数功能模块

## 概述

ChatData App 是一个基于 LangGraph 实现的智能问数服务，当前主链路为语义层 NL2DSL2SQL：通过语义层将自然语言转为 DSL，再转为 SQL。

此外，模块还提供两类配套能力：
1. **SQL 编辑器 AI 辅助**：根据自然语言快速生成 SQL 草稿
2. **Builder / Dashboard 支撑接口**：服务可视化构建和数据看板

## 功能特点

- **语义问数**：基于指标、维度、关系配置完成标准化查询
- **SQL 自动生成**：根据语义配置和用户意图生成查询 SQL
- **语义层支持**：通过配置指标/维度实现标准化查询
- **多数据库支持**：支持 MySQL 和 Hive 语法
- **LangGraph集成**：使用 LangGraph 框架进行工作流编排
- **RESTful API**：提供标准的HTTP接口供前端调用

---

## 语义层架构

### 核心流程：NL → DSL → SQL

```
用户自然语言
     ↓
[1. 智能体选择指标集和语义配置]
     ↓
[2. 智能体只生成 DSL 参数（指标/维度/过滤/排序/limit）
     ↓
[3. SemanticSQLGenerator 校验 DSL 并映射语义配置
     ↓
[4. compiler.compile_builder_sql 生成 SQL
     ↓
[5. engine.dialect 按 source_type 适配 MySQL/Hive 方言
     ↓
最终 SQL
```

### 语义配置结构

```python
semantic_config = {
    "id": "config_id",
    "name": "配置名称",
    "source_type": "mysql",  # mysql / hive

    # 可查询的指标
    "metrics": [
        {
            "name": "集群数量",
            "expression": "COUNT(DISTINCT cluster_id)",
            "table": "db_cluster",
            "description": "统计集群总数",
            "filter": "is_deleted = 0"  # 可选：指标自带过滤
        }
    ],

    # 可用维度
    "dimensions": [
        {
            "name": "数据库类型",
            "column": "db_type",
            "table": "db_cluster",
            "logical_type": "enum",  # text / number / date / datetime / enum
            "allowed_values": ["MySQL", "PostgreSQL"],  # 枚举约束
            "mapping": {"mysql": "MySQL"}  # 同义词映射
        }
    ],

    # 表关系（用于 JOIN）
    "joins": [
        {
            "left_table": "db_cluster",
            "right_table": "db_instance",
            "left_column": "cluster_id",
            "right_column": "cluster_id",
            "join_type": "left"
        }
    ]
}
```

### DSL 入参结构

```python
dsl = {
    "metrics": ["集群数量"],
    "dimensions": ["数据库类型"],  # 或 [{"name": "创建时间", "time_grain": "month"}]
    "filters": [],                # WHERE 条件，支持树形 AND/OR
    "having": [],                 # HAVING 条件
    "order_by": [],               # [{"field": "集群数量", "direction": "DESC"}]
    "limit": 100
}
```

---

## SQL 场景覆盖

### 已支持场景

| 类别 | 支持的功能 | 示例 |
|-----|----------|------|
| **聚合函数** | COUNT, SUM, AVG, MAX, MIN, COUNT(DISTINCT) | `COUNT(DISTINCT cluster_id)` |
| **自定义表达式** | 复杂计算表达式 | `SUM(quantity * unit_price)` |
| **WHERE 过滤** | =, !=, >, <, >=, <=, IN, NOT IN, LIKE | `WHERE db_type = 'MySQL'` |
| **树形过滤** | AND/OR 嵌套组合 | `(A OR B) AND C` |
| **GROUP BY** | 单/多维度分组 | `GROUP BY db_type, status` |
| **时间粒度** | year/quarter/month/week/day/hour | `DATE_FORMAT(col, '%Y-%m')` |
| **HAVING** | 聚合结果过滤（支持 AND/OR 树形结构） | `HAVING (a > 10 OR b > 5)` |
| **ORDER BY** | 单/多字段排序 | `ORDER BY count DESC` |
| **LIMIT/OFFSET** | 分页 | `LIMIT 10 OFFSET 20` |
| **JOIN** | LEFT/INNER/RIGHT，自动路径查找 | 自动多表关联 |
| **SQL函数值** | 时间函数等 | `date_sub(current_date(), 7)` |
| **枚举验证** | 值约束和同义词映射 | `mysql` → `MySQL` |
| **明细查询** | 非聚合原始数据查询 | 列出明细数据 |

### MySQL/Hive 语法适配

| 时间粒度 | MySQL | Hive |
|---------|-------|------|
| year | `DATE_FORMAT(col, '%Y')` | `DATE_FORMAT(col, 'yyyy')` |
| month | `DATE_FORMAT(col, '%Y-%m')` | `DATE_FORMAT(col, 'yyyy-MM')` |
| day | `DATE(col)` | `TO_DATE(col)` |
| hour | `DATE_FORMAT(col, '%Y-%m-%d %H:00')` | `DATE_FORMAT(col, 'yyyy-MM-dd HH:00')` |

### 不支持/受限场景

| 类别 | 缺失功能 | 影响 | 替代方案 |
|-----|---------|-----|---------|
| **子查询** | IN (SELECT...), EXISTS | 无法做关联过滤 | 分步查询 |
| **UNION** | UNION / UNION ALL | 无法合并结果集 | 分别查询后合并 |
| **窗口函数** | ROW_NUMBER, RANK, LAG/LEAD | 无法做排名 | 暂无 |
| **同比/环比** | compare SQL | 不在 DSL2SQL 内生成 | 基础 DSL 查询后由智能体脚本计算 |
| **FULL OUTER JOIN** | 全外连接 | - | LEFT + UNION |
| **CASE WHEN 动态** | 只能配置静态表达式 | - | 预定义表达式 |

### 场景覆盖率

```
┌──────────────────────────────────────────────────────────┐
│ 日常 BI 报表查询（聚合统计、多维分析）     ████████████ 95% │
│ 多表关联查询                              █████████░░ 85% │
│ 时间序列分析（趋势、时间粒度）             █████████░░ 85% │
│ 高级分析（排名、复杂过滤）                 ██░░░░░░░░░ 15% │
└──────────────────────────────────────────────────────────┘
```

---

## 核心组件

### 文件结构

```
chat_data_app/
├── agent/
│   ├── state.py           # LangGraph 状态定义
│   ├── chat_data_graph.py # chat_data 主图定义
│   ├── sql_assist_graph.py# SQL 辅助图定义
│   ├── data_analyze_graph.py # 数据分析图定义
│   ├── nodes.py           # Agent 节点
│   ├── tools/             # 工具包
│   ├── config.py          # 模式配置
│   ├── prompts/           # 提示词包
│   └── skills/            # 图表生成技能
├── services/
│   ├── semantic_tools.py              # 语义工具处理器
│   ├── datasource_manager.py          # 数据源管理
│   ├── mysql_sync.py                  # MySQL 元数据同步
│   └── hive_sync.py                   # Hive 元数据同步
├── evaluate/
│   └── accuracy/                      # 页面/API 准确率评测执行器
├── tests/
│   ├── unit/                          # 默认单元测试
│   └── api/                           # 开发阶段手动执行的 API 契约测试
└── handlers.py                        # SSE 流式响应
```

### 关键组件说明

| 组件 | 文件 | 职责 |
|---|-----|------|
| `SemanticSQLGenerator` | semantic/semantic_sql_generator.py | 校验 DSL，映射指标/维度，组装 builder_config |
| `compile_builder_sql` | semantic/builder_sql_compiler.py | 将 builder_config 编译为 SQL |
| `get_dialect` | semantic/dialect.py | 按 source_type 提供 MySQL/Hive 方言 |
| `InputValidator` | semantic/input_validator.py | 校验指标、维度、过滤、排序和 limit |

### LangGraph 工作流

```
START → [agent节点] → [tools_condition] → [tools执行] → [agent节点] → END
              ↑                                    |
              └────────────────────────────────────┘
```

### 工具列表

| 工具 | 说明 |
|-----|------|
| `skill_load` | 智能问数只读加载业务知识技能；只返回 SKILL.md 和 references 文本，不支持脚本、资产或沙箱 |
| `dsl2sql_tool` | 根据语义参数生成 SQL |
| `semantic_query_tool` | 执行语义查询；接收语义 DSL，内部生成 SQL 后执行 |

### 智能问数上下文边界

智能问数按四层组织上下文，避免系统提示词、指标集、技能和复杂任务流程互相覆盖：

| 层级 | 职责 | 不负责 |
|---|---|---|
| 系统提示词 | 全局问数工作流、工具协议、安全边界、输出约束 | 具体业务口径、物理表细节 |
| 指标集 | 可执行事实源：指标、维度、关系、枚举、日期格式、单位、口径 | 业务培训材料、跨领域常识 |
| 用户自定义问数技能 | 用户显式引用时补充业务术语、口径说明、追问策略、典型问法 | 可执行 SQL/DSL 表达式、工具编排、脚本和 artifact |
| Workflow 技能 | 看板、图表、智能分析等复杂产物任务的专用步骤 | 智能问数主链路和指标事实 |

优先级固定为：

```text
安全规则 > 系统工具协议 > 数据源组说明 > 当前指标集 > 用户明确补充 > 显式指定的问数技能 > 模型常识
```

智能问数主链路由系统提示词和工具链固定为：

```text
判断是否查数
-> 未选指标集时，先搜索当前数据源组中的真实指标集
-> 基于指标集名称和描述做轻量目录召回
-> 在候选指标集中做完整指标/维度判断
-> 选中唯一指标集后，按需加载用户本轮显式引用的只读问数技能
-> 生成 DSL
-> DSL2SQL
-> 查询
-> 回答
```

`skill_load` 在智能问数中只允许加载用户显式引用的只读文本型问数技能；不加载 scripts、assets，不挂载沙箱，也不替代当前指标集。系统不再内置默认业务问数技能，业务背景优先通过数据源组智能问数说明和指标集配置维护。

指标集选择分两层：

1. 目录召回：只使用当前用户可见、启用指标集的 `id/name/description`，召回 topK 候选；召回失败时回退全量。
2. 候选精排：只对候选指标集加载完整指标、维度、枚举和补充说明，判断是否完整覆盖问题。

多指标集问题第一阶段只支持“拆分查询 + 汇总/计算”：

```text
一个用户问题 -> 多个 query_plan.tasks
每个 task -> 一个 semantic_config_id -> 独立 DSL -> 独立查询
最终由智能体汇总；需要比例、差值时使用 calculator_tool
```

不支持跨指标集 JOIN、自动补关系、混合维度分组或拼成一张宽表。

---

## 高级特性

### 1. 树形过滤条件（WHERE）

支持复杂的 AND/OR 嵌套：

```python
# (数据库类型=MySQL OR 数据库类型=PostgreSQL) AND 状态=active
filters = [{
    "and": [
        {"or": [
            {"dimension": "数据库类型", "operator": "=", "value": "MySQL"},
            {"dimension": "数据库类型", "operator": "=", "value": "PostgreSQL"}
        ]},
        {"dimension": "状态", "operator": "=", "value": "active"}
    ]
}]
```

### 2. 树形 HAVING 条件

HAVING 也支持 AND/OR 嵌套，用于复杂的聚合结果过滤：

```python
# 集群数量 > 10 OR 实例数量 > 100
having = [{
    "or": [
        {"metric": "集群数量", "operator": ">", "value": 10},
        {"metric": "实例数量", "operator": ">", "value": 100}
    ]
}]

# 生成: HAVING (`集群数量` > 10 OR `实例数量` > 100)
```

### 3. 自动 JOIN 路径查找

使用 BFS 算法找到表间最短连接路径，支持多表链式 JOIN。

### 4. 时间粒度转换

支持 year/quarter/month/week/day/hour，自动适配 MySQL/Hive 语法。

### 5. 枚举值验证

- 自动验证值是否在 `allowed_values` 中
- 支持同义词映射（如 `mysql` → `MySQL`）

### 6. SQL 函数识别

自动识别 `date_sub()`、`current_date()` 等函数，不加引号处理。

---

## 错误处理

| 返回前缀 | 说明 | 处理方式 |
|---------|------|---------|
| `【需要确认】` | 需要用户补充信息（含参数遗漏） | 直接展示给用户，等待补充 |
| `抱歉，当前配置不支持` | 配置无法满足需求 | 直接告知用户 |

# 参考的一些NL2SQL / 语义层产品全景

### 云厂商方案
- AWS NL2SQL
- Google BigQuery
- Azure AI
- QuickSight Q

### BI 产品
- ThoughtSpot
- Tableau GPT
- Power BI Q&A/Copilot
- Qlik
- FineBI

### 语义层工具
- Cube.js
- dbt Metrics
- LookML
- AtScale
- MetricFlow

### 开源项目
- Vanna AI
- Chat2DB
- SQLCoder
- WrenAI
- Dataherald
