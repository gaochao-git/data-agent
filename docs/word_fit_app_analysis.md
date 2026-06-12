# word_fit_app 模块分析

## 1. 模块定位

`word_fit_app` 是一个通用“文件字段抽取”模块，对外提供单接口 `POST /api/word-fit/extract`，输入 `file_id` 和字段定义，输出 SSE 流。

相关文件：
- [README.md](/Users/fengyingsheng/workspaces/personal/fxcjg/fxcjg/server/apps/word_fit_app/README.md)
- [routes.py](/Users/fengyingsheng/workspaces/personal/fxcjg/fxcjg/server/apps/word_fit_app/routes.py)
- [handlers.py](/Users/fengyingsheng/workspaces/personal/fxcjg/fxcjg/server/apps/word_fit_app/handlers.py)
- [graph.py](/Users/fengyingsheng/workspaces/personal/fxcjg/fxcjg/server/apps/word_fit_app/agent/graph.py)
- [nodes.py](/Users/fengyingsheng/workspaces/personal/fxcjg/fxcjg/server/apps/word_fit_app/agent/nodes.py)

## 2. 对外能力

接口：
- `POST /api/word-fit/extract`

请求体核心字段：
- `file_id`
- `extract_fields`
- `conversation_id` 可选
- `locate_enabled` 可选

返回方式：
- `text/event-stream`

SSE 事件类型：
- `node`
- `llm_chunk`
- `result`

## 3. 工作流结构

模块通过 LangGraph 编排，状态定义在 [state.py](/Users/fengyingsheng/workspaces/personal/fxcjg/fxcjg/server/apps/word_fit_app/agent/state.py)。

主流程：

1. `fetch_file`
2. `resolve_file`
3. 根据文件后缀路由到：
   - `ocr_node`
   - `doc_node`
   - `read_text_node`
4. `extract_info`
5. 可选 `locate_info`

路由逻辑在 [graph.py](/Users/fengyingsheng/workspaces/personal/fxcjg/fxcjg/server/apps/word_fit_app/agent/graph.py)：
- 图片和 PDF 走 OCR
- `doc/docx/wps/xls/xlsx` 走文档节点
- 其他文本文件直接读取
- `locate_enabled=true` 时才执行定位

## 4. 文件处理策略

### 4.1 文件获取

[nodes.py](/Users/fengyingsheng/workspaces/personal/fxcjg/fxcjg/server/apps/word_fit_app/agent/nodes.py) 里的 `fetch_file_node` 会调用外部下载接口：
- 默认 `FILE_DOWNLOAD_API_URL=http://localhost:8000/download`
- 参数里包含 `filePath`、`user`、`clusterList`、`token`

如果下载失败，`resolve_file_node` 会继续兜底：
- 查 DB：`get_file_path_by_id`
- 查本地 `uploads/`
- 直接把 `file_id` 当本地路径

这个设计说明模块既支持“外部文件服务拉取”，也支持“服务本机已有文件”。

### 4.2 OCR 路径

OCR 实现在 [image_text_extractor_llm.py](/Users/fengyingsheng/workspaces/personal/fxcjg/fxcjg/server/apps/word_fit_app/tools/image_text_extractor_llm.py)：
- 图片转 base64 后走 OpenAI 兼容接口
- PDF 用 `PyMuPDF` 逐页转 PNG，再逐页 OCR
- 结果使用 `diskcache` 做哈希缓存
- 文本会落到 `uploads/texts` 或 complaints 目录对应的 `texts/`

特点：
- 不是本地 OCR 引擎，而是“视觉模型 OCR”
- PDF 页数越多，调用成本和耗时越高

### 4.3 文档路径

`doc_node` 的处理方式：
- `doc/docx/wps`：通过 `complaint_app.tools.doc_markdown` 转 Markdown
- `xls/xlsx`：通过 `openpyxl` 把表格拼接为纯文本
- 其他：尝试直接文本读取

这说明 `word_fit_app` 对 Office 文档并没有单独实现，而是复用了投诉模块已有文档转换能力。

## 5. 提取策略

`extract_info_node` 的核心做法是：
- 把 `extract_fields` 序列化进 prompt
- 要求模型直接返回 JSON
- 模型用 `get_llm(streaming=True)` 调用

当前实现特点：
- 输出格式依赖 prompt 约束
- 节点本身没有服务端 schema 级严格校验
- handler 里会尝试 `json.loads`，解析失败时会退回原始字符串

这意味着它适合“高灵活度抽取”，但对“必须稳定返回结构化 JSON”的场景，鲁棒性还不算最强。

## 6. 定位能力

`locate_info_node` 只对 PDF 生效：
- 先从前一步的提取结果里取非空字段
- 再让模型基于 `file_path` 和抽取值返回页码、坐标、置信度

输出结构类似：
- `page`
- `x`
- `y`
- `width`
- `height`
- `confidence`

定位能力的前提是：
- 文件必须是 PDF
- 提取结果能被成功解析成 JSON

## 7. 模块优点

- 入口清晰，对外只有一个抽取接口，调用成本低。
- 工作流拆分比较合理，下载、解析、提取、定位职责分开。
- 文件类型分流明确，适合做通用字段抽取底座。
- SSE 事件设计完整，前端可以拿到阶段进度和流式内容。
- OCR 做了缓存，重复处理同一文件时成本较低。

## 8. 当前限制和风险

### 8.1 JSON 约束偏弱

`extract_info_node` 主要靠 prompt 约束 JSON，缺少更强的服务端 schema 校验和失败重试策略。对结构复杂字段，可能出现：
- JSON 不合法
- 字段丢失
- 数组结构不稳定

### 8.2 文件下载配置耦合较强

默认下载地址是 `http://localhost:8000/download`，并依赖特定参数：
- `FILE_DOWNLOAD_USER`
- `FILE_DOWNLOAD_TOKEN`
- `FILE_DOWNLOAD_CLUSTER`

如果部署环境里没有这套下载服务，模块会退回本地兜底路径。

### 8.3 OCR 成本较高

PDF 每页都要走视觉模型，长文档场景下：
- 耗时高
- token 成本高
- 网络抖动影响明显

### 8.4 定位能力依赖模型理解

`locate_info_node` 不是基于 PDF 原生文本坐标搜索，而是再次调用模型做“位置理解”，准确率和稳定性会受模型表现影响。

## 9. 适合的使用场景

比较适合：
- 单文件字段抽取
- OCR 后结构化抽取
- 发票、表单、举报材料、申请材料等半结构化文档
- 需要前端看到抽取进度的场景

不太适合：
- 强事务型抽取
- 必须 100% 严格 JSON 的场景
- 超长 PDF 批量处理
- 对坐标定位精度要求极高的场景

## 10. 结论

`word_fit_app` 的定位很明确：它是一个“通用文件抽取中台”，而不是某一类文档的强规则引擎。

如果后续要继续增强，优先级建议是：

1. 给 `extract_info` 增加更强的 JSON 校验和失败回退。
2. 给 `locate_info` 增加非模型方式的文本定位能力。
3. 把下载服务配置和本地文件模式做得更显式。
4. 对超长 PDF 增加分页、限流和更细的错误反馈。
