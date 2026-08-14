import type { ConversationItem, DataSource } from '../types'

export type ConversationViewMode = 'grouped' | 'flat'

export interface ConversationGroup {
  key: string
  label: string
  items: ConversationItem[]
}

const PINNED_GROUP_KEY = 'system:pinned'
const FLAT_GROUP_KEY = 'system:flat'
const UNCLASSIFIED_GROUP_KEY = 'system:unclassified'
const RESERVED_GROUP_LABELS = new Set(['置顶', '未分类', '未知数据源'])

const getDatasourceGroupLabel = (datasourceName: string): string => (
  RESERVED_GROUP_LABELS.has(datasourceName) ? `${datasourceName}（数据源）` : datasourceName
)

const sortConversations = (conversations: ConversationItem[]): ConversationItem[] => (
  [...conversations].sort((a, b) => {
    const favoriteDiff = Number(Boolean(b.is_favorited)) - Number(Boolean(a.is_favorited))
    if (favoriteDiff !== 0) return favoriteDiff
    return String(b.updated_at || b.created_at).localeCompare(String(a.updated_at || a.created_at))
  })
)

export const buildConversationGroups = (
  conversations: ConversationItem[],
  datasources: Pick<DataSource, 'id' | 'group_name'>[],
  viewMode: ConversationViewMode,
): ConversationGroup[] => {
  const sorted = sortConversations(conversations)
  if (sorted.length === 0) return []
  if (viewMode === 'flat') {
    return [{ key: FLAT_GROUP_KEY, label: '', items: sorted }]
  }

  const datasourceNameMap = new Map(datasources.map(datasource => [datasource.id, datasource.group_name]))
  const regularGroups = new Map<string, ConversationGroup>()
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

  const groups: ConversationGroup[] = []
  if (pinnedItems.length > 0) {
    groups.push({ key: PINNED_GROUP_KEY, label: '置顶', items: pinnedItems })
  }

  const fallbackGroups: ConversationGroup[] = []
  for (const group of regularGroups.values()) {
    if (group.key === UNCLASSIFIED_GROUP_KEY || group.label.startsWith('未知数据源（')) {
      fallbackGroups.push(group)
    } else {
      groups.push(group)
    }
  }
  return [...groups, ...fallbackGroups]
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
