<script setup lang="ts">
import { ref, reactive, computed, onUnmounted, withDefaults } from 'vue'
import FieldTypeIcon from '../icons/FieldTypeIcon.vue'
import type { SemanticConfigItem, SemanticDimension } from '../../types'

interface Props {
  semanticConfig: SemanticConfigItem
  semanticConfigs?: SemanticConfigItem[]
  selectedDatasetId?: string | null
  loading?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  loading: false
})

const emit = defineEmits<{
  dragDimension: [dimension: SemanticDimension, event: DragEvent]
  dragMetric: [metric: SemanticConfigItem['metrics'][number], event: DragEvent]
  dragEnd: []
  datasetChange: [datasetId: string]
  refresh: []
}>()

const tooltipState = reactive({
  visible: false,
  x: 0,
  y: 0,
  lines: [] as string[]
})

const showTooltip = (e: MouseEvent, lines: string[]) => {
  const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
  tooltipState.x = rect.right + 8
  tooltipState.y = rect.top + rect.height / 2
  tooltipState.lines = lines
  tooltipState.visible = true
}

const hideTooltip = () => {
  tooltipState.visible = false
}

const containerRef = ref<HTMLElement | null>(null)
const sectionHeights = ref([50, 50])
const globalKeyword = ref('')
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
  next[idx] = Math.max(minPercent, next[idx] + deltaPercent)
  next[idx + 1] = Math.max(minPercent, next[idx + 1] - deltaPercent)
  sectionHeights.value = next
}

const stopSectionResize = () => {
  resizeState.value.active = false
  document.removeEventListener('mousemove', onSectionResize)
  document.removeEventListener('mouseup', stopSectionResize)
}

const getDimensionType = (dim: SemanticDimension): 'string' | 'number' | 'date' => {
  if (dim.type === 'number') return 'number'
  if (dim.type === 'time') return 'date'
  return 'string'
}

