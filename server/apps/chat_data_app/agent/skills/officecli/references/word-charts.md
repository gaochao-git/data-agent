# Word 原生图表参考

OfficeCLI 当前版本支持把图表作为 Word 的原生 DrawingML 图表写入 `.docx`。原生图表可在 Word 中继续修改数据、类型、颜色和标题；不要先生成前端截图再嵌入，除非用户明确要求图片。

## 创建流程

先创建一个段落，再把图表添加到该段落。图表写入后用全局 `/chart[N]` 查询和回读：

```bash
officecli create '/mnt/user-data/workspace/report.docx'
officecli add '/mnt/user-data/workspace/report.docx' /body --type paragraph
officecli add '/mnt/user-data/workspace/report.docx' '/body/p[1]' --type chart \
  --prop chartType=line \
  --prop title='月度销售额趋势' \
  --prop categories='1月,2月,3月,4月' \
  --prop data='销售额:120,155,132,188' \
  --prop width=15cm --prop height=8cm \
  --prop legend=none --prop gridlines=true
officecli save '/mnt/user-data/workspace/report.docx'
officecli validate '/mnt/user-data/workspace/report.docx'
officecli query '/mnt/user-data/workspace/report.docx' chart --json
```

包含多个系列时，`data` 使用分号分隔系列，每个系列使用 `名称:数值,数值,...`：

```text
实际销售:120,155,132,188;目标销售:130,145,160,175
```

`categories` 数量必须与每个系列的数值数量一致。中文路径和包含方括号的 OfficeCLI DOM 路径必须整体加引号。

## 图表类型

支持的常用类型：

- `line`：时间趋势或多系列趋势
- `column`：分类对比
- `bar`：长分类名的横向对比
- `pie` / `doughnut`：非负构成占比
- `area`：连续趋势和累计量
- `funnel`：访问、留资、商机、成交等阶段转化
- `scatter`、`bubble`、`radar`、`waterfall`、`treemap`、`sunburst`：仅在数据结构和业务语义确实匹配时使用

不要默认使用 `combo`。当前 chart-series 的混合类型和次坐标轴能力不作为报告技能的稳定契约。

## 常用属性

- `title`：图表标题
- `categories`：逗号分隔的分类标签，也可使用工作表范围引用
- `data`：内联系列数据，只在 `add` 时使用
- `colors`：系列颜色，逗号分隔
- `legend`：`none`、`bottom`、`right` 等
- `dataLabels`：`value`、`percent`、`category` 或组合
- `gridlines`：是否显示数值轴网格线
- `width`、`height`：图表尺寸，例如 `15cm`、`8cm`

饼图可用 `varyColors=true` 让每个数据点使用不同颜色；漏斗图需要确认当前 OfficeCLI 版本的 `query chart` 能读回数据系列，不能只根据命令成功就声称数据已写入。

## 校验与回读

生成多个图表时，使用：

```bash
officecli save '/mnt/user-data/workspace/report.docx'
officecli validate '/mnt/user-data/workspace/report.docx'
officecli view '/mnt/user-data/workspace/report.docx' issues
officecli query '/mnt/user-data/workspace/report.docx' chart --json
officecli get '/mnt/user-data/workspace/report.docx' '/chart[1]'
```

`query chart` 应确认图表数量、`chartType`、标题、分类和系列值。外部预览工具无法使用时，结构回读只能证明 Office Open XML 结构与数据存在，不等同于视觉布局已确认。
