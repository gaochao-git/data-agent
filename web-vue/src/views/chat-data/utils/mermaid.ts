/**
 * Mermaid 图表按需渲染 + 全屏查看器。
 *
 * 模型回复可能包含 ```mermaid 代码块（流程图/思维导图等）。mermaid 渲染入口
 * 收敛在本文件：markdown.ts 保持纯 HTML 产出不动；mermaid 库体积大，只在真正
 * 出现 mermaid 代码块时才动态加载。解析或渲染失败（含流式期间语法不完整）
 * 一律保持原代码块展示，不放大错误。
 *
 * 全屏查看器：渲染成功的图表支持全屏查看（点击图表或右上角按钮），全屏内
 * 滚轮缩放（以鼠标为锚点）、拖拽平移、按钮步进缩放、Esc/空白处关闭。查看器
 * 为纯 DOM 实现——图表注入在 v-html 容器内，Vue 不管理该子树；所有节点均
 * createElement 构建、不拼接 HTML 字符串，除 mermaid 受控 svg 外不注入任何
 * 模型产出。
 *
 * 安全：mermaid 全程 securityLevel: 'strict'（模型输出不可信，禁交互与外部
 * 内容），svg 为 mermaid 在该模式下产出的受控结果。
 */

type MermaidApi = typeof import('mermaid').default

/** SVG 结果缓存上限：DOM 重建时按源码命中回填，避免重复渲染 */
const SVG_CACHE_LIMIT = 100

let mermaidLoader: Promise<MermaidApi> | null = null

const loadMermaid = (): Promise<MermaidApi> => {
  if (!mermaidLoader) {
    mermaidLoader = import('mermaid').then((module) => {
      module.default.initialize({ startOnLoad: false, securityLevel: 'strict' })
      return module.default
    })
  }
  return mermaidLoader
}

const svgCache = new Map<string, string>()
let renderSequence = 0

const renderMermaidCode = async (mermaid: MermaidApi, source: string): Promise<string | null> => {
  const cached = svgCache.get(source)
  if (cached) {
    // 命中后移到末尾，维持简单 LRU 语义
    svgCache.delete(source)
    svgCache.set(source, cached)
    return cached
  }
  try {
    const diagram = await mermaid.parse(source, { suppressErrors: true })
    if (!diagram) return null
    renderSequence += 1
    const { svg } = await mermaid.render(`mermaid-svg-${renderSequence}`, source)
    svgCache.set(source, svg)
    if (svgCache.size > SVG_CACHE_LIMIT) {
      const oldest = svgCache.keys().next().value
      if (oldest !== undefined) svgCache.delete(oldest)
    }
    return svg
  } catch {
    // 解析/渲染失败（流式不完整或语法不支持）：保持代码块展示
    return null
  }
}

// ============================== 全屏查看器 ==============================

const VIEWER_ZOOM_STEP = 1.25
const VIEWER_SCALE_MIN = 0.2
const VIEWER_SCALE_MAX = 10
/** 全屏画布四周预留边距（px） */
const VIEWER_MARGIN_X = 80
const VIEWER_MARGIN_Y = 120

interface ViewerState {
  root: HTMLElement
  body: HTMLElement
  canvas: HTMLElement
  zoomLabel: HTMLElement
  scale: number
  tx: number
  ty: number
}

let viewer: ViewerState | null = null
let viewerPrevOverflow = ''

const applyViewerTransform = () => {
  if (!viewer) return
  viewer.canvas.style.transform = `translate(${viewer.tx}px, ${viewer.ty}px) scale(${viewer.scale})`
  viewer.zoomLabel.textContent = `${Math.round(viewer.scale * 100)}%`
}

function onViewerKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') closeMermaidViewer()
}

const closeMermaidViewer = () => {
  if (!viewer) return
  document.removeEventListener('keydown', onViewerKeydown, true)
  document.documentElement.style.overflow = viewerPrevOverflow
  viewer.root.remove()
  viewer = null
}

