const padDateTimePart = (value: number): string => String(value).padStart(2, '0')

export const parseDateTimeValue = (value?: Date | string | null): Date | null => {
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : value
  }
  if (typeof value === 'string' && value.trim()) {
    const parsed = new Date(value.trim().replace(' ', 'T'))
    return Number.isNaN(parsed.getTime()) ? null : parsed
  }
  return null
}

export const formatLocalDateTime = (date: Date): string => {
  const year = date.getFullYear()
  const month = padDateTimePart(date.getMonth() + 1)
  const day = padDateTimePart(date.getDate())
  const hours = padDateTimePart(date.getHours())
  const minutes = padDateTimePart(date.getMinutes())
  const seconds = padDateTimePart(date.getSeconds())
  return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`
}

export const formatDateTimeValue = (value?: Date | string | null): string => {
  const date = parseDateTimeValue(value)
  return date ? formatLocalDateTime(date) : ''
}

export const buildRecentDaysDateTimeRange = (days: number): [string, string] => {
  const end = new Date()
  const start = new Date(end)
  start.setDate(start.getDate() - days)
  return [formatLocalDateTime(start), formatLocalDateTime(end)]
}
