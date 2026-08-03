# Word / DOCX 参考

OfficeCLI 对 `.docx` 提供语义路径 API（`/body/p[1]/r[2]`），几乎不需碰原始 XML。本文件覆盖读取、创建、结构化编辑、报告级配方、字段/目录、模板合并（`merge`）与交付前 QA。所有命令都通过 `officecli_run(command_args=[...])` 执行——**每个元素是一个独立参数，不要加引号、不要拼接成一条命令**。

## 执行模型（必读）

- 调用形态：`officecli_run(command_args=["add", "/mnt/user-data/workspace/report.docx", "/body", "--type", "paragraph", "--prop", "text=营收增长", "--prop", "style=Heading1"])`。路径、flag、值都是数组里的独立字符串。
- **不要给路径加引号。** 在 argv 模型里引号会变成文件名的一部分，导致定位失败。上游 shell 文档写"ALWAYS quote `/body/p[1]`"在我们的模型下是**错的**——shell 才需要引号防 glob，我们没有 shell。
- **`$` 安全、但 `\n`/`\t` 仍生效。** 无 shell，所以 `"$50M"` 原样写入、不会被展开；但 `--prop text=` 里的 `\n` 解释成软换行（`<w:br/>`）、`\t` 解释成制表符（`<w:tab/>`），表格单元格 `c1="a\nb"` 同理。这是 CLI 层行为，与调用方式无关。
- **禁 `open`/`close`。** 当前工具不启用常驻进程；成功的修改命令会由运行时同步 `workspace`/`outputs` 文件。需要显式确认 OfficeCLI 刷新文件时再调用 `save`，不必每条修改后重复调用。`batch` 可一次提交整个 JSON 数组。
- **Help-First。** 属性名/枚举不确定时先 `officecli_run(command_args=["help", "docx"])`、`help docx <element>`、`help docx <verb> <element>`、`--json` 可取机器可读 schema。skill 与 help 冲突时 **help 为准**。
- **增量执行。** officecli 每条命令就地修改文件。一次只跑一条并看返回，结构性操作（新样式、表格、目录、分节）后先 `get` 确认再叠加。

## 读取与分析

先宽后窄：`outline` 看已有结构，再 `view text`/`get`/`query` 深入。

```text
officecli_run(command_args=["view", "/mnt/user-data/workspace/report.docx", "outline"])   # 标题树、分节数、表格/图片计数、是否有 TOC/水印/修订
officecli_run(command_args=["view", "/mnt/user-data/workspace/report.docx", "text", "--start", "1", "--end", "80"])  # 文本内容 QA，路径显示为 [/body/p[N]]
officecli_run(command_args=["view", "/mnt/user-data/workspace/report.docx", "annotated"]) # 每 run 的值 + 样式/字体/字号 + 警告
officecli_run(command_args=["view", "/mnt/user-data/workspace/report.docx", "html"])      # 读返回的 HTML 路径：第一批视觉检查（层级、空段、缺 TOC）
officecli_run(command_args=["view", "/mnt/user-data/workspace/report.docx", "svg"])       # 矢量预览
officecli_run(command_args=["view", "/mnt/user-data/workspace/report.docx", "screenshot", "--grid", "auto", "-o", "/mnt/user-data/workspace/sheet.png"])  # 整文档拼图（每页一格），再用 file_read 看
officecli_run(command_args=["view", "/mnt/user-data/workspace/report.docx", "issues"])    # 空段、缺 alt、间距异常
officecli_run(command_args=["view", "/mnt/user-data/workspace/report.docx", "stats"])     # 段落计数、字体/样式分布
```

**检查单个元素**（XPath 风格、1-based；末位用 `[last()]` 带括号，`[last]` 报错）：
```text
officecli_run(command_args=["get", "/mnt/user-data/workspace/report.docx", "/body/tbl[1]", "--depth", "3"])
officecli_run(command_args=["get", "/mnt/user-data/workspace/report.docx", "/footer[1]", "--depth", "3"])  # 检查 fldChar 是否存在（域是否真为活值）
officecli_run(command_args=["get", "/mnt/user-data/workspace/report.docx", "/numbering", "--depth", "2"])
```

