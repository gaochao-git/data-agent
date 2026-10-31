/**
 * 贴底跟随（主对话消息列表 / 右侧子智能体过程共用）。
 *
 * 统一 100px 阈值：距底部小于阈值视为「贴底」，内容增长时自动跟随；
 * 用户主动上翻离开底部即暂停跟随，滚回底部附近后自动恢复。
 */
export const NEAR_BOTTOM_THRESHOLD_PX = 100

export const isScrolledToBottom = (
  element: Pick<HTMLElement, 'scrollHeight' | 'scrollTop' | 'clientHeight'>,
  threshold: number = NEAR_BOTTOM_THRESHOLD_PX,
): boolean => (
  element.scrollHeight - element.scrollTop - element.clientHeight < threshold
)
