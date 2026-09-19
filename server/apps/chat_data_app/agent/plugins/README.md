# agent/plugins — 技能包插件

把「技能 + MCP server 声明」打包成可安装的资源型插件，安装到
``CHAT_DATA_HOME/plugins/<name>/`` 后由宿主在装配/快照构建时自动发现。

## 目录布局

```
CHAT_DATA_HOME/plugins/<name>/
  plugin.json                 # manifest（必须）
  skills/<skill>/SKILL.md     # manifest.skills 声明的技能目录
  .disabled                   # 禁用标记（存在即禁用）
```

## plugin.json schema

```json
{
  "name": "ops-readonly",
  "version": "1.0.0",
  "display_name": "智能运维只读插件",
  "description": "提供故障分析技能与只读 MCP 工具",
  "skills": ["fault-analysis"],
  "mcp_servers": [
    {"id": "fs-ro", "transport": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp/ro"]}
  ]
}
```

- `name`：`^[a-z][a-z0-9_-]{1,63}$`，同时是磁盘目录名与管理 API 资源 id；
- `skills` / `mcp_servers` 可任选其一或同时声明；
- 未知字段忽略（前向兼容）。

## 生效链路

| 声明 | 消费点 | 生效时机 |
| --- | --- | --- |
| `skills` | `loader.enabled_plugin_skill_sources()` → `skill_service.list_runtime_skill_sources_async()` → 会话 runtime-skills 快照 | 新会话/下一次快照重建（技能指纹变更自动触发）；与系统/用户技能同名时跳过并告警 |
| `mcp_servers` | `loader.enabled_plugin_mcp_servers()` → `mcp/config.load_mcp_server_configs()` → agent 装配 | 下一次 agent 装配（新运行） |

## 管理接口（`routers/plugins.py`，service token 保护）

- `GET  /api/chatdata/admin/plugins`：列出已安装插件；
- `POST /api/chatdata/admin/plugins/install`：上传 zip 安装（≤50MB，manifest 校验、
  zip-slip/符号链接/解压总量防护，同名拒绝）；
- `PUT  /api/chatdata/admin/plugins/{plugin_id}/enabled`：启用/禁用（`.disabled` 标记）；
- `DELETE /api/chatdata/admin/plugins/{plugin_id}`：卸载（删除目录）。

## 明确不做（v1）

- 不做进程内 Python 工具热插拔；工具扩展统一走 MCP（见 `agent/mcp/`）。
- 不做插件市场与版本管理；升级 = 卸载后重装新版本 zip。
- 不提供前端管理页面（接口先行，页面后续按后台管理手册补齐）。
