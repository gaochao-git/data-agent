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

OfficeCLI 在 ChatData 运行环境中可用（OpenSandbox 镜像预装，本地运行环境宿主也预装）。它是 Office Open XML 的结构化编辑器，不需要安装 Microsoft Office、LibreOffice 或其他依赖。

## 前置条件与安全边界

1. 先调用 `skill_load("officecli")`。本技能通过 `officecli_run` 工具执行 OfficeCLI，该工具在 local 与 sandbox 两种后端均可用、且不需要开启 `bash_run`；运行失败（如环境确实无 OfficeCLI）时如实说明，不要伪造修改结果。
2. 禁止安装、升级、联网下载、插件、MCP、`watch`、后台进程或访问宿主机路径。
3. OfficeCLI 只能操作 `/mnt/user-data/uploads/`、`/mnt/user-data/workspace/` 和 `/mnt/user-data/outputs/` 中的 Office 文件；只允许写入 `workspace` 或 `outputs`，禁止修改 uploads 原文件或 `outputs/.tool-offload/`。`/mnt/skills/` 仅供 `skill_load`、`file_read` 读取参考资料，不能作为 OfficeCLI 的文档输入、输出或 cwd。
4. `officecli_run(command_args=[...])` 的每个数组元素就是一个独立参数，**不要加引号、不要把整条命令拼成一个字符串**。文件路径一律用完整绝对路径；含空格、方括号或中文时照原样写（如 `"/mnt/user-data/workspace/我的 报告.docx"`），加引号会被当成文件名的一部分。
5. 不确定命令、元素路径或属性名时，先用 `officecli help <format> <element>` 查询，不要猜测。`docx`、`xlsx`、`pptx` 分别对应 Word、Excel、PowerPoint。

质量规则：生成或修改 Office 文件时，按需读取 `/mnt/skills/officecli/references/delivery-quality.md`。它定义了通用校验门，以及 Word、Excel、PowerPoint 的交付底线。

## 工作流程

1. 调用 `skill_load("officecli")`，再执行 `officecli_run(command_args=["--version"])` 验证 OfficeCLI 可用。
2. 根据文件类型按需用 `file_read` 读取一份参考文档：
   - Word（含原生图表）：`/mnt/skills/officecli/references/word.md`
   - Excel：`/mnt/skills/officecli/references/excel.md`
   - PowerPoint：`/mnt/skills/officecli/references/powerpoint.md`
3. 编辑已有上传文件时，先复制到 workspace，例如：`file_copy(source_path="/mnt/user-data/uploads/原文件.docx", destination_path="/mnt/user-data/workspace/修订稿.docx")`。不要覆盖 uploads 中的原文件。
4. 用 `view`、`get` 或 `query` 读取当前文件结构，例如 `officecli_run(command_args=["view", "/mnt/user-data/workspace/报告.docx", "outline"])`；每次编辑前重新读取目标结构。DOM 选择器（如 `/body/p[1]`、`/slide[1]`、`/Sheet1/A1`）是文档内部定位，作为独立数组元素原样传入即可。
5. 用 `create`、`add`、`set`、`remove`、`move` 或 `batch` 完成修改，例如 `officecli_run(command_args=["set", "/mnt/user-data/workspace/报告.docx", "/body/p[1]", "--prop", "bold=true"])`。`batch` 仅在已通过 `help` 确认 JSON 结构时使用；**`batch` 不接受内联 JSON 位置参数**，须先用 `file_write` 把 JSON 数组写入 workspace 临时文件，再用 `officecli_run(command_args=["batch", "<file>", "--input", "<json临时文件>"])` 提交（详见各格式参考文档的 batch 章节）；不使用 `raw`、`raw-set` 或 `add-part` 直接改 OOXML XML。
6. 修改命令成功后，当前运行时会将 `workspace`/`outputs` 的变更同步到应用侧；需要显式确认 OfficeCLI 已刷新文件时可调用 `save`，但不必在每条修改后重复调用。随后执行 `validate` 和 `view ... issues` 校验。校验失败或样式未解析时先修复，不交付损坏或显示异常的文件。（`open`/`close` 被禁用。）
7. 用 `file_copy` 把最终文件复制到 `/mnt/user-data/outputs/<业务名>.<docx|xlsx|pptx>`，确认文件存在后调用 `present_artifacts(files=["/mnt/user-data/outputs/<实际文件名>"])`。

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
- `/body/p[1]`、`/slide[1]` 等是 OfficeCLI 文档选择器，不是宿主绝对路径。选择器**不要加引号**，作为独立数组元素原样传入即可；真实文件路径仍受 ChatData 工作区安全限制。
- `style not found` 可能仍能通过结构校验，但不代表渲染样式正确；需重新读取或预览确认。
