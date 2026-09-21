# PowerPoint 操作参考

本文件覆盖 `.pptx` 的创建、读取、编辑与交付审查。所有命令都通过 `officecli_run(command_args=[...])` 执行——**每个元素是一个独立参数，不要加引号、不要拼成一条命令**（详见下方"执行模型"）。`docx`/`xlsx` 见同目录 `word.md` / `excel.md`。

## 执行模型（officecli_run 的 argv 约定）

```python
# 正确：每个 token 独立成元素，路径含空格/选择器含 [N] 照原样写
officecli_run(command_args=["view", "/mnt/user-data/workspace/经营汇报.pptx", "outline"])
officecli_run(command_args=["add", "/mnt/user-data/workspace/经营汇报.pptx", "/slide[1]",
                            "--type", "shape", "--prop", "name=Title", "--prop", "text=销售额同比增长"])

# 错误：把整条命令当一个字符串、或给路径加引号
officecli_run(command_args=["view '/mnt/user-data/workspace/经营汇报.pptx' outline"])   # 引号会变成文件名一部分
```

- **不要使用 `open` / `close`**：这两个子命令被 `officecli_run` 禁用（常驻进程会锁文件）。每次命令是独立的编辑周期。
- **修改命令成功后运行时会同步 `workspace`/`outputs` 文件**。需要显式确认 OfficeCLI 刷新文件时再调用 `save`，不必每条修改后重复调用；交给渲染/交付前可调用一次 `save`。
- **`$` 不会被 shell 展开**（我们无 shell），但 CLI 层的转义仍生效：`\n` = 段落换行、`\t` = 制表符；要写字面反斜杠-n 用 `\\n`。
- **Help-First**：属性名、枚举值、`prop` vs 别名不确定时先查 help，不要猜：
  - `officecli_run(command_args=["help", "pptx"])` —— 列出所有 pptx 元素
  - `officecli_run(command_args=["help", "pptx", "shape"])` —— 元素 schema
  - `officecli_run(command_args=["help", "pptx", "add", "chart"])` —— 动词级 schema
  - `officecli_run(command_args=["help", "pptx", "<元素>", "--json"])` —— 机器可读 schema
  - 当技能文档与 help 冲突时，**help 为准**。出现 `UNSUPPORTED props:` 警告、未知动画预设、枚举漂移时立即查 help。

## 读取与分析（Reading & Analysis）

先宽后窄：`outline` 看全局，再 `get` / `query` / `view text` 定位细节。

```python
officecli_run(command_args=["view", file, "outline"])     # 页数 + 每页标题
officecli_run(command_args=["view", file, "annotated"])   # 每页完整明细：字体/字号/表格/图表
officecli_run(command_args=["view", file, "text", "--start", "1", "--end", "5"])  # 文本转储（含表格单元格）
officecli_run(command_args=["view", file, "issues"])      # 空页、溢出、对比度问题
officecli_run(command_args=["view", file, "stats"])       # 计数与汇总（含缺 alt 的图片）
```

**单元素检查**。XPath 风格路径，1-based。优先用 `@name=` / `@id=` 选择器（随增删稳定），而非位置 `/shape[3]`：

```python
officecli_run(command_args=["get", file, "/slide[1]", "--depth", "1"])            # 形状列表（含 id 与 name）
officecli_run(command_args=["get", file, "/slide[1]/shape[@name=Title]"])         # 按命名定位
officecli_run(command_args=["get", file, "/slide[1]/table[1]", "--depth", "3"])    # 表格行列/单元格
```

**跨 deck 查询**。CSS 风格选择器，支持 `=`、`!=`、`~=`、`>=`、`<=`、`[attr]`、`:contains()`、`:no-alt`：

```python
officecli_run(command_args=["query", file, 'shape:contains("Revenue")'])
officecli_run(command_args=["query", file, "picture:no-alt"])          # 无障碍缺口
officecli_run(command_args=["query", file, "shape[fill=1E2761]"])      # 颜色匹配
officecli_run(command_args=["query", file, "shape[width>=10cm]"])      # 数值过滤
```

- `query --json` 的结果包在 `.data.results[]` 里（取 `.data.results[0].format.id`，不是 `.[0].id`）；形状名是 `.name`，填充 `.format.fill`，文字色 `.format.textColor`。
- **视觉预览（优先）**：`view <file> html` 会打印一个 HTML 预览路径，用 `file_read` 打开它做逐页结构审查（最佳结构真相）；`view <file> svg --page N` 出单页 SVG（图表与渐变在 SVG 不渲染）。

