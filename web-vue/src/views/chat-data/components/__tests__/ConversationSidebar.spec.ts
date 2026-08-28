import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ConversationSidebar from '../sidebars/ConversationSidebar.vue'
import { formatLocalDateTime } from '../../utils'
import type { ConversationItem } from '../../types'

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

describe('ConversationSidebar conversation row role zone', () => {
  // 组件内部用 new Date() 取当前时间，测试侧用本地时间字符串构造"5 分钟前"，渲染只会更晚、必然落在 5 分钟档
  const fiveMinutesAgo = () => formatLocalDateTime(new Date(Date.now() - 5 * 60 * 1000))

  const mountSidebar = (items: ConversationItem[]) => mount(ConversationSidebar, {
    props: {
      activeTab: 'conversation',
      semanticConfigs: [],
      groupedConversations: [{ key: 'system:pinned', label: '置顶', items }],
    },
  })

  it('shows relative time by default and keeps actions hidden until hover', () => {
    const updatedAt = fiveMinutesAgo()
    const wrapper = mountSidebar([
      { id: 'conv-time', title: '五分钟前的会话', created_at: updatedAt, updated_at: updatedAt },
      { id: 'conv-old', title: '超龄会话', created_at: '2026-01-05 10:00:00', updated_at: '2026-01-05 10:00:00' },
    ])

    const times = wrapper.findAll('[data-testid="conversation-time"]')
    expect(times).toHaveLength(2)
    expect(times[0]!.text()).toBe('5分钟前')
    expect(times[0]!.attributes('title')).toBe(updatedAt)
    // 超过 7 天回落 MM-DD（不显示年份）
    expect(times[1]!.text()).toBe('01-05')

    const actions = wrapper.findAll('[data-testid="conversation-actions"]')
    expect(actions).toHaveLength(2)
    for (const layer of actions) {
      expect(layer.classes()).toContain('opacity-0')
      expect(layer.classes()).toContain('group-hover:opacity-100')
    }
  })

  it('stacks time and buttons in one fixed-width zone so the hover swap causes no layout shift', () => {
    const wrapper = mountSidebar([{ id: 'conv-role', title: '角色区会话', created_at: '2026-08-14 10:00:00' }])

    // 两态共用同一固定宽度容器（2×24px 按钮 + 4px 间距），hover 时互不挤占
    const zone = wrapper.find('[data-testid="conversation-role-zone"]')
    expect(zone.classes()).toContain('w-[52px]')
    expect(zone.classes()).toContain('relative')

    const time = wrapper.find('[data-testid="conversation-time"]')
    expect(time.classes()).toContain('absolute')
    expect(time.classes()).toContain('group-hover:opacity-0')

    const actions = wrapper.find('[data-testid="conversation-actions"]')
    expect(actions.classes()).toContain('absolute')
    expect(actions.classes()).toContain('group-hover:opacity-100')

    expect(actions.find('button[title="置顶"]').exists()).toBe(true)
    expect(actions.find('button[title="修改标题"]').exists()).toBe(true)
  })

  it('keeps pinned rows quiet by default and reveals the pinned state only on hover', () => {
    const updatedAt = fiveMinutesAgo()
    const wrapper = mountSidebar([
      { id: 'conv-pinned', title: '已置顶会话', created_at: updatedAt, updated_at: updatedAt, is_favorited: true },
    ])

    // 默认态仍是时间：置顶状态不常驻展示（分组视图靠「置顶」分组、平铺视图靠排序置前感知）
    const time = wrapper.find('[data-testid="conversation-time"]')
    expect(time.text()).toBe('5分钟前')

    const actions = wrapper.find('[data-testid="conversation-actions"]')
    expect(actions.classes()).toContain('opacity-0')

    // hover 才出现的钉按状态着色（amber = 已置顶），且不再有常驻 opacity-100
    const pin = actions.find('button[title="取消置顶"]')
    expect(pin.exists()).toBe(true)
    expect(pin.classes()).toContain('text-amber-500')
    expect(pin.classes()).not.toContain('opacity-100')
  })
})
