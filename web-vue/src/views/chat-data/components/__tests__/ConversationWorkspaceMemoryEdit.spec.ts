import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

const apiMocks = vi.hoisted(() => ({
  getFiles: vi.fn(),
  getContent: vi.fn(),
  saveMemory: vi.fn(),
}))

vi.mock('../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api')>()
  return {
    ...actual,
    getConversationWorkspaceFiles: apiMocks.getFiles,
    getConversationWorkspaceFileContent: apiMocks.getContent,
    saveConversationWorkspaceFile: apiMocks.saveMemory,
  }
})

import ConversationWorkspacePanel from '../conversation/ConversationWorkspacePanel.vue'

const flush = () => new Promise(resolve => setTimeout(resolve, 40))

const memoryFile = {
  path: '/mnt/user-data/memory/memory.md',
  relative_path: 'memory/memory.md',
  directory: 'memory',
  name: 'memory.md',
  size: 12,
  mtime: 1_000,
  source: 'memory',
}

const mountPanel = () => mount(ConversationWorkspacePanel, {
  props: {
    open: true,
    conversationId: 'conv-1',
    activeToolCall: null,
  },
  global: {
    stubs: {
      MarkdownFilePreview: true,
      CodeFilePreview: true,
      ToolCallDetailPanel: true,
      QueryResultPanel: true,
      ChartSpecRenderer: true,
    },
  },
})

