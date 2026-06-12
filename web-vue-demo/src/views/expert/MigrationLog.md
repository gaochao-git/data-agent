# 法律助手（/expert）Vue 迁移日志

## 迁移概述

- **源代码**: `web/src/pages/GeneralAssistant` (React)
- **目标代码**: `web-vue/src/views/expert` (Vue3)
- **访问路径**: `/expert` 和 `/expert/:conversationId`
- **迁移范围**: Phase 1（基础架构） + Phase 2（API与SSE流处理） + Phase 3（核心Composable） + Phase 4（UI组件）

---

## 待办事项清单

### Phase 1: 基础架构迁移 ✅ 已完成
- [x] 迁移类型定义 `types/index.ts`
  - [x] ChatMessage 类型
  - [x] ConversationHistoryItem 类型
  - [x] UploadedFile 类型
  - [x] ParsedCitation 类型（来自 AISpecialistHome）
  - [x] Agent、AgentOpeningMessage 等配置类型
- [x] 迁移线程管理工具 `utils/threadHelpers.ts`
- [x] 迁移智能体配置 `config.ts`

### Phase 2: API与SSE流处理迁移 ✅ 已完成
- [x] 迁移 API 封装 `api/expert.ts`
  - [x] createConversation
  - [x] getConversation
  - [x] getConversationHistory
  - [x] uploadFile
  - [x] pollFileStatus
  - [x] generateTitle
  - [x] fetchPolicy
- [x] 迁移 SSE 流处理 `composables/useSSEStream.ts`

### Phase 3: 核心Composable迁移 ✅ 已完成
- [x] 迁移核心状态管理 `composables/useExpertAssistant.ts`

### Phase 4: UI组件迁移 ✅ 已完成
- [x] 迁移法规预览面板 `components/PolicyPreviewPanel.vue`
- [x] 迁移调试面板 `components/TracePanel.vue`
- [x] 迁移工作台容器 `components/ExpertWorkbench.vue`
- [x] 迁移核心聊天逻辑 `components/UnifiedEmbeddedContent.vue`

---

## 迁移过程记录

### Phase 1 迁移进度 ✅ 已完成

#### 1. 类型定义 (types/index.ts) ✅

**状态**: 已完成

**源文件**:
- `web/src/pages/GeneralAssistant/types.ts`
- `web/src/pages/AISpecialistHome/types.ts`

**迁移要点**:
- 保持字段名不变（后端接口字段、业务字段不进行驼峰转换）
- 包含 ChatMessage、ThinkingItem、AssistantReference 类型
- 包含 ParsedCitation、PolicyResponse 类型

**已迁移的类型**:
- UploadedFile - 上传文件状态
- MessageRole - 消息角色
- StepStatus - 思考步骤状态
- ThinkingItem - 思考步骤项
- AssistantReference - 法规引用信息
- ChatMessage - 聊天消息
- ConversationHistoryItem - 对话历史项
- ModelType - 模型类型
- AgentCategory - 智能体分类
- AgentOpeningMessage - 智能体开场消息
- AgentGuideConfig - AI 引导消息配置
- AgentInputAction - 输入区域操作
- AgentInputControls - 输入控件配置
- ChatSseEvent - SSE 事件
- EmbeddedComponentProps - 嵌入组件的 props 类型
- Agent - 智能体配置
- ChatMockMessage - Mock 消息
- ParsedCitation - 法规引用解析结果
- PolicyResponse - 法规响应
- ViewMode - 视图模式
- ActiveAgentState - 激活的智能体状态

#### 2. 线程管理工具 (utils/threadHelpers.ts) ✅

**状态**: 已完成

**源文件**: `web/src/pages/GeneralAssistant/utils/threadHelpers.ts`

**迁移要点**:
- 使用模块级 promise 变量实现锁机制
- ensureThreadId 函数检查现有 threadId，如无则创建
- Promise 锁防止并发创建多个线程

**已迁移的函数**:
- ensureThreadId() - 确保线程 ID 存在

#### 3. 智能体配置 (config.ts) ✅

**状态**: 已完成

**源文件**: `web/src/pages/GeneralAssistant/agentConfig/expertAgent.ts`

**迁移要点**:
- expert_agent 配置（ID: expert_agent，名称: 法律检索）
- openingMessage 包含标题、内容、快捷提示
- inputActions 包含模型选择、深度思考开关
- workbench 配置

**已迁移的内容**:
- expertAgent 常量 - 法律检索智能体完整配置
- EXPERT_AGENT_ID 常量 - 智能体 ID

### Phase 2 迁移进度 ✅ 已完成

#### 1. API 封装 (api/expert.ts) ✅

**状态**: 已完成

