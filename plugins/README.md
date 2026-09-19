# 插件示例包

仓库内置的资源型插件示例，源码目录结构即打包结构：

```text
plugins/<plugin-name>/
├── plugin.json              # manifest（name/version/skills/mcp_servers）
└── skills/<skill-name>/
    └── SKILL.md             # 技能入口（可带 references/ 子文档）
```

## fault-analysis 示例的 MCP 接入

`fault-analysis/plugin.json` 声明了 5 个 `streamable_http` 传输的 MCP server，
源自 `fullstack-langgraph/mcp_servers`（本地常驻服务，`manage.py start` 启动）：

| 声明 id | 上游服务 | 端口 | 工具 |
|---|---|---|---|
| fault-db | db_query_server | 3001 | MySQL 只读诊断（连接/锁/复制/事务/慢查询等 8 个） |
| fault-ssh | ssh_exec_server | 3002 | Linux 只读巡检（磁盘/进程/网络/日志/快照等 12 个固化命令） |
| fault-es | es_search_server | 3003 | Elasticsearch 日志检索（3 个） |
| fault-zabbix | zabbix_monitor_server | 3004 | Zabbix 监控指标（2 个） |
| fault-web | web_search_server | 3008 | 联网搜索与网页抓取（2 个） |

注意事项：

- server 未启动时 agent 装配 fail-soft（告警跳过该 server，不影响其他工具）；
- `fault-ssh` 上游按 `config.yaml` 使用宿主 SSH 私钥连接目标机（`ip` 由模型在
  工具调用时指定）；目标机 22 端口不可达时返回结构化错误，由 agent 如实转述；
- 本机访问 `127.0.0.1` 若被系统代理劫持（httpx 502），启动后端时设置
  `NO_PROXY=127.0.0.1,localhost`；
- url/端口按部署环境调整；上游数据源（MySQL/ES/Zabbix）可达性决定工具返回内容，
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

