<script setup lang="ts">
/**
 * SQL编辑器侧边栏
 * 用于SQL查询模式，点击箭头插入表名/字段名
 */
import { ref, computed, reactive, watch, onUnmounted } from 'vue'
import type { DatasourceColumnDictInfo } from '../../types'
import { buildFieldDictSection, createTooltipSection, type FieldTooltipSection } from '../../utils/fieldDict'
import { useColumnDictCache } from '../../composables/useColumnDictCache'
import FieldTypeIcon from '../icons/FieldTypeIcon.vue'

// 悬浮提示状态
const tooltipState = reactive({
  visible: false,
  x: 0,
  y: 0,
  sections: [] as FieldTooltipSection[]
})
let tooltipHideTimer: ReturnType<typeof window.setTimeout> | null = null

const clearTooltipHideTimer = () => {
  if (!tooltipHideTimer) return
  window.clearTimeout(tooltipHideTimer)
  tooltipHideTimer = null
}

// 显示 tooltip（右侧弹出）
const showTooltip = (e: MouseEvent, sections: FieldTooltipSection[]) => {
  clearTooltipHideTimer()
  const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
  tooltipState.x = rect.right + 8
  tooltipState.y = rect.top + rect.height / 2
  tooltipState.sections = sections
  tooltipState.visible = true
}

// 隐藏 tooltip
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

onUnmounted(() => {
  clearTooltipHideTimer()
})

interface ColumnInfo {
  name: string
  type: string
  mysql_type?: string
  hive_type?: string
  description?: string
  column_dict?: DatasourceColumnDictInfo
}

interface TableInfo {
  table_name: string
  description?: string
  business_desc?: string
  columns?: ColumnInfo[]
  groups?: Array<{
    table_group_name: string
  }>
}

interface DataSource {
  id: string
  group_name: string
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
const collapsedGroups = ref<Set<string>>(new Set())
const tableLabelMode = ref<'name' | 'description'>('name')
const { getColumnDict, loadTableColumnDicts, resetColumnDictCache } = useColumnDictCache()

const getCachedColumnDict = (tableName: string, columnName: string) => {
  return getColumnDict(props.datasource?.id || '', tableName, columnName)
}

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

const buildTableTooltipSections = (table: TableInfo) => {
  return [
    createTooltipSection('表信息', [
      `${tableLabelMode.value === 'description' ? '表名' : '备注'}：${getTableHoverLabel(table)}`,
      table.business_desc ? `表业务说明：${table.business_desc}` : '',
    ]),
  ].filter(Boolean) as FieldTooltipSection[]
}

const buildColumnTooltipSections = (table: TableInfo, col: ColumnInfo) => {
  const cachedDict = getCachedColumnDict(table.table_name, col.name)
  return [
    createTooltipSection('表信息', [
      `数据源表：${table.table_name}`,
      table.description ? `数据表备注：${table.description}` : '',
      table.business_desc ? `表业务说明：${table.business_desc}` : '',
    ]),
    createTooltipSection('字段信息', [
      `${tableLabelMode.value === 'description' ? '列名' : '备注'}：${getColumnHoverLabel(col)}`,
      `类型：${col.mysql_type || col.hive_type || col.type || '-'}`,
    ]),
    buildFieldDictSection(cachedDict || col.column_dict),
  ].filter(Boolean) as FieldTooltipSection[]
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
    t.description?.toLowerCase().includes(keyword) ||
    t.business_desc?.toLowerCase().includes(keyword)
  )
})

const hasTableGroups = computed(() =>
  (props.datasource?.ds_tables || []).some(table => (table.groups || []).some(group => group.table_group_name?.trim()))
)

const groupedTables = computed(() => {
  const groups = new Map<string, TableInfo[]>()
  filteredTables.value.forEach(table => {
    const tableGroups = (table.groups || [])
      .map(group => group.table_group_name?.trim())
      .filter(Boolean) as string[]
    const targetGroups = tableGroups.length > 0 ? tableGroups : ['未分组']
    targetGroups.forEach(groupName => {
      if (!groups.has(groupName)) groups.set(groupName, [])
      groups.get(groupName)?.push(table)
    })
  })
  return Array.from(groups.entries())
    .sort(([a], [b]) => {
      if (a === '未分组') return 1
      if (b === '未分组') return -1
      return a.localeCompare(b)
    })
    .map(([name, tables]) => ({ name, tables }))
})

// 切换表展开
const toggleTable = (tableName: string, physicalTableName = tableName) => {
  const newSet = new Set(expandedTables.value)
  if (newSet.has(tableName)) {
    newSet.delete(tableName)
  } else {
    newSet.add(tableName)
    void loadTableColumnDicts(props.datasource?.id || '', physicalTableName)
  }
  expandedTables.value = newSet
}

const isGroupExpanded = (groupName: string) => !collapsedGroups.value.has(groupName)

const toggleGroup = (groupName: string) => {
  const nextCollapsedGroups = new Set(collapsedGroups.value)
  if (nextCollapsedGroups.has(groupName)) {
    nextCollapsedGroups.delete(groupName)
  } else {
    nextCollapsedGroups.add(groupName)
  }
  collapsedGroups.value = nextCollapsedGroups
}