**源文件**: `web/src/pages/GeneralAssistant/hooks/useGeneralAssistant.ts` (API部分)

**已迁移的函数**:
- createConversation() - 创建会话
- getConversation() - 获取会话消息
- getConversationHistory() - 获取历史会话列表
- generateTitle() - 生成会话标题
- uploadFile() - 上传文件
- getFileInfo() - 获取文件信息
- pollFileStatus() - 轮询文件处理状态
- deleteFile() - 删除文件
- fetchPolicy() - 获取法规内容
- conversationCompletion() - 会话补全请求（SSE 流式）

**API 基础路径**: `${BASE_PREFIX}/api` 使用 `VITE_API_BASE_URL` 环境变量

#### 2. SSE 流处理 (composables/useSSEStream.ts) ✅

**状态**: 已完成

**源文件**: `web/src/pages/GeneralAssistant/utils/parseChatSse.ts`

**已迁移的功能**:
- processSSEStream() - 处理 SSE 流式响应（ChatMessage 格式）
- useSSEStream() - SSE Composable Hook

**支持的 SSE 事件类型**:
- message_start - 开始新消息
- node_start - 开始思考步骤
- node_end - 结束思考步骤
- content_delta - 内容增量
- message_end - 消息结束
- completed - 流式完成
- error - 错误事件
- [DONE] - 流式结束标记

**导出的 Composable**:
- startStream() - 启动 SSE 流
- stopStream() - 停止 SSE 流
- isStreaming - 流式传输状态（响应式）

### Phase 3 迁移进度 ✅ 已完成

#### 核心状态管理 (composables/useExpertAssistant.ts) ✅

**状态**: 已完成

**源文件**: `web/src/pages/GeneralAssistant/hooks/useGeneralAssistant.ts`

**已迁移的功能**:
- sendChatMessage() - 发送聊天消息
- stopChatStreaming() - 停止流式输出
- loadConversationHistory() - 加载历史对话列表
- switchToThread() - 切换到历史对话
- clearChatState() - 清空聊天状态
- resetChatHistory() - 重置对话
- createNewThread() - 创建新对话线程
- handleFileUpload() - 处理文件上传
- removeUploadedFile() - 移除已上传文件
- clearUploadedFiles() - 清空已上传文件
- handleCitationClick() - 处理引用点击

**已迁移的状态**:
- chatMessages - 聊天消息列表
- chatInput - 当前输入框内容
- isChatStreaming - 是否正在流式接收回复
- activeAssistantMessageId - 当前 AI 消息 ID
- conversationHistory - 历史对话列表
- activeHistoryId - 当前激活的历史 ID
- isHistoryCollapsed - 历史侧边栏折叠状态
- isHistoryLoading - 历史加载状态
- chatThreadId - 当前会话 ID
- historyRefreshTrigger - 历史刷新触发器
- uploadedFiles - 已上传文件列表
- authError - 认证错误

**迁移要点**:
- 使用 Vue 3 的 ref 替代 React 的 useState
- 使用 watch 替代 React 的 useEffect（依赖数组监听）
- 使用 onMounted/onUnmounted 替代 React 的 useEffect
- 使用 vue-router 的 useRoute/useRouter 替代 react-router-dom

### Phase 4 迁移进度 ✅ 已完成

#### 1. 法规预览面板 (components/PolicyPreviewPanel.vue) ✅

**状态**: 已完成

**源文件**: `web/src/pages/AISpecialistHome/RightPanels/PolicyPreviewPanel.tsx`

**迁移要点**:
- 显示法规原文，支持条款高亮和自动定位
- 监听 citation 变化，调用 fetchPolicy 获取法规内容
- 处理加载状态和错误状态
- 使用字符串分割和 <mark> 标签实现高亮
- 高亮渲染完成后，使用 scrollIntoView 将第一个高亮元素滚动到可视区域

**已迁移的功能**:
- 法规内容获取（基于 citation.sourceId 或 citation.anchorId）
- 文本高亮（基于 citation.clause 或 citation.description）
- 自动滚动定位到高亮区域
- 加载状态、错误状态显示

#### 2. 调试面板 (components/TracePanel.vue) ✅

**状态**: 已完成

**源文件**: `web/src/pages/AISpecialistHome/RightPanels/TracePanel.tsx`

**迁移要点**:
- 显示 AI 思考过程和调试信息
- 使用树形结构展示嵌套的思考步骤
- 不同状态（进行中、已完成、失败）使用不同图标和颜色
- 支持刷新 trace 调试记录

**已迁移的功能**:
- Trace 记录获取（GET /api/ai_chat/traces/{conversationId}）
- 事件列表展示（包含 ts_ms、event、payload）
- 格式化时间戳显示
- 手动刷新功能

