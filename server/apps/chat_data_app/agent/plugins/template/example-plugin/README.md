# ChatData 插件模板

## 1. 修改插件元信息

编辑 `plugin.json`：

- `name`：插件唯一标识，必须匹配 `^[a-z][a-z0-9_-]{1,63}$`，同时作为安装目录名；
- `version`：非空字符串；
- `display_name` / `description`：管理页展示；
- `skills`：声明 `skills/<技能名>/SKILL.md`；
- `mcp_servers`：可选，声明 MCP server（见下方示例）。

## 2. 技能

技能目录必须与 `skills` 中的名字一致：

```text
skills/example-skill/SKILL.md
skills/example-skill/references/
skills/example-skill/assets/
```

`SKILL.md` 是入口正文，必须带 frontmatter；`references/` 放详细资料，`assets/` 放模板/素材，其他目录不允许：

```markdown
---
name: example-skill
title: 示例技能
description: 一句话说明
metadata:
  category: custom
  output_type: text
---
```

正文建议控制在 32KB 内；详细资料放 `references/`，按技能正文指示读取。

## 3. MCP server（可选）

模板的 `plugin.json` 已内置一个 `enabled: false` 的 streamable_http 示例；
修改 `url` / `headers` 后把 `enabled` 改为 `true`。示例：

```json
{
  "id": "demo-http",
  "transport": "streamable_http",
  "url": "https://mcp.example.com/mcp",
  "enabled": false,
  "headers": {
    "Authorization": "Bearer <token>"
  },
  "tool_approvals": {
    "read_data": "allow",
    "write_data": "ask"
  },
  "tool_labels": {
    "read_data": "读取数据"
  }
}
```

- `transport`：只支持 `streamable_http`；
- `enabled`：`false` 表示该 server 只保留声明、不参与装配；模板默认 `false`，改成 `true` 才会连接；
- `tool_approvals`：每工具 `ask` 或 `allow`，未配置默认 `ask`；
- `tool_labels`：审批卡/工具卡展示名；
- 安装时会做 MCP 准入检查，未通过会拒绝安装。

### MCP Server 必须按新版协议实现

插件只负责声明 MCP Server，**不在插件包内实现 Server**；Server 需独立部署。
当前只接受 **MCP 2026-07-28 + streamable_http**：

```text
POST https://your-host/mcp
MCP-Protocol-Version: 2026-07-28
Mcp-Method: server/discover | tools/list | tools/call
```

新版协议要求：

- 不再使用 `initialize` / `initialized` 握手；
- 不再使用 `Mcp-Session-Id`；
- 每个请求在 `params._meta` 中携带：

```json
{
  "io.modelcontextprotocol/protocolVersion": "2026-07-28",
  "io.modelcontextprotocol/clientCapabilities": {}
}
```

- `tools/call` 额外带 `Mcp-Name: <tool_name>`；
- 用 `server/discover` 发现能力；
- 列表响应可带 `ttlMs` / `cacheScope`；
- 响应使用 `resultType` 等新版字段。

语言 SDK 现状：

- Python：官方 `mcp>=2.2.0`，使用 `mcp.server.mcpserver.MCPServer`；
- Java：官方 Java SDK `2.2.0-M1` 已有 modern server（server-only，modern client 尚未支持），生产接入需评估里程碑版本风险；
- 其他语言/框架需确认已实现 2026-07-28 协议，不能只实现旧版 Streamable HTTP。

生产要求 HTTPS；本地回环地址例外。MCP Server 侧必须自行实现 Bearer Token 鉴权
和网络访问限制。

## 4. 打包安装

把 `plugin.json`、`skills/` 等放在 zip 根目录（或唯一顶层目录）后上传：

```text
example-plugin/
  plugin.json
  skills/example-skill/SKILL.md
  skills/example-skill/references/
  skills/example-skill/assets/
```

安装后对新会话与新装配生效。
