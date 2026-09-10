/**
 * 侧边栏 Tab 记忆
 *
 * 记忆以「视图模式」为维度存储（chatdata:sidebar-tab），各模式各自记住自己
 * 的 tab，避免在 A 模式的选择被 B 模式覆盖；存储不可用时降级为默认 tab。
 */
import type { ViewMode } from '../types'
import { readLocalStorage, writeLocalStorage } from './localStorage'

export const SIDEBAR_TAB_STORAGE_KEY = 'chatdata:sidebar-tab'

export type SidebarTabMemory = Record<string, string>

/** 读取全部模式记忆；数据损坏/非对象/值非字符串一律剔除，视为未记忆 */
export const readSidebarTabMemory = (): SidebarTabMemory => {
  try {
    const parsed: unknown = JSON.parse(readLocalStorage(SIDEBAR_TAB_STORAGE_KEY) || '{}')
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {}
    // 过滤非字符串值，防止手改存储留下的脏结构被读-改-写永久保留
    const memory: SidebarTabMemory = {}
    for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
      if (typeof value === 'string') memory[key] = value
    }
    return memory
  } catch {
    return {}
  }
}

/** 记住某模式下的 tab 选择 */
export const rememberSidebarTab = (mode: ViewMode, tab: string): void => {
  const memory = readSidebarTabMemory()
  memory[mode] = tab
  writeLocalStorage(SIDEBAR_TAB_STORAGE_KEY, JSON.stringify(memory))
}

/** 各模式缺省展示的 tab（与 sidebarTabs 的默认项一致） */
export const defaultSidebarTab = (mode: ViewMode): string => {
  if (mode === 'conversation') return 'conversation'
  if (mode === 'dashboard') return 'dashboard'
  if (mode === 'metricConfig') return 'metrics'
  return 'tables'
}

/**
 * 解析某模式应展示的 tab：优先取记忆值，但记忆值可能因 tab key 演进失效
 * （改名/删除），必须落在当前模式的可用 tab 内才采用，否则回落默认 tab。
 */
export const resolveSidebarTab = (mode: ViewMode, availableTabs: string[]): string => {
  const remembered = readSidebarTabMemory()[mode]
  if (remembered && availableTabs.includes(remembered)) return remembered
  const fallback = defaultSidebarTab(mode)
  return availableTabs.includes(fallback) ? fallback : availableTabs[0] || fallback
}
