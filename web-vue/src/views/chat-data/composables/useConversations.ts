/**
 * 会话列表管理 composable
 * 获取、创建和更新会话。
 */
import { ref, computed } from 'vue'
import {
  getConversations,
  createConversation as apiCreateConversation,
  updateConversationTitle as apiUpdateConversationTitle
} from '../api'

interface Conversation {
  id: string
  title: string
  created_at: string
  updated_at: string
  datasource_id?: string
  message_count?: number
}

export function useConversations(agentId: string = 'chat_data') {
  const loading = ref(false)
  const error = ref<string | null>(null)
  const conversations = ref<Conversation[]>([])
  const currentConversationId = ref<string | null>(null)

  // 加载会话列表
  const loadConversations = async (datasourceId?: string): Promise<boolean> => {
    loading.value = true
    error.value = null

    try {
      const response = await getConversations(agentId, datasourceId)

      const data = response.data as unknown
      const list = Array.isArray(data)
        ? data
        : Array.isArray((data as any)?.conversations)
          ? (data as any).conversations
          : []

      if (list.length > 0 || response.status === 'success') {
        conversations.value = list as Conversation[]
        return true
      }

      error.value = '加载会话列表失败'
      return false
    } catch (err) {
      error.value = err instanceof Error ? err.message : '加载会话列表失败'
      return false
    } finally {
      loading.value = false
    }
  }

  // 创建新会话
  const createConversation = async (datasourceId?: string, title?: string): Promise<string | null> => {
    loading.value = true
    error.value = null

    try {
      const response = await apiCreateConversation({
        agent_id: agentId,
        datasource_id: datasourceId,
        title: title || '新会话'
      })

      if (response.data) {
        const convId = response.data.conversation?.id || response.data.id
        if (convId) {
          // 添加到列表
          if (response.data.conversation) {
            conversations.value.unshift(response.data.conversation as Conversation)
          } else {
            conversations.value.unshift({
              id: convId,
              title: title || '新会话',
              created_at: new Date().toISOString(),
              updated_at: new Date().toISOString(),
              datasource_id: datasourceId
            })
          }
          currentConversationId.value = convId
          return convId
        }
      }

      error.value = '创建会话失败'
      return null
    } catch (err) {
      error.value = err instanceof Error ? err.message : '创建会话失败'
      return null
    } finally {
      loading.value = false
    }
  }

  // 更新会话标题
  const updateConversationTitle = async (conversationId: string, title: string): Promise<boolean> => {
    try {
      const res = await apiUpdateConversationTitle(conversationId, title)
      const conv = conversations.value.find((c) => c.id === conversationId)
      if (conv) {
        // 后端会截断标题长度，以返回值为准
        conv.title = res.data?.title ?? title
        conv.updated_at = new Date().toISOString()
      }
      return true
    } catch {
      return false
    }
  }

  // 选择会话
  const selectConversation = (conversationId: string | null) => {
    currentConversationId.value = conversationId
  }

  // 当前会话
  const currentConversation = computed(() => {
    if (!currentConversationId.value) return null
    return conversations.value.find((c) => c.id === currentConversationId.value) || null
  })

  // 是否有会话
  const hasConversations = computed(() => conversations.value.length > 0)

  return {
    loading,
    error,
    conversations,
    currentConversationId,
    currentConversation,
    hasConversations,
    loadConversations,
    createConversation,
    updateConversationTitle,
    selectConversation
  }
}
