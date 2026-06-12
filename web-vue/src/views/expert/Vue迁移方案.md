# 法律助手（/expert）Vue3迁移方案

## 一、迁移背景与目标

### 1.1 迁移背景
- **原代码位置（React）**: `web/src/pages/GeneralAssistant` 及相关文件
- **目标位置（Vue3）**: `web-vue/src/views/expert`
- **访问路径**: `/expert` 和 `/expert/:conversationId`

### 1.2 核心变化
1. **历史面板独立**: Vue版本中历史面板已从功能界面拆出，通过TopNavBar进入，与expert模块无嵌入关系
2. **集成方式**: expert模块作为嵌入式组件，被 `general_assistant/Layout.vue` 调用
3. **高保真要求**: 样式与React版本完全一致

### 1.3 参考资源
- **已完成参考**: `web-vue/src/views/inspection`（目录结构参考，内容不参考）
- **入口调用方**: `web-vue/src/views/general_assistant/Layout.vue`（已由其他同事负责）
- **React源码**: `web/src/pages/GeneralAssistant`

---

## 二、React源码结构分析

### 2.1 核心文件清单

```
web/src/pages/GeneralAssistant/
├── agentConfig/
│   ├── expertAgent.ts          # 法律检索智能体配置（ID: expert_agent）
│   └── components.ts           # 动态组件加载配置
├── components/
│   ├── UnifiedEmbeddedContent.tsx   # 核心聊天逻辑（~1177行）
│   ├── unified/
│   │   ├── workbench/
│   │   │   └── ExpertWorkbench.tsx  # 法规依据+调试面板
│   │   ├── Conversation.tsx
│   │   └── Workbench.tsx
│   └── ...
├── hooks/
│   └── useGeneralAssistant.ts   # 核心状态管理Hook
├── types.ts                     # 类型定义
├── utils/
│   ├── parseChatSse.ts          # SSE事件解析
│   └── threadHelpers.ts         # 线程创建锁
└── UnifiedEmbeddedLayout.tsx    # 嵌入布局

# 复用的AISpecialistHome组件
web/src/pages/AISpecialistHome/
├── RightPanels/
│   ├── PolicyPreviewPanel.tsx   # 法规预览（被ExpertWorkbench使用）
│   └── TracePanel.tsx           # 调试面板
└── types.ts                     # ParsedCitation类型
```

### 2.2 关键架构特点

1. **三层架构**:
   - **外壳层** (`UnifiedEmbeddedLayout`): 调度中心，从agentRegistry提取配置
   - **内核层** (`UnifiedEmbeddedContent`): 核心AI交互逻辑，管理SSE流
   - **业务配置层** (`expertAgent.ts`): JSON-like纯数据定义

2. **工作台模式** (`ExpertWorkbench`):
   - 包含"法规依据" (`PolicyPreviewPanel`) 和 "调试" (`TracePanel`) 两个面板
   - 通过tab切换

3. **状态管理** (`useGeneralAssistant`):
   - 封装所有后端API交互
   - 线程创建锁 (`threadHelpers`)
   - SSE流管理

---

## 三、Vue目标架构设计

### 3.1 目录结构

```
web-vue/src/views/expert/
├── api/
│   └── expert.ts                    # API封装（类比inspection/api/inspection.ts）
├── components/
│   ├── ExpertWorkbench.vue          # 工作台容器（法规依据+调试）
│   ├── PolicyPreviewPanel.vue       # 法规预览面板（从AISpecialistHome迁移）
│   ├── TracePanel.vue               # 调试面板（从AISpecialistHome迁移）
│   ├── UnifiedEmbeddedContent.vue   # 核心聊天逻辑
│   ├── Conversation.vue             # 对话组件
│   ├── MessageList.vue              # 消息列表
│   ├── Composer.vue                 # 输入框组件
│   └── ...
├── composables/
│   ├── useExpertAssistant.ts        # 核心状态管理（类比useGeneralAssistant）
│   └── useSSEStream.ts              # SSE流处理
├── types/
│   └── index.ts                     # 类型定义
├── utils/
│   └── threadHelpers.ts             # 线程管理辅助函数
├── views/
│   └── index.vue                    # 独立页面入口
├── EmbeddedLayout.vue               # 嵌入布局（供general_assistant调用）
├── config.ts                        # 专家智能体配置（从expertAgent.ts迁移）
├── routes.ts                        # 路由配置
└── index.ts                         # 模块导出
```

