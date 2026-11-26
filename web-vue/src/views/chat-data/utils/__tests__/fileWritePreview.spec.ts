import { describe, expect, it } from 'vitest'

import {
  detectFilePreviewFormat,
  extractFileWritePreview,
  extractStreamingFileWritePreview,
  fileBaseName,
  formatJsonPreview,
} from '../fileWritePreview'

describe('fileWritePreview', () => {
  it('extracts file_write content and metadata', () => {
    const preview = extractFileWritePreview('file_write', {
      file_path: '/mnt/user-data/outputs/report.md',
      content: '# 报告\n正文',
    })

    expect(preview).toEqual({
      path: '/mnt/user-data/outputs/report.md',
      fileName: 'report.md',
      content: '# 报告\n正文',
      action: 'write',
    })
  })

  it('extracts file_edit new content', () => {
    const preview = extractFileWritePreview('file_edit', {
      file_path: '/mnt/user-data/workspace/app.py',
      old_content: 'print(1)',
      new_content: 'print(2)',
    })

    expect(preview?.action).toBe('edit')
    expect(preview?.content).toBe('print(2)')
  })

  it('serializes non-string content and ignores non-write tools or empty content', () => {
    const preview = extractFileWritePreview('file_write', {
      file_path: '/mnt/user-data/outputs/data.json',
      content: { ok: true },
    })
    expect(preview?.content).toBe('{\n  "ok": true\n}')

    expect(extractFileWritePreview('file_read', { file_path: '/a.txt', content: 'x' })).toBeNull()
    expect(extractFileWritePreview('file_write', { file_path: '/a.txt', content: '' })).toBeNull()
  })

  it('detects display format by file extension', () => {
    expect(detectFilePreviewFormat('/a/report.md').kind).toBe('markdown')
    expect(detectFilePreviewFormat('/a/data.json').kind).toBe('json')
    expect(detectFilePreviewFormat('/a/config.yaml').kind).toBe('yaml')
    expect(detectFilePreviewFormat('/a/query.sql').kind).toBe('sql')
    expect(detectFilePreviewFormat('/a/app.py')).toMatchObject({ kind: 'code', label: 'Python' })
    expect(detectFilePreviewFormat('/a/notes.txt')).toMatchObject({ kind: 'text', label: '文本' })
    expect(detectFilePreviewFormat('/a/unknown.abc').kind).toBe('text')
  })

  it('recognizes file name and partial content before the args JSON is closed', () => {
    const preview = extractStreamingFileWritePreview(
      'file_write',
      '{"file_path":"/mnt/user-data/outputs/note.md","content":"# 标题\\n正文还在生成',
    )

    expect(preview).toMatchObject({
      path: '/mnt/user-data/outputs/note.md',
      fileName: 'note.md',
      content: '# 标题\n正文还在生成',
      action: 'write',
    })
    expect(extractStreamingFileWritePreview('file_edit', '{"file_path":"/a.py","new_content":"print(')).toMatchObject({
      action: 'edit',
      content: 'print(',
    })
    expect(extractStreamingFileWritePreview('file_read', '{"file_path":"/a.txt"}')).toBeNull()
    expect(extractStreamingFileWritePreview('file_write', '{"file_path":')).toBeNull()
  })

  it('pretty-prints valid JSON and falls back to raw text on parse errors', () => {
    expect(formatJsonPreview('{"a":1}')).toEqual({ text: '{\n  "a": 1\n}', error: '' })
    expect(formatJsonPreview('{"a":')).toEqual({
      text: '{"a":',
      error: 'JSON 解析失败，已按原文展示',
    })
  })

  it('extracts the base name from both unix and windows paths', () => {
    expect(fileBaseName('/mnt/user-data/outputs/a.md')).toBe('a.md')
    expect(fileBaseName('C:\\workspace\\a.py')).toBe('a.py')
    expect(fileBaseName('')).toBe('')
  })
})
