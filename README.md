---
base_model:
- ""
frameworks:
- other
tasks: []
---

# Data Agent

Data Agent 是面向数据查询与分析场景的智能问数项目，以指标语义层为核心，包含后端 FastAPI 服务与前端 Vue 应用。

## 设计边界

语义层采用**「指标语义层 + 本体读侧」**双轨：

- **指标语义层**（对象/事件/关系建模之外的指标/维度/口径统一定义）是主路径：现阶段用户问题以标准化看数为主，指标层投入产出比最高；当归因/解释类问题占比显著上升时再向对象与事件扩张。
- **本体读侧**（已实现）：面向 AI 问数的「Palantir 语义半边子集 + Ossie 三层同构」——对象/属性/关系（概念层）+ 到语义模型的映射（映射层）+ 既有指标语义模型（语义模型层），消费方式为只读导航（AI 目录注入 + 定位报告 + 一致性巡检），不含写回/动作（Palantir kinetic 半边）。定位说明：概念模型是 Palantir 形（一等属性 + 二元关系），仅在「三层分层 + 映射只引用语义模型」层面与 Ossie 三文档（ontology/mapping/core）同构，**不能直接产出合规 Ossie ontology/mapping 文档**；Ossie 交换目前只覆盖语义模型（converter/importer）。n 元关系、派生概念（extends/derived_by/requires）、表达式映射不在表达范围内，后续导入 Ossie 本体须做降级校验（不支持的结构拒绝而非静默丢弃）。

## 项目结构

```text
data-agent/
├── server/      # 后端服务，FastAPI 入口为 server/main.py
├── web-vue/     # 前端应用，Vue 3 + Vite
└── AGENTS.md、后台管理/普通用户使用手册、系统内核链路手册（均在仓库根目录）
```

## 核心能力

- 数据源管理与表结构同步
- 指标集、维度、字段字典配置
- 自然语言智能问数
- 语义 DSL 转 SQL
- SQL 执行、结果展示与历史会话
- 智能分析与查询结果 artifact 管理
- 看板配置与面板查询
- 通过指标语义层统一口径，解决的是“这个数到底怎么算”的指标统一、口径复用和标准化问数问题，是把业务世界中的大量问题收敛成“指标 + 维度 + 条件 + 时间”的表达方式。


## 本地启动

### 后端

```bash
python server/main.py
```

默认端口为 `5001`，可通过 `PORT` 环境变量修改。

### 前端

```bash
cd web-vue
npm install
npm run dev
```

默认访问地址以 Vite 输出为准，通常为 `http://localhost:5173`。

## 常用检查

```bash
# 后端测试
cd server
pytest apps/chat_data_app/tests

# 前端构建（含 vue-tsc 类型检查）
cd web-vue
npm run build
```

说明：`npm run build` 会同时执行 `vue-tsc` 类型检查与 Vite 打包，当前可完整通过；仅需快速验证打包时可用 `npm run build-only`。

## 远程仓库

当前独立仓库使用 ModelScope：

```text
https://www.modelscope.cn/dzkk666/data-agent.git
```

当前约定只使用 `master` 分支。

## 安全说明

- 不要提交真实 `.env`、密钥、Token、运行日志和上传文件。
- `server/conf/.env-*`、`web-vue/.env` 等本地环境文件已在 `.gitignore` 中忽略。
- 如曾在命令行或对话中暴露 ModelScope Token，建议及时在 ModelScope 账号设置中重置。