watch(
  () => props.datasource?.id,
  () => {
    expandedTables.value = new Set()
    collapsedGroups.value = new Set()
    resetColumnDictCache()
  }
)

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
          placeholder="支持中英文搜索表名/字段"
          class="w-full h-[28px] pl-[28px] pr-[34px] bg-white border border-[#e3e5eb] rounded text-[12px] text-[#21252C] outline-none transition-all duration-150 focus:border-[#165DFF] placeholder:text-[#93949580]"
        />
        <button
          type="button"
          class="absolute right-[5px] top-1/2 -translate-y-1/2 min-w-[30px] h-5.5 px-1 border border-[#e3e5eb] rounded bg-white text-[10px] leading-none text-[#57585a] hover:text-[#165DFF] hover:border-[#B3DBD2] transition-colors"
          :title="tableLabelMode === 'name' ? '当前显示：英文表名/字段名，点击切换为中文备注' : '当前显示：中文备注，点击切换为英文表名/字段名'"
          @click="tableLabelMode = tableLabelMode === 'name' ? 'description' : 'name'"
        >
          <span :class="tableLabelMode === 'description' ? 'text-[#165DFF] font-semibold' : 'text-[#57585a]'">中</span>
          <span class="text-[#B8BDC7]">/</span>
          <span :class="tableLabelMode === 'name' ? 'text-[#165DFF] font-semibold' : 'text-[#57585a]'">英</span>
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

      <template v-else-if="!hasTableGroups">
        <div
          v-for="table in filteredTables"
          :key="table.table_name"
          class="mb-0.5"
        >
          <!-- 表头 -->
          <div
            class="group flex items-center h-[28px] px-4 rounded transition-colors duration-100 cursor-pointer hover:bg-[#f0f1f3]"
            @click="toggleTable(table.table_name)"
            @mouseenter="showTooltip($event, buildTableTooltipSections(table))"
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
              @mouseenter="showTooltip($event, buildColumnTooltipSections(table, col))"
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
      <template v-else>
        <div v-for="group in groupedTables" :key="group.name" class="mb-2">
          <div
            class="flex h-[28px] cursor-pointer items-center gap-1.5 rounded px-4 text-[12px] font-semibold text-[#57585a] transition-colors hover:bg-[#f0f1f3]"
            @click="toggleGroup(group.name)"
          >
            <i :class="['fa-solid fa-chevron-right text-[10px] text-[#57585a] transition-transform duration-150', isGroupExpanded(group.name) && 'rotate-90']"></i>
            <i class="fa-solid fa-folder text-[11px] text-amber-600"></i>
            <span class="min-w-0 flex-1 truncate">{{ group.name }}</span>
            <span class="text-[10px] font-normal text-[#939495]">{{ group.tables.length }}</span>
          </div>
          <template v-if="isGroupExpanded(group.name)">
            <div
              v-for="table in group.tables"
              :key="`${group.name}-${table.table_name}`"
              class="mb-0.5 ml-4"
            >
              <!-- 表头 -->
              <div
                class="group flex items-center h-[28px] px-4 rounded transition-colors duration-100 cursor-pointer hover:bg-[#f0f1f3]"
                @click="toggleTable(`${group.name}:${table.table_name}`, table.table_name)"
                @mouseenter="showTooltip($event, buildTableTooltipSections(table))"
                @mouseleave="hideTooltip"
              >
                <i :class="['fa-solid fa-chevron-right text-[10px] text-[#57585a] mr-1.5 transition-transform duration-150', expandedTables.has(`${group.name}:${table.table_name}`) && 'rotate-90']"></i>
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
              <div v-if="expandedTables.has(`${group.name}:${table.table_name}`)" class="ml-4">
                <div
                  v-for="col in table.columns"
                  :key="col.name"
                  class="group flex items-center h-[28px] px-4 rounded transition-colors duration-100 cursor-pointer hover:bg-[#f0f1f3]"
                  @mouseenter="showTooltip($event, buildColumnTooltipSections(table, col))"
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
      </template>
    </div>

    <!-- 表计数（底部状态栏） -->
    <div v-if="datasource && !loading" class="py-1 text-center text-[11px] text-[#939495] border-t border-[#f0f1f3]">
      {{ searchText.trim() ? `匹配 ${filteredTables.length} 张，共 ${datasource?.ds_tables?.length ?? 0} 张表` : `共 ${datasource?.ds_tables?.length ?? 0} 张表` }}
    </div>

    <!-- Tooltip（右侧弹出） -->
    <Teleport to="body">
      <div
        v-if="tooltipState.visible"
        class="fixed max-w-[420px] px-3 py-2 bg-[#373839] rounded text-white text-[12px] z-[10000] whitespace-pre-wrap break-words -translate-y-1/2 shadow-[0_4px_10px_rgba(0,0,0,0.15)] overflow-visible select-text cursor-text"
        :style="{ left: tooltipState.x + 'px', top: tooltipState.y + 'px' }"
        @mouseenter="keepTooltipVisible"
        @mouseleave="hideTooltipNow"
      >
        <!-- 左侧三角箭头 -->
        <div class="absolute top-1/2 -translate-y-1/2" style="left: -6px; width: 0; height: 0; border: 6px solid transparent; border-right-color: #373839;"></div>
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
  </div>
</template>
