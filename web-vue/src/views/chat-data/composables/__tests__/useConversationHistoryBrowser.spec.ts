import { ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiMocks = vi.hoisted(() => ({
  getConversations: vi.fn(),
}))

vi.mock('../../api', () => apiMocks)

import { useConversationHistoryBrowser } from '../useConversationHistoryBrowser'

const conversation = (id: string, datasourceId: string, lastFinishedAt: string | null = null) => ({
  id,
  title: id,
  create_time: '2026-08-14 10:00:00',
  update_time: '2026-08-14 10:00:00',
  datasource_id: datasourceId,
  is_favorited: false,
  last_finished_at: lastFinishedAt,
})

describe('useConversationHistoryBrowser', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })

  it('loads every group first page, then appends only the requested group', async () => {
    apiMocks.getConversations
      .mockResolvedValueOnce({
        status: 'success',
        data: {
          groups: [
            { key: 'datasource:ds_a', datasource_id: 'ds_a', conversations: [conversation('a-1', 'ds_a')], page: 1, page_size: 1, has_more: true, next_cursor: 'cursor-a-1' },
            { key: 'datasource:ds_b', datasource_id: 'ds_b', conversations: [conversation('b-1', 'ds_b')], page: 1, page_size: 1, has_more: false },
            { key: 'system:unclassified', datasource_id: null, conversations: [conversation('u-1', '')], page: 1, page_size: 1, has_more: false },
          ],
        },
      })
      .mockResolvedValueOnce({
        status: 'success',
        data: {
          groups: [
            { key: 'datasource:ds_a', datasource_id: 'ds_a', conversations: [conversation('a-2', 'ds_a')], page: 2, page_size: 1, has_more: false },
          ],
        },
      })

    const history = useConversationHistoryBrowser({
      agentId: 'chat_data',
      pageSize: 1,
      viewMode: ref('grouped'),
      datasources: ref([
        { id: 'ds_a', group_name: '数据源 A' },
        { id: 'ds_b', group_name: '数据源 B' },
      ] as any),
    })

    await history.ensureConversationsLoaded()

    expect(history.groupedConversations.value.map(group => group.key)).toEqual([
      'datasource:ds_a',
      'datasource:ds_b',
      'system:unclassified',
    ])
    expect(apiMocks.getConversations).toHaveBeenNthCalledWith(1, 'chat_data', {
      page: 1,
      page_size: 1,
      view: 'grouped',
    })

    await history.loadMoreConversationGroup('datasource:ds_a')

    expect(apiMocks.getConversations).toHaveBeenNthCalledWith(2, 'chat_data', {
      page: 2,
      page_size: 1,
      view: 'grouped',
      group_key: 'datasource:ds_a',
      cursor: 'cursor-a-1',
    })
    expect(history.groupedConversations.value.find(group => group.key === 'datasource:ds_a')?.items.map(item => item.id)).toEqual(['a-1', 'a-2'])
    expect(history.groupedConversations.value.find(group => group.key === 'datasource:ds_b')?.items.map(item => item.id)).toEqual(['b-1'])
  })

  it('does not mark historical conversations loaded on later pages as unread', async () => {
    apiMocks.getConversations
      .mockResolvedValueOnce({
        status: 'success',
        data: {
          groups: [{
            key: 'datasource:ds_a',
            datasource_id: 'ds_a',
            conversations: [conversation('a-1', 'ds_a', '2026-08-14 10:00:00')],
            page: 1,
            page_size: 1,
            has_more: true,
            next_cursor: 'cursor-a-1',
          }],
        },
      })
      .mockResolvedValueOnce({
        status: 'success',
        data: {
          groups: [{
            key: 'datasource:ds_a',
            datasource_id: 'ds_a',
            conversations: [conversation('a-2', 'ds_a', '2026-08-14 10:00:00')],
            page: 2,
            page_size: 1,
            has_more: false,
          }],
        },
      })

    const history = useConversationHistoryBrowser({
      agentId: 'chat_data',
      pageSize: 1,
      viewMode: ref('grouped'),
      datasources: ref([{ id: 'ds_a', group_name: '数据源 A' }] as any),
    })
    await history.ensureConversationsLoaded()
    expect(history.groupedConversations.value[0]?.items[0]?.has_unread_completion).toBe(false)

    await history.loadMoreConversationGroup('datasource:ds_a')
    const loadedPage2 = history.groupedConversations.value[0]?.items.find(item => item.id === 'a-2')
    expect(loadedPage2?.has_unread_completion).toBe(false)
  })

  it('keeps loaded group pages and still discovers new completions on a polling refresh', async () => {
    const futureFinishedAt = new Date(Date.now() + 60 * 60 * 1000).toISOString()
    apiMocks.getConversations
      .mockResolvedValueOnce({
        status: 'success',
        data: {
          groups: [{
            key: 'datasource:ds_a',
            datasource_id: 'ds_a',
            conversations: [conversation('a-1', 'ds_a')],
            page: 1,
            page_size: 1,
            has_more: true,
            next_cursor: 'cursor-a-1',
          }],
        },
      })
      .mockResolvedValueOnce({
        status: 'success',
        data: {
          groups: [{
            key: 'datasource:ds_a',
            datasource_id: 'ds_a',
            conversations: [conversation('a-2', 'ds_a')],
            page: 2,
            page_size: 1,
            has_more: false,
          }],
        },
      })
      .mockResolvedValueOnce({
        status: 'success',
        data: {
          groups: [{
            key: 'datasource:ds_a',
            datasource_id: 'ds_a',
            conversations: [conversation('a-1', 'ds_a', futureFinishedAt)],
            page: 1,
            page_size: 1,
            has_more: true,
            next_cursor: 'cursor-a-1',
          }],
        },
      })

    const history = useConversationHistoryBrowser({
      agentId: 'chat_data',
      pageSize: 1,
      viewMode: ref('grouped'),
      datasources: ref([{ id: 'ds_a', group_name: '数据源 A' }] as any),
    })
    await history.ensureConversationsLoaded()
    await history.loadMoreConversationGroup('datasource:ds_a')

    await history.refreshConversationsPreservingPages()

    const group = history.groupedConversations.value.find(item => item.key === 'datasource:ds_a')
    expect(group?.items.map(item => item.id)).toEqual(['a-1', 'a-2'])
    expect(group?.page).toBe(2)
    expect(group?.has_more).toBe(false)
    expect(group?.items.find(item => item.id === 'a-1')?.has_unread_completion).toBe(true)
  })

  it('keeps flat pages when a polling refresh reloads the first page', async () => {
    apiMocks.getConversations
      .mockResolvedValueOnce({
        status: 'success',
        data: { conversations: [conversation('a-1', 'ds_a')], has_more: true },
      })
      .mockResolvedValueOnce({
        status: 'success',
        data: { conversations: [conversation('a-2', 'ds_a')], has_more: false },
      })
      .mockResolvedValueOnce({
        status: 'success',
        data: { conversations: [conversation('a-1', 'ds_a')], has_more: true },
      })

    const history = useConversationHistoryBrowser({
      agentId: 'chat_data',
      pageSize: 1,
      viewMode: ref('flat'),
      datasources: ref([{ id: 'ds_a', group_name: '数据源 A' }] as any),
    })
    await history.ensureConversationsLoaded()
    await history.loadMoreConversations()
    expect(history.conversations.value.map(item => item.id)).toContain('a-2')

    await history.refreshConversationsPreservingPages()

    expect(history.conversations.value.map(item => item.id)).toContain('a-2')
    expect(history.hasMoreConversations.value).toBe(false)
  })

  it('keeps cached content visible while refreshing the current view', async () => {
    let resolveRefresh: ((value: unknown) => void) | undefined
    apiMocks.getConversations
      .mockResolvedValueOnce({
        status: 'success',
        data: {
          groups: [{
            key: 'datasource:ds_a',
            datasource_id: 'ds_a',
            conversations: [conversation('a-1', 'ds_a')],
            page: 1,
            page_size: 20,
            has_more: false,
          }],
        },
      })
      .mockImplementationOnce(() => new Promise(resolve => {
        resolveRefresh = resolve
      }))

    const history = useConversationHistoryBrowser({
      agentId: 'chat_data',
      pageSize: 20,
      viewMode: ref('grouped'),
      datasources: ref([{ id: 'ds_a', group_name: '数据源 A' }] as any),
    })
    await history.ensureConversationsLoaded()

    const refreshPromise = history.ensureConversationsLoaded(true)
    expect(history.isLoadingConversations.value).toBe(false)
    expect(history.groupedConversations.value[0]?.items[0]?.id).toBe('a-1')

    resolveRefresh?.({
      status: 'success',
      data: {
        groups: [{
          key: 'datasource:ds_a',
          datasource_id: 'ds_a',
          conversations: [conversation('a-2', 'ds_a')],
          page: 1,
          page_size: 20,
          has_more: false,
        }],
      },
    })
    await refreshPromise

    expect(history.groupedConversations.value[0]?.items[0]?.id).toBe('a-2')
  })

  it('inserts a newly created conversation and updates its running status', async () => {
    const history = useConversationHistoryBrowser({
      agentId: 'chat_data',
      pageSize: 20,
      viewMode: ref('grouped'),
      datasources: ref([{ id: 'ds_a', group_name: '数据源 A' }] as any),
    })

    history.upsertConversation({
      id: 'new-1',
      title: '新会话',
      datasource_id: '',
      create_time: '2026-08-16 10:00:00',
      updated_at: '2026-08-16 10:00:00',
      is_favorited: false,
      run_status: null,
    })
    history.updateConversationRunStatus('new-1', 'active')

    expect(history.groupedConversations.value[0]?.key).toBe('system:unclassified')
    expect(history.groupedConversations.value[0]?.items[0]?.run_status).toBe('active')

    history.upsertConversation({
      id: 'new-1',
      datasource_id: 'ds_a',
      create_time: '2026-08-16 10:00:00',
      updated_at: '2026-08-16 10:01:00',
      run_status: 'active',
    })

    expect(history.groupedConversations.value.map(group => group.key)).toEqual(['datasource:ds_a'])
    expect(history.groupedConversations.value[0]?.items[0]?.id).toBe('new-1')
  })

  it('keeps the raw create_time and re-sorts by creation time when sort-by changes', async () => {
    // 服务端返回 update_time 晚于 create_time 的旧会话：normalize 不得用 update_time 污染 create_time
    apiMocks.getConversations.mockResolvedValue({
      status: 'success',
      data: {
        conversations: [
          { id: 'old-updated', title: '老会话刚更新', create_time: '2026-08-01 10:00:00', update_time: '2026-08-14 10:00:00' },
          { id: 'new-created', title: '新会话', create_time: '2026-08-13 10:00:00', update_time: '2026-08-13 10:00:00' },
        ],
        has_more: false,
      },
    })

    const sortBy = ref<'updated' | 'created'>('updated')
    const history = useConversationHistoryBrowser({
      agentId: 'chat_data',
      pageSize: 20,
      viewMode: ref('flat'),
      datasources: ref([]),
      sortBy,
    })
    await history.ensureConversationsLoaded()

    // 默认按最近更新：老会话刚更新排前；create_time 保持原始创建时间
    expect(history.groupedConversations.value[0]?.items.map(item => item.id)).toEqual(['old-updated', 'new-created'])
    expect(history.conversations.value.find(item => item.id === 'old-updated')?.create_time).toBe('2026-08-01 10:00:00')

    sortBy.value = 'created'
    await Promise.resolve()

    // 切到创建时间排序：computed 依赖 sortBy 自动重排，无需重新请求
    expect(history.groupedConversations.value[0]?.items.map(item => item.id)).toEqual(['new-created', 'old-updated'])
    expect(apiMocks.getConversations).toHaveBeenCalledTimes(1)
  })
})
