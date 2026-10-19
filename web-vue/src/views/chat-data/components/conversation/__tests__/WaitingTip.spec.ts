import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import WaitingTip from '../WaitingTip.vue'

describe('WaitingTip 等待期知识轮播', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('未激活时不渲染；激活后展示提示并轮播；失活后停止', async () => {
    const wrapper = mount(WaitingTip, { props: { active: false, intervalMs: 8000 } })
    expect(wrapper.find('[data-waiting-tip]').exists()).toBe(false)

    await wrapper.setProps({ active: true })
    const first = wrapper.find('[data-waiting-tip]').text()
    expect(first.length).toBeGreaterThan(0)

    await vi.advanceTimersByTimeAsync(8000)
    expect(wrapper.find('[data-waiting-tip]').text()).not.toBe(first)

    await wrapper.setProps({ active: false })
    expect(wrapper.find('[data-waiting-tip]').exists()).toBe(false)
  })

  it('卸载时清理定时器，不抛异常', async () => {
    const wrapper = mount(WaitingTip, { props: { active: true, intervalMs: 1000 } })
    await vi.advanceTimersByTimeAsync(1000)
    wrapper.unmount()
    await vi.advanceTimersByTimeAsync(5000)
    expect(vi.getTimerCount()).toBe(0)
  })
})
