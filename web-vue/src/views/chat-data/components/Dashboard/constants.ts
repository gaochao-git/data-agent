/**
 * Dashboard 常量配置
 */

export const DASHBOARD_BACKGROUNDS = [
  { id: 'monitor-deep-gold', src: '', name: '深邃蓝', theme: 'monitor-deep-gold' },
  { id: 'monitor-midnight-cyan', src: '', name: '极夜黑 x 青', theme: 'monitor-midnight-cyan' },
  { id: 'monitor-space-violet', src: '', name: '石墨黑', theme: 'monitor-space-violet' },
  { id: 'light01', src: '', name: '简约白', theme: 'light' },
  { id: 'light02', src: '', name: '淡雅灰', theme: 'light' },
  { id: 'light03', src: '', name: '清新蓝', theme: 'light' },
] as const

// 主题类型
export type ThemeType = 'dark' | 'light' | 'monitor-deep-gold' | 'monitor-midnight-cyan' | 'monitor-space-violet'

// 主题配置接口
export interface ThemeConfig {
  // 背景
  pageBg: string
  // 面板
  panelBg: string
  panelBorder: string
  panelHeaderBg: string
  // 文字
  titleColor: string
  textColor: string
  textMuted: string
  // 装饰线
  accentColor: string
  accentGlow: string
  decorLine: string
  // 图表
  chartTooltipBg: string
  chartTooltipBorder: string
  chartTooltipText: string
  chartAxisText: string
  chartGridLine: string
  // 表格
  tableHeaderBg: string
  tableRowHover: string
  tableBorder: string
  // 按钮/交互
  buttonBg: string
  buttonHover: string
  buttonText: string
  // 空状态
  emptyBorder: string
  emptyBg: string
  // 卡片背景（用于全屏等场景）
  cardBg: string
  // 图表默认色板
  chartPalette: string[]
}

