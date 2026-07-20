<script setup lang="ts">
/**
 * SQL 对话组件
 * 支持结构化消息和工具调用展示
 */
import { ref, computed, watch, nextTick, toRef, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import MarkdownIt from 'markdown-it'
import type { ChatDataModelOption, ChatDataTokenUsage, DataSource, ToolCallInfo, LangGraphMessage, Message, MessageSegment, ExecutionTargetOption, SkillInfo, DashboardSpecPayload, SemanticConfigSelectionInfo, SemanticConfigItem, SemanticDimension, SemanticMetric, ThinkingSegment } from '../types'
import { createDashboardFromSpec, getChatDataConfig } from '../api'
import type { Dashboard } from '../api/dashboard'
import { useSqlGenerator } from '../composables/useSqlGenerator'
import { useConversationHistory } from '../composables/useConversationHistory'
import { useDeepThinking } from '../composables/useDeepThinking'
import { useSkillCommandMenu } from '../composables/useSkillCommandMenu'
import { extractGeneratedSqlFromToolCall, extractSemanticParamsFromArgs, getRenderableQueryResultData, parseQueryResultEnvelope } from '../utils/semanticTool'
import QueryResultPanel from './QueryResultPanel.vue'
import QuickQueries, { type FallbackQuery } from './QuickQueries.vue'
import SaveToPanelModal from './SaveToPanelModal.vue'
import ToolCallList from './ToolCallList.vue'
import ToolCallDetailPanel from './ToolCallDetailPanel.vue'
import ConversationSidePanel from './ConversationSidePanel.vue'
import ExecutionTargetPicker from './ExecutionTargetPicker.vue'
import SkillManager from './SkillManager.vue'
import AssistantMessageFooter from './AssistantMessageFooter.vue'
import ConversationInputBar from './ConversationInputBar.vue'
import ConversationTokenUsage from './ConversationTokenUsage.vue'
import SkillCommandMenu from './SkillCommandMenu.vue'
import { formatDateTimeValue, parseDateTimeValue, hideSemanticConfigSelectionPayloads } from '../utils'

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
  agentChange: [agentId: 'chat_data' | 'chat_data_data_analyze']
  dashboardCreated: [dashboard: Dashboard]
  semanticConfigSelected: [payload: SemanticConfigSelectionInfo]
  'update:selectedMemberDatasourceIds': [value: string[]]
  'update:selectedModelId': [value: string]
}>()

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
const recommendationContentType = computed<'skills' | 'queries'>(() =>
  selectedAgent.value === 'chat_data_data_analyze' ? 'skills' : 'queries'
)
const modelOptions = ref<ChatDataModelOption[]>([])
const selectedModel = computed({
  get: () => props.selectedModelId || '',
  set: (value: string) => emit('update:selectedModelId', value),
})
const { enableDeepThinking } = useDeepThinking()
const tokenUsage = ref<ChatDataTokenUsage | null>(null)
const tokenUsagePanelRef = ref<InstanceType<typeof ConversationTokenUsage> | null>(null)
const toolCallsMap = ref<Map<string, ToolCallInfo>>(new Map())
const activeToolCallId = ref<string | null>(null)
const sidePanelOpen = ref(false)
const collapsedThinkingSegmentIds = ref<Set<string>>(new Set())

const findToolCallById = (toolCallId: string | null): ToolCallInfo | null => {
  if (!toolCallId) return null
  const cached = toolCallsMap.value.get(toolCallId)
  if (cached) return cached
  for (const message of messages.value) {
    const toolCall = message.toolCalls?.find(item => item.tool_call_id === toolCallId)
    if (toolCall) return toolCall
  }
  return null
}

const activeToolCall = computed(() => findToolCallById(activeToolCallId.value))
const selectToolCall = (toolCall: ToolCallInfo) => {
  activeToolCallId.value = toolCall.tool_call_id
  sidePanelOpen.value = true
}
const closeToolDetailPanel = () => {
  sidePanelOpen.value = false
}

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

const resetTokenUsagePanel = () => {
  tokenUsage.value = null
  tokenUsagePanelRef.value?.closePanel()
}

const closeTokenUsagePanel = () => {
  tokenUsagePanelRef.value?.closePanel()
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
  loadModelOptions()
})

