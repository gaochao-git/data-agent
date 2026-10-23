# Community: Fourth Age — Practical Ontologies & How to Build Them (Part 4)

> 来源: https://fourthage.substack.com/p/practical-ontologies-and-how-to-build-82e
> 抓取: 2026-10-23（**要点导读卡**：Substack 域名在当前网络不可直连，代理均 522，未能存全文副本；本档为基于原文摘录与检索摘要的要点导读，原文见来源链接）
> 版权: 属 Fourth Age（Peter Mills）；本档观点为社区实践总结，非 Palantir 官方文档，与官方文档冲突时以官方为准
> 作者: Fourth Age（Palantir 合作咨询团队）；发布: 2025-08-30；系列四篇之四（完结篇，距 Part 3 约 5 个月）
> 系列结构: Part 1 数据库设计基础 → Part 2 本体构建 → Part 3 对象·事件·时序 → **Part 4 Actions 操作化（本档）**

---

## 核心主题

通过 **Foundry Actions** 把本体从"只读的世界模型"升级为"可操作的系统"：用户在应用中做出的决策通过 Action 写回本体，把真实世界的业务结果固化下来。这是四部曲的落脚点——前三篇解决"如何描述业务"，本篇解决"如何改变业务"。

## 要点摘录

### Actions 的价值驱动

- **写回（writeback）**：决策不留在聊天记录/Excel 里，而是作为对象属性变更、链接变更、新事件对象持久化进本体。
- **治理（governance）**：Action 是唯一写入口时，校验规则、权限、审计天然集中（对应官方 `../action-types/` 的 submission criteria / rules / action log）。
- **中心化（centralization）**：同一 Action 被多个应用复用（Workshop、报表、外部 API），避免每个入口自己拼 UPDATE 逻辑。

### 组织落地流程

文中给出了一套从识别决策点到发布 Action 的组织化流程（识别 → 建模参数 → 定义校验 → 配置安全 → 试运行 → 培训上线等环节的迭代），强调 Action 设计是**业务流程设计**，不只是技术封装。

### 模式（patterns）

- **Curated Core Actions**：核心高频决策收敛为少数精心维护的 Action，宁少勿多。
- **Disciplined Deprecation**：Action 也要走废弃流程，避免僵尸 Action 越积越多。
- **Progressive Complexity**：先上简单 Action（改属性），随成熟度再引入函数 Action、多步 Action、Webhook。

### 反模式（anti-patterns）

- **"Everything is an Action"**：把所有写路径都包成 Action 会让治理成本失控——只把"需要治理的决策"包成 Action，机械性数据搬运走管道。
- **冗余 Action（Redundant Actions）**：同一决策因入口不同被重复定义多份，字段口径随后漂移（我们项目中对应"同一业务操作只在 action_types 定义一次"的约束）。
- **"Every Action Needs a Function"**：不必为每个 Action 写后端函数，对象/链接编辑类 Action 用规则即可，函数留给真正需要计算的决策。
- **参数贪大（对照 MVP parameters）**：参数集从最小可用集开始，随使用反馈扩展，避免表单吓跑用户。

## 与本项目的关联

- 我们已实现 Action 类型定义与执行（写覆盖层），本篇的 patterns/anti-patterns 可直接作为 `action-types` 管理端的使用规范素材（尤其"冗余 Action"与"Everything is an Action"两条）。
- "Action 是唯一写入口"与官方一致性保证（`../action-types/consistency-guarantees.md`）共同支撑"编辑写覆盖层"的设计；后续若开放前端直写数据源，应先对照本篇评估治理代价。

## 待办

- [ ] 网络可达时抓全文补充本档（同 Part 2 的 JSON API 路径）
