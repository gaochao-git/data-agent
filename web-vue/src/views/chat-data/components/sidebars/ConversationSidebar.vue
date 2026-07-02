<script setup lang="ts">
/**
 * 会话列表侧边栏
 * 用于智能问数模式，包含指标和会话两个 tab
 * 看板已提取为独立的 DashboardPanel 组件
 */
import { ref, reactive, computed, watch, onUnmounted, type ComponentPublicInstance } from 'vue'
import { getSemanticConfigs } from '../../api'
import type { SemanticConfigItem, SemanticDimension, SemanticMetric, TableInfo } from '../../types'
import { getSemanticConfigTableCount, getSemanticConfigsTableCount } from '../../utils'
import {
  buildFieldDictSection,
  createTooltipSection,
  extractMetricFieldName as extractMetricSourceFieldName,
  findColumnDict,
  type FieldTooltipSection
} from '../../utils/fieldDict'
import { useColumnDictCache } from '../../composables/useColumnDictCache'
import FieldTypeIcon from '../icons/FieldTypeIcon.vue'

interface ConversationItem {
  id: string
  title: string
  created_at: string
}

interface ConversationGroup {
  label: string
  items: ConversationItem[]
}

const CONVERSATION_ICON_POOL = [
  { icon: 'fa-comment-dots', color: '#B3DBD2' }, // 普通对话
  { icon: 'fa-chart-line', color: '#B3DBD2' },   // 聚合查询
  { icon: 'fa-table', color: '#B3DBD2' },        // 明细查询
] as const

const getConversationIconMeta = (conversationId: string | undefined) => {
  const id = conversationId || ''
  let hash = 0
  for (let i = 0; i < id.length; i++) {
    hash = (hash * 31 + id.charCodeAt(i)) >>> 0
  }
  return CONVERSATION_ICON_POOL[hash % CONVERSATION_ICON_POOL.length] || CONVERSATION_ICON_POOL[0]
}

