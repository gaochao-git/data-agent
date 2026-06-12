# complaint_app skills 设计（对齐 opencode）

本文件为 skills 体系的设计说明，仅描述结构与职责划分，不包含实现代码。

## 设计目标
- 对齐 opencode 的发现规则与 frontmatter 规范。
- 项目内优先，全局次之（同名冲突保留本地版本）。
- 简化白名单权限控制（后续可扩展为 allow/deny/ask 模式）。
- 支持热更新（按 mtime 检测 + 最小刷新间隔）。
- 允许用户自定义全局 skills 目录（默认 `~/.complaint_app/skills`）。

## 目录与发现规则
本模块遵循 opencode 的理念：**目录发现 + 按需加载**。

### 放置位置
- 项目内主路径：`server/apps/complaint_app/opencode_runtime/skills/.complaint_skills/<name>/SKILL.md`
- 全局路径：`~/.claude/skills/<name>/SKILL.md`

### 发现规则
- 从仓库根开始扫描项目内路径（local 优先）。
- 全局路径单独扫描（global 次之）。
- 同名冲突：保留 local，记录 warning。

## Skills 的工作方式（渐进披露）
Skills 使用“渐进披露”来管理上下文：
- **Discovery**：启动时仅加载每个技能的 `name` 与 `description`，用于判断是否可能相关。
- **Activation**：当任务匹配某个技能的描述时，读取完整 `SKILL.md` 并加入上下文。
- **Execution**：执行阶段按需加载 `references/` 内容或执行 `scripts/`，完成任务。

建议按以下层级组织内容：
1. 元信息（约 100 tokens）：`name/description`，启动时加载
2. 指令正文（建议 < 5000 tokens）：激活时加载
3. 资源文件（按需加载）：`scripts/`、`references/`、`assets/`

## Agent 集成方式（参考 AgentSkills）
### 两类集成方式
- **文件系统型**：Agent 可直接读取 `SKILL.md` 与引用文件（需要绝对路径）。
- **工具型**：通过技能工具接口加载 `SKILL.md` 与资源（无需暴露绝对路径）。

### 集成流程（5 步）
1. 发现技能目录  
2. 启动时仅加载 `name/description`  
3. 任务匹配到技能  
4. 激活时加载完整 `SKILL.md`  
5. 执行阶段按需加载资源或脚本

### 元信息注入建议
- 启动阶段只解析 frontmatter
- 将技能元信息注入 system prompt
- 每条技能元信息建议控制在 50-100 tokens

### 注入到上下文（Injecting into context）
建议在 system prompt 中注入可用技能元信息。示例（XML，仅供参考）：
```xml
<available_skills>
  <skill>
    <name>pdf-processing</name>
    <description>Extracts text and tables from PDF files, fills forms, merges documents.</description>
    <location>/absolute/path/to/skills/pdf-processing/SKILL.md</location>
  </skill>
  <skill>
    <name>data-analysis</name>
    <description>Analyzes datasets, generates charts, and creates summary reports.</description>
    <location>/absolute/path/to/skills/data-analysis/SKILL.md</location>
  </skill>
</available_skills>
```
说明：
- 文件系统型 agent 需要提供 `location`（绝对路径）。
- 工具型 agent 可省略 `location`。
- 保持元信息简洁，单个技能元信息约 50-100 tokens。

### 安全注意事项
- 允许执行脚本前应进行权限校验或用户确认
- 建议记录执行日志与输入输出摘要

## SKILL.md 规范（结合 AgentSkills）

### frontmatter 字段
- 必填：`name`、`description`
- 可选：`license`、`compatibility`、`metadata`、`allowed-tools`
- 其它字段允许扩展，但客户端仅保证识别上述字段。

### name 规则
- 长度 1-64
- 仅小写字母/数字与 `-`
- 不以 `-` 开头或结尾
- 不允许 `--`
- 必须与所在目录名一致
- 参考正则：`^[a-z0-9]+(-[a-z0-9]+)*$`

### description 规则
- 长度 1-1024
- 应同时描述“做什么”和“何时使用”
- 建议包含可检索的关键字

### license（可选）
- 许可证名称或指向随附许可文件的简短说明

### compatibility（可选）
- 长度 1-500
- 仅在存在环境/依赖要求时填写（产品、系统包、网络访问等）

### metadata（可选）
- 仅接受 `string -> string` 的键值映射
- 用于存放规范未定义的扩展信息

### allowed-tools（可选）
- 以空格分隔的预批准工具列表（实验性字段，是否支持取决于实现）。
  - 示例：`allowed-tools: Bash(git:*) Bash(jq:*) Read`

### 其它约束
- 文件名必须为全大写 `SKILL.md`

### SKILL.md 示例
```md
---
name: pdf-processing
description: Extract text and tables from PDF files, fill forms, merge documents.
---

# PDF Processing

## When to use this skill
Use this skill when the user needs to work with PDF files...

## How to extract text
1. Use pdfplumber for text extraction...

## How to fill forms
...
```

## 技能目录结构（推荐）
一个技能是一个文件夹，最少包含 `SKILL.md`，推荐结构：
```
<skill-name>/
  SKILL.md
  README.md (可选)
  scripts/ (可选)
  references/ (可选)
  assets/ (可选)
```
说明：
- `scripts/`：可执行脚本或工具（是否允许执行取决于权限策略）。
- `references/`：引用资料或模板，执行阶段按需读取。
- `assets/`：静态资源（图片、示例文件等）。

