import { computed, ref, type Ref } from 'vue'

import { getConversations } from '../api'
import type { ConversationGroupPage, ConversationItem, DataSource } from '../types'
import { readLocalStorage, writeLocalStorage } from '../utils/localStorage'
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
    last_finished_at: typeof conversation.last_finished_at === 'string' ? conversation.last_finished_at : null,
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
  currentUserId?: Ref<string>
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

  // 「完成未读」纯前端记录：不为它增加 user_threads 字段和数据库迁移。
  const unreadStorageKey = computed(() => `chatdata-conversation-read:${options.currentUserId?.value || 'anonymous'}`)
  const unreadBaselineKey = computed(() => `${unreadStorageKey.value}:baseline`)
  const readAtMap = ref<Record<string, number>>({})
  const unreadBaselineAt = ref(0)

  /** 每次列表加载都重读 localStorage：同时兼容切换账号与同浏览器多标签页写入。 */
  const loadUnreadState = () => {
    try {
      const parsed = JSON.parse(readLocalStorage(unreadStorageKey.value) || '{}') as Record<string, unknown>
      readAtMap.value = Object.fromEntries(
        Object.entries(parsed).filter(([, value]) => typeof value === 'number' && Number.isFinite(value)),
      ) as Record<string, number>
    } catch {
      readAtMap.value = {}
    }
    const storedBaseline = Number(readLocalStorage(unreadBaselineKey.value) || '')
    // 旧版存的是 '1' 占位；只接受毫秒时间戳，遇到旧值重新建立水位。
    unreadBaselineAt.value = Number.isFinite(storedBaseline) && storedBaseline > 1_000_000_000_000 ? storedBaseline : 0
  }

  const persistUnreadState = () => {
    writeLocalStorage(unreadStorageKey.value, JSON.stringify(readAtMap.value))
  }

  const parseFinishedAt = (value?: string | null): number => {
    if (!value) return 0
    // 后端返回带时区偏移的 ISO 时间；replace 只为兼容旧的「YYYY-MM-DD HH:mm:ss」格式。
    const parsed = Date.parse(value.replace(' ', 'T'))
    return Number.isFinite(parsed) ? parsed : 0
  }

  const decorateUnread = (items: ConversationItem[]): ConversationItem[] => items.map(item => {
    const finishedAt = parseFinishedAt(item.last_finished_at)
    if (!finishedAt) return { ...item, has_unread_completion: false }
    const readAt = readAtMap.value[item.id]
    if (readAt !== undefined) return { ...item, has_unread_completion: finishedAt > readAt }
    // 首屏之外翻出来的历史会话也按同一时间水位判断，避免只给首屏写已读。
    return { ...item, has_unread_completion: finishedAt > unreadBaselineAt.value }
  })

  /** 首次加载保存全局时间水位：所有早于水位的完成历史都视为已读，翻页也不受首批影响。 */
  const ensureUnreadBaseline = () => {
    loadUnreadState()
    if (unreadBaselineAt.value > 0) return
    unreadBaselineAt.value = Date.now()
    writeLocalStorage(unreadBaselineKey.value, String(unreadBaselineAt.value))
  }

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

  const loadFlatPage = async (page: number, preservePaging = false) => {
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
      const rawIncoming = normalizeConversations(payload.conversations || [], options.agentId)
      ensureUnreadBaseline()
      const incoming = decorateUnread(rawIncoming)
      if (page === 1) {
        // 强制刷新时保留用户已翻出来的页，避免定时轮询把列表弹回首屏。
        const keepPaging = preservePaging && flatPage.value > 1
        const incomingIds = new Set(incoming.map(item => item.id))
        const retained = keepPaging
          ? decorateUnread(flatConversations.value.filter(item => !incomingIds.has(item.id)))
          : []
        flatConversations.value = mergeItems(incoming, retained)
        flatPage.value = keepPaging ? flatPage.value : 1
        flatHasMore.value = keepPaging ? flatHasMore.value : Boolean(payload.has_more)
      } else {
        flatConversations.value = mergeItems(flatConversations.value, incoming)
        flatPage.value = page
        flatHasMore.value = Boolean(payload.has_more)
      }
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

  const loadGroupedFirstPages = async (preservePaging = false) => {
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
      const nextGroupPages = (payload.groups || []).map(group => ({
        ...group,
        conversations: normalizeConversations(group.conversations || [], options.agentId),
      }))
      ensureUnreadBaseline()
      const previousGroups = new Map(groupPages.value.map(group => [group.key, group]))
      groupPages.value = nextGroupPages.map(group => {
        const conversations = decorateUnread(group.conversations)
        const previous = previousGroups.get(group.key)
        if (!preservePaging || !previous || previous.page <= 1) {
          return { ...group, conversations }
        }
        // 保留该分组已「加载更多」翻出来的会话，避免定时轮询把展开的分组收回去。
        const incomingIds = new Set(conversations.map(item => item.id))
        const retained = decorateUnread(previous.conversations.filter(item => !incomingIds.has(item.id)))
        return {
          ...group,
          conversations: mergeItems(conversations, retained),
          page: previous.page,
          has_more: previous.has_more,
          next_cursor: previous.next_cursor,
        }
      })
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

  /** 定时状态刷新：强制重拉首屏以发现新的完成终态，但保留用户已展开的分页。 */
  const refreshConversationsPreservingPages = async () => {
    if (options.viewMode.value === 'flat') {
      if (isLoadingFlat.value) return
      await loadFlatPage(1, true)
      return
    }
    if (isLoadingGrouped.value) return
    await loadGroupedFirstPages(true)
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
      const rawNext = normalizeConversations(nextGroup.conversations || [], options.agentId)
      ensureUnreadBaseline()
      group.conversations = mergeItems(group.conversations, decorateUnread(rawNext))
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

  /** 浏览器本地记录已读；没有新增用户表字段，因此换设备/清缓存不继承已读状态。 */
  const markConversationRead = (conversationId: string) => {
    loadUnreadState()
    readAtMap.value[conversationId] = Date.now()
    persistUnreadState()
    const cachedItems = [
      ...flatConversations.value,
      ...groupPages.value.flatMap(group => group.conversations),
    ]
    cachedItems.filter(item => item.id === conversationId).forEach(item => {
      item.has_unread_completion = false
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
    refreshConversationsPreservingPages,
    loadMoreConversations: loadMoreFlat,
    loadMoreConversationGroup: loadMoreGroup,
    updateConversationTitle: updateTitle,
    updateConversationRunStatus: updateRunStatus,
    markConversationRead,
    upsertConversation,
    refreshAfterFavorite,
    invalidateInactiveView,
  }
}
