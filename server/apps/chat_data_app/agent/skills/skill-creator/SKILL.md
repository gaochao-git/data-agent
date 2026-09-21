---
name: skill-creator
title: 技能创建
description: 根据业务人员的自然语言需求，设计并生成符合当前智能分析平台规范的用户技能目录、SKILL.md、参考文档和素材。
metadata:
  domain: 数据分析
  category: skill_creation
  output_type: skill
---

# 技能创建

## 适用目标

把用户的“帮我做一个技能 / 生成一个分析技能 / 把这套流程沉淀成技能 / 业务人员不会写技能”等自然语言需求，转换成当前平台可管理、可加载、可发布的技能草稿。

本技能不是直接完成业务分析任务，而是帮助用户把可复用的分析流程、领域知识、输出规范和素材沉淀成一个新技能。

## 当前平台技能目录

用户技能必须遵守固定目录结构：

```text
<skill-name>/
├── SKILL.md
├── references/
└── assets/
```

只允许生成或修改这些内容：

- `SKILL.md`：必须存在，包含 YAML frontmatter 和正文说明。
- `references/**`：可选，放详细业务口径、字段说明、示例、流程细节。文档格式不限（md/txt/pdf 等，上传白名单与主对话一致）。
- `assets/**`：可选，放模板、样例文件、静态资源。

平台技能不支持 `scripts/` 目录（可执行脚本存在安全风险，已整体移除）；也不要生成 `README.md`、`CHANGELOG.md`、安装文档、无关说明文档或临时文件。

## 命名规则

- 技能英文名只允许小写字母、数字和中划线（对齐 Agent Skills 规范：`a-z`、`0-9`、`-`）。
- 不以中划线开头或结尾，也不出现连续中划线。
- 长度 3 到 64。
- **单个单词合法，不强制用中划线分隔**；多个单词时用中划线连接更易读。
- 不使用中文、下划线、空格。
- 技能名对应技能目录名。

示例：

```text
dashboard
sales-analysis
customer-profile
pdf2x
```

非法示例：

```text
Sales
sales_analysis
销售分析
-sales
sales-
sales--analysis
sales analysis
```

## 初始化规则

创建技能时必须先初始化固定目录结构，再写入内容。禁止只创建一个 `SKILL.md` 或临时散文件。

初始化后的空目录也必须存在：

```text
references/
assets/
```

在对话中生成待导入技能时，目录必须写到 `/mnt/user-data/outputs/<skill-name>/`，再调用 `present_artifacts(files=["/mnt/user-data/outputs/<skill-name>/SKILL.md"])` 显式呈现。只有该调用成功后，用户才能在聊天消息中点击“导入技能”；不要把文件写入成功当作已交付。

## SKILL.md frontmatter

必须包含：

```yaml
---
name: sales-analysis
title: 中文技能名
description: 一句话说明何时使用这个技能，要包含触发场景和产出目标。
metadata:
  domain: 数据分析
  category: custom
  output_type: text
---
```

`description` 很重要，系统会用它帮助模型判断何时应该加载该技能。不要写空泛描述。

## 必读资料

进入本技能后执行：

1. 需要设计复杂工作流时，用 `file_read` 读取：
   - `/mnt/skills/skill-creator/references/workflows.md`
2. 需要约束输出格式时，用 `file_read` 读取：
   - `/mnt/skills/skill-creator/references/output-patterns.md`

## 工作流程

### 1. 澄清需求

优先用 1 到 3 个问题确认下面信息，不要一次问太多：

- 技能要解决什么重复问题？
- 用户通常会怎么问？
- 最终产物是什么：文本、图表、看板、SQL、文件、报告，还是其他？
- 是否有固定字段口径、业务规则、模板或示例？

如果用户已经提供足够信息，可以直接进入设计。

### 2. 设计技能边界

明确技能是否应该包含：

- 工作流程：适合多步骤任务。
- 参考文档：适合业务口径、字段含义、示例说明较多的任务。
- 素材模板：适合固定报告、表格、配置模板。

原则：

- `SKILL.md` 保留核心流程和必须遵守的规则。
- 详细口径和例子放到 `references/`。
- 稳定的计算、转换、校验逻辑直接写成清晰的步骤说明放在 `SKILL.md` 或 `references/` 中，不要生成脚本目录。
- 不为了一次性任务创建复杂技能。

### 3. 初始化技能结构

创建技能草稿前，先确定合法技能名并初始化固定目录：

```text
<skill-name>/
├── SKILL.md
├── references/
└── assets/
```

初始化完成后再写入或修改文件。对会话中的待导入技能，先依次调用 `dir_create` 创建 `/mnt/user-data/outputs/<skill-name>`、`references`、`assets`，再用 `file_write` 写入 `SKILL.md` 和需要的子文件。不要在未初始化目录结构的情况下直接输出零散文件。

### 4. 生成技能草稿

生成技能时，优先产出可直接保存到技能管理系统的内容：

- 技能英文名
- 中文标题
- 一句话描述
- `SKILL.md`
- 需要的 `references/**`（文档格式不限）
- 可选 `assets/**`

对话中生成待导入技能时，写入 `/mnt/user-data/outputs/<skill-name>/` 后调用 `present_artifacts(files=["/mnt/user-data/outputs/<skill-name>/SKILL.md"])`。用户在消息卡片中点击导入后，系统再创建草稿；不要绕过权限直接写系统技能目录或用户技能管理目录。

### 5. 校验技能

检查项：

- `SKILL.md` frontmatter 合法。
- `name` 与目录名一致。
- `name` 只含小写字母、数字和中划线，不以中划线开头/结尾、无连续中划线（单词名合法）。
- `references/`、`assets/` 两个目录都存在。
- `description` 能清楚说明触发场景。
- 正文不包含无关长篇背景。
- 引用文件路径在正文中说明何时读取。
- 不包含密钥、真实敏感数据或不该沉淀的业务数据。

## 输出要求

面向业务用户时，不要直接甩一大堆工程说明。最终回复应包含：

- 推荐技能名和中文名
- 这个技能解决什么问题
- 生成了哪些文件
- 下一步：保存为草稿、继续编辑或发布

需要展示文件内容时，只展示核心文件或用户明确要求的文件。

## 禁止做法

- 禁止生成不受平台管理的任意目录结构。
- 禁止只生成 `SKILL.md` 而不初始化固定目录结构。
- 禁止生成 `scripts/` 目录或任何可执行脚本文件。
- 禁止使用非法技能名：含大写、下划线、空格、中文，或以中划线开头/结尾、出现连续中划线。单词名（如 `sales`）合法。
- 禁止绕过用户权限写系统技能目录。
- 禁止把一次性分析结果伪装成技能。
- 禁止在技能中硬编码真实密钥、账号、个人敏感信息或生产数据。
- 禁止生成超长、泛泛而谈、没有触发场景的 `description`。
- 禁止让技能依赖未确认存在的外部服务、命令或私有路径。

## 完成判定

满足以下条件才算完成：

1. 技能名、标题、描述清晰可用。
2. `SKILL.md` 符合 frontmatter 规范。
3. 技能目录已初始化，且 `references/`、`assets/` 都存在。
4. 如有 references/assets，已说明用途。
5. 已调用 `present_artifacts` 展示可导入的 `outputs/<skill-name>/SKILL.md`，而不是只给出抽象建议。
