/**
 * Mermaid 图表按需渲染 + 全屏查看器。
 *
 * 模型回复可能包含 ```mermaid 代码块（流程图/思维导图等）。mermaid 渲染入口
 * 收敛在本文件：markdown.ts 保持纯 HTML 产出不动；mermaid 库体积大，只在真正
 * 出现 mermaid 代码块时才动态加载。解析或渲染失败（含流式期间语法不完整）
 * 一律保持原代码块展示，不放大错误。
 *
 * 展示约定（刻意保持简单，不要再引入 transform 画布模型）：
 * - 内联展示（会话消息、文件预览）= 原生布局：svg 宽度自适应容器、高度按比例，
 *   超出容器走 overflow 滚动。曾经的版本用 translate/scale 画布模型做「整图适配 +
 *   拖拽」，代价是一连串定位 bug（双重偏移、克隆残留、居中误算、flex 收缩），
 *   收益抵不过浏览器原生滚动。
 * - 全屏查看器 = 唯一保留交互（拖拽平移、滚轮/按钮缩放、复制源码）的一层，
 *   在那儿才有足够空间做这些事。
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
  // 画布 origin 0 0 且布局在 (0,0)：锚点在 body 坐标系里的位置即平移基准
  const ax = (anchorX ?? rect.left + rect.width / 2) - rect.left
  const ay = (anchorY ?? rect.top + rect.height / 2) - rect.top
  const ratio = clamped / viewer.scale
  viewer.tx = ax - (ax - viewer.tx) * ratio
  viewer.ty = ay - (ay - viewer.ty) * ratio
  viewer.scale = clamped
  applyViewerTransform()
}

/** 初始视图与「重置」共用。必须在 root 入 DOM 后调用：居中按 body 实际可视尺寸算。
 *
 * 以「可读」为先而非「整图可见」：按可用宽度铺满、放大不超过原始尺寸。长竖图若按
 * 整图可见会缩到 20%、宽只剩 130px，完全看不清——铺满宽度后纵向溢出，靠拖拽/滚轮
 * 查看。横向不溢出的图（宽图、方图）结果仍是完整居中可见。 */
