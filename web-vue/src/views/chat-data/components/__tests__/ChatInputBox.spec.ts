import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import ChatInputBox from '../ChatInputBox.vue'

type ChatInputBoxProps = InstanceType<typeof ChatInputBox>['$props']

const mountBox = (props: ChatInputBoxProps) => mount(ChatInputBox, { props })

describe('ChatInputBox 按钮', () => {
  it('生成中只显示停止按钮，空闲时只显示发送按钮', async () => {
    const wrapper = mountBox({ input: '', isGenerating: true, canSubmit: false })

    expect(wrapper.find('[title="停止生成"]').exists()).toBe(true)
    expect(wrapper.find('button[type="submit"]').exists()).toBe(false)

    await wrapper.setProps({ isGenerating: false })

    expect(wrapper.find('[title="停止生成"]').exists()).toBe(false)
    expect(wrapper.find('button[type="submit"]').exists()).toBe(true)
  })

  it('点击停止与表单提交发出对应事件', async () => {
    const wrapper = mountBox({ input: '', isGenerating: true, canSubmit: false })

    await wrapper.find('[title="停止生成"]').trigger('click')
    expect(wrapper.emitted('stop')).toHaveLength(1)

    // 浏览器里 submit 按钮触发 form submit；jsdom 直接派发 submit 事件验证接线
    await wrapper.find('form').trigger('submit')
    expect(wrapper.emitted('submit')).toHaveLength(1)
  })

  it('停止中显示 spinner，禁止重复停止与 Enter 提交', async () => {
    const wrapper = mountBox({ input: '', isGenerating: true, isStopping: true, canSubmit: false })

    const stop = wrapper.find('[title="正在停止"]')
    expect(stop.exists()).toBe(true)
    expect(stop.attributes('disabled')).toBeDefined()
    expect(stop.find('.fa-spinner').exists()).toBe(true)

    await stop.trigger('click')
    expect(wrapper.emitted('stop')).toBeUndefined()

    await wrapper.find('textarea').trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('submit')).toBeUndefined()
  })

  it('showSubmit=false 时不渲染发送/停止按钮', () => {
    const wrapper = mountBox({ input: '', isGenerating: true, canSubmit: false, showSubmit: false })

    expect(wrapper.find('[title="停止生成"]').exists()).toBe(false)
    expect(wrapper.find('button[type="submit"]').exists()).toBe(false)
  })
})

describe('ChatInputBox 键盘行为', () => {
  it('Enter 触发提交，Shift+Enter 不触发', async () => {
    const wrapper = mountBox({ input: '你好' })

    await wrapper.find('textarea').trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('submit')).toHaveLength(1)

    await wrapper.find('textarea').trigger('keydown', { key: 'Enter', shiftKey: true })
    expect(wrapper.emitted('submit')).toHaveLength(1)
    expect(wrapper.emitted('keydown')).toHaveLength(2)
  })

  it('submitOnEnter=false 时 Enter 不触发提交（多行指令）', async () => {
    const wrapper = mountBox({ input: '', submitOnEnter: false })

    await wrapper.find('textarea').trigger('keydown', { key: 'Enter' })

    expect(wrapper.emitted('submit')).toBeUndefined()
  })

  it('消费者 preventDefault 后不再兜底提交', async () => {
    const wrapper = mount(ChatInputBox, {
      props: { input: '' },
      attrs: {
        onKeydown: (event: KeyboardEvent) => {
          if (event.key === 'Enter') event.preventDefault()
        },
      },
    })

    await wrapper.find('textarea').trigger('keydown', { key: 'Enter' })

    expect(wrapper.emitted('submit')).toBeUndefined()
  })
})

describe('ChatInputBox 输入', () => {
  it('输入时发出 update:input', async () => {
    const wrapper = mountBox({ input: '' })

    await wrapper.find('textarea').setValue('汇总销售额')

    expect(wrapper.emitted('update:input')?.[0]).toEqual(['汇总销售额'])
  })

  it('设置 maxlength 时展示字数统计', () => {
    const wrapper = mountBox({ input: 'abc', maxlength: 2000 })

    expect(wrapper.text()).toContain('3/2000')
  })
})

describe('ChatInputBox 粘贴文件', () => {
  const dispatchPaste = (wrapper: ReturnType<typeof mountBox>, clipboardData: { files: File[] } | null) => {
    const event = new Event('paste', { bubbles: true, cancelable: true })
    Object.defineProperty(event, 'clipboardData', { value: clipboardData })
    const preventDefaultSpy = vi.spyOn(event, 'preventDefault')
    wrapper.find('textarea').element.dispatchEvent(event)

    return preventDefaultSpy
  }

  it('剪贴板携带文件时上抛 paste-files 并拦截默认行为', () => {
    const wrapper = mountBox({ input: '' })
    const file = new File(['img'], 'image.png', { type: 'image/png' })

    const preventDefaultSpy = dispatchPaste(wrapper, { files: [file] })

    expect(wrapper.emitted('paste-files')?.[0]).toEqual([[file]])
    expect(preventDefaultSpy).toHaveBeenCalled()
  })

  it('纯文本粘贴不拦截、不发 paste-files', () => {
    const wrapper = mountBox({ input: '' })

    const preventDefaultSpy = dispatchPaste(wrapper, { files: [] })

    expect(wrapper.emitted('paste-files')).toBeUndefined()
    expect(preventDefaultSpy).not.toHaveBeenCalled()
  })

  it('无 clipboardData（非标准环境）时静默放行', () => {
    const wrapper = mountBox({ input: '' })

    const preventDefaultSpy = dispatchPaste(wrapper, null)

    expect(wrapper.emitted('paste-files')).toBeUndefined()
    expect(preventDefaultSpy).not.toHaveBeenCalled()
  })
})
