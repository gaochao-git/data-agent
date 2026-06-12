<template>
  <div class="embedded-layout w-full h-full">
    <div
      id="embedded-resizable-container"
      class="flex items-stretch gap-0 w-full h-full bg-white dark:bg-gray-900"
    >
      <!-- 对话区域 -->
      <div class="relative flex-1 flex min-w-0 h-full">
        <div
          :class="isDocumentViewerVisible ? 'flex-shrink-0 h-full' : 'flex flex-col h-full'"
          :style="{
            width: isDocumentViewerVisible ? `${conversationWidth}%` : '100%',
            minWidth: '300px'
          }"
        >
          <div class="h-full w-full">
            <EmbeddedConversation
              :analysis-stage="analysisStage"
              :analysis-history="analysisHistory"
              :analysis-tasks="analysisTasks"
              :chat-messages="chatMessages"
              :chat-input="chatInput"
              :set-chat-input="setChatInput"
              :is-chat-streaming="isChatStreaming"
              :chat-streaming-status="chatStreamingStatus"
              :active-assistant-message-id="activeAssistantMessageId"
              :has-five-required-inputs="hasFiveRequiredInputs"
              :missing-required-labels="missingRequiredLabels"
              :inspection-summary="inspectionSummary"
              :analysis-result="analysisResult"
              :register="register"
              :handle-submit="handleSubmit"
              :errors="errors"
              :on-submit="onSubmit"
              :is-analyzing="isAnalyzing"
              :chat-container-ref="chatContainerRef"
              :send-chat-message="sendChatMessage"
              :send-report-request="sendReportRequest"
              :stop-chat-streaming="stopChatStreaming"
              :on-start-new-inspection="handleStartNewInspection"
              :conversation-title="currentConversationTitle"
              :on-law-click="handleLawClickWrapper"
              :on-word-document-view="handleWordDocumentView"
              :on-excel-document-view="handleExcelDocumentView"
              :on-report-click="handleReportClick"
              :on-task-click="handleTaskClick"
              :panel-height-class="panelHeightClass"
              :thread-id="chatThreadId"
              :on-add-chat-message="handleAddChatMessage"
              :on-ensure-thread="createNewThread"
              :reset-trigger="resetTrigger"
              :updated-word-filename="updatedWordFilename"
              :agent-id="agentId"
              :guide="agentGuide"
            />
          </div>
        </div>

        <!-- 文档查看器区域 -->
        <template v-if="isDocumentViewerVisible">
          <!-- 拖动分隔条 -->
          <div
            :class="[
              'flex items-center justify-center cursor-col-resize bg-gray-100 hover:bg-indigo-200 dark:bg-gray-800 dark:hover:bg-indigo-900 transition-colors',
              panelHeightClass,
              isDragging ? 'bg-indigo-300 dark:bg-indigo-800' : ''
            ]"
            style="width: 4px; user-select: none"
            @mousedown="handleDragStart"
          >
            <div class="w-0.5 h-12 bg-gray-400 dark:bg-gray-600 rounded-full"></div>
          </div>

          <!-- 文档查看器 -->
          <div
            class="overflow-hidden flex-shrink-0 h-full"
            :style="{
              width: `${100 - conversationWidth}%`,
              minWidth: '300px'
            }"
          >
            <Console
              :key="consoleKey"
              :document="selectedDocument"
              :word-document="wordDocument"
              :excel-document="excelDocument"
              :case-report="caseReport"
              :review-editor="reviewEditorData"
              @close="handleConsoleClose"
              @regenerate="handleReportRegenerate"
              @review-confirm="handleReviewEditorConfirm"
              @review-cancel="handleReviewEditorCancel"
              @view-law="handleViewLaw"
              @view-attachment="handleViewAttachment"
              :panel-height-class="panelHeightClass"
              :thread-id="chatThreadId || ''"
            />
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useViolationAnalysis } from './composables/useViolationAnalysis'
import EmbeddedConversation from './components/EmbeddedConversation.vue'
import Console from './components/Console.vue'
import type { ChatMessage } from './types'
import { SUB_FUNCTION_CONFIGS, AGENT_ID_TO_REPORT_TYPE } from './constants'

