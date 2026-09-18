import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'

import CodeFilePreview from '../CodeFilePreview.vue'

const SAMPLE_YAML = [
  'version: 0.2.0.dev0',
  'semantic_model:',
  '  - name: 用户模型',
  '    datasets:',
  '      - name: users',
  '        primary_key: []',
].join('\n')

describe('CodeFilePreview', () => {
  it('renders yaml content through the CodeMirror viewer with line numbers and readonly mode', async () => {
    const wrapper = mount(CodeFilePreview, {
      props: { content: SAMPLE_YAML },
    })
    await nextTick()

    const editor = wrapper.find('.cm-editor')
    expect(editor.exists()).toBe(true)
    expect(wrapper.find('.cm-lineNumbers').exists()).toBe(true)
    // 只读：内容层不可编辑
    const content = wrapper.find('.cm-content')
    expect(content.attributes('contenteditable')).toBe('false')
  })

  it('wraps yaml tokens in styled spans via the default highlight style (not a plain text dump)', async () => {
    const wrapper = mount(CodeFilePreview, {
      props: { content: SAMPLE_YAML },
    })
    await nextTick()

    // 语法高亮来自 vue-codemirror 注入的 basicSetup defaultHighlightStyle（哈希类名，
    // 不产出 cm-propertyName 等语义类）；有 token 被 span 包裹即代表高亮生效，
    // 纯文本 dump 时行内容是裸文本节点、没有 span。
    const tokenSpans = wrapper.findAll('.cm-line span')
    expect(tokenSpans.length).toBeGreaterThan(0)
  })

  it('re-renders when content changes', async () => {
    const wrapper = mount(CodeFilePreview, {
      props: { content: 'a: 1' },
    })
    await nextTick()
    expect(wrapper.findAll('.cm-line').length).toBe(1)

    await wrapper.setProps({ content: 'a: 1\nb: 2\nc: 3' })
    await nextTick()
    expect(wrapper.findAll('.cm-line').length).toBe(3)
  })
})
