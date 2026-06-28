import { ref } from 'vue'
import { getDatasourceTableColumnDicts } from '../api'
import type { DatasourceColumnDictInfo } from '../types'

type TableDictCache = Record<string, Record<string, DatasourceColumnDictInfo>>

export const useColumnDictCache = () => {
  const tableColumnDicts = ref<TableDictCache>({})
  const tableColumnDictLoading = ref<Set<string>>(new Set())

  const buildCacheKey = (datasourceId: string, tableName: string) => `${datasourceId}::${tableName}`

  const resetColumnDictCache = () => {
    tableColumnDicts.value = {}
    tableColumnDictLoading.value = new Set()
  }

  const getColumnDict = (datasourceId: string, tableName: string, columnName: string) => {
    const normalizedDatasourceId = String(datasourceId || '').trim()
    const normalizedTableName = String(tableName || '').trim()
    const normalizedColumnName = String(columnName || '').trim()
    if (!normalizedDatasourceId || !normalizedTableName || !normalizedColumnName) return undefined
    return tableColumnDicts.value[buildCacheKey(normalizedDatasourceId, normalizedTableName)]?.[normalizedColumnName]
  }

  const loadTableColumnDicts = async (datasourceId: string, tableName: string) => {
    const normalizedDatasourceId = String(datasourceId || '').trim()
    const normalizedTableName = String(tableName || '').trim()
    if (!normalizedDatasourceId || !normalizedTableName) return
    const key = buildCacheKey(normalizedDatasourceId, normalizedTableName)
    if (tableColumnDicts.value[key] || tableColumnDictLoading.value.has(key)) return
    tableColumnDictLoading.value = new Set([...tableColumnDictLoading.value, key])
    try {
      const res = await getDatasourceTableColumnDicts(normalizedDatasourceId, normalizedTableName)
      tableColumnDicts.value = {
        ...tableColumnDicts.value,
        [key]: res.status === 'success' ? (res.data?.dicts || {}) : {}
      }
    } catch (error) {
      console.warn('加载字段字典失败:', error)
      tableColumnDicts.value = { ...tableColumnDicts.value, [key]: {} }
    } finally {
      const nextLoading = new Set(tableColumnDictLoading.value)
      nextLoading.delete(key)
      tableColumnDictLoading.value = nextLoading
    }
  }

  const loadTablesColumnDicts = async (datasourceId: string, tableNames: string[]) => {
    const uniqueTableNames = Array.from(new Set(
      tableNames.map(item => String(item || '').trim()).filter(Boolean)
    ))
    await Promise.all(uniqueTableNames.map(tableName => loadTableColumnDicts(datasourceId, tableName)))
  }

  return {
    getColumnDict,
    loadTableColumnDicts,
    loadTablesColumnDicts,
    resetColumnDictCache,
  }
}
