import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import UserQuestionCard from '../conversation/UserQuestionCard.vue'
import type { PendingUserQuestion } from '../../types'

const question = {
  question_id: 'question-1',
  questions: [
    {
      id: 'scope',
      question: '选择统计范围',
      header: '范围',
      options: [{ label: '全部' }, { label: '本月' }],
      multi_select: false,
    },
    {
      id: 'metrics',
      question: '选择指标',
      options: [{ label: '订单' }, { label: '销售额' }],
      multi_select: true,
    },
  ],
}

describe('UserQuestionCard', () => {
  it('keeps paged answers and submits single and multi selections', async () => {
    const wrapper = mount(UserQuestionCard, { props: { question } })
    const clickButton = async (text: string) => {
      const button = wrapper.findAll('button').find(item => item.text().trim() === text)
      if (!button) throw new Error(`button not found: ${text}`)
      await button.trigger('click')
    }

    await clickButton('全部')
    await clickButton('本月')
    await clickButton('下一题')

    await clickButton('订单')
    await clickButton('销售额')
    await clickButton('提交')

    expect(wrapper.emitted('submit')?.[0]).toEqual([[
      { id: 'scope', selected: ['本月'], custom: '', skipped: false },
      { id: 'metrics', selected: ['订单', '销售额'], custom: '', skipped: false },
    ]])
  })

  it('supports skipping and resets local state for a new question', async () => {
    const wrapper = mount(UserQuestionCard, { props: { question } })

    await wrapper.findAll('button').find(button => button.text().trim() === '跳过本题')!.trigger('click')
    expect(wrapper.text()).toContain('选择指标')

    await wrapper.setProps({
      question: {
        question_id: 'question-2',
        questions: [{ id: 'note', question: '补充说明', options: [], multi_select: false }],
      },
    })

    expect(wrapper.text()).toContain('补充说明')
    expect(wrapper.text()).toContain('1/1')
    expect((wrapper.find('textarea').element as HTMLTextAreaElement).value).toBe('')
    expect(wrapper.findAll('button').find(button => button.text().trim() === '提交')!.attributes('disabled')).toBeDefined()
  })

  it('caps a long option list with an internal scroll area', () => {
    const wrapper = mount(UserQuestionCard, {
      props: {
        question: {
          question_id: 'question-3',
          questions: [{
            id: 'fields',
            question: '选择字段',
            options: Array.from({ length: 12 }, (_, index) => ({ label: `字段${index + 1}` })),
            multi_select: false,
          }],
        },
      },
    })

    const optionButtons = wrapper.findAll('button').filter(button => button.text().startsWith('字段'))
    expect(optionButtons).toHaveLength(12)

    const scrollArea = optionButtons[0]!.element.parentElement!
    expect(scrollArea.className).toContain('max-h-[240px]')
    expect(scrollArea.className).toContain('overflow-y-auto')
  })
})


