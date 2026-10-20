import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiMocks = vi.hoisted(() => ({
  getConversationApprovalGrants: vi.fn(),
  revokeConversationApprovalGrants: vi.fn(),
  resetConversationApprovalGrants: vi.fn(),
}))
const messageBoxMock = vi.hoisted(() => ({ confirm: vi.fn() }))

vi.mock('../../api', () => apiMocks)
vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() },
  ElMessageBox: messageBoxMock,
}))

import ApprovalGrantsDialog from '../conversation/ApprovalGrantsDialog.vue'

const stubs = {
  'el-dialog': { template: '<div><slot /><slot name="footer" /></div>' },
  'el-button': { template: '<button @click="$emit(\'click\')"><slot /></button>' },
}

const mountDialog = () => mount(ApprovalGrantsDialog, {
  props: { visible: false, conversationId: 'conv-1' },
  global: {
    stubs,
    directives: { loading: {} },
  },
})

describe('ApprovalGrantsDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMocks.getConversationApprovalGrants.mockResolvedValue({
      status: 'success',
      data: { grants: ['tool:file_delete'] },
    })
    apiMocks.revokeConversationApprovalGrants.mockResolvedValue({
      status: 'success',
      data: { grants: [] },
    })
    apiMocks.resetConversationApprovalGrants.mockResolvedValue({ status: 'success', msg: '已重置' })
    messageBoxMock.confirm.mockResolvedValue('confirm')
  })

  it('loads grants when opened and revokes a single tool', async () => {
    const wrapper = mountDialog()
    await wrapper.setProps({ visible: true })
    await flushPromises()

    expect(apiMocks.getConversationApprovalGrants).toHaveBeenCalledWith('conv-1')
    expect(wrapper.text()).toContain('删除文件')

    const revokeButton = wrapper.findAll('button').find(button => button.text().includes('恢复审批'))!
    await revokeButton.trigger('click')
    await flushPromises()

    expect(apiMocks.revokeConversationApprovalGrants).toHaveBeenCalledWith('conv-1', ['tool:file_delete'])
    expect(wrapper.text()).toContain('当前没有已授权的工具')
  })

  it('shows an empty state when there are no grants', async () => {
    apiMocks.getConversationApprovalGrants.mockResolvedValue({ status: 'success', data: { grants: [] } })
    const wrapper = mountDialog()
    await wrapper.setProps({ visible: true })
    await flushPromises()
    expect(wrapper.text()).toContain('当前没有已授权的工具')
  })

  it('keeps an error state instead of pretending there are no grants', async () => {
    apiMocks.getConversationApprovalGrants.mockRejectedValue(new Error('boom'))
    const wrapper = mountDialog()
    await wrapper.setProps({ visible: true })
    await flushPromises()

    expect(wrapper.text()).toContain('许可加载失败')
    expect(wrapper.text()).not.toContain('当前没有已授权的工具')
  })

  it('asks for confirmation before resetting all grants', async () => {
    const wrapper = mountDialog()
    await wrapper.setProps({ visible: true })
    await flushPromises()

    const resetButton = wrapper.findAll('button').find(button => button.text().includes('全部重置'))!
    await resetButton.trigger('click')
    await flushPromises()

    expect(messageBoxMock.confirm).toHaveBeenCalled()
    expect(apiMocks.resetConversationApprovalGrants).toHaveBeenCalledWith('conv-1')
  })
})
