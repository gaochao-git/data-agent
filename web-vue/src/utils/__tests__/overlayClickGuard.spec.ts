import { beforeEach, describe, expect, it } from 'vitest'
import { guardHiddenOverlays } from '../overlayClickGuard'

const makeOverlay = (opacity: string, pointerEvents = ''): HTMLElement => {
  const el = document.createElement('div')
  el.className = 'el-overlay'
  el.style.opacity = opacity
  if (pointerEvents) el.style.pointerEvents = pointerEvents
  document.body.appendChild(el)
  return el
}

describe('guardHiddenOverlays', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
  })

  it('不可见（opacity<0.01）的过渡死锁遮罩被强制 pointer-events:none', () => {
    const zombie = makeOverlay('0')
    guardHiddenOverlays()
    expect(zombie.style.pointerEvents).toBe('none')
  })

  it('接近透明但未达阈值的过渡中遮罩同样被兜底', () => {
    const fading = makeOverlay('0.005')
    guardHiddenOverlays()
    expect(fading.style.pointerEvents).toBe('none')
  })

  it('可见遮罩不被改动，保持正常弹窗的遮罩行为', () => {
    const visible = makeOverlay('1')
    guardHiddenOverlays()
    expect(visible.style.pointerEvents).toBe('')
  })

  it('被兜底的遮罩恢复可见时还原 pointer-events', () => {
    const recovered = makeOverlay('0')
    guardHiddenOverlays()
    expect(recovered.style.pointerEvents).toBe('none')
    recovered.style.opacity = '1'
    guardHiddenOverlays()
    expect(recovered.style.pointerEvents).toBe('')
  })

  it('非遮罩元素不受影响', () => {
    const notOverlay = document.createElement('div')
    notOverlay.style.opacity = '0'
    document.body.appendChild(notOverlay)
    guardHiddenOverlays()
    expect(notOverlay.style.pointerEvents).toBe('')
  })
})
