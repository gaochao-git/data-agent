import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import HtmlPagePreview from '../conversation/HtmlPagePreview.vue'

describe('HtmlPagePreview', () => {
  it('renders sandbox iframe with hardened document for non-empty html', () => {
    const wrapper = mount(HtmlPagePreview, {
      props: { html: '<!DOCTYPE html><html><head><title>t</title></head><body>ok</body></html>' },
    })
    const iframe = wrapper.find('iframe')
    expect(iframe.exists()).toBe(true)
    expect(iframe.attributes('sandbox')).toBe('allow-scripts')
    expect(iframe.attributes('referrerpolicy')).toBe('no-referrer')
    const srcdoc = iframe.attributes('srcdoc') || ''
    expect(srcdoc).toContain('Content-Security-Policy')
    // CSP 注入点在原 head 内部闭合之前
    expect(srcdoc.indexOf('Content-Security-Policy')).toBeLessThan(srcdoc.indexOf('</head>'))
  })

  it('does not render iframe for empty html', () => {
    const wrapper = mount(HtmlPagePreview, { props: { html: '  ' } })
    expect(wrapper.find('iframe').exists()).toBe(false)
  })
})
