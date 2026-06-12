<script setup lang="ts">
/**
 * SQL编辑器侧边栏
 * 用于SQL查询模式，点击箭头插入表名/字段名
 */
import { ref, computed, reactive } from 'vue'
import FieldTypeIcon from '../icons/FieldTypeIcon.vue'

// 悬浮提示状态
const tooltipState = reactive({
  visible: false,
  x: 0,
  y: 0,
  type: '' as 'table' | 'column',
  content: {
    name: '',
    label: '',
    columnType: ''
  }
})

// 显示 tooltip（右侧弹出）
const showTooltip = (e: MouseEvent, type: 'table' | 'column', data: any) => {
  const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
  tooltipState.x = rect.right + 8
  tooltipState.y = rect.top + rect.height / 2
  tooltipState.type = type
  tooltipState.content = data
  tooltipState.visible = true
}

// 隐藏 tooltip
const hideTooltip = () => {
  tooltipState.visible = false
}

interface ColumnInfo {
  name: string
  type: string
  mysql_type?: string
  description?: string
}

interface TableInfo {
  table_name: string
  description?: string
  columns?: ColumnInfo[]
}

interface DataSource {
  id: string
  ds_name: string
  ds_tables?: TableInfo[]
}

interface Props {
  datasource: DataSource | null
  loading?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  loading: false
})

const emit = defineEmits<{
  insertTable: [tableName: string]
  insertColumn: [tableName: string, columnName: string]
}>()

// 搜索和展开
const searchText = ref('')
const expandedTables = ref<Set<string>>(new Set())
const tableLabelMode = ref<'name' | 'description'>('name')

const getTableDisplayLabel = (table: TableInfo) => {
  if (tableLabelMode.value === 'description') {
    return table.description || table.table_name
  }
  return table.table_name
}

const getTableHoverLabel = (table: TableInfo) => {
  if (tableLabelMode.value === 'description') {
    return table.table_name
  }
  return table.description || table.table_name
}

const getColumnDisplayLabel = (col: ColumnInfo) => {
  if (tableLabelMode.value === 'description') {
    return col.description || col.name
  }
  return col.name
}

const getColumnHoverLabel = (col: ColumnInfo) => {
  if (tableLabelMode.value === 'description') {
    return col.name
  }
  return col.description || col.name
}

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

const handleInsertTableClick = (tableName: string) => {
  emit('insertTable', tableName)
}

const handleInsertColumnClick = (tableName: string, columnName: string) => {
  emit('insertColumn', tableName, columnName)
}

// 根据字段类型返回图标类型
const getFieldType = (col: ColumnInfo): 'string' | 'number' | 'date' => {
  const t = (col.mysql_type || col.type || '').toLowerCase()
  if (/int|float|double|decimal|numeric|number|bigint|tinyint|smallint|mediumint/.test(t)) return 'number'
  if (/date|time|timestamp|datetime|year/.test(t)) return 'date'
  return 'string'
}
</script>

