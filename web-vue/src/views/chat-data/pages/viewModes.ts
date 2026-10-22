/**
 * 主模式页签可见性计算（纯函数，便于单测）。
 *
 * 两个隐藏来源：
 * 1) 宿主通过 `options.hideTabs` 指定（支持模式 key 或当前展示名，展示名随 modeLabels 联动）；
 * 2) 后台「运行配置 → 功能开关」CHAT_DATA_USER_SEMANTIC_MODEL_ENABLED 关闭时，
 *    整个「语义模型配置」页签不展示（管理员仍可从后台管理维护建模）。
 *
 * 全部被隐藏时回退为「未被功能开关关闭的页签」，避免出现空页签栏；
 * 即回退也不会把开关关闭的页签放回来。
 */
import type { ViewMode } from '../types'

export interface ViewModeItem {
  key: ViewMode
  label: string
}

const ALL_MODE_KEYS: ViewMode[] = ['conversation', 'builder', 'editor', 'dashboard', 'metricConfig']

export function resolveVisibleModes(
  items: ViewModeItem[],
  hideTabs: Array<ViewMode | string> | undefined = [],
  semanticModelEnabled = false,
): ViewModeItem[] {
  const hidden = new Set<ViewMode>()
  const labelToKey = new Map<string, ViewMode>(items.map(item => [item.label, item.key]))

  for (const raw of hideTabs) {
    const value = String(raw ?? '').trim()
    if (!value) continue
    // 先按 key 匹配（与历史行为一致），再按展示名映射
    const byKey = ALL_MODE_KEYS.find(key => key === value)
    if (byKey) {
      hidden.add(byKey)
      continue
    }
    const mapped = labelToKey.get(value)
    if (mapped) hidden.add(mapped)
  }

  // 功能开关关闭：语义模型配置页签整页隐藏（口径同 useModelOptions：仅 enabled 状态不隐藏）
  if (!semanticModelEnabled) hidden.add('metricConfig')

  const filtered = items.filter(item => !hidden.has(item.key))
  if (filtered.length > 0) return filtered
  // 全部被隐藏时回退，避免出现空页签栏；但功能开关关闭的页签不因回退被放回来
  const fallback = items.filter(item => item.key !== 'metricConfig' || semanticModelEnabled)
  return fallback.length > 0 ? fallback : [...items]
}
