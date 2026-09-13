import { describe, expect, it } from 'vitest'
import { buildSandboxHtmlDocument } from '../sandboxHtml'

const expectCsp = (html: string) => {
  expect(html).toContain('http-equiv="Content-Security-Policy"')
  expect(html).toContain("default-src 'none'")
  expect(html).toContain("script-src 'unsafe-inline'")
  expect(html).toContain("style-src 'unsafe-inline'")
  expect(html).toContain('img-src data: blob:')
}

describe('buildSandboxHtmlDocument', () => {
  it('returns empty string for blank input', () => {
    expect(buildSandboxHtmlDocument('')).toBe('')
    expect(buildSandboxHtmlDocument('   ')).toBe('')
  })

  it('injects CSP before closing head tag', () => {
    const html = '<!DOCTYPE html><html><head><title>报告</title></head><body>ok</body></html>'
    const result = buildSandboxHtmlDocument(html)
    expectCsp(result)
    expect(result.indexOf('Content-Security-Policy')).toBeLessThan(result.indexOf('</head>'))
    expect(result).toContain('<title>报告</title>')
  })

  it('injects CSP after opening head tag when head is not closed', () => {
    const html = '<html><head><title>x</title><body>ok</body></html>'
    const result = buildSandboxHtmlDocument(html)
    expectCsp(result)
    expect(result).toContain('<head><meta')
  })

  it('injects head with CSP for html document without head', () => {
    const html = '<html lang="zh"><body>ok</body></html>'
    const result = buildSandboxHtmlDocument(html)
    expectCsp(result)
    expect(result).toContain('<html lang="zh"><head>')
  })

  it('wraps fragment with a full document and CSP', () => {
    const result = buildSandboxHtmlDocument('<div>hello</div>')
    expect(result.startsWith('<!DOCTYPE html>')).toBe(true)
    expectCsp(result)
    expect(result).toContain('<body><div>hello</div></body>')
  })

  it('always injects our CSP even when document already has one', () => {
    // 多个 CSP meta 并存时浏览器取最严格交集：模型自带的宽松 CSP 不能放开外联。
    const html = '<html><head><meta http-equiv="Content-Security-Policy" content="default-src *"></head><body>x</body></html>'
    const result = buildSandboxHtmlDocument(html)
    expectCsp(result)
    expect(result).toContain("default-src *")
    expect(result.match(/http-equiv="Content-Security-Policy"/g)).toHaveLength(2)
  })

  it('injects CSP when CSP marker only appears in comments or text', () => {
    // 旧实现会因正文/注释里出现 CSP 字样而误判跳过；现实现无条件注入。
    const html = '<html><head><title>t</title></head><body><!-- http-equiv="Content-Security-Policy" -->ok</body></html>'
    const result = buildSandboxHtmlDocument(html)
    expectCsp(result)
  })
})
