/**
 * 数据表面板共享逻辑
 * 智能问数 QueryBuilder 与 SQL 编辑器 SqlEditor 的左侧数据表面板
 * 交互和样式各自不同，但搜索过滤、展开切换、悬浮标题逻辑一致，在此统一维护
 */
import { computed, ref, type ComputedRef, type Ref } from 'vue'

export interface TableSchemaInfo {
  table_name: string
  description?: string
  business_desc?: string
}

export const useTableSchemaPanel = <T extends TableSchemaInfo>(tablesSource: Ref<T[]> | ComputedRef<T[]>) => {
  const searchText = ref('')
  const expandedTables = ref<Set<string>>(new Set())

  const filteredTables = computed(() => {
    const tables = tablesSource.value
    if (!searchText.value.trim()) return tables
    const keyword = searchText.value.toLowerCase()
    return tables.filter(t =>
      t.table_name.toLowerCase().includes(keyword) ||
      t.description?.toLowerCase().includes(keyword) ||
      t.business_desc?.toLowerCase().includes(keyword)
    )
  })

  const buildTableTitle = (table: TableSchemaInfo) => [
    `表名：${table.table_name}`,
    `备注：${table.description || '-'}`,
    table.business_desc ? `表业务说明：${table.business_desc}` : '',
  ].filter(Boolean).join('\n')

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

  return {
    searchText,
    expandedTables,
    filteredTables,
    buildTableTitle,
    toggleTable,
  }
}
