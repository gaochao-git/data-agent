<script setup lang="ts">
/**
 * SQL 对话组件
 * 支持结构化消息和工具调用展示
 */
import { ref, computed, watch, nextTick, toRef, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import MarkdownIt from 'markdown-it'
import type { DataSource, ToolCallInfo, LangGraphMessage, Message, SemanticParams, ExecutionTargetOption } from '../types'
import { useSqlGenerator } from '../composables/useSqlGenerator'
import { useChartRecommend } from '../composables/useChartRecommend'
import { useConversationHistory } from '../composables/useConversationHistory'
import StructuredMessage from './StructuredMessage.vue'
import ToolCallList from './ToolCallList.vue'
import AsyncQueryResult from './AsyncQueryResult.vue'
import QuickQueries from './QuickQueries.vue'
import SaveToPanelModal from './SaveToPanelModal.vue'
import QueryFavoriteModal from './QueryFavoriteModal.vue'
import AnalysisPanel from './AnalysisPanel.vue'
import ExecutionTargetPicker from './ExecutionTargetPicker.vue'
import type { AnalysisData } from './AnalysisPanel.vue'

interface ConversationItem {
  id: string
  title: string
  created_at: string
}

interface ConversationGroup {
  label: string
  items: ConversationItem[]
}

interface Props {
  datasources: DataSource[]
  datasource?: DataSource | null
  conversationId?: string | null
  conversationTitle?: string
  selectedDatasourceId?: string
  hideDatasourceSelector?: boolean
  hideHeader?: boolean
  hideSidebar?: boolean
  // 会话列表相关
  conversations?: ConversationItem[]
  groupedConversations?: ConversationGroup[]
  isLoadingConversations?: boolean
  // 懒创建会话
  ensureConversationId?: () => Promise<string | null>
  // 语义配置（数据集）ID
  semanticConfigId?: string | null
  // 是否显示分析面板（由外层切换控制）
  showAnalysisPanel?: boolean
  executionTargets?: ExecutionTargetOption[]
  executionTargetMultiple?: boolean
  selectedMemberDatasourceIds?: string[]
  hideExecutionTargetSelector?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  datasource: null,
  hideDatasourceSelector: false,
  hideHeader: false,
  hideSidebar: false,
  conversations: () => [],
  groupedConversations: () => [],
  isLoadingConversations: false,
  semanticConfigId: null,
  showAnalysisPanel: true,
  executionTargetMultiple: true,
  hideExecutionTargetSelector: false,
})

// 图表推荐开关（共享状态）
const { chartRecommendEnabled } = useChartRecommend()
const md = new MarkdownIt({ breaks: true, html: false, linkify: true })

// 表结构面板
const searchText = ref('')
const expandedTables = ref<Set<string>>(new Set())
const isDatasourceHovered = ref(false)

// 过滤表
const filteredTables = computed(() => {
  if (!props.datasource?.ds_tables) return []
  if (!searchText.value.trim()) return props.datasource.ds_tables
  const keyword = searchText.value.toLowerCase()
  return props.datasource.ds_tables.filter(t =>
    t.table_name.toLowerCase().includes(keyword) ||
    t.description?.toLowerCase().includes(keyword)
  )
})

// 切换表展开
const toggleTable = (tableName: string) => {
  const newSet = new Set(expandedTables.value)
  if (newSet.has(tableName)) {
    newSet.delete(tableName)
  } else {
    newSet.add(tableName)
  }
  expandedTables.value = newSet
}

const emit = defineEmits<{
  newConversation: []
  firstMessageComplete: [conversationId: string]
  datasourceChange: [datasourceId: string]
  selectConversation: [conversationId: string]
  analysisPanelChange: [open: boolean]
  switchToConversation: []
  switchToAnalysis: []
  'update:selectedMemberDatasourceIds': [value: string[]]
}>()

// ===== 分析面板状态（内部管理） =====
const isAnalysisPanelOpen = ref(false)
const analysisPanelData = ref<AnalysisData | null>(null)
const currentAnalysisRoundId = ref<string | null>(null)
const analysisStreaming = ref(false)
const analysisPanelWidth = ref(40) // 百分比
const isDraggingDivider = ref(false)
const containerRef = ref<HTMLElement | null>(null)
const isAnalysisVisible = computed(() => isAnalysisPanelOpen.value && props.showAnalysisPanel)
const loadingParticles = Array.from({ length: 30 }, (_, index) => ({
  id: index,
  top: Math.random() * 112,
  duration: 2.6 + Math.random() * 2.2,
  delay: Math.random() * 2,
  drift: Math.random() * 112
}))

// 打开分析面板
const handleOpenAnalysisPanel = (data: AnalysisData) => {
  if (isAnalysisPanelOpen.value && analysisPanelData.value?.roundId === data.roundId) {
    // 点击同一个，关闭面板（非流式时才允许 toggle）
    if (!analysisStreaming.value) {
      isAnalysisPanelOpen.value = false
      analysisPanelData.value = null
      currentAnalysisRoundId.value = null
      emit('analysisPanelChange', false)
      return
    }
  }
  analysisPanelData.value = data
  currentAnalysisRoundId.value = data.roundId
  isAnalysisPanelOpen.value = true
  emit('analysisPanelChange', true)
  // 如果数据为空（流式开始），标记为流式中
  if (!data.thinkingContent && data.toolCalls.length === 0) {
    analysisStreaming.value = true
  }
}

// 实时更新分析面板（流式中）
const handleUpdateAnalysisPanel = (data: AnalysisData) => {
  analysisPanelData.value = data
  currentAnalysisRoundId.value = data.roundId
  analysisStreaming.value = true
}

// 流式结束标记
const handleAnalysisStreamEnd = () => {
  analysisStreaming.value = false
}

// 关闭分析面板
const handleCloseAnalysisPanel = () => {
  isAnalysisPanelOpen.value = false
  analysisPanelData.value = null
  currentAnalysisRoundId.value = null
  analysisStreaming.value = false
  emit('analysisPanelChange', false)
}

// 分析面板拖拽分隔条
const startDraggingDivider = (e: MouseEvent) => {
  e.preventDefault()
  isDraggingDivider.value = true
  document.body.classList.add('is-resizing')
  document.addEventListener('mousemove', handleDividerDrag)
  document.addEventListener('mouseup', stopDraggingDivider)
}

const handleDividerDrag = (e: MouseEvent) => {
  if (!isDraggingDivider.value || !containerRef.value) return
  const containerRect = containerRef.value.getBoundingClientRect()
  const containerWidth = containerRect.width
  const mouseX = e.clientX - containerRect.left
  const mainWidthPercent = (mouseX / containerWidth) * 100
  let panelWidth = 100 - mainWidthPercent
  panelWidth = Math.max(20, Math.min(60, panelWidth))
  analysisPanelWidth.value = panelWidth
}

