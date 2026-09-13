import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import HtmlArtifactCard from '../HtmlArtifactCard.vue'
import * as api from '../../api'

vi.mock('../../api', () => ({
  getConversationWorkspaceRawFile: vi.fn(),
}))

const mockedGetRaw = vi.mocked(api.getConversationWorkspaceRawFile)

const artifact = { path: 'outputs/report.html', name: 'report.html', artifactType: 'html' as const }

// jsdom 的 Blob 未实现 text()，用带 text() 的对象模拟真实 Blob 返回。
const blobOf = (content: string) => ({ text: async () => content }) as unknown as Blob

const mountCard = () => mount(HtmlArtifactCard, {
  props: { conversationId: 'conv-1', artifact },
})

describe('HtmlArtifactCard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('stays collapsed and does not fetch content before expanding', async () => {
    const wrapper = mountCard()
    expect(wrapper.find('iframe').exists()).toBe(false)
    expect(mockedGetRaw).not.toHaveBeenCalled()
  })

  it('loads html lazily and renders sandbox iframe after expanding', async () => {
    mockedGetRaw.mockResolvedValue(blobOf('<html><body>ok</body></html>'))
    const wrapper = mountCard()

    await wrapper.find('button').trigger('click')
    await flushPromises()

    expect(mockedGetRaw).toHaveBeenCalledWith('conv-1', 'outputs/report.html')
    const iframe = wrapper.find('iframe')
    expect(iframe.exists()).toBe(true)
    expect(iframe.attributes('sandbox')).toBe('allow-scripts')
    expect(wrapper.text()).not.toContain('加载失败')
  })

  it('collapsing again keeps content and avoids refetch on re-expand', async () => {
    mockedGetRaw.mockResolvedValue(blobOf('<html><body>ok</body></html>'))
    const wrapper = mountCard()

    await wrapper.find('button').trigger('click')
    await flushPromises()
    await wrapper.find('button').trigger('click')
    expect(wrapper.find('iframe').exists()).toBe(false)

    await wrapper.find('button').trigger('click')
    await flushPromises()
    expect(mockedGetRaw).toHaveBeenCalledTimes(1)
  })

  it('shows error and supports retry for invalid html content', async () => {
    mockedGetRaw.mockResolvedValue(blobOf('plain text, not html'))
    const wrapper = mountCard()

    await wrapper.find('button').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('文件不是有效的 HTML 页面')

    mockedGetRaw.mockResolvedValue(blobOf('<html><body>ok</body></html>'))
    const retryButton = wrapper.findAll('button').find(b => b.attributes('title') === '重新加载')!
    await retryButton.trigger('click')
    await flushPromises()
    expect(wrapper.find('iframe').exists()).toBe(true)
  })

  it('shows error state when the request rejects', async () => {
    mockedGetRaw.mockRejectedValue(new Error('network down'))
    const wrapper = mountCard()

    await wrapper.find('button').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('加载失败，请重试')
  })
})
