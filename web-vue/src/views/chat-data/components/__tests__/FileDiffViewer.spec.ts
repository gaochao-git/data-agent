import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiMocks = vi.hoisted(() => ({
  getConversationWorkspaceFileContent: vi.fn(),
}))

vi.mock('../../api', () => apiMocks)

// jsdom 不真跑 CodeMirror：MergeView 只断言构造入参，其余扩展 API 置空
const mergeMocks = vi.hoisted(() => ({
  MergeView: vi.fn(),
}))

vi.mock('@codemirror/merge', () => ({ MergeView: mergeMocks.MergeView }))
vi.mock('@codemirror/view', () => ({
  EditorView: { theme: () => [], editable: { of: () => [] }, lineWrapping: [] },
  lineNumbers: () => ['line-numbers-ext'],
}))
vi.mock('@codemirror/state', () => ({ EditorState: { readOnly: { of: () => [] } } }))
vi.mock('@codemirror/lang-yaml', () => ({ yaml: () => [] }))
vi.mock('@codemirror/lang-sql', () => ({ sql: () => [] }))

import FileDiffViewer from '../FileDiffViewer.vue'

const changedPayload = {
  file_a: '/mnt/user-data/workspace/test_a.txt',
  file_b: '/mnt/user-data/workspace/test_b.txt',
  status: 'changed',
  added_lines: 2,
  removed_lines: 1,
  diff: '--- /mnt/user-data/workspace/test_a.txt\n+++ /mnt/user-data/workspace/test_b.txt\n@@ -1,3 +1,4 @@\n 第一行\n-第三行：待修改\n+第三行：已修改\n+新增行',
}

const mountViewer = async (payload: unknown = changedPayload) => {
  const wrapper = mount(FileDiffViewer, {
    props: {
      visible: false,
      payload: payload as never,
      conversationId: 'conv-1',
    },
    global: {
      stubs: {
        'el-dialog': {
          props: ['modelValue'],
          emits: ['update:modelValue'],
          template: '<div v-if="modelValue" class="dialog-stub"><slot name="header" /><slot /></div>',
        },
      },
    },
  })
  await wrapper.setProps({ visible: true })
  await flushPromises()
  return wrapper
}

const clickMergeModeButton = async (wrapper: Awaited<ReturnType<typeof mountViewer>>) => {
  const button = wrapper.findAll('button').find(b => b.text().includes('并排对比'))
  expect(button).toBeTruthy()
  await button?.trigger('click')
  await flushPromises()
}

describe('FileDiffViewer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders simple unified diff with colored lines by default', async () => {
    const wrapper = await mountViewer()
    expect(wrapper.text()).toContain('test_a.txt')
    expect(wrapper.text()).toContain('+2')
    expect(wrapper.text()).toContain('-1')
    expect(wrapper.findAll('.bg-emerald-50').length).toBe(2)
    expect(wrapper.findAll('.bg-rose-50').length).toBe(1)
    expect(wrapper.text()).toContain('+第三行：已修改')
  })

  it('shows identical hint without diff body', async () => {
    const wrapper = await mountViewer({
      ...changedPayload,
      status: 'identical',
      added_lines: 0,
      removed_lines: 0,
      diff: undefined,
    })
    expect(wrapper.text()).toContain('完全一致')
    expect(wrapper.find('.bg-emerald-50').exists()).toBe(false)
  })

  it('loads both files and builds MergeView in merge mode', async () => {
    apiMocks.getConversationWorkspaceFileContent
      .mockResolvedValueOnce({ data: { content: '第一行\n第三行：待修改' } })
      .mockResolvedValueOnce({ data: { content: '第一行\n第三行：已修改\n新增行' } })
    const wrapper = await mountViewer()
    await clickMergeModeButton(wrapper)

    expect(apiMocks.getConversationWorkspaceFileContent).toHaveBeenCalledTimes(2)
    const [leftCall, rightCall] = apiMocks.getConversationWorkspaceFileContent.mock.calls
    expect(leftCall?.[0]).toBe('conv-1')
    expect(leftCall?.[1]).toBe('/mnt/user-data/workspace/test_a.txt')
    expect(rightCall?.[1]).toBe('/mnt/user-data/workspace/test_b.txt')
    expect(mergeMocks.MergeView).toHaveBeenCalledTimes(1)
    const options = mergeMocks.MergeView.mock.calls[0]?.[0]
    expect(options?.a.doc).toBe('第一行\n第三行：待修改')
    expect(options?.b.doc).toBe('第一行\n第三行：已修改\n新增行')
    expect(options?.parent).toBeInstanceOf(HTMLElement)
    // 行号必须显式注入（MergeView 手动拼 extensions，无 basicSetup 兜底）
    expect(options?.a.extensions).toContainEqual(['line-numbers-ext'])
    expect(options?.b.extensions).toContainEqual(['line-numbers-ext'])
  })

  it('falls back to simple mode when file contents fail to load', async () => {
    apiMocks.getConversationWorkspaceFileContent.mockRejectedValue(new Error('gone'))
    const wrapper = await mountViewer()
    await clickMergeModeButton(wrapper)

    expect(mergeMocks.MergeView).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('文件全文读取失败，已切换为简洁对比')
    // 降级后仍能看到简洁 diff
    expect(wrapper.findAll('.bg-emerald-50').length).toBe(2)
  })

  it('retries merge load after a previous failure', async () => {
    apiMocks.getConversationWorkspaceFileContent.mockRejectedValueOnce(new Error('gone'))
    const wrapper = await mountViewer()
    await clickMergeModeButton(wrapper)
    expect(wrapper.text()).toContain('文件全文读取失败，已切换为简洁对比')

    // 失败后再次切换到并排对比：清掉旧错误重新拉取
    apiMocks.getConversationWorkspaceFileContent
      .mockResolvedValueOnce({ data: { content: '第一行' } })
      .mockResolvedValueOnce({ data: { content: '第一行' } })
    await clickMergeModeButton(wrapper)

    expect(wrapper.text()).not.toContain('文件全文读取失败')
    expect(mergeMocks.MergeView).toHaveBeenCalledTimes(1)
  })
})
