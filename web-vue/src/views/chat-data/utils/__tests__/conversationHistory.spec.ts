import { describe, expect, it } from 'vitest'

import type { ConversationItem } from '../../types'
import { buildConversationGroups, buildConversationGroupViews, shouldLoadMoreConversations } from '../conversationHistory'

const conversation = (
  id: string,
  datasourceId: string,
  options: Partial<ConversationItem> = {},
): ConversationItem => ({
  id,
  title: id,
  created_at: '2026-08-14 10:00:00',
  datasource_id: datasourceId,
  ...options,
})

describe('conversationHistory', () => {
  it('uses datasource ids as group identity even when labels collide with system groups', () => {
    const groups = buildConversationGroups([
      conversation('favorite', 'ds_normal', { is_favorited: true }),
      conversation('named-pinned', 'ds_pinned'),
      conversation('named-unclassified', 'ds_unclassified'),
      conversation('unclassified', ''),
    ], [
      { id: 'ds_normal', group_name: '业务数据' },
      { id: 'ds_pinned', group_name: '置顶' },
      { id: 'ds_unclassified', group_name: '未分类' },
    ], 'grouped')

    expect(groups.map(group => [group.key, group.label, group.items.map(item => item.id)])).toEqual([
      ['system:pinned', '置顶', ['favorite']],
      ['datasource:ds_pinned', '置顶（数据源）', ['named-pinned']],
      ['datasource:ds_unclassified', '未分类（数据源）', ['named-unclassified']],
      ['system:unclassified', '未分类', ['unclassified']],
    ])
  })

  it('resolves names from the complete authorized datasource list and keeps unavailable ids in a fallback group', () => {
    const groups = buildConversationGroups([
      conversation('other-module', 'ds_other'),
      conversation('unavailable-a', 'deleted_a'),
      conversation('unavailable-b', 'deleted_b'),
    ], [
      { id: 'ds_other', group_name: '其他模块数据源' },
    ], 'grouped')

    expect(groups.map(group => [group.key, group.label, group.items.map(item => item.id)])).toEqual([
      ['datasource:ds_other', '其他模块数据源', ['other-module']],
      ['datasource:deleted_a', '未知数据源（deleted_a）', ['unavailable-a']],
      ['datasource:deleted_b', '未知数据源（deleted_b）', ['unavailable-b']],
    ])
  })

  it('keeps flat mode sorted with favorites first', () => {
    const groups = buildConversationGroups([
      conversation('old', 'ds_a', { updated_at: '2026-08-13 10:00:00' }),
      conversation('favorite', 'ds_a', { is_favorited: true, updated_at: '2026-08-12 10:00:00' }),
      conversation('new', 'ds_a', { updated_at: '2026-08-14 10:00:00' }),
    ], [], 'flat')

    expect(groups[0]?.key).toBe('system:flat')
    expect(groups[0]?.items.map(item => item.id)).toEqual(['favorite', 'new', 'old'])
  })

  it('builds every backend group first page without requiring totals', () => {
    const groups = buildConversationGroupViews([
      { key: 'datasource:ds_a', datasource_id: 'ds_a', conversations: [conversation('a', 'ds_a')], page: 1, page_size: 20, has_more: true },
      { key: 'system:unclassified', datasource_id: null, conversations: [conversation('u', '')], page: 1, page_size: 20, has_more: false },
      { key: 'system:pinned', datasource_id: null, conversations: [conversation('p', 'ds_a', { is_favorited: true })], page: 1, page_size: 20, has_more: false },
    ], [{ id: 'ds_a', group_name: '业务数据' }])

    expect(groups.map(group => [group.key, group.label, group.has_more])).toEqual([
      ['system:pinned', '置顶', false],
      ['datasource:ds_a', '业务数据', true],
      ['system:unclassified', '未分类', false],
    ])
  })

  it('only requests another page from an explicit scroll-near-bottom check', () => {
    expect(shouldLoadMoreConversations({
      hasMore: true,
      isLoading: false,
      isLoadingMore: false,
      scrollHeight: 300,
      clientHeight: 500,
    })).toBe(true)
    expect(shouldLoadMoreConversations({
      hasMore: true,
      isLoading: false,
      isLoadingMore: false,
      scrollHeight: 900,
      scrollTop: 100,
      clientHeight: 500,
    })).toBe(false)
    expect(shouldLoadMoreConversations({
      hasMore: true,
      isLoading: false,
      isLoadingMore: true,
      scrollHeight: 300,
      clientHeight: 500,
    })).toBe(false)
  })
})