**跨文档查询**（CSS 风格选择器，`=`、`!=`、`~=`、`>=`、`<=`、`[attr]`）：
```text
officecli_run(command_args=["query", "/mnt/user-data/workspace/report.docx", 'paragraph[style=Heading1]'])   # 全部 H1
officecli_run(command_args=["query", "/mnt/user-data/workspace/report.docx", 'p:empty'])                    # 空段落（噪音）
officecli_run(command_args=["query", "/mnt/user-data/workspace/report.docx", 'image:no-alt'])                # 无障碍缺口
officecli_run(command_args=["query", "/mnt/user-data/workspace/report.docx", 'field[fieldType!=page]'])     # 非 PAGE 的域
```

## 创建与编辑

动词：`add`（新建）、`set`（改属性）、`remove`、`move`、`swap`、`batch`、`raw-set`（最后手段 XML）。九成工作量是段落、run、表格、几张图、一个目录、一个页脚。

### 段落、run、样式

段落 `p` 是块；run `r` 是块内一致字符格式。段落级属性（style、对齐、间距、缩进）设在 `p`；字体/字号/颜色/粗体设在 `r`。

```text
officecli_run(command_args=["add", "F", "/body", "--type", "paragraph", "--prop", "text=执行摘要", "--prop", "style=Heading1", "--prop", "size=18", "--prop", "bold=true", "--prop", "spaceAfter=12"])
officecli_run(command_args=["set", "F", "/body/p[1]/r[1]", "--prop", "color=1F4E79"])
```

纵向间距用 `spaceBefore`/`spaceAfter`，**绝不用空段落堆叠**（会被 `view issues` 标红）。左缩进用 `--prop indent=720`（twips）、`firstLineIndent=360`、`hangingIndent=720`；行首空格会触发 issues。

> ⚠️ **`create` 产出的是极简文档，不含内置 Heading 样式。** `style=Heading1` 会回退为"按原样引用"（help 有 `WARNING: style 'Heading1' not found`）。两种处理：① 用带样式的真实模板 `file_copy` 进来再编辑；② 显式给 `size`/`bold` 等尺寸，不依赖样式定义。TOC 只认内置 Heading 样式或带 `outlineLvl` 的自定义段落样式。

### 表格

`/body/tbl[N]`，行 `tr[N]`，单元格 `tc[N]`。先按行列数建，再填。

```text
officecli_run(command_args=["add", "F", "/body", "--type", "table", "--prop", "rows=4", "--prop", "cols=3", "--prop", "width=100%"])
officecli_run(command_args=["set", "F", "/body/tbl[1]/tr[1]", "--prop", "header=true", "--prop", "c1=季度", "--prop", "c2=营收", "--prop", "c3=增长"])
officecli_run(command_args=["set", "F", "/body/tbl[1]/tr[1]/tc[1]/p[1]/r[1]", "--prop", "bold=true"])
```

行级 `set` 只支持 `height`、`header`、`c1..cN` 文本快捷（列数任意扩展）；单元格加粗/填充/颜色设在单元格的段落/run 上，**不是**行级。单元格边框用 `--prop border.bottom="single;6;000000;0"`（格式 `style;size;color;space`，十六进制不带 `#`）；段落级底边框分隔线用 `--prop pbdr.bottom="single;6;2E75B6"`。

**水平分隔线 = 段落底边框，不是一行表格。** 表格当分隔线会渲染成空 min-height 盒子（页眉页脚里尤其糟）。用 `pbdr.bottom`。

### 列表（项目符号 / 编号 / 多级）

单级项目符号/编号：`listStyle` 是**段落属性**（不是 run 属性，常见错）：
```text
officecli_run(command_args=["add", "F", "/body", "--type", "paragraph", "--prop", "text=第一点", "--prop", "listStyle=bullet"])
```

多级（法律式 1 / 1.1 / 1.1.1）：先 `abstractNum`，再 `num`，再按段引用 `numId`：
```text
officecli_run(command_args=["add", "F", "/numbering", "--type", "abstractnum", "--prop", "format=decimal"])   # → abstractNum id=0
officecli_run(command_args=["add", "F", "/numbering", "--type", "num", "--prop", "abstractNumId=0"])           # → num id=1
officecli_run(command_args=["add", "F", "/body", "--type", "paragraph", "--prop", "text=第一节", "--prop", "numId=1", "--prop", "ilvl=0"])
```
ID 是 0-based：首个 `abstractNum` 是 id=0；`num` 经 `abstractNumId=0` 引用、自身 id=1。不存在的 `abstractNumId` 报错。用 `query 'paragraph[numId>0]'` 验证。