<template>
  <div class="flex flex-col h-full overflow-hidden bg-white">
    <!-- 搜索框 -->
    <div class="sidebar-baseline-target">
      <div class="relative">
        <i class="fa-solid fa-magnifying-glass absolute left-[10px] top-1/2 -translate-y-1/2 text-[12px] text-[#57585a]"></i>
        <input
          v-model="searchText"
          type="text"
          placeholder="搜索表..."
          class="w-full h-[28px] pl-[28px] pr-[34px] bg-white border border-[#e3e5eb] rounded text-[12px] text-[#21252C] outline-none transition-all duration-150 focus:border-[#165DFF] placeholder:text-[#93949580]"
        />
        <button
          type="button"
          class="absolute right-[5px] top-1/2 -translate-y-1/2 w-5.5 h-5.5 border border-[#e3e5eb] rounded bg-white text-[#57585a] hover:text-[#165DFF] hover:border-[#B3DBD2] transition-colors"
          :title="tableLabelMode === 'name' ? '当前显示：表名/列名，点击切换为备注' : '当前显示：备注，点击切换为表名/列名'"
          @click="tableLabelMode = tableLabelMode === 'name' ? 'description' : 'name'"
        >
          <svg viewBox="0 0 16 16" class="w-[10px] h-[10px]" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M2 5.25H12.5L10.6 3.65" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
            <path d="M14 10.75H3.5L5.4 12.35" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </button>
      </div>
    </div>

    <!-- 表列表 -->
    <div class="flex-1 overflow-y-auto px-2 py-1 scrollbar-thin scrollbar-thumb-black/10">
      <div v-if="loading" class="flex flex-col items-center justify-center gap-2 p-6 text-gray-400 text-xs">
        <i class="fa-solid fa-spinner fa-spin text-xl opacity-50"></i>
        <span>加载中...</span>
      </div>

      <div v-else-if="!datasource" class="flex flex-col items-center justify-center gap-2 p-6 text-gray-400 text-xs">
        <i class="fa-solid fa-database text-xl opacity-50"></i>
        <span>请选择数据源</span>
      </div>

      <div v-else-if="filteredTables.length === 0" class="flex flex-col items-center justify-center gap-2 p-6 text-gray-400 text-xs">
        <span>未找到表</span>
      </div>

      <template v-else>
        <div
          v-for="table in filteredTables"
          :key="table.table_name"
          class="mb-0.5"
        >
          <!-- 表头 -->
          <div
            class="group flex items-center h-[28px] px-4 rounded transition-colors duration-100 cursor-pointer hover:bg-[#f0f1f3]"
            @click="toggleTable(table.table_name)"
            @mouseenter="showTooltip($event, 'table', { name: getTableHoverLabel(table), label: tableLabelMode === 'description' ? '表名' : '备注' })"
            @mouseleave="hideTooltip"
          >
            <i :class="['fa-solid fa-chevron-right text-[10px] text-[#57585a] mr-1.5 transition-transform duration-150', expandedTables.has(table.table_name) && 'rotate-90']"></i>
            <!-- <svg class="w-[14px] h-[14px] mr-1.5 shrink-0" viewBox="0 0 20 20" fill="none"><path fill="#fff" d="M1.596 5.695h16.863v11.632H1.596z" /><path fill="#165DFF" d="M18.125 2.083a.833.833 0 0 1 .834.834v14.166a.833.833 0 0 1-.834.834H1.875a.833.833 0 0 1-.833-.834V2.917a.833.833 0 0 1 .833-.834h16.25Zm0 .834H1.875v14.166h16.25V2.917Z" /><path fill="#165DFF" d="M1.875 2.083h16.25a.833.833 0 0 1 .834.834V6.25H1.042V2.917a.833.833 0 0 1 .833-.834Z" /><path fill="#94BFFF" d="M1.875 9.583h16.25v.834H1.875v-.834Zm0 3.75h16.25v.834H1.875v-.834Z" /><path fill="#94BFFF" d="M6.667 6.25H7.5v10.833h-.833V6.25Zm5.833 0h.834v10.833H12.5V6.25Z" /></svg> -->
            <span class="flex-1 text-[12px] font-semibold text-[#2E2F32] overflow-hidden text-ellipsis whitespace-nowrap leading-[28px]">
              {{ getTableDisplayLabel(table) }}
            </span>
            <button
              class="ml-1 flex h-5 w-5 items-center justify-center rounded text-[#165DFF] opacity-0 transition-all duration-150 hover:bg-[#E8F3FF] hover:text-[#0E42D2] group-hover:opacity-100"
              title="插入表名到 SQL"
              @click.stop="handleInsertTableClick(table.table_name)"
            >
              <i class="fa-solid fa-arrow-right text-[10px]"></i>
            </button>
          </div>

          <!-- 字段列表 -->
          <div v-if="expandedTables.has(table.table_name)" class="ml-4">
            <div
              v-for="col in table.columns"
              :key="col.name"
              class="group flex items-center h-[28px] px-4 rounded transition-colors duration-100 cursor-pointer hover:bg-[#f0f1f3]"
              @mouseenter="showTooltip($event, 'column', { name: getColumnHoverLabel(col), label: tableLabelMode === 'description' ? '列名' : '备注', columnType: col.mysql_type || col.type })"
              @mouseleave="hideTooltip"
            >
              <FieldTypeIcon :type="getFieldType(col)" class="text-[#165DFF] mr-1.5" />
              <span class="flex-1 text-[12px] text-[#21252C] overflow-hidden text-ellipsis whitespace-nowrap">{{ getColumnDisplayLabel(col) }}</span>
              <button
                class="ml-1 flex h-5 w-5 items-center justify-center rounded text-[#165DFF] opacity-0 transition-all duration-150 hover:bg-[#E8F3FF] hover:text-[#0E42D2] group-hover:opacity-100"
                title="插入字段到 SQL"
                @click.stop="handleInsertColumnClick(table.table_name, col.name)"
              >
                <i class="fa-solid fa-arrow-right text-[10px]"></i>
              </button>
            </div>
          </div>
        </div>
      </template>
    </div>

    <!-- Tooltip（右侧弹出） -->
    <Teleport to="body">
      <div
        v-if="tooltipState.visible"
        class="fixed px-3 py-2 bg-[#373839] rounded text-white text-[12px] z-[10000] whitespace-nowrap pointer-events-none -translate-y-1/2 shadow-[0_4px_10px_rgba(0,0,0,0.15)] overflow-visible"
        :style="{ left: tooltipState.x + 'px', top: tooltipState.y + 'px' }"
      >
        <!-- 左侧三角箭头 -->
        <div class="absolute top-1/2 -translate-y-1/2" style="left: -6px; width: 0; height: 0; border: 6px solid transparent; border-right-color: #373839;"></div>
        <div v-if="tooltipState.type === 'table'" class="leading-relaxed">
          <span class="text-white/70">{{ tooltipState.content.label || '表名' }}：</span>
          <span class="font-mono">{{ tooltipState.content.name }}</span>
        </div>
        <template v-else>
          <div class="leading-relaxed">
            <span class="text-white/70">{{ tooltipState.content.label || '列名' }}：</span>
            <span class="font-mono">{{ tooltipState.content.name }}</span>
          </div>
          <div class="leading-relaxed">
            <span class="text-white/70">类型：</span>
            <span class="font-mono">{{ tooltipState.content.columnType }}</span>
          </div>
        </template>
      </div>
    </Teleport>
  </div>
</template>
