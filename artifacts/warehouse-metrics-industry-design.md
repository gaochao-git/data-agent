# 业内"数仓分层 + 指标体系"设计调研与 data-agent 对标

> 日期：2026-09-27
> 主线：Kimball 维度建模（方法论源头）→ 阿里 OneData（国内工程规范）→ Headless BI / 指标层（现代，面向多消费端与 AI）
> 核心命题：原子指标在哪定义、派生/复合指标怎么组织、口径如何"一次定义、处处一致"

---

## 1. 三代设计范式总览

| 代际 | 代表 | 组织方式 | 指标收口机制 | 消费端 |
| --- | --- | --- | --- | --- |
| 第一代（1996） | Kimball | 事实表 + 维度表（星型/雪花） | **一致性事实 / 一致性维度** | 数据集市、报表 |
| 第二代（2010s） | 阿里 OneData | ODS / CDM(DIM+DWD+DWS) / ADS | **原子/派生/复合指标**公式化拼装 | 报表、实时大屏 |
| 第三代（2020s） | dbt Semantic Layer、Cube、Looker | 独立**语义层（Metrics Layer / Headless BI）** | 代码定义一次、自动生成 SQL、多端复用 | BI、API、嵌入式、**AI Agent** |

---

## 2. 第一代：Kimball 维度建模（思想源头）

- **事实表类型**（决定了后来的 DWD/DWS）：
  - **事务事实表 Transaction**：一行一个事件（最细明细）→ 对应 DWD
  - **周期快照事实表 Periodic Snapshot**：固定周期（日/周）沉淀累计状态 → 对应 DWS
  - **累积快照事实表 Accumulating Snapshot**：跟踪一个业务流程的多个时间节点（下单→支付→发货→签收）
  - 无事实事实表 Factless、聚合事实表 / 立方体 Aggregated
- **关键概念**：
  - **可加 / 半可加 / 不可加事实**（additive / semi / non-additive）：余额类只能按特定维度加总
  - **一致性事实 Conformed Facts**：同一度量在不同事实表 / 数据集市中，口径与定义保持一致
  - **一致性维度 Conformed Dimensions**：日期、客户等维度在各处复用同一版本
  - 代理键 surrogate key、缓慢变化维 SCD（处理维度属性随时间变化）
- Kimball 没有 ODS/DWD/DWS/ADS 的叫法，但"事务事实 → 聚合事实 + 一致性"正是其思想内核。

---

## 3. 第二代：阿里 OneData（把 Kimball 工程化、分层化）

### 3.1 分层：ODS / CDM / ADS
- **ODS**：贴源、保留原貌、记录历史变化（数据准备区）
- **CDM 公共数据模型**：
  - **DIM 维度层**：一致性维度，全局代理键（如 user_sk）
  - **DWD 明细层**：维度建模的明细事实表，标准化事实与维度、做 SCD
  - **DWS 汇总层**：宽表，按主题公共粒度**预聚合指标**，减少重复计算
- **ADS 应用层**：高度反范式，面向实时大屏 / 具体报表，做同环比、目标达成率、Top N、排行榜

### 3.2 上层规划：业务板块 → 数据域（主题域：用户/商品/交易/直播/物流）→ 业务过程

### 3.3 指标体系（业内最完整的术语，建议直接对齐）
- **原子指标**：业务事件行为下不可再拆的度量，分两种：
  - **原生原子指标**：如 支付金额
  - **衍生原子指标**：基于原子指标组合，如 **客单价 = 支付金额 ÷ 买家数**
- **派生指标 = 原子指标 + 统计周期 + 业务限定（修饰词）+ 统计粒度**
  - 例：近 30 天（周期）+ 母婴品类（修饰词）+ 支付金额（原子）+ 按用户（粒度）
- **复合指标**：跨"派生指标"做计算，如 **近 30 天转化率 = 近 30 天买家数 ÷ 近 30 天访客数**
- 配套：修饰词 Modifier、统计周期 Period、统计粒度 Granularity；统一命名规范与单位规范（时间/比例/对象量词等）
- 目标：**同一指标、同一口径、一次定义多处复用**，消灭烟囱式重复建设

> 呼应" DWD 能否有派生"：业内把"行级 / 同粒度组合"归为**衍生原子指标**（可下沉 DWD）；把"加周期 / 修饰"归为**派生指标**（DWS）；把"跨派生再计算"归为**复合指标**（ADS）。

---

## 4. 第三代：Headless BI / 现代指标层（面向多消费端 + AI）

代表：**dbt Semantic Layer（MetricFlow）、Cube、Looker（LookML）、AtScale**。

