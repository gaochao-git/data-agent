---
name: officecli
title: Office 文档处理
description: 使用沙箱内预装的 OfficeCLI 创建、检查和修改 Word、Excel、PowerPoint 文件。
metadata:
  domain: 文档处理
  category: office_document
  output_type: file
---

# Office 文档处理

## 适用目标

处理 `.docx`、`.xlsx`、`.pptx` 文件（含宏格式 `.docm`/`.xlsm`/`.pptm`，1.0.144+ 支持就地打开编辑；保存新文件时仍用标准扩展名）：创建文档、修改文字和格式、编辑工作簿单元格/公式，或调整演示文稿页面和形状。

OfficeCLI 是 Office Open XML 的结构化编辑器，不需要安装 Microsoft Office、LibreOffice 或其他依赖。

## 前置条件与安全边界

1. 本 `SKILL.md` 已通过 `load_skill` 读取。本技能通过 `officecli_run` 工具执行 OfficeCLI；运行失败时如实说明，不要伪造修改结果。
2. 禁止安装、升级、联网下载、插件、MCP、`watch`、后台进程或访问宿主机路径。
3. OfficeCLI 只能操作 `/mnt/user-data/uploads/`、`/mnt/user-data/workspace/` 和 `/mnt/user-data/outputs/` 中的 Office 文件；只允许写入 `workspace` 或 `outputs`，禁止修改 uploads 原文件或系统管理的 `outputs/query_result/`、`outputs/.tool-offload/`。`/mnt/skills/` 仅供 `file_read` 读取参考资料，不能作为 OfficeCLI 的文档输入、输出或 cwd。
4. `officecli_run(command_args=[...])` 的每个数组元素就是一个独立参数，**不要加引号、不要把整条命令拼成一个字符串**。文件路径一律用完整绝对路径；含空格、方括号或中文时照原样写（如 `"/mnt/user-data/workspace/我的 报告.docx"`），加引号会被当成文件名的一部分。
5. 不确定命令、元素路径或属性名时，先用 `officecli help <format> <element>` 查询，不要猜测。`docx`、`xlsx`、`pptx` 分别对应 Word、Excel、PowerPoint。

质量规则：生成或修改 Office 文件时，按需读取 `/mnt/skills/officecli/references/delivery-quality.md`。它定义通用校验门；Word 排版口径统一为标准汇报模版与党政机关公文两套，不允许第三套通用默认值。

## 工作流程

1. 执行 `officecli_run(command_args=["--version"])` 验证 OfficeCLI 可用。
2. 按文件类型读取参考文档（Word 强制，其余按需）：
   - 普通报告：生成前必须读取 `/mnt/skills/officecli/references/standard-report.md`（样式口径唯一事实源）。
   - 党政机关公文：生成前必须读取 `/mnt/skills/officecli/references/gb-t-9704-2012.md`。
   - Word 命令手册（只含命令用法与结构校验，不含排版口径）：`/mnt/skills/officecli/references/word.md`。
   - Excel：`/mnt/skills/officecli/references/excel.md`；PowerPoint：`/mnt/skills/officecli/references/powerpoint.md`。
   - 多章节长报告成稿（需求整理→样式解析→分块生成→校验的过程治理）：`/mnt/skills/officecli/references/report-workflow.md`。
3. 先确定 Word 起始文件，优先级如下：
   - 用户提供或明确指定模板：保留用户模板，不套系统默认模板。
   - 党政机关正式公文（请示、通知、决定、函等）：按 `/mnt/skills/officecli/references/gb-t-9704-2012.md` 和用户单位模板处理，不使用标准汇报模版冒充正式公文。
   - 其他新建 Word（经营分析、专题汇报、工作总结、情况报告、调研报告等普通报告）：默认执行标准汇报模版的样式约定，读取 `/mnt/skills/officecli/references/standard-report.md`，从 `officecli create` 起步并按样式映射表逐级显式设置字体/字号/行距（按标准配置，不是自由发挥）；可用 `view annotated` / `outline` 查看样板 `/mnt/skills/officecli/assets/standard-report-template.docx`；样板已包含封面、目录、正文分节、真实 `Heading 1/2/3` 样式、表格案例、柱状图案例与 TOC 活域，从样板起步时可直接复用样式；用户提供模板或明确要求样式时按用户执行。
4. 编辑已有上传文件时，先复制到 workspace，例如：`file_copy(source_path="/mnt/user-data/uploads/原文件.docx", destination_path="/mnt/user-data/workspace/修订稿.docx")`。不要覆盖 uploads 中的原文件。
   技能内的 .docx 是只读样式样板，仅供 `view`/`get` 查看参考；新建起点按第 3 条执行；`/mnt/skills/` 禁止作为 OfficeCLI 的写入目标。
5. 用 `view`、`get` 或 `query` 读取当前文件结构，例如 `officecli_run(command_args=["view", "/mnt/user-data/workspace/报告.docx", "outline"])`；每次编辑前重新读取目标结构。DOM 选择器（如 `/body/p[1]`、`/slide[1]`、`/Sheet1/A1`）是文档内部定位，作为独立数组元素原样传入即可。
6. 用 `create`、`add`、`set`、`remove`、`move` 或 `batch` 完成修改，例如 `officecli_run(command_args=["set", "/mnt/user-data/workspace/报告.docx", "/body/p[1]", "--prop", "bold=true"])`。**按场景选择写入方式**：
   - 新建文档铺整篇（段落/表格/幻灯片较多时）：优先用一个 `batch` 提交全部结构（一次工具调用写完，实测 200 段约 0.4s，逐段 `add` 每段一轮工具调用，很慢）；段落只有两三个时逐条 `add` 更简单；
   - 编辑已有文档：先 `view`/`query` 读取结构，建议用 `@paraId` 选择器定位目标段落后再修改；注意 `batch` 内的 remove/move 会使后续**位置索引**动态重算，按删除前快照写 `p[N]` 会错位——含 remove/move 的批量应改用 `@paraId` 定位；
   - 修改已存在文件先 `file_copy` 到 workspace 再操作。
   `batch` 不接受内联 JSON 位置参数，须先用 `file_write` 把 JSON 数组写入 workspace 临时文件，再 `officecli_run(command_args=["batch", "<file>", "--input", "<json临时文件>"])` 提交；batch 是原子的，一条失败整批不落盘（exit=1），按逐条 ERROR 修复后整批重提。不使用 `raw`、`raw-set` 或 `add-part` 直接改 OOXML XML。
7. 修改命令成功后，当前运行时会将 `workspace`/`outputs` 的变更同步到应用侧；需要显式确认 OfficeCLI 已刷新文件时可调用 `save`，但不必在每条修改后重复调用。随后执行 `validate` 和 `view ... issues` 校验。校验失败或样式未解析时先修复，不交付损坏或显示异常的文件。（`open`/`close` 被禁用；视觉检查：优先 `view screenshot` 截图后用 `file_read` 看图复核，报 `No headless browser available` 时改用 `view html`，不要尝试自行安装浏览器。）
8. 用 `file_copy` 把最终文件复制到 `/mnt/user-data/outputs/<业务名>.<docx|xlsx|pptx>`，确认文件存在后调用 `present_artifacts(files=["/mnt/user-data/outputs/<实际文件名>"])`。

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
