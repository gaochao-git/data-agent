# Excel / XLSX 操作参考

本文件覆盖 `.xlsx` 的读取、创建工作簿、单元格/公式编辑、命名范围、图表、条件格式、数据验证、透视表、CSV 导入与批量提交。所有命令都通过 `officecli_run(command_args=[...])` 执行——**每个元素是一个独立参数，不要加引号、不要拼成一条命令**（详见下方"执行模型"）。`docx` / `pptx` 见同目录 `word.md` / `powerpoint.md`。

## 执行模型（必读）

```python
# 正确：每个 token 独立成元素，路径含空格/选择器含 [N] 照原样写
officecli_run(command_args=["view", "/mnt/user-data/workspace/销售分析.xlsx", "outline"])
officecli_run(command_args=["set", "/mnt/user-data/workspace/销售分析.xlsx", "/Sheet1/B5",
                            "--prop", "formula=SUM(B2:B4)"])
# 错误：把整条命令当一个字符串、或给路径加引号
officecli_run(command_args=["view '/mnt/user-data/workspace/销售分析.xlsx' outline"])   # 引号会变成文件名一部分
```

- **不要使用 `open` / `close`**：这两个子命令被 `officecli_run` 禁用（常驻进程会锁文件）。每次命令是独立的编辑周期。
- **修改命令成功后运行时会同步 `workspace`/`outputs` 文件**。需要显式确认 OfficeCLI 刷新文件时再调用 `save`，不必每条修改后重复调用；交给渲染/交付前可调用一次 `save`。
- **无 shell —— 这是关键优势**：我们没有 shell，所以公式里的 `!`（跨表）、命名范围里的 `$`、文本里的 `$` 都**字面安全**，不会被展开或改写。上游 shell 文档要求用 heredoc / 单引号保护 `!` `$`，在我们的模型下**不需要**。
- **`\n` / `\t` 仍生效**：CLI 层会解释 `--prop value=` 里的 `\n`（单元格内换行）、`\t`（制表符）；要写字面反斜杠-n 用 `\\n`。
- **Help-First**：属性名、枚举值、`prop` 选项不确定时先查 help，不要猜：
  - `officecli_run(command_args=["help", "xlsx"])` —— 列出所有 xlsx 元素
  - `officecli_run(command_args=["help", "xlsx", "chart"])` —— 元素 schema
  - `officecli_run(command_args=["help", "xlsx", "add", "chart"])` —— 动词级 schema
  - `officecli_run(command_args=["help", "xlsx", "<元素>", "--json"])` —— 机器可读 schema
  - 当文档与 help 冲突时，**help 为准**。出现 `UNSUPPORTED props:` 警告、枚举漂移时立即查 help。

## 读取与分析（Reading & Analysis）

先宽后窄：`outline` 看全局，再 `get` / `query` / `view` 深入。

```python
officecli_run(command_args=["view", file, "outline"])      # 工作表列表 + 维度
officecli_run(command_args=["view", file, "text", "--start", "1", "--end", "50", "--cols", "A,B,C"])  # 纯文本转储
officecli_run(command_args=["view", file, "html"])          # HTML 预览路径（file_read 打开做结构审查）
officecli_run(command_args=["view", file, "annotated"])     # 值 + 类型 + 警告
officecli_run(command_args=["view", file, "stats"])         # 数值摘要
officecli_run(command_args=["view", file, "issues"])        # 错误/空表/破损引用
```

**单元素检查**。XPath 风格路径，1-based：

```python
officecli_run(command_args=["get", file, "/", "--depth", "1", "--json"])        # 工作簿级：sheet / namedrange / chart 列表
officecli_run(command_args=["get", file, "/Sheet1/A1", "--json"])
officecli_run(command_args=["get", file, "/Sheet1/A1:D10", "--json"])
officecli_run(command_args=["get", file, "/Sheet1/chart[1]", "--depth", "2"])
officecli_run(command_args=["get", file, "/namedrange[1]", "--json"])
```

**跨工作簿查询**。CSS 风格选择器，支持 `=`、`!=`、`~=`、`>=`、`<=`、`[attr]`、`:contains()`：

