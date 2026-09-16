# Agent Skills

技能加载与管理。运行域技能统一为扁平 `/mnt/skills/<name>/...`（会话级只读快照）；
物理管理域在 `CHAT_DATA_HOME/skills/{system,user}/<name>/`，路径常量的单一事实源是
`utils/skill_paths.py`。

- `loader.py`：SKILL.md frontmatter 解析与技能内容读取。
- `skill_service.py`：技能 CRUD、可见性、导入校验（routers 调用）。
- `skill-creator/`：**skill-creator 技能的种子资源（非运行时代码）**——会话内导入该技能时
  作为模板来源，其中文件以技能资源路径（`/mnt/skills/skill-creator/...`）
  被模型使用，Python 代码不直接 import。
