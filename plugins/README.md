# 插件示例包

仓库内置的资源型插件示例，源码目录结构即打包结构：

```text
plugins/<plugin-name>/
├── plugin.json              # manifest（name/version/skills/mcp_servers）
└── skills/<skill-name>/
    └── SKILL.md             # 技能入口（可带 references/ 子文档）
```

## 打包与安装

```bash
cd plugins
zip -r /tmp/fault-analysis.zip fault-analysis
curl -X POST -H "X-Service-Token: <token>" -F "file=@/tmp/fault-analysis.zip" \
  http://127.0.0.1:5001/api/chatdata/admin/plugins/install
```

安装后经 `PUT /admin/plugins/<name>/enabled` 启停、`DELETE /admin/plugins/<name>` 卸载；
插件技能对新会话生效（进入会话 runtime-skills 快照），声明了 `mcp_servers` 的插件
同时参与 agent 装配的 MCP 工具枚举。校验规则与生效链路见
`server/apps/chat_data_app/agent/plugins/README.md`。