const isSkillManagerOpen = ref(false)
const creatingDashboardMessageId = ref<string | null>(null)
const buildSkillRequestText = (text: string, skill: Pick<SkillInfo, 'name' | 'title' | 'description'> | null) => {
  const normalizedText = String(text || '').trim()
  if (!skill) return normalizedText
  return `使用「${skill.name}」技能回答：${normalizedText}`
}
const {
  selectedSkill,
  selectedSkillTitle,
  displayedCommandSkills,
  isCommandSkillLoading,
  highlightedSkillIndex,
  showSkillCommand,
  selectCommandSkill: selectCommandSkillBase,
  clearSelectedSkill: clearSelectedSkillBase,
  handleSkillCommandKeyDown,
} = useSkillCommandMenu({
  input,
  enabled: computed(() => selectedAgent.value === 'chat_data'),
  agent: 'query',
  loadErrorText: '获取专家技能失败:',
})

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
        activeToolCallId.value = null
        sidePanelOpen.value = false
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

const handleDisabledAgentClick = () => ElMessage.info('功能暂未开放')

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
const inputBarRef = ref<InstanceType<typeof ConversationInputBar> | null>(null)
const focusInput = () => {
  inputBarRef.value?.focus()
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
  nextTick(() => inputBarRef.value?.resizeTextarea())
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
          const normalizedReasoningContent = String(reasoningContent || '')
          appendThinkingSegment(m, normalizedReasoningContent)
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
        const queryResultData = getRenderableQueryResultData(msg.content)
        if (queryResultData) {
          renderId = queryResultData
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
      msg.thinkingSegments = []
      msg.segments = []
      msg.semanticConfigSelection = effectiveSemanticConfigSelection || undefined
      msg.skill = effectiveSkill || undefined
    }
  }

  input.value = ''
  selectedSkill.value = null
  resetSemanticQuestionDraft()
  isGenerating.value = true
  toolCallsMap.value.clear()
  activeToolCallId.value = null
  sidePanelOpen.value = false
  closeTokenUsagePanel()

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
}

// 键盘事件
const handleKeyDown = (e: KeyboardEvent) => {
  if (handleSkillCommandKeyDown(e, selectCommandSkill)) return
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
  inputBarRef.value?.focus()
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
  inputBarRef.value?.focus()
}

const handleSkillSelect = (skill: SkillInfo) => {
  selectedSkill.value = skill
  resetSemanticQuestionDraft()
  inputBarRef.value?.focus()
}

const selectCommandSkill = (skill: SkillInfo) => {
  selectCommandSkillBase(skill)
  resetSemanticQuestionDraft()
  inputBarRef.value?.focus()
}

const clearSelectedSkill = () => {
  clearSelectedSkillBase()
  inputBarRef.value?.focus()
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
  if (segment.type === 'thinkingSegment') {
    if (segments.some(item => item.type === 'thinkingSegment' && item.thinkingSegmentId === segment.thinkingSegmentId)) return
  }
  message.segments = [...segments, segment]
}