export const THEMES: Record<ThemeType, ThemeConfig> = {
  dark: {
    pageBg: '#0f172a',
    panelBg: 'rgba(30, 41, 59, 0.9)',
    panelBorder: 'rgba(99, 102, 241, 0.2)',
    panelHeaderBg: 'rgba(30, 41, 59, 0.8)',
    titleColor: '#06b6d4',
    textColor: '#e2e8f0',
    textMuted: '#94a3b8',
    accentColor: '#0ea5e9',
    accentGlow: 'rgba(6, 182, 212, 0.1)',
    decorLine: 'linear-gradient(90deg, transparent 0%, #0ea5e9 20%, #06b6d4 50%, #0ea5e9 80%, transparent 100%)',
    chartTooltipBg: 'rgba(30, 41, 59, 0.9)',
    chartTooltipBorder: 'rgba(99, 102, 241, 0.3)',
    chartTooltipText: '#e2e8f0',
    chartAxisText: '#94a3b8',
    chartGridLine: 'rgba(148, 163, 184, 0.25)',
    tableHeaderBg: 'rgba(30, 41, 59, 0.95)',
    tableRowHover: 'rgba(99, 102, 241, 0.1)',
    tableBorder: 'rgba(99, 102, 241, 0.2)',
    buttonBg: 'rgba(99, 102, 241, 0.2)',
    buttonHover: 'rgba(99, 102, 241, 0.3)',
    buttonText: '#a5b4fc',
    emptyBorder: 'rgba(99, 102, 241, 0.4)',
    emptyBg: 'rgba(30, 41, 59, 0.5)',
    cardBg: '#1e293b',
    chartPalette: ['#0ea5e9', '#06b6d4', '#22c55e', '#f59e0b', '#ef4444'],
  },
  light: {
    pageBg: '#F5F7FA',
    panelBg: 'rgba(255, 255, 255, 1)',
    panelBorder: 'rgba(22, 119, 255, 0.32)',
    panelHeaderBg: 'rgba(255, 255, 255, 1)',
    titleColor: '#1677FF',
    textColor: '#1f1f1f',
    textMuted: '#8c8c8c',
    accentColor: '#1677FF',
    accentGlow: 'rgba(22, 119, 255, 0.1)',
    decorLine: 'linear-gradient(90deg, transparent 0%, #1677FF 20%, #36CFC9 50%, #1677FF 80%, transparent 100%)',
    chartTooltipBg: 'rgba(255, 255, 255, 0.98)',
    chartTooltipBorder: 'rgba(22, 119, 255, 0.15)',
    chartTooltipText: '#1f1f1f',
    chartAxisText: '#8c8c8c',
    chartGridLine: 'rgba(0, 0, 0, 0.04)',
    tableHeaderBg: '#f0f5ff',
    tableRowHover: 'rgba(22, 119, 255, 0.06)',
    tableBorder: '#dbe7ff',
    buttonBg: 'rgba(22, 119, 255, 0.08)',
    buttonHover: 'rgba(22, 119, 255, 0.15)',
    buttonText: '#1677FF',
    emptyBorder: 'rgba(22, 119, 255, 0.2)',
    emptyBg: 'rgba(255, 255, 255, 0.9)',
    cardBg: '#ffffff',
    chartPalette: ['#1677FF', '#36CFC9', '#52C41A', '#FAAD14', '#F5222D'],
  },
  'monitor-deep-gold': {
    pageBg: '#060e1c',
    panelBg: 'rgba(10, 22, 40, 0.88)',
    panelBorder: 'rgba(201, 168, 76, 0.38)',
    panelHeaderBg: 'rgba(8, 18, 34, 0.62)',
    titleColor: '#f4d36d',
    textColor: '#e8eef7',
    textMuted: '#75849a',
    accentColor: '#c9a84c',
    accentGlow: 'rgba(201, 168, 76, 0.14)',
    decorLine: 'linear-gradient(90deg, transparent, #c9a84c 18%, #e8c96a 50%, #c9a84c 82%, transparent)',
    chartTooltipBg: 'rgba(7, 17, 32, 0.94)',
    chartTooltipBorder: 'rgba(201, 168, 76, 0.46)',
    chartTooltipText: '#f8f1d7',
    chartAxisText: '#66758c',
    chartGridLine: 'rgba(122, 143, 171, 0.16)',
    tableHeaderBg: 'rgba(14, 32, 56, 0.92)',
    tableRowHover: 'rgba(201, 168, 76, 0.08)',
    tableBorder: 'rgba(201, 168, 76, 0.16)',
    buttonBg: 'rgba(201, 168, 76, 0.14)',
    buttonHover: 'rgba(201, 168, 76, 0.24)',
    buttonText: '#f4d36d',
    emptyBorder: 'rgba(201, 168, 76, 0.34)',
    emptyBg: 'rgba(10, 22, 40, 0.48)',
    cardBg: '#0a1628',
    chartPalette: ['#c9a84c', '#e8c96a', '#4ccf8a', '#f59e0b', '#e8565c'],
  },
  'monitor-midnight-cyan': {
    pageBg: '#080810',
    panelBg: 'rgba(13, 13, 30, 0.88)',
    panelBorder: 'rgba(0, 229, 255, 0.34)',
    panelHeaderBg: 'rgba(10, 11, 26, 0.68)',
    titleColor: '#00e5ff',
    textColor: '#eef7ff',
    textMuted: '#8393bc',
    accentColor: '#00e5ff',
    accentGlow: 'rgba(0, 229, 255, 0.15)',
    decorLine: 'linear-gradient(90deg, transparent, #00e5ff 18%, #36CFC9 58%, transparent)',
    chartTooltipBg: 'rgba(8, 8, 18, 0.95)',
    chartTooltipBorder: 'rgba(0, 229, 255, 0.42)',
    chartTooltipText: '#eef7ff',
    chartAxisText: '#7d88a8',
    chartGridLine: 'rgba(117, 130, 168, 0.18)',
    tableHeaderBg: 'rgba(17, 17, 40, 0.94)',
    tableRowHover: 'rgba(0, 229, 255, 0.08)',
    tableBorder: 'rgba(0, 229, 255, 0.15)',
    buttonBg: 'rgba(0, 229, 255, 0.12)',
    buttonHover: 'rgba(0, 229, 255, 0.22)',
    buttonText: '#00e5ff',
    emptyBorder: 'rgba(0, 229, 255, 0.32)',
    emptyBg: 'rgba(13, 13, 30, 0.5)',
    cardBg: '#0d0d1e',
    chartPalette: ['#00e5ff', '#36CFC9', '#00dc75', '#ffdc3d', '#ff3d00'],
  },
  'monitor-space-violet': {
    // 石墨黑主题（id 保持 monitor-space-violet 兼容已保存看板）：中性灰黑 + 冷白强调，无紫。
    pageBg: '#0d0f14',
    panelBg: 'rgba(19, 22, 30, 0.88)',
    panelBorder: 'rgba(148, 163, 184, 0.28)',
    panelHeaderBg: 'rgba(17, 20, 27, 0.7)',
    titleColor: '#e2e8f0',
    textColor: '#f1f5f9',
    textMuted: '#8b93a5',
    accentColor: '#67e8f9',
    accentGlow: 'rgba(103, 232, 249, 0.14)',
    decorLine: 'linear-gradient(90deg, transparent, #67e8f9 18%, #e2e8f0 58%, transparent)',
    chartTooltipBg: 'rgba(13, 15, 20, 0.95)',
    chartTooltipBorder: 'rgba(148, 163, 184, 0.35)',
    chartTooltipText: '#f1f5f9',
    chartAxisText: '#8b93a5',
    chartGridLine: 'rgba(148, 163, 184, 0.14)',
    tableHeaderBg: 'rgba(24, 28, 37, 0.94)',
    tableRowHover: 'rgba(103, 232, 249, 0.06)',
    tableBorder: 'rgba(148, 163, 184, 0.16)',
    buttonBg: 'rgba(103, 232, 249, 0.10)',
    buttonHover: 'rgba(103, 232, 249, 0.20)',
    buttonText: '#e2e8f0',
    emptyBorder: 'rgba(148, 163, 184, 0.30)',
    emptyBg: 'rgba(19, 22, 30, 0.5)',
    cardBg: '#13161e',
    chartPalette: ['#22d3ee', '#4ade80', '#fbbf24', '#60a5fa', '#fb7185'],
  },
}

