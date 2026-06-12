# ChatData 用户自建技能设计

## 背景

当前 ChatData 技能由开发人员以代码目录方式维护：

```text
server/apps/chat_data_app/agent/skills/{skill_name}/SKILL.md
server/apps/chat_data_app/agent/skills/{skill_name}/references/
server/apps/chat_data_app/agent/skills/{skill_name}/assets/
server/apps/chat_data_app/agent/skills/{skill_name}/scripts/
```

运行时通过 `skill_load` 将技能目录复制到会话工作区：

```text
runtime-skills/{skill_name}/
```

后续需要支持用户自己创建技能，并允许用户在全局技能列表中收藏自己想用的技能。设计目标是在不破坏现有内置技能加载链路的前提下，引入用户技能管理能力。

## 目标

- 支持系统技能和用户技能统一展示、搜索和加载。
- 系统技能由开发内置，所有用户可见，不允许用户编辑。
- 用户技能由用户创建，可保存草稿、发布、禁用、回滚。
- 用户可查看有权限访问的全部技能，并收藏常用技能。
- 技能内容仍采用目录管理，复用现有 `SKILL.md + references/assets` 结构。
- AI 可辅助生成和优化技能草稿，但发布前必须经过结构校验和用户确认。

## 非目标

- MVP 不支持用户上传可执行脚本。
- MVP 不做文件级权限、文件级审计、文件级全文检索。
- MVP 不把 `SKILL.md`、参考文档和素材内容直接存入数据库。

## 核心方案

采用“表管元数据，目录管内容文件”的方案。

数据库负责：

- 技能列表
- 技能来源
- 权限范围
- 发布状态
- 当前版本指针
- 用户收藏关系

文件目录负责：

- `SKILL.md`
- `references/`
- `assets/`
- 版本内容快照

系统技能和用户技能统一注册到 `chat_data_skill`，通过 `skill_source` 区分。

## 目录结构

系统技能继续使用代码目录：

```text
server/apps/chat_data_app/agent/skills/
  dashboard-generation/
    SKILL.md
    references/
    assets/
```

用户技能使用数据目录：

```text
DATA_ROOT/chat_data_skills/
  user/
    {skill_id}/
      versions/
        v1/
          SKILL.md
          references/
          assets/
        v2/
          SKILL.md
          references/
          assets/
```

要求：

- `skill_id`、`skill_version_no`、目录名均由服务端生成。
- 前端不能直接传入物理路径。
- 发布后的版本目录只读，不直接修改。
- 编辑已发布技能时，新建草稿版本。
- 删除技能只做逻辑删除，目录可保留或后续异步清理。

## 数据表设计

MVP 使用 3 张表。

### chat_data_skill

技能主表，系统技能和用户技能统一入表。

