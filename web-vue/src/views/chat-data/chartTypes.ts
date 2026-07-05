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
export type ChartTypeHelp = { summary: string; example: string; sql: string }

export const CHART_TYPE_HELP_TEXT: Record<ChartType, ChartTypeHelp> = {
  table: {
    summary: '适合查看明细记录和多字段结果。',
    example: '示例：近30天订单明细列表',
    sql: 'SELECT order_id, user_name, amount, order_time FROM orders ORDER BY order_time DESC LIMIT 100',
  },
  kpi: {
    summary: '适合展示单个核心指标结果。',
    example: '示例：今日销售额、活跃用户数',
    sql: 'SELECT SUM(amount) AS total_amount FROM orders WHERE order_date = CURRENT_DATE',
  },
  bar: {
    summary: '适合分类对比和排行分析。',
    example: '示例：各城市销售额对比',
    sql: 'SELECT city, SUM(amount) AS total_amount FROM orders GROUP BY city ORDER BY total_amount DESC',
  },
  horizontal_bar: {
    summary: '适合名称较长的分类排行。',
    example: '示例：各渠道订单量排行',
    sql: 'SELECT channel_name, COUNT(*) AS order_count FROM orders GROUP BY channel_name ORDER BY order_count DESC',
  },
  line: {
    summary: '适合查看时间趋势和连续变化。',
    example: '示例：近12个月销售额趋势',
    sql: 'SELECT month, SUM(amount) AS total_amount FROM orders GROUP BY month ORDER BY month',
  },
  area: {
    summary: '适合查看趋势规模和累计变化。',
    example: '示例：近30天活跃用户趋势',
    sql: 'SELECT stat_date, COUNT(DISTINCT user_id) AS active_users FROM user_events GROUP BY stat_date ORDER BY stat_date',
  },
  pie: {
    summary: '适合查看分类占比分布。',
    example: '示例：各品类销售额占比',
    sql: 'SELECT category_name, SUM(amount) AS total_amount FROM orders GROUP BY category_name',
  },
  map: {
    summary: '适合查看区域分布和省市对比。',
    example: '示例：各省销售额分布',
    sql: 'SELECT province, SUM(amount) AS total_amount FROM orders GROUP BY province',
  },
  combo: {
    summary: '适合同维度下同时展示柱状和折线指标。',
    example: '示例：按月份看订单数和销售额',
    sql: 'SELECT month, COUNT(*) AS order_count, SUM(amount) AS total_amount FROM orders GROUP BY month ORDER BY month',
  },
  funnel: {
    summary: '适合查看阶段转化和流程流失。',
    example: '示例：访问、下单、支付转化漏斗',
    sql: 'SELECT stage_name, COUNT(DISTINCT user_id) AS user_count FROM funnel_events GROUP BY stage_name',
  },
  graph: {
    summary: '适合查看实体关系和上下游连接。',
    example: '示例：用户、订单、商品关系图',
    sql: 'SELECT user_name AS source, product_name AS target, COUNT(*) AS value FROM order_items GROUP BY user_name, product_name',
  },
}

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
