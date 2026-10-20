import { describe, expect, it } from 'vitest'
import {
  MAX_QUOTED_SNIPPETS,
  MAX_QUOTED_SNIPPET_CHARS,
  normalizeQuotedText,
  useQuotedSnippets,
} from '../useQuotedSnippets'

describe('normalizeQuotedText', () => {
  it('去首尾空白并压缩 3+ 连续换行', () => {
    expect(normalizeQuotedText('  第一行\n\n\n\n第二行  ')).toBe('第一行\n\n第二行')
    expect(normalizeQuotedText('   ')).toBe('')
    expect(normalizeQuotedText('')).toBe('')
  })

  it('超长片段截断并追加省略号', () => {
    const long = '字'.repeat(MAX_QUOTED_SNIPPET_CHARS + 10)
    const normalized = normalizeQuotedText(long)
    expect(normalized).toHaveLength(MAX_QUOTED_SNIPPET_CHARS + 1)
    expect(normalized.endsWith('…')).toBe(true)
  })
})

describe('useQuotedSnippets', () => {
  it('添加片段：生成 id、去重同文本、拒绝空文本', () => {
    const { quotedSnippets, addQuotedSnippet } = useQuotedSnippets()

    expect(addQuotedSnippet('第一段引用')).toBe(true)
    expect(addQuotedSnippet('第一段引用')).toBe(false)
    expect(addQuotedSnippet('   ')).toBe(false)
    expect(quotedSnippets.value).toHaveLength(1)
    expect(quotedSnippets.value[0]).toMatchObject({ text: '第一段引用' })
    expect(quotedSnippets.value[0]!.id).toBeTruthy()
  })

  it('条数达到上限后拒绝新增', () => {
    const { quotedSnippets, addQuotedSnippet } = useQuotedSnippets()

    for (let index = 0; index < MAX_QUOTED_SNIPPETS; index += 1) {
      expect(addQuotedSnippet(`片段 ${index}`)).toBe(true)
    }
    expect(addQuotedSnippet('超出上限的片段')).toBe(false)
    expect(quotedSnippets.value).toHaveLength(MAX_QUOTED_SNIPPETS)
  })

  it('移除与清空', () => {
    const { quotedSnippets, addQuotedSnippet, removeQuotedSnippet, clearQuotedSnippets } = useQuotedSnippets()

    addQuotedSnippet('片段 A')
    addQuotedSnippet('片段 B')
    const removed = quotedSnippets.value[0]!
    removeQuotedSnippet(removed.id)
    expect(quotedSnippets.value.map(item => item.text)).toEqual(['片段 B'])

    clearQuotedSnippets()
    expect(quotedSnippets.value).toEqual([])
  })
})
