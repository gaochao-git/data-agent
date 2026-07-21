export const ensureArrayValues = (value: unknown): string[] => {
  if (Array.isArray(value)) {
    return value.map(v => String(v)).filter(Boolean)
  }
  if (typeof value === 'string') {
    return value.split(',').map(v => v.trim()).filter(Boolean)
  }
  return []
}

export const parseManualValues = (text: string, delimiter: ',' | ';' | ' ' | '\n') => {
  if (!text.trim()) return []
  if (delimiter === '\n') {
    return text.split(/\r?\n/).map(v => v.trim()).filter(Boolean)
  }
  if (delimiter === ' ') {
    return text.split(/\s+/).map(v => v.trim()).filter(Boolean)
  }
  return text.split(delimiter).map(v => v.trim()).filter(Boolean)
}
