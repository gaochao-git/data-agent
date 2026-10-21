# agent/plugins — 技能包插件

把「技能 + MCP server 声明」打包成可安装的资源型插件，安装到
``CHAT_DATA_HOME/plugins/<name>/`` 后由宿主在装配/快照构建时自动发现。

## 目录布局

```
CHAT_DATA_HOME/plugins/<name>/
  plugin.json                 # manifest（必须）
  skills/<skill>/SKILL.md     # manifest.skills 声明的技能目录
```

磁盘只存内容；启停不是磁盘标记，而以 DB 表 `chat_data_plugin` 为唯一真值
（`daos/plugin_dao.py`）。历史版本的 `.disabled` 文件已废弃，不再参与判定。

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

启停读取链路：`loader.refresh_plugin_state()` 从 DB 拉启用名集合写入进程内快照
`_enabled_state`，装配/技能快照/管理列表三个入口都先 `await` 它。DB 读失败按
fail-closed 处理（快照清空、当轮全部按禁用，不兜底旧值），因此**表不可用 = 插件静默
不生效**，排障先看 `刷新插件启停状态失败` 日志。

## 管理入口

前端：后台管理 → 插件（`web-vue/src/views/chat-data/components/PluginAdminPanel.vue`，
由 `ChatDataAdminManager.vue` 挂载），支持列表分页、zip 安装、启停开关、卸载二次确认。

接口（`routers/plugins.py`，service token 保护）：

- `GET  /api/chatdata/admin/plugins`：列出已安装插件（含审计字段，分页 limit∈[1,200]）；
- `POST /api/chatdata/admin/plugins/install`：上传 zip 安装（≤50MB，manifest 校验、
  zip-slip/符号链接/解压总量防护，同名拒绝）；
- `PUT  /api/chatdata/admin/plugins/{plugin_id}/enabled`：启用/禁用（写 DB
  `enabled_flag`，立即影响新会话快照与新装配）；
- `DELETE /api/chatdata/admin/plugins/{plugin_id}`：卸载（删磁盘目录 + 软删 DB 行）。

安装先解包落盘再写 DB，DB 写失败回滚目录；卸载先删盘后软删 DB。重装按 `plugin_name`
复活同一行（单列 UNIQUE + `delete_flag` 翻转），可无限次装卸循环。磁盘有目录而 DB 无行
的"半装态"插件，在列表里审计字段留空，触发一次启停即自愈补行。

## 上线前置

- 建表：`chat_data_plugin` 的 DDL 在 `utils/schema.py` 的
  `MYSQL_CHAT_DATA_PLUGIN_TABLE`，**需在业务库手工执行**（生产代码无自动建表入口）。
- 迁移：历史版本（磁盘 `.disabled` 时代）遗留的插件目录，执行一次
  `server/scripts/migrate_plugins_to_db.py` 把启停状态导入 DB。

## 明确不做（v1）

- 不做进程内 Python 工具热插拔；工具扩展统一走 MCP（见 `agent/mcp/`）。
- 不做插件市场与版本管理；升级 = 卸载后重装新版本 zip。
- 插件不做运行时热更新到进行中的会话：安装/启停只对新会话与下一次装配生效。

## MCP server 准入状态（管理列表）

`GET /api/chatdata/admin/plugins` 返回的每个 `mcp_servers[]` 条目附带
`admission: {ok, errors[], warnings[]}`（由 `services/plugin_service._mcp_server_admission_view`
按 `mcp/admission.py` 同口径计算），管理页据此对未通过准入的 server 标红并展示原因；
安装时未通过准入的插件会被直接拒绝（不落盘）。