// 组件 Props
interface Props {
  /** 后端 agent_id */
  agentId?: string
  /** 初始线程ID（用于从历史记录打开） */
  initialThreadId?: string
  /** 历史记录刷新回调 */
  onHistoryRefresh?: () => void
  /** 会话ID变化回调 */
  onConversationIdChange?: (conversationId: string) => void
  /** 标题变化回调 */
  onTitleChange?: (title: string) => void
}

const props = withDefaults(defineProps<Props>(), {
  agentId: 'report_jcbg'
})

// 组件事件
const emit = defineEmits<{
  (e: 'history-refresh'): void
  (e: 'conversation-id-change', conversationId: string): void
  (e: 'title-change', title: string): void
}>()

// 可调整面板宽度状态（百分比 - 相对于可用空间）
const conversationWidth = ref(50)
const isDragging = ref(false)

// 面板高度类（嵌入模式使用全高度）
const panelHeightClass = 'h-full'

// 重置输入区域的触发器
const resetTrigger = ref(0)

// 当前会话标题
const currentThreadTitle = ref<string | undefined>(undefined)

// 标记是否已经初始加载过标题
const initialTitleLoadedRef = ref(false)

// 计算属性：动态 agentId
// 事实分析模式使用 fact_analysis agent，报告生成模式使用 report_jcbg agent
const activeAgentId = computed(() => {
  // 根据当前分析阶段和是否有历史记录来判断应该使用哪个 agent
  // 如果有消息历史，说明已经在某个对话中，保持原有的 agent
  // 否则根据分析阶段判断
  if (chatMessages.value.length > 0) {
    // 有历史记录，使用原有的 agent（通过 props 传入）
    return props.agentId
  }
  // 新会话，根据分析阶段判断
  if (analysisStage.value === 'analyzing' || analysisStage.value === 'completed') {
    // 分析阶段，使用 fact_analysis agent（直接输入文本分析）
    return 'fact_analysis'
  }
  // 默认使用 report_jcbg agent（上传文件生成报告）
  return props.agentId
})

// 初始化 useViolationAnalysis
const {
  // State
  analysisResult,
  isAnalyzing,
  taskStatus,
  analysisTasks,
  chatMessages,
  chatInput,
  setChatInput,
  isChatStreaming,
  chatStreamingStatus,
  activeAssistantMessageId,
  analysisStage,
  analysisHistory,
  activeHistoryId,
  setActiveHistoryId,
  missingRequiredLabels,
  hasFiveRequiredInputs,
  violationDescriptionValue,
  activeHistory,
  selectedDocument,
  setSelectedDocument,
  chatThreadId,
  historyRefreshTrigger,

  // Form
  register,
  handleSubmit,
  errors,
  onSubmit,

  // Refs
  chatContainerRef,

  // Functions
  sendChatMessage,
  sendReportRequest,
  stopChatStreaming,
  resetChatHistory,
  createNewThread,
  setAnalysisResult,
  setAnalysisStage,
  setTaskStatus,
  handleLawClick,
  switchToThread,
  loadThreadMessages,
  handleHumanReviewApprove,
  handleHumanReviewEdit,
  handleHumanReviewReject,
  resumeReportGeneration,
} = useViolationAnalysis({ agentId: props.agentId })

// Word 文档预览状态
const wordDocument = ref<{
  filename: string
  title: string
  sourceData?: {
    report_type: string
    inspection_content?: string
    violation_facts?: string[]
    legal_basis?: string[]
    penalty_clauses?: string[]
  }
} | null>(null)

// Excel 文档预览状态
const excelDocument = ref<{ filename: string; title: string; excelData: any } | null>(null)

// 相似案例报告预览状态
const caseReport = ref<{ name: string; content: string } | null>(null)

// 更新后的 Word 文件名
const updatedWordFilename = ref<string | undefined>(undefined)

// 审核编辑状态
const reviewEditorData = ref<{
  taskId: string
  messageId: string
  reviewData: {
    review_type: 'split' | 'retrieve' | 'extract' | 'analyze' | 'evidence'
    message: string
    current_data: any
  }
} | null>(null)