describe('UserQuestionCard batch approval', () => {
  const batchQuestion: PendingUserQuestion = {
    question_id: 'approval-batch',
    kind: 'approval',
    approval_items: [
      {
        call_id: 'c1', tool_name: 'file_delete', label: '删除文件 test_1.txt',
        intent: '删除第一个测试文件',
        args_summary: { file_path: '/mnt/user-data/workspace/test_1.txt' },
      },
      {
        call_id: 'c2', tool_name: 'dir_delete', label: '删除目录 tmp',
        args_summary: { dir_path: '/mnt/user-data/workspace/tmp' },
      },
    ],
    questions: [
      {
        id: 'c1', question: '删除文件',
        options: [
          { label: '允许' }, { label: '本轮内始终允许' }, { label: '本会话内始终允许' }, { label: '拒绝' },
        ], multi_select: false,
      },
      {
        id: 'c2', question: '删除目录',
        options: [
          { label: '允许' }, { label: '本轮内始终允许' }, { label: '本会话内始终允许' }, { label: '拒绝' },
        ], multi_select: false,
      },
    ],
  }

  it('renders each operation and submits per-item allow/deny decisions', async () => {
    const wrapper = mount(UserQuestionCard, { props: { question: batchQuestion } })

    expect(wrapper.text()).toContain('共 2 个操作')
    expect(wrapper.text()).toContain('删除文件 test_1.txt')
    expect(wrapper.text()).toContain('删除第一个测试文件')
    expect(wrapper.text()).toContain('file_path: workspace/test_1.txt')
    expect(wrapper.find('.approval-args-line').attributes('title')).toContain('"file_path": "workspace/test_1.txt"')
    expect(wrapper.text()).toContain('整个会话内该工具不再询问')

    const approvalButtons = wrapper.findAll('button').filter(button => button.text().trim() === '允许')
    const runAlwaysButtons = wrapper.findAll('button').filter(button => button.text().trim() === '本轮内始终允许')
    const alwaysButtons = wrapper.findAll('button').filter(button => button.text().trim() === '本会话内始终允许')
    const rejectButtons = wrapper.findAll('button').filter(button => button.text().trim() === '拒绝')
    expect(approvalButtons).toHaveLength(2)
    expect(runAlwaysButtons).toHaveLength(2)
    expect(alwaysButtons).toHaveLength(2)
    expect(rejectButtons).toHaveLength(2)

    await approvalButtons[0]!.trigger('click')
    await rejectButtons[1]!.trigger('click')

    const submit = wrapper.findAll('button').find(button => button.text().trim().startsWith('提交'))!
    expect(submit.attributes('disabled')).toBeUndefined()
    await submit.trigger('click')

    expect(wrapper.emitted('submit')?.[0]).toEqual([[
      { id: 'c1', selected: ['允许'], custom: '', skipped: false },
      { id: 'c2', selected: ['拒绝'], custom: '', skipped: false },
    ]])
  })

  it('supports per-item always-allow in a batch', async () => {
    const wrapper = mount(UserQuestionCard, { props: { question: batchQuestion } })
    const alwaysButtons = wrapper.findAll('button').filter(button => button.text().trim() === '本会话内始终允许')
    const rejectButtons = wrapper.findAll('button').filter(button => button.text().trim() === '拒绝')
    await alwaysButtons[0]!.trigger('click')
    await rejectButtons[1]!.trigger('click')

    const submit = wrapper.findAll('button').find(button => button.text().trim().startsWith('提交'))!
    await submit.trigger('click')

    expect(wrapper.emitted('submit')?.[0]).toEqual([[
      { id: 'c1', selected: ['本会话内始终允许'], custom: '', skipped: false },
      { id: 'c2', selected: ['拒绝'], custom: '', skipped: false },
    ]])
  })

  it('supports per-item run-scoped always-allow', async () => {
    const wrapper = mount(UserQuestionCard, { props: { question: batchQuestion } })
    const runAlwaysButtons = wrapper.findAll('button').filter(button => button.text().trim() === '本轮内始终允许')
    const rejectButtons = wrapper.findAll('button').filter(button => button.text().trim() === '拒绝')
    await runAlwaysButtons[0]!.trigger('click')
    await rejectButtons[1]!.trigger('click')

    const submit = wrapper.findAll('button').find(button => button.text().trim().startsWith('提交'))!
    await submit.trigger('click')

    expect(wrapper.emitted('submit')?.[0]).toEqual([[
      { id: 'c1', selected: ['本轮内始终允许'], custom: '', skipped: false },
      { id: 'c2', selected: ['拒绝'], custom: '', skipped: false },
    ]])
  })

  it('supports allow-all / deny-all shortcuts before submit', async () => {
    const wrapper = mount(UserQuestionCard, { props: { question: batchQuestion } })
    await wrapper.findAll('button').find(button => button.text().trim() === '全部允许')!.trigger('click')
    const submit = wrapper.findAll('button').find(button => button.text().trim().startsWith('提交'))!
    expect(submit.attributes('disabled')).toBeUndefined()
  })

  it('shows the shared Chinese tool name, visible paths and the always-allow scope', () => {
    const wrapper = mount(UserQuestionCard, {
      props: {
        question: {
          ...batchQuestion,
          approval_items: [
            {
              call_id: 'c1', tool_name: 'file_delete', label: 'file_delete',
              args_summary: { file_path: '/mnt/user-data/workspace/a.txt' },
            },
            {
              call_id: 'c2', tool_name: 'dir_delete', label: 'dir_delete',
              args_summary: { dir_path: '/mnt/user-data/workspace/tmp' },
            },
          ],
        },
      },
    })

    expect(wrapper.text()).toContain('删除文件')
    expect(wrapper.text()).toContain('删除目录')
    expect(wrapper.text()).toContain('file_path: workspace/a.txt')
    expect(wrapper.text()).not.toContain('/mnt/user-data')

    const always = wrapper.findAll('button').find(button => button.text().trim() === '本会话内始终允许')!
    expect(always.attributes('title')).toContain('本会话内不再询问「删除文件」')
  })
})


