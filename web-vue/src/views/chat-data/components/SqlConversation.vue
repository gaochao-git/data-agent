<script setup lang="ts">
/**
 * SQL 对话组件
 * 支持结构化消息和工具调用展示
 */
import { ref, computed, watch, nextTick, toRef, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import MarkdownIt from 'markdown-it'
import type { ChatDataModelOption, ChatDataTokenUsage, ChatDataTokenUsageBreakdown, DataSource, ToolCallInfo, LangGraphMessage, Message, SemanticParams, ExecutionTargetOption, SkillInfo, DashboardSpecPayload, SemanticConfigSelectionInfo, SemanticConfigSelectRequiredInfo } from '../types'
import { createDashboardFromSpec, getChatDataConfig } from '../api'
import type { Dashboard } from '../api/dashboard'
import { useSqlGenerator } from '../composables/useSqlGenerator'
import { useConversationHistory } from '../composables/useConversationHistory'
import { useDeepThinking } from '../composables/useDeepThinking'
import { extractGeneratedSqlFromToolCall, extractSemanticParamsFromArgs } from '../utils/semanticTool'
import StructuredMessage from './StructuredMessage.vue'
import ToolCallList from './ToolCallList.vue'
import AsyncQueryResult from './AsyncQueryResult.vue'
import QuickQueries from './QuickQueries.vue'
import SaveToPanelModal from './SaveToPanelModal.vue'
import QueryFavoriteModal from './QueryFavoriteModal.vue'
import AnalysisPanel from './AnalysisPanel.vue'
import ExecutionTargetPicker from './ExecutionTargetPicker.vue'
import type { AnalysisData } from './AnalysisPanel.vue'
import { hideSemanticConfigSelectionPayloads } from '../utils'

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
  conversationAgentId?: string
  selectedDatasourceId?: string
  hideDatasourceSelector?: boolean
  hideHeader?: boolean
  hideSidebar?: boolean
  // 会话列表相关
  conversations?: ConversationItem[]
  groupedConversations?: ConversationGroup[]
  isLoadingConversations?: boolean
  // 懒创建会话
  ensureConversationId?: (agentId?: string) => Promise<string | null>
  // 语义配置（数据集）ID
  semanticConfigId?: string | null
  semanticConfigSelection?: SemanticConfigSelectionInfo | null
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
  semanticConfigSelection: null,
  showAnalysisPanel: true,
  executionTargetMultiple: true,
  hideExecutionTargetSelector: false,
})

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
  agentChange: [agentId: 'chat_data' | 'chat_data_data_analyze']
  switchToConversation: []
  switchToAnalysis: []
  dashboardCreated: [dashboard: Dashboard]
  semanticConfigSelected: [payload: SemanticConfigSelectionInfo]
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

const hasAnalysisDataContent = (data: AnalysisData | null): boolean => {
  return Boolean(data?.thinkingContent || data?.toolCalls.length || data?.semanticConfigSelection || data?.semanticConfigSelectRequired)
}

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
  if (!hasAnalysisDataContent(data)) {
    analysisStreaming.value = true
  }
}

const handleUpdateAnalysisPanel = (data: AnalysisData, streaming = true) => {
  analysisPanelData.value = data
  currentAnalysisRoundId.value = data.roundId
  analysisStreaming.value = streaming
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
  document.removeEventListener('click', handleAgentSelectorDocumentClick)
})

const HIDDEN_TOOLS = ['load_skill_tool']

// 状态
const messages = ref<Message[]>([])
const input = ref('')
const internalDatasourceId = ref('')
const isGenerating = ref(false)
const inputFocused = ref(false)
const semanticQuestionDraft = ref({
  text: '',
  dimensions: [] as string[],
  metrics: [] as string[],
})

const resetSemanticQuestionDraft = () => {
  semanticQuestionDraft.value = { text: '', dimensions: [], metrics: [] }
}

// 模型选择
const dataAnalyzeEnabled = ref(false)
const agentOptions = computed(() => [
  { id: 'chat_data', label: '智能问数', disabled: false },
  { id: 'chat_data_data_analyze', label: '智能分析', disabled: !dataAnalyzeEnabled.value },
] as const)
const selectedAgent = ref<'chat_data' | 'chat_data_data_analyze'>(
  'chat_data'
)
const isAgentSelectorOpen = ref(false)
const agentSelectorRef = ref<HTMLElement | null>(null)
const recommendationContentType = computed<'skills' | 'queries'>(() =>
  selectedAgent.value === 'chat_data_data_analyze' ? 'skills' : 'queries'
)
const selectedAgentLabel = computed(() => agentOptions.value.find(agent => agent.id === selectedAgent.value)?.label || '智能问数')
const modelOptions = ref<ChatDataModelOption[]>([])
const selectedModel = ref('')
const { enableDeepThinking } = useDeepThinking()
const tokenUsage = ref<ChatDataTokenUsage | null>(null)
const tokenUsagePanelOpen = ref(false)
const tokenUsageSort = ref<'round' | 'desc' | 'asc'>('round')
const expandedTokenMessageKeys = ref<Set<string>>(new Set())
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

const selectedModelValue = computed(() => modelOptions.value.find(m => m.id === selectedModel.value)?.value || selectedModel.value || null)

const formatTokenUsageDisplay = (usage: ChatDataTokenUsage | null) => {
  if (!usage || !Number.isFinite(usage.used) || !Number.isFinite(usage.total) || usage.total <= 0) {
    return ''
  }
  const formatValue = (value: number) => {
    if (value >= 1000000) {
      const scaled = value / 1000000
      return `${scaled >= 100 ? scaled.toFixed(0) : scaled.toFixed(1).replace(/\.0$/, '')}m`
    }
    if (value >= 1000) {
      const scaled = value / 1000
      return `${scaled >= 100 ? scaled.toFixed(0) : scaled.toFixed(1).replace(/\.0$/, '')}k`
    }
    return String(Math.round(value))
  }
  return `${usage.estimated ? '约 ' : ''}${formatValue(usage.used)} / ${formatValue(usage.total)}`
}

const tokenUsageDisplay = computed(() => formatTokenUsageDisplay(tokenUsage.value))
const tokenUsageBreakdown = computed(() => {
  const usage = tokenUsage.value
  if (!usage?.breakdown?.length) return []
  return usage.breakdown
    .filter(item => Number.isFinite(item.tokens) && item.tokens !== 0)
    .map(item => ({
      ...item,
      percentage: usage.used > 0 ? Math.round((item.tokens / usage.used) * 1000) / 10 : 0,
    }))
})

