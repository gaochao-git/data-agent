import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import UserQuestionCard from '../conversation/UserQuestionCard.vue'

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
