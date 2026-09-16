# 技能输出格式参考

## SKILL.md 基础模板

```markdown
---
name: sales-analysis
title: 中文技能名
description: 当用户需要 XXX 时使用，帮助完成 YYY 并输出 ZZZ。
metadata:
  domain: 数据分析
  category: custom
  output_type: text
---

# 中文技能名

## 适用目标

说明这个技能解决什么问题，什么时候应该使用。

## 必读资料

列出需要按需读取的 references 或 assets。

## 工作流程

1. ...
2. ...
3. ...

## 约束规则

- ...

## 完成判定

1. ...
2. ...

## 用户输出

说明最终回复应该包含什么。
```

## references 文档模板

适合放业务口径、字段解释、流程细节和示例。

```markdown
# 业务口径

## 核心概念

- 指标 A：...
- 维度 B：...

## 判断规则

1. ...

## 示例

用户问题：

```text
...
```

期望处理：

```text
...
```
```

## assets 素材要求

平台技能不支持 `scripts/` 目录（可执行脚本存在安全风险，已整体移除）。
稳定的计算、转换、校验逻辑写成清晰的步骤说明放进 `SKILL.md` 或 `references/`。

素材（模板、样例文件、配置）应满足：

- 放在 `assets/` 下并按用途命名。
- 不读取固定私有路径，不包含权限外目录的引用。

## 最终回复模板

```markdown
已生成技能草稿：

- 技能名：sales-analysis
- 中文名：中文技能名
- 用途：...
- 文件：SKILL.md、references/xxx.md、assets/xxx

下一步可以在技能管理中保存草稿并发布。
```
