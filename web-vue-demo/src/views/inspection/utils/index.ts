export * from './dataMapper';
export * from './formatters';
export * from './messageParser';
export * from './streamHelpers';

/**
 * 工具函数集合
 */

import type { ChatMessage, ContentBlock, TextContentBlock, LawItem } from '../types'

/**
 * 格式化时间戳
 */
export function formatTimestamp(date: Date | string): string {
  const d = new Date(date)
  const hours = d.getHours().toString().padStart(2, '0')
  const minutes = d.getMinutes().toString().padStart(2, '0')
  return `${hours}:${minutes}`
}

/**
 * 截断提示文本
 */
export function formatPromptPreview(prompt: string, maxLength: number = 25): string {
  if (prompt.length <= maxLength) return prompt
  return prompt.slice(0, maxLength) + '...'
}

/**
 * 合并 className（简单实现）
 */
export function mergeClassNames(...classes: (string | undefined | false)[]): string {
  return classes.filter(Boolean).join(' ')
}

/**
 * 格式化法律条款显示
 */
export function formatLawItemDisplay(law: { law_name?: string; law_item_name?: string } | undefined): string | null {
  if (!law || !law.law_name || !law.law_item_name) return null
  return `${law.law_name} ${law.law_item_name}`
}
