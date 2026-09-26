# Session Memory（会话记忆）

本目录负责会话记忆的存储、跨进程合并、自动抽取调度与注入渲染，是会话记忆能力的单一权威入口。

## 定位与边界

- **仅本会话生效**：记忆文件存放在当前会话目录的 `memory/` 下（与 `workspace/`、`outputs/`、`uploads/` 平级），不跨会话、不跨用户共享；企业多人场景下不做任何 LLM 自动沉淀的共享口径（那类内容应由用户手动维护的项目/数据源级文档承载）。
- **人工治理通道**：记忆文件对模型可见、对用户可见，用户可在工作区面板直接查看、编辑、删除 `memory.md`，删除即失效。
- **失败降级**：抽取、解析、写入任何环节失败只记 warning 日志并跳过，不影响对话主流程。

## 文件与路径

| 路径 | 说明 |
|---|---|
| `<会话 user-data>/memory/memory.md` | 记忆文件，模型可见、用户可编辑可删除 |
| `<会话 user-data>/.memory.lock` | 跨进程合并锁文件（user-data 层，不出现在用户可见文件列表） |

条目行格式统一为 `- [类别] 结论（适用范围）`，范围后缀可省略；文档内顺序即新旧顺序（越靠后越新），淘汰与预算裁剪均保留最新。

## 模块结构

- `memory_parser.py`：纯函数层。条目解析、字段限长、去重、封顶、转义与预算裁剪，无 IO、无配置依赖。
- `conversation_memory_service.py`：服务层。路径定位、`fcntl.flock` 临界区内的「读-去重-淘汰-写」合并（tmp 文件 + `os.replace` 原子写）、LLM 自动抽取调度与注入渲染。

## 自动抽取触发点

1. **整轮正常结束**：`after_agent_turn_with_memory` 挂在 `TurnLifecycleHooks.after_turn`（仅 `outcome == "completed"` 触发），30 秒防抖合并连续轮次，同一会话连续调度只保留最新一次。
2. **上下文压缩即将丢弃消息**：压缩中间件经 `on_dropped_messages` 回调触发 `schedule_memory_extraction_now` 立即抽取，防止口径结论随消息丢弃而丢失。

抽取使用与摘要压缩同源的 LLM（`get_chat_data_llm`），输出 JSON 由服务端 `parse_llm_json` 解析校验，最多保留 5 条、逐字段限长。

## 注入

- 主图：`render_session_memory_block` 读文件→解析→按预算从最新往回裁剪→逐条转义，经 `build_unified_system_prompt(memory_block=...)` 拼入 system prompt 的「会话记忆」段。
- 子图：与主图同源渲染产物经 `SubagentSpec.memory_block` 传入，以「只读」段注入；子任务只引用、不写入记忆文件。

## 配置（`.env.example`「智能体上下文管理」节）

| 变量 | 默认 | 说明 |
|---|---|---|
| `CHAT_DATA_SESSION_MEMORY_ENABLED` | `true` | 总开关（抽取与注入共用） |
| `CHAT_DATA_SESSION_MEMORY_MAX_ITEMS` | `20` | 单会话条目上限，超出保留最新 |
| `CHAT_DATA_SESSION_MEMORY_INJECTION_MAX_TOKENS` | `1500` | 注入字符预算（1 token≈1 字符） |

## 测试

- `tests/unit/test_memory_parser.py`：纯函数层解析/规整/裁剪。
- `tests/unit/test_conversation_memory_service.py`：路径、flock 合并（含双进程并发）、防抖调度、抽取降级、注入渲染。
- `tests/unit/test_session_memory_injection.py`：system prompt 注入、子图只读段、压缩丢弃回调链。
