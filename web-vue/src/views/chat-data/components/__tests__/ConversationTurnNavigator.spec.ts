import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import ConversationTurnNavigator from '../ConversationTurnNavigator.vue'

const turns = Array.from({ length: 6 }, (_, index) => ({
  id: `user-${index + 1}`,
  role: 'user',
  content: `第 ${index + 1} 次提问`,
}))

const createContainer = (scrollHeight = 800) => {
  const container = document.createElement('div')
  const scrollTo = vi.fn()
  Object.defineProperty(container, 'scrollTo', { value: scrollTo })
  Object.defineProperty(container, 'scrollTop', { value: 0, writable: true })
  Object.defineProperty(container, 'clientHeight', { value: 400 })
  Object.defineProperty(container, 'scrollHeight', { value: scrollHeight })
  Object.defineProperty(container, 'getBoundingClientRect', {
    value: () => ({ top: 0 }),
  })
  turns.forEach((turn, index) => {
    const element = document.createElement('div')
    element.dataset.conversationTurnId = turn.id
    Object.defineProperty(element, 'getBoundingClientRect', {
      value: () => ({ top: index * 180 }),
    })
    container.appendChild(element)
  })
  document.body.appendChild(container)
  return { container, scrollTo }
}

describe('ConversationTurnNavigator', () => {
  it('shows user-turn dots for a long conversation and scrolls to the selected turn', async () => {
    const { container, scrollTo } = createContainer()
    const wrapper = mount(ConversationTurnNavigator, {
      props: { messages: turns, container },
      attachTo: document.body,
    })

    expect(wrapper.findAll('button')).toHaveLength(6)

    await wrapper.findAll('button')[3]!.trigger('click')

    expect(scrollTo).toHaveBeenCalledWith({ top: 516, behavior: 'smooth' })
    wrapper.unmount()
    container.remove()
  })

  it('does not show the navigator for a short conversation that fits in one screen', async () => {
    const { container } = createContainer(700)
    const wrapper = mount(ConversationTurnNavigator, {
      props: { messages: turns.slice(0, 2), container },
      attachTo: document.body,
    })

    expect(wrapper.find('nav').exists()).toBe(false)
    wrapper.unmount()
    container.remove()
  })

  it('expands a readable turn list on hover and collapses on leave', async () => {
    const { container, scrollTo } = createContainer()
    const wrapper = mount(ConversationTurnNavigator, {
      props: { messages: turns, container },
      attachTo: document.body,
    })

    expect(wrapper.findAll('button')).toHaveLength(6)
    expect(wrapper.text()).not.toContain('第 1 次提问')

    await wrapper.trigger('mouseenter')

    const rows = wrapper.findAll('button')
    expect(rows).toHaveLength(6)
    expect(rows[0]!.text()).toContain('第 1 次提问')
    expect(rows[5]!.text()).toContain('第 6 次提问')

    await rows[1]!.trigger('click')

    expect(scrollTo).toHaveBeenCalledWith({ top: 156, behavior: 'smooth' })

    await wrapper.trigger('mouseleave')

    expect(wrapper.findAll('button')).toHaveLength(6)
    wrapper.unmount()
    container.remove()
  })
})