**已知非缺陷**：`layout=blank` 没有标题占位符，标题就是普通 `shape`，所以 `view outline` 报 `(untitled)` 是**预期**的，不是缺陷。

## 创建与编辑（Creating & Editing）

动词：`add` / `set` / `remove` / `move` / `swap` / `batch`。九成工作量是幻灯片、形状、文本、少量图表、图片、连接器。

### 幻灯片与背景

每页是 `/slide[N]`。自定义设计一律 `layout=blank`。背景支持纯色、渐变、图片：

```python
officecli_run(command_args=["add", file, "/", "--type", "slide", "--prop", "layout=blank", "--prop", "background=1E2761"])
officecli_run(command_args=["add", file, "/", "--type", "slide", "--prop", "layout=blank", "--prop", "background=1E2761-CADCFC-180"])  # 渐变 start-end-angle
officecli_run(command_args=["add", file, "/", "--type", "slide", "--prop", "layout=blank", "--prop", "background=image:/mnt/user-data/workspace/hero.jpg"])
```

### 形状

`shape` 承载文字、填充、边框、位置，以及可选的动画/链接。

```python
officecli_run(command_args=["add", file, "/slide[2]", "--type", "shape", "--prop", "name=Title", "--prop", "text=关键洞察",
                            "--prop", "x=2cm", "--prop", "y=2cm", "--prop", "width=20cm", "--prop", "height=3cm",
                            "--prop", "font=Georgia", "--prop", "size=36", "--prop", "bold=true", "--prop", "color=1E2761", "--prop", "fill=none"])
```

- 位置是显式的——没有布局引擎，网格坐标自己算。`--prop preset=` 选几何（`rect` / `roundRect` / `ellipse` / `triangle` / `arrow` / `star5` …）；不支持自定义 `M…Z` 路径，选 preset。
- **创建时命名**（`--prop name=HeroTitle`），之后用 `/slide[N]/shape[@name=HeroTitle]` 访问——名字在 z 序/删后重建后都稳定，而位置 `/shape[3]`（甚至 `@id=`）会漂移。任何结构改动后重新 `get --depth 1` 再使用位置索引。

### 形状内文本（段落 / run / 样式）

形状有段落 `paragraph[K]` 与 run `run[K]`。单行文本直接 `--prop text=`；文本里的 `\n` 是段落换行、`\t` 是制表符（双写 `\\n` 得字面反斜杠-n）。`add --type paragraph` 接受与形状相同的样式属性；行内混合样式追加一个 styled run：

```python
officecli_run(command_args=["add", file, "/slide[2]/shape[@name=Card1]/paragraph[1]", "--type", "run",
                            "--prop", "text= (内联细节)", "--prop", "size=14", "--prop", "italic=true", "--prop", "color=8899BB"])
```

### 图表

按下方"图表选型表"选类型。完整属性（`chartType` 枚举、`seriesN.*`、`data=`/`categories=`、坐标轴）查 `help pptx add chart`：

```python
officecli_run(command_args=["add", file, "/slide[3]", "--type", "chart", "--prop", "chartType=column",
                            "--prop", "series1.name=营收", "--prop", "series1.values=42,45,48", "--prop", "series1.color=1E2761",
                            "--prop", "series2.name=增长", "--prop", "series2.values=2,7,7", "--prop", "series2.color=CADCFC",
                            "--prop", "categories=Q1,Q2,Q3",
                            "--prop", "x=2cm", "--prop", "y=4cm", "--prop", "width=20cm", "--prop", "height=10cm"])
```

陷阱：(1) 图表标题里的 `(`、`[`、占位符（如 `TBD`、`{{x}}`）会按字面显示；(2) 部分查看器会把图表颜色归一到主题色——在目标查看器里复核。系列可创建后再加（`add --type series`）。

### 图片

```python
officecli_run(command_args=["add", file, "/slide[4]", "--type", "picture", "--prop", "src=/mnt/user-data/workspace/hero.jpg",
                            "--prop", "x=1cm", "--prop", "y=1cm", "--prop", "width=32cm", "--prop", "height=18cm",
                            "--prop", "alt=产品主视觉，右侧渐变打光"])
```

交付前确认 `query <file> picture:no-alt` 为空。

### 连接器（流程图 / 决策树一等公民）

