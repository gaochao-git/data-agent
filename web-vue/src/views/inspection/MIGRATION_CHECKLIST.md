# Inspection 模块迁移检查清单

## 迁移状态总览

| 步骤 | 模块 | 状态 | 说明 |
|------|------|------|------|
| 步骤一 | 类型定义 (types/) | ✅ 已完成 | 所有核心类型已定义 |
| 步骤二 | API + SSE | ✅ 已完成 | API 封装和 SSE 流处理完成 |
| 步骤三 | useViolationAnalysis | ✅ 已完成 | 主状态管理 composable 完成 |
| 步骤四 | EmbeddedLayout | ✅ 已完成 | 主布局组件完成 |
| 步骤五 | EmbeddedConversation | ✅ 已完成 | 对话组件基础结构 |
| 步骤六 | Console | ✅ 已完成 | 控制台组件基础结构 |

## 文件清单

### 步骤一：类型定义 ✅
```
src/views/inspection/types/index.ts (320 行)
├── LawItem
├── HistoricalCase
├── EvidenceItem
├── ViolationAnalysis
├── AnalysisResult
├── ChatMessage / ChatRole
├── ContentBlock / TextContentBlock / FileContentBlock / NonStandardContentBlock
├── AnalysisTask / TaskStatus / SSETaskStatus
├── HumanReviewData / ResumeInput / ReviewAction
├── AnalysisHistoryItem / AnalysisStage
└── ViolationFileGroup
```

### 步骤二：API 和 SSE ✅
```
src/views/inspection/api/inspection.ts (200+ 行)
├── createConversation
├── getConversationMessages
├── getConversationState
├── getConversationFiles
├── uploadFile
├── deleteFileGroup / deleteFile
├── completionStream
├── resumeStream
├── generateTitle
└── getDownloadUrl

src/views/inspection/composables/useSSEStream.ts (200+ 行)
├── processSSEStream - 核心 SSE 处理函数
├── useSSEStream - SSE Composable
└── SSEEventHandlers / SSEStreamOptions 类型定义
```

### 步骤三：useViolationAnalysis ✅
```
src/views/inspection/composables/useViolationAnalysis.ts (1800+ 行)
├── 响应式状态 (analysisResult, isAnalyzing, chatMessages, etc.)
├── 计算属性 (missingRequiredLabels, hasFiveRequiredInputs, activeHistory)
├── 方法：
│   ├── loadThreadMessages - 加载历史消息
│   ├── createNewThread - 创建新线程
│   ├── callViolationAnalysisSSE - 调用分析 SSE
│   ├── sendChatMessage - 发送聊天消息
│   ├── sendReportRequest - 发送报告请求
│   ├── stopChatStreaming - 停止流式输出
│   ├── resumeReportGeneration - 恢复报告生成
│   └── resumeConversation - 恢复对话
└── Human-in-the-Loop 审核回调：
    ├── handleHumanReviewApprove
    ├── handleHumanReviewEdit
    └── handleHumanReviewReject
```

### 步骤四：EmbeddedLayout ✅
```
src/views/inspection/EmbeddedLayout.vue (400+ 行)
├── Props：
│   ├── agentId - 后端 agent_id
│   └── initialThreadId - 初始线程ID
├── Events：
│   ├── conversation-id-change
│   ├── history-refresh
│   └── title-change
├── 布局功能：
│   ├── 历史记录面板（可折叠，宽度可调）
│   ├── 对话面板（左侧面板，宽度可调）
│   ├── 控制台/预览面板（右侧）
│   └── 拖拽条支持实时调整面板宽度
├── 子组件：
│   ├── EmbeddedConversation
│   └── Console
└── 交互功能：
    ├── 新建对话
    ├── 选择历史记录
    ├── 发送消息
    ├── 停止流式输出
    ├── 上传文件
    ├── 生成报告
    └── 任务操作
```

### 步骤五：EmbeddedConversation ✅
```
src/views/inspection/components/EmbeddedConversation.vue (500+ 行)
├── Props：
│   ├── agentId, threadId
│   ├── messages, isStreaming
│   ├── analysisTasks, analysisResult
│   └── ...
├── Events：
│   ├── send-message
│   ├── stop-streaming
│   ├── upload-files
│   └── generate-report
├── 功能：
│   ├── 消息列表渲染
│   ├── 任务进度显示
│   ├── 输入框发送
│   └── 文件上传
```

### 步骤六：Console ✅
```
src/views/inspection/components/Console.vue (600+ 行)
├── Props：
│   ├── threadId
│   ├── analysisResult
│   ├── selectedDocument
│   └── ...
├── Events：
│   ├── update:analysis-result
│   ├── regenerate
│   └── close
├── 功能：
│   ├── 审核编辑器
│   ├── Word/Excel 文档预览
│   ├── 案例报告显示
│   ├── 法律条款显示
│   └── 分析结果展示
```

## 测试文件

```
src/views/inspection/__tests__/unit/migration.spec.ts - 单元测试
src/views/inspection/test-migration.ts - 导出测试
```

## 检查清单

- [x] 类型定义完整
- [x] 常量定义完整
- [x] 工具函数完整
- [x] API 封装完整
- [x] SSE 流处理完整
- [x] useViolationAnalysis composable 完整
- [x] EmbeddedLayout 组件完整
- [x] EmbeddedConversation 组件基础结构
- [x] Console 组件基础结构

## 下一步计划

1. 完成剩余子组件迁移（TaskManage, FileUploadZone 等）
2. 接入独立路由
3. 导出嵌入式组件供 general-assistant 接入
4. 完整联调 5 个子功能

## 迁移总结

当前迁移进度：**60% 完成**

已完成的模块：
- 基础层（类型、常量、工具函数）100%
- 数据层（API、SSE）100%
- 逻辑层（useViolationAnalysis）100%
- 布局层（EmbeddedLayout）100%
- 基础组件（EmbeddedConversation、Console）80%

待完成：
- 剩余子组件（TaskManage 等）
- 路由集成
- 嵌入式组件导出
- 联调测试
