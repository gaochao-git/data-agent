<script setup lang="ts">
/**
 * 异步查询结果组件
 * 支持表格、卡片、图表多种展示模式
 */
import { ref, computed, watch, useAttrs } from 'vue'
import ResizableTable from './ResizableTable.vue'
import MetricCards from './MetricCards.vue'
import ResultChart from './ResultChart.vue'
import { CHART_TYPES, normalizeChartType, type ChartType } from '../chartTypes'
import { getConversationArtifact } from '../api'

interface QueryResult {
  success: boolean
  sql?: string
  columns?: string[]
  data?: Record<string, unknown>[]
  total_count?: number
  execution_time_ms?: number
  error?: string
  cache_hit?: boolean
  cache_age_seconds?: number | null
  cache_expires_in_seconds?: number | null
}

interface MemberQueryResult extends QueryResult {
  member_datasource_id?: string
  member_name?: string
}

interface MultiQueryResult extends QueryResult {
  member_datasource_id?: string
  member_name?: string
  results?: MemberQueryResult[]
  artifact_id?: string
  llm_analysis_skipped?: boolean
  message?: string
}

interface SemanticParams {
  metrics?: string[]
  // dimensions 可以是字符串或对象
  dimensions?: Array<string | { name: string; time_grain?: string; [key: string]: unknown }>
  filters?: Array<{
    dimension?: string
    operator?: string
    value?: unknown
    [key: string]: unknown
  }>
}

interface VisualizationConfig {
  type?: string
  x_field?: string
  y_field?: string
}

type ResultChartType = Extract<ChartType, 'bar' | 'horizontal_bar' | 'line' | 'area' | 'pie' | 'funnel' | 'graph'>
type ViewMode = 'vertical' | 'card' | ChartType

interface Props {
  resultData: string
  semanticParams?: SemanticParams
  visualization?: VisualizationConfig
  sql?: string
  onSave?: (sql: string, chartType: string, memberDatasourceId?: string) => void
  onViewAnalysis?: () => void
  isAnalysisActive?: boolean
  paginationEnabled?: boolean
  pageSize?: number
  maxRows?: number
  showIndex?: boolean
  autoWrap?: boolean
  metricsFirst?: boolean
  allowFullscreen?: boolean
  initialViewMode?: ViewMode
}

defineOptions({
  name: 'AsyncQueryResult',
  inheritAttrs: false
})

const props = withDefaults(defineProps<Props>(), {
  isAnalysisActive: false,
  paginationEnabled: false,
  pageSize: 20,
  maxRows: 1000,
  showIndex: false,
  autoWrap: false,
  metricsFirst: false,
  allowFullscreen: true
})
const attrs = useAttrs()
const rootClass = computed(() => [
  'relative flex h-full flex-col overflow-hidden rounded-xl border border-black/[0.06] bg-white',
  attrs.class
])

const viewMode = ref<ViewMode>('table')
const artifactResult = ref<MultiQueryResult | null>(null)
const artifactLoading = ref(false)
const artifactError = ref('')

// 图表类型下拉菜单状态
const showChartMenu = ref(false)
const viewBtnRef = ref<HTMLElement | null>(null)
const menuStyle = ref<{ top: string; left: string }>({ top: '0px', left: '0px' })
const activeMemberResultIndex = ref(0)
const isFullscreen = ref(false)

// 更新菜单位置
const updateMenuPosition = () => {
  if (viewBtnRef.value) {
    const rect = viewBtnRef.value.getBoundingClientRect()
    menuStyle.value = {
      top: `${rect.top - 8}px`,
      left: `${rect.right - 120}px`
    }
  }
}

watch(showChartMenu, (val) => {
  if (val) {
    // 使用 requestAnimationFrame 确保 DOM 更新后计算位置
    requestAnimationFrame(updateMenuPosition)
  }
})

