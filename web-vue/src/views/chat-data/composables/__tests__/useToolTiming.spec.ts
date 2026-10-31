import { afterEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, ref } from 'vue'
import { mount } from '@vue/test-utils'
import { useToolTiming } from '../useToolTiming'

const mountTiming = () => {
  const started = ref<number | null>(null)
  const ended = ref<number | null>(null)
  const Harness = defineComponent({
    setup() {
      const { durationText, isRunning } = useToolTiming(() => started.value, () => ended.value)
      return () => h('div', `${durationText.value}|${isRunning.value ? 'running' : 'settled'}`)
    },
  })
  return { wrapper: mount(Harness), started, ended }
}

describe('useToolTiming', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('没有开始时间不展示耗时', () => {
    expect(mountTiming().wrapper.text()).toBe('|settled')
  })

  it('已完成按后端盖章的起止时间差展示', async () => {
    const { wrapper, started, ended } = mountTiming()
    started.value = 1_000
    ended.value = 3_500
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toBe('2.5s|settled')
  })

  it('进行中以后端开始时间为起点按秒刷新，结束后固定为总耗时', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(10_000)

    const { wrapper, started, ended } = mountTiming()
    started.value = 8_000
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toBe('2.0s|running')

    vi.advanceTimersByTime(3_000)
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toBe('5.0s|running')

    ended.value = 8_000 + 9_000
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toBe('9.0s|settled')
  })

  it('浏览器时钟落后服务端时按 0 处理，不出现负值', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(10_000)
    const { wrapper, started } = mountTiming()
    started.value = 20_000
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toBe('0ms|running')
  })
})
