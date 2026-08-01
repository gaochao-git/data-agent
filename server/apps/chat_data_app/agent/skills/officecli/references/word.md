# Word 操作参考

## 读取与定位

编辑前先查看结构，不按猜测的段落编号修改：

```bash
officecli view '/mnt/user-data/workspace/报告.docx' outline
officecli get '/mnt/user-data/workspace/报告.docx' '/body' --depth 2 --json
officecli query '/mnt/user-data/workspace/报告.docx' 'paragraph:contains("经营摘要")' --json
```

段落路径通常形如 `'/body/p[3]'`。先通过 `officecli help docx paragraph` 或 `officecli help docx run` 确认目标元素和支持属性。

## 常见操作

```bash
# 新建空白文档后添加标题和正文。paragraph 的 text 属性在当前版本受支持。
officecli create '/mnt/user-data/workspace/经营摘要.docx'
officecli add '/mnt/user-data/workspace/经营摘要.docx' '/body' --type paragraph --prop text='经营摘要' --prop bold=true --prop size=18pt
officecli add '/mnt/user-data/workspace/经营摘要.docx' '/body' --type paragraph --prop text='本期经营情况如下。'

# 显式 run 写法，适合同一段内需要分别控制多个文本片段的场景
officecli create '/mnt/user-data/workspace/经营摘要-分段格式.docx'
officecli add '/mnt/user-data/workspace/经营摘要-分段格式.docx' '/body' --type paragraph
officecli add '/mnt/user-data/workspace/经营摘要-分段格式.docx' '/body/p[1]' --type run --prop text='经营'
officecli add '/mnt/user-data/workspace/经营摘要-分段格式.docx' '/body/p[1]' --type run --prop text='摘要' --prop bold=true

# 仅替换已经确认存在的文字
officecli set '/mnt/user-data/workspace/经营摘要.docx' '/' --find '草稿' --replace '正式版' --json

# 只调整已定位段落的格式
officecli set '/mnt/user-data/workspace/经营摘要.docx' '/body/p[2]' --prop font.bold=true --prop font.size=14pt
```

添加表格、图片、页眉页脚、目录或批注前，先查询对应的 `officecli help docx <element>`。不要用 raw XML 绕过结构化接口。

## 常见误区

- DOM 选择器不是文件路径。`/body`、`/body/p[1]`、`/slide[1]`、`/Sheet1/A1` 等参数表示文档内部节点；在 shell 中仍应整体加引号，尤其是包含方括号的路径。
- `create` 的格式是 `officecli create <文件路径> [--type docx|xlsx|pptx]`。通常由扩展名推断类型，显式 `--type` 时必须与扩展名一致；不要把类型直接拼在文件名或命令名中。
- `paragraph --prop text=...` 在当前 OfficeCLI 版本可以创建隐式 run，并非无效参数。需要分别控制段落样式和多个文本片段时，先创建 paragraph，再向 `'/body/p[N]'` 添加 run。
- `style ... not found` 表示样式引用没有在文档样式表中解析，文件可能仍能通过结构校验，但显示效果不一定符合预期。交付前应重新读取样式/预览；必要时使用已有样式或先创建样式。
- 修改完成后先执行 `save`，再由 `validate` 和 `view ... issues` 检查，最后复制到 `outputs` 并调用 `present_artifacts`。OfficeCLI resident 未 flush 时，外部复制可能拿到旧文件。

## 交付前检查

```bash
officecli save '/mnt/user-data/workspace/经营摘要.docx'
officecli validate '/mnt/user-data/workspace/经营摘要.docx'
officecli view '/mnt/user-data/workspace/经营摘要.docx' issues
```

`validate` 失败时停止交付。`issues` 仅用于发现需要根据用户目标处理的问题，不把原始输出直接发给用户。

`create` 默认按文件扩展名推断格式，也可以显式传 `--type docx|xlsx|pptx`。当前版本的空白文档不一定包含完整的 Word 样式表；使用 `style=Heading1` 等样式时若出现 `style not found`，不要只看 `validate` 结果，应通过 `view outline` 或 HTML 预览确认显示效果。
