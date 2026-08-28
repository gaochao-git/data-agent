# Agent Prompts

提示词包。`chat_data.py` 是主 system prompt（环境块教规范路径 `/mnt/user-data/...`），
其余按功能拆分：nl2sql、上下文压缩、维度指标预填、评测集生成、历史学习、安全提示。

约定：提示词中的路径示例必须使用规范形式（与工具 docstring 同一口径）；改提示词需验证
千问 3.5+ 的消息格式约束。
