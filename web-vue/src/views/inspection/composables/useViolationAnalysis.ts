/**
 * 违法行为分析 Composable
 *
 * 迁移自 React: useViolationAnalysis hook
 */

import { ref, reactive, computed, watch, onMounted } from 'vue'
import { useForm } from '@/utils/useForm'
import { ElMessage } from 'element-plus'
import { violationFormSchema, createInitialChatMessages, requiredLabelsMap, REPORT_TYPE_MAP } from '../constants'
import {
  createMessageId,
  ensureThreadId,
  appendMessageContent,
  setMessageError
} from '../utils'
import { processSSEStream } from './useSSEStream'
import type {
  AnalysisResult,
  SSETaskStatus,
  ChatMessage,
  AnalysisStage,
  AnalysisHistoryItem,
  ViolationFormValues,
  AnalysisTask,
  HumanReviewData,
  ResumeInput,
} from '../types'
import { getToken } from '@/utils/auth'

// 使用 /agents 前缀代理到 AI Agent 后端 (5001端口)
const API_BASE = import.meta.env.VITE_APP_BASE_API || ''

// 获取带认证的 fetch headers
function getAuthHeaders(): HeadersInit {
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
  }
  const token = getToken()
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  return headers
}

interface UseViolationAnalysisOptions {
  /** 后端 agent_id */
  agentId?: string
}

// 默认 agent_id
const DEFAULT_AGENT_ID = 'report_jcbg'