// 计算属性：当选择了文档或审核编辑时显示右边面板
const isDocumentViewerVisible = computed(() => {
  return selectedDocument.value !== null ||
    wordDocument.value !== null ||
    excelDocument.value !== null ||
    caseReport.value !== null ||
    reviewEditorData.value !== null
})

// 计算属性：当前会话标题
const currentConversationTitle = computed(() => {
  if (activeHistory.value?.title) {
    return activeHistory.value.title
  }
  return currentThreadTitle.value
})

// 计算属性：当前 agentId 对应的配置
const agentGuide = computed(() => {
  const config = SUB_FUNCTION_CONFIGS[props.agentId]
  if (!config) {
    // 默认配置
    return {
      welcomeMessage: '您好！我可以帮您进行执法检查相关的分析和报告撰写。',
      description: '智能执法检查助手',
      uploadRequirements: [],
    }
  }
  return {
    welcomeMessage: config.welcomeMessage,
    description: config.description,
    uploadRequirements: config.uploadRequirements,
    name: config.name,
    icon: config.icon,
    color: config.color,
  }
})

// 计算属性：Console 组件的 key
const consoleKey = computed(() => {
  return reviewEditorData.value?.taskId ||
    caseReport.value?.name ||
    wordDocument.value?.filename ||
    excelDocument.value?.filename ||
    selectedDocument.value?.law_item_name ||
    'default'
})

// 计算属性：检查摘要
const inspectionSummary = computed(() => {
  if (activeHistory.value) {
    return {
      inspectionContent: activeHistory.value.inspectionContent,
      violationDescription: activeHistory.value.violationDescription,
    }
  }
  return {
    inspectionContent: '',
    violationDescription: violationDescriptionValue.value,
  }
})

// 处理拖动开始
const handleDragStart = (e: MouseEvent) => {
  e.preventDefault()
  isDragging.value = true
}

// 处理拖动
const handleDrag = (e: MouseEvent) => {
  if (!isDragging.value) return

  const container = document.getElementById('embedded-resizable-container')
  if (!container) return

  const containerRect = container.getBoundingClientRect()
  const availableWidth = containerRect.width
  const mouseX = e.clientX - containerRect.left

  // 计算新的宽度百分比（限制在 30% - 70% 之间）
  let newWidth = (mouseX / availableWidth) * 100
  newWidth = Math.max(30, Math.min(70, newWidth))

  conversationWidth.value = newWidth
}

// 处理拖动结束
const handleDragEnd = () => {
  isDragging.value = false
}

// 处理法律条款预览
const handleLawClickWrapper = (law: { law_name: string; law_item_name: string; law_item_content: string }) => {
  handleLawClick(law)
  wordDocument.value = null
  excelDocument.value = null
  caseReport.value = null
}

// 处理 Word 文档预览
const handleWordDocumentView = (doc: { filename: string; title: string; sourceData?: any }) => {
  console.log('[EmbeddedLayout] handleWordDocumentView called', doc)
  wordDocument.value = doc
  setSelectedDocument(null)
  excelDocument.value = null
  caseReport.value = null
}

// 处理 Excel 文档预览
const handleExcelDocumentView = (doc: { filename: string; title: string; excelData: any }) => {
  console.log('[EmbeddedLayout] handleExcelDocumentView called', doc)
  excelDocument.value = doc
  setSelectedDocument(null)
  wordDocument.value = null
  caseReport.value = null
  reviewEditorData.value = null
}

// 处理相似案例报告预览
const handleReportClick = (report: { name: string; content: string }) => {
  caseReport.value = report
  setSelectedDocument(null)
  wordDocument.value = null
  excelDocument.value = null
}

// 处理打开审核编辑器
const handleOpenReviewEditor = (taskId: string, messageId: string, reviewData: { review_type: string; message: string; current_data: any }) => {
  reviewEditorData.value = { taskId, messageId, reviewData: reviewData as any }
  setSelectedDocument(null)
  wordDocument.value = null
  excelDocument.value = null
  caseReport.value = null
}