### 3.2 与inspection模块对比

| 维度 | inspection | expert（法律助手） |
|------|-----------|------------------|
| **主要功能** | 执法检查报告生成 | 法律条文检索问答 |
| **工作台内容** | 文档预览、审核编辑 | 法规依据预览、调试 |
| **右侧面板** | Console.vue | ExpertWorkbench.vue |
| **核心组件** | EmbeddedConversation | UnifiedEmbeddedContent |
| **特殊逻辑** | 文件分组、报告类型切换 | 法规引用高亮、条款检索 |
| **Agent ID** | inspection相关 | expert_agent |

---

## 四、分阶段迁移详细方案

### **Phase 1: 基础架构迁移**
**目标**: 类型定义、常量、工具函数

#### 1.1 源文件 → 目标文件映射

| 源文件(React) | 目标文件(Vue) | 迁移要点 |
|--------------|--------------|---------|
| `GeneralAssistant/types.ts` | `expert/types/index.ts` | Agent、ChatMessage、ThinkingItem等类型 |
| `AISpecialistHome/types.ts` | `expert/types/index.ts` | ParsedCitation、PolicyResponse等类型 |
| `GeneralAssistant/utils/threadHelpers.ts` | `expert/utils/threadHelpers.ts` | 线程创建锁逻辑 |
| `GeneralAssistant/agentConfig/expertAgent.ts` | `expert/config.ts` | 智能体配置（纯数据） |

#### 1.2 迁移注意事项

**类型定义迁移要点：**
1. **保持字段名不变**：所有后端接口字段名、业务字段名保持原样，不进行驼峰转换
2. **ChatMessage类型**：包含message_id、role、text/content（流式）、steps（思考过程）、references（引用）
3. **ThinkingItem类型**：用于展示思考步骤，包含label、status、children嵌套
4. **AssistantReference类型**：法规引用信息，包含sourceId、anchorId、clause等字段
5. **AgentConfig类型**：智能体配置，包含openingMessage、inputActions、workbench配置

**线程创建锁迁移要点：**
1. **模块级变量**：使用模块级promise变量实现锁机制
2. **ensureThreadId函数**：检查现有threadId，如无则创建，如有则等待现有创建完成
3. **Promise锁**：使用全局promise防止并发创建多个线程
4. **错误处理**：创建失败后清除锁，允许重试

**智能体配置迁移要点：**
1. **expert_agent配置**：ID为'expert_agent'，名称为'法律检索'
2. **openingMessage**：包含title、content、prompts（快捷提示）
3. **inputActions**：模型选择（DeepSeek-R1）、深度思考开关
4. **workbench配置**：enabled: true, variant: 'expert'
5. **图标和颜色**：icon: 'fa-search', color: 'green'

---

### **Phase 2: API与SSE流处理迁移**
**目标**: API封装、SSE流处理Composable

#### 2.1 源文件 → 目标文件映射

| 源文件(React) | 目标文件(Vue) | 迁移要点 |
|--------------|--------------|---------|
| `GeneralAssistant/hooks/useGeneralAssistant.ts` (API部分) | `expert/api/expert.ts` | API封装函数 |
| `GeneralAssistant/utils/parseChatSse.ts` | `expert/composables/useSSEStream.ts` | SSE流处理 |

#### 2.2 API层迁移要点

**文件位置**: `expert/api/expert.ts`

**需要封装的API函数：**
1. **createConversation(agentId)** - 创建新会话，POST /api/conversations
2. **getConversation(conversationId)** - 获取会话消息，GET /api/conversations/:id
3. **getConversationHistory()** - 获取历史会话列表，GET /api/conversations
4. **uploadFile(file, conversationId)** - 上传文件，POST /api/uploaded-files/upload
5. **pollFileStatus(fileId)** - 轮询文件处理状态，GET /api/uploaded-files/:id
6. **generateTitle(conversationId)** - 生成会话标题，POST /api/conversations/:id/title
7. **fetchPolicy(policyKey)** - 获取法规内容，GET /api/policies/:key

**迁移注意事项：**
- 所有请求必须携带 `credentials: 'include'` 以传递cookie
- Content-Type默认为application/json，文件上传使用FormData
- 返回数据格式统一为 `{ data: ... }`，错误处理需提取error message
- 字段名保持与后端一致，不做驼峰转换