// 解析查询结果
const parseResult = computed<{ result: MultiQueryResult | null; error: string | null }>(() => {
  try {
    const parsed = JSON.parse(props.resultData)
    return { result: artifactResult.value || parsed, error: null }
  } catch {
    return { result: null, error: '无法解析查询结果' }
  }
})

const queryResult = computed(() => parseResult.value.result)
const parseError = computed(() => parseResult.value.error)
const artifactId = computed(() => {
  try {
    const parsed = JSON.parse(props.resultData) as MultiQueryResult
    return String(parsed.artifact_id || '').trim()
  } catch {
    return ''
  }
})
const memberResults = computed(() => queryResult.value?.results || [])
const hasMultipleMemberResults = computed(() => memberResults.value.length > 1)
const activeResult = computed<MemberQueryResult | MultiQueryResult | null>(() => {
  if (!memberResults.value.length) return queryResult.value
  return memberResults.value[activeMemberResultIndex.value] || memberResults.value[0] || queryResult.value
})

watch(
  () => memberResults.value.map(item => item.member_datasource_id || item.member_name || '').join('|'),
  () => {
    if (!memberResults.value.length) {
      activeMemberResultIndex.value = 0
      return
    }
    if (activeMemberResultIndex.value >= memberResults.value.length) {
      activeMemberResultIndex.value = 0
    }
  },
  { immediate: true }
)

watch(
  artifactId,
  async (id) => {
    artifactResult.value = null
    artifactError.value = ''
    if (!id) {
      artifactLoading.value = false
      return
    }
    artifactLoading.value = true
    try {
      const response = await getConversationArtifact<MultiQueryResult>(id)
      if (id !== artifactId.value) return
      artifactResult.value = response.data || null
      if (!artifactResult.value) {
        artifactError.value = '查询结果详情为空'
      }
    } catch (error) {
      if (id !== artifactId.value) return
      artifactError.value = error instanceof Error ? error.message : '加载查询结果详情失败'
    } finally {
      if (id === artifactId.value) {
        artifactLoading.value = false
      }
    }
  },
  { immediate: true }
)

