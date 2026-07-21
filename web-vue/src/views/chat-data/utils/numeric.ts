const NUMERIC_TEXT_PATTERN = /^-?(?:\d+|\d*\.\d+)$/

const normalizeDecimalText = (value: string): string => {
  const isNegative = value.startsWith('-')
  const unsignedValue = isNegative ? value.slice(1) : value
  const [integerPart = '', fractionPart = ''] = unsignedValue.split('.')
  const normalizedInteger = integerPart.replace(/^0+/, '') || '0'
  const normalizedFraction = fractionPart.replace(/0+$/, '')
  const normalizedValue = normalizedFraction
    ? `${normalizedInteger}.${normalizedFraction}`
    : normalizedInteger

  return isNegative && normalizedValue !== '0' ? `-${normalizedValue}` : normalizedValue
}

export const isNumericValue = (value: unknown): boolean => {
  if (typeof value === 'number') return Number.isFinite(value)
  return typeof value === 'string' && NUMERIC_TEXT_PATTERN.test(value.trim())
}

export const toSafeNumber = (value: unknown): number | null => {
  if (typeof value === 'number') {
    if (!Number.isFinite(value) || Math.abs(value) > Number.MAX_SAFE_INTEGER) return null
    return value
  }
  if (typeof value !== 'string') return null

  const text = value.trim()
  if (!isNumericValue(text)) return null

  const normalized = normalizeDecimalText(text)
  const parsed = Number(normalized)
  if (!Number.isFinite(parsed) || Math.abs(parsed) > Number.MAX_SAFE_INTEGER) return null

  const fractionLength = normalized.split('.')[1]?.length ?? 0
  if (fractionLength === 0) return Number.isSafeInteger(parsed) ? parsed : null
  if (fractionLength > 100) return null

  // Charts may only use decimals that can reproduce the original value at its source scale.
  if (normalizeDecimalText(parsed.toFixed(fractionLength)) !== normalized) return null

  return parsed
}

export const isSafeNumericValue = (value: unknown): boolean => toSafeNumber(value) !== null

export const getSafeNumericColumns = (
  rows: Record<string, unknown>[],
  columns: string[]
): string[] => {
  return columns.filter((column) => {
    const values = rows
      .map(row => row[column])
      .filter(value => value !== null && value !== undefined && String(value).trim() !== '')
    return values.length > 0 && values.every(value => toSafeNumber(value) !== null)
  })
}