### 域（PAGE / NUMPAGES / DATE / MERGEFIELD / REF）

域是渲染时计算的活值。`fieldType` 选域类型，`name` 给目标（合并名或书签名）。

| 域 | 用途 | 写法 |
|---|---|---|
| `page` | 当前页码 | `--prop field=page`（页脚）或 `--prop fieldType=page`（内联） |
| `numpages` | 总页数 | `--prop field=numpages` |
| `date` | 今天 | `--prop fieldType=date --prop format=yyyy-MM-dd` |
| `mergefield` | 模板合并令牌 | `--prop fieldType=mergefield --prop name=customer_name` |
| `ref` | 交叉引用书签 | `--prop fieldType=ref --prop name=bookmarkName` |

完整 `fieldType` 枚举（30+，含 `pageref`/`seq`/`styleref`/`docproperty`）见 `help docx field`。**没有 `fieldInstr` 这个 fieldType**——原始指令用 `--prop instruction='…'`。

```text
officecli_run(command_args=["add", "F", "/body/p[3]", "--type", "field", "--prop", "fieldType=mergefield", "--prop", "name=customer_name"])
```
MERGEFIELD 渲染成 «customer_name» 可见占位符，在 Word 邮件合并时替换。**绝不要把 `{{name}}`/`$NAME$` 当正文渲染**——那是对收件人可见的失败模板。要么插真实 MERGEFIELD，要么把字面令牌放进明显的"使用说明"段落。

**SEQ / PAGE / PAGEREF / TOC 域值是缓存值**，写入时可能为空或过期：
- SEQ 编号：`officecli_run(command_args=["set", "F", "/", "--prop", "recalcFields=seq"])` 按正文顺序写入正确缓存值。
- PAGE / PAGEREF / NUMPAGES / TOC 页码需要分页引擎，officecli 没有——`officecli_run(command_args=["set", "F", "/settings", "--prop", "updateFields=true"])` 交给 Word 打开时重算。交付时如实说明"TOC 页码未计算"。

### 页眉页脚（页码）

单命令模式——CLI 自动注入 `<w:fldChar>`，无需手工拼域：
```text
# 空的首页页脚：自动开启 differentFirstPage，封面不显示页码
officecli_run(command_args=["add", "F", "/", "--type", "footer", "--prop", "type=first", "--prop", "text="])
# 默认页脚带活页码
officecli_run(command_args=["add", "F", "/", "--type", "footer", "--prop", "type=default", "--prop", "align=center", "--prop", "size=9", "--prop", "text=Page ", "--prop", "field=page"])
```
两者都存在时默认页脚是 `/footer[2]`，单独是 `/footer[1]`。**验证**：`get --depth 3` 必须显示 `fldChar` 子节点，而不只是一个含字面 "Page" 的 run（`view outline` 对活域和静态文本都打印 "Footer: Page"，不可靠）。**不要** `set --prop differentFirstPage=true`（不支持，exit 2 拒绝）；加 first 页脚自动翻转该位。复合 "Page X of Y" 见配方 (b)。

### 目录 TOC

任何含 3+ 标题的文档：源必须内置 Heading 样式或带 `outlineLvl` 的自定义段落样式；粗体大号 Normal 文本不算源。没有合格源就先修标题结构，别插出 "Error! No table of contents entries found."。

```text
officecli_run(command_args=["add", "F", "/body", "--type", "paragraph", "--prop", "text=引言", "--prop", "style=Heading1"])
officecli_run(command_args=["add", "F", "/styles", "--type", "style", "--prop", "id=ThesisH1", "--prop", "type=paragraph", "--prop", "outlineLvl=0"])  # 自定义样式需 outlineLvl（0=Heading1）
# 源存在后再加 TOC
officecli_run(command_args=["add", "F", "/body", "--type", "toc", "--prop", "levels=1-3", "--prop", "title=目录", "--prop", "hyperlinks=true", "--index", "0"])
officecli_run(command_args=["set", "F", "/settings", "--prop", "updateFields=true"])
```
直接用 `/toc[1]` 或 `/tableofcontents` 做 `get`/`set`/`remove`。

### 图片 / Word 原生图表 / 超链接