## 正文内容与引用规则
- `SKILL.md` 正文无固定格式限制，建议包含：步骤、示例、边界情况。
- 建议正文控制在 **500 行以内**，超长内容拆到 `references/`。
- 建议正文规模 **< 5000 tokens**（用于 activation 阶段）。
- 文件引用使用相对路径（相对技能根目录），避免深层级引用链。

示例：
```
See [the reference guide](references/REFERENCE.md).
scripts/extract.py
```

## 权限控制（简化白名单）
- 配置项：`ALLOWED_SKILLS`（逗号分隔）
- 未配置时默认全部允许（local + global）
- `skills.list` 仅返回允许列表
- `skills.get` 对未授权技能返回拒绝

## 模块拆分
- `skills/config.py`：配置与环境变量解析。
- `skills/models.py`：SkillProperties 数据结构。
- `skills/loader.py`：扫描与解析 SKILL.md。
- `skills/registry.py`：缓存与查询入口（热更新核心）。
- `skills/permission.py`：白名单权限策略。
- `skills/watcher.py`：热更新检测。
- `skills/service.py`：对外统一入口（供 API/工具调用）。
- `skills/api.py`：HTTP 暴露（可选）。
- `skills/cli.py`：调试命令（可选）。

## 核心类与职责

### SkillConfig（config.py）
负责读取配置与路径。
- 属性：
  - `local_root`：`opencode_runtime/skills/.complaint_skills`
  - `claude_global_root`：`~/.claude/skills`
  - `min_refresh_interval`：最小刷新间隔

### SkillProperties（models.py）
对齐 AgentSkills 规范的 frontmatter 属性结构。
- 字段：`name/description/license/compatibility/allowed_tools/metadata`

### SkillLoader（loader.py）
负责扫描与解析 frontmatter（严格校验模式）。
- `scan(paths) -> list[Path]`
- `load(skill_dir) -> SkillProperties | None`

### PermissionPolicy（permission.py）
负责白名单过滤。
- `is_allowed(name) -> bool`
- `filter(skills) -> list[SkillProperties]`

### SkillWatcher（watcher.py）
负责热更新判断。
- `should_refresh(mtime_map) -> bool`

### SkillRegistry（registry.py）
负责缓存与查询，组合 Loader/Watcher。
- 状态：
  - `skills`：`name -> SkillProperties`
  - `locations`：`name -> SKILL.md`
  - `sources`：`name -> local/global`
  - `mtime_map`：`path -> mtime`
  - `last_scan_at`
- 方法：
  - `all() -> list[SkillProperties]`
  - `get(name) -> SkillProperties | None`
  - `get_location(name) -> str | None`
  - `get_source(name) -> str | None`
  - `refresh_if_needed()`
- 冲突策略：同名保留 local，并记录 warning。

### SkillService（service.py）
对外统一入口，组合 Registry + Permission。
- `list() -> list[SkillProperties]`（权限过滤）
- `get(name) -> SkillProperties`（权限校验）
- `load_content(name) -> str`（按需读取 SKILL.md 正文）
- `to_prompt_xml() -> str`（生成 <available_skills> XML）

### SkillAPI（api.py，可选）
- `GET /complaint/skills`（返回 SkillProperties 列表）
- `GET /complaint/skills/{name}`（返回 SKILL.md 正文）

## 调用时序图（文本）

### list() 时序
1. `SkillService.list()`  
2. `SkillRegistry.refresh_if_needed()`  
3. `SkillWatcher.should_refresh()`  
4. 如需刷新：`SkillLoader.scan()` -> `SkillLoader.load()`  
5. `PermissionPolicy.filter()`  
6. 返回 SkillProperties 列表

### get(name) 时序
1. `SkillService.get(name)`  
2. `SkillRegistry.refresh_if_needed()`  
3. `SkillWatcher.should_refresh()`  
4. 如需刷新：`SkillLoader.scan()` -> `SkillLoader.load()`  
5. `PermissionPolicy.is_allowed(name)`  
6. 返回 SkillProperties 或错误

### load_content(name) 时序
1. `SkillService.load_content(name)`  
2. `SkillService.get(name)`（权限校验+获取 SkillProperties）  
3. 从 Registry 获取 location 并读取 SKILL.md 正文  
4. 返回正文内容

### resolve_references(name) 时序（可选）
1. `SkillService.resolve_references(name)`  
2. `SkillService.get(name)`（权限校验+获取 SkillProperties）  
3. 遍历 `references/` 目录，按需读取内容  
4. 返回引用内容列表（或映射）

### list_assets(name) 时序（可选）
1. `SkillService.list_assets(name)`  
2. `SkillService.get(name)`（权限校验+获取 SkillProperties）  
3. 返回 `assets/` 下的可用资源清单

## 排错清单
1. 确认 `SKILL.md` 为全大写
2. frontmatter 包含 `name` 与 `description`
3. `name` 与目录一致、且符合命名规则
4. 未通过白名单的技能不会出现在列表中

## 校验建议
可使用 `skills-ref` 对技能目录进行规范校验（若引入该工具链）。
