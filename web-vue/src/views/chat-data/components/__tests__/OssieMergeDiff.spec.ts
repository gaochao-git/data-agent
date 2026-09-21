import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

const mergeMocks = vi.hoisted(() => ({
  MergeView: vi.fn(),
}))

// jsdom 不真跑 CodeMirror：MergeView 只断言构造入参，其余扩展 API 置空
vi.mock('@codemirror/merge', () => ({ MergeView: mergeMocks.MergeView }))
vi.mock('@codemirror/view', () => ({
  EditorView: { theme: () => [], editable: { of: () => [] }, lineWrapping: [] },
  lineNumbers: () => ['line-numbers-ext'],
}))
vi.mock('@codemirror/state', () => ({ EditorState: { readOnly: { of: () => [] } } }))
vi.mock('@codemirror/lang-yaml', () => ({ yaml: () => [] }))
vi.mock('../../utils/yamlDump', () => ({ toYaml: (value: unknown) => JSON.stringify(value) }))

import OssieMergeDiff from '../semantic/OssieMergeDiff.vue'

describe('OssieMergeDiff', () => {
  it('builds a read-only MergeView with line numbers on both sides', () => {
    mount(OssieMergeDiff, {
      props: {
        existing: { id: 'sc_old', name: '商品模型', metrics: [], dimensions: [], relationships: [] } as never,
        imported: { name: '商品模型', metrics: [], dimensions: [], relationships: [] } as never,
      },
    })

    expect(mergeMocks.MergeView).toHaveBeenCalledTimes(1)
    const options = mergeMocks.MergeView.mock.calls[0]?.[0]
    // 行号必须显式注入（MergeView 手动拼 extensions，无 basicSetup 兜底）
    expect(options?.a.extensions).toContainEqual(['line-numbers-ext'])
    expect(options?.b.extensions).toContainEqual(['line-numbers-ext'])
    expect(options?.collapseUnchanged).toEqual({ margin: 3, minSize: 8 })
  })
})