const stopDraggingDivider = () => {
  isDraggingDivider.value = false
  document.body.classList.remove('is-resizing')
  document.removeEventListener('mousemove', handleDividerDrag)
  document.removeEventListener('mouseup', stopDraggingDivider)
}

onUnmounted(() => {
  document.body.classList.remove('is-resizing')
  document.removeEventListener('mousemove', handleDividerDrag)
  document.removeEventListener('mouseup', stopDraggingDivider)
})

// 隐藏的工具
const HIDDEN_TOOLS = ['load_skill_tool']

// 状态
const messages = ref<Message[]>([])
const input = ref('')
const internalDatasourceId = ref('')
const isGenerating = ref(false)
const inputFocused = ref(false)

// 模型选择
const modelOptions = [
  { id: 'qwen3-30b', label: 'Qwen3-30B', value: 'Qwen/Qwen3-30B-A3B-Instruct-2507' },
  { id: 'deepseek-r1', label: 'DeepSeek-R1', value: 'deepseek-r1-distill-qwen-32b' },
]
const selectedModel = ref('qwen3-30b')
const toolCallsMap = ref<Map<string, ToolCallInfo>>(new Map())
// 追踪 dsl2sql_tool 调用，用于关联语义参数
const dsl2sqlToolCalls = ref<ToolCallInfo[]>([])

// 保存到看板弹窗
const showSaveModal = ref(false)
const saveSql = ref('')
const saveChartType = ref('table')
const handleSaveToDashboard = (sql: string, chartType: string) => {
  saveSql.value = sql
  saveChartType.value = chartType
  showSaveModal.value = true
}

// 收藏
const conversationFavoriteRefreshKey = ref(0)
const showFavoriteModal = ref(false)
const favoriteTitle = ref('')
const favoriteContent = ref('')

const openFavoriteModalFromQuestion = (question: string) => {
  if (!selectedDatasource.value) {
    ElMessage.warning('请先选择数据源')
    return
  }
  const content = (question || '').trim()
  if (!content) return
  favoriteTitle.value = content.slice(0, 40)
  favoriteContent.value = content
  showFavoriteModal.value = true
}

const handleFavoriteSaved = () => {
  conversationFavoriteRefreshKey.value++
}
// 会话历史加载
const conversationIdRef = toRef(props, 'conversationId')
const { isLoadingHistory, resetHistory } = useConversationHistory({
  conversationId: conversationIdRef,
  messages,
})

// 监听 conversationId 变化，清空消息以便重新加载历史
watch(
  () => props.conversationId,
  (newId, oldId) => {
    if (newId !== oldId) {
      // 切换会话时，如果是新会话（null），清空消息
      if (newId === null) {
        messages.value = []
        toolCallsMap.value.clear()
        resetHistory()
      }
    }
  }
)

// 使用外部传入的数据源 ID 或内部状态
const selectedDatasource = computed({
  get: () => props.selectedDatasourceId ?? internalDatasourceId.value,
  set: (val) => {
    if (props.selectedDatasourceId !== undefined) {
      emit('datasourceChange', val)
    } else {
      internalDatasourceId.value = val
    }
  }
})

const currentDatasource = computed(() =>
  props.datasources.find(item => item.id === selectedDatasource.value) || props.datasource || null
)

const localExecutionTargets = computed<ExecutionTargetOption[]>(() => {
  const datasource = currentDatasource.value
  const members = datasource?.member_sources || []
  if (members.length) {
    return members
      .filter(member => member.member_datasource_id)
      .map(member => ({
        member_datasource_id: String(member.member_datasource_id),
        datasource_name: member.datasource_name || '',
        source_type: member.source_type || datasource?.source_type || '',
        host: member.host,
        port: member.port,
        database: member.database,
        label: member.datasource_name || `${member.host}:${member.port}/${member.database}`,
      }))
  }
  if (!selectedDatasource.value || !datasource) return []
  return [{
    member_datasource_id: datasource.template_datasource_id || selectedDatasource.value,
    datasource_name: datasource.ds_name || selectedDatasource.value,
    source_type: datasource.source_type || '',
    host: datasource.source_config?.host,
    port: datasource.source_config?.port,
    database: datasource.source_config?.database,
    label: datasource.ds_name || selectedDatasource.value,
  }]
})

const executionTargets = computed<ExecutionTargetOption[]>(() => props.executionTargets ?? localExecutionTargets.value)
const internalSelectedMemberDatasourceIds = ref<string[]>([])
const normalizeSelectedMemberDatasourceIds = (value: string[]) => (
  props.executionTargetMultiple ? value : value.slice(0, 1)
)
const isSameStringArray = (left: string[], right: string[]) => {
  if (left.length !== right.length) return false
  return left.every((item, index) => item === right[index])
}
const selectedMemberDatasourceIds = computed({
  get: () => props.selectedMemberDatasourceIds ?? internalSelectedMemberDatasourceIds.value,
  set: (value: string[]) => {
    const normalizedValue = normalizeSelectedMemberDatasourceIds(value)
    if (props.selectedMemberDatasourceIds !== undefined) {
      if (isSameStringArray(normalizedValue, props.selectedMemberDatasourceIds)) return
      emit('update:selectedMemberDatasourceIds', normalizedValue)
      return
    }
    if (isSameStringArray(normalizedValue, internalSelectedMemberDatasourceIds.value)) return
    internalSelectedMemberDatasourceIds.value = normalizedValue
  }
})

const syncSelectedExecutionTargets = () => {
  const availableIds = executionTargets.value.map(item => item.member_datasource_id)
  if (!availableIds.length) {
    selectedMemberDatasourceIds.value = []
    return
  }
  const retainedIds = selectedMemberDatasourceIds.value.filter(id => availableIds.includes(id))
  if (retainedIds.length > 0) {
    selectedMemberDatasourceIds.value = normalizeSelectedMemberDatasourceIds(retainedIds)
    return
  }
  const templateId = currentDatasource.value?.template_datasource_id
  if (templateId && availableIds.includes(templateId)) {
    selectedMemberDatasourceIds.value = [templateId]
    return
  }
  selectedMemberDatasourceIds.value = [availableIds[0]]
}

watch(
  () => [
    selectedDatasource.value,
    currentDatasource.value?.template_datasource_id,
    executionTargets.value.map(item => item.member_datasource_id).join(','),
  ],
  () => {
    syncSelectedExecutionTargets()
  },
  { immediate: true }
)

// 引用
const chatContainerRef = ref<HTMLDivElement | null>(null)
const textareaRef = ref<HTMLTextAreaElement | null>(null)

// SQL 生成器
const { generateSqlStream, stopGeneration } = useSqlGenerator()