export function useViolationAnalysis(options: UseViolationAnalysisOptions = {}) {
  const { agentId = DEFAULT_AGENT_ID } = options

  // State (using ref for reactive state)
  const analysisResult = ref<AnalysisResult | null>(null)
  const isAnalyzing = ref(false)
  const taskStatus = ref<SSETaskStatus | null>(null)
  const chatMessages = ref<ChatMessage[]>(createInitialChatMessages())
  const chatInput = ref('')
  const isChatStreaming = ref(false)
  const chatStreamingStatus = ref('')
  const activeAssistantMessageId = ref<string | null>(null)
  const currentChatTaskId = ref('')
  const chatThreadId = ref('')
  const analysisStage = ref<AnalysisStage>('initial')
  const analysisHistory = ref<AnalysisHistoryItem[]>([])
  const activeHistoryId = ref<string | null>(null)
  const isHistoryCollapsed = ref(false)
  const analysisTasks = ref<AnalysisTask[]>([])
  const selectedDocument = ref<{
    law_name: string
    law_item_name: string
    law_item_content: string
  } | null>(null)
  const historyRefreshTrigger = ref(0) // 历史记录刷新触发器

  // Human-in-the-Loop: 当前待审核的任务ID（用于追踪哪个任务处于待确认状态）
  const pendingReviewTaskIdRef = ref<string | null>(null)

  const chatContainerRef = ref<HTMLDivElement | null>(null)
  const chatAbortControllerRef = ref<AbortController | null>(null)
  const analysisAbortControllerRef = ref<AbortController | null>(null) // 分析流的 AbortController
  const chatTaskIdRef = ref<string>('')
  const manualStopRef = ref(false)
  const lastProgressMessageRef = ref<string>('')
  const activeHistoryIdRef = ref<string | null>(null)
  const tasksMessageIdRef = ref<string | null>(null)
  const loadedThreadIdRef = ref<string | null>(null) // 记录已加载历史消息的 thread_id

  // Sync activeHistoryIdRef with activeHistoryId
  watch(activeHistoryId, (newVal) => {
    activeHistoryIdRef.value = newVal
  })

  // 加载指定会话的历史消息
  const loadThreadMessages = async (threadId: string) => {
    if (!threadId || loadedThreadIdRef.value === threadId) {
      return
    }

    try {
      const response = await fetch(`${API_BASE}/api/violation/conversations/${threadId}`, {
        credentials: 'include',
        headers: getAuthHeaders(),
      })

      if (!response.ok) {
        console.error('[加载会话] 加载会话失败:', response.status)
        return
      }

      const data = await response.json()
      const messages = data.data.messages
      const interruptInfo = data.data.interrupt
      const persistedTaskProgress = data.data.task_progress
      let lastTaskProgressIndex = -1
      for (let i = messages.length - 1; i >= 0; i--) {
        const msg = messages[i]
        if (msg.role === 'assistant' && Array.isArray(msg.content)) {
          const hasTaskProgress = msg.content.some(
            (b: any) => b.type === 'non_standard' && b.value?.type === 'task_progress'
          )
          if (hasTaskProgress) {
            lastTaskProgressIndex = i
            break
          }
        }
      }

      const formattedMessages: ChatMessage[] = []

      messages.forEach((msg: any, index: number) => {
        if (msg.role === 'assistant' && Array.isArray(msg.content)) {
          const hasTaskProgress = msg.content.some(
            (b: any) => b.type === 'non_standard' && b.value?.type === 'task_progress'
          )
          if (hasTaskProgress && index !== lastTaskProgressIndex) {
            return
          }
        }

        let content = msg.content

        if (Array.isArray(content)) {
          const hasFile = content.some((b: any) => b.type === 'file')
          if (hasFile) {
            content = content.map((block: any) => {
              if (block.type === 'non_standard' && block.value?.type === 'task_progress') {
                return {
                  ...block,
                  value: {
                    ...block.value,
                    data: (block.value.data || []).map((task: AnalysisTask) => ({
                      ...task,
                      status: 'completed' as const
                    }))
                  }
                }
              }
              return block
            })
          }
        }

        formattedMessages.push({
          id: `history-${threadId}-${index}`,
          role: msg.role,
          content: content,
          timestamp: new Date(),
          additional_kwargs: msg.additional_kwargs,
        })
      })

      console.log('[加载会话] 原始消息:', messages.length, '过滤后:', formattedMessages.length)

      if (interruptInfo && interruptInfo.interrupt_data) {
        const reviewData = interruptInfo.interrupt_data
        const reviewType = reviewData.review_type

        const reviewTypeToTaskId: Record<string, string> = {
          'split': 'review_split',
          'retrieve': 'review_retrieve',
          'extract': 'review_extract',
          'analyze': 'review_analyze',
          'evidence': 'review_evidence',
        }
        const taskIdToUpdate = reviewTypeToTaskId[reviewType]

        console.log('[加载会话] 检测到中断状态:', reviewType, '任务ID:', taskIdToUpdate)

        const lastAssistantIndex = formattedMessages.findLastIndex(msg => msg.role === 'assistant')
        if (lastAssistantIndex >= 0 && taskIdToUpdate) {
          const lastMsg = formattedMessages[lastAssistantIndex]
          let newContent = Array.isArray(lastMsg.content) ? [...lastMsg.content] : []

          const taskBlockIndex = newContent.findIndex(
            (b: any) => b.type === 'non_standard' && b.value?.type === 'task_progress'
          )

          let baseTasks: any[] = []
          if (persistedTaskProgress && persistedTaskProgress.length > 0) {
            baseTasks = persistedTaskProgress
          } else if (taskBlockIndex >= 0) {
            baseTasks = (newContent[taskBlockIndex] as any).value?.data || []
          }

          const updatedTasks = baseTasks.map((t: any) => {
            if (t.id === taskIdToUpdate) {
              return {
                ...t,
                status: 'awaiting_confirm',
                reviewData: reviewData,
          }
            }
            return t
          })

          const taskBlock = {
            type: 'non_standard',
            value: {
              type: 'task_progress',
              data: updatedTasks,
            },
          }

          if (taskBlockIndex >= 0) {
            newContent[taskBlockIndex] = taskBlock
          } else {
            newContent.push(taskBlock)
          }

          formattedMessages[lastAssistantIndex] = {
            ...lastMsg,
            content: newContent,
          }
        }
      }

      chatMessages.value = formattedMessages
      chatThreadId.value = threadId
      loadedThreadIdRef.value = threadId
    } catch (error) {
      console.error('[加载会话] 加载会话消息失败:', error)
    }
  }

  const missingRequiredLabels = computed(() => {
    const r = analysisResult.value
    if (!r || !r.违法行为分析 || r.违法行为分析.length === 0) {
      return Object.values(requiredLabelsMap)
    }

    const firstViolation = r.违法行为分析[0]
    if (!firstViolation) {
      return Object.values(requiredLabelsMap)
    }

    const fieldChecks = [
      { field: firstViolation.违反条款, label: requiredLabelsMap.legalBasis },
      { field: firstViolation.处罚条款, label: requiredLabelsMap.penaltyClauseList },
      { field: firstViolation.整改措施, label: requiredLabelsMap.rectificationMeasures },
      { field: firstViolation.裁量基准, label: requiredLabelsMap.adminPenaltyStandards },
      { field: firstViolation.处罚建议, label: requiredLabelsMap.penaltySuggestions },
    ]

    return fieldChecks
      .filter(({ field }) => !Array.isArray(field) || field.length === 0)
      .map(({ label }) => label)
  })

  const hasFiveRequiredInputs = computed(
    () => missingRequiredLabels.value.length === 0
  )

  // Form handling (simulating react-hook-form)
  const {
    register: formRegister,
    handleSubmit: formHandleSubmit,
    errors: formErrors,
    reset: formReset,
    getValues: formGetValues,
    watch: formWatch,
  } = useForm<ViolationFormValues>({
    resolver: violationFormSchema,
  })

  const register = formRegister
  const errors = formErrors
  const reset = formReset

  const handleSubmit = (callback: (data: ViolationFormValues) => void | Promise<void>) => {
    return formHandleSubmit(callback)
  }

  const getValues = () => ({
    violationDescription: chatInput.value
  })

  const violationDescriptionValue = computed(() => chatInput.value)

  const activeHistory = computed(
    () => analysisHistory.value.find((item) => item.id === activeHistoryId.value) ?? null
  )

  const updateHistoryItem = (
    historyId: string,
    updater: (item: AnalysisHistoryItem) => AnalysisHistoryItem
  ) => {
    analysisHistory.value = analysisHistory.value.map((item) =>
      item.id === historyId ? updater(item) : item
    )
  }

  // 跟踪已生成标题的线程 ID，确保每个线程只生成一次标题
  const generatedTitlesRef = ref<Set<string>>(new Set())

  const generateAndUpdateTitle = async (threadId: string) => {
    // 检查是否已经为这个线程生成过标题
    if (generatedTitlesRef.value.has(threadId)) {
      return
    }

    // 标记为正在生成
    generatedTitlesRef.value.add(threadId)

    try {
      const response = await fetch(`${API_BASE}/api/conversations/${threadId}/title`, {
        method: 'POST',
        headers: getAuthHeaders(),
        credentials: 'include',
      })

      if (!response.ok) {
        throw new Error(`生成标题失败: ${response.status}`)
      }

      const data = await response.json()
      // business_response 格式：{ status, msg, code, data: { title } }
      const title = data.data.title

      // 更新当前 activeHistoryId 对应的历史记录
      if (activeHistoryIdRef.value) {
        updateHistoryItem(activeHistoryIdRef.value, (item) => ({
          ...item,
          title: title,
        }))
      }
      // 触发历史记录刷新
      historyRefreshTrigger.value++
    } catch (error) {
      console.warn('[标题生成] 失败:', error)
      // 失败时从 Set 中移除，允许重试
      generatedTitlesRef.value.delete(threadId)
    }
  }

  const appendAnalysisMessage = (tasks: AnalysisTask[]) => {
    const messageId = createMessageId()
    tasksMessageIdRef.value = messageId
    chatMessages.value = [
      ...chatMessages.value,
      {
        id: messageId,
        role: 'assistant',
        content: [
          // 使用 NonStandardContentBlock 存放任务进度
          {
            type: 'non_standard',
            value: { type: 'task_progress', data: tasks }
          }
        ],
        timestamp: new Date(),
      },
    ]
  }

  const updateAnalysisMessage = (
    updates: {
      tasks?: AnalysisTask[]
      analysisResult?: AnalysisResult | null
      isCompleted?: boolean
      content?: string
    }
  ) => {
    const messageId = tasksMessageIdRef.value
    if (!messageId) {
      console.warn('[updateAnalysisMessage] 跳过更新，messageId为空')
      return
    }

    chatMessages.value = chatMessages.value.map((msg) => {
      if (msg.id === messageId) {
        let newContent = Array.isArray(msg.content) ? [...msg.content] : []

        // 更新任务进度（NonStandardContentBlock）
        if (updates.tasks !== undefined) {
          const taskBlockIndex = newContent.findIndex(
            (b: any) => b.type === 'non_standard' && b.value?.type === 'task_progress'
          )
          const taskBlock = {
            type: 'non_standard',
            value: { type: 'task_progress', data: updates.tasks }
          }
          if (taskBlockIndex >= 0) {
            newContent[taskBlockIndex] = taskBlock
          } else {
            newContent.push(taskBlock)
          }
        }

        // 更新分析数据（NonStandardContentBlock）
        if (updates.analysisResult !== undefined) {
          const dataBlockIndex = newContent.findIndex(
            (b: any) => b.type === 'non_standard' && b.value?.type === 'analysis_data'
          )
          const dataBlock = {
            type: 'non_standard',
            value: { type: 'analysis_data', data: updates.analysisResult }
          }
          if (dataBlockIndex >= 0) {
            newContent[dataBlockIndex] = dataBlock
          } else if (updates.analysisResult) {
            newContent.push(dataBlock)
          }
        }

        // 更新文本内容（TextContentBlock）
        if (updates.content !== undefined) {
          const textBlockIndex = newContent.findIndex(
            (b: any) => b.type === 'text'
          )
          const textBlock = { type: 'text', text: updates.content }
          if (textBlockIndex >= 0) {
            newContent[textBlockIndex] = textBlock
          } else {
            newContent.unshift(textBlock)  // 文本放在最前面
          }
        }

        return {
          ...msg,
          content: newContent
        }
      }
      return msg
    })
  }

  // 当任务状态更新时，同步更新分析消息中的任务
  watch(analysisTasks, (newTasks) => {
    if (newTasks.length > 0 && tasksMessageIdRef.value) {
      updateAnalysisMessage({ tasks: newTasks })
    }
  })

  const pushProgressMessageFromEvent = (eventData: any) => {
    // 处理 NonStandardContentBlock 格式的任务进度
    if (eventData?.value?.type === 'task_progress') {
      // 直接返回，不添加文本消息，任务列表由 TasksMessage 组件单独渲染
      return
    }

    const candidates = [
      typeof eventData?.message === 'string' ? eventData.message.trim() : '',
      typeof eventData?.current_node === 'string'
        ? eventData.current_node.trim()
        : '',
      typeof eventData?.status_text === 'string'
        ? eventData.status_text.trim()
        : '',
      typeof eventData?.title === 'string' ? eventData.title.trim() : '',
      typeof eventData?.data?.message === 'string'
        ? eventData.data.message.trim()
        : '',
      typeof eventData?.data?.current_node === 'string'
        ? eventData.data.current_node.trim()
        : '',
      typeof eventData?.data?.title === 'string'
        ? eventData.data.title.trim()
        : '',
    ]

    let statusText = candidates.find((text) => text.length > 0) || ''

    if (!statusText && eventData?.status === 'completed') {
      statusText = '智能分析完成'
    }

    if (
      !statusText &&
      eventData?.status === 'error' &&
      typeof eventData?.error === 'string'
    ) {
      statusText = `分析失败：${eventData.error}`
    }

    if (!statusText) {
      return
    }

    const formattedMessage = statusText

    // 不要重复相同的消息
    if (lastProgressMessageRef.value === formattedMessage) {
      return
    }

    lastProgressMessageRef.value = formattedMessage

    // 根据消息内容更新任务状态
    if (formattedMessage.includes('开始分析') || formattedMessage.includes('分析事实认定')) {
      analysisTasks.value = analysisTasks.value.map((task) =>
        task.id === 'task-1' ? { ...task, status: 'in_progress' } : task
      )
    } else if (formattedMessage.includes('拆分完成') || formattedMessage.includes('识别出')) {
      // 拆分完成 = task-1 完成
      analysisTasks.value = analysisTasks.value.map((task) =>
        task.id === 'task-1' ? { ...task, status: 'completed' } : task
      )
    } else if (formattedMessage.includes('检索法律法规')) {
      analysisTasks.value = analysisTasks.value.map((task) =>
        task.id === 'task-1'
          ? { ...task, status: 'completed' }
          : task.id === 'task-2'
            ? { ...task, status: 'in_progress' }
            : task
      )
    } else if (formattedMessage.includes('格式化输出') || formattedMessage.includes('整理检索')) {
      analysisTasks.value = analysisTasks.value.map((task) =>
        task.id === 'task-2'
          ? { ...task, status: 'completed' }
          : task.id === 'task-3'
            ? { ...task, status: 'in_progress' }
            : task
      )
    } else if (formattedMessage.includes('生成分析报告') || formattedMessage.includes('分析完成')) {
      analysisTasks.value = analysisTasks.value.map((task) =>
        task.id === 'task-2'
          ? { ...task, status: 'completed' }
          : task.id === 'task-3'
            ? { ...task, status: 'completed' }
            : task
      )
    }
  }

  const resetChatHistory = () => {
    chatAbortControllerRef.value?.abort()
    chatAbortControllerRef.value = null
    chatMessages.value = createInitialChatMessages()
    chatThreadId.value = ''
    isChatStreaming.value = false
    chatStreamingStatus.value = ''
    chatInput.value = ''
    activeAssistantMessageId.value = null
    currentChatTaskId.value = ''
    chatTaskIdRef.value = ''
    analysisTasks.value = []
    loadedThreadIdRef.value = null // 清空已加载的 thread_id 标记
  }

  // 使用传入的 agentId（已在参数解构时设置默认值）

  /**
   * 创建新线程（符合 LangGraph 官方最佳实践）
   * 在新建会话时立即调用，预先创建 thread_id
   */
  const createNewThread = async (): Promise<string | null> => {
    try {
      const response = await fetch(`${API_BASE}/api/conversations`, {
        method: 'POST',
        headers: getAuthHeaders(),
        credentials: 'include',
        body: JSON.stringify({
          agent_id: agentId,
        }),
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(errorData.msg || errorData.error || '对话创建失败')
      }

      const data = await response.json()
      console.log('[线程创建] 响应数据:', data)
      // business_response 格式：{ status, msg, code, data: { id, created_at } }
      if (!data.data || !data.data.id) {
        throw new Error(`响应格式错误: ${JSON.stringify(data)}`)
      }
      const newThreadId = data.data.id
      // 立即保存到状态
      chatThreadId.value = newThreadId

      // 标记该 thread_id 已加载,避免 URL 变化时重复加载历史
      loadedThreadIdRef.value = newThreadId

      return newThreadId
    } catch (error) {
      console.error('[线程创建] 失败:', error)
      ElMessage.error({
        message: '线程创建失败',
        description: error instanceof Error ? error.message : '无法创建会话线程，请重试',
      })
      return null
    }
  }

  const callViolationAnalysisSSE = async (
    data: ViolationFormValues,
    historyId: string,
    threadId: string,
    retryCount = 0,
  ): Promise<void> => {
    const maxRetries = 3

    // 不再提前初始化任务，等后端返回 task_progress 时再渲染
    // 任务卡片由 onTask 事件驱动创建

    // 重置消息 ID ref，确保新分析会创建新消息
    tasksMessageIdRef.value = null
    // 立即更新 activeHistoryIdRef，因为 watch 是异步的
    activeHistoryIdRef.value = historyId
    manualStopRef.value = false
    isAnalyzing.value = true
    isChatStreaming.value = true
    if (activeHistoryIdRef.value === historyId) {
      analysisStage.value = 'analyzing'
    }
    taskStatus.value = {
      taskId: historyId,
      status: 'pending',
      progress: 0,
      currentStep:
        retryCount > 0
          ? `重试中 (${retryCount}/${maxRetries})...`
          : '准备分析...',
    }

    lastProgressMessageRef.value = ''
    updateHistoryItem(historyId, (item) => ({
      ...item,
      status: 'analyzing',
      progress: 0,
      error: undefined,
      updatedAt: new Date(),
    }))

    const controller = new AbortController()
    analysisAbortControllerRef.value = controller

    try {
      await processSSEStream({
        url: `${API_BASE}/api/conversations/${threadId}/completion`,
        method: 'POST',
        body: {
          query: data.violationDescription,
          agent_id: agentId,
          options: { task_type: 'violation_analysis' },
        },
        signal: controller.signal,
        handlers: {
          onMessage: (content) => {
            // 收到文本消息，说明后端走的是 QA 流程（意图识别判断不是分析请求）
            // 创建或追加普通文本消息
            if (!tasksMessageIdRef.value) {
              // 还没有消息，创建一个新的助手消息
              const newMessageId = createMessageId()
              tasksMessageIdRef.value = newMessageId
              chatMessages.value = [
                ...chatMessages.value,
                {
                  id: newMessageId,
                  role: 'assistant' as const,
                  content: content,
                  timestamp: new Date(),
                }
              ]
            } else {
              // 已有消息，追加内容
              chatMessages.value = chatMessages.value.map((msg) =>
                msg.id === tasksMessageIdRef.value
                  ? { ...msg, content: (typeof msg.content === 'string' ? msg.content : '') + content }
                  : msg
              )
            }
            // 清除任务/分析状态，切换到 QA 模式
            analysisTasks.value = []
            isAnalyzing.value = false
            analysisStage.value = 'initial'
            taskStatus.value = null
          },
          onTask: (tasks) => {
            if (tasks) {
          if (!tasksMessageIdRef.value) {
                appendAnalysisMessage(tasks)
              }
              analysisTasks.value = tasks
            }
          },
          onCompleted: (eventData) => {
            console.log('[onCompleted] 收到完成事件:', eventData)

            // 从 completed 事件中提取 message
            // 后端返回格式：{ messages: [{...}] } 或 { message: {...} }
            const message = eventData?.messages?.[0] || eventData?.message
            console.log('[onCompleted] 提取的 message:', message)

            // 从 content 数组中的 NonStandardContentBlock 提取 analysis_data
            let analysisData: AnalysisResult | null = null
            if (message && Array.isArray(message.content)) {
              console.log('[onCompleted] message.content 是数组，长度:', message.content.length)
              console.log('[onCompleted] message.content 内容:', message.content)

              // 尝试多种方式提取 analysis_data
              // 方式1: 非标准块
              const dataBlock = message.content.find(
                (b: any) => b.type === 'non_standard' && b.value?.type === 'analysis_data'
              )
              if (dataBlock) {
                console.log('[onCompleted] 找到 analysis_data 块:', dataBlock)
                analysisData = dataBlock.value?.data || null
              }

              // 方式2: 直接查找 analysis_data 类型
              if (!analysisData) {
                const directBlock = message.content.find((b: any) => b.type === 'analysis_data')
                if (directBlock) {
                  console.log('[onCompleted] 找到直接 analysis_data 块:', directBlock)
                  analysisData = (directBlock as any).data || null
                }
              }

              // 方式3: 检查所有非标准块
              if (!analysisData) {
                const nonStandardBlocks = message.content.filter((b: any) => b.type === 'non_standard')
                console.log('[onCompleted] 所有非标准块:', nonStandardBlocks)
              }
            }

            console.log('[onCompleted] 最终提取的 analysisData:', analysisData)

            // 总是生成标题（无论是分析还是QA流程）
            void generateAndUpdateTitle(threadId)

            if (analysisData) {
              updateHistoryItem(historyId, (item) => ({
                ...item,
                status: 'completed',
                result: analysisData,
                updatedAt: new Date(),
              }))

              if (activeHistoryIdRef.value === historyId) {
                analysisResult.value = analysisData
                analysisStage.value = 'completed'
                taskStatus.value = taskStatus.value ? { ...taskStatus.value, status: 'completed', currentStep: '分析完成' } : null
                analysisTasks.value = analysisTasks.value.map((task) => ({ ...task, status: 'completed' as const }))
                updateAnalysisMessage({
                  analysisResult: analysisData,
                  isCompleted: true,
                  content: `分析完成，共识别出 ${analysisData.违法行为数量} 项事实认定`
                })
                pushProgressMessageFromEvent(eventData)
              }
              isAnalyzing.value = false
              ElMessage.success('违规分析完成！')
            } else {
              console.warn('[onCompleted] 没有找到 analysisData，无法更新分析结果')
            }
          },
          onError: (errorMsg) => {
            console.error('[SSE] 收到错误事件:', errorMsg)
            throw new Error(errorMsg)
          },
          onInterrupt: (interruptData) => {
            // Human-in-the-Loop: 图被中断，等待用户审核
            console.log('[SSE] 收到中断事件:', interruptData)
            const reviewData = interruptData as HumanReviewData

            // 根据 review_type 确定是哪个任务需要确认
            // 后端任务ID: split, review_split, retrieve, review_retrieve, format
            const taskIdToUpdate = reviewData.review_type === 'split'
              ? 'review_split'  // 拆分结果审核
              : 'review_retrieve' // 检索结果审核

            pendingReviewTaskIdRef.value = taskIdToUpdate

            // 更新任务状态为待确认，并附加审核数据
            analysisTasks.value = analysisTasks.value.map((task) =>
              task.id === taskIdToUpdate
                ? { ...task, status: 'awaiting_confirm' as const, reviewData }
                : task.status === 'in_progress'
                  ? { ...task, status: 'pending' as const }
                  : task
            )
          },
          onEnd: () => {
            // 流结束统一清理 UI 状态
            isChatStreaming.value = false
            chatStreamingStatus.value = ''
            activeAssistantMessageId.value = null
            analysisAbortControllerRef.value = null
          },
        },
      })
    } catch (error) {
      console.error('SSE调用失败:', error)

      const message =
        error instanceof Error ? error.message : '未知错误'

      // 如果是手动停止，设置为完成状态而不是错误状态
      if (manualStopRef.value) {
        console.log('[错误处理] 检测到手动停止，设置完成状态')
        taskStatus.value = taskStatus.value
          ? {
              ...taskStatus.value,
              status: 'completed',
            }
          : null
        updateHistoryItem(historyId, (item) => ({
          ...item,
          status: 'completed',
          updatedAt: new Date(),
        }))
        if (activeHistoryIdRef.value === historyId) {
          analysisStage.value = 'completed'
        }
        console.log('[错误处理] 完成状态已设置')
      } else {
        console.log('[错误处理] 真实错误，设置错误状态')
        // 真实错误：显示错误提示并设置错误状态
        ElMessage.error(`违规分析失败: ${message}`)
        taskStatus.value = taskStatus.value
          ? {
              ...taskStatus.value,
              status: 'error',
              error: message,
            }
          : null
        updateHistoryItem(historyId, (item) => ({
          ...item,
          status: 'error',
          error: message,
          updatedAt: new Date(),
        }))
        if (activeHistoryIdRef.value === historyId) {
          analysisStage.value = 'error'
          // 更新所有任务为错误状态
          analysisTasks.value = analysisTasks.value.map((task) =>
            task.status === 'in_progress' || task.status === 'pending'
              ? { ...task, status: 'error' as const }
              : task
          )
          // 更新分析消息为错误状态
          updateAnalysisMessage({
            analysisResult: null,
            isCompleted: true,
            content: `分析失败: ${message}`
          })
        }
      }
    } finally {
      isAnalyzing.value = false
      isChatStreaming.value = false // 重置流式状态
      analysisAbortControllerRef.value = null // 清理 AbortController
      manualStopRef.value = false // 重置手动停止标志
    }
  }

  const onSubmit = async (data: ViolationFormValues) => {
    const historyId = createMessageId()

    // 检查是否已有线程 ID，如果没有则创建新线程
    const currentThreadId = await ensureThreadId(chatThreadId.value, createNewThread, '违规分析')
    if (!currentThreadId) {
      return // 终止提交流程
    }

    const newHistory: AnalysisHistoryItem = {
      id: historyId,
      inspectionContent: '',
      violationDescription: data.violationDescription,
      startedAt: new Date(),
      updatedAt: new Date(),
      status: 'analyzing',
      progress: 0,
      result: null,
    }

    analysisHistory.value = [newHistory, ...analysisHistory.value]
    activeHistoryId.value = historyId
    analysisStage.value = 'analyzing'
    analysisResult.value = null
    taskStatus.value = null
    isAnalyzing.value = false
    currentChatTaskId.value = ''
    chatTaskIdRef.value = ''

    // 添加用户的事实认定输入作为消息
    const userMessage: ChatMessage = {
      id: createMessageId(),
      role: 'user',
      content: data.violationDescription,
      timestamp: new Date(),
      additional_kwargs: {
        task: {
          task_type: 'violation_analysis',
          uploaded_files: data.uploadedFiles || []
        }
      },
    }
    chatMessages.value = [...chatMessages.value, userMessage]

    await callViolationAnalysisSSE(data, historyId, currentThreadId)
  }

  const sendChatMessage = async (rawText: string) => {
    const trimmed = rawText.trim()
    if (!trimmed || isChatStreaming.value) {
      return
    }

    // 确保有线程 ID（如果没有则创建新线程）
    const currentThreadId = await ensureThreadId(chatThreadId.value, createNewThread, '问答')
    if (!currentThreadId) {
      return
    }

    chatTaskIdRef.value = ''
    currentChatTaskId.value = ''
    manualStopRef.value = false

    const userMessage: ChatMessage = {
      id: createMessageId(),
      role: 'user',
      content: trimmed,
      timestamp: new Date(),
    }

    const assistantMessageId = createMessageId()
    const assistantMessage: ChatMessage = {
      id: assistantMessageId,
      role: 'assistant',
      content: '',
      timestamp: new Date(),
    }

    // 先设置流式状态,确保"正在生成..."立即显示
    isChatStreaming.value = true
    chatStreamingStatus.value = '正在生成...'
    activeAssistantMessageId.value = assistantMessageId
    chatInput.value = ''
    // 然后再添加消息
    chatMessages.value = [...chatMessages.value, userMessage, assistantMessage]

    // 如果是第一条 QA 消息且没有 activeHistoryId，创建历史记录
    let currentHistoryId = activeHistoryId.value
    if (!activeHistoryId.value && chatMessages.value.length === 0) {
      const newHistoryId = `qa_${Date.now()}`
      const newHistory: AnalysisHistoryItem = {
        id: newHistoryId,
        inspectionContent: '',
        violationDescription: trimmed,
        startedAt: new Date(),
        updatedAt: new Date(),
        status: 'draft',
        progress: 0,
        result: null,
      }
      analysisHistory.value = [newHistory, ...analysisHistory.value]
      activeHistoryId.value = newHistoryId
      activeHistoryIdRef.value = newHistoryId // 同步更新 ref
      currentHistoryId = newHistoryId
      console.log('[QA会话] 创建新的历史记录:', newHistoryId)
    }

    const controller = new AbortController()
    chatAbortControllerRef.value = controller

    // 重置任务消息 ref，用于追踪分析消息
    tasksMessageIdRef.value = null

    try {
      await processSSEStream({
        url: `${API_BASE}/api/conversations/${currentThreadId}/completion`,
        method: 'POST',
        body: {
          query: trimmed,
          agent_id: agentId,
        },
        signal: controller.signal,
        handlers: {
          onMessage: (content) => {
            chatMessages.value = appendMessageContent(
              chatMessages.value,
              assistantMessageId,
              content
            )
          },
          onTask: (tasks) => {
            // 后端判断是分析请求，返回 task_progress
            // 需要把消息转换为结构化消息
            if (tasks) {
              // 如果还没有创建任务消息，将当前助手消息转换为任务消息
              if (!tasksMessageIdRef.value) {
                tasksMessageIdRef.value = assistantMessageId
                chatMessages.value = chatMessages.value.map((msg) =>
                  msg.id === assistantMessageId
                    ? {
                        ...msg,
                        content: [
                          {
                            type: 'non_standard',
                            value: { type: 'task_progress', data: tasks }
                          }
                        ]
                      }
                    : msg
                )
              } else {
                // 更新任务进度
                chatMessages.value = chatMessages.value.map((msg) => {
                  if (msg.id !== tasksMessageIdRef.value) return msg
                  let newContent = Array.isArray(msg.content) ? [...msg.content] : []
                  const taskBlockIndex = newContent.findIndex(
                    (b: any) => b.type === 'non_standard' && b.value?.type === 'task_progress'
                  )
                  const taskBlock = {
                    type: 'non_standard',
                    value: { type: 'task_progress', data: tasks }
                  }
                  if (taskBlockIndex >= 0) {
                    newContent[taskBlockIndex] = taskBlock
                  } else {
                    newContent.push(taskBlock)
                  }
                  return { ...msg, content: newContent }
                })
              }
              analysisTasks.value = tasks
              isAnalyzing.value = true
              analysisStage.value = 'analyzing'
            }
          },
          onCompleted: (eventData) => {
            console.log('[sendChatMessage onCompleted] 收到完成事件:', eventData)

            // 异步生成标题（第一次完成时）
            void generateAndUpdateTitle(currentThreadId)
            currentChatTaskId.value = ''
            chatTaskIdRef.value = ''

            // 获取要更新的消息 ID - 优先使用任务消息 ID，否则使用当前助手消息 ID
            const targetMessageId = tasksMessageIdRef.value || assistantMessageId
            console.log('[sendChatMessage onCompleted] targetMessageId:', targetMessageId)
            console.log('[sendChatMessage onCompleted] tasksMessageIdRef.value:', tasksMessageIdRef.value)
            console.log('[sendChatMessage onCompleted] assistantMessageId:', assistantMessageId)

            if (eventData?.messages?.[0]) {
              const message = eventData.messages[0]
              console.log('[sendChatMessage onCompleted] message:', message)

              if (Array.isArray(message.content)) {
                console.log('[sendChatMessage onCompleted] message.content 数组:', message.content)

                // 如果是分析流程，提取 analysis_data
                const dataBlock = message.content.find(
                  (b: any) => b.type === 'non_standard' && b.value?.type === 'analysis_data'
                )
                console.log('[sendChatMessage onCompleted] 找到的 dataBlock:', dataBlock)

                if (dataBlock) {
                  const analysisData = dataBlock.value?.data
                  console.log('[sendChatMessage onCompleted] analysisData:', analysisData)
                  if (analysisData) {
                    analysisResult.value = analysisData
                    analysisStage.value = 'completed'
                    analysisTasks.value = analysisTasks.value.map((task) => ({ ...task, status: 'completed' as const }))
                  }
                }
                // 更新消息内容为完整的后端返回
                chatMessages.value = chatMessages.value.map((msg) =>
                  msg.id === targetMessageId
                    ? { ...msg, content: message.content }
                    : msg
                )
              } else {
                console.log('[sendChatMessage onCompleted] message.content 不是数组:', message.content)
                // 普通文本消息，直接更新
                chatMessages.value = chatMessages.value.map((msg) =>
                  msg.id === targetMessageId
                    ? { ...msg, content: message.content || msg.content }
                    : msg
                )
              }
              isAnalyzing.value = false
            } else {
              console.log('[sendChatMessage onCompleted] eventData.messages:', eventData?.messages)
            }
          },
          onError: (errorMsg) => {
            console.error('[QA SSE] 收到错误事件:', errorMsg)
            chatMessages.value = setMessageError(
              chatMessages.value,
              assistantMessageId,
              `❌ ${errorMsg}`
            )
            ElMessage.error(errorMsg)
          },
          onEnd: () => {
            // 流结束统一清理 UI 状态
            isChatStreaming.value = false
            chatStreamingStatus.value = ''
            activeAssistantMessageId.value = null
            chatAbortControllerRef.value = null
          },
        },
      })
    } catch (error) {
      const message =
        error instanceof Error ? error.message : '发送失败，请稍后再试'
      console.error('执法追问失败:', error)
      ElMessage.error(message)
      chatMessages.value = chatMessages.value.map((msg) =>
        msg.id === assistantMessageId
          ? {
            ...msg,
            content:
              msg.content ||
              `（对话失败：${
                error instanceof Error ? error.message : '未知错误'
              }）`,
            timestamp: new Date(),
          }
          : msg,
      )
    }
  }

  const stopChatStreaming = () => {
    if (!isChatStreaming.value) {
      return
    }

    const assistantIdSnapshot = activeAssistantMessageId.value

    console.log('[停止] 用户手动停止，设置 manualStopRef = true')
    manualStopRef.value = true

    // 断开聊天连接
    if (chatAbortControllerRef.value) {
      console.log('[停止] 中止聊天连接')
      chatAbortControllerRef.value.abort()
      chatAbortControllerRef.value = null
    }

    // 断开分析连接
    if (analysisAbortControllerRef.value) {
      console.log('[停止] 中止分析连接')
      analysisAbortControllerRef.value.abort()
      analysisAbortControllerRef.value = null
    }

    // 重置聊天状态
    isChatStreaming.value = false
    chatStreamingStatus.value = ''
    activeAssistantMessageId.value = null
    currentChatTaskId.value = ''
    chatTaskIdRef.value = ''

    // 重置分析状态
    isAnalyzing.value = false
    analysisStage.value = 'completed'

    console.log('[停止] 状态已重置')

    // 更新消息状态
    chatMessages.value = chatMessages.value.map((msg) => {
      // 更新聊天消息（assistant）
      if (msg.id === assistantIdSnapshot) {
        if (!msg.content) {
          return { ...msg, content: '（已取消）', timestamp: new Date() }
        }
        if (typeof msg.content === 'string' && msg.content.includes('（已取消）')) {
          return msg
        }
        return {
          ...msg,
          content: `${msg.content}\n（已取消）`,
          timestamp: new Date(),
        }
      }
      // 更新分析消息 - 将所有进行中的任务标记为已取消
      // 任务进度存储在 content 数组的 NonStandardContentBlock 中
      if (Array.isArray(msg.content)) {
        const hasTaskProgress = msg.content.some(
          (b: any) => b.type === 'non_standard' && b.value?.type === 'task_progress'
        )
        if (hasTaskProgress) {
          console.log('[停止] 任务状态设为已取消')
          return {
            ...msg,
            content: msg.content.map((block: any) => {
              if (block.type === 'non_standard' && block.value?.type === 'task_progress') {
                return {
                  ...block,
                  value: {
                    ...block.value,
                    data: (block.value.data || []).map((task: AnalysisTask) =>
                      task.status === 'in_progress'
                        ? { ...task, status: 'cancelled' as const }
                        : task
                    )
                  }
                }
              }
              return block
            }),
            timestamp: new Date(),
          }
        }
      }
      return msg
    })
  }

  // 处理法律条款点击
  const handleLawClick = (
    law: { law_name: string; law_item_name: string; law_item_content: string }
  ) => {
    selectedDocument.value = law
  }

  const sendReportRequest = async (
    reportSubType: string,
    uploadedFiles?: Array<{file_id: string; file_name: string; file_type: string; uploaded_at?: string}>,
    onAutoPreview?: (doc: { filename: string; title: string; excelData?: any; sourceData?: any }) => void
  ) => {
    if (!reportSubType || isChatStreaming.value) {
      return
    }

    // 确保有线程 ID
    const currentThreadId = await ensureThreadId(chatThreadId.value, createNewThread, '报告生成')
    if (!currentThreadId) {
      return
    }

    const reportTypeInfo = REPORT_TYPE_MAP[reportSubType]
    if (!reportTypeInfo) {
      ElMessage.error(`未知的报告类型: ${reportSubType}`)
      return
    }

    const reportMessage = `请生成${reportTypeInfo.name}`

    chatTaskIdRef.value = ''
    currentChatTaskId.value = ''
    manualStopRef.value = false

    const userMessage: ChatMessage = {
      id: createMessageId(),
      role: 'user',
      content: reportMessage,
      timestamp: new Date(),
      additional_kwargs: {
        task: {
          task_type: 'report',
          report_type: reportTypeInfo.pinyin,
          uploaded_files: uploadedFiles || []  // 统一使用 uploaded_files，与后端一致
        }
      },
    }

    const assistantMessageId = createMessageId()
    const assistantMessage: ChatMessage = {
      id: assistantMessageId,
      role: 'assistant',
      content: [
        { type: 'text', text: '正在生成报告...' }
      ],
      timestamp: new Date(),
    }

    tasksMessageIdRef.value = assistantMessageId
    chatMessages.value = [...chatMessages.value, userMessage, assistantMessage]
    isChatStreaming.value = true
    chatStreamingStatus.value = '正在生成报告...'
    activeAssistantMessageId.value = assistantMessageId

    const controller = new AbortController()
    chatAbortControllerRef.value = controller

    try {
      await processSSEStream({
        url: `${API_BASE}/api/conversations/${currentThreadId}/completion`,
        method: 'POST',
        body: {
          query: reportMessage,
          agent_id: agentId,
          options: {
            task_type: 'report',
            report_type: reportTypeInfo.pinyin,
            uploaded_files: uploadedFiles || [],
          }
        },
        signal: controller.signal,
        handlers: {
          onMessage: (content) => {
            chatMessages.value = chatMessages.value.map((msg) =>
              msg.id === assistantMessageId
                ? { ...msg, content: msg.content + content }
                : msg
            )
          },
          onFile: (fileData) => {
            // 将 file 块添加到消息 content 中
            chatMessages.value = chatMessages.value.map((msg) => {
              if (msg.id !== assistantMessageId) return msg
              let newContent = Array.isArray(msg.content) ? [...msg.content] : [msg.content]
              // 移除 '正在生成报告...' 文本块
              newContent = newContent.filter((b: any) => !(b.type === 'text' && b.text === '正在生成报告...'))
              // 添加 file 块（如果已存在则替换）
              const existingFileIndex = newContent.findIndex((b: any) => b.type === 'file')
              const fileBlock = { type: 'file' as const, ...fileData }
              if (existingFileIndex >= 0) {
                newContent[existingFileIndex] = fileBlock
              } else {
                newContent.push(fileBlock)
              }
              return { ...msg, content: newContent }
            })
          },
          onFileTitle: (titleData) => {
            // 将 file_title 块添加到消息 content 中
            chatMessages.value = chatMessages.value.map((msg) => {
              if (msg.id !== assistantMessageId) return msg
              let newContent = Array.isArray(msg.content) ? [...msg.content] : [msg.content]
              // 移除 '正在生成报告...' 文本块
              newContent = newContent.filter((b: any) => !(b.type === 'text' && b.text === '正在生成报告...'))
              // 添加 file_title 块（如果已存在则替换）
              const existingTitleIndex = newContent.findIndex((b: any) =>
                b.type === 'non_standard' && b.value?.type === 'file_title'
              )
              const titleBlock = { type: 'non_standard' as const, value: { type: 'file_title' as const, data: titleData.title } }
              if (existingTitleIndex >= 0) {
                newContent[existingTitleIndex] = titleBlock
              } else {
                newContent.push(titleBlock)
              }
              return { ...msg, content: newContent }
            })
          },
          onExcelData: (excelData) => {
            // 将 excel_data 块添加到消息 content 中
            chatMessages.value = chatMessages.value.map((msg) => {
              if (msg.id !== assistantMessageId) return msg
              let newContent = Array.isArray(msg.content) ? [...msg.content] : [msg.content]
              // 移除 '正在生成报告...' 文本块
              newContent = newContent.filter((b: any) => !(b.type === 'text' && b.text === '正在生成报告...'))
              // 添加 excel_data 块（如果已存在则替换）
              const existingExcelIndex = newContent.findIndex((b: any) =>
                b.type === 'non_standard' && b.value?.type === 'excel_data'
              )
              const excelBlock = { type: 'non_standard' as const, value: { type: 'excel_data' as const, data: excelData } }
              if (existingExcelIndex >= 0) {
                newContent[existingExcelIndex] = excelBlock
              } else {
                newContent.push(excelBlock)
              }
              return { ...msg, content: newContent }
            })
          },
          onReportSourceData: (sourceData) => {
            // 将 report_source_data 块添加到消息 content 中
            chatMessages.value = chatMessages.value.map((msg) => {
              if (msg.id !== assistantMessageId) return msg
              let newContent = Array.isArray(msg.content) ? [...msg.content] : [msg.content]
              // 移除 '正在生成报告' 文本块
              newContent = newContent.filter((b: any) => !(b.type === 'text' && b.text === '正在生成报告...'))
              // 添加 report_source_data 块（如果已存在则替换）
              const existingSourceIndex = newContent.findIndex((b: any) =>
                b.type === 'non_standard' && b.value?.type === 'report_source_data'
              )
              const sourceBlock = { type: 'non_standard' as const, value: { type: 'report_source_data' as const, data: sourceData } }
              if (existingSourceIndex >= 0) {
                newContent[existingSourceIndex] = sourceBlock
              } else {
                newContent.push(sourceBlock)
              }
              return { ...msg, content: newContent }
            })
          },
          onTask: (tasks) => {
            // 将 task_progress 放到 content 数组的 NonStandardContentBlock 中
            // 符合 LangChain 官方规范
            chatMessages.value = chatMessages.value.map((msg) => {
              if (msg.id !== assistantMessageId) return msg

              let newContent = Array.isArray(msg.content) ? [...msg.content] : []

              // 查找现有的 task_progress 块
              const taskBlockIndex = newContent.findIndex(
                (b: any) => b.type === 'non_standard' && b.value?.type === 'task_progress'
              )
              const taskBlock = {
                type: 'non_standard',
                value: { type: 'task_progress', data: tasks }
              }

              if (taskBlockIndex >= 0) {
                newContent[taskBlockIndex] = taskBlock
              } else {
                newContent.push(taskBlock)
              }

              return {
                ...msg,
                content: newContent
              }
            })
          },
          onInterrupt: (interruptData) => {
            // Human-in-the-Loop: 报告生成被中断，等待用户审核
            console.log('[报告生成] 收到中断事件:', interruptData)
            console.log('[报告生成] current_data:', interruptData.current_data)
            console.log('[报告生成] current_data 类型:', typeof interruptData.current_data)
            console.log('[报告生成] current_data.files:', interruptData.current_data?.files)
            const reviewData = interruptData as HumanReviewData

            // 根据 review_type 确定任务ID
            const reviewTypeToTaskId: Record<string, string> = {
              'extract': 'review_extract',
              'analyze': 'review_analyze',
              'retrieve': 'review_retrieve',
              'evidence': 'review_evidence',
            }
            const taskIdToUpdate = reviewTypeToTaskId[reviewData.review_type] || 'review_extract'
            console.log('[报告生成] 审核类型:', reviewData.review_type, '任务ID:', taskIdToUpdate)
            pendingReviewTaskIdRef.value = taskIdToUpdate

            // 更新消息中的任务状态为待确认，并附加审核数据
            chatMessages.value = chatMessages.value.map((msg) => {
              if (msg.id !== assistantMessageId) return msg

              let newContent = Array.isArray(msg.content) ? [...msg.content] : []

              // 查找现有的 task_progress 块并更新
              const taskBlockIndex = newContent.findIndex(
                (b: any) => b.type === 'non_standard' && b.value?.type === 'task_progress'
              )

              if (taskBlockIndex >= 0) {
                const currentTasks = (newContent[taskBlockIndex] as any).value?.data || []
                const updatedTasks = currentTasks.map((task: AnalysisTask) =>
                  task.id === taskIdToUpdate
                    ? { ...task, status: 'awaiting_confirm' as const, reviewData }
                    : task.status === 'in_progress'
                      ? { ...task, status: 'pending' as const }
                      : task
                )
                newContent[taskBlockIndex] = {
                  type: 'non_standard',
                  value: { type: 'task_progress', data: updatedTasks }
                }
              } else {
                // 没有找到 task_progress 块，创建一个包含审核任务的新块
                console.log('[报告生成] 未找到 task_progress 块，创建新块')
                const newTaskBlock = {
                  type: 'non_standard',
                  value: {
                    type: 'task_progress',
                    data: [{
                      id: taskIdToUpdate,
                      title: reviewData.message || '待审核',
                      status: 'awaiting_confirm' as const,
                      reviewData
                    }]
                  }
                }
                // 移除 '正在生成报告...' 文本，添加任务块
                newContent = newContent.filter((b: any) => !(b.type === 'text' && b.text === '正在生成报告...'))
                newContent.push(newTaskBlock)
              }

              return {
                ...msg,
                content: newContent
              }
            })
          },
          // onFile 已删除 - 文件信息在 completed 事件的 content 数组中
          onCompleted: (eventData) => {
            console.log('[报告生成] 收到完成事件:', eventData)

            // 从 completed 事件中提取 message
            // 后端返回格式：{ messages: [{...}] } 或 { message: {...} }
            const message = eventData?.messages?.[0] || eventData?.message
            if (message) {
              chatMessages.value = chatMessages.value.map((msg) => {
                if (msg.id !== assistantMessageId) return msg

                // 直接使用后端返回的 content（包含 NonStandardContentBlock）
                return {
                  ...msg,
                  content: message.content || msg.content
                }
              })

              // 自动预览：从 content 中提取文件信息
              if (onAutoPreview && message.content) {
                const content = message.content
                if (Array.isArray(content)) {
                  // 查找文件块（FileContentBlock，使用 file_id 作为文件名）
                  const fileBlock = content.find((b: any) => b.type === 'file')
                  // 查找 excel_data 块
                  const excelBlock = content.find((b: any) =>
                    b.type === 'non_standard' && b.value?.type === 'excel_data'
                  )
                  // 查找 file_title 块
                  const titleBlock = content.find((b: any) =>
                    b.type === 'non_standard' && b.value?.type === 'file_title'
                  )
                  // 查找 report_source_data 块（用于编辑后重新生成）
                  const sourceDataBlock = content.find((b: any) =>
                    b.type === 'non_standard' && b.value?.type === 'report_source_data'
                  )

                  if (fileBlock) {
                    // file_id 就是文件名（如 "整改通知书_xxx.docx"）
                    const filename = fileBlock.file_id
                    const title = titleBlock?.value?.data || reportTypeInfo.name
                    const excelData = excelBlock?.value?.data
                    const sourceData = sourceDataBlock?.value?.data

                    if (filename) {
                      onAutoPreview({ filename, title, excelData, sourceData })
                    }
                  }
                }
              }
            }

            // 生成会话标题（第一次完成时）
            void generateAndUpdateTitle(currentThreadId)
            ElMessage.success('报告生成成功')
          },
          onError: (errorMsg) => {
            console.error('[报告生成] 收到错误事件:', errorMsg)
            chatMessages.value = chatMessages.value.map((msg) =>
              msg.id === assistantMessageId
                ? { ...msg, content: `❌ ${errorMsg}` }
                : msg
            )
            ElMessage.error(errorMsg)
          },
          onEnd: () => {
            // 流结束统一清理 UI 状态
            isChatStreaming.value = false
            chatStreamingStatus.value = ''
            activeAssistantMessageId.value = null
            chatAbortControllerRef.value = null
          },
        },
      })
    } catch (error: unknown) {
      if (error instanceof Error) {
        if (error.name === 'AbortError') {
          console.log('[报告生成] 用户取消')
        } else {
          console.error('[报告生成] 错误:', error)
          ElMessage.error(error.message || '报告生成失败')
          chatMessages.value = chatMessages.value.map((msg) =>
            msg.id === assistantMessageId
              ? { ...msg, content: `错误: ${error.message}` }
              : msg
          )
        }
      }
    }
  }

  // 切换到指定线程
  const switchToThread = (threadId: string) => {
    chatThreadId.value = threadId
  }

  /**
   * Human-in-the-Loop: 恢复被中断的报告生成
   */
  const resumeReportGeneration = async (
    resumeInput: ResumeInput,
    taskId: string,
    messageId: string,
    onAutoPreview?: (doc: { filename: string; title: string; excelData?: any; sourceData?: any }) => void
  ) => {
    if (!chatThreadId.value) {
      ElMessage.error('没有活动的会话')
      return
    }

    // 如果用户取消，停止报告生成
    if (resumeInput.action === 'reject') {
      isChatStreaming.value = false
      chatMessages.value = chatMessages.value.map((msg) => {
        if (msg.id !== messageId) return msg

        let newContent = Array.isArray(msg.content) ? [...msg.content] : []
        const taskBlockIndex = newContent.findIndex(
          (b: any) => b.type === 'non_standard' && b.value?.type === 'task_progress'
        )

        if (taskBlockIndex >= 0) {
          const currentTasks = (newContent[taskBlockIndex] as any).value?.data || []
          const updatedTasks = currentTasks.map((task: AnalysisTask) =>
            task.status === 'pending' || task.status === 'in_progress' || task.status === 'awaiting_confirm'
              ? { ...task, status: 'cancelled' as const, reviewData: undefined }
              : task
          )
          newContent[taskBlockIndex] = {
            type: 'non_standard',
            value: { type: 'task_progress', data: updatedTasks }
          }
        }

        return { ...msg, content: newContent }
      })
      pendingReviewTaskIdRef.value = null
      ElMessage.info('已取消报告生成')
      return
    }

    // 将待确认任务标记为已完成
    chatMessages.value = chatMessages.value.map((msg) => {
      if (msg.id !== messageId) return msg

      let newContent = Array.isArray(msg.content) ? [...msg.content] : []
      const taskBlockIndex = newContent.findIndex(
        (b: any) => b.type === 'non_standard' && b.value?.type === 'task_progress'
      )

      if (taskBlockIndex >= 0) {
        const currentTasks = (newContent[taskBlockIndex] as any).value?.data || []
        const updatedTasks = currentTasks.map((task: AnalysisTask) =>
          task.id === taskId
            ? { ...task, status: 'completed' as const, reviewData: undefined }
            : task
        )
        newContent[taskBlockIndex] = {
          type: 'non_standard',
          value: { type: 'task_progress', data: updatedTasks }
        }
      }

      return { ...msg, content: newContent }
    })
    pendingReviewTaskIdRef.value = null

    // 恢复流式状态
    isChatStreaming.value = true

    const controller = new AbortController()
    chatAbortControllerRef.value = controller

    try {
      await processSSEStream({
        url: `${API_BASE}/api/conversations/${chatThreadId.value}/resume`,
        method: 'POST',
        body: {
          action: resumeInput.action,
          data: resumeInput.data,
        },
        signal: controller.signal,
        handlers: {
          onTask: (tasks) => {
            // 更新消息中的任务进度
            chatMessages.value = chatMessages.value.map((msg) => {
              if (msg.id !== messageId) return msg

              let newContent = Array.isArray(msg.content) ? [...msg.content] : []
              const taskBlockIndex = newContent.findIndex(
                (b: any) => b.type === 'non_standard' && b.value?.type === 'task_progress'
              )
              const taskBlock = {
                type: 'non_standard',
                value: { type: 'task_progress', data: tasks }
              }

              if (taskBlockIndex >= 0) {
                newContent[taskBlockIndex] = taskBlock
              } else {
                newContent.push(taskBlock)
              }

              return { ...msg, content: newContent }
            })
          },
          onCompleted: (eventData) => {
            console.log('[报告恢复] 收到完成事件:', eventData)

            // 从 completed 事件中提取 message
            // 后端返回格式：{ messages: [{...}] } 或 { message: {...} }
            const message = eventData?.messages?.[0] || eventData?.message
            if (message) {
              chatMessages.value = chatMessages.value.map((msg) => {
                if (msg.id !== messageId) return msg
                return {
                  ...msg,
                  content: message.content || msg.content
                }
              })

              // 自动预览
              if (onAutoPreview && message.content) {
                const content = message.content
                if (Array.isArray(content)) {
                  const fileBlock = content.find((b: any) => b.type === 'file')
                  const excelBlock = content.find((b: any) =>
                    b.type === 'non_standard' && b.value?.type === 'excel_data'
                  )
                  const titleBlock = content.find((b: any) =>
                    b.type === 'non_standard' && b.value?.type === 'file_title'
                  )
                  // 查找 report_source_data 块（用于编辑后重新生成）
                  const sourceDataBlock = content.find((b: any) =>
                    b.type === 'non_standard' && b.value?.type === 'report_source_data'
                  )

                  if (fileBlock) {
                    const filename = fileBlock.file_id
                    const title = titleBlock?.value?.data || '报告'
                    const excelData = excelBlock?.value?.data
                    const sourceData = sourceDataBlock?.value?.data

                    if (filename) {
                      onAutoPreview({ filename, title, excelData, sourceData })
                    }
                  }
                }
              }
            }

            void generateAndUpdateTitle(chatThreadId.value)
            ElMessage.success('报告生成成功')
          },
          onError: (errorMsg) => {
            console.error('[报告恢复] 收到错误事件:', errorMsg)
            ElMessage.error(errorMsg)
          },
          onInterrupt: (interruptData) => {
            // 再次被中断
            console.log('[报告恢复] 再次收到中断事件:', interruptData)
            const reviewData = interruptData as HumanReviewData
            // 根据 review_type 确定任务ID
            const reviewTypeToTaskId: Record<string, string> = {
              'extract': 'review_extract',
              'analyze': 'review_analyze',
              'retrieve': 'review_retrieve',
              'evidence': 'review_evidence',
            }
            const taskIdToUpdate = reviewTypeToTaskId[reviewData.review_type] || 'review_extract'
            console.log('[报告恢复] 审核类型:', reviewData.review_type, '任务ID:', taskIdToUpdate)
            pendingReviewTaskIdRef.value = taskIdToUpdate

            chatMessages.value = chatMessages.value.map((msg) => {
              if (msg.id !== messageId) return msg

              let newContent = Array.isArray(msg.content) ? [...msg.content] : []
              const taskBlockIndex = newContent.findIndex(
                (b: any) => b.type === 'non_standard' && b.value?.type === 'task_progress'
              )

              if (taskBlockIndex >= 0) {
                const currentTasks = (newContent[taskBlockIndex] as any).value?.data || []
                const updatedTasks = currentTasks.map((task: AnalysisTask) =>
                  task.id === taskIdToUpdate
                    ? { ...task, status: 'awaiting_confirm' as const, reviewData }
                    : task.status === 'in_progress'
                      ? { ...task, status: 'pending' as const }
                      : task
                )
                newContent[taskBlockIndex] = {
                  type: 'non_standard',
                  value: { type: 'task_progress', data: updatedTasks }
                }
              }

              return { ...msg, content: newContent }
            })
          },
          onEnd: () => {
            isChatStreaming.value = false
            chatStreamingStatus.value = ''
            activeAssistantMessageId.value = null
            chatAbortControllerRef.value = null
          },
        },
      })
    } catch (error) {
      console.error('Resume report SSE调用失败:', error)
      const message = error instanceof Error ? error.message : '未知错误'
      ElMessage.error(`恢复报告生成失败: ${message}`)
      isChatStreaming.value = false
    }
  }

  /**
   * Human-in-the-Loop: 恢复被中断的对话
   */
  const resumeConversation = async (resumeInput: ResumeInput, taskId: string) => {
    if (!chatThreadId.value) {
      ElMessage.error('没有活动的会话')
      return
    }

    // 如果用户取消，停止分析
    if (resumeInput.action === 'reject') {
      isAnalyzing.value = false
      isChatStreaming.value = false
      analysisTasks.value = analysisTasks.value.map((task) =>
        task.status === 'pending' || task.status === 'in_progress' || task.status === 'awaiting_confirm'
          ? { ...task, status: 'cancelled' as const, reviewData: undefined }
          : task
      )
      pendingReviewTaskIdRef.value = null
      ElMessage.info('已取消分析')
      return
    }

    // 将待确认任务标记为已完成，清除审核数据
    analysisTasks.value = analysisTasks.value.map((task) =>
      task.id === taskId
        ? { ...task, status: 'completed' as const, reviewData: undefined }
        : task
    )
    pendingReviewTaskIdRef.value = null

    // 恢复分析状态
    isAnalyzing.value = true
    isChatStreaming.value = true

    const controller = new AbortController()
    analysisAbortControllerRef.value = controller

    try {
      await processSSEStream({
        url: `${API_BASE}/api/conversations/${chatThreadId.value}/resume`,
        method: 'POST',
        body: {
          action: resumeInput.action,
          data: resumeInput.data,
        },
        signal: controller.signal,
        handlers: {
          onTask: (tasks) => {
            if (tasks) {
              analysisTasks.value = tasks
            }
          },
          onCompleted: (eventData) => {
            // 从 completed 事件中提取分析结果
            // 后端返回格式：{ messages: [{...}] } 或 { message: {...} }
            const message = eventData?.messages?.[0] || eventData?.message
            if (!message) {
              console.warn('[resume onCompleted] 没有找到 message, eventData:', eventData)
              return
            }

            let analysisData: AnalysisResult | null = null
            if (Array.isArray(message.content)) {
              const dataBlock = message.content.find(
                (b: any) => b.type === 'non_standard' && b.value?.type === 'analysis_data'
              )
              if (dataBlock) {
                analysisData = dataBlock.value?.data || null
              }
            }

            if (analysisData) {
              // 更新历史记录
              if (activeHistoryIdRef.value) {
                updateHistoryItem(activeHistoryIdRef.value, (item) => ({
                  ...item,
                  status: 'completed',
                  result: analysisData,
                  updatedAt: new Date(),
                }))
              }

              // 生成会话标题
              void generateAndUpdateTitle(chatThreadId.value)

              // 更新 UI 状态
              analysisResult.value = analysisData
              analysisStage.value = 'completed'
              taskStatus.value = taskStatus.value ? { ...taskStatus.value, status: 'completed', currentStep: '分析完成' } : null
              analysisTasks.value = analysisTasks.value.map((task) => ({ ...task, status: 'completed' as const }))
              updateAnalysisMessage({
                analysisResult: analysisData,
                isCompleted: true,
                content: `分析完成，共识别出 ${analysisData.违法行为数量} 项事实认定`
              })

              isAnalyzing.value = false
              ElMessage.success('违规分析完成！')
            }
          },
          onError: (errorMsg) => {
            console.error('[resume SSE] 收到错误事件:', errorMsg)
            ElMessage.error(errorMsg)
            isAnalyzing.value = false
            isChatStreaming.value = false
          },
          onInterrupt: (interruptData) => {
            // 再次被中断，更新任务为待确认状态
            console.log('[resume SSE] 再次收到中断事件:', interruptData)
            const reviewData = interruptData as HumanReviewData

            // 根据 review_type 确定是哪个任务需要确认
            // 后端任务ID: split, review_split, retrieve, review_retrieve, format
            const taskIdToUpdate = reviewData.review_type === 'split'
              ? 'review_split'
              : 'review_retrieve'

            pendingReviewTaskIdRef.value = taskIdToUpdate

            analysisTasks.value = analysisTasks.value.map((task) =>
              task.id === taskIdToUpdate
                ? { ...task, status: 'awaiting_confirm' as const, reviewData }
                : task.status === 'in_progress'
                  ? { ...task, status: 'pending' as const }
                  : task
            )
          },
          onEnd: () => {
            isChatStreaming.value = false
            analysisAbortControllerRef.value = null
          },
        },
      })
    } catch (error) {
      console.error('Resume SSE调用失败:', error)
      const message = error instanceof Error ? error.message : '未知错误'
      ElMessage.error(`恢复分析失败: ${message}`)
      isAnalyzing.value = false
      isChatStreaming.value = false
    }
  }

  // Human-in-the-Loop 回调函数
  const handleHumanReviewApprove = (taskId: string) => {
    void resumeConversation({ action: 'approve' }, taskId)
  }

  const handleHumanReviewEdit = (taskId: string, data: { violations: any[] }) => {
    void resumeConversation({ action: 'edit', data }, taskId)
  }

  const handleHumanReviewReject = (taskId: string) => {
    void resumeConversation({ action: 'reject' }, taskId)
  }

  return {
    // State
    analysisResult,
    isAnalyzing,
    taskStatus,
    analysisTasks,
    chatMessages,
    setChatMessages: (value: ChatMessage[]) => { chatMessages.value = value },
    chatInput,
    setChatInput: (value: string) => { chatInput.value = value },
    isChatStreaming,
    chatStreamingStatus,
    activeAssistantMessageId,
    currentChatTaskId,
    chatThreadId,
    analysisStage,
    analysisHistory,
    activeHistoryId,
    setActiveHistoryId: (value: string | null) => { activeHistoryId.value = value },
    isHistoryCollapsed,
    setIsHistoryCollapsed: (value: boolean) => { isHistoryCollapsed.value = value },
    missingRequiredLabels,
    hasFiveRequiredInputs,
    violationDescriptionValue,
    activeHistory,
    selectedDocument,
    setSelectedDocument: (value: { law_name: string; law_item_name: string; law_item_content: string } | null) => { selectedDocument.value = value },
    historyRefreshTrigger, // 历史记录刷新触发器

    // Form
    register,
    handleSubmit,
    errors,
    reset,
    onSubmit,

    // Refs
    chatContainerRef,

    // Functions
    sendChatMessage,
    sendReportRequest,
    stopChatStreaming,
    resetChatHistory,
    createNewThread,
    setAnalysisResult: (value: AnalysisResult | null) => { analysisResult.value = value },
    setAnalysisStage: (value: AnalysisStage) => { analysisStage.value = value },
    setTaskStatus: (value: SSETaskStatus | null) => { taskStatus.value = value },
    handleLawClick,
    switchToThread,
    loadThreadMessages,  // 加载指定会话的历史消息

    // Human-in-the-Loop 人工审核回调
    handleHumanReviewApprove,
    handleHumanReviewEdit,
    handleHumanReviewReject,

    // 报告生成恢复（用于报告审核后继续生成）
    resumeReportGeneration,
  }
}

export default useViolationAnalysis
