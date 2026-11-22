/**
 * 写入类工具（file_write / file_edit）的内容预览口径。
 *
 * 工具卡和侧边栏共用：从参数里取出真正写入的文本，再按文件扩展名决定展示方式。
 * 只服务于展示，不改变工具参数本身；参数视图仍按原格式展示。
 */

import { extractPartialJsonString } from './partialJson'

export type FilePreviewKind = 'markdown' | 'json' | 'yaml' | 'sql' | 'code' | 'text'

export interface FilePreviewFormat {
  kind: FilePreviewKind
  label: string
  icon: string
  /** CodeFilePreview 可用的语法扩展；没有对应语言包时按纯文本展示。 */
  codeLanguage: 'yaml' | 'sql' | 'text'
}

export interface FileWritePreviewPayload {
  path: string
  fileName: string
  content: string
  action: 'write' | 'edit'
}

const FILE_WRITE_TOOL_NAMES = new Set(['file_write', 'file_edit'])

const CODE_LANGUAGES: Record<string, string> = {
  py: 'Python',
  pyw: 'Python',
  js: 'JavaScript',
  mjs: 'JavaScript',
  cjs: 'JavaScript',
  jsx: 'JSX',
  ts: 'TypeScript',
  tsx: 'TSX',
  vue: 'Vue',
  html: 'HTML',
  htm: 'HTML',
  css: 'CSS',
  scss: 'SCSS',
  less: 'Less',
  xml: 'XML',
  java: 'Java',
  kt: 'Kotlin',
  kts: 'Kotlin',
  go: 'Go',
  rs: 'Rust',
  c: 'C',
  h: 'C',
  cpp: 'C++',
  cc: 'C++',
  hpp: 'C++',
  cs: 'C#',
  php: 'PHP',
  rb: 'Ruby',
  swift: 'Swift',
  sh: 'Shell',
  bash: 'Shell',
  zsh: 'Shell',
  ps1: 'PowerShell',
  toml: 'TOML',
  ini: 'INI',
  conf: 'Conf',
  properties: 'Properties',
  gradle: 'Gradle',
  lua: 'Lua',
  r: 'R',
  scala: 'Scala',
  dart: 'Dart',
  makefile: 'Makefile',
  dockerfile: 'Dockerfile'
}

const isFileWriteToolName = (toolName: string): boolean => FILE_WRITE_TOOL_NAMES.has(toolName)

const asText = (value: unknown): string => {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

const firstText = (...values: unknown[]): string => {
  for (const value of values) {
    const text = asText(value).trim()
    if (text) return text
  }
  return ''
}

export const fileBaseName = (path: string): string => {
  const normalized = String(path || '').trim()
  if (!normalized) return ''
  const parts = normalized.split(/[\\/]/)
  return parts[parts.length - 1] || normalized
}

/** 仅识别文本写入工具；结果为空内容时返回 null（此时只展示原始参数）。 */
export const extractFileWritePreview = (toolName: string, args: unknown): FileWritePreviewPayload | null => {
  if (!isFileWriteToolName(toolName)) return null
  const record = args && typeof args === 'object' && !Array.isArray(args)
    ? args as Record<string, unknown>
    : {}
  const action: 'write' | 'edit' = toolName === 'file_edit' ? 'edit' : 'write'
  const content = asText(action === 'edit' ? record.new_content : record.content)
  if (!content) return null
  const path = firstText(record.file_path, record.path)
  return {
    path,
    fileName: fileBaseName(path) || '未命名文件',
    content,
    action,
  }
}

/**
 * 参数生成中从半截 JSON 里提前取文件名与正文。
 *
 * 工具参数通常按 schema 顺序输出（file_path 在 content 之前），因此文件名和格式
 * 可以早于正文完成识别；正文本身也按已生成部分实时展示，避免用户先看一屏转义 JSON。
 */
export const extractStreamingFileWritePreview = (
  toolName: string,
  argsText: string,
): FileWritePreviewPayload | null => {
  if (!isFileWriteToolName(toolName)) return null
  const raw = String(argsText || '')
  if (!raw) return null
  const action: 'write' | 'edit' = toolName === 'file_edit' ? 'edit' : 'write'
  const path = firstText(
    extractPartialJsonString(raw, 'file_path'),
    extractPartialJsonString(raw, 'path'),
  )
  const content = extractPartialJsonString(raw, action === 'edit' ? 'new_content' : 'content')
  if (!path && !content) return null
  return {
    path,
    fileName: fileBaseName(path) || '未命名文件',
    content,
    action,
  }
}

const extensionOf = (path: string): string => {
  const name = fileBaseName(path).toLowerCase()
  if (name === 'dockerfile' || name === 'makefile') return name
  const dot = name.lastIndexOf('.')
  return dot >= 0 ? name.slice(dot + 1) : ''
}

export const detectFilePreviewFormat = (path: string): FilePreviewFormat => {
  const ext = extensionOf(path)
  if (ext === 'md' || ext === 'markdown') {
    return { kind: 'markdown', label: 'Markdown', icon: 'fa-file-lines', codeLanguage: 'text' }
  }
  if (ext === 'json' || ext === 'jsonc') {
    return { kind: 'json', label: 'JSON', icon: 'fa-file-code', codeLanguage: 'text' }
  }
  if (ext === 'yaml' || ext === 'yml') {
    return { kind: 'yaml', label: 'YAML', icon: 'fa-file-code', codeLanguage: 'yaml' }
  }
  if (ext === 'sql') {
    return { kind: 'sql', label: 'SQL', icon: 'fa-file-code', codeLanguage: 'sql' }
  }
  if (ext === 'txt' || ext === 'text' || ext === 'log' || ext === 'csv' || ext === 'tsv') {
    return { kind: 'text', label: '文本', icon: 'fa-file-lines', codeLanguage: 'text' }
  }
  if (CODE_LANGUAGES[ext]) {
    return { kind: 'code', label: CODE_LANGUAGES[ext], icon: 'fa-file-code', codeLanguage: 'text' }
  }
  return { kind: 'text', label: '文本', icon: 'fa-file-lines', codeLanguage: 'text' }
}

/** JSON 文件优先美化；解析失败时保留原文，避免为了格式化吞掉用户要写的内容。 */
export const formatJsonPreview = (content: string): { text: string; error: string } => {
  const text = String(content || '')
  try {
    return { text: JSON.stringify(JSON.parse(text), null, 2), error: '' }
  } catch {
    return { text, error: 'JSON 解析失败，已按原文展示' }
  }
}
