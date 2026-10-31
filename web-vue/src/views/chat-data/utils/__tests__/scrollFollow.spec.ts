import { describe, expect, it } from 'vitest'
import { isScrolledToBottom, NEAR_BOTTOM_THRESHOLD_PX } from '../scrollFollow'

const scrollElement = (scrollHeight: number, scrollTop: number, clientHeight: number) => ({
  scrollHeight,
  scrollTop,
  clientHeight,
})

describe('scrollFollow', () => {
  it('距底小于阈值算贴底，主对话与子智能体共用 100px 口径', () => {
    expect(NEAR_BOTTOM_THRESHOLD_PX).toBe(100)
    expect(isScrolledToBottom(scrollElement(1_000, 700, 200))).toBe(false) // 距底 100，不小于阈值
    expect(isScrolledToBottom(scrollElement(1_000, 701, 200))).toBe(true)
    expect(isScrolledToBottom(scrollElement(1_000, 800, 200))).toBe(true) // 已到底
    expect(isScrolledToBottom(scrollElement(1_000, 0, 200))).toBe(false)
  })

  it('支持自定义阈值（贴底判定的唯一来源）', () => {
    expect(isScrolledToBottom(scrollElement(1_000, 700, 200), 50)).toBe(false) // 距底 100
    expect(isScrolledToBottom(scrollElement(1_000, 700, 200), 101)).toBe(true)
  })
})
