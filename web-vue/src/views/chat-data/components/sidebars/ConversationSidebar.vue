<script setup lang="ts">
/**
 * 会话列表侧边栏
 * 用于智能问数模式，包含指标和会话两个 tab
 * 看板已提取为独立的 DashboardPanel 组件
 */
import { ref, reactive, computed, watch, onUnmounted, nextTick, type ComponentPublicInstance } from 'vue'
import { getSemanticConfigs, updateConversationTitle as apiUpdateConversationTitle, toggleConversationFavorite as apiToggleConversationFavorite } from '../../api'
import type {
  ConversationGroupView,
  ConversationItem,
  SemanticConfigItem,
  SemanticDimension,
  SemanticMetric,
  TableInfo,
} from '../../types'
import { getSemanticConfigTableCount, getSemanticConfigsTableCount } from '../../utils'
import {
  buildFieldDictSection,
  createTooltipSection,
  extractMetricFieldName as extractMetricSourceFieldName,
  findColumnDict,
  type FieldTooltipSection
} from '../../utils/fieldDict'
import { useColumnDictCache } from '../../composables/useColumnDictCache'
import { formatRelativeConversationTime } from '../../utils'
import { shouldLoadMoreConversations } from '../../utils/conversationHistory'
import FieldTypeIcon from '../icons/FieldTypeIcon.vue'

const getRunStatusLabel = (status: ConversationItem['run_status']): string => {
  if (status === 'pending') return '排队中'
  if (status === 'active') return '运行中'
  if (status === 'waiting_user') return '等待回答'
  if (status === 'cancelling') return '停止中'
  return ''
}

const getRunStatusClass = (status: ConversationItem['run_status']): string => {
  if (status === 'waiting_user') return 'text-amber-500'
  if (status === 'cancelling') return 'text-slate-400'
  return 'text-blue-500'
}

const getConversationTimeText = (conv: ConversationItem): string => (
  formatRelativeConversationTime(conv.updated_at || conv.create_time)
)

interface Props {
  activeTab: 'metrics' | 'conversation'
  groupedConversations?: ConversationGroupView[]
  currentConversationId?: string | null
  isLoading?: boolean
  isLoadingMore?: boolean
  hasMore?: boolean
  showNewConversationButton?: boolean
  conversationViewMode?: 'grouped' | 'flat'
  datasourceId?: string
  semanticConfigs?: SemanticConfigItem[]
  tables?: TableInfo[]
  loadingConfigs?: boolean
  bootstrapLoading?: boolean
  selectedDatasetId?: string | null
  hideManagement?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  groupedConversations: () => [],
  currentConversationId: null,
  isLoading: false,
  isLoadingMore: false,
  hasMore: false,
  showNewConversationButton: true,
  conversationViewMode: 'grouped',
  datasourceId: '',
  semanticConfigs: undefined,
  tables: () => [],
  loadingConfigs: undefined,
  bootstrapLoading: false,
  selectedDatasetId: null,
  hideManagement: false
})

const emit = defineEmits<{
  selectConversation: [conversationId: string]
  newConversation: []
  loadMoreConversations: []
  loadMoreConversationGroup: [groupKey: string]
  updateConversationTitle: [conversationId: string, title: string]
  toggleConversationFavorite: [conversationId: string, isFavorited: boolean]
  toggleConversationViewMode: []
  selectDataset: [configId: string | null]
  refreshSemanticConfigs: []
  fillQuestion: [payload: { fieldName: string; fieldType: 'dimension' | 'metric' }]
}>()

// 每次打开历史弹窗时全部展开；稳定 key 仅用于本次打开期间的折叠状态。
const collapsedGroups = ref<Set<string>>(new Set())

const isConversationGroupCollapsed = (key: string): boolean => collapsedGroups.value.has(key)

const toggleConversationGroup = (key: string) => {
  const next = new Set(collapsedGroups.value)
  if (next.has(key)) {
    next.delete(key)
  } else {
    next.add(key)
  }
  collapsedGroups.value = next
}

// 会话搜索：本地过滤已加载的会话（按标题模糊匹配，不区分大小写）。
// 仅过滤客户端已有数据，不触发额外的服务端查询——如需搜索更早的会话，仍走各分组底部的「加载更多」。
const conversationSearchKeyword = ref('')
const hasConversationSearch = computed(() => conversationSearchKeyword.value.trim().length > 0)

const filteredConversationGroups = computed<ConversationGroupView[]>(() => {
  const keyword = conversationSearchKeyword.value.trim().toLowerCase()
  if (!keyword) return props.groupedConversations
  return props.groupedConversations
    .map(group => ({
      ...group,
      items: group.items.filter(conv => (conv.title || '').toLowerCase().includes(keyword)),
    }))
    .filter(group => group.items.length > 0)
})

// 指标集列表
const internalSemanticConfigs = ref<SemanticConfigItem[]>([])
const internalIsLoadingConfigs = ref(false)
const semanticConfigs = computed(() => props.semanticConfigs ?? internalSemanticConfigs.value)
const isLoadingConfigs = computed(() => props.loadingConfigs ?? internalIsLoadingConfigs.value)
const semanticConfigTableCount = computed(() => getSemanticConfigsTableCount(semanticConfigs.value))
const configSearchKeyword = ref('')
const dimensionSearchKeywords = reactive<Record<string, string>>({})
const metricSearchKeywords = reactive<Record<string, string>>({})
const sectionHeightsByConfig = reactive<Record<string, [number, number]>>({})
const sectionPanelRefs = new Map<string, HTMLElement>()
const sectionResizeState = ref<{
  active: boolean
  configId: string
  startY: number
  startHeights: [number, number]
}>({
  active: false,
  configId: '',
  startY: 0,
  startHeights: [50, 50]
})
const tooltipState = reactive({
  visible: false,
  x: 0,
  y: 0,
  sections: [] as FieldTooltipSection[],
})
const { getColumnDict, loadTablesColumnDicts, resetColumnDictCache } = useColumnDictCache()
let tooltipHideTimer: number | null = null

