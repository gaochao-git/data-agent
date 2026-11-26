/**
 * EP 弹窗过渡死锁的兜底看守。
 *
 * 背景（推断）：窗口失焦期间 Chromium 可能暂停 rAF，Element Plus 弹窗过渡
 * （fade-in-linear）依赖 double-rAF 推进，极端时序下 enter/leave 类名叠加残留，
 * 留下 opacity:0 但 pointer-events:auto 的全屏遮罩，拦截整页点击且零 JS 报错
 * （证据：server/apps/chat_data_app/tests/browser_click_test_runs/2026-11-20-subagent-e2e-qwen36.md §3）。
 *
 * 策略：不依赖过渡动画自己完成——窗口重新获得焦点/可见时扫描全部 .el-overlay，
 * 把不可见（opacity<0.01）的遮罩强制 pointer-events:none；若它随后恢复可见
 * （过渡正常完成的情形），清掉内联覆盖，不影响正常弹窗的遮罩行为。
 */

let installed = false

/** 单次扫描：不可见遮罩兜底禁点，恢复可见的遮罩还原。幂等，可反复调用。 */
export function guardHiddenOverlays(): void {
  const overlays = document.querySelectorAll<HTMLElement>('.el-overlay')
  overlays.forEach((overlay) => {
    const opacity = Number.parseFloat(getComputedStyle(overlay).opacity)
    if (Number.isFinite(opacity) && opacity < 0.01) {
      overlay.style.pointerEvents = 'none'
    } else if (overlay.style.pointerEvents === 'none') {
      overlay.style.pointerEvents = ''
    }
  })
}

/** 全局安装（幂等）：focus / visibilitychange / transitionend 时扫描，安装时先跑一遍。 */
export function installOverlayClickGuard(): void {
  if (installed) return
  installed = true
  const onGuard = () => {
    if (document.visibilityState === 'visible') guardHiddenOverlays()
  }
  window.addEventListener('focus', onGuard, true)
  document.addEventListener('visibilitychange', onGuard)
  // 过渡正常结束时复扫：若入场动画中途被误判禁点，这里会还原，避免弹窗可见却点不动。
  document.addEventListener('transitionend', onGuard, true)
  onGuard()
}
