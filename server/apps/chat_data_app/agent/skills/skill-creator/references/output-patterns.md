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

列出需要按需读取的 references、scripts 或 assets。

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

## scripts 脚本要求

脚本应满足：

- 优先使用 Python 标准库。
- 输入参数清晰，可通过命令行传入。
- 输出 JSON 或明确的文件路径。
- 错误时返回非 0 退出码并打印清楚原因。
- 不读取固定私有路径，不写入权限外目录。

脚本头部示例：

```python
#!/usr/bin/env python3
"""简短说明脚本用途。"""

from __future__ import annotations

import argparse


def main() -> int:
    parser = argparse.ArgumentParser(description="...")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

## 最终回复模板

```markdown
已生成技能草稿：

- 技能名：sales-analysis
- 中文名：中文技能名
- 用途：...
- 文件：SKILL.md、references/xxx.md、scripts/xxx.py

下一步可以在技能管理中保存草稿并发布。
```
