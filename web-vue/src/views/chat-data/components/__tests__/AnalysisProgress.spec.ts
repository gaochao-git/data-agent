import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import AnalysisProgress from '../conversation/AnalysisProgress.vue'
import type { AnalysisTodosState } from '../../utils/analysisTodos'

const buildState = (statuses: string[]): AnalysisTodosState => ({
  items: statuses.map((status, index) => ({ content: `步骤${index + 1}`, status: status as 'pending' | 'in_progress' | 'completed' })),
  finished: statuses.length > 0 && statuses.every(status => status === 'completed'),
})

describe('AnalysisProgress', () => {
  it('collapses to one line by default and shows live segment counts while running', () => {
    const wrapper = mount(AnalysisProgress, { props: { todos: buildState(['completed', 'in_progress', 'pending']) } })

    expect(wrapper.text()).toContain('任务')
    expect(wrapper.text()).toContain('1 已完成 · 1 进行中 · 1 待处理')
    expect(wrapper.find('.fa-spinner').exists()).toBe(false)
    expect(wrapper.find('.fa-list-check').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('步骤1')
  })

  it('expands the step list on header click with status styles', async () => {
    const wrapper = mount(AnalysisProgress, { props: { todos: buildState(['completed', 'in_progress', 'pending']) } })

    await wrapper.find('button').trigger('click')

    const list = wrapper.find('.max-h-\\[180px\\]')
    expect(list.exists()).toBe(true)
    expect(list.classes()).toContain('overflow-y-auto')
    expect(wrapper.text()).toContain('步骤1')
    expect(wrapper.text()).toContain('步骤3')
    expect(list.find('.fa-circle-check.text-emerald-500').exists()).toBe(true)
    expect(list.find('.fa-spinner.fa-spin').exists()).toBe(true)
    expect(list.find('.border-dashed').exists()).toBe(true)
  })

  it('shows a completed summary when every step is done', () => {
    const wrapper = mount(AnalysisProgress, { props: { todos: buildState(['completed', 'completed']) } })

    expect(wrapper.text()).toContain('2 已完成')
    expect(wrapper.text()).not.toContain('待处理')
  })
})
