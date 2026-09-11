<script setup lang="ts">
import { ref, reactive, computed, onMounted, onUnmounted, watch } from 'vue'
import FieldTypeIcon from '../icons/FieldTypeIcon.vue'
import type { SemanticConfigItem, SemanticDimension, SemanticMetric, TableInfo } from '../../types'
import { findColumnDict, type FieldTooltipSection } from '../../utils/fieldDict'
import {
  buildDimensionTooltip as sharedBuildDimensionTooltip,
  buildMetricTooltip as sharedBuildMetricTooltip,
  isLogicalMetric,
  metricIconColorClass,
  type SemanticTooltipContext,
} from '../../utils/semanticFieldTooltip'
import { useColumnDictCache } from '../../composables/useColumnDictCache'

interface Props {
  datasourceId?: string
  semanticConfig?: SemanticConfigItem | null
  semanticConfigs?: SemanticConfigItem[]
  selectedDatasetId?: string | null
  tables?: TableInfo[]
  loading?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  datasourceId: '',
  semanticConfig: null,
  tables: () => [],
  loading: false
})

const emit = defineEmits<{
  dragDimension: [dimension: SemanticDimension, event: DragEvent]
  dragMetric: [metric: SemanticConfigItem['metrics'][number], event: DragEvent]
  dragEnd: []
  datasetChange: [datasetId: string]
  refresh: []
  fillQuestion: [payload: { fieldName: string; fieldType: 'dimension' | 'metric' }]
}>()