#### 3. 工作台容器 (components/ExpertWorkbench.vue) ✅

**状态**: 已完成

**源文件**: `web/src/pages/GeneralAssistant/components/unified/workbench/ExpertWorkbench.tsx`

**迁移要点**:
- 管理"法规依据"和"调试"两个面板
- 支持面板切换和关闭
- 根据 traceEnabled 决是否显示调试面板

**已迁移的功能**:
- 面板切换 tabs（法规依据、调试）
- 面板内容渲染（PolicyPreviewPanel 或 TracePanel）
- 关闭按钮
- 响应式布局样式

#### 4. 核心聊天逻辑 (components/UnifiedEmbeddedContent.vue) ✅

**状态**: 已完成

**源文件**: `web/src/pages/GeneralAssistant/components/UnifiedEmbeddedContent.tsx`

**迁移要点**:
- 管理聊天消息列表
- 处理消息发送和流式接收
- 文件上传管理
- 法规引用处理
- 工作台面板控制
- 支持调整工作台宽度

**已迁移的功能**:
- 消息列表展示（开场消息、用户消息、AI 消息）
- 思考步骤展示
- 法规引用展示（点击后打开工作台）
- 输入框（支持 Enter 发送、Shift+Enter 换行）
- 模型选择、深度思考开关
- 文件上传按钮（法律助手不显示）
- 发送/停止按钮
- 工作台显示/隐藏
- 工作台宽度调整（拖拽）

**迁移要点**:
- 使用 Vue 3 Composition API
- 使用 useExpertAssistant composable 管理状态
- 使用 v-model 双向绑定
- 使用 watch 监听状态变化
- 使用 computed 计算属性
- 使用 nextTick 处理 DOM 更新

---

## 风险与注意事项

### 高风险项
1. **线程创建锁逻辑**: `threadHelpers.ts` 中的并发控制需精确迁移 ✅ 已完成
2. **SSE事件解析**: 必须保持与后端一致的事件格式解析逻辑 ✅ 已完成
3. **法规高亮逻辑**: `PolicyPreviewPanel` 中的文本高亮和自动滚动定位需精确复现 ✅ 已完成

### 中风险项
1. **引用传递差异**: Vue 的响应式对象与 React 的不可变更新存在差异 ✅ 已处理
2. **生命周期差异**: React 的 useEffect 与 Vue 的 watch/onMounted 行为不同 ✅ 已处理

### 低风险项
1. **纯样式组件**: 无复杂逻辑的纯展示组件可直接迁移模板 ✅ 已完成
2. **常量配置**: 纯数据配置可直接复制 ✅ 已完成

---

## 已创建的文件结构

```
web-vue/src/views/expert/
├── types/
│   └── index.ts                    ✅ 类型定义
├── utils/
│   └── threadHelpers.ts            ✅ 线程管理工具
├── api/
│   └── expert.ts                   ✅ API 封装
├── composables/
│   ├── useSSEStream.ts            ✅ SSE 流处理
│   ├── useExpertAssistant.ts       ✅ 核心状态管理
│   └── index.ts                   ✅ Composables 导出
├── components/
│   ├── PolicyPreviewPanel.vue       ✅ 法规预览面板
│   ├── TracePanel.vue              ✅ 调试面板
│   ├── ExpertWorkbench.vue         ✅ 工作台容器
│   └── UnifiedEmbeddedContent.vue   ✅ 核心聊天逻辑
├── config.ts                      ✅ 智能体配置
├── Vue迁移方案.md                  ✅ 迁移方案文档
├── 相关待迁移文件列表.md          ✅ 待迁移文件列表
└── MigrationLog.md                 ✅ 本迁移日志
```

---

## 最后更新时间
2026-03-12

---

## 迁移完成总结

Phase 1、Phase 2、Phase 3 和 Phase 4 已全部完成。

### 已完成的迁移内容

1. **类型定义**: 完整的类型系统，包括所有数据结构和接口
2. **工具函数**: 线程管理、API 封装、SSE 流处理
3. **状态管理**: useExpertAssistant composable 提供核心状态和方法
4. **UI 组件**:
   - PolicyPreviewPanel.vue - 法规预览面板
   - TracePanel.vue - 调试面板
   - ExpertWorkbench.vue - 工作台容器
   - UnifiedEmbeddedContent.vue - 核心聊天逻辑

### 待完成事项

以下组件需要由 general_assistant 模块提供支持：
- Conversation.vue - 对话组件（消息列表 + 输入框）
- Workbench.vue - 工作台包装组件
- EmbeddedLayout.vue - 嵌入布局

这些组件由 general_assistant/Layout.vue 负责调用和渲染。