/** 缩放到指定倍率；锚点为视口坐标（默认画布中心），缩放前后锚点下的图内容保持不动 */
const zoomViewerTo = (nextScale: number, anchorX?: number, anchorY?: number) => {
  if (!viewer) return
  const clamped = Math.min(VIEWER_SCALE_MAX, Math.max(VIEWER_SCALE_MIN, nextScale))
  const rect = viewer.body.getBoundingClientRect()
  const ox =
    (anchorX ?? rect.left + rect.width / 2) - rect.left - rect.width / 2
  const oy =
    (anchorY ?? rect.top + rect.height / 2) - rect.top - rect.height / 2
  const ratio = clamped / viewer.scale
  viewer.tx = ox - (ox - viewer.tx) * ratio
  viewer.ty = oy - (oy - viewer.ty) * ratio
  viewer.scale = clamped
  applyViewerTransform()
}

const openMermaidViewer = (sourceSvg: SVGSVGElement) => {
  closeMermaidViewer()
  // 原始尺寸：viewBox 为 mermaid 记录的图形真实坐标；异常时退回显示尺寸
  const viewBox = sourceSvg.viewBox.baseVal
  const sourceRect = sourceSvg.getBoundingClientRect()
  const naturalWidth = viewBox.width || sourceRect.width || 800
  const naturalHeight = viewBox.height || sourceRect.height || 600

  const root = document.createElement('div')
  root.className = 'mermaid-viewer'

  const body = document.createElement('div')
  body.className = 'mermaid-viewer-body'

  const canvas = document.createElement('div')
  canvas.className = 'mermaid-viewer-canvas'
  const svg = sourceSvg.cloneNode(true) as SVGSVGElement
  // 摘掉容器内的自适应约束，按原始尺寸铺开，交给画布 transform 控制
  svg.style.maxWidth = 'none'
  svg.style.width = `${naturalWidth}px`
  svg.style.height = `${naturalHeight}px`
  canvas.append(svg)

  const bar = document.createElement('div')
  bar.className = 'mermaid-viewer-bar'
  const zoomLabel = document.createElement('span')
  zoomLabel.className = 'mermaid-viewer-zoom'
  const makeButton = (text: string, action: string, title: string) => {
    const button = document.createElement('button')
    button.type = 'button'
    button.className = 'mermaid-viewer-btn'
    button.dataset.viewerAction = action
    button.title = title
    button.textContent = text
    return button
  }
  bar.append(
    zoomLabel,
    makeButton('−', 'out', '缩小'),
    makeButton('重置', 'reset', '重置缩放与位置'),
    makeButton('＋', 'in', '放大'),
    makeButton('✕ 关闭', 'close', '关闭（Esc）'),
  )

  root.append(body, bar)
  body.append(canvas)

  viewer = { root, body, canvas, zoomLabel, scale: 1, tx: 0, ty: 0 }
  // 初始自适应：整图完整可见（不超过原始大小的 100%）
  viewer.scale = Math.min(
    1,
    (window.innerWidth - VIEWER_MARGIN_X) / naturalWidth,
    (window.innerHeight - VIEWER_MARGIN_Y) / naturalHeight,
  )
  applyViewerTransform()

  viewerPrevOverflow = document.documentElement.style.overflow
  document.documentElement.style.overflow = 'hidden'
  document.addEventListener('keydown', onViewerKeydown, true)
  document.body.append(root)

  bar.addEventListener('click', (event) => {
    const state = viewer
    if (!state) return
    const target = event.target
    if (!(target instanceof HTMLElement)) return
    const action = target.dataset.viewerAction
    if (action === 'in') zoomViewerTo(state.scale * VIEWER_ZOOM_STEP)
    else if (action === 'out') zoomViewerTo(state.scale / VIEWER_ZOOM_STEP)
    else if (action === 'reset') {
      state.scale = 1
      state.tx = 0
      state.ty = 0
      applyViewerTransform()
    } else if (action === 'close') closeMermaidViewer()
  })

  body.addEventListener(
    'wheel',
    (event) => {
      event.preventDefault()
      const factor = event.deltaY < 0 ? VIEWER_ZOOM_STEP : 1 / VIEWER_ZOOM_STEP
      zoomViewerTo((viewer?.scale ?? 1) * factor, event.clientX, event.clientY)
    },
    { passive: false },
  )

  let dragStart: { x: number; y: number; tx: number; ty: number; moved: boolean } | null = null
  let suppressClick = false
  // pointer capture 会把后续 click 的 target 重定向为 body，必须以按下起点判断是否点在空白处
  let downOnBlank = false
  body.addEventListener('pointerdown', (event) => {
    if (!viewer) return
    suppressClick = false
    downOnBlank = event.target === body
    event.preventDefault()
    body.setPointerCapture(event.pointerId)
    dragStart = { x: event.clientX, y: event.clientY, tx: viewer.tx, ty: viewer.ty, moved: false }
  })
  body.addEventListener('pointermove', (event) => {
    if (!viewer || !dragStart) return
    const dx = event.clientX - dragStart.x
    const dy = event.clientY - dragStart.y
    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) dragStart.moved = true
    viewer.tx = dragStart.tx + dx
    viewer.ty = dragStart.ty + dy
    applyViewerTransform()
  })
  const endDrag = () => {
    if (dragStart) suppressClick = dragStart.moved
    dragStart = null
  }
  body.addEventListener('pointerup', endDrag)
  body.addEventListener('pointercancel', endDrag)
  // 点击画布空白处（按下起点在空白、非拖拽）关闭；点击图表或拖拽后的误触不关闭
  body.addEventListener('click', (event) => {
    if (suppressClick) {
      suppressClick = false
      return
    }
    if (downOnBlank && event.target === body) closeMermaidViewer()
  })
}