#### 2.3 SSE流处理迁移要点

**文件位置**: `expert/composables/useSSEStream.ts`

**核心功能：**
1. **startStream(options)** - 启动SSE流连接
2. **stopStream()** - 中止当前流
3. **isStreaming状态** - 响应式状态标识是否正在流式传输

**SSE事件类型处理：**
- `content_delta` - 内容增量，追加到消息文本
- `completed` - 流式完成，携带完整数据
- `error` - 错误事件，显示错误信息
- `[DONE]` - 流式结束标记

**迁移注意事项：**
- 使用AbortController实现流的中止
- 使用TextDecoder逐字节解码响应流
- 按行解析data:前缀的内容
- JSON解析失败时，将原始数据作为纯文本内容
- 组件卸载时必须调用stopStream()避免内存泄漏

---

### **Phase 3: 核心Composable迁移**
**目标**: `useExpertAssistant.ts` - 核心状态管理Composable

#### 3.1 源文件 → 目标文件映射

| 源文件(React) | 目标文件(Vue) | 迁移要点 |
|--------------|--------------|---------|
| `GeneralAssistant/hooks/useGeneralAssistant.ts` (状态逻辑) | `expert/composables/useExpertAssistant.ts` | 核心状态管理 |
| `GeneralAssistant/utils/threadHelpers.ts` | `expert/utils/threadHelpers.ts` | 线程创建锁(已在Phase 1迁移) |

#### 3.2 核心状态定义迁移要点

**文件位置**: `expert/composables/useExpertAssistant.ts`

**需要定义的响应式状态：**
1. **chatMessages** - 聊天消息列表，包含user/assistant角色的消息
2. **chatInput** - 当前输入框内容
3. **chatThreadId** - 当前会话ID
4. **uploadedFiles** - 已上传文件列表
5. **isChatStreaming** - 是否正在流式接收回复
6. **activeReferences** - 当前引用的法规列表（用于工作台显示）
7. **traceSteps** - 调试步骤信息
8. **inputActions** - 输入区域操作按钮状态（深度思考开关等）

#### 3.3 核心方法迁移要点

**需要实现的方法：**
1. **sendChatMessage(message)** - 发送消息
   - 检查/确保threadId存在（使用Phase 1的线程锁）
   - 构造请求体，包含message、agentId、modelConfig
   - 调用useSSEStream开始流式接收
   - 流式内容实时追加到assistant消息
   - 流结束后处理references、title生成

2. **handleFileUpload(files)** - 处理文件上传
   - 确保threadId存在
   - 遍历文件列表，调用API上传
   - 轮询文件处理状态
   - 添加到uploadedFiles列表

3. **removeUploadedFile(fileId)** - 移除已上传文件

4. **loadConversation(threadId)** - 加载历史会话
   - 调用API获取会话消息
   - 解析并设置chatMessages

5. **handleCitationClick(reference)** - 处理引用点击
   - 设置activeReferences供工作台显示
   - 调用fetchPolicy获取法规详情

#### 3.4 生命周期与监听迁移要点

1. **初始加载**: 在onMounted中检查路由参数，如有threadId则加载会话
2. **路由监听**: 监听路由变化，切换会话时重新加载消息
3. **清理**: 在onUnmounted中停止SSE流，避免内存泄漏

#### 3.5 与React Hook的差异注意事项

1. **状态更新**: React使用setState合并更新，Vue直接修改ref.value
2. **依赖追踪**: React需要手动指定依赖数组，Vue自动追踪响应式依赖
3. **副作用**: useEffect需要清理函数，Vue使用独立的onUnmounted
4. **引用稳定性**: useCallback在Vue中不需要，普通函数即可

---

### **Phase 4: UI组件迁移**
**目标**: 核心UI组件 - 工作台、法规预览、聊天内容

#### 4.1 源文件 → 目标文件映射

| 源文件(React) | 目标文件(Vue) | 迁移要点 |
|--------------|--------------|---------|
| `AISpecialistHome/RightPanels/PolicyPreviewPanel.tsx` | `expert/components/PolicyPreviewPanel.vue` | 法规预览与高亮 |
| `AISpecialistHome/RightPanels/TracePanel.tsx` | `expert/components/TracePanel.vue` | 调试面板 |
| `GeneralAssistant/components/unified/workbench/ExpertWorkbench.tsx` | `expert/components/ExpertWorkbench.vue` | 工作台容器 |
| `GeneralAssistant/components/UnifiedEmbeddedContent.tsx` | `expert/components/UnifiedEmbeddedContent.vue` | 核心聊天逻辑 |