// 初始化数据源
watch(
  () => props.datasources,
  (ds) => {
    if (!selectedDatasource.value && ds.length > 0) {
      const defaultDs = ds.find((d) => d.is_default)
      const firstDs = ds[0]
      selectedDatasource.value = defaultDs?.id || firstDs?.id || ''
    }
  },
  { immediate: true }
)

// 自动滚动
const isNearBottom = ref(true)

const handleScroll = () => {
  const container = chatContainerRef.value
  if (!container) return
  const { scrollTop, scrollHeight, clientHeight } = container
  isNearBottom.value = scrollHeight - scrollTop - clientHeight < 100
}

watch(
  messages,
  () => {
    if (isNearBottom.value) {
      nextTick(() => {
        if (chatContainerRef.value) {
          chatContainerRef.value.scrollTop = chatContainerRef.value.scrollHeight
        }
      })
    }
  },
  { deep: true }
)

// 自适应输入框高度
watch(input, () => {
  nextTick(() => {
    if (textareaRef.value) {
      textareaRef.value.style.height = 'auto'
      textareaRef.value.style.height = `${Math.min(textareaRef.value.scrollHeight, 200)}px`
    }
  })
})

// 处理 LangGraph 消息
const handleLangGraphMessage = (msg: LangGraphMessage, messageId: string) => {
  // 处理工具调用
  if (msg.tool_calls && msg.tool_calls.length > 0) {
    for (const tc of msg.tool_calls) {
      if (tc.id) {
        const existingInfo = toolCallsMap.value.get(tc.id)
        const mergedArgs = existingInfo ? { ...existingInfo.args, ...(tc.args || {}) } : tc.args || {}
        const toolName = tc.name || existingInfo?.tool_name || ''
        if (!toolName) continue

        // 如果是 dsl2sql_tool，提取语义参数
        let semanticParams = existingInfo?.semanticParams
        if (toolName === 'dsl2sql_tool' && mergedArgs) {
          semanticParams = {
            metrics: mergedArgs.metrics as string[] | undefined,
            dimensions: mergedArgs.dimensions as SemanticParams['dimensions'],
            filters: mergedArgs.filters as SemanticParams['filters'],
            having: mergedArgs.having as SemanticParams['having'],
            order_by: mergedArgs.order_by as SemanticParams['order_by'],
            limit: mergedArgs.limit as number | undefined,
          }
        }

        const toolCallInfo: ToolCallInfo = {
          tool_call_id: tc.id,
          tool_name: toolName,
          args: mergedArgs,
          status: 'generating',
          result: existingInfo?.result,
          semanticParams,
          visualization: mergedArgs.visualization as ToolCallInfo['visualization'],
        }
        toolCallsMap.value.set(tc.id, toolCallInfo)

        // 追踪 dsl2sql_tool 调用
        if (toolName === 'dsl2sql_tool') {
          dsl2sqlToolCalls.value.push(toolCallInfo)
        }

        // 更新消息中的工具调用
        if (!HIDDEN_TOOLS.includes(toolName)) {
          const msgIndex = messages.value.findIndex((m) => m.id === messageId)
          if (msgIndex >= 0) {
            const m = messages.value[msgIndex]
            if (m) {
              const existingToolCalls = m.toolCalls || []
              const existsInMsg = existingToolCalls.find((t) => t.tool_call_id === tc.id)
              if (existsInMsg) {
                m.toolCalls = existingToolCalls.map((t) => (t.tool_call_id === tc.id ? toolCallInfo : t))
              } else {
                m.toolCalls = [...existingToolCalls, toolCallInfo]
              }
            }
          }
        }
      }
    }
  }

  // 处理工具消息（结果）
  if (msg.type === 'tool' && msg.tool_call_id && msg.content) {
    const toolInfo = toolCallsMap.value.get(msg.tool_call_id)
    if (toolInfo) {
      let renderId: string | undefined
      let renderType: 'queryResult' | 'chart' | undefined
      let semanticParams = toolInfo.semanticParams
      let visualization = toolInfo.visualization

      // 从 accumulated_args 获取完整参数（流式模式下后端累积的完整参数）
      const accumulatedArgs = msg.accumulated_args
      if (accumulatedArgs) {
        // 更新工具调用的 args
        toolInfo.args = { ...toolInfo.args, ...accumulatedArgs }

        // 如果是 dsl2sql_tool，从 accumulated_args 提取语义参数和 visualization
        if (toolInfo.tool_name === 'dsl2sql_tool') {
          semanticParams = {
            metrics: accumulatedArgs.metrics as string[] | undefined,
            dimensions: accumulatedArgs.dimensions as SemanticParams['dimensions'],
            filters: accumulatedArgs.filters as SemanticParams['filters'],
            having: accumulatedArgs.having as SemanticParams['having'],
            order_by: accumulatedArgs.order_by as SemanticParams['order_by'],
            limit: accumulatedArgs.limit as number | undefined,
          }
          visualization = accumulatedArgs.visualization as ToolCallInfo['visualization']

          // 更新 dsl2sqlToolCalls 中对应的记录
          const dslIndex = dsl2sqlToolCalls.value.findIndex(t => t.tool_call_id === msg.tool_call_id)
          if (dslIndex >= 0) {
            dsl2sqlToolCalls.value[dslIndex] = {
              ...dsl2sqlToolCalls.value[dslIndex]!,
              semanticParams,
              visualization,
              args: toolInfo.args,
            }
          }
        }
      }

      if (toolInfo.tool_name === 'execute_sql_tool') {
        const queryResultMatch = msg.content.match(/<query_result>([\s\S]*?)<\/query_result>/)
        const queryResultId = queryResultMatch?.[1]
        if (queryResultId) {
          renderId = queryResultId.trim()
          renderType = 'queryResult'
        }

        // 从对应的 dsl2sql_tool 获取语义参数
        // 策略：按顺序匹配（第 N 个 execute_sql_tool 对应第 N 个 dsl2sql_tool）
        const executeSqlTools = Array.from(toolCallsMap.value.values())
          .filter(t => t.tool_name === 'execute_sql_tool')
        const currentIndex = executeSqlTools.findIndex(t => t.tool_call_id === msg.tool_call_id)
        if (currentIndex >= 0 && currentIndex < dsl2sqlToolCalls.value.length) {
          const dsl2sqlTool = dsl2sqlToolCalls.value[currentIndex]
          if (dsl2sqlTool) {
            semanticParams = dsl2sqlTool.semanticParams
            visualization = dsl2sqlTool.visualization
          }
        }
      } else if (toolInfo.tool_name === 'generate_chart_tool') {
        const chartMatch = msg.content.match(/<chart_config>([^<]+)<\/chart_config>/)
        const chartId = chartMatch?.[1]
        if (chartId) {
          const id = chartId.trim()
          if (/^[a-f0-9-]{36}$/.test(id)) {
            renderId = id
            renderType = 'chart'
          }
        }
      }

      const updatedToolInfo: ToolCallInfo = {
        ...toolInfo,
        result: msg.content,
        status: 'completed',
        renderId,
        renderType,
        semanticParams,
        visualization,
      }
      toolCallsMap.value.set(msg.tool_call_id, updatedToolInfo)

      // 更新消息中的工具调用
      if (!HIDDEN_TOOLS.includes(toolInfo.tool_name)) {
        const msgIndex = messages.value.findIndex((m) => m.id === messageId)
        if (msgIndex >= 0) {
          const m = messages.value[msgIndex]
          if (m && m.toolCalls) {
            m.toolCalls = m.toolCalls.map((t) =>
              t.tool_call_id === msg.tool_call_id ? updatedToolInfo : t
            )
          }
        }
      }
    }
    return
  }

  // 处理普通 AI 消息内容
  if (msg.content) {
    const msgIndex = messages.value.findIndex((m) => m.id === messageId)
    if (msgIndex >= 0) {
      const m = messages.value[msgIndex]
      if (m) {
        m.content += msg.content
      }
    }
  }

  // 实时更新分析面板（仅当有思考内容或工具调用时才打开/更新）
  const currentMsg = messages.value.find(m => m.id === messageId)
  if (currentMsg?.isStreaming) {
    const thinkingContent = extractThinkingContent(currentMsg.content)
    const visibleToolCalls = getVisibleToolCalls(currentMsg.toolCalls)
    if (thinkingContent || visibleToolCalls.length > 0) {
      const data = { roundId: messageId, thinkingContent, toolCalls: visibleToolCalls, textContent: '' }
      // 首次检测到分析数据时自动打开面板
      if (!isAnalysisPanelOpen.value || currentAnalysisRoundId.value !== messageId) {
        handleOpenAnalysisPanel(data)
      } else {
        handleUpdateAnalysisPanel(data)
      }
    }
  }
}

