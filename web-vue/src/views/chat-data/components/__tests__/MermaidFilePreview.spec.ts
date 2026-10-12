import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const renderMermaidSourceBlock = vi.hoisted(() =>
  vi.fn<(container: HTMLElement, source: string) => Promise<boolean>>(),
)

vi.mock('../../utils/mermaid', () => ({
  renderMermaidSourceBlock,
}))

import MermaidFilePreview from '../conversation/MermaidFilePreview.vue'

const renderFailedOf = (wrapper: { vm: unknown }) =>
  (wrapper.vm as unknown as { renderFailed: boolean }).renderFailed

describe('MermaidFilePreview', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('渲染成功：整份内容交给渲染入口，不显示源码兜底', async () => {
    renderMermaidSourceBlock.mockResolvedValue(true)
    const wrapper = mount(MermaidFilePreview, {
      props: { content: 'flowchart TD\n  A --> B' },
      attachTo: document.body,
    })
    await flushPromises()

    expect(renderMermaidSourceBlock).toHaveBeenCalledTimes(1)
    expect(renderMermaidSourceBlock.mock.calls[0]?.[1]).toBe('flowchart TD\n  A --> B')
    expect(renderFailedOf(wrapper)).toBe(false)
    expect(wrapper.find('pre').exists()).toBe(false)
    wrapper.unmount()
  })

  it('渲染失败：回退源码展示', async () => {
    renderMermaidSourceBlock.mockResolvedValue(false)
    const wrapper = mount(MermaidFilePreview, {
      props: { content: 'flowchart TD\n  A -->' },
      attachTo: document.body,
    })
    await flushPromises()

    expect(renderFailedOf(wrapper)).toBe(true)
    expect(wrapper.find('pre').text()).toBe('flowchart TD\n  A -->')
    wrapper.unmount()
  })

  it('内容变化后异步竞态：过期渲染结果不回写', async () => {
    let resolveFirst: ((ok: boolean) => void) | undefined
    renderMermaidSourceBlock.mockImplementationOnce(
      () => new Promise<boolean>((resolve) => { resolveFirst = resolve }),
    )
    renderMermaidSourceBlock.mockResolvedValueOnce(true)
    const wrapper = mount(MermaidFilePreview, {
      props: { content: 'first' },
      attachTo: document.body,
    })
    await flushPromises()
    // 第一份内容渲染尚未返回，内容切换并完成第二份渲染
    await wrapper.setProps({ content: 'second' })
    await flushPromises()
    expect(renderFailedOf(wrapper)).toBe(false)

    // 旧渲染此时才返回，应被 token 判定为过期而丢弃
    resolveFirst?.(false)
    await flushPromises()
    expect(renderFailedOf(wrapper)).toBe(false)
    wrapper.unmount()
  })
})
