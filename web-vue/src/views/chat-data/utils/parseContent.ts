/**
 * 解析结构化内容
 * 支持 <thinking>、多个sql代码块、多个<explanation> 标签
 */

// 内容片段类型
export type ContentSegment =
  | { type: 'thinking'; content: string; inProgress?: boolean }
  | { type: 'semanticParams'; content: string; inProgress?: boolean }
  | { type: 'sql'; sql: string; explanation?: string; sqlInProgress?: boolean; explanationInProgress?: boolean }
  | { type: 'text'; content: string }
  | { type: 'queryResult'; resultId: string }
  | { type: 'chart'; chartId: string }

export interface ParsedContent {
  segments: ContentSegment[]
  raw: string
}

interface StructuredElement {
  type: 'thinking' | 'semanticParams' | 'sql' | 'queryResult' | 'chart' | 'explanation'
  content: string
  startIndex: number
  endIndex: number
  inProgress?: boolean
}

export function parseStructuredContent(content: string, isStreaming = false): ParsedContent {
  // 空值检查
  if (!content) {
    return { raw: '', segments: [] }
  }

  const result: ParsedContent = { raw: content, segments: [] }
  const elements: StructuredElement[] = []

  // 1. 提取 thinking
  const thinkingRegex = /<thinking>([\s\S]*?)<\/thinking>/g
  let match: RegExpExecArray | null
  while ((match = thinkingRegex.exec(content)) !== null) {
    const matchedContent = match[1]
    if (matchedContent !== undefined) {
      elements.push({
        type: 'thinking',
        content: matchedContent.trim(),
        startIndex: match.index,
        endIndex: match.index + match[0].length
      })
    }
  }
  // 流式：检查未闭合的 thinking
  if (isStreaming) {
    const unclosedThinking = content.match(/<thinking>([\s\S]*?)$/)
    const unclosedContent = unclosedThinking?.[1]
    if (unclosedThinking && unclosedThinking.index !== undefined && unclosedContent !== undefined && !content.includes('</thinking>')) {
      elements.push({
        type: 'thinking',
        content: unclosedContent.trim(),
        startIndex: unclosedThinking.index,
        endIndex: content.length,
        inProgress: true
      })
    }
  }

  // 2. 提取 semantic_params
  const semanticParamsRegex = /<semantic_params>([\s\S]*?)<\/semantic_params>/g
  while ((match = semanticParamsRegex.exec(content)) !== null) {
    const matchedContent = match[1]
    if (matchedContent !== undefined) {
      elements.push({
        type: 'semanticParams',
        content: matchedContent.trim(),
        startIndex: match.index,
        endIndex: match.index + match[0].length
      })
    }
  }
  // 流式：检查未闭合的 semantic_params
  if (isStreaming) {
    const unclosedParams = content.match(/<semantic_params>([\s\S]*?)$/)
    const unclosedContent = unclosedParams?.[1]
    if (unclosedParams && unclosedParams.index !== undefined && unclosedContent !== undefined && !content.includes('</semantic_params>')) {
      elements.push({
        type: 'semanticParams',
        content: unclosedContent.trim(),
        startIndex: unclosedParams.index,
        endIndex: content.length,
        inProgress: true
      })
    }
  }

  // 3. 提取 sql 代码块
  const sqlRegex = /```sql\s*([\s\S]*?)```/g
  while ((match = sqlRegex.exec(content)) !== null) {
    const matchedContent = match[1]
    if (matchedContent !== undefined) {
      elements.push({
        type: 'sql',
        content: matchedContent.trim(),
        startIndex: match.index,
        endIndex: match.index + match[0].length
      })
    }
  }
  // 流式：检查未闭合的 sql
  if (isStreaming) {
    const sqlStarts = (content.match(/```sql/g) || []).length
    const codeEnds = (content.match(/```(?!sql)/g) || []).length
    if (sqlStarts > codeEnds) {
      const lastSqlStart = content.lastIndexOf('```sql')
      const sqlContent = content.substring(lastSqlStart + 6)
      elements.push({
        type: 'sql',
        content: sqlContent.trim(),
        startIndex: lastSqlStart,
        endIndex: content.length,
        inProgress: true
      })
    }
  }

  // 4. 提取 query_result
  const queryResultRegex = /<query_result>([\s\S]*?)<\/query_result>/g
  while ((match = queryResultRegex.exec(content)) !== null) {
    const matchedContent = match[1]
    if (matchedContent !== undefined) {
      elements.push({
        type: 'queryResult',
        content: matchedContent.trim(),
        startIndex: match.index,
        endIndex: match.index + match[0].length
      })
    }
  }

  // 5. 提取 chart_config
  const chartConfigRegex = /<chart_config>([\s\S]*?)<\/chart_config>/g
  while ((match = chartConfigRegex.exec(content)) !== null) {
    const matchedContent = match[1]
    if (matchedContent !== undefined) {
      elements.push({
        type: 'chart',
        content: matchedContent,
        startIndex: match.index,
        endIndex: match.index + match[0].length
      })
    }
  }

  // 6. 提取 explanation
  const explanationRegex = /<explanation>([\s\S]*?)<\/explanation>/g
  while ((match = explanationRegex.exec(content)) !== null) {
    const matchedContent = match[1]
    if (matchedContent !== undefined) {
      elements.push({
        type: 'explanation',
        content: matchedContent.trim(),
        startIndex: match.index,
        endIndex: match.index + match[0].length
      })
    }
  }
  // 流式：检查未闭合的 explanation
  if (isStreaming) {
    const unclosedExplanation = content.match(/<explanation>([\s\S]*?)$/)
    const unclosedContent = unclosedExplanation?.[1]
    if (unclosedExplanation && unclosedExplanation.index !== undefined && unclosedContent !== undefined) {
      const afterIndex = content.slice(unclosedExplanation.index)
      if (!/<explanation>[\s\S]*?<\/explanation>/.test(afterIndex)) {
        elements.push({
          type: 'explanation',
          content: unclosedContent.trim(),
          startIndex: unclosedExplanation.index,
          endIndex: content.length,
          inProgress: true
        })
      }
    }
  }

  // 按位置排序
  elements.sort((a, b) => a.startIndex - b.startIndex)

  // 构建 segments：在结构化元素之间插入文本片段
  let lastIndex = 0

  // 辅助函数：清理文本内容
  const cleanText = (text: string): string => {
    // 移除工具调用提示
    let cleaned = text.replace(/\[查询完成\][\s\S]*?请勿重复描述数据内容。/g, '')
    // cleaned = cleaned.replace(/查询结果已生成[。.]?/g, '') // 已改为输出简要说明
    // 清理多余空行
    cleaned = cleaned.replace(/\n{3,}/g, '\n\n').trim()
    return cleaned
  }

  for (const elem of elements) {
    // 添加前面的文本片段
    if (elem.startIndex > lastIndex) {
      const textContent = cleanText(content.slice(lastIndex, elem.startIndex))
      if (textContent) {
        result.segments.push({ type: 'text', content: textContent })
      }
    }

    // 添加结构化元素
    if (elem.type === 'thinking') {
      result.segments.push({
        type: 'thinking',
        content: elem.content,
        inProgress: elem.inProgress
      })
    } else if (elem.type === 'semanticParams') {
      result.segments.push({
        type: 'semanticParams',
        content: elem.content,
        inProgress: elem.inProgress
      })
    }
    // sql、explanation、queryResult、chart 在工具卡片内渲染，这里只是从文本中移除

    lastIndex = elem.endIndex
  }

  // 添加最后的文本片段
  if (lastIndex < content.length) {
    const textContent = cleanText(content.slice(lastIndex))
    if (textContent) {
      result.segments.push({ type: 'text', content: textContent })
    }
  }

  return result
}