interface Props {
  activeTab: 'metrics' | 'conversation'
  groupedConversations?: ConversationGroup[]
  currentConversationId?: string | null
  isLoading?: boolean
  isLoadingMore?: boolean
  hasMore?: boolean
  showNewConversationButton?: boolean
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
  selectDataset: [configId: string | null]
  refreshSemanticConfigs: []
  fillQuestion: [payload: { fieldName: string; fieldType: 'dimension' | 'metric' }]
}>()

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
let tooltipHideTimer: ReturnType<typeof window.setTimeout> | null = null

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
  return `${semanticConfigs.value.length}指标集/${semanticConfigTableCount.value}表`
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
  return `${getConfigScopeLabel(config)}指标集`
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
  if (!props.hasMore || props.isLoading || props.isLoadingMore) return
  const target = event.currentTarget as HTMLElement
  if (target.scrollHeight - target.scrollTop - target.clientHeight <= 80) {
    emit('loadMoreConversations')
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
  if (dimension.type === 'number') return 'number'
  if (dimension.type === 'time') return 'date'
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
  <div class="flex flex-col h-full overflow-hidden">
    <!-- ==================== 会话 Tab ==================== -->
    <template v-if="activeTab === 'conversation'">
      <!-- 新建会话按钮 -->
      <div v-if="showNewConversationButton" class="p-4">
        <button
          class="w-[82%] mx-auto flex items-center justify-center gap-2 px-5 py-3 border-none rounded-lg bg-gradient-to-br from-[#1f82d9] to-[#135589] text-white text-sm font-medium cursor-pointer transition-all duration-200 shadow-[0_2px_8px_rgba(31,130,217,0.3)] hover:-translate-y-0.5 hover:shadow-[0_4px_12px_rgba(31,130,217,0.4)]"
          @click="emit('newConversation')"
        >
          <i class="fa-solid fa-plus"></i>
          <span>新建会话</span>
        </button>
      </div>

      <!-- 会话列表 -->
      <div class="flex-1 overflow-y-auto px-4 pb-4 scrollbar-thin scrollbar-thumb-black/10" @scroll="handleConversationScroll">
        <div v-if="isLoading" class="flex flex-col items-center justify-center gap-2 p-8 text-gray-400 text-xs">
          <i class="fa-solid fa-spinner fa-spin text-2xl opacity-50"></i>
          <span>加载历史会话...</span>
        </div>

        <div v-else-if="groupedConversations.length === 0" class="flex flex-col items-center justify-center gap-2 p-8 text-gray-400 text-xs">
          <i class="fa-regular fa-message text-2xl opacity-50"></i>
          <span>暂无会话</span>
        </div>

        <template v-else>
          <template v-for="group in groupedConversations" :key="group.label">
            <div class="mb-2">
              <div class="py-2 text-xs text-gray-400 font-medium uppercase tracking-wide">{{ group.label }}</div>
              <div
                v-for="conv in group.items"
                :key="conv.id"
                class="flex items-center gap-2 py-3 cursor-pointer transition-all duration-150"
                :class="currentConversationId === conv.id ? 'text-blue-500 font-medium' : 'text-gray-800 hover:text-blue-500'"
                @click="emit('selectConversation', conv.id)"
              >
                <i
                  :class="['fa-solid text-xs shrink-0', getConversationIconMeta(conv.id).icon]"
                  :style="{ color: getConversationIconMeta(conv.id).color }"
                ></i>
                <span class="flex-1 text-sm overflow-hidden text-ellipsis whitespace-nowrap leading-relaxed">{{ conv.title }}</span>
              </div>
            </div>
          </template>
          <div v-if="isLoadingMore" class="flex items-center justify-center gap-2 py-3 text-xs text-gray-400">
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
        <span>暂无指标集</span>
      </div>

      <div v-else class="semantic-config-list flex-1 min-h-0 overflow-y-auto px-2 py-1 scrollbar-thin scrollbar-thumb-black/10">
        <div class="mb-2 px-3">
          <div class="flex h-8 items-center rounded-md border border-[#E5E6EB] bg-white px-2.5 text-[#86909C] transition-colors focus-within:border-[#165DFF]">
            <i class="fa-solid fa-magnifying-glass mr-2 text-[11px]"></i>
            <input
              v-model="configSearchKeyword"
              class="h-full min-w-0 flex-1 border-none bg-transparent p-0 text-[12px] text-[#1D2129] outline-none placeholder:text-[#A8ABB2]"
              placeholder="搜索指标集、维度、指标"
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
          :title="`智能选择指标集：可用指标集 ${semanticConfigs.length} 个，涉及表 ${semanticConfigTableCount} 张`"
          @click="handleSelectConfig(null)"
        >
          <span
            :class="[
              'mr-2 flex h-4 w-4 shrink-0 cursor-pointer items-center justify-center rounded-full border text-[9px]',
              selectedConfigId === null ? 'border-[#165DFF] bg-[#165DFF] text-white' : 'border-[#C9CDD4] bg-white text-transparent hover:border-[#165DFF]'
            ]"
            title="使用智能选择指标集提问"
          >
            <i class="fa-solid fa-check"></i>
          </span>
          <span class="min-w-0 flex-1 overflow-hidden text-ellipsis whitespace-nowrap font-semibold text-[#2E2F32]">智能选择指标集</span>
          <span
            class="semantic-config-summary ml-2 shrink-0 text-[10px] text-[#939495]"
            :title="`可用指标集：${semanticConfigs.length}；涉及表：${semanticConfigTableCount}`"
          >
            {{ getSmartSelectionSummary() }}
          </span>
          <button
            type="button"
            class="ml-1 flex h-5 w-5 items-center justify-center rounded text-[#5B5C60] transition-colors hover:bg-[#E8F3FF] hover:text-[#165DFF] disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="isLoadingConfigs"
            title="刷新指标集"
            @click.stop="loadSemanticConfigs"
          >
            <i :class="['fa-solid fa-rotate-right text-[10px]', isLoadingConfigs ? 'fa-spin' : '']"></i>
          </button>
        </div>

        <div
          v-if="filteredSemanticConfigs.length === 0"
          class="px-4 py-5 text-center text-[12px] text-[#A8ABB2]"
        >
          未找到匹配的指标集
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
              :title="`${selectedConfigId === config.id ? '当前提问使用该指标集' : '使用该指标集提问'}（${getConfigScopeTitle(config)}）`"
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
