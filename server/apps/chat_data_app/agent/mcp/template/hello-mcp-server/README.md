# Hello MCP Server 模板

最小可运行的 MCP Server 示例，基于官方 `mcp>=2.2.0` 的 `MCPServer`，
使用 **MCP 2026-07-28 + streamable_http**，包含两个工具示例：

- `hello(name)`：返回一句问候；
- `add(a, b)`：返回两个整数之和。

`mcp_auth.py` 单独承载 Bearer Token 鉴权，`server.py` 只负责组装与启动。

> 运行环境：**Python 3.10+**（官方 `mcp 2.x` 的硬性要求；本仓库使用 Python 3.12）。

## 环境

- Python **>= 3.10**（`mcp==2.2.x` 的官方要求）
- 安装依赖：

```bash
cd hello-mcp-server
python -m pip install -r requirements.txt
```

## 启动

后台运行（推荐）：

```bash
nohup env MCP_AUTH_TOKEN=change-me MCP_HOST=0.0.0.0 MCP_PORT=8099 python server.py > mcp-server.log 2>&1 &
```

前台调试：

```bash
MCP_AUTH_TOKEN=change-me MCP_HOST=0.0.0.0 MCP_PORT=8099 python server.py
```

默认地址：

```text
http://127.0.0.1:8099/mcp
```

参数：

```bash
MCP_AUTH_TOKEN=change-me   # 必填
MCP_HOST=0.0.0.0          # 监听所有网卡
MCP_PORT=8099             # 端口
```

查看日志、端口和停止：

```bash
tail -f mcp-server.log
lsof -nP -iTCP:8099 -sTCP:LISTEN
kill $(lsof -t -iTCP:8099 -sTCP:LISTEN)
```

> `nohup` 只保证终端关闭后进程继续运行，不会崩溃自动重启。
> 生产建议用 systemd、supervisor 或容器平台托管。

## 接入插件

在插件的 `plugin.json` 中声明：

```json
{
  "mcp_servers": [
    {
      "id": "hello-http",
      "transport": "streamable_http",
      "url": "https://your-host/mcp",
      "headers": {
        "Authorization": "Bearer <YOUR_TOKEN>"
      },
      "enabled": true
    }
  ]
}
```

本地联调可使用 `http://127.0.0.1:8099/mcp`；生产要求 HTTPS（本地回环例外）。

## 新版协议说明

- 只使用 `streamable_http`；
- 客户端由 MCP SDK 处理 2026-07-28 的 `_meta`、`Mcp-Method`、`Mcp-Name` 等协议细节；
- 不要改成 stdio / sse。

## Bearer Token

鉴权实现位于 `mcp_auth.py`，使用 MCP SDK 原生鉴权（`TokenVerifier` + `AuthSettings`），
不需要额外起 uvicorn，也不需要手写 ASGI 中间件：

```bash
MCP_AUTH_TOKEN=change-me python server.py
```

客户端在插件 `mcp_servers[].headers` 中带同一个 token：

```json
{
  "Authorization": "Bearer change-me"
}
```

- 未带 token 或 token 不匹配时返回 `401 Unauthorized`；
- 生产请使用随机长 token，并通过 HTTPS 传输；
- 模板只演示静态 token。
