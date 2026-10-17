# Word / DOCX 参考

OfficeCLI 对 `.docx` 提供语义路径 API（`/body/p[1]/r[2]`），几乎不需碰原始 XML。本文件覆盖读取、创建、结构化编辑、报告级配方、字段/目录、模板合并（`merge`）与交付前 QA。所有命令都通过 `officecli_run(command_args=[...])` 执行——**每个元素是一个独立参数，不要加引号、不要拼接成一条命令**。

> 排版口径说明：本文件只定义命令用法与结构校验，**不含独立排版口径**；示例中的字体/字号取值与 `standard-report.md` 样式映射表一致，照抄示例即符合标准。报告排版以标准汇报模版（`standard-report.md`）与党政机关公文（`gb-t-9704-2012.md`）两套口径为准。

## 执行模型（必读）

- 调用形态：`officecli_run(command_args=["add", "/mnt/user-data/workspace/report.docx", "/body", "--type", "paragraph", "--prop", "text=营收增长", "--prop", "font.ea=仿宋_GB2312", "--prop", "size=16"])`。路径、flag、值都是数组里的独立字符串。
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
officecli_run(command_args=["view", "/mnt/user-data/workspace/report.docx", "screenshot", "--grid", "auto", "-o", "/mnt/user-data/workspace/sheet.png"])
officecli_run(command_args=["view", "/mnt/user-data/workspace/report.docx", "issues"])    # 空段、缺 alt、间距异常；可用 --type format|content|structure 与 --limit N 收窄
officecli_run(command_args=["view", "/mnt/user-data/workspace/report.docx", "stats"])     # 段落计数、字体/样式分布
```

**检查单个元素**（XPath 风格、1-based；末位用 `[last()]` 带括号，`[last]` 报错）：
```text
officecli_run(command_args=["get", "/mnt/user-data/workspace/report.docx", "/body/tbl[1]", "--depth", "3"])
officecli_run(command_args=["get", "/mnt/user-data/workspace/report.docx", "/footer[1]", "--depth", "3"])  # 检查 fldChar 是否存在（域是否真为活值）
officecli_run(command_args=["get", "/mnt/user-data/workspace/report.docx", "/numbering", "--depth", "2"])
```

**跨文档查询**（CSS 风格选择器，`=`、`!=`、`~=`、`>=`、`<=`、`[attr]`、`:contains("文本")`、`:empty`、`:has(formula)`、`:no-alt`；布尔 and/or 可组合）：
```text
officecli_run(command_args=["query", "/mnt/user-data/workspace/report.docx", 'paragraph[style=Heading1]'])   # 全部 H1
officecli_run(command_args=["query", "/mnt/user-data/workspace/report.docx", 'p:empty'])                    # 空段落（噪音）
officecli_run(command_args=["query", "/mnt/user-data/workspace/report.docx", 'image:no-alt'])                # 无障碍缺口
officecli_run(command_args=["query", "/mnt/user-data/workspace/report.docx", 'field[fieldType!=page]'])     # 非 PAGE 的域
```

## 创建与编辑

动词：`add`（新建）、`set`（改属性）、`remove`、`move`、`swap`、`batch`、`raw-set`（最后手段 XML）。九成工作量是段落、run、表格、几张图、一个目录、一个页脚。

**修改方式先问后动。** 用户要求「修改 Word」时，若修改意图未指明方式，先问一句「直接修改，还是用修订（保留修改痕迹）？」再动手——直接修改对内容做静默变更，修订式用 `revision.*` 属性留痕可审可拒（见下方查找替换与批注/修订章节），两者结果形态完全不同，猜错要返工。用户已明确说「改一下就行/直接改」或「标注出来/留痕」时不必再问。

**执行策略：按场景选择写入方式**——新建文档铺整篇时优先把段落/表格生成为一个 JSON 数组走 `batch`（见下方 batch 章节），一次工具调用写完，段落只有两三个时逐条 `add` 更简单；编辑已有文档先 `view`/`query` 读结构并优先用 `@paraId` 定位目标，再批量或点状修改，含 remove/move 的批量建议改用 `@paraId`（位置索引会动态重算，易错位）。

### 查找与替换（编辑已有文档的主力）

`set` 顶层的 `--find` / `--replace`：**路径控制范围**（`/` 全文，`/body/p[N]` 单段，`/header[1]`/`/footer[1]` 页眉脚），匹配可**跨 run 边界**，不用自己拼选择器逐段改。

```text
# 全文替换（改错别字、统一术语、换年份）
officecli_run(command_args=["set", "F", "/", "--find", "2025年目标", "--replace", "2026年目标"])
# 只格式化不替换：匹配文本自动拆 run 后加格式（数字标红加粗等）
officecli_run(command_args=["set", "F", "/body/p[2]", "--find", "\\d+\\.\\d+%", "--prop", "regex=true", "--prop", "bold=true", "--prop", "color=C00000"])
# 修订式替换（保留痕迹）
officecli_run(command_args=["set", "F", "/", "--find", "草稿", "--replace", "终稿", "--prop", "revision.author=系统修订"])
```

规则与易错点：默认大小写敏感，不敏感用 `--prop find=(?i)error --prop regex=true`；**无匹配也是成功退出**，必须带 `--json` 读 `matched: N` 确认命中数，为 0 时检查文本是否跨样式 run；正则里的 `\d`、`\.` 在 command_args 数组元素里原样写一个反斜杠即可（无 shell 二次转义）。

**文本锚点插入**：`--after find:X` / `--before find:X` 按匹配文本定位插入点（与 `--index` 互斥）：内联元素（run/hyperlink/picture）插入匹配点旁，块类型（paragraph/table）会**自动拆开所在段落**插入。找不到锚文本即报错，比盲写位置索引稳。
```text
officecli_run(command_args=["add", "F", "/body/p[1]", "--type", "run", "--after", "find:利润率提升", "--prop", "text=（经审计）"])
```

**克隆**：`add F /body --from /body/p[3]` 带跨部分关系复制现有元素——复制带样式段落/表格再改文字，比从零拼 props 短得多。

### 段落、run、样式

段落 `p` 是块；run `r` 是块内一致字符格式。段落级属性（style、对齐、间距、缩进）设在 `p`；字体/字号/颜色/粗体设在 `r`。

```text
officecli_run(command_args=["add", "F", "/body", "--type", "paragraph", "--prop", "text=一、执行摘要", "--prop", "font.ea=黑体", "--prop", "font.latin=Times New Roman", "--prop", "size=16", "--prop", "bold=true", "--prop", "spaceAfter=12"])
officecli_run(command_args=["set", "F", "/body/p[1]/r[1]", "--prop", "color=1F4E79"])
```

纵向间距用 `spaceBefore`/`spaceAfter`，**绝不用空段落堆叠**（会被 `view issues` 标红）。左缩进用 `--prop indent=720`（twips）；首行缩进按标准正文用 `--prop firstLineIndent=32pt`（三号 16pt 的 2 字符）；行首空格会触发 issues。

**单位（易错点）**：`size` 的单位是**磅**，`size=16` 即 16pt（三号），照 standard-report.md 映射表的 pt 值原样写，**不要按 OOXML 半磅（w:sz）翻倍**——半磅只在你用 `raw-set` 直接改 `w:sz` 时才出现。`indent`/`firstLineIndent` 裸数是 twips、也可带单位（`32pt`）；`spaceBefore`/`spaceAfter`/`lineSpacing` 一律带单位（`12pt`、`0.5cm`、`1.5x`）。

> ⚠️ **`create` 产出的是极简文档，不含内置 Heading 样式。** `style=Heading1` 会回退为"按原样引用"（help 有 `WARNING: style 'Heading1' not found`）。报告类文档按 `standard-report.md` 样式映射表逐级显式设置字体/字号/行距（可 `view annotated` 样板 `standard-report-template.docx` 照抄具体配置）；用户提供了带样式的模板时以用户模板为准。TOC 只认内置 Heading 样式或带 `outlineLvl` 的自定义段落样式。

### 表格

`/body/tbl[N]`，行 `tr[N]`，单元格 `tc[N]`。先按行列数建，再填。同时给 `data=` 和显式 `rows=`/`cols=` 时，显式尺寸是**下限**：解析出的网格更小会补空行/列到显式值（1.0.145 起；此前全空行会被坍缩丢失，导致后续 `set tr[2]` 报 Row not found）。

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

### batch（JSON 批量，用 `--input` 文件）——新建文档的首选路径

三条关键语义（实测确认）：

1. **原子性**：一条命令失败，整批不落盘（`atomic: no changes were applied`，exit=1），不会产生写了一半的文档。逐条 ERROR 自带修复信息（合法元素类型、可用路径范围），按提示修 JSON 后整批重提即可。
2. **位置索引在 batch 内动态重算**：remove/move 之后的 `p[N]` 按最新结构解析，按删除前快照写的索引必错位。
3. **`@paraId` 是稳定锚点**：`view`/`get` 会显示每段的 `@paraId`，不受增删影响。**编辑已有文档时先读 paraId、用 `/body/p[@paraId=…]` 定位**，含 remove/move 的批量也安全；新建文档无此问题，直接顺序 add。

`batch` **不接受内联 JSON 位置参数**（实测 `officecli batch file '[{...}]'` 报 `Unrecognized command or argument`）。JSON 必须经 **`--input` 文件** 传入。无 shell 模型下最干净的做法：用 `file_write` 把 JSON 数组写成 workspace 临时文件，再交给 `batch --input`。结构：`command` 是裸动词，`parent`/`path`/`props` 是同级的字段。大批量建议按章节拆成多个 batch 文件，单批失败重提成本更低。默认原子；`--best-effort` 可改"成功的保留、失败的跳过"（dump 回放等允许有损的场景用），`--stop-on-error` 只控制遇错是否提前停止（剩余条目标记 skipped），需与 `--best-effort` 组合才能"停在首错且保留已成功项"。

```text
# 1) 用 file_write 把 batch JSON 写入 workspace 临时文件（无 shell 转义顾虑）
file_write(file_path="/mnt/user-data/workspace/_batch.json",
           content='[{"command":"add","parent":"/body","type":"paragraph","props":{"text":"（一）条款一","font.ea":"楷体_GB2312","font.latin":"Times New Roman","size":"16"}},{"command":"set","path":"/body/p[1]/r[1]","props":{"color":"1F4E79"}}]')
