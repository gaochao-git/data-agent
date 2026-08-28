import { describe, expect, it } from 'vitest'

import { formatRelativeConversationTime } from '../dateTime'

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
