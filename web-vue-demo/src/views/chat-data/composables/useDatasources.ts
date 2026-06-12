/**
 * 数据源管理 composable
 * 获取和管理数据源列表
 */
import { ref, computed } from 'vue'
import { getDatasources } from '../api'
import type { DataSource } from '../types'

export function useDatasources() {
  const loading = ref(false)
  const error = ref<string | null>(null)
  const datasources = ref<DataSource[]>([])
  const selectedDatasourceId = ref<string>('')

  // 加载数据源列表
  const loadDatasources = async (): Promise<boolean> => {
    loading.value = true
    error.value = null

    try {
      const response = await getDatasources()

      if (response.data?.datasources) {
        datasources.value = response.data.datasources

        // 自动选择默认数据源
        if (!selectedDatasourceId.value && datasources.value.length > 0) {
          const defaultDs = datasources.value.find((d) => d.is_default)
          selectedDatasourceId.value = defaultDs?.id || datasources.value[0]?.id || ''
        }

        return true
      }

      error.value = '加载数据源失败'
      return false
    } catch (err) {
      error.value = err instanceof Error ? err.message : '加载数据源失败'
      return false
    } finally {
      loading.value = false
    }
  }

  // 选择数据源
  const selectDatasource = (datasourceId: string) => {
    selectedDatasourceId.value = datasourceId
  }

  // 当前数据源
  const currentDatasource = computed(() => {
    if (!selectedDatasourceId.value) return null
    return datasources.value.find((d) => d.id === selectedDatasourceId.value) || null
  })

  // 是否有数据源
  const hasDatasources = computed(() => datasources.value.length > 0)

  return {
    loading,
    error,
    datasources,
    selectedDatasourceId,
    currentDatasource,
    hasDatasources,
    loadDatasources,
    selectDatasource
  }
}