画两个形状或坐标之间的连线。完整属性/枚举（`shape`、`headEnd`/`tailEnd`、`from`/`to` 引用形式）查 `help pptx add connector`：

```python
officecli_run(command_args=["add", file, "/slide[5]", "--type", "connector",
                            "--prop", "from=/slide[5]/shape[@name=BoxA]", "--prop", "to=/slide[5]/shape[@name=BoxB]",
                            "--prop", "shape=elbow", "--prop", "color=333333", "--prop", "tailEnd=triangle"])
```

**每个流程连接器都要箭头**（`tailEnd=`）。没有箭头 `bentConnector3` 会渲染成无方向的线。

### 动画

按"设计质量线"的动画三原则使用（有目的、可优雅降级、真机复核）。预设名与时长语法查 `help pptx animation`：

```python
officecli_run(command_args=["set", file, "/slide[2]/shape[@name=HeroCard]", "--prop", "animation=fade-entrance-400"])
officecli_run(command_args=["set", file, "/slide[2]/shape[@name=HeroCard]", "--prop", "animation=none"])  # 清除全部
```

### 超链接 / 工具提示 / 页内跳转

`--prop link=slide[N]`（1-based，目标页须存在）、`link=nextslide`/`firstslide`/`lastslide`/`previousslide`/`endshow`、`link=https://...`；`--prop tooltip=...` 设悬停文本。

### 表格 / 占位符 / 组 / zoom / 批注（一行速查）

- **表格**：`--type table --prop rows=N --prop cols=M`。行级 `set` 支持 `height` 与 `c1/c2/c3`（种子单元格文本）。表头样式是表级（`firstRow=true` / `headerFill=`），不是行属性。单元格格式在单元格段落/run 上。**先填行、再设表级字体**（字体级联会被行操作重置）。
- **占位符**：仅当幻灯片用了带占位符的 layout（非 `layout=blank`）时可用，`/slide[N]/placeholder[title]` / `placeholder[body]`。
- **组**：`/slide[N]/group[@name=G]/shape[1]`，比位置索引更抗重排。
- **zoom 页**：`--type zoom --prop target=N`（每目标一个链接；别名 `slide`）。zoom 是运行时特性，`view html` 只看静态几何。
- **批注**：锚在 `/slide[N]/comment[M]`，完整生命周期 `add/set/get/query/remove`；属性 `text`/`author`/`initials`/`date`(ISO8601)/`x`/`y`。

### batch（JSON 批量，用 `--input` 文件）

`batch` **不接受内联 JSON 位置参数**（实测 `officecli batch file '[{...}]'` 报 `Unrecognized command or argument`）。JSON 必须经 **`--input` 文件** 传入。无 shell 模型下最干净的做法：用 `file_write` 把 JSON 数组写成 workspace 临时文件，再交给 `batch --input`。

- **不要写 `<<EOF` heredoc、`$FILE`/`$SLIDE` 等 shell 变量**——agent 需先把实际路径/页码替换进 JSON 再 `file_write`。
- 优先用 `/slide[last()]` 指代刚加的页，省去索引算术。
- 推荐块大小 ≤ 50 ops/块（纯值设置可 80+）。

```python
# 1) 用 file_write 把 batch JSON 写入 workspace 临时文件（无 shell 转义顾虑）
file_write(file_path="/mnt/user-data/workspace/_batch.json",
           content='[{"command":"add","parent":"/slide[last()]","type":"shape","props":{"name":"Step1","preset":"roundRect","fill":"1E2761","line":"none","x":"1.5cm","y":"8cm","width":"6cm","height":"3cm","text":"Step 1","font":"Georgia","size":"20","bold":"true","color":"FFFFFF","align":"center","valign":"middle"}},'
                   '{"command":"add","parent":"/slide[last()]","type":"shape","props":{"name":"Step2","preset":"roundRect","fill":"CADCFC","line":"none","x":"9.79cm","y":"8cm","width":"6cm","height":"3cm","text":"Step 2","font":"Georgia","size":"20","bold":"true","color":"1E2761","align":"center","valign":"middle"}}]')
# 2) 用 --input 交给 batch（--input 指向的文件在 workspace 内，只读，不在 uploads）
officecli_run(command_args=["batch", "/mnt/user-data/workspace/deck.pptx",
                            "--input", "/mnt/user-data/workspace/_batch.json"])
# 3) 可选清理
file_delete(file_path="/mnt/user-data/workspace/_batch.json")
```

