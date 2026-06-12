<script setup lang="ts">
/**
 * 可调整列宽的数据表格组件
 */
import { ref, onMounted, onUnmounted, computed, watch } from 'vue'

interface Column {
  key: string
  title: string
  width?: number
}

interface Props {
  columns: Column[]
  data: Record<string, unknown>[]
  maxHeight?: string
  showIndex?: boolean
  autoWrap?: boolean
  rowIndexStart?: number
}

const props = withDefaults(defineProps<Props>(), {
  maxHeight: '100%',
  showIndex: false,
  autoWrap: false,
  rowIndexStart: 1
})

// 列宽状态
const columnWidths = ref<Record<string, number>>({})

// 拖拽状态
const isDragging = ref(false)
const dragCol = ref<string | null>(null)
const dragStartX = ref(0)
const dragStartWidth = ref(0)

const INDEX_COLUMN_KEY = '__row_index__'

const displayColumns = computed<Column[]>(() => {
  if (!props.showIndex) return props.columns
  return [
    { key: INDEX_COLUMN_KEY, title: '序号', width: 72 },
    ...props.columns
  ]
})

// 初始化列宽
const initColumnWidths = () => {
  displayColumns.value.forEach((col) => {
    if (!columnWidths.value[col.key]) {
      columnWidths.value[col.key] = col.width || 150
    }
  })
}

onMounted(() => {
  initColumnWidths()
})

watch(displayColumns, () => {
  initColumnWidths()
}, { deep: true })

watch(() => props.columns, () => {
  props.columns.forEach((col) => {
    columnWidths.value[col.key] = col.width || 150
  })
})

// 获取列宽
const getColumnWidth = (key: string): number => {
  return columnWidths.value[key] || 150
}

// 开始拖拽
const startResize = (col: string, event: MouseEvent) => {
  event.preventDefault()
  isDragging.value = true
  dragCol.value = col
  dragStartX.value = event.clientX
  dragStartWidth.value = columnWidths.value[col] || 150

  document.addEventListener('mousemove', onMouseMove)
  document.addEventListener('mouseup', onMouseUp)
}

const onMouseMove = (e: MouseEvent) => {
  if (!isDragging.value || !dragCol.value) return
  const diff = e.clientX - dragStartX.value
  columnWidths.value[dragCol.value] = Math.max(60, dragStartWidth.value + diff)
}

const onMouseUp = () => {
  isDragging.value = false
  dragCol.value = null
  document.removeEventListener('mousemove', onMouseMove)
  document.removeEventListener('mouseup', onMouseUp)
}

onUnmounted(() => {
  document.removeEventListener('mousemove', onMouseMove)
  document.removeEventListener('mouseup', onMouseUp)
})

// 格式化单元格值
const formatCellValue = (value: unknown): string => {
  if (value === null || value === undefined) return '-'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

const getCellRawValue = (row: Record<string, unknown>, rowIndex: number, col: Column): unknown => {
  if (col.key === INDEX_COLUMN_KEY) return (props.rowIndexStart || 1) + rowIndex
  return row[col.key]
}

// 判断是否为数值
const isNumeric = (value: unknown): boolean => {
  return typeof value === 'number'
}

const isIndexColumn = (col: Column) => col.key === INDEX_COLUMN_KEY

// 业务第一列（序号开启后是第 2 列）
const isFirstBusinessColumn = (colIndex: number): boolean => {
  return colIndex === (props.showIndex ? 1 : 0)
}

const isRatioColumn = (columnKey: string): boolean => {
  const name = (columnKey || '').toLowerCase()
  return name === 'ratio_value' || name === '比率'
}

const formatNumberValue = (value: number): string => {
  if (Number.isInteger(value)) return value.toLocaleString('zh-CN')
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2
  })
}

const formatDisplayValue = (value: unknown, col: Column): string => {
  if (value === null || value === undefined) return '-'
  if (typeof value === 'number' && isRatioColumn(col.key)) {
    return `${(value * 100).toFixed(2)}%`
  }
  if (typeof value === 'number') {
    return formatNumberValue(value)
  }
  return formatCellValue(value)
}

// 复制单元格内容
const copyCellValue = (value: unknown, col: Column) => {
  const text = formatDisplayValue(value, col)
  navigator.clipboard.writeText(text)
}
</script>

<template>
  <div class="flex flex-col overflow-auto text-[13px] rounded-xl bg-slate-50" :style="{ maxHeight }">
    <!-- 表头 -->
    <div class="flex w-fit min-w-full sticky top-0 z-[1] bg-slate-50 border-b-2 border-[#1f82d9]/10">
      <div
        v-for="(col, index) in displayColumns"
        :key="col.key"
        class="relative shrink-0 px-4 py-3 font-semibold text-xs text-slate-600 uppercase tracking-wide overflow-hidden text-ellipsis whitespace-nowrap border-r border-black/[0.04] last:border-r-0"
        :style="{ width: `${getColumnWidth(col.key)}px`, minWidth: `${getColumnWidth(col.key)}px` }"
      >
        <span class="block overflow-hidden text-ellipsis pr-2" :title="col.title">{{ col.title }}</span>
        <div
          class="absolute right-0 top-0 bottom-0 w-1.5 cursor-col-resize bg-transparent transition-colors duration-150 hover:bg-gradient-to-br hover:from-[#1f82d9] hover:to-[#135589]"
          @mousedown="startResize(col.key, $event)"
        />
      </div>
    </div>

    <!-- 表体 -->
    <div class="flex-1">
      <div
        v-for="(row, rowIndex) in data"
        :key="rowIndex"
        class="flex w-fit min-w-full transition-all duration-200 even:bg-white odd:bg-slate-50 hover:bg-blue-50"
      >
        <div
          v-for="(col, colIndex) in displayColumns"
          :key="col.key"
          :class="[
            'shrink-0 px-4 py-3 text-gray-700 overflow-hidden text-ellipsis border-b border-black/[0.04] border-r border-r-black/[0.02] last:border-r-0 cursor-default transition-all duration-150',
            autoWrap ? 'whitespace-normal break-all' : 'whitespace-nowrap',
            isNumeric(getCellRawValue(row, rowIndex, col)) && !isFirstBusinessColumn(colIndex) && !isIndexColumn(col) && 'text-right tabular-nums font-mono text-[#1f82d9]',
            isFirstBusinessColumn(colIndex) && 'font-semibold text-gray-900',
            isIndexColumn(col) && 'font-semibold text-gray-900',
            (getCellRawValue(row, rowIndex, col) === null || getCellRawValue(row, rowIndex, col) === undefined) && 'text-gray-400 italic'
          ]"
          :style="{ width: `${getColumnWidth(col.key)}px`, minWidth: `${getColumnWidth(col.key)}px` }"
          :title="formatDisplayValue(getCellRawValue(row, rowIndex, col), col)"
          @dblclick="copyCellValue(getCellRawValue(row, rowIndex, col), col)"
        >
          {{ formatDisplayValue(getCellRawValue(row, rowIndex, col), col) }}
        </div>
      </div>
    </div>

    <!-- 空状态 -->
    <div v-if="data.length === 0" class="flex flex-col items-center justify-center gap-3 p-12 text-gray-400 text-sm bg-gradient-to-br from-slate-50 to-slate-100">
      <i class="fa-solid fa-inbox text-[32px] opacity-50"></i>
      <span>暂无数据</span>
    </div>
  </div>
</template>