// 浅色主题纯色背景
export const LIGHT_BACKGROUNDS: Record<string, string> = {
  light01: '#FFFFFF',
  light02: 'linear-gradient(180deg, #F5F7FA 0%, #EEF2F6 100%)',
  light03: 'linear-gradient(135deg, #F0F5FF 0%, #E6F4FF 100%)',
}

export const MONITOR_BACKGROUNDS: Record<string, string> = {
  'monitor-deep-gold': 'radial-gradient(circle at 78% 8%, rgba(96,165,250,0.09), transparent 26%), linear-gradient(180deg, rgba(9,14,26,0.98), rgba(14,21,38,0.98)), repeating-linear-gradient(0deg, rgba(122,143,171,0.05) 0 1px, transparent 1px 36px), repeating-linear-gradient(90deg, rgba(122,143,171,0.04) 0 1px, transparent 1px 36px)',
  'monitor-midnight-cyan': 'radial-gradient(circle at 74% 10%, rgba(0,229,255,0.10), transparent 24%), linear-gradient(180deg, rgba(8,10,18,0.98), rgba(13,15,28,0.98)), repeating-linear-gradient(0deg, rgba(0,229,255,0.05) 0 1px, transparent 1px 34px), repeating-linear-gradient(90deg, rgba(0,229,255,0.04) 0 1px, transparent 1px 34px)',
  'monitor-space-violet': 'radial-gradient(circle at 76% 10%, rgba(148,197,255,0.06), transparent 26%), linear-gradient(180deg, rgba(13,15,20,0.98), rgba(19,22,30,0.98)), repeating-linear-gradient(0deg, rgba(148,163,184,0.04) 0 1px, transparent 1px 36px), repeating-linear-gradient(90deg, rgba(148,163,184,0.04) 0 1px, transparent 1px 36px)',
}