## Deck 级配方（Deck-level recipes）

下面每个配方先给**视觉结果**，再给可运行块。用 `/slide[last()]` 指代刚加的页。坐标示例用深蓝 `1E2761` + Georgia 只是范例主题，**请换成你为本主题选的配色/字体**，不要照抄范例色。

### (a) 封面 / 分隔页

深蓝填充、居中 44pt 标题、18pt 冰蓝副标题。

```python
officecli_run(command_args=["add", file, "/", "--type", "slide", "--prop", "layout=blank", "--prop", "background=1E2761"])
officecli_run(command_args=["add", file, "/slide[last()]", "--type", "shape", "--prop", "text=战略增长复盘",
                            "--prop", "x=2cm", "--prop", "y=7cm", "--prop", "width=29.87cm", "--prop", "height=3cm",
                            "--prop", "font=Georgia", "--prop", "size=44", "--prop", "bold=true", "--prop", "color=FFFFFF", "--prop", "align=center"])
officecli_run(command_args=["add", file, "/slide[last()]", "--type", "shape", "--prop", "text=致 Acme 管理层 — FY26 展望",
                            "--prop", "x=2cm", "--prop", "y=11cm", "--prop", "width=29.87cm", "--prop", "height=1.2cm",
                            "--prop", "font=Calibri", "--prop", "size=18", "--prop", "color=CADCFC", "--prop", "align=center"])
```

**分隔页** = 同上，加一个超大半透明数字（`size=120`、`opacity=0.15`）且**最先加**让它垫在标题背后。

### (b) 数据页（图表 + 注释卡）

左 2/3 列图表（品牌系列色），右 1/3 是"关键洞察"卡（20pt 标题 + 18pt 正文）——观众先读结论再看柱。

```python
officecli_run(command_args=["add", file, "/", "--type", "slide", "--prop", "layout=blank", "--prop", "background=FFFFFF"])
officecli_run(command_args=["add", file, "/slide[last()]", "--type", "shape", "--prop", "text=FY26 营收超计划 18%",
                            "--prop", "x=1.5cm", "--prop", "y=1cm", "--prop", "width=30cm", "--prop", "height=1.8cm",
                            "--prop", "font=Georgia", "--prop", "size=36", "--prop", "bold=true", "--prop", "color=1E2761"])
# 图表 — 左 2/3
officecli_run(command_args=["add", file, "/slide[last()]", "--type", "chart", "--prop", "chartType=column",
                            "--prop", "series1.name=实际", "--prop", "series1.values=42,45,48,55", "--prop", "series1.color=1E2761",
                            "--prop", "series2.name=计划", "--prop", "series2.values=40,42,45,48", "--prop", "series2.color=CADCFC",
                            "--prop", "categories=Q1,Q2,Q3,Q4", "--prop", "x=1.5cm", "--prop", "y=3.5cm", "--prop", "width=20cm", "--prop", "height=14cm", "--prop", "title=FY26 营收"])
# 注释卡 — 右 1/3：底 + 标题 + 正文
officecli_run(command_args=["add", file, "/slide[last()]", "--type", "shape", "--prop", "preset=roundRect", "--prop", "fill=F5F7FA", "--prop", "line=none",
                            "--prop", "x=22.5cm", "--prop", "y=3.5cm", "--prop", "width=9.8cm", "--prop", "height=14cm"])
officecli_run(command_args=["add", file, "/slide[last()]", "--type", "shape", "--prop", "text=关键洞察",
                            "--prop", "x=23cm", "--prop", "y=4cm", "--prop", "width=9cm", "--prop", "height=1.2cm",
                            "--prop", "font=Georgia", "--prop", "size=20", "--prop", "bold=true", "--prop", "color=1E2761"])
officecli_run(command_args=["add", file, "/slide[last()]", "--type", "shape", "--prop", "text=EMEA 上线 + NRR 118% 贡献了 18pp 中的 12pp。",
                            "--prop", "x=23cm", "--prop", "y=5.5cm", "--prop", "width=9cm", "--prop", "height=11cm",
                            "--prop", "font=Calibri", "--prop", "size=18", "--prop", "color=333333"])
```

### (c) 流程图（盒子 + 连接器）

四个圆角盒横排于 y=8cm，每 6×3cm，深蓝/冰蓝交替，elbow 连接器带三角箭头。

网格算法（4 盒，33.87cm 页宽，1.5cm 边距）：`gap = (33.87 − 3 − 24) / 3 = 2.29cm`，x 位置：`1.5, 9.79, 18.08, 26.37`。

