# 插件示例包

仓库内置的资源型插件示例，源码目录结构即打包结构：

```text
plugins/<plugin-name>/
├── plugin.json              # manifest（name/version/skills/mcp_servers）
└── skills/<skill-name>/
    └── SKILL.md             # 技能入口（可带 references/ 子文档）
```

## fault-analysis 示例的 MCP 接入

`fault-analysis/plugin.json` 声明了 2 个 `streamable_http` 传输的 MCP server，
对应本仓库自维护的 `mcp_servers/`（MCP 协议 2026-07-28 世代，见 `mcp_servers/README.md`）：

| 声明 id | 上游服务 | 端口 | 工具 |
|---|---|---|---|
| fault-db | mcp_servers/db_query_server | 3001 | MySQL 只读诊断（`db_query` 单工具，action 路由 8 类能力） |
| fault-ssh | mcp_servers/ssh_exec_server | 3002 | Linux 只读巡检（`ssh_exec` 单工具，action 路由 12 类能力） |

注意事项：

- server 未启动时 agent 装配 fail-soft（告警跳过该 server，不影响其他工具）；
- `tool_labels`（可选）：原始工具名 → 用户可见中文友好名的映射，作用于前端工具卡片
  标题与 MCP 工具检索目录（展示名进检索域、随检索摘要下发）；未配置的工具回退展示限定名；
- `fault-ssh` 上游按 `mcp_servers/common/config.py` 使用宿主 SSH 私钥连接目标机
  （`ip` 由模型在工具调用时指定）；目标机 22 端口不可达时返回结构化错误，由 agent 如实转述；
- 本机访问 `127.0.0.1` 若被系统代理劫持（httpx 502），启动后端时设置
  `NO_PROXY=127.0.0.1,localhost`；
- url/端口按部署环境调整；上游数据源（MySQL/目标机）可达性决定工具返回内容，
  不可达时工具返回错误文本，由 agent 如实转述。

## 打包与安装

```bash
cd plugins
zip -r /tmp/fault-analysis.zip fault-analysis
curl -X POST -H "X-Service-Token: <token>" -F "file=@/tmp/fault-analysis.zip" \
  http://127.0.0.1:5001/api/chatdata/admin/plugins/install
```

安装后经 `PUT /admin/plugins/<name>/enabled` 启停、`DELETE /admin/plugins/<name>` 卸载；
插件技能对新会话生效（进入会话 runtime-skills 快照），声明了 `mcp_servers` 的插件
同时参与 agent 装配的 MCP 工具枚举（工具名 `mcp_<id>_<tool>`）。校验规则与生效
链路见 `server/apps/chat_data_app/agent/plugins/README.md`。