// ============================== 容器内缩放 ==============================
// 不能用 transform：transform 不改变布局尺寸，overflow 容器不会出现滚动条，
// 放大后超出的部分无法滚动查看。这里改 svg 的布局宽度，配合容器 overflow 滚动。

const VIEW_ZOOM_STEP = 1.25
const VIEW_ZOOM_MAX_RATIO = 4
const VIEW_ZOOM_MIN_RATIO = 0.25

const zoomDiagram = (diagram: HTMLElement, action: string) => {
  const svg = diagram.querySelector(':scope > svg')
  if (!(svg instanceof SVGSVGElement)) return
  const viewBox = svg.viewBox.baseVal
  const rect = svg.getBoundingClientRect()
  const naturalWidth = viewBox.width || rect.width || 800
  const naturalHeight = viewBox.height || rect.height || 600
  if (action === 'reset') {
    // 重置 = 整图完整落入容器可视区：宽高比取小（竖图缩到全高可见，横图缩到全宽可见）
    svg.style.removeProperty('max-width')
    const style = window.getComputedStyle(diagram)
    const availWidth = diagram.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight)
    const availHeight = diagram.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom)
    const fit = Math.min(1, availWidth / naturalWidth, availHeight / naturalHeight)
    svg.style.width = `${naturalWidth * fit}px`
    return
  }
  const ratio = (rect.width || naturalWidth) / naturalWidth
  const next = action === 'in' ? ratio * VIEW_ZOOM_STEP : ratio / VIEW_ZOOM_STEP
  const clamped = Math.min(VIEW_ZOOM_MAX_RATIO, Math.max(VIEW_ZOOM_MIN_RATIO, next))
  if (clamped === ratio) return
  svg.style.maxWidth = 'none'
  svg.style.width = `${naturalWidth * clamped}px`
}

/** 从触发元素向上找到所属图表块并打开全屏 */
const openViewerFrom = (scope: Element) => {
  const wrap = scope.closest('.mermaid-wrap') ?? scope
  // 直接子选择器：部分图型内部存在嵌套 svg，不能让后代选择器误取
  const svg = wrap.querySelector('.mermaid-diagram > svg')
  if (svg instanceof SVGSVGElement) openMermaidViewer(svg)
}