```text
officecli_run(command_args=["add", "F", "/body/p[5]", "--type", "picture", "--prop", "src=logo.png", "--prop", "width=1.5in", "--prop", "alt=公司 Logo"])  # alt 必填
officecli_run(command_args=["add", "F", "/body", "--type", "chart", "--prop", "chartType=bar", "--prop", "title=分区域营收", "--prop", "categories=EMEA,APAC,Americas", "--prop", "data=营收:120,150,180", "--prop", "width=15cm", "--prop", "height=8cm"])
officecli_run(command_args=["add", "F", "/body/p[2]", "--type", "hyperlink", "--prop", "url=https://example.com", "--prop", "text=官网"])
officecli_run(command_args=["add", "F", "/body/p[2]", "--type", "hyperlink", "--prop", "anchor=chapter1", "--prop", "text=见第一章"])  # 内部跳转用 anchor，不是 #fragment
```
`picture` 的 `src` 必须是允许目录中的真实文件路径，并提供 `alt`。图表的 `categories` 与 `data` 必须来自真实查询结果，不能由模型凭空编造。`data` 使用 `系列名:数值,数值,...`；多系列用分号分隔，所有系列的点数必须与 `categories` 数量一致。

当前稳定使用的图表类型是 `bar`、`column`、`line`、`pie`、`doughnut`、`area`、`scatter` 和 `funnel`。`combo`、`waterfall`、`treemap`、`sunburst` 等高级类型先用 `officecli_run(command_args=["help", "docx", "chart"])` 确认当前版本支持及参数，再使用；不要仅因为命令成功就声称视觉效果正确。

图表添加到 `/body` 后，当前 OfficeCLI 版本返回的路径通常是 `/chart[1]`；用 `officecli_run(command_args=["query", "F", "chart", "--json"])` 查看所有图表，用返回的实际路径读取或修改，不要假定图表一定位于 `/body/p[N]/chart[M]`。

### 分节与页面设置 / 分页

文档根 `/` 带页面设置（`pageWidth`/`pageHeight`/边距，twips）。多节（横向插页、分栏）加 `section` 分节符（见 `help docx section`）。`pageWidth` 与 `pagewidth` 都接受，优先 camelCase。
```text
officecli_run(command_args=["set", "F", "/", "--prop", "pageWidth=12240", "--prop", "pageHeight=15840", "--prop", "marginTop=1440", "--prop", "marginLeft=1440"])
officecli_run(command_args=["set", "F", "/", "--prop", "columns=2", "--prop", "columnSpace=720"])  # 报纸式双栏（columnSpace 单位 twips，720=0.5in）
```

强制分页：每处逻辑边界只用一个机制。默认在目标标题上 `pageBreakBefore=true`；或插一个显式 `pagebreak`。**不要两者并用**（会出空白页）。`--prop break=newPage` 是 `pageBreakBefore=true` 的别名。
```text
officecli_run(command_args=["add", "F", "/body", "--type", "paragraph", "--prop", "text=引言", "--prop", "style=Heading1", "--prop", "pageBreakBefore=true"])
```

### 批注 / 修订 / 水印（高级，按需）

- 修订：批量接受/拒绝 `officecli_run(command_args=["set", "F", "/revision", "--prop", "revision.action=accept"])`；按 `/revision[@author=Alice]` 或 `/revision[@type=ins]` 收窄。在 run 上建修订用 `--prop revision.type=ins|del --prop revision.author=…`。
- 批注：`add "/body/p[4]" --type comment --prop author=… --prop text=…`；`--prop parentId=N` 建回复线程；`set "/comments/comment[N]" --prop done=true` 标记已解决（保留审计轨迹优于删除）。
- 水印：`officecli_run(command_args=["add", "F", "/", "--type", "watermark", "--prop", "text=DRAFT", "--prop", "color=BFBFBF", "--prop", "opacity=0.8"])`。

### batch（JSON 批量，用 `--input` 文件）

`batch` **不接受内联 JSON 位置参数**（实测 `officecli batch file '[{...}]'` 报 `Unrecognized command or argument`）。JSON 必须经 **`--input` 文件** 传入。无 shell 模型下最干净的做法：用 `file_write` 把 JSON 数组写成 workspace 临时文件，再交给 `batch --input`。结构：`command` 是裸动词，`parent`/`path`/`props` 是同级的字段。