const clearTooltipHideTimer = () => {
  if (!tooltipHideTimer) return
  window.clearTimeout(tooltipHideTimer)
  tooltipHideTimer = null
}

const showTooltip = (e: MouseEvent, sections: FieldTooltipSection[]) => {
  clearTooltipHideTimer()
  const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
  tooltipState.x = rect.right + 8
  tooltipState.y = rect.top + rect.height / 2
  tooltipState.sections = sections
  tooltipState.visible = true
}

const hideTooltip = () => {
  clearTooltipHideTimer()
  tooltipHideTimer = window.setTimeout(() => {
    tooltipState.visible = false
    tooltipHideTimer = null
  }, 160)
}

const keepTooltipVisible = () => {
  clearTooltipHideTimer()
}

const hideTooltipNow = () => {
  clearTooltipHideTimer()
  tooltipState.visible = false
}

const filteredSemanticConfigs = computed(() => {
  const keyword = configSearchKeyword.value.trim().toLowerCase()
  if (!keyword) return semanticConfigs.value
  return semanticConfigs.value.filter(config => {
    const text = [
      config.name,
      ...(config.metrics || []).map(metric => metric.name),
      ...(config.dimensions || []).map(dimension => dimension.name),
    ].join(' ').toLowerCase()
    return text.includes(keyword)
  })
})

const getSmartSelectionSummary = () => {
  return `${semanticConfigs.value.length}语义模型/${semanticConfigTableCount.value}表`
}

const getConfigSummary = (config: SemanticConfigItem) => {
  return `${getSemanticConfigTableCount(config)}表/${(config.relationships || []).length}关系/${(config.dimensions || []).length}维度/${(config.metrics || []).length}指标`
}

const getConfigOrdinal = (config: SemanticConfigItem) => {
  const index = semanticConfigs.value.findIndex(item => item.id === config.id)
  return index >= 0 ? index + 1 : 0
}

const getConfigDisplayName = (config: SemanticConfigItem) => {
  const ordinal = getConfigOrdinal(config)
  return ordinal > 0 ? `${ordinal}. ${config.name}` : config.name
}

const getConfigScopeLabel = (config: SemanticConfigItem) => {
  return config.scope_type === 'private' ? '个人' : '公共'
}

const getConfigScopeTitle = (config: SemanticConfigItem) => {
  return `${getConfigScopeLabel(config)}语义模型`
}

const getConfigSelectCircleClass = (config: SemanticConfigItem) => {
  const isSelected = selectedConfigId.value === config.id
  if (isSelected) {
    return 'border-[#165DFF] bg-[#165DFF] text-white'
  }
  if (config.scope_type === 'private') {
    return 'border-2 border-[#00B42A] bg-white text-transparent hover:border-[#00B42A]'
  }
  return 'border-[#9CC7FF] bg-white text-transparent hover:border-[#165DFF]'
}

const getFilteredDimensions = (config: SemanticConfigItem) => {
  const dimensions = config.dimensions || []
  const keyword = (dimensionSearchKeywords[config.id] || '').trim().toLowerCase()
  if (!keyword) return dimensions
  return dimensions.filter(dimension => {
    const text = [
      dimension.name,
      dimension.column,
      dimension.field_name,
      dimension.expression,
      dimension.description,
      dimension.metadata_supplement
    ].join(' ').toLowerCase()
    return text.includes(keyword)
  })
}

const getFilteredMetrics = (config: SemanticConfigItem) => {
  const metrics = config.metrics || []
  const keyword = (metricSearchKeywords[config.id] || '').trim().toLowerCase()
  if (!keyword) return metrics
  return metrics.filter(metric => {
    const text = [
      metric.name,
      metric.field_name,
      metric.expression,
      metric.description,
      metric.metadata_supplement
    ].join(' ').toLowerCase()
    return text.includes(keyword)
  })
}

const getSectionHeights = (configId: string): [number, number] => {
  return sectionHeightsByConfig[configId] || [50, 50]
}

const ensureSectionHeights = (configId: string): [number, number] => {
  if (!sectionHeightsByConfig[configId]) {
    sectionHeightsByConfig[configId] = [50, 50]
  }
  return sectionHeightsByConfig[configId]
}

const getSectionPanelStyle = (config: SemanticConfigItem) => {
  const dimensionCount = config.dimensions?.length || 0
  const metricCount = config.metrics?.length || 0
  if (!dimensionCount || !metricCount) return {}
  const visibleRows = Math.min(dimensionCount, 10) + Math.min(metricCount, 8)
  const height = Math.min(560, Math.max(280, visibleRows * 28 + 96))
  return { height: `${height}px` }
}

const setSectionPanelRef = (configId: string, el: Element | ComponentPublicInstance | null) => {
  if (el instanceof HTMLElement) {
    sectionPanelRefs.set(configId, el)
    return
  }
  sectionPanelRefs.delete(configId)
}

const startSectionResize = (configId: string, e: MouseEvent) => {
  e.preventDefault()
  stopSectionResize()
  sectionResizeState.value = {
    active: true,
    configId,
    startY: e.clientY,
    startHeights: [...ensureSectionHeights(configId)]
  }
  document.addEventListener('mousemove', onSectionResize)
  document.addEventListener('mouseup', stopSectionResize)
}