const extractFieldName = (expression?: string) => {
  const expr = (expression || '').trim()
  if (!expr) return '-'
  const inner = expr.match(/\((.*)\)/)?.[1] || expr
  const cleaned = inner.replace(/DISTINCT/ig, '').replace(/`/g, '').trim()
  if (!cleaned) return '-'
  const first = cleaned.split(',')[0]?.trim() || ''
  const parts = first.split('.')
  return parts[parts.length - 1] || '-'
}

const buildDimensionTooltip = (dim: SemanticDimension) => {
  const expression = dim.expression || `\`${dim.column}\``
  return [
    '基础信息',
    `显示名称：${dim.name}`,
    `字段名称：${dim.column}`,
    `表达式：${expression}`,
    `数据源表：${dim.table}`,
  ]
}

const buildMetricTooltip = (metric: SemanticConfigItem['metrics'][number]) => {
  const expression = metric.expression || ''
  return [
    '基础信息',
    `显示名称：${metric.name}`,
    `字段名称：${extractFieldName(expression)}`,
    `表达式：${expression || '-'}`,
    `数据源表：${metric.table}`,
  ]
}

const filteredDimensions = computed(() => {
  const keyword = globalKeyword.value.trim().toLowerCase()
  if (!keyword) return props.semanticConfig.dimensions
  return props.semanticConfig.dimensions.filter(dim => {
    const name = (dim.name || '').toLowerCase()
    const col = (dim.column || '').toLowerCase()
    const expr = (dim.expression || '').toLowerCase()
    return name.includes(keyword) || col.includes(keyword) || expr.includes(keyword)
  })
})

const filteredMetrics = computed(() => {
  const keyword = globalKeyword.value.trim().toLowerCase()
  if (!keyword) return props.semanticConfig.metrics
  return props.semanticConfig.metrics.filter(metric => {
    const name = (metric.name || '').toLowerCase()
    const col = (metric.column || '').toLowerCase()
    const expr = (metric.expression || '').toLowerCase()
    return name.includes(keyword) || col.includes(keyword) || expr.includes(keyword)
  })
})

const availableSemanticConfigs = computed(() => {
  const source = props.semanticConfigs?.length ? props.semanticConfigs : [props.semanticConfig]
  const enabled = source.filter(cfg => cfg.enabled)
  return enabled.length > 0 ? enabled : source
})

const currentDatasetValue = computed(() => {
  const currentId = props.selectedDatasetId || props.semanticConfig.id
  if (availableSemanticConfigs.value.some(cfg => cfg.id === currentId)) {
    return currentId
  }
  return availableSemanticConfigs.value[0]?.id || currentId
})

onUnmounted(() => {
  document.removeEventListener('mousemove', onSectionResize)
  document.removeEventListener('mouseup', stopSectionResize)
})
</script>

<template>
  <div class="h-full flex flex-col min-h-0">
    <div class="mb-1 sidebar-baseline-target flex items-center gap-0">
      <select
        class="flex-1 h-[28px] px-2 border border-[#DDE1E8] rounded-l rounded-r-none bg-white text-[13px] font-medium text-[#21252C] outline-none min-w-0 appearance-none"
        :value="currentDatasetValue"
        @change="emit('datasetChange', ($event.target as HTMLSelectElement).value)"
      >
        <option v-for="cfg in availableSemanticConfigs" :key="cfg.id" :value="cfg.id">
          {{ cfg.name }}
        </option>
      </select>
      <div class="relative w-[56px] shrink-0">
        <input
          v-model.trim="globalKeyword"
          type="text"
          placeholder="搜索字段"
          class="w-full h-[28px] px-1 text-[12px] border border-[#DDE1E8] border-l-0 rounded-r rounded-l-none bg-white text-[#21252C] outline-none focus:border-[#0FC6C2] placeholder:text-[#93949580]"
        />
      </div>
      <button
        type="button"
        class="ml-1 h-[28px] w-[28px] shrink-0 rounded border border-[#DDE1E8] bg-white text-[#5B5C60] transition-colors hover:border-[#0FC6C2] hover:text-[#0FC6C2] disabled:cursor-not-allowed disabled:opacity-50"
        :disabled="loading"
        title="刷新数据集"
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
        <div class="px-2.5 pt-1 pb-1 select-none shrink-0">
          <span class="text-[12px] font-semibold text-[#2e2f32]">维度</span>
        </div>
        <div class="flex-1 overflow-y-scroll always-scrollbar pr-1">
          <div
            v-for="dim in filteredDimensions"
            :key="`dim-${dim.name}`"
            class="group flex items-center gap-2 h-[28px] px-2 rounded cursor-grab transition-colors duration-100 hover:bg-[#f0f1f3] active:cursor-grabbing"
            draggable="true"
            @mouseenter="showTooltip($event, buildDimensionTooltip(dim))"
            @mouseleave="hideTooltip"
            @dragstart="emit('dragDimension', dim, $event)"
            @dragend="emit('dragEnd')"
          >
            <FieldTypeIcon :type="getDimensionType(dim)" class="text-[#165DFF] mr-1.5 shrink-0" />
            <span class="flex-1 text-xs text-[#21252C] overflow-hidden text-ellipsis whitespace-nowrap">
              {{ dim.name }}
            </span>
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
        <div class="px-2.5 pt-1 pb-1 select-none shrink-0">
          <span class="text-[12px] font-semibold text-[#2e2f32]">指标</span>
        </div>
        <div class="flex-1 overflow-y-scroll always-scrollbar pr-1">
          <div
            v-for="metric in filteredMetrics"
            :key="`metric-${metric.name}`"
            class="group flex items-center gap-2 h-[28px] px-2 rounded cursor-grab transition-colors duration-100 hover:bg-[#f0f1f3] active:cursor-grabbing"
            draggable="true"
            @mouseenter="showTooltip($event, buildMetricTooltip(metric))"
            @mouseleave="hideTooltip"
            @dragstart="emit('dragMetric', metric, $event)"
            @dragend="emit('dragEnd')"
          >
            <FieldTypeIcon type="number" class="text-[#0FC6C2] mr-1.5 shrink-0" />
            <span class="flex-1 text-xs text-[#21252C] overflow-hidden text-ellipsis whitespace-nowrap">
              {{ metric.name }}
            </span>
          </div>
          <div v-if="filteredMetrics.length === 0" class="px-2 py-1 text-[12px] text-[#9CA3AF]">暂无指标</div>
        </div>
      </div>
    </div>
  </div>
  <Teleport to="body">
    <div
      v-if="tooltipState.visible"
      class="fixed px-3 py-2 bg-[#373839] border border-white/15 rounded text-white text-[12px] z-[10000] whitespace-nowrap pointer-events-none -translate-y-1/2 shadow-[0_6px_16px_rgba(0,0,0,0.2)]"
      :style="{ left: tooltipState.x + 'px', top: tooltipState.y + 'px' }"
    >
      <div class="absolute top-1/2 -translate-y-1/2" style="left: -10px; width: 10px; height: 16px; background: rgba(255,255,255,0.15); clip-path: polygon(100% 0, 100% 100%, 0 50%);"></div>
      <div class="absolute top-1/2 -translate-y-1/2" style="left: -9px; width: 9px; height: 14px; background: #373839; clip-path: polygon(100% 0, 100% 100%, 0 50%);"></div>
      <div v-for="(line, idx) in tooltipState.lines" :key="idx" class="leading-relaxed">{{ line }}</div>
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
