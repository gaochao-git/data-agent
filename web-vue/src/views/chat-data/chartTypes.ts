export const CHART_TYPES = [
  { key: 'table', label: '表格', icon: 'fa-table', echarts: false, supportsDualAxis: false, supportsSeriesType: false },
  { key: 'kpi', label: '指标卡', icon: 'fa-hashtag', echarts: false, supportsDualAxis: false, supportsSeriesType: false },
  { key: 'bar', label: '柱状图', icon: 'fa-chart-column', echarts: true, supportsDualAxis: false, supportsSeriesType: false },
  { key: 'horizontal_bar', label: '条形图', icon: 'fa-chart-bar', echarts: true, supportsDualAxis: false, supportsSeriesType: false },
  { key: 'line', label: '折线图', icon: 'fa-chart-line', echarts: true, supportsDualAxis: false, supportsSeriesType: false },
  { key: 'area', label: '面积图', icon: 'fa-chart-area', echarts: true, supportsDualAxis: false, supportsSeriesType: false },
  { key: 'pie', label: '饼图', icon: 'fa-chart-pie', echarts: true, supportsDualAxis: false, supportsSeriesType: false },
  { key: 'map', label: '地图', icon: 'fa-map', echarts: true, supportsDualAxis: false, supportsSeriesType: false },
  { key: 'combo', label: '柱线组合图', icon: 'fa-chart-simple', echarts: true, supportsDualAxis: true, supportsSeriesType: false },
  { key: 'funnel', label: '漏斗图', icon: 'fa-filter', echarts: true, supportsDualAxis: false, supportsSeriesType: false },
  { key: 'graph', label: '关系图', icon: 'fa-diagram-project', echarts: true, supportsDualAxis: false, supportsSeriesType: false },
] as const

export type ChartType = typeof CHART_TYPES[number]['key']

export const CHART_TYPE_ICONS: Record<ChartType, string> = CHART_TYPES.reduce((result, item) => {
  result[item.key] = item.icon
  return result
}, {} as Record<ChartType, string>)

const CHART_TYPE_KEYS = new Set<string>(CHART_TYPES.map(item => item.key))

export const normalizeChartType = (chartType: unknown): ChartType => {
  const normalized = String(chartType || 'table').trim().toLowerCase()
  if (CHART_TYPE_KEYS.has(normalized)) return normalized as ChartType
  return 'table'
}

export const isEchartsChartType = (chartType: unknown) => {
  const normalized = normalizeChartType(chartType)
  return Boolean(CHART_TYPES.find(item => item.key === normalized)?.echarts)
}

export const supportsDualAxis = (chartType: unknown) => {
  const normalized = normalizeChartType(chartType)
  return Boolean(CHART_TYPES.find(item => item.key === normalized)?.supportsDualAxis)
}

export const supportsSeriesType = (chartType: unknown) => {
  const normalized = normalizeChartType(chartType)
  return Boolean(CHART_TYPES.find(item => item.key === normalized)?.supportsSeriesType)
}
