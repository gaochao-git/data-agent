# Agent Prompts

提示词包。`chat_data.py` 是主 system prompt（环境块教规范路径 `/mnt/user-data/...`），
其余按功能拆分：上下文压缩、维度指标预填、评测集生成、历史学习、安全提示、
会话标题生成（`conversation_title.py`，供 services 会话服务使用）。

约定：提示词中的路径示例必须使用规范形式（与工具 docstring 同一口径）；改提示词需验证
千问 3.5+ 的消息格式约束。

系统提示词按查询能力模式（语义 / 本体，见 `services/ontology/query_mode.py`）二选一：
语义模式用 `ENVIRONMENT_BLOCK` / `SKILLS_USAGE_BLOCK` / `ERROR_HANDLING_BLOCK` 与
`data-analyzing` 技能链路；本体模式用 `ONTOLOGY_ENVIRONMENT_BLOCK` /
`ONTOLOGY_WORKFLOW_BLOCK` / `ONTOLOGY_SKILLS_USAGE_BLOCK` / `ONTOLOGY_ERROR_HANDLING_BLOCK`，
并给子智能体追加 `ONTOLOGY_SUBAGENT_HINT`（`build_subagent_system_prompt`）。
两套段落不得同时出现在同一份 system prompt 中。