```python
# 用 batch 一次性加四个盒（file_write 落 JSON + batch --input）
file_write(file_path="/mnt/user-data/workspace/_batch.json",
           content='[{"command":"add","parent":"/slide[last()]","type":"shape","props":{"name":"Step1","preset":"roundRect","fill":"1E2761","line":"none","x":"1.5cm","y":"8cm","width":"6cm","height":"3cm","text":"Step 1","font":"Georgia","size":"20","bold":"true","color":"FFFFFF","align":"center","valign":"middle"}},'
                   '{"command":"add","parent":"/slide[last()]","type":"shape","props":{"name":"Step2","preset":"roundRect","fill":"CADCFC","line":"none","x":"9.79cm","y":"8cm","width":"6cm","height":"3cm","text":"Step 2","font":"Georgia","size":"20","bold":"true","color":"1E2761","align":"center","valign":"middle"}},'
                   '{"command":"add","parent":"/slide[last()]","type":"shape","props":{"name":"Step3","preset":"roundRect","fill":"1E2761","line":"none","x":"18.08cm","y":"8cm","width":"6cm","height":"3cm","text":"Step 3","font":"Georgia","size":"20","bold":"true","color":"FFFFFF","align":"center","valign":"middle"}},'
                   '{"command":"add","parent":"/slide[last()]","type":"shape","props":{"name":"Step4","preset":"roundRect","fill":"CADCFC","line":"none","x":"26.37cm","y":"8cm","width":"6cm","height":"3cm","text":"Step 4","font":"Georgia","size":"20","bold":"true","color":"1E2761","align":"center","valign":"middle"}}]')
officecli_run(command_args=["batch", file, "--input", "/mnt/user-data/workspace/_batch.json"])
file_delete(file_path="/mnt/user-data/workspace/_batch.json")
# 连接器：每个盒子之间一个，复用此模式
officecli_run(command_args=["add", file, "/slide[last()]", "--type", "connector",
                            "--prop", "from=/slide[last()]/shape[@name=Step1]", "--prop", "to=/slide[last()]/shape[@name=Step2]",
                            "--prop", "shape=elbow", "--prop", "color=333333", "--prop", "tailEnd=triangle"])
# Step2→Step3、Step3→Step4 同理
```

### (d) 多页骨架节奏

不是代码块，是节奏。下面是一组可行的韵律（交替深蓝分隔页 + 白底内容页），**不是强制顺序**——先从内容推导叙事弧，再借用合适的分隔/内容节奏：
- **10 页复盘**：封面 · 议程 · 3 KPI · 分01 · 图表 · 图表 · 分02 · 流程 · 时间线 · 收尾
- **20 页路演**：同上 ×2，分节 Problem · Solution · Market · Product · Traction · Model · Team · Financials · Ask
- 每个分隔页必须出现在其**内容之前**（顺序门会查）
- 封面/分隔页 = (a)；图表页 = (b)；流程页 = (c)；KPI 页 = (e)；决策页 = (f)

### (e) KPI 大数字卡

一行巨号数字 + 小单位副标 + 单色变化芯片。执行 deck 最常见的元素。

字号规则：60pt Georgia 粗体在 9.78cm 卡里约放 5 个字符（`84.2`、`118%`、`24.5`）。更长的值（`84.2M`）拆开：`84.2` 当大数字、`USD millions` 当副标——**不要为塞单位而缩字号**，会换行。

网格算法（3 卡，1.5cm 边距，0.76cm 间距）：`col_width = (33.87 − 3 − 1.52) / 3 = 9.78cm`，x 位置：`1.5, 12.04, 22.58`。用 accent 色标一张"watch"卡让风险一秒可辨。