```python
officecli_run(command_args=["query", file, 'cell:has(formula)'])        # 所有含公式的单元格
officecli_run(command_args=["query", file, 'cell:contains("#REF!")'])    # 破损引用
officecli_run(command_args=["query", file, 'cell[type=Number]'])         # 数值单元格
officecli_run(command_args=["query", file, 'Sheet1!B[value!=0]'])        # 按值过滤
```

## 创建工作簿

```python
officecli_run(command_args=["create", "/mnt/user-data/workspace/销售分析.xlsx"])
# 新建工作表（默认只有 Sheet1；导入/落数据前确保目标 sheet 已存在）
officecli_run(command_args=["add", "/mnt/user-data/workspace/销售分析.xlsx", "/", "--type", "sheet", "--prop", "name=数据源"])
```

## 单元格与公式

```python
# 文本 / 数字
officecli_run(command_args=["set", file, "/Sheet1/A1", "--prop", "value=月份"])
officecli_run(command_args=["set", file, "/Sheet1/B2", "--prop", "value=1200"])

# 公式：不要写起始的 = （CLI 会自动剥离前导等号，写上也不报错但多余）
officecli_run(command_args=["set", file, "/Sheet1/B5", "--prop", "formula=SUM(B2:B4)"])

# 跨表引用：用 ! 连接工作表名；无 shell 模型下字面安全，无需转义
officecli_run(command_args=["set", file, "/Summary/B2", "--prop", "formula=Revenue!B13"])

# 保护分母：避免 #DIV/0!
officecli_run(command_args=["set", file, "/Sheet1/C5", "--prop", "formula=IFERROR(B5/B4,0)"])
```

- **公式不加前导 `=`**：写 `formula=SUM(B2:B4)`，不要写 `formula==SUM(B2:B4)`（CLI 会剥离首个 `=`）。
- **验证计算值**：`set` 后 officecli 通常就地算出结果；用 `get /Sheet1/B5 --json`（回看 `text` 字段即计算值）或 `view text` 复核。若某公式 `text` 为空，先在相邻单元格 `get` 确认，或先 `save` 再读取。
- **财务/模型约定**（若做财务模型）：输入假设用命名范围（见下节）；硬编码输入单元格标蓝（`--prop fontColor=1F4E79`）并附来源注释；每行公式保护分母。

## 命名范围（Named Ranges）

优先于 `$B$6` 绝对引用，自文档化、跨表稳定。`parent` 为 `"/"` 是工作簿级，`/SheetName` 是表级；`ref` 含 `!`/`$` 在我们的模型下字面安全。

```python
officecli_run(command_args=["add", file, "/", "--type", "namedrange",
                            "--prop", "name=GrowthRate", "--prop", "ref=Sheet1!$B$6"])
officecli_run(command_args=["add", file, "/Lookups", "--type", "namedrange",
                            "--prop", "name=StatusList", "--prop", "ref=Lookups!$A$2:$A$4"])
# 公式中直接用名字
officecli_run(command_args=["set", file, "/Summary/B2", "--prop", "formula=IFERROR(Revenue/GrowthRate,0)"])
officecli_run(command_args=["get", file, "/namedrange[1]", "--json"])
```

## 图表（三种数据供给，每图选一种，不可混用）

```python
# (a) 内联 data + categories：微型演示，数据写在 XML 里
officecli_run(command_args=["add", file, "/Sheet1", "--type", "chart", "--prop", "chartType=column",
                            "--prop", "data=Sales:100,200,300", "--prop", "categories=Jan,Feb,Mar", "--prop", "title=季度营收"])

# (b) 2D dataRange：常规用法，必须二维且含类别列，表名前缀必填
officecli_run(command_args=["add", file, "/Sheet1", "--type", "chart", "--prop", "chartType=column",
                            "--prop", "dataRange=Sheet1!A1:B4", "--prop", "title=季度营收"])

# (c) 点号式多序列：序列不连续时用
officecli_run(command_args=["add", file, "/Sheet1", "--type", "chart", "--prop", "chartType=line",
                            "--prop", "series1.name=实际", "--prop", "series1.values=42,45,48",
                            "--prop", "series1.categories=Jan,Feb,Mar",
                            "--prop", "series2.name=计划", "--prop", "series2.values=40,42,45",
                            "--prop", "series2.categories=Jan,Feb,Mar"])
```