describe('工作区文本文件编辑', () => {
  it('编辑并保存 memory.md 时带读取版本，成功后退出编辑态', async () => {
    apiMocks.getFiles.mockResolvedValue({
      status: 'success',
      code: 200,
      msg: '',
      data: { files: [memoryFile] },
    })
    apiMocks.getContent.mockResolvedValue({
      status: 'success',
      code: 200,
      msg: '',
      data: {
        path: '/mnt/user-data/memory/memory.md',
        relative_path: 'memory/memory.md',
        content: '- [口径] 旧值\n',
        size: 12,
        mtime: 1_000,
      },
    })
    apiMocks.saveMemory.mockResolvedValue({
      status: 'success',
      code: 200,
      msg: '保存成功',
      data: { path: '/mnt/user-data/memory/memory.md', bytes: 14, mtime: 1_001, saved: true },
    })

    const wrapper = mountPanel()
    const vm = wrapper.vm as unknown as { openFile: (path: string) => Promise<void> }
    await flush()
    await vm.openFile('memory/memory.md')
    await flush()

    const editButton = wrapper.find('[data-console-memory-edit]')
    expect(editButton.exists()).toBe(true)
    await editButton.trigger('click')
    const editor = wrapper.find('[data-workspace-memory-editor]')
    expect(editor.exists()).toBe(true)
    expect((editor.element as HTMLTextAreaElement).value).toBe('- [口径] 旧值\n')

    await editor.setValue('- [口径] 新值\n')
    await wrapper.find('[data-console-memory-save]').trigger('click')
    await flush()

    expect(apiMocks.saveMemory).toHaveBeenCalledWith(
      'conv-1',
      'memory/memory.md',
      '- [口径] 新值\n',
      1_000,
    )
    expect(wrapper.find('[data-workspace-memory-editor]').exists()).toBe(false)
    expect(wrapper.find('[data-console-memory-edit]').exists()).toBe(true)
  })

  it('普通工作区文本文件也显示编辑入口', async () => {
    apiMocks.getFiles.mockResolvedValue({
      status: 'success',
      code: 200,
      msg: '',
      data: {
        files: [{ ...memoryFile, path: '/mnt/user-data/workspace/notes.md', relative_path: 'workspace/notes.md', source: 'workspace' }],
      },
    })
    apiMocks.getContent.mockResolvedValue({
      status: 'success',
      code: 200,
      msg: '',
      data: {
        path: '/mnt/user-data/workspace/notes.md',
        relative_path: 'workspace/notes.md',
        content: '# notes\n',
        size: 8,
        mtime: 2_000,
      },
    })

    const wrapper = mountPanel()
    await flush()
    await (wrapper.vm as unknown as { openFile: (path: string) => Promise<void> }).openFile('workspace/notes.md')
    await flush()

    expect(wrapper.find('[data-console-memory-edit]').exists()).toBe(true)
  })

  it('编辑期间预览被外部刷新时，保存仍使用进入编辑时的版本', async () => {
    apiMocks.getFiles.mockResolvedValue({
      status: 'success',
      code: 200,
      msg: '',
      data: { files: [memoryFile] },
    })
    apiMocks.getContent.mockResolvedValue({
      status: 'success',
      code: 200,
      msg: '',
      data: {
        path: '/mnt/user-data/memory/memory.md',
        relative_path: 'memory/memory.md',
        content: '- [口径] 旧值\n',
        size: 12,
        mtime: 1_000,
      },
    })
    apiMocks.saveMemory.mockResolvedValue({
      status: 'success',
      code: 200,
      msg: '保存成功',
      data: { path: '/mnt/user-data/memory/memory.md', bytes: 14, mtime: 1_101, saved: true },
    })

    const wrapper = mountPanel()
    const vm = wrapper.vm as unknown as {
      openFile: (path: string) => Promise<void>
      refreshFiles: () => void
    }
    await flush()
    await vm.openFile('memory/memory.md')
    await flush()
    await wrapper.find('[data-console-memory-edit]').trigger('click')
    await wrapper.find('[data-workspace-memory-editor]').setValue('- [口径] 新值\n')

    apiMocks.getContent.mockResolvedValueOnce({
      status: 'success',
      code: 200,
      msg: '',
      data: {
        path: '/mnt/user-data/memory/memory.md',
        relative_path: 'memory/memory.md',
        content: '- [口径] 外部更新\n',
        size: 16,
        mtime: 1_100,
      },
    })
    vm.refreshFiles()
    await flush()

    await wrapper.find('[data-console-memory-save]').trigger('click')
    await flush()

    expect(apiMocks.saveMemory).toHaveBeenCalledWith(
      'conv-1',
      'memory/memory.md',
      '- [口径] 新值\n',
      1_000,
    )
  })

  it('workspace/.system 只读文件不显示编辑入口', async () => {
    apiMocks.getFiles.mockResolvedValue({
      status: 'success',
      code: 200,
      msg: '',
      data: {
        files: [{
          ...memoryFile,
          path: '/mnt/user-data/workspace/.system/semantic/model.yaml',
          relative_path: 'workspace/.system/semantic/model.yaml',
          source: 'workspace',
        }],
      },
    })
    apiMocks.getContent.mockResolvedValue({
      status: 'success',
      code: 200,
      msg: '',
      data: {
        path: '/mnt/user-data/workspace/.system/semantic/model.yaml',
        relative_path: 'workspace/.system/semantic/model.yaml',
        content: 'name: model\n',
        size: 12,
        mtime: 3_000,
      },
    })

    const wrapper = mountPanel()
    await flush()
    await (wrapper.vm as unknown as { openFile: (path: string) => Promise<void> }).openFile('workspace/.system/semantic/model.yaml')
    await flush()

    expect(wrapper.find('[data-console-memory-edit]').exists()).toBe(false)
  })

  it('outputs 文本交付文件也显示编辑入口', async () => {
    apiMocks.getFiles.mockResolvedValue({
      status: 'success',
      code: 200,
      msg: '',
      data: {
        files: [{
          ...memoryFile,
          path: '/mnt/user-data/outputs/report.md',
          relative_path: 'outputs/report.md',
          source: 'outputs',
        }],
      },
    })
    apiMocks.getContent.mockResolvedValue({
      status: 'success',
      code: 200,
      msg: '',
      data: {
        path: '/mnt/user-data/outputs/report.md',
        relative_path: 'outputs/report.md',
        content: '# report\n',
        size: 9,
        mtime: 4_000,
      },
    })

    const wrapper = mountPanel()
    await flush()
    await (wrapper.vm as unknown as { openFile: (path: string) => Promise<void> }).openFile('outputs/report.md')
    await flush()

    expect(wrapper.find('[data-console-memory-edit]').exists()).toBe(true)
  })
})
