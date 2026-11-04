import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import ConversationSidePanel from '../conversation/ConversationSidePanel.vue'

const flush = () => new Promise(resolve => setTimeout(resolve, 40))

const mountPanel = async (autoFollow: boolean) => {
  const wrapper = mount(ConversationSidePanel, {
    props: { open: true, title: '工具详情', showTrigger: false, showClose: false, autoFollow },
    slots: { default: '<div class="grow">内容</div>' },
    attachTo: document.body,
  })
  await flush()
  const body = wrapper.find('[data-side-panel-body]').element as HTMLElement
  let scrollTop = 0
  Object.defineProperty(body, 'scrollHeight', { value: 1_000, configurable: true })
  Object.defineProperty(body, 'clientHeight', { value: 300, configurable: true })
  Object.defineProperty(body, 'scrollTop', {
    get: () => scrollTop,
    set: (value: number) => { scrollTop = value },
    configurable: true,
  })
  return { wrapper, body, getScrollTop: () => scrollTop }
}

describe('ConversationSidePanel 贴底跟随', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    document.body.innerHTML = ''
  })

  it('autoFollow：内容增长且贴底时自动滚到底部', async () => {
    const { wrapper, body, getScrollTop } = await mountPanel(true)
    const grow = body.querySelector<HTMLElement>('.grow')!

    grow.textContent = '新增过程内容'
    await flush()
    expect(getScrollTop()).toBe(1_000)
    wrapper.unmount()
  })

  it('面板打开与 autoFollow 同时生效时仍会挂上跟随（v-if 渲染时序回归）', async () => {
    const wrapper = mount(ConversationSidePanel, {
      props: { open: false, title: '工具详情', showTrigger: false, showClose: false, autoFollow: false },
      slots: { default: '<div class="grow">内容</div>' },
      attachTo: document.body,
    })
    await flush()
    expect(wrapper.find('[data-side-panel-body]').exists()).toBe(false)

    await wrapper.setProps({ open: true, autoFollow: true })
    await flush()
    const body = wrapper.find('[data-side-panel-body]').element as HTMLElement
    let scrollTop = 0
    Object.defineProperty(body, 'scrollHeight', { value: 1_000, configurable: true })
    Object.defineProperty(body, 'clientHeight', { value: 300, configurable: true })
    Object.defineProperty(body, 'scrollTop', {
      get: () => scrollTop,
      set: (value: number) => { scrollTop = value },
      configurable: true,
    })

    body.querySelector<HTMLElement>('.grow')!.textContent = '打开后新增的过程内容'
    await flush()
    expect(scrollTop).toBe(1_000)
    wrapper.unmount()
  })

  it('用户上翻查看后内容增长不再自动滚动（暂停），关闭 autoFollow 时也不跟随', async () => {
    const { wrapper, body, getScrollTop } = await mountPanel(true)
    const grow = body.querySelector<HTMLElement>('.grow')!

    // 用户上翻：距底 600 > 100px 阈值
    body.scrollTop = 100
    body.dispatchEvent(new Event('scroll'))
    await flush()
    grow.textContent = '用户正在查看历史'
    await flush()
    expect(getScrollTop()).toBe(100)
    wrapper.unmount()

    const plain = await mountPanel(false)
    const plainGrow = plain.body.querySelector<HTMLElement>('.grow')!
    plainGrow.textContent = '非跟随内容'
    await flush()
    expect(plain.getScrollTop()).toBe(0)
    plain.wrapper.unmount()
  })
})
