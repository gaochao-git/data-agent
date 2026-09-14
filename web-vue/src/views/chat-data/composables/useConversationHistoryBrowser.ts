import { computed, ref, type Ref } from 'vue'

import { getConversations } from '../api'
import type { ConversationGroupPage, ConversationItem, DataSource } from '../types'
import {
  buildConversationGroups,
  buildConversationGroupViews,
  type ConversationSortBy,
  type ConversationViewMode,
} from '../utils/conversationHistory'

interface ConversationListPayload {
  conversations?: unknown[]
  groups?: ConversationGroupPage[]
  has_more?: boolean
}

const sortConversations = (
  items: ConversationItem[],
  sortBy: ConversationSortBy = 'updated',
) => [...items].sort((a, b) => {
  const favoriteDiff = Number(Boolean(b.is_favorited)) - Number(Boolean(a.is_favorited))
  if (favoriteDiff !== 0) return favoriteDiff
  const keyOf = sortBy === 'created'
    ? (item: ConversationItem) => String(item.create_time || item.updated_at || '')
    : (item: ConversationItem) => String(item.updated_at || item.create_time || '')
  return keyOf(b).localeCompare(keyOf(a))
})

const normalizeConversation = (raw: unknown, fallbackAgentId: string): ConversationItem => {
  const conversation = raw as Record<string, unknown>
  return {
    id: String(conversation.id || conversation.conversation_id || ''),
    title: String(conversation.title || conversation.first_message_preview || '未命名会话'),
    // create_time 必须用原始创建时间：排序「按创建时间」与展示兜底都依赖它，不能用 update_time 顶替
    create_time: String(conversation.create_time || conversation.created_at || ''),
    updated_at: String(conversation.update_time || conversation.updated_at || conversation.create_time || conversation.created_at || ''),
    agent_id: String(conversation.agent_id || fallbackAgentId),
    datasource_id: typeof conversation.datasource_id === 'string' ? conversation.datasource_id : '',
    is_favorited: Boolean(conversation.is_favorited),
    run_status: conversation.run_status === null || conversation.run_status === undefined
      ? null
      : String(conversation.run_status) as ConversationItem['run_status'],
  }
}

const normalizeConversations = (rows: unknown[], agentId: string) => rows
  .map(row => normalizeConversation(row, agentId))
  .filter(conversation => Boolean(conversation.id))

const mergeConversations = (
  current: ConversationItem[],
  incoming: ConversationItem[],
  sortBy: ConversationSortBy = 'updated',
) => {
  const merged = new Map(current.map(item => [item.id, item]))
  incoming.forEach(item => merged.set(item.id, item))
  return sortConversations([...merged.values()], sortBy)
}

type ConversationUpsert = Pick<ConversationItem, 'id'> & Partial<Omit<ConversationItem, 'id'>>

