/** 距顶部阈值（px）：滚到顶自动加载更早消息的判定边界 */
export const NEAR_TOP_THRESHOLD_PX = 120

interface TopReachTracker {
  /** 喂入本次 scroll 的 scrollTop，返回是否应当触发一次顶部续页 */
  observe: (scrollTop: number) => boolean
  /** 复位为「已在阈值之外」，用于清空/切换会话 */
  reset: () => void
}

/**
 * 顶部触达追踪器：记录上一次 scrollTop，只在「从阈值之外向上越过阈值」时放行一次。
 *
 * 这样处理有两个好处：向上翻页补入更早内容后会把 scrollTop 推回阈值之外（视口锚定），
 * 于是自动重新武装，不需要外部额外复位；用户停在阈值内继续滚动也不会连环触发请求。
 */
export function createTopReachTracker(threshold: number = NEAR_TOP_THRESHOLD_PX): TopReachTracker {
  let lastScrollTop = Number.POSITIVE_INFINITY
  return {
    observe(scrollTop: number): boolean {
      const crossed = lastScrollTop > threshold && scrollTop < threshold
      lastScrollTop = scrollTop
      return crossed
    },
    reset(): void {
      lastScrollTop = Number.POSITIVE_INFINITY
    },
  }
}
