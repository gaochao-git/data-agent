import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import AsyncQueryResult from '../AsyncQueryResult.vue'

describe('AsyncQueryResult', () => {
  it('renders SQL errors inline and expands the complete ANSI-free detail on demand', async () => {
    const fullError = '\u001b[4mInvalid expression\u001b[0m near LIMIT1\nPlease use LIMIT 100'
    const wrapper = mount(AsyncQueryResult, {
      props: {
        resultData: JSON.stringify({
          status: 'failed',
          sql: 'select * from orders limit1 100',
          error: fullError,
          columns: [],
          data: [],
          total_count: 0,
        }),
      },
      global: {
        stubs: {
          'el-dialog': true,
        },
      },
    })

    expect(wrapper.text()).toContain('Invalid expression near LIMIT1')
    expect(wrapper.text()).not.toContain('Please use LIMIT 100')
    expect(wrapper.text()).not.toContain('\u001b[4m')
    expect(wrapper.text()).toContain('失败')
    expect(wrapper.text()).not.toContain('成功')
    expect(wrapper.text()).not.toContain('共 0 条')
    expect(wrapper.get('[data-testid="toggle-query-error"]').attributes('aria-expanded')).toBe('false')

    await wrapper.get('[data-testid="toggle-query-error"]').trigger('click')

    expect(wrapper.text()).toContain('Please use LIMIT 100')
    expect(wrapper.text()).not.toContain('\u001b[4m')
    expect(wrapper.get('[data-testid="toggle-query-error"]').attributes('aria-expanded')).toBe('true')
  })
})
