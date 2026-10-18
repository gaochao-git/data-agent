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
          items: [{ id: 'conv-a', title: '查询销售额', create_time: '2026-08-14 10:00:00' }],
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
            items: [{ id: 'favorite', title: '收藏会话', create_time: '2026-08-14 10:00:00' }],
          },
          {
            key: 'datasource:ds_pinned',
            label: '置顶（数据源）',
            items: [{ id: 'datasource', title: '同名数据源会话', create_time: '2026-08-14 09:00:00' }],
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
          items: [{ id: 'conv-a', title: '查询销售额', create_time: '2026-08-14 10:00:00' }],
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
            create_time: '2026-08-14 10:00:00',
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
      { id: 'conv-time', title: '五分钟前的会话', create_time: updatedAt, updated_at: updatedAt },
      { id: 'conv-old', title: '超龄会话', create_time: '2026-01-05 10:00:00', updated_at: '2026-01-05 10:00:00' },
    ])

    const times = wrapper.findAll('[data-testid="conversation-time"]')
    expect(times).toHaveLength(2)
    // 简短/完整两档时间分别渲染在各自 span，由容器查询按侧栏宽度切换显示
    expect(times[0]!.find('[data-testid="conversation-time-short"]').text()).toBe('5分钟前')
    expect(times[0]!.find('[data-testid="conversation-time-full"]').text()).toBe(updatedAt)
    expect(times[0]!.attributes('title')).toBe(updatedAt)
    // 超过 7 天回落 MM-DD（不显示年份）
    expect(times[1]!.find('[data-testid="conversation-time-short"]').text()).toBe('01-05')

    const actions = wrapper.findAll('[data-testid="conversation-actions"]')
    expect(actions).toHaveLength(2)
    for (const layer of actions) {
      expect(layer.classes()).toContain('opacity-0')
      expect(layer.classes()).toContain('group-hover:opacity-100')
    }
  })

  it('stacks time and buttons in one fixed-width zone so the hover swap causes no layout shift', () => {
    const wrapper = mountSidebar([{ id: 'conv-role', title: '角色区会话', create_time: '2026-08-14 10:00:00' }])

    // 两态共用同一固定宽度容器（3×24px 按钮 + 2×4px 间距 = 80px），hover 时互不挤占
    const zone = wrapper.find('[data-testid="conversation-role-zone"]')
    expect(zone.classes()).toContain('w-[80px]')
    expect(zone.classes()).toContain('relative')
    // 兜底裁剪：按钮束宽度一旦超过角色区，绝不能溢出压到标题（曾因「复制会话 ID」导致）
    expect(zone.classes()).toContain('overflow-hidden')

    const time = wrapper.find('[data-testid="conversation-time"]')
    expect(time.classes()).toContain('absolute')
    expect(time.classes()).toContain('group-hover:opacity-0')

    const actions = wrapper.find('[data-testid="conversation-actions"]')
    expect(actions.classes()).toContain('absolute')
    expect(actions.classes()).toContain('group-hover:opacity-100')
    // 三枚操作与宽度无关、任何档位都常驻在 DOM 里（hover 淡入即可，不做宽度档位的 display 隐藏）：
    // 窄侧栏只把时间让位给按钮束，绝不隐藏按钮，也不允许溢出压标题
    expect(actions.classes()).toContain('flex')
    expect(actions.classes()).not.toContain('hidden')
    expect(actions.classes().some(name => name.startsWith('@min-'))).toBe(false)

    // 原子性：三枚操作（置顶 / 复制会话 ID / 改名）同属一个按钮束，一起出现、一起消失，
    // 不做单个按钮的档位隐藏（曾把复制按钮单独 hidden 到 380px）
    const actionButtons = actions.findAll('button')
    expect(actionButtons).toHaveLength(3)
    expect(actionButtons.map(button => button.attributes('title'))).toEqual(['置顶', '复制会话 ID', '修改标题'])
    for (const button of actionButtons) {
      expect(button.classes()).not.toContain('hidden')
      expect(button.classes().some(name => name.startsWith('@min-'))).toBe(false)
    }
  })

  it('shows the time at every width and only switches relative/full format by tier', () => {
    const wrapper = mountSidebar([{ id: 'conv-tier', title: '宽度档位会话', create_time: '2026-08-14 10:00:00' }])

    // 容器上下文由组件 style 块的 .conversation-tab-root { container-type: inline-size } 声明，
    // 侧栏拖拽范围 200-500px（index.vue）
    expect(wrapper.find('.conversation-tab-root').exists()).toBe(true)

    // 时间在静止态始终显示：窄/中档是相对时间（80px 角色区装得下），只有 ≥380px 换成完整时间；
    // 不再有「窄档隐藏时间」的 display:none 档位
    const shortTime = wrapper.find('[data-testid="conversation-time-short"]')
    expect(shortTime.classes()).toContain('block')
    expect(shortTime.classes()).toContain('@min-[380px]:hidden')
    expect(shortTime.classes()).not.toContain('hidden')

    const fullTime = wrapper.find('[data-testid="conversation-time-full"]')
    expect(fullTime.classes()).toContain('hidden')
    expect(fullTime.classes()).toContain('@min-[380px]:block')
    expect(fullTime.text()).toBe('2026-08-14 10:00:00')

    // 宽档角色区同步加宽（132px ≈ 19 字符完整时间），中窄档固定 80px（容得下三枚按钮/相对时间）
    const zone = wrapper.find('[data-testid="conversation-role-zone"]')
    expect(zone.classes()).toContain('w-[80px]')
    expect(zone.classes()).toContain('@min-[380px]:w-[132px]')
  })

  it('keeps pinned rows quiet by default and reveals the pinned state only on hover', () => {
    const updatedAt = fiveMinutesAgo()
    const wrapper = mountSidebar([
      { id: 'conv-pinned', title: '已置顶会话', create_time: updatedAt, updated_at: updatedAt, is_favorited: true },
    ])

    // 默认态仍是时间：置顶状态不常驻展示（分组视图靠「置顶」分组、平铺视图靠排序置前感知）
    const time = wrapper.find('[data-testid="conversation-time"]')
    expect(time.find('[data-testid="conversation-time-short"]').text()).toBe('5分钟前')

    const actions = wrapper.find('[data-testid="conversation-actions"]')
    expect(actions.classes()).toContain('opacity-0')

    // hover 才出现的钉按状态着色（amber = 已置顶），且不再有常驻 opacity-100
    const pin = actions.find('button[title="取消置顶"]')
    expect(pin.exists()).toBe(true)
    expect(pin.classes()).toContain('text-amber-500')
    expect(pin.classes()).not.toContain('opacity-100')
  })
})