const tooltipState = reactive({
  visible: false,
  x: 0,
  y: 0,
  sections: [] as FieldTooltipSection[]
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

const containerRef = ref<HTMLElement | null>(null)
const datasetSelectRef = ref<HTMLElement | null>(null)
const sectionHeights = ref([50, 50])
const dimensionKeyword = ref('')
const metricKeyword = ref('')
const datasetKeyword = ref('')
const datasetDropdownVisible = ref(false)
const resizeState = ref<{ active: boolean; handleIndex: number; startY: number; startHeights: number[] }>({
  active: false,
  handleIndex: 0,
  startY: 0,
  startHeights: [50, 50]
})

const startSectionResize = (handleIndex: number, e: MouseEvent) => {
  e.preventDefault()
  resizeState.value = {
    active: true,
    handleIndex,
    startY: e.clientY,
    startHeights: [...sectionHeights.value]
  }
  document.addEventListener('mousemove', onSectionResize)
  document.addEventListener('mouseup', stopSectionResize)
}

const onSectionResize = (e: MouseEvent) => {
  if (!resizeState.value.active) return
  const container = containerRef.value
  if (!container) return
  const deltaPercent = ((e.clientY - resizeState.value.startY) / container.clientHeight) * 100
  const idx = resizeState.value.handleIndex
  const next = [...resizeState.value.startHeights]
  const minPercent = 10
  next[idx] = Math.max(minPercent, (next[idx] ?? minPercent) + deltaPercent)
  next[idx + 1] = Math.max(minPercent, (next[idx + 1] ?? minPercent) - deltaPercent)
  sectionHeights.value = next
}

const stopSectionResize = () => {
  resizeState.value.active = false
  document.removeEventListener('mousemove', onSectionResize)
  document.removeEventListener('mouseup', stopSectionResize)
}

const getDimensionType = (dim: SemanticDimension): 'string' | 'number' | 'date' => {
  if (dim.logical_type === 'number') return 'number'
  if (dim.logical_type === 'date' || dim.logical_type === 'datetime') return 'date'
  return 'string'
}

const tooltipContext = (): SemanticTooltipContext => ({
  tables: props.tables,
  resolveColumnDict: (tableName, columnName) =>
    getColumnDict(props.datasourceId, tableName || '', columnName || '')
    || findColumnDict(props.tables, tableName, columnName),
})

const buildDimensionTooltip = (dim: SemanticDimension) => sharedBuildDimensionTooltip(dim, tooltipContext())
const buildMetricTooltip = (metric: SemanticMetric) => sharedBuildMetricTooltip(metric, tooltipContext())

const handleFillQuestion = (fieldName: string | undefined, fieldType: 'dimension' | 'metric') => {
  const normalizedName = String(fieldName || '').trim()
  if (!normalizedName) return
  emit('fillQuestion', { fieldName: normalizedName, fieldType })
}

const filteredDimensions = computed(() => {
  const dimensions = props.semanticConfig?.dimensions || []
  const keyword = dimensionKeyword.value.trim().toLowerCase()
  if (!keyword) return dimensions
  return dimensions.filter(dim => {
    const name = (dim.name || '').toLowerCase()
    const col = (dim.column || '').toLowerCase()
    const expr = (dim.expression || '').toLowerCase()
    return name.includes(keyword) || col.includes(keyword) || expr.includes(keyword)
  })
})

const filteredMetrics = computed(() => {
  const metrics = props.semanticConfig?.metrics || []
  const keyword = metricKeyword.value.trim().toLowerCase()
  if (!keyword) return metrics
  return metrics.filter(metric => {
    const name = (metric.name || '').toLowerCase()
    const col = (metric.field_name || '').toLowerCase()
    const expr = (metric.expression || '').toLowerCase()
    return name.includes(keyword) || col.includes(keyword) || expr.includes(keyword)
  })
})

const availableSemanticConfigs = computed(() => {
  const source = props.semanticConfigs?.length ? props.semanticConfigs : (props.semanticConfig ? [props.semanticConfig] : [])
  return source
})

const filteredSemanticConfigs = computed(() => {
  const keyword = datasetKeyword.value.trim().toLowerCase()
  if (!keyword) return availableSemanticConfigs.value
  return availableSemanticConfigs.value.filter(cfg => {
    const name = (cfg.name || '').toLowerCase()
    const desc = (cfg.description || '').toLowerCase()
    return name.includes(keyword) || desc.includes(keyword)
  })
})

const currentDatasetValue = computed(() => {
  const currentId = props.selectedDatasetId
  if (availableSemanticConfigs.value.some(cfg => cfg.id === currentId)) {
    return currentId
  }
  return availableSemanticConfigs.value[0]?.id || ''
})

const currentDatasetName = computed(() => {
  return availableSemanticConfigs.value.find(cfg => cfg.id === currentDatasetValue.value)?.name || '请选择语义模型'
})

const toggleDatasetDropdown = () => {
  datasetDropdownVisible.value = !datasetDropdownVisible.value
  if (datasetDropdownVisible.value) {
    datasetKeyword.value = ''
  }
}

const selectDataset = (datasetId: string) => {
  emit('datasetChange', datasetId)
  datasetDropdownVisible.value = false
  datasetKeyword.value = ''
}

const getConfigTableNames = (config?: SemanticConfigItem | null) => [
  ...((config?.dimensions || []).map(item => item.table)),
  ...((config?.metrics || []).map(item => item.table)),
]

const handleDocumentClick = (event: MouseEvent) => {
  if (!datasetDropdownVisible.value) return
  const target = event.target as Node
  if (datasetSelectRef.value?.contains(target)) return
  datasetDropdownVisible.value = false
}

onMounted(() => {
  document.addEventListener('click', handleDocumentClick)
  void loadTablesColumnDicts(props.datasourceId, getConfigTableNames(props.semanticConfig))
})

watch(
  () => [props.datasourceId, props.semanticConfig?.id],
  () => {
    resetColumnDictCache()
    void loadTablesColumnDicts(props.datasourceId, getConfigTableNames(props.semanticConfig))
  }
)

onUnmounted(() => {
  clearTooltipHideTimer()
  document.removeEventListener('mousemove', onSectionResize)
  document.removeEventListener('mouseup', stopSectionResize)
  document.removeEventListener('click', handleDocumentClick)
})
</script>

<template>
  <div class="h-full flex flex-col min-h-0">
    <div class="mb-1 sidebar-baseline-target flex items-center gap-0">
      <div ref="datasetSelectRef" class="relative flex-1 min-w-0">
        <button
          type="button"
          class="flex h-[28px] w-full min-w-0 items-center justify-between gap-1 rounded-l rounded-r-none border border-[#DDE1E8] bg-white px-2 text-left text-[13px] font-medium text-[#21252C] outline-none hover:border-[#165DFF]"
          :title="currentDatasetName"
          @click.stop="toggleDatasetDropdown"
        >
          <span class="min-w-0 flex-1 overflow-hidden text-ellipsis whitespace-nowrap">{{ currentDatasetName }}</span>
          <i :class="['fa-solid text-[10px] text-[#8A8F99] transition-transform', datasetDropdownVisible ? 'fa-chevron-up' : 'fa-chevron-down']"></i>
        </button>
        <div
          v-if="datasetDropdownVisible"
          class="absolute left-0 top-[31px] z-[30] w-[calc(100%+32px)] rounded-lg border border-[#DDE1E8] bg-white p-2 shadow-[0_8px_24px_rgba(15,23,42,0.18)]"
          @click.stop
        >
          <input
            v-model.trim="datasetKeyword"
            type="text"
            placeholder="搜索语义模型"
            class="mb-1 h-[28px] w-full rounded border border-[#DDE1E8] bg-white px-2 text-[12px] text-[#21252C] outline-none focus:border-[#165DFF] placeholder:text-[#93949580]"
          />
          <div class="max-h-[190px] overflow-y-auto">
            <button
              v-for="cfg in filteredSemanticConfigs"
              :key="cfg.id"
              type="button"
              :title="cfg.name"
              :class="[
                'flex h-[28px] w-full min-w-0 items-center gap-2 overflow-hidden rounded px-2 text-left text-[13px] transition-colors',
                currentDatasetValue === cfg.id ? 'bg-[#E8F3FF] text-[#165DFF]' : 'text-[#21252C] hover:bg-[#F2F3F5]'
              ]"
              @click="selectDataset(cfg.id)"
            >
              <i v-if="currentDatasetValue === cfg.id" class="fa-solid fa-check w-3 shrink-0 text-[11px]"></i>
              <span v-else class="w-3 shrink-0"></span>
              <span class="min-w-0 flex-1 overflow-hidden text-ellipsis whitespace-nowrap">{{ cfg.name }}</span>
            </button>
            <div v-if="filteredSemanticConfigs.length === 0" class="px-2 py-2 text-[12px] text-[#9CA3AF]">无匹配语义模型</div>
          </div>
        </div>
      </div>
      <button
        type="button"
        class="ml-1 h-[28px] w-[28px] shrink-0 rounded border border-[#DDE1E8] bg-white text-[#5B5C60] transition-colors hover:border-[#165DFF] hover:text-[#165DFF] disabled:cursor-not-allowed disabled:opacity-50"
        :disabled="loading"
        title="刷新语义模型"
        @click="emit('refresh')"
      >
        <i :class="['fa-solid fa-rotate-right text-[12px]', loading ? 'fa-spin' : '']"></i>
      </button>
    </div>
    <div ref="containerRef" class="semantic-resize-container mt-0 flex flex-col overflow-hidden flex-1 min-h-0">
      <div
        class="flex flex-col overflow-hidden px-2"
        :style="{ height: sectionHeights[0] + '%' }"
      >
        <div class="flex items-center gap-2 px-2.5 pt-1 pb-1 select-none shrink-0">
          <span class="shrink-0 text-[12px] font-semibold text-[#2e2f32]">维度</span>
          <input
            v-model.trim="dimensionKeyword"
            type="text"
            placeholder="搜索维度"
            class="h-[24px] min-w-0 flex-1 rounded border border-[#DDE1E8] bg-white px-2 text-[12px] text-[#21252C] outline-none focus:border-[#165DFF] placeholder:text-[#93949580]"
          />
        </div>
        <div class="flex-1 overflow-y-scroll always-scrollbar pr-1">
          <div
            v-for="dim in filteredDimensions"
            :key="`dim-${dim.table_alias || dim.table}-${dim.name}`"
            class="group flex items-center gap-2 h-[28px] px-2 rounded cursor-grab transition-colors duration-100 hover:bg-[#f0f1f3] active:cursor-grabbing"
            draggable="true"
            @mouseenter="showTooltip($event, buildDimensionTooltip(dim))"
            @mouseleave="hideTooltip"
            @dragstart="emit('dragDimension', dim, $event)"
            @dragend="emit('dragEnd')"
          >
            <FieldTypeIcon :type="getDimensionType(dim)" class="text-[#165DFF] mr-1.5 shrink-0" />
            <span class="min-w-0 flex-1 text-xs text-[#21252C] overflow-hidden text-ellipsis whitespace-nowrap">
              {{ dim.name }}
            </span>
            <button
              type="button"
              draggable="false"
              class="ml-1 flex h-5 w-5 shrink-0 cursor-pointer items-center justify-center rounded text-[#165DFF] opacity-0 transition-all duration-150 hover:bg-[#E8F3FF] hover:text-[#0E42D2] group-hover:opacity-100"
              title="填充查询问题"
              @mousedown.stop
              @click.stop="handleFillQuestion(dim.name, 'dimension')"
            >
              <i class="fa-solid fa-arrow-right text-[10px]"></i>
            </button>
          </div>
          <div v-if="filteredDimensions.length === 0" class="px-2 py-1 text-[12px] text-[#9CA3AF]">暂无维度</div>
        </div>
      </div>

      <div class="relative shrink-0 flex items-center justify-center h-[16px] my-1 cursor-row-resize group select-none" @mousedown="startSectionResize(0, $event)">
        <div class="w-full h-px bg-[#E5E7EB]"></div>
        <div class="absolute w-10 h-[4px] rounded-full bg-[#D1D5DB] group-hover:bg-[#9CA3AF] transition-colors"></div>
      </div>

      <div
        class="flex flex-col overflow-hidden px-2 pt-1"
        :style="{ height: sectionHeights[1] + '%' }"
      >
        <div class="flex items-center gap-2 px-2.5 pt-1 pb-1 select-none shrink-0">
          <span class="shrink-0 text-[12px] font-semibold text-[#2e2f32]">指标</span>
          <input
            v-model.trim="metricKeyword"
            type="text"
            placeholder="搜索指标"
            class="h-[24px] min-w-0 flex-1 rounded border border-[#DDE1E8] bg-white px-2 text-[12px] text-[#21252C] outline-none focus:border-[#165DFF] placeholder:text-[#93949580]"
          />
        </div>
        <div class="flex-1 overflow-y-scroll always-scrollbar pr-1">
          <div
            v-for="metric in filteredMetrics"
            :key="`metric-${metric.table_alias || metric.table}-${metric.name}`"
            :class="[
              'group flex items-center gap-2 h-[28px] px-2 rounded transition-colors duration-100 hover:bg-[#f0f1f3]',
              isLogicalMetric(metric) ? 'cursor-default' : 'cursor-grab active:cursor-grabbing'
            ]"
            :draggable="!isLogicalMetric(metric)"
            @mouseenter="showTooltip($event, buildMetricTooltip(metric))"
            @mouseleave="hideTooltip"
            @dragstart="!isLogicalMetric(metric) && emit('dragMetric', metric, $event)"
            @dragend="emit('dragEnd')"
          >
            <FieldTypeIcon type="number" :class="['mr-1.5 shrink-0', metricIconColorClass(metric)]" />
            <span class="min-w-0 flex-1 text-xs text-[#21252C] overflow-hidden text-ellipsis whitespace-nowrap">
              {{ metric.name }}
            </span>
            <button
              type="button"
              draggable="false"
              class="ml-1 flex h-5 w-5 shrink-0 cursor-pointer items-center justify-center rounded text-[#165DFF] opacity-0 transition-all duration-150 hover:bg-[#E8F3FF] hover:text-[#0E42D2] group-hover:opacity-100"
              :title="isLogicalMetric(metric) ? '插入到指标区' : '填充查询问题'"
              @mousedown.stop
              @click.stop="handleFillQuestion(metric.name, 'metric')"
            >
              <i class="fa-solid fa-arrow-right text-[10px]"></i>
            </button>
          </div>
          <div v-if="filteredMetrics.length === 0" class="px-2 py-1 text-[12px] text-[#9CA3AF]">暂无指标</div>
        </div>
      </div>
    </div>
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
.always-scrollbar {
  scrollbar-gutter: stable;
}

.always-scrollbar::-webkit-scrollbar {
  width: 8px;
}

.always-scrollbar::-webkit-scrollbar-track {
  background: #f3f4f6;
  border-radius: 9999px;
}

.always-scrollbar::-webkit-scrollbar-thumb {
  background: #c8ccd4;
  border-radius: 9999px;
}

.always-scrollbar::-webkit-scrollbar-thumb:hover {
  background: #aeb4bf;
}
</style>