```python
# 两张卡：深蓝标准 + 赤陶 watch。每卡 = 底 + 大数字 + 副标 + 芯片
# file_write 落 JSON + batch --input
file_write(file_path="/mnt/user-data/workspace/_batch.json",
           content='[{"command":"add","parent":"/slide[last()]","type":"shape","props":{"preset":"roundRect","fill":"1E2761","line":"none","x":"1.5cm","y":"4cm","width":"9.78cm","height":"7cm"}},'
                   '{"command":"add","parent":"/slide[last()]","type":"shape","props":{"text":"84.2","x":"1.5cm","y":"4.8cm","width":"9.78cm","height":"2.8cm","font":"Georgia","size":"60","bold":"true","color":"FFFFFF","align":"center"}},'
                   '{"command":"add","parent":"/slide[last()]","type":"shape","props":{"text":"USD millions · ARR","x":"1.5cm","y":"8cm","width":"9.78cm","height":"0.8cm","font":"Calibri","size":"14","color":"CADCFC","align":"center"}},'
                   '{"command":"add","parent":"/slide[last()]","type":"shape","props":{"text":"+24% YoY","x":"1.5cm","y":"9cm","width":"9.78cm","height":"0.8cm","font":"Calibri","size":"14","bold":"true","color":"CADCFC","align":"center"}},'
                   '{"command":"add","parent":"/slide[last()]","type":"shape","props":{"preset":"roundRect","fill":"B85042","line":"none","x":"22.58cm","y":"4cm","width":"9.78cm","height":"7cm"}},'
                   '{"command":"add","parent":"/slide[last()]","type":"shape","props":{"text":"1.42","x":"22.58cm","y":"4.8cm","width":"9.78cm","height":"2.8cm","font":"Georgia","size":"60","bold":"true","color":"FFFFFF","align":"center"}},'
                   '{"command":"add","parent":"/slide[last()]","type":"shape","props":{"text":"CAC payback (yrs)","x":"22.58cm","y":"8cm","width":"9.78cm","height":"0.8cm","font":"Calibri","size":"14","color":"FFFFFF","align":"center"}},'
                   '{"command":"add","parent":"/slide[last()]","type":"shape","props":{"text":"+8% — watch","x":"22.58cm","y":"9cm","width":"9.78cm","height":"0.8cm","font":"Calibri","size":"14","bold":"true","color":"FFFFFF","align":"center"}}]')
officecli_run(command_args=["batch", file, "--input", "/mnt/user-data/workspace/_batch.json"])
file_delete(file_path="/mnt/user-data/workspace/_batch.json")
```

### (f) 决策树（YES/NO 分支）

菱形置顶居中；YES/NO 子盒左右分叉；两者汇入同一终止盒。约定：红=停止/升级，蓝=标准，绿=安全终止。**每个连接器都要箭头**。

布局：菱形 `x=13.94, y=2cm, 6×3cm`；YES `3cm, 7.5cm`；NO `22.87cm, 7.5cm`；终止 `13.94cm, 13cm`。

```python
file_write(file_path="/mnt/user-data/workspace/_batch.json",
           content='[{"command":"add","parent":"/slide[last()]","type":"shape","props":{"name":"Decide","preset":"diamond","fill":"1E2761","line":"none","x":"13.94cm","y":"2cm","width":"6cm","height":"3cm","text":"存在危险能量？","font":"Calibri","size":"14","bold":"true","color":"FFFFFF","align":"center","valign":"middle"}},'
                   '{"command":"add","parent":"/slide[last()]","type":"shape","props":{"name":"YesBox","preset":"roundRect","fill":"B85042","line":"none","x":"3cm","y":"7.5cm","width":"8cm","height":"3cm","text":"上锁 + 挂牌 + 验证","font":"Calibri","size":"16","bold":"true","color":"FFFFFF","align":"center","valign":"middle"}},'
                   '{"command":"add","parent":"/slide[last()]","type":"shape","props":{"name":"NoBox","preset":"roundRect","fill":"CADCFC","line":"none","x":"22.87cm","y":"7.5cm","width":"8cm","height":"3cm","text":"按标准 PPE 进行","font":"Calibri","size":"16","bold":"true","color":"1E2761","align":"center","valign":"middle"}},'
                   '{"command":"add","parent":"/slide[last()]","type":"shape","props":{"name":"Done","preset":"roundRect","fill":"2C5F2D","line":"none","x":"13.94cm","y":"13cm","width":"6cm","height":"2.5cm","text":"开始作业","font":"Calibri","size":"16","bold":"true","color":"FFFFFF","align":"center","valign":"middle"}}]')
officecli_run(command_args=["batch", file, "--input", "/mnt/user-data/workspace/_batch.json"])
file_delete(file_path="/mnt/user-data/workspace/_batch.json")
# 4 个连接器：Decide→YesBox、Decide→NoBox、YesBox→Done、NoBox→Done（复用 (c) 的 connector 模式）
```

## 设计质量线（Design floor）

交付前必须满足的底线。违反任意一条 = 没做完，与内容好坏无关。

### 类型层级（显式设置，别依赖主题默认）