```text
# 1) 用 file_write 把 batch JSON 写入 workspace 临时文件（无 shell 转义顾虑）
file_write(file_path="/mnt/user-data/workspace/_batch.json",
           content='[{"command":"add","parent":"/body","type":"paragraph","props":{"text":"条款一","style":"Heading2","size":"14","bold":"true"}},{"command":"set","path":"/body/p[1]/r[1]","props":{"color":"1F4E79"}}]')
# 2) 用 --input 交给 batch（--input 指向的文件在 workspace 内，只读，不在 uploads）
officecli_run(command_args=["batch", "/mnt/user-data/workspace/report.docx", "--input", "/mnt/user-data/workspace/_batch.json"])
# 3) 可选清理
file_delete(file_path="/mnt/user-data/workspace/_batch.json")
```

> 注意：word 的 batch 与 pptx/xlsx 同构，但 `props` 里的 key 仍是 `--prop` 的 `key=value`（如 `text`、`style`、`color`）。`merge` 仍是独立子命令（见上），不要与 batch 混用。

### merge（模板占位符 + JSON 数据，高价值）

把含 `{{key}}` 占位符的模板与 JSON 数据合并，生成成品——报告/合同/信函批量生成的主力命令。`<template>` 与 `<output>` 是位置参数，`--data` 接 JSON 字符串（也是单个 argv 元素），`--force` 覆盖已存在输出。

```text
# 1) 建模板（上传的 .docx 亦可，用 file_copy 进 workspace）
officecli_run(command_args=["create", "/mnt/user-data/workspace/tpl.docx"])
officecli_run(command_args=["add", "/mnt/user-data/workspace/tpl.docx", "/body", "--type", "paragraph", "--prop", "text=尊敬的 {{name}}："])
officecli_run(command_args=["add", "/mnt/user-data/workspace/tpl.docx", "/body", "--type", "paragraph", "--prop", "text=感谢您 {{action}}，本期金额 {{amount}}。"])
officecli_run(command_args=["save", "/mnt/user-data/workspace/tpl.docx"])
# 2) 合并
officecli_run(command_args=["merge", "/mnt/user-data/workspace/tpl.docx", "/mnt/user-data/workspace/out.docx", "--data", '{"name":"张三","action":"续约","amount":"¥12,800"}', "--force"])
```
实测：`merge` 返回 `Replaced keys: 3`，文本里 `{{name}}` 等被替换。模板可含多个占位符；`--data` 也可指向 `.json` 文件路径（见 `help merge`）。合并后建议 `validate` + `view text` 确认无残留 `{{...}}`。

> `refresh`（重算 TOC 页码 / PAGE / NUMPAGES / 交叉引用）对 `.docx` **需要 Word + Windows** 才能生效，其余环境靠 `updateFields=true` 交给 Word 打开时重算；`dump` 把子树序列化为可重放的 batch 脚本（辅助批量改造）。

## 报告级配方

每一条都经 `validate` 通过。占位符写法 `F` 在真实调用时替换为完整路径（如 `/mnt/user-data/workspace/report.docx`），不能把字面量 `F` 传给工具。

**(a) 富封面页（填满 ≥60%）。** 保密横幅 + 标题 + 副标题 + 受送方/项目/日期块 + 关键主题条，再强制下节换页：
```text
officecli_run(command_args=["add", "F", "/body", "--type", "paragraph", "--prop", "text=CONFIDENTIAL — 仅限客户使用", "--prop", "align=center", "--prop", "size=9", "--prop", "color=C00000", "--prop", "spaceAfter=24"])
officecli_run(command_args=["add", "F", "/body", "--type", "paragraph", "--prop", "text=战略增长复盘", "--prop", "style=Title", "--prop", "size=32", "--prop", "bold=true", "--prop", "align=center", "--prop", "font=Cambria", "--prop", "spaceAfter=8"])
officecli_run(command_args=["add", "F", "/body", "--type", "paragraph", "--prop", "text=FY26 展望与情景规划", "--prop", "italic=true", "--prop", "size=16", "--prop", "align=center", "--prop", "spaceAfter=36"])
officecli_run(command_args=["add", "F", "/body", "--type", "paragraph", "--prop", "text=呈送：Acme 公司管理层", "--prop", "align=center", "--prop", "size=11"])
officecli_run(command_args=["add", "F", "/body", "--type", "paragraph", "--prop", "text=关键主题：1) 毛利韧性 2) EMEA 扩张 3) 资本配置", "--prop", "align=center", "--prop", "italic=true", "--prop", "size=10"])
officecli_run(command_args=["add", "F", "/body", "--type", "paragraph", "--prop", "text=执行摘要", "--prop", "style=Heading1", "--prop", "pageBreakBefore=true"])
```