const onSectionResize = (e: MouseEvent) => {
  if (!sectionResizeState.value.active) return
  const { configId, startY, startHeights } = sectionResizeState.value
  const panel = sectionPanelRefs.get(configId)
  if (!panel) return
  const deltaPercent = ((e.clientY - startY) / Math.max(panel.clientHeight, 1)) * 100
  const minPercent = 16
  const first = Math.min(100 - minPercent, Math.max(minPercent, startHeights[0] + deltaPercent))
  sectionHeightsByConfig[configId] = [first, 100 - first]
}

const stopSectionResize = () => {
  sectionResizeState.value = {
    active: false,
    configId: '',
    startY: 0,
    startHeights: [50, 50]
  }
  document.removeEventListener('mousemove', onSectionResize)
  document.removeEventListener('mouseup', stopSectionResize)
}

// 选中的指标集 ID
const selectedConfigId = ref<string | null>(null)
const expandedConfigIds = ref<Set<string>>(new Set())

const handleConversationScroll = (event: Event) => {
  if (props.conversationViewMode !== 'flat') return
  const target = event.currentTarget as HTMLElement
  if (shouldLoadMoreConversations({
    hasMore: props.hasMore,
    isLoading: props.isLoading,
    isLoadingMore: props.isLoadingMore,
    scrollHeight: target.scrollHeight,
    scrollTop: target.scrollTop,
    clientHeight: target.clientHeight,
  })) {
    emit('loadMoreConversations')
  }
}

const handleConversationGroupScroll = (group: ConversationGroupView, event: Event) => {
  const target = event.currentTarget as HTMLElement
  if (shouldLoadMoreConversations({
    hasMore: Boolean(group.has_more),
    isLoading: false,
    isLoadingMore: Boolean(group.is_loading_more),
    scrollHeight: target.scrollHeight,
    scrollTop: target.scrollTop,
    clientHeight: target.clientHeight,
  })) {
    emit('loadMoreConversationGroup', group.key)
  }
}

// 会话标题编辑状态
const editingConversationId = ref<string | null>(null)
const editingTitle = ref('')
const editingInputRef = ref<HTMLInputElement | null>(null)
const isSavingTitle = ref(false)

// 会话置顶（收藏）切换状态
const favoritingId = ref<string | null>(null)

// v-for 内的字符串 ref 会被 Vue 3 收集为数组，改用函数 ref 确保拿到单个 DOM 元素
const setEditingInputRef = (el: Element | ComponentPublicInstance | null) => {
  editingInputRef.value = (el as HTMLInputElement) || null
}

const startEditConversationTitle = (conv: ConversationItem, event: MouseEvent) => {
  event.stopPropagation()
  editingConversationId.value = conv.id
  editingTitle.value = conv.title || ''
  void nextTick(() => {
    editingInputRef.value?.focus()
    editingInputRef.value?.select()
  })
}

const cancelEditConversationTitle = () => {
  editingConversationId.value = null
  editingTitle.value = ''
}

const confirmEditConversationTitle = async (convId: string) => {
  const newTitle = editingTitle.value.trim()
  if (!newTitle || isSavingTitle.value) {
    cancelEditConversationTitle()
    return
  }

  // 找到当前显示标题以判断是否变化
  const currentTitle = props.groupedConversations
    .flatMap(g => g.items)
    .find(c => c.id === convId)?.title

  if (newTitle === currentTitle) {
    cancelEditConversationTitle()
    return
  }

  isSavingTitle.value = true
  try {
    const res = await apiUpdateConversationTitle(convId, newTitle)
    if (res.status === 'success') {
      // 后端会截断标题长度，以返回值为准回显，避免前后端不一致
      emit('updateConversationTitle', convId, res.data?.title ?? newTitle)
      cancelEditConversationTitle()
    } else {
      // 保存失败时保持编辑状态，方便用户重试或取消
      editingInputRef.value?.focus()
    }
  } catch (err) {
    console.error('更新会话标题失败:', err)
    editingInputRef.value?.focus()
  } finally {
    isSavingTitle.value = false
  }
}

const handleEditTitleKeydown = (convId: string, event: KeyboardEvent) => {
  if (event.key === 'Enter') {
    event.preventDefault()
    event.stopPropagation()
    void confirmEditConversationTitle(convId)
  } else if (event.key === 'Escape') {
    event.preventDefault()
    event.stopPropagation()
    cancelEditConversationTitle()
  }
}

// 切换会话置顶（复用后端 is_favorited 收藏能力，置顶 = 收藏并排序到顶部）
const toggleConversationFavoriteState = async (conv: ConversationItem) => {
  if (favoritingId.value === conv.id) return
  favoritingId.value = conv.id
  try {
    const res = await apiToggleConversationFavorite(conv.id)
    if (res.status === 'success') {
      const isFavorited = Boolean(res.data?.is_favorited)
      emit('toggleConversationFavorite', conv.id, isFavorited)
    }
  } catch (err) {
    console.error('切换置顶状态失败:', err)
  } finally {
    favoritingId.value = null
  }
}

const syncSelectedConfig = () => {
  const configs = semanticConfigs.value
  if (configs.length === 0) {
    if (selectedConfigId.value !== null) {
      selectedConfigId.value = null
      emit('selectDataset', null)
    }
    expandedConfigIds.value = new Set()
    return
  }
  if (selectedConfigId.value && configs.find(c => c.id === selectedConfigId.value)) {
    return
  }
  if (selectedConfigId.value !== null) {
    selectedConfigId.value = null
    emit('selectDataset', null)
  }
  expandedConfigIds.value = new Set([...expandedConfigIds.value].filter(id => configs.some(c => c.id === id)))
}

// 加载语义配置列表
const loadSemanticConfigs = async () => {
  if (props.semanticConfigs !== undefined) {
    emit('refreshSemanticConfigs')
    return
  }
  if (!props.datasourceId) return
  internalIsLoadingConfigs.value = true
  try {
    const res = await getSemanticConfigs(props.datasourceId)
    if (res.status === 'success') {
      internalSemanticConfigs.value = res.data?.configs || []
      syncSelectedConfig()
    }
  } catch (err) {
    console.error('加载语义配置失败:', err)
  } finally {
    internalIsLoadingConfigs.value = false
  }
}