export const getThemeTypeByBackgroundId = (backgroundId: string): ThemeType => {
  const matched = DASHBOARD_BACKGROUNDS.find(bg => bg.id === backgroundId)
  const theme = matched?.theme
  if (backgroundId === 'monitor-iron-red') return 'monitor-deep-gold'
  return theme && theme in THEMES ? theme as ThemeType : backgroundId.startsWith('light') ? 'light' : 'dark'
}

// 图表类型图标
export { CHART_TYPE_ICONS as CHART_ICONS, CHART_TYPES } from '../../chartTypes'

// KPI 可选图标
export const KPI_ICONS = [
  { icon: 'fa-users', label: '用户' },
  { icon: 'fa-user', label: '个人' },
  { icon: 'fa-yen-sign', label: '人民币' },
  { icon: 'fa-dollar-sign', label: '美元' },
  { icon: 'fa-coins', label: '金额' },
  { icon: 'fa-chart-line', label: '趋势' },
  { icon: 'fa-percent', label: '比率' },
  { icon: 'fa-file-invoice', label: '订单' },
  { icon: 'fa-shopping-cart', label: '购物' },
  { icon: 'fa-heart', label: '收藏' },
  { icon: 'fa-eye', label: '浏览' },
  { icon: 'fa-clock', label: '时间' },
  { icon: 'fa-database', label: '数据' },
  { icon: 'fa-server', label: '服务器' },
  { icon: 'fa-bolt', label: '性能' },
]

// KPI 可选颜色
export const KPI_COLORS = [
  { color: '#1677FF', label: '蓝色' },
  { color: '#36CFC9', label: '青色' },
  { color: '#52C41A', label: '绿色' },
  { color: '#FAAD14', label: '橙色' },
  { color: '#F5222D', label: '红色' },
  { color: '#722ED1', label: '紫色' },
  { color: '#EB2F96', label: '粉色' },
  { color: '#8c8c8c', label: '灰色' },
]

// 图表可选颜色
export const CHART_COLOR_OPTIONS = [
  { color: '#1677FF', label: '蓝色' },
  { color: '#36CFC9', label: '青色' },
  { color: '#52C41A', label: '绿色' },
  { color: '#FAAD14', label: '橙色' },
  { color: '#F5222D', label: '红色' },
  { color: '#722ED1', label: '紫色' },
  { color: '#EB2F96', label: '粉色' },
]

// 折线图样式选项
export const LINE_STYLES = [
  { id: 'smooth', label: '平滑曲线(推荐)', smooth: true, areaFill: true },
  { id: 'straight', label: '直线', smooth: false, areaFill: true },
  { id: 'smooth-no-fill', label: '平滑无填充', smooth: true, areaFill: false },
  { id: 'straight-no-fill', label: '直线无填充', smooth: false, areaFill: false },
]

// 网格配置
export const GRID_CONFIG = {
  COLS: 12,
  ROW_HEIGHT: 50,
}

// 面板数据运行状态类型
export interface PanelDataStateMap {
  [panelId: string]: {
    data: import('../../api/dashboard').PanelData | null
    loading: boolean
    error: string | null
  }
}

// 面板字段配置
export interface ChartFieldConfig {
  xField: string
  yField: string
  seriesField?: string
}

export interface PanelFieldConfigState {
  [panelId: string]: ChartFieldConfig
}