**(b) Page X of Y 复合页脚。** 加页脚段落，再三个子操作拼出活值（官方 `help docx footer` 配方）：
```text
officecli_run(command_args=["add", "F", "/", "--type", "footer", "--prop", "type=default", "--prop", "text=Page ", "--prop", "align=center", "--prop", "size=9"])
officecli_run(command_args=["add", "F", "/footer[1]/p[1]", "--type", "field", "--prop", "fieldType=page"])
officecli_run(command_args=["add", "F", "/footer[1]/p[1]", "--type", "run", "--prop", "text= of "])
officecli_run(command_args=["add", "F", "/footer[1]/p[1]", "--type", "field", "--prop", "fieldType=numpages"])
```

**(c) 表头填充（填充顺序：先填文本 → 再填充 → 再 run 格式）。** 空单元格没有 run，`set .../tc[N]/p[1]/r[1]` 会报 "No r found"：
```text
officecli_run(command_args=["add", "F", "/body", "--type", "table", "--prop", "rows=5", "--prop", "cols=4", "--prop", "width=100%"])
officecli_run(command_args=["set", "F", "/body/tbl[1]/tr[1]", "--prop", "header=true", "--prop", "c1=季度", "--prop", "c2=营收", "--prop", "c3=增长", "--prop", "c4=状态"])
officecli_run(command_args=["set", "F", "/body/tbl[1]/tr[1]/tc[1]", "--prop", "fill=1F4E79"])
officecli_run(command_args=["set", "F", "/body/tbl[1]/tr[1]/tc[1]/p[1]/r[1]", "--prop", "bold=true", "--prop", "color=FFFFFF"])
officecli_run(command_args=["set", "F", "/body/tbl[1]/tr[3]/tc[1]", "--prop", "fill=D9E2F3"])  # 斑马纹
```

**(d) 财务表（数字右对齐、合计加粗、合计行底边框）。**
```text
officecli_run(command_args=["set", "F", "/body/tbl[1]/tr[2]/tc[2]/p[1]", "--prop", "align=right"])
officecli_run(command_args=["set", "F", "/body/tbl[1]/tr[5]/tc[1]/p[1]/r[1]", "--prop", "bold=true"])
officecli_run(command_args=["set", "F", "/body/tbl[1]/tr[4]/tc[1]/p[1]", "--prop", "pbdr.bottom=single;6;000000;0"])
```

**(e) 单元格内多要点（SWOT / 风险矩阵）。** `c1="a\nb"` 是 `<w:br/>` 软换行（单段落）；真正的子弹要点要独立段落：
```text
officecli_run(command_args=["set", "F", "/body/tbl[1]/tr[1]", "--prop", "c1=已安装 1.8 万企业席位"])
officecli_run(command_args=["add", "F", "/body/tbl[1]/tr[1]/tc[1]", "--type", "paragraph", "--prop", "text=毛利高于同业中位", "--prop", "listStyle=bullet"])
officecli_run(command_args=["set", "F", "/body/tbl[1]/tr[1]/tc[1]/p[1]", "--prop", "listStyle=bullet"])
```

**(f) 无字段引擎的 TOC。** CLI 无法算出可靠页码——保留活 TOC、`updateFields=true`，告诉收件人用 Word 打开重算。绝不用猜的静态页码替换。

## 设计质量线（每份文档须满足）

- **清晰层级**：非平凡文档必有 Title → H1 → H2 → 正文，不能是未排版 Normal 段落墙。`view outline` 若是一长条平铺列表即缺层级。
- **显式标题字号**（Word 默认样式字号会漂）：H1 ≥ 18pt（长报告 20pt），H2 = 14pt 粗体，H3 = 12pt 粗体，正文 11–12pt，行距 1.15–1.5x。优先 `style=Heading1` 而非内联尺寸（换主题只改定义一次）；无法信任模板样式时显式给尺寸。
- **一种正文 + 一种强调**：一种可读正文字体（Calibri/Cambria/Georgia/Times New Roman）；强调色用于标题或表头，不彩虹式排版。
- **间距靠属性**：用 `spaceBefore`/`spaceAfter`；空段落行会破坏分页并被 issues 标红。
- **排版质量**：新内容用弯引号（' " " "）而非 ASCII；范围用 en-dash（–），括号插用 em-dash（—）。
- **>1 页必有页眉页脚页码**：走活 `PAGE` 域，绝不写字面 "Page 1"。
- **保留现有模板**：编辑已有样式的文件时匹配其约定，本指南让位。

