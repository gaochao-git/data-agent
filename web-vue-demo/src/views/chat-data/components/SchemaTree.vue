<script setup lang="ts">
/**
 * 表结构树组件
 * 显示数据源的表和字段结构
 */
import { ref, computed, watch } from 'vue'
import type { DataSource } from '../types'

interface Props {
  datasource: DataSource | null
  loading?: boolean
  hideHeader?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  loading: false,
  hideHeader: false
})

const emit = defineEmits<{
  selectTable: [tableName: string]
  selectColumn: [tableName: string, columnName: string]
}>()

// 搜索
const searchQuery = ref('')

// 展开的表
const expandedTables = ref<Set<string>>(new Set())

// 过滤后的表
const filteredTables = computed(() => {
  if (!props.datasource?.ds_tables) return []

  const query = searchQuery.value.toLowerCase().trim()
  if (!query) return props.datasource.ds_tables

  return props.datasource.ds_tables.filter((table) => {
    // 匹配表名
    if (table.table_name.toLowerCase().includes(query)) return true
    // 匹配表描述
    if (table.description?.toLowerCase().includes(query)) return true
    // 匹配列名
    if (table.columns?.some((col) => col.name.toLowerCase().includes(query))) return true
    return false
  })
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

// 获取字段图标
const getColumnIcon = (type: string): string => {
  const t = type.toLowerCase()
  if (t.includes('int') || t.includes('decimal') || t.includes('float') || t.includes('double') || t.includes('numeric')) {
    return 'fa-hashtag'
  }
  if (t.includes('date') || t.includes('time')) {
    return 'fa-calendar'
  }
  if (t.includes('bool')) {
    return 'fa-toggle-on'
  }
  return 'fa-font'
}

// 获取字段类型颜色
const getTypeColor = (type: string): string => {
  const t = type.toLowerCase()
  if (t.includes('int') || t.includes('decimal') || t.includes('float') || t.includes('double')) {
    return '#1677FF'
  }
  if (t.includes('date') || t.includes('time')) {
    return '#52C41A'
  }
  if (t.includes('bool')) {
    return '#FAAD14'
  }
  return '#6b7280'
}

// 复制表名
const copyTableName = (tableName: string, e: Event) => {
  e.stopPropagation()
  navigator.clipboard.writeText(tableName)
}

// 复制列名
const copyColumnName = (columnName: string, e: Event) => {
  e.stopPropagation()
  navigator.clipboard.writeText(columnName)
}
</script>

<template>
  <div class="flex flex-col h-full overflow-hidden">
    <!-- 头部 -->
    <div v-if="!hideHeader" class="panel-header">
      <div class="panel-title">
        <i class="fa-solid fa-database"></i>
        数据表
      </div>
    </div>

    <!-- 搜索框 -->
    <div class="relative p-2 border-b border-black/[0.06]">
      <i class="fa-solid fa-magnifying-glass absolute left-4 top-1/2 -translate-y-1/2 text-[11px] text-gray-400"></i>
      <input
        v-model="searchQuery"
        type="text"
        placeholder="搜索表..."
        class="w-full h-[30px] pl-7 pr-2.5 border border-black/[0.08] rounded-md text-xs outline-none transition-all duration-200 focus:border-blue-500/40 focus:shadow-[0_0_0_2px_rgba(22,119,255,0.1)]"
      />
    </div>

    <!-- 加载状态 -->
    <div v-if="loading" class="flex flex-col items-center justify-center gap-2 p-6 text-gray-400 text-xs">
      <i class="fa-solid fa-spinner fa-spin text-xl opacity-50"></i>
      <span>加载中...</span>
    </div>

    <!-- 无数据 -->
    <div v-else-if="!datasource" class="flex flex-col items-center justify-center gap-2 p-6 text-gray-400 text-xs">
      <i class="fa-solid fa-database text-xl opacity-50"></i>
      <span>请选择数据源</span>
    </div>

    <!-- 表列表 -->
    <div v-else class="flex-1 overflow-y-auto p-1">
      <div v-if="filteredTables.length === 0" class="flex flex-col items-center justify-center gap-2 p-6 text-gray-400 text-xs">
        <span>未找到匹配的表</span>
      </div>

      <div
        v-for="table in filteredTables"
        :key="table.table_name"
        class="mb-1.5"
      >
        <!-- 表头 -->
        <div
          class="group flex items-center gap-2 py-2 px-2.5 rounded-lg cursor-pointer transition-all duration-200 bg-gradient-to-br from-blue-50/50 to-blue-100/30 border border-blue-500/10 hover:from-blue-100/80 hover:to-blue-200/50 hover:border-blue-500/20"
          @click="toggleTable(table.table_name)"
        >
          <div class="drag-handle">
            <i class="fa-solid fa-grip-vertical"></i>
          </div>
          <i class="fa-solid fa-table w-5 h-5 flex items-center justify-center text-[11px] text-[#1f82d9] bg-[#1f82d9]/10 rounded shrink-0"></i>
          <span class="text-[13px] font-medium text-gray-800 overflow-hidden text-ellipsis whitespace-nowrap" :title="table.table_name">
            {{ table.description || table.table_name }}
          </span>
          <button class="expand-btn">
            <i :class="['fa-solid', expandedTables.has(table.table_name) ? 'fa-chevron-down' : 'fa-chevron-right']"></i>
          </button>
        </div>

        <!-- 字段列表 -->
        <div v-if="expandedTables.has(table.table_name)" class="pl-5">
          <div
            v-for="col in table.columns"
            :key="col.name"
            class="flex items-center gap-1.5 py-1 px-2 rounded cursor-pointer transition-colors duration-150 hover:bg-black/[0.03]"
            @click="emit('selectColumn', table.table_name, col.name)"
          >
            <div class="drag-handle drag-handle--small">
              <i class="fa-solid fa-grip-vertical"></i>
            </div>
            <span class="text-xs text-gray-600">{{ col.description || col.name }}</span>
            <span class="text-[10px] text-gray-400">{{ col.mysql_type || col.type }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

