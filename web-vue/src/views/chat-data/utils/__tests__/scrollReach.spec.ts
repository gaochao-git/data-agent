import { describe, expect, it } from 'vitest'

import { NEAR_TOP_THRESHOLD_PX, createTopReachTracker } from '../scrollReach'

describe('createTopReachTracker', () => {
  it('从阈值之外向上越过阈值时放行一次', () => {
    const tracker = createTopReachTracker()
    expect(tracker.observe(600)).toBe(false)
    expect(tracker.observe(300)).toBe(false)
    expect(tracker.observe(50)).toBe(true)
  })

  it('停在阈值内继续滚动不重复放行', () => {
    const tracker = createTopReachTracker()
    tracker.observe(600)
    expect(tracker.observe(50)).toBe(true)
    expect(tracker.observe(10)).toBe(false)
    expect(tracker.observe(0)).toBe(false)
  })

  it('翻页锚定把 scrollTop 推回阈值之外后重新武装', () => {
    const tracker = createTopReachTracker()
    tracker.observe(600)
    expect(tracker.observe(50)).toBe(true)
    // 锚定补偿：补入更早内容后 scrollTop 被推回阈值之外
    expect(tracker.observe(1200)).toBe(false)
    expect(tracker.observe(60)).toBe(true)
  })

  it('阈值内向下滚动不会误判为「越过」', () => {
    const tracker = createTopReachTracker()
    tracker.observe(30)
    expect(tracker.observe(200)).toBe(false)
    expect(tracker.observe(60)).toBe(true)
  })

  it('reset 后回到已武装状态', () => {
    const tracker = createTopReachTracker()
    // 首次观测就落在阈值内，按「已在阈值之外」放行
    expect(tracker.observe(5)).toBe(true)
    expect(tracker.observe(10)).toBe(false)
    tracker.reset()
    expect(tracker.observe(10)).toBe(true)
  })

  it('默认阈值覆盖「接近顶部」区间', () => {
    const tracker = createTopReachTracker()
    expect(tracker.observe(2000)).toBe(false)
    expect(tracker.observe(NEAR_TOP_THRESHOLD_PX - 1)).toBe(true)
  })
})
