/**
 * 模型列表加载：/config 下发的模型注册表（含思考能力声明）。
 * 主对话、SQL 助手、定时任务共用，避免各入口重复拉取与解析；
 */
import { computed, ref } from 'vue'

import { getChatDataConfig } from '../api'
import type { ChatDataRuntimeConfig } from '../api'
import type { ChatDataModelOption } from '../types'

// 状态提升到模块级：config 下发的是会话期静态数据（模型注册表等），
// 且消费方（主对话）会因新建任务 conversationKey++ 强制重挂载，实例级缓存会被清空、
// 每次点「新建任务」都重复请求 /config。共享后重挂载直接复用缓存，inflight 去重也随之跨实例生效。
const modelOptions = ref<ChatDataModelOption[]>([])
const config = ref<ChatDataRuntimeConfig | null>(null)
const loading = ref(false)
let inflight: Promise<void> | null = null

export function useModelOptions() {
  const defaultModelId = computed(
    () => modelOptions.value.find(item => item.is_default)?.id || modelOptions.value[0]?.id || ''
  )

  const load = async (force = false) => {
    if (modelOptions.value.length && !force) return
    if (inflight) return inflight
    loading.value = true
    inflight = (async () => {
      try {
        const res = await getChatDataConfig()
        if (res.status === 'success') {
          config.value = res.data
          modelOptions.value = (res.data.models || []) as ChatDataModelOption[]
        }
      } catch (err) {
        console.error('加载模型列表失败:', err)
      } finally {
        loading.value = false
        inflight = null
      }
    })()
    return inflight
  }

  return { modelOptions, config, defaultModelId, loading, load }
}