// 发送消息
const handleSubmit = async () => {
  if (!input.value.trim() || isGenerating.value || !selectedDatasource.value) return
  if (executionTargets.value.length > 0 && selectedMemberDatasourceIds.value.length === 0) {
    ElMessage.warning('请选择执行对象')
    return
  }

  const queryText = input.value.trim()

  // 先推入消息，防止 ensureConversationId 触发历史加载时 messages 为空
  const userMessage: Message = {
    id: `user-${Date.now()}`,
    role: 'user',
    content: queryText,
    timestamp: new Date()
  }

  const assistantMessageId = `assistant-${Date.now()}`
  const assistantMessage: Message = {
    id: assistantMessageId,
    role: 'assistant',
    content: '',
    timestamp: new Date(),
    isStreaming: true,
    toolCalls: []
  }

  messages.value.push(userMessage, assistantMessage)
  input.value = ''
  isGenerating.value = true
  toolCallsMap.value.clear()
  dsl2sqlToolCalls.value = []

  // 新一轮开始，关闭上一轮的分析面板
  handleCloseAnalysisPanel()

  // 确保有会话 ID（懒创建模式）
  let convId = props.conversationId
  if (!convId && props.ensureConversationId) {
    convId = await props.ensureConversationId()
  }
  if (!convId) {
    ElMessage.error('无法创建会话')
    // 回滚消息
    messages.value = messages.value.filter(m => m.id !== userMessage.id && m.id !== assistantMessageId)
    isGenerating.value = false
    input.value = queryText
    return
  }

  try {
    await generateSqlStream(
      queryText,
      true,
      selectedDatasource.value,
      convId,
      'query',
      // onMessage
      (msg) => handleLangGraphMessage(msg, assistantMessageId),
      // onResult
      () => {
        const msgIndex = messages.value.findIndex((m) => m.id === assistantMessageId)
        if (msgIndex >= 0 && messages.value[msgIndex]) {
          const msg = messages.value[msgIndex]!
          msg.isStreaming = false
          // 发送最终的分析数据并标记流式结束
          handleUpdateAnalysisPanel({
            roundId: assistantMessageId,
            thinkingContent: extractThinkingContent(msg.content),
            toolCalls: getVisibleToolCalls(msg.toolCalls),
            textContent: ''
          })
          handleAnalysisStreamEnd()
        }
        emit('firstMessageComplete', convId)
      },
      // onStatus
      () => {},
      // onError
      (error) => {
        const msgIndex = messages.value.findIndex((m) => m.id === assistantMessageId)
        if (msgIndex >= 0 && messages.value[msgIndex]) {
          messages.value[msgIndex]!.error = error
          messages.value[msgIndex]!.isStreaming = false
        }
        handleAnalysisStreamEnd()
      },
      // onPendingTool
      () => {},
      // onRunningTool
      (toolInfo) => {
        if (toolInfo.tool_call_id) {
          const existing = toolCallsMap.value.get(toolInfo.tool_call_id)
          if (existing) {
            const updatedInfo: ToolCallInfo = { ...existing, status: 'running' }
            toolCallsMap.value.set(toolInfo.tool_call_id, updatedInfo)

            // 更新消息中的工具调用状态
            const msgIndex = messages.value.findIndex((m) => m.id === assistantMessageId)
            if (msgIndex >= 0) {
              const m = messages.value[msgIndex]
              if (m && m.toolCalls) {
                m.toolCalls = m.toolCalls.map((t) =>
                  t.tool_call_id === toolInfo.tool_call_id ? updatedInfo : t
                )
              }
            }
          }
        }
      },
      // semanticConfigId - query 模式必须传明确的语义配置 ID
      props.semanticConfigId || null,
      // modelId - 选中模型的 value
      modelOptions.find(m => m.id === selectedModel.value)?.value || null,
      selectedMemberDatasourceIds.value
    )
  } catch (err) {
    console.error('生成失败:', err)
    const msgIndex = messages.value.findIndex((m) => m.id === assistantMessageId)
    if (msgIndex >= 0 && messages.value[msgIndex]) {
      messages.value[msgIndex]!.error = '生成失败，请重试'
      messages.value[msgIndex]!.isStreaming = false
    }
    handleAnalysisStreamEnd()
  } finally {
    isGenerating.value = false
  }
}

// 停止生成
const handleStop = () => {
  stopGeneration()
  // 标记当前流式消息为结束
  const streamingMsg = messages.value.find(m => m.isStreaming)
  if (streamingMsg) {
    streamingMsg.isStreaming = false
  }
  isGenerating.value = false
  handleAnalysisStreamEnd()
}

// 键盘事件
const handleKeyDown = (e: KeyboardEvent) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSubmit()
  }
}

// 常用推荐点击
const handleQuickQuerySelect = (query: string) => {
  input.value = query
  nextTick(() => textareaRef.value?.focus())
}