describe('UserQuestionCard single approval args', () => {
  it('renders args summary for a single approval tool', () => {
    const wrapper = mount(UserQuestionCard, {
      props: {
        question: {
          question_id: 'approval-single',
          kind: 'approval',
          approval_items: [{
            call_id: 'c1',
            tool_name: 'file_delete',
            label: 'file_delete',
            intent: '删除测试文件',
            args_summary: { file_path: '/mnt/user-data/workspace/test.txt' },
          }],
          questions: [{
            id: 'approval',
            question: '是否允许执行「file_delete」？',
            options: [{ label: '允许' }, { label: '本会话内始终允许' }, { label: '拒绝' }],
            multi_select: false,
          }],
        },
      },
    })

    // 审批卡与工具卡同口径：剥掉沙箱前缀，工具名走中文显示名
    expect(wrapper.text()).toContain('删除测试文件')
    expect(wrapper.text()).toContain('file_path: workspace/test.txt')
    expect(wrapper.find('.approval-args-line').attributes('title')).toContain('"file_path": "workspace/test.txt"')
    expect(wrapper.text()).not.toContain('/mnt/user-data')
    expect(wrapper.text()).toContain('是否允许执行「删除文件」？')
    // 审批不接受自由文本：输入框与「跳过」会让人误以为能放行/只是跳过，实际都落成拒绝
    expect(wrapper.find('textarea').exists()).toBe(false)
    expect(wrapper.findAll('button').some(button => button.text().trim() === '跳过本题')).toBe(false)
  })
})


describe('UserQuestionCard batch approval legacy payload', () => {
  it('renders only two buttons when backend still sends two options', () => {
    const wrapper = mount(UserQuestionCard, {
      props: {
        question: {
          question_id: 'approval-legacy',
          kind: 'approval',
          approval_items: [
            { call_id: 'c1', tool_name: 'file_delete', label: 'file_delete' },
            { call_id: 'c2', tool_name: 'file_delete', label: 'file_delete' },
          ],
          questions: [
            { id: 'c1', question: 'file_delete', options: [{ label: '允许' }, { label: '拒绝' }], multi_select: false },
            { id: 'c2', question: 'file_delete', options: [{ label: '允许' }, { label: '拒绝' }], multi_select: false },
          ],
        },
      },
    })

    expect(wrapper.findAll('button').filter(button => button.text().trim() === '允许')).toHaveLength(2)
    expect(wrapper.findAll('button').filter(button => button.text().trim() === '拒绝')).toHaveLength(2)
    expect(wrapper.findAll('button').filter(button => button.text().trim() === '本会话内始终允许')).toHaveLength(0)
  })
})


