# Agent Streaming Interaction Guide

## 💡 流式节点展示（草案）

> 本节用于梳理“思考过程”可视化的交互与节点分类，待方案成熟后再上升为正式协议字段。

### 节点类型

- **思考节点**：对应模型纯内部推理过程（是否调用工具、如何解读条款等）。
  - 进行中：默认展开并滚动展示最新思考文本；标题形如“正在分析条款解读”。
  - 完成后：自动收起，标题切换为“条款解读已完成”，点击可再次展开查看完整内容。
- **工具节点**：表示一次直接触发的工具调用（例如查询数据库、触发补充检索等纯函数式能力）。
  - 进行中：标题示例“正在进行数据库查询”，展开区域内可以按子步骤显示“准备参数”“等待响应”等实时状态。
  - 完成后：标题切换为“数据库查询完成”，整体收起，点击可展开查看执行日志或输出片段。
- **子 Agent 节点**：表示委派给另一个 Agent 的任务（例如“法规检索”在当前业务中即属于子 Agent）。
  - 进行中：标题可为“Agent[图标] 法规检索进行中”，展开内容由子 Agent 自己的 `node_delta` 组成。通常子 Agent 里会像主agent节点一样，里面也有同样的嵌套结构。
  - 完成后：标题切换成“Agent[图标] 法规检索已完成”，默认收起，用户可点开查看记录。

以上三类节点共享统一的事件模型：`node_start`（建节点）、`node_delta`（内容增量）、`node_end`（结束并标注状态）。这样前端可以仅依赖 node_* 三类事件完成展开/收起、进度展示与嵌套渲染。

### 完整示例：

下面示例聚焦“主 Agent → 子 Agent → 主内容输出”的典型流。为便于阅读，统一用 SSE 行的 `data:` 形式，并在 `node_delta` 中展示嵌套事件。

```
data: {"event":"message_start","response_id":"r1","message_id":"m1","created":1}

data: {"event":"node_start","response_id":"r1","message_id":"m1","node_id":"n1","type":"thinking","title":"思考","created":2}
data: {"event":"node_delta","response_id":"r1","message_id":"m1","node_id":"n1","delta":"我需要检索'违规发行预付卡'相关的法律","created":3}
data: {"event":"node_end","response_id":"r1","message_id":"m1","node_id":"n1","status":"completed","title":"思考","created":4}

data: {"event":"node_start","response_id":"r1","message_id":"m1","node_id":"n2","type":"agent","title":"Agent⚙️ 法规检索进行中","created":5}
data: {"event":"node_delta","response_id":"r1","message_id":"m1","node_id":"n2","delta":{"event":"node_start","node_id":"s1","type":"thinking","title":"任务规划","created":7}}
data: {"event":"node_delta","response_id":"r1","message_id":"m1","node_id":"n2","delta":{"event":"node_delta","node_id":"s1","delta":"检索目录 -> 阅读原文 -> 输出条款","created":8}}
data: {"event":"node_delta","response_id":"r1","message_id":"m1","node_id":"n2","delta":{"event":"node_end","node_id":"s1","status":"completed","title":"任务规划","created":9}}
data: {"event":"node_delta","response_id":"r1","message_id":"m1","node_id":"n2","delta":{"event":"node_start","node_id":"s2","type":"tool","title":"阅读目录","created":10}}
data: {"event":"node_delta","response_id":"r1","message_id":"m1","node_id":"n2","delta":{"event":"node_delta","node_id":"s2","delta":"阅读目录，分析可能相关法律...","created":11}}
data: {"event":"node_delta","response_id":"r1","message_id":"m1","node_id":"n2","delta":{"event":"node_delta","node_id":"s2","delta":"进度10%","ephemeral":"true","created":12}}
data: {"event":"node_delta","response_id":"r1","message_id":"m1","node_id":"n2","delta":{"event":"node_delta","node_id":"s2","delta":"进度50%","ephemeral":"true","created":13}}
data: {"event":"node_delta","response_id":"r1","message_id":"m1","node_id":"n2","delta":{"event":"node_delta","node_id":"s2","delta":"进度100%","ephemeral":"true","created":14}}
data: {"event":"node_delta","response_id":"r1","message_id":"m1","node_id":"n2","delta":{"event":"node_end","node_id":"s2","status":"completed","title":"完成初步检索","created":15}}
data: {"event":"node_delta","response_id":"r1","message_id":"m1","node_id":"n2","delta":{"event":"node_start","node_id":"s3","type":"tool","title":"阅读原文","created":16}}
data: {"event":"node_delta","response_id":"r1","message_id":"m1","node_id":"n2","delta":{"event":"node_delta","node_id":"s3","delta":"阅读法律原文，分析可能相关条款...","created":17}}
data: {"event":"node_delta","response_id":"r1","message_id":"m1","node_id":"n2","delta":{"event":"node_end","node_id":"s3","status":"completed","title":"完成条款检索","created":18}}
data: {"event":"node_delta","response_id":"r1","message_id":"m1","node_id":"n2","delta":{"event":"node_start","response_id":"r1","message_id":"m1-1","node_id":null,"index":0,"delta":"正在进行资料整理","role":"assistant","created":19}}
data: {"event":"node_delta","response_id":"r1","message_id":"m1","node_id":"n2","delta":{"event":"node_delta","response_id":"r1","message_id":"m1-1","node_id":null,"index":0,"delta":"正在进行资料整理","role":"assistant","created":19}}
data: {"event":"node_delta","response_id":"r1","message_id":"m1","node_id":"n2","delta":{"event":"node_end","response_id":"r1","message_id":"m1-1","node_id":null,"index":0,"delta":"检索到了7个法律条款","role":"assistant","created":19}}
data: {"event":"node_end","response_id":"r1","message_id":"m1","node_id":"n2","status":"completed","title":"Agent⚙️ 法规检索已完成","created":22}

data: {"event":"node_start","response_id":"r1","message_id":"m1","node_id":"n3","type":"thinking","title":"正在综合分析","created":23}
data: {"event":"node_delta","response_id":"r1","message_id":"m1","node_id":"n3","delta":"结合法规检索结果生成最终建议...","created":24}
data: {"event":"node_end","response_id":"r1","message_id":"m1","node_id":"n3","status":"completed","title":"综合分析完成","created":25}

data: {"event":"content_delta","response_id":"r1","message_id":"m1","node_id":null,"index":0,"delta":"违规发行预付卡违反了《大气污染防治法》第三条引用...","role":"assistant","created":26,"metadata":{"channel":"final"}}
data: {"event":"message_end","response_id":"r1","message_id":"m1","created":27}
data: {"event":"done","response_id":"r1","message_id":"m1","created":28}
```

