import { describe, expect, it } from 'vitest'

import { evictWorkspaceFileCache } from '../workspacePreviewCache'

interface TestBlob {
  url: string
  mime?: string
}

const OLD = 'uploads/报告.md'

describe('evictWorkspaceFileCache（删除文件后作废预览缓存）', () => {
  it('命中文本缓存时移除该路径，且不改动入参', () => {
    const contents = { [OLD]: '上一份内容', 'outputs/其它.md': '无关' }
    const blobs: Record<string, TestBlob> = {}
    const result = evictWorkspaceFileCache(OLD, contents, blobs)

    expect(result.contents).toEqual({ 'outputs/其它.md': '无关' })
    expect(result.contents).not.toBe(contents)
    // 原对象必须保持完整：组件里的「上一帧」引用可能仍被读，原地改会让取消中的请求读到脏值
    expect(contents).toEqual({ [OLD]: '上一份内容', 'outputs/其它.md': '无关' })
    expect(result.revokedUrl).toBeNull()
  })

  it('命中二进制缓存时移除条目并交出 blob URL 供调用方 revoke', () => {
    const contents: Record<string, string> = {}
    const blobs = { [OLD]: { url: 'blob:http://localhost/old', mime: 'text/markdown' } }
    const result = evictWorkspaceFileCache(OLD, contents, blobs)

    expect(result.blobs).toEqual({})
    expect(result.blobs).not.toBe(blobs)
    expect(result.revokedUrl).toBe('blob:http://localhost/old')
  })

  it('文本与二进制同时命中时一并清掉', () => {
    const result = evictWorkspaceFileCache(
      OLD,
      { [OLD]: 'x' },
      { [OLD]: { url: 'blob:abc' } },
    )
    expect(result.contents).toEqual({})
    expect(result.blobs).toEqual({})
    expect(result.revokedUrl).toBe('blob:abc')
  })

  it('空文件的文本缓存（空串）同样视为命中', () => {
    const result = evictWorkspaceFileCache(OLD, { [OLD]: '' }, {})
    expect(Object.prototype.hasOwnProperty.call(result.contents, OLD)).toBe(false)
  })

  it('未命中该路径时返回入参原引用，不制造多余的新对象', () => {
    const contents = { 'outputs/a.md': 'a' }
    const blobs = { 'outputs/a.md': { url: 'blob:a' } }
    const result = evictWorkspaceFileCache(OLD, contents, blobs)

    expect(result.contents).toBe(contents)
    expect(result.blobs).toBe(blobs)
    expect(result.revokedUrl).toBeNull()
  })

  it('错误态条目的空 url 不会被当成待 revoke 的地址', () => {
    const result = evictWorkspaceFileCache(OLD, {}, { [OLD]: { url: '' } })
    expect(result.blobs).toEqual({})
    expect(result.revokedUrl).toBeNull()
  })

  it('空路径原样返回，避免误清整个缓存', () => {
    const contents = { 'outputs/a.md': 'a' }
    const blobs = { 'outputs/a.md': { url: 'blob:a' } }
    const result = evictWorkspaceFileCache('', contents, blobs)

    expect(result.contents).toBe(contents)
    expect(result.blobs).toBe(blobs)
    expect(result.revokedUrl).toBeNull()
  })

  it('只影响目标路径，其它路径的内容与引用都不变', () => {
    const contents = { [OLD]: '旧', 'uploads/其它.md': '留' }
    const blobs = { [OLD]: { url: 'blob:target' }, 'uploads/其它.md': { url: 'blob:keep' } }
    const result = evictWorkspaceFileCache(OLD, contents, blobs)

    expect(result.contents['uploads/其它.md']).toBe('留')
    expect(result.blobs['uploads/其它.md'].url).toBe('blob:keep')
  })
})
