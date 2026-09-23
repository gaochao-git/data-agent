# Agent Tools

模型可见工具集。每个模块暴露 `create_*_tool(workspace_runtime)` 工厂，工具只做参数校验、
调用工作区运行时/领域服务和输出格式化，不直接触达文件系统或沙箱。

## 工具清单

| 工具 | 模块 | 说明 |
|---|---|---|
| `file_read`/`file_write`/`file_edit`/`file_move`/`file_copy`/`file_delete`/`dir_create`/`dir_list`/`file_glob`/`file_grep` | `filesystem.py` | 会话工作区文件操作（统一由 `create_filesystem_tools` 构建） |
| `read_image` | `read_image.py` | 图片读入视觉上下文（下一轮注入） |
| `parse_document` | `parse_document.py` | 上传文件解析为 Markdown 写入工作区 |
| `bash_run` | `bash_run.py` | 受限命令执行；**由 `runtime_options.bash_enabled` 控制注册（默认关）** |
| `officecli_run` | `officecli_run.py` | OfficeCLI 文档解析/编辑（argv 直 execve，非通用执行器） |
| `present_artifacts` | `present_artifacts.py` | 展示 outputs 最终产物 |
| `data_analyzing_tool` | `data_analyzing.py` | 语义分析统一工具（`action` + `action_payload` 分派：`validate_dsl`/`validate_dashboard` DSL 与 dashboard_spec 校验（不执行）、`execute_query` 语义模型查询（DSL/只读 SQL 二选一执行）、`check_yaml` Ossie YAML 交付前自检（schema/结构/物理/质量，与导入接口同口径，只读）、`list_execution_targets` 实时获取当前用户可用执行对象清单（只读）） |
| `ask_user_question` | `ask_user_question.py` | 向用户追问 |
| `calculator`/`current_time` | 同名模块 | 基础工具 |

## 约定

- 路径参数只接受规范形式 `/mnt/user-data/{workspace,outputs,uploads}/...` 与只读
  `/mnt/skills/<name>/...`；docstring 是模型照抄的输入模板，示例必须用规范形式。
- 工具错误分两类返回：参数/路径类给可纠正消息；意外异常给通用文案并记录完整堆栈。
- 新增工具需同步：注册表登记（`tools/registry.py` 的 `TOOL_SPECS`，含 `provides` 工具名与子智能体可见性策略）、前端展示类型（web-vue）、单元测试。
