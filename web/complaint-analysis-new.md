# 举报件处理新界面设计（ComplaintAnalysisNew）

## 背景
- 现有 `ComplaintAnalysis` 页面文件体量大、结构集中，难以继续演进新的视觉与交互方案。
- 举报件处理模块需要一套独立的新样式，用于验证新的布局与组件拆分，同时保持历史入口与功能可用。
- 为避免对既有流程产生影响，计划通过新增路由来加载新界面，两套样式可在上线初期并行存在。

## 路由策略
- 新增前端路由：`/complaint-analysis-new`（最终路径），由 `src/pages/ComplaintAnalysisNew/index.tsx` 作为入口。
- 原有 `/complaint-analysis` 保持不变，继续渲染旧版组件，方便业务方回退或对比。
- `App.tsx` 中新增 `Route path="/complaint-analysis-new" element={<ComplaintAnalysisNew />} />`，其余鉴权逻辑沿用旧版。
- 后端无需修改；依然复用同一批 API（上传、SSE、抽取等）。

## 目录结构规划（按三栏拆分）
```
src/pages/ComplaintAnalysisNew/
├── index.tsx                         # 路由入口，负责加载 Layout Shell
├── hooks/
│   ├── useComplaintAnalysisNew.ts    # 核心数据、上传、SSE
│   ├── useLayoutState.ts             # 三栏展开/折叠、面板宽度
│   ├── useSessionList.ts             # 新建/历史会话管理
│   └── useWorkspacePanel.ts          # 工作台的显示状态
├── components/
│   ├── layout/
│   │   ├── SplitLayout.tsx           # 三栏栅格 + 响应式
│   │   └── SectionContainer.tsx      # 统一的卡片/滚动容器
│   ├── expertPanel/
│   │   ├── ExpertPanel.tsx           # 左栏整合组件
│   │   ├── ExpertHeader.tsx          # 图标 + 文案 + 折叠按钮
│   │   ├── NewSessionEntry.tsx       # “新会话”按钮行
│   │   ├── SessionList.tsx           # 历史会话列表
│   │   └── SessionListItem.tsx
│   ├── conversation/
│   │   ├── ConversationShell.tsx     # 中栏容器（居中）
│   │   ├── NewConversationCard.tsx   # 新会话卡片（输入 + 上传）
│   │   ├── ConversationTimeline.tsx  # 消息列表
│   │   └── ConversationComposer.tsx  # 输入区（贴底 + AI 说明文案）
│   ├── workspace/
│   │   ├── WorkspacePanel.tsx        # 右栏总入口：Header + 内容切换（保持贴底）
│   │   ├── WorkspaceHeader.tsx       # Tab/Segment 控制器，切换四大内容
│   │   ├── PreviewPanel.tsx          # 举报扫描件预览（PDF 预览器）
│   │   ├── RawTextPanel.tsx          # 举报件原文
│   │   ├── SummaryPanel.tsx          # 举报件摘要
│   │   └── AnalysisPanel.tsx         # 举报件分析（结论/图表 Tab）
│   ├── upload/
│   │   ├── UploadDrawer.tsx          # 右侧或中栏弹出的抽屉，展示上传步骤/队列
│   │   └── UploadDropzone.tsx        # 拖拽/点选组件，复用黑白灰风格
│   └── shared/                       # 黑白灰 UI 通用组件
│       ├── IconButton.tsx
│       └── EmptyState.tsx
├── constants.ts
├── types.ts
└── utils.ts
```
- 组件以“栏位 → 功能块 → 子组件”划分，防止单文件承担多种职责。
- Shared 目录沉淀可复用的纯黑/纯白按钮、分割线、滚动条等样式，保持风格一致。

## 数据与状态复用
- 优先复用 `ComplaintAnalysis` 中既有 Hook（如 `useComplaintAnalysis`）的核心请求与 SSE 逻辑。
  - 若旧 Hook 难以直接复用，则在 `hooks/useComplaintAnalysisNew.ts` 中对接口层做轻量包装，保持返回的 `analysis` 结构与旧版一致，降低后端改动。
  - 公共逻辑（缓存解析、聊天记录处理、拖拽宽度等）抽取到 `src/pages/ComplaintAnalysis/shared/` 或 `src/lib/`，避免“新旧两套复制”。
- 新增 Hook `useLayoutState` 只处理前端交互状态：面板伸缩、Tab 记忆、抽屉开关，不与业务数据耦合。