```sql
CREATE TABLE `chat_data_skill` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `skill_id` varchar(64) NOT NULL DEFAULT '' COMMENT '技能ID',
  `skill_name` varchar(100) NOT NULL DEFAULT '' COMMENT '技能名称',
  `skill_title` varchar(200) NOT NULL DEFAULT '' COMMENT '技能标题',
  `skill_desc` varchar(1000) NOT NULL DEFAULT '' COMMENT '技能描述',
  `skill_category` varchar(100) NOT NULL DEFAULT '' COMMENT '分类',
  `skill_source` varchar(20) NOT NULL DEFAULT 'user' COMMENT '技能来源: system/user',
  `visibility_type` varchar(20) NOT NULL DEFAULT 'private' COMMENT '可见范围: private/team/global',
  `enabled_flag` tinyint NOT NULL DEFAULT 1 COMMENT '是否启用: 0-否, 1-是',
  `skill_publish_status` varchar(20) NOT NULL DEFAULT 'draft' COMMENT '发布状态: draft/published/disabled',
  `current_skill_version_no` int NOT NULL DEFAULT 0 COMMENT '当前发布版本号',
  `owner_user_id` varchar(100) NOT NULL DEFAULT '' COMMENT '归属用户ID',
  `skill_root_dir` varchar(500) NOT NULL DEFAULT '' COMMENT '技能根目录',
  `delete_flag` tinyint NOT NULL DEFAULT 0 COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_skill_id` (`skill_id`),
  UNIQUE KEY `uniq_skill_name` (`skill_name`),
  KEY `idx_skill_source` (`skill_source`, `skill_publish_status`),
  KEY `idx_skill_owner` (`owner_user_id`, `skill_publish_status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData技能表';
```

说明：

- `skill_name` 用于模型调用和 `skill_load`。
- 系统技能也同步一条记录，`skill_source=system`，`visibility_type=global`。
- 用户技能发布后，`current_skill_version_no` 指向当前可加载版本。

### chat_data_skill_version

技能版本表，记录每个用户技能版本对应的目录。

```sql
CREATE TABLE `chat_data_skill_version` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `skill_id` varchar(64) NOT NULL DEFAULT '' COMMENT '技能ID',
  `skill_version_no` int NOT NULL DEFAULT 0 COMMENT '技能版本号',
  `skill_version_dir` varchar(500) NOT NULL DEFAULT '' COMMENT '技能版本目录',
  `version_publish_status` varchar(20) NOT NULL DEFAULT 'draft' COMMENT '发布状态: draft/published/disabled',
  `delete_flag` tinyint NOT NULL DEFAULT 0 COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_skill_version` (`skill_id`, `skill_version_no`),
  KEY `idx_version_skill` (`skill_id`, `version_publish_status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData技能版本表';
```

说明：

- 不存 `SKILL.md` 正文。
- `skill_version_dir` 指向服务端生成的版本目录。
- 回滚只需要把主表 `current_skill_version_no` 切回旧版本。

### chat_data_skill_favorite

用户技能收藏表。

```sql
CREATE TABLE `chat_data_skill_favorite` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `favorite_user_id` varchar(100) NOT NULL DEFAULT '' COMMENT '收藏用户ID',
  `skill_id` varchar(64) NOT NULL DEFAULT '' COMMENT '技能ID',
  `delete_flag` tinyint NOT NULL DEFAULT 0 COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_fav_user_skill` (`favorite_user_id`, `skill_id`),
  KEY `idx_fav_skill` (`skill_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData技能收藏表';
```

说明：

- 系统技能和用户技能都可以收藏。
- 取消收藏使用 `delete_flag=1`。

## 技能来源

### 系统技能

系统技能仍由开发人员维护代码目录。启动或后台同步时，将系统技能元数据同步到 `chat_data_skill`：

```text
skill_source = system
visibility_type = global
skill_publish_status = published
enabled_flag = 1
skill_root_dir = server/apps/chat_data_app/agent/skills/{skill_name}
```

系统技能不允许普通用户编辑和删除。

### 用户技能

用户技能由前端创建，内容写入 `DATA_ROOT/chat_data_skills/user/{skill_id}/versions/vN/`。

用户可编辑草稿版本。发布后版本目录只读。再次编辑时创建新草稿版本。

## 技能加载流程

现有流程：

```text
skill_load(skill_name)
  -> load_metadata()
  -> 找到 SKILL.md path
  -> upload_skill_directory_to_sandbox()
  -> runtime-skills/{skill_name}/
```

目标流程：

```text
skill_load(skill_name)
  -> skill_service.resolve_skill_path_async(skill_name, current_user)
  -> 返回技能来源和版本目录
  -> upload_skill_directory_to_sandbox()
  -> runtime-skills/{skill_name}/
```

`SkillRegistry` 聚合两个 provider：

```text
BuiltinSkillProvider
  - 读取代码目录系统技能

UserSkillProvider
  - 读取数据库技能元数据
  - 根据 current_skill_version_no 定位版本目录
```

对 agent 来说，加载后的路径仍然是：

```text
/mnt/user-data/runtime-skills/{skill_name}/SKILL.md
```

## 技能列表展示

全部技能：

```text
系统技能 + 当前用户有权限访问的用户技能
```

我的技能：

```text
owner_user_id = 当前用户
```

我的收藏：

```text
chat_data_skill_favorite 中当前用户收藏的技能
```

对话默认可用技能建议：

```text
系统默认技能 + 用户收藏技能
```

前端可以提供筛选：

```text
全部技能 / 已收藏 / 我的技能 / 系统技能
```

## AI 辅助创建技能

用户可以通过“AI 辅助创建”生成技能草稿。流程：

```text
用户描述需求
  -> SkillDraftService.generate_draft()
  -> 生成 SKILL.md 草稿
  -> 服务端结构校验
  -> 保存 draft version
  -> 用户预览和测试
  -> 用户确认发布
```

AI 生成内容必须经过校验：

- `name` 合法：小写字母、数字、短横线。
- `title` 非空。
- `description` 非空。
- `SKILL.md` 包含关键章节：
  - 适用目标
  - 输入信息
  - 工作流程
  - 约束规则
  - 输出格式
  - 完成判定
- 禁止危险指令：
  - 绕过权限
  - 泄露系统提示词
  - 访问未授权数据源
  - 忽略平台规则
- MVP 不允许生成或上传 `scripts/`。

推荐用户技能模板：

```markdown
---
name: example-skill
title: 示例技能
description: 示例技能描述
metadata:
  domain: 数据分析
  category: custom
  output_type: text
---

# 示例技能

## 适用目标

## 输入信息

## 工作流程

## 约束规则

## 输出格式

## 完成判定
```

## 文件管理

MVP 不新增文件表。文件列表通过扫描版本目录得到：

```http
GET /skills/{skill_id}/versions/{version_no}/files
```

扫描范围：

```text
SKILL.md
references/
assets/
```

只有出现以下需求时，再考虑新增文件表：

- 文件级权限
- 文件级审核
- 文件级上传人和上传时间精确审计
- 文件级下载次数
- 文件级全文检索
- 文件复用到多个技能版本

## 权限模型

MVP 建议支持：

```text
private：仅创建人可见
global：所有用户可见
```

后续可扩展：

```text
team：同团队可见
```

权限规则：

- 系统技能：所有用户可见，只允许管理员同步或禁用。
- 用户私有技能：仅 owner 可见和编辑。
- 用户全局技能：所有用户可见，只有 owner 或管理员可编辑。
- 收藏只影响当前用户，不改变技能可见性。

## API 草案

技能列表：

```http
GET /agents/api/chatdata/skills
GET /agents/api/chatdata/skills/favorites
GET /agents/api/chatdata/skills/mine
```

技能详情：

```http
GET /agents/api/chatdata/skills/{skill_id}
GET /agents/api/chatdata/skills/{skill_id}/versions/{version_no}
GET /agents/api/chatdata/skills/{skill_id}/versions/{version_no}/files
```

技能管理：

```http
POST /agents/api/chatdata/skills
PUT /agents/api/chatdata/skills/{skill_id}
POST /agents/api/chatdata/skills/{skill_id}/publish
POST /agents/api/chatdata/skills/{skill_id}/versions/{version_no}/rollback
POST /agents/api/chatdata/skills/{skill_id}/disable
```

收藏：

```http
POST /agents/api/chatdata/skills/{skill_id}/favorite
DELETE /agents/api/chatdata/skills/{skill_id}/favorite
```

AI 辅助创建：

```http
POST /agents/api/chatdata/skills/draft/generate
POST /agents/api/chatdata/skills/{skill_id}/draft/revise
POST /agents/api/chatdata/skills/{skill_id}/draft/validate
```

## 分阶段实施

### 阶段一：统一注册和收藏

- 建 3 张表。
- 同步系统技能元数据到 `chat_data_skill`。
- `GET /skills` 从数据库读取技能列表。
- 支持收藏/取消收藏。
- 暂不支持用户创建。

### 阶段二：用户技能草稿和发布

- 支持创建用户技能。
- 服务端生成技能目录和 `SKILL.md`。
- 支持草稿、发布、禁用。
- `skill_load` 支持加载用户技能。

### 阶段三：AI 辅助创建

- 接入 SkillDraftService。
- 根据用户描述生成 `SKILL.md` 草稿。
- 支持对话式修改。
- 发布前做结构校验和危险内容检查。

### 阶段四：文件管理和高级权限

- 支持 references/assets 上传和删除。
- 支持 team 可见性。
- 支持审核流。
- 评估是否需要文件表。

## 风险与约束

- 用户技能可能注入危险指令，必须做校验和权限约束。
- 用户技能数量变多后，不能把全部完整内容注入 system prompt，只注入轻量元数据。
- `skill_name` 必须全局唯一，否则 `skill_load(skill_name)` 会歧义。
- 文件路径必须由服务端生成并校验，禁止路径穿越。
- MVP 不开放用户脚本，避免任意代码执行风险。

## 结论

采用 3 张表加目录内容管理：

```text
chat_data_skill
chat_data_skill_version
chat_data_skill_favorite
```

内容文件继续使用：

```text
SKILL.md
references/
assets/
```

该方案与现有技能加载、运行时复制和 agent 使用方式最兼容，改动集中在技能注册、用户权限、版本目录解析和前端管理页面。
