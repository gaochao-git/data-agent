# ChatData Load Test

智能问数压测目录。默认脚本直接压后端真实 SSE 接口：

```text
POST /api/chatdata/conversations
POST /api/chatdata/conversations/{conversation_id}/completion
```

脚本会自动完成：

- 注入 `X-Service-Token`、`X-User-Id`、`X-Username` 服务转发认证头
- `/api/chatdata/conversations` 创建会话
- 并发请求 completion
- 消费 SSE 流并统计成功率、首包耗时、总耗时、分位耗时
- 结束后输出普通摘要和 Markdown Summary 表格

## 智能问数压测

```bash
cd /Users/gaochao/work/git_repo/fxcjg

/Users/gaochao/work/miniconda3/envs/fxcjg/bin/python \
server/apps/chat_data_app/evaluate/load_test/chat_data_loadtest.py \
--base-url http://127.0.0.1:5001 \
--agent-id chat_data \
--requests 8 \
--concurrency 4 \
--user-id 10001 \
--username 5Y6L5rWL55So5oi3 \
--datasource-id ds_20260116115851_95505 \
--semantic-config-id sc_order_operating_baseline_v1 \
--mode query \
--queries-file server/apps/chat_data_app/evaluate/load_test/queries/order_operating.json \
--request-timeout 180
```

当前示例对应：

- 数据源：`hive-localhost` / `ds_20260116115851_95505`
- 指标集：`订单分析基线` / `sc_order_operating_baseline_v1`
- 服务令牌：默认读取当前进程环境变量 `SERVICE_AUTH_TOKEN`
- 模型：默认读取当前进程环境变量 `CHAT_DATA_MODEL` 第一项中的模型名

## 并发口径

- `--requests`：总请求数。
- `--concurrency`：并发 worker 数。
- 默认每个请求新建一个会话。
- 加 `--reuse-conversation` 后，每个 worker 复用一个会话。
- 这是并发 worker 压测，不是固定 QPS 压测。

## 输出指标

- `success_rate`：成功率。
- `throughput`：整体吞吐，单位 `req/s`。
- `first_event`：收到第一行 SSE 数据的耗时，近似首包/首 token 耗时，输出 `avg/p50/p90/p99`。
- `total_time`：单请求完整消费 SSE 流的总耗时，输出 `avg/p50/p95/p99`。
- `Markdown Summary`：脚本结束后额外输出一行 Markdown 表格，便于复制汇总。

## Agent ID

- `chat_data`：统一的数据查询、分析和图表处理智能体。
- `chat_data_sql_assist`：SQL 辅助。
