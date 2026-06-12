1) 用户输入进入会话

  - 用户在 CLI/TUI/Web 发起消息 → 后端创建用户消息与 parts → 进入 SessionPrompt.prompt()
    代码入口：packages/opencode/src/session/prompt.ts

  2) 会话级基础初始化

  - SessionPrompt.prompt 会：
      - 读取并创建 Session（若需要）
      - 将用户输入解析成 MessageV2 parts（文本/文件/@agent/command/subtask 等）
      - 若用户指定 tools（兼容旧接口），会转换成 session 权限
        代码：packages/opencode/src/session/prompt.ts

  3) 代理初始化（一次性、惰性）

  - 当 prompt 处理中需要 agent 时，会调用 Agent.get / Agent.list / Agent.defaultAgent，触发 Agent.state() 初始化：
      - Config.get() 多层配置合并（远端→全局→项目→.opencode/agents/*.md → 命令行 flags）
        packages/opencode/src/config/config.ts
      - 构建内置代理（build/plan/general/explore/compaction/title/summary）
        packages/opencode/src/agent/agent.ts
      - 合并用户自定义 agent 配置（opencode.json / .opencode/agents/*.md）
      - 兜底权限规则（Truncate.DIR/GLOB）
        代码：packages/opencode/src/agent/agent.ts

  4) 系统提示词（System Prompt）拼装

  - LLM.stream() 会合并：
      - provider 默认系统提示
      - agent 专属 prompt
      - 用户系统规则（AGENTS.md / CLAUDE.md / config.instructions）
      - 当前会话 system 字段
        代码：packages/opencode/src/session/llm.ts + packages/opencode/src/session/system.ts

  5) 工具初始化（ToolRegistry）

  - 在 LLM.stream() 中调用 ToolRegistry.tools()：
      - 注册内置工具：bash/read/edit/write/task/webfetch/websearch/...
      - 加载 .opencode/tools/plugins 提供的自定义工具
      - 根据模型/配置启用或禁用某些工具
        代码：packages/opencode/src/tool/registry.ts

  6) 权限体系加载

  - 每个 agent 都有 permission ruleset
  - 每次工具执行前 PermissionNext.ask 决定 allow/deny/ask
    代码：packages/opencode/src/permission/next.ts

  7) 运行核心循环（SessionPrompt.loop）

  - SessionPrompt.loop() 驱动会话：
      - 如果有 subtask/compaction 等待处理，先执行
      - 否则进入 LLM 生成 → 工具调用 → 再生成
        代码：packages/opencode/src/session/prompt.ts

  8) 子代理（Subagent）调用流程

  - 用户 @agent → 被解析为 AgentPart → LLM 触发 task 工具
  - TaskTool 创建子会话，切换到目标 subagent，运行 SessionPrompt.prompt()
  - 子会话完成后把输出作为 tool result 返回主会话
    代码：packages/opencode/src/tool/task.ts

  9) Skill（技能）的执行

  - 技能在这个项目里是通过 SkillTool 实现：
      - LLM 调用 skill 工具 → 读取本地 SKILL.md
      - 决定执行步骤（可能调用 bash、read 等）
        代码：packages/opencode/src/tool/skill.ts
  - 在你当前环境中，技能由 AGENTS.md 指定（你给的列表），属于“工具级扩展”，不是 agent 本身。

  10) 输出与最终消息

  - 工具执行结果写回 MessageV2.ToolPart
  - 生成最终 assistant 回复并完成 loop