## QA 三道交付门（必跑）

**假设一定有问题——QA 是找 bug，不是确认。** 首次零 issues 通常代表查得不够细。`view outline` 可能显示 H3 直接挂在 H1 下；页脚可能显示 "Page 1" 但 `get --depth 3` 才露出它是静态 run 而非域。

1. `officecli_run(command_args=["view", "F", "issues"])` — 空段、缺 alt、间距异常。
2. `officecli_run(command_args=["view", "F", "outline"])` — 标题层级（无 H1→H3 跳跃）、TOC 是否存在、节数。
3. `officecli_run(command_args=["view", "F", "text", "--max-lines", "400"])` — 错别字、残留 `\$`/`\t`/`\n` 字面、占位符令牌。
4. `officecli_run(command_args=["validate", "F"])` — schema 校验（交付门在落盘文件上重跑）。
5. **视觉整页拼图**（能读图的 agent）：`view screenshot --grid auto -o /mnt/user-data/workspace/sheet.png` 然后 `file_read` 看。能看到分页、空白页、标题节奏、页边距、TOC/封面位置。Windows+Word 走真实 Word 渲染每页，其余走 HTML。截图失败则退回 `view html` 并在交付时标注"跨页对齐/节奏未视觉验证"。"validate 通过"不等于"可交付"，"看起来像真文档"才是。
6. 任一失败→修复→**重跑整轮**（一次修复常引入新问题）。

**域按结构判断，不按可见文本**：PAGE 页脚 `get /footer[N] --depth 3` 应见 begin/instrText/separate/cached/end 的 run 链（单个 PAGE ≥5 个 run，复合 "Page X of Y" ≥11 个）；只有一个含 "Page" 的 run = 域缺失，重加 `--prop field=page`。TOC `get /toc[1] --depth 2` 看域结构；页码可能读 `1 1 1 1` 直到重算。MERGEFIELD 用 `query 'field[fieldType=mergefield]'` 确认每槽一个、别处无字面 `{{name}}`。

**已知非缺陷（别追）**：`view text` 给每个编号列表项显示 `"1."`（实际正确递增）；`view issues` 在封面段、居中标题、列表项、参考文献条目上标"正文缺首行缩进"（块式专业文档里这些本就预期，仅 APA/学术正文需要首行缩进）。

## 常见误区

| 误区 | 正确做法 |
|---|---|
| 路径加引号 | **不要加引号**（argv 模型下引号变文件名一部分；上游 shell 写法不适用） |
| `--index` vs `[N]` | `--index` 是 0-based；`[N]` 路径是 1-based |
| 多个 `add --index N` 同值 | 每次插入把后续下移；复用 N 会让后加项排在前面——逆序插入，或 `move --after/--before` 锚定 paraId |
| `[last]` 谓语 | 必须 `[last()]` 带括号 |
| 间距用原始 twips | 用带单位的：`12pt`、`0.5cm`、`1.5x` |
| 空段落撑间距 | 用 `spaceBefore`/`spaceAfter` |
| 行级 `set` 做单元格格式 | 行 `set` 只支持 `height`/`header`/`c1..cN`；格式设在单元格段落/run |
| `listStyle` 设在 run | 它是段落属性 |
| 行首空格缩进 | `indent=720`/`firstLineIndent=360`/`hangingIndent=720` |
| 封面免页码用 `set differentFirstPage=true` | 不支持——加 first 类型空页脚：`--type footer --prop type=first --prop text=` |
| 新章节页 | 标题上 `pageBreakBefore=true`；显式 `pagebreak` 仅作替代，二者不可并用 |
| 单元格多子弹段落 | `c1="a\nb"` 是单段落软换行；真子弹要独立段落（配方 e） |
| 能用 dotted-attr 却用 `raw-set` | 优先 L2 dotted-attr（`pbdr.bottom=`/`ind.left=`），非必要不碰 L3 `raw-set` |
| 下段继承上一段 Heading 样式 | 在下一段显式 `--prop style=Normal` |
| `create` 后用 `style=Heading1` | 极简文档无内置样式，会回退引用；用真实模板或显式给尺寸 |
| `merge` 写成 `-o` | 位置参数：先 `<template>` 再 `<output>`，数据走 `--data` |