// 处理任务点击 - 当用户点击待确认任务时打开审核编辑器
const handleTaskClick = (task: any) => {
  if (task.status === 'awaiting_confirm' && task.reviewData) {
    // 查找包含此任务的消息 ID
    const messageWithTask = chatMessages.value.find((msg: ChatMessage) => {
      if (!Array.isArray(msg.content)) return false
      const taskBlock = msg.content.find(
        (b: any) => b.type === 'non_standard' && b.value?.type === 'task_progress'
      )
      if (!taskBlock) return false
      const tasks = (taskBlock as any).value?.data || []
      return tasks.some((t: any) => t.id === task.id && t.status === 'awaiting_confirm')
    })

    if (messageWithTask) {
      handleOpenReviewEditor(task.id, messageWithTask.id, task.reviewData)
    }
  }
}

// 处理报告重新生成
const handleReportRegenerate = (filename: string, sourceData: any) => {
  wordDocument.value = { filename, title: wordDocument.value?.title || '检查报告', sourceData }
  updatedWordFilename.value = filename
}

// 处理 Console 关闭
const handleConsoleClose = () => {
  setSelectedDocument(null)
  wordDocument.value = null
  excelDocument.value = null
  caseReport.value = null
  reviewEditorData.value = null
}

// 处理审核编辑确认
const handleReviewEditorConfirm = (data: any, isEdited: boolean) => {
  if (!reviewEditorData.value) return

  const { taskId, messageId } = reviewEditorData.value

  if (isEdited) {
    resumeReportGeneration({ action: 'edit', data }, taskId, messageId, createAutoPreviewHandler())
  } else {
    resumeReportGeneration({ action: 'approve' }, taskId, messageId, createAutoPreviewHandler())
  }

  reviewEditorData.value = null
}

// 处理审核编辑取消
const handleReviewEditorCancel = () => {
  if (!reviewEditorData.value) return

  const { taskId, messageId } = reviewEditorData.value
  resumeReportGeneration({ action: 'reject' }, taskId, messageId)
  reviewEditorData.value = null
}

// 处理查看法规
const handleViewLaw = (lawName: string, lawItemName: string) => {
  handleLawClick({ law_name: lawName, law_item_name: lawItemName, law_item_content: '' })
}

// 处理查看附件
const handleViewAttachment = (file: any) => {
  // 查看附件的逻辑可以扩展
  console.log('查看附件:', file)
}

// 自动预览处理函数
const createAutoPreviewHandler = () => {
  return (doc: { filename: string; title: string; excelData?: any; sourceData?: any }) => {
    const isExcel = doc.filename.endsWith('.xlsx') || doc.filename.endsWith('.xls')
    if (isExcel && doc.excelData) {
      handleExcelDocumentView({ filename: doc.filename, title: doc.title, excelData: doc.excelData })
    } else {
      handleWordDocumentView({ filename: doc.filename, title: doc.title, sourceData: doc.sourceData })
    }
  }
}

// 新建检查
const handleStartNewInspection = () => {
  setAnalysisStage('initial')
  setAnalysisResult(null)
  setTaskStatus(null)
  setActiveHistoryId(null)
  resetChatHistory()
  currentThreadTitle.value = undefined
  initialTitleLoadedRef.value = false
  setSelectedDocument(null)
  wordDocument.value = null
  excelDocument.value = null
  caseReport.value = null
  updatedWordFilename.value = undefined
  resetTrigger.value++
}

// 查找包含指定任务的消息
const findMessageWithTask = (taskId: string): ChatMessage | undefined => {
  return chatMessages.value.find((msg: ChatMessage) => {
    if (!Array.isArray(msg.content)) return false
    const taskBlock = msg.content.find(
      (b: any) => b.type === 'non_standard' && b.value?.type === 'task_progress'
    )
    if (!taskBlock) return false
    const tasks = (taskBlock as any).value?.data || []
    return tasks.some((t: any) => t.id === taskId && t.status === 'awaiting_confirm')
  })
}

// 报告的审核任务ID列表
const REPORT_REVIEW_TASK_IDS = ['review_extract', 'review_analyze', 'review_retrieve', 'review_evidence']

