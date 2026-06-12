/**
 * Dashboard 常量配置
 */

// 背景图列表
import bg01 from '../../assets/dashboard-bg/bg01.png'
import bg02 from '../../assets/dashboard-bg/bg02.png'
import bg03 from '../../assets/dashboard-bg/bg03.png'
import bg04 from '../../assets/dashboard-bg/bg04.png'
import bg05 from '../../assets/dashboard-bg/bg05.png'
import bg06 from '../../assets/dashboard-bg/bg06.png'
import bg07 from '../../assets/dashboard-bg/bg07.png'
import bg08 from '../../assets/dashboard-bg/bg08.png'
import bg09 from '../../assets/dashboard-bg/bg09.jpg'
import bg10 from '../../assets/dashboard-bg/bg10.png'

export const DASHBOARD_BACKGROUNDS = [
  { id: 'bg01', src: bg01, name: '星空蓝', theme: 'dark' },
  { id: 'bg02', src: bg02, name: '科技蓝', theme: 'dark' },
  { id: 'bg03', src: bg03, name: '深邃蓝', theme: 'dark' },
  { id: 'bg04', src: bg04, name: '极光蓝', theme: 'dark' },
  { id: 'bg05', src: bg05, name: '星云紫', theme: 'dark' },
  { id: 'bg06', src: bg06, name: '渐变蓝', theme: 'dark' },
  { id: 'bg07', src: bg07, name: '网格蓝', theme: 'dark' },
  { id: 'bg08', src: bg08, name: '简约蓝', theme: 'dark' },
  { id: 'bg09', src: bg09, name: '地球蓝', theme: 'dark' },
  { id: 'bg10', src: bg10, name: '经典蓝', theme: 'dark' },
  // 浅色主题背景
  { id: 'light01', src: '', name: '简约白', theme: 'light' },
  { id: 'light02', src: '', name: '淡雅灰', theme: 'light' },
  { id: 'light03', src: '', name: '清新蓝', theme: 'light' },
] as const

// 主题类型
export type ThemeType = 'dark' | 'light'

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
  },
  light: {
    pageBg: '#F5F7FA',
    panelBg: 'rgba(255, 255, 255, 1)',
    panelBorder: 'rgba(22, 119, 255, 0.08)',
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
    tableBorder: '#e5e7eb',
    buttonBg: 'rgba(22, 119, 255, 0.08)',
    buttonHover: 'rgba(22, 119, 255, 0.15)',
    buttonText: '#1677FF',
    emptyBorder: 'rgba(22, 119, 255, 0.2)',
    emptyBg: 'rgba(255, 255, 255, 0.9)',
    cardBg: '#ffffff',
  },
}

// 浅色主题纯色背景
export const LIGHT_BACKGROUNDS: Record<string, string> = {
  light01: '#FFFFFF',
  light02: 'linear-gradient(180deg, #F5F7FA 0%, #EEF2F6 100%)',
  light03: 'linear-gradient(135deg, #F0F5FF 0%, #E6F4FF 100%)',
}

// 图表类型图标
export const CHART_ICONS: Record<string, string> = {
  table: 'fa-table',
  bar: 'fa-chart-column',
  horizontal_bar: 'fa-chart-bar',
  line: 'fa-chart-line',
  pie: 'fa-chart-pie',
  map: 'fa-map',
  kpi: 'fa-hashtag',
}

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

// 图表类型选项
export const CHART_TYPES = [
  { key: 'table', label: '表格', icon: 'fa-table' },
  { key: 'bar', label: '柱状图', icon: 'fa-chart-column' },
  { key: 'horizontal_bar', label: '条形图', icon: 'fa-chart-bar' },
  { key: 'line', label: '折线图', icon: 'fa-chart-line' },
  { key: 'pie', label: '饼图', icon: 'fa-chart-pie' },
  { key: 'kpi', label: 'KPI卡片', icon: 'fa-hashtag' },
  { key: 'map', label: '地图', icon: 'fa-map' },
]

// 面板数据缓存类型
export interface PanelDataCache {
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