export const useConversationHistoryBrowser = (options: {
  agentId: string
  pageSize: number
  viewMode: Ref<ConversationViewMode>
  datasources: Ref<DataSource[]>
  sortBy?: Ref<ConversationSortBy>
}) => {
  // 未显式传入时保持历史行为：按最近更新排序
  const sortBy = options.sortBy ?? ref<ConversationSortBy>('updated')
  const sortItems = (items: ConversationItem[]) => sortConversations(items, sortBy.value)
  const mergeItems = (current: ConversationItem[], incoming: ConversationItem[]) =>
    mergeConversations(current, incoming, sortBy.value)
  const flatConversations = ref<ConversationItem[]>([])
  const groupPages = ref<ConversationGroupPage[]>([])
  const flatPage = ref(1)
  const flatHasMore = ref(false)
  const flatLoaded = ref(false)
  const groupedLoaded = ref(false)
  const isLoadingFlat = ref(false)
  const isLoadingGrouped = ref(false)
  const flatLoadingPage = ref<number | null>(null)
  const loadingGroupKeys = ref<Set<string>>(new Set())
  let flatLoadSequence = 0
  let groupedLoadSequence = 0

  const groupedConversations = computed(() => {
    if (options.viewMode.value === 'flat') {
      return buildConversationGroups(flatConversations.value, options.datasources.value, 'flat', sortBy.value)
    }
    return buildConversationGroupViews(groupPages.value, options.datasources.value, loadingGroupKeys.value, sortBy.value)
  })

  const conversations = computed(() => {
    const allItems = [
      ...flatConversations.value,
      ...groupPages.value.flatMap(group => group.conversations),
    ]
    return [...new Map(allItems.map(item => [item.id, item])).values()]
  })

  // isLoading：仅「首屏加载且无旧数据」为真（避免刷新时列表闪骨架屏）；
  // isRefreshing：原始请求在途状态，供手动刷新按钮转圈/禁用使用。
  const isLoading = computed(() => options.viewMode.value === 'flat'
    ? isLoadingFlat.value && flatConversations.value.length === 0
    : isLoadingGrouped.value && groupPages.value.length === 0)
  const isRefreshing = computed(() => options.viewMode.value === 'flat'
    ? isLoadingFlat.value
    : isLoadingGrouped.value)
  const isLoadingMoreFlat = computed(() => isLoadingFlat.value && (flatLoadingPage.value || 0) > 1)

  const loadFlatPage = async (page: number) => {
    const sequence = ++flatLoadSequence
    isLoadingFlat.value = true
    flatLoadingPage.value = page
    try {
      const response = await getConversations(options.agentId, {
        page,
        page_size: options.pageSize,
        view: 'flat',
      })
      if (sequence !== flatLoadSequence) return
      if (response.status !== 'success' || Array.isArray(response.data)) return
      const payload = response.data as ConversationListPayload
      const incoming = normalizeConversations(payload.conversations || [], options.agentId)
      flatConversations.value = page === 1
        ? sortItems(incoming)
        : mergeItems(flatConversations.value, incoming)
      flatPage.value = page
      flatHasMore.value = Boolean(payload.has_more)
      flatLoaded.value = true
    } catch (error) {
      console.error('加载平铺会话失败:', error)
    } finally {
      if (sequence === flatLoadSequence) {
        isLoadingFlat.value = false
        flatLoadingPage.value = null
      }
    }
  }

  const loadGroupedFirstPages = async () => {
    const sequence = ++groupedLoadSequence
    isLoadingGrouped.value = true
    try {
      const response = await getConversations(options.agentId, {
        page: 1,
        page_size: options.pageSize,
        view: 'grouped',
      })
      if (sequence !== groupedLoadSequence) return
      if (response.status !== 'success' || Array.isArray(response.data)) return
      const payload = response.data as ConversationListPayload
      groupPages.value = (payload.groups || []).map(group => ({
        ...group,
        conversations: normalizeConversations(group.conversations || [], options.agentId),
      }))
      groupedLoaded.value = true
    } catch (error) {
      console.error('加载分组会话失败:', error)
    } finally {
      if (sequence === groupedLoadSequence) isLoadingGrouped.value = false
    }
  }

  const ensureLoaded = async (force = false) => {
    if (options.viewMode.value === 'flat') {
      if (isLoadingFlat.value || (!force && flatLoaded.value)) return
      await loadFlatPage(1)
      return
    }
    if (isLoadingGrouped.value || (!force && groupedLoaded.value)) return
    await loadGroupedFirstPages()
  }

  const loadMoreFlat = async () => {
    if (!flatLoaded.value || isLoadingFlat.value || !flatHasMore.value) return
    await loadFlatPage(flatPage.value + 1)
  }

  const loadMoreGroup = async (groupKey: string) => {
    const group = groupPages.value.find(item => item.key === groupKey)
    if (!group?.has_more || loadingGroupKeys.value.has(groupKey)) return
    loadingGroupKeys.value = new Set(loadingGroupKeys.value).add(groupKey)
    try {
      const response = await getConversations(options.agentId, {
        page: group.page + 1,
        page_size: options.pageSize,
        view: 'grouped',
        group_key: groupKey,
        cursor: group.next_cursor || undefined,
      })
      if (response.status !== 'success' || Array.isArray(response.data)) return
      const payload = response.data as ConversationListPayload
      const nextGroup = payload.groups?.[0]
      if (!nextGroup || nextGroup.key !== groupKey) return
      group.conversations = mergeItems(
        group.conversations,
        normalizeConversations(nextGroup.conversations || [], options.agentId),
      )
      group.page = nextGroup.page
      group.has_more = nextGroup.has_more
      group.next_cursor = nextGroup.next_cursor
    } catch (error) {
      console.error(`加载会话分组失败: ${groupKey}`, error)
    } finally {
      const nextLoading = new Set(loadingGroupKeys.value)
      nextLoading.delete(groupKey)
      loadingGroupKeys.value = nextLoading
    }
  }

  const updateTitle = (conversationId: string, title: string) => {
    const cachedItems = [
      ...flatConversations.value,
      ...groupPages.value.flatMap(group => group.conversations),
    ]
    cachedItems.filter(item => item.id === conversationId).forEach(item => {
      item.title = title
      item.updated_at = new Date().toISOString()
    })
  }

  const updateRunStatus = (conversationId: string, status: ConversationItem['run_status']) => {
    const cachedItems = [
      ...flatConversations.value,
      ...groupPages.value.flatMap(group => group.conversations),
    ]
    cachedItems.filter(item => item.id === conversationId).forEach(item => {
      item.run_status = status
    })
  }

  const upsertConversation = (conversation: ConversationUpsert) => {
    if (!conversation.id) return

    const cachedConversation = [
      ...flatConversations.value,
      ...groupPages.value.flatMap(group => group.conversations),
    ].find(item => item.id === conversation.id)
    const mergedConversation = cachedConversation
      ? { ...cachedConversation, ...conversation }
      : conversation.title && conversation.create_time
        ? conversation as ConversationItem
        : null
    if (!mergedConversation) return

    const upsert = (items: ConversationItem[]) => {
      const index = items.findIndex(item => item.id === conversation.id)
      if (index >= 0) items[index] = { ...items[index], ...mergedConversation }
      else items.unshift(mergedConversation)
    }

    upsert(flatConversations.value)
    flatConversations.value = sortItems(flatConversations.value)

    const groupKey = mergedConversation.is_favorited
      ? 'system:pinned'
      : mergedConversation.datasource_id
        ? `datasource:${mergedConversation.datasource_id}`
        : 'system:unclassified'
    const existingGroup = groupPages.value.find(group => group.key === groupKey)
    groupPages.value.forEach(group => {
      if (group.key !== groupKey) {
        group.conversations = group.conversations.filter(item => item.id !== conversation.id)
      }
    })
    groupPages.value = groupPages.value.filter(group => (
      group.conversations.length > 0 || group.has_more || group.key === groupKey
    ))
    if (existingGroup) {
      upsert(existingGroup.conversations)
      existingGroup.conversations = sortItems(existingGroup.conversations)
      return
    }
    groupPages.value.push({
      key: groupKey,
      datasource_id: mergedConversation.is_favorited ? null : mergedConversation.datasource_id || null,
      conversations: [mergedConversation],
      page: 1,
      page_size: options.pageSize,
      has_more: false,
      next_cursor: null,
    })
  }

  const refreshAfterFavorite = async (conversationId: string, isFavorited: boolean) => {
    flatConversations.value.filter(item => item.id === conversationId).forEach(item => {
      item.is_favorited = isFavorited
    })
    flatConversations.value = sortItems(flatConversations.value)
    groupedLoaded.value = false
    await loadGroupedFirstPages()
  }

  const invalidateInactiveView = () => {
    if (options.viewMode.value === 'flat') groupedLoaded.value = false
    else flatLoaded.value = false
  }

  return {
    conversations,
    groupedConversations,
    isLoadingConversations: isLoading,
    isRefreshingConversations: isRefreshing,
    isLoadingMoreConversations: isLoadingMoreFlat,
    hasMoreConversations: flatHasMore,
    ensureConversationsLoaded: ensureLoaded,
    loadMoreConversations: loadMoreFlat,
    loadMoreConversationGroup: loadMoreGroup,
    updateConversationTitle: updateTitle,
    updateConversationRunStatus: updateRunStatus,
    upsertConversation,
    refreshAfterFavorite,
    invalidateInactiveView,
  }
}