describe('UserQuestionCard tool-level approval propagation', () => {
  const options = [
    { label: '允许' }, { label: '本轮内始终允许' }, { label: '本会话内始终允许' }, { label: '拒绝' },
  ]
  const question = {
    question_id: 'approval-tool-scope',
    kind: 'approval',
    approval_items: [
      { call_id: 'a1', tool_name: 'file_delete', label: 'file_delete' },
      { call_id: 'a2', tool_name: 'file_delete', label: 'file_delete' },
      { call_id: 'b1', tool_name: 'dir_delete', label: 'dir_delete' },
    ],
    questions: [
      { id: 'a1', question: 'file_delete', options, multi_select: false },
      { id: 'a2', question: 'file_delete', options, multi_select: false },
      { id: 'b1', question: 'dir_delete', options, multi_select: false },
    ],
  }

  it('selects all same-tool rows when a tool-level option is chosen', async () => {
    const wrapper = mount(UserQuestionCard, { props: { question } })
    const runAlwaysButtons = wrapper.findAll('button').filter(button => button.text().trim() === '本轮内始终允许')
    const allowButtons = wrapper.findAll('button').filter(button => button.text().trim() === '允许')

    // 第 1、2 行是 file_delete，第 3 行是 dir_delete
    await runAlwaysButtons[0]!.trigger('click')
    await allowButtons[2]!.trigger('click')

    const submit = wrapper.findAll('button').find(button => button.text().trim().startsWith('提交'))!
    expect(submit.attributes('disabled')).toBeUndefined()
    await submit.trigger('click')

    expect(wrapper.emitted('submit')?.[0]).toEqual([[
      { id: 'a1', selected: ['本轮内始终允许'], custom: '', skipped: false },
      { id: 'a2', selected: ['本轮内始终允许'], custom: '', skipped: false },
      { id: 'b1', selected: ['允许'], custom: '', skipped: false },
    ]])
  })
})


describe('UserQuestionCard tool-level override combinations', () => {
  it('keeps an individual deny after tool-level propagation', async () => {
    const options = [
      { label: '允许' }, { label: '本轮内始终允许' }, { label: '本会话内始终允许' }, { label: '拒绝' },
    ]
    const wrapper = mount(UserQuestionCard, {
      props: {
        question: {
          question_id: 'approval-combination',
          kind: 'approval',
          approval_items: [
            { call_id: 'a1', tool_name: 'file_delete', label: 'file_delete' },
            { call_id: 'a2', tool_name: 'file_delete', label: 'file_delete' },
            { call_id: 'b1', tool_name: 'dir_delete', label: 'dir_delete' },
          ],
          questions: [
            { id: 'a1', question: 'file_delete', options, multi_select: false },
            { id: 'a2', question: 'file_delete', options, multi_select: false },
            { id: 'b1', question: 'dir_delete', options, multi_select: false },
          ],
        },
      },
    })

    const windowButtons = wrapper.findAll('button').filter(button => button.text().trim() === '本会话内始终允许')
    const denyButtons = wrapper.findAll('button').filter(button => button.text().trim() === '拒绝')
    const allowButtons = wrapper.findAll('button').filter(button => button.text().trim() === '允许')

    // 行 1 选本窗口 → 同工具的行 1、2 自动选中；此时该工具的「允许」被工具级许可覆盖
    await windowButtons[0]!.trigger('click')
    expect(allowButtons[0]!.attributes('disabled')).toBeDefined()
    expect(allowButtons[1]!.attributes('disabled')).toBeDefined()
    expect(allowButtons[2]!.attributes('disabled')).toBeUndefined()
    // 行 2 单独改拒绝：只影响这一条操作
    await denyButtons[1]!.trigger('click')
    // 另一个工具单独允许
    await allowButtons[2]!.trigger('click')

    const submit = wrapper.findAll('button').find(button => button.text().trim().startsWith('提交'))!
    await submit.trigger('click')

    expect(wrapper.emitted('submit')?.[0]).toEqual([[
      { id: 'a1', selected: ['本会话内始终允许'], custom: '', skipped: false },
      { id: 'a2', selected: ['拒绝'], custom: '', skipped: false },
      { id: 'b1', selected: ['允许'], custom: '', skipped: false },
    ]])
  })
})