# 2) 用 --input 交给 batch（--input 指向的文件在 workspace 内，只读，不在 uploads）
officecli_run(command_args=["batch", "/mnt/user-data/workspace/report.docx", "--input", "/mnt/user-data/workspace/_batch.json"])
# 3) 可选清理
file_delete(file_path="/mnt/user-data/workspace/_batch.json")
```

> 注意：word 的 batch 与 pptx/xlsx 同构，但 `props` 里的 key 仍是 `--prop` 的 `key=value`（如 `text`、`style`、`color`）。`merge` 仍是独立子命令（见上），不要与 batch 混用。

batch 能力边界与常见用法（实测确认）：

- **能进 batch 的动词**：add/set/remove/move/swap/get/query/raw-set 等文档内容操作；`create`、`merge`、`import` 是文件级独立子命令，不进 batch（流程：`create` → `batch` → 需要时 `merge`/`import`）。
- **item 字段白名单**：`command, op, path, parent, type, from, index, after, before, to, path2, props, selector, text, mode, depth, part, xpath, action, xml, dumpversion`。写出白名单外字段整批被拒，报错会列出完整白名单。
- **元素易错点**：hyperlink 是内联元素，必须挂在段落里（`parent` 写 `/body/p[N]` 不能写 `/body`），属性名是 `url`（不是 address）；watermark 会自动创建 default/first/even 三个 header，之后再 add header 会报已存在，改水印文字用 `set /watermark --prop text=…`；raw-set 需要显式 `action`（append/prepend/insertbefore/insertafter/replace/remove/setattr）。
- **试错成本低**：任何一条失败整批回滚、文档不落半成品；逐条 ERROR 自带合法值列表和解法提示，修 JSON 重提即可，不会损坏文档。

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

> `refresh`（重算 TOC 页码 / PAGE / NUMPAGES / 交叉引用）：无 Word 的环境（1.0.147 起）自动走 **headless-HTML 回退**（返回 `backend: html`），但 HTML 分页与真实 Word 分页有偏差，算出的是**近似页码**——交付主路径仍是 `updateFields=true` 交给收件人 Word 打开时重算；`refresh` 只用于交付前预填一个比空白好的近似值并如实标注。`dump` 把子树序列化为可重放的 batch 脚本（辅助批量改造，回放配 `--best-effort` 容忍单条不支持项）。

## 分节与页码控制

docx 天然有一个**末节**（body 级 sectPr，路径 `/`）；`add / --type section` 在光标处插入**中间节**并产生分页。全部能力先 `help word section` 确认（schema 为准），以下是实测过（1.0.157）的标准路径。

**标准流程：一步一验证**——每步结构操作后立即 `get`/`query` 回读确认，再叠加下一步：

```text
# 1) 插入中间节（nextPage=新页起节；titlePage=该节首页独立页眉脚；pageStart=该节起始页码）
officecli_run(command_args=["add", "F", "/", "--type", "section", "--prop", "type=nextPage", "--prop", "titlePage=true", "--prop", "pageStart=1"])
# 2) 回读确认（属性在 format 里，见下方解析陷阱）
officecli_run(command_args=["get", "F", "/section[1]", "--json"])
# 3) 加页脚（自动挂到【末节】；field=page 为页码域）
officecli_run(command_args=["add", "F", "/", "--type", "footer", "--prop", "field=page", "--prop", "align=center"])
# 4) 回读确认挂接关系
officecli_run(command_args=["query", "F", "section", "--json"])
```

要点与实测边界：

- **分节属性解析陷阱**：`get`/`query` 返回的 JSON 里，分节属性在 `data.results[N].format` 下（`titlePage`/`pageStart`/`type`/`pageWidth` 等），不在 `data` 顶层。写解析代码前先看原始返回。
- **节属性写后持久**：`pageStart`（int）、`titlePage`（bool）、`pageNumFmt`、页边距、分栏数等 set 之后不会被后续段落/表格操作静默重置；若"看起来丢了"，先查解析路径再怀疑数据。
- **末节限制**：body 级 `/` 指向的末节**没有分页类型**——对 `/` 设 `type` 会被拒绝并提示改用 `/section[N]`，这不是 bug。
- **页脚挂接是单向的**：`add / --type footer`（type 取 `default`/`first`/`even`）自动挂到**末节**；中间节的 `footerRef`/`headerRef` 是**只读**属性，高层 API 无法把页脚挂到指定中间节。跨节自定义页脚只能 `raw-set` 往该节 sectPr 内插 `footerReference`（最后手段，插入位置错了会 schema 校验失败）——需求能简化成"全文档统一页脚 + 首页不同（titlePage+first 页脚）"就**不要**走跨节挂接。
- **titlePage 语义**：`titlePage=true` 只是启用"该节首页用 first 引用"，还要有 `--prop type=first` 的页眉/页脚才有内容，否则首页是空的。
- **验证以回读为准**：分节/页码是结构信息，`get`/`query` 回读是唯一可信依据。**HTML 预览是连续流，不反映分页与页码**（同 `refresh` 的 HTML 回退口径）；screenshot 的分页渲染也不保证体现 `pageStart`/分节符——不要用视觉渲染判断分节对错，避免"看着不对→盲改"循环。

## 报告级配方

每一条都经 `validate` 通过。占位符写法 `F` 在真实调用时替换为完整路径（如 `/mnt/user-data/workspace/report.docx`），不能把字面量 `F` 传给工具。

**(a) 封面页（标准汇报模版式）。** 主标题 + 副标题 + 汇报信息块，再强制下节换页进入正文；取值一律来自 standard-report.md 映射表：
```text
officecli_run(command_args=["add", "F", "/body", "--type", "paragraph", "--prop", "text=经营分析专题汇报", "--prop", "font.ea=方正小标宋简体", "--prop", "font.latin=Times New Roman", "--prop", "size=22", "--prop", "align=center", "--prop", "spaceAfter=8"])
officecli_run(command_args=["add", "F", "/body", "--type", "paragraph", "--prop", "text=（副标题或主题语）", "--prop", "font.ea=楷体_GB2312", "--prop", "font.latin=Times New Roman", "--prop", "size=16", "--prop", "align=center", "--prop", "spaceAfter=36"])
officecli_run(command_args=["add", "F", "/body", "--type", "paragraph", "--prop", "text=汇报单位　2026年8月", "--prop", "font.ea=仿宋_GB2312", "--prop", "font.latin=Times New Roman", "--prop", "size=14", "--prop", "align=center"])
officecli_run(command_args=["add", "F", "/body", "--type", "paragraph", "--prop", "text=一、执行摘要", "--prop", "font.ea=黑体", "--prop", "font.latin=Times New Roman", "--prop", "size=16", "--prop", "bold=true", "--prop", "pageBreakBefore=true"])
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
- **排版口径外置**：字体/字号/行距以标准汇报模版（`standard-report.md`）或党政机关公文（`gb-t-9704-2012.md`）为准，本文件不提供第三套通用口径；新建文档按映射表逐级显式配置（create 无内置样式），用户模板带标题样式时可沿用其样式。
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
5. **视觉检查**：优先 `view screenshot`（真实分页渲染，能查分页位置、空白页、标题节奏、页边距、TOC/封面位置），截图后用 `file_read` 读图复核。`view screenshot` 报 `No headless browser available` 表示该环境未装无头浏览器，改用 `view html` 做近似视觉检查，**不要尝试安装浏览器**（浏览器由运维统一安装）。截图拼图可能把页面放在画布左侧、右侧/底部留空灰底（渲染视口大于纸面），这是渲染特性，不是文档版式问题。HTML 渲染不响应 keepNext/pageStart 等分页属性，分页细节以 `validate`/`view issues` 为准。无法视觉验证时在交付标注"跨页对齐/节奏未视觉验证"。"validate 通过"不等于"可交付"。
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
| 逐段 `set` 改错别字/统一术语 | 一条 `set F / --find X --replace Y` 搞定（跨 run 匹配），带 `--json` 读 `matched` 确认命中数 |
| `listStyle` 设在 run | 它是段落属性 |
| 行首空格缩进 | `indent=720`/`firstLineIndent=360`/`hangingIndent=720` |
| 封面免页码用 `set differentFirstPage=true` | 不支持——加 first 类型空页脚：`--type footer --prop type=first --prop text=` |
| 新章节页 | 标题上 `pageBreakBefore=true`；显式 `pagebreak` 仅作替代，二者不可并用 |
| 单元格多子弹段落 | `c1="a\nb"` 是单段落软换行；真子弹要独立段落（配方 e） |
| 能用 dotted-attr 却用 `raw-set` | 优先 L2 dotted-attr（`pbdr.bottom=`/`ind.left=`），非必要不碰 L3 `raw-set` |
| 下段继承上一段 Heading 样式 | 在下一段显式 `--prop style=Normal` |
| `create` 后用 `style=Heading1` | 极简文档无内置样式，会回退引用；报告按 `standard-report.md` 映射表显式设置，或沿用用户模板样式 |
| `size` 按半磅翻倍（22pt 写 44） | `size` 单位是磅，映射表 pt 值原样写；半磅只出现在 `raw-set` 改 `w:sz` 时 |
| 行距写成 `line` | 属性名是 `lineSpacing=28pt` 配 `lineRule=exact`；不存在 `line` |
| `merge` 写成 `-o` | 位置参数：先 `<template>` 再 `<output>`，数据走 `--data` |
| batch 条目漏 `command` 字段 | 每个条目必须有 `command`（裸动词 `add`/`set`/…），缺了整批被拒 `missing required 'command' field` |
| batch/单条命令里属性裸写 `text=xxx` | 单条命令必须 `--prop text=xxx`；batch JSON 里属性是 `props` 对象的 key，不带 `--prop` 前缀，两种形态别混 |
| 用 `file_write` 造 .docx，或指望 `add` 自动建文件 | .docx 是 ZIP 包，0 字节不是合法文档——新建唯一入口是 `officecli create`（或 `file_copy` 已有文件），`add`/`batch` 只能改已存在的合法文档 |
| 在 `get`/`query` 的 JSON 顶层找分节属性 | 分节属性在 `data.results[N].format` 下（`pageStart`/`titlePage`/`type`…），先看原始返回再写解析 |
| 用 HTML 预览/screenshot 判断分节、页码对错 | 结构回读（`get`/`query` section）是唯一可信依据；HTML 预览不分页、截图页码不保证反映 `pageStart` |
| 想给中间节挂独立页脚就反复 `set` 试探 | 中间节 `footerRef` 只读，高层 API 挂不了；需求能收敛成"统一页脚 + titlePage + first 页脚"就别跨节挂接 |
