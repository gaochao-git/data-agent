const ANSI_ESCAPE_PATTERN = /\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])/g
const BRACKET_ANSI_PATTERN = /\[(?:\d{1,3};)*\d{1,3}m/g

const normalizeWhitespace = (value: string) => value.replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim()

export const cleanDisplayError = (value?: string | null, fallback = '暂无错误信息') => {
  const rawText = String(value || '').replace(/\r\n?/g, '\n')
  const text = rawText
    .replace(ANSI_ESCAPE_PATTERN, '')
    .replace(BRACKET_ANSI_PATTERN, '')
  return normalizeWhitespace(text) || fallback
}

export const stripAnsiErrorText = (value?: string | null, fallback = '暂无错误信息') => {
  const rawText = String(value || '').replace(/\r\n?/g, '\n')
  return rawText
    .replace(ANSI_ESCAPE_PATTERN, '')
    .replace(BRACKET_ANSI_PATTERN, '')
    .trim() || fallback
}

export const normalizeErrorKey = (value?: string | null) => cleanDisplayError(value, '').toLowerCase()

export const dedupeRepeatedErrorText = (value?: string | null, fallback = '查询失败') => {
  const text = cleanDisplayError(value, '')
  if (!text) return fallback
  const parts = text
    .split(/[;\n]+/)
    .map(item => normalizeWhitespace(item))
    .filter(Boolean)
  if (parts.length <= 1) return text

  const seen = new Set<string>()
  const uniqueParts: string[] = []
  parts.forEach((part) => {
    const key = part.toLowerCase()
    if (seen.has(key)) return
    seen.add(key)
    uniqueParts.push(part)
  })
  return uniqueParts.join('；') || fallback
}
