<script setup lang="ts">
/**
 * 辅助查询侧边栏
 * 仅支持数据集模式，拖拽维度和指标
 */
import SemanticDatasetPanel from './SemanticDatasetPanel.vue'
import type { SemanticConfigItem, SemanticDimension, TableInfo } from '../../types'

interface DataSource {
  id: string
  group_name: string
}

interface Props {
  datasource: DataSource | null
  semanticConfig?: SemanticConfigItem | null
  semanticConfigs?: SemanticConfigItem[]
  selectedDatasetId?: string | null
  tables?: TableInfo[]
  loading?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  semanticConfig: null,
  semanticConfigs: () => [],
  selectedDatasetId: null,
  tables: () => [],
  loading: false,
})

const emit = defineEmits<{
  dragStart: [type: 'field', data: any, event: DragEvent]
  dragEnd: []
  addDimension: [data: any]
  addMetric: [data: any]
  datasetChange: [datasetId: string]
  refreshSemanticConfigs: []
}>()

const parseMetricDragMeta = (metric: SemanticConfigItem['metrics'][number]) => {
  const expr = (metric.expression || '').trim()
  const normalizeColumn = (raw: string) => {
    const cleaned = (raw || '').replace(/`/g, '').trim()
    if (!cleaned) return '*'
    const parts = cleaned.split('.')
    return parts[parts.length - 1] || '*'
  }

  const distinctMatch = expr.match(/^COUNT\s*\(\s*DISTINCT\s+([^)]+)\s*\)$/i)
  if (distinctMatch) {
    const col = normalizeColumn(distinctMatch[1] || '')
    return { func: 'COUNT_DISTINCT', column: col || '*' }
  }

  const aggMatch = expr.match(/^(COUNT|SUM|AVG|MAX|MIN)\s*\(\s*([^)]+)\s*\)$/i)
  if (aggMatch) {
    const fn = (aggMatch[1] || 'COUNT').toUpperCase()
    const raw = (aggMatch[2] || '').trim()
    const col = raw === '*' ? '*' : normalizeColumn(raw)
    return { func: fn, column: col || '*' }
  }

  // 非聚合表达式：按“原始值”处理，避免默认 COUNT
  return { func: 'RAW', column: normalizeColumn(expr) }
}

const parseFuncTag = (expression?: string) => {
  const expr = (expression || '').trim()
  if (!expr) return ''
  const match = expr.match(/^([A-Z_]+)\s*\(/i)
  if (!match?.[1]) return ''
  return match[1].toUpperCase()
}

const buildDimensionDragLabel = (dim: SemanticDimension) => {
  const fn = parseFuncTag(dim.expression)
  if (!fn) return dim.name
  return `${fn}(${dim.name})`
}

const handleSemanticDimensionDragStart = (dim: SemanticDimension, e: DragEvent) => {
  emit('dragStart', 'field', buildSemanticDimensionField(dim), e)
}

const handleSemanticMetricDragStart = (metric: SemanticConfigItem['metrics'][number], e: DragEvent) => {
  emit('dragStart', 'field', buildSemanticMetricField(metric), e)
}

const buildSemanticDimensionField = (dim: SemanticDimension) => ({
  table: dim.table,
  tableAlias: dim.table_alias || dim.table,
  tableLabel: dim.table_alias && dim.table_alias !== dim.table ? `${dim.table_alias}（${dim.table}）` : dim.table,
  column: dim.column,
  expression: dim.expression,
  displayName: dim.name,
  label: buildDimensionDragLabel(dim),
  type: dim.type
})

const buildSemanticMetricField = (metric: SemanticConfigItem['metrics'][number]) => {
  const meta = parseMetricDragMeta(metric)
  return {
    table: metric.table,
    tableAlias: metric.table_alias || metric.table,
    tableLabel: metric.table_alias && metric.table_alias !== metric.table ? `${metric.table_alias}（${metric.table}）` : metric.table,
    column: meta.column,
    displayName: metric.name,
    expression: metric.expression,
    label: metric.name,
    aggFunc: meta.func
  }
}

const handleSemanticFillQuestion = (payload: { fieldName: string; fieldType: 'dimension' | 'metric' }) => {
  if (payload.fieldType === 'dimension') {
    const dim = props.semanticConfig?.dimensions.find(item => item.name === payload.fieldName)
    if (dim) emit('addDimension', buildSemanticDimensionField(dim))
    return
  }
  const metric = props.semanticConfig?.metrics.find(item => item.name === payload.fieldName)
  if (metric) emit('addMetric', buildSemanticMetricField(metric))
}

// 拖拽结束
const handleDragEnd = () => {
  emit('dragEnd')
}
</script>

<template>
  <div class="flex flex-col h-full overflow-hidden bg-white">
    <!-- 表列表 -->
    <div class="flex-1 overflow-y-auto bg-white scrollbar-thin scrollbar-thumb-black/10">
      <div v-if="loading" class="flex flex-col items-center justify-center gap-2 p-6 text-gray-400 text-xs">
        <i class="fa-solid fa-spinner fa-spin text-xl opacity-50"></i>
        <span>加载中...</span>
      </div>

      <div v-else-if="!datasource" class="flex flex-col items-center justify-center gap-2 p-6 text-gray-400 text-xs">
        <i class="fa-solid fa-database text-xl opacity-50"></i>
        <span>请选择数据源</span>
      </div>

      <div v-else-if="!semanticConfig" class="flex flex-col items-center justify-center gap-2 p-6 text-gray-400 text-xs">
        <i class="fa-solid fa-layer-group text-xl opacity-50"></i>
        <span>请选择数据集</span>
      </div>

      <template v-else>
        <SemanticDatasetPanel
          :semantic-config="semanticConfig"
          :semantic-configs="semanticConfigs"
          :selected-dataset-id="selectedDatasetId"
          :tables="tables"
          :loading="loading"
          @drag-dimension="handleSemanticDimensionDragStart"
          @drag-metric="handleSemanticMetricDragStart"
          @drag-end="handleDragEnd"
          @dataset-change="(id: string) => emit('datasetChange', id)"
          @refresh="emit('refreshSemanticConfigs')"
          @fill-question="handleSemanticFillQuestion"
        />
      </template>
    </div>
  </div>
</template>
