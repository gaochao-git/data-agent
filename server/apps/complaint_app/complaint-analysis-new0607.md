目前的举报事项分析接口是/api/complants/complaint-analysis, 这个接口暂时不动。

写一个新的接口/api/complants/complaint-analysis-v2；
1、新的接口的入参和当前的接口保持一致。
2、出参是需要按照SSE event给出。event事件包括：Start、Reasoning、Acting、Answer、Reference、Done。

重新设计一套新的代码结构和流程：
主流程为：先撰写举报摘要，再对摘要中的举报事项分析，最后给出调阅材料清单及核实思路。

## 撰写举报摘要：
- 调用大模型，输入的message : [{role: system, content: (load from local system.md，md后续我来填充), role: user, content: (summary.md) }]
- summary.md 包括2部分：
  - 程序提供的信息，包括：1）举报件原文；2）举报人；3）被举报机构，基于占位符实现，由程序提供。
  - 分析摘要的技能（来自skill1.md，指导如何分析和生成摘要，这个技能内容空着，我后续来填写）。这个技能会要求输出的是一段自然语言内容（markdown格式）

## 举报事项分析：
- 调用大模型，输入的message：[{role: system, content: (同摘要), role: user, content: (analysis.md) }]
- analysis.md 包括2部分：
  - 1. 程序提供的信息，包括：1）举报件原文；2）摘要的输出内容；
  - 2. 举报事项分析的技能（来自skill2.md，指导如何分析举报事项，这个技能内容空着，我后续来填写）。
    - 2.1 这个技能会要求输出的是一段自然语言内容（markdown格式）

## 生成调阅材料及核实思路：
- 调用大模型，输入的message：[{role: system, content: (同摘要), role: user, content: (final.md) }]
- final.md 包括2部分：
  - 1. 程序提供的信息，举报事项的分析的输出
  - 2. 生成材料和思路的思路（指导如何分析举报事项，这个技能内容空着，我后续来填写）