## 页面布局确认与风格要求
- 页面水平三栏：`举报件 AI 专家 | 对话区 | 结论/工作台`
  - **举报件 AI 助手（左栏）**
    - Header：左侧为头像/徽标图标 + 文案“举报办理 AI 辅助”，右侧固定一个“双箭头”收起按钮，控制整个左栏折叠。
    - 内容区域允许滚动，分上下两段：
      1. 独立一行“新会话”入口（加号图标 + 文案），点击后在中间对话区进入新建会话状态。
      2. “历史会话”标题，下方每一行渲染一条历史对话摘要（标识状态、更新时间、最后一句话等），点击后右侧加载对应会话。
  - **对话区（中栏）**
    - 新会话：居中展示对话启动卡片，卡片含标题、副标题，主体为可点击的两个入口：
      - “请输入举报受理时间”（输入组件或触发弹框）。
      - “并上传举报件”（文件拖拽/选择区域），卡片右下角放置黑白配色的“向上箭头”图标按钮，提交后触发会话创建。
    - 历史会话：沿用旧有聊天体验（消息列表、建议提问、附件气泡等），但整体内容在宽屏中保持居中（可设置最大宽度 + 居中容器），左右保留留白，避免视觉过宽。
    - 聊天输入区 `ConversationComposer` 固定在浏览器底部，内容区在其上方滚动；输入框底部居中加一句“AI 生成内容仅供参考”等提示文案。
    - 当用户在聊天消息中点击“分析结论/工作台”类按钮时，触发右侧栏展开。
  - **结论/工作台（右栏）**
    - 默认收起，仅当对话中触发分析结论、证据面板等操作时滑出，可包含摘要卡片、阶段导航、证据列表、图表。
    - `WorkspacePanel` 拆分为 Header + Content：Header 提供 Tab/Segment 控件（可含图标 + 状态），切换时更新下方内容；Content 使用 `flex` + `min-height: 100%` 贴合浏览器底部，确保任一模块都能占满视口高度并在内部滚动。
    - 右栏展开后不遮挡中栏，而是并排展示，可提供关闭/返回按钮。
- 上传区仍拆成按钮 + 抽屉（挂在中栏卡片或右栏入口上），沿用 `MAX_FILE_SIZE` 等限制提示。
- `UploadDropzone` 在“新会话”卡片中占据独立模块，可拖入 PDF/图片/Office 文件；允许点击后打开系统文件选择器，底部提示文件限制与合规说明。
- `UploadDrawer` 作为抽屉组件托管上传队列、进度、失败重试入口，必要时展示多文件并发上传状态；完成后提示“可点击发起会话”。
- ChatPanel 子组件化：消息列表、输入区、建议提问分别独立文件，便于居中/响应式布局。
- 右栏四大模块明确对应现有功能：**举报扫描件预览（PDF Previewer）**、**举报件原文**、**举报件摘要**、**举报件分析**，每块独立组件，通过 Tab 或折叠区切换，保持与旧版内容一致但 UI 风格焕新。
- **风格**
  - 整体黑白灰配色 + 淡蓝背景：页面主背景保持 `#f1f6ff`，按钮/入口（如“新会话”）使用更亮的淡蓝色（`#e7efff`，hover `#d9e6ff`），彻底弃用黑色块。
  - 文案与图标保持简洁线性风格，强调科技感，减少色块干扰；重点状态可辅以单色渐变或轻微亮度变化。
  - 统一使用定制的圆角、行高与分割线样式，保存于 `shared` 组件或 `constants` 中，方便后续覆盖主题。
  - 专家 Panel Header 仅保留“举报处理 AI 专家”主标题，无额外副标题；收起按钮与其他操作按钮取消外框（`IconButton` 无边框，仅保留 hover 变色）。
  - “新会话”入口使用纯色块按钮，无描边/虚线样式；历史会话列表采用平直分隔线（去掉圆角卡片），保持极简。

## 开发与迁移策略
1. **第一阶段**：完成目录骨架、路由占位组件，接入旧 Hook，确保功能完整。
2. **第二阶段**：迭代视觉与交互细节（暗色模式、响应式、图表主题）。
3. **第三阶段**：根据效果与业务反馈，决定是否用新页面替换旧路由或保留 A/B。
- 所有公共逻辑抽离后需补充单元/组件测试（若已有测试框架）或在文档记录手测点，避免回归。

## 待确认事项
- 与设计稿一致的视觉规范（间距、配色、图标库）是否已有定稿，若无需要约定临时样式基线。
- “新会话对话框”标题/文案的最终措辞，以及箭头按钮的交互（仅提交？是否需 loading 状态）。
- 历史会话列表需要展示的字段（状态、涉案主体、更新时间等）和最大条数。
- 是否需要在前端入口增加“切换新样式”入口（例如 Banner 或按钮）供内部人员互跳。

## 下一步
- 评审本设计文档后，即可按上述结构初始化 `ComplaintAnalysisNew` 目录与入口组件。
- 在 MR/PR 中附带路由截图或动图，便于业务侧审阅新旧界面差异。