// 包装的审核回调
const handleReviewApproveWrapper = (taskId: string) => {
  if (REPORT_REVIEW_TASK_IDS.includes(taskId)) {
    const messageWithTask = findMessageWithTask(taskId)
    if (messageWithTask) {
      resumeReportGeneration({ action: 'approve' }, taskId, messageWithTask.id, createAutoPreviewHandler())
    }
  } else {
    handleHumanReviewApprove(taskId)
  }
}

const handleReviewEditWrapper = (taskId: string, data: any) => {
  if (REPORT_REVIEW_TASK_IDS.includes(taskId)) {
    const messageWithTask = findMessageWithTask(taskId)
    if (messageWithTask) {
      resumeReportGeneration({ action: 'edit', data }, taskId, messageWithTask.id, createAutoPreviewHandler())
    }
  } else {
    handleHumanReviewEdit(taskId, data)
  }
}

const handleReviewRejectWrapper = (taskId: string) => {
  if (REPORT_REVIEW_TASK_IDS.includes(taskId)) {
    const messageWithTask = findMessageWithTask(taskId)
    if (messageWithTask) {
      resumeReportGeneration({ action: 'reject' }, taskId, messageWithTask.id)
    }
  } else {
    handleHumanReviewReject(taskId)
  }
}

// 监听拖动事件
const setupDragListeners = () => {
  if (isDragging.value) {
    document.addEventListener('mousemove', handleDrag)
    document.addEventListener('mouseup', handleDragEnd)
  }
}

const removeDragListeners = () => {
  document.removeEventListener('mousemove', handleDrag)
  document.removeEventListener('mouseup', handleDragEnd)
}

// 监听线程ID变化
watch(chatThreadId, (newThreadId) => {
  if (newThreadId) {
    emit('conversation-id-change', newThreadId)
  }
})

// 监听历史记录刷新触发器
watch(historyRefreshTrigger, () => {
  emit('history-refresh')
})

// 监听活跃历史记录标题变化
watch(
  () => activeHistory.value?.title,
  (newTitle) => {
    if (newTitle) {
      emit('title-change', newTitle)
    }
  }
)

// 监听拖动状态
watch(isDragging, (newVal) => {
  if (newVal) {
    setupDragListeners()
  } else {
    removeDragListeners()
  }
})

// 自动展开审核编辑器 - 当 chatMessages 中有待确认的任务时自动打开
watch([chatMessages, reviewEditorData], () => {
  if (reviewEditorData.value) return

  for (const msg of chatMessages.value) {
    if (msg.role !== 'assistant' || !Array.isArray(msg.content)) continue

    const taskBlock = msg.content.find(
      (b: any) => b.type === 'non_standard' && b.value?.type === 'task_progress'
    )
    if (!taskBlock) continue

    const tasks = (taskBlock as any).value?.data || []
    const awaitingTask = tasks.find((t: any) => t.status === 'awaiting_confirm' && t.reviewData)

    if (awaitingTask) {
      handleOpenReviewEditor(awaitingTask.id, msg.id, awaitingTask.reviewData)
      break
    }
  }
})

// 监听初始线程ID变化
watch(
  () => props.initialThreadId,
  (newThreadId) => {
    if (newThreadId && newThreadId !== chatThreadId.value) {
      loadThreadMessages(newThreadId)
    }
  },
  { immediate: true }
)

// 获取会话标题
onMounted(async () => {
  if (!chatThreadId.value) return

  try {
    const response = await fetch('/api/conversations', {
      credentials: 'include'
    })

    if (response.ok) {
      const data = await response.json()
      const conversations = data.data || []
      const currentConversation = conversations.find((c: any) => c.id === chatThreadId.value)
      if (currentConversation?.title !== undefined) {
        currentThreadTitle.value = currentConversation.title
      }
      initialTitleLoadedRef.value = true
    }
  } catch (error) {
    console.error('获取会话标题失败:', error)
  }
})

// 清理工作
onUnmounted(() => {
  removeDragListeners()
  stopChatStreaming()
})
</script>

<style scoped>
.embedded-layout {
  width: 100%;
  height: 100%;
}
</style>