// 过滤可见的工具调用
const getVisibleToolCalls = (toolCalls?: ToolCallInfo[]): ToolCallInfo[] => {
  if (!toolCalls) return []
  return toolCalls.filter((tc) => !HIDDEN_TOOLS.includes(tc.tool_name))
}

// 提取 thinking 内容（支持流式中未闭合的 thinking 标签）
const extractThinkingContent = (content: string): string => {
  // 优先匹配完整闭合的 thinking 块
  const completeMatch = content.match(/<thinking>([\s\S]*?)<\/thinking>/)
  if (completeMatch) return `<thinking>${completeMatch[1]}</thinking>`
  // 流式中：匹配已打开但未闭合的 thinking 标签
  const incompleteMatch = content.match(/<thinking>([\s\S]*)$/)
  if (incompleteMatch) return `<thinking>${incompleteMatch[1]}</thinking>`
  return ''
}

// 去除 thinking 内容（主对话区不显示思考过程）
const stripThinkingContent = (content: string): string => {
  return content.replace(/<thinking>[\s\S]*?<\/thinking>/g, '').trim()
}

// 检查是否有已完成的查询结果
const hasCompletedQueryResult = (toolCalls?: ToolCallInfo[]): boolean => {
  if (!toolCalls) return false
  return toolCalls.some(
    (tc) => tc.tool_name === 'execute_sql_tool' && tc.status === 'completed' && tc.renderId
  )
}

// 获取已完成的查询结果工具调用
const getCompletedQueryResults = (toolCalls?: ToolCallInfo[]): ToolCallInfo[] => {
  if (!toolCalls) return []
  return toolCalls.filter(
    (tc) => tc.tool_name === 'execute_sql_tool' && tc.status === 'completed' && tc.renderId
  )
}

// 判断消息是否应该显示简洁模式（隐藏思考和工具调用，只显示结果）
const shouldShowCompactMode = (msg: Message): boolean => {
  // 查询结果一旦产出，立即进入简洁模式（支持总结流式更新）
  if (hasCompletedQueryResult(msg.toolCalls)) return true
  // 纯流式文本阶段不使用简洁模式
  if (msg.isStreaming) return false
  // 有工具调用时使用简洁模式（无论成功或失败，工具过程都在分析面板展示）
  if (getVisibleToolCalls(msg.toolCalls).length > 0) return true
  return false
}

// 提取文字总结（移除技术标签内容）
const getSummaryText = (content: string): string => {
  if (!content) return ''
  let text = content
  // 移除所有技术标签和内容
  text = text.replace(/<thinking>[\s\S]*?<\/thinking>/g, '')
  text = text.replace(/<query_result>[\s\S]*?<\/query_result>/g, '')
  text = text.replace(/<chart_config>[\s\S]*?<\/chart_config>/g, '')
  // text = text.replace(/查询结果已生成[。.]?/g, '') // 已改为输出简要说明
  return text.trim()
}

const renderSummaryMarkdown = (content: string) => {
  return md.render(content || '')
}

// 创建分析数据
const createAnalysisData = (msg: Message): AnalysisData | null => {
  const visibleToolCalls = getVisibleToolCalls(msg.toolCalls)
  if (!hasCompletedQueryResult(msg.toolCalls)) return null

  const thinkingContent = extractThinkingContent(msg.content)
  return {
    roundId: msg.id,
    thinkingContent,
    toolCalls: visibleToolCalls,
    textContent: ''
  }
}

// 获取流式阶段文案
const getStreamingStageText = (msg: Message): string => {
  const toolCalls = getVisibleToolCalls(msg.toolCalls)
  if (toolCalls.some(tc => tc.tool_name === 'execute_sql_tool' && tc.status === 'running')) {
    return '正在查询数据...'
  }
  if (toolCalls.some(tc => tc.tool_name === 'execute_sql_tool')) {
    return '正在执行查询...'
  }
  if (toolCalls.length > 0) {
    return '正在生成查询...'
  }
  return '正在理解您的问题...'
}

// 打开分析面板
const handleViewAnalysis = (msg: Message) => {
  const analysisData = createAnalysisData(msg)
  if (analysisData) {
    handleOpenAnalysisPanel(analysisData)
  }
}
</script>