// 文档级事件委托只注册一次：注入的图表散落在多个 v-html 容器里，逐个绑定不可靠
let viewerDelegated = false
const ensureViewerDelegation = () => {
  if (viewerDelegated) return
  viewerDelegated = true
  document.addEventListener('click', (event) => {
    if (viewer) return
    const target = event.target
    if (!(target instanceof HTMLElement)) return
    const copyTrigger = target.closest<HTMLElement>('[data-mermaid-copy]')
    if (copyTrigger) {
      event.preventDefault()
      const wrap = copyTrigger.closest<HTMLElement>('.mermaid-wrap')
      const source = wrap ? diagramSources.get(wrap) : undefined
      if (!source) return
      void copyDiagramSource(source).then((ok) => flashButton(copyTrigger, ok ? '✓ 已复制' : '✗ 复制失败'))
      return
    }
    const zoomTrigger = target.closest<HTMLElement>('[data-mermaid-zoom]')
    if (zoomTrigger) {
      event.preventDefault()
      const diagram = zoomTrigger.closest<HTMLElement>('.mermaid-wrap')?.querySelector<HTMLElement>(':scope > .mermaid-diagram')
      if (diagram) zoomDiagram(diagram, zoomTrigger.dataset.mermaidZoom ?? '')
      return
    }
    const trigger = target.closest('[data-mermaid-fullscreen]')
    if (trigger) {
      event.preventDefault()
      openViewerFrom(trigger)
      return
    }
    const diagram = target.closest('.mermaid-diagram')
    if (diagram) openViewerFrom(diagram)
  })
}

// ============================== 渲染入口 ==============================

/** 图表外框 → mermaid 源码（复制按钮用；WeakMap 不阻碍 DOM 回收，源码不进 HTML attribute） */
const diagramSources = new WeakMap<HTMLElement, string>()

/** 复制文本：clipboard API 需 secure context，内网 http 部署走 textarea 兜底（同 AgentScheduleManager 口径） */
const copyDiagramSource = async (text: string): Promise<boolean> => {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // 权限拒绝或文档非激活，降级 execCommand
    }
  }
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.setAttribute('readonly', '')
  textarea.style.position = 'fixed'
  textarea.style.opacity = '0'
  document.body.append(textarea)
  textarea.select()
  let ok = false
  try {
    ok = document.execCommand('copy')
  } catch {
    ok = false
  }
  textarea.remove()
  return ok
}

/** 复制按钮的瞬时反馈：改文案后恢复 */
const flashButton = (button: HTMLElement, text: string) => {
  const original = button.textContent
  button.textContent = text
  window.setTimeout(() => {
    button.textContent = original
  }, 1500)
}

/** 渲染结果包装：外框（可点击全屏）+ 悬浮工具条（复制源码 / 缩放 / 全屏）+ 图表容器 */
const createToolButton = (text: string, datasetKey: 'mermaidCopy' | 'mermaidZoom' | 'mermaidFullscreen', datasetValue: string, title: string) => {
  const button = document.createElement('button')
  button.type = 'button'
  button.className = 'mermaid-tool-btn'
  button.dataset[datasetKey] = datasetValue
  button.title = title
  button.textContent = text
  return button
}

const buildDiagramBlock = (svg: string, source: string): HTMLElement => {
  const wrap = document.createElement('div')
  wrap.className = 'mermaid-wrap'
  diagramSources.set(wrap, source)
  const tools = document.createElement('div')
  tools.className = 'mermaid-tools'
  tools.append(
    createToolButton('⧉ 复制', 'mermaidCopy', 'true', '复制 Mermaid 源码'),
    createToolButton('－', 'mermaidZoom', 'out', '缩小'),
    createToolButton('＋', 'mermaidZoom', 'in', '放大'),
    createToolButton('⟲', 'mermaidZoom', 'reset', '重置大小'),
    createToolButton('⛶', 'mermaidFullscreen', 'true', '全屏查看（也可直接点击图表）'),
  )
  const diagram = document.createElement('div')
  diagram.className = 'mermaid-diagram'
  diagram.innerHTML = svg
  wrap.append(tools, diagram)
  return wrap
}

/**
 * 把 root 内的 ```mermaid 代码块替换为渲染后的 SVG；解析失败保持代码块。
 * 可对同一容器重复调用：已替换的块不会再命中选择器。
 */
export const renderMermaidBlocks = async (root: HTMLElement | null | undefined): Promise<void> => {
  const codeBlocks = root?.querySelectorAll<HTMLElement>('pre > code.language-mermaid') ?? []
  if (!codeBlocks.length) return
  ensureViewerDelegation()
  const mermaid = await loadMermaid()
  await Promise.all(
    Array.from(codeBlocks, async (code) => {
      const source = code.textContent || ''
      if (!source.trim() || !code.isConnected) return
      const svg = await renderMermaidCode(mermaid, source)
      if (!svg || !code.isConnected) return
      code.replaceWith(buildDiagramBlock(svg, source))
    }),
  )
}