| 元素 | 最小 | 典型 | 最小形状高 |
|---|---|---|---|
| 幻灯片标题 | **≥ 36pt** 粗体 | 36–44pt | ≥ 2cm |
| 分节/副标题 | ≥ 20pt | 20–24pt | ≥ 1.2cm |
| 正文 | **≥ 18pt** | 18–22pt | ≥ 1cm |
| 标题/轴标签 | ≥ 10pt 柔和 | 10–12pt | ≥ 0.6cm |

经验：最小形状高 ≈ 字号pt × 0.05cm。18pt 副标放在 0.8cm 高的框会溢出（`view annotated` 能抓到）。标题必须 **≥ 2× 正文**（36pt 配 20pt 合适）。左对齐正文，只居中标题与巨型数字。正文 < 18pt 的 4 个合法例外：图表轴标签、图例、页脚/页码、≤5 词 KPI 副标。

**每页一图（承载信息，非装饰）**。形状、图表、图标、承载含义的渐变带都算；纯要点墙跟 Word 文档没区别。例外：引用页、代码块、单张汇总表。

**每内容页加演讲者备注**（`--type notes --prop text=...`）。

**文案像人写的，不像 AI**。标题讲内容不玩噱头；不用"不是 X，是 Y"、制造紧张、假洞察、单词装腔（"Momentum."）；砍掉 hype 形容词（seamless、robust、game-changing）。

**保留既有模板**：文件已有主题与母版就匹配它，惯例优先于本指南。

### 视觉交付地板（每页都满足）

- **没有占位符当内容渲染**：`{{name}}`、`<TODO>`、`lorem`、图表标题里空 `()`/`[]` 不得出现。
- **不溢出页边、形状内文字不被裁**：`view issues` 会标出（`shape_off_slide` + 文本适配提示）。修复裁切靠放大框或缩短值，**绝不为了塞下而砍内容**。
- **封面带定向元素**：标题 + 副标题 + 演讲者/客户 + 日期 + 品牌带或关键结论条；只放标题的封面像占位符。
- **对比度**：`view issues` 自动标常见情况（深色文字落在形状自身深色填充上 `low_contrast`），但它看不到图标/图表系列填充、或文字压在**另一**背景形状上的情况。所以任何亮度 < 30% 的填充（`1E2761`、`36454F`、深林/莓/樱桃）上，仍要确认每个正文 run、卡体、图表系列、图标是 `FFFFFF` 或亮度 > 80%——中灰（`6B7B8D`≈44%）在笔记本上能看、投屏上消失。深填充通过后用 `view html` 抽查。

### 字体配对 / 配色 / 图表选型（速查种子）

- **两字体一配色**：一个标题字体 + 一个正文字体（如 Georgia + Calibri）；一个主品牌色（60–70% 权重）+ 一个辅助 + 一个强调。正文别混 4+ 色。用户给了品牌色/字体或既有模板先匹配，否则用下表作校准种子。
- **字体**：Georgia/Calibri（金融/高管）、Arial Black/Arial（营销发布）、Calibri/Calibri Light（极简）、Cambria/Calibri（传统/法务）、Trebuchet MS/Calibri（友好科技）、Impact/Arial（bold 标题）、Palatino/Garamond（优雅编辑）、Consolas/Calibri（工程）。每个形状显式设 `--prop font=`。
- **配色**（主/辅/强调/文字/柔和）：Midnight Executive `1E2761`/`CADCFC`/`FFFFFF`/`333333`/`8899BB`；Forest & Moss `2C5F2D`/`97BC62`/`F5F5F5`/`2D2D2D`/`6B8E6B`；Charcoal `36454F`/`F2F2F2`/`212121`/`333333`/`7A8A94`；Coral Energy `F96167`/`F9E795`/`2F3C7E`/`333333`/`8B7E6A`；Cherry Bold `990011`/`FCF6F5`/`2F3C7E`/`333333`/`8B6B6B`；Teal Trust `028090`/`00A896`/`02C39A`/`2D3B3B`/`5E8C8C`。
- **图表选型**：分类比较→`column`(竖)/`bar`(≥6 类横)；时间序列 1–3 系列→`line`；部分-整体 2–5 片→`pie`/`doughnut`；相关/分布→`scatter`；多维密集→堆叠 `column` 或热力图；单一大数字 KPI→**大号文本形状（60–72pt）而非图表**。>3 系列且 >8 类就拆成两图或改用表。

