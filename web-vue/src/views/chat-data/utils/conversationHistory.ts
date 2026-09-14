import type { ConversationGroupPage, ConversationGroupView, ConversationItem, DataSource } from '../types'

export type ConversationViewMode = 'grouped' | 'flat'

// 排序依据：updated 按最近更新（默认），created 按创建时间
export type ConversationSortBy = 'updated' | 'created'

export const PINNED_GROUP_KEY = 'system:pinned'
export const FLAT_GROUP_KEY = 'system:flat'
export const UNCLASSIFIED_GROUP_KEY = 'system:unclassified'
const RESERVED_GROUP_LABELS = new Set(['置顶', '未分类', '未知数据源'])

const getDatasourceGroupLabel = (datasourceName: string): string => (
  RESERVED_GROUP_LABELS.has(datasourceName) ? `${datasourceName}（数据源）` : datasourceName
)

const sortConversations = (
  conversations: ConversationItem[],
  sortBy: ConversationSortBy = 'updated',
): ConversationItem[] => (
  [...conversations].sort((a, b) => {
    const favoriteDiff = Number(Boolean(b.is_favorited)) - Number(Boolean(a.is_favorited))
    if (favoriteDiff !== 0) return favoriteDiff
    // 缺失字段回退到另一侧时间，避免脏数据被排到两端
    const keyOf = sortBy === 'created'
      ? (item: ConversationItem) => String(item.create_time || item.updated_at || '')
      : (item: ConversationItem) => String(item.updated_at || item.create_time || '')
    return keyOf(b).localeCompare(keyOf(a))
  })
)

export const buildConversationGroups = (
  conversations: ConversationItem[],
  datasources: Pick<DataSource, 'id' | 'group_name'>[],
  viewMode: ConversationViewMode,
  sortBy: ConversationSortBy = 'updated',
): ConversationGroupView[] => {
  const sorted = sortConversations(conversations, sortBy)
  if (sorted.length === 0) return []
  if (viewMode === 'flat') {
    return [{ key: FLAT_GROUP_KEY, label: '', items: sorted }]
  }

  const datasourceNameMap = new Map(datasources.map(datasource => [datasource.id, datasource.group_name]))
  const regularGroups = new Map<string, ConversationGroupView>()
  const pinnedItems: ConversationItem[] = []

  for (const conversation of sorted) {
    if (conversation.is_favorited) {
      pinnedItems.push(conversation)
      continue
    }

    const datasourceId = String(conversation.datasource_id || '').trim()
    const datasourceName = datasourceId ? datasourceNameMap.get(datasourceId) : undefined
    const key = datasourceId ? `datasource:${datasourceId}` : UNCLASSIFIED_GROUP_KEY
    const label = datasourceId
      ? (datasourceName ? getDatasourceGroupLabel(datasourceName) : `未知数据源（${datasourceId}）`)
      : '未分类'
    const group = regularGroups.get(key) || { key, label, items: [] }
    group.items.push(conversation)
    regularGroups.set(key, group)
  }

  const groups: ConversationGroupView[] = []
  if (pinnedItems.length > 0) {
    groups.push({ key: PINNED_GROUP_KEY, label: '置顶', items: pinnedItems })
  }

  const fallbackGroups: ConversationGroupView[] = []
  for (const group of regularGroups.values()) {
    if (group.key === UNCLASSIFIED_GROUP_KEY || group.label.startsWith('未知数据源（')) {
      fallbackGroups.push(group)
    } else {
      groups.push(group)
    }
  }
  return [...groups, ...fallbackGroups]
}

export const buildConversationGroupViews = (
  groups: ConversationGroupPage[],
  datasources: Pick<DataSource, 'id' | 'group_name'>[],
  loadingGroupKeys: ReadonlySet<string> = new Set(),
  sortBy: ConversationSortBy = 'updated',
): ConversationGroupView[] => {
  const datasourceNameMap = new Map(datasources.map(datasource => [datasource.id, datasource.group_name]))
  const views = groups.map((group): ConversationGroupView => {
    const datasourceId = String(group.datasource_id || '').trim()
    const datasourceName = datasourceId ? datasourceNameMap.get(datasourceId) : undefined
    let label = '未分类'
    if (group.key === PINNED_GROUP_KEY) {
      label = '置顶'
    } else if (datasourceId) {
      label = datasourceName
        ? getDatasourceGroupLabel(datasourceName)
        : `未知数据源（${datasourceId}）`
    }
    return {
      key: group.key,
      label,
      items: sortConversations(group.conversations, sortBy),
      page: group.page,
      has_more: group.has_more,
      next_cursor: group.next_cursor,
      is_loading_more: loadingGroupKeys.has(group.key),
    }
  })
  const pinned = views.filter(group => group.key === PINNED_GROUP_KEY)
  const regular = views.filter(group => (
    group.key !== PINNED_GROUP_KEY
    && group.key !== UNCLASSIFIED_GROUP_KEY
    && !group.label.startsWith('未知数据源（')
  ))
  const fallback = views.filter(group => (
    group.key === UNCLASSIFIED_GROUP_KEY || group.label.startsWith('未知数据源（')
  ))
  return [...pinned, ...regular, ...fallback]
}

export const shouldLoadMoreConversations = (options: {
  hasMore: boolean
  isLoading: boolean
  isLoadingMore: boolean
  scrollHeight: number
  scrollTop?: number
  clientHeight: number
  threshold?: number
}): boolean => {
  if (!options.hasMore || options.isLoading || options.isLoadingMore) return false
  const threshold = options.threshold ?? 80
  return options.scrollHeight - (options.scrollTop || 0) - options.clientHeight <= threshold
}
