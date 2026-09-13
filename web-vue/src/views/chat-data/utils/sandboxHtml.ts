/**
 * 自包含 HTML artifact 的沙箱渲染安全层（唯一权威入口）。
 *
 * 渲染容器是 `sandbox="allow-scripts"` 的 iframe：
 * - 不给 `allow-same-origin`，页面脚本运行在 opaque origin，读不到父域
 *   cookie / localStorage / DOM，也无法携带凭证请求会话接口；
 * - 注入 CSP 禁止一切外部资源加载（内网部署无外网，同时防止数据外泄），
 *   页面必须完全自包含：样式、脚本、数据全部内联，不引用任何外部 URL。
 */

const SANDBOX_CSP = [
  "default-src 'none'",
  "script-src 'unsafe-inline'",
  "style-src 'unsafe-inline'",
  "img-src data: blob:",
  "font-src data:",
  "media-src data: blob:",
].join('; ')

const CSP_META_TAG = `<meta http-equiv="Content-Security-Policy" content="${SANDBOX_CSP}">`

/**
 * 把模型生成的自包含 HTML 规整为可安全进 iframe srcdoc 的完整文档。
 * 始终注入 CSP meta：多个 CSP 并存时浏览器取最严格交集，因此模型自带的
 * 宽松 CSP 不会放开外联（sandbox 属性只隔离父域，拦不住外部请求）；
 * 纯片段则包裹为完整文档。
 */
export const buildSandboxHtmlDocument = (rawHtml: string): string => {
  const html = String(rawHtml || '')
  if (!html.trim()) return ''
  if (/<\/head>/i.test(html)) {
    return html.replace(/<\/head>/i, () => `${CSP_META_TAG}</head>`)
  }
  if (/<head[^>]*>/i.test(html)) {
    return html.replace(/<head[^>]*>/i, (match) => `${match}${CSP_META_TAG}`)
  }
  if (/<html[\s>]/i.test(html)) {
    // 有 <html> 却没有 <head>：显式补一个 head（HTML 解析器允许 head 在前）。
    return html.replace(/<html([^>]*)>/i, (_match, attrs: string) => `<html${attrs}><head>${CSP_META_TAG}</head>`)
  }
  return `<!DOCTYPE html><html><head>${CSP_META_TAG}</head><body>${html}</body></html>`
}