describe('ConversationSidebar toolbar: refresh + view settings popover', () => {
  const mountToolbar = (props: Record<string, unknown> = {}) => mount(ConversationSidebar, {
    props: {
      activeTab: 'conversation',
      semanticConfigs: [],
      groupedConversations: [{
        key: 'datasource:ds_a',
        label: '业务数据',
        items: [{ id: 'conv-a', title: '查询销售额', create_time: '2026-08-14 10:00:00' }],
      }],
      ...props,
    },
  })

  it('emits refreshConversations from the manual refresh button', async () => {
    const wrapper = mountToolbar()

    const refresh = wrapper.find('[data-testid="conversation-refresh"]')
    expect(refresh.exists()).toBe(true)
    expect(wrapper.emitted('refreshConversations')).toBeUndefined()
    await refresh.trigger('click')

    expect(wrapper.emitted('refreshConversations')).toHaveLength(1)
  })

  it('disables the refresh button and spins the rotate icon while loading', () => {
    const wrapper = mountToolbar({ isLoading: true })

    const refresh = wrapper.find('[data-testid="conversation-refresh"]')
    expect(refresh.attributes('disabled')).toBeDefined()
    // 图标保持 rotate-right 原样，仅加载时自转（fa-spin），不切换成别的图标。
    const icon = refresh.find('i')
    expect(icon.classes()).toContain('fa-rotate-right')
    expect(icon.classes()).toContain('fa-spin')
    expect(icon.classes()).not.toContain('fa-spinner')
  })

  it('spins while a manual refresh request is in flight (isRefreshing)', () => {
    // 手动刷新时旧数据仍在，isLoading（首屏语义）为 false，靠 isRefreshing 驱动转圈。
    const wrapper = mountToolbar({ isRefreshing: true })

    const refresh = wrapper.find('[data-testid="conversation-refresh"]')
    expect(refresh.attributes('disabled')).toBeDefined()
    expect(refresh.find('i').classes()).toContain('fa-spin')
  })

  it('opens the settings popover, checks current grouping/sorting and emits changes', async () => {
    const wrapper = mountToolbar({ conversationViewMode: 'grouped', conversationSortBy: 'updated' })

    // 初始不渲染弹层
    expect(wrapper.find('[data-testid="conversation-view-settings-panel"]').exists()).toBe(false)

    await wrapper.find('[data-testid="conversation-view-settings"]').trigger('click')
    const panel = wrapper.find('[data-testid="conversation-view-settings-panel"]')
    expect(panel.exists()).toBe(true)
    expect(panel.text()).toContain('分组方式')
    expect(panel.text()).toContain('排序方式')

    // 当前选中项带勾
    const checkRows = panel.findAll('button').filter(button => button.find('.fa-check').exists())
    expect(checkRows.map(row => row.text().trim())).toEqual(['按数据源分组', '最近更新'])

    // 切到单列表 → 上抛 updateConversationViewMode 并关闭弹层
    const flatRow = panel.findAll('button').find(button => button.text().trim() === '单列表')
    await flatRow!.trigger('click')
    expect(wrapper.emitted('updateConversationViewMode')).toEqual([['flat']])
    expect(wrapper.find('[data-testid="conversation-view-settings-panel"]').exists()).toBe(false)

    // 再开弹层切排序 → 上抛 updateConversationSortBy
    await wrapper.find('[data-testid="conversation-view-settings"]').trigger('click')
    const createdRow = wrapper
      .find('[data-testid="conversation-view-settings-panel"]')
      .findAll('button')
      .find(button => button.text().trim() === '创建时间')
    await createdRow!.trigger('click')
    expect(wrapper.emitted('updateConversationSortBy')).toEqual([['created']])
  })

  it('closes the popover when clicking outside', async () => {
    const wrapper = mountToolbar({ conversationViewMode: 'flat' })

    await wrapper.find('[data-testid="conversation-view-settings"]').trigger('click')
    expect(wrapper.find('[data-testid="conversation-view-settings-panel"]').exists()).toBe(true)

    // 半透明遮罩点击即关闭
    const overlay = wrapper.find('.fixed.inset-0')
    expect(overlay.exists()).toBe(true)
    await overlay.trigger('click')
    expect(wrapper.find('[data-testid="conversation-view-settings-panel"]').exists()).toBe(false)
  })
})
