import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'

import ConversationSidebar from '../sidebars/ConversationSidebar.vue'

describe('ConversationSidebar history groups', () => {
  beforeEach(() => {
    sessionStorage.clear()
  })

  it('loads another page when grouped content does not fill the container', async () => {
    const wrapper = mount(ConversationSidebar, {
      props: {
        activeTab: 'conversation',
        semanticConfigs: [],
        hasMore: true,
        groupedConversations: [{
          key: 'datasource:ds_a',
          label: '业务数据',
          items: [{ id: 'conv-a', title: '查询销售额', created_at: '2026-08-14 10:00:00' }],
        }],
      },
    })

    await flushPromises()

    expect(wrapper.emitted('loadMoreConversations')).toHaveLength(1)
  })

  it('stores collapse state by stable group key instead of display label', async () => {
    const wrapper = mount(ConversationSidebar, {
      props: {
        activeTab: 'conversation',
        semanticConfigs: [],
        groupedConversations: [
          {
            key: 'system:pinned',
            label: '置顶',
            items: [{ id: 'favorite', title: '收藏会话', created_at: '2026-08-14 10:00:00' }],
          },
          {
            key: 'datasource:ds_pinned',
            label: '置顶（数据源）',
            items: [{ id: 'datasource', title: '同名数据源会话', created_at: '2026-08-14 09:00:00' }],
          },
        ],
      },
    })

    const groupButtons = wrapper.findAll('button').filter(button => button.text().trim() === '置顶1')
    await groupButtons[0]!.trigger('click')

    expect(wrapper.text()).not.toContain('收藏会话')
    expect(wrapper.text()).toContain('同名数据源会话')
    expect(JSON.parse(sessionStorage.getItem('chatdata-conversation-collapsed-groups') || '[]')).toEqual([
      'system:pinned',
    ])
  })
})
