# Agent Tools

模型可见工具集。每个模块暴露 `create_*_tool(workspace_runtime)` 工厂，工具只做参数校验、
调用工作区运行时/领域服务和输出格式化，不直接触达文件系统或沙箱。

## 工具清单

| 工具 | 模块 | 说明 |
|---|---|---|
| `file_read`/`file_write`/`file_edit`/`file_move`/`file_copy`/`file_delete`/`dir_create`/`dir_list`/`file_glob`/`file_grep` | `filesystem.py` | 会话工作区文件操作（统一由 `create_filesystem_tools` 构建） |
| `read_image` | `read_image.py` | 图片读入视觉上下文（下一轮注入） |
| `parse_document` | `parse_document.py` | 上传文件解析为 Markdown 写入工作区 |
| `bash_run` | `bash_run.py` | 受限命令执行；**由 `ToolDefinition.enabled` 控制注册（默认关）** |
| `officecli_run` | `officecli_run.py` | OfficeCLI 文档解析/编辑（argv 直 execve，非通用执行器） |
| `present_artifacts` | `present_artifacts.py` | 展示 outputs 最终产物 |
| `data_analyzing_tool` | `data_analyzing.py` | 语义分析统一工具（`action` + `action_payload` 分派：`validate_dsl`/`validate_dashboard` DSL 与 dashboard_spec 校验（不执行）、`execute_query` 语义模型查询（DSL/只读 SQL 二选一执行）、`check_yaml` Ossie YAML 交付前自检（schema/结构/物理/质量，与导入接口同口径，只读）、`list_execution_targets` 实时获取当前用户可用执行对象清单（只读））；**语义模式专用** |
| `ontology_tool` | `ontology_tool.py` | 本体对象/关系/Action 查询工具（`list_object_types`/`locate_object_type`/`search_around` 导航，`find_objects`/`aggregate_objects`/`get_object` 对象查询，`list_execution_targets` 机构范围，`list_action_types`/`execute_action` 动作）；走 Logical Dataset source binding + `object_projection`，不依赖语义模型；**本体模式专用**，导航/对象读取/Action 分别受 `CHAT_DATA_ONTOLOGY_LOCATE_ENABLED` / `CHAT_DATA_ONTOLOGY_OBJECT_READ_ENABLED` / `CHAT_DATA_ONTOLOGY_ACTION_EXECUTE_ENABLED` 门控 |
| `ask_user_question` | `ask_user_question.py` | 向用户追问 |
| `calculator`/`current_time` | 同名模块 | 基础工具 |

## 语义 / 本体二选一

同一业务域内 `data_analyzing_tool`（语义查询）与 `ontology_tool`（本体查询）**互斥装配**：
选择逻辑唯一入口是 `services/ontology/query_mode.py`（业务域 = 当前数据源组），
`registry.py` 只读取判定结果，不自行探测目录。本体模式下语义工具不进入模型上下文，
语义模式下本体工具不挂载（即使功能开关打开）。

## 约定

- 路径参数只接受规范形式 `/mnt/user-data/{workspace,outputs,uploads}/...` 与只读
  `/mnt/skills/<name>/...`；docstring 是模型照抄的输入模板，示例必须用规范形式。
- 工具错误分两类返回：参数/路径类给可纠正消息；意外异常给通用文案并记录完整堆栈。
- `ontology_tool` 的分层约定：**参数契约归工具，业务规则归技能**。
  参数（字段/必填/默认/上限）由 `action="help"` 从 `ONTOLOGY_ACTION_PAYLOAD_MODELS` 现算，
  不写进工具 description、也不写进技能文档；`ONTOLOGY_ACTION_HELP` 只登记每个动作的一句话用法
  与对应工作流文档。`data-analyzing` 技能的 `references/ontology/` 只写何时用哪个动作、顺序、
  口径与边界，不重复参数模板（反向护栏见 `tests/unit/test_ontology_tool.py`）。
- 新增工具需同步：`ToolBuildGroup` 构建组、`ToolDefinition` 单工具策略、子智能体白名单（`data_agent.SUBAGENT_TOOL_ALLOWLISTS`）、前端展示类型（web-vue）、单元测试。

## 工具能力目录

`tools/catalog.py` 把内置工具（`TOOL_BUILD_GROUPS`）、中间件注入工具和动态 MCP 工具收成
`ToolCatalog`，统一提供来源、风险、权限、审批、启停和可检索性。装配诊断、
审批策略和管理端都应读取这份目录，不再各自维护 name map。

## 声明分层

- `ToolBuildGroup`（`TOOL_BUILD_GROUPS`）只描述构建组：哪些工具共享一次构建、如何构建；
- `ToolDefinition`（`TOOL_DEFINITIONS`）逐工具声明审批、风险、展示名、技能前置、
  交互需求和启停，构建组不再携带 `approval` / `risk` / `risk_overrides`；
- `tool_display_names()` 由 `/config` 下发，前端本地映射只做兜底；
- `ToolCatalog` 合并两类声明，运行时仍按工具粒度管理。
