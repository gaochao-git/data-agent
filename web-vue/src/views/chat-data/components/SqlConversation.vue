<script setup lang="ts">
/**
 * SQL 对话组件
 * 支持结构化消息和工具调用展示
 */
import { ref, computed, watch, nextTick, toRef, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import MarkdownIt from 'markdown-it'
import type { ChatDataModelOption, ChatDataTokenUsage, ChatDataTokenUsageBreakdown, DataSource, ToolCallInfo, LangGraphMessage, Message, MessageSegment, ExecutionTargetOption, SkillInfo, DashboardSpecPayload, SemanticConfigSelectionInfo, SemanticConfigItem, SemanticDimension, SemanticMetric } from '../types'
import { createDashboardFromSpec, getChatDataConfig, getSkills } from '../api'
import type { Dashboard } from '../api/dashboard'
import { useSqlGenerator } from '../composables/useSqlGenerator'
import { useConversationHistory } from '../composables/useConversationHistory'
import { useDeepThinking } from '../composables/useDeepThinking'
import { extractGeneratedSqlFromToolCall, extractSemanticParamsFromArgs } from '../utils/semanticTool'
import StructuredMessage from './StructuredMessage.vue'
import QueryResultPanel from './QueryResultPanel.vue'
import QuickQueries, { type FallbackQuery } from './QuickQueries.vue'
import SaveToPanelModal from './SaveToPanelModal.vue'
import AnalysisPanel from './AnalysisPanel.vue'
import ExecutionTargetPicker from './ExecutionTargetPicker.vue'
import SkillManager from './SkillManager.vue'
import AiGeneratedNotice from './AiGeneratedNotice.vue'
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
  ensureConversationDatasource?: () => Promise<boolean>
  // 语义配置（指标集）ID
  semanticConfigId?: string | null
  semanticConfigSelection?: SemanticConfigSelectionInfo | null
  semanticConfigs?: SemanticConfigItem[]
  // 是否显示分析面板（由外层切换控制）
  showAnalysisPanel?: boolean
  executionTargets?: ExecutionTargetOption[]
  executionTargetMultiple?: boolean
  selectedMemberDatasourceIds?: string[]
  hideExecutionTargetSelector?: boolean
  selectedModelId?: string
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
  semanticConfigs: () => [],
  showAnalysisPanel: true,
  executionTargetMultiple: true,
  hideExecutionTargetSelector: false,
  selectedModelId: '',
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
  'update:selectedModelId': [value: string]
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
  return Boolean(data?.thinkingSegments?.length || data?.toolCalls.length || data?.semanticConfigSelection)
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
  { id: 'chat_data', label: '问数', disabled: false },
  { id: 'chat_data_data_analyze', label: '分析', disabled: !dataAnalyzeEnabled.value },
] as const)
const selectedAgent = ref<'chat_data' | 'chat_data_data_analyze'>(
  'chat_data'
)
const isAgentSelectorOpen = ref(false)
const agentSelectorRef = ref<HTMLElement | null>(null)
const recommendationContentType = computed<'skills' | 'queries'>(() =>
  selectedAgent.value === 'chat_data_data_analyze' ? 'skills' : 'queries'
)
const selectedAgentLabel = computed(() => agentOptions.value.find(agent => agent.id === selectedAgent.value)?.label || '问数')
const modelOptions = ref<ChatDataModelOption[]>([])
const selectedModel = computed({
  get: () => props.selectedModelId || '',
  set: (value: string) => emit('update:selectedModelId', value),
})
const { enableDeepThinking } = useDeepThinking()
const tokenUsage = ref<ChatDataTokenUsage | null>(null)
const tokenUsagePanelOpen = ref(false)
const tokenUsageSort = ref<'round' | 'desc' | 'asc'>('round')
const expandedTokenMessageKeys = ref<Set<string>>(new Set())
const tokenUsageButtonRef = ref<HTMLElement | null>(null)
const tokenUsagePanelPosition = ref({ left: 24, bottom: 112 })
const toolCallsMap = ref<Map<string, ToolCallInfo>>(new Map())