陷阱：
- **单列 `dataRange` 报错**：`dataRange=Sheet1!B2:B13`（只有值列）会失败；必须含类别列 `A1:B13`，或改用 (c)。
- **`dataRange` 必须带表名前缀**：写 `Summary!A17:C22`，不能只写 `A17:C22`。
- **系列不可变**：改系列需 `remove` 旧 `chart` 再 `add` 新图。
- **标题禁占位符**：`TBD` / `{{x}}` / 空 `()` 会按字面显示；数据用真实值。
- 完整图表类型枚举（20+，含 `boxWhisker` / `waterfall` 等）查 `help xlsx chart`。

## 条件格式（Conditional Formatting）

元素类型 `conditionalformatting`，路径 `/cf[N]`。

```python
# 色阶
officecli_run(command_args=["add", file, "/Sheet1", "--type", "conditionalformatting",
                            "--prop", "sqref=A1:A10", "--prop", "type=colorscale",
                            "--prop", "minColor=FF0000", "--prop", "midColor=FFFF00", "--prop", "maxColor=00FF00"])
# 数据条
officecli_run(command_args=["add", file, "/Sheet1", "--type", "conditionalformatting",
                            "--prop", "sqref=B1:B10", "--prop", "type=databar", "--prop", "min=0", "--prop", "max=1000"])
# 公式规则
officecli_run(command_args=["add", file, "/Sheet1", "--type", "conditionalformatting",
                            "--prop", "sqref=C2:C20", "--prop", "type=formula",
                            "--prop", "formula=$C2>1000", "--prop", "fill=FFC7CE"])
```

查 schema：`officecli_run(command_args=["help", "xlsx", "conditionalformatting", "--json"])`。

## 数据验证（Data Validation）

```python
# (a) 内联列表
officecli_run(command_args=["add", file, "/Sheet1", "--type", "validation",
                            "--prop", "sqref=C2:C100", "--prop", "type=list", "--prop", "formula1=Yes,No,Maybe"])
# (b) 命名范围（推荐，便于集中维护）
officecli_run(command_args=["add", file, "/Lookups", "--type", "namedrange",
                            "--prop", "name=StatusList", "--prop", "ref=Lookups!$A$2:$A$4"])
officecli_run(command_args=["add", file, "/Sheet1", "--type", "validation",
                            "--prop", "sqref=B2:B100", "--prop", "type=list", "--prop", "formula1=StatusList"])
# (c) 直接跨表
officecli_run(command_args=["add", file, "/Sheet1", "--type", "validation",
                            "--prop", "sqref=C2:C100", "--prop", "type=list", "--prop", "formula1=Lookups!$A$2:$A$4"])
```

其他 `type`：`decimal` / `whole` / `date` / `textLength` / `custom`。具体枚举与 `formula1` 语法查 `help xlsx validation`。

## 透视表 / 切片器 / 迷你图

```python
# 透视表：source 范围必须含真实数据，否则报 "Source range has no data"
officecli_run(command_args=["add", file, "/Sheet1", "--type", "pivottable",
                            "--prop", "source=Sheet1!A1:D100",
                            "--prop", "rowFields=Region", "--prop", "dataFields=Sales"])
# 切片器（交互过滤）
officecli_run(command_args=["add", file, "/Sheet1", "--type", "slicer", "--prop", "pivotTable=1", "--prop", "field=Region"])
# 迷你图
officecli_run(command_args=["add", file, "/Sheet1", "--type", "sparkline", "--prop", "sqref=E2:E10",
                            "--prop", "type=line", "--prop", "data=Sheet1!B2:B10"])
```

字段名与枚举（`pivottable` 的 `rowFields`/`colFields`/`dataFields`/`aggFunc` 等）以 `help xlsx pivottable` 为准——不同版本略有差异。

## CSV / 批量导入

原生 `import` 是首选（保留类型推断、快）：

```python
# 目标 sheet 必须已存在；不存在就先 add --type sheet
officecli_run(command_args=["add", file, "/", "--type", "sheet", "--prop", "name=导入"])
officecli_run(command_args=["import", file, "/导入", "--file", "/mnt/user-data/workspace/data.csv", "--header"])
# TSV / 从 stdin / 指定起始格
officecli_run(command_args=["import", file, "/导入", "--file", "/mnt/user-data/workspace/data.tsv", "--format", "tsv", "--header"])
officecli_run(command_args=["import", file, "/导入", "--stdin", "--start-cell", "B2"])   # 配合 file_read 内容经 stdin 喂入
```

