# community/ — 社区实战文章归档

本目录收录 **非官方** 的 Palantir Ontology 社区文章本地参考副本，用于补充官方文档（`../` 各目录）没有覆盖的实战建模经验与第三方视角。

## 归档规则

- 每篇头部标注：来源 URL、抓取日期、版权归属。
- **版权属原作者/机构，仅供内部学习参考，不得再分发。**
- 社区内容是二手解读，含作者演绎与厂商营销；与官方文档（`../ontology/`、`../object-link-types/` 等）冲突时，**以官方文档为准**。
- 已知冲突示例：Coomia 深度文 §2.4 称派生属性可依赖其他派生属性形成 DAG 自动重算，而官方 `../object-link-types/derived-properties.md` 明确 *“A derived property cannot take its value from another derived property”*，以官方为准。

## 篇目

| 文件 | 来源 | 形态 | 主题 |
| --- | --- | --- | --- |
| coomia-ontology-deep-dive.md | Coomia（2025-03-20） | 全文副本 | 全景深度文：哲学源流、三支柱、与 ER/UML/OWL 对比、生命周期、锁定效应（含厂商营销，需甄别） |
| fourthage-practical-ontologies-part2.md | Fourth Age（2025-03-13） | 要点导读卡 | 本体建模实战：概念建模 vs 数据约束、对象拆分与属性归属启发式、有意去规范化 |
| fourthage-practical-ontologies-part3.md | Fourth Age（2025-03-21） | 要点导读卡 | 对象 / 事件 / 时序三类构件的区分与建模取舍（事件对象 vs 时序属性 vs 聚合视图） |
| fourthage-practical-ontologies-part4.md | Fourth Age（2025-08-30） | 要点导读卡 | Actions 操作化本体：价值驱动、组织流程、模式与反模式（最小参数、禁冗余 Action 等） |

形态说明：**全文副本** = 原文完整本地存档；**要点导读卡** = 原文站点当前网络不可直连（Substack 域名阻断，直连/archive.org/公共代理均超时或 522），仅存要点导读，文件内"待办"节记录了网络可达后的补全文路径（Substack JSON API）。