#### 4.2 PolicyPreviewPanel 迁移要点

**文件位置**: `expert/components/PolicyPreviewPanel.vue`

**组件职责**: 显示法规原文，支持条款高亮和自动定位

**Props定义:**
- `citation`: ParsedCitation - 引用信息，包含sourceId、anchorId、clause等
- `conversationId`: string - 当前会话ID（用于调试）

**核心功能迁移要点:**
1. **法规获取**:
   - 监听citation变化，调用fetchPolicy获取法规内容
   - 处理加载状态和错误状态

2. **文本高亮逻辑**:
   - 解析citation.clause或citation.description确定高亮目标
   - 使用字符串分割和<mark>标签实现高亮
   - 处理多个高亮区域和嵌套结构

3. **自动滚动定位**:
   - 高亮渲染完成后，使用scrollIntoView将第一个高亮元素滚动到可视区域
   - 添加平滑滚动动画

4. **样式保持**:
   - 保持法规原文的段落结构和缩进
   - 高亮样式与React版本一致（背景色、边框等）

#### 4.3 TracePanel 迁移要点

**文件位置**: `expert/components/TracePanel.vue`

**组件职责**: 显示AI思考过程和调试信息

**Props定义:**
- `steps`: TraceStep[] - 思考步骤列表
- `enabled`: boolean - 是否启用调试模式

**迁移要点:**
1. **步骤展示**: 使用树形结构展示嵌套的思考步骤
2. **状态标识**: 不同状态（进行中、已完成、失败）使用不同图标和颜色
3. **详细信息**: 支持展开查看步骤的详细输出

#### 4.4 ExpertWorkbench 迁移要点

**文件位置**: `expert/components/ExpertWorkbench.vue`

**组件职责**: 工作台容器，管理

## 四、关键映射表

### React → Vue 状态映射

| React (useState) | Vue (ref/reactive) |
|-----------------|-------------------|
| `const [state, setState] = useState(x)` | `const state = ref(x)` |
| `useEffect(() => {...}, [])` | `onMounted(() => {...})` |
| `useEffect(() => {...}, [dep])` | `watch(dep, () => {...})` |
| `useCallback(fn, [])` | 普通函数定义 |
| `useMemo(() => val, [])` | `computed(() => val)` |

### 文件映射汇总

| React源文件 | Vue目标文件 | 优先级 |
|------------|------------|--------|
| `types.ts` | `types/index.ts` | P0 |
| `utils/threadHelpers.ts` | `utils/threadHelpers.ts` | P0 |
| `api/expert.ts` | `api/expert.ts` | P0 |
| `composables/useSSEStream.ts` | `composables/useSSEStream.ts` | P0 |
| `composables/useExpertAssistant.ts` | `composables/useExpertAssistant.ts` | P1 |
| `components/PolicyPreviewPanel.vue` | `components/PolicyPreviewPanel.vue` | P1 |
| `components/TracePanel.vue` | `components/TracePanel.vue` | P1 |
| `components/ExpertWorkbench.vue` | `components/ExpertWorkbench.vue` | P1 |
| `components/UnifiedEmbeddedContent.vue` | `components/UnifiedEmbeddedContent.vue` | P1 |
| `EmbeddedLayout.vue` | `EmbeddedLayout.vue` | P2 |
| `views/index.vue` | `views/index.vue` | P2 |
| `routes.ts` | `routes.ts` | P2 |
| `index.ts` | `index.ts` | P2 |

---

## 五、风险与注意事项

### 高风险项
1. **线程创建锁逻辑**: `threadHelpers.ts`中的并发控制需精确迁移，确保不会重复创建会话
2. **SSE事件解析**: 必须保持与后端一致的事件格式解析逻辑
3. **法规高亮逻辑**: `PolicyPreviewPanel`中的文本高亮和自动滚动定位需精确复现

### 中风险项
1. **引用传递差异**: Vue的响应式对象与React的不可变更新存在差异，需注意状态共享问题
2. **生命周期差异**: React的useEffect与Vue的watch/onMounted行为不同，需仔细映射

### 低风险项
1. **纯样式组件**: 无复杂逻辑的纯展示组件可直接迁移模板
2. **常量配置**: 纯数据配置可直接复制
