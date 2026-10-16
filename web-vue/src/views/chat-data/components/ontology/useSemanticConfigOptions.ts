/**
 * 本体映射共享的语义模型选项装配：数据源组 → 组内语义模型扁平清单。
 * 映射编辑器（组→模型级联选择）与对象面板（映射行回显模型名/组名）共用，
 * 全量语义配置跨组一次就绪（公司级量级有限），避免多处重复加载。
 */
import { ref } from 'vue'
import { getAdminDatasources, getAdminSemanticConfigs } from '../../api'
import type { SemanticConfigItem } from '../../types'

export interface SemanticConfigOption {
  config: SemanticConfigItem
  groupId: string
  groupName: string
  /** 展示标签：模型名（组名） */
  label: string
}

export interface SemanticConfigOptionGroup {
  groupId: string
  groupName: string
  configs: SemanticConfigOption[]
}

export function useSemanticConfigOptions() {
  const options = ref<SemanticConfigOption[]>([])
  const loading = ref(false)
  const loaded = ref(false)

  const load = async () => {
    if (loading.value) return
    loading.value = true
    try {
      const dsRes = await getAdminDatasources()
      if (dsRes.status !== 'success') {
        console.error('获取数据源组清单失败:', dsRes.msg)
        return
      }
      const groups = dsRes.data?.datasources || []
      const results = await Promise.all(
        groups.map(async ds => {
          try {
            const res = await getAdminSemanticConfigs(ds.id)
            if (res.status !== 'success') {
              console.error(`获取数据源组 ${ds.id} 的语义配置失败:`, res.msg)
              return []
            }
            return (res.data?.configs || []).map(config => ({
              config,
              groupId: ds.id,
              groupName: ds.group_name,
              label: `${config.name}（${ds.group_name}）`
            }))
          } catch (err) {
            // 单个数据源组失败不阻断整体清单，拦截器已弹出具体错误
            console.error(`获取数据源组 ${ds.id} 的语义配置失败:`, err)
            return []
          }
        })
      )
      options.value = results.flat()
      loaded.value = true
    } catch (err) {
      console.error('获取语义配置清单失败:', err)
    } finally {
      loading.value = false
    }
  }

  const ensureLoaded = async () => {
    if (!loaded.value) await load()
  }

  const optionOf = (configId: string): SemanticConfigOption | undefined =>
    options.value.find(option => option.config.id === configId)

  /** 组→模型级联：按数据源组聚合（el-option-group 渲染用） */
  const groupedOptions = (): SemanticConfigOptionGroup[] => {
    const groups = new Map<string, SemanticConfigOptionGroup>()
    for (const option of options.value) {
      if (!groups.has(option.groupId)) {
        groups.set(option.groupId, { groupId: option.groupId, groupName: option.groupName, configs: [] })
      }
      groups.get(option.groupId)!.configs.push(option)
    }
    return Array.from(groups.values())
  }

  return { options, loading, loaded, load, ensureLoaded, optionOf, groupedOptions }
}
