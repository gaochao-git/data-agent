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

当前采用**指标语义层**路线（指标/维度/口径统一定义），暂不实现本体语义层（对象/事件/关系建模）。原因与演进条件：

- 现阶段用户问题以标准化看数为主，指标层投入产出比最高；
- 本体化需要为对象/事件/关系逐一绑定业务含义，建模产能约为指标层的数倍，行业也尚无标准化的落地范式；
- 指标层可增量长出对象与事件；当归因/解释类问题占比显著上升时再演进。

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