- **核心思想**：把指标做成**独立语义层，与具体报表 / BI 解耦**：
  - 在代码（YAML / LookML / JS）中统一定义 semantic model + metrics，**版本控制、CI 测试、文档、权限、审计**
  - MetricFlow 自动完成 SQL 构造、自动 JOIN、支持跨多个语义模型组合 measures
  - Cube：Cube 作为命名空间（measures / dimensions / segments / joins / pre-aggregations），内置缓存与访问控制，对外提供 **SQL / REST / GraphQL / MCP** 接口
  - **Hub-and-Spoke**：在语义层定义一次，BI 工具、嵌入式应用、AI Agent 到处消费
- **指标类型（dbt）**：simple、ratio、cumulative（累计，可指定窗口）、derived（跨指标表达式）
- **面向 AI 的关键判断（dbt 2026）**：AI 真正需要的是"**受治理的语义模型**"——机器可读、版本控制、经过测试与文档、CI 审查、权限控制、可审计，让 AI 依赖**被批准的逻辑**而不是猜。

---

## 5. 横向对比

| 维度 | Kimball | 阿里 OneData | Headless BI（dbt / Cube） |
| --- | --- | --- | --- |
| 组织 | 事实表 + 维度表 | ODS / CDM / ADS | 语义模型 + 指标（独立层） |
| 指标放哪 | 事务事实（明细）+ 聚合事实 | 原子在 DWD、派生 DWS、复合 ADS | measure 在语义模型、metric 可跨模型 |
| 口径统一 | 一致性事实 / 维度 | 原子 + 周期 + 修饰 + 粒度拼装 | 代码定义一次、自动生成 SQL |
| 关键机制 | 代理键、SCD、可加性 | 数据域、修饰词、命名 / 单位规范 | 版本控制 / CI / 缓存 / 权限 / MCP |
| 消费端 | 数据集市、报表 | 报表、大屏 | BI、API、AI Agent |

**三代共识**：没有一家把指标口径交给模型自由发挥——都是**确定性定义 + 语义 / 元数据层 + 自动生成**，AI 只在受治理的语义层之上做编排。

---

## 6. 对 data-agent 的对标启示

**✅ 已踩在正确范式上**
- Ossie 语义模型与 Headless BI 的 semantic model **同构**（dbt / Cube 同类），方向是最新一代。
- 已具备：原子 / 派生 / 复核指标、按主题拆模型、模型内重名与表达式校验、YAML 物化与回导、对话补齐口径。

**📐 指标术语建议直接对齐阿里**
- `base_metric` = 原子指标（含**衍生原子指标**，承接 DWD 行级 / 同粒度组合）
- 派生指标 = 原子 + 周期 + 修饰 + 粒度（DWS）
- 复合指标 = 跨派生计算（ADS：转化率、客单价）

**🔴 主要差距（与前两份分析一致）**
1. **跨模型一致性**：Kimball 的"一致性事实"、阿里的"同指标同口径"——需要跨模型口径比对（同指标不同口径、同义不同名）。
2. **指标治理工程化**：dbt 强调的版本控制 / CI 测试 / 审批审计——目前有 YAML 回导，缺指标级校验与评审闭环。
3. **多消费端 Hub-and-Spoke**：目前主要服务对话窗口，未来可像 Cube 那样对外提供统一接口 / MCP，让其他部门与系统接入。

> 结论：data-agent 的底座（语义模型物化 + 自动治理 + 对话编排）与业内最先进路线一致；下一步重心是**跨模型口径收口**与**指标治理工程化**，而非另起炉灶。

---

## 7. 参考来源

- Kimball Group：Dimensional Modeling Techniques https://www.kimballgroup.com/data-warehouse-business-intelligence-resources/kimball-techniques/dimensional-modeling-techniques/
- 阿里云：数据仓库建模方法论与分层架构 https://developer.aliyun.com/article/1673583
- 阿里云：OneData 数据中台体系 https://developer.aliyun.com/article/1674625
- 阿里云 DataWorks：数仓分层 https://help.aliyun.com/en/dataworks/data-layer-1
- 阿里云 Dataphin：核心概念（原子/派生指标、衍生原子指标） https://help.aliyun.com/zh/dataphin/fullmanaged/getting-started/several-warehouse-build-process
- 阿里云：派生与复合指标 https://www.alibabacloud.com/help/en/dataphin/semimanaged-v4/user-guide/create-a-logical-summary-table
- dbt：How the Semantic Layer works https://www.getdbt.com/blog/how-the-dbt-semantic-layer-works
- dbt：About MetricFlow https://docs.getdbt.com/docs/build/about-metricflow
- dbt：Centrally defined metrics — the key to AI success https://www.getdbt.com/blog/centrally-defined-metrics
- Cube：Best Semantic Layer for AI and BI 2026 https://cube.dev/articles/best-semantic-layer-for-ai-and-bi-2026
- 腾讯云：数仓建模理论（CDM/DIM/DWD/DWS） https://cloud.tencent.com/developer/article/2073518