要点：
- 所有节点仍遵循 `node_start → node_delta* → node_end` 的生命周期。
- `node_delta` 可携带纯文本，也可以像示例中那样承载结构化事件，方便将当前 Agent 视作父节点，而把子 Agent 的所有事件原样嵌入。
- 顶层 `content_delta`、`message_end`、`done` 保持在节点树之外，通过 `node_id:null` 等字段明确归属，便于前端区分“主回复”与“节点内输出”。
- `node_delta` 现在会携带 `line_id`：后端会为每个 `node_id` 内的增量分配单调递增的行标识（格式类似 `node1-line-1`），同一行的后续增量可复用同一个 `line_id` 以便前端合并展示；如需自定义行拼接，可在发送时传入固定的 `line_id`。

### 会话标题更新（新增）

当后端在生成结束后得到了一个用于展示的会话标题时，会通过新增的事件类型通知前端：

- **事件名**：`title_generation`
- **字段**：
  - `title`：字符串，新的会话标题，前端用于更新当前会话的显示名称；不影响内容流。
  - `conversation_id`：字符串，标识需要更新标题的会话。
- **行为**：仅更新会话列表/详情中的标题，不改变节点或内容流的处理逻辑。

示例：

```
data: {"event":"title_generation","conversation_id":"c_1234","title":"新的处罚建议会话"}
```

### 子 Agent 输出的封装指南

主 Agent 并不需要将子 Agent 的原始 `content_delta`/`node_delta` 一股脑转发给前端，可以按需封装为更友好的节点结构。推荐做法：

- **转译为内容节点**：当子 Agent 产生阶段性 `content_delta` 时，父节点可新建 `type:"content"` 的子节点，然后将阶段性进展写入 `node_delta`，示例：
  - `node_start(node_id:"s_content", type:"content", title:"法规检索进展")`
  - `node_delta(node_id:"s_content", delta:"已完成目录检索，准备阅读原文...")`
  - `node_end(node_id:"s_content", status:"completed")`
- **结果描述节点**：当子 Agent 得到最终输出，可再创建 `type:"result"`（或其他自定义类型）的节点，仅保留面向用户的结论，屏蔽内部日志。
- **敏感信息过滤**：子 Agent 的任何 `node_delta`/`content_delta` 都可以先经过主 Agent 判断，只有需要让前端看到的才封装进新的节点；无论是实时进度还是最终结果，都由主 Agent 决定是否 `yield`。

通过这层封装，前端协议保持简洁一致，同时主 Agent 也能灵活控制不同子 Agent 的展示粒度。
