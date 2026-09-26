# Agent Workspace（会话工作区虚拟文件系统）

模型可见的会话级虚拟文件系统。核心原则：**模型永远只看到规范虚拟路径，真实存储按后端对接**。

## 规范路径（严进严出，唯一口径）

- 读写域：`/mnt/user-data/{workspace,outputs,uploads}/...`
- 技能只读：`/mnt/skills/<name>/...`
- 其它形式（裸别名 `/workspace/...`、相对形式 `workspace/...`、`./` 前缀）一律被
  `workspace_path_policy.WORKSPACE_PATH_POLICY.parse` 拒绝，错误消息携带规范形式要求，模型可自纠。
- 输出统一规范前缀：`to_visible_path`（runtime.py）产出 `/mnt/user-data/...`；提示词、工具
  docstring、错误消息、技能内容均以此口径教学。
- 权限分层：`uploads/` 模型只读；`workspace/semantic/`、`outputs/query_result/`、
  `outputs/.tool-offload/` 为系统管理只读/隐藏区（`assert_writable` 强制）。

## 模块

| 文件 | 职责 |
|---|---|
| `workspace_path_policy.py` | 路径解析与权限策略（后端无关的单一权威，含 `USER_DATA_VIRTUAL_ROOT`） |
| `runtime.py` | 后端协议（`WorkspaceBackend` / `ExecutableWorkspaceBackend`）、`ConversationWorkspaceRuntime` 门面、`resolve_workspace_path`、`WorkspacePathMapping`（虚拟↔物理翻译）、`create_workspace_backend` 工厂 |
| `workspace_local_backend.py` | local 后端：路径校验后的宿主机文件系统实现 |
| `workspace_sandbox_backend.py` | sandbox 后端：OpenSandbox 远程容器适配 |
| `workspace_sandbox_paths.py` | sandbox 路径根、可见性映射、managed/unmanaged 谓词（原 workspace_execution.py） |
| `workspace_sandbox_api.py` | OpenSandbox SDK 导入、Files API 原语、命令执行、`truncate_output`（原 workspace_execution.py） |
| `workspace_sync.py` | manifest 指纹、会话初始化上传、持久化回写、working trees 同步（原 workspace_execution.py） |
| `workspace_session.py` | OpenSandbox 会话缓存/生命周期 + prepare 专属的 env/network/connection 配置（原 workspace_execution.py） |
| `workspace_ops.py` | 模型面文件操作（read/list/create/delete/move/copy/write）与 artifact/office 渲染（原 workspace_execution.py） |
| `workspace_search.py` | rg 文本搜索 + glob（原 workspace_execution.py） |
| `officecli_adapter.py` | OfficeCLI 参数解析、路径值提取、argv 翻译 |
| `workspace_special_services.py` | 语义模型物料化、工具结果写入、产物加载、Office 预览四个 special service |
| `workspace_text_budget.py` | 分页读取的行字符预算 |
| `uploaded_file_parser.py` | 上传文件 → Markdown 解析 |
| `artifact_registry.py` | outputs 产物识别（chart/dashboard/skill/markdown） |

## 路径映射机制（local 后端）

- **文件工具**：结构化解析——`resolve_workspace_path` 剥规范前缀 → 校验（`..`/symlink/越界/可见根）
  → 拼物理路径；返回值用相对路径现拼规范形式，物理路径不进模型视野。
- **bash/officecli 命令**：文本翻译——`WorkspacePathMapping.translate` 把命令文本里的虚拟路径
  翻成物理路径执行；`mask` 把输出里的物理路径反向遮蔽。物理根含 `/mnt/user-data/` 会被构造期
  拒绝（防替换自引用膨胀）。
- `_ALIAS_RE` 裸别名翻译**仅存在于 bash 文本通道**：是防宿主机同名目录被访问的纵深防御 +
  容错层，不是输入接口（结构化入口一律严格拒绝）。

## 安全边界定位（重要）

- **路径校验是护栏，不是安全墙**：它防模型无意越界、给直白注入制造审计记录，防不住脚本/
  编码/变量拼接的蓄意绕过。
- 真正的安全边界：sandbox 后端（容器隔离）、env 白名单裁剪、文件 mode 只读区、网络白名单、
  全量事件审计。
- `local 后端 = 可信部署前提`；不可信流量必须使用 sandbox 后端。`bash_enabled=True` 且
  `local` 的组合属于高危配置（见上级 README）。

## 会话编排

会话文件列表/文本/原始字节读取、Office 预览、DAO 归属校验等会话级 API 位于
`services/agent_run/conversation_workspace.py`（本包 `runtime.py` 的符号在该模块 re-export 兼容）。