**Python + `batch` 回退**：需要自定义类型、注入公式、或超大文件分块时，生成 batch JSON 再回放（见下方 batch 章节的 `--input` 模式）。块大小建议 ≤ 80 ops/块。

## batch（JSON 批量提交，用 `--input` 文件）

`batch` **不接受内联 JSON 位置参数**（实测 `officecli batch file '[{...}]'` 报 `Unrecognized command or argument`）。JSON 必须经 **`--input` 文件** 传入。我们的无 shell 模型下，最干净的做法：用 `file_write` 把 JSON 数组写成 workspace 内的临时文件，再交给 `batch --input`。

```python
# 1) 用 file_write 把 batch JSON 写入 workspace 临时文件（无 shell 转义顾虑）
file_write(file_path="/mnt/user-data/workspace/_batch.json",
           content='[{"command":"set","path":"/Sheet1/D1","props":{"value":"hello"}},'
                   '{"command":"set","path":"/Sheet1/D2","props":{"formula":"SUM(B2:B4)"}}]')
# 2) 用 --input 交给 batch（--input 指向的文件在 workspace 内，只读，不在 uploads）
officecli_run(command_args=["batch", "/mnt/user-data/workspace/销售分析.xlsx",
                            "--input", "/mnt/user-data/workspace/_batch.json"])
# 3) 可选清理
file_delete(file_path="/mnt/user-data/workspace/_batch.json")
```

JSON 结构（与 pptx/word 一致）：每个元素是 `{"command": <裸动词>, "parent"|"path": <目标>, "type"?: <元素类型>, "props": {...}}`。`props` 的键即 `--prop` 的 `key=value`（如 `formula`、`value`、`numFmt`、`fill`）。推荐块大小 ≤ 50 ops/块（纯值设置可 80+）；大批量用 Python 生成多块。

## 交付前 QA（Required）

**假设一定有问题**——首稿几乎从不正确。

```python
officecli_run(command_args=["save", file])                       # 落盘（必做，常驻进程不自动 flush）
officecli_run(command_args=["validate", file])                  # schema 校验（不捕捉设计错误）
officecli_run(command_args=["view", file, "issues"])            # 破损引用 / 空表 / #REF!
officecli_run(command_args=["view", file, "text", "--start", "1", "--end", "50"])  # 扫残留 #REF! / 占位符
```

- 门 1 `validate`：任何 schema 错误 → 驳回修复。
- 门 2 `view issues`：列出**任何**问题（`#REF!`、空表、破损图表引用）→ 驳回、修复、重跑直到干净。
- 门 3 视觉：能读图时用 `view <file> html` 打印的 HTML 路径做结构审查；图表系列填充、跨表公式结果需在目标查看器复核。
- 修复-验证最多 3 轮；3 轮不收敛就停，报告未解决问题交用户定。

## 常见误区（Common Pitfalls）

| 误区 | 正确做法 |
|---|---|
| `formula==SUM(...)`（带前导 `=`） | 写 `formula=SUM(...)`；CLI 自动剥离前导 `=`，写上多余 |
| 给路径加引号（`"/Sheet1/A1"`） | **不要加引号**——argv 模型里引号会变成文件名一部分；路径/选择器照原样写独立元素 |
| `batch file '[{...}]'`（内联 JSON） | 不支持；用 `file_write` 落 JSON + `batch --input <文件>` |
| `<<EOF` heredoc / `$FILE` 变量 | 无 shell：直接把路径替换进 `command_args`；batch 的 JSON 走 `--input` 文件 |
| `--name "foo"` | 所有属性走 `--prop`：`--prop name=foo`（两个独立元素） |
| `dataRange=Sheet1!B2:B13`（单列） | 必须含类别列 `A1:B13`，或改用点号式 `series1.values=...` |
| `dataRange=A1:B4`（缺表名前缀） | 必须 `Sheet1!A1:B4` |
| 跨表 `Revenue!B13` 被改写 | 无 shell，`!` 字面安全，无需转义 |
| 导入到不存在的 sheet | `import` 目标 sheet 须先存在（`add --type sheet`）；否则报 "Sheet not found" |
| 需要确认落盘 | 可在一批编辑完成后调用一次 `save` |
| 用 `open`/`close` | 被禁用；每条命令独立编辑周期，用 `save` 落盘 |