// 选择指标集
const handleSelectConfig = (configId: string | null) => {
  selectedConfigId.value = configId
  emit('selectDataset', configId)
}

const getConfigTableNames = (config: SemanticConfigItem) => [
  ...(config.dimensions || []).map(item => item.table),
  ...(config.metrics || []).map(item => item.table),
]

const toggleConfig = (configId: string) => {
  const next = new Set(expandedConfigIds.value)
  if (next.has(configId)) {
    next.delete(configId)
  } else {
    next.add(configId)
    const config = semanticConfigs.value.find(item => item.id === configId)
    if (config) {
      void loadTablesColumnDicts(props.datasourceId, getConfigTableNames(config))
    }
  }
  expandedConfigIds.value = next
}

const getDimensionIconType = (dimension: SemanticDimension): 'string' | 'number' | 'date' => {
  if (dimension.logical_type === 'number') return 'number'
  if (dimension.logical_type === 'date' || dimension.logical_type === 'datetime') return 'date'
  return 'string'
}

const buildMetadataSupplementSection = (metadataSupplement?: string) => {
  const content = String(metadataSupplement || '').trim()
  if (!content) return null
  return createTooltipSection('元数据补充', content
    .split(/\n+/)
    .map(item => item.trim())
    .filter(Boolean))
}

const getTableDescription = (tableName?: string) => {
  if (!tableName) return '-'
  const table = props.tables.find(item => item.table_name === tableName)
  return table?.description || '-'
}

const getTableBusinessDesc = (tableName?: string) => {
  if (!tableName) return ''
  const table = props.tables.find(item => item.table_name === tableName)
  return table?.business_desc || ''
}

const buildTableBusinessDescLines = (tableName?: string) => {
  const businessDesc = getTableBusinessDesc(tableName)
  return businessDesc ? [`表业务说明：${businessDesc}`] : []
}

const getTableRoleLabel = (table?: string, alias?: string) => {
  const tableName = String(table || '').trim()
  const tableAlias = String(alias || tableName).trim()
  if (!tableName) return tableAlias || '-'
  if (!tableAlias || tableAlias === tableName) return tableName
  return `${tableAlias}（${tableName}）`
}

const buildDimensionTooltip = (dimension: SemanticDimension) => {
  const expression = dimension.expression || `\`${dimension.column}\``
  const fieldDict = getColumnDict(props.datasourceId, dimension.table, dimension.column)
    || findColumnDict(props.tables, dimension.table, dimension.column)
  return [
    createTooltipSection('表信息', [
      `数据源表：${getTableRoleLabel(dimension.table, dimension.table_alias)}`,
      `数据表备注：${getTableDescription(dimension.table)}`,
      ...buildTableBusinessDescLines(dimension.table),
    ]),
    createTooltipSection('字段信息', [
      `显示名称：${dimension.name || '-'}`,
      `字段名称：${dimension.column || '-'}`,
      `表达式：${expression}`,
      dimension.description ? `描述：${dimension.description}` : '',
    ]),
    buildFieldDictSection(fieldDict),
    buildMetadataSupplementSection(dimension.metadata_supplement),
  ].filter(Boolean) as FieldTooltipSection[]
}

const buildMetricTooltip = (metric: SemanticMetric) => {
  const expression = metric.expression || ''
  const fieldName = extractMetricSourceFieldName(metric)
  const fieldDict = getColumnDict(props.datasourceId, metric.table, fieldName)
    || findColumnDict(props.tables, metric.table, fieldName)
  return [
    createTooltipSection('表信息', [
      `数据源表：${getTableRoleLabel(metric.table, metric.table_alias)}`,
      `数据表备注：${getTableDescription(metric.table)}`,
      ...buildTableBusinessDescLines(metric.table),
    ]),
    createTooltipSection('字段信息', [
      `显示名称：${metric.name || '-'}`,
      `字段名称：${fieldName || '-'}`,
      `表达式：${expression || '-'}`,
      metric.description ? `描述：${metric.description}` : '',
    ]),
    buildFieldDictSection(fieldDict),
    buildMetadataSupplementSection(metric.metadata_supplement),
  ].filter(Boolean) as FieldTooltipSection[]
}

const handleFillMetric = (metric: SemanticMetric) => {
  emit('fillQuestion', { fieldName: metric.name, fieldType: 'metric' })
}

const handleFillDimension = (dimension: SemanticDimension) => {
  emit('fillQuestion', { fieldName: dimension.name, fieldType: 'dimension' })
}

// 监听数据源变化
watch(() => props.datasourceId, () => {
  selectedConfigId.value = null
  expandedConfigIds.value = new Set()
  resetColumnDictCache()
  if (props.semanticConfigs === undefined) {
    loadSemanticConfigs()
    return
  }
  syncSelectedConfig()
}, { immediate: true })

watch(() => props.semanticConfigs, () => {
  if (props.semanticConfigs === undefined) return
  syncSelectedConfig()
}, { immediate: true, deep: true })

// 父子同步：父层指标集变化时，同步当前选中指标集
watch(() => props.selectedDatasetId, (newId) => {
  if (newId === selectedConfigId.value) return
  if (!newId) {
    selectedConfigId.value = null
    return
  }
  if (semanticConfigs.value.find(c => c.id === newId)) {
    selectedConfigId.value = newId
  }
}, { immediate: true })

onUnmounted(() => {
  clearTooltipHideTimer()
  document.removeEventListener('mousemove', onSectionResize)
  document.removeEventListener('mouseup', stopSectionResize)
})

</script>

