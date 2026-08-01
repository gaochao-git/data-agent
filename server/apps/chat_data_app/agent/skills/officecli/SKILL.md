---
name: officecli
title: Office 文档处理技能
description: 使用沙箱内预装的 OfficeCLI 创建、检查和修改 Word、Excel、PowerPoint 文件。
metadata:
  domain: 文档处理
  category: office_document
  output_type: file
---

# Office 文档处理技能

## 适用目标

处理 `.docx`、`.xlsx`、`.pptx` 文件：创建文档、修改文字和格式、编辑工作簿单元格/公式，或调整演示文稿页面和形状。

OfficeCLI 已预装在 ChatData 的 OpenSandbox 镜像中。它是 Office Open XML 的结构化编辑器，不需要安装 Microsoft Office、LibreOffice 或其他依赖。

## 前置条件与安全边界

1. 先调用 `skill_load("officecli")`。本技能只能在本轮存在 `bash_run` 且工作区后端为 sandbox 时执行；不满足时说明当前运行环境无法编辑 Office 文件，不要伪造修改结果。
2. 禁止安装、升级、联网下载、插件、MCP、`watch`、后台进程或访问宿主机路径。
3. 只允许读取 `/mnt/user-data/uploads/`、`/mnt/user-data/workspace/`、`/mnt/user-data/outputs/` 和 `/mnt/skills/`；只允许修改 `workspace` 或 `outputs`。禁止修改 uploads 原文件、技能目录或 `outputs/.tool-offload/`。
4. 所有 shell 路径使用完整绝对路径，并对包含空格、方括号或中文的路径加单引号。
5. 不确定命令、元素路径或属性名时，先用 `officecli help <format> <element>` 查询，不要猜测。`docx`、`xlsx`、`pptx` 分别对应 Word、Excel、PowerPoint。

## 工作流程

1. 调用 `skill_load("officecli")`，确认当前有 `bash_run`，再执行 `bash_run(command="officecli --version")` 验证 OfficeCLI 可用。
2. 根据文件类型按需用 `file_read` 读取一份参考文档：
   - Word：`/mnt/skills/officecli/references/word.md`
   - Word 原生图表：`/mnt/skills/officecli/references/word-charts.md`
   - Excel：`/mnt/skills/officecli/references/excel.md`
   - PowerPoint：`/mnt/skills/officecli/references/powerpoint.md`
3. 编辑已有上传文件时，先复制到 workspace，例如：`cp '/mnt/user-data/uploads/原文件.docx' '/mnt/user-data/workspace/修订稿.docx'`。不要覆盖 uploads 中的原文件。
4. 用 `officecli view`、`get` 或 `query` 读取当前文件结构；每次编辑前重新读取目标结构。OfficeCLI 的 DOM 选择器（如 `/body/p[1]`、`/slide[1]`、`/Sheet1/A1`）不是宿主文件路径，含 `[N]` 时必须整体加引号。
5. 使用 `officecli create`、`add`、`set`、`remove`、`move` 或 `batch` 完成修改。`batch` 仅在已通过 `help` 确认 JSON 结构时使用；不使用 `raw`、`raw-set` 或 `add-part` 直接改 OOXML XML。
6. 修改后先执行 `officecli save <file>`，再执行 `officecli validate <file>` 和 `officecli view <file> issues`。校验失败或样式未解析时先修复，不交付损坏或显示异常的文件。
7. 将最终文件复制到 `/mnt/user-data/outputs/<业务名>.<docx|xlsx|pptx>`，确认文件存在后调用 `present_artifacts(files=["/mnt/user-data/outputs/<实际文件名>"])`。

## 输出要求

- 最终回复只说明完成状态、实际输出文件名和必要的简短结果。
- 不在用户回复中暴露命令、参数、JSON、OOXML、工具原文或错误堆栈。
- 用户未明确要求保留原文件时，仍保留 uploads 原件，只交付 outputs 中的修改版。

## 完成判定

1. OfficeCLI 已验证可用，且只在允许的会话目录内操作。
2. 上传原文件未被修改，所有编辑发生在 workspace 或 outputs。
3. 最终文件已通过 `officecli validate`。
4. 最终 `.docx`、`.xlsx` 或 `.pptx` 已位于 outputs，且已调用 `present_artifacts`。

## 常见误区

- `officecli create <文件路径> [--type docx|xlsx|pptx]` 中类型可由扩展名推断；不要把类型拼进命令名或以错误顺序传参。
- `paragraph --prop text=...` 在当前版本有效，并会创建隐式 run；需要多段格式时再显式 `add ... --type run`。
- `/body/p[1]`、`/slide[1]` 等是 OfficeCLI 文档选择器，不是宿主绝对路径。选择器应加引号，真实文件路径仍受 ChatData 工作区安全限制。
- `style not found` 可能仍能通过结构校验，但不代表渲染样式正确；需重新读取或预览确认。
