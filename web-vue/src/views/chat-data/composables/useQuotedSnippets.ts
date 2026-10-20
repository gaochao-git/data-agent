import { ref } from 'vue'

/** 输入框待发送的引用片段：来自助手消息划选（复制/添加到对话浮层）。 */
export interface QuotedSnippet {
  id: string
  text: string
}

/** 引用片段条数上限：chip 行高度可控，超出引导用户先发送。 */
export const MAX_QUOTED_SNIPPETS = 5
/** 单条片段字符上限：划选可能覆盖整条回复，超长截断并追加省略号（服务端同上限兜底）。 */
export const MAX_QUOTED_SNIPPET_CHARS = 2000

/** 归一化划选文本：去首尾空白、压缩 3+ 连续换行、超长截断。 */
export const normalizeQuotedText = (raw: string): string => {
  const text = String(raw || '').replace(/\n{3,}/g, '\n\n').trim()
  if (text.length <= MAX_QUOTED_SNIPPET_CHARS) return text
  return `${text.slice(0, MAX_QUOTED_SNIPPET_CHARS)}…`
}

/**
 * 助手消息划选引用：划选片段 → 浮层「添加到对话」→ chip 列表。
 * 发送时按快照随请求透传服务端（quoted_snippets 结构化通道），由后端在
 * Human 消息统一追加模型可见标记块并持久化，气泡 chip 与历史回放同构。
 * 同文本去重、条数与单条长度有上限，全部在前端收敛。
 */
export const useQuotedSnippets = () => {
  const quotedSnippets = ref<QuotedSnippet[]>([])
  let snippetSeq = 0

  const addQuotedSnippet = (raw: string): boolean => {
    const text = normalizeQuotedText(raw)
    if (!text) return false
    if (quotedSnippets.value.some(item => item.text === text)) return false
    if (quotedSnippets.value.length >= MAX_QUOTED_SNIPPETS) return false
    snippetSeq += 1
    quotedSnippets.value = [...quotedSnippets.value, { id: `quote-${snippetSeq}`, text }]
    return true
  }

  const removeQuotedSnippet = (id: string) => {
    quotedSnippets.value = quotedSnippets.value.filter(item => item.id !== id)
  }

  const clearQuotedSnippets = () => {
    quotedSnippets.value = []
  }

  return {
    quotedSnippets,
    addQuotedSnippet,
    removeQuotedSnippet,
    clearQuotedSnippets,
  }
}
