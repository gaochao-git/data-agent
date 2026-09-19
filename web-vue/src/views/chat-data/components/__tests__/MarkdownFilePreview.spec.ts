import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import MarkdownFilePreview from '../conversation/MarkdownFilePreview.vue'

describe('MarkdownFilePreview', () => {
  it('renders Markdown structure without enabling raw HTML', () => {
    const wrapper = mount(MarkdownFilePreview, {
      props: {
        content: '# 报告\n\n| 指标 | 数值 |\n| --- | --- |\n| 订单数 | 12 |\n\n<script>alert(1)</script>',
      },
    })

    expect(wrapper.find('h1').text()).toBe('报告')
    expect(wrapper.find('table').text()).toContain('订单数')
    expect(wrapper.find('script').exists()).toBe(false)
    expect(wrapper.text()).toContain('<script>alert(1)</script>')
  })
})