const tokenUsageDetailRows = computed(() => {
  const usage = tokenUsage.value
  if (!usage) return []
  const rows = [
    { label: '当前上下文', value: usage.current_input_tokens ?? usage.input_tokens ?? usage.used },
    { label: '总上下文', value: usage.total },
  ]
  return rows.filter(row => Number.isFinite(row.value))
})

type TokenUsageRow = {
  key: string
  label: string
  tokens: number
  percentage: number
  description?: string
  content?: string
  role?: string
  round?: number
  messageIndex?: number
  children?: TokenUsageRow[]
}

const toTokenUsageRow = (item: ChatDataTokenUsageBreakdown, labelPrefix = '', keyPrefix = ''): TokenUsageRow => ({
  key: keyPrefix ? `${keyPrefix}_${item.key}` : item.key,
  label: labelPrefix ? `${labelPrefix} · ${item.label}` : item.label,
  tokens: item.tokens,
  percentage: tokenUsage.value?.used ? Math.round((item.tokens / tokenUsage.value.used) * 1000) / 10 : 0,
  description: item.description,
  content: item.content || item.description,
  role: item.role,
  round: item.round,
  messageIndex: item.message_index,
})

const tokenUsageMessageRows = computed(() => {
  const rows: TokenUsageRow[] = []
  for (const item of tokenUsageBreakdown.value) {
    if (!item.children?.length) continue
    if (item.key === 'tool_schema') {
      rows.push({ ...toTokenUsageRow(item, '', item.key), messageIndex: rows.length, children: item.children.map(child => toTokenUsageRow(child, item.label, item.key)) })
      continue
    }
    for (const child of item.children) {
      rows.push(toTokenUsageRow(child, item.label, item.key))
    }
  }
  if (!rows.length) {
    rows.push(...tokenUsageBreakdown.value.map(item => toTokenUsageRow(item)))
  }
  if (tokenUsageSort.value === 'desc') {
    return [...rows].sort((a, b) => b.tokens - a.tokens)
  }
  if (tokenUsageSort.value === 'asc') {
    return [...rows].sort((a, b) => a.tokens - b.tokens)
  }
  return [...rows].sort((a, b) => {
    const roundDiff = (a.round ?? 0) - (b.round ?? 0)
    if (roundDiff !== 0) return roundDiff
    return (a.messageIndex ?? 0) - (b.messageIndex ?? 0)
  })
})

const toggleTokenMessage = (key: string) => {
  const next = new Set(expandedTokenMessageKeys.value)
  if (next.has(key)) {
    next.delete(key)
  } else {
    next.add(key)
  }
  expandedTokenMessageKeys.value = next
}

const resetTokenUsagePanel = () => {
  tokenUsage.value = null
  tokenUsagePanelOpen.value = false
  expandedTokenMessageKeys.value = new Set()
}

const loadModelOptions = async () => {
  try {
    const response = await getChatDataConfig()
    const models = response.data?.models || []
    dataAnalyzeEnabled.value = !!response.data?.data_analyze_enabled
    if (props.conversationAgentId === 'chat_data_data_analyze' && dataAnalyzeEnabled.value) {
      selectedAgent.value = 'chat_data_data_analyze'
    } else if (selectedAgent.value === 'chat_data_data_analyze' && !dataAnalyzeEnabled.value) {
      selectedAgent.value = 'chat_data'
    }
    modelOptions.value = models
    const defaultModel = models.find(item => item.is_default)?.id || models[0]?.id || ''
    if (defaultModel && !models.some(item => item.id === selectedModel.value)) {
      selectedModel.value = defaultModel
    }
  } catch (err) {
    modelOptions.value = []
    selectedModel.value = ''
    console.error('加载模型列表失败:', err)
  }
}

onMounted(() => {
  document.addEventListener('click', handleAgentSelectorDocumentClick)
  loadModelOptions()
})

// 收藏
const conversationFavoriteRefreshKey = ref(0)
const selectedSkill = ref<SkillInfo | null>(null)
const showFavoriteModal = ref(false)
const favoriteTitle = ref('')
const favoriteContent = ref('')
const creatingDashboardMessageId = ref<string | null>(null)

const resolveSkillNameFromText = (text: string): string | null => {
  const match = String(text || '').match(/^使用「([^」]+)」技能[:：]/)
  const title = match?.[1]?.trim()
  if (!title) return null
  if (title === '看板生成技能' || title === '风险分析技能' || title === '机构画像分析') return 'dashboard-generation'
  return null
}

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
      selectedSkill.value = null
      resetSemanticQuestionDraft()
      resetTokenUsagePanel()
      // 切换会话时，如果是新会话（null），清空消息
      if (newId === null) {
        messages.value = []
        toolCallsMap.value.clear()
        resetHistory()
      }
    }
  }
)

watch(
  () => props.conversationAgentId,
  (agentId) => {
    selectedAgent.value = agentId === 'chat_data_data_analyze' && dataAnalyzeEnabled.value ? 'chat_data_data_analyze' : 'chat_data'
  }
)

watch(selectedAgent, (agentId) => {
  selectedSkill.value = null
  resetSemanticQuestionDraft()
  if (agentId !== props.conversationAgentId) {
    emit('agentChange', agentId)
  }
})

const selectAgent = (agent: typeof agentOptions.value[number]) => {
  if (agent.disabled) {
    ElMessage.info('功能暂未开放')
    isAgentSelectorOpen.value = false
    return
  }
  selectedAgent.value = agent.id
  isAgentSelectorOpen.value = false
}