const normalizeColumnName = (value: string) => (value || '').replace(/`/g, '').trim().toLowerCase()

// 表格列配置
const baseColumns = computed(() => {
  if (!activeResult.value?.columns) return []
  return activeResult.value.columns.map((col) => ({
    key: col,
    title: col,
    width: 150
  }))
})

const columns = computed(() => {
  if (!props.metricsFirst) return baseColumns.value
  const hasMetrics = (props.semanticParams?.metrics || []).length > 0
  const hasDimensions = (props.semanticParams?.dimensions || []).length > 0
  // 只有同时存在维度和指标时才做重排，避免仅指标场景下“列顺序被打乱”的体感
  if (!hasMetrics || !hasDimensions) return baseColumns.value
  const metricSet = new Set((props.semanticParams?.metrics || []).map(item => normalizeColumnName(String(item))))
  if (metricSet.size === 0) return baseColumns.value

  const metricColumns = baseColumns.value.filter(col => metricSet.has(normalizeColumnName(col.key)))
  if (metricColumns.length === 0) return baseColumns.value
  const nonMetricColumns = baseColumns.value.filter(col => !metricSet.has(normalizeColumnName(col.key)))
  return [...metricColumns, ...nonMetricColumns]
})

// 原始结果与前端展示数据
const rawData = computed(() => activeResult.value?.data || [])
const frontMaxRows = computed(() => Math.max(1, Number(props.maxRows) || 1000))
const limitedData = computed(() => {
  if (!props.paginationEnabled) return rawData.value
  return rawData.value.slice(0, frontMaxRows.value)
})
const isTruncated = computed(() => props.paginationEnabled && rawData.value.length > limitedData.value.length)

// 前端分页（仅表格）
const currentPage = ref(1)
const pageSizeValue = computed(() => Math.max(1, Number(props.pageSize) || 20))
const totalPages = computed(() => {
  if (!props.paginationEnabled) return 1
  return Math.max(1, Math.ceil(limitedData.value.length / pageSizeValue.value))
})

const clampCurrentPage = () => {
  if (currentPage.value > totalPages.value) currentPage.value = totalPages.value
  if (currentPage.value < 1) currentPage.value = 1
}

watch(
  () => [props.paginationEnabled, props.pageSize, props.maxRows, rawData.value.length],
  () => {
    clampCurrentPage()
    if (!props.paginationEnabled) currentPage.value = 1
  }
)

const pagedTableData = computed(() => {
  if (!props.paginationEnabled) return limitedData.value
  const start = (currentPage.value - 1) * pageSizeValue.value
  return limitedData.value.slice(start, start + pageSizeValue.value)
})

const tableData = computed(() => (viewMode.value === 'table' ? pagedTableData.value : limitedData.value))

const formatVerticalCellValue = (value: unknown): string => {
  if (value === null) return 'NULL'
  if (value === undefined) return ''
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean' || typeof value === 'bigint') return String(value)
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

const formatExecutionSeconds = (value?: number | null): string => {
  if (value === null || value === undefined) return ''
  const ms = Number(value)
  if (!Number.isFinite(ms) || ms < 0) return ''
  return `${(ms / 1000).toFixed(2)}秒`
}

const verticalText = computed(() => {
  const resultColumns = activeResult.value?.columns || []
  if (!limitedData.value.length) return ''
  if (resultColumns.length === 1) {
    const column = resultColumns[0] || ''
    return limitedData.value.map((row) => formatVerticalCellValue(row[column])).join('\n')
  }

  const maxColumnLength = resultColumns.reduce((max, col) => Math.max(max, col.length), 0)
  return limitedData.value.map((row, index) => {
    const header = `*************************** ${index + 1}. row ***************************`
    const body = resultColumns.map((col) => `${col.padStart(maxColumnLength, ' ')}: ${formatVerticalCellValue(row[col])}`).join('\n')
    return `${header}\n${body}`
  }).join('\n\n')
})

const pageStart = computed(() => {
  if (!limitedData.value.length) return 0
  return (currentPage.value - 1) * pageSizeValue.value + 1
})

const pageEnd = computed(() => {
  if (!limitedData.value.length) return 0
  return Math.min(currentPage.value * pageSizeValue.value, limitedData.value.length)
})

// 数值列检测
const numericColumns = computed(() => {
  if (!activeResult.value?.columns) return []
  const data = limitedData.value
  if (data.length === 0) return []

  return activeResult.value.columns.filter((col) => {
    const firstValue = data[0]?.[col]
    return typeof firstValue === 'number'
  })
})

// 图表可用性检测
const chartAvailable = computed(() => {
  return numericColumns.value.length > 0 && limitedData.value.length > 0
})

const valueChartAvailable = computed(() => {
  return chartAvailable.value
})

const graphAvailable = computed(() => {
  const columns = activeResult.value?.columns || []
  return columns.length >= 2 && limitedData.value.length > 0
})

// 视图模式配置
const unsupportedResultViewTypes = new Set<ChartType>(['kpi', 'map', 'combo'])
const viewModes: Array<{ key: ViewMode; icon: string; label: string }> = [
  ...CHART_TYPES
    .filter(item => !unsupportedResultViewTypes.has(item.key))
    .map(item => ({ key: item.key as ViewMode, icon: item.icon, label: item.label })),
  { key: 'card', icon: 'fa-id-card', label: '指标卡' },
  { key: 'vertical', icon: 'fa-align-left', label: '文本' },
]
const chartViewModes = new Set<ResultChartType>(
  CHART_TYPES
    .filter(item => item.echarts && !unsupportedResultViewTypes.has(item.key))
    .map(item => item.key as ResultChartType)
)
const isChartViewMode = (mode: ViewMode): mode is ResultChartType => chartViewModes.has(mode as ResultChartType)
const normalizeResultViewMode = (chartType: unknown): ViewMode => {
  const rawType = String(chartType || '').trim().toLowerCase()
  if (rawType === 'vertical' || rawType === 'card') return rawType
  const normalized = normalizeChartType(rawType)
  return unsupportedResultViewTypes.has(normalized) ? 'table' : normalized
}
const isViewModeDisabled = (mode: ViewMode) => {
  if (mode === 'graph') return !graphAvailable.value
  if (isChartViewMode(mode)) return !valueChartAvailable.value
  return false
}

// 初始化视图模式
watch(
  () => [props.initialViewMode, props.visualization?.type] as const,
  ([initialViewMode, visualizationType]) => {
    const nextMode = initialViewMode || visualizationType
    if (nextMode) viewMode.value = normalizeResultViewMode(nextMode)
  },
  { immediate: true }
)

// 保存到仪表板
const handleSave = () => {
  const sql = activeResult.value?.sql || props.sql
  if (props.onSave && sql) {
    props.onSave(
      sql,
      viewMode.value === 'vertical' || viewMode.value === 'card' ? 'table' : viewMode.value,
      activeResult.value?.member_datasource_id
    )
  }
}

const openFullscreen = () => {
  showChartMenu.value = false
  isFullscreen.value = true
}

const toggleVerticalView = () => {
  viewMode.value = viewMode.value === 'vertical' ? 'table' : 'vertical'
}

// 获取维度名称（处理字符串和对象两种格式）
const getDimensionName = (dim: string | { name: string; time_grain?: string }): string => {
  if (typeof dim === 'string') return dim
  if (dim.time_grain) return `${dim.name}(${dim.time_grain})`
  return dim.name
}

// 获取维度的唯一 key
const getDimensionKey = (dim: string | { name: string }): string => {
  return typeof dim === 'string' ? dim : dim.name
}
</script>

<template>
  <div
    v-bind="attrs"
    :class="rootClass"
  >
    <!-- 错误状态 -->
    <div v-if="parseError" class="flex flex-col items-center justify-center gap-3 p-8 text-sm text-red-600 bg-gradient-to-br from-red-100/50 to-red-200/30">
      <i class="fa-solid fa-circle-exclamation text-2xl opacity-80"></i>
      <span>{{ parseError }}</span>
    </div>

    <!-- 正常展示（包括无数据的情况） -->
    <template v-else>
      <div v-if="artifactLoading" class="flex flex-col items-center justify-center gap-3 p-8 text-sm text-gray-500 bg-gradient-to-br from-slate-50 to-slate-100">
        <i class="fa-solid fa-spinner fa-spin text-2xl text-[#1f82d9]"></i>
        <span>正在加载查询结果详情...</span>
      </div>

      <div v-else-if="artifactError" class="flex flex-col items-center justify-center gap-3 p-8 text-sm text-red-600 bg-gradient-to-br from-red-100/50 to-red-200/30">
        <i class="fa-solid fa-circle-exclamation text-2xl opacity-80"></i>
        <span>{{ artifactError }}</span>
      </div>

      <template v-else>
      <div v-if="hasMultipleMemberResults" class="shrink-0 flex items-center gap-2 px-3 py-2 border-b border-black/[0.04] bg-gradient-to-br from-slate-50 to-slate-100 overflow-x-auto">
        <button
          v-for="(item, index) in memberResults"
          :key="item.member_datasource_id || item.member_name || index"
          :class="[
            'shrink-0 h-7 px-3 rounded-md border text-xs transition-all duration-150',
            activeMemberResultIndex === index
              ? 'border-[#1f82d9] bg-[#e8f3ff] text-[#1f82d9]'
              : 'border-black/10 bg-white text-gray-600 hover:border-[#1f82d9]/40 hover:text-[#1f82d9]'
          ]"
          @click="activeMemberResultIndex = index"
        >
          {{ item.member_name || item.member_datasource_id || `结果${index + 1}` }}
        </button>
      </div>

      <!-- 查询失败 -->
      <div v-if="activeResult && !activeResult.success" class="flex-1 flex flex-col items-center justify-center gap-3 p-8 text-sm text-red-600 bg-gradient-to-br from-red-100/50 to-red-200/30">
        <i class="fa-solid fa-circle-exclamation text-2xl opacity-80"></i>
        <span>{{ activeResult.error || '查询失败' }}</span>
      </div>

      <!-- 内容区域 -->
      <div v-else class="relative flex-1 min-h-0 overflow-auto">
        <button
          v-if="allowFullscreen"
          type="button"
          class="absolute right-2 top-2 z-20 flex h-7 w-7 items-center justify-center rounded-md border border-black/10 bg-white/90 text-gray-500 shadow-sm backdrop-blur transition-all duration-150 hover:border-[#1f82d9]/40 hover:bg-white hover:text-[#1f82d9]"
          title="全屏查看"
          @click.stop="openFullscreen"
        >
          <i class="fa-solid fa-expand text-xs"></i>
        </button>

        <!-- 无数据 -->
        <div v-if="!tableData.length" class="flex flex-col items-center justify-center gap-3 p-8 text-sm text-gray-400 bg-gradient-to-br from-slate-50 to-slate-100">
          <i class="fa-solid fa-inbox text-2xl opacity-50"></i>
          <span>暂无数据</span>
        </div>

        <!-- 表格视图 -->
        <ResizableTable
          v-else-if="viewMode === 'table'"
          class="h-full"
          :columns="columns"
          :data="tableData"
          :show-index="showIndex"
          :auto-wrap="autoWrap"
          :row-index-start="paginationEnabled ? pageStart : 1"
          max-height="100%"
        />

        <!-- 文本视图，适合 SHOW CREATE TABLE 等长字段结果 -->
        <div v-else-if="viewMode === 'vertical'" class="h-full p-3 bg-slate-50">
          <textarea
            readonly
            class="w-full h-full resize-none rounded-md border border-black/10 bg-white px-3 py-2 font-mono text-xs leading-5 text-slate-800 outline-none whitespace-pre"
            :value="verticalText"
          ></textarea>
        </div>

        <!-- 指标卡视图 -->
        <MetricCards
          v-else-if="viewMode === 'card'"
          :data="tableData"
          :columns="activeResult?.columns || []"
          :metrics="semanticParams?.metrics"
          :dimensions="semanticParams?.dimensions?.map((d) => typeof d === 'string' ? d : d.name)"
        />

        <!-- 图表视图 -->
        <ResultChart
          v-else-if="isChartViewMode(viewMode)"
          :type="viewMode as ResultChartType"
          :data="tableData"
          :columns="activeResult?.columns || []"
          :x-field="visualization?.x_field"
          :y-field="visualization?.y_field"
          :numeric-columns="numericColumns"
        />
      </div>

      <!-- 底部工具栏 -->
      <div class="shrink-0 flex flex-wrap items-center justify-between gap-2 px-3 py-1.5 border-t border-black/[0.04] bg-gradient-to-br from-slate-50 to-slate-100">
        <!-- 左侧：状态信息 -->
        <div class="flex min-w-0 flex-wrap items-center gap-2">
          <span class="shrink-0 whitespace-nowrap flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-semibold text-emerald-500 bg-gradient-to-br from-emerald-500/12 to-emerald-500/6">
            <i class="fa-solid fa-circle-check text-[10px]"></i>
            成功
          </span>
          <span v-if="activeResult?.execution_time_ms != null" class="shrink-0 whitespace-nowrap text-xs text-gray-500 px-1.5 py-0.5 bg-black/[0.04] rounded">
            {{ formatExecutionSeconds(activeResult.execution_time_ms) }}
          </span>
          <span v-if="activeResult?.total_count !== undefined" class="shrink-0 whitespace-nowrap text-xs font-medium text-gray-700">
            共 {{ activeResult.total_count }} 条
          </span>
          <span v-if="activeResult?.cache_hit" class="shrink-0 whitespace-nowrap text-xs text-[#165DFF] px-1.5 py-0.5 bg-[#E8F3FF] rounded">
            缓存结果
          </span>
          <span v-if="isTruncated" class="shrink-0 whitespace-nowrap text-xs text-amber-600">
            已按前端上限展示前 {{ frontMaxRows }} 条
          </span>
        </div>

        <!-- 中间：翻页器 -->
        <div
          v-if="paginationEnabled && viewMode === 'table' && limitedData.length > 0"
          class="flex shrink-0 flex-wrap items-center gap-1.5 text-xs text-gray-600"
        >
          <button
            class="h-6 px-2 border border-black/10 rounded bg-white disabled:opacity-40 disabled:cursor-not-allowed"
            :disabled="currentPage <= 1"
            @click="currentPage = Math.max(1, currentPage - 1)"
          >
            上一页
          </button>
          <span class="whitespace-nowrap">{{ pageStart }}-{{ pageEnd }} / {{ limitedData.length }}</span>
          <span class="whitespace-nowrap">第 {{ currentPage }} / {{ totalPages }} 页</span>
          <button
            class="h-6 px-2 border border-black/10 rounded bg-white disabled:opacity-40 disabled:cursor-not-allowed"
            :disabled="currentPage >= totalPages"
            @click="currentPage = Math.min(totalPages, currentPage + 1)"
          >
            下一页
          </button>
        </div>

        <!-- 右侧：视图切换和保存 -->
        <div class="flex shrink-0 flex-wrap items-center justify-end gap-2">
          <!-- 查看分析过程按钮 -->
          <button
            v-if="onViewAnalysis"
            :class="[
              'shrink-0 whitespace-nowrap h-[26px] min-w-[62px] px-2.5 flex items-center justify-center gap-1.5 border rounded-md text-[11px] cursor-pointer transition-all duration-200',
              isAnalysisActive
                ? 'border-blue-500 text-blue-500 bg-blue-500/10'
                : 'border-gray-300 text-gray-600 bg-white hover:border-blue-500 hover:text-blue-500 hover:bg-blue-500/5'
            ]"
            @click="onViewAnalysis"
          >
            <i class="fa-solid fa-diagram-project text-[11px]"></i>
            <span>分析</span>
          </button>

          <button
            class="shrink-0 whitespace-nowrap h-[26px] min-w-[62px] px-2.5 flex items-center justify-center gap-1.5 border border-black/10 rounded-md bg-white text-gray-600 text-[11px] cursor-pointer transition-all duration-200 hover:border-[#1f82d9]/40 hover:bg-gradient-to-br hover:from-[#1f82d9]/10 hover:to-[#1f82d9]/5 hover:text-[#1f82d9]"
            :title="viewMode === 'vertical' ? '切回表格视图' : '切换到文本，适合查看长字段结果'"
            @click="toggleVerticalView"
          >
            <i :class="`fa-solid ${viewMode === 'vertical' ? 'fa-table' : 'fa-align-left'}`"></i>
            <span>{{ viewMode === 'vertical' ? '表格' : '文本' }}</span>
          </button>

          <!-- 视图切换下拉 -->
          <div class="relative">
            <button
              ref="viewBtnRef"
              class="shrink-0 whitespace-nowrap h-[26px] min-w-[62px] px-2.5 flex items-center justify-center gap-1.5 border border-transparent rounded-md bg-gradient-to-br from-[#1f82d9] to-[#135589] text-white text-[11px] cursor-pointer transition-all duration-200 shadow-[0_1px_4px_rgba(31,130,217,0.3)]"
              title="切换视图"
              @click="showChartMenu = !showChartMenu"
            >
              <i :class="`fa-solid ${viewModes.find(m => m.key === viewMode)?.icon || 'fa-table'}`"></i>
              <span>视图</span>
              <i class="fa-solid fa-chevron-down text-[8px] ml-0.5 opacity-60"></i>
            </button>

            <!-- 下拉菜单（使用 Teleport 避免被裁剪） -->
            <Teleport to="body">
              <div v-if="showChartMenu" class="fixed inset-0 z-[9999] pointer-events-none">
                <div class="fixed inset-0 pointer-events-auto" @click="showChartMenu = false"></div>
                <div
                  ref="chartMenuRef"
                  class="fixed -translate-y-full min-w-[100px] p-1 bg-white border border-black/10 rounded-lg shadow-[0_4px_16px_rgba(0,0,0,0.15)] pointer-events-auto"
                  :style="menuStyle"
                >
                  <button
                    v-for="mode in viewModes"
                    :key="mode.key"
                    :class="[
                      'w-full flex items-center gap-2 px-3 py-2 border-none rounded-md bg-transparent text-xs cursor-pointer transition-all duration-150 whitespace-nowrap',
                      viewMode === mode.key
                        ? 'bg-gradient-to-br from-[#1f82d9]/15 to-[#1f82d9]/5 text-[#1f82d9]'
                        : 'text-gray-700 hover:bg-gray-100',
                      isViewModeDisabled(mode.key) && 'opacity-40 cursor-not-allowed'
                    ]"
                    :disabled="isViewModeDisabled(mode.key)"
                    @click="viewMode = mode.key; showChartMenu = false"
                  >
                    <i :class="`fa-solid ${mode.icon} w-3.5 text-center`"></i>
                    <span>{{ mode.label }}</span>
                  </button>
                </div>
              </div>
            </Teleport>
          </div>

          <!-- 保存按钮 -->
          <button
            v-if="activeResult?.sql || sql"
            class="shrink-0 whitespace-nowrap h-[26px] min-w-[62px] px-2.5 flex items-center justify-center gap-1.5 border border-black/10 rounded-md bg-white text-gray-600 text-[11px] cursor-pointer transition-all duration-200 hover:border-[#1f82d9]/40 hover:bg-gradient-to-br hover:from-[#1f82d9]/10 hover:to-[#1f82d9]/5 hover:text-[#1f82d9]"
            title="保存到看板"
            @click="handleSave"
          >
            <i class="fa-solid fa-bookmark"></i>
            <span>保存</span>
          </button>
        </div>
      </div>
      </template>
    </template>
  </div>

  <el-dialog
    v-if="allowFullscreen"
    v-model="isFullscreen"
    width="calc(100vw - 48px)"
    top="24px"
    append-to-body
    :z-index="3900"
    :show-close="false"
    :close-on-click-modal="true"
    class="query-result-fullscreen-dialog"
  >
    <template #header>
      <div class="flex items-center justify-between gap-3">
        <div class="flex min-w-0 items-center gap-2 text-sm font-semibold text-slate-700">
          <i class="fa-solid fa-table text-[#1f82d9]"></i>
          <span>查询结果</span>
        </div>
        <button
          type="button"
          class="flex h-8 w-8 items-center justify-center rounded-md border border-black/10 bg-white text-gray-500 transition-colors duration-150 hover:border-[#1f82d9]/40 hover:text-[#1f82d9]"
          title="退出全屏"
          @click="isFullscreen = false"
        >
          <i class="fa-solid fa-compress text-xs"></i>
        </button>
      </div>
    </template>

    <div style="height: calc(100vh - 132px); min-height: 420px;">
      <AsyncQueryResult
        class="h-full"
        :result-data="resultData"
        :semantic-params="semanticParams"
        :visualization="visualization"
        :sql="sql"
        :on-save="onSave"
        :on-view-analysis="onViewAnalysis"
        :is-analysis-active="isAnalysisActive"
        :pagination-enabled="paginationEnabled"
        :page-size="pageSize"
        :max-rows="maxRows"
        :show-index="showIndex"
        :auto-wrap="autoWrap"
        :metrics-first="metricsFirst"
        :allow-fullscreen="false"
        :initial-view-mode="viewMode"
      />
    </div>
  </el-dialog>
</template>