const appendThinkingSegment = (message: Message, content: string) => {
  const normalizedContent = String(content || '')
  if (!normalizedContent.trim()) return
  const toolCallCount = getVisibleToolCalls(message.toolCalls).length
  const thinkingSegments = [...(message.thinkingSegments || [])]
  const lastSegment = thinkingSegments[thinkingSegments.length - 1]
  if (lastSegment && lastSegment.toolCallCount === toolCallCount) {
    lastSegment.content += normalizedContent
    message.thinkingSegments = thinkingSegments
    appendMessageSegment(message, { type: 'thinkingSegment', thinkingSegmentId: lastSegment.id })
    return
  }
  const thinkingSegment: ThinkingSegment = {
    id: `thinking-${Date.now()}-${thinkingSegments.length}`,
    content: normalizedContent,
    toolCallCount,
  }
  message.thinkingSegments = [...thinkingSegments, thinkingSegment]
  appendMessageSegment(message, { type: 'thinkingSegment', thinkingSegmentId: thinkingSegment.id })
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

const stripTechnicalContent = (content: string): string => {
  const cleaned = hideSemanticConfigSelectionPayloads(String(content || '').trim())
  if (parseQueryResultEnvelope(cleaned)) return ''
  return cleaned
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
  return hasVisibleAssistantContent(message.content)
    || Boolean(message.thinkingSegments?.length)
    || getVisibleToolCalls(message.toolCalls).length > 0
    || Boolean(message.semanticConfigSelection)
}

const getToolCallById = (msg: Message, toolCallId: string): ToolCallInfo | null => {
  return msg.toolCalls?.find(tc => tc.tool_call_id === toolCallId) || null
}

const getRenderableQueryResultToolCall = (msg: Message, toolCallId: string): ToolCallInfo | null => {
  const toolCall = getToolCallById(msg, toolCallId)
  return toolCall?.renderType === 'queryResult' && toolCall.renderId ? toolCall : null
}

const getThinkingSegment = (message: Message, thinkingSegmentId: string): ThinkingSegment | null => {
  return message.thinkingSegments?.find(segment => segment.id === thinkingSegmentId) || null
}

const isThinkingSegmentCollapsed = (message: Message, thinkingSegmentId: string): boolean => {
  if (message.isStreaming) return false
  return collapsedThinkingSegmentIds.value.has(`${message.id}:${thinkingSegmentId}`)
}

const toggleThinkingSegment = (message: Message, thinkingSegmentId: string) => {
  if (!getThinkingSegment(message, thinkingSegmentId)) return
  const key = `${message.id}:${thinkingSegmentId}`
  const next = new Set(collapsedThinkingSegmentIds.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  collapsedThinkingSegmentIds.value = next
}

const finishAssistantMessage = (msg: Message) => {
  msg.isStreaming = false
}

const updateAssistantFinishedAt = (messageId: string, timestamp?: string) => {
  const finishedAt = parseDateTimeValue(timestamp)
  if (!finishedAt) return
  const msg = messages.value.find(item => item.id === messageId)
  if (msg?.role === 'assistant') {
    msg.timestamp = finishedAt
  }
}

const getCopyableAssistantContent = (msg: Message): string => {
  const orderedText = (msg.segments || [])
    .filter((segment): segment is Extract<MessageSegment, { type: 'text' }> => segment.type === 'text')
    .map(segment => getConversationDisplayContent(segment.content))
    .filter(Boolean)
    .join('\n\n')
    .trim()
  return orderedText || getConversationDisplayContent(msg.content)
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

    <!-- 右侧：对话主区域和按需打开的工具详情 -->
    <div class="flex-1 flex min-w-0 overflow-hidden">
      <div class="flex flex-1 flex-col min-w-0 overflow-hidden bg-gray-50">
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
              {{ formatDateTimeValue(msg.timestamp) }}
            </div>
          </div>

          <!-- AI 消息 -->
          <div v-else class="flex flex-col gap-3">
            <div class="max-w-full p-4 rounded-2xl rounded-bl-md bg-white border border-black/[0.06] shadow-[0_2px_8px_rgba(0,0,0,0.04)]">
              <div v-if="msg.segments?.length" class="flex min-w-0 flex-col gap-3">
                <template v-for="(segment, index) in msg.segments" :key="`${segment.type}-${index}`">
                  <div
                    v-if="segment.type === 'text' && getConversationDisplayContent(segment.content)"
                    class="markdown-body min-w-0 text-sm leading-relaxed text-gray-700"
                    v-html="md.render(getConversationDisplayContent(segment.content))"
                  ></div>
                  <div
                    v-else-if="segment.type === 'thinkingSegment' && getThinkingSegment(msg, segment.thinkingSegmentId)"
                    class="overflow-hidden rounded-lg border border-slate-100 bg-slate-50"
                  >
                    <button
                      type="button"
                      class="flex w-full items-center gap-2 border-none bg-transparent px-3 py-2 text-left text-sm font-medium text-slate-700 hover:bg-slate-100"
                      @click="toggleThinkingSegment(msg, segment.thinkingSegmentId)"
                    >
                      <i class="fa-solid fa-brain text-xs text-slate-500"></i>
                      <span>思考</span>
                      <i
                        :class="[
                          'ml-auto fa-solid fa-chevron-right text-[10px] text-slate-400 transition-transform',
                          !isThinkingSegmentCollapsed(msg, segment.thinkingSegmentId) && 'rotate-90'
                        ]"
                      ></i>
                    </button>
                    <div
                      v-if="!isThinkingSegmentCollapsed(msg, segment.thinkingSegmentId)"
                      class="border-t border-slate-100 px-3 py-2 text-sm leading-relaxed text-slate-500 whitespace-pre-wrap"
                    >
                      {{ getThinkingSegment(msg, segment.thinkingSegmentId)!.content }}
                    </div>
                  </div>
                  <div v-else-if="segment.type === 'toolCall' && getToolCallById(msg, segment.toolCallId)" class="min-w-0">
                    <ToolCallList
                      :tool-calls="[getToolCallById(msg, segment.toolCallId)!]"
                      :datasource-id="selectedDatasource"
                      :execution-targets="executionTargets"
                      compact
                      :active-tool-call-id="activeToolCallId"
                      @select-tool="selectToolCall"
                    />
                    <QueryResultPanel
                      v-if="getRenderableQueryResultToolCall(msg, segment.toolCallId)"
                      class="mt-2 max-h-[46vh]"
                      :result-data="getRenderableQueryResultToolCall(msg, segment.toolCallId)!.renderId!"
                      :sql="extractGeneratedSqlFromToolCall(getRenderableQueryResultToolCall(msg, segment.toolCallId)!)"
                      :semantic-params="getRenderableQueryResultToolCall(msg, segment.toolCallId)!.semanticParams"
                      :on-save="handleSaveToDashboard"
                    />
                  </div>
                </template>
              </div>
              <div v-else-if="getConversationDisplayContent(msg.content)" class="markdown-body min-w-0 text-sm leading-relaxed text-gray-700" v-html="md.render(getConversationDisplayContent(msg.content))"></div>
              <div v-else-if="msg.isStreaming" class="flex items-center gap-2 text-gray-500 text-sm">
                <i class="fa-solid fa-spinner fa-spin"></i>
                <span>正在思考...</span>
              </div>

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

              <!-- 错误消息 -->
              <div v-if="msg.error" class="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-red-500/10 text-red-600 text-[13px] mt-2">
                <i class="fa-solid fa-circle-exclamation"></i>
                <span>{{ msg.error }}</span>
              </div>
            </div>

            <div v-if="!msg.isStreaming && shouldShowMessageCopyButton(msg)" class="px-0">
              <AssistantMessageFooter :timestamp="formatDateTimeValue(msg.timestamp)">
                <button
                  v-if="shouldShowMessageCopyButton(msg)"
                  class="inline-flex h-[26px] min-w-[58px] shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-md bg-transparent px-2 text-[11px] text-gray-500 transition-all duration-200 hover:bg-blue-500/5 hover:text-blue-500"
                  @click="handleCopyAssistantMessage(msg)"
                >
                  <i class="fa-regular fa-copy text-[11px]"></i>
                  <span>复制</span>
                </button>
              </AssistantMessageFooter>
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

    <!-- 底部输入区 -->
    <div v-if="!isLoadingHistory || messages.length > 0" class="p-4 bg-gray-50">
      <div class="max-w-[774px] mx-auto w-[90%]">
        <ConversationInputBar
          ref="inputBarRef"
          v-model:input="input"
          v-model:selected-agent="selectedAgent"
          v-model:selected-model="selectedModel"
          v-model:enable-deep-thinking="enableDeepThinking"
          placeholder="请输入问题查询数据，输入 / 可选择技能"
          :selected-skill-title="selectedSkillTitle"
          :agent-options="agentOptions"
          :model-options="modelOptions"
          :is-generating="isGenerating"
          :can-submit="Boolean(input.trim() && selectedDatasource)"
          :input-focused="inputFocused"
          :show-skill-button="selectedAgent === 'chat_data'"
          :submit-title="!selectedDatasource ? '请先选择数据源' : '发送 (Enter)'"
          @submit="handleSubmit"
          @stop="handleStop"
          @clear-skill="clearSelectedSkill"
          @open-skill-manager="isSkillManagerOpen = true"
          @disabled-agent-click="handleDisabledAgentClick"
          @keydown="handleKeyDown"
          @focus="inputFocused = true"
          @blur="inputFocused = false"
        >
          <template #toolbar-extra>
            <ExecutionTargetPicker
              v-if="!hideExecutionTargetSelector && executionTargets.length > 0"
              v-model="selectedMemberDatasourceIds"
              :targets="executionTargets"
              :multiple="executionTargetMultiple"
            />
            <ConversationTokenUsage ref="tokenUsagePanelRef" :usage="tokenUsage" />
          </template>

          <template #command-menu>
            <SkillCommandMenu
              :open="showSkillCommand"
              :loading="isCommandSkillLoading"
              :skills="displayedCommandSkills"
              :highlighted-index="highlightedSkillIndex"
              @select="selectCommandSkill"
            />
          </template>
        </ConversationInputBar>
      </div>
    </div>
      </div>
      <ConversationSidePanel
        :open="sidePanelOpen"
        title="工具详情"
        subtitle="工具调用参数与结果"
        :show-trigger="false"
        @close="closeToolDetailPanel"
      >
        <ToolCallDetailPanel :tool-call="activeToolCall" />
      </ConversationSidePanel>
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

</style>
