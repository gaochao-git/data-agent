# Complaint App (Python) — OpenCode-Style Minimal Architecture

按 OpenCode 的代码设计思路，落到投诉/举报办理系统里，最少需要这些组件与元素（模块级，不写代码）：

---

## 一、核心会话与消息层

- Session：会话元数据、状态、父子会话关系
- Message / Part：用户/assistant/工具调用/子任务等分段结构
- Message 序列化：用于上下文拼接与模型输入

对应 OpenCode：`session/*`, `message-v2.ts`

---

## 二、Agent 系统（调度层）

- Agent Registry：内置 + 自定义代理配置
- 主代理：build（主控） / plan（规划，可选）
- 子代理：summary / issue-analysis / verify
- Agent 权限配置：允许哪些工具

对应：`agent/agent.ts`

---

## 三、Prompt 构建器

- System Prompt 合成：provider prompt + agent prompt + 项目规则
- User Parts 解析：@agent、文件、subtask
- 注入规则：plan/build 切换提示

对应：`session/system.ts`, `session/prompt.ts`

---

## 四、Tool 系统（执行层）

- Tool Registry：内置工具 + 插件工具
- 工具定义：OCR、PDF抽取、写入、搜索、MCP 等
- 执行上下文：sessionID / messageID / permissions

对应：`tool/registry.ts`

---

## 五、子代理调度（TaskTool）

- 子会话创建
- 子代理执行
- 结果回填主会话

对应：`tool/task.ts`

---

## 六、Skill（配置化模板）

- Skill Loader：读取 `skills/` 下模板
- SkillTool：把模板变成工具调用流程
- 按省份/场景动态选择

对应：`tool/skill.ts`

---

## 七、权限系统

- Permission ruleset
- ask/allow/deny 判定
- 工具执行前检查

对应：`permission/next.ts`

---

## 八、上下文管理与压缩

- Overflow 判断（token 计数）
- Compaction（summary 压缩）
- Tool 输出裁剪（prune）

对应：`session/compaction.ts`

---

## 九、配置与扩展

- 配置层：全局/项目/环境变量/本地
- 插件加载：tools/skills/commands
- 模型与 provider 配置

对应：`config/config.ts`

---

## 十、接口层（对外入口）

- prompt API（会话入口）
- command API（子任务）
- 文件上传/附件解析入口

对应：`server/routes/*`

---

如需进一步细化为举报件场景的最小落地版本，可在此文档上继续扩展（例如：附件识别与分类、核查材料验证、结构化报告输出等）。