// 保存到看板弹窗
const showSaveModal = ref(false)
const saveSql = ref('')
const saveChartType = ref('table')
const saveMemberDatasourceId = ref('')
const saveMemberDatasourceIds = ref<string[]>([])
const handleSaveToDashboard = (sql: string, chartType: string, memberDatasourceId?: string, memberDatasourceIds?: string[]) => {
  saveSql.value = sql
  saveChartType.value = chartType
  saveMemberDatasourceId.value = String(memberDatasourceId || selectedMemberDatasourceIds.value[0] || '').trim()
  const executedMemberIds = (memberDatasourceIds || []).map(item => String(item || '').trim()).filter(Boolean)
  saveMemberDatasourceIds.value = executedMemberIds.length > 1
    ? executedMemberIds
    : (saveMemberDatasourceId.value ? [saveMemberDatasourceId.value] : [])
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
const tokenUsagePercentage = computed(() => {
  const usage = tokenUsage.value
  if (!usage || !Number.isFinite(usage.used) || !Number.isFinite(usage.total) || usage.total <= 0) return 0
  const percentage = Number.isFinite(usage.percentage)
    ? Number(usage.percentage)
    : Math.round((usage.used / usage.total) * 1000) / 10
  return Math.max(0, Math.min(100, percentage))
})
const tokenUsagePercentageText = computed(() => tokenUsageDisplay.value ? `${Math.round(tokenUsagePercentage.value)}` : '')
const tokenUsageRingStyle = computed(() => {
  const percentage = tokenUsageDisplay.value ? tokenUsagePercentage.value : 0
  const color = percentage >= 90 ? '#ef4444' : percentage >= 70 ? '#f59e0b' : '#1677FF'
  return {
    background: `conic-gradient(${color} ${percentage}%, #d8dee8 0)`,
  }
})
const tokenUsageButtonTitle = computed(() => tokenUsageDisplay.value ? `输入预算占用：${tokenUsageDisplay.value}` : '上下文计算中')
const TOKEN_USAGE_PANEL_WIDTH = 460
const TOKEN_USAGE_PANEL_MARGIN = 16
const tokenUsagePanelStyle = computed(() => ({
  left: `${tokenUsagePanelPosition.value.left}px`,
  bottom: `${tokenUsagePanelPosition.value.bottom}px`,
  width: `${TOKEN_USAGE_PANEL_WIDTH}px`,
  maxWidth: `calc(100vw - ${TOKEN_USAGE_PANEL_MARGIN * 2}px)`,
}))

const updateTokenUsagePanelPosition = () => {
  const rect = tokenUsageButtonRef.value?.getBoundingClientRect()
  if (!rect || typeof window === 'undefined') return
  const viewportWidth = window.innerWidth || document.documentElement.clientWidth || TOKEN_USAGE_PANEL_WIDTH
  const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0
  const maxLeft = Math.max(TOKEN_USAGE_PANEL_MARGIN, viewportWidth - TOKEN_USAGE_PANEL_WIDTH - TOKEN_USAGE_PANEL_MARGIN)
  tokenUsagePanelPosition.value = {
    left: Math.min(Math.max(rect.right - TOKEN_USAGE_PANEL_WIDTH, TOKEN_USAGE_PANEL_MARGIN), maxLeft),
    bottom: Math.max(TOKEN_USAGE_PANEL_MARGIN, viewportHeight - rect.top + 8),
  }
}

const toggleTokenUsagePanel = () => {
  if (!tokenUsageDisplay.value) return
  if (!tokenUsagePanelOpen.value) {
    updateTokenUsagePanelPosition()
  }
  tokenUsagePanelOpen.value = !tokenUsagePanelOpen.value
}

const handleTokenUsageViewportChange = () => {
  if (tokenUsagePanelOpen.value) {
    updateTokenUsagePanelPosition()
  }
}

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
    { label: '当前输入', value: usage.current_input_tokens ?? usage.input_tokens ?? usage.used },
    { label: '可用输入预算', value: usage.total },
    { label: '模型上下文窗口', value: usage.context_window },
    { label: '输出预留', value: usage.max_output_tokens },
  ]
  return rows.filter((row): row is { label: string; value: number } => (
    typeof row.value === 'number' && Number.isFinite(row.value)
  ))
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

const closeTokenUsagePanel = () => {
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
    console.error('加载模型列表失败:', err)
  }
}

onMounted(() => {
  document.addEventListener('click', handleAgentSelectorDocumentClick)
  window.addEventListener('resize', handleTokenUsageViewportChange)
  window.addEventListener('scroll', handleTokenUsageViewportChange, true)
  loadModelOptions()
})

onUnmounted(() => {
  window.removeEventListener('resize', handleTokenUsageViewportChange)
  window.removeEventListener('scroll', handleTokenUsageViewportChange, true)
})

const selectedSkill = ref<SkillInfo | null>(null)
const commandSkills = ref<SkillInfo[]>([])
const isCommandSkillLoading = ref(false)
const isSkillManagerOpen = ref(false)
const highlightedSkillIndex = ref(0)
const creatingDashboardMessageId = ref<string | null>(null)

const selectedSkillTitle = computed(() => selectedSkill.value?.title || selectedSkill.value?.name || '')
const buildSkillRequestText = (text: string, skill: Pick<SkillInfo, 'name' | 'title' | 'description'> | null) => {
  const normalizedText = String(text || '').trim()
  if (!skill) return normalizedText
  return `使用「${skill.name}」技能回答：${normalizedText}`
}
const skillCommandKeyword = computed(() => {
  if (selectedAgent.value !== 'chat_data') return null
  const match = input.value.match(/^\/([^\s/]*)/)
  return match ? (match[1] || '').toLowerCase() : null
})
const displayedCommandSkills = computed(() => {
  const keyword = skillCommandKeyword.value
  if (keyword === null) return []
  const result = keyword
    ? commandSkills.value.filter(skill =>
        String(skill.title || skill.name || '').toLowerCase().includes(keyword) ||
        String(skill.description || '').toLowerCase().includes(keyword) ||
        String(skill.category || '').toLowerCase().includes(keyword)
      )
    : commandSkills.value
  return result.slice(0, 8)
})
const showSkillCommand = computed(() => skillCommandKeyword.value !== null)

const loadCommandSkills = async () => {
  if (commandSkills.value.length || isCommandSkillLoading.value) return
  isCommandSkillLoading.value = true
  try {
    const response = await getSkills({ agent: 'query' })
    commandSkills.value = response.data?.skills || []
  } catch (err) {
    console.error('获取专家技能失败:', err)
    commandSkills.value = []
  } finally {
    isCommandSkillLoading.value = false
  }
}

// 会话历史加载
const conversationIdRef = toRef(props, 'conversationId')
const { isLoadingHistory, resetHistory } = useConversationHistory({
  conversationId: conversationIdRef,
  messages,
  tokenUsage,
})

