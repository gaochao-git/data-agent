export const normalizeMarkdownFences = (content: string): string => {
  return String(content || '')
    .replace(/([^\n])(```[\w-]*\n)/g, '$1\n$2')
}

const parseJsonObject = (raw: string): Record<string, unknown> | null => {
  const text = String(raw || '').trim().replace(/^json\s*\n/i, '').trim()
  if (!text) return null
  try {
    const payload = JSON.parse(text)
    return payload && typeof payload === 'object' && !Array.isArray(payload)
      ? payload as Record<string, unknown>
      : null
  } catch {
    return null
  }
}

const isSemanticConfigSelectionPayload = (payload: Record<string, unknown>): boolean => {
  return (
    Object.prototype.hasOwnProperty.call(payload, 'semantic_config_ids')
    || Object.prototype.hasOwnProperty.call(payload, 'semantic_config_directory')
  )
}

const findJsonObjectEnd = (text: string, start: number): number => {
  let depth = 0
  let inString = false
  let escaped = false
  for (let index = start; index < text.length; index += 1) {
    const char = text[index]
    if (inString) {
      if (escaped) {
        escaped = false
      } else if (char === '\\') {
        escaped = true
      } else if (char === '"') {
        inString = false
      }
      continue
    }
    if (char === '"') {
      inString = true
      continue
    }
    if (char === '{') {
      depth += 1
      continue
    }
    if (char === '}') {
      depth -= 1
      if (depth === 0) return index + 1
    }
  }
  return -1
}

const hideTaggedSemanticConfigSelectionPayloads = (content: string): string => {
  const markers = ['[search_dataset]']
  let text = content
  let searchFrom = 0
  while (searchFrom < text.length) {
    const matches = markers
      .map(marker => ({ marker, index: text.indexOf(marker, searchFrom) }))
      .filter(item => item.index >= 0)
      .sort((left, right) => left.index - right.index)
    const match = matches[0]
    if (!match) break
    const markerIndex = match.index
    let jsonStart = markerIndex + match.marker.length
    while (jsonStart < text.length && /\s/.test(text[jsonStart] || '')) {
      jsonStart += 1
    }
    if (text[jsonStart] !== '{') {
      searchFrom = markerIndex + match.marker.length
      continue
    }
    const jsonEnd = findJsonObjectEnd(text, jsonStart)
    if (jsonEnd < 0) break
    const payload = parseJsonObject(text.slice(jsonStart, jsonEnd))
    if (!payload || !isSemanticConfigSelectionPayload(payload)) {
      searchFrom = jsonEnd
      continue
    }
    text = `${text.slice(0, markerIndex)}${text.slice(jsonEnd)}`
    searchFrom = markerIndex
  }
  return text
}

export const hideSemanticConfigSelectionPayloads = (content: string): string => {
  return hideTaggedSemanticConfigSelectionPayloads(normalizeMarkdownFences(content))
    .replace(/```(?:json)?\s*\n([\s\S]*?)```/gi, (full, rawJson) => {
      const payload = parseJsonObject(rawJson)
      return payload && isSemanticConfigSelectionPayload(payload) ? '' : full
    })
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}
