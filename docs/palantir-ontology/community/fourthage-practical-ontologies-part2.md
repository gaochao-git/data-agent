# Community: Fourth Age — Practical Ontologies & How to Build Them (Part 2)

> 来源: https://fourthage.substack.com/p/practical-ontologies-and-how-to-build-d7e
> 抓取: 2026-10-23（**要点导读卡**：Substack 域名在当前网络不可直连，代理均 522，未能存全文副本；本档为基于原文摘录与检索摘要的要点导读，原文见来源链接）
> 版权: 属 Fourth Age（Peter Mills）；本档观点为社区实践总结，非 Palantir 官方文档，与官方文档冲突时以官方为准
> 作者: Fourth Age（Palantir 合作咨询团队）；发布: 2025-03-13；系列四篇之二
> 系列结构: Part 1 数据库设计基础 → **Part 2 本体构建（本档）** → Part 3 对象·事件·时序 → Part 4 Actions 操作化

---

## 核心主题

在 Palantir Foundry 中构建**操作性本体**（operational ontology）的实战方法论。与 Part 1 的数据库设计（3NF、规范化）相对，本篇强调本体是**概念建模**而非数据库建模：设计问题不是"表怎么拆、外键怎么放"，而是"业务里有什么对象、谁在什么流程中对它们做决策"。

## 要点摘录

### 数据约束 vs 概念建模

数据库范式约束（消除冗余、依赖主键）服务于存储一致性与事务正确性；本体建模服务于**可操作性与决策**。两者目标不同，直接把 3NF 数据模型照搬成本体会得到"技术正确但不可用"的本体——对象类型应以业务实体与工作流为锚点，而不是以存储表为锚点。

### 设计问题清单（8 问框架）

建模前应回答一组设计问题，其中与本项目最相关的两个：

- **用户会执行哪些操作（What actions will users take?）**——对象类型的边界应支撑 Action 与工作流，而不是仅仅描述数据。
- **这个属性到底属于哪个对象（What object type is this actually a property of?）**——属性归属判定是本体建模最高频的争议点：同一字段（如"客户等级"）挂在订单还是客户上，取决于它在哪些对象的上下文中被读写。

### 对象拆分启发式

- 一个对象类型应围绕**一个可单独操作的业务实体**：如果一组属性总是整体出现、整体更新，就不必拆成多个对象类型。
- 属性是否该独立成对象类型，取决于它**是否需要被单独链接、单独操作、单独追踪生命周期**（如"地址"通常只是属性；"案件"则是对象）。

### 有意去规范化（deliberate denormalization）

本体允许在对象上冗余存放派生/汇总类属性（如"客户当前未结案件数"），代价是管道维护与一致性风险，收益是操作查询与决策场景的直观性。这与数据库设计"消灭冗余"的取向相反，是**有意识的取舍**而非失误。Foundry 的原生手段包括 shared properties、derived/inline 属性与管道回写；本项目中的派生属性（查询时计算、不落盘）与该思路同源，但把"维护管道"换成了"查询时计算"。

### 案例线索

文中用车辆制造质量场景贯穿：`Vehicles` 对象类型关联周报 `Weekly Issue Reports`，展示"Recently Manufactured Vehicles with Identified Issues by Week"如何从对象+链接+属性推导出来；并用"Persons / Homes（卧室归属）"例子讨论属性归属判定。

## 与本项目的关联

- 对象类型拆分/属性归属的判定口径，可直接用于执法检查本体评审（如"检查记录"的属性 vs "被检查对象"的属性）。
- "有意去规范化"的取舍框架可用于评估我们后续是否需要把高频派生属性物化为存储列（当前派生属性是查询时计算，若性能不足可参考此思路转为管道回写）。

## 待办

- [ ] 网络可达时抓全文补充本档（curl 直连 / archive.org / r.jina.ai 均 522 或超时；可用 `https://fourthage.substack.com/api/v1/posts/practical-ontologies-and-how-to-build-d7e` 的 JSON API）