<template>
  <div class="flex h-full min-h-0 flex-col overflow-hidden">
    <!-- ==================== 会话 Tab ==================== -->
    <template v-if="activeTab === 'conversation'">
      <!-- 会话工具栏：新建 + 视图切换（固定，不随列表滚动） -->
      <div class="flex shrink-0 items-center gap-2 px-3 py-2">
        <button
          v-if="showNewConversationButton"
          type="button"
          class="flex h-7 shrink-0 items-center gap-1.5 rounded-md bg-[#1f82d9] px-2.5 text-xs font-medium text-white transition-colors hover:bg-[#1668b3]"
          @click="emit('newConversation')"
        >
          <i class="fa-solid fa-plus text-[10px]"></i>
          <span>新建任务</span>
        </button>
        <div class="relative min-w-0 flex-1">
          <i class="fa-solid fa-magnifying-glass absolute left-2 top-1/2 -translate-y-1/2 text-[10px] text-slate-400"></i>
          <input
            v-model="conversationSearchKeyword"
            type="text"
            placeholder="搜索任务"
            class="h-7 w-full rounded-md border border-slate-200 bg-white pl-6 pr-2 text-xs text-gray-700 outline-none transition-colors placeholder:text-slate-400 focus:border-[#1f82d9]/50"
          />
        </div>
        <button
          type="button"
          class="flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-md border border-slate-200 bg-white px-2 text-xs text-slate-400 transition-colors hover:border-[#1f82d9]/40 hover:text-[#1f82d9]"
          :title="props.conversationViewMode === 'grouped' ? '切换为平铺列表' : '切换为按数据源分组'"
          @click="emit('toggleConversationViewMode')"
        >
          <i :class="props.conversationViewMode === 'grouped' ? 'fa-solid fa-list' : 'fa-solid fa-layer-group'" class="text-[10px]"></i>
          {{ props.conversationViewMode === 'grouped' ? '平铺' : '分组' }}
        </button>
      </div>

      <!-- 会话列表 -->
      <div data-testid="conversation-scroll" class="min-h-0 flex-1 overflow-x-hidden overflow-y-auto px-4 pb-4 scrollbar-thin scrollbar-thumb-black/10" @scroll="handleConversationScroll">
        <div v-if="isLoading" class="flex flex-col items-center justify-center gap-2 p-8 text-gray-400 text-xs">
          <i class="fa-solid fa-spinner fa-spin text-2xl opacity-50"></i>
          <span>加载历史会话...</span>
        </div>

        <div v-else-if="filteredConversationGroups.length === 0" class="flex flex-col items-center justify-center gap-2 p-8 text-gray-400 text-xs">
          <i :class="['text-2xl opacity-50', hasConversationSearch ? 'fa-regular fa-face-frown' : 'fa-regular fa-message']"></i>
          <span>{{ hasConversationSearch ? '未找到匹配的任务' : '暂无会话' }}</span>
        </div>

        <template v-else>
          <template v-for="group in filteredConversationGroups" :key="group.key">
            <div class="mb-1">
              <button
                v-if="group.label"
                type="button"
                class="flex w-full items-center gap-1.5 rounded-md px-1.5 py-2 text-xs font-medium text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700"
                @click="toggleConversationGroup(group.key)"
              >
                <i
                  class="fa-solid fa-chevron-down shrink-0 text-[9px] text-gray-400 transition-transform duration-150"
                  :class="{ '-rotate-90': isConversationGroupCollapsed(group.key) }"
                ></i>
                <i
                  class="fa-solid shrink-0 text-[11px] text-amber-400"
                  :class="isConversationGroupCollapsed(group.key) ? 'fa-folder' : 'fa-folder-open'"
                ></i>
                <span class="min-w-0 flex-1 truncate text-left">{{ group.label }}</span>
              </button>
              <template v-if="!group.label || !isConversationGroupCollapsed(group.key) || hasConversationSearch">
                <div
                  class="conversation-group-items scrollbar-thin scrollbar-thumb-black/10"
                  :class="group.label ? 'ml-[18px] border-l border-slate-200 pl-3' : ''"
                  :style="group.label && (group.has_more || group.items.length > 8) ? { maxHeight: '320px', overflowY: 'auto' } : undefined"
                  @scroll="handleConversationGroupScroll(group, $event)"
                >
                  <div
                    v-for="conv in group.items"
                    :key="conv.id"
                    class="group flex items-center gap-2 rounded-md py-2.5 cursor-pointer transition-all duration-150"
                    :class="[
                      group.label ? 'px-2' : 'px-1',
                      currentConversationId === conv.id ? 'text-blue-500 font-medium bg-blue-50/60' : 'text-gray-800 hover:text-blue-500 hover:bg-gray-50',
                    ]"
                    @click="emit('selectConversation', conv.id)"
                  >
                    <!-- 标题编辑态 -->
                    <template v-if="editingConversationId === conv.id">
                      <input
                        :ref="setEditingInputRef"
                        v-model="editingTitle"
                        type="text"
                        class="flex-1 min-w-0 text-sm bg-white border border-blue-400 rounded px-1.5 py-0.5 outline-none text-gray-800 leading-relaxed"
                        :disabled="isSavingTitle"
                        placeholder="输入新标题"
                        @click.stop
                        @keydown="handleEditTitleKeydown(conv.id, $event)"
                        @blur="confirmEditConversationTitle(conv.id)"
                      />
                    </template>

                    <!-- 标题展示态 -->
                    <template v-else>
                      <span
                        v-if="getRunStatusLabel(conv.run_status)"
                        :class="['flex shrink-0 items-center gap-1 text-[11px]', getRunStatusClass(conv.run_status)]"
                        :title="getRunStatusLabel(conv.run_status)"
                      >
                        <i :class="conv.run_status === 'active' || conv.run_status === 'pending' ? 'fa-solid fa-spinner fa-spin' : 'fa-solid fa-circle'" class="text-[9px]"></i>
                      </span>
                      <span class="min-w-0 flex-1 overflow-hidden text-ellipsis whitespace-nowrap text-sm leading-relaxed">{{ conv.title }}</span>
                      <!-- 右侧固定角色区：平时显示相对时间，hover 淡出时间、淡入置顶/编辑按钮。
                           两态共用同一固定宽度容器（w-[52px] = 2×24px 按钮 + 4px 间距），互不挤占、不跳动。
                           置顶状态不常驻展示（分组视图靠「置顶」分组、平铺视图靠排序置前感知），hover 时钉按状态着色提示。 -->
                      <div data-testid="conversation-role-zone" class="relative h-6 w-[52px] shrink-0">
                        <span
                          data-testid="conversation-time"
                          class="absolute inset-y-0 right-0 flex max-w-full items-center truncate text-[10px] text-gray-400 transition-opacity duration-150 group-hover:opacity-0"
                          :title="conv.updated_at || conv.create_time"
                        >{{ getConversationTimeText(conv) }}</span>
                        <div
                          data-testid="conversation-actions"
                          class="absolute inset-y-0 right-0 flex items-center gap-1 opacity-0 transition-opacity duration-150 group-hover:opacity-100"
                        >
                          <button
                            type="button"
                            class="flex h-6 w-6 items-center justify-center rounded transition-colors duration-150"
                            :class="conv.is_favorited
                              ? 'text-amber-500 hover:bg-amber-100 hover:text-amber-600'
                              : 'text-gray-400 hover:bg-blue-100 hover:text-blue-600'"
                            :title="conv.is_favorited ? '取消置顶' : '置顶'"
                            :disabled="favoritingId === conv.id"
                            @click.stop="toggleConversationFavoriteState(conv)"
                          >
                            <i :class="['fa-solid text-[10px]', favoritingId === conv.id ? 'fa-spinner fa-spin' : 'fa-thumbtack']"></i>
                          </button>
                          <button
                            type="button"
                            class="flex h-6 w-6 items-center justify-center rounded text-gray-400 transition-colors duration-150 hover:bg-blue-100 hover:text-blue-600"
                            title="修改标题"
                            @click.stop="startEditConversationTitle(conv, $event)"
                          >
                            <i class="fa-solid fa-pen text-[10px]"></i>
                          </button>
                        </div>
                      </div>
                    </template>
                  </div>
                  <div v-if="group.is_loading_more" class="flex items-center justify-center gap-2 py-2 text-xs text-gray-400">
                    <i class="fa-solid fa-spinner fa-spin"></i>
                    <span>加载更多...</span>
                  </div>
                  <button
                    v-else-if="group.has_more"
                    type="button"
                    class="flex h-8 w-full items-center justify-center text-xs text-slate-400 transition-colors hover:text-[#1f82d9]"
                    @click="emit('loadMoreConversationGroup', group.key)"
                  >
                    加载更多
                  </button>
                </div>
              </template>
            </div>
          </template>
          <div v-if="conversationViewMode === 'flat' && isLoadingMore" class="flex items-center justify-center gap-2 py-3 text-xs text-gray-400">
            <i class="fa-solid fa-spinner fa-spin"></i>
            <span>加载更多...</span>
          </div>
        </template>
      </div>
    </template>

    <!-- ==================== 指标 Tab ==================== -->
    <template v-else-if="activeTab === 'metrics'">
      <!-- 加载状态 -->
      <div v-if="bootstrapLoading || isLoadingConfigs" class="flex flex-col items-center justify-center gap-2 p-8 text-gray-400 text-xs">
        <i class="fa-solid fa-spinner fa-spin text-2xl opacity-50"></i>
        <span>加载中...</span>
      </div>

      <div v-else-if="semanticConfigs.length === 0" class="flex flex-col items-center justify-center gap-2 p-8 text-gray-400 text-xs">
        <i class="fa-solid fa-database text-2xl opacity-50"></i>
        <span>暂无语义模型</span>
      </div>

      <div v-else class="semantic-config-list flex-1 min-h-0 overflow-y-auto px-2 py-1 scrollbar-thin scrollbar-thumb-black/10">
        <div class="mb-2 px-3">
          <div class="flex h-8 items-center rounded-md border border-[#E5E6EB] bg-white px-2.5 text-[#86909C] transition-colors focus-within:border-[#165DFF]">
            <i class="fa-solid fa-magnifying-glass mr-2 text-[11px]"></i>
            <input
              v-model="configSearchKeyword"
              class="h-full min-w-0 flex-1 border-none bg-transparent p-0 text-[12px] text-[#1D2129] outline-none placeholder:text-[#A8ABB2]"
        placeholder="搜索语义模型、维度、指标"
            />
            <button
              v-if="configSearchKeyword"
              type="button"
              class="ml-1 flex h-5 w-5 items-center justify-center rounded text-[#A8ABB2] transition-colors hover:bg-[#F2F3F5] hover:text-[#4E5969]"
              title="清空搜索"
              @click="configSearchKeyword = ''"
            >
              <i class="fa-solid fa-xmark text-[10px]"></i>
            </button>
          </div>
        </div>

        <div
          :class="[
            'mb-1 flex h-[28px] cursor-pointer items-center rounded px-4 text-[12px] text-[#4E5969] transition-colors hover:bg-[#f0f1f3]',
            selectedConfigId === null ? 'bg-[#E8F3FF]' : ''
          ]"
          :title="`智能选择语义模型：可用语义模型 ${semanticConfigs.length} 个，涉及表 ${semanticConfigTableCount} 张`"
          @click="handleSelectConfig(null)"
        >
          <span
            :class="[
              'mr-2 flex h-4 w-4 shrink-0 cursor-pointer items-center justify-center rounded-full border text-[9px]',
              selectedConfigId === null ? 'border-[#165DFF] bg-[#165DFF] text-white' : 'border-[#C9CDD4] bg-white text-transparent hover:border-[#165DFF]'
            ]"
            title="使用智能选择语义模型提问"
          >
            <i class="fa-solid fa-check"></i>
          </span>
          <span class="min-w-0 flex-1 overflow-hidden text-ellipsis whitespace-nowrap font-semibold text-[#2E2F32]">智能选择语义模型</span>
          <span
            class="semantic-config-summary ml-2 shrink-0 text-[10px] text-[#939495]"
            :title="`可用语义模型：${semanticConfigs.length}；涉及表：${semanticConfigTableCount}`"
          >
            {{ getSmartSelectionSummary() }}
          </span>
          <button
            type="button"
            class="ml-1 flex h-5 w-5 items-center justify-center rounded text-[#5B5C60] transition-colors hover:bg-[#E8F3FF] hover:text-[#165DFF] disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="isLoadingConfigs"
            title="刷新语义模型"
            @click.stop="loadSemanticConfigs"
          >
            <i :class="['fa-solid fa-rotate-right text-[10px]', isLoadingConfigs ? 'fa-spin' : '']"></i>
          </button>
        </div>

        <div
          v-if="filteredSemanticConfigs.length === 0"
          class="px-4 py-5 text-center text-[12px] text-[#A8ABB2]"
        >
          未找到匹配的语义模型
        </div>

        <div v-for="config in filteredSemanticConfigs" :key="config.id" class="mb-0.5">
          <div
            :class="[
              'group flex h-[28px] cursor-pointer items-center rounded px-4 transition-colors duration-100 hover:bg-[#f0f1f3]',
              selectedConfigId === config.id ? 'bg-[#E8F3FF]' : '',
              expandedConfigIds.has(config.id) ? 'text-[#1D2129]' : ''
            ]"
            :title="`${getConfigDisplayName(config)}：${getConfigSummary(config)}`"
            @click="toggleConfig(config.id)"
          >
            <button
              type="button"
              :class="[
                'mr-2 flex h-4 w-4 shrink-0 cursor-pointer items-center justify-center rounded-full border text-[9px] transition-all duration-150',
                getConfigSelectCircleClass(config)
              ]"
              :title="`${selectedConfigId === config.id ? '当前提问使用该语义模型' : '使用该语义模型提问'}（${getConfigScopeTitle(config)}）`"
              @click.stop="handleSelectConfig(selectedConfigId === config.id ? null : config.id)"
            >
              <i class="fa-solid fa-check"></i>
            </button>
            <span class="min-w-0 flex-1 overflow-hidden text-ellipsis whitespace-nowrap text-[12px] font-semibold leading-[28px] text-[#2E2F32]" :title="getConfigDisplayName(config)">
              {{ getConfigDisplayName(config) }}
            </span>
            <span
              class="semantic-config-summary ml-2 shrink-0 text-[10px] text-[#939495]"
              :title="`${getConfigDisplayName(config)}：${getConfigScopeTitle(config)}，${getConfigSummary(config)}`"
            >
              {{ getConfigSummary(config) }}
            </span>
          </div>

          <div v-if="expandedConfigIds.has(config.id)" class="ml-6 border-l border-[#E5E6EB] pl-1">
            <div
              v-if="(config.dimensions?.length || 0) > 0 || (config.metrics?.length || 0) > 0"
              :ref="(el) => setSectionPanelRef(config.id, el)"
              class="flex min-h-[180px] flex-col overflow-hidden"
              :style="getSectionPanelStyle(config)"
            >
              <div
                v-if="(config.dimensions?.length || 0) > 0"
                class="mt-0.5 flex min-h-0 flex-col overflow-hidden"
                :style="(config.metrics?.length || 0) > 0 ? { height: `${getSectionHeights(config.id)[0]}%` } : { flex: 1 }"
              >
                <div class="mb-1 flex h-[30px] shrink-0 items-center gap-2 px-4">
                  <div class="flex shrink-0 items-center text-[13px] font-semibold text-[#1D2129]">
                    <span>维度</span>
                    <span class="ml-1 text-[10px] font-normal text-[#C0C4CC]">{{ config.dimensions?.length || 0 }}</span>
                  </div>
                  <div class="flex h-[26px] min-w-0 flex-1 items-center rounded border border-[#DDE1E8] bg-white px-2.5 text-[#A8ABB2] focus-within:border-[#165DFF]">
                    <input
                      v-model="dimensionSearchKeywords[config.id]"
                      class="h-full min-w-0 flex-1 border-none bg-transparent p-0 text-[12px] text-[#1D2129] outline-none placeholder:text-[#A8ABB2]"
                      placeholder="搜索维度"
                      @click.stop
                    />
                  </div>
                </div>
                <div class="min-h-0 flex-1 overflow-y-auto pr-1 scrollbar-thin scrollbar-thumb-black/10">
                  <div
                    v-for="dimension in getFilteredDimensions(config)"
                    :key="`dimension-${config.id}-${dimension.name}`"
                    class="group flex h-[28px] items-center rounded px-4 transition-colors duration-100 hover:bg-[#f0f1f3]"
                    @mouseenter="showTooltip($event, buildDimensionTooltip(dimension))"
                    @mouseleave="hideTooltip"
                  >
                    <FieldTypeIcon :type="getDimensionIconType(dimension)" class="mr-1.5 text-[#165DFF]" />
                    <span class="min-w-0 flex-1 overflow-hidden text-ellipsis whitespace-nowrap text-[12px] text-[#21252C]">{{ dimension.name }}</span>
                    <button
                      type="button"
                      class="ml-1 flex h-5 w-5 items-center justify-center rounded text-[#165DFF] opacity-0 transition-all duration-150 hover:bg-[#E8F3FF] hover:text-[#0E42D2] group-hover:opacity-100"
                      title="填充到提问框"
                      @click.stop="handleFillDimension(dimension)"
                    >
                      <i class="fa-solid fa-arrow-right text-[10px]"></i>
                    </button>
                  </div>
                  <div v-if="getFilteredDimensions(config).length === 0" class="px-4 py-2 text-[12px] text-[#9CA3AF]">暂无匹配维度</div>
                </div>
              </div>

              <div
                v-if="(config.dimensions?.length || 0) > 0 && (config.metrics?.length || 0) > 0"
                class="relative mx-3 my-1 flex h-[16px] shrink-0 cursor-row-resize select-none items-center justify-center group"
                @mousedown="startSectionResize(config.id, $event)"
              >
                <div class="h-px w-full bg-[#E5E7EB]"></div>
                <div
                  class="absolute h-[4px] w-10 rounded-full bg-[#D1D5DB] transition-colors group-hover:bg-[#9CA3AF]"
                  :class="{ '!bg-[#165DFF]': sectionResizeState.active && sectionResizeState.configId === config.id }"
                ></div>
              </div>

              <div
                v-if="(config.metrics?.length || 0) > 0"
                class="mt-0.5 flex min-h-0 flex-col overflow-hidden"
                :style="(config.dimensions?.length || 0) > 0 ? { height: `${getSectionHeights(config.id)[1]}%` } : { flex: 1 }"
              >
                <div class="mb-1 flex h-[30px] shrink-0 items-center gap-2 px-4">
                  <div class="flex shrink-0 items-center text-[13px] font-semibold text-[#1D2129]">
                    <span>指标</span>
                    <span class="ml-1 text-[10px] font-normal text-[#C0C4CC]">{{ config.metrics?.length || 0 }}</span>
                  </div>
                  <div class="flex h-[26px] min-w-0 flex-1 items-center rounded border border-[#DDE1E8] bg-white px-2.5 text-[#A8ABB2] focus-within:border-[#7C3AED]">
                    <input
                      v-model="metricSearchKeywords[config.id]"
                      class="h-full min-w-0 flex-1 border-none bg-transparent p-0 text-[12px] text-[#1D2129] outline-none placeholder:text-[#A8ABB2]"
                      placeholder="搜索指标"
                      @click.stop
                    />
                  </div>
                </div>
                <div class="min-h-0 flex-1 overflow-y-auto pr-1 scrollbar-thin scrollbar-thumb-black/10">
                  <div
                    v-for="metric in getFilteredMetrics(config)"
                    :key="`metric-${config.id}-${metric.name}`"
                    class="group flex h-[28px] items-center rounded px-4 transition-colors duration-100 hover:bg-[#f0f1f3]"
                    @mouseenter="showTooltip($event, buildMetricTooltip(metric))"
                    @mouseleave="hideTooltip"
                  >
                    <FieldTypeIcon type="number" class="mr-1.5 text-[#7C3AED]" />
                    <span class="min-w-0 flex-1 overflow-hidden text-ellipsis whitespace-nowrap text-[12px] text-[#21252C]">{{ metric.name }}</span>
                    <button
                      type="button"
                      class="ml-1 flex h-5 w-5 items-center justify-center rounded text-[#7C3AED] opacity-0 transition-all duration-150 hover:bg-[#F3E8FF] hover:text-[#5B21B6] group-hover:opacity-100"
                      title="填充到提问框"
                      @click.stop="handleFillMetric(metric)"
                    >
                      <i class="fa-solid fa-arrow-right text-[10px]"></i>
                    </button>
                  </div>
                  <div v-if="getFilteredMetrics(config).length === 0" class="px-4 py-2 text-[12px] text-[#9CA3AF]">暂无匹配指标</div>
                </div>
              </div>
            </div>
            <div v-if="(config.dimensions?.length || 0) === 0 && (config.metrics?.length || 0) === 0" class="px-4 py-1 text-[12px] text-[#9CA3AF]">
              暂无指标和维度
            </div>
          </div>
        </div>
      </div>

    </template>
  </div>
  <Teleport to="body">
    <div
      v-if="tooltipState.visible"
      class="fixed max-w-[420px] px-3 py-2 bg-[#373839] border border-white/15 rounded text-white text-[12px] z-[10000] whitespace-pre-wrap break-words -translate-y-1/2 shadow-[0_6px_16px_rgba(0,0,0,0.2)] select-text cursor-text"
      :style="{ left: tooltipState.x + 'px', top: tooltipState.y + 'px' }"
      @mouseenter="keepTooltipVisible"
      @mouseleave="hideTooltipNow"
    >
      <div class="absolute top-1/2 -translate-y-1/2" style="left: -10px; width: 10px; height: 16px; background: rgba(255,255,255,0.15); clip-path: polygon(100% 0, 100% 100%, 0 50%);"></div>
      <div class="absolute top-1/2 -translate-y-1/2" style="left: -9px; width: 9px; height: 14px; background: #373839; clip-path: polygon(100% 0, 100% 100%, 0 50%);"></div>
      <div
        v-for="section in tooltipState.sections"
        :key="section.title"
        class="space-y-1 border-t border-white/10 py-1 first:border-t-0 first:pt-0 last:pb-0"
      >
        <div class="text-[12px] font-semibold text-white">{{ section.title }}</div>
        <div v-for="line in section.lines" :key="line" class="leading-relaxed text-white/90">{{ line }}</div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.semantic-config-list {
  container-type: inline-size;
}

.semantic-config-summary {
  display: none;
}

@container (min-width: 320px) {
  .semantic-config-summary {
    display: inline;
  }
}
</style>
