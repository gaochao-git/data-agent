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

export const buildRecentDaysDateTimeRange = (days: number): [string, string] =>
  formatDateTimeRange(buildRecentDaysRange(days))

// 以下返回 [Date, Date],供"最近X分钟/小时/天"快捷按钮使用
export const buildRecentMinutesRange = (minutes: number): [Date, Date] => {
  const end = new Date()
  const start = new Date(end)
  start.setMinutes(start.getMinutes() - minutes)
  return [start, end]
}

export const buildRecentHoursRange = (hours: number): [Date, Date] => {
  const end = new Date()
  const start = new Date(end)
  start.setHours(start.getHours() - hours)
  return [start, end]
}

export const buildRecentDaysRange = (days: number): [Date, Date] => {
  const end = new Date()
  const start = new Date(end)
  start.setDate(start.getDate() - days)
  return [start, end]
}

// 将 [Date, Date] 区间格式化为 el-date-picker 字符串值
export const formatDateTimeRange = (range: [Date, Date]): [string, string] => [
  formatLocalDateTime(range[0]),
  formatLocalDateTime(range[1])
]

const MINUTE_SECONDS = 60
const HOUR_SECONDS = 60 * MINUTE_SECONDS
const DAY_SECONDS = 24 * HOUR_SECONDS
const RELATIVE_FALLBACK_DAYS = 7

/**
 * 会话列表行的相对时间：刚刚 / N分钟前 / N小时前 / N天前，超过 7 天回落 MM-DD。
 * 面板宽度紧张，超龄不显示年份；解析失败返回空串由调用方隐藏。
 */
export const formatRelativeConversationTime = (value?: Date | string | null, now: Date = new Date()): string => {
  const date = parseDateTimeValue(value)
  if (!date) return ''
  const elapsedSeconds = Math.floor((now.getTime() - date.getTime()) / 1000)
  if (elapsedSeconds < MINUTE_SECONDS) return '刚刚'
  if (elapsedSeconds < HOUR_SECONDS) return `${Math.floor(elapsedSeconds / MINUTE_SECONDS)}分钟前`
  if (elapsedSeconds < DAY_SECONDS) return `${Math.floor(elapsedSeconds / HOUR_SECONDS)}小时前`
  const elapsedDays = Math.floor(elapsedSeconds / DAY_SECONDS)
  if (elapsedDays <= RELATIVE_FALLBACK_DAYS) return `${elapsedDays}天前`
  return `${padDateTimePart(date.getMonth() + 1)}-${padDateTimePart(date.getDate())}`
}

/**
 * 秒级 Unix 时间戳（后端 `os.stat().st_mtime` 的口径）→ `YYYY-MM-DD HH:mm:ss` 本地时间。
 *
 * 工作区文件行用绝对时间而非相对时间：文件是版本对照的场景，需要能直接比出先后与具体时刻。
 * 该格式每行等宽（19 字符），所以时间列不需要额外钉宽度就能对齐。
 * 0 / 负数 / 非有限值一律返回空串：这些值代表「没有时间信息」，
 * 换算成 1970 年会显示成 1970-01-01 00:00:00，比不显示更容易误导。
 */
export const formatEpochDateTime = (seconds?: number | null): string => {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds <= 0) return ''
  return formatLocalDateTime(new Date(seconds * 1000))
}