<template>
  <div class="flex flex-row h-full bg-gradient-to-br from-slate-50 to-slate-100">
    <!-- 左侧：会话列表面板 -->
    <div v-if="!props.hideSidebar" class="w-60 flex flex-col bg-white border-r border-black/[0.06]">
      <!-- 数据源选择（悬浮显示） -->
      <div class="relative" @mouseenter="isDatasourceHovered = true" @mouseleave="isDatasourceHovered = false">
        <div :class="[
          'overflow-hidden transition-all duration-300 bg-gradient-to-br from-white/95 to-gray-50/95 backdrop-blur-[10px] border-b border-black/[0.06]',
          isDatasourceHovered ? 'max-h-[60px] opacity-100 px-3 py-2.5' : 'max-h-0 opacity-0'
        ]">
          <div class="flex items-center gap-2 px-2.5 py-1.5 bg-[#1f82d9]/[0.05] rounded-lg border border-[#1f82d9]/10">
            <i class="fa-solid fa-database text-xs text-[#1f82d9]"></i>
            <select v-model="selectedDatasource" class="flex-1 border-none bg-transparent text-[13px] text-gray-700 outline-none cursor-pointer">
              <option v-for="ds in datasources" :key="ds.id" :value="ds.id">
                {{ ds.ds_name }}
              </option>
            </select>
          </div>
        </div>
        <div :class="[
          'flex justify-center py-1 transition-all duration-300',
          isDatasourceHovered && 'opacity-0 h-0 py-0'
        ]">
          <div class="w-9 h-1 bg-gradient-to-b from-gray-400 to-gray-300 rounded-b transition-transform duration-200 hover:scale-x-110"></div>
        </div>
      </div>

      <!-- 新建会话按钮 -->
      <div class="p-3 border-b border-black/[0.06]">
        <button class="w-full flex items-center justify-center gap-2 px-4 py-2.5 border-none rounded-lg bg-gradient-to-br from-[#1f82d9] to-[#135589] text-white text-[13px] font-medium cursor-pointer transition-all duration-200 hover:-translate-y-px hover:shadow-[0_4px_12px_rgba(31,130,217,0.3)]" @click="emit('newConversation')">
          <i class="fa-solid fa-plus"></i>
          <span>新建会话</span>
        </button>
      </div>

      <!-- 会话列表 -->
      <div class="flex-1 overflow-y-auto p-2">
        <template v-if="groupedConversations.length > 0">
          <div v-for="group in groupedConversations" :key="group.label" class="mb-3">
            <div class="px-2 py-1 text-[11px] text-gray-400 font-medium">{{ group.label }}</div>
            <div
              v-for="conv in group.items"
              :key="conv.id"
              :class="[
                'flex items-center gap-2.5 px-3 py-2.5 rounded-lg cursor-pointer transition-all duration-150',
                conversationId === conv.id ? 'bg-gradient-to-br from-[#1f82d9]/10 to-[#1f82d9]/[0.05]' : 'hover:bg-black/[0.04]'
              ]"
              @click="emit('selectConversation', conv.id)"
            >
              <i :class="['fa-regular fa-message text-sm', conversationId === conv.id ? 'text-[#1f82d9]' : 'text-gray-400']"></i>
              <span :class="['flex-1 text-[13px] overflow-hidden text-ellipsis whitespace-nowrap', conversationId === conv.id ? 'text-[#1f82d9] font-medium' : 'text-gray-700']">{{ conv.title }}</span>
            </div>
          </div>
        </template>

        <div v-else-if="!isLoadingConversations" class="flex flex-col items-center justify-center gap-2 p-8 text-gray-400 text-[13px]">
          <i class="fa-regular fa-comments"></i>
          <span>暂无会话</span>
        </div>
      </div>
    </div>

    <!-- 右侧：对话区域 + 分析面板 -->
    <div ref="containerRef" class="flex-1 flex min-w-0 overflow-hidden">
      <div
        class="flex flex-col min-w-0 overflow-hidden bg-gray-50"
        :style="{ flex: isAnalysisVisible ? `0 0 ${100 - analysisPanelWidth}%` : '1 1 auto' }"
      >
      <!-- 顶部工具栏 -->
      <div v-if="!hideHeader" class="flex items-center justify-between h-13 px-4 shrink-0">
      <div class="w-[200px]"></div>
      <h2 :class="[
        'text-sm font-medium max-w-[300px] overflow-hidden text-ellipsis whitespace-nowrap',
        conversationTitle && conversationTitle !== '新会话' ? 'text-gray-800' : 'text-gray-400'
      ]">
        {{ conversationTitle || '新会话' }}
      </h2>
      <div class="flex items-center gap-1.5 w-[200px] justify-end">
        <button class="flex items-center gap-1.5 h-7 px-2.5 border border-gray-200 rounded-md bg-white text-xs text-gray-600 cursor-pointer transition-all duration-200 hover:border-gray-300 hover:bg-gray-50" @click="emit('newConversation')">
          <i class="fa-solid fa-plus"></i>
          <span>新建会话</span>
        </button>
        <div v-if="!hideDatasourceSelector">
          <select v-model="selectedDatasource" class="h-7 px-2 border border-gray-200 rounded-md bg-white text-xs text-gray-600 outline-none">
            <option v-for="ds in datasources" :key="ds.id" :value="ds.id">
              {{ ds.ds_name }}
            </option>
          </select>
        </div>
      </div>
    </div>

    <!-- 消息区域 -->
    <div
      ref="chatContainerRef"
      :class="[
        'flex-1 overflow-y-auto p-4',
        messages.length === 0 && 'flex flex-col'
      ]"
      @scroll="handleScroll"
    >
      <!-- 加载历史中 -->
      <div v-if="isLoadingHistory" class="flex-1 flex flex-col items-center justify-center gap-3 text-gray-500 text-sm">
        <i class="fa-solid fa-spinner fa-spin text-2xl text-blue-500"></i>
        <span>加载历史消息...</span>
      </div>
      <!-- 无消息时：欢迎界面 -->
      <div v-else-if="messages.length === 0" class="flex-1 flex items-start justify-center pt-[10%]">
        <div class="w-[774px] max-w-[90%] text-center">
          <h3 class="text-xl font-semibold text-gray-800 m-0 mb-2">您好，有什么可以帮您？</h3>
          <!-- 输入框 -->
          <form class="relative" @submit.prevent="handleSubmit">
            <div
              :class="[
                'rounded-2xl bg-white border border-black/[0.08] shadow-[0_2px_12px_rgba(0,0,0,0.06),0_0_0_1px_rgba(0,0,0,0.02)] transition-all duration-200',
                inputFocused && 'border-blue-400/40 shadow-[0_2px_12px_rgba(0,0,0,0.06),0_0_0_3px_rgba(0,122,255,0.1)]'
              ]"
            >
              <div class="flex items-start min-h-[54px] px-4 pt-4 pb-2">
                <textarea
                  ref="textareaRef"
                  v-model="input"
                  placeholder="请输入问题查询数据"
                  class="w-full min-h-[26px] max-h-[200px] border-none bg-transparent text-[15px] leading-relaxed text-gray-700 resize-none outline-none overflow-y-hidden placeholder:text-gray-400"
                  @keydown="handleKeyDown"
                  @focus="inputFocused = true"
                  @blur="inputFocused = false"
                ></textarea>
              </div>
              <div class="flex items-center justify-between gap-2 px-4 py-2 pb-3">
                <div class="flex items-center gap-2">
                  <div class="flex items-center gap-1.5 px-2 py-1 rounded-full" style="background: rgba(0,0,0,0.03)">
                    <i class="fa-solid fa-microchip text-[10px] text-gray-400"></i>
                    <select
                      v-model="selectedModel"
                      class="border-none bg-transparent text-xs text-gray-500 outline-none cursor-pointer appearance-none pr-3"
                    >
                      <option v-for="m in modelOptions" :key="m.id" :value="m.id">{{ m.label }}</option>
                    </select>
                    <i class="fa-solid fa-chevron-down text-[8px] text-gray-400 -ml-2"></i>
                  </div>
                  <ExecutionTargetPicker
                    v-if="!hideExecutionTargetSelector && executionTargets.length > 0"
                    v-model="selectedMemberDatasourceIds"
                    :targets="executionTargets"
                    :multiple="executionTargetMultiple"
                  />
                </div>
                <button
                  type="submit"
                  :class="[
                    'w-8 h-8 flex items-center justify-center border-none rounded-full bg-gradient-to-br from-[#1677FF] to-[#1677FF] text-white shadow-[0_2px_8px_rgba(0,122,255,0.35)] cursor-pointer transition-all duration-200',
                    (isGenerating || !input.trim() || !selectedDatasource) ? 'bg-gray-200 shadow-none cursor-not-allowed text-gray-400' : 'hover:scale-105 active:scale-95'
                  ]"
                  :disabled="isGenerating || !input.trim() || !selectedDatasource"
                >
                  <i class="fa-solid fa-arrow-up"></i>
                </button>
              </div>
            </div>
          </form>

          <!-- 常用推荐 -->
          <QuickQueries
            v-if="selectedDatasource"
            :datasource-id="selectedDatasource"
            mode="conversation"
            :refresh-trigger="conversationFavoriteRefreshKey"
            :max-items="8"
            @select-query="handleQuickQuerySelect"
          />
        </div>
      </div>

      <!-- 有消息时：对话列表 -->
      <div v-else class="max-w-[774px] mx-auto w-[90%]">
        <div v-for="msg in messages" :key="msg.id" class="mb-5">
          <!-- 用户消息 -->
          <div v-if="msg.role === 'user'" class="group flex flex-col items-end gap-1">
            <div class="max-w-[80%] px-4 py-2.5 rounded-2xl rounded-br-md bg-gradient-to-br from-blue-500 to-blue-600 text-white text-sm leading-relaxed shadow-[0_2px_8px_rgba(59,130,246,0.3)]">{{ msg.content }}</div>
            <button
              class="h-6 px-2.5 border border-[#dbe7f5] rounded bg-white text-[12px] text-[#4b5563] cursor-pointer opacity-0 transition-all duration-150 -mt-0.5 group-hover:opacity-100 hover:text-[#1f82d9] hover:border-[#bfdbfe]"
              @click="openFavoriteModalFromQuestion(msg.content)"
            >
              <i class="fa-regular fa-star mr-1"></i>收藏
            </button>
            <div class="text-[10px] text-gray-400 mr-1">
              {{ msg.timestamp.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) }}
            </div>
          </div>

          <!-- AI 消息 -->
          <div v-else class="flex flex-col gap-3">
            <div class="max-w-full p-4 rounded-2xl rounded-bl-md bg-white border border-black/[0.06] shadow-[0_2px_8px_rgba(0,0,0,0.04)]">
              <!-- 简洁模式：查询完成后只显示结果 -->
              <template v-if="shouldShowCompactMode(msg)">
                <!-- 文字总结（在结果之前） -->
                <!-- 查询结果 -->
                <div class="flex flex-col gap-4">
                  <AsyncQueryResult
                    v-for="tc in getCompletedQueryResults(msg.toolCalls)"
                    :key="tc.tool_call_id"
                    class="max-h-[46vh]"
                    :result-data="tc.renderId!"
                    :sql="tc.args?.sql as string"
                    :semantic-params="tc.semanticParams"
                    :visualization="chartRecommendEnabled ? tc.visualization : undefined"
                    :on-view-analysis="() => handleViewAnalysis(msg)"
                    :is-analysis-active="isAnalysisPanelOpen && currentAnalysisRoundId === msg.id"
                    :on-save="handleSaveToDashboard"
                  />
                </div>
                <!-- 文字总结 -->
                <div v-if="getSummaryText(msg.content) || msg.isStreaming" class="mt-3 text-sm text-gray-700 leading-relaxed markdown-body">
                  <span v-html="renderSummaryMarkdown(getSummaryText(msg.content))"></span>
                  <span v-if="msg.isStreaming" class="animate-pulse text-gray-400">▌</span>
                </div>
              </template>

              <!-- 流式中：数据查询显示加载状态，普通问答直接流式输出 -->
              <template v-else-if="msg.isStreaming">
                <!-- 有思考/工具调用 → 数据查询，过程在侧边栏展示 -->
                <div v-if="extractThinkingContent(msg.content) || getVisibleToolCalls(msg.toolCalls).length > 0" class="py-1">
                  <div class="flex items-center gap-2 mb-3">
                    <div class="w-5 h-5 rounded-full bg-gradient-to-br from-blue-500/15 to-blue-400/10 flex items-center justify-center">
                      <i class="fa-solid fa-wand-magic-sparkles text-[10px] text-blue-500 animate-pulse"></i>
                    </div>
                    <span class="text-sm text-gray-500 font-medium">{{ getStreamingStageText(msg) }}</span>
                    <button
                      v-if="isAnalysisPanelOpen && !showAnalysisPanel"
                      class="ml-auto h-6 px-2 border border-blue-200 rounded bg-blue-50 text-[11px] text-blue-600 hover:bg-blue-100 hover:border-blue-300"
                      @click="emit('switchToAnalysis')"
                    >
                      查看分析
                    </button>
                  </div>
                  <div class="px-3 py-2">
                    <div class="particle-storm relative h-[88px] overflow-hidden bg-transparent">
                      <div
                        v-for="particle in loadingParticles"
                        :key="particle.id"
                        class="particle-storm__dot absolute h-1 w-1 rounded-full bg-[#1f82d9]"
                        :style="{
                          top: `${particle.top}px`,
                          left: '-20px',
                          animationDuration: `${particle.duration}s`,
                          animationDelay: `${particle.delay}s`,
                          '--particle-drift': `${particle.drift}px`,
                        }"
                      ></div>
                    </div>
                  </div>
                </div>
                <!-- 普通问答 → 直接流式显示文字 -->
                <StructuredMessage
                  v-else-if="msg.content"
                  :content="msg.content"
                  :is-streaming="true"
                />
                <div v-else class="flex items-center gap-2 text-gray-500 text-sm">
                  <i class="fa-solid fa-spinner fa-spin"></i>
                  <span>正在思考...</span>
                </div>
              </template>

              <!-- 非流式、无查询结果：显示内容（去除思考过程） -->
              <template v-else>
                <StructuredMessage
                  v-if="stripThinkingContent(msg.content)"
                  :content="stripThinkingContent(msg.content)"
                  :is-streaming="false"
                />
                <!-- 全是思考内容、没有正式回复时，显示思考内容作为兜底 -->
                <StructuredMessage
                  v-else-if="msg.content"
                  :content="msg.content"
                  :is-streaming="false"
                />

                <!-- 工具调用列表（在气泡内） -->
                <ToolCallList
                  v-if="getVisibleToolCalls(msg.toolCalls).length > 0"
                  :tool-calls="getVisibleToolCalls(msg.toolCalls)"
                  :datasource-id="selectedDatasource"
                  :on-view-analysis="hasCompletedQueryResult(msg.toolCalls) ? () => handleViewAnalysis(msg) : undefined"
                  :is-analysis-active="isAnalysisPanelOpen && currentAnalysisRoundId === msg.id"
                  class="mt-3 pt-3 border-t border-black/[0.06]"
                />
              </template>

              <!-- 错误消息 -->
              <div v-if="msg.error" class="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-red-500/10 text-red-600 text-[13px] mt-2">
                <i class="fa-solid fa-circle-exclamation"></i>
                <span>{{ msg.error }}</span>
              </div>
            </div>

            <!-- 生成状态（仅普通问答显示，数据查询已在卡片内展示） -->
            <div v-if="msg.isStreaming && !(extractThinkingContent(msg.content) || getVisibleToolCalls(msg.toolCalls).length > 0)" class="flex items-center gap-2 py-1">
              <span class="text-xs font-medium text-gray-500">正在生成</span>
              <div class="flex gap-1">
                <span v-for="i in 3" :key="i" class="w-1.5 h-1.5 rounded-full bg-[#1677FF] animate-pulse" :style="{ animationDelay: `${i * 0.2}s` }"></span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 底部输入区（有消息时显示） -->
    <div v-if="messages.length > 0" class="p-4 bg-gray-50">
      <div class="max-w-[774px] mx-auto w-[90%]">
        <form class="relative" @submit.prevent="handleSubmit">
          <div
            :class="[
              'rounded-2xl bg-white border border-black/[0.08] shadow-[0_2px_12px_rgba(0,0,0,0.06),0_0_0_1px_rgba(0,0,0,0.02)] transition-all duration-200',
              inputFocused && 'border-blue-400/40 shadow-[0_2px_12px_rgba(0,0,0,0.06),0_0_0_3px_rgba(0,122,255,0.1)]'
            ]"
          >
            <div class="flex items-start min-h-[54px] px-4 pt-4 pb-2">
              <textarea
                ref="textareaRef"
                v-model="input"
                placeholder="请输入问题查询数据"
                class="w-full min-h-[26px] max-h-[200px] border-none bg-transparent text-[15px] leading-relaxed text-gray-700 resize-none outline-none overflow-y-hidden placeholder:text-gray-400"
                @keydown="handleKeyDown"
                @focus="inputFocused = true"
                @blur="inputFocused = false"
              ></textarea>
            </div>
            <div class="flex items-center justify-between gap-2 px-4 py-2 pb-3">
              <div class="flex items-center gap-2">
                <div class="flex items-center gap-1.5 px-2 py-1 rounded-full" style="background: rgba(0,0,0,0.03)">
                  <i class="fa-solid fa-microchip text-[10px] text-gray-400"></i>
                  <select
                    v-model="selectedModel"
                    class="border-none bg-transparent text-xs text-gray-500 outline-none cursor-pointer appearance-none pr-3"
                  >
                    <option v-for="m in modelOptions" :key="m.id" :value="m.id">{{ m.label }}</option>
                  </select>
                  <i class="fa-solid fa-chevron-down text-[8px] text-gray-400 -ml-2"></i>
                </div>
                <ExecutionTargetPicker
                  v-if="!hideExecutionTargetSelector && executionTargets.length > 0"
                  v-model="selectedMemberDatasourceIds"
                  :targets="executionTargets"
                  :multiple="executionTargetMultiple"
                />
              </div>
              <!-- 停止按钮（生成中） -->
              <button
                v-if="isGenerating"
                type="button"
                class="relative w-8 h-8 flex items-center justify-center border-none rounded-full bg-gradient-to-br from-[#1677FF] to-[#1677FF] text-white shadow-[0_2px_8px_rgba(0,122,255,0.35)] cursor-pointer transition-all duration-200 animate-breathe hover:scale-105 active:scale-95"
                title="停止生成"
                @click="handleStop"
              >
                <i class="fa-solid fa-stop"></i>
                <span class="absolute inset-0 rounded-full bg-gradient-to-br from-[#1677FF] to-[#1677FF] opacity-40 animate-ping"></span>
              </button>
              <!-- 发送按钮（非生成中） -->
              <button
                v-else
                type="submit"
                :class="[
                  'w-8 h-8 flex items-center justify-center border-none rounded-full bg-gradient-to-br from-[#1677FF] to-[#1677FF] text-white shadow-[0_2px_8px_rgba(0,122,255,0.35)] cursor-pointer transition-all duration-200',
                  (!input.trim() || !selectedDatasource) ? 'bg-gray-200 shadow-none cursor-not-allowed text-gray-400' : 'hover:scale-105 active:scale-95'
                ]"
                :disabled="!input.trim() || !selectedDatasource"
                :title="!selectedDatasource ? '请先选择数据源' : '发送 (Enter)'"
              >
                <i class="fa-solid fa-arrow-up"></i>
              </button>
            </div>
          </div>
        </form>
        <div class="text-center pt-1.5 text-xs text-gray-400">内容由AI生成，请仔细甄别</div>
      </div>
    </div>
      </div>

      <!-- 分析过程面板 -->
      <template v-if="isAnalysisVisible">
        <!-- 拖拽分隔条 -->
        <div
          :class="[
            'w-1.5 flex items-center justify-center bg-gray-100 cursor-col-resize transition-colors duration-200 shrink-0 select-none',
            isDraggingDivider && 'bg-blue-300'
          ]"
          @mousedown="startDraggingDivider"
        >
          <div :class="[
            'w-0.5 h-12 bg-gray-400 rounded-sm transition-colors duration-200',
            isDraggingDivider && 'bg-blue-500'
          ]"></div>
        </div>
        <!-- 面板内容 -->
        <div
          class="min-w-[280px] max-w-[60%] border-l border-gray-200 bg-white overflow-hidden"
          :style="{ flex: `0 0 ${analysisPanelWidth}%` }"
        >
          <AnalysisPanel
            :is-open="isAnalysisPanelOpen"
            :data="analysisPanelData"
            :datasource-id="selectedDatasource"
            :is-streaming="analysisStreaming"
            @switch-conversation="emit('switchToConversation')"
            @close="emit('switchToConversation')"
          />
        </div>
      </template>
    </div>

    <!-- 保存到看板弹窗 -->
    <SaveToPanelModal
      v-model:visible="showSaveModal"
      :sql="saveSql"
      :chart-type="saveChartType"
      :datasource-id="selectedDatasource"
    />
    <QueryFavoriteModal
      v-model:visible="showFavoriteModal"
      mode="conversation"
      :datasource-id="selectedDatasource || ''"
      :semantic-config-id="props.semanticConfigId"
      :initial-title="favoriteTitle"
      :initial-content="favoriteContent"
      @saved="handleFavoriteSaved"
    />
  </div>
</template>

<style scoped>
.markdown-body :deep(p) {
  margin: 0 0 8px 0;
}

.markdown-body :deep(p:last-child) {
  margin-bottom: 0;
}

.markdown-body :deep(strong) {
  font-weight: 700;
}

.markdown-body :deep(code) {
  padding: 1px 6px;
  border-radius: 4px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  background: rgba(15, 23, 42, 0.06);
}

.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  margin: 4px 0;
  padding-left: 18px;
}

.particle-storm__dot {
  opacity: 0;
  animation-name: particle-storm-flow;
  animation-timing-function: linear;
  animation-iteration-count: infinite;
  will-change: transform, opacity;
}

@keyframes particle-storm-flow {
  0% {
    left: -20px;
    transform: translate3d(0, 0, 0) scale(0.5);
    opacity: 0;
  }

  15% {
    opacity: 1;
  }

  50% {
    left: calc(50% - 10px);
    transform: translate3d(0, var(--particle-drift), 0) scale(1.5);
    opacity: 0.95;
  }

  100% {
    left: calc(100% + 20px);
    transform: translate3d(0, 0, 0) scale(0.5);
    opacity: 0;
  }
}
</style>
