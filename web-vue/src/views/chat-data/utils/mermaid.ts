/**
 * Mermaid 图表按需渲染。
 *
 * 模型回复可能包含 ```mermaid 代码块（流程图/思维导图等）。mermaid 渲染入口
 * 收敛在本文件：markdown.ts 保持纯 HTML 产出不动；mermaid 库体积大，只在真正
 * 出现 mermaid 代码块时才动态加载。解析或渲染失败（含流式期间语法不完整）
 * 一律保持原代码块展示，不放大错误。
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

/**
 * 把 root 内的 ```mermaid 代码块替换为渲染后的 SVG；解析失败保持代码块。
 * 可对同一容器重复调用：已替换的块不会再命中选择器。
 */
export const renderMermaidBlocks = async (root: HTMLElement | null | undefined): Promise<void> => {
  const codeBlocks = root?.querySelectorAll<HTMLElement>('pre > code.language-mermaid') ?? []
  if (!codeBlocks.length) return
  const mermaid = await loadMermaid()
  await Promise.all(
    Array.from(codeBlocks, async (code) => {
      const source = code.textContent || ''
      if (!source.trim() || !code.isConnected) return
      const svg = await renderMermaidCode(mermaid, source)
      if (!svg || !code.isConnected) return
      const container = document.createElement('div')
      container.className = 'mermaid-diagram'
      container.innerHTML = svg
      code.replaceWith(container)
    }),
  )
}
