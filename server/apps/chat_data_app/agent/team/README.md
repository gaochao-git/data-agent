# Persistent Agent Team (MVP)

实验性持久 teammate 与共享任务板。

## 能力

- `spawn_teammate`：创建具名 teammate，并执行第一轮任务
- `send_message`：在同一个 teammate thread 上继续多轮对话
- `list_agents`：查看 roster
- `team_task_create/list/get/update`：共享任务板，revision CAS

## 持久化

- teammate 与 task 状态：`CHAT_DATA_HOME/agent_teams/<conversation_id>.json`，原子写
- teammate 对话：复用 LangGraph checkpointer，thread id 为
  `team:<conversation_id>:<name>`

## 边界（MVP）

- 单进程；不做跨进程锁
- 同一 teammate 的 turn 串行执行
- 不做 Team UI、roster 广播、cold resume 管理
- teammate 不注册 team 工具，避免嵌套 Team
- feature flag：`CHAT_DATA_PERSISTENT_TEAM_ENABLED`，默认关闭

## 测试

```bash
cd server
pytest apps/chat_data_app/tests/unit/test_team_service.py
```
