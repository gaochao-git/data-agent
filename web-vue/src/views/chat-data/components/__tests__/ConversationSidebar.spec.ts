import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ConversationSidebar from '../sidebars/ConversationSidebar.vue'

describe('ConversationSidebar history groups', () => {

  it('does not load every page merely because grouped content does not fill the container', async () => {
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

    await wrapper.vm.$nextTick()

    expect(wrapper.emitted('loadMoreConversations')).toBeUndefined()
    expect(wrapper.text()).not.toContain('加载更多')
    expect(wrapper.find('.conversation-group-items').classes()).toEqual(expect.arrayContaining([
      'ml-[18px]',
      'border-l',
      'pl-3',
    ]))
  })

  it('starts with every group expanded and keeps collapse state local to the current view', async () => {
    const wrapper = mount(ConversationSidebar, {
      props: {
        activeTab: 'conversation',
        semanticConfigs: [],
        hasMore: true,
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

    expect(wrapper.text()).toContain('收藏会话')
    expect(wrapper.text()).toContain('同名数据源会话')

    const groupButtons = wrapper.findAll('button').filter(button => button.text().trim() === '置顶')
    await groupButtons[0]!.trigger('click')

    expect(wrapper.text()).not.toContain('收藏会话')
    expect(wrapper.text()).toContain('同名数据源会话')
    expect(wrapper.emitted('loadMoreConversations')).toBeUndefined()
    expect(sessionStorage.getItem('chatdata-conversation-collapsed-groups')).toBeNull()
  })

  it('loads more inside the requested group without showing a global grouped loader', async () => {
    const wrapper = mount(ConversationSidebar, {
      props: {
        activeTab: 'conversation',
        semanticConfigs: [],
        conversationViewMode: 'grouped',
        hasMore: true,
        groupedConversations: [{
          key: 'datasource:ds_a',
          label: '业务数据',
          items: [{ id: 'conv-a', title: '查询销售额', created_at: '2026-08-14 10:00:00' }],
          has_more: true,
        }],
      },
    })

    const loadMoreButtons = wrapper.findAll('button').filter(button => button.text().trim() === '加载更多')
    expect(loadMoreButtons).toHaveLength(1)
    await loadMoreButtons[0]!.trigger('click')

    expect(wrapper.emitted('loadMoreConversationGroup')).toEqual([['datasource:ds_a']])
    expect(wrapper.emitted('loadMoreConversations')).toBeUndefined()
  })

  it('loads the next flat page only after the user scrolls near the bottom', async () => {
    const wrapper = mount(ConversationSidebar, {
      props: {
        activeTab: 'conversation',
        semanticConfigs: [],
        conversationViewMode: 'flat',
        hasMore: true,
        groupedConversations: [{
          key: 'system:flat',
          label: '',
          items: Array.from({ length: 20 }, (_, index) => ({
            id: `conv-${index}`,
            title: `查询销售额 ${index}`,
            created_at: '2026-08-14 10:00:00',
          })),
        }],
      },
    })
    const scrollContainer = wrapper.find('[data-testid="conversation-scroll"]')
    Object.defineProperties(scrollContainer.element, {
      scrollHeight: { configurable: true, value: 1000 },
      clientHeight: { configurable: true, value: 400 },
      scrollTop: { configurable: true, value: 550 },
    })

    expect(wrapper.text()).not.toContain('加载更多')
    expect(scrollContainer.classes()).toContain('overflow-x-hidden')
    expect(scrollContainer.classes()).toContain('overflow-y-auto')
    expect(scrollContainer.classes()).toContain('flex-1')
    expect(wrapper.find('.conversation-group-items').attributes('style')).toBeUndefined()
    expect(wrapper.emitted('loadMoreConversations')).toBeUndefined()
    await scrollContainer.trigger('scroll')

    expect(wrapper.emitted('loadMoreConversations')).toHaveLength(1)
  })
})