// 监听 conversationId 变化，清空消息以便重新加载历史
watch(
  () => props.conversationId,
  (newId, oldId) => {
    if (newId !== oldId) {
      selectedSkill.value = null
      resetSemanticQuestionDraft()
      resetTokenUsagePanel()
      focusInput()
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

watch(skillCommandKeyword, (keyword) => {
  if (keyword === null) {
    highlightedSkillIndex.value = 0
    return
  }
  highlightedSkillIndex.value = 0
  loadCommandSkills()
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

const selectedSemanticConfigForRecommendations = computed(() => {
  const selectionId = String(props.semanticConfigSelection?.semantic_config_id || props.semanticConfigId || '').trim()
  if (selectionId) {
    return props.semanticConfigs.find(item => item.id === selectionId) || null
  }
  return props.semanticConfigs.find(item => item.enabled) || null
})

const getMetricDisplayName = (metric: SemanticMetric) => (
  String(metric.name || metric.field_name || metric.expression || '').trim()
)

const getDimensionDisplayName = (dimension: SemanticDimension) => (
  String(dimension.name || dimension.field_name || dimension.column || '').trim()
)

const recommendationPage = ref(0)
const recommendationPageSize = 5
const recommendationOffset = computed(() => recommendationPage.value * recommendationPageSize)
const canRefreshRecommendations = computed(() => fallbackConversationQueries.value.length > recommendationPageSize)
const refreshRecommendations = () => {
  if (!canRefreshRecommendations.value) return
  const pageCount = Math.ceil(fallbackConversationQueries.value.length / recommendationPageSize)
  recommendationPage.value = (recommendationPage.value + 1) % pageCount
}

const pushFallbackQuery = (items: FallbackQuery[], seen: Set<string>, title: string, icon = 'fa-lightbulb') => {
  const normalizedTitle = String(title || '').trim()
  if (!normalizedTitle || seen.has(normalizedTitle)) return
  seen.add(normalizedTitle)
  items.push({
    id: String(items.length + 1),
    title: normalizedTitle,
    query: normalizedTitle,
    icon,
  })
}

const configuredConversationQueries = computed<FallbackQuery[]>(() => {
  const items: FallbackQuery[] = []
  const seen = new Set<string>()
  ;(currentDatasource.value?.recommended_questions || []).forEach(question => {
    pushFallbackQuery(items, seen, question, 'fa-message')
  })
  return items
})

const generatedConversationQueries = computed<FallbackQuery[]>(() => {
  const config = selectedSemanticConfigForRecommendations.value
  const metrics = (config?.metrics || []).map(getMetricDisplayName).filter(Boolean)
  const dimensions = (config?.dimensions || []).map(getDimensionDisplayName).filter(Boolean)
  const isTimeDimension = (item: SemanticDimension) => item.logical_type === 'date' || item.logical_type === 'datetime'
  const timeDimension = (config?.dimensions || []).find(isTimeDimension)
  const timeDimensionName = timeDimension ? getDimensionDisplayName(timeDimension) : ''
  const categoricalDimensions = (config?.dimensions || []).filter(item => !isTimeDimension(item)).map(getDimensionDisplayName).filter(Boolean)
  const firstMetric = metrics[0] || ''
  const secondMetric = metrics[1] || ''
  const thirdMetric = metrics[2] || ''
  const firstDimension = dimensions[0] || ''
  const secondDimension = dimensions.find(item => item !== firstDimension) || ''
  const items: FallbackQuery[] = []
  const seen = new Set<string>()

  if (firstMetric) {
    pushFallbackQuery(items, seen, `查询${firstMetric}总览`, 'fa-chart-simple')
  }
  if (firstMetric && firstDimension) {
    pushFallbackQuery(items, seen, `按${firstDimension}统计${firstMetric}`, 'fa-table-cells')
  }
  if (firstMetric && timeDimensionName) {
    pushFallbackQuery(items, seen, `查看${firstMetric}按${timeDimensionName}的变化趋势`, 'fa-chart-line')
  }
  if (firstMetric && secondMetric) {
    pushFallbackQuery(items, seen, `对比${firstMetric}和${secondMetric}`, 'fa-scale-balanced')
  }
  if (firstMetric && secondDimension) {
    pushFallbackQuery(items, seen, `按${secondDimension}分析${firstMetric}`, 'fa-layer-group')
  }
  if (secondMetric && firstDimension) {
    pushFallbackQuery(items, seen, `按${firstDimension}统计${secondMetric}`, 'fa-table-cells')
  }
  if (thirdMetric) {
    pushFallbackQuery(items, seen, `查询${thirdMetric}总览`, 'fa-chart-simple')
  }
  metrics.slice(0, 4).forEach(metric => {
    pushFallbackQuery(items, seen, `查看${metric}排名前十`, 'fa-ranking-star')
  })
  categoricalDimensions.slice(0, 3).forEach(dimension => {
    pushFallbackQuery(items, seen, `按${dimension}查看数据分布`, 'fa-chart-pie')
  })
  if (!firstMetric && firstDimension) {
    pushFallbackQuery(items, seen, `查询${firstDimension}分布`, 'fa-chart-pie')
  }

  return items.slice(0, 20)
})

const fallbackConversationQueries = computed<FallbackQuery[]>(() => {
  return configuredConversationQueries.value.length > 0
    ? configuredConversationQueries.value
    : generatedConversationQueries.value
})

watch(
  () => [
    selectedDatasource.value,
    props.semanticConfigId || '',
    props.semanticConfigSelection?.semantic_config_id || '',
    configuredConversationQueries.value.map(item => item.title).join('|'),
    fallbackConversationQueries.value.map(item => item.title).join('|'),
  ],
  () => {
    recommendationPage.value = 0
  }
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
        label: member.org_name || member.org_code || String(member.database || '') || String(member.member_datasource_id),
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
const focusInput = () => {
  nextTick(() => textareaRef.value?.focus())
}

onMounted(focusInput)

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
          const toolCallCount = getVisibleToolCalls(m.toolCalls).length
          const thinkingSegments = [...(m.thinkingSegments || [])]
          const lastSegment = thinkingSegments[thinkingSegments.length - 1]
          const normalizedReasoningContent = String(reasoningContent || '')
          if (!normalizedReasoningContent.trim()) return
          if (lastSegment && lastSegment.toolCallCount === toolCallCount) {
            lastSegment.content += normalizedReasoningContent
          } else {
            thinkingSegments.push({
              id: `thinking-${Date.now()}-${thinkingSegments.length}`,
              content: normalizedReasoningContent,
              toolCallCount,
            })
          }
          m.thinkingSegments = thinkingSegments
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
                appendMessageSegment(m, { type: 'toolCall', toolCallId: tc.id })
              }
              syncAnalysisPanelFromMessage(m, true)
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
        appendTextSegment(m, msg.content)
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

const submitQuery = async (
  queryText: string,
  options: {
    semanticConfigIdOverride?: string | null
    semanticConfigSelection?: SemanticConfigSelectionInfo | null
    assistantMessageId?: string
    appendUserMessage?: boolean
    messageSkill?: Message['skill'] | null
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

  const selectedSkillSnapshot = selectedSkill.value
  const effectiveSkill = options.messageSkill !== undefined
    ? options.messageSkill
    : (selectedSkillSnapshot
        ? {
            name: selectedSkillSnapshot.name,
            title: selectedSkillSnapshot.title,
            description: selectedSkillSnapshot.description,
          }
        : null)
  const normalizedQuery = queryText.trim()
  const requestText = buildSkillRequestText(normalizedQuery, effectiveSkill || null)
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
      content: requestText,
      timestamp: new Date(),
      skill: effectiveSkill || undefined,
    }
    const assistantMessage: Message = {
      id: assistantMessageId,
      role: 'assistant',
      content: '',
      timestamp: new Date(),
      isStreaming: true,
      toolCalls: [],
      semanticConfigSelection: effectiveSemanticConfigSelection || undefined,
      skill: effectiveSkill || undefined,
    }
    messages.value.push(userMessage, assistantMessage)
  } else if (assistantMessageId) {
    const msg = messages.value.find((m) => m.id === assistantMessageId)
    if (msg) {
      msg.content = ''
      msg.error = undefined
      msg.isStreaming = true
      msg.toolCalls = []
      msg.semanticConfigSelection = effectiveSemanticConfigSelection || undefined
      msg.skill = effectiveSkill || undefined
    }
  }

  input.value = ''
  selectedSkill.value = null
  resetSemanticQuestionDraft()
  isGenerating.value = true
  toolCallsMap.value.clear()
  closeTokenUsagePanel()

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
    selectedSkill.value = selectedSkillSnapshot
    return
  }
  if (props.ensureConversationDatasource && !await props.ensureConversationDatasource()) {
    if (userMessageId) {
      messages.value = messages.value.filter(m => m.id !== userMessageId && m.id !== assistantMessageId)
    }
    isGenerating.value = false
    input.value = normalizedQuery
    selectedSkill.value = selectedSkillSnapshot
    return
  }

  try {
    await generateSqlStream(
      requestText,
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
          finishAssistantMessage(msg)
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
          const msg = messages.value[msgIndex]!
          msg.error = error
          finishAssistantMessage(msg)
        }
        handleAnalysisStreamEnd()
      },
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
      // 未手动选择时由主模型通过检索工具决定本次查询使用的指标集。
      options.semanticConfigIdOverride !== undefined ? options.semanticConfigIdOverride : (props.semanticConfigId || null),
      // modelId - 选中模型的 value
      selectedModelValue.value,
      selectedMemberDatasourceIds.value,
      activeAgentId,
      null,
      (payload) => handleDashboardSpec(payload, assistantMessageId),
      undefined,
      (payload) => {
        tokenUsage.value = payload.usage || null
        updateAssistantFinishedAt(assistantMessageId, payload.timestamp)
      },
      (usage) => {
        tokenUsage.value = usage
      },
      effectiveSemanticConfigSelection,
      enableDeepThinking.value
    )
  } catch (err) {
    console.error('生成失败:', err)
    const msgIndex = messages.value.findIndex((m) => m.id === assistantMessageId)
    if (msgIndex >= 0 && messages.value[msgIndex]) {
      const msg = messages.value[msgIndex]!
      msg.error = '生成失败，请重试'
      finishAssistantMessage(msg)
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

// 停止生成
const handleStop = () => {
  stopGeneration()
  // 标记当前流式消息为结束
  const streamingMsg = messages.value.find(m => m.isStreaming)
  if (streamingMsg) {
    finishAssistantMessage(streamingMsg)
  }
  isGenerating.value = false
  resetSemanticQuestionDraft()
  handleAnalysisStreamEnd()
}

// 键盘事件
const handleKeyDown = (e: KeyboardEvent) => {
  if (showSkillCommand.value) {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      highlightedSkillIndex.value = Math.min(highlightedSkillIndex.value + 1, Math.max(displayedCommandSkills.value.length - 1, 0))
      return
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault()
      highlightedSkillIndex.value = Math.max(highlightedSkillIndex.value - 1, 0)
      return
    }
    if ((e.key === 'Enter' || e.key === 'Tab') && displayedCommandSkills.value[highlightedSkillIndex.value]) {
      e.preventDefault()
      const skill = displayedCommandSkills.value[highlightedSkillIndex.value]
      if (skill) selectCommandSkill(skill)
      return
    }
    if (e.key === 'Escape') {
      e.preventDefault()
      highlightedSkillIndex.value = 0
      return
    }
  }
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
  nextTick(() => textareaRef.value?.focus())
}

const selectCommandSkill = (skill: SkillInfo) => {
  selectedSkill.value = skill
  resetSemanticQuestionDraft()
  input.value = input.value.replace(/^\/[^\s/]*\s?/, '')
  highlightedSkillIndex.value = 0
  nextTick(() => textareaRef.value?.focus())
}

const clearSelectedSkill = () => {
  selectedSkill.value = null
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

const appendMessageSegment = (message: Message, segment: MessageSegment) => {
  const segments = message.segments || []
  if (segment.type === 'toolCall') {
    if (segments.some(item => item.type === 'toolCall' && item.toolCallId === segment.toolCallId)) return
  }
  message.segments = [...segments, segment]
}

const appendTextSegment = (message: Message, content: string) => {
  const visibleContent = getConversationDisplayContent(content)
  const segments = [...(message.segments || [])]
  const lastSegment = segments[segments.length - 1]
  if (lastSegment?.type === 'text') {
    if (!visibleContent && !content) return
    segments[segments.length - 1] = {
      ...lastSegment,
      content: `${lastSegment.content}${content}`,
    }
    message.segments = segments
    return
  }
  if (!visibleContent) return
  message.segments = [...segments, { type: 'text', content }]
}

const getConversationDisplayContent = (content: string): string => {
  return stripTechnicalContent(content)
}

const buildAnalysisDataFromMessage = (msg: Message): AnalysisData => {
  return {
    roundId: msg.id,
    thinkingSegments: msg.thinkingSegments || [],
    toolCalls: getVisibleToolCalls(msg.toolCalls),
    semanticConfigSelection: msg.semanticConfigSelection || null,
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

const stripTechnicalContent = (content: string): string => {
  return hideSemanticConfigSelectionPayloads(String(content || '').trim())
    .replace(/<query_result>[\s\S]*?<\/query_result>/g, '')
    .replace(/<chart_config>[\s\S]*?<\/chart_config>/g, '')
    .trim()
}

// 判断是否已输出可见正文，排除思考标签和占位文案。
const hasVisibleAssistantContent = (content: string): boolean => {
  const text = getConversationDisplayContent(content)
  return Boolean(text && text !== '正在思考...')
}

// 首 token 前已有气泡占位时，底部状态行不重复显示。
const shouldShowStreamingStatus = (message: Message): boolean => {
  return hasVisibleAssistantContent(message.content) || getVisibleToolCalls(message.toolCalls).length > 0 || Boolean(message.semanticConfigSelection)
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

type OrderedMessageSegment =
  | { type: 'text'; content: string }
  | { type: 'queryResult'; toolCall: ToolCallInfo }
  | { type: 'chart'; toolCall: ToolCallInfo }

const getToolCallById = (msg: Message, toolCallId: string): ToolCallInfo | null => {
  return msg.toolCalls?.find(tc => tc.tool_call_id === toolCallId) || null
}

const isRenderableToolCall = (toolCall: ToolCallInfo | null | undefined): toolCall is ToolCallInfo => {
  return Boolean(
    toolCall?.renderId
    && (toolCall.renderType === 'queryResult' || toolCall.renderType === 'chart')
  )
}

const toOrderedToolSegment = (toolCall: ToolCallInfo): OrderedMessageSegment => {
  return toolCall.renderType === 'chart'
    ? { type: 'chart', toolCall }
    : { type: 'queryResult', toolCall }
}

const getOrderedMessageSegments = (msg: Message): OrderedMessageSegment[] => {
  const segments = msg.segments || []
  const orderedSegments: OrderedMessageSegment[] = []
  for (const segment of segments) {
    if (segment.type === 'text') {
      const content = getConversationDisplayContent(segment.content)
      if (content) {
        orderedSegments.push({ type: 'text', content })
      }
      continue
    }
    if (segment.type === 'toolCall') {
      const toolCall = getToolCallById(msg, segment.toolCallId)
      if (isRenderableToolCall(toolCall)) {
        orderedSegments.push(toOrderedToolSegment(toolCall))
      }
    }
  }
  if (orderedSegments.length > 0) return orderedSegments
  const fallbackSegments: OrderedMessageSegment[] = []
  const summaryText = getSummaryText(msg.content)
  if (summaryText) {
    fallbackSegments.push({ type: 'text', content: summaryText })
  }
  for (const toolCall of getVisibleToolCalls(msg.toolCalls)) {
    if (isRenderableToolCall(toolCall)) {
      fallbackSegments.push(toOrderedToolSegment(toolCall))
    }
  }
  return fallbackSegments
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
  let text = getConversationDisplayContent(content)
  // 移除所有技术标签和内容
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
  if (!hasCompletedQueryResult(msg.toolCalls) && !data.semanticConfigSelection && !data.thinkingSegments?.length && data.toolCalls.length === 0) return null
  return data
}

// 获取流式阶段文案
const getStreamingStageText = (msg: Message): string => {
  const toolCalls = getVisibleToolCalls(msg.toolCalls)
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

const shouldShowStreamingProgress = (msg: Message): boolean => {
  return Boolean(
    msg.thinkingSegments?.length
    || getVisibleToolCalls(msg.toolCalls).length > 0
    || msg.semanticConfigSelection
  )
}

const padDatePart = (value: number): string => String(value).padStart(2, '0')

const parseMessageDateTime = (value?: Date | string | null): Date | null => {
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : value
  }
  if (typeof value === 'string' && value.trim()) {
    const parsed = new Date(value.trim().replace(' ', 'T'))
    return Number.isNaN(parsed.getTime()) ? null : parsed
  }
  return null
}

const formatMessageDateTime = (value?: Date | string | null): string => {
  const date = parseMessageDateTime(value)
  if (!date) return ''
  return [
    date.getFullYear(),
    padDatePart(date.getMonth() + 1),
    padDatePart(date.getDate()),
  ].join('-') + ' ' + [
    padDatePart(date.getHours()),
    padDatePart(date.getMinutes()),
    padDatePart(date.getSeconds()),
  ].join(':')
}

const finishAssistantMessage = (msg: Message) => {
  msg.isStreaming = false
}

const updateAssistantFinishedAt = (messageId: string, timestamp?: string) => {
  const finishedAt = parseMessageDateTime(timestamp)
  if (!finishedAt) return
  const msg = messages.value.find(item => item.id === messageId)
  if (msg?.role === 'assistant') {
    msg.timestamp = finishedAt
  }
}

const shouldShowMessageAnalysisButton = (msg: Message): boolean => {
  return Boolean(createAnalysisData(msg))
}

const getCopyableAssistantContent = (msg: Message): string => {
  const orderedText = getOrderedMessageSegments(msg)
    .filter((segment): segment is { type: 'text'; content: string } => segment.type === 'text')
    .map(segment => getConversationDisplayContent(segment.content))
    .filter(Boolean)
    .join('\n\n')
    .trim()
  return orderedText || getSummaryText(msg.content)
}

const shouldShowMessageCopyButton = (msg: Message): boolean => {
  return Boolean(getCopyableAssistantContent(msg))
}

const handleCopyAssistantMessage = async (msg: Message) => {
  const text = getCopyableAssistantContent(msg)
  if (!text) {
    ElMessage.info('暂无可复制内容')
    return
  }
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制')
  } catch (err) {
    console.error('复制回复失败:', err)
    ElMessage.error('复制失败，请稍后重试')
  }
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
        :style="{ flex: isAnalysisVisible ? '1 1 0' : '1 1 auto' }"
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
      <!-- 无消息时：推荐问题 -->
      <div v-else-if="messages.length === 0" class="flex-1 flex justify-center pt-8 md:pt-10">
        <div class="w-[774px] max-w-[90%]">
          <div class="mb-5 rounded-2xl bg-gradient-to-r from-blue-50/80 via-white to-indigo-50/80 px-5 py-4 text-left shadow-[0_8px_24px_rgba(15,23,42,0.04)]">
            <div class="text-[17px] font-semibold leading-7 text-slate-800">你好，我是数据智能体</div>
          </div>

          <div v-if="selectedDatasource" class="max-w-[560px]">
            <div class="mb-2 flex items-center justify-between gap-3">
              <div class="flex items-center gap-1.5 text-[13px] font-medium text-slate-400">
                <i class="fa-regular fa-lightbulb text-[12px] text-amber-400"></i>
                <span>你可以这样问：</span>
              </div>
              <button
                v-if="canRefreshRecommendations"
                type="button"
                class="inline-flex h-7 shrink-0 items-center gap-1.5 rounded-lg border border-transparent bg-transparent px-2 text-[12px] text-slate-400 transition-colors hover:bg-slate-100 hover:text-blue-600"
                title="换一批推荐问题"
                @click="refreshRecommendations"
              >
                <i class="fa-solid fa-rotate-right text-[10px]"></i>
                <span>换一批</span>
              </button>
            </div>
            <QuickQueries
              :key="recommendationContentType"
              :datasource-id="selectedDatasource"
              mode="conversation"
              content-type="queries"
              :fallback-queries="fallbackConversationQueries"
              :max-items="5"
              :page-offset="recommendationOffset"
              prefer-fallback
              :load-favorites="false"
              compact
              @select-query="handleQuickQuerySelect"
              @select-skill="handleSkillSelect"
            />
          </div>
        </div>
      </div>

      <!-- 有消息时：对话列表 -->
      <div v-else class="max-w-[774px] mx-auto w-[90%]">
        <div v-for="msg in messages" :key="msg.id" class="mb-5">
          <!-- 用户消息 -->
          <div v-if="msg.role === 'user'" class="flex flex-col items-end gap-1">
            <div class="max-w-[80%] px-4 py-2.5 rounded-2xl rounded-br-md bg-[#1677FF]/10 text-[#1F3A56] text-sm leading-relaxed shadow-[0_2px_8px_rgba(59,130,246,0.12)]">{{ msg.content }}</div>
            <div class="text-[10px] text-gray-400 mr-1">
              {{ formatMessageDateTime(msg.timestamp) }}
            </div>
          </div>

          <!-- AI 消息 -->
          <div v-else class="flex flex-col gap-3">
            <div class="max-w-full p-4 rounded-2xl rounded-bl-md bg-white border border-black/[0.06] shadow-[0_2px_8px_rgba(0,0,0,0.04)]">
              <!-- 简洁模式：查询完成后只显示结果 -->
              <template v-if="shouldShowCompactMode(msg)">
                <div class="flex flex-col gap-4">
                  <template
                    v-for="(segment, segmentIndex) in getOrderedMessageSegments(msg)"
                    :key="`${segment.type}-${segmentIndex}-${segment.type !== 'text' ? segment.toolCall.tool_call_id : segmentIndex}`"
                  >
                    <div
                      v-if="segment.type === 'text'"
                      class="text-sm text-gray-700 leading-relaxed markdown-body"
                    >
                      <span v-html="renderSummaryMarkdown(segment.content)"></span>
                    </div>
                    <QueryResultPanel
                      v-else-if="segment.type === 'queryResult'"
                      class="max-h-[46vh]"
                      :result-data="segment.toolCall.renderId!"
                      :sql="extractGeneratedSqlFromToolCall(segment.toolCall)"
                      :semantic-params="segment.toolCall.semanticParams"
                      :on-save="handleSaveToDashboard"
                    />
                    <div
                      v-else
                      class="flex items-center gap-2.5 rounded-xl border border-dashed border-black/10 bg-[linear-gradient(135deg,#fafbfc_0%,#f5f7fa_100%)] p-5 text-center text-[13px] text-gray-500"
                    >
                      <i class="fa-solid fa-chart-column text-lg bg-[linear-gradient(135deg,#1f82d9_0%,#135589_100%)] bg-clip-text text-transparent"></i>
                      <span>图表配置已生成</span>
                    </div>
                  </template>
                </div>
                <span v-if="msg.isStreaming" class="mt-2 inline-block animate-pulse text-gray-400">▌</span>
              </template>

              <!-- 流式中：数据查询显示加载状态，普通问答直接流式输出 -->
              <template v-else-if="msg.isStreaming">
                <!-- 有思考/工具调用 → 数据查询，过程在侧边栏展示 -->
                <div
                  v-if="shouldShowStreamingProgress(msg)"
                  class="px-1 py-1"
                >
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
                  <StructuredMessage
                    v-if="getConversationDisplayContent(msg.content)"
                    class="mt-3"
                    :content="getConversationDisplayContent(msg.content)"
                    :is-streaming="false"
                  />
                </div>
                <StructuredMessage
                  v-else-if="getConversationDisplayContent(msg.content)"
                  :content="getConversationDisplayContent(msg.content)"
                  :is-streaming="true"
                />
                <!-- 普通问答 → 直接流式显示文字 -->
                <div v-else-if="!getConversationDisplayContent(msg.content)" class="flex items-center gap-2 text-gray-500 text-sm">
                  <i class="fa-solid fa-spinner fa-spin"></i>
                  <span>正在思考...</span>
                </div>
              </template>

              <!-- 非流式、无查询结果：显示内容（去除思考过程） -->
              <template v-else>
                <StructuredMessage
                  v-if="getConversationDisplayContent(msg.content)"
                  :content="getConversationDisplayContent(msg.content)"
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

                <button
                  v-if="getVisibleToolCalls(msg.toolCalls).length > 0 && !getConversationDisplayContent(msg.content)"
                  class="mt-2 h-7 px-2.5 rounded-md border border-blue-100 bg-blue-50 text-xs text-blue-600 hover:bg-blue-100"
                  @click="handleViewAnalysis(msg)"
                >
                  查看工具调用
                </button>
              </template>

              <!-- 错误消息 -->
              <div v-if="msg.error" class="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-red-500/10 text-red-600 text-[13px] mt-2">
                <i class="fa-solid fa-circle-exclamation"></i>
                <span>{{ msg.error }}</span>
              </div>
            </div>

            <div
              v-if="!msg.isStreaming && (shouldShowMessageCopyButton(msg) || shouldShowMessageAnalysisButton(msg))"
              class="flex items-center gap-2 px-1"
            >
              <button
                v-if="shouldShowMessageCopyButton(msg)"
                class="inline-flex h-[26px] min-w-[58px] shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-md bg-transparent px-2 text-[11px] text-gray-500 transition-all duration-200 hover:bg-blue-500/5 hover:text-blue-500"
                @click="handleCopyAssistantMessage(msg)"
              >
                <i class="fa-regular fa-copy text-[11px]"></i>
                <span>复制</span>
              </button>
              <button
                v-if="shouldShowMessageAnalysisButton(msg)"
                :class="[
                  'inline-flex h-[26px] min-w-[58px] shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-md bg-transparent px-2 text-[11px] transition-all duration-200',
                  isAnalysisPanelOpen && currentAnalysisRoundId === msg.id
                    ? 'bg-blue-500/10 text-blue-500'
                    : 'text-gray-500 hover:bg-blue-500/5 hover:text-blue-500'
                ]"
                @click="handleViewAnalysis(msg)"
              >
                <i class="fa-solid fa-diagram-project text-[11px]"></i>
                <span>分析</span>
              </button>
              <div class="ml-auto flex items-center gap-3">
                <AiGeneratedNotice />
                <span class="whitespace-nowrap text-[11px] text-gray-400">
                  {{ formatMessageDateTime(msg.timestamp) }}
                </span>
              </div>
            </div>

            <!-- 生成状态 -->
            <div v-if="msg.isStreaming && shouldShowStreamingStatus(msg)" class="flex items-center gap-2 py-1">
              <span class="text-xs font-medium text-gray-500">{{ shouldShowStreamingProgress(msg) ? getStreamingStageText(msg) : (hasVisibleAssistantContent(msg.content) ? '正在生成' : '正在思考') }}</span>
              <div class="flex gap-1">
                <span v-for="i in 3" :key="i" class="w-1.5 h-1.5 rounded-full bg-[#1677FF] animate-pulse" :style="{ animationDelay: `${i * 0.2}s` }"></span>
              </div>
              <button
                v-if="shouldShowStreamingProgress(msg) && isAnalysisPanelOpen && !showAnalysisPanel"
                class="ml-auto h-6 px-2 border border-blue-200 rounded bg-blue-50 text-[11px] text-blue-600 hover:bg-blue-100 hover:border-blue-300"
                @click="emit('switchToAnalysis')"
              >
                查看分析
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 底部输入区 -->
    <div v-if="!isLoadingHistory || messages.length > 0" class="p-4 bg-gray-50">
      <div class="max-w-[774px] mx-auto w-[90%]">
        <form class="relative" @submit.prevent="handleSubmit">
          <div
            :class="[
              'chat-input-box rounded-2xl bg-white border border-black/[0.08] shadow-[0_2px_12px_rgba(0,0,0,0.06),0_0_0_1px_rgba(0,0,0,0.02)] transition-all duration-200',
              inputFocused && 'border-blue-400/40 shadow-[0_2px_12px_rgba(0,0,0,0.06),0_0_0_3px_rgba(0,122,255,0.1)]'
            ]"
          >
            <div class="min-h-[54px] px-4 pt-4 pb-2">
              <div
                v-if="selectedSkillTitle"
                class="mb-2 flex w-fit items-center gap-1.5 rounded-full bg-blue-50 px-2 py-1 text-xs text-blue-700"
              >
                <i class="fa-solid fa-wand-magic-sparkles text-[10px]"></i>
                <span class="max-w-[140px] truncate">{{ selectedSkillTitle }}</span>
                <button type="button" class="text-blue-400 hover:text-blue-700" @click="clearSelectedSkill">
                  <i class="fa-solid fa-xmark text-[10px]"></i>
                </button>
              </div>
              <textarea
                ref="textareaRef"
                v-model="input"
                placeholder="请输入问题查询数据，输入 / 可选择技能"
                class="block w-full min-h-[26px] max-h-[200px] border-none bg-transparent text-[15px] leading-relaxed text-gray-700 resize-none outline-none overflow-y-hidden placeholder:text-gray-400"
                @keydown="handleKeyDown"
                @focus="inputFocused = true"
                @blur="inputFocused = false"
              ></textarea>
            </div>
              <div class="flex items-center justify-between gap-2 px-4 py-2 pb-3">
                <div class="chat-input-toolbar flex min-w-0 flex-1 items-center gap-2">
                <button
                  v-if="selectedAgent === 'chat_data'"
                  type="button"
                  class="flex h-6 items-center gap-1.5 whitespace-nowrap rounded-full px-2 text-xs text-gray-500 hover:bg-gray-100 hover:text-gray-700"
                  title="技能管理"
                  @click="isSkillManagerOpen = true"
                >
                  <i class="fa-solid fa-wand-magic-sparkles text-[10px]"></i>
                  <span class="toolbar-label">技能</span>
                </button>
                <div ref="agentSelectorRef" class="relative">
                  <button
                    type="button"
                    class="flex items-center gap-1.5 whitespace-nowrap px-2 py-1 rounded-full border-none bg-transparent text-xs text-gray-500 outline-none cursor-pointer hover:bg-black/[0.05]"
                    @click="isAgentSelectorOpen = !isAgentSelectorOpen"
                  >
                    <i class="fa-solid fa-robot text-[10px] text-gray-400"></i>
                    <span class="toolbar-label">{{ selectedAgentLabel }}</span>
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
                <div class="flex items-center gap-1.5 whitespace-nowrap px-2 py-1 rounded-full hover:bg-black/[0.05]">
                  <i class="fa-solid fa-microchip text-[10px] text-gray-400"></i>
                  <select
                    v-model="selectedModel"
                    class="model-select max-w-[150px] truncate border-none bg-transparent text-xs text-gray-500 outline-none cursor-pointer appearance-none pr-3"
                  >
                    <option v-for="m in modelOptions" :key="m.id" :value="m.id">{{ m.label }}</option>
                  </select>
                  <i class="fa-solid fa-chevron-down text-[8px] text-gray-400 -ml-2"></i>
                </div>
                <button
                  type="button"
                  :class="[
                    'flex items-center gap-1.5 whitespace-nowrap px-2 py-1 rounded-full border-none text-xs outline-none cursor-pointer transition-colors',
                    enableDeepThinking ? 'bg-blue-50 text-blue-600' : 'text-gray-500 hover:bg-black/[0.05]'
                  ]"
                  :title="enableDeepThinking ? '已开启深度思考' : '开启深度思考'"
                  :aria-pressed="enableDeepThinking"
                  @click="enableDeepThinking = !enableDeepThinking"
                >
                  <i class="fa-solid fa-brain text-[10px]"></i>
                  <span class="toolbar-label">思考</span>
                </button>
                <ExecutionTargetPicker
                  v-if="!hideExecutionTargetSelector && executionTargets.length > 0"
                  v-model="selectedMemberDatasourceIds"
                  :targets="executionTargets"
                  :multiple="executionTargetMultiple"
                />
                <div v-if="tokenUsageDisplay" class="relative">
                  <button
                    ref="tokenUsageButtonRef"
                    type="button"
                    class="flex h-7 shrink-0 items-center gap-1.5 rounded-full border-none bg-transparent px-1.5 text-xs text-gray-500 cursor-pointer hover:bg-gray-100 disabled:cursor-default disabled:hover:bg-transparent"
                    :disabled="!tokenUsageDisplay"
                    :title="tokenUsageButtonTitle"
                    @click="toggleTokenUsagePanel"
                  >
                    <span class="relative flex h-5 w-5 shrink-0 items-center justify-center rounded-full" :style="tokenUsageRingStyle">
                      <span class="flex h-4 w-4 items-center justify-center rounded-full bg-white text-[8px] font-semibold leading-none text-gray-500">
                        {{ tokenUsagePercentageText }}
                      </span>
                    </span>
                    <span class="toolbar-label">上下文</span>
                  </button>
                  <div
                    v-if="tokenUsagePanelOpen"
                    class="fixed z-[10000] rounded-lg border border-gray-200 bg-white shadow-lg p-3 text-xs text-gray-600"
                    :style="tokenUsagePanelStyle"
                  >
                    <div class="flex items-center justify-between mb-2">
                      <div class="font-semibold text-gray-800">输入预算明细</div>
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
                        <span class="text-[11px] text-gray-400">本次输入内容</span>
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
          <div
            v-if="showSkillCommand"
            class="absolute left-0 right-0 bottom-full z-20 mb-2 overflow-hidden rounded-xl border border-slate-200 bg-white text-left shadow-[0_12px_32px_rgba(15,23,42,0.16)]"
          >
            <div v-if="isCommandSkillLoading" class="px-3 py-3 text-xs text-slate-400">正在加载技能</div>
            <div v-else-if="displayedCommandSkills.length === 0" class="px-3 py-3 text-xs text-slate-400">没有匹配的技能</div>
            <template v-else>
              <button
                v-for="(skill, index) in displayedCommandSkills"
                :key="skill.name"
                type="button"
                :class="[
                  'flex w-full items-start gap-2 border-none px-3 py-2.5 text-left transition-colors',
                  index === highlightedSkillIndex ? 'bg-blue-50' : 'bg-white hover:bg-slate-50'
                ]"
                @mousedown.prevent="selectCommandSkill(skill)"
              >
                <i class="fa-solid fa-wand-magic-sparkles mt-0.5 text-xs text-blue-500"></i>
                <span class="min-w-0 flex-1">
                  <span class="block truncate text-sm font-medium text-slate-800">{{ skill.title || skill.name }}</span>
                  <span class="mt-0.5 block truncate text-xs text-slate-400">{{ skill.description || skill.name }}</span>
                </span>
                <span
                  :class="[
                    'shrink-0 rounded-md px-2 py-0.5 text-[11px] leading-5',
                    skill.source === 'system' ? 'bg-slate-100 text-slate-500' : 'bg-blue-50 text-blue-600'
                  ]"
                >
                  {{ skill.source === 'system' ? '系统' : '用户' }}
                </span>
              </button>
            </template>
          </div>
        </form>
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
          class="h-full min-w-[280px] max-w-[60%] border-l border-gray-200 bg-white overflow-hidden"
          :style="{ flex: `0 0 ${analysisPanelWidth}%` }"
        >
          <AnalysisPanel
            :is-open="isAnalysisPanelOpen"
            :data="analysisPanelData"
            :datasource-id="selectedDatasource"
            :execution-targets="executionTargets"
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
      :member-datasource-id="saveMemberDatasourceId"
      :member-datasource-ids="saveMemberDatasourceIds"
      :execution-targets="executionTargets"
    />
    <el-dialog v-model="isSkillManagerOpen" width="min(1280px, 92vw)" title="技能管理" append-to-body>
      <div class="h-[72vh]">
        <SkillManager default-skill-scope="query" agent="query" />
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
.chat-input-box {
  container-type: inline-size;
}

@container (max-width: 560px) {
  .chat-input-toolbar {
    gap: 4px;
  }

  .chat-input-toolbar .toolbar-label {
    display: none;
  }

  .chat-input-toolbar .model-select {
    max-width: 92px;
  }
}

@container (max-width: 430px) {
  .chat-input-toolbar .model-select {
    max-width: 0;
    padding-right: 0;
  }
}

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
