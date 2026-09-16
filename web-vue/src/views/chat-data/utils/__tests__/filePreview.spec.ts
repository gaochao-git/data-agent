import { describe, expect, it } from 'vitest'
import {
  BINARY_PREVIEW_EXTENSIONS,
  IMAGE_PREVIEW_EXTENSIONS,
  OFFICE_HTML_PREVIEW_EXTENSIONS,
  PDF_PREVIEW_EXTENSIONS,
  getPreviewFileKind,
  isBinaryPreviewPath
} from '../filePreview'

describe('getPreviewFileKind', () => {
  it('图片扩展名判定为 image', () => {
    expect(getPreviewFileKind('logo.png')).toBe('image')
    expect(getPreviewFileKind('references/标准1.JPG')).toBe('image')
    expect(getPreviewFileKind('a/b/photo.jpeg')).toBe('image')
    expect(getPreviewFileKind('icon.webp')).toBe('image')
    expect(getPreviewFileKind('icon.svg')).toBe('image')
  })

  it('pdf 判定为 pdf，office 三件套判定为 office', () => {
    expect(getPreviewFileKind('证书.pdf')).toBe('pdf')
    expect(getPreviewFileKind('报告.docx')).toBe('office')
    expect(getPreviewFileKind('报表.xlsx')).toBe('office')
    expect(getPreviewFileKind('演示.pptx')).toBe('office')
  })

  it('文本与无扩展名判定为 text', () => {
    expect(getPreviewFileKind('SKILL.md')).toBe('text')
    expect(getPreviewFileKind('notes.txt')).toBe('text')
    expect(getPreviewFileKind('query.sql')).toBe('text')
    expect(getPreviewFileKind('README')).toBe('text')
  })

  it('双后缀取最后一个扩展名', () => {
    expect(getPreviewFileKind('data.backup.docx')).toBe('office')
    expect(getPreviewFileKind('a.tar.gz')).toBe('text')
  })
})

describe('isBinaryPreviewPath', () => {
  it('二进制类型（image/pdf/office）为 true，文本为 false', () => {
    expect(isBinaryPreviewPath('a.png')).toBe(true)
    expect(isBinaryPreviewPath('a.pdf')).toBe(true)
    expect(isBinaryPreviewPath('a.docx')).toBe(true)
    expect(isBinaryPreviewPath('a.md')).toBe(false)
    expect(isBinaryPreviewPath('a.txt')).toBe(false)
    expect(isBinaryPreviewPath('a.sql')).toBe(false)
  })
})

describe('预览常量集合一致性', () => {
  it('BINARY_PREVIEW_EXTENSIONS 是三类可预览扩展名的并集', () => {
    for (const ext of IMAGE_PREVIEW_EXTENSIONS) {
      expect(BINARY_PREVIEW_EXTENSIONS.has(ext)).toBe(true)
    }
    for (const ext of PDF_PREVIEW_EXTENSIONS) {
      expect(BINARY_PREVIEW_EXTENSIONS.has(ext)).toBe(true)
    }
    for (const ext of OFFICE_HTML_PREVIEW_EXTENSIONS) {
      expect(BINARY_PREVIEW_EXTENSIONS.has(ext)).toBe(true)
    }
    // 文本扩展名不得混入二进制集合
    expect(BINARY_PREVIEW_EXTENSIONS.has('md')).toBe(false)
    expect(BINARY_PREVIEW_EXTENSIONS.has('txt')).toBe(false)
  })

  it('集合判定与 getPreviewFileKind 对每个扩展名的归类一致', () => {
    const allExtensions = [...BINARY_PREVIEW_EXTENSIONS, 'md', 'txt', 'sql', '']
    for (const ext of allExtensions) {
      const path = ext ? `file.${ext}` : 'file'
      expect(BINARY_PREVIEW_EXTENSIONS.has(ext)).toBe(isBinaryPreviewPath(path))
    }
  })
})
