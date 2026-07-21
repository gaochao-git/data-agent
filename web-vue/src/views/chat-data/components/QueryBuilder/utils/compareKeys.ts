import type { DimensionField } from '../types'

export const padNumber = (value: number, size = 2) => String(value).padStart(size, '0')

export const formatDateKey = (date: Date) => {
  return `${date.getFullYear()}-${padNumber(date.getMonth() + 1)}-${padNumber(date.getDate())}`
}

export const formatMonthKey = (date: Date) => {
  return `${date.getFullYear()}-${padNumber(date.getMonth() + 1)}`
}

export const formatQuarterKey = (year: number, quarter: number) => `${year}-Q${quarter}`

export const parseDateValue = (value: unknown): Date | null => {
  if (value instanceof Date && !Number.isNaN(value.getTime())) return value
  const text = String(value ?? '').trim()
  if (!text) return null
  const normalized = text.replace(/\//g, '-')
  const date = new Date(normalized)
  if (Number.isNaN(date.getTime())) return null
  return new Date(date.getFullYear(), date.getMonth(), date.getDate())
}

export const parseWeekKey = (value: unknown): { year: number; week: number } | null => {
  const text = String(value ?? '').trim().toUpperCase()
  if (!text) return null
  const compact = text.replace(/\s+/g, '')
  let matched = compact.match(/^(\d{4})[-_/]?W?(\d{1,2})$/)
  if (!matched && /^\d{6}$/.test(compact)) {
    matched = compact.match(/^(\d{4})(\d{2})$/)
  }
  if (!matched) return null
  const year = Number(matched[1])
  const week = Number(matched[2])
  if (!Number.isInteger(year) || !Number.isInteger(week) || week < 1 || week > 53) return null
  return { year, week }
}

export const normalizeCompareKey = (value: unknown, transform: DimensionField['transform']) => {
  const text = String(value ?? '').trim()
  if (!text) return null
  switch (transform) {
    case 'year': {
      const year = Number(text)
      return Number.isInteger(year) ? String(year) : null
    }
    case 'month': {
      const date = parseDateValue(text.length <= 7 ? `${text}-01` : text)
      return date ? formatMonthKey(date) : null
    }
    case 'day': {
      const date = parseDateValue(text)
      return date ? formatDateKey(date) : null
    }
    case 'week': {
      const week = parseWeekKey(text)
      return week ? `${week.year}-W${padNumber(week.week)}` : null
    }
    case 'quarter': {
      const matched = text.toUpperCase().match(/^(\d{4})[-_/]?Q([1-4])$/)
      if (!matched) return null
      return formatQuarterKey(Number(matched[1]), Number(matched[2]))
    }
    default:
      return null
  }
}

export const getPreviousCompareKey = (
  normalizedKey: string,
  transform: DimensionField['transform'],
  type: 'mom' | 'yoy'
) => {
  switch (transform) {
    case 'year': {
      const year = Number(normalizedKey)
      if (!Number.isInteger(year)) return null
      return String(year - 1)
    }
    case 'month': {
      const matched = normalizedKey.match(/^(\d{4})-(\d{2})$/)
      if (!matched) return null
      const date = new Date(Number(matched[1]), Number(matched[2]) - 1, 1)
      if (type === 'mom') date.setMonth(date.getMonth() - 1)
      else date.setFullYear(date.getFullYear() - 1)
      return formatMonthKey(date)
    }
    case 'day': {
      const date = parseDateValue(normalizedKey)
      if (!date) return null
      if (type === 'mom') date.setDate(date.getDate() - 1)
      else date.setFullYear(date.getFullYear() - 1)
      return formatDateKey(date)
    }
    case 'week': {
      const matched = normalizedKey.match(/^(\d{4})-W(\d{2})$/)
      if (!matched) return null
      const year = Number(matched[1])
      const week = Number(matched[2])
      if (type === 'yoy') return `${year - 1}-W${padNumber(week)}`
      if (week > 1) return `${year}-W${padNumber(week - 1)}`
      return `${year - 1}-W53`
    }
    case 'quarter': {
      const matched = normalizedKey.match(/^(\d{4})-Q([1-4])$/)
      if (!matched) return null
      const year = Number(matched[1])
      const quarter = Number(matched[2])
      if (type === 'yoy') return formatQuarterKey(year - 1, quarter)
      if (quarter > 1) return formatQuarterKey(year, quarter - 1)
      return formatQuarterKey(year - 1, 4)
    }
    default:
      return null
  }
}
