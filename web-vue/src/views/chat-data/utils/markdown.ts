import MarkdownIt from 'markdown-it'

export const normalizeMarkdownFences = (content: string): string => {
  return String(content || '')
    .replace(/([^\n])(```[\w-]*\n)/g, '$1\n$2')
}

/**
 * chat-data 内唯一的 markdown 渲染器。
 *
 * 会话正文、子智能体中间文本、文件预览等展示位共用同一实例与配置，避免
 * 「各处各 new 一个、配置靠巧合一致」——改配置只需动这一处。
 * `html: false` 是安全底线：模型输出不可信，禁止注入原始 HTML。
 */
const renderer = new MarkdownIt({ breaks: true, html: false, linkify: true })

export const renderMarkdown = (content: string): string => renderer.render(String(content || ''))
