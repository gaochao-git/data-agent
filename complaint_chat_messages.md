# 举报件对话消息结构（前后端约定）

本文仅覆盖“举报件”场景下的对话 SSE `chat_message` 字段和示例（ConversationTimeline 用途）。其他业务模块不在此约束范围内。

**适用范围（当前落地）**

- `/api/complaint/text` 链路：思考 → 工具 → 结论三类片段的推送。每条消息只承载其中一个片段，通过 `status` 标记开始、进行中、结束。

如需扩展到其他接口/阶段，需后端按同样字段推送、前端按本约定渲染。

## `chat_message` 字段（`event` = `chat_message`）

- `id`: 消息唯一标识（同一阶段的增量复用同一个 id，`content_delta` 追加）。
- `role`: 固定 `"assistant"`。
- `type`: `"thought"` | `"tool"` | `"result"`，分别表示思考、工具调用、结论。
- `title`: 标题文案，如 `"思考"` / `"工具调用"` / `"结论"`。
- `status`: `"start"` | `"processing"` | `"end"`，表示阶段开始、进行中、结束。开始/结束消息仅做状态提示，与业务文案解耦。
- `content`: 展示文案。`processing` 时用于流式增量（`content_delta`）。
- `render_mode`: `"streaming"`（流式拼接）或 `"list"`（单行展示/覆盖，常用于工具进度），必填。
- `line_id`（仅当 `render_mode=list` 且 `status=processing` 时可选）: 同一行的唯一标识（建议后端生成唯一 id，如 `tool-line-1`），相同 `line_id` 覆盖同一行。
- `order`（仅当 `render_mode=list` 且 `status=processing` 时可选）: 行的排序序号，整数，从小到大排列；缺省时按到达顺序。
- `collapse_on_end`（可选）: `end` 时是否自动收起该段，默认 `true`；设为 `false` 时结束后保持展开（例如结论希望保持展开）。

## 状态语义

- `start`: 阶段开场提示，内容可为空；前端据此展示标题并展开。
- `processing`: 阶段内容更新。`render_mode=streaming` 走增量拼接；`render_mode=list` 下每条消息是一行，有 `line_id` 时覆盖同一行。
- `end`: 阶段结束提示，收到后收起该阶段；通常无需携带正文（结果阶段可在结束前已完整输出内容）。

## 前端展示约定（ConversationTimeline）

- 单条 AI 消息内按固定顺序渲染三段：思考 → 工具 → 结论；缺失的不展示，SSE 到达顺序无论如何，呈现顺序保持不变。
- 思考（`type=thought`, `render_mode=streaming`）：`start` 显示标题“思考·进行中”并展开正文容器，`processing` 流式累加正文，`end` 标记“已完成”，可自动/手动收起但保留正文。
- 工具（`type=tool`, `render_mode=list`）：`start` 显示标题“工具调用·进行中”并展开列表容器，`processing` 每条是一行，按 `order`（无则到达顺序）排序；有 `line_id` 时覆盖同一行；`end` 标记“已完成”，可收起但保留列表。
- 结论（`type=result`, `render_mode=streaming`）：不新建单独卡片，在同一 AI 消息内的结论区域展示；`start` 显示标题“结论·进行中”，`processing` 流式累加正文（markdown），`end` 标记“已完成”，如需要保持展开可传 `collapse_on_end=false`。
- 三个段落标题风格保持一致（同色、同风格图标/文案/状态），正文采用统一文案样式；`end` 后默认可收起，对应段内容不丢失。

## 示例

### 思考
```json
// 开始
{
  "event": "chat_message",
  "chat_message": {
    "id": "abc-thought",
    "role": "assistant",
    "type": "thought",
    "title": "思考",
    "status": "start",
    "render_mode": "streaming",
    "content": ""
  }
}
// 进行中（流式）
{
  "event": "chat_message",
  "chat_message": {
    "id": "abc-thought",
    "role": "assistant",
    "type": "thought",
    "title": "思考",
    "status": "processing",
    "render_mode": "streaming",
    "content": "附件名为 mock_举报信_5图片_交易流水.pdf，文件为 PDF..."
  }
}
// 结束
{
  "event": "chat_message",
  "chat_message": {
    "id": "abc-thought",
    "role": "assistant",
    "type": "thought",
    "title": "思考",
    "status": "end",
    "render_mode": "streaming",
    "content": "思考结束"
  }
}
```

### 工具调用（行级进度，覆盖更新）
```json
// 开始
{
  "chat_message": {
    "id": "abc-tool",
    "role": "assistant",
    "type": "tool",
    "title": "工具调用",
    "status": "start",
    "render_mode": "list",
    "content": "准备调用原文提取工具"
  }
}
// 进度行 1
{
  "chat_message": {
    "id": "abc-tool",
    "role": "assistant",
    "type": "tool",
    "title": "工具调用",
    "status": "processing",
    "render_mode": "list",
    "line_id": "tool-line-1",
    "order": 1,
    "content": "上传 PDF 中…"
  }
}
// 同一行覆盖更新
{
  "chat_message": {
    "id": "abc-tool",
    "role": "assistant",
    "type": "tool",
    "title": "工具调用",
    "status": "processing",
    "render_mode": "list",
    "line_id": "tool-line-1",
    "order": 1,
    "content": "上传完成，开始 OCR…"
  }
}
// 另一行进度（追加一行）
{
  "chat_message": {
    "id": "abc-tool",
    "role": "assistant",
    "type": "tool",
    "title": "工具调用",
    "status": "processing",
    "render_mode": "list",
    "line_id": "tool-line-2",
    "order": 2,
    "content": "OCR 处理中…"
  }
}
// 结束
{
  "chat_message": {
    "id": "abc-tool",
    "role": "assistant",
    "type": "tool",
    "title": "工具调用",
    "status": "end",
    "render_mode": "list",
    "content": "工具执行完成"
  }
}
```

### 结论
```json
// 开始
{
  "chat_message": {
    "id": "abc-result",
    "role": "assistant",
    "type": "result",
    "title": "结论",
    "status": "start",
    "render_mode": "streaming",
    "content": ""
  }
}
{
  "chat_message": {
    "id": "abc-result",
    "role": "assistant",
    "type": "result",
    "title": "结论",
    "status": "processing",
    "render_mode": "streaming",
    "content": "已提取完文本内容，整体完整但部分段落有缺字错乱..."
  }
}
{
  "chat_message": {
    "id": "abc-result",
    "role": "assistant",
    "type": "result",
    "title": "结论",
    "status": "end",
    "render_mode": "streaming",
    "content": "结论阶段结束"
  }
}
```
