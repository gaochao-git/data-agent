import { describe, expect, it } from 'vitest'

import { formatEpochDateTime, formatRelativeConversationTime } from '../dateTime'

const buildNow = (): Date => new Date('2026-08-28T12:00:00')

const minutesAgo = (minutes: number): string => {
  const date = new Date(buildNow().getTime() - minutes * 60 * 1000)
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

describe('formatRelativeConversationTime', () => {

  it.each([
    { label: '30 秒前显示刚刚', value: minutesAgo(0.5), expected: '刚刚' },
    { label: '5 分钟前', value: minutesAgo(5), expected: '5分钟前' },
    { label: '59 分钟前仍算分钟档', value: minutesAgo(59), expected: '59分钟前' },
    { label: '3 小时前', value: minutesAgo(3 * 60), expected: '3小时前' },
    { label: '23 小时前仍算小时档', value: minutesAgo(23 * 60), expected: '23小时前' },
    { label: '3 天前', value: minutesAgo(3 * 24 * 60), expected: '3天前' },
    { label: '7 天前仍是天档上限', value: minutesAgo(7 * 24 * 60), expected: '7天前' },
  ])('$label', ({ value, expected }) => {
    expect(formatRelativeConversationTime(value, buildNow())).toBe(expected)
  })

  it('beyond 7 days falls back to MM-DD without the year', () => {
    expect(formatRelativeConversationTime('2026-07-14 09:30:00', buildNow())).toBe('07-14')
  })

  it('parses datetime-local style strings the same as spaced strings', () => {
    expect(formatRelativeConversationTime('2026-08-28T11:55:00', buildNow())).toBe('5分钟前')
  })

  it('returns empty string for missing or unparseable values so the caller can hide the span', () => {
    expect(formatRelativeConversationTime(undefined, buildNow())).toBe('')
    expect(formatRelativeConversationTime('', buildNow())).toBe('')
    expect(formatRelativeConversationTime('not-a-date', buildNow())).toBe('')
  })
})

describe('formatEpochDateTime（秒级时间戳，工作区文件的 st_mtime）', () => {
  // 用本地时间字符串反推时间戳，避免测试依赖运行环境的时区
  const secondsOf = (localText: string): number =>
    Math.floor(new Date(localText.replace(' ', 'T')).getTime() / 1000)

  it('输出 YYYY-MM-DD HH:mm:ss 本地时间', () => {
    expect(formatEpochDateTime(secondsOf('2026-09-11 14:32:05'))).toBe('2026-09-11 14:32:05')
    expect(formatEpochDateTime(secondsOf('2025-11-01 09:05:00'))).toBe('2025-11-01 09:05:00')
  })

  it('每行等宽：个位数的月/日/时/分/秒都补零', () => {
    const text = formatEpochDateTime(secondsOf('2026-01-02 03:04:05'))
    expect(text).toBe('2026-01-02 03:04:05')
    expect(text).toHaveLength(19)
  })

  it('st_mtime 的小数部分被截断（秒级精度足够）', () => {
    expect(formatEpochDateTime(secondsOf('2026-09-11 14:32:05') + 0.87)).toBe('2026-09-11 14:32:05')
  })

  it('0 / 负数 / 非有限值 / 空值返回空串，不显示成 1970-01-01', () => {
    expect(formatEpochDateTime(0)).toBe('')
    expect(formatEpochDateTime(-1)).toBe('')
    expect(formatEpochDateTime(Number.NaN)).toBe('')
    expect(formatEpochDateTime(Number.POSITIVE_INFINITY)).toBe('')
    expect(formatEpochDateTime(null)).toBe('')
    expect(formatEpochDateTime(undefined)).toBe('')
  })
})