const handleAgentSelectorDocumentClick = (event: MouseEvent) => {
  const target = event.target
  if (!(target instanceof Node)) return
  if (agentSelectorRef.value?.contains(target)) return
  isAgentSelectorOpen.value = false
}

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
        org_name: member.org_name || '',
        org_code: member.org_code || '',
        source_type: member.source_type || datasource?.source_type || '',
        host: String(member.host || ''),
        port: Number(member.port || 0),
        database: String(member.database || ''),
        label: member.org_name || `${member.host}:${member.port}/${member.database}`,
      }))
  }
  if (!selectedDatasource.value || !datasource) return []
  const sourceConfig = datasource.source_config || {}
  return [{
    member_datasource_id: datasource.template_datasource_id || selectedDatasource.value,
    source_type: datasource.source_type || '',
    host: String(sourceConfig.host || ''),
    port: Number(sourceConfig.port || 0),
    database: String(sourceConfig.database || ''),
    label: datasource.group_name || selectedDatasource.value,
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
  const firstAvailableId = availableIds[0]
  selectedMemberDatasourceIds.value = firstAvailableId ? [firstAvailableId] : []
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
  if (msg.type === 'thinking') {
    const reasoningContent = typeof msg.content === 'string'
      ? msg.content
      : typeof msg.additional_kwargs?.reasoning_content === 'string'
        ? msg.additional_kwargs.reasoning_content
        : ''
    if (reasoningContent) {
      const msgIndex = messages.value.findIndex((m) => m.id === messageId)
      if (msgIndex >= 0) {
        const m = messages.value[msgIndex]
        if (m) {
          const visibleContent = stripThinkingContent(m.content)
          const toolCallCount = getVisibleToolCalls(m.toolCalls).length
          const thinkingSegments = [...(m.thinkingSegments || [])]
          const lastSegment = thinkingSegments[thinkingSegments.length - 1]
          if (lastSegment && lastSegment.toolCallCount === toolCallCount) {
            lastSegment.content += reasoningContent
          } else {
            thinkingSegments.push({
              id: `thinking-${Date.now()}-${thinkingSegments.length}`,
              content: reasoningContent,
              toolCallCount,
            })
          }
          m.thinkingSegments = thinkingSegments
          const nextThinking = thinkingSegments
            .map(segment => `<thinking>${segment.content}</thinking>`)
            .join('')
          m.content = visibleContent ? `${nextThinking}${visibleContent}` : nextThinking
          syncAnalysisPanelFromMessage(m, true)
        }
      }
    }
    return
  }

  // 处理工具调用
  if (msg.tool_calls && msg.tool_calls.length > 0) {
    for (const tc of msg.tool_calls) {
      if (tc.id) {
        const existingInfo = toolCallsMap.value.get(tc.id)
        const mergedArgs = existingInfo ? { ...existingInfo.args, ...(tc.args || {}) } : tc.args || {}
        const toolName = tc.name || existingInfo?.tool_name || ''
        if (!toolName) continue

        // 如果是语义工具，提取 DSL 参数用于结果展示
        let semanticParams = existingInfo?.semanticParams
        if ((toolName === 'dsl2sql_tool' || toolName === 'semantic_query_tool') && mergedArgs) {
          semanticParams = extractSemanticParamsFromArgs(mergedArgs) || semanticParams
        }

        const toolCallInfo: ToolCallInfo = {
          tool_call_id: tc.id,
          tool_name: toolName,
          args: mergedArgs,
          status: 'generating',
          result: existingInfo?.result,
          semanticParams,
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

      // 从 accumulated_args 获取完整参数（流式模式下后端累积的完整参数）
      const accumulatedArgs = msg.accumulated_args
      if (accumulatedArgs) {
        // 更新工具调用的 args
        toolInfo.args = { ...toolInfo.args, ...accumulatedArgs }

        // 从 accumulated_args 提取语义参数。semantic_query_tool 新格式直接携带 dsl。
        if (toolInfo.tool_name === 'dsl2sql_tool' || toolInfo.tool_name === 'semantic_query_tool') {
          semanticParams = extractSemanticParamsFromArgs(toolInfo.args) || semanticParams

          // 更新 dsl2sqlToolCalls 中对应的记录
          if (toolInfo.tool_name === 'dsl2sql_tool') {
            const dslIndex = dsl2sqlToolCalls.value.findIndex(t => t.tool_call_id === msg.tool_call_id)
            if (dslIndex >= 0) {
              dsl2sqlToolCalls.value[dslIndex] = {
                ...dsl2sqlToolCalls.value[dslIndex]!,
                semanticParams,
                args: toolInfo.args,
              }
            }
          }
        }
      }

      if (toolInfo.tool_name === 'semantic_query_tool') {
        const queryResultMatch = msg.content.match(/<query_result>([\s\S]*?)<\/query_result>/)
        const queryResultId = queryResultMatch?.[1]
        if (queryResultId) {
          renderId = queryResultId.trim()
          renderType = 'queryResult'
        }

        semanticParams = extractSemanticParamsFromArgs(toolInfo.args) || semanticParams

        // 从对应的 dsl2sql_tool 获取语义参数作为旧历史和异常流式 chunk 的兜底
        // 策略：按顺序匹配（第 N 个 semantic_query_tool 对应第 N 个 dsl2sql_tool）
        const executeSqlTools = Array.from(toolCallsMap.value.values())
          .filter(t => t.tool_name === 'semantic_query_tool')
        const currentIndex = executeSqlTools.findIndex(t => t.tool_call_id === msg.tool_call_id)
        if (currentIndex >= 0 && currentIndex < dsl2sqlToolCalls.value.length) {
          const dsl2sqlTool = dsl2sqlToolCalls.value[currentIndex]
          if (!semanticParams && dsl2sqlTool) {
            semanticParams = dsl2sqlTool.semanticParams
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
            syncAnalysisPanelFromMessage(m, false)
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
    syncAnalysisPanelFromMessage(currentMsg, true)
  }
}

const handleDashboardSpec = (payload: DashboardSpecPayload, messageId: string) => {
  const msgIndex = messages.value.findIndex((m) => m.id === messageId)
  if (msgIndex >= 0 && messages.value[msgIndex]) {
    messages.value[msgIndex]!.dashboardSpec = payload
  }
}

const handleSemanticConfigSelected = (payload: SemanticConfigSelectionInfo, messageId: string) => {
  const msgIndex = messages.value.findIndex((m) => m.id === messageId)
  if (msgIndex >= 0 && messages.value[msgIndex]) {
    const msg = messages.value[msgIndex]!
    msg.semanticConfigSelection = payload
    msg.semanticConfigSelectRequired = undefined
    syncAnalysisPanelFromMessage(msg, true)
  }
  if (payload.user_selected) {
    emit('semanticConfigSelected', payload)
  }
}

const handleCreateDashboardFromSpec = async (msg: Message) => {
  if (!msg.dashboardSpec?.dashboard_spec) return
  creatingDashboardMessageId.value = msg.id
  try {
    const specSemanticConfigId = String(msg.dashboardSpec.dashboard_spec?.semantic_config_id || '').trim()
    const semanticConfigId = msg.semanticConfigSelection?.semantic_config_id
      || msg.dashboardSpec.semantic_config_id
      || specSemanticConfigId
      || props.semanticConfigId
      || null
    const res = await createDashboardFromSpec({
      dashboard_spec: msg.dashboardSpec.dashboard_spec,
      datasource_id: msg.dashboardSpec.datasource_id || selectedDatasource.value,
      semantic_config_id: semanticConfigId,
      conversation_id: msg.dashboardSpec.conversation_id || props.conversationId || null,
    })
    if (res.status !== 'success') {
      const warnings = res.data?.warnings || []
      throw new Error(warnings.length ? `${res.msg || '创建看板失败'}：${warnings[0]}` : (res.msg || '创建看板失败'))
    }
    const warnings = res.data?.warnings || []
    const dashboard = res.data?.dashboard
    if (dashboard?.id) {
      emit('dashboardCreated', dashboard)
    }
    msg.dashboardSpec = undefined
    ElMessage.success(warnings.length ? `看板已创建，${warnings.length} 个卡片需调整` : '看板已创建，已切换到数据看板')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '创建看板失败')
  } finally {
    creatingDashboardMessageId.value = null
  }
}

const getDashboardSpecWritableCards = (payload?: DashboardSpecPayload): number => {
  if (typeof payload?.writable_card_count === 'number') {
    return Math.max(0, payload.writable_card_count)
  }
  const spec = payload?.dashboard_spec
  if (!spec) return 0
  if (spec.need_clarify) return 0
  const status = String(spec.generation_status || '')
  if (status === 'dashboard_spec_needs_semantic_config') return 0
  const cards = Array.isArray(spec.cards) ? spec.cards : []
  return cards.filter((card) => {
    if (!card || typeof card !== 'object') return false
    const item = card as Record<string, unknown>
    const dsl = item.dsl && typeof item.dsl === 'object' ? item.dsl as Record<string, unknown> : {}
    const metrics = Array.isArray(item.metrics) ? item.metrics : []
    const dslMetrics = Array.isArray(dsl.metrics) ? dsl.metrics : []
    const explicitSql = String(item.sql || '').trim()
    return Boolean(explicitSql || metrics.length > 0 || dslMetrics.length > 0)
  }).length
}

const hasDashboardSpecContent = (payload?: DashboardSpecPayload): boolean => {
  return getDashboardSpecWritableCards(payload) > 0
}

const getDashboardSpecWarningCount = (payload?: DashboardSpecPayload): number => {
  return Array.isArray(payload?.validation_warnings) ? payload.validation_warnings.length : 0
}

const handleSemanticConfigSelectRequired = (payload: SemanticConfigSelectRequiredInfo, messageId: string) => {
  const msgIndex = messages.value.findIndex((m) => m.id === messageId)
  if (msgIndex >= 0 && messages.value[msgIndex]) {
    const msg = messages.value[msgIndex]!
    msg.semanticConfigSelectRequired = payload
    msg.error = undefined
    msg.isStreaming = false
    syncAnalysisPanelFromMessage(msg, true)
  }
}

const submitQuery = async (
  queryText: string,
  options: {
    semanticConfigIdOverride?: string | null
    semanticConfigSelection?: SemanticConfigSelectionInfo | null
    assistantMessageId?: string
    appendUserMessage?: boolean
  } = {}
) => {
  if (!queryText.trim() || isGenerating.value || !selectedDatasource.value) return
  if (selectedAgent.value === 'chat_data_data_analyze' && !dataAnalyzeEnabled.value) {
    ElMessage.info('功能暂未开放')
    selectedAgent.value = 'chat_data'
    return
  }
  if (selectedAgent.value === 'chat_data_data_analyze') {
    emit('agentChange', 'chat_data_data_analyze')
    return
  }
  if (executionTargets.value.length > 0 && selectedMemberDatasourceIds.value.length === 0) {
    ElMessage.warning('请选择执行对象')
    return
  }

  const normalizedQuery = queryText.trim()
  const skillName = null
  const activeAgentId = 'chat_data'
  const effectiveSemanticConfigSelection = options.semanticConfigSelection !== undefined
    ? options.semanticConfigSelection
    : (props.semanticConfigSelection || null)
  if (activeAgentId !== props.conversationAgentId) {
    emit('agentChange', activeAgentId)
  }

  let userMessageId = ''
  let assistantMessageId = options.assistantMessageId || ''
  if (options.appendUserMessage !== false) {
    const now = Date.now()
    userMessageId = `user-${now}`
    assistantMessageId = assistantMessageId || `assistant-${now}`
    const userMessage: Message = {
      id: userMessageId,
      role: 'user',
      content: normalizedQuery,
      timestamp: new Date()
    }
    const assistantMessage: Message = {
      id: assistantMessageId,
      role: 'assistant',
      content: '',
      timestamp: new Date(),
      isStreaming: true,
      toolCalls: [],
      semanticConfigSelection: effectiveSemanticConfigSelection || undefined,
    }
    messages.value.push(userMessage, assistantMessage)
  } else if (assistantMessageId) {
    const msg = messages.value.find((m) => m.id === assistantMessageId)
    if (msg) {
      msg.content = ''
      msg.error = undefined
      msg.isStreaming = true
      msg.toolCalls = []
      msg.semanticConfigSelectRequired = undefined
      msg.semanticConfigSelection = effectiveSemanticConfigSelection || undefined
    }
  }

  input.value = ''
  resetSemanticQuestionDraft()
  isGenerating.value = true
  toolCallsMap.value.clear()
  dsl2sqlToolCalls.value = []
  resetTokenUsagePanel()

  // 新一轮开始，关闭上一轮的分析面板
  handleCloseAnalysisPanel()
  const currentAssistantMessage = messages.value.find((m) => m.id === assistantMessageId && m.role === 'assistant')
  if (currentAssistantMessage?.semanticConfigSelection) {
    syncAnalysisPanelFromMessage(currentAssistantMessage, true)
  }

  // 确保有会话 ID（懒创建模式）
  let convId = props.conversationId
  if (convId && props.conversationAgentId && props.conversationAgentId !== activeAgentId) {
    convId = null
  }
  if (!convId && props.ensureConversationId) {
    convId = await props.ensureConversationId(activeAgentId)
  }
  if (!convId) {
    ElMessage.error('无法创建会话')
    // 回滚消息
    if (userMessageId) {
      messages.value = messages.value.filter(m => m.id !== userMessageId && m.id !== assistantMessageId)
    }
    isGenerating.value = false
    input.value = normalizedQuery
    return
  }

  try {
    await generateSqlStream(
      normalizedQuery,
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
          syncAnalysisPanelFromMessage(msg, false)
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
      // semanticConfigId 为空时由后端智能选择指标集
      options.semanticConfigIdOverride !== undefined ? options.semanticConfigIdOverride : (props.semanticConfigId || null),
      // modelId - 选中模型的 value
      selectedModelValue.value,
      selectedMemberDatasourceIds.value,
      activeAgentId,
      skillName,
      (payload) => handleDashboardSpec(payload, assistantMessageId),
      undefined,
      (usage) => {
        tokenUsage.value = usage
      },
      (payload) => handleSemanticConfigSelected(payload, assistantMessageId),
      (payload) => handleSemanticConfigSelectRequired(payload, assistantMessageId),
      effectiveSemanticConfigSelection,
      enableDeepThinking.value
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

// 发送消息
const handleSubmit = async () => {
  const queryText = input.value.trim()
  await submitQuery(queryText)
}

const handleSemanticConfigCandidateClick = async (msg: Message, candidate: SemanticConfigSelectRequiredInfo['candidates'][number]) => {
  if (msg.semanticConfigSelectRequired?.cross_config_required) return
  const configId = String(candidate.id || '').trim()
  const query = String(msg.semanticConfigSelectRequired?.query || '').trim()
  if (!configId || !query) return
  const selection: SemanticConfigSelectionInfo = {
    semantic_config_id: configId,
    semantic_config_name: candidate.name || configId,
    confidence: candidate.confidence || '',
    reason: candidate.reason || msg.semanticConfigSelectRequired?.reason || '用户从候选指标集中选择',
    matched_metrics: [],
    matched_dimensions: [],
    auto_selected: false,
    user_selected: true,
  }
  msg.semanticConfigSelection = selection
  msg.semanticConfigSelectRequired = undefined
  await submitQuery(query, {
    semanticConfigIdOverride: configId,
    semanticConfigSelection: selection,
    assistantMessageId: msg.id,
    appendUserMessage: false,
  })
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
  resetSemanticQuestionDraft()
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
  selectedSkill.value = null
  resetSemanticQuestionDraft()
  input.value = query
  nextTick(() => textareaRef.value?.focus())
}

const buildSemanticQuestionText = (dimensions: string[], metrics: string[]) => {
  if (dimensions.length > 0 && metrics.length > 0) {
    return `请按${dimensions.join('、')}分组查询${metrics.join('、')}`
  }
  return `请查询${[...metrics, ...dimensions].join('、')}`
}

const fillSemanticFieldQuestion = (payload: { fieldName: string; fieldType: 'dimension' | 'metric' }) => {
  const fieldName = payload?.fieldName
  const fieldType = payload?.fieldType
  const normalizedName = String(fieldName || '').trim()
  if (!normalizedName) return
  selectedSkill.value = null
  const currentInput = input.value.trim()
  const draft = currentInput === semanticQuestionDraft.value.text
    ? {
        dimensions: [...semanticQuestionDraft.value.dimensions],
        metrics: [...semanticQuestionDraft.value.metrics],
      }
    : { dimensions: [] as string[], metrics: [] as string[] }
  const targetFields = fieldType === 'dimension' ? draft.dimensions : draft.metrics
  if (!targetFields.includes(normalizedName)) {
    targetFields.push(normalizedName)
  }
  const nextText = buildSemanticQuestionText(draft.dimensions, draft.metrics)
  semanticQuestionDraft.value = {
    text: nextText,
    dimensions: draft.dimensions,
    metrics: draft.metrics,
  }
  input.value = nextText
  nextTick(() => textareaRef.value?.focus())
}

const handleSkillSelect = (skill: SkillInfo) => {
  selectedSkill.value = skill
  resetSemanticQuestionDraft()
  const title = skill.title || skill.name
  input.value = `使用「${title}」技能：`
  nextTick(() => textareaRef.value?.focus())
}

defineExpose({
  fillSemanticFieldQuestion,
})

// 过滤可见的工具调用
const getVisibleToolCalls = (toolCalls?: ToolCallInfo[]): ToolCallInfo[] => {
  if (!toolCalls) return []
  return toolCalls.filter((tc) => !HIDDEN_TOOLS.includes(tc.tool_name))
}

const buildAnalysisDataFromMessage = (msg: Message): AnalysisData => {
  return {
    roundId: msg.id,
    thinkingContent: extractThinkingContent(msg.content),
    thinkingSegments: msg.thinkingSegments || extractThinkingSegments(msg.content),
    toolCalls: getVisibleToolCalls(msg.toolCalls),
    semanticConfigSelection: msg.semanticConfigSelection || null,
    semanticConfigSelectRequired: msg.semanticConfigSelectRequired || null,
    textContent: ''
  }
}

const syncAnalysisPanelFromMessage = (msg: Message, autoOpen: boolean) => {
  const data = buildAnalysisDataFromMessage(msg)
  if (!hasAnalysisDataContent(data)) return
  if (autoOpen && (!isAnalysisPanelOpen.value || currentAnalysisRoundId.value !== msg.id)) {
    handleOpenAnalysisPanel(data)
    analysisStreaming.value = Boolean(msg.isStreaming)
    return
  }
  if (isAnalysisPanelOpen.value && currentAnalysisRoundId.value === msg.id) {
    handleUpdateAnalysisPanel(data, Boolean(msg.isStreaming))
  }
}

// 提取第一个 thinking 内容（兼容历史数据）
const extractThinkingContent = (content: string): string => {
  // 优先匹配完整闭合的 thinking 块
  const completeMatch = content.match(/<thinking>([\s\S]*?)<\/thinking>/)
  if (completeMatch) return `<thinking>${completeMatch[1]}</thinking>`
  // 流式中：匹配已打开但未闭合的 thinking 标签
  const incompleteMatch = content.match(/<thinking>([\s\S]*)$/)
  if (incompleteMatch) return `<thinking>${incompleteMatch[1]}</thinking>`
  return ''
}

const extractThinkingSegments = (content: string) => {
  const segments: Array<{ id: string; content: string; toolCallCount: number }> = []
  const completeRegex = /<thinking>([\s\S]*?)<\/thinking>/g
  let match: RegExpExecArray | null
  while ((match = completeRegex.exec(content)) !== null) {
    segments.push({
      id: `thinking-history-${segments.length}`,
      content: match[1] || '',
      toolCallCount: segments.length,
    })
  }
  if (segments.length) return segments
  const incompleteMatch = content.match(/<thinking>([\s\S]*)$/)
  if (incompleteMatch) {
    return [{
      id: 'thinking-history-0',
      content: incompleteMatch[1] || '',
      toolCallCount: 0,
    }]
  }
  return []
}

// 去除 thinking 内容（主对话区不显示思考过程）
const stripThinkingContent = (content: string): string => {
  return content.replace(/<thinking>[\s\S]*?<\/thinking>/g, '').trim()
}

// 判断是否已输出可见正文，排除思考标签和占位文案。
const hasVisibleAssistantContent = (content: string): boolean => {
  const text = stripThinkingContent(content)
    .replace(/<query_result>[\s\S]*?<\/query_result>/g, '')
    .replace(/<chart_config>[\s\S]*?<\/chart_config>/g, '')
    .trim()
  return Boolean(text && text !== '正在思考...')
}

// 首 token 前已有气泡占位时，底部状态行不重复显示。
const shouldShowStreamingStatus = (message: Message): boolean => {
  return hasVisibleAssistantContent(message.content) || getVisibleToolCalls(message.toolCalls).length > 0 || Boolean(message.semanticConfigSelection || message.semanticConfigSelectRequired)
}

// 检查是否有已完成的查询结果
const hasCompletedQueryResult = (toolCalls?: ToolCallInfo[]): boolean => {
  if (!toolCalls) return false
  return toolCalls.some(
    (tc) => tc.tool_name === 'semantic_query_tool' && tc.status === 'completed' && tc.renderId
  )
}

// 获取已完成的查询结果工具调用
const getCompletedQueryResults = (toolCalls?: ToolCallInfo[]): ToolCallInfo[] => {
  if (!toolCalls) return []
  return toolCalls.filter(
    (tc) => tc.tool_name === 'semantic_query_tool' && tc.status === 'completed' && tc.renderId
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
  let text = hideSemanticConfigSelectionPayloads(content)
  // 移除所有技术标签和内容
  text = text.replace(/<thinking>[\s\S]*?<\/thinking>/g, '')
  text = text.replace(/<query_result>[\s\S]*?<\/query_result>/g, '')
  text = text.replace(/<chart_config>[\s\S]*?<\/chart_config>/g, '')
  // text = text.replace(/查询结果已生成[。.]?/g, '') // 已改为输出简要说明
  return text.trim()
}

const renderSummaryMarkdown = (content: string) => {
  return md.render(hideSemanticConfigSelectionPayloads(content))
}

// 创建分析数据
const createAnalysisData = (msg: Message): AnalysisData | null => {
  const data = buildAnalysisDataFromMessage(msg)
  if (!hasAnalysisDataContent(data)) return null
  if (!hasCompletedQueryResult(msg.toolCalls) && !data.semanticConfigSelection && !data.semanticConfigSelectRequired && !data.thinkingContent && data.toolCalls.length === 0) return null
  return data
}

// 获取流式阶段文案
const getStreamingStageText = (msg: Message): string => {
  const toolCalls = getVisibleToolCalls(msg.toolCalls)
  if (msg.semanticConfigSelectRequired) {
    return '等待选择指标集...'
  }
  if (msg.semanticConfigSelection && toolCalls.length === 0) {
    return '已选择指标集，正在生成查询...'
  }
  if (toolCalls.some(tc => tc.tool_name === 'semantic_query_tool' && tc.status === 'running')) {
    return '正在查询数据...'
  }
  if (toolCalls.some(tc => tc.tool_name === 'semantic_query_tool')) {
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
                {{ ds.group_name }}
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
          <div v-for="group in groupedConversations" :key="group.label" class="mb-2">
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
              {{ ds.group_name }}
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
                  <div ref="agentSelectorRef" class="relative">
                    <button
                      type="button"
                      class="flex items-center gap-1.5 px-2 py-1 rounded-full border-none text-xs text-gray-500 outline-none cursor-pointer hover:bg-black/[0.05]"
                      style="background: rgba(0,0,0,0.03)"
                      @click="isAgentSelectorOpen = !isAgentSelectorOpen"
                    >
                      <i class="fa-solid fa-robot text-[10px] text-gray-400"></i>
                      <span>{{ selectedAgentLabel }}</span>
                      <i class="fa-solid fa-chevron-down text-[8px] text-gray-400"></i>
                    </button>
                    <div
                      v-if="isAgentSelectorOpen"
                      class="absolute left-0 bottom-8 z-50 min-w-[130px] overflow-hidden rounded-lg border border-gray-200 bg-white py-1 text-xs text-gray-600 shadow-lg"
                    >
                      <button
                        v-for="agent in agentOptions"
                        :key="agent.id"
                        type="button"
                        :aria-disabled="agent.disabled"
                        :title="agent.disabled ? '功能暂未开放' : undefined"
                        :class="[
                          'flex w-full items-center justify-between gap-2 border-none bg-transparent px-3 py-1.5 text-left text-xs',
                          selectedAgent === agent.id ? 'text-blue-600 bg-blue-50' : 'text-gray-600',
                          agent.disabled ? '!text-gray-400 cursor-not-allowed bg-gray-50' : 'cursor-pointer hover:bg-gray-50'
                        ]"
                        @click="selectAgent(agent)"
                      >
                        <span>{{ agent.label }}</span>
                        <i v-if="!agent.disabled && selectedAgent === agent.id" class="fa-solid fa-check text-[10px]"></i>
                      </button>
                    </div>
                  </div>
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
                  <button
                    type="button"
                    :class="[
                      'flex items-center gap-1.5 px-2 py-1 rounded-full border-none text-xs outline-none cursor-pointer transition-colors',
                      enableDeepThinking ? 'bg-blue-50 text-blue-600' : 'text-gray-500 hover:bg-black/[0.05]'
                    ]"
                    :title="enableDeepThinking ? '已开启深度思考' : '开启深度思考'"
                    :aria-pressed="enableDeepThinking"
                    @click="enableDeepThinking = !enableDeepThinking"
                  >
                    <i class="fa-solid fa-brain text-[10px]"></i>
                    <span>深度思考</span>
                  </button>
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
            :key="recommendationContentType"
            :datasource-id="selectedDatasource"
            mode="conversation"
            :content-type="recommendationContentType"
            :refresh-trigger="conversationFavoriteRefreshKey"
            :max-items="8"
            @select-query="handleQuickQuerySelect"
            @select-skill="handleSkillSelect"
          />
        </div>
      </div>

      <!-- 有消息时：对话列表 -->
      <div v-else class="max-w-[774px] mx-auto w-[90%]">
        <div v-for="msg in messages" :key="msg.id" class="mb-5">
          <!-- 用户消息 -->
          <div v-if="msg.role === 'user'" class="group flex flex-col items-end gap-1">
            <div class="max-w-[80%] px-4 py-2.5 rounded-2xl rounded-br-md bg-[#1677FF]/10 text-[#1F3A56] text-sm leading-relaxed shadow-[0_2px_8px_rgba(59,130,246,0.12)]">{{ msg.content }}</div>
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
              <div
                v-if="msg.semanticConfigSelectRequired"
                class="mb-3 rounded-lg border border-red-100 bg-red-50/70 px-3 py-2 text-[13px] text-red-700"
              >
                <div class="flex items-start gap-2">
                  <i class="fa-solid fa-circle-exclamation mt-0.5 text-[12px]"></i>
                  <div class="min-w-0 flex-1">
                    <div class="font-medium">{{ msg.semanticConfigSelectRequired.message }}</div>
                    <div v-if="msg.semanticConfigSelectRequired.candidates.length && !msg.semanticConfigSelectRequired.cross_config_required" class="mt-2 flex flex-wrap gap-2">
                      <button
                        v-for="candidate in msg.semanticConfigSelectRequired.candidates"
                        :key="candidate.id"
                        type="button"
                        class="rounded-md border border-red-200 bg-white px-2.5 py-1 text-[12px] font-medium text-red-700 transition-colors hover:border-red-300 hover:bg-red-100 disabled:cursor-not-allowed disabled:opacity-60"
                        :disabled="isGenerating"
                        :title="candidate.reason || '使用该指标集继续查询'"
                        @click="handleSemanticConfigCandidateClick(msg, candidate)"
                      >
                        {{ candidate.name || candidate.id }}
                      </button>
                    </div>
                  </div>
                </div>
              </div>

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
                    :sql="extractGeneratedSqlFromToolCall(tc)"
                    :semantic-params="tc.semanticParams"
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
                <div
                  v-if="extractThinkingContent(msg.content) || getVisibleToolCalls(msg.toolCalls).length > 0 || msg.semanticConfigSelection || msg.semanticConfigSelectRequired"
                  class="px-1 py-1"
                >
                  <div class="flex items-center gap-2 mb-2">
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
                  <div class="px-2 py-1">
                    <div class="particle-storm relative h-[88px] overflow-hidden">
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

                <div v-if="hasDashboardSpecContent(msg.dashboardSpec) && !msg.isStreaming" class="mt-3 rounded-2xl border border-blue-100 bg-gradient-to-br from-blue-50/90 to-white px-3.5 py-3 shadow-[0_8px_24px_rgba(22,119,255,0.08)]">
                  <div class="flex items-start justify-between gap-3">
                    <div class="min-w-0">
                      <div class="flex items-center gap-2">
                        <span class="inline-flex h-7 w-7 items-center justify-center rounded-xl bg-blue-600 text-white shadow-sm">
                          <i class="fa-solid fa-chart-pie text-xs"></i>
                        </span>
                        <div>
                          <div class="text-sm font-semibold text-slate-800">看板方案已准备好</div>
                          <div class="text-[11px] text-slate-500">
                            可写入 {{ getDashboardSpecWritableCards(msg.dashboardSpec) }} 个卡片，不会立即执行查询。
                          </div>
                        </div>
                      </div>
                      <div v-if="getDashboardSpecWarningCount(msg.dashboardSpec) > 0" class="mt-2 text-[11px] text-amber-600">
                        <i class="fa-solid fa-triangle-exclamation mr-1"></i>
                        {{ getDashboardSpecWarningCount(msg.dashboardSpec) }} 条数据口径提示，写入时会跳过不可生成 SQL 的卡片。
                      </div>
                    </div>
                    <button
                      class="shrink-0 h-8 px-3.5 rounded-xl border border-blue-200 bg-blue-600 text-xs font-semibold text-white shadow-sm hover:bg-blue-700 disabled:opacity-60 disabled:cursor-not-allowed"
                      :disabled="creatingDashboardMessageId === msg.id"
                      @click="handleCreateDashboardFromSpec(msg)"
                    >
                      <i v-if="creatingDashboardMessageId === msg.id" class="fa-solid fa-spinner fa-spin mr-1"></i>
                      <i v-else class="fa-solid fa-check mr-1"></i>
                      确认写入
                    </button>
                  </div>
                </div>

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

            <!-- 生成状态 -->
            <div v-if="msg.isStreaming && shouldShowStreamingStatus(msg)" class="flex items-center gap-2 py-1">
              <span class="text-xs font-medium text-gray-500">{{ hasVisibleAssistantContent(msg.content) ? '正在生成' : '正在思考' }}</span>
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
                <div ref="agentSelectorRef" class="relative">
                  <button
                    type="button"
                    class="flex items-center gap-1.5 px-2 py-1 rounded-full border-none text-xs text-gray-500 outline-none cursor-pointer hover:bg-black/[0.05]"
                    style="background: rgba(0,0,0,0.03)"
                    @click="isAgentSelectorOpen = !isAgentSelectorOpen"
                  >
                    <i class="fa-solid fa-robot text-[10px] text-gray-400"></i>
                    <span>{{ selectedAgentLabel }}</span>
                    <i class="fa-solid fa-chevron-down text-[8px] text-gray-400"></i>
                  </button>
                  <div
                    v-if="isAgentSelectorOpen"
                    class="absolute left-0 bottom-8 z-50 min-w-[130px] overflow-hidden rounded-lg border border-gray-200 bg-white py-1 text-xs text-gray-600 shadow-lg"
                  >
                    <button
                      v-for="agent in agentOptions"
                      :key="agent.id"
                      type="button"
                      :aria-disabled="agent.disabled"
                      :title="agent.disabled ? '功能暂未开放' : undefined"
                      :class="[
                        'flex w-full items-center justify-between gap-2 border-none bg-transparent px-3 py-1.5 text-left text-xs',
                        selectedAgent === agent.id ? 'text-blue-600 bg-blue-50' : 'text-gray-600',
                        agent.disabled ? '!text-gray-400 cursor-not-allowed bg-gray-50' : 'cursor-pointer hover:bg-gray-50'
                      ]"
                      @click="selectAgent(agent)"
                    >
                      <span>{{ agent.label }}</span>
                      <i v-if="!agent.disabled && selectedAgent === agent.id" class="fa-solid fa-check text-[10px]"></i>
                    </button>
                  </div>
                </div>
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
                <button
                  type="button"
                  :class="[
                    'flex items-center gap-1.5 px-2 py-1 rounded-full border-none text-xs outline-none cursor-pointer transition-colors',
                    enableDeepThinking ? 'bg-blue-50 text-blue-600' : 'text-gray-500 hover:bg-black/[0.05]'
                  ]"
                  :title="enableDeepThinking ? '已开启深度思考' : '开启深度思考'"
                  :aria-pressed="enableDeepThinking"
                  @click="enableDeepThinking = !enableDeepThinking"
                >
                  <i class="fa-solid fa-brain text-[10px]"></i>
                  <span>深度思考</span>
                </button>
                <ExecutionTargetPicker
                  v-if="!hideExecutionTargetSelector && executionTargets.length > 0"
                  v-model="selectedMemberDatasourceIds"
                  :targets="executionTargets"
                  :multiple="executionTargetMultiple"
                />
                <div v-if="tokenUsageDisplay" class="relative">
                  <button
                    type="button"
                    class="text-xs text-gray-400 whitespace-nowrap border-none bg-transparent px-1 py-0.5 rounded cursor-pointer hover:text-gray-600 hover:bg-gray-100"
                    @click="tokenUsagePanelOpen = !tokenUsagePanelOpen"
                  >
                    {{ tokenUsageDisplay }}
                  </button>
                  <div
                    v-if="tokenUsagePanelOpen"
                    class="absolute left-0 bottom-7 z-50 w-[460px] rounded-lg border border-gray-200 bg-white shadow-lg p-3 text-xs text-gray-600"
                  >
                    <div class="flex items-center justify-between mb-2">
                      <div class="font-semibold text-gray-800">上下文占用明细</div>
                      <button type="button" class="border-none bg-transparent text-gray-400 cursor-pointer hover:text-gray-600" @click="tokenUsagePanelOpen = false">
                        <i class="fa-solid fa-xmark"></i>
                      </button>
                    </div>
                    <div class="grid grid-cols-2 gap-2 mb-2">
                      <div v-for="row in tokenUsageDetailRows" :key="row.label" class="rounded bg-gray-50 px-2 py-1">
                        <div class="text-[11px] text-gray-400">{{ row.label }}</div>
                        <div class="font-medium text-gray-700">{{ formatTokenUsageDisplay({ used: row.value, total: 1 }).split(' / ')[0] }}</div>
                      </div>
                    </div>
                    <div v-if="tokenUsageMessageRows.length" class="space-y-1.5">
                      <div class="flex items-center justify-between">
                        <span class="text-[11px] text-gray-400">完整上下文占用</span>
                        <div class="inline-flex rounded bg-gray-100 p-0.5">
                          <button
                            type="button"
                            class="border-none rounded px-2 py-0.5 text-[11px] cursor-pointer"
                            :class="tokenUsageSort === 'round' ? 'bg-white text-gray-700 shadow-sm' : 'bg-transparent text-gray-400'"
                            @click="tokenUsageSort = 'round'"
                          >
                            时序
                          </button>
                          <button
                            type="button"
                            class="border-none rounded px-2 py-0.5 text-[11px] cursor-pointer"
                            :class="tokenUsageSort === 'desc' ? 'bg-white text-gray-700 shadow-sm' : 'bg-transparent text-gray-400'"
                            @click="tokenUsageSort = 'desc'"
                          >
                            Token↓
                          </button>
                          <button
                            type="button"
                            class="border-none rounded px-2 py-0.5 text-[11px] cursor-pointer"
                            :class="tokenUsageSort === 'asc' ? 'bg-white text-gray-700 shadow-sm' : 'bg-transparent text-gray-400'"
                            @click="tokenUsageSort = 'asc'"
                          >
                            Token↑
                          </button>
                        </div>
                      </div>
                      <div class="max-h-[300px] overflow-auto pr-1 space-y-1.5">
                        <div v-for="item in tokenUsageMessageRows" :key="item.key" class="rounded border border-gray-100 bg-white px-2 py-1">
                          <button type="button" class="w-full border-none bg-transparent p-0 text-left cursor-pointer" @click="toggleTokenMessage(item.key)">
                            <div class="flex items-center justify-between gap-2">
                              <span class="text-gray-700">{{ item.label }}</span>
                              <span class="font-mono text-gray-500">{{ formatTokenUsageDisplay({ used: item.tokens, total: 1 }).split(' / ')[0] }}</span>
                            </div>
                            <div class="mt-1 h-1.5 rounded-full bg-gray-100 overflow-hidden">
                              <div class="h-full rounded-full bg-blue-400" :style="{ width: `${Math.min(Math.abs(item.percentage), 100)}%` }"></div>
                            </div>
                            <div v-if="item.description && !expandedTokenMessageKeys.has(item.key)" class="mt-1 truncate text-[11px] text-gray-400">{{ item.description }}</div>
                          </button>
                          <div v-if="expandedTokenMessageKeys.has(item.key) && item.children?.length" class="mt-2 space-y-1.5">
                            <div v-for="child in item.children" :key="child.key" class="rounded bg-gray-50 p-2">
                              <div class="flex items-center justify-between gap-2 text-[11px] text-gray-500">
                                <span>{{ child.label }}</span>
                                <span class="font-mono">{{ formatTokenUsageDisplay({ used: child.tokens, total: 1 }).split(' / ')[0] }}</span>
                              </div>
                              <pre class="mt-1 max-h-32 overflow-auto whitespace-pre-wrap text-[11px] leading-relaxed text-gray-600">{{ child.content || child.description || '' }}</pre>
                            </div>
                          </div>
                          <pre v-else-if="expandedTokenMessageKeys.has(item.key)" class="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-gray-50 p-2 text-[11px] leading-relaxed text-gray-600">{{ item.content || item.description || '' }}</pre>
                        </div>
                      </div>
                    </div>
                    <div v-else class="text-gray-400">暂无可拆分明细。</div>
                  </div>
                </div>
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
.markdown-body {
  max-width: 100%;
  min-width: 0;
  overflow-wrap: anywhere;
}

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
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.markdown-body :deep(pre) {
  max-width: 100%;
  margin: 8px 0;
  padding: 12px;
  overflow-x: auto;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #f8fafc;
  white-space: pre;
}

.markdown-body :deep(pre code) {
  display: block;
  min-width: max-content;
  padding: 0;
  background: transparent;
  white-space: pre;
  overflow-wrap: normal;
  word-break: normal;
  font-size: 12px;
  line-height: 1.6;
  color: #334155;
}

.markdown-body :deep(table) {
  width: 100%;
  max-width: 100%;
  margin: 10px 0;
  border-collapse: collapse;
  table-layout: fixed;
}

.markdown-body :deep(th),
.markdown-body :deep(td) {
  border: 1px solid #d8dee8;
  padding: 8px 10px;
  text-align: left;
  vertical-align: top;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.markdown-body :deep(th) {
  background: #f8fafc;
  color: #334155;
  font-weight: 700;
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
