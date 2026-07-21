<script setup lang="ts">
/**
 * 编辑表达式弹窗
 * 支持为指标/维度字段编写 SQL 表达式，并提供聚合函数、常用函数快捷插入
 */
import { computed, ref, watch } from 'vue'
import {
  SQL_FUNCTION_GROUPS,
  buildMetricExpression,
  buildSqlFunctionExpression,
  getSqlFunctionOptions,
  quoteIdentifier,
} from '../../utils'

interface ExpressionEditorRow {
  table: string
  column: string
  sourceColumn?: string
  isMetric?: boolean
  isDimension?: boolean
  expression?: string
}

const props = defineProps<{
  row: ExpressionEditorRow | null
  datasourceType?: string
}>()

const emit = defineEmits<{
  save: [expression: string]
}>()

const visible = defineModel<boolean>('visible', { default: false })

const editingExpressionText = ref('')

const AGG_FUNCTION_OPTIONS = [
  { value: 'COUNT', label: 'COUNT', desc: '计数' },
  { value: 'COUNT_DISTINCT', label: 'COUNT DISTINCT', desc: '去重计数' },
  { value: 'SUM', label: 'SUM', desc: '求和' },
  { value: 'AVG', label: 'AVG', desc: '平均值' },
  { value: 'MAX', label: 'MAX', desc: '最大值' },
  { value: 'MIN', label: 'MIN', desc: '最小值' },
]

const dimensionFunctionOptions = computed(() => (
  getSqlFunctionOptions(props.datasourceType, true).filter(item => item.usesExpression !== false)
))
const visibleDimensionFunctionGroups = computed(() => {
  return SQL_FUNCTION_GROUPS
    .map(group => ({
      ...group,
      items: dimensionFunctionOptions.value.filter(item => item.group === group.key),
    }))
    .filter(group => group.items.length > 0)
})

const buildDimensionExpression = (func: string, column: string) => {
  const quoted = quoteIdentifier(column)
  return buildSqlFunctionExpression(func, quoted, props.datasourceType)
}

const insertExpressionFromEditor = (expression: string) => {
  editingExpressionText.value = expression
}

const saveExpressionEditor = () => {
  emit('save', editingExpressionText.value.trim())
  visible.value = false
}

watch(visible, (isVisible) => {
  if (!isVisible) return
  const row = props.row
  editingExpressionText.value = row?.expression || quoteIdentifier(row?.sourceColumn || row?.column || '')
})
</script>

<template>
  <el-dialog v-model="visible" width="760px" title="编辑表达式">
    <div class="space-y-3">
      <div v-if="row" class="rounded-md bg-gray-50 px-3 py-2 text-xs text-gray-500">
        {{ row.table }}.{{ row.sourceColumn || row.column }}
      </div>
      <textarea
        v-model="editingExpressionText"
        rows="8"
        class="w-full rounded-md border border-gray-200 px-3 py-2 font-mono text-[13px] leading-relaxed text-gray-700 outline-none focus:border-blue-500"
        placeholder="请输入字段表达式"
      ></textarea>
      <div
        v-if="row"
        :class="[
          'grid gap-3',
          row.isMetric && row.isDimension ? 'grid-cols-2' : 'grid-cols-1'
        ]"
      >
        <div v-if="row.isMetric" class="rounded-md border border-gray-200 p-3">
          <div class="mb-1.5 text-xs font-medium text-gray-600">聚合函数</div>
          <div class="flex flex-wrap gap-1.5">
            <button
              v-for="fn in AGG_FUNCTION_OPTIONS"
              :key="`expr-agg-${fn.value}`"
              class="rounded border border-gray-200 bg-white px-2 py-1 text-[12px] text-gray-600 hover:border-blue-200 hover:text-blue-600"
              @click="insertExpressionFromEditor(buildMetricExpression(fn.value, row.sourceColumn || row.column))"
            >
              {{ fn.label }}
            </button>
          </div>
        </div>
        <div v-if="row.isDimension" class="rounded-md border border-gray-200 p-3">
          <div class="mb-1.5 text-xs font-medium text-gray-600">常用函数</div>
          <div class="max-h-[220px] overflow-auto pr-1">
            <div
              v-for="group in visibleDimensionFunctionGroups"
              :key="`expr-dim-group-${group.key}`"
              class="mb-2 last:mb-0"
            >
              <div class="mb-1 text-[11px] font-medium text-gray-400">{{ group.label }}</div>
              <div class="flex flex-wrap gap-1.5">
                <button
                  v-for="fn in group.items"
                  :key="`expr-dim-${fn.value}`"
                  class="rounded border border-gray-200 bg-white px-2 py-1 text-[12px] text-gray-600 hover:border-blue-200 hover:text-blue-600"
                  :title="fn.desc"
                  @click="insertExpressionFromEditor(buildDimensionExpression(fn.value, row.sourceColumn || row.column))"
                >
                  {{ fn.label }}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
    <template #footer>
      <button
        class="px-3 py-1.5 border border-gray-200 rounded bg-white text-xs text-gray-600 hover:bg-gray-50"
        @click="visible = false"
      >
        取消
      </button>
      <button
        class="ml-2 px-3 py-1.5 border-none rounded bg-[#1677FF] text-xs text-white hover:bg-[#0958D9]"
        @click="saveExpressionEditor"
      >
        保存
      </button>
    </template>
  </el-dialog>
</template>
