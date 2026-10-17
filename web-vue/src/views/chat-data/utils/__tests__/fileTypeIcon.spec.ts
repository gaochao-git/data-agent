import { describe, expect, it } from 'vitest'
import { extractFileExtension, fileIconClass, fileIconNameByExtension } from '../fileTypeIcon'

describe('extractFileExtension', () => {
  it('取小写扩展名', () => {
    expect(extractFileExtension('报告.DOCX')).toBe('docx')
    expect(extractFileExtension('a.b.json')).toBe('json')
  })

  it('无扩展名、以点结尾、空串返回空', () => {
    expect(extractFileExtension('README')).toBe('')
    expect(extractFileExtension('name.')).toBe('')
    expect(extractFileExtension('')).toBe('')
  })

  it('隐藏文件取尾段（.env 走映射回落，无碍）', () => {
    expect(extractFileExtension('.env')).toBe('env')
    expect(extractFileExtension('_batch1.json')).toBe('json')
  })
})

describe('fileIconClass', () => {
  it('命中映射返回图标 + 语义色', () => {
    expect(fileIconClass('订单模型__order.yaml')).toBe('fa-solid fa-file-code text-emerald-600')
    expect(fileIconClass('报告.docx')).toBe('fa-regular fa-file-word text-blue-600')
    expect(fileIconClass('_batch1.json')).toBe('fa-solid fa-file-code text-sky-600')
    expect(fileIconClass('大纲.md')).toBe('fa-brands fa-markdown text-slate-600')
  })

  it('未识别类型回通用 file-lines 灰', () => {
    expect(fileIconClass('README')).toBe('fa-regular fa-file-lines text-slate-400')
    expect(fileIconClass('archive.zip')).toBe('fa-regular fa-file-lines text-slate-400')
  })
})

describe('fileIconNameByExtension', () => {
  it('命中映射返回纯图标名（不带颜色类）', () => {
    expect(fileIconNameByExtension('pdf')).toBe('fa-solid fa-file-pdf')
    expect(fileIconNameByExtension('XLSX')).toBe('fa-regular fa-file-excel')
    expect(fileIconNameByExtension('md')).toBe('fa-brands fa-markdown')
  })

  it('未识别/空扩展名回通用 file 图标', () => {
    expect(fileIconNameByExtension('zip')).toBe('fa-regular fa-file')
    expect(fileIconNameByExtension(undefined)).toBe('fa-regular fa-file')
    expect(fileIconNameByExtension('')).toBe('fa-regular fa-file')
  })
})
