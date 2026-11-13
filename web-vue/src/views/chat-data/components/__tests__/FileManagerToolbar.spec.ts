import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import FileManagerToolbar from '../conversation/FileManagerToolbar.vue'

const mountToolbar = (refreshing = false) => mount(FileManagerToolbar, {
  props: {
    root: '/mnt/user-data',
    refreshing,
  },
})

describe('FileManagerToolbar refresh action', () => {
  it('exposes a refresh button and emits refresh on click', async () => {
    const wrapper = mountToolbar()
    const button = wrapper.find('[data-console-refresh="true"]')

    expect(button.exists()).toBe(true)
    expect(button.attributes('aria-label')).toBe('刷新文件和预览')
    expect(button.attributes('title')).toBe('刷新文件和预览')
    expect(button.attributes('disabled')).toBeUndefined()
    expect(button.find('i').classes()).not.toContain('fa-spin')

    await button.trigger('click')

    expect(wrapper.emitted('refresh')).toHaveLength(1)
  })

  it('disables and spins while a refresh is in flight', () => {
    const wrapper = mountToolbar(true)
    const button = wrapper.find('[data-console-refresh="true"]')

    expect(button.attributes('disabled')).toBeDefined()
    expect(button.attributes('title')).toBe('正在刷新文件和预览')
    expect(button.find('i').classes()).toContain('fa-spin')
  })
})
