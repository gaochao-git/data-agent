import { describe, expect, it } from 'vitest'
import { nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import ThinkingSegmentBody from '../conversation/ThinkingSegmentBody.vue'

describe('ThinkingSegmentBody', () => {
  // jsdom 无真实布局：mock scrollHeight/clientHeight/scrollTop（同 ToolCallList.spec 手法）
  const stubScroll = (el: HTMLElement) => {
    const state = { scrollTop: 0 }
    Object.defineProperty(el, 'scrollHeight', { value: 600, configurable: true })
    Object.defineProperty(el, 'clientHeight', { value: 210, configurable: true })
    Object.defineProperty(el, 'scrollTop', {
      get: () => state.scrollTop,
      set: (v: number) => {
        state.scrollTop = v
      },
      configurable: true,
    })
    return state
  }

  const flush = async () => {
    await nextTick()
    await new Promise((resolve) => setTimeout(resolve, 0))
  }

  it('keeps sticking to the bottom while the active segment streams', async () => {
    const wrapper = mount(ThinkingSegmentBody, {
      props: { content: '开头', active: true },
    })
    const state = stubScroll(wrapper.element as HTMLElement)

    // 流式追加：粘底跟随
    await wrapper.setProps({ content: '开头\n中间' })
    await flush()
    expect(state.scrollTop).toBe(600)
  })

  it('stops following when the user scrolls up and resumes near the bottom', async () => {
    const wrapper = mount(ThinkingSegmentBody, {
      props: { content: '开头', active: true },
    })
    const el = wrapper.element as HTMLElement
    const state = stubScroll(el)
    await flush()

    // 用户上翻回看（scroll 事件：距底 390px ≥ 100）：暂停跟随，不强行拉底
    state.scrollTop = 0
    await wrapper.trigger('scroll')
    await wrapper.setProps({ content: '开头\n中间' })
    await flush()
    expect(state.scrollTop).toBe(0)

    // 滚回底部附近（距底 50px < 100）：恢复跟随
    state.scrollTop = 340
    await wrapper.trigger('scroll')
    await wrapper.setProps({ content: '开头\n中间\n更多' })
    await flush()
    expect(state.scrollTop).toBe(600)
  })

  it('does not scroll when the segment is no longer active', async () => {
    const wrapper = mount(ThinkingSegmentBody, {
      props: { content: '开头', active: false },
    })
    const state = stubScroll(wrapper.element as HTMLElement)

    await wrapper.setProps({ content: '开头\n中间' })
    await flush()
    expect(state.scrollTop).toBe(0)
  })
})