### 避免的 AI 痕迹

- 标题下**装饰性下划线**（最常见的 AI 幻灯片痕迹）——用留白或背景色变化替代。
- **圆角卡 + 左侧色条**——另一个经典 AI 痕迹——用实填充、顶部强调带或留白分隔替代。
- emoji 当图标（除非品牌用）——用形状或真实图标资源。
- 动画：多用 `fade`/`appear`/单个 `zoom-entrance`（数百 ms）；`bounce`/`swivel`/`spin`/飞边/密集编舞通常显业余。动画是工具不是装饰；每页作为**静态帧**必须可读（pptx 动画跨查看器渲染不一致，且 `view html`/截图看不到，需真机复核）。

## QA（交付前必做）

**假设一定有问题**。首稿几乎从不正确。零问题=你看得不够狠。

### 交付门（任一失败 = 驳回，不交付）

- **门 1 — schema**：`officecli_run(command_args=["validate", file])`。任何 schema 错误→驳回修复。
- **门 2 — 溢出/格式/结构**：`officecli_run(command_args=["view", file, "issues"])`。列出**任何**问题（`[O1]`/`[C1]`/`[S1]`…）→驳回、修复、重跑直到干净。
- **门 2b — 残留占位符**：`officecli_run(command_args=["view", file, "text"])`，扫输出里的 `xxxx`、`lorem`/`ipsum`、`<TODO>`、`placeholder`、"this slide layout"、空 `()`/`[]`。命中→驳回。

### 门 3 — 视觉审查（强制）

选**一条**路径：

- **截图（默认）**：逐页 `officecli_run(command_args=["view", file, "screenshot", "--page", "1", "-o", "/mnt/user-data/workspace/slide1.png"])`，再 `--page 2` …直到页码越界（一截图=一页）。用 `file_read` 看每张 PNG。
- **HTML 文本（无视觉能力或截图失败）**：`file_read` 读 `view <file> html` 打印的 HTML 路径。DOM 无法证明**深底深字/细微重叠/箭头/间距度量/列对齐**——这些标"未视觉验证"而非通过。

**逐页对抗式审查**（假设有问题存在）：每页报一行 `slide N: <问题>` 或 `PASS`。检查清单：溢出/裁切、窄文本框（1–2 词换行）、深底深字、图片拉伸/文字压在忙图上（无卡/遮罩）、缺箭头、装饰线与标题不匹配、页脚碰撞、边距/间距过紧、间隙不均、列/重复元素未对齐、顺序合理性（封面→议程→分隔页在内容前→收尾）。

**修复-验证（强制，最多 3 轮）**：修复→重跑门 3→重复直到零新问题；一次修复常引出另一个。3 轮仍不收敛就**停**——报告 `slide N: <问题> — 已尝试: <修复> — 可能根因: <模板|设计冲突|歧义>`，交用户定。

### 落盘（门的一部分）

门 3 收敛后，以 `officecli_run(command_args=["save", file])` 收尾——保证编辑写入磁盘再交付（`close` 被禁用，用 `save` 即可）。改完不在 save 后继续编辑则无需重复 save；但若要交给 `python-pptx`/渲染/交付，save 是必做最后一步。

## 常见误区（Common Pitfalls）

| 误区 | 正确做法 |
|---|---|
| 给路径加引号（`"/slide[1]"`） | **不要加引号**——argv 模型里引号会变成文件名一部分；路径/选择器照原样写独立元素 |
| `--name "foo"` | 所有属性走 `--prop`：`--prop name=foo`（两个独立元素） |
| `/shape[myname]`（裸名放方括号） | 用 `@name=` 选择器：`/shape[@name=myname]` 或 `/shape[@id=10007]` |
| 路径 1-based vs `--index` 0-based | `/slide[1]` = 第一页；`--index 0` = 第一个位置 |
| `\n` / `\t` 在 `--prop text=` | 由 CLI 解释：`\n`=段落换行、`\t`=制表符；双写 `\\n` 得字面 |
| 写 `<<EOF` heredoc / `$FILE` 变量 / 内联 JSON | 无 shell：路径/页码直接替换进 `command_args`；batch 的 JSON 走 `file_write` 落盘 + `batch --input <文件>`，**不能内联** |
| 需要确认落盘 | 可在一批编辑完成后调用一次 `save` |
| 用 `open`/`close` | 被禁用；每条命令独立编辑周期，用 `save` 落盘 |