const applyFitView = (state: ViewerState, naturalWidth: number, naturalHeight: number) => {
  const rect = state.body.getBoundingClientRect()
  const availWidth = Math.max(1, rect.width - VIEWER_MARGIN_X)
  const availHeight = Math.max(1, rect.height - VIEWER_MARGIN_Y)
  const scale = Math.max(VIEWER_SCALE_MIN, Math.min(1, availWidth / naturalWidth))
  const scaledHeight = naturalHeight * scale
  state.scale = scale
  state.tx = (rect.width - naturalWidth * scale) / 2
  // 纵向溢出时顶部对齐（从上往下读）；否则垂直居中
  state.ty = scaledHeight > availHeight ? VIEWER_MARGIN_Y / 2 : (rect.height - scaledHeight) / 2
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
  // 摘掉内联展示留下的自适应宽高，按原始尺寸铺开，交给画布 transform 控制
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
  // 工具条与消息内图表同款：右上角、同顺序（缩小/倍率/放大/重置/复制/关闭）
  bar.append(makeButton('－', 'out', '缩小'), zoomLabel, makeButton('＋', 'in', '放大'), makeButton('⟲', 'reset', '重置缩放与位置'))
  const sourceWrap = sourceSvg.closest<HTMLElement>('.mermaid-wrap')
  const viewerSource = sourceWrap ? diagramSources.get(sourceWrap) : undefined
  if (viewerSource) bar.append(makeButton('⧉ 复制', 'copy', '复制 Mermaid 源码'))
  bar.append(makeButton('✕ 关闭', 'close', '关闭（Esc）'))

  root.append(body, bar)
  body.append(canvas)

  viewer = { root, body, canvas, zoomLabel, scale: 1, tx: 0, ty: 0 }
  viewerPrevOverflow = document.documentElement.style.overflow
  document.documentElement.style.overflow = 'hidden'
  document.addEventListener('keydown', onViewerKeydown, true)
  document.body.append(root)
  // 初始视图：可读优先，且必须在入 DOM 后算（居中依赖 body 实际尺寸）
  applyFitView(viewer, naturalWidth, naturalHeight)

  bar.addEventListener('click', (event) => {
    const state = viewer
    if (!state) return
    const target = event.target
    if (!(target instanceof HTMLElement)) return
    const action = target.dataset.viewerAction
    if (action === 'in') zoomViewerTo(state.scale * VIEWER_ZOOM_STEP)
    else if (action === 'out') zoomViewerTo(state.scale / VIEWER_ZOOM_STEP)
    else if (action === 'reset') {
      applyFitView(state, naturalWidth, naturalHeight)
    } else if (action === 'copy' && viewerSource) {
      void copyDiagramSource(viewerSource).then((ok) => flashButton(target, ok ? '✓ 已复制' : '✗ 复制失败'))
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

/** 从触发元素向上找到所属图表块并打开全屏 */
const openViewerFrom = (scope: Element) => {
  const wrap = scope.closest('.mermaid-wrap') ?? scope
  // 直接子选择器：部分图型内部存在嵌套 svg，不能让后代选择器误取
  const svg = wrap.querySelector('.mermaid-diagram > svg')
  if (svg instanceof SVGSVGElement) openMermaidViewer(svg)
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

/** 渲染结果包装：外框（可点击全屏）+ 悬浮工具条（源码 / 复制 / 全屏）+ 图表容器 */
const createToolButton = (text: string, datasetKey: 'mermaidCopy' | 'mermaidToggle' | 'mermaidFullscreen', datasetValue: string, title: string) => {
  const button = document.createElement('button')
  button.type = 'button'
  button.className = 'mermaid-tool-btn'
  button.dataset[datasetKey] = datasetValue
  button.title = title
  button.textContent = text
  return button
}

/**
 * mermaid 产出的 svg 自带内联 `style="max-width: NNNpx"`（锁死天然宽度），容器更宽时
 * 图不跟随、两侧留大片空白。这里放开为「宽度自适应容器、高度按比例」，放不下的部分
 * 交给容器 overflow 滚动——浏览器原生行为，无需 JS 参与换算。
 */
const relaxSvgSizing = (diagram: HTMLElement) => {
  const svg = diagram.querySelector(':scope > svg')
  if (!(svg instanceof SVGSVGElement)) return
  svg.style.maxWidth = '100%'
  svg.style.width = '100%'
  svg.style.height = 'auto'
}

const buildDiagramBlock = (svg: string, source: string): HTMLElement => {
  const wrap = document.createElement('div')
  wrap.className = 'mermaid-wrap'
  diagramSources.set(wrap, source)
  const tools = document.createElement('div')
  tools.className = 'mermaid-tools'
  tools.append(
    createToolButton('源码', 'mermaidToggle', 'true', '查看 Mermaid 源码'),
    createToolButton('⧉ 复制', 'mermaidCopy', 'true', '复制 Mermaid 源码'),
    createToolButton('⛶', 'mermaidFullscreen', 'true', '全屏查看（也可直接点击图表）'),
  )
  const diagram = document.createElement('div')
  diagram.className = 'mermaid-diagram'
  diagram.innerHTML = svg
  relaxSvgSizing(diagram)
  // 源码视图：与图表互斥显示（切换按钮控制）。code 不用 language-mermaid 类，
  // 避免 renderMermaidBlocks 的代码块选择器把源码视图再次替换成图表。
  const sourceView = document.createElement('pre')
  sourceView.className = 'mermaid-source'
  const sourceCode = document.createElement('code')
  sourceCode.className = 'mermaid-source-code'
  sourceCode.textContent = source
  sourceView.append(sourceCode)
  wrap.append(tools, diagram, sourceView)
  return wrap
}

// 文档级事件委托只注册一次：注入的图表散落在多个 v-html 容器里，逐个绑定不可靠
let viewerDelegated = false
/** 清理钩子挂 document 上的键：字符串而非 Symbol——HMR 后是新模块实例，Symbol 对不上旧值 */
const DELEGATION_CLEANUP_KEY = '__chatDataMermaidDelegationCleanup'

const ensureViewerDelegation = () => {
  if (viewerDelegated) return
  viewerDelegated = true
  const onDocumentClick = (event: MouseEvent) => {
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
    const toggleTrigger = target.closest<HTMLElement>('[data-mermaid-toggle]')
    if (toggleTrigger) {
      event.preventDefault()
      const wrap = toggleTrigger.closest<HTMLElement>('.mermaid-wrap')
      if (!wrap) return
      const showSource = !wrap.classList.contains('mermaid-show-source')
      wrap.classList.toggle('mermaid-show-source', showSource)
      toggleTrigger.textContent = showSource ? '图' : '源码'
      toggleTrigger.title = showSource ? '查看图表' : '查看 Mermaid 源码'
      return
    }
    const trigger = target.closest('[data-mermaid-fullscreen]')
    if (trigger) {
      event.preventDefault()
      openViewerFrom(trigger)
      return
    }
    const diagram = target.closest('.mermaid-diagram')
    if (!diagram) return
    openViewerFrom(diagram)
  }
  document.addEventListener('click', onDocumentClick)
  // HMR 会重新执行本模块：旧实例挂在 document 上的监听不会自动卸载，且新旧实例
  // 各自持有 viewer 模块全局 → 全屏浮层叠层、要关多次。重注册前先拆旧监听、
  // 移除旧实例可能遗留的孤儿浮层（overflow 一并复位）。
  const previous = (document as unknown as Record<string, unknown>)[DELEGATION_CLEANUP_KEY]
  if (typeof previous === 'function') (previous as () => void)()
  ;(document as unknown as Record<string, unknown>)[DELEGATION_CLEANUP_KEY] = () => {
    viewerDelegated = false
    document.removeEventListener('click', onDocumentClick)
    document.querySelectorAll('.mermaid-viewer').forEach((node) => node.remove())
    document.documentElement.style.overflow = ''
  }
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

/**
 * 独立渲染入口（.mmd 文件预览等非 markdown 场景）：整份文本即 mermaid 源码。
 * 成功时把带工具条的图表块写入 container 并返回 true；解析失败返回 false，
 * 由调用方回退源码展示。复用消息内图表的源码/复制/全屏工具链。
 */
export const renderMermaidSourceBlock = async (container: HTMLElement, source: string): Promise<boolean> => {
  if (!source.trim()) return false
  ensureViewerDelegation()
  const mermaid = await loadMermaid()
  const svg = await renderMermaidCode(mermaid, source)
  if (!svg) return false
  container.replaceChildren(buildDiagramBlock(svg, source))
  return true
}
